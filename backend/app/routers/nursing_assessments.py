"""
Nursing Workflows' remaining structured-assessment gaps, closed in one pass: Admission
Assessment, Pain Assessment, Fall Risk Assessment, Pressure Ulcer Assessment. All four were
explicitly flagged as a deliberate fast-follow (not silently dropped) in
routers/nursing_charting.py's own module docstring when IV Fluid Management and Intake/Output
Charting were built. Access control mirrors nursing_charting.py exactly (Nurse must hold an
Active NurseAssignment for the patient; HeadNurse can act on any patient in the org) -- same
shape, not reinvented.

Fall Risk and Pressure Ulcer use real, standard bedside scoring instruments (Morse Fall Scale
and Braden Scale respectively) rather than an invented scale, since both are widely-used,
well-defined clinical tools with no ambiguity about what the input fields or score bands mean.
Both compute and persist risk_score/risk_level at creation time rather than deriving it at read
time, so a later change to scoring bands never rewrites a historical assessment's clinical
meaning (same reasoning DispensingRecord's own docstring gives for snapshotting price at
dispense time).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_head_nurse, is_nurse, log_audit
from ..database import get_db
from ..models import (
    AdmissionAssessment, FallRiskAssessment, NurseAssignment, Patient, PainAssessment,
    PressureUlcerAssessment,
)

router = APIRouter(tags=["nursing-assessments"])


def _require_nursing_access(user: dict) -> None:
    if not (is_nurse(user) or is_head_nurse(user)):
        raise HTTPException(403, "Only nurses and head nurses can record nursing assessments")


def _get_assigned_patient(db: Session, patient_id: int, current_user: dict) -> Patient:
    """Same org-scope + assignment check as nursing_charting.py's own helper of the same name."""
    patient = db.query(Patient).filter(
        Patient.id == patient_id, Patient.organization_id == current_user.get("organization_id")
    ).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    if is_nurse(current_user):
        assignment = db.query(NurseAssignment).filter(
            NurseAssignment.patient_id == patient_id, NurseAssignment.nurse_id == current_user["id"],
            NurseAssignment.status == "Active",
        ).first()
        if not assignment:
            raise HTTPException(403, "Not assigned to this patient")
    return patient


# ---------------------------------------------------------------------------
# Admission Assessment -- one per admission
# ---------------------------------------------------------------------------

class AdmissionAssessmentCreate(BaseModel):
    patient_id: int
    presenting_complaint: Optional[str] = None
    known_allergies: Optional[str] = None
    past_medical_history: Optional[str] = None
    current_medications: Optional[str] = None
    functional_status: Optional[str] = None
    risk_screening_notes: Optional[str] = None


def _admission_assessment_out(a: AdmissionAssessment) -> dict:
    return {
        "id": a.id, "patient_id": a.patient_id, "presenting_complaint": a.presenting_complaint,
        "known_allergies": a.known_allergies, "past_medical_history": a.past_medical_history,
        "current_medications": a.current_medications, "functional_status": a.functional_status,
        "risk_screening_notes": a.risk_screening_notes, "assessed_by": a.assessed_by,
        "assessed_at": a.assessed_at.isoformat(),
    }


@router.post("/api/ipd/admission-assessments", status_code=201)
def create_admission_assessment(payload: AdmissionAssessmentCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, payload.patient_id, current_user)
    org_id = current_user.get("organization_id")

    existing = db.query(AdmissionAssessment).filter(
        AdmissionAssessment.patient_id == payload.patient_id, AdmissionAssessment.organization_id == org_id
    ).first()
    if existing:
        raise HTTPException(400, "An admission assessment already exists for this patient's current admission")

    assessment = AdmissionAssessment(
        organization_id=org_id, patient_id=payload.patient_id,
        presenting_complaint=payload.presenting_complaint, known_allergies=payload.known_allergies,
        past_medical_history=payload.past_medical_history, current_medications=payload.current_medications,
        functional_status=payload.functional_status, risk_screening_notes=payload.risk_screening_notes,
        assessed_by=current_user["id"],
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_admission_assessment",
              f"admission_assessments/{assessment.id}", "Success")
    return _admission_assessment_out(assessment)


