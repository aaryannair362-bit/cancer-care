"""
FastAPI Router for CCA Cancer Care AI OS.
Implements the complete Section 46 API surface and AI service governance.
"""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..models import User
from ..auth import (
    get_current_user, is_admin, is_doctor, is_cca_oncologist, is_cca_front_desk,
    is_cca_nurse_navigator,
)
from ..config import settings
from ..ocr_service import extract_document
from ..scribe import scribe
from .. import drug_matcher
from ..models_cca import (
    CCAPatient, CCAConsent, CCAQueueEvent, CCAEncounter, CCAIntakeAssessment,
    CCADocument, ClinicalFact, CCAContradiction, CCACancerDiagnosis,
    CCABiomarkerResult, CCAOrder, CCAResult, StagingRecord, StagingEvidence,
    GuidelineContext, ClinicalBrief, MDTCase, MDTDecision, CarePlan,
    CarePlanVersion, CarePlanTask, TreatmentPlan, TreatmentSession,
    ToxicityEvent, TreatmentClearance, ResponseAssessment, CCAJourneyEvent
)
from ..cca_engine import (
    calculate_bsa, detect_contradictions, evaluate_staging_readiness,
    evaluate_guideline_readiness, synthesize_nexus_brief, generate_care_plan_prefill,
    classify_document, extract_clinical_facts,
)
from ..cca_seed import seed_cca_database, simulate_ct_result

router = APIRouter(prefix="/api/cca", tags=["CCA Oncology OS"])
ALLOWED_DOCUMENT_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/tiff"}

def get_cca_db():
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------
# Auth / tenancy / attribution helpers
#
# Every endpoint below requires a logged-in user (current_user) and scopes all
# patient access to that user's organization_id -- CCAPatient.organization_id
# is the tenancy boundary; child records (documents, facts, orders, etc.) carry
# no organization_id of their own, so they're scoped transitively by first
# resolving the CCAPatient they belong to via _get_org_patient/_check_patient_in_org,
# the same pattern the rest of this codebase's routers already use for
# child-table access. Actor identity (verified_by/recorded_by/decided_by/...)
# is always the authenticated caller, never a hardcoded name.
# ---------------------------------------------------------

def _org_id(current_user: dict) -> int:
    org_id = current_user.get("organization_id")
    if org_id is None:
        raise HTTPException(401, "No organization associated with this account")
    return org_id


def _actor(current_user: dict) -> str:
    return current_user.get("email") or "unknown"


def _require_clinician(current_user: dict):
    """Gate for irreversible clinical decisions (staging confirm, treatment clearance,
    MDT recommendation, care-plan approval/amendment): a treating oncologist (Medical/
    Surgical/Radiation), the general Doctor role, or Admin only."""
    if not (is_doctor(current_user) or is_admin(current_user) or is_cca_oncologist(current_user)):
        raise HTTPException(403, "Only a treating oncologist or Admin may perform this action")


def _get_org_patient(db: Session, patient_id: int, org_id: int) -> CCAPatient:
    patient = db.query(CCAPatient).filter(
        CCAPatient.id == patient_id,
        CCAPatient.organization_id == org_id
    ).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    return patient


def _check_patient_in_org(db: Session, patient_id: Optional[int], org_id: int):
    """For child records that only carry a patient_id: confirm that patient_id
    resolves to a CCAPatient in the caller's org, 404 otherwise (never confirms
    existence of another org's patient to the caller)."""
    if patient_id is None:
        raise HTTPException(404, "Patient not found")
    _get_org_patient(db, patient_id, org_id)


def _require_patient_id(body: dict) -> int:
    patient_id = body.get("patient_id")
    if patient_id is None:
        raise HTTPException(422, "patient_id is required")
    try:
        return int(patient_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "patient_id must be an integer")


def _coerce_float(body: dict, key: str, default: float) -> float:
    value = body.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key} must be a number")


def _coerce_int(body: dict, key: str, default: int) -> int:
    value = body.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key} must be an integer")


# ---------------------------------------------------------
# 1. Patients, Command Centre & Contextual Summary (SCR-02/10)
# ---------------------------------------------------------

