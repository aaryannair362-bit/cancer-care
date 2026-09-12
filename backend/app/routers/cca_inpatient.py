"""
Inpatient Oncology (C.21) -- Product 1 vs Product 2 gap report finding: no CCA-specific
representation at all. See models_cca_inpatient.py's module docstring for the full design
rationale (why this stays in CCA's own patient identity space rather than bridging to the
generic HMS ward module).

No dose-calculation or clinical-safety-threshold/scoring logic here (standing repo rule) --
every write below is a structured capture or a workflow-sequencing transition, never a
computed clinical judgment.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_admin, is_nurse, is_head_nurse, is_cca_infusion_nurse
from ..models_cca import CCAPatient
from ..models_cca_inpatient import (
    InpatientAdmission, InpatientHistoryAndPhysical, InpatientProblemListItem,
    InpatientMedicationAdministration, InpatientWardRoundNote, InpatientNursingFlowsheet,
    InpatientDeteriorationEvent, InpatientGoalsOfCare, InpatientTransferHandover,
    InpatientDischargeSummary, InpatientDeathDocumentation,
)
from ..events import publish
from .cca import get_cca_db, _org_id, _actor, _get_org_patient, _check_patient_in_org, _require_clinician

router = APIRouter(prefix="/api/cca", tags=["CCA Inpatient Oncology"])


def _require_inpatient_nurse(current_user: dict):
    """No dedicated CCA Inpatient Nurse role exists in this codebase's role vocabulary --
    the closest existing nursing roles stand in, matching how record_radiation_fraction_event
    reuses CCARadiologist as the RTT stand-in elsewhere in this codebase."""
    if not (is_nurse(current_user) or is_head_nurse(current_user) or is_cca_infusion_nurse(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only nursing staff may perform this action")


def _admission_dict(a: InpatientAdmission) -> dict:
    return {
        "id": a.id, "patient_id": a.patient_id, "episode_id": a.episode_id,
        "admission_reason": a.admission_reason, "admitting_diagnosis": a.admitting_diagnosis,
        "admission_type": a.admission_type, "ward": a.ward, "bed": a.bed,
        "admitting_clinician": a.admitting_clinician, "status": a.status,
        "admitted_at": a.admitted_at.isoformat() if a.admitted_at else None,
        "discharged_at": a.discharged_at.isoformat() if a.discharged_at else None,
        "requested_by": a.requested_by,
    }


def _get_org_admission(db: Session, admission_id: int, org_id: int) -> InpatientAdmission:
    admission = db.query(InpatientAdmission).filter(InpatientAdmission.id == admission_id).first()
    if not admission:
        raise HTTPException(404, "Inpatient admission not found")
    _check_patient_in_org(db, admission.patient_id, org_id)
    return admission


@router.post("/inpatient/admissions", status_code=201)
async def create_admission(request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Bed request / admission -- starts BED_REQUESTED; POST .../admit moves it to ADMITTED
    once a ward/bed is actually assigned, matching this codebase's draft/committed posture
    for every other clinical workflow."""
    _require_clinician(current_user)
    org_id = _org_id(current_user)
    body = await request.json()
    patient_id = body.get("patient_id")
    if not patient_id:
        raise HTTPException(422, "patient_id is required")
    _get_org_patient(db, patient_id, org_id)
    reason = (body.get("admission_reason") or "").strip()
    if not reason:
        raise HTTPException(422, "admission_reason is required")
    actor = _actor(current_user)
    admission = InpatientAdmission(
        patient_id=patient_id, episode_id=body.get("episode_id"), admission_reason=reason,
        admitting_diagnosis=body.get("admitting_diagnosis"), admission_type=body.get("admission_type", "Elective"),
        admitting_clinician=body.get("admitting_clinician", actor), requested_by=actor,
    )
    db.add(admission)
    db.flush()
    publish(
        db, "INPATIENT_ADMISSION_REQUESTED", patient_id=patient_id, actor=actor, role=current_user.get("role"),
        title="Inpatient bed requested", category="INPATIENT",
        description=f"{actor} requested inpatient admission: {reason}",
        admission_id=admission.id,
    )
    db.commit()
    db.refresh(admission)
    return {"status": "success", "admission": _admission_dict(admission)}


