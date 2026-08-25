"""
IV Fluid Management and Intake/Output Charting -- the two Nursing Workflows structured-data
gaps the current-state audit named specifically: present only as free text inside
NursingNote.notes / Vital.notes, no dedicated model or field for either. Access control mirrors
`record_vital`/`create_nursing_note` in main.py exactly (Nurse must hold an Active
NurseAssignment for the patient; HeadNurse can act on any patient in the org) -- same shape of
access control, not reinvented.

Pain/Fall-risk/Pressure-ulcer assessments (also named in the source doc's Nursing Workflows
section, but rated lower-priority there) are not built in this pass -- flagged as a fast-follow,
not silently dropped.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_head_nurse, is_nurse, log_audit
from ..database import get_db
from ..models import IntakeOutputRecord, IVFluidOrder, NurseAssignment, Patient

router = APIRouter(tags=["nursing-charting"])


def _require_nursing_access(user: dict) -> None:
    if not (is_nurse(user) or is_head_nurse(user)):
        raise HTTPException(403, "Only nurses and head nurses can chart IV fluids or intake/output")


def _get_assigned_patient(db: Session, patient_id: int, current_user: dict) -> Patient:
    """Same org-scope + assignment check as main.py's record_vital/create_nursing_note."""
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


def _iv_out(o: IVFluidOrder) -> dict:
    return {
        "id": o.id, "patient_id": o.patient_id, "fluid_type": o.fluid_type, "volume_ml": o.volume_ml,
        "rate_ml_per_hr": o.rate_ml_per_hr, "route": o.route, "status": o.status,
        "started_by": o.started_by, "started_at": o.started_at.isoformat(),
        "stopped_by": o.stopped_by, "stopped_at": o.stopped_at.isoformat() if o.stopped_at else None,
        "notes": o.notes,
    }


def _io_out(r: IntakeOutputRecord) -> dict:
    return {
        "id": r.id, "patient_id": r.patient_id, "entry_type": r.entry_type, "category": r.category,
        "volume_ml": r.volume_ml, "recorded_by": r.recorded_by, "recorded_at": r.recorded_at.isoformat(),
        "notes": r.notes,
    }


class IVFluidStart(BaseModel):
    patient_id: int
    fluid_type: str = Field(min_length=1, max_length=200)
    volume_ml: int = Field(gt=0)
    rate_ml_per_hr: Optional[int] = Field(default=None, gt=0)
    route: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = None


class IVFluidStop(BaseModel):
    notes: Optional[str] = None


class IntakeOutputCreate(BaseModel):
    patient_id: int
    entry_type: str = Field(pattern="^(Intake|Output)$")
    category: str = Field(min_length=1, max_length=50)
    volume_ml: int = Field(gt=0)
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# IV Fluid Management
# ---------------------------------------------------------------------------

@router.post("/api/ipd/iv-fluids", status_code=201)
def start_iv_fluid(payload: IVFluidStart, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, payload.patient_id, current_user)
    org_id = current_user.get("organization_id")

    order = IVFluidOrder(
        organization_id=org_id, patient_id=payload.patient_id, fluid_type=payload.fluid_type,
        volume_ml=payload.volume_ml, rate_ml_per_hr=payload.rate_ml_per_hr, route=payload.route,
        status="Active", started_by=current_user["id"], notes=payload.notes,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    log_audit(db, current_user["id"], current_user["email"], org_id, "start_iv_fluid", f"iv_fluid_orders/{order.id}", "Success")
    return _iv_out(order)


@router.get("/api/ipd/iv-fluids")
def list_iv_fluids(
    patient_id: int, status: Optional[str] = None,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, patient_id, current_user)
    query = db.query(IVFluidOrder).filter(IVFluidOrder.patient_id == patient_id)
    if status:
        query = query.filter(IVFluidOrder.status == status)
    return [_iv_out(o) for o in query.order_by(IVFluidOrder.started_at.desc()).all()]


@router.patch("/api/ipd/iv-fluids/{order_id}/stop")
def stop_iv_fluid(order_id: int, payload: IVFluidStop, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    org_id = current_user.get("organization_id")
    order = db.query(IVFluidOrder).filter(IVFluidOrder.id == order_id, IVFluidOrder.organization_id == org_id).first()
    if not order:
        raise HTTPException(404, "IV fluid order not found")
    _get_assigned_patient(db, order.patient_id, current_user)
    if order.status != "Active":
        raise HTTPException(400, f"Cannot stop an IV fluid order that is {order.status}")

    order.status = "Completed"
    order.stopped_by = current_user["id"]
    order.stopped_at = datetime.utcnow()
    if payload.notes:
        order.notes = f"{order.notes or ''}\n{payload.notes}".strip()
    db.commit()
    log_audit(db, current_user["id"], current_user["email"], org_id, "stop_iv_fluid", f"iv_fluid_orders/{order_id}", "Success")
    return _iv_out(order)


# ---------------------------------------------------------------------------
# Intake/Output Charting
# ---------------------------------------------------------------------------

@router.post("/api/ipd/intake-output", status_code=201)
def record_intake_output(payload: IntakeOutputCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_nursing_access(current_user)
    _get_assigned_patient(db, payload.patient_id, current_user)
    org_id = current_user.get("organization_id")

    record = IntakeOutputRecord(
        organization_id=org_id, patient_id=payload.patient_id, entry_type=payload.entry_type,
        category=payload.category, volume_ml=payload.volume_ml, recorded_by=current_user["id"],
        notes=payload.notes,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    log_audit(db, current_user["id"], current_user["email"], org_id, "record_intake_output", f"intake_output_records/{record.id}", "Success")
    return _io_out(record)


@router.get("/api/ipd/intake-output")
def list_intake_output(
    patient_id: int, hours: int = 24,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Returns the raw entries plus a computed intake/output balance over the trailing
    `hours` window -- always summed live from the entries, never a stored running total, since
    a nurse's chart must never disagree with its own history (same reasoning as
    ControlledDrugRegisterEntry's reconstructed ledger, applied here for the same "never let a
    displayed total silently drift from the underlying records" reason, not copied code)."""
    _require_nursing_access(current_user)
    _get_assigned_patient(db, patient_id, current_user)
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    records = db.query(IntakeOutputRecord).filter(
        IntakeOutputRecord.patient_id == patient_id, IntakeOutputRecord.recorded_at >= cutoff
    ).order_by(IntakeOutputRecord.recorded_at.desc()).all()

    total_intake = sum(r.volume_ml for r in records if r.entry_type == "Intake")
    total_output = sum(r.volume_ml for r in records if r.entry_type == "Output")
    return {
        "patient_id": patient_id, "window_hours": hours,
        "total_intake_ml": total_intake, "total_output_ml": total_output,
        "balance_ml": total_intake - total_output,
        "entries": [_io_out(r) for r in records],
    }
