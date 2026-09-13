import re
from datetime import datetime, timedelta
from typing import Dict, Optional
from jose import jwt
from passlib.context import CryptContext
from fastapi import APIRouter, HTTPException, status, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from .config import settings
from .models import User, PasswordHistory, AuditLog

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
security = HTTPBearer()
router = APIRouter()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def validate_password_complexity(password: str, email: str) -> tuple[bool, str]:
    if len(password) < 12:
        return False, "Password must be at least 12 characters long"
    if len(password) > 128:
        return False, "Password must be no more than 128 characters long"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r'[0-9]', password):
        return False, "Password must contain at least one number"
    if not re.search(r'[^A-Za-z0-9]', password):
        return False, "Password must contain at least one special character"
    
    normalized = password.lower()
    for i in range(len(normalized) - 2):
        c1, c2, c3 = ord(normalized[i]), ord(normalized[i+1]), ord(normalized[i+2])
        if c2 == c1 + 1 and c3 == c2 + 1:
            return False, "Password contains sequential characters"
        if c1 == c2 == c3:
            return False, "Password contains repeated characters"
    
    common = ['qwerty', 'asdfg', 'zxcvb', 'password', 'admin']
    for p in common:
        if p in normalized:
            return False, f"Password contains common pattern: {p}"
    
    email_prefix = email.split('@')[0].lower()
    if email_prefix in normalized:
        return False, "Password contains part of your email"
    
    return True, ""