@router.get("/inpatient/admissions")
def list_admissions(status: str = None, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Org-wide inpatient worklist."""
    query = db.query(InpatientAdmission).join(
        CCAPatient, InpatientAdmission.patient_id == CCAPatient.id
    ).filter(CCAPatient.organization_id == _org_id(current_user))
    if status:
        query = query.filter(InpatientAdmission.status == status)
    rows = query.order_by(InpatientAdmission.id.desc()).all()
    return {"admissions": [_admission_dict(a) for a in rows]}


@router.get("/patients/{patient_id}/inpatient/admissions")
def list_patient_admissions(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(InpatientAdmission).filter(InpatientAdmission.patient_id == patient_id).order_by(InpatientAdmission.id.desc()).all()
    return {"admissions": [_admission_dict(a) for a in rows]}


@router.get("/inpatient/admissions/{id}")
def get_admission(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    return {
        "admission": _admission_dict(admission),
        "open_problem_count": db.query(InpatientProblemListItem).filter(
            InpatientProblemListItem.admission_id == admission.id, InpatientProblemListItem.status == "Active"
        ).count(),
        "has_signed_hp": db.query(InpatientHistoryAndPhysical).filter(
            InpatientHistoryAndPhysical.admission_id == admission.id, InpatientHistoryAndPhysical.status == "SIGNED"
        ).first() is not None,
    }


@router.post("/inpatient/admissions/{id}/admit")
async def admit_patient(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    _require_clinician(current_user)
    if admission.status != "BED_REQUESTED":
        raise HTTPException(409, f"Cannot admit from status {admission.status}")
    body = await request.json()
    ward = (body.get("ward") or "").strip()
    if not ward:
        raise HTTPException(422, "ward is required")
    admission.ward = ward
    admission.bed = body.get("bed")
    admission.status = "ADMITTED"
    admission.admitted_at = datetime.utcnow()
    actor = _actor(current_user)
    publish(
        db, "INPATIENT_ADMITTED", patient_id=admission.patient_id, actor=actor, role=current_user.get("role"),
        title="Patient admitted", category="INPATIENT",
        description=f"{actor} admitted the patient to {ward}" + (f" bed {admission.bed}" if admission.bed else "") + ".",
        admission_id=admission.id,
    )
    db.commit()
    db.refresh(admission)
    return {"status": "success", "admission": _admission_dict(admission)}


@router.post("/inpatient/admissions/{id}/discharge")
async def discharge_patient(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """A signed discharge summary must exist first -- a sequencing gate (like every other
    sign-before-close workflow in this codebase), never a clinical-safety computation."""
    admission = _get_org_admission(db, id, _org_id(current_user))
    _require_clinician(current_user)
    if admission.status not in ("ADMITTED", "TRANSFERRED"):
        raise HTTPException(409, f"Cannot discharge from status {admission.status}")
    signed_summary = db.query(InpatientDischargeSummary).filter(
        InpatientDischargeSummary.admission_id == admission.id, InpatientDischargeSummary.status == "SIGNED"
    ).first()
    if not signed_summary:
        raise HTTPException(409, "A signed discharge summary is required before discharge")
    admission.status = "DISCHARGED"
    admission.discharged_at = datetime.utcnow()
    actor = _actor(current_user)
    publish(
        db, "INPATIENT_DISCHARGED", patient_id=admission.patient_id, actor=actor, role=current_user.get("role"),
        title="Patient discharged", category="INPATIENT",
        description=f"{actor} discharged the patient.", admission_id=admission.id,
    )
    db.commit()
    db.refresh(admission)
    return {"status": "success", "admission": _admission_dict(admission)}


# ---------------------------------------------------------------------------
# Oncology History & Physical
# ---------------------------------------------------------------------------

def _hp_dict(h: InpatientHistoryAndPhysical) -> dict:
    return {
        "id": h.id, "admission_id": h.admission_id, "chief_complaint": h.chief_complaint,
        "history_of_present_illness": h.history_of_present_illness, "past_oncologic_history": h.past_oncologic_history,
        "current_treatment_summary": h.current_treatment_summary, "performance_status": h.performance_status,
        "allergies": h.allergies, "medications_on_admission": h.medications_on_admission or [],
        "physical_exam": h.physical_exam, "assessment_and_plan": h.assessment_and_plan,
        "status": h.status, "authored_by": h.authored_by,
        "signed_at": h.signed_at.isoformat() if h.signed_at else None,
    }


@router.post("/inpatient/admissions/{id}/history-physical", status_code=201)
async def upsert_history_physical(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    _require_clinician(current_user)
    body = await request.json()
    chief_complaint = (body.get("chief_complaint") or "").strip()
    if not chief_complaint:
        raise HTTPException(422, "chief_complaint is required")
    hp = db.query(InpatientHistoryAndPhysical).filter(InpatientHistoryAndPhysical.admission_id == admission.id).order_by(InpatientHistoryAndPhysical.id.desc()).first()
    if hp and hp.status == "SIGNED":
        raise HTTPException(409, "The H&P for this admission is already signed")
    actor = _actor(current_user)
    if not hp:
        hp = InpatientHistoryAndPhysical(admission_id=admission.id, chief_complaint=chief_complaint, authored_by=actor)
        db.add(hp)
    else:
        hp.chief_complaint = chief_complaint
    hp.history_of_present_illness = body.get("history_of_present_illness")
    hp.past_oncologic_history = body.get("past_oncologic_history")
    hp.current_treatment_summary = body.get("current_treatment_summary")
    hp.performance_status = body.get("performance_status")
    hp.allergies = body.get("allergies")
    hp.medications_on_admission = body.get("medications_on_admission")
    hp.physical_exam = body.get("physical_exam")
    hp.assessment_and_plan = body.get("assessment_and_plan")
    db.commit()
    db.refresh(hp)
    return {"status": "success", "history_physical": _hp_dict(hp)}


@router.get("/inpatient/admissions/{id}/history-physical")
def get_history_physical(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    hp = db.query(InpatientHistoryAndPhysical).filter(InpatientHistoryAndPhysical.admission_id == admission.id).order_by(InpatientHistoryAndPhysical.id.desc()).first()
    return {"history_physical": _hp_dict(hp) if hp else None}


@router.post("/inpatient/history-physicals/{id}/sign")
def sign_history_physical(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    hp = db.query(InpatientHistoryAndPhysical).filter(InpatientHistoryAndPhysical.id == id).first()
    if not hp:
        raise HTTPException(404, "History & Physical not found")
    admission = db.query(InpatientAdmission).filter(InpatientAdmission.id == hp.admission_id).first()
    _check_patient_in_org(db, admission.patient_id, _org_id(current_user))
    _require_clinician(current_user)
    if hp.status == "SIGNED":
        raise HTTPException(409, "This H&P is already signed")
    hp.status = "SIGNED"
    hp.signed_at = datetime.utcnow()
    db.commit()
    db.refresh(hp)
    return {"status": "success", "history_physical": _hp_dict(hp)}


# ---------------------------------------------------------------------------
# Problem List
# ---------------------------------------------------------------------------

def _problem_dict(p: InpatientProblemListItem) -> dict:
    return {
        "id": p.id, "admission_id": p.admission_id, "problem": p.problem, "status": p.status,
        "priority": p.priority, "onset_date": p.onset_date.isoformat() if p.onset_date else None,
        "resolved_date": p.resolved_date.isoformat() if p.resolved_date else None, "notes": p.notes,
        "created_by": p.created_by,
    }


@router.post("/inpatient/admissions/{id}/problems", status_code=201)
async def add_problem(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    _require_clinician(current_user)
    body = await request.json()
    problem = (body.get("problem") or "").strip()
    if not problem:
        raise HTTPException(422, "problem is required")
    item = InpatientProblemListItem(
        admission_id=admission.id, problem=problem, priority=body.get("priority", "Routine"),
        onset_date=datetime.fromisoformat(body["onset_date"]).date() if body.get("onset_date") else None,
        notes=body.get("notes"), created_by=_actor(current_user),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"status": "success", "problem": _problem_dict(item)}


@router.get("/inpatient/admissions/{id}/problems")
def list_problems(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    rows = db.query(InpatientProblemListItem).filter(InpatientProblemListItem.admission_id == admission.id).order_by(InpatientProblemListItem.id.desc()).all()
    return {"problems": [_problem_dict(p) for p in rows]}


@router.post("/inpatient/problems/{id}/resolve")
async def resolve_problem(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    item = db.query(InpatientProblemListItem).filter(InpatientProblemListItem.id == id).first()
    if not item:
        raise HTTPException(404, "Problem list item not found")
    admission = db.query(InpatientAdmission).filter(InpatientAdmission.id == item.admission_id).first()
    _check_patient_in_org(db, admission.patient_id, _org_id(current_user))
    _require_clinician(current_user)
    item.status = "Resolved"
    item.resolved_date = datetime.utcnow().date()
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    if body.get("notes"):
        item.notes = body["notes"]
    db.commit()
    db.refresh(item)
    return {"status": "success", "problem": _problem_dict(item)}


# ---------------------------------------------------------------------------
# Systemic-therapy-linked MAR
# ---------------------------------------------------------------------------

def _med_admin_dict(m: InpatientMedicationAdministration) -> dict:
    return {
        "id": m.id, "admission_id": m.admission_id, "treatment_order_id": m.treatment_order_id,
        "drug_name": m.drug_name, "dose_administered": m.dose_administered, "route": m.route,
        "scheduled_time": m.scheduled_time.isoformat() if m.scheduled_time else None,
        "administered_time": m.administered_time.isoformat() if m.administered_time else None,
        "status": m.status, "hold_reason": m.hold_reason,
        "administered_by": m.administered_by, "witnessed_by": m.witnessed_by,
    }


@router.post("/inpatient/admissions/{id}/medications", status_code=201)
async def schedule_medication(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    body = await request.json()
    drug_name = (body.get("drug_name") or "").strip()
    if not drug_name:
        raise HTTPException(422, "drug_name is required")
    med = InpatientMedicationAdministration(
        admission_id=admission.id, treatment_order_id=body.get("treatment_order_id"), drug_name=drug_name,
        route=body.get("route"),
        scheduled_time=datetime.fromisoformat(body["scheduled_time"]) if body.get("scheduled_time") else datetime.utcnow(),
    )
    db.add(med)
    db.commit()
    db.refresh(med)
    return {"status": "success", "medication": _med_admin_dict(med)}


@router.get("/inpatient/admissions/{id}/medications")
def list_medications(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    rows = db.query(InpatientMedicationAdministration).filter(InpatientMedicationAdministration.admission_id == admission.id).order_by(InpatientMedicationAdministration.scheduled_time).all()
    return {"medications": [_med_admin_dict(m) for m in rows]}


_MED_ADMIN_STATUSES = ("Given", "Held", "Refused", "Omitted")


@router.post("/inpatient/medications/{id}/administer")
async def administer_medication(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    med = db.query(InpatientMedicationAdministration).filter(InpatientMedicationAdministration.id == id).first()
    if not med:
        raise HTTPException(404, "Medication administration record not found")
    admission = db.query(InpatientAdmission).filter(InpatientAdmission.id == med.admission_id).first()
    _check_patient_in_org(db, admission.patient_id, _org_id(current_user))
    _require_inpatient_nurse(current_user)
    if med.status != "Scheduled":
        raise HTTPException(409, f"This medication is already recorded as {med.status}")
    body = await request.json()
    status_value = body.get("status")
    if status_value not in _MED_ADMIN_STATUSES:
        raise HTTPException(422, f"status must be one of {_MED_ADMIN_STATUSES}")
    if status_value != "Given" and not (body.get("hold_reason") or "").strip():
        raise HTTPException(422, "hold_reason is required when status is not Given")
    med.status = status_value
    med.dose_administered = body.get("dose_administered")
    med.hold_reason = body.get("hold_reason")
    med.administered_time = datetime.utcnow()
    med.administered_by = _actor(current_user)
    med.witnessed_by = body.get("witnessed_by")
    db.commit()
    db.refresh(med)
    return {"status": "success", "medication": _med_admin_dict(med)}


# ---------------------------------------------------------------------------
# Ward Round Notes
# ---------------------------------------------------------------------------

def _ward_round_dict(w: InpatientWardRoundNote) -> dict:
    return {
        "id": w.id, "admission_id": w.admission_id,
        "round_datetime": w.round_datetime.isoformat() if w.round_datetime else None,
        "department": w.department, "subjective": w.subjective, "objective": w.objective,
        "assessment": w.assessment, "plan": w.plan, "performance_status_today": w.performance_status_today,
        "author": w.author,
    }


@router.post("/inpatient/admissions/{id}/ward-rounds", status_code=201)
async def add_ward_round_note(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    _require_clinician(current_user)
    body = await request.json()
    assessment = (body.get("assessment") or "").strip()
    plan = (body.get("plan") or "").strip()
    if not (assessment and plan):
        raise HTTPException(422, "assessment and plan are both required")
    note = InpatientWardRoundNote(
        admission_id=admission.id, department=body.get("department", "Oncology"),
        subjective=body.get("subjective"), objective=body.get("objective"), assessment=assessment, plan=plan,
        performance_status_today=body.get("performance_status_today"), author=_actor(current_user),
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return {"status": "success", "ward_round_note": _ward_round_dict(note)}


@router.get("/inpatient/admissions/{id}/ward-rounds")
def list_ward_round_notes(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    rows = db.query(InpatientWardRoundNote).filter(InpatientWardRoundNote.admission_id == admission.id).order_by(InpatientWardRoundNote.round_datetime.desc()).all()
    return {"ward_round_notes": [_ward_round_dict(w) for w in rows]}


# ---------------------------------------------------------------------------
# Nursing Flowsheet
# ---------------------------------------------------------------------------

def _flowsheet_dict(f: InpatientNursingFlowsheet) -> dict:
    return {
        "id": f.id, "admission_id": f.admission_id,
        "recorded_at": f.recorded_at.isoformat() if f.recorded_at else None,
        "vitals": f.vitals or {}, "intake_output": f.intake_output or {},
        "pain_score": f.pain_score, "mobility_status": f.mobility_status, "notes": f.notes,
        "recorded_by": f.recorded_by,
    }


@router.post("/inpatient/admissions/{id}/flowsheet", status_code=201)
async def add_flowsheet_entry(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    _require_inpatient_nurse(current_user)
    body = await request.json()
    entry = InpatientNursingFlowsheet(
        admission_id=admission.id, vitals=body.get("vitals"), intake_output=body.get("intake_output"),
        pain_score=body.get("pain_score"), mobility_status=body.get("mobility_status"), notes=body.get("notes"),
        recorded_by=_actor(current_user),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"status": "success", "flowsheet_entry": _flowsheet_dict(entry)}


@router.get("/inpatient/admissions/{id}/flowsheet")
def list_flowsheet_entries(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    rows = db.query(InpatientNursingFlowsheet).filter(InpatientNursingFlowsheet.admission_id == admission.id).order_by(InpatientNursingFlowsheet.recorded_at.desc()).all()
    return {"flowsheet_entries": [_flowsheet_dict(f) for f in rows]}


# ---------------------------------------------------------------------------
# Deterioration / Escalation
# ---------------------------------------------------------------------------

def _deterioration_dict(d: InpatientDeteriorationEvent) -> dict:
    return {
        "id": d.id, "admission_id": d.admission_id,
        "detected_at": d.detected_at.isoformat() if d.detected_at else None, "trigger": d.trigger,
        "early_warning_score": d.early_warning_score, "rapid_response_called": bool(d.rapid_response_called),
        "escalated_to": d.escalated_to, "escalation_time": d.escalation_time.isoformat() if d.escalation_time else None,
        "response_action": d.response_action, "outcome": d.outcome, "reported_by": d.reported_by,
    }


@router.post("/inpatient/admissions/{id}/deterioration-events", status_code=201)
async def record_deterioration_event(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    body = await request.json()
    trigger = (body.get("trigger") or "").strip()
    if not trigger:
        raise HTTPException(422, "trigger is required")
    actor = _actor(current_user)
    event = InpatientDeteriorationEvent(
        admission_id=admission.id, trigger=trigger, early_warning_score=body.get("early_warning_score"),
        rapid_response_called=bool(body.get("rapid_response_called", False)), escalated_to=body.get("escalated_to"),
        escalation_time=datetime.utcnow() if body.get("escalated_to") else None,
        response_action=body.get("response_action"), reported_by=actor,
    )
    db.add(event)
    db.flush()
    publish(
        db, "INPATIENT_DETERIORATION_REPORTED", patient_id=admission.patient_id, actor=actor, role=current_user.get("role"),
        title="Patient deterioration reported", category="INPATIENT",
        description=f"{actor} reported: {trigger}", admission_id=admission.id, deterioration_event_id=event.id,
    )
    db.commit()
    db.refresh(event)
    return {"status": "success", "deterioration_event": _deterioration_dict(event)}


@router.get("/inpatient/admissions/{id}/deterioration-events")
def list_deterioration_events(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    rows = db.query(InpatientDeteriorationEvent).filter(InpatientDeteriorationEvent.admission_id == admission.id).order_by(InpatientDeteriorationEvent.detected_at.desc()).all()
    return {"deterioration_events": [_deterioration_dict(d) for d in rows]}


# ---------------------------------------------------------------------------
# Goals of Care
# ---------------------------------------------------------------------------

def _goals_dict(g: InpatientGoalsOfCare) -> dict:
    return {
        "id": g.id, "admission_id": g.admission_id, "code_status": g.code_status,
        "goals_discussed_with": g.goals_discussed_with,
        "discussion_date": g.discussion_date.isoformat() if g.discussion_date else None,
        "summary": g.summary, "documented_by": g.documented_by,
    }


@router.post("/inpatient/admissions/{id}/goals-of-care", status_code=201)
async def upsert_goals_of_care(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    _require_clinician(current_user)
    body = await request.json()
    code_status = body.get("code_status")
    if code_status not in ("Full Code", "DNR", "DNI", "Comfort Care"):
        raise HTTPException(422, "code_status must be one of Full Code, DNR, DNI, Comfort Care")
    goals = InpatientGoalsOfCare(
        admission_id=admission.id, code_status=code_status, goals_discussed_with=body.get("goals_discussed_with"),
        discussion_date=datetime.fromisoformat(body["discussion_date"]).date() if body.get("discussion_date") else datetime.utcnow().date(),
        summary=body.get("summary"), documented_by=_actor(current_user),
    )
    db.add(goals)
    db.commit()
    db.refresh(goals)
    return {"status": "success", "goals_of_care": _goals_dict(goals)}


@router.get("/inpatient/admissions/{id}/goals-of-care")
def list_goals_of_care(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    rows = db.query(InpatientGoalsOfCare).filter(InpatientGoalsOfCare.admission_id == admission.id).order_by(InpatientGoalsOfCare.id.desc()).all()
    return {"goals_of_care": [_goals_dict(g) for g in rows]}


# ---------------------------------------------------------------------------
# Transfer / Handover
# ---------------------------------------------------------------------------

def _transfer_dict(t: InpatientTransferHandover) -> dict:
    return {
        "id": t.id, "admission_id": t.admission_id, "transfer_type": t.transfer_type,
        "from_location": t.from_location, "to_location": t.to_location, "handover_summary": t.handover_summary,
        "handed_over_by": t.handed_over_by, "received_by": t.received_by,
        "transfer_datetime": t.transfer_datetime.isoformat() if t.transfer_datetime else None,
    }


@router.post("/inpatient/admissions/{id}/transfers", status_code=201)
async def record_transfer(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    body = await request.json()
    summary = (body.get("handover_summary") or "").strip()
    if not summary:
        raise HTTPException(422, "handover_summary is required")
    actor = _actor(current_user)
    transfer = InpatientTransferHandover(
        admission_id=admission.id, transfer_type=body.get("transfer_type"), from_location=body.get("from_location"),
        to_location=body.get("to_location"), handover_summary=summary, handed_over_by=actor,
        received_by=body.get("received_by"),
    )
    db.add(transfer)
    if body.get("to_location"):
        admission.ward = body["to_location"]
        admission.status = "TRANSFERRED"
    db.commit()
    db.refresh(transfer)
    return {"status": "success", "transfer": _transfer_dict(transfer)}


@router.get("/inpatient/admissions/{id}/transfers")
def list_transfers(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    rows = db.query(InpatientTransferHandover).filter(InpatientTransferHandover.admission_id == admission.id).order_by(InpatientTransferHandover.transfer_datetime.desc()).all()
    return {"transfers": [_transfer_dict(t) for t in rows]}


# ---------------------------------------------------------------------------
# Oncology Discharge Summary
# ---------------------------------------------------------------------------

def _discharge_summary_dict(s: InpatientDischargeSummary) -> dict:
    return {
        "id": s.id, "admission_id": s.admission_id, "discharge_diagnosis": s.discharge_diagnosis,
        "hospital_course": s.hospital_course, "procedures_during_stay": s.procedures_during_stay,
        "medications_at_discharge": s.medications_at_discharge or [], "follow_up_plan": s.follow_up_plan,
        "pending_results": s.pending_results, "discharge_disposition": s.discharge_disposition,
        "condition_at_discharge": s.condition_at_discharge, "status": s.status, "signed_by": s.signed_by,
        "signed_at": s.signed_at.isoformat() if s.signed_at else None,
    }


@router.post("/inpatient/admissions/{id}/discharge-summary", status_code=201)
async def upsert_discharge_summary(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    _require_clinician(current_user)
    body = await request.json()
    summary = db.query(InpatientDischargeSummary).filter(InpatientDischargeSummary.admission_id == admission.id).order_by(InpatientDischargeSummary.id.desc()).first()
    if summary and summary.status == "SIGNED":
        raise HTTPException(409, "The discharge summary for this admission is already signed")
    actor = _actor(current_user)
    if not summary:
        summary = InpatientDischargeSummary(admission_id=admission.id, created_by=actor)
        db.add(summary)
    summary.discharge_diagnosis = body.get("discharge_diagnosis")
    summary.hospital_course = body.get("hospital_course")
    summary.procedures_during_stay = body.get("procedures_during_stay")
    summary.medications_at_discharge = body.get("medications_at_discharge")
    summary.follow_up_plan = body.get("follow_up_plan")
    summary.pending_results = body.get("pending_results")
    summary.discharge_disposition = body.get("discharge_disposition")
    summary.condition_at_discharge = body.get("condition_at_discharge")
    db.commit()
    db.refresh(summary)
    return {"status": "success", "discharge_summary": _discharge_summary_dict(summary)}


@router.get("/inpatient/admissions/{id}/discharge-summary")
def get_discharge_summary(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    summary = db.query(InpatientDischargeSummary).filter(InpatientDischargeSummary.admission_id == admission.id).order_by(InpatientDischargeSummary.id.desc()).first()
    return {"discharge_summary": _discharge_summary_dict(summary) if summary else None}


@router.post("/inpatient/discharge-summaries/{id}/sign")
async def sign_discharge_summary(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    summary = db.query(InpatientDischargeSummary).filter(InpatientDischargeSummary.id == id).first()
    if not summary:
        raise HTTPException(404, "Discharge summary not found")
    admission = db.query(InpatientAdmission).filter(InpatientAdmission.id == summary.admission_id).first()
    _check_patient_in_org(db, admission.patient_id, _org_id(current_user))
    _require_clinician(current_user)
    if summary.status == "SIGNED":
        raise HTTPException(409, "This discharge summary is already signed")
    if not (summary.discharge_diagnosis and summary.hospital_course and summary.follow_up_plan):
        raise HTTPException(422, "discharge_diagnosis, hospital_course, and follow_up_plan are all required before signing")
    actor = _actor(current_user)
    summary.status = "SIGNED"
    summary.signed_by = actor
    summary.signed_at = datetime.utcnow()
    publish(
        db, "INPATIENT_DISCHARGE_SUMMARY_SIGNED", patient_id=admission.patient_id, actor=actor, role=current_user.get("role"),
        title="Discharge summary signed", category="INPATIENT",
        description=f"{actor} signed the discharge summary for this admission.", admission_id=admission.id,
    )
    db.commit()
    db.refresh(summary)
    return {"status": "success", "discharge_summary": _discharge_summary_dict(summary)}


# ---------------------------------------------------------------------------
# Death Documentation
# ---------------------------------------------------------------------------

def _death_doc_dict(d: InpatientDeathDocumentation) -> dict:
    return {
        "id": d.id, "admission_id": d.admission_id, "date_of_death": d.date_of_death.isoformat() if d.date_of_death else None,
        "time_of_death": d.time_of_death, "immediate_cause": d.immediate_cause,
        "contributing_factors": d.contributing_factors, "certifying_clinician": d.certifying_clinician,
        "family_notified": bool(d.family_notified), "family_notified_by": d.family_notified_by,
        "autopsy_requested": bool(d.autopsy_requested), "autopsy_consent": d.autopsy_consent,
        "death_certificate_number": d.death_certificate_number,
    }


@router.post("/inpatient/admissions/{id}/death-documentation", status_code=201)
async def record_death_documentation(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    _require_clinician(current_user)
    if admission.status == "DECEASED":
        raise HTTPException(409, "Death documentation already recorded for this admission")
    body = await request.json()
    immediate_cause = (body.get("immediate_cause") or "").strip()
    certifying_clinician = (body.get("certifying_clinician") or "").strip()
    if not (immediate_cause and certifying_clinician):
        raise HTTPException(422, "immediate_cause and certifying_clinician are both required")
    actor = _actor(current_user)
    doc = InpatientDeathDocumentation(
        admission_id=admission.id,
        date_of_death=datetime.fromisoformat(body["date_of_death"]).date() if body.get("date_of_death") else datetime.utcnow().date(),
        time_of_death=body.get("time_of_death"), immediate_cause=immediate_cause,
        contributing_factors=body.get("contributing_factors"), certifying_clinician=certifying_clinician,
        family_notified=bool(body.get("family_notified", False)), family_notified_by=body.get("family_notified_by"),
        autopsy_requested=bool(body.get("autopsy_requested", False)), autopsy_consent=body.get("autopsy_consent"),
        death_certificate_number=body.get("death_certificate_number"), created_by=actor,
    )
    db.add(doc)
    admission.status = "DECEASED"
    admission.discharged_at = datetime.utcnow()
    publish(
        db, "INPATIENT_DEATH_DOCUMENTED", patient_id=admission.patient_id, actor=actor, role=current_user.get("role"),
        title="Death documented", category="INPATIENT",
        description=f"{actor} documented the patient's death.", admission_id=admission.id,
    )
    db.commit()
    db.refresh(doc)
    return {"status": "success", "death_documentation": _death_doc_dict(doc)}


@router.get("/inpatient/admissions/{id}/death-documentation")
def get_death_documentation(id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    admission = _get_org_admission(db, id, _org_id(current_user))
    doc = db.query(InpatientDeathDocumentation).filter(InpatientDeathDocumentation.admission_id == admission.id).first()
    return {"death_documentation": _death_doc_dict(doc) if doc else None}
