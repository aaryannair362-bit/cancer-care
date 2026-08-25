"""
Pharmacy module: drug formulary, batch receiving, FEFO dispensing, and the NDPS-statutory
controlled-drug register. Wires up Drug/DrugBatch/DispensingRecord/ControlledDrugRegisterEntry
(backend/app/models.py) -- these tables existed in the schema before this router did, but had
zero endpoints anywhere in the app (confirmed by grep before writing this file).

Access is gated to Admin/Pharmacist (a new role string; no enum exists anywhere in this codebase
-- role is a free-text column compared via equality helpers, same as every other role, so an
Admin can create a Pharmacist user today via the existing POST /api/auth/admin/create-user with
zero other changes needed). auth.is_pharmacist already existed before this router was written --
a prior pass evidently scaffolded the role but never wired a module to it.

Uses Pydantic request models (unlike most of main.py's raw `await request.json()` dict-body
pattern) -- a deliberate choice for this module specifically: the current-state audit
(analysis-output/02-current-system/current-state-assessment.md) flagged raw dict bodies as a
concrete correctness risk (SQLite silently accepts what Postgres would reject), and this module
handles money and controlled substances, where that risk is least acceptable. Not a repaint of
main.py's existing endpoints -- just not repeating the pattern in new, higher-stakes code.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_admin, is_pharmacist, is_inventory_manager, log_audit
from ..database import get_db
from ..models import Consultation, ControlledDrugRegisterEntry, Drug, DrugBatch, DispensingRecord, Patient
from ..stock_utils import claim_batch_stock, on_hand

router = APIRouter(prefix="/api/pharmacy", tags=["pharmacy"])


def _require_pharmacy_staff(user: dict) -> None:
    if not (is_admin(user) or is_pharmacist(user)):
        raise HTTPException(403, "Only Admin or Pharmacist can access the pharmacy module")


def _require_pharmacy_read(user: dict) -> None:
    """Read-only drug-catalog lookup (name/price/stock, never dispensing or the controlled-drug
    register) -- also used by Inventory & Stores (frontend/inventory.html) to know what it's
    raising a purchase order or transfer against. Kept separate from _require_pharmacy_staff so
    InventoryManager never gains write access to dispensing/controlled-substance endpoints."""
    if not (is_admin(user) or is_pharmacist(user) or is_inventory_manager(user)):
        raise HTTPException(403, "Only Admin, Pharmacist, or Inventory Manager can read the drug catalog")


def _get_org_drug(db: Session, drug_id: int, org_id: int) -> Drug:
    drug = db.query(Drug).filter(Drug.id == drug_id, Drug.organization_id == org_id).first()
    if not drug:
        raise HTTPException(404, "Drug not found")
    return drug


def _drug_out(db: Session, d: Drug) -> dict:
    return {
        "id": d.id, "name": d.name, "form": d.form, "strength": d.strength,
        "barcode": d.barcode, "unit_price": d.unit_price, "is_controlled": d.is_controlled,
        "reorder_level": d.reorder_level, "quantity_on_hand": on_hand(db, d.id),
    }


def _batch_out(b: DrugBatch) -> dict:
    return {
        "id": b.id, "drug_id": b.drug_id, "batch_number": b.batch_number,
        "received_quantity": b.received_quantity, "quantity_on_hand": b.quantity_on_hand,
        "expiry_date": b.expiry_date.isoformat(), "is_expired": b.expiry_date < date.today(),
        "location": b.location,
    }


def _dispensing_out(r: DispensingRecord) -> dict:
    return {
        "id": r.id, "drug_id": r.drug_id, "batch_id": r.batch_id, "patient_id": r.patient_id,
        "consultation_id": r.consultation_id, "quantity": r.quantity,
        "unit_price_at_dispense": r.unit_price_at_dispense, "total_amount": r.total_amount,
        "dispensed_by": r.dispensed_by, "counselling_notes": r.counselling_notes,
        "refill_of_id": r.refill_of_id, "dispensed_at": r.dispensed_at.isoformat(),
    }


class DrugCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    form: Optional[str] = Field(default=None, max_length=50)
    strength: Optional[str] = Field(default=None, max_length=50)
    barcode: Optional[str] = Field(default=None, max_length=100)
    unit_price: Decimal = Field(ge=0)
    is_controlled: bool = False
    reorder_level: int = Field(default=0, ge=0)


class DrugUpdate(BaseModel):
    unit_price: Optional[Decimal] = Field(default=None, ge=0)
    is_controlled: Optional[bool] = None
    reorder_level: Optional[int] = Field(default=None, ge=0)
    barcode: Optional[str] = Field(default=None, max_length=100)


class BatchReceive(BaseModel):
    batch_number: Optional[str] = Field(default=None, max_length=100)
    received_quantity: int = Field(gt=0)
    expiry_date: date


class DispenseRequest(BaseModel):
    drug_id: int
    quantity: int = Field(gt=0)
    patient_id: Optional[int] = None
    consultation_id: Optional[int] = None
    prescriber_id: Optional[int] = None
    counselling_notes: Optional[str] = None


class RefillRequest(BaseModel):
    quantity: Optional[int] = Field(default=None, gt=0)
    counselling_notes: Optional[str] = None


def _consume_fefo(
    db: Session, drug: Drug, quantity: int, org_id: int, *,
    patient_id: Optional[int], consultation_id: Optional[int], dispensed_by: int,
    prescriber_id: Optional[int], counselling_notes: Optional[str], refill_of_id: Optional[int] = None,
) -> list[DispensingRecord]:
    """
    First-Expired-First-Out consumption across a drug's batches, per DrugBatch's own docstring.
    Claims stock batch-by-batch via `stock_utils.claim_batch_stock` (the actual
    concurrency-safety mechanism); if the full requested quantity can't be claimed, rolls back
    everything this call claimed and raises clean, so a request that can't be fully satisfied
    never leaves stock partially decremented.
    """
    batch_ids = [
        row.id for row in db.query(DrugBatch.id).filter(
            DrugBatch.drug_id == drug.id, DrugBatch.quantity_on_hand > 0, DrugBatch.expiry_date >= date.today()
        ).order_by(DrugBatch.expiry_date.asc(), DrugBatch.id.asc()).all()
    ]

    records: list[DispensingRecord] = []
    remaining = quantity
    for batch_id in batch_ids:
        if remaining <= 0:
            break
        take = claim_batch_stock(db, batch_id, remaining)
        if take <= 0:
            continue
        remaining -= take
        record = DispensingRecord(
            organization_id=org_id, patient_id=patient_id, consultation_id=consultation_id,
            drug_id=drug.id, batch_id=batch_id, quantity=take,
            unit_price_at_dispense=drug.unit_price, total_amount=round(take * drug.unit_price, 2),
            dispensed_by=dispensed_by, counselling_notes=counselling_notes, refill_of_id=refill_of_id,
        )
        db.add(record)
        db.flush()
        records.append(record)
        if drug.is_controlled:
            db.add(ControlledDrugRegisterEntry(
                organization_id=org_id, drug_id=drug.id, dispensing_record_id=record.id,
                patient_id=patient_id, prescriber_id=prescriber_id,
                quantity_dispensed=take, dispensed_by=dispensed_by,
            ))

    if remaining > 0:
        db.rollback()
        available = on_hand(db, drug.id)
        raise HTTPException(400, f"Insufficient stock: {available} on hand, {quantity} requested")

    return records


# ---------------------------------------------------------------------------
# Drug formulary
# ---------------------------------------------------------------------------

@router.post("/drugs", status_code=201)
def create_drug(payload: DrugCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    existing = db.query(Drug).filter(
        Drug.organization_id == org_id, Drug.name == payload.name,
        Drug.form == payload.form, Drug.strength == payload.strength,
    ).first()
    if existing:
        raise HTTPException(400, "A drug with this name/form/strength already exists in the formulary")
    drug = Drug(organization_id=org_id, created_by=current_user["id"], **payload.model_dump())
    db.add(drug)
    db.commit()
    db.refresh(drug)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_drug", f"drugs/{drug.id}", "Success")
    return _drug_out(db, drug)


@router.get("/drugs")
def list_drugs(
    search: Optional[str] = None, low_stock: bool = False,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_pharmacy_read(current_user)
    org_id = current_user.get("organization_id")
    q = db.query(Drug).filter(Drug.organization_id == org_id)
    if search:
        q = q.filter(Drug.name.ilike(f"%{search}%"))
    out = []
    for d in q.order_by(Drug.name).all():
        row = _drug_out(db, d)
        if low_stock and row["quantity_on_hand"] > d.reorder_level:
            continue
        out.append(row)
    return out


@router.get("/drugs/by-barcode/{barcode}")
def get_drug_by_barcode(barcode: str, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_read(current_user)
    org_id = current_user.get("organization_id")
    drug = db.query(Drug).filter(Drug.organization_id == org_id, Drug.barcode == barcode).first()
    if not drug:
        raise HTTPException(404, "No drug found for this barcode")
    return _drug_out(db, drug)


@router.get("/drugs/{drug_id}")
def get_drug(drug_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_read(current_user)
    drug = _get_org_drug(db, drug_id, current_user.get("organization_id"))
    out = _drug_out(db, drug)
    batches = db.query(DrugBatch).filter(DrugBatch.drug_id == drug.id).order_by(DrugBatch.expiry_date.asc()).all()
    out["batches"] = [_batch_out(b) for b in batches]
    return out


@router.patch("/drugs/{drug_id}")
def update_drug(drug_id: int, payload: DrugUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    drug = _get_org_drug(db, drug_id, org_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(drug, field, value)
    db.commit()
    log_audit(db, current_user["id"], current_user["email"], org_id, "update_drug", f"drugs/{drug_id}", "Success")
    return _drug_out(db, drug)


# ---------------------------------------------------------------------------
# Stock receiving (goods receipt) and expiry monitoring
# ---------------------------------------------------------------------------

@router.post("/drugs/{drug_id}/batches", status_code=201)
def receive_batch(drug_id: int, payload: BatchReceive, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    drug = _get_org_drug(db, drug_id, org_id)
    if payload.expiry_date <= date.today():
        raise HTTPException(400, "expiry_date must be in the future -- a batch received already expired is not dispensable stock")
    batch = DrugBatch(
        drug_id=drug.id, batch_number=payload.batch_number,
        received_quantity=payload.received_quantity, quantity_on_hand=payload.received_quantity,
        expiry_date=payload.expiry_date, received_by=current_user["id"],
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    log_audit(db, current_user["id"], current_user["email"], org_id, "receive_batch", f"drug_batches/{batch.id}",
               "Success", f"drug_id={drug.id} qty={payload.received_quantity}")
    return _batch_out(batch)


@router.get("/expiry-alerts")
def expiry_alerts(days: int = 30, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    cutoff = date.today() + timedelta(days=days)
    rows = (
        db.query(DrugBatch, Drug)
        .join(Drug, DrugBatch.drug_id == Drug.id)
        .filter(Drug.organization_id == org_id, DrugBatch.quantity_on_hand > 0, DrugBatch.expiry_date <= cutoff)
        .order_by(DrugBatch.expiry_date.asc())
        .all()
    )
    return [
        {**_batch_out(b), "drug_name": d.name, "drug_form": d.form, "drug_strength": d.strength}
        for b, d in rows
    ]


# ---------------------------------------------------------------------------
# Prescription Receipt: recent consultations' medication orders as a dispensing queue
# ---------------------------------------------------------------------------

@router.get("/pending-prescriptions")
def pending_prescriptions(
    days: int = 7, limit: int = 50,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    """
    Prescription Receipt (Pharmacy Workflow): surfaces recent consultations' medication orders
    as a pharmacy-facing queue, so staff pick from what a doctor actually ordered instead of
    re-typing a drug name off a paper/verbal prescription -- the one Pharmacy capability the
    current-state audit rated Partial rather than Present. Matches each medication line against
    the formulary by name (case-insensitive) since Consultation.medications is free text, not
    formulary-linked; a line with no matching Drug is still shown, flagged `in_formulary=False`,
    since hiding it would just recreate the manual-lookup problem this endpoint exists to solve.
    Dispensed quantity is cross-referenced against DispensingRecord.consultation_id, so a
    partially- or fully-dispensed line reads as such rather than staying "Pending" forever.
    """
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    cutoff = datetime.utcnow() - timedelta(days=max(1, min(days, 90)))
    limit = max(1, min(limit, 100))

    consultations = db.query(Consultation).filter(
        Consultation.organization_id == org_id, Consultation.created_at >= cutoff,
    ).order_by(Consultation.created_at.desc()).limit(limit).all()

    out = []
    for c in consultations:
        meds = [m for m in (c.medications or []) if isinstance(m, dict) and m.get("drugName") and not m.get("discontinued")]
        if not meds:
            continue
        lines = []
        for med in meds:
            drug_name = med["drugName"]
            drug = db.query(Drug).filter(Drug.organization_id == org_id, Drug.name.ilike(drug_name)).first()
            dispensed_qty = 0
            if drug:
                dispensed_qty = db.query(func.coalesce(func.sum(DispensingRecord.quantity), 0)).filter(
                    DispensingRecord.consultation_id == c.id, DispensingRecord.drug_id == drug.id,
                ).scalar() or 0
            lines.append({
                "drug_name": drug_name, "dose": med.get("dose"), "route": med.get("route"),
                "frequency": med.get("frequency"), "duration": med.get("duration"),
                "drug_id": drug.id if drug else None, "in_formulary": drug is not None,
                "dispensed_quantity": dispensed_qty,
                "status": "Dispensed" if dispensed_qty > 0 else "Pending",
            })
        if all(line["status"] == "Dispensed" for line in lines):
            continue
        out.append({
            "consultation_id": c.id, "case_id": c.case_id, "patient_id": c.patient_id,
            "patient_name": c.patient_name, "created_at": c.created_at.isoformat(),
            "prescribed_by": c.user_id, "medications": lines,
        })
    return out


# ---------------------------------------------------------------------------
# Dispensing, refill, and dispensing history
# ---------------------------------------------------------------------------

@router.post("/dispense", status_code=201)
def dispense(payload: DispenseRequest, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    drug = _get_org_drug(db, payload.drug_id, org_id)

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

    records = _consume_fefo(
        db, drug, payload.quantity, org_id,
        patient_id=payload.patient_id, consultation_id=payload.consultation_id,
        dispensed_by=current_user["id"], prescriber_id=payload.prescriber_id,
        counselling_notes=payload.counselling_notes,
    )
    db.commit()
    for r in records:
        db.refresh(r)
    log_audit(db, current_user["id"], current_user["email"], org_id, "dispense_drug",
               f"drugs/{drug.id}", "Success", f"qty={payload.quantity} lines={len(records)}")
    return {
        "drug_id": drug.id, "quantity": payload.quantity,
        "total_amount": round(sum(r.total_amount for r in records), 2),
        "lines": [_dispensing_out(r) for r in records],
    }


@router.post("/refill/{dispensing_record_id}", status_code=201)
def refill(dispensing_record_id: int, payload: RefillRequest, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    original = db.query(DispensingRecord).filter(
        DispensingRecord.id == dispensing_record_id, DispensingRecord.organization_id == org_id
    ).first()
    if not original:
        raise HTTPException(404, "Original dispensing record not found")
    drug = _get_org_drug(db, original.drug_id, org_id)
    quantity = payload.quantity or original.quantity

    records = _consume_fefo(
        db, drug, quantity, org_id,
        patient_id=original.patient_id, consultation_id=original.consultation_id,
        dispensed_by=current_user["id"], prescriber_id=None,
        counselling_notes=payload.counselling_notes, refill_of_id=original.id,
    )
    db.commit()
    for r in records:
        db.refresh(r)
    log_audit(db, current_user["id"], current_user["email"], org_id, "refill_drug",
               f"dispensing_records/{original.id}", "Success", f"qty={quantity} lines={len(records)}")
    return {
        "drug_id": drug.id, "quantity": quantity, "refill_of_id": original.id,
        "total_amount": round(sum(r.total_amount for r in records), 2),
        "lines": [_dispensing_out(r) for r in records],
    }


@router.get("/dispensing-records")
def list_dispensing_records(
    patient_id: Optional[int] = None, drug_id: Optional[int] = None, limit: int = 50,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    q = db.query(DispensingRecord).filter(DispensingRecord.organization_id == org_id)
    if patient_id is not None:
        q = q.filter(DispensingRecord.patient_id == patient_id)
    if drug_id is not None:
        q = q.filter(DispensingRecord.drug_id == drug_id)
    limit = max(1, min(limit, 200))
    rows = q.order_by(DispensingRecord.dispensed_at.desc()).limit(limit).all()
    return [_dispensing_out(r) for r in rows]


# ---------------------------------------------------------------------------
# Controlled Drug Register (NDPS statutory ledger)
# ---------------------------------------------------------------------------

@router.get("/controlled-drug-register")
def controlled_drug_register(
    drug_id: Optional[int] = None, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    """
    Reconstructs a running balance per controlled drug by replaying batch receipts (+) and
    register entries (-) in chronological order -- matching ControlledDrugRegisterEntry's own
    docstring: no stored running-balance column, so it can never silently drift from the real
    receipt/dispense history.
    """
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    q = db.query(Drug).filter(Drug.organization_id == org_id, Drug.is_controlled == True)  # noqa: E712
    if drug_id is not None:
        q = q.filter(Drug.id == drug_id)

    ledger = []
    for d in q.order_by(Drug.name).all():
        events = []
        for b in db.query(DrugBatch).filter(DrugBatch.drug_id == d.id).all():
            events.append({
                "type": "receipt", "at": b.received_at, "quantity": b.received_quantity,
                "batch_id": b.id, "batch_number": b.batch_number,
            })
        entries = db.query(ControlledDrugRegisterEntry).filter(ControlledDrugRegisterEntry.drug_id == d.id).all()
        for e in entries:
            events.append({
                "type": "dispense", "at": e.recorded_at, "quantity": -e.quantity_dispensed,
                "dispensing_record_id": e.dispensing_record_id, "patient_id": e.patient_id,
                "prescriber_id": e.prescriber_id, "dispensed_by": e.dispensed_by,
            })
        events.sort(key=lambda ev: ev["at"] or datetime.min)

        balance = 0
        out_events = []
        for ev in events:
            balance += ev["quantity"]
            out_events.append({**ev, "at": ev["at"].isoformat() if ev["at"] else None, "running_balance": balance})
        ledger.append({"drug_id": d.id, "drug_name": d.name, "current_balance": balance, "entries": out_events})
    return ledger