@router.get("/patients")
def list_patients(
    q: Optional[str] = None, scope: Optional[str] = "all",
    db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    query = db.query(CCAPatient).filter(CCAPatient.organization_id == org_id)
    if q:
        # "mobile" is in this search box's own placeholder text (frontdesk.html) but was never
        # actually matched here -- searching by phone number silently always returned nothing.
        query = query.filter(
            (CCAPatient.name.ilike(f"%{q}%")) |
            (CCAPatient.mrn.ilike(f"%{q}%")) |
            (CCAPatient.phone.ilike(f"%{q}%")) |
            (CCAPatient.journey_state.ilike(f"%{q}%"))
        )
    patients = query.order_by(CCAPatient.id.asc()).all()

    results = []
    for p in patients:
        staging_info = evaluate_staging_readiness(db, p.id)
        results.append({
            "id": p.id,
            "mrn": p.mrn,
            "name": p.name,
            "age": p.age,
            "sex": p.sex,
            "dob": p.dob,
            # phone/address were missing here entirely -- frontdesk.html's "select existing
            # patient to continue registration" flow (selectSearchResultPatient()) reads exactly
            # these fields to prefill the wizard. Without phone, the mobile field silently came
            # up blank; since mobile is required to submit, every "Continue Registration" for a
            # patient found via search hit a validation alert and never actually saved anything.
            "phone": p.phone,
            "address": p.address,
            "photo_url": p.photo_url,
            "journey_state": p.journey_state,
            "primary_oncologist": p.primary_oncologist,
            "staging_state": staging_info["state"],
            "demo_flag": p.demo_flag,
            "created_at": p.created_at.isoformat() if p.created_at else None
        })
    return {"results": results, "total": len(results)}


@router.post("/patients", status_code=201)
async def register_cca_patient(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Registers a new patient directly into the Oncology OS's own patient population
    (CCAPatient) -- previously the ONLY way a CCAPatient row could ever exist was
    cca_seed.py's hardcoded demo data, so Front Desk's registration wizard had nothing real to
    call and was pointed at the general HMS's POST /api/patients/register instead, which writes
    to a completely separate Patient table that no CCA role (Nurse Navigator, oncologists, MDT,
    etc.) ever reads from -- a newly registered patient was findable only via Front Desk's own
    Registration search (backed by that general table) and invisible everywhere else in the
    Oncology OS. This endpoint is the real fix."""
    if not (is_cca_front_desk(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only CCA Front Desk or Admin can register a new oncology patient")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Patient name is required")
    org_id = _org_id(current_user)

    age = body.get("age")
    try:
        age = int(age) if age not in (None, "") else None
    except (TypeError, ValueError):
        age = None

    patient = CCAPatient(
        mrn="PENDING",
        name=name,
        dob=body.get("dob") or None,
        age=age,
        sex=body.get("sex") or None,
        phone=body.get("phone") or None,
        address=body.get("address") or None,
        journey_state="Registered",
        primary_oncologist=body.get("primary_oncologist") or None,
        attender_name=body.get("attender_name") or None,
        attender_phone=body.get("attender_phone") or None,
        attender_relationship=body.get("attender_relationship") or None,
        id_proof_type=body.get("id_proof_type") or None,
        id_proof_number=body.get("id_proof_number") or None,
        id_proof_name=body.get("id_proof_name") or None,
        id_proof_dob=body.get("id_proof_dob") or None,
        id_proof_verification_status=body.get("id_proof_verification_status") or None,
        organization_id=org_id,
        demo_flag=False,
    )
    db.add(patient)
    db.flush()
    patient.mrn = f"CCA-{org_id}-{patient.id:06d}"

    actor = _actor(current_user)
    j_ev = CCAJourneyEvent(
        patient_id=patient.id,
        event_type="PATIENT_REGISTERED",
        event_title="Patient Registered",
        event_category="REGISTRATION",
        description=f"{actor} registered {name} into the Oncology OS.",
        actor_name=actor,
        actor_role=current_user.get("role"),
    )
    db.add(j_ev)
    db.commit()
    db.refresh(patient)
    return {
        "status": "success",
        "patient": {
            "id": patient.id, "mrn": patient.mrn, "name": patient.name,
            "age": patient.age, "sex": patient.sex, "journey_state": patient.journey_state,
        },
    }


@router.patch("/patients/{patient_id}/visit")
async def register_return_visit(
    patient_id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Front Desk's registration wizard has a 'search and select existing patient' step
    (selectSearchResultPatient() in frontdesk.html) specifically so a returning patient's new
    visit/documents attach to their existing record instead of creating a duplicate -- but until
    this endpoint existed, submitFullRegistration() had nothing to call for that case and always
    POSTed /patients, silently creating a brand-new CCAPatient (and a new, unrelated MRN) every
    time, orphaning that patient's actual prior history from GET .../case-summary. This is the
    'continue registration' counterpart to POST /patients: same permission and field set, but it
    updates the existing row and records a RETURN_VISIT journey event instead of minting a new
    patient. Blank fields leave the existing value alone rather than overwriting it -- Front
    Desk's wizard pre-fills from the selected patient, but a field left blank here should not be
    read as 'clear this on file'."""
    if not (is_cca_front_desk(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only CCA Front Desk or Admin can register a return visit")
    patient = _get_org_patient(db, patient_id, _org_id(current_user))
    body = await request.json()

    if body.get("name"):
        patient.name = body["name"].strip()
    if body.get("dob"):
        patient.dob = body["dob"]
    if body.get("age") not in (None, ""):
        try:
            patient.age = int(body["age"])
        except (TypeError, ValueError):
            pass
    if body.get("sex"):
        patient.sex = body["sex"]
    if body.get("phone"):
        patient.phone = body["phone"]
    if body.get("address"):
        patient.address = body["address"]
    if body.get("primary_oncologist"):
        patient.primary_oncologist = body["primary_oncologist"]
    if body.get("attender_name"):
        patient.attender_name = body["attender_name"]
    if body.get("attender_phone"):
        patient.attender_phone = body["attender_phone"]
    if body.get("attender_relationship"):
        patient.attender_relationship = body["attender_relationship"]
    if body.get("id_proof_type"):
        patient.id_proof_type = body["id_proof_type"]
    if body.get("id_proof_number"):
        patient.id_proof_number = body["id_proof_number"]
    if body.get("id_proof_name"):
        patient.id_proof_name = body["id_proof_name"]
    if body.get("id_proof_dob"):
        patient.id_proof_dob = body["id_proof_dob"]
    if body.get("id_proof_verification_status"):
        patient.id_proof_verification_status = body["id_proof_verification_status"]

    actor = _actor(current_user)
    db.add(CCAJourneyEvent(
        patient_id=patient.id,
        event_type="RETURN_VISIT",
        event_title="Patient Returned for New Visit",
        event_category="REGISTRATION",
        description=f"{actor} registered a new front-desk visit for returning patient {patient.name} (MRN {patient.mrn}).",
        actor_name=actor,
        actor_role=current_user.get("role"),
    ))
    db.commit()
    db.refresh(patient)
    return {
        "status": "success",
        "patient": {
            "id": patient.id, "mrn": patient.mrn, "name": patient.name,
            "age": patient.age, "sex": patient.sex, "journey_state": patient.journey_state,
        },
    }


@router.get("/patients/{patient_id}")
def get_patient(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    patient = _get_org_patient(db, patient_id, _org_id(current_user))

    staging_info = evaluate_staging_readiness(db, patient.id)
    guideline_info = evaluate_guideline_readiness(db, patient.id)
    contradictions = db.query(CCAContradiction).filter(
        CCAContradiction.patient_id == patient.id,
        CCAContradiction.status == "OPEN"
    ).all()

    intake = db.query(CCAIntakeAssessment).filter(
        CCAIntakeAssessment.patient_id == patient.id
    ).order_by(CCAIntakeAssessment.created_at.desc()).first()

    header = {
        "mrn": patient.mrn,
        "name": patient.name,
        "age": patient.age,
        "sex": patient.sex,
        "journey_state": patient.journey_state,
        "primary_oncologist": patient.primary_oncologist,
        "bsa": intake.bsa if intake else None,
        "ecog": intake.ecog if intake else None,
        "engine_pills": {
            "summary": {"label": "SUMMARY", "status": "ACTIVE", "badge": "UPDATED"},
            "journey": {"label": "JOURNEY", "status": "ACTIVE", "badge": f"{db.query(CCAJourneyEvent).filter(CCAJourneyEvent.patient_id == patient.id).count()} Events"},
            "staging": {"label": "STAGING", "status": staging_info["state"], "badge": staging_info["state"].replace("_", " ")},
            "guideline": {"label": "NCCN CONTEXT", "status": guideline_info["state"], "badge": guideline_info["state"].replace("_", " ")},
            "nexus": {"label": "NEXUS BRIEF", "status": "READY", "badge": "13 SECTIONS"},
            "care_plan": {"label": "CARE PLAN", "status": "ACTIVE", "badge": "v1.0"}
        },
        "open_contradictions_count": len(contradictions)
    }

    return {
        "patient": {
            "id": patient.id,
            "mrn": patient.mrn,
            "name": patient.name,
            "age": patient.age,
            "sex": patient.sex,
            "dob": patient.dob,
            "phone": patient.phone,
            "address": patient.address,
            "photo_url": patient.photo_url,
            "journey_state": patient.journey_state,
            "primary_oncologist": patient.primary_oncologist,
            "attender_name": patient.attender_name,
            "attender_phone": patient.attender_phone,
            "attender_relationship": patient.attender_relationship,
            "id_proof_type": patient.id_proof_type,
            "id_proof_number": patient.id_proof_number,
            "id_proof_name": patient.id_proof_name,
            "id_proof_dob": patient.id_proof_dob,
            "id_proof_verification_status": patient.id_proof_verification_status,
            "demo_flag": patient.demo_flag
        },
        "header": header
    }


@router.get("/patients/{patient_id}/summary")
def get_patient_summary(
    patient_id: int, context: str = "initial_consult", db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))

    facts = db.query(ClinicalFact).filter(
        ClinicalFact.patient_id == patient_id,
        ClinicalFact.status == "VERIFIED"
    ).all()

    fact_dict = {f.fact_type: f for f in facts}
    staging_info = evaluate_staging_readiness(db, patient_id)
    guideline_info = evaluate_guideline_readiness(db, patient_id)
    contradictions = db.query(CCAContradiction).filter(
        CCAContradiction.patient_id == patient_id,
        CCAContradiction.status == "OPEN"
    ).all()

    intake = db.query(CCAIntakeAssessment).filter(
        CCAIntakeAssessment.patient_id == patient_id
    ).order_by(CCAIntakeAssessment.created_at.desc()).first()

    blocks = [
        {
            "key": "diagnosis",
            "tier": 1,
            "title": "Oncologic Diagnosis",
            "value": fact_dict["HISTOLOGY"].value if "HISTOLOGY" in fact_dict else "[NOT_RECORDED]",
            "absenceState": "NORMAL" if "HISTOLOGY" in fact_dict else "NOT_RECORDED",
            "contentClass": "OBSERVATION",
            "provenance": {"fact_id": fact_dict["HISTOLOGY"].id, "doc_id": fact_dict["HISTOLOGY"].document_id} if "HISTOLOGY" in fact_dict else None
        },
        {
            "key": "laterality_site",
            "tier": 1,
            "title": "Primary Site & Laterality",
            "value": (
                f"[CONTRADICTED: {contradictions[0].description}]" if contradictions
                else (fact_dict["LATERALITY"].value if "LATERALITY" in fact_dict else "[NOT_RECORDED]")
            ),
            "absenceState": "CONTRADICTED" if contradictions else ("NORMAL" if "LATERALITY" in fact_dict else "NOT_RECORDED"),
            "contentClass": "OBSERVATION",
            "provenance": {"fact_id": fact_dict["LATERALITY"].id, "doc_id": fact_dict["LATERALITY"].document_id} if "LATERALITY" in fact_dict else None
        },
        {
            "key": "staging",
            "tier": 1,
            "title": "AJCC 8th Ed Staging",
            "value": (
                staging_info["confirmed_record"]["stage_value"] if staging_info["confirmed_record"]
                else f"[{staging_info['state']}: Missing {', '.join([m['input'] for m in staging_info['missing']])}]"
            ),
            "absenceState": "NORMAL" if staging_info["confirmed_record"] else "INCOMPLETE",
            "contentClass": "EVALUATION"
        },
        {
            "key": "biomarkers",
            "tier": 1,
            "title": "Biomarker Profile",
            "value": ", ".join([f.value for f in facts if f.fact_type == "BIOMARKER_RESULT"]) or "[NOT_TESTED]",
            "absenceState": "NORMAL" if any(f.fact_type == "BIOMARKER_RESULT" for f in facts) else "NOT_RECORDED",
            "contentClass": "OBSERVATION"
        },
        {
            "key": "performance_status",
            "tier": 2,
            "title": "Performance Status & Vitals",
            "value": (
                f"ECOG {intake.ecog}, BSA {intake.bsa} m² (DuBois), BP {intake.bp_systolic}/{intake.bp_diastolic} mmHg, SpO2 {intake.oxygen_sat}%"
                if intake else "[NOT_RECORDED]"
            ),
            "absenceState": "NORMAL" if intake else "NOT_RECORDED",
            "contentClass": "OBSERVATION"
        },
        {
            "key": "comorbidities_meds",
            "tier": 2,
            "title": "Comorbidities & Concurrent Meds",
            "value": f"{fact_dict['COMORBIDITY'].value if 'COMORBIDITY' in fact_dict else 'None recorded'}; Meds: {fact_dict['MEDICATION'].value if 'MEDICATION' in fact_dict else 'None'}",
            "absenceState": "NORMAL",
            "contentClass": "OBSERVATION"
        },
        {
            "key": "guidelines",
            "tier": 2,
            "title": "Guideline Context",
            "value": f"{guideline_info['guideline_source']} ({guideline_info['version']}) - State: {guideline_info['state']}",
            "absenceState": "NORMAL" if guideline_info["state"] == "READY" else "PENDING_REVIEW",
            "contentClass": "EVALUATION"
        }
    ]

    return {"patient_id": patient_id, "context": context, "blocks": blocks}


@router.get("/patients/{patient_id}/case-summary")
def get_case_summary(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Single aggregated read backing the "Patient History & Documents" panel (Medical/Surgical/
    Radiation Oncologist pages, Financial Counsellor page): every document ever uploaded for this
    patient (front desk or otherwise), the clinical facts extracted/verified from them, the
    visit-by-visit encounter history, orders/results on record, and the journey timeline. This is
    what makes a returning patient's prior history show up automatically -- the query is just
    "everything in the database for patient_id", not scoped to the current visit/session.
    """
    org_id = _org_id(current_user)
    patient = _get_org_patient(db, patient_id, org_id)

    docs = db.query(CCADocument).filter(CCADocument.patient_id == patient_id).order_by(CCADocument.uploaded_at.desc()).all()
    facts = db.query(ClinicalFact).filter(
        ClinicalFact.patient_id == patient_id, ClinicalFact.status.in_(["VERIFIED", "PROPOSED"])
    ).order_by(ClinicalFact.created_at.desc()).all()
    encounters = db.query(CCAEncounter).filter(CCAEncounter.patient_id == patient_id).order_by(CCAEncounter.started_at.desc()).all()
    orders = db.query(CCAOrder).filter(CCAOrder.patient_id == patient_id).order_by(CCAOrder.ordered_at.desc()).all()
    results = db.query(CCAResult).filter(CCAResult.patient_id == patient_id).order_by(CCAResult.resulted_at.desc()).all()
    events = db.query(CCAJourneyEvent).filter(CCAJourneyEvent.patient_id == patient_id).order_by(CCAJourneyEvent.timestamp.desc()).limit(30).all()

    facts_by_type: Dict[str, List[dict]] = {}
    facts_per_document: Dict[int, int] = {}
    for f in facts:
        facts_by_type.setdefault(f.fact_type, []).append({
            "id": f.id, "value": f.value, "status": f.status, "document_id": f.document_id,
            "confidence": f.confidence,
        })
        if f.document_id is not None:
            facts_per_document[f.document_id] = facts_per_document.get(f.document_id, 0) + 1

    def _note_field(note_content, key):
        return note_content.get(key) if isinstance(note_content, dict) else None

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "patient": {
            "id": patient.id, "mrn": patient.mrn, "name": patient.name, "age": patient.age,
            "sex": patient.sex, "dob": patient.dob, "journey_state": patient.journey_state,
            "primary_oncologist": patient.primary_oncologist,
            "id_proof_type": patient.id_proof_type, "id_proof_number": patient.id_proof_number,
            "id_proof_verification_status": patient.id_proof_verification_status,
        },
        "overview": {
            "document_count": len(docs),
            "verified_fact_count": sum(f.status == "VERIFIED" for f in facts),
            "encounter_count": len(encounters),
            "last_visit": encounters[0].started_at.isoformat() if encounters and encounters[0].started_at else None,
            # A RETURN_VISIT journey event (register_return_visit, raised when Front Desk selects
            # an existing patient to "Continue Registration") is itself proof this patient came
            # back -- checking only encounters/docs missed exactly that case: a patient re-
            # registered the same day with no new document or consultation yet would otherwise
            # still show "No prior visits on record" right above a Journey Timeline that visibly
            # contradicts it.
            "is_returning_patient": (
                len(encounters) > 1 or len(docs) > 0
                or any(ev.event_type == "RETURN_VISIT" for ev in events)
            ),
        },
        "documents": [
            {
                "id": d.id, "filename": d.filename, "classification": d.classification_class,
                "status": d.status, "uploaded_by": d.uploaded_by,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                "excerpt": (d.ocr_text or "")[:400],
                "fact_count": facts_per_document.get(d.id, 0),
                "file_url": f"/api/cca/patients/{patient_id}/documents/{d.id}/file" if d.file_content else None,
            }
            for d in docs
        ],
        "clinical_facts": facts_by_type,
        "encounters": [
            {
                "id": e.id, "started_at": e.started_at.isoformat() if e.started_at else None,
                "specialty": e.specialty, "clinician": e.clinician, "note_status": e.note_status,
                "chief_complaint": _note_field(e.note_content, "chief_complaint"),
                "diagnosis": _note_field(e.note_content, "primary_diagnosis"),
                "advice": _note_field(e.note_content, "advice"),
                "medications": _note_field(e.note_content, "ai_scribe_medications") or [],
            }
            for e in encounters
        ],
        "orders": [
            {"id": o.id, "order_type": o.order_type, "item_name": o.item_name, "status": o.status,
             "ordered_at": o.ordered_at.isoformat() if o.ordered_at else None}
            for o in orders
        ],
        "results": [
            {"id": r.id, "result_type": r.result_type, "title": r.title, "is_critical": r.is_critical,
             "status": r.status, "resulted_at": r.resulted_at.isoformat() if r.resulted_at else None}
            for r in results
        ],
        "journey": [
            {"event_type": ev.event_type, "title": ev.event_title, "description": ev.description,
             "actor": ev.actor_name, "actor_role": ev.actor_role,
             "timestamp": ev.timestamp.isoformat() if ev.timestamp else None}
            for ev in events
        ],
        "disclaimer": "This summary combines hospital records with OCR-derived text. Verify clinical decisions against the original documents.",
    }


# ---------------------------------------------------------
# 2. Documents, Extractions & Verification Workspace (SCR-06/07)
# ---------------------------------------------------------

@router.post("/documents", status_code=201)
async def upload_document(
    patient_id: int = Query(...), file: UploadFile = File(...),
    db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)
):
    """
    Real document ingestion -- OCR + deterministic classification + AI-drafted fact extraction,
    all landing as status=PROPOSED ClinicalFact rows for clinician verification. Previously the
    only way a CCADocument/ClinicalFact ever existed was cca_seed.py's hardcoded demo data;
    this is what makes the verification workspace actually usable for a real patient's real
    uploaded records, not just the one scripted demo case.
    """
    org_id = _org_id(current_user)
    patient = _get_org_patient(db, patient_id, org_id)

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_DOCUMENT_TYPES:
        raise HTTPException(415, "Upload a PDF, JPEG, PNG, or TIFF file")
    max_bytes = settings.MAX_PATIENT_DOCUMENT_MB * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if not content:
        raise HTTPException(400, "Uploaded file is empty")
    if len(content) > max_bytes:
        raise HTTPException(413, f"File exceeds {settings.MAX_PATIENT_DOCUMENT_MB} MB limit")

    filename = re.sub(r"[^A-Za-z0-9._() -]", "_", file.filename or "cca-document")[:255]
    digest = hashlib.sha256(content).hexdigest()
    duplicate = db.query(CCADocument).filter(
        CCADocument.patient_id == patient_id, CCADocument.file_hash == digest
    ).first()
    if duplicate:
        raise HTTPException(409, f"This file was already uploaded as document #{duplicate.id}")

    actor = _actor(current_user)
    ocr_failed_reason = None
    try:
        ocr_result = extract_document(content, content_type)
        ocr_text = ocr_result["text"]
        page_count = ocr_result["page_count"]
        doc_class, confidence = classify_document(ocr_text)
    except Exception as exc:
        # A document that fails OCR (blank page, corrupted scan, unreadable image) is still a
        # real file the front desk / clinician needs on record -- reject the file outright would
        # lose it entirely. Save it for manual review instead, matching the general HMS module's
        # PatientDocument upload (routers/patient_documents.py), which never hard-fails on OCR
        # error either.
        ocr_failed_reason = str(exc)
        ocr_text, page_count, doc_class, confidence = None, 1, None, None

    doc = CCADocument(
        patient_id=patient_id, filename=filename, mime_type=content_type, page_count=page_count,
        file_hash=digest, file_content=content, classification_class=doc_class,
        classification_confidence=confidence, ocr_text=ocr_text, uploaded_by=actor,
        status="OCR_FAILED" if ocr_failed_reason else "EXTRACTED",
    )
    db.add(doc)
    db.flush()

    fact_rows = []
    if not ocr_failed_reason:
        drafted_facts = extract_clinical_facts(ocr_text)
        for f in drafted_facts:
            fact = ClinicalFact(
                patient_id=patient_id, document_id=doc.id, fact_type=f["fact_type"], value=f["value"],
                verbatim_span=f["verbatim"], page_number=1, confidence=f["confidence"], status="PROPOSED",
            )
            db.add(fact)
            fact_rows.append(fact)
        db.flush()
        detect_contradictions(db, patient_id)

    j_ev = CCAJourneyEvent(
        patient_id=patient_id,
        event_type="DOC_INGESTION",
        event_title=f"Document Ingested: {filename}",
        event_category="INVESTIGATION",
        description=(
            f"{actor} uploaded {filename}, but text extraction failed ({ocr_failed_reason}). Saved for manual review."
            if ocr_failed_reason else
            f"{actor} uploaded {filename}, classified as {doc_class}. {len(fact_rows)} candidate fact(s) drafted for review."
        ),
        actor_name=actor,
        actor_role=current_user.get("role"),
        provenance_doc_id=doc.id,
    )
    db.add(j_ev)
    db.commit()
    db.refresh(doc)

    return {
        "status": "success",
        "document": {
            "id": doc.id, "filename": doc.filename, "classification": doc.classification_class,
            "confidence": doc.classification_confidence, "page_count": doc.page_count,
            "status": doc.status,
        },
        "facts_drafted": len(fact_rows),
        "ocr_warning": ocr_failed_reason,
    }


@router.get("/documents")
def list_documents(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))

    docs = db.query(CCADocument).filter(CCADocument.patient_id == patient_id).order_by(CCADocument.id.asc()).all()
    results = []
    for d in docs:
        fact_count = db.query(ClinicalFact).filter(ClinicalFact.document_id == d.id).count()
        verified_count = db.query(ClinicalFact).filter(
            ClinicalFact.document_id == d.id,
            ClinicalFact.status == "VERIFIED"
        ).count()
        results.append({
            "id": d.id,
            "filename": d.filename,
            "classification": d.classification_class,
            "confidence": d.classification_confidence,
            "status": d.status,
            "fact_count": fact_count,
            "verified_count": verified_count,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None
        })
    return {"documents": results}


@router.get("/patients/{patient_id}/documents/{document_id}/file")
def view_cca_document(
    patient_id: int, document_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Stream the raw uploaded file so it can actually be opened/previewed -- until now
    CCADocument.file_content was written on upload but nothing ever read it back. Mirrors
    the general HMS module's equivalent (routers/patient_documents.py:view_document)."""
    _get_org_patient(db, patient_id, _org_id(current_user))
    doc = db.query(CCADocument).filter(
        CCADocument.id == document_id, CCADocument.patient_id == patient_id
    ).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    if not doc.file_content:
        raise HTTPException(404, "No file stored for this document")
    safe_name = doc.filename.replace('"', "")
    return Response(
        doc.file_content, media_type=doc.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/extractions/{document_id}")
def get_extractions(
    document_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    doc = db.query(CCADocument).filter(CCADocument.id == document_id).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    _check_patient_in_org(db, doc.patient_id, _org_id(current_user))

    facts = db.query(ClinicalFact).filter(ClinicalFact.document_id == document_id).all()
    open_ctrs = db.query(CCAContradiction).filter(
        CCAContradiction.patient_id == doc.patient_id,
        CCAContradiction.status == "OPEN"
    ).all()

    fact_results = []
    for f in facts:
        is_conflicted = any(f.id in (c.conflicting_fact_ids or []) for c in open_ctrs)
        fact_results.append({
            "id": f.id,
            "type": f.fact_type,
            "value": f.value,
            "verbatim": f.verbatim_span,
            "page": f.page_number,
            "bbox": f.bounding_box,
            "confidence": f.confidence,
            "status": f.status,
            "is_conflicted": is_conflicted,
            "verified_by": f.verified_by
        })

    return {
        "document": {
            "id": doc.id,
            "filename": doc.filename,
            "classification": doc.classification_class,
            "ocr_text": doc.ocr_text
        },
        "facts": fact_results,
        "contradictions": [{"id": c.id, "rule_id": c.rule_id, "description": c.description} for c in open_ctrs]
    }


@router.post("/verification/{fact_id}/accept")
def accept_fact(
    fact_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    fact = db.query(ClinicalFact).filter(ClinicalFact.id == fact_id).first()
    if not fact:
        raise HTTPException(404, "Fact not found")
    _check_patient_in_org(db, fact.patient_id, _org_id(current_user))

    open_ctr = db.query(CCAContradiction).filter(
        CCAContradiction.patient_id == fact.patient_id,
        CCAContradiction.status == "OPEN"
    ).first()
    if open_ctr and fact.id in (open_ctr.conflicting_fact_ids or []):
        raise HTTPException(422, f"Cannot accept fact: Part of unresolved contradiction ({open_ctr.rule_id}). Resolve contradiction first.")

    fact.status = "VERIFIED"
    fact.verified_by = _actor(current_user)
    fact.verified_at = datetime.utcnow()
    db.commit()
    return {"status": "success", "fact_id": fact.id, "state": fact.status}


@router.post("/verification/{fact_id}/correct")
async def correct_fact(
    fact_id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    body = await request.json()
    new_value = body.get("value")
    if not new_value:
        raise HTTPException(400, "New value required")

    fact = db.query(ClinicalFact).filter(ClinicalFact.id == fact_id).first()
    if not fact:
        raise HTTPException(404, "Fact not found")
    _check_patient_in_org(db, fact.patient_id, _org_id(current_user))

    fact.status = "SUPERSEDED"

    new_fact = ClinicalFact(
        patient_id=fact.patient_id,
        document_id=fact.document_id,
        fact_type=fact.fact_type,
        value=new_value,
        verbatim_span=fact.verbatim_span,
        page_number=fact.page_number,
        bounding_box=fact.bounding_box,
        confidence=1.0,
        status="VERIFIED",
        original_value=fact.value,
        verified_by=_actor(current_user),
        verified_at=datetime.utcnow()
    )
    db.add(new_fact)
    db.commit()
    return {"status": "success", "new_fact_id": new_fact.id, "superseded_id": fact.id}


@router.post("/verification/{fact_id}/reject")
async def reject_fact(
    fact_id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    body = await request.json()
    reason = body.get("reason", "Clinician rejection")

    fact = db.query(ClinicalFact).filter(ClinicalFact.id == fact_id).first()
    if not fact:
        raise HTTPException(404, "Fact not found")
    _check_patient_in_org(db, fact.patient_id, _org_id(current_user))

    fact.status = "REJECTED"
    fact.reject_reason = reason
    fact.verified_by = _actor(current_user)
    fact.verified_at = datetime.utcnow()
    db.commit()
    return {"status": "success", "fact_id": fact.id, "state": fact.status}


@router.post("/verification/bulk-accept")
async def bulk_accept_facts(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    body = await request.json()
    fact_ids = body.get("fact_ids", [])

    open_ctrs = db.query(CCAContradiction).join(
        CCAPatient, CCAContradiction.patient_id == CCAPatient.id
    ).filter(CCAContradiction.status == "OPEN", CCAPatient.organization_id == org_id).all()
    conflicted_ids = set()
    for c in open_ctrs:
        conflicted_ids.update(c.conflicting_fact_ids or [])

    accepted, skipped = [], []
    for fid in fact_ids:
        if fid in conflicted_ids:
            skipped.append({"id": fid, "reason": "Blocked by open contradiction"})
            continue
        fact = db.query(ClinicalFact).join(
            CCAPatient, ClinicalFact.patient_id == CCAPatient.id
        ).filter(ClinicalFact.id == fid, CCAPatient.organization_id == org_id).first()
        if fact and fact.status == "PROPOSED":
            fact.status = "VERIFIED"
            fact.verified_by = _actor(current_user)
            fact.verified_at = datetime.utcnow()
            accepted.append(fid)
        else:
            skipped.append({"id": fid, "reason": "Not found or not in a verifiable state"})

    db.commit()
    return {"accepted": accepted, "skipped": skipped}


@router.get("/patients/{patient_id}/contradictions")
def get_contradictions(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    ctrs = db.query(CCAContradiction).filter(CCAContradiction.patient_id == patient_id).all()
    return {
        "contradictions": [
            {
                "id": c.id,
                "rule_id": c.rule_id,
                "description": c.description,
                "status": c.status,
                "conflicting_fact_ids": c.conflicting_fact_ids,
                "disposition": c.disposition,
                "disposition_note": c.disposition_note,
                "dispositioned_by": c.dispositioned_by
            }
            for c in ctrs
        ]
    }


@router.post("/contradictions/{id}/disposition")
async def resolve_contradiction(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    ctr = db.query(CCAContradiction).filter(CCAContradiction.id == id).first()
    if not ctr:
        raise HTTPException(404, "Contradiction not found")
    _check_patient_in_org(db, ctr.patient_id, _org_id(current_user))

    body = await request.json()
    disposition = body.get("disposition")
    note = body.get("note")
    keep_fact_id = body.get("keep_fact_id")
    if not disposition or not note:
        raise HTTPException(422, "disposition and note are required to resolve a contradiction")

    actor = _actor(current_user)
    ctr.status = "RESOLVED"
    ctr.disposition = disposition
    ctr.disposition_note = note
    ctr.dispositioned_by = actor
    ctr.dispositioned_at = datetime.utcnow()

    if ctr.conflicting_fact_ids:
        for fid in ctr.conflicting_fact_ids:
            f = db.query(ClinicalFact).filter(ClinicalFact.id == fid).first()
            if f:
                if keep_fact_id and f.id == keep_fact_id:
                    f.status = "VERIFIED"
                    f.verified_by = actor
                    f.verified_at = datetime.utcnow()
                elif "Left" in f.value:
                    f.status = "REJECTED"
                    f.reject_reason = "Rejected in favor of pathology-confirmed Right laterality"

    j_ev = CCAJourneyEvent(
        patient_id=ctr.patient_id,
        event_type="CONTRADICTION_RESOLVED",
        event_title="Laterality Contradiction Resolved",
        event_category="INVESTIGATION",
        description=f"{actor} resolved {ctr.rule_id}: {note}",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    return {
        "status": "success",
        "contradiction": {
            "id": ctr.id,
            "rule_id": ctr.rule_id,
            "status": ctr.status,
            "disposition": ctr.disposition
        }
    }


# ---------------------------------------------------------
# 3. Clinical Facts & 2-Click Provenance UX (SCR-10/42)
# ---------------------------------------------------------

@router.get("/clinical-facts/{fact_id}/provenance")
def get_fact_provenance(
    fact_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    fact = db.query(ClinicalFact).filter(ClinicalFact.id == fact_id).first()
    if not fact:
        raise HTTPException(404, "Fact not found")
    _check_patient_in_org(db, fact.patient_id, _org_id(current_user))

    doc = db.query(CCADocument).filter(CCADocument.id == fact.document_id).first() if fact.document_id else None

    return {
        "fact_id": fact.id,
        "fact_type": fact.fact_type,
        "value": fact.value,
        "verbatim_span": fact.verbatim_span,
        "confidence": fact.confidence,
        "page_number": fact.page_number,
        "bounding_box": fact.bounding_box or {"x": 0.15, "y": 0.35, "w": 0.65, "h": 0.08},
        "document": {
            "id": doc.id if doc else None,
            "filename": doc.filename if doc else "Clinician Direct Entry",
            "classification": doc.classification_class if doc else "MANUAL_ENTRY",
            "uploaded_at": doc.uploaded_at.isoformat() if doc and doc.uploaded_at else None
        },
        "verification_history": {
            "status": fact.status,
            "verified_by": fact.verified_by,
            "verified_at": fact.verified_at.isoformat() if fact.verified_at else None,
            "original_value": fact.original_value,
            "reject_reason": fact.reject_reason
        }
    }


@router.get("/patients/{patient_id}/journey")
def get_patient_journey(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    events = db.query(CCAJourneyEvent).filter(
        CCAJourneyEvent.patient_id == patient_id
    ).order_by(CCAJourneyEvent.timestamp.asc()).all()
    return {
        "journey_events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "event_title": e.event_title,
                "event_category": e.event_category,
                "description": e.description,
                "actor_name": e.actor_name,
                "actor_role": e.actor_role,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None
            }
            for e in events
        ]
    }


# ---------------------------------------------------------
# 4. Nurse Intake & Doctor OPD Consultation (SCR-08/09)
# ---------------------------------------------------------

@router.post("/patients/{patient_id}/encounters", status_code=201)
async def open_encounter(
    patient_id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Idempotent: returns the patient's existing OPEN encounter if one exists, else opens a new
    one. Nurse Intake and Doctor OPD Consultation both write against an encounter id (see
    /encounters/{id}/intake and /encounters/{id}/note/finalise below) but previously had no way
    to obtain one outside cca_seed.py's demo seeder."""
    org_id = _org_id(current_user)
    _get_org_patient(db, patient_id, org_id)
    body = await request.json()

    existing = db.query(CCAEncounter).filter(
        CCAEncounter.patient_id == patient_id,
        CCAEncounter.status == "OPEN"
    ).order_by(CCAEncounter.id.desc()).first()
    if existing:
        return {"encounter": {"id": existing.id, "status": existing.status, "note_status": existing.note_status}}

    actor = _actor(current_user)
    encounter = CCAEncounter(
        patient_id=patient_id,
        encounter_type=body.get("encounter_type", "OPD_CONSULTATION"),
        specialty=body.get("specialty", "Medical Oncology"),
        clinician=actor,
        status="OPEN",
        note_status="TRANSCRIPT"
    )
    db.add(encounter)

    j_ev = CCAJourneyEvent(
        patient_id=patient_id,
        event_type="ENCOUNTER_OPENED",
        event_title=f"Encounter Opened ({encounter.encounter_type})",
        event_category="CONSULTATION",
        description=f"{actor} opened a new {encounter.encounter_type} encounter.",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    db.refresh(encounter)
    return {"encounter": {"id": encounter.id, "status": encounter.status, "note_status": encounter.note_status}}


@router.post("/encounters/{id}/intake")
async def complete_nurse_intake(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    body = await request.json()
    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)

    height = _coerce_float(body, "height_cm", 158.0)
    weight = _coerce_float(body, "weight_kg", 64.0)
    bsa, bmi = calculate_bsa(height, weight)
    actor = _actor(current_user)

    intake = CCAIntakeAssessment(
        encounter_id=id,
        patient_id=patient_id,
        height_cm=height,
        weight_kg=weight,
        bmi=bmi,
        bsa=bsa,
        bp_systolic=_coerce_int(body, "bp_systolic", 130),
        bp_diastolic=_coerce_int(body, "bp_diastolic", 80),
        heart_rate=_coerce_int(body, "heart_rate", 74),
        temperature_c=_coerce_float(body, "temperature_c", 36.8),
        oxygen_sat=_coerce_int(body, "oxygen_sat", 99),
        ecog=_coerce_int(body, "ecog", 1),
        karnofsky=_coerce_int(body, "karnofsky", 80),
        pain_score=_coerce_int(body, "pain_score", 0),
        handoff_note=body.get("handoff_note", "Intake complete."),
        recorded_by=actor,
        status="COMPLETED"
    )
    db.add(intake)

    j_ev = CCAJourneyEvent(
        patient_id=intake.patient_id,
        event_type="INTAKE_COMPLETED",
        event_title=f"Nurse Intake Completed (BSA: {bsa} m²)",
        event_category="INTAKE",
        description=f"Vitals, ECOG {intake.ecog}, BSA {bsa} m² recorded. Handoff note generated.",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    db.refresh(intake)
    return {
        "status": "success",
        "intake": {
            "id": intake.id,
            "bsa": intake.bsa,
            "bmi": intake.bmi,
            "bp_systolic": intake.bp_systolic,
            "bp_diastolic": intake.bp_diastolic,
            "heart_rate": intake.heart_rate,
            "ecog": intake.ecog,
            "karnofsky": intake.karnofsky,
            "pain_score": intake.pain_score
        }
    }


@router.post("/encounters/{id}/note/draft")
async def draft_encounter_note(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """AI Scribe for oncology consultations: turns a recorded transcript into a structured
    clinical note draft, mirroring Doctor OPD's POST /api/scribe. Lands on this CCAEncounter's
    own note_content/raw_transcript fields rather than the general Consultation table, since
    oncology encounters are keyed against CCAPatient, a separate patient population from the
    general Patient table /api/scribe writes against."""
    _require_clinician(current_user)
    encounter = db.query(CCAEncounter).filter(CCAEncounter.id == id).first()
    if not encounter:
        raise HTTPException(404, "Encounter not found")
    _check_patient_in_org(db, encounter.patient_id, _org_id(current_user))

    body = await request.json()
    transcript = body.get("transcript")
    if not isinstance(transcript, str) or len(transcript.strip()) < 10:
        raise HTTPException(400, "Transcript too short")

    # Off the event loop -- scribe.scribe_transcript makes a blocking call to the LLM provider;
    # see backend/app/main.py's POST /api/scribe for why this must not block the event loop.
    result = await run_in_threadpool(scribe.scribe_transcript, transcript)
    result["medications"] = drug_matcher.correct_medication_names(result.get("medications", []) or [])

    encounter.raw_transcript = transcript
    encounter.note_content = result
    encounter.note_status = "AI_DRAFT"

    actor = _actor(current_user)
    j_ev = CCAJourneyEvent(
        patient_id=encounter.patient_id,
        event_type="NOTE_AI_DRAFTED",
        event_title="AI Scribe Draft Generated",
        event_category="CONSULTATION",
        description=f"{actor} recorded a consultation and generated an AI-drafted clinical note for review.",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    db.refresh(encounter)
    return {
        "status": "success",
        "encounter": {"id": encounter.id, "status": encounter.status, "note_status": encounter.note_status},
        "note_content": encounter.note_content,
    }


@router.post("/encounters/{id}/note/finalise")
async def finalise_encounter_note(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    encounter = db.query(CCAEncounter).filter(CCAEncounter.id == id).first()
    if not encounter:
        raise HTTPException(404, "Encounter not found")
    _check_patient_in_org(db, encounter.patient_id, _org_id(current_user))

    body = await request.json()
    actor = _actor(current_user)
    if body:
        encounter.note_content = body
    encounter.note_status = "FINAL"
    encounter.status = "CLOSED"
    encounter.ended_at = datetime.utcnow()

    patient = db.query(CCAPatient).filter(CCAPatient.id == encounter.patient_id).first()
    if patient:
        patient.journey_state = "UnderInvestigation"

    j_ev = CCAJourneyEvent(
        patient_id=encounter.patient_id,
        event_type="NOTE_FINALISED",
        event_title="Doctor Consultation Note Finalised",
        event_category="CONSULTATION",
        description=f"{actor} finalized the clinical consultation note and raised staging investigation orders.",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    return {
        "status": "success",
        "encounter": {
            "id": encounter.id,
            "status": encounter.status,
            "note_status": encounter.note_status
        }
    }


# ---------------------------------------------------------
# 5. Orders, Results Inbox & Acknowledgement (SCR-13/14)
# ---------------------------------------------------------

@router.post("/orders")
async def raise_order(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    body = await request.json()
    indication = body.get("clinical_indication")
    if not indication:
        raise HTTPException(422, "clinical_indication is required when raising clinical orders.")
    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)

    order = CCAOrder(
        patient_id=patient_id,
        encounter_id=body.get("encounter_id"),
        order_type=body.get("order_type", "RADIOLOGY"),
        item_name=body.get("item_name", "CECT Chest, Abdomen & Pelvis"),
        item_code=body.get("item_code", "RAD-CT-CAP-01"),
        clinical_indication=indication,
        priority=body.get("priority", "ROUTINE"),
        staging_relevant=body.get("staging_relevant", True),
        status="RAISED",
        requested_by=_actor(current_user)
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return {
        "status": "success",
        "order": {
            "id": order.id,
            "item_name": order.item_name,
            "status": order.status,
            "clinical_indication": order.clinical_indication
        }
    }


@router.get("/results")
def list_results(
    patient_id: Optional[int] = None, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    if patient_id is not None:
        _get_org_patient(db, patient_id, org_id)
        query = db.query(CCAResult).filter(CCAResult.patient_id == patient_id)
    else:
        query = db.query(CCAResult).join(
            CCAPatient, CCAResult.patient_id == CCAPatient.id
        ).filter(CCAPatient.organization_id == org_id)
    results = query.order_by(CCAResult.resulted_at.desc()).all()
    return {
        "results": [
            {
                "id": r.id,
                "order_id": r.order_id,
                "title": r.title,
                "result_type": r.result_type,
                "findings_text": r.findings_text,
                "extracted_values": r.extracted_values,
                "status": r.status,
                "is_critical": r.is_critical,
                "acknowledged_by": r.acknowledged_by,
                "resulted_at": r.resulted_at.isoformat() if r.resulted_at else None
            }
            for r in results
        ]
    }


@router.post("/results/{id}/acknowledge")
def acknowledge_result(
    id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    result = db.query(CCAResult).filter(CCAResult.id == id).first()
    if not result:
        raise HTTPException(404, "Result not found")
    _check_patient_in_org(db, result.patient_id, _org_id(current_user))

    actor = _actor(current_user)
    result.status = "ACKNOWLEDGED"
    result.acknowledged_by = actor
    result.acknowledged_at = datetime.utcnow()

    j_ev = CCAJourneyEvent(
        patient_id=result.patient_id,
        event_type="RESULT_ACKNOWLEDGED",
        event_title=f"Result Acknowledged: {result.title}",
        event_category="INVESTIGATION",
        description=f"{actor} formally acknowledged imaging/lab result in Results Inbox.",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    return {
        "status": "success",
        "result": {
            "id": result.id,
            "status": result.status,
            "title": result.title,
            "acknowledged_by": result.acknowledged_by
        }
    }


# ---------------------------------------------------------
# 6. Staging Workspace & Confirmation (SCR-17)
# ---------------------------------------------------------

@router.get("/patients/{patient_id}/staging/readiness")
def get_staging_readiness(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    return evaluate_staging_readiness(db, patient_id)


@router.get("/patients/{patient_id}/staging")
def get_staging_workspace(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    readiness = evaluate_staging_readiness(db, patient_id)
    facts = db.query(ClinicalFact).filter(
        ClinicalFact.patient_id == patient_id,
        ClinicalFact.status == "VERIFIED"
    ).all()

    diagnosis = db.query(CCACancerDiagnosis).filter(
        CCACancerDiagnosis.patient_id == patient_id
    ).first()

    history = db.query(StagingRecord).filter(
        StagingRecord.patient_id == patient_id
    ).order_by(StagingRecord.version_no.asc()).all()

    return {
        "cancer_context": {
            "site": diagnosis.primary_site if diagnosis else "[NOT_RECORDED]",
            "histology": diagnosis.histology if diagnosis else "[NOT_RECORDED]",
            "grade": diagnosis.grade if diagnosis else "[NOT_RECORDED]"
        },
        "readiness": readiness,
        "evidence_cards": {
            "T": [{"id": f.id, "value": f.value, "verbatim": f.verbatim_span} for f in facts if f.fact_type in ["T_EVIDENCE", "IMAGING_FINDING"]],
            "N": [{"id": f.id, "value": f.value, "verbatim": f.verbatim_span} for f in facts if f.fact_type == "N_EVIDENCE"],
            "M": [{"id": f.id, "value": f.value, "verbatim": f.verbatim_span} for f in facts if f.fact_type == "M_EVIDENCE"],
            "Pathology": [{"id": f.id, "value": f.value, "verbatim": f.verbatim_span} for f in facts if f.fact_type in ["HISTOLOGY", "GRADE"]],
            "Biomarkers": [{"id": f.id, "value": f.value, "verbatim": f.verbatim_span} for f in facts if f.fact_type == "BIOMARKER_RESULT"]
        },
        "history": [
            {
                "id": h.id,
                "prefix": h.classification_prefix,
                "t_stage": h.t_stage,
                "n_stage": h.n_stage,
                "m_stage": h.m_stage,
                "stage_value": h.stage_value,
                "group": h.prognostic_stage_group,
                "status": h.status,
                "confirmed_by": h.confirmed_by,
                "version_no": h.version_no
            }
            for h in history
        ]
    }


@router.post("/patients/{patient_id}/staging/confirm")
async def confirm_stage(
    patient_id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    _require_clinician(current_user)

    body = await request.json()
    for required in ("stage_value", "t_stage", "n_stage", "m_stage", "stage_group"):
        if not body.get(required):
            raise HTTPException(422, f"{required} is required to confirm a clinical stage")

    stage_value = body["stage_value"]
    prefix = body.get("classification_prefix", "c")
    t_stage = body["t_stage"]
    n_stage = body["n_stage"]
    m_stage = body["m_stage"]
    group = body["stage_group"]
    actor = _actor(current_user)

    prior = db.query(StagingRecord).filter(
        StagingRecord.patient_id == patient_id,
        StagingRecord.status == "CLINICIAN_CONFIRMED"
    ).all()
    for p in prior:
        p.status = "SUPERSEDED"

    next_ver = (max([p.version_no for p in prior], default=0)) + 1

    record = StagingRecord(
        patient_id=patient_id,
        staging_system="AJCC Cancer Staging Manual",
        system_version="8th Edition",
        classification_prefix=prefix,
        t_stage=t_stage,
        n_stage=n_stage,
        m_stage=m_stage,
        stage_value=stage_value,
        prognostic_stage_group=group,
        status="CLINICIAN_CONFIRMED",
        confirmed_by=actor,
        confirmed_at=datetime.utcnow(),
        version_no=next_ver,
        change_reason=body.get("change_reason", "Clinical staging confirmation.")
    )
    db.add(record)

    patient = db.query(CCAPatient).filter(CCAPatient.id == patient_id).first()
    if patient:
        patient.journey_state = "Staged"

    j_ev = CCAJourneyEvent(
        patient_id=patient_id,
        event_type="STAGE_CONFIRMED",
        event_title=f"Clinical Stage Confirmed: {stage_value}",
        event_category="STAGING",
        description=f"{actor} formally confirmed AJCC Stage {stage_value} ({group}).",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    db.refresh(record)
    return {
        "status": "success",
        "staging_record": {
            "id": record.id,
            "status": record.status,
            "stage_value": record.stage_value,
            "prefix": record.classification_prefix,
            "group": record.prognostic_stage_group
        }
    }


# ---------------------------------------------------------
# 7. Guideline Readiness & NCCN Context (SCR-19)
# ---------------------------------------------------------

@router.get("/patients/{patient_id}/guidelines/readiness")
def get_guideline_readiness(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    return evaluate_guideline_readiness(db, patient_id)


@router.get("/patients/{patient_id}/guidelines/context")
def get_guideline_context(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    readiness = evaluate_guideline_readiness(db, patient_id)
    if readiness["state"] != "READY":
        raise HTTPException(404, "Guideline context unavailable until Staging is Clinician-Confirmed (Rule G-5).")

    return {
        "guideline_source": "NCCN Clinical Practice Guidelines in Oncology (NCCN Guidelines®)",
        "guideline_version": "Breast Cancer Version 4.2026",
        "pathway_node": "Invasive Breast Cancer: Stage IIA (T2 N0 M0), HR-Positive, HER2-Negative",
        "variables_matched": [
            {"variable": "Histology", "value": "Invasive Ductal Carcinoma"},
            {"variable": "Anatomic Stage", "value": "cT2 cN0 cM0 (Stage IIA)"},
            {"variable": "ER Status", "value": "Positive (80%)"},
            {"variable": "PR Status", "value": "Positive (65%)"},
            {"variable": "HER2 Status", "value": "Negative (1+)"},
            {"variable": "Menopausal Status", "value": "Post-Menopausal (58y)"}
        ],
        "pathway_options": [
            {
                "sequence": "Option 1: Neoadjuvant Systemic Therapy",
                "recommendation": "Dose-dense AC followed by Paclitaxel (AC-T) or TC regimen",
                "clinical_intent": "Facilitate breast conservation, assess in-vivo response (Category 1)"
            },
            {
                "sequence": "Option 2: Upfront Surgery followed by Adjuvant Therapy",
                "recommendation": "Breast Conserving Surgery + SLNB followed by Adjuvant Chemotherapy + Endocrine Therapy",
                "clinical_intent": "Surgical resection with genomic assay consideration (Category 1)"
            }
        ],
        "disclaimer": "NCCN Clinical Context is displayed as a decision-support reference. All treatment selections require Multidisciplinary Discussion and Treating Oncologist Approval."
    }


# ---------------------------------------------------------
# 8. NEXUS Clinical Brief & MDT Board (SCR-20/21/22)
# ---------------------------------------------------------

@router.get("/patients/{patient_id}/clinical-brief")
def get_clinical_brief(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    return synthesize_nexus_brief(db, patient_id)


@router.post("/mdt/cases")
async def create_mdt_case(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    body = await request.json()
    question = body.get("question")
    if not question:
        raise HTTPException(422, "MDT submission requires a clear clinical question.")

    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)
    actor = _actor(current_user)
    brief = synthesize_nexus_brief(db, patient_id)

    mdt_case = MDTCase(
        patient_id=patient_id,
        question=question,
        priority=body.get("priority", "STANDARD"),
        tumor_board=body.get("tumor_board", "Breast Oncology Tumor Board"),
        package_data=brief,
        status="SCHEDULED",
        requested_by=actor,
        scheduled_for=datetime.utcnow() + timedelta(days=2)
    )
    db.add(mdt_case)

    j_ev = CCAJourneyEvent(
        patient_id=patient_id,
        event_type="MDT_CASE_SUBMITTED",
        event_title="1-Click MDT Case Package Submitted",
        event_category="MDT",
        description=f"Case referred to Breast Tumor Board. Question: '{question}'",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    db.refresh(mdt_case)
    return {
        "status": "success",
        "mdt_case": {
            "id": mdt_case.id,
            "question": mdt_case.question,
            "status": mdt_case.status
        }
    }


@router.get("/mdt/cases")
def list_mdt_cases(
    patient_id: Optional[int] = None, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    if patient_id is not None:
        _get_org_patient(db, patient_id, org_id)
        query = db.query(MDTCase).filter(MDTCase.patient_id == patient_id)
    else:
        query = db.query(MDTCase).join(
            CCAPatient, MDTCase.patient_id == CCAPatient.id
        ).filter(CCAPatient.organization_id == org_id)
    cases = query.order_by(MDTCase.id.desc()).all()
    return {
        "mdt_cases": [
            {
                "id": c.id,
                "patient_id": c.patient_id,
                "question": c.question,
                "priority": c.priority,
                "tumor_board": c.tumor_board,
                "status": c.status,
                "requested_by": c.requested_by,
                "scheduled_for": c.scheduled_for.isoformat() if c.scheduled_for else None
            }
            for c in cases
        ]
    }


@router.post("/mdt/cases/{id}/recommendation")
async def record_mdt_recommendation(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    case = db.query(MDTCase).filter(MDTCase.id == id).first()
    if not case:
        raise HTTPException(404, "MDT case not found")
    _check_patient_in_org(db, case.patient_id, _org_id(current_user))
    _require_clinician(current_user)

    body = await request.json()
    rec = body.get("recommendation")
    if not rec:
        raise HTTPException(422, "recommendation is required to record an MDT decision")
    actor = _actor(current_user)

    decision = MDTDecision(
        case_id=case.id,
        patient_id=case.patient_id,
        recommendation=rec,
        modality_direction=body.get("modality_direction"),
        rationale=body.get("rationale"),
        attendees=body.get("attendees"),
        status="FINAL",
        recorded_by=actor,
        recorded_at=datetime.utcnow()
    )
    db.add(decision)
    case.status = "RECOMMENDED"

    j_ev = CCAJourneyEvent(
        patient_id=case.patient_id,
        event_type="MDT_RECOMMENDATION",
        event_title="Tumor Board Recommendation Formulated",
        event_category="MDT",
        description=f"Tumor Board Consensus recorded by {actor}: {rec}",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    db.refresh(decision)
    return {
        "status": "success",
        "decision": {
            "id": decision.id,
            "recommendation": decision.recommendation,
            "status": decision.status
        }
    }


# ---------------------------------------------------------
# 9. Live Care Plan (SCR-23)
# ---------------------------------------------------------

@router.get("/care-plans/prefill")
def get_care_plan_prefill(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    return generate_care_plan_prefill(db, patient_id)


@router.get("/care-plans/current")
def get_current_care_plan(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """The Live Care Plan Hub needs the active plan's id/version to open a real amendment
    against it (PUT /care-plans/{id} below) -- previously only /care-plans/prefill (pre-MDT
    draft generation) and POST/PUT existed, with no way to read back an already-created plan."""
    _get_org_patient(db, patient_id, _org_id(current_user))
    plan = db.query(CarePlan).filter(
        CarePlan.patient_id == patient_id
    ).order_by(CarePlan.id.desc()).first()
    if not plan:
        return {"care_plan": None}
    return {
        "care_plan": {
            "id": plan.id,
            "intent": plan.intent,
            "goals": plan.goals,
            "components": plan.components,
            "monitoring_plan": plan.monitoring_plan,
            "follow_up_plan": plan.follow_up_plan,
            "next_decision_point": plan.next_decision_point,
            "version_no": plan.version_no,
            "status": plan.status
        }
    }


@router.post("/care-plans")
async def create_care_plan(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    body = await request.json()
    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)
    _require_clinician(current_user)
    actor = _actor(current_user)

    plan = CarePlan(
        patient_id=patient_id,
        intent=body.get("intent", "Curative / Neoadjuvant"),
        goals=body.get("goals", []),
        components=body.get("components", {}),
        monitoring_plan=body.get("monitoring_plan", {}),
        follow_up_plan=body.get("follow_up_plan", {}),
        next_decision_point=body.get("next_decision_point"),
        version_no=1,
        status="ACTIVE",
        created_by=actor
    )
    db.add(plan)
    db.flush()

    ver = CarePlanVersion(
        care_plan_id=plan.id,
        version_no=1,
        snapshot=body,
        change_reason="Initial Plan Approval following MDT Consensus",
        created_by=actor
    )
    db.add(ver)

    tx_plan = TreatmentPlan(
        care_plan_id=plan.id,
        patient_id=patient_id,
        modality=body.get("modality", "Systemic Chemotherapy"),
        protocol_name=body.get("protocol_name"),
        planned_sessions=_coerce_int(body, "planned_sessions", 8),
        completed_sessions=0,
        status="ACTIVE"
    )
    db.add(tx_plan)
    db.flush()

    session_1 = TreatmentSession(
        treatment_plan_id=tx_plan.id,
        patient_id=patient_id,
        session_no=1,
        cycle_no=1,
        day_no=1,
        planned_on=datetime.utcnow().date(),
        status="PLANNED"
    )
    db.add(session_1)

    patient = db.query(CCAPatient).filter(CCAPatient.id == patient_id).first()
    if patient:
        patient.journey_state = "PlanApproved"

    j_ev = CCAJourneyEvent(
        patient_id=patient_id,
        event_type="CARE_PLAN_APPROVED",
        event_title="Live Care Plan v1.0 Formally Approved",
        event_category="CARE_PLAN",
        description=f"{actor} approved Live Care Plan v1.0.",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    db.refresh(plan)
    return {
        "status": "success",
        "care_plan": {
            "id": plan.id,
            "intent": plan.intent,
            "version_no": plan.version_no,
            "status": plan.status
        }
    }


@router.put("/care-plans/{id}")
async def update_care_plan(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    plan = db.query(CarePlan).filter(CarePlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Care plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    _require_clinician(current_user)

    body = await request.json()
    change_reason = body.get("change_reason")
    if not change_reason:
        raise HTTPException(422, "Care plan amendments require a mandatory change_reason (E-36).")

    plan.version_no += 1
    plan.components = body.get("components", plan.components)
    plan.intent = body.get("intent", plan.intent)

    ver = CarePlanVersion(
        care_plan_id=plan.id,
        version_no=plan.version_no,
        snapshot=body,
        change_reason=change_reason,
        created_by=_actor(current_user)
    )
    db.add(ver)
    db.commit()
    return {"status": "success", "care_plan": {"id": plan.id, "version_no": plan.version_no}, "version": plan.version_no}


# ---------------------------------------------------------
# 10. Treatment-Day Assessment & 5 Clearance Exits (SCR-24)
# ---------------------------------------------------------

@router.get("/treatment/day-assessment")
def get_treatment_day_assessment(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    patient = _get_org_patient(db, patient_id, _org_id(current_user))
    intake = db.query(CCAIntakeAssessment).filter(CCAIntakeAssessment.patient_id == patient_id).order_by(CCAIntakeAssessment.created_at.desc()).first()
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.patient_id == patient_id, TreatmentPlan.status == "ACTIVE").first()
    toxicities = db.query(ToxicityEvent).filter(ToxicityEvent.patient_id == patient_id).all()

    return {
        "patient": {"name": patient.name, "mrn": patient.mrn, "bsa": intake.bsa if intake else None},
        "protocol": plan.protocol_name if plan else "[NOT_RECORDED] No active treatment plan on record.",
        "cycle_info": f"Cycle {toxicities and len(toxicities) or 1}" if plan else "[NOT_RECORDED]",
        "lab_parameters": [],
        "lab_parameters_note": "Live laboratory integration is not yet connected -- treatment-day lab values must be reviewed directly in the lab system before clearance.",
        "toxicity_history": [
            {
                "id": t.id,
                "term": t.term,
                "grade": t.grade,
                "baseline_value": t.baseline_value
            }
            for t in toxicities
        ],
        "clearance_exits": [
            {"code": "CLEARED", "label": "Proceed with Standard Dose (100%)", "requires_task": False},
            {"code": "CLEARED_DOSE_REDUCTION", "label": "Cleared with Dose Reduction (e.g. 75% / 80%)", "requires_task": False},
            {"code": "HELD", "label": "Hold Treatment (Toxicity / Cytopenia)", "requires_task": True},
            {"code": "DEFERRED", "label": "Defer to Future Date", "requires_task": True},
            {"code": "DISCONTINUED", "label": "Discontinue Regimen", "requires_task": True}
        ]
    }


@router.post("/treatment/toxicity")
async def record_toxicity(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    body = await request.json()
    baseline = body.get("baseline_value")
    if baseline is None:
        raise HTTPException(422, "Toxicity recording requires baseline_value (NOT NULL).")
    term = body.get("term")
    if not term:
        raise HTTPException(422, "term is required to record a toxicity event.")
    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)

    tox = ToxicityEvent(
        patient_id=patient_id,
        term=term,
        grade=_coerce_int(body, "grade", 0),
        baseline_value=str(baseline),
        grading_standard="CTCAE v5.0",
        onset_date=datetime.utcnow().date()
    )
    db.add(tox)
    db.commit()
    db.refresh(tox)
    return {
        "status": "success",
        "toxicity": {
            "id": tox.id,
            "term": tox.term,
            "grade": tox.grade,
            "baseline_value": tox.baseline_value
        }
    }


@router.post("/treatment/clearance")
async def record_clearance_decision(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    _require_clinician(current_user)
    body = await request.json()
    decision = body.get("decision")
    reason = body.get("reason")
    if not decision:
        raise HTTPException(422, "decision is required to record a treatment clearance.")
    if not reason:
        raise HTTPException(422, "reason is required to record a treatment clearance.")
    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)
    actor = _actor(current_user)

    session = db.query(TreatmentSession).filter(TreatmentSession.patient_id == patient_id).order_by(TreatmentSession.id.desc()).first()

    clearance = TreatmentClearance(
        session_id=session.id if session else None,
        patient_id=patient_id,
        decision=decision,
        reason=reason,
        reassess_on=(datetime.utcnow() + timedelta(days=7)).date() if decision in ["HELD", "DEFERRED"] else None,
        task_owner_id=body.get("task_owner_id", actor),
        decided_by=actor,
        decided_at=datetime.utcnow()
    )
    if clearance.session_id is None:
        raise HTTPException(422, "No treatment session on record for this patient -- cannot record a clearance decision.")
    db.add(clearance)

    if decision in ["HELD", "DEFERRED"]:
        care_plan_id = None
        if session and session.treatment_plan_id:
            tx_plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == session.treatment_plan_id).first()
            care_plan_id = tx_plan.care_plan_id if tx_plan else None
        if care_plan_id is not None:
            task = CarePlanTask(
                care_plan_id=care_plan_id,
                patient_id=patient_id,
                description=f"Reassessment following Treatment Hold: {reason}",
                owner_id=str(current_user.get("id")),
                owner_name=actor,
                due_date=datetime.utcnow() + timedelta(days=7),
                status="OPEN"
            )
            db.add(task)
        if session:
            session.status = "HELD"
    elif decision == "CLEARED":
        if session:
            session.status = "ADMINISTERED"
            session.administered_on = datetime.utcnow()
            session.administered_by = actor

    j_ev = CCAJourneyEvent(
        patient_id=patient_id,
        event_type="TREATMENT_CLEARANCE",
        event_title=f"Treatment Clearance: {decision}",
        event_category="TREATMENT",
        description=f"Decision by {actor}: {decision}. Reason: {reason}",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    db.refresh(clearance)
    return {
        "status": "success",
        "clearance": {
            "id": clearance.id,
            "decision": clearance.decision,
            "reason": clearance.reason,
            "reassess_on": clearance.reassess_on.isoformat() if clearance.reassess_on else None
        }
    }


# ---------------------------------------------------------
# 11. Follow-Up & RECIST Response Assessment (SCR-25)
# ---------------------------------------------------------

@router.post("/response-assessments")
async def record_response_assessment(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    org_id = _org_id(current_user)
    body = await request.json()
    category = body.get("response_category")
    if not category:
        raise HTTPException(422, "response_category is required to record a RECIST assessment.")
    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)
    actor = _actor(current_user)

    resp = ResponseAssessment(
        patient_id=patient_id,
        framework="RECIST",
        framework_version="1.1",
        response_category=category,
        confirmed=body.get("confirmed", True),
        lesions=body.get("lesions"),
        imaging_reference=body.get("imaging_reference"),
        recorded_by=actor,
        recorded_at=datetime.utcnow()
    )
    db.add(resp)

    j_ev = CCAJourneyEvent(
        patient_id=patient_id,
        event_type="RESPONSE_ASSESSED",
        event_title=f"Response Assessment Recorded: {category}",
        event_category="FOLLOW_UP",
        description=f"RECIST 1.1 evaluation recorded by {actor}.",
        actor_name=actor,
        actor_role=current_user.get("role")
    )
    db.add(j_ev)
    db.commit()
    db.refresh(resp)
    return {
        "status": "success",
        "response": {
            "id": resp.id,
            "response_category": resp.response_category,
            "framework": resp.framework,
            "confirmed": resp.confirmed
        }
    }


# ---------------------------------------------------------
# 12. Demo Simulation & Presenter Controls (SCR-27)
#
# Admin-only: these endpoints mutate/reset demo data wholesale and must never
# be reachable by an unauthenticated caller or a non-admin user.
# ---------------------------------------------------------

@router.post("/demo/simulate-result")
def demo_simulate_result(
    patient_id: int = 1, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    if not is_admin(current_user):
        raise HTTPException(403, "Admin only")
    _get_org_patient(db, patient_id, _org_id(current_user))
    result = simulate_ct_result(db, patient_id)
    return {"status": "success", "result": {"id": result.id, "title": result.title, "status": result.status} if result else None, "message": "CECT Staging Result Simulated (cM0). Staging Readiness flipped to READY."}


@router.post("/demo/advance-clock")
async def demo_advance_clock(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    if not is_admin(current_user):
        raise HTTPException(403, "Admin only")
    org_id = _org_id(current_user)
    body = await request.json()
    target_day = body.get("target_day", "D+7")
    patient_id = _require_patient_id(body)
    patient = _get_org_patient(db, patient_id, org_id)

    if target_day == "D+7":
        patient.journey_state = "InTreatment"
        j_ev = CCAJourneyEvent(
            patient_id=patient_id,
            event_type="CLOCK_ADVANCED",
            event_title="Demo Clock Advanced to D+7 (Treatment Day)",
            event_category="TREATMENT",
            description="Patient arrives for Cycle 1 Chemotherapy Administration.",
            actor_name=_actor(current_user),
            actor_role="Demo Controller"
        )
        db.add(j_ev)
    elif target_day == "D+21":
        patient.journey_state = "InFollowUp"
        j_ev = CCAJourneyEvent(
            patient_id=patient_id,
            event_type="CLOCK_ADVANCED",
            event_title="Demo Clock Advanced to D+21 (Interim Response Evaluation)",
            event_category="FOLLOW_UP",
            description="Patient presents for interim clinical and imaging response assessment.",
            actor_name=_actor(current_user),
            actor_role="Demo Controller"
        )
        db.add(j_ev)

    db.commit()
    return {"status": "success", "current_stage": target_day}


@router.post("/demo/reset")
def demo_reset(
    db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    if not is_admin(current_user):
        raise HTTPException(403, "Admin only")
    org_id = _org_id(current_user)
    patient = seed_cca_database(db, force_reset=True, organization_id=org_id)
    return {"status": "success", "message": "Demo database successfully reset to clean D-0 baseline.", "patient_id": patient.id}
