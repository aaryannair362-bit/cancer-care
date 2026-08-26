"""
Appointment Scheduling and Queue/Token Management -- the front-of-house half of OPD that the
current-state audit found entirely missing (the clinical-documentation half, POST /api/scribe,
was already mature). Also carries this build's minimal, honest take on "Emergency (ER)
Workflow": Basic HMS.pdf names it as a single bullet under Patient Administration with no
further detail (unlike the fuller All Modules.pdf, which expands it into its own 10-step
Emergency Department module -- triage, critical care assessment, emergency orders/lab/
radiology/medication, observation -- explicitly out of scope for this Basic HMS.pdf build).
What's built here: an `is_emergency` flag on a walk-in token that queue-jumps ahead of the
regular queue, which is a real, useful capability, not a stand-in for full ED triage.

Token numbering is one sequential counter per (organization, calendar day) -- not per-doctor --
matching the common single-front-desk-ticket-counter model (a numbered ticket at registration,
doctor assignment tracked separately on the token), a reasonable inferred design since Basic
HMS.pdf's "Token Management" bullet doesn't specify per-doctor vs. hospital-wide queues.
Protected against the walk-in-registration-desk concurrent-issuance case (two token requests
landing at the same moment) by a DB unique constraint plus a bounded retry -- lower-stakes than
Pharmacy/Inventory's stock races (a collision just means retrying an insert, not overselling
anything), so a bounded retry-on-conflict is proportionate here, not the same atomic-claim
mechanism stock manipulation needed.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_admin, is_head_nurse, is_nursing_station, is_cca_front_desk, log_audit
from ..database import get_db
from ..models import Appointment, Consultation, Patient, QueueToken, User

router = APIRouter(tags=["appointments"])


def _require_front_desk(user: dict) -> None:
    if not (is_admin(user) or is_head_nurse(user) or is_nursing_station(user) or is_cca_front_desk(user)):
        raise HTTPException(403, "Only Admin, HeadNurse, NursingStation, or CCAFrontDesk can manage appointments/queue")


def _require_view_access(user: dict) -> None:
    role = user.get("role")
    if not (is_admin(user) or is_head_nurse(user) or is_nursing_station(user) or is_cca_front_desk(user) or role == "Doctor"):
        raise HTTPException(403, "Not permitted to view the appointment/queue schedule")


def _get_org_patient(db: Session, patient_id: int, org_id: int) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id, Patient.organization_id == org_id).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    return patient


def _get_org_doctor(db: Session, doctor_id: int, org_id: int) -> User:
    doctor = db.query(User).filter(User.id == doctor_id, User.organization_id == org_id, User.role == "Doctor").first()
    if not doctor:
        raise HTTPException(404, "Doctor not found")
    return doctor


def _appointment_out(a: Appointment, db: Optional[Session] = None) -> dict:
    patient_name = None
    patient_mrn = None
    doctor_name = None
    if db:
        p = db.query(Patient).filter(Patient.id == a.patient_id).first()
        if p:
            patient_name = p.name
            patient_mrn = getattr(p, "mrn", f"MRN-{p.id}")
        d = db.query(User).filter(User.id == a.doctor_id).first()
        if d:
            doctor_name = f"Dr. {d.email.split('@')[0].replace('.', ' ').title()}" if d.email else f"Doctor #{d.id}"
    return {
        "id": a.id,
        "patient_id": a.patient_id,
        "patient_name": patient_name or f"Patient #{a.patient_id}",
        "patient_mrn": patient_mrn or f"#{a.patient_id}",
        "doctor_id": a.doctor_id,
        "doctor_name": doctor_name or f"Dr. #{a.doctor_id}",
        "scheduled_at": a.scheduled_at.isoformat(),
        "status": a.status,
        "reason": a.reason,
        "follow_up_of_consultation_id": a.follow_up_of_consultation_id,
    }


def _token_out(t: QueueToken, db: Optional[Session] = None) -> dict:
    patient_name = None
    patient_mrn = None
    doctor_name = None
    if db:
        p = db.query(Patient).filter(Patient.id == t.patient_id).first()
        if p:
            patient_name = p.name
            patient_mrn = getattr(p, "mrn", f"MRN-{p.id}")
        if t.doctor_id:
            d = db.query(User).filter(User.id == t.doctor_id).first()
            if d:
                doctor_name = f"Dr. {d.email.split('@')[0].replace('.', ' ').title()}" if d.email else f"Doctor #{d.id}"
    return {
        "id": t.id,
        "patient_id": t.patient_id,
        "patient_name": patient_name or f"Patient #{t.patient_id}",
        "patient_mrn": patient_mrn or f"#{t.patient_id}",
        "appointment_id": t.appointment_id,
        "doctor_id": t.doctor_id,
        "doctor_name": doctor_name or (f"Dr. #{t.doctor_id}" if t.doctor_id else "Any Doctor"),
        "token_number": t.token_number,
        "token_date": t.token_date.isoformat(),
        "is_emergency": t.is_emergency,
        "status": t.status,
        "issued_at": t.issued_at.isoformat(),
        "called_at": t.called_at.isoformat() if t.called_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


class AppointmentCreate(BaseModel):
    patient_id: int
    doctor_id: int
    scheduled_at: datetime
    reason: Optional[str] = None
    follow_up_of_consultation_id: Optional[int] = None


class TokenIssue(BaseModel):
    patient_id: int
    doctor_id: Optional[int] = None
    is_emergency: bool = False


class TokenStatusNote(BaseModel):
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Appointment Scheduling
# ---------------------------------------------------------------------------

@router.get("/api/appointments/doctors")
def list_doctors_for_scheduling(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    A minimal doctor picker for the appointment/token-issuing front-desk UI. GET
    /api/auth/users (the only other user-listing endpoint) is Admin/HeadNurse-only, which
    would leave NursingStation -- this codebase's actual front-desk role, and the primary
    caller of POST /api/appointments/POST /api/queue/tokens -- with no way to see which
    doctors exist to schedule against. Reuses `_require_view_access` (the same gate already on
    reading the appointment/queue schedule) rather than inventing a new one, and returns only
    the id/email a scheduling dropdown needs, not the fuller user-management payload.
    """
    _require_view_access(current_user)
    org_id = current_user.get("organization_id")
    doctors = db.query(User).filter(
        User.organization_id == org_id, User.role == "Doctor", User.status == "Active",
    ).order_by(User.email).all()
    return [{"id": d.id, "email": d.email} for d in doctors]


