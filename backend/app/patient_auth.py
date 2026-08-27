"""
Patient self-service authentication -- structurally separate from auth.py's staff JWT
issuance/validation, not a role bolted onto it. A patient token has a distinct claim shape
(`type: "patient_access"`, no `role`, no `organization_id`) and is checked only by
get_current_patient below, which staff endpoints never depend on; staff auth
(get_current_user, is_admin, is_cca_*) never touches this module either. That makes "a
patient token accidentally accepted by a staff endpoint" (or the reverse) structurally
impossible rather than dependent on every endpoint remembering to check a claim correctly.

Scope note: this is the shell -- the identity/token/provisioning mechanics -- not a full
patient portal. Identity verification at activation is a staff-issued, in-person one-time
code (see routers/patient_portal.py's issue_patient_activation_code), not phone/email OTP:
this codebase has no SMS/email-OTP gateway today, and picking one is a vendor/cost decision
for the institution, not something to assume. Swapping in OTP later only touches
activate_patient_account's verification step below.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .config import settings

PATIENT_TOKEN_TYPE = "patient_access"
PATIENT_ACCESS_TOKEN_EXPIRE_MINUTES = 30
ACTIVATION_CODE_VALID_HOURS = 24

_security = HTTPBearer()


def generate_activation_code() -> str:
    """A 6-digit code, handed to the patient in person by staff -- never transmitted
    electronically by this shell, so there is no SMS/email delivery step to get wrong."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_activation_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def create_patient_access_token(patient_account_id: int, patient_id: int) -> str:
    payload = {
        "patient_account_id": patient_account_id,
        "patient_id": patient_id,
        "type": PATIENT_TOKEN_TYPE,
        "exp": datetime.utcnow() + timedelta(minutes=PATIENT_ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_current_patient(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> dict:
    """The only entry point into patient-authenticated endpoints. patient_id in the returned
    payload is the sole source of truth for "which patient is this" -- callers must use it
    exactly the way staff endpoints use a path parameter, except this one can never be
    supplied or overridden by the client, closing off IDOR by construction rather than by
    convention."""
    try:
        payload = jwt.decode(credentials.credentials, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(401, "Invalid or expired patient session")
    if payload.get("type") != PATIENT_TOKEN_TYPE:
        # A staff token has no `type` claim at all (see auth.py's create_access_token), so
        # this rejects it outright rather than risking a staff identity being misread as a
        # patient one under any future payload-shape coincidence.
        raise HTTPException(401, "Not a patient session")
    return payload
