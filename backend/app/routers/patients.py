"""
Patient Administration: Master Patient Index (MRN + search) and standalone registration --
the two capabilities the current-state audit found flatly missing (`Patient` had no MRN field,
only an autoincrement id, and "registration" only ever existed as a side effect of IPD
admission via POST /api/ipd/patients). Neither of those endpoints is touched or duplicated
here -- IPD admission still works exactly as before; this adds the missing front-of-house half
(a walk-in or OPD patient can now be registered, searched, and found by MRN without being
admitted to a ward at all).

MRN is generated server-side as `MRN-{organization_id}-{patient.id:06d}` -- deterministic and
collision-free by construction, since it's derived from the patient's own already-unique
primary key rather than a separately-tracked sequence. Stored as a real column (not computed at
read time) so it's a stable, searchable identifier and the generation scheme could be changed
later without needing to recompute historical MRNs. Existing patients (registered via the IPD
admission endpoint before this pass, or before this migration ran at all) keep `mrn = NULL` --
no backfill, matching this codebase's established additive-migration convention (e.g. DrugBatch
.location) of leaving legacy rows alone rather than manufacturing retroactive data.

Registration/search access is gated the same as IPD admission (Admin/HeadNurse/NursingStation
-- NursingStation is this codebase's front-desk/admission-desk role per ARCHITECTURE_NOTES.md),
plus Doctor for search only (a doctor plausibly wants to look up a patient by MRN before a
consultation). Plain Nurse is excluded from search, matching this codebase's existing pattern of
a ward Nurse's visibility being scoped narrowly to their assigned patients, not the whole org
roster -- an inferred choice, not a stated requirement, flagged here as such.

TPA (Insurance/TPA claims pipeline, see PreAuthorizationRequest in models.py) also gets search
access, org-wide like Doctor/Admin/HeadNurse/NursingStation -- a TPA reviewer needs to be able to
find any patient in the hospital to pull up their record for pre-authorization, not just ones
somehow pre-linked to that TPA (no such link exists in this codebase yet).
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_admin, is_head_nurse, is_nursing_station, is_tpa, log_audit
from ..database import get_db
from ..models import Patient

router = APIRouter(tags=["patients"])


def _require_registration_staff(user: dict) -> None:
    if not (is_admin(user) or is_head_nurse(user) or is_nursing_station(user)):
        raise HTTPException(403, "Only Admin, HeadNurse, or NursingStation can register patients")


def _require_search_access(user: dict) -> None:
    role = user.get("role")
    if not (is_admin(user) or is_head_nurse(user) or is_nursing_station(user) or is_tpa(user) or role == "Doctor"):
        raise HTTPException(403, "Not permitted to search the patient index")


def _patient_out(p: Patient) -> dict:
    return {
        "id": p.id, "mrn": p.mrn, "name": p.name, "age": p.age, "gender": p.gender,
        "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else None,
        "phone": p.phone, "address": p.address,
        "id_proof_type": p.id_proof_type, "id_proof_number": p.id_proof_number,
        "allergies": p.allergies,
        "status": p.status,
    }


class PatientRegister(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    age: Optional[int] = Field(default=None, ge=0, le=130)
    date_of_birth: Optional[date] = None
    gender: Optional[str] = Field(default=None, max_length=20)
    phone: Optional[str] = Field(default=None, max_length=20)
    address: Optional[str] = None
    id_proof_type: Optional[str] = Field(default=None, max_length=50)
    id_proof_number: Optional[str] = Field(default=None, max_length=100)
    allergies: Optional[list[str]] = None


@router.post("/api/patients/register", status_code=201)
def register_patient(payload: PatientRegister, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_registration_staff(current_user)
    if payload.age is None and payload.date_of_birth is None:
        raise HTTPException(400, "Either age or date_of_birth is required")
    org_id = current_user.get("organization_id")

    patient = Patient(
        name=payload.name, age=payload.age, date_of_birth=payload.date_of_birth, gender=payload.gender,
        phone=payload.phone, address=payload.address, id_proof_type=payload.id_proof_type,
        id_proof_number=payload.id_proof_number, allergies=payload.allergies, status="Registered",
        organization_id=org_id, created_by=current_user["id"],
    )
    db.add(patient)
    db.flush()
    patient.mrn = f"MRN-{org_id}-{patient.id:06d}"
    db.commit()
    db.refresh(patient)
    log_audit(db, current_user["id"], current_user["email"], org_id, "register_patient", f"patients/{patient.id}", "Success")
    return _patient_out(patient)


@router.get("/api/patients/search")
def search_patients(
    q: Optional[str] = None, limit: int = 20,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Matches name (partial, case-insensitive), phone (partial), or mrn (exact) -- a single
    free-text `q` covers the common real front-desk lookup patterns (partial name, full phone
    number, or a printed/read-aloud MRN) without asking the caller to pick a field."""
    _require_search_access(current_user)
    org_id = current_user.get("organization_id")
    query = db.query(Patient).filter(Patient.organization_id == org_id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Patient.name.ilike(like), Patient.phone.ilike(like), Patient.mrn == q))
    limit = max(1, min(limit, 100))
    results = query.order_by(Patient.name).limit(limit).all()
    return [_patient_out(p) for p in results]


@router.get("/api/patients/by-mrn/{mrn}")
def get_patient_by_mrn(mrn: str, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_search_access(current_user)
    org_id = current_user.get("organization_id")
    patient = db.query(Patient).filter(Patient.organization_id == org_id, Patient.mrn == mrn).first()
    if not patient:
        raise HTTPException(404, "No patient found for this MRN")
    return _patient_out(patient)