@router.post("/api/appointments", status_code=201)
def create_appointment(payload: AppointmentCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_front_desk(current_user)
    org_id = current_user.get("organization_id")
    _get_org_patient(db, payload.patient_id, org_id)
    _get_org_doctor(db, payload.doctor_id, org_id)
    if payload.follow_up_of_consultation_id is not None:
        prior = db.query(Consultation).filter(
            Consultation.id == payload.follow_up_of_consultation_id, Consultation.organization_id == org_id
        ).first()
        if not prior:
            raise HTTPException(404, "follow_up_of_consultation_id does not reference a consultation in this organization")

    appointment = Appointment(
        organization_id=org_id, patient_id=payload.patient_id, doctor_id=payload.doctor_id,
        scheduled_at=payload.scheduled_at, reason=payload.reason, status="Scheduled",
        created_by=current_user["id"], follow_up_of_consultation_id=payload.follow_up_of_consultation_id,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_appointment", f"appointments/{appointment.id}", "Success")
    return _appointment_out(appointment, db=db)


@router.get("/api/appointments")
def list_appointments(
    on_date: Optional[date] = None, doctor_id: Optional[int] = None, patient_id: Optional[int] = None,
    status: Optional[str] = None, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_view_access(current_user)
    org_id = current_user.get("organization_id")
    query = db.query(Appointment).filter(Appointment.organization_id == org_id)
    if current_user.get("role") == "Doctor" and doctor_id is None:
        query = query.filter(Appointment.doctor_id == current_user["id"])
    elif doctor_id is not None:
        query = query.filter(Appointment.doctor_id == doctor_id)
    if patient_id is not None:
        query = query.filter(Appointment.patient_id == patient_id)
    if status:
        query = query.filter(Appointment.status == status)
    if on_date is not None:
        query = query.filter(func.date(Appointment.scheduled_at) == on_date)
    return [_appointment_out(a, db=db) for a in query.order_by(Appointment.scheduled_at).all()]


@router.post("/api/appointments/{appointment_id}/cancel")
def cancel_appointment(appointment_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_front_desk(current_user)
    org_id = current_user.get("organization_id")
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id, Appointment.organization_id == org_id).first()
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    if appointment.status not in ("Scheduled",):
        raise HTTPException(400, f"Cannot cancel an appointment that is {appointment.status}")
    appointment.status = "Cancelled"
    db.commit()
    log_audit(db, current_user["id"], current_user["email"], org_id, "cancel_appointment", f"appointments/{appointment_id}", "Success")
    return _appointment_out(appointment, db=db)


def _issue_token(db: Session, org_id: int, patient_id: int, doctor_id: Optional[int],
                  appointment_id: Optional[int], is_emergency: bool, issued_by: int) -> QueueToken:
    today = date.today()
    for _ in range(5):
        next_number = (db.query(func.coalesce(func.max(QueueToken.token_number), 0))
                       .filter(QueueToken.organization_id == org_id, QueueToken.token_date == today).scalar()) + 1
        token = QueueToken(
            organization_id=org_id, patient_id=patient_id, appointment_id=appointment_id, doctor_id=doctor_id,
            token_number=next_number, token_date=today, is_emergency=is_emergency, status="Waiting",
            issued_by=issued_by,
        )
        db.add(token)
        try:
            db.flush()
            return token
        except IntegrityError:
            db.rollback()
    raise HTTPException(500, "Could not issue a token number after several attempts -- please retry")


@router.post("/api/appointments/{appointment_id}/check-in", status_code=201)
def check_in_appointment(appointment_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_front_desk(current_user)
    org_id = current_user.get("organization_id")
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id, Appointment.organization_id == org_id).first()
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    if appointment.status != "Scheduled":
        raise HTTPException(400, f"Cannot check in an appointment that is {appointment.status}")

    token = _issue_token(db, org_id, appointment.patient_id, appointment.doctor_id, appointment.id, False, current_user["id"])
    appointment.status = "CheckedIn"
    db.commit()
    db.refresh(token)
    log_audit(db, current_user["id"], current_user["email"], org_id, "check_in_appointment", f"appointments/{appointment_id}", "Success")
    return {"appointment": _appointment_out(appointment, db=db), "token": _token_out(token, db=db)}


# ---------------------------------------------------------------------------
# Queue / Token Management (incl. emergency queue-jump)
# ---------------------------------------------------------------------------

@router.post("/api/queue/tokens", status_code=201)
def issue_walkin_token(payload: TokenIssue, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_front_desk(current_user)
    org_id = current_user.get("organization_id")
    _get_org_patient(db, payload.patient_id, org_id)
    if payload.doctor_id is not None:
        _get_org_doctor(db, payload.doctor_id, org_id)

    token = _issue_token(db, org_id, payload.patient_id, payload.doctor_id, None, payload.is_emergency, current_user["id"])
    db.commit()
    db.refresh(token)
    log_audit(db, current_user["id"], current_user["email"], org_id, "issue_token", f"queue_tokens/{token.id}",
               "Success", f"emergency={payload.is_emergency}")
    return _token_out(token, db=db)


@router.get("/api/queue/tokens")
def list_queue(
    on_date: Optional[date] = None, doctor_id: Optional[int] = None, status: Optional[str] = None,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Ordered emergency-first, then by token number -- a real (if simple) implementation of
    queue-jumping for the Emergency (ER) Workflow bullet, not just a flag with no effect."""
    _require_view_access(current_user)
    org_id = current_user.get("organization_id")
    query = db.query(QueueToken).filter(QueueToken.organization_id == org_id, QueueToken.token_date == (on_date or date.today()))
    if current_user.get("role") == "Doctor" and doctor_id is None:
        query = query.filter(QueueToken.doctor_id == current_user["id"])
    elif doctor_id is not None:
        query = query.filter(QueueToken.doctor_id == doctor_id)
    if status:
        query = query.filter(QueueToken.status == status)
    tokens = query.order_by(QueueToken.is_emergency.desc(), QueueToken.token_number.asc()).all()
    return [_token_out(t, db=db) for t in tokens]


def _get_org_token(db: Session, token_id: int, org_id: int) -> QueueToken:
    token = db.query(QueueToken).filter(QueueToken.id == token_id, QueueToken.organization_id == org_id).first()
    if not token:
        raise HTTPException(404, "Token not found")
    return token


@router.patch("/api/queue/tokens/{token_id}/call")
def call_token(token_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_front_desk(current_user)
    org_id = current_user.get("organization_id")
    token = _get_org_token(db, token_id, org_id)
    if token.status != "Waiting":
        raise HTTPException(400, f"Cannot call a token that is {token.status}")
    token.status = "InProgress"
    token.called_at = datetime.utcnow()
    db.commit()
    return _token_out(token, db=db)


@router.patch("/api/queue/tokens/{token_id}/complete")
def complete_token(token_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_front_desk(current_user)
    org_id = current_user.get("organization_id")
    token = _get_org_token(db, token_id, org_id)
    if token.status != "InProgress":
        raise HTTPException(400, f"Cannot complete a token that is {token.status}")
    token.status = "Completed"
    token.completed_at = datetime.utcnow()
    db.commit()
    return _token_out(token, db=db)


@router.patch("/api/queue/tokens/{token_id}/skip")
def skip_token(token_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_front_desk(current_user)
    org_id = current_user.get("organization_id")
    token = _get_org_token(db, token_id, org_id)
    if token.status not in ("Waiting", "InProgress"):
        raise HTTPException(400, f"Cannot skip a token that is {token.status}")
    token.status = "Skipped"
    db.commit()
    return _token_out(token, db=db)
