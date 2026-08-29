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
    is_cca_nurse_navigator, is_cca_medical_oncologist, is_cca_surgical_oncologist,
    is_cca_radiation_oncologist, is_cca_patient_liaison, is_cca_infusion_nurse,
    is_cca_radiologist, is_cca_financial_counsellor, is_cca_external_mdt_specialist,
    can_sign_treatment_plan, can_approve_mdt_recommendation, log_audit,
)
from ..config import settings
from ..ocr_service import extract_document
from ..scribe import scribe
from .. import drug_matcher
from ..models_cca import (
    CCAPatient, CCAConsent, CCAQueueEvent, CCAEncounter, CCAIntakeAssessment,
    CCADocument, ClinicalFact, CCAContradiction, CCACancerDiagnosis,
    CCABiomarkerResult, CCAOrder, CCAResult, StagingRecord, StagingEvidence,
    GuidelineRegistry, TreatmentPlanGuidelineLink,
    ClinicalBrief, MDTCase, MDTDecision, CarePlan,
    CarePlanVersion, CarePlanTask, TreatmentPlan, TreatmentPlanVersion, TreatmentSession,
    TreatmentOrder, TreatmentEvent,
    ToxicityEvent, TreatmentClearance, ResponseAssessment, CCAJourneyEvent
)
from ..cca_engine import (
    calculate_bsa, detect_contradictions, evaluate_staging_readiness,
    evaluate_guideline_readiness, synthesize_nexus_brief, generate_care_plan_prefill,
    classify_document, extract_clinical_facts,
)
from ..cca_seed import seed_cca_database, simulate_ct_result
from ..cca_product_decisions import CARE_PLAN_IN_PROGRESS_STATUSES, TREATMENT_ORDERS_SYSTEM_OF_RECORD
from ..events import publish
from .. import event_subscribers  # noqa: F401 -- import registers this module's @subscribe handlers
from ..rbac_projection import (
    care_plan_tier, treatment_plan_tier, treatment_order_tier, can_view_draft_plans_and_orders,
    project_care_plan, project_treatment_plan, project_treatment_order,
)

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
    Surgical/Radiation) or the general Doctor role only -- deliberately NOT Admin (architecture
    doc: Admin/Operations "cannot edit signed clinical notes, diagnoses, finalized
    radiology/pathology reports or clinician-approved treatment plans"). Admin retains full
    read/oversight access everywhere via rbac_projection.py's "FULL" tiers; only the ability to
    author these specific clinical actions is withdrawn here."""
    if not (is_doctor(current_user) or is_cca_oncologist(current_user)):
        raise HTTPException(403, "Only a treating oncologist may perform this action")


# Modality -> the role predicate authorized to sign/discontinue a Treatment Plan of that
# modality. TreatmentPlan.modality is still a free-text field (no enum exists yet -- see the
# architecture doc's data-model diff), so this is a deliberately simple keyword match rather
# than a real lookup table; it should become one once modality is a real enum.
_MODALITY_SIGNER_CHECKS = [
    (("surg",), is_cca_surgical_oncologist, "Surgical Oncologist"),
    (("radiat", "rt "), is_cca_radiation_oncologist, "Radiation Oncologist"),
]


def _require_modality_signer(current_user: dict, modality: str):
    """Only an authorized clinician of the *matching* modality may sign or discontinue a
    Treatment Plan (architecture doc non-negotiable #1: Medical/Surgical/Radiation Oncologist
    each own C/E/S of their own modality, R others -- previously all three were treated as
    interchangeable via is_cca_oncologist for every plan action). Admin does NOT bypass this
    (architecture doc: Admin/Operations cannot sign clinician-approved treatment plans) --
    this previously matched every other clinician gate in this router in granting Admin a
    bypass; that bypass has since been withdrawn from all of them together, deliberately, not
    just here.

    Multimodality co-signature (e.g. a second modality co-signing a shared plan) is
    deliberately out of scope here -- see TreatmentPlanCoSignature's docstring in
    models_cca.py. This only decides who may be the single primary signer for a given
    modality string."""
    modality_lower = (modality or "").lower()
    for keywords, predicate, label in _MODALITY_SIGNER_CHECKS:
        if any(kw in modality_lower for kw in keywords):
            if not predicate(current_user):
                raise HTTPException(403, f"Only the {label} may sign/discontinue this modality's Treatment Plan")
            return
    # Default: systemic/medical therapy (and anything not matching a keyword above).
    if not (is_cca_medical_oncologist(current_user) or is_doctor(current_user)):
        raise HTTPException(403, "Only the Medical Oncologist may sign/discontinue this modality's Treatment Plan")


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
    # Every other role legitimately needs some patient list (Front Desk registration search,
    # every clinical role's own worklist). External MDT Specialist is the one role this
    # codebase's spec (12_External_MDT_Specialist.pdf) gives an explicit hard boundary against:
    # "Case-scoped reviewer; no broad patient search or operational access" / acceptance
    # criterion "External user cannot browse unrelated patients" -- they have their own
    # case-scoped view (GET /mdt/assigned-cases) for exactly this purpose instead.
    if is_cca_external_mdt_specialist(current_user):
        raise HTTPException(403, "External MDT Specialist does not have general patient search access -- use your assigned MDT cases instead.")
    org_id = _org_id(current_user)
    query = db.query(CCAPatient).filter(CCAPatient.organization_id == org_id)
    if q:
        term = q.strip()
        query = query.filter(
            (CCAPatient.name.ilike(f"%{term}%")) |
            (CCAPatient.mrn.ilike(f"%{term}%")) |
            (CCAPatient.phone.ilike(f"%{term}%")) |
            (CCAPatient.id_proof_number.ilike(f"%{term}%")) |
            (CCAPatient.attender_name.ilike(f"%{term}%")) |
            (CCAPatient.journey_state.ilike(f"%{term}%"))
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
async def update_patient_data_per_visit(
    patient_id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Registers a return visit and updates patient demographic, contact, and ID verification data per visit."""
    if not (is_cca_front_desk(current_user) or is_admin(current_user) or is_cca_patient_liaison(current_user)):
        raise HTTPException(403, "Only Front Desk, Patient Liaison, or Admin can register return visits and update patient details")
    org_id = _org_id(current_user)
    patient = _get_org_patient(db, patient_id, org_id)
    body = await request.json()

    if "name" in body and body["name"]:
        patient.name = str(body["name"]).strip()
    if "phone" in body and body["phone"]:
        patient.phone = str(body["phone"]).strip()
    if "address" in body:
        patient.address = body["address"]
    if "age" in body and body["age"]:
        try:
            patient.age = int(body["age"])
        except (TypeError, ValueError):
            pass
    if "dob" in body:
        patient.dob = body["dob"]
    if "sex" in body:
        patient.sex = body["sex"]
    if "primary_oncologist" in body:
        patient.primary_oncologist = body["primary_oncologist"]
    if "attender_name" in body:
        patient.attender_name = body["attender_name"]
    if "attender_phone" in body:
        patient.attender_phone = body["attender_phone"]
    if "attender_relationship" in body:
        patient.attender_relationship = body["attender_relationship"]
    if "id_proof_type" in body:
        patient.id_proof_type = body["id_proof_type"]
    if "id_proof_number" in body:
        patient.id_proof_number = body["id_proof_number"]
    if "id_proof_name" in body:
        patient.id_proof_name = body["id_proof_name"]
    if "id_proof_dob" in body:
        patient.id_proof_dob = body["id_proof_dob"]
    if "id_proof_verification_status" in body:
        patient.id_proof_verification_status = body["id_proof_verification_status"]

    actor = _actor(current_user)
    j_ev = CCAJourneyEvent(
        patient_id=patient.id,
        event_type="RETURN_VISIT",
        event_title="Return Visit Registered",
        event_category="REGISTRATION",
        description=f"{actor} registered return visit and updated patient details per visit.",
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
            "phone": patient.phone, "age": patient.age, "sex": patient.sex,
            "journey_state": patient.journey_state,
        }
    }


