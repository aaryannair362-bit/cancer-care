"""
R10 Anaesthetist module (11 Additional Modules Detailed Developer Handoff, page 11) + the
generic Cross-Module Requirements (page 13) applied to R10 -- the only role from that PDF in
scope for this pass. Sibling of routers/cca_inpatient.py -- own file, imports shared
tenancy/actor helpers from .cca and the surgical-plan lookup helper from .cca_oncology_ext,
the established pattern in this codebase.

No dose-calculation or clinical-safety-threshold logic here (standing repo rule) -- every
write below is a structured capture, a status attestation, or a workflow-sequencing
transition, never a computed clinical judgment.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_admin, is_cca_anaesthetist, is_cca_surgical_oncologist, is_cca_surgical_nurse
from ..models_cca import CCAPatient
from ..models_cca_oncology_ext import (
    AnaesthesiaPreOpEvaluation, AnaesthesiaPreOpEvaluationVersion,
    AnaesthesiaIntraOpRecord, AnaesthesiaRecoveryRecord, SurgicalPlan,
)
from ..events import publish
from .cca import get_cca_db, _org_id, _actor, _check_patient_in_org
from .cca_oncology_ext import _get_org_surgical_plan, _surgical_plan_out

router = APIRouter(prefix="/api/cca", tags=["CCA Anaesthesia"])

_ASA_GRADES = ("I", "II", "III", "IV", "V", "VI")
_MEDICAL_CLEARANCE_STATUSES = ("Pending", "Cleared", "ClearedWithConditions", "NotCleared")
_POST_OP_DESTINATIONS = ("Ward", "HDU", "ICU")

_PRE_OP_FIELDS = (
    "diagnosis", "proposed_procedure", "asa_grade", "relevant_history",
    "previous_anaesthesia_complications", "current_medications_reviewed", "current_medications_notes",
    "allergies_reviewed", "allergies_notes", "prohibited_high_risk_drugs", "airway_assessment",
    "head_neck_dentition_findings", "system_review", "investigations_reviewed",
    "medical_clearance_status", "clearance_conditions", "anaesthetic_plan",
    "consent_obtained", "consent_notes",
)


def _require_anaesthetist(current_user: dict):
    """Only the Anaesthetist (or Admin) may write anaesthesia records -- deliberately
    narrower than the surgical-team gates: an Anaesthetist's own documentation is a distinct
    professional record, not something the operating surgeon or surgical nurse authors."""
    if not (is_cca_anaesthetist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Anaesthetist may perform this action")


def _require_anaesthesia_reader(current_user: dict):
    """Cross-module requirement: 'relevant summaries remain available to downstream care
    teams' -- the surgical team (surgeon, surgical nurse) can read anaesthesia records even
    though they cannot write them."""
    if not (
        is_cca_anaesthetist(current_user) or is_admin(current_user)
        or is_cca_surgical_oncologist(current_user) or is_cca_surgical_nurse(current_user)
    ):
        raise HTTPException(403, "Not authorized to view anaesthesia records")


def _validate_asa_grade(value):
    if value is not None and value not in _ASA_GRADES:
        raise HTTPException(422, f"asa_grade must be one of {_ASA_GRADES}")


def _validate_clearance_status(value):
    if value is not None and value not in _MEDICAL_CLEARANCE_STATUSES:
        raise HTTPException(422, f"medical_clearance_status must be one of {_MEDICAL_CLEARANCE_STATUSES}")


def _apply_pre_op_fields(evaluation: AnaesthesiaPreOpEvaluation, body: dict):
    if "asa_grade" in body:
        _validate_asa_grade(body["asa_grade"])
    if "medical_clearance_status" in body:
        _validate_clearance_status(body["medical_clearance_status"])
    for field in _PRE_OP_FIELDS:
        if field in body:
            setattr(evaluation, field, body[field])


def _pre_op_out(e: AnaesthesiaPreOpEvaluation) -> dict:
    return {
        "id": e.id, "surgical_plan_id": e.surgical_plan_id, "patient_id": e.patient_id,
        "diagnosis": e.diagnosis, "proposed_procedure": e.proposed_procedure, "asa_grade": e.asa_grade,
        "relevant_history": e.relevant_history,
        "previous_anaesthesia_complications": e.previous_anaesthesia_complications,
        "current_medications_reviewed": e.current_medications_reviewed,
        "current_medications_notes": e.current_medications_notes,
        "allergies_reviewed": e.allergies_reviewed, "allergies_notes": e.allergies_notes,
        "prohibited_high_risk_drugs": e.prohibited_high_risk_drugs,
        "airway_assessment": e.airway_assessment,
        "head_neck_dentition_findings": e.head_neck_dentition_findings,
        "system_review": e.system_review, "investigations_reviewed": e.investigations_reviewed,
        "medical_clearance_status": e.medical_clearance_status, "clearance_conditions": e.clearance_conditions,
        "anaesthetic_plan": e.anaesthetic_plan, "consent_obtained": e.consent_obtained,
        "consent_notes": e.consent_notes, "status": e.status,
        "evaluated_by": e.evaluated_by, "evaluated_at": e.evaluated_at.isoformat() if e.evaluated_at else None,
    }


@router.get("/surgical-plans/worklist")
def anaesthesia_surgical_plan_worklist(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Cross-patient worklist for the Anaesthetist -- surgical plans awaiting or already
    linked to a pre-op evaluation, org-wide (this role works off "who needs me next" across
    the whole caseload, matching radiation_phase_worklist's own reasoning for the same
    problem in the Radiation module)."""
    _require_anaesthesia_reader(current_user)
    org_id = _org_id(current_user)
    rows = (
        db.query(SurgicalPlan, CCAPatient)
        .join(CCAPatient, SurgicalPlan.patient_id == CCAPatient.id)
        .filter(CCAPatient.organization_id == org_id, SurgicalPlan.status.in_(["planned", "pre_op_ready", "scheduled"]))
        .order_by(SurgicalPlan.id.desc())
        .all()
    )
    out = []
    for plan, patient in rows:
        latest_evaluation = db.query(AnaesthesiaPreOpEvaluation).filter(
            AnaesthesiaPreOpEvaluation.surgical_plan_id == plan.id
        ).order_by(AnaesthesiaPreOpEvaluation.id.desc()).first()
        out.append({
            **_surgical_plan_out(plan), "patient_name": patient.name, "patient_mrn": patient.mrn,
            "pre_op_evaluation": _pre_op_out(latest_evaluation) if latest_evaluation else None,
        })
    return {"worklist": out}