def create_access_token(data: Dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def create_refresh_token(data: Dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_token(token: str) -> Dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.JWTError:
        return {}

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token")
    return {
        "id": payload.get("user_id"),
        "email": payload.get("email"),
        "role": payload.get("role"),
        "organization_id": payload.get("organization_id")
    }

def log_audit(db: Session, user_id: int, email: str, org_id: int, action: str, resource: str, result: str, details: str = None):
    audit = AuditLog(
        user_id=user_id,
        email=email,
        organization_id=org_id,
        action=action,
        resource=resource,
        result=result,
        details=details
    )
    db.add(audit)
    db.commit()

def is_head_nurse(user: dict) -> bool:
    return user.get("role") == "HeadNurse"

def is_nursing_station(user: dict) -> bool:
    return user.get("role") == "NursingStation"

def is_nurse(user: dict) -> bool:
    return user.get("role") == "Nurse"

def is_admin(user: dict) -> bool:
    return user.get("role") == "Admin"

def is_pharmacist(user: dict) -> bool:
    return user.get("role") == "Pharmacist"

def is_billing_staff(user: dict) -> bool:
    return user.get("role") == "Billing"

def is_tpa(user: dict) -> bool:
    return user.get("role") == "TPA"

def is_inventory_manager(user: dict) -> bool:
    return user.get("role") == "InventoryManager"

def is_doctor(user: dict) -> bool:
    return user.get("role") == "Doctor"

# --- CCA Cancer Care OS role-screen-spec personas -----------------------------------------
# 15 distinct roles named in the role-screen specifications (updates/documentation/*.pdf).
# Deliberately separate from the general HMS role vocabulary above (Doctor/Nurse/...) rather
# than overloaded onto it: "Nurse Navigator" and "Infusion Nurse" are both nurse-like but
# distinct CCA personas from the IPD ward "Nurse" role, and conflating them would make a role
# check ambiguous about which screen set a user should see. Admin/Operations reuses the
# existing Admin role (is_admin) rather than adding a 15th -- a real hospital Admin account is
# expected to also want CCA operational visibility, not a separate login.
CCA_ROLES = (
    "CCAFrontDesk", "CCANurseNavigator", "CCAMedicalOncologist", "CCASurgicalOncologist",
    "CCARadiationOncologist", "CCARadiologist", "CCARadiologyCoordinator", "CCAPathologist",
    "CCALabPhlebotomy", "CCAInfusionNurse", "CCAMDTCoordinator", "CCAExternalMDTSpecialist",
    "CCAPatientLiaison", "CCAFinancialCounsellor", "CCAPharmacist",
    "CCARadiationPhysicist", "CCASurgicalNurse",
    "CCAPalliativeCareSpecialist",
    # 7 Role/Module Updates (developer handoff PDF): split "Radiologist / Radiation
    # Technologist" and "Financial Counsellor / Patient Financial Services" into their own
    # roles, plus two genuinely new modules -- see is_cca_radiation_technologist etc. below
    # for what each one actually gates.
    "CCARadiationTechnologist", "CCARadiologyTechnician", "CCABiller",
    "CCAPatientRelationsExecutive", "CCAInpatientOncologyNurse",
    # Core Oncology 4 Sections + 11 Additional Modules developer handoffs: R10 Anaesthetist
    # (the only role from the 11-modules PDF in scope for this pass) -- see
    # is_cca_anaesthetist below for what it gates.
    "CCAAnaesthetist",
)


def is_cca_front_desk(user: dict) -> bool:
    return user.get("role") == "CCAFrontDesk"


def is_cca_nurse_navigator(user: dict) -> bool:
    return user.get("role") == "CCANurseNavigator"


def is_cca_medical_oncologist(user: dict) -> bool:
    return user.get("role") == "CCAMedicalOncologist"


def is_cca_surgical_oncologist(user: dict) -> bool:
    return user.get("role") == "CCASurgicalOncologist"


def is_cca_radiation_oncologist(user: dict) -> bool:
    return user.get("role") == "CCARadiationOncologist"


def is_cca_radiation_physicist(user: dict) -> bool:
    return user.get("role") == "CCARadiationPhysicist"


def is_cca_palliative_care_specialist(user: dict) -> bool:
    """Palliative Treatment Orders and Procedures & Notes (Gap Analysis PDF items 30-31) --
    supportive/comfort care, distinct from the three treating-oncologist modalities in
    is_cca_oncologist above (this specialty doesn't sign a systemic/surgical/radiation
    TreatmentPlan, so it's deliberately excluded from that predicate)."""
    return user.get("role") == "CCAPalliativeCareSpecialist"


def is_cca_surgical_nurse(user: dict) -> bool:
    """The OR-side nurse who documents intra-operative monitoring, specimen labelling/lab
    handoff, and surgical blood transfusion (Gap Analysis PDF items 24, 26, 27) -- kept
    separate from CCAInfusionNurse the same way that role is kept separate from the general
    'Nurse': a different screen set and a different care setting (operating theatre vs.
    day-care infusion chair)."""
    return user.get("role") == "CCASurgicalNurse"


def is_cca_oncologist(user: dict) -> bool:
    """Any of the three treating-oncologist personas -- used for permissions shared across
    all three (e.g. Consultation, NEXUS, Guideline Pathway, Staging, MDT read/contribute)."""
    return user.get("role") in (
        "CCAMedicalOncologist", "CCASurgicalOncologist", "CCARadiationOncologist",
    )


def is_cca_radiologist(user: dict) -> bool:
    return user.get("role") == "CCARadiologist"


def is_cca_radiology_coordinator(user: dict) -> bool:
    return user.get("role") == "CCARadiologyCoordinator"


def is_cca_radiation_technologist(user: dict) -> bool:
    """Executes radiation treatment fractions under the released plan -- pre-treatment
    verification, fraction delivery, interruption/resume, and equipment-issue reporting
    (cca_oncology_ext.py). Previously stood in for by CCARadiologist (see that module's git
    history); now its own role per the 7 Role/Module Updates developer handoff."""
    return user.get("role") == "CCARadiationTechnologist"


def is_cca_radiology_technician(user: dict) -> bool:
    """Performs the technical imaging acquisition step (identity/study verification, modality/
    protocol/contrast capture, technical completion) between CCARadiologyCoordinator's
    scheduling and CCARadiologist's interpretation/reporting -- a workflow step that had no
    role of its own before the 7 Role/Module Updates developer handoff."""
    return user.get("role") == "CCARadiologyTechnician"


def is_cca_pathologist(user: dict) -> bool:
    return user.get("role") == "CCAPathologist"


def is_cca_lab_phlebotomy(user: dict) -> bool:
    return user.get("role") == "CCALabPhlebotomy"


def is_cca_infusion_nurse(user: dict) -> bool:
    return user.get("role") == "CCAInfusionNurse"


def is_cca_pharmacist(user: dict) -> bool:
    """Distinct from the general HMS 'Pharmacist' role (backend/app/routers/pharmacy.py's
    formulary/FEFO-dispensing/narcotics-register module) for the same reason CCANurseNavigator
    and CCAInfusionNurse stay separate from the general 'Nurse' role above: this persona
    verifies/prepares/dispenses against the CCA oncology treatment-order chain
    (PharmacyReadiness, InfusionMedicationAdministration), a different screen set and a
    different data model from general hospital pharmacy inventory."""
    return user.get("role") == "CCAPharmacist"


def is_cca_mdt_coordinator(user: dict) -> bool:
    return user.get("role") == "CCAMDTCoordinator"


def is_cca_external_mdt_specialist(user: dict) -> bool:
    return user.get("role") == "CCAExternalMDTSpecialist"


def is_cca_patient_liaison(user: dict) -> bool:
    return user.get("role") == "CCAPatientLiaison"


def is_cca_financial_counsellor(user: dict) -> bool:
    """Broader financial-coordination role (displayed as "Finance / Billing" per the 7
    Role/Module Updates developer handoff -- the role identifier itself is unchanged to avoid
    migrating existing seeded users). Owns financial-case counselling/estimate/insurance/
    clearance and preauthorization/high-cost-drug-approval decisions; transaction-level billing
    (claims/refunds/billable-events) is CCABiller's, a separate role, below."""
    return user.get("role") == "CCAFinancialCounsellor"


def is_cca_biller(user: dict) -> bool:
    """Transaction/operations-facing billing role (7 Role/Module Updates developer handoff) --
    billable-event capture, claim tracking, and refunds/credit notes (cca_coordination.py).
    Deliberately excludes the broader financial-counselling/preauthorization scope that stays
    with CCAFinancialCounsellor ("Finance / Billing") -- see that predicate's docstring."""
    return user.get("role") == "CCABiller"


def is_cca_patient_relations_executive(user: dict) -> bool:
    """PRE: a strictly non-clinical, operational role -- appointment coordination, patient-
    facing tasks/alerts, and activity/location visibility (7 Role/Module Updates developer
    handoff). Distinct from CCAPatientLiaison, which has broader clinical Patient History
    visibility; PRE must never see clinical case-summary detail (see cca.py's get_case_summary,
    which 403s PRE the same way it already does CCAFrontDesk)."""
    return user.get("role") == "CCAPatientRelationsExecutive"


def is_cca_inpatient_oncology_nurse(user: dict) -> bool:
    """Dedicated inpatient-ward nursing role for admitted oncology patients (7 Role/Module
    Updates developer handoff) -- MAR/medication administration, nursing flowsheet, transfer/
    handover, and deterioration/escalation (cca_inpatient.py). Added alongside (not instead of)
    the general Nurse/HeadNurse/CCAInfusionNurse roles that stood in for this before, so
    existing access keeps working."""
    return user.get("role") == "CCAInpatientOncologyNurse"


def is_cca_anaesthetist(user: dict) -> bool:
    """R10 Anaesthetist (11 Additional Modules Detailed Developer Handoff, page 11) --
    pre-operative evaluation/clearance, intra-operative anaesthesia record, and post-
    anaesthesia recovery documentation (routers/cca_anaesthesia.py). Deliberately narrower
    than is_cca_surgical_oncologist/is_cca_surgical_nurse: an Anaesthetist never writes
    SurgicalPlan.procedure or SurgicalOperativeNote content (cross-module requirement: do not
    alter surgeon-owned diagnosis/operative findings)."""
    return user.get("role") == "CCAAnaesthetist"


def can_sign_treatment_plan(user: dict) -> bool:
    """Only treating oncologists or Doctor can sign treatment plans -- deliberately NOT Admin
    (architecture doc: Admin/Operations "cannot edit signed clinical notes, diagnoses,
    finalized radiology/pathology reports or clinician-approved treatment plans"). Actual
    Treatment Plan signing is gated separately by routers/cca.py's _require_modality_signer;
    this predicate's one caller is can_approve_mdt_recommendation below."""
    return is_doctor(user) or is_cca_oncologist(user)


def can_approve_mdt_recommendation(user: dict) -> bool:
    """Who may record the Accept/Partial/Reject disposition on a finalized MDT recommendation
    (routers/cca.py's approve_mdt_recommendation). Always the treating oncologist/Doctor; also
    the MDT Coordinator per cca_product_decisions.MDT_COORDINATOR_CAN_APPROVE_RECOMMENDATIONS
    -- a deliberate, later override of this module's original hard boundary, made by explicit
    product-owner instruction (see that flag's docstring for who and why). Does not affect
    actual Treatment Plan signing (can_sign_treatment_plan / _require_modality_signer,
    unaffected) or Care Plan/Treatment Plan authorship, both of which stay with the treating
    clinician."""
    if can_sign_treatment_plan(user):
        return True
    from .cca_product_decisions import MDT_COORDINATOR_CAN_APPROVE_RECOMMENDATIONS
    return MDT_COORDINATOR_CAN_APPROVE_RECOMMENDATIONS and is_cca_mdt_coordinator(user)


def can_finalize_diagnostic_report(user: dict) -> bool:
    """Only Radiologist, Pathologist, or Doctor can sign diagnostic reports -- deliberately
    NOT Admin, for the same reason as can_sign_treatment_plan above."""
    return is_doctor(user) or is_cca_radiologist(user) or is_cca_pathologist(user)


def create_external_specialist_token(case_id: int, specialist_email: str, expires_hours: int = 72) -> str:
    """Create a case-scoped, time-bounded JWT token for External MDT Specialist access."""
    expire = datetime.utcnow() + timedelta(hours=expires_hours)
    payload = {
        "sub": specialist_email,
        "role": "CCAExternalMDTSpecialist",
        "case_id": case_id,
        "type": "external_specialist",
        "exp": expire
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)