@router.get("/api/ipd/admission-assessments/{patient_id}")
def get_admission_assessment(patient_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, patient_id, current_user)
    org_id = current_user.get("organization_id")
    assessment = db.query(AdmissionAssessment).filter(
        AdmissionAssessment.patient_id == patient_id, AdmissionAssessment.organization_id == org_id
    ).first()
    if not assessment:
        raise HTTPException(404, "No admission assessment recorded for this patient")
    return _admission_assessment_out(assessment)


# ---------------------------------------------------------------------------
# Pain Assessment -- recorded serially, like Vital
# ---------------------------------------------------------------------------

class PainAssessmentCreate(BaseModel):
    patient_id: int
    pain_score: int = Field(ge=0, le=10)
    location: Optional[str] = Field(default=None, max_length=200)
    character: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = None


def _pain_out(p: PainAssessment) -> dict:
    return {
        "id": p.id, "patient_id": p.patient_id, "pain_score": p.pain_score, "location": p.location,
        "character": p.character, "notes": p.notes, "recorded_by": p.recorded_by,
        "recorded_at": p.recorded_at.isoformat(),
    }


@router.post("/api/ipd/pain-assessments", status_code=201)
def create_pain_assessment(payload: PainAssessmentCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, payload.patient_id, current_user)
    org_id = current_user.get("organization_id")

    record = PainAssessment(
        organization_id=org_id, patient_id=payload.patient_id, pain_score=payload.pain_score,
        location=payload.location, character=payload.character, notes=payload.notes,
        recorded_by=current_user["id"],
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_pain_assessment", f"pain_assessments/{record.id}", "Success")
    return _pain_out(record)


@router.get("/api/ipd/pain-assessments")
def list_pain_assessments(patient_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, patient_id, current_user)
    records = db.query(PainAssessment).filter(PainAssessment.patient_id == patient_id).order_by(PainAssessment.recorded_at.desc()).all()
    return [_pain_out(r) for r in records]


# ---------------------------------------------------------------------------
# Fall Risk Assessment -- Morse Fall Scale
# ---------------------------------------------------------------------------

class FallRiskAssessmentCreate(BaseModel):
    patient_id: int
    history_of_falling: bool = False
    secondary_diagnosis: bool = False
    ambulatory_aid: str = Field(default="None", pattern="^(None|Crutches-Cane-Walker|Furniture)$")
    iv_therapy: bool = False
    gait: str = Field(default="Normal", pattern="^(Normal|Weak|Impaired)$")
    mental_status: str = Field(default="OrientedToOwnAbility", pattern="^(OrientedToOwnAbility|OverestimatesForgets)$")
    notes: Optional[str] = None


def _score_morse(payload: FallRiskAssessmentCreate) -> int:
    """Standard Morse Fall Scale point values."""
    score = 0
    score += 25 if payload.history_of_falling else 0
    score += 15 if payload.secondary_diagnosis else 0
    score += {"None": 0, "Crutches-Cane-Walker": 15, "Furniture": 30}[payload.ambulatory_aid]
    score += 20 if payload.iv_therapy else 0
    score += {"Normal": 0, "Weak": 10, "Impaired": 20}[payload.gait]
    score += {"OrientedToOwnAbility": 0, "OverestimatesForgets": 15}[payload.mental_status]
    return score


def _risk_level_morse(score: int) -> str:
    if score >= 45:
        return "High"
    if score >= 25:
        return "Moderate"
    return "Low"


def _fall_risk_out(f: FallRiskAssessment) -> dict:
    return {
        "id": f.id, "patient_id": f.patient_id, "history_of_falling": f.history_of_falling,
        "secondary_diagnosis": f.secondary_diagnosis, "ambulatory_aid": f.ambulatory_aid,
        "iv_therapy": f.iv_therapy, "gait": f.gait, "mental_status": f.mental_status,
        "risk_score": f.risk_score, "risk_level": f.risk_level, "notes": f.notes,
        "assessed_by": f.assessed_by, "assessed_at": f.assessed_at.isoformat(),
    }


@router.post("/api/ipd/fall-risk-assessments", status_code=201)
def create_fall_risk_assessment(payload: FallRiskAssessmentCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, payload.patient_id, current_user)
    org_id = current_user.get("organization_id")

    score = _score_morse(payload)
    record = FallRiskAssessment(
        organization_id=org_id, patient_id=payload.patient_id,
        history_of_falling=payload.history_of_falling, secondary_diagnosis=payload.secondary_diagnosis,
        ambulatory_aid=payload.ambulatory_aid, iv_therapy=payload.iv_therapy, gait=payload.gait,
        mental_status=payload.mental_status, risk_score=score, risk_level=_risk_level_morse(score),
        notes=payload.notes, assessed_by=current_user["id"],
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_fall_risk_assessment",
              f"fall_risk_assessments/{record.id}", "Success", f"score={score} level={record.risk_level}")
    return _fall_risk_out(record)


@router.get("/api/ipd/fall-risk-assessments")
def list_fall_risk_assessments(patient_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, patient_id, current_user)
    records = db.query(FallRiskAssessment).filter(FallRiskAssessment.patient_id == patient_id).order_by(FallRiskAssessment.assessed_at.desc()).all()
    return [_fall_risk_out(r) for r in records]


# ---------------------------------------------------------------------------
# Pressure Ulcer Assessment -- Braden Scale
# ---------------------------------------------------------------------------

class PressureUlcerAssessmentCreate(BaseModel):
    patient_id: int
    sensory_perception: int = Field(ge=1, le=4)
    moisture: int = Field(ge=1, le=4)
    activity: int = Field(ge=1, le=4)
    mobility: int = Field(ge=1, le=4)
    nutrition: int = Field(ge=1, le=4)
    friction_shear: int = Field(ge=1, le=3)
    notes: Optional[str] = None


def _risk_level_braden(score: int) -> str:
    """Standard Braden Scale bands (lower score = higher risk)."""
    if score <= 9:
        return "Severe"
    if score <= 12:
        return "High"
    if score <= 14:
        return "Moderate"
    if score <= 18:
        return "Low"
    return "None"


def _pressure_ulcer_out(p: PressureUlcerAssessment) -> dict:
    return {
        "id": p.id, "patient_id": p.patient_id, "sensory_perception": p.sensory_perception,
        "moisture": p.moisture, "activity": p.activity, "mobility": p.mobility,
        "nutrition": p.nutrition, "friction_shear": p.friction_shear,
        "risk_score": p.risk_score, "risk_level": p.risk_level, "notes": p.notes,
        "assessed_by": p.assessed_by, "assessed_at": p.assessed_at.isoformat(),
    }


@router.post("/api/ipd/pressure-ulcer-assessments", status_code=201)
def create_pressure_ulcer_assessment(payload: PressureUlcerAssessmentCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, payload.patient_id, current_user)
    org_id = current_user.get("organization_id")

    score = (
        payload.sensory_perception + payload.moisture + payload.activity
        + payload.mobility + payload.nutrition + payload.friction_shear
    )
    record = PressureUlcerAssessment(
        organization_id=org_id, patient_id=payload.patient_id,
        sensory_perception=payload.sensory_perception, moisture=payload.moisture,
        activity=payload.activity, mobility=payload.mobility, nutrition=payload.nutrition,
        friction_shear=payload.friction_shear, risk_score=score, risk_level=_risk_level_braden(score),
        notes=payload.notes, assessed_by=current_user["id"],
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_pressure_ulcer_assessment",
              f"pressure_ulcer_assessments/{record.id}", "Success", f"score={score} level={record.risk_level}")
    return _pressure_ulcer_out(record)


@router.get("/api/ipd/pressure-ulcer-assessments")
def list_pressure_ulcer_assessments(patient_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, patient_id, current_user)
    records = db.query(PressureUlcerAssessment).filter(
        PressureUlcerAssessment.patient_id == patient_id
    ).order_by(PressureUlcerAssessment.assessed_at.desc()).all()
    return [_pressure_ulcer_out(r) for r in records]