def _get_org_pre_op_evaluation(db: Session, evaluation_id: int, org_id: int) -> AnaesthesiaPreOpEvaluation:
    evaluation = db.query(AnaesthesiaPreOpEvaluation).filter(AnaesthesiaPreOpEvaluation.id == evaluation_id).first()
    if not evaluation:
        raise HTTPException(404, "Anaesthesia pre-operative evaluation not found")
    _check_patient_in_org(db, evaluation.patient_id, org_id)
    return evaluation


@router.get("/surgical-plans/{plan_id}/anaesthesia/pre-op")
def get_pre_op_evaluation(plan_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_anaesthesia_reader(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    evaluation = db.query(AnaesthesiaPreOpEvaluation).filter(
        AnaesthesiaPreOpEvaluation.surgical_plan_id == plan.id
    ).order_by(AnaesthesiaPreOpEvaluation.id.desc()).first()
    return {"evaluation": _pre_op_out(evaluation) if evaluation else None}


@router.post("/surgical-plans/{plan_id}/anaesthesia/pre-op", status_code=201)
async def create_pre_op_evaluation(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Creates the (one) pre-op evaluation for this surgical plan. Once one exists, further
    changes go through PATCH .../pre-op (update while Draft, or amend once Finalized)."""
    _require_anaesthetist(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    existing = db.query(AnaesthesiaPreOpEvaluation).filter(
        AnaesthesiaPreOpEvaluation.surgical_plan_id == plan.id
    ).first()
    if existing:
        raise HTTPException(409, "A pre-operative evaluation already exists for this surgical plan -- use PATCH to update or amend it")
    body = await request.json()
    evaluation = AnaesthesiaPreOpEvaluation(
        surgical_plan_id=plan.id, patient_id=plan.patient_id, status="Draft",
        evaluated_by=_actor(current_user), evaluated_at=datetime.utcnow(),
    )
    _apply_pre_op_fields(evaluation, body)
    db.add(evaluation)
    db.flush()
    publish(
        db, "ANAESTHESIA_PRE_OP_EVALUATION_CREATED", patient_id=plan.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Anaesthesia pre-op evaluation started", category="TREATMENT",
        description=f"{_actor(current_user)} started the pre-operative anaesthesia evaluation for surgical plan #{plan.id}.",
    )
    db.commit()
    db.refresh(evaluation)
    return {"status": "success", "evaluation": _pre_op_out(evaluation)}


@router.patch("/surgical-plans/{plan_id}/anaesthesia/pre-op")
async def update_pre_op_evaluation(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """While Draft, edits in place. Once Finalized, the same "signed records must not be
    silently overwritten" rule as CCAEncounter (models_cca.py item 1.5) applies: an
    amendment_reason is required and the pre-amendment content is snapshotted into
    AnaesthesiaPreOpEvaluationVersion before the fields are updated."""
    _require_anaesthetist(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    evaluation = db.query(AnaesthesiaPreOpEvaluation).filter(
        AnaesthesiaPreOpEvaluation.surgical_plan_id == plan.id
    ).first()
    if not evaluation:
        raise HTTPException(404, "No pre-operative evaluation exists yet for this surgical plan -- create one first")
    body = await request.json()

    if evaluation.status == "Finalized":
        amendment_reason = body.get("amendment_reason")
        if not amendment_reason or not str(amendment_reason).strip():
            raise HTTPException(400, "amendment_reason is required to amend a finalized pre-operative evaluation")
        db.add(AnaesthesiaPreOpEvaluationVersion(
            evaluation_id=evaluation.id,
            snapshot=_pre_op_out(evaluation),
            amendment_reason=amendment_reason,
            amended_by=_actor(current_user),
            amended_at=datetime.utcnow(),
        ))

    _apply_pre_op_fields(evaluation, body)
    evaluation.evaluated_by = _actor(current_user)
    evaluation.evaluated_at = datetime.utcnow()
    db.commit()
    db.refresh(evaluation)
    return {"status": "success", "evaluation": _pre_op_out(evaluation)}


@router.post("/anaesthesia/pre-op/{evaluation_id}/finalize")
def finalize_pre_op_evaluation(evaluation_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Finalizing is what makes this evaluation eligible to satisfy SurgicalPlan's
    pre_op_ready gate (see routers/cca_oncology_ext.py's transition_surgical_plan) -- requires
    an ASA grade and a decided (non-Pending) medical_clearance_status first."""
    _require_anaesthetist(current_user)
    evaluation = _get_org_pre_op_evaluation(db, evaluation_id, _org_id(current_user))
    if evaluation.status == "Finalized":
        raise HTTPException(409, "This pre-operative evaluation is already finalized")
    if not evaluation.asa_grade:
        raise HTTPException(422, "asa_grade is required before finalizing")
    if not evaluation.medical_clearance_status or evaluation.medical_clearance_status == "Pending":
        raise HTTPException(422, "medical_clearance_status must be decided (not Pending) before finalizing")
    evaluation.status = "Finalized"
    publish(
        db, "ANAESTHESIA_PRE_OP_EVALUATION_FINALIZED", patient_id=evaluation.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Anaesthesia pre-op evaluation finalized", category="TREATMENT",
        description=f"{_actor(current_user)} finalized the pre-operative anaesthesia evaluation ({evaluation.medical_clearance_status}).",
    )
    db.commit()
    db.refresh(evaluation)
    return {"status": "success", "evaluation": _pre_op_out(evaluation)}


# ---------------------------------------------------------------------------
# Intra-operative anaesthesia record and post-anaesthesia recovery documentation.
# ---------------------------------------------------------------------------

def _intraop_record_out(r: AnaesthesiaIntraOpRecord) -> dict:
    return {
        "id": r.id, "surgical_plan_id": r.surgical_plan_id, "patient_id": r.patient_id,
        "anaesthesia_type": r.anaesthesia_type, "monitoring_notes": r.monitoring_notes,
        "airway_management": r.airway_management, "analgesia_given": r.analgesia_given,
        "intraop_events": r.intraop_events, "recorded_by": r.recorded_by,
        "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
    }


@router.get("/surgical-plans/{plan_id}/anaesthesia/intra-op")
def list_intraop_records(plan_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_anaesthesia_reader(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    rows = db.query(AnaesthesiaIntraOpRecord).filter(
        AnaesthesiaIntraOpRecord.surgical_plan_id == plan.id
    ).order_by(AnaesthesiaIntraOpRecord.recorded_at.asc()).all()
    return {"results": [_intraop_record_out(r) for r in rows]}


@router.post("/surgical-plans/{plan_id}/anaesthesia/intra-op", status_code=201)
async def record_intraop_anaesthesia(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_anaesthetist(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    body = await request.json()
    record = AnaesthesiaIntraOpRecord(
        surgical_plan_id=plan.id, patient_id=plan.patient_id,
        anaesthesia_type=body.get("anaesthesia_type"), monitoring_notes=body.get("monitoring_notes"),
        airway_management=body.get("airway_management"), analgesia_given=body.get("analgesia_given"),
        intraop_events=body.get("intraop_events"), recorded_by=_actor(current_user),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"status": "success", "record": _intraop_record_out(record)}


def _recovery_record_out(r: AnaesthesiaRecoveryRecord) -> dict:
    return {
        "id": r.id, "surgical_plan_id": r.surgical_plan_id, "patient_id": r.patient_id,
        "recovery_vitals": r.recovery_vitals, "pain_score": r.pain_score,
        "post_op_destination": r.post_op_destination,
        "readiness_for_discharge_confirmed": r.readiness_for_discharge_confirmed,
        "discharge_criteria_notes": r.discharge_criteria_notes, "recorded_by": r.recorded_by,
        "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
    }


@router.get("/surgical-plans/{plan_id}/anaesthesia/recovery")
def list_recovery_records(plan_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_anaesthesia_reader(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    rows = db.query(AnaesthesiaRecoveryRecord).filter(
        AnaesthesiaRecoveryRecord.surgical_plan_id == plan.id
    ).order_by(AnaesthesiaRecoveryRecord.recorded_at.asc()).all()
    return {"results": [_recovery_record_out(r) for r in rows]}


@router.post("/surgical-plans/{plan_id}/anaesthesia/recovery", status_code=201)
async def record_recovery(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_anaesthetist(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    body = await request.json()
    destination = body.get("post_op_destination")
    if destination is not None and destination not in _POST_OP_DESTINATIONS:
        raise HTTPException(422, f"post_op_destination must be one of {_POST_OP_DESTINATIONS}")
    record = AnaesthesiaRecoveryRecord(
        surgical_plan_id=plan.id, patient_id=plan.patient_id,
        recovery_vitals=body.get("recovery_vitals"), pain_score=body.get("pain_score"),
        post_op_destination=destination,
        readiness_for_discharge_confirmed=bool(body.get("readiness_for_discharge_confirmed", False)),
        discharge_criteria_notes=body.get("discharge_criteria_notes"), recorded_by=_actor(current_user),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"status": "success", "record": _recovery_record_out(record)}
