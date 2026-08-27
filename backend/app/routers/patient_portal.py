"""
Patient self-service portal -- the shell described in the Care Plan & Treatment Plan
architecture doc's P2 patient-facing item: a real, separate patient identity and auth path,
built on top of the content-safety gate already established in cca.py
(CarePlan.patient_facing_approved / CarePlanTask.patient_visible_note /
build_patient_facing_summary). See patient_auth.py's module docstring for why this is
structurally isolated from staff auth, and for the explicit scope note that identity
verification here is a staff-issued in-person code, not phone/email OTP (no SMS/email
gateway exists in this codebase; that's a vendor/cost decision for the institution).
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_admin, is_doctor, is_cca_oncologist, is_cca_nurse_navigator, is_cca_patient_liaison, is_cca_front_desk
from ..models_cca import CCAPatient, CCAConsent, PatientAccount
from ..patient_auth import (
    generate_activation_code, hash_activation_code, create_patient_access_token,
    get_current_patient, ACTIVATION_CODE_VALID_HOURS,
)
from ..events import publish
from .cca import get_cca_db, _org_id, _actor, _get_org_patient, build_patient_facing_summary

router = APIRouter(prefix="/api/cca", tags=["Patient Portal"])


def _has_portal_consent(db: Session, patient_id: int) -> bool:
    consents = db.query(CCAConsent).filter(
        CCAConsent.patient_id == patient_id, CCAConsent.status == "ACTIVE"
    ).all()
    return any("patient_portal_access" in (c.consent_types or []) for c in consents)


@router.post("/patients/{patient_id}/issue-activation-code")
def issue_patient_activation_code(
    patient_id: int, db: Session = Depends(get_cca_db),
    current_user: dict = Depends(get_current_user)
):
    """Staff-facing: generates a one-time code to hand the patient in person. Requires an
    already-captured 'patient_portal_access' consent -- this endpoint does not itself
    capture consent, matching the architecture doc's 'only after clinical/consent review'
    principle: provisioning is a separate, later step from consenting, not a side effect of
    it. The raw code is returned exactly once and never stored in plaintext."""
    if not (
        is_doctor(current_user) or is_admin(current_user) or is_cca_oncologist(current_user)
        or is_cca_nurse_navigator(current_user) or is_cca_patient_liaison(current_user) or is_cca_front_desk(current_user)
    ):
        raise HTTPException(403, "This role has no patient-contact responsibility for portal provisioning")
    _get_org_patient(db, patient_id, _org_id(current_user))
    if not _has_portal_consent(db, patient_id):
        raise HTTPException(422, "No active 'patient_portal_access' consent on record for this patient -- capture consent before issuing an activation code.")

    account = db.query(PatientAccount).filter(PatientAccount.patient_id == patient_id).first()
    if not account:
        account = PatientAccount(patient_id=patient_id)
        db.add(account)

    code = generate_activation_code()
    account.activation_code_hash = hash_activation_code(code)
    account.activation_code_expires_at = datetime.utcnow() + timedelta(hours=ACTIVATION_CODE_VALID_HOURS)
    account.issued_by = _actor(current_user)
    db.commit()
    return {
        "status": "success",
        "activation_code": code,
        "expires_at": account.activation_code_expires_at.isoformat(),
        "instructions": "Hand this code to the patient in person. It expires in 24 hours and is single-use.",
    }


@router.post("/patient-portal/activate")
async def activate_patient_account(
    request: Request, db: Session = Depends(get_cca_db)
):
    """Public (no staff/patient auth yet -- this IS the login). Identity is verified by
    MRN + date of birth + the staff-issued code, all three required to match. On success
    the code is consumed (cannot be replayed) and a patient session token is returned."""
    body = await request.json()
    mrn = body.get("mrn")
    dob = body.get("date_of_birth")
    code = body.get("activation_code")
    if not mrn or not dob or not code:
        raise HTTPException(422, "mrn, date_of_birth, and activation_code are all required")

    patient = db.query(CCAPatient).filter(CCAPatient.mrn == mrn, CCAPatient.dob == dob).first()
    if not patient:
        raise HTTPException(401, "No matching patient record for that MRN and date of birth")

    account = db.query(PatientAccount).filter(PatientAccount.patient_id == patient.id).first()
    if (
        not account or not account.activation_code_hash
        or account.activation_code_hash != hash_activation_code(code)
        or not account.activation_code_expires_at or account.activation_code_expires_at < datetime.utcnow()
    ):
        raise HTTPException(401, "Invalid or expired activation code")

    account.is_activated = True
    account.activated_at = datetime.utcnow()
    account.activation_code_hash = None  # single-use -- consumed on success
    account.activation_code_expires_at = None
    publish(
        db, "PATIENT_PORTAL_ACTIVATED", patient_id=patient.id, actor=f"patient:{patient.mrn}", role="Patient",
        title="Patient portal account activated", category="PATIENT_PORTAL",
        description=f"Patient {patient.mrn} activated their portal account.",
        patient_account_id=account.id,
    )
    db.commit()

    token = create_patient_access_token(account.id, patient.id)
    return {"status": "success", "access_token": token, "token_type": "bearer"}


@router.get("/patient-portal/me/summary")
def get_my_patient_facing_summary(
    db: Session = Depends(get_cca_db),
    current_patient: dict = Depends(get_current_patient)
):
    """The real patient-authenticated endpoint. patient_id comes only from the token
    (get_current_patient) -- never a path/query parameter a client could substitute -- so
    there is no IDOR surface here by construction. Content is byte-identical to the staff
    preview endpoint (cca.py's get_patient_facing_summary): one definition of what's safe to
    show, reused, not reimplemented."""
    return build_patient_facing_summary(db, current_patient["patient_id"])