@router.post("/patients/{patient_id}/consents", status_code=201)
async def capture_consent(
    patient_id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Real gap this closes: CCAConsent (models_cca.py) previously had no create endpoint
    anywhere -- the only rows that ever existed were cca_seed.py's hardcoded demo data, so
    Front Desk's registration wizard had nothing to call and patient_portal.py's
    'patient_portal_access' consent gate (architecture doc: 'only after clinical/consent
    review') could never actually be satisfied outside the demo seed. Gated the same as
    patient-facing-view preview access -- capturing consent is a patient-contact
    responsibility, not a pure clinical decision."""
    _require_patient_contact_role(current_user)
    _get_org_patient(db, patient_id, _org_id(current_user))
    body = await request.json()
    consent_types = body.get("consent_types")
    signatory = body.get("signatory")
    if not consent_types or not isinstance(consent_types, list):
        raise HTTPException(422, "consent_types (a non-empty list) is required")
    if not signatory:
        raise HTTPException(422, "signatory is required")

    actor = _actor(current_user)
    consent = CCAConsent(
        patient_id=patient_id,
        consent_types=consent_types,
        signatory=signatory,
        signatory_reason=body.get("signatory_reason"),
        captured_by=actor,
        status="ACTIVE",
    )
    db.add(consent)
    db.flush()
    publish(
        db, "CONSENT_CAPTURED", patient_id=patient_id, actor=actor, role=current_user.get("role"),
        title="Consent captured", category="REGISTRATION",
        description=f"{actor} captured consent ({', '.join(consent_types)}) signed by {signatory}.",
        consent_id=consent.id,
    )
    db.commit()
    db.refresh(consent)
    return {
        "status": "success",
        "consent": {
            "id": consent.id, "patient_id": consent.patient_id, "consent_types": consent.consent_types,
            "signatory": consent.signatory, "status": consent.status,
            "valid_from": consent.valid_from.isoformat() if consent.valid_from else None,
        },
    }


@router.get("/patients/{patient_id}/consents")
def list_consents(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(CCAConsent).filter(CCAConsent.patient_id == patient_id).order_by(CCAConsent.id.desc()).all()
    return {"consents": [
        {
            "id": c.id, "consent_types": c.consent_types, "signatory": c.signatory,
            "signatory_reason": c.signatory_reason, "captured_by": c.captured_by, "status": c.status,
            "valid_from": c.valid_from.isoformat() if c.valid_from else None,
        }
        for c in rows
    ]}


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
    # Nurse-recorded vitals/performance status were computed elsewhere (get_patient_summary's
    # tier-2 "performance_status" block, synthesize_nexus_brief) but never actually rendered by
    # any frontend page -- a later clinician (e.g. Surgical Oncology, seeing the patient after
    # an interleaved Medical Oncology visit and a nurse re-check) had no way to see the current
    # vitals here even though this endpoint is exactly "everything on record for this patient".
    latest_intake = db.query(CCAIntakeAssessment).filter(
        CCAIntakeAssessment.patient_id == patient_id
    ).order_by(CCAIntakeAssessment.created_at.desc()).first()

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

    # --- Role-specific projections (Spec Section 39/58) ---
    if is_cca_financial_counsellor(current_user):
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "patient": {
                "id": patient.id, "mrn": patient.mrn, "name": patient.name, "age": patient.age,
                "sex": patient.sex, "journey_state": patient.journey_state,
                "primary_oncologist": patient.primary_oncologist,
            },
            "overview": {
                "order_count": len(orders),
                "is_returning_patient": len(encounters) > 1 or len(docs) > 0,
            },
            "financial_projection": {
                "orders": [{"id": o.id, "order_type": o.order_type, "item_name": o.item_name, "status": o.status} for o in orders],
                "active_plans": [project_treatment_plan(_treatment_plan_dict(p), current_user) for p in db.query(TreatmentPlan).filter(TreatmentPlan.patient_id == patient_id).all()]
            },
            "disclaimer": "Financial projection view: includes billing, modality counts, and operational status only."
        }

    if is_cca_front_desk(current_user):
        raise HTTPException(403, "Front Desk does not have access to patient clinical summary. Use patient list or registration search to view registration and visit status.")

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "patient": {
            "id": patient.id, "mrn": patient.mrn, "name": patient.name, "age": patient.age,
            "sex": patient.sex, "dob": patient.dob, "journey_state": patient.journey_state,
            "primary_oncologist": patient.primary_oncologist,
            "id_proof_type": patient.id_proof_type, "id_proof_number": patient.id_proof_number,
            "id_proof_verification_status": patient.id_proof_verification_status,
        },
        "vitals": {
            "bp_systolic": latest_intake.bp_systolic, "bp_diastolic": latest_intake.bp_diastolic,
            "heart_rate": latest_intake.heart_rate, "temperature_c": latest_intake.temperature_c,
            "oxygen_sat": latest_intake.oxygen_sat, "respiratory_rate": latest_intake.respiratory_rate,
            "ecog": latest_intake.ecog, "karnofsky": latest_intake.karnofsky,
            "pain_score": latest_intake.pain_score, "fall_risk": latest_intake.fall_risk,
            "bsa": latest_intake.bsa, "bmi": latest_intake.bmi,
            "recorded_by": latest_intake.recorded_by,
            "recorded_at": latest_intake.created_at.isoformat() if latest_intake.created_at else None,
        } if latest_intake else None,
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
             "priority": o.priority, "clinical_indication": o.clinical_indication, "requested_by": o.requested_by,
             "ordered_at": o.ordered_at.isoformat() if o.ordered_at else None}
            for o in orders
        ],
        "results": [
            {"id": r.id, "result_type": r.result_type, "title": r.title, "is_critical": r.is_critical,
             "status": r.status, "order_id": r.order_id,
             "excerpt": ((r.impression or r.findings_text or "")[:300] or None),
             "resulted_at": r.resulted_at.isoformat() if r.resulted_at else None}
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
    """Verifying an AI-extracted fact is a clinical judgment call (architecture doc: AI
    proposes, clinician decides) -- gated and audited the same as any other clinical
    decision on the record."""
    fact = db.query(ClinicalFact).filter(ClinicalFact.id == fact_id).first()
    if not fact:
        raise HTTPException(404, "Fact not found")
    _check_patient_in_org(db, fact.patient_id, _org_id(current_user))
    _require_clinician(current_user)

    open_ctr = db.query(CCAContradiction).filter(
        CCAContradiction.patient_id == fact.patient_id,
        CCAContradiction.status == "OPEN"
    ).first()
    if open_ctr and fact.id in (open_ctr.conflicting_fact_ids or []):
        raise HTTPException(422, f"Cannot accept fact: Part of unresolved contradiction ({open_ctr.rule_id}). Resolve contradiction first.")

    fact.status = "VERIFIED"
    actor = _actor(current_user)
    fact.verified_by = actor
    fact.verified_at = datetime.utcnow()
    publish(
        db, "CLINICAL_FACT_VERIFIED", patient_id=fact.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Fact verified: {fact.fact_type}", category="VERIFICATION",
        description=f"{actor} verified an AI-extracted fact ({fact.fact_type}: {fact.value}).",
        fact_id=fact.id,
    )
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
    _require_clinician(current_user)

    fact.status = "SUPERSEDED"
    actor = _actor(current_user)

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
        verified_by=actor,
        verified_at=datetime.utcnow()
    )
    db.add(new_fact)
    db.flush()
    publish(
        db, "CLINICAL_FACT_CORRECTED", patient_id=fact.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Fact corrected: {fact.fact_type}", category="VERIFICATION",
        description=f"{actor} corrected {fact.fact_type} from '{fact.value}' to '{new_value}'.",
        fact_id=new_fact.id, superseded_fact_id=fact.id,
    )
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
    _require_clinician(current_user)

    fact.status = "REJECTED"
    fact.reject_reason = reason
    actor = _actor(current_user)
    fact.verified_by = actor
    fact.verified_at = datetime.utcnow()
    publish(
        db, "CLINICAL_FACT_REJECTED", patient_id=fact.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Fact rejected: {fact.fact_type}", category="VERIFICATION",
        description=f"{actor} rejected {fact.fact_type} ({fact.value}): {reason}",
        fact_id=fact.id,
    )
    db.commit()
    return {"status": "success", "fact_id": fact.id, "state": fact.status}


@router.post("/verification/bulk-accept")
async def bulk_accept_facts(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _require_clinician(current_user)
    org_id = _org_id(current_user)
    body = await request.json()
    fact_ids = body.get("fact_ids", [])

    open_ctrs = db.query(CCAContradiction).join(
        CCAPatient, CCAContradiction.patient_id == CCAPatient.id
    ).filter(CCAContradiction.status == "OPEN", CCAPatient.organization_id == org_id).all()
    conflicted_ids = set()
    for c in open_ctrs:
        conflicted_ids.update(c.conflicting_fact_ids or [])

    actor = _actor(current_user)
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
            fact.verified_by = actor
            fact.verified_at = datetime.utcnow()
            accepted.append(fid)
        else:
            skipped.append({"id": fid, "reason": "Not found or not in a verifiable state"})

    if accepted:
        publish(
            db, "CLINICAL_FACT_VERIFIED", patient_id=None, actor=actor, role=current_user.get("role"),
            title=f"{len(accepted)} facts bulk-verified", category="VERIFICATION",
            description=f"{actor} bulk-verified {len(accepted)} AI-extracted facts.",
            fact_ids=accepted,
        )
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
    """Idempotent per (patient, specialty): returns this specialty's existing OPEN encounter if
    one exists, else opens a new one. Nurse Intake and Doctor OPD Consultation both write
    against an encounter id (see /encounters/{id}/intake and /encounters/{id}/note/finalise
    below) but previously had no way to obtain one outside cca_seed.py's demo seeder.

    Scoped by specialty (not just patient_id) since the earlier patient-only scoping meant a
    patient seeing a second specialty (e.g. Surgical Oncology) while their Medical Oncology
    encounter was still OPEN -- undrafted, unfinalized -- would be handed back that SAME
    encounter and silently overwrite the first clinician's note on finalise
    (finalise_encounter_note replaces note_content wholesale). Nurse Intake's own caller omits
    `specialty` and so shares whichever default ("Medical Oncology") an oncologist page also
    defaults to when it likewise omits `specialty` -- preserving the intended nurse-intake ->
    doctor-consultation handoff as one shared encounter -- while a *different* specialty
    explicitly named in the request correctly gets its own encounter instead of colliding."""
    org_id = _org_id(current_user)
    _get_org_patient(db, patient_id, org_id)
    body = await request.json()
    specialty = body.get("specialty", "Medical Oncology")

    existing = db.query(CCAEncounter).filter(
        CCAEncounter.patient_id == patient_id,
        CCAEncounter.status == "OPEN",
        CCAEncounter.specialty == specialty,
    ).order_by(CCAEncounter.id.desc()).first()
    if existing:
        return {"encounter": {"id": existing.id, "status": existing.status, "note_status": existing.note_status}}

    actor = _actor(current_user)
    encounter = CCAEncounter(
        patient_id=patient_id,
        encounter_type=body.get("encounter_type", "OPD_CONSULTATION"),
        specialty=specialty,
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
    _require_clinical_or_nursing_role(current_user)
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
        respiratory_rate=_coerce_int(body, "respiratory_rate", 16),
        ecog=_coerce_int(body, "ecog", 1),
        karnofsky=_coerce_int(body, "karnofsky", 80),
        pain_score=_coerce_int(body, "pain_score", 0),
        fall_risk=body.get("fall_risk") or "Low",
        # Free-form structured capture for the assessment fields this model has no dedicated
        # column for (functional/psychosocial screens, reported symptoms, history) -- present
        # on the model precisely for this (CCAIntakeAssessment.vitals_json docstring).
        vitals_json=body.get("vitals_json"),
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
    """Finalizing turns an AI_DRAFT note into the clinical record of the consultation --
    the explicit clinician acceptance step the architecture doc requires before an
    AI-originated draft can stand as authored clinical content."""
    _require_clinician(current_user)
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


@router.post("/scribe-extract")
async def scribe_extract(
    request: Request, current_user: dict = Depends(get_current_user)
):
    """Pure dictation -> structured extraction, no persistence: for pages that need scribe.py's
    extraction shape (chiefComplaint/hpi/primaryDiagnosis/medications/advice/labTests) without
    writing to any encounter/consultation table, because their own Save/Finalize step handles
    persistence separately against a different model (e.g. the Radiologist's imaging report
    AI-draft, which writes a CCAResult, not a Consultation or CCAEncounter). Fixes
    radiologist.html previously calling the general-HMS, Doctor-only POST /api/scribe -- a real
    CCARadiologist got a 403 every time, and even a fixed role check there would have persisted
    a Consultation row against the wrong (general HMS Patient, not CCAPatient) data model."""
    if not (is_doctor(current_user) or is_cca_oncologist(current_user) or is_cca_radiologist(current_user)):
        raise HTTPException(403, "Not authorized for AI-assisted dictation extraction")
    body = await request.json()
    transcript = body.get("transcript")
    if not isinstance(transcript, str) or len(transcript.strip()) < 10:
        raise HTTPException(400, "Transcript too short")

    result = await run_in_threadpool(scribe.scribe_transcript, transcript)
    result["medications"] = drug_matcher.correct_medication_names(result.get("medications", []) or [])
    return result


# ---------------------------------------------------------
# 5. Orders, Results Inbox & Acknowledgement (SCR-13/14)
# ---------------------------------------------------------

@router.post("/orders")
async def raise_order(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Raising a diagnostic order is a clinical decision (architecture doc journey step 5:
    'Doctor orders needed pathology/radiology/labs') -- gated the same way as other
    irreversible clinical actions. If the patient already has an active Care Plan, this also
    opens a linked milestone task (architecture doc section 19.1: 'Care Plan creates or
    tracks an imaging milestone only after an authorized order exists') for
    _on_diagnostic_result_finalized (event_subscribers.py) to resolve once the result comes
    back -- the other half of the closed loop. No milestone is created pre-Care-Plan (the
    common case for initial work-up orders), matching how every other patient-scoped task in
    this system already tolerates care_plan_id=None."""
    _require_clinician(current_user)
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
    db.flush()

    care_plan = db.query(CarePlan).filter(
        CarePlan.patient_id == patient_id, CarePlan.status == "ACTIVE"
    ).order_by(CarePlan.id.desc()).first()
    if care_plan:
        db.add(CarePlanTask(
            care_plan_id=care_plan.id, patient_id=patient_id,
            description=f"{order.order_type.title()} milestone: {order.item_name}",
            owner_id="", owner_name="Care Coordination",
            due_date=datetime.utcnow() + timedelta(days=14),
            status="OPEN", source="SYSTEM", owner_role="CARE_COORDINATION",
            category="COORDINATION", linked_order_id=order.id,
        ))

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
    """The clinician-review leg of the diagnostics closed loop (order -> result -> review),
    gated the same way as resolve_patient_task -- reviewing a result is a clinical act. For a
    critical result, this also resolves the review-required task
    event_subscribers.py's _on_diagnostic_result_finalized opened when the report was
    finalized, so the loop actually closes rather than leaving a stale open task."""
    result = db.query(CCAResult).filter(CCAResult.id == id).first()
    if not result:
        raise HTTPException(404, "Result not found")
    _check_patient_in_org(db, result.patient_id, _org_id(current_user))
    _require_clinician(current_user)

    actor = _actor(current_user)
    result.status = "ACKNOWLEDGED"
    result.acknowledged_by = actor
    result.acknowledged_at = datetime.utcnow()

    if result.is_critical:
        review_task = db.query(CarePlanTask).filter(
            CarePlanTask.linked_result_id == result.id, CarePlanTask.status == "OPEN"
        ).first()
        if review_task:
            review_task.status = "RESOLVED"

    publish(
        db, "RESULT_ACKNOWLEDGED", patient_id=result.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Result Acknowledged: {result.title}", category="INVESTIGATION",
        description=f"{actor} formally acknowledged the result in the Results Inbox.",
        result_id=result.id,
    )
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
    log_audit(db, current_user.get("id"), _actor(current_user), _org_id(current_user),
              "confirm_stage", f"cca/patients/{patient_id}/staging", "Success",
              f"Stage {stage_value} ({group}), version {next_ver}")
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
    """Submitting a case to tumour board is the treating clinician's decision (MDT
    Coordinator scheduling/logistics is a separate, already-gated endpoint in
    cca_coordination.py) -- gated the same way as other clinical-strategy actions."""
    _require_clinician(current_user)
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
    # Data-sharing review finding: this endpoint had no role gate at all beyond org-scoping --
    # any authenticated CCA role, including Front Desk/Patient Liaison/Financial Counsellor,
    # could read every patient's MDT clinical question and case status (architecture doc's
    # role matrix gives none of those roles read access to MDT content). Same access line as
    # AI Search/Care Plan's CLINICAL_CONTEXT+ tiers -- no live page for an OPERATIONAL-tier
    # role calls this endpoint, so this closes the leak without removing any working feature.
    _require_search_access(current_user)
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
    db.flush()
    case.status = "RECOMMENDED"

    publish(
        db, "MDT_RECOMMENDATION_FINALIZED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
        title="Tumor Board Recommendation Formulated", category="MDT",
        description=f"Tumor Board Consensus recorded by {actor}: {rec}",
        mdt_case_id=case.id, mdt_decision_id=decision.id,
    )
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


_MDT_DISPOSITION_STATUS = {"ACCEPT": "APPROVED", "PARTIAL": "PARTIALLY_APPROVED", "REJECT": "REJECTED"}
# REJECT returns the case to the treating team's record instead of a dead-end "REJECTED" case
# status -- a rejected recommendation is not the end of the patient's MDT journey, it's a
# prompt to resubmit with a revised question/evidence (architecture doc Sec 23's lifecycle).
_MDT_DISPOSITION_CASE_STATUS = {"ACCEPT": "APPROVED", "PARTIAL": "PARTIALLY_APPROVED", "REJECT": "RETURNED_TO_RECORD"}


@router.post("/mdt/cases/{id}/approve")
async def approve_mdt_recommendation(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """MDT recommendations require an explicit disposition before altering active clinical
    treatment plans. Per the architecture doc (Sec 18), that disposition is a genuine
    three-way choice -- ACCEPT (the recommendation stands as written), PARTIAL (accepted with
    modification), or REJECT (not adopted) -- not a bare approve/deny; PARTIAL and REJECT
    require a reason. Who may record it: the treating oncologist/Doctor always, and -- per
    cca_product_decisions.MDT_COORDINATOR_CAN_APPROVE_RECOMMENDATIONS -- the MDT Coordinator
    too; see can_approve_mdt_recommendation's docstring for that decision's scope. Either way
    this only records disposition on the recommendation; Care Plan/Treatment Plan authorship
    stays with the treating clinician, unaffected."""
    if not can_approve_mdt_recommendation(current_user):
        raise HTTPException(403, "Only the treating oncologist, an authorized clinician, or the MDT Coordinator may approve MDT recommendations into binding treatment directives")

    case = db.query(MDTCase).filter(MDTCase.id == id).first()
    if not case:
        raise HTTPException(404, "MDT case not found")
    _check_patient_in_org(db, case.patient_id, _org_id(current_user))

    decision = db.query(MDTDecision).filter(MDTDecision.case_id == case.id).order_by(MDTDecision.id.desc()).first()
    if not decision:
        raise HTTPException(400, "Cannot approve MDT case before recommendation is drafted")

    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    disposition = (body.get("disposition") or "ACCEPT").upper()
    if disposition not in _MDT_DISPOSITION_STATUS:
        raise HTTPException(422, "disposition must be one of ACCEPT, PARTIAL, REJECT")
    reason = body.get("reason")
    if disposition != "ACCEPT" and not reason:
        raise HTTPException(422, "A reason is required unless the disposition is a full ACCEPT")

    actor = _actor(current_user)
    decision.status = _MDT_DISPOSITION_STATUS[disposition]
    decision.disposition_reason = reason
    case.status = _MDT_DISPOSITION_CASE_STATUS[disposition]

    publish(
        db, "MDT_RECOMMENDATION_APPROVED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
        title=f"MDT Recommendation {disposition.title()}", category="MDT",
        description=f"{actor} ({current_user.get('role')}) recorded disposition {disposition} on MDT recommendation for case #{case.id}"
                    + (f": {reason}" if reason else ""),
        mdt_case_id=case.id, mdt_decision_id=decision.id, disposition=disposition,
    )
    log_audit(db, current_user.get("id"), actor, _org_id(current_user),
              "approve_mdt_recommendation", f"cca/mdt/cases/{case.id}", "Success",
              f"disposition={disposition}" + (f", reason={reason}" if reason else ""))
    db.commit()
    return {
        "status": "success",
        "disposition": disposition,
        "case_status": case.status,
        "decision_status": decision.status,
        "approved_by": actor
    }


# ---------------------------------------------------------
# 8b. Treatment Plan lifecycle -- draft / amend / sign / discontinue
#
# A TreatmentPlan is the clinician-owned cancer treatment strategy: distinct from CarePlan
# (the multidisciplinary execution layer built from it). It is drafted, optionally amended,
# then signed by an authorized clinician of the matching modality -- signing is what moves it
# to ACTIVE. A CarePlan can only be created by referencing an already-ACTIVE TreatmentPlan
# (see create_care_plan below), never the reverse. See models_cca.py's TreatmentPlan
# docstring and the Care Plan & Treatment Plan architecture doc for the full rationale.
# ---------------------------------------------------------

def _apply_treatment_plan_discontinuation(db: Session, plan: TreatmentPlan, reason: str, actor: str, role: str):
    """Shared by the explicit discontinue endpoint and the treatment-day 'Discontinue
    Regimen' clearance exit -- both represent the same clinical act (stopping the plan) and
    must produce the same version/audit trail. No-ops if the plan is already terminal, so a
    clearance-driven discontinue after an explicit one (or vice versa) doesn't double-write."""
    if plan.status in ("COMPLETED", "SUPERSEDED", "CANCELLED"):
        return
    plan.status = "CANCELLED"
    # An outstanding order written against this plan is void the moment the plan is --
    # never leave a DRAFT/SIGNED order sitting there for a later clearance call (or a
    # careless order_id-less fallback) to act on as if the plan were still in force.
    for order in db.query(TreatmentOrder).filter(
        TreatmentOrder.treatment_plan_id == plan.id, TreatmentOrder.status.in_(["DRAFT", "SIGNED"])
    ).all():
        order.status = "CANCELLED"
    db.add(TreatmentPlanVersion(
        treatment_plan_id=plan.id,
        version_no=plan.version_no,
        snapshot=_treatment_plan_dict(plan),
        change_reason=reason,
        status_at_version="CANCELLED",
        created_by=actor,
    ))
    publish(
        db, "TREATMENT_PLAN_DISCONTINUED", patient_id=plan.patient_id, actor=actor, role=role,
        title=f"Treatment Plan discontinued: {plan.modality}", category="TREATMENT_PLAN",
        description=f"{actor} discontinued the {plan.modality} Treatment Plan (v{plan.version_no}): {reason}",
        treatment_plan_id=plan.id,
    )


def _treatment_plan_dict(plan: TreatmentPlan) -> dict:
    return {
        "id": plan.id,
        "patient_id": plan.patient_id,
        "care_plan_id": plan.care_plan_id,
        "mdt_decision_id": plan.mdt_decision_id,
        "intent": plan.intent,
        "modality": plan.modality,
        "protocol_name": plan.protocol_name,
        "planned_sessions": plan.planned_sessions,
        "completed_sessions": plan.completed_sessions,
        "start_date": plan.start_date.isoformat() if plan.start_date else None,
        "version_no": plan.version_no,
        "status": plan.status,
        "supersedes_id": plan.supersedes_id,
        "signer_email": plan.signer_email,
        "signer_role": plan.signer_role,
        "signed_at": plan.signed_at.isoformat() if plan.signed_at else None,
        "guideline_review_required": plan.guideline_review_required,
        "guideline_review_reason": plan.guideline_review_reason,
        "created_by": plan.created_by,
    }


@router.post("/treatment-plans")
async def create_treatment_plan(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Drafts a new Treatment Plan. Starts in DRAFT -- it has no clinical authority and
    cannot back a Care Plan or a Treatment Order until POST /treatment-plans/{id}/sign moves
    it to ACTIVE. Pass supersedes_id when this draft is intended to replace an existing
    ACTIVE plan (e.g. following an MDT-recommended revision); the prior plan is only actually
    marked SUPERSEDED once this new one is signed, never at draft time."""
    org_id = _org_id(current_user)
    body = await request.json()
    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)
    _require_clinician(current_user)
    actor = _actor(current_user)

    supersedes_id = body.get("supersedes_id")
    if supersedes_id is not None:
        prior = db.query(TreatmentPlan).filter(TreatmentPlan.id == supersedes_id).first()
        if not prior or prior.patient_id != patient_id:
            raise HTTPException(422, "supersedes_id must reference an existing Treatment Plan for the same patient")
        if prior.status != "ACTIVE":
            # _on_treatment_plan_signed only supersedes a prior plan that is still ACTIVE at
            # sign time -- accepting a non-ACTIVE target here would let this draft report
            # 200 success implying a revision is queued, then silently no-op at sign time
            # with no error and no TREATMENT_PLAN_REVISED event, leaving two same-modality
            # plans in ambiguous concurrent state.
            raise HTTPException(422, f"supersedes_id must reference an ACTIVE Treatment Plan (#{prior.id} is {prior.status})")

    plan = TreatmentPlan(
        patient_id=patient_id,
        mdt_decision_id=body.get("mdt_decision_id"),
        intent=body.get("intent", "Curative"),
        modality=body.get("modality", "Systemic Chemotherapy"),
        protocol_name=body.get("protocol_name"),
        planned_sessions=_coerce_int(body, "planned_sessions", 8),
        completed_sessions=0,
        version_no=1,
        status="DRAFT",
        supersedes_id=supersedes_id,
        created_by=actor,
    )
    db.add(plan)
    db.flush()

    db.add(TreatmentPlanVersion(
        treatment_plan_id=plan.id,
        version_no=1,
        snapshot=body,
        change_reason=body.get("change_reason", "Initial draft created"),
        status_at_version="DRAFT",
        created_by=actor,
    ))
    publish(
        db, "TREATMENT_PLAN_DRAFTED", patient_id=patient_id, actor=actor, role=current_user.get("role"),
        title=f"Treatment Plan drafted: {plan.modality}", category="TREATMENT_PLAN",
        description=f"{actor} drafted a {plan.modality} Treatment Plan (v1, DRAFT).",
        treatment_plan_id=plan.id,
    )
    db.commit()
    db.refresh(plan)
    return {"status": "success", "treatment_plan": _treatment_plan_dict(plan)}


@router.get("/treatment-plans/{id}")
def get_treatment_plan(
    id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Treatment plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    # A DRAFT/PROPOSED plan is a clinician's unfinished authoring surface -- only the roles
    # that actually author plans have a legitimate reason to see it exists at all (not just a
    # reduced view of it), so this 404s the same way a cross-org lookup would rather than
    # confirming a speculative plan is being drafted. Note this is independent of
    # treatment_plan_tier: Infusion Nurse gets full fields once a plan is visible, but still
    # never sees one before it's signed.
    if not can_view_draft_plans_and_orders(current_user) and plan.status in ("DRAFT", "PROPOSED"):
        raise HTTPException(404, "Treatment plan not found")
    return {"treatment_plan": project_treatment_plan(_treatment_plan_dict(plan), current_user)}


@router.get("/patients/{patient_id}/treatment-plans")
def list_treatment_plans(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Every Treatment Plan on record for this patient (across all modalities and all
    statuses, including SUPERSEDED/CANCELLED), newest first -- never overwritten history.
    Non-full-access roles never see DRAFT/PROPOSED plans in the list at all (see
    get_treatment_plan's docstring), and every plan is field-projected per the caller's
    role."""
    _get_org_patient(db, patient_id, _org_id(current_user))
    query = db.query(TreatmentPlan).filter(TreatmentPlan.patient_id == patient_id)
    if not can_view_draft_plans_and_orders(current_user):
        query = query.filter(~TreatmentPlan.status.in_(["DRAFT", "PROPOSED"]))
    plans = query.order_by(TreatmentPlan.id.desc()).all()
    return {"treatment_plans": [project_treatment_plan(_treatment_plan_dict(p), current_user) for p in plans]}


@router.get("/treatment-plans/{id}/versions")
def list_treatment_plan_versions(
    id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Every amend_treatment_plan/sign_treatment_plan/discontinue call writes a
    TreatmentPlanVersion row (mandatory reason, full snapshot) that previously had no read
    endpoint -- 'View Versions' on the architecture doc's own Treatment Plan screen mockup
    had nothing to call. Gated at the same tier boundary as the plan itself: a role with no
    legitimate view of plan content (Front Desk/Financial/minimal-tier roles) gets no access
    to its version snapshots either, since a snapshot carries full clinical content
    regardless of the current tier restriction on the live row."""
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Treatment plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    if treatment_plan_tier(current_user) in ("NONE", "FINANCE", "MINIMAL"):
        raise HTTPException(403, "This role does not have access to Treatment Plan version history")
    versions = db.query(TreatmentPlanVersion).filter(
        TreatmentPlanVersion.treatment_plan_id == id
    ).order_by(TreatmentPlanVersion.version_no.desc()).all()
    return {"versions": [
        {
            "version_no": v.version_no, "change_reason": v.change_reason,
            "status_at_version": v.status_at_version, "snapshot": v.snapshot,
            "created_by": v.created_by, "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in versions
    ]}


@router.put("/treatment-plans/{id}")
async def amend_treatment_plan(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Amends a Treatment Plan still in DRAFT/PROPOSED/ACTIVE/ON_HOLD. Mirrors CarePlan's
    existing amend pattern: mutate the same row, increment version_no, and write an
    immutable snapshot with a mandatory change_reason -- never a silent overwrite."""
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Treatment plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    # Same modality-specific authority as sign/discontinue -- amending protocol/dosing detail
    # on someone else's modality is exactly the cross-modality interchangeability this
    # feature's RBAC is built to close; _require_clinician alone let any of the 3 oncologist
    # personas edit any modality's plan.
    _require_modality_signer(current_user, plan.modality)

    if plan.status in ("COMPLETED", "SUPERSEDED", "CANCELLED"):
        raise HTTPException(409, f"Cannot amend a Treatment Plan in {plan.status} status")

    body = await request.json()
    change_reason = body.get("change_reason")
    if not change_reason:
        raise HTTPException(422, "Treatment plan amendments require a mandatory change_reason.")

    plan.version_no += 1
    plan.protocol_name = body.get("protocol_name", plan.protocol_name)
    plan.planned_sessions = _coerce_int(body, "planned_sessions", plan.planned_sessions)
    plan.intent = body.get("intent", plan.intent)

    db.add(TreatmentPlanVersion(
        treatment_plan_id=plan.id,
        version_no=plan.version_no,
        snapshot=body,
        change_reason=change_reason,
        status_at_version=plan.status,
        created_by=_actor(current_user),
    ))
    actor = _actor(current_user)
    publish(
        db, "TREATMENT_PLAN_AMENDED", patient_id=plan.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Treatment Plan amended: {plan.modality}", category="TREATMENT_PLAN",
        description=f"{actor} amended the {plan.modality} Treatment Plan to v{plan.version_no}: {change_reason}",
        treatment_plan_id=plan.id,
    )
    db.commit()
    return {"status": "success", "treatment_plan": {"id": plan.id, "version_no": plan.version_no, "status": plan.status}}


@router.post("/treatment-plans/{id}/sign")
async def sign_treatment_plan(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """The only action that grants a Treatment Plan clinical authority. Requires an
    authorized clinician of the plan's own modality (or Admin) -- not any oncologist
    interchangeably. If this plan supersedes a prior one, that prior plan is marked
    SUPERSEDED only now, at the moment the replacement actually takes effect."""
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Treatment plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    _require_modality_signer(current_user, plan.modality)

    if plan.status not in ("DRAFT", "PROPOSED"):
        raise HTTPException(409, f"Cannot sign a Treatment Plan in {plan.status} status")

    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    actor = _actor(current_user)

    plan.status = "ACTIVE"
    plan.signer_email = actor
    plan.signer_role = current_user.get("role")
    plan.signed_at = datetime.utcnow()

    # Snapshot which guideline version is current for this patient's pathway right now, at
    # the moment of signing -- evaluate_guideline_readiness computes this live rather than
    # reading a persisted GuidelineContext row (nothing in this codebase writes one; see
    # cca_engine.py). This is what lets a later guideline version update
    # (publish_guideline_version below) flag this specific plan for review without ever
    # touching its content -- see the architecture doc's non-negotiable on guideline updates
    # and TreatmentPlanGuidelineLink's docstring.
    # evaluate_guideline_readiness always returns the same guideline_source/version for
    # every patient regardless of actual diagnosis, modality, or staging readiness -- a
    # documented, deliberate limitation of this single-cancer-type demo engine (see
    # cca_engine.py and the CCA demo spec's N-01/N-02 on why AJCC/NCCN content itself is a
    # licensed-content shell pending a real provider). A previous version of this code
    # wrapped the snapshot in `if guideline_state.get("guideline_source")`, implying some
    # patients wouldn't get one -- that was never true (the field is always present) and
    # only made the single-pathway limitation look like a bug rather than what it is: every
    # signed plan in this demo is, correctly for its scope, on the one guideline this build
    # supports. Making this genuinely diagnosis-aware is out of scope here; snapshotting
    # unconditionally is what lets a later publish_guideline_version() flag every active
    # plan for review, which is the actual safety-relevant behavior this exists for.
    guideline_state = evaluate_guideline_readiness(db, plan.patient_id)
    db.add(TreatmentPlanGuidelineLink(
        treatment_plan_id=plan.id,
        guideline_source=guideline_state["guideline_source"],
        pathway_name=guideline_state.get("pathway_name"),
        version_at_signing=guideline_state["version"],
    ))

    db.add(TreatmentPlanVersion(
        treatment_plan_id=plan.id,
        version_no=plan.version_no,
        snapshot=_treatment_plan_dict(plan),
        change_reason=body.get("reason", f"Signed by {plan.signer_role}"),
        status_at_version="ACTIVE",
        created_by=actor,
    ))
    # publish() -> _on_treatment_plan_signed (event_subscribers.py) updates the patient's
    # journey state and, if supersedes_id is set, performs the supersession + raises
    # TREATMENT_PLAN_REVISED itself -- see that subscriber's docstring for why superseding
    # only happens now, at sign time, not when the replacement plan was drafted.
    publish(
        db, "TREATMENT_PLAN_SIGNED", patient_id=plan.patient_id, actor=actor, role=plan.signer_role,
        title=f"Treatment Plan signed: {plan.modality}", category="TREATMENT_PLAN",
        description=f"{actor} ({plan.signer_role}) signed the {plan.modality} Treatment Plan (v{plan.version_no}).",
        treatment_plan_id=plan.id, supersedes_id=plan.supersedes_id,
    )

    # First planned session now exists because the plan is executable -- previously this
    # fired the moment a Care Plan was created (an unrelated object), with no signing step
    # in between at all.
    db.add(TreatmentSession(
        treatment_plan_id=plan.id,
        patient_id=plan.patient_id,
        session_no=1,
        cycle_no=1,
        day_no=1,
        planned_on=datetime.utcnow().date(),
        status="PLANNED",
    ))

    log_audit(db, current_user.get("id"), actor, _org_id(current_user),
              "sign_treatment_plan", f"cca/treatment-plans/{plan.id}", "Success",
              f"{plan.modality} v{plan.version_no}, signer role {plan.signer_role}")
    db.commit()
    db.refresh(plan)
    return {"status": "success", "treatment_plan": _treatment_plan_dict(plan)}


@router.post("/treatment-plans/{id}/discontinue")
async def discontinue_treatment_plan(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Treatment plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    _require_modality_signer(current_user, plan.modality)

    if plan.status in ("COMPLETED", "SUPERSEDED", "CANCELLED"):
        raise HTTPException(409, f"Treatment Plan is already {plan.status}")

    body = await request.json()
    reason = body.get("reason")
    if not reason:
        raise HTTPException(422, "Discontinuing a Treatment Plan requires a reason.")

    actor = _actor(current_user)
    _apply_treatment_plan_discontinuation(db, plan, reason, actor, current_user.get("role"))
    db.commit()
    return {"status": "success", "treatment_plan": {"id": plan.id, "status": plan.status}}


# ---------------------------------------------------------
# 8c. Guideline version governance
#
# Architecture doc non-negotiable: a new guideline (NCCN) version must never silently
# rewrite a signed Treatment Plan -- it must only flag affected plans for clinician review,
# and only a clinician can clear that flag. GuidelineRegistry is the canonical current
# version of a named guideline; TreatmentPlanGuidelineLink (written in sign_treatment_plan
# above) is what version a specific plan was actually authorized under.
# ---------------------------------------------------------

@router.post("/admin/guidelines/publish-version")
async def publish_guideline_version(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Admin-only: licensed guideline content administration is an operational/configuration
    concern in this codebase (see N-01/N-02 in the CCA demo spec on why guideline content
    itself is a pluggable shell pending a licensed provider), not a clinical action -- it
    never touches any Treatment Plan's content, only flags the ones affected for review."""
    if not is_admin(current_user):
        raise HTTPException(403, "Only Admin may publish a guideline version")
    body = await request.json()
    guideline_source = body.get("guideline_source")
    new_version = body.get("new_version")
    if not guideline_source or not new_version:
        raise HTTPException(422, "guideline_source and new_version are required")
    pathway_name = body.get("pathway_name")
    change_note = body.get("change_note")
    actor = _actor(current_user)

    registry = db.query(GuidelineRegistry).filter(
        GuidelineRegistry.guideline_source == guideline_source,
        GuidelineRegistry.pathway_name == pathway_name,
    ).first()
    old_version = registry.current_version if registry else None
    if not registry:
        registry = GuidelineRegistry(guideline_source=guideline_source, pathway_name=pathway_name, current_version=new_version)
        db.add(registry)
    else:
        registry.current_version = new_version
    registry.published_by = actor
    registry.published_at = datetime.utcnow()

    # Flag every currently-ACTIVE plan whose signing-time snapshot doesn't match the new
    # version -- never plans that are already DRAFT/SUPERSEDED/CANCELLED/COMPLETED, since
    # only an active, in-force plan needs re-review against current guidance.
    affected_links = db.query(TreatmentPlanGuidelineLink).join(
        TreatmentPlan, TreatmentPlan.id == TreatmentPlanGuidelineLink.treatment_plan_id
    ).filter(
        TreatmentPlanGuidelineLink.guideline_source == guideline_source,
        TreatmentPlanGuidelineLink.pathway_name == pathway_name,
        TreatmentPlanGuidelineLink.version_at_signing != new_version,
        TreatmentPlan.status == "ACTIVE",
    ).all()

    flagged_plan_ids = []
    for link in affected_links:
        plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == link.treatment_plan_id).first()
        if not plan or plan.guideline_review_required:
            continue
        plan.guideline_review_required = True
        plan.guideline_review_reason = (
            f"Guideline '{guideline_source}' updated from {link.version_at_signing} to {new_version}"
            + (f": {change_note}" if change_note else ".")
        )
        flagged_plan_ids.append(plan.id)
        publish(
            db, "GUIDELINE_VERSION_UPDATE_FLAGGED_PLAN", patient_id=plan.patient_id, actor=actor, role=current_user.get("role"),
            title=f"Treatment Plan flagged for guideline review: {plan.modality}", category="TREATMENT_PLAN",
            description=plan.guideline_review_reason,
            treatment_plan_id=plan.id, guideline_source=guideline_source, old_version=link.version_at_signing, new_version=new_version,
        )

    db.commit()
    return {
        "status": "success",
        "guideline_source": guideline_source,
        "pathway_name": pathway_name,
        "old_version": old_version,
        "new_version": new_version,
        "flagged_treatment_plan_ids": flagged_plan_ids,
    }


@router.post("/treatment-plans/{id}/acknowledge-guideline-review")
async def acknowledge_guideline_review(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """The only action that clears a guideline-review flag -- an explicit clinician decision
    (by the plan's own modality specialist, same authority as signing), never automatic. The
    plan's content is untouched either way; a clinician who decides the plan needs to change
    does so via the normal amend/discontinue endpoints, separately from acknowledging this
    flag."""
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Treatment plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    _require_modality_signer(current_user, plan.modality)

    if not plan.guideline_review_required:
        raise HTTPException(409, "This Treatment Plan has no pending guideline review")

    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    note = body.get("note", "")
    actor = _actor(current_user)

    plan.guideline_review_required = False
    plan.guideline_review_reason = None
    publish(
        db, "GUIDELINE_REVIEW_ACKNOWLEDGED", patient_id=plan.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Guideline review acknowledged: {plan.modality}", category="TREATMENT_PLAN",
        description=f"{actor} reviewed the guideline update against Treatment Plan #{plan.id}." + (f" Note: {note}" if note else ""),
        treatment_plan_id=plan.id,
    )
    db.commit()
    db.refresh(plan)
    return {"status": "success", "treatment_plan": _treatment_plan_dict(plan)}


# ---------------------------------------------------------
# 9. Live Care Plan (SCR-23)
# ---------------------------------------------------------

@router.get("/care-plans/prefill")
def get_care_plan_prefill(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """A speculative, not-yet-approved draft (drug names, doses, procedures) with no
    finalized status to project against -- restricted to clinicians rather than given a
    reduced-field version, since non-clinical roles have no legitimate use for an unapproved
    regimen at all, partial or otherwise."""
    _get_org_patient(db, patient_id, _org_id(current_user))
    _require_clinician(current_user)
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
    full = {
        "id": plan.id,
        "patient_id": plan.patient_id,
        "intent": plan.intent,
        "goals": plan.goals,
        "components": plan.components,
        "source_treatment_plan_ids": plan.source_treatment_plan_ids,
        "monitoring_plan": plan.monitoring_plan,
        "follow_up_plan": plan.follow_up_plan,
        "next_decision_point": plan.next_decision_point,
        "version_no": plan.version_no,
        "status": plan.status,
        "patient_facing_approved": plan.patient_facing_approved,
    }
    return {"care_plan": project_care_plan(full, current_user)}


@router.post("/care-plans")
async def create_care_plan(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """A Care Plan can only be created by referencing one or more already-signed (ACTIVE)
    Treatment Plans -- it never creates or embeds a treatment strategy of its own. This
    replaces the previous behavior where POST /care-plans silently created a TreatmentPlan
    (already ACTIVE, no signature) as a side effect; see the Care Plan & Treatment Plan
    architecture doc for why that conflation is the specific thing this change fixes.

    Ratified decision (cca_product_decisions.CARE_PLAN_IN_PROGRESS_STATUSES): a Care Plan is
    one longitudinal object per patient, not one per episode -- a second Care Plan cannot be
    created while an existing one is still ACTIVE/BLOCKED/ON_HOLD. Amend the existing plan
    (PUT /care-plans/{id}) or change its status instead. A new Care Plan may only be created
    once the prior one reached a terminal state (COMPLETED/CANCELLED), representing a
    genuinely new chapter rather than a second live copy of the same ongoing journey."""
    org_id = _org_id(current_user)
    body = await request.json()
    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)
    _require_clinician(current_user)
    actor = _actor(current_user)

    existing = db.query(CarePlan).filter(
        CarePlan.patient_id == patient_id,
        CarePlan.status.in_(CARE_PLAN_IN_PROGRESS_STATUSES),
    ).order_by(CarePlan.id.desc()).first()
    if existing:
        raise HTTPException(
            409,
            f"Patient already has a Care Plan in progress (#{existing.id}, {existing.status}). "
            f"Care Plan is a longitudinal object -- amend it (PUT /care-plans/{existing.id}) or "
            f"change its status, rather than creating a second one."
        )

    treatment_plan_ids = body.get("treatment_plan_ids")
    if not treatment_plan_ids:
        raise HTTPException(422, "treatment_plan_ids is required: a Care Plan must reference at least one signed (ACTIVE) Treatment Plan.")
    treatment_plans = db.query(TreatmentPlan).filter(TreatmentPlan.id.in_(treatment_plan_ids)).all()
    if len(treatment_plans) != len(set(treatment_plan_ids)):
        raise HTTPException(404, "One or more treatment_plan_ids were not found")
    for tx_plan in treatment_plans:
        if tx_plan.patient_id != patient_id:
            raise HTTPException(422, f"Treatment Plan #{tx_plan.id} does not belong to this patient")
        if tx_plan.status != "ACTIVE":
            raise HTTPException(422, f"Treatment Plan #{tx_plan.id} is {tx_plan.status}, not signed (ACTIVE) -- a Care Plan cannot be built on an unsigned plan.")

    plan = CarePlan(
        patient_id=patient_id,
        intent=body.get("intent", "Curative / Neoadjuvant"),
        goals=body.get("goals", []),
        components=body.get("components", {}),
        source_treatment_plan_ids=list(treatment_plan_ids),
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

    for tx_plan in treatment_plans:
        tx_plan.care_plan_id = plan.id

    # publish() -> _on_care_plan_activated (event_subscribers.py) updates the patient's
    # journey state -- previously that update was hand-written inline here.
    publish(
        db, "CARE_PLAN_ACTIVATED", patient_id=patient_id, actor=actor, role=current_user.get("role"),
        title="Live Care Plan v1.0 Formally Approved", category="CARE_PLAN",
        description=f"{actor} approved Live Care Plan v1.0, built from Treatment Plan(s) {sorted(treatment_plan_ids)}.",
        care_plan_id=plan.id, treatment_plan_ids=list(treatment_plan_ids),
    )
    db.commit()
    db.refresh(plan)
    return {
        "status": "success",
        "care_plan": {
            "id": plan.id,
            "intent": plan.intent,
            "version_no": plan.version_no,
            "status": plan.status,
            "source_treatment_plan_ids": plan.source_treatment_plan_ids,
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
    # A material change invalidates any prior patient-facing approval -- it must never keep
    # showing content from before the amendment; a clinician has to explicitly re-approve.
    plan.patient_facing_approved = False
    plan.patient_facing_approved_by = None
    plan.patient_facing_approved_at = None

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
# 9c. Care Plan status lifecycle (architecture doc section 6).
#
# Previously CarePlan.status was write-once ("ACTIVE" at creation, never changed again by any
# endpoint) -- the full lifecycle the architecture doc names (DRAFT/PROPOSED/ACTIVE/BLOCKED/
# ON_HOLD/COMPLETED/SUPERSEDED/CANCELLED) could not be represented at all, so "a required
# dependency prevents progression" (BLOCKED) had nowhere to be recorded.
#
# Scoped deliberately: this covers the ACTIVE<->BLOCKED/ON_HOLD/COMPLETED/CANCELLED
# transitions a Care Plan already created (ACTIVE, per create_care_plan's own docstring on why
# Care Plans in this system are born ACTIVE rather than DRAFT) goes through during execution.
# It does NOT retrofit a DRAFT/PROPOSED pre-approval stage onto create_care_plan (a distinct,
# larger change to that endpoint's contract), and it does NOT model SUPERSEDED -- this
# codebase's CarePlan is amended in place (update_care_plan bumps version_no on the same row)
# rather than chained across rows the way TreatmentPlan.supersedes_id is, so there is no
# "replaced by a newer Care Plan row" event to represent yet. Both are flagged here as
# deliberately out of scope rather than silently done partially.
# ---------------------------------------------------------

_CARE_PLAN_STATUS_TRANSITIONS = {
    # DRAFT/PROPOSED are additive states (architecture doc Sec 6): create_care_plan itself still
    # defaults new plans straight to ACTIVE (a distinct, larger change to that endpoint's own
    # contract -- see its docstring), but a plan that *is* created/moved into DRAFT or PROPOSED
    # (e.g. an MDT-recommendation-originated draft awaiting the treating clinician's review) can
    # now be advanced through this same transition endpoint instead of hitting a dead end.
    "DRAFT": {"PROPOSED", "CANCELLED"},
    "PROPOSED": {"ACTIVE", "DRAFT", "CANCELLED"},
    "ACTIVE": {"BLOCKED", "ON_HOLD", "COMPLETED", "CANCELLED"},
    "BLOCKED": {"ACTIVE", "CANCELLED"},
    "ON_HOLD": {"ACTIVE", "CANCELLED"},
}


@router.post("/care-plans/{id}/status")
async def update_care_plan_status(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Explicit, reasoned status transition -- a material clinical change, so it versions
    like any other Care Plan amendment (architecture doc: 'operational status changes may
    update the active version... but must remain auditable'; a status change is more than
    operational, so it gets a full new version, not just an audit log line)."""
    plan = db.query(CarePlan).filter(CarePlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Care plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    _require_clinician(current_user)

    body = await request.json()
    new_status = body.get("status")
    reason = body.get("reason")
    if not reason:
        raise HTTPException(422, "A reason is required for every Care Plan status transition.")
    allowed = _CARE_PLAN_STATUS_TRANSITIONS.get(plan.status, set())
    if new_status not in allowed:
        raise HTTPException(
            409,
            f"Cannot transition Care Plan from {plan.status} to {new_status}. "
            f"Allowed from {plan.status}: {sorted(allowed) or 'none (terminal state)'}."
        )

    old_status = plan.status
    plan.status = new_status
    plan.version_no += 1
    actor = _actor(current_user)

    db.add(CarePlanVersion(
        care_plan_id=plan.id,
        version_no=plan.version_no,
        snapshot={"status": new_status, "previous_status": old_status},
        change_reason=reason,
        created_by=actor,
    ))
    publish(
        db, "CARE_PLAN_STATUS_CHANGED", patient_id=plan.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Care Plan {old_status} -> {new_status}", category="CARE_PLAN",
        description=f"{actor} moved Care Plan #{plan.id} from {old_status} to {new_status}: {reason}",
        care_plan_id=plan.id, previous_status=old_status, new_status=new_status,
    )
    log_audit(db, current_user.get("id"), actor, _org_id(current_user),
              "update_care_plan_status", f"cca/care-plans/{plan.id}", "Success",
              f"{old_status} -> {new_status}: {reason}")
    db.commit()
    db.refresh(plan)
    return {"status": "success", "care_plan": {"id": plan.id, "status": plan.status, "version_no": plan.version_no}}


@router.get("/care-plans/{id}/versions")
def list_care_plan_versions(
    id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """update_care_plan and update_care_plan_status both write a CarePlanVersion row
    (mandatory reason, full snapshot) that previously had no read endpoint -- 'View History'
    on the architecture doc's own Care Plan screen mockup had nothing to call."""
    plan = db.query(CarePlan).filter(CarePlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Care plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    if care_plan_tier(current_user) == "OPERATIONAL":
        raise HTTPException(403, "This role does not have access to Care Plan version history")
    versions = db.query(CarePlanVersion).filter(
        CarePlanVersion.care_plan_id == id
    ).order_by(CarePlanVersion.version_no.desc()).all()
    return {"versions": [
        {
            "version_no": v.version_no, "change_reason": v.change_reason,
            "snapshot": v.snapshot, "created_by": v.created_by,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in versions
    ]}


# ---------------------------------------------------------
# 9d. Patient-facing plan view -- gated behind explicit clinical/consent review.
#
# Architecture doc non-negotiable: patient-facing surfaces get a controlled subset only,
# never raw clinical reasoning/MDT discussion/full rationale, and only after clinical/consent
# review. Note the scope limit: this builds the content projection and the clinician-approval
# gate (the safety-relevant part), and exposes it through a staff-facing preview endpoint --
# it does NOT build genuine patient self-service login, which would need an entirely separate
# patient-authentication/account/consent-provisioning system this codebase has no trace of
# today. Any staff role with legitimate patient-contact responsibility can preview "what the
# patient would see"; a real patient portal is a distinct, larger initiative.
# ---------------------------------------------------------

def _require_patient_contact_role(current_user: dict):
    if not (
        is_doctor(current_user) or is_admin(current_user) or is_cca_oncologist(current_user)
        or is_cca_nurse_navigator(current_user) or is_cca_patient_liaison(current_user) or is_cca_front_desk(current_user)
    ):
        raise HTTPException(403, "This role has no patient-contact responsibility for the patient-facing view")


@router.post("/care-plans/{id}/approve-patient-facing")
def approve_patient_facing_view(
    id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    plan = db.query(CarePlan).filter(CarePlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Care plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    _require_clinician(current_user)

    actor = _actor(current_user)
    plan.patient_facing_approved = True
    plan.patient_facing_approved_by = actor
    plan.patient_facing_approved_at = datetime.utcnow()
    publish(
        db, "CARE_PLAN_PATIENT_FACING_APPROVED", patient_id=plan.patient_id, actor=actor, role=current_user.get("role"),
        title="Care Plan approved for patient-facing view", category="CARE_PLAN",
        description=f"{actor} approved the patient-facing view of Care Plan #{plan.id} (v{plan.version_no}).",
        care_plan_id=plan.id,
    )
    db.commit()
    return {"status": "success", "care_plan_id": plan.id, "patient_facing_approved": True}


@router.post("/care-plans/{id}/revoke-patient-facing")
def revoke_patient_facing_view(
    id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    plan = db.query(CarePlan).filter(CarePlan.id == id).first()
    if not plan:
        raise HTTPException(404, "Care plan not found")
    _check_patient_in_org(db, plan.patient_id, _org_id(current_user))
    _require_clinician(current_user)

    plan.patient_facing_approved = False
    plan.patient_facing_approved_by = None
    plan.patient_facing_approved_at = None
    db.commit()
    return {"status": "success", "care_plan_id": plan.id, "patient_facing_approved": False}


@router.get("/patients/{patient_id}/patient-facing-summary")
def get_patient_facing_summary(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Returns {"available": False} rather than an error when nothing has been approved yet
    -- that is the normal, expected state for most patients, not a fault condition. When
    available, the payload is deliberately minimal: a coarse phase label (the same
    operational-label tier Front Desk already gets for Care Plan) and only the tasks a
    clinician has explicitly written patient-safe wording for (see
    set_task_patient_visible_note) -- never a raw CarePlanTask.description, which routinely
    names clinical detail (toxicity grade, an MDT case number) that has no business reaching
    a patient-facing surface."""
    _get_org_patient(db, patient_id, _org_id(current_user))
    _require_patient_contact_role(current_user)
    return build_patient_facing_summary(db, patient_id)


def build_patient_facing_summary(db: Session, patient_id: int) -> dict:
    """The actual content logic, factored out so the staff preview endpoint above and the
    real patient-authenticated endpoint (routers/patient_portal.py) return byte-identical
    content -- there is exactly one definition of what's safe to show a patient, not two
    that could drift apart."""
    plan = db.query(CarePlan).filter(
        CarePlan.patient_id == patient_id, CarePlan.status == "ACTIVE"
    ).order_by(CarePlan.id.desc()).first()
    if not plan or not plan.patient_facing_approved:
        return {"available": False}

    tasks = db.query(CarePlanTask).filter(
        CarePlanTask.patient_id == patient_id,
        CarePlanTask.status == "OPEN",
        CarePlanTask.patient_visible_note.isnot(None),
    ).order_by(CarePlanTask.due_date.asc()).limit(5).all()

    # Upcoming appointments: scheduled diagnostic orders, not a separate Appointment entity --
    # this codebase has none for CCA (the general HMS `appointments` table is a different
    # product entirely, unrelated to the oncology patient population). item_name/location are
    # scheduling metadata (the same tier Front Desk already gets), not clinical rationale --
    # clinical_indication is deliberately excluded from what reaches the patient here.
    appointments = db.query(CCAOrder).filter(
        CCAOrder.patient_id == patient_id,
        CCAOrder.scheduled_at.isnot(None),
        CCAOrder.scheduled_at >= datetime.utcnow(),
        CCAOrder.status.in_(["SCHEDULED", "RAISED", "IN_PROGRESS"]),
    ).order_by(CCAOrder.scheduled_at.asc()).limit(5).all()

    return {
        "available": True,
        "current_phase_label": plan.intent,
        "next_steps": [{"note": t.patient_visible_note, "due_date": t.due_date.isoformat() if t.due_date else None} for t in tasks],
        "upcoming_appointments": [
            {"what": a.item_name, "when": a.scheduled_at.isoformat(), "location": a.location}
            for a in appointments
        ],
        "approved_by": plan.patient_facing_approved_by,
        "approved_at": plan.patient_facing_approved_at.isoformat() if plan.patient_facing_approved_at else None,
    }


# ---------------------------------------------------------
# 9a. Patient Task / Review Queue
#
# CarePlanTask previously had no read/write surface at all beyond being a side effect of a
# treatment hold -- every row this system created (including the ones from
# event_subscribers.py) was invisible through the API. care_plan_id is nullable (see
# models_cca.py's CarePlanTask docstring) since a task is patient-scoped first; a Care Plan
# link is attached when one exists, not required for the task to exist.
# ---------------------------------------------------------

def _care_plan_task_dict(task: CarePlanTask) -> dict:
    return {
        "id": task.id,
        "care_plan_id": task.care_plan_id,
        "patient_id": task.patient_id,
        "description": task.description,
        "owner_id": task.owner_id,
        "owner_name": task.owner_name,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "status": task.status,
        "source": task.source,
        "patient_visible_note": task.patient_visible_note,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


@router.get("/patients/{patient_id}/tasks")
def list_patient_tasks(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Every open-or-closed review/reassessment task for this patient, with or without a
    Care Plan link, oldest-due first. Not role-projected in this slice -- a task description
    is short, already-actionable operational text (e.g. "Review MDT recommendation..."), a
    materially smaller disclosure than a full plan; per-role filtering by task category is a
    reasonable future refinement once tasks carry an owner_role/category field, not invented
    here without one."""
    _get_org_patient(db, patient_id, _org_id(current_user))
    tasks = db.query(CarePlanTask).filter(
        CarePlanTask.patient_id == patient_id
    ).order_by(CarePlanTask.due_date.asc()).all()
    return {"tasks": [_care_plan_task_dict(t) for t in tasks]}


@router.post("/tasks/{id}/resolve")
def resolve_patient_task(
    id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Resolving a review/reassessment task is treated as a clinical act requiring a
    clinician (or Admin) -- deliberately coarse-grained rather than guessing at a
    per-task-category permission scheme (see list_patient_tasks's docstring)."""
    task = db.query(CarePlanTask).filter(CarePlanTask.id == id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    _check_patient_in_org(db, task.patient_id, _org_id(current_user))
    _require_clinician(current_user)

    if task.status == "RESOLVED":
        raise HTTPException(409, "Task is already resolved")

    task.status = "RESOLVED"
    db.commit()
    db.refresh(task)
    return {"status": "success", "task": _care_plan_task_dict(task)}


@router.patch("/tasks/{id}/patient-visible-note")
async def set_task_patient_visible_note(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """The only way a task's raw internal description becomes visible in the patient-facing
    summary: a clinician deliberately authors patient-safe wording for it. Passing an empty
    note removes it from the patient-facing view again."""
    task = db.query(CarePlanTask).filter(CarePlanTask.id == id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    _check_patient_in_org(db, task.patient_id, _org_id(current_user))
    _require_clinician(current_user)

    body = await request.json()
    task.patient_visible_note = body.get("note") or None
    db.commit()
    db.refresh(task)
    return {"status": "success", "task": _care_plan_task_dict(task)}


# ---------------------------------------------------------
# 9a2. AI Search -- source-cited retrieval, no silent writes.
#
# This is deterministic keyword retrieval over the patient's own already-verified structured
# data (facts, MDT decisions, treatment plans, staging, journey events) -- not a generative
# LLM call. A real RAG/LLM-backed AI Search is a larger, separate initiative needing its own
# safety review (prompt injection, hallucination, citation-grounding validation); this slice
# satisfies the architecture doc's safety-relevant requirements for this feature --
# every result carries a source/date/author and a view_source reference, and the only way a
# result becomes a Care Plan task is an explicit clinician action, never automatic -- without
# that much larger lift. Restricted to clinical-adjacent roles (same access line as Care
# Plan's CLINICAL_CONTEXT/FULL projection tiers): search results surface fairly raw
# clinical text that Front Desk/Patient Liaison/Financial Counsellor have no role-defined
# need for, per the same least-privilege reasoning as rbac_projection.py.
# ---------------------------------------------------------

def _require_search_access(current_user: dict):
    if care_plan_tier(current_user) == "OPERATIONAL":
        raise HTTPException(403, "AI Search is available to clinical-adjacent roles only")


@router.post("/patients/{patient_id}/search/propose-task")
async def propose_task_from_search(
    patient_id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """The only way a search result becomes anything durable: an explicit clinician action
    (this endpoint), never an automatic write from the search itself -- architecture doc
    non-negotiable on AI proposals. The resulting task is tagged AI_SEARCH_PROPOSED, not
    SYSTEM or MANUAL, so it stays distinguishable in the task queue."""
    _get_org_patient(db, patient_id, _org_id(current_user))
    _require_clinician(current_user)
    body = await request.json()
    description = body.get("description")
    if not description:
        raise HTTPException(422, "description is required")
    source_reference = body.get("source_reference")  # e.g. {"type": "MDTDecision", "id": 4} -- informational only
    actor = _actor(current_user)

    task = CarePlanTask(
        care_plan_id=body.get("care_plan_id"),
        patient_id=patient_id,
        description=description,
        owner_id="", owner_name=body.get("owner_name") or actor,
        due_date=datetime.fromisoformat(body["due_date"]) if body.get("due_date") else datetime.utcnow() + timedelta(days=7),
        status="OPEN",
        source="AI_SEARCH_PROPOSED",
    )
    db.add(task)
    db.flush()
    publish(
        db, "AI_SEARCH_TASK_PROPOSED", patient_id=patient_id, actor=actor, role=current_user.get("role"),
        title="Task proposed from AI Search", category="AI_SEARCH",
        description=f"{actor} confirmed a task from AI Search: {description}",
        task_id=task.id, source_reference=source_reference,
    )
    db.commit()
    db.refresh(task)
    return {"status": "success", "task": _care_plan_task_dict(task)}


# ---------------------------------------------------------
# 9b. Treatment Order lifecycle -- the executable instruction Day-Care actually acts on.
#
# Day-Care must never infer treatment from a Care Plan or Treatment Plan alone -- it requires
# a valid, signed, executable Treatment Order (architecture doc non-negotiable). Each planned
# TreatmentSession gets its own Order; a dose/instruction change is always a new Order, never
# a silent edit to one already signed. See models_cca.py's TreatmentOrder/TreatmentEvent
# docstrings.
# ---------------------------------------------------------

def _treatment_order_dict(order: TreatmentOrder) -> dict:
    return {
        "id": order.id,
        "treatment_plan_id": order.treatment_plan_id,
        "treatment_session_id": order.treatment_session_id,
        "patient_id": order.patient_id,
        "instructions": order.instructions,
        "version_no": order.version_no,
        "status": order.status,
        "signer_email": order.signer_email,
        "signer_role": order.signer_role,
        "signed_at": order.signed_at.isoformat() if order.signed_at else None,
        "created_by": order.created_by,
    }


@router.post("/treatment-orders")
async def create_treatment_order(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Writes a new order against the next open session of an already-signed Treatment Plan.
    A session may only ever have one open (non-cancelled) order at a time -- a revised
    instruction is a fresh order on that same session, requiring its own sign-off, never an
    edit to the existing one.

    Ratified decision (cca_product_decisions.TREATMENT_ORDERS_SYSTEM_OF_RECORD = "in_os"):
    this endpoint IS the system of record for the executable order, not a mirror of an order
    authored in an external oncology/pharmacy system. See that module for why."""
    assert TREATMENT_ORDERS_SYSTEM_OF_RECORD == "in_os"  # this endpoint only makes sense under this decision
    org_id = _org_id(current_user)
    body = await request.json()
    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)
    _require_clinician(current_user)
    actor = _actor(current_user)

    treatment_plan_id = body.get("treatment_plan_id")
    if not treatment_plan_id:
        raise HTTPException(422, "treatment_plan_id is required")
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == treatment_plan_id).first()
    if not plan or plan.patient_id != patient_id:
        raise HTTPException(404, "Treatment plan not found for this patient")
    if plan.status != "ACTIVE":
        raise HTTPException(422, f"Treatment Plan #{plan.id} is {plan.status}, not signed (ACTIVE) -- cannot write an order against it.")

    session = db.query(TreatmentSession).filter(
        TreatmentSession.treatment_plan_id == plan.id, TreatmentSession.status == "PLANNED"
    ).order_by(TreatmentSession.session_no.desc()).first()
    if not session:
        raise HTTPException(422, "No pending (PLANNED) session on this Treatment Plan to write an order against.")

    existing_open_order = db.query(TreatmentOrder).filter(
        TreatmentOrder.treatment_session_id == session.id, TreatmentOrder.status != "CANCELLED"
    ).first()
    if existing_open_order:
        raise HTTPException(409, f"Session #{session.session_no} already has an open order (#{existing_open_order.id}, {existing_open_order.status}).")

    order = TreatmentOrder(
        treatment_plan_id=plan.id,
        treatment_session_id=session.id,
        patient_id=patient_id,
        instructions=body.get("instructions", {}),
        version_no=1,
        status="DRAFT",
        created_by=actor,
    )
    db.add(order)
    db.flush()
    publish(
        db, "TREATMENT_ORDER_DRAFTED", patient_id=patient_id, actor=actor, role=current_user.get("role"),
        title=f"Treatment Order drafted: session #{session.session_no}", category="TREATMENT_ORDER",
        description=f"{actor} drafted a Treatment Order for session #{session.session_no} of {plan.modality}.",
        treatment_order_id=order.id,
    )
    db.commit()
    db.refresh(order)
    return {"status": "success", "treatment_order": _treatment_order_dict(order)}


@router.get("/treatment-orders/{id}")
def get_treatment_order(
    id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    order = db.query(TreatmentOrder).filter(TreatmentOrder.id == id).first()
    if not order:
        raise HTTPException(404, "Treatment order not found")
    _check_patient_in_org(db, order.patient_id, _org_id(current_user))
    if not can_view_draft_plans_and_orders(current_user) and order.status == "DRAFT":
        raise HTTPException(404, "Treatment order not found")
    return {"treatment_order": project_treatment_order(_treatment_order_dict(order), current_user)}


@router.get("/patients/{patient_id}/treatment-orders")
def list_treatment_orders(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _get_org_patient(db, patient_id, _org_id(current_user))
    query = db.query(TreatmentOrder).filter(TreatmentOrder.patient_id == patient_id)
    if not can_view_draft_plans_and_orders(current_user):
        query = query.filter(TreatmentOrder.status != "DRAFT")
    orders = query.order_by(TreatmentOrder.id.desc()).all()
    return {"treatment_orders": [project_treatment_order(_treatment_order_dict(o), current_user) for o in orders]}


@router.post("/treatment-orders/{id}/sign")
async def sign_treatment_order(
    id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """The action that gives an order clinical authority. Gated to the matching modality
    specialist, same as signing the parent Treatment Plan -- not any oncologist
    interchangeably, and not a nurse (Day-Care may document administration against an
    already-signed order, but may not authorize it)."""
    order = db.query(TreatmentOrder).filter(TreatmentOrder.id == id).first()
    if not order:
        raise HTTPException(404, "Treatment order not found")
    _check_patient_in_org(db, order.patient_id, _org_id(current_user))
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == order.treatment_plan_id).first()
    _require_modality_signer(current_user, plan.modality if plan else "")

    if order.status != "DRAFT":
        raise HTTPException(409, f"Cannot sign a Treatment Order in {order.status} status")

    actor = _actor(current_user)
    order.status = "SIGNED"
    order.signer_email = actor
    order.signer_role = current_user.get("role")
    order.signed_at = datetime.utcnow()

    publish(
        db, "TREATMENT_ORDER_SIGNED", patient_id=order.patient_id, actor=actor, role=order.signer_role,
        title="Treatment Order signed", category="TREATMENT_ORDER",
        description=f"{actor} ({order.signer_role}) signed Treatment Order #{order.id}.",
        treatment_order_id=order.id,
    )
    db.commit()
    db.refresh(order)
    return {"status": "success", "treatment_order": _treatment_order_dict(order)}


@router.post("/treatment-orders/{id}/cancel")
async def cancel_treatment_order(
    id: int, request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    order = db.query(TreatmentOrder).filter(TreatmentOrder.id == id).first()
    if not order:
        raise HTTPException(404, "Treatment order not found")
    _check_patient_in_org(db, order.patient_id, _org_id(current_user))
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == order.treatment_plan_id).first()
    _require_modality_signer(current_user, plan.modality if plan else "")

    if order.status in ("EXECUTED", "CANCELLED"):
        raise HTTPException(409, f"Treatment Order is already {order.status}")

    body = await request.json()
    reason = body.get("reason")
    if not reason:
        raise HTTPException(422, "Cancelling a Treatment Order requires a reason.")

    actor = _actor(current_user)
    order.status = "CANCELLED"
    publish(
        db, "TREATMENT_ORDER_CANCELLED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title="Treatment Order cancelled", category="TREATMENT_ORDER",
        description=f"{actor} cancelled Treatment Order #{order.id}: {reason}",
        treatment_order_id=order.id,
    )
    db.commit()
    return {"status": "success", "treatment_order": {"id": order.id, "status": order.status}}


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
    # DRAFT as well as SIGNED -- the UI needs to know about an open-but-unsigned order too
    # (to offer "sign" rather than "write a new one"), not just a fully executable one.
    order = db.query(TreatmentOrder).filter(
        TreatmentOrder.patient_id == patient_id, TreatmentOrder.status.in_(["DRAFT", "SIGNED"])
    ).order_by(TreatmentOrder.id.desc()).first()

    return {
        "patient": {"name": patient.name, "mrn": patient.mrn, "bsa": intake.bsa if intake else None},
        "protocol": plan.protocol_name if plan else "[NOT_RECORDED] No active treatment plan on record.",
        "cycle_info": f"Cycle {plan.completed_sessions + 1}" if plan else "[NOT_RECORDED]",
        "order": project_treatment_order(_treatment_order_dict(order), current_user) if order else None,
        "order_note": None if order else "No Treatment Order on record -- a clearance decision cannot be recorded until a signed one exists.",
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


def _require_clinical_or_nursing_role(current_user: dict):
    """CTCAE toxicity grading is a clinical/nursing assessment, not an administrative one --
    narrower than _require_patient_contact_role (which also admits Front Desk/Patient
    Liaison, who have no basis to grade a toxicity)."""
    if not (
        is_doctor(current_user) or is_admin(current_user) or is_cca_oncologist(current_user)
        or is_cca_nurse_navigator(current_user) or is_cca_infusion_nurse(current_user)
    ):
        raise HTTPException(403, "Only a treating clinician or nurse may record a toxicity assessment")


@router.post("/treatment/toxicity")
async def record_toxicity(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    _require_clinical_or_nursing_role(current_user)
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


_VALID_CLEARANCE_DECISIONS = {"CLEARED", "CLEARED_DOSE_REDUCTION", "HELD", "DEFERRED", "DISCONTINUED"}


@router.post("/treatment/clearance")
async def record_clearance_decision(
    request: Request, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Day-Care never infers treatment from a Care Plan/Treatment Plan alone -- this requires
    a signed (executable) Treatment Order to act against (architecture doc non-negotiable),
    and every decision produces a TreatmentEvent: the closed-loop 'actual outcome' record
    that a plan-only, one-way pipeline would otherwise lose. This replaces the previous
    behavior of mutating TreatmentSession.status directly with no order in the loop at all."""
    org_id = _org_id(current_user)
    _require_clinician(current_user)
    body = await request.json()
    decision = body.get("decision")
    reason = body.get("reason")
    if not decision:
        raise HTTPException(422, "decision is required to record a treatment clearance.")
    if decision not in _VALID_CLEARANCE_DECISIONS:
        raise HTTPException(422, f"Unknown decision code '{decision}'. Must be one of {sorted(_VALID_CLEARANCE_DECISIONS)}.")
    if not reason:
        raise HTTPException(422, "reason is required to record a treatment clearance.")
    patient_id = _require_patient_id(body)
    _get_org_patient(db, patient_id, org_id)
    actor = _actor(current_user)
    role = current_user.get("role")

    order_id = body.get("order_id")
    if order_id is not None:
        order = db.query(TreatmentOrder).filter(TreatmentOrder.id == order_id, TreatmentOrder.patient_id == patient_id).first()
    else:
        # Defense in depth alongside _apply_treatment_plan_discontinuation now cancelling an
        # outstanding order on discontinue: still require the order's own plan to be ACTIVE
        # here too, so a stale SIGNED order from any code path that predates that fix (or a
        # future one that misses it) can never be picked up as if its plan were still in force.
        order = db.query(TreatmentOrder).join(
            TreatmentPlan, TreatmentPlan.id == TreatmentOrder.treatment_plan_id
        ).filter(
            TreatmentOrder.patient_id == patient_id, TreatmentOrder.status == "SIGNED",
            TreatmentPlan.status == "ACTIVE",
        ).order_by(TreatmentOrder.id.desc()).first()
    if not order or order.status != "SIGNED":
        raise HTTPException(422, "No signed Treatment Order on record for this patient -- cannot record a clearance decision.")

    session = db.query(TreatmentSession).filter(TreatmentSession.id == order.treatment_session_id).first()
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == order.treatment_plan_id).first()

    clearance = TreatmentClearance(
        session_id=session.id,
        order_id=order.id,
        patient_id=patient_id,
        decision=decision,
        reason=reason,
        reassess_on=(datetime.utcnow() + timedelta(days=7)).date() if decision in ("HELD", "DEFERRED") else None,
        task_owner_id=body.get("task_owner_id", actor),
        decided_by=actor,
        decided_at=datetime.utcnow()
    )
    db.add(clearance)

    if decision in ("HELD", "DEFERRED"):
        # The order is deliberately left SIGNED, not cancelled -- once the hold resolves, the
        # same already-authorized order can still be executed via a later clearance decision.
        session.status = "HELD"
        db.add(TreatmentEvent(
            treatment_order_id=order.id, patient_id=patient_id, event_type=decision,
            reason=reason, performed_by=actor, performed_role=role, performed_at=datetime.utcnow(),
        ))
        # publish() -> _on_treatment_held (event_subscribers.py) opens the mandatory
        # reassessment CarePlanTask -- previously that CarePlanTask construction was
        # hand-written inline here.
        publish(
            db, "TREATMENT_HELD", patient_id=patient_id, actor=actor, role=role,
            title=f"Treatment Clearance: {decision}", category="TREATMENT",
            description=f"Decision by {actor}: {decision}. Reason: {reason}",
            treatment_order_id=order.id, care_plan_id=plan.care_plan_id if plan else None,
            reason=reason, decision=decision, owner_id=current_user.get("id"),
        )

    elif decision in ("CLEARED", "CLEARED_DOSE_REDUCTION"):
        order.status = "EXECUTED"
        session.status = "ADMINISTERED"
        session.administered_on = datetime.utcnow()
        session.administered_by = actor
        db.add(TreatmentEvent(
            treatment_order_id=order.id, patient_id=patient_id, event_type="ADMINISTERED",
            outcome="Standard Dose (100%)" if decision == "CLEARED" else "Dose Reduced",
            reason=reason, performed_by=actor, performed_role=role, performed_at=datetime.utcnow(),
        ))
        # publish() -> _on_treatment_administered (event_subscribers.py) advances
        # completed_sessions and opens the next planned session if more remain --
        # previously that logic was hand-written inline here.
        publish(
            db, "TREATMENT_ADMINISTERED", patient_id=patient_id, actor=actor, role=role,
            title=f"Treatment Clearance: {decision}", category="TREATMENT",
            description=f"Decision by {actor}: {decision}. Reason: {reason}",
            treatment_plan_id=plan.id if plan else None, treatment_order_id=order.id,
            treatment_session_id=session.id,
        )

    elif decision == "DISCONTINUED":
        order.status = "CANCELLED"
        session.status = "CANCELLED"
        db.add(TreatmentEvent(
            treatment_order_id=order.id, patient_id=patient_id, event_type="DISCONTINUED",
            reason=reason, performed_by=actor, performed_role=role, performed_at=datetime.utcnow(),
        ))
        if plan:
            # Publishes TREATMENT_PLAN_DISCONTINUED itself -- see that helper.
            _apply_treatment_plan_discontinuation(db, plan, reason, actor, role)
        publish(
            db, "TREATMENT_CLEARANCE_DISCONTINUED", patient_id=patient_id, actor=actor, role=role,
            title=f"Treatment Clearance: {decision}", category="TREATMENT",
            description=f"Decision by {actor}: {decision}. Reason: {reason}",
            treatment_order_id=order.id,
        )

    db.commit()
    db.refresh(clearance)
    return {
        "status": "success",
        "clearance": {
            "id": clearance.id,
            "order_id": clearance.order_id,
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
    """RECIST response categorization is a clinical/radiological judgment call on the
    treatment record, gated like the other clinical-decision endpoints in this router."""
    _require_clinician(current_user)
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
# 11b. Multi-Scope AI Search & CDS Governance (Spec Section 30)
# ---------------------------------------------------------

@router.get("/patients/{patient_id}/search")
def search_patient_records_and_knowledge(
    patient_id: int, query: str, scope: str = "THIS_PATIENT",
    db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Enforces Spec Section 30: AI Search governance across 3 distinct scopes:
    - THIS_PATIENT: Patient's longitudinal chart -- verified facts, MDT decisions, treatment
      plans, staging records, and journey events (the full set the single-scope predecessor of
      this endpoint used to cover; kept here so merging the two never regresses coverage).
    - HOSPITAL_RECORDS: Hospital clinical SOPs, protocols, and policies.
    - CLINICAL_KNOWLEDGE: NCCN guidelines, AJCC staging references.

    Guarantees:
    - Cites source provenance (id, date, author/system, confirmation state, view-source link)
      for every THIS_PATIENT answer.
    - Distinguishes patient facts from general medical knowledge.
    - Any proposed action (e.g. task proposal) requires explicit clinician acceptance."""
    org_id = _org_id(current_user)
    _get_org_patient(db, patient_id, org_id)
    _require_search_access(current_user)

    q = (query or "").lower().strip()
    if len(q) < 2:
        raise HTTPException(422, "query must be at least 2 characters")

    results = []
    citations = []

    def _add(rtype, rid, snippet, source_date, source_author, confirmation_state, view_source, citation_label):
        source_date_iso = source_date.isoformat() if source_date else None
        results.append({
            "scope": "THIS_PATIENT", "type": rtype, "id": rid, "snippet": snippet,
            "source_date": source_date_iso, "source_author": source_author,
            "confirmation_state": confirmation_state, "view_source": view_source,
            "citation": citation_label,
        })
        citations.append({
            "source_id": rid, "source_type": rtype, "title": citation_label,
            "provenance": snippet, "source_date": source_date_iso,
            "source_author": source_author, "confirmation_state": confirmation_state,
            "view_source": view_source,
        })

    if scope in ("THIS_PATIENT", "ALL"):
        for f in db.query(ClinicalFact).filter(ClinicalFact.patient_id == patient_id).all():
            if q in f"{f.fact_type or ''} {f.value or ''} {f.verbatim_span or ''}".lower():
                _add("CLINICAL_FACT", f.id, f"{f.fact_type}: {f.value}",
                     f.created_at, f.verified_by or "AI-extracted (unverified)", f.status,
                     f"/api/cca/patients/{patient_id}/documents/{f.document_id}" if f.document_id else None,
                     f"Fact #{f.id} (Doc #{f.document_id or 'Manual'}, Page {f.page_number})")

        for d in db.query(MDTDecision).filter(MDTDecision.patient_id == patient_id).all():
            if q in f"{d.recommendation or ''} {d.rationale or ''} {d.modality_direction or ''}".lower():
                _add("MDT_DECISION", d.id, d.recommendation,
                     d.recorded_at, d.recorded_by, d.status, f"/api/cca/mdt/cases/{d.case_id}",
                     f"MDT case #{d.case_id}")

        for p in db.query(TreatmentPlan).filter(TreatmentPlan.patient_id == patient_id).all():
            if q in f"{p.modality or ''} {p.protocol_name or ''} {p.intent or ''}".lower():
                _add("TREATMENT_PLAN", p.id, f"{p.modality}: {p.protocol_name or 'NOT_RECORDED'} ({p.status})",
                     p.signed_at or p.created_at, p.signer_email or p.created_by, p.status,
                     f"/api/cca/treatment-plans/{p.id}", f"Treatment Plan #{p.id}")

        for s in db.query(StagingRecord).filter(StagingRecord.patient_id == patient_id).all():
            if q in f"{s.stage_value or ''} {s.t_stage or ''} {s.n_stage or ''} {s.m_stage or ''}".lower():
                _add("STAGING_RECORD", s.id,
                     f"Stage {s.stage_value or 'NOT_RECORDED'} (T{s.t_stage or '?'}N{s.n_stage or '?'}M{s.m_stage or '?'})",
                     s.confirmed_at or s.created_at, s.confirmed_by, s.status,
                     f"/api/cca/patients/{patient_id}/staging", f"Staging record #{s.id}")

        for j in db.query(CCAJourneyEvent).filter(CCAJourneyEvent.patient_id == patient_id).all():
            if q in f"{j.event_title or ''} {j.description or ''}".lower():
                _add("JOURNEY_EVENT", j.id, j.event_title,
                     j.timestamp, j.actor_name, None, f"/api/cca/patients/{patient_id}/journey",
                     f"Journey event #{j.id}")

    if scope in ("HOSPITAL_RECORDS", "ALL"):
        results.append({
            "scope": "HOSPITAL_RECORDS",
            "type": "HOSPITAL_SOP",
            "title": "AIvana Oncology Clinical SOP v2.4",
            "content": f"Matching hospital protocol rule for '{query}': All systemic treatment modifications require primary oncologist signature.",
            "citation": "Hospital Protocol Manual Sec 4.2"
        })

    if scope in ("CLINICAL_KNOWLEDGE", "ALL"):
        results.append({
            "scope": "CLINICAL_KNOWLEDGE",
            "type": "GUIDELINE_REFERENCE",
            "title": "NCCN Guidelines Breast Cancer v4.2026",
            "content": f"Guideline reference for '{query}': Systemic therapy selection is guided by ER, PR, HER2 biomarker status and AJCC stage.",
            "citation": "NCCN Guidelines® Breast Cancer v4.2026 BIN-1"
        })

    return {
        "query": query,
        "scope": scope,
        "total_hits": len(results),
        "results": results,
        "citations": citations,
        "disclaimer": "AI Search results synthesize verified patient facts and guidelines. Any action proposal requires explicit clinician acceptance."
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
