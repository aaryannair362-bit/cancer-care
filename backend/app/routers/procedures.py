"""
Procedure Documentation (Clinical Workflows) -- the one capability in that module the
current-state audit found with no dedicated model or field at all. Access mirrors the existing
clinical-documentation gate (Doctor creates; Doctor/HeadNurse/NursingStation/assigned-Nurse can
read, matching get_patient_details' role shape) rather than inventing a new pattern.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_head_nurse, is_nursing_station, is_nurse, log_audit
from ..database import get_db
from ..models import Consultation, NurseAssignment, Patient, ProcedureRecord

router = APIRouter(prefix="/api/procedures", tags=["procedures"])


def _is_doctor(user: dict) -> bool:
    return user.get("role") == "Doctor"


def _require_read_access(user: dict, db: Session, patient_id: Optional[int]) -> None:
    if _is_doctor(user) or is_head_nurse(user) or is_nursing_station(user):
        return
    if is_nurse(user) and patient_id is not None:
        assignment = db.query(NurseAssignment).filter(
            NurseAssignment.patient_id == patient_id, NurseAssignment.nurse_id == user["id"],
            NurseAssignment.status == "Active",
        ).first()
        if assignment:
            return
    raise HTTPException(403, "Not permitted to view procedure records")


def _procedure_out(p: ProcedureRecord) -> dict:
    return {
        "id": p.id, "patient_id": p.patient_id, "consultation_id": p.consultation_id,
        "procedure_name": p.procedure_name, "notes": p.notes,
        "performed_by": p.performed_by, "performed_at": p.performed_at.isoformat(),
    }


class ProcedureCreate(BaseModel):
    procedure_name: str = Field(min_length=1, max_length=200)
    patient_id: Optional[int] = None
    consultation_id: Optional[int] = None
    notes: Optional[str] = None


@router.post("", status_code=201)
def create_procedure(payload: ProcedureCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if not _is_doctor(current_user):
        raise HTTPException(403, "Only a doctor can document a procedure")
    org_id = current_user.get("organization_id")

    if payload.patient_id is not None:
        patient = db.query(Patient).filter(Patient.id == payload.patient_id, Patient.organization_id == org_id).first()
        if not patient:
            raise HTTPException(404, "Patient not found")
    if payload.consultation_id is not None:
        consultation = db.query(Consultation).filter(
            Consultation.id == payload.consultation_id, Consultation.organization_id == org_id
        ).first()
        if not consultation:
            raise HTTPException(404, "Consultation not found")

    record = ProcedureRecord(
        organization_id=org_id, patient_id=payload.patient_id, consultation_id=payload.consultation_id,
        procedure_name=payload.procedure_name, notes=payload.notes, performed_by=current_user["id"],
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_procedure", f"procedure_records/{record.id}", "Success")
    return _procedure_out(record)


@router.get("")
def list_procedures(
    patient_id: Optional[int] = None, consultation_id: Optional[int] = None,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_read_access(current_user, db, patient_id)
    org_id = current_user.get("organization_id")
    query = db.query(ProcedureRecord).filter(ProcedureRecord.organization_id == org_id)
    if patient_id is not None:
        query = query.filter(ProcedureRecord.patient_id == patient_id)
    if consultation_id is not None:
        query = query.filter(ProcedureRecord.consultation_id == consultation_id)
    records = query.order_by(ProcedureRecord.performed_at.desc()).all()
    return [_procedure_out(r) for r in records]
