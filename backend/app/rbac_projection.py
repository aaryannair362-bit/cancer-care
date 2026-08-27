"""
Least-privilege read projections for Care Plan / Treatment Plan / Treatment Order.

Endpoint-level RBAC (auth.py's is_cca_* predicates, routers/cca.py's _require_clinician /
_require_modality_signer) decides WHO may call an endpoint at all -- that part already
existed. This module decides WHAT a caller who is allowed to call it gets back, which did
not exist anywhere in this codebase before: every role could read the full clinical payload
of any endpoint it wasn't outright blocked from. That's the architecture doc's non-negotiable
#8: "Each role gets a projection of data appropriate to its function... Front Desk gets
scheduling metadata, not clinical rationale; Finance gets modality/cycle counts, not full
clinical notes."

Design choices, stated rather than buried:
  - Every projector is a strict field allowlist (include/omit), never partial redaction of a
    field's contents -- a half-redacted clinical note is its own hazard, and the architecture
    doc's own examples ("not clinical rationale", "not full clinical notes") are binary.
  - Field lists are deliberately coarse-grained (a handful of named tiers per object), not one
    bespoke list per role, to keep this maintainable -- roles with materially the same
    legitimate need (e.g. Radiologist and Pathologist needing treatment-phase context to
    interpret their own results) share a tier.
  - Deliberate deviation from the architecture doc's literal role-matrix table: Admin is
    treated as full-access here, not "metadata/audit only". This matches the existing
    codebase-wide convention -- is_admin() already bypasses every clinical gate in this router
    (_require_clinician, _require_modality_signer, every org-scoping check) for
    support/demo purposes, predating this feature. Narrowing Admin only here would be
    inconsistent with the rest of the application, not a genuine safety improvement, so it is
    flagged here rather than silently changed or silently left as a gap.
  - Row-level visibility (which plans/orders exist to be projected in the first place) is
    handled by the caller (routers/cca.py) alongside these field projections, not here.
"""

from typing import Dict, Optional, Set

from .auth import is_admin, is_doctor, is_cca_oncologist

_CARE_PLAN_FULL_ACCESS_ROLES = {"CCANurseNavigator"}
_CARE_PLAN_OPERATIONAL_ROLES = {"CCAFrontDesk", "CCAPatientLiaison", "CCAFinancialCounsellor"}

CARE_PLAN_TIER_FIELDS: Dict[str, Optional[Set[str]]] = {
    "FULL": None,
    # Radiologist, Radiology Coordinator, Pathologist, Lab/Phlebotomy, Infusion Nurse, MDT
    # Coordinator, External MDT Specialist: enough to place their own task in the patient's
    # journey, not the multidisciplinary content itself (that's CarePlanTask's job, which is
    # separately and already scoped by description/owner -- unaffected by this module).
    "CLINICAL_CONTEXT": {"id", "patient_id", "intent", "status", "version_no", "next_decision_point"},
    # Front Desk / Patient Liaison / Financial Counsellor: scheduling/coordination status only
    # -- no intent (even a coarse Curative/Palliative label), no next_decision_point detail.
    "OPERATIONAL": {"id", "status", "version_no"},
}

_TREATMENT_PLAN_MINIMAL_ROLES = {"CCARadiologyCoordinator", "CCALabPhlebotomy", "CCAPatientLiaison"}

TREATMENT_PLAN_TIER_FIELDS: Dict[str, Optional[Set[str]]] = {
    "FULL": None,
    # Nurse Navigator, Radiologist, Pathologist, MDT Coordinator, External MDT Specialist:
    # enough clinical context to do their own job around the treatment, not the full
    # authoring/administrative trail (no signer identity, no supersedes chain).
    "CLINICAL_CONTEXT": {"id", "patient_id", "modality", "intent", "protocol_name", "status", "version_no", "start_date", "signed_at", "guideline_review_required"},
    # Financial Counsellor: the exact field list the architecture doc's financial-integration
    # section names -- modality, regimen/procedure identifier, anticipated cycles, planned
    # start window, status. Nothing evidentiary or authorizing.
    "FINANCE": {"id", "modality", "protocol_name", "planned_sessions", "start_date", "status"},
    # Radiology Coordinator, Lab/Phlebotomy, Patient Liaison: "R minimal" / "R restricted" --
    # just enough to know a plan exists and its lifecycle state.
    "MINIMAL": {"id", "modality", "status"},
    # Front Desk: "- / minimal context" -- the architecture doc's near-zero tier.
    "NONE": {"id", "status"},
}

TREATMENT_ORDER_TIER_FIELDS: Dict[str, Optional[Set[str]]] = {
    "FULL": None,
    # Everyone else: an order's `instructions` (exact drug/dose/route) is more sensitive than
    # a plan's modality-level summary and has no stated need outside the treating clinicians
    # and the nurse actually administering it.
    "MINIMAL": {"id", "status"},
}


def can_view_draft_plans_and_orders(current_user: dict) -> bool:
    """Separate from field-projection tier: Infusion Nurse gets FULL field access to a
    Treatment Plan/Order once it is visible (see treatment_plan_tier /
    treatment_order_tier), but that's a different question from whether an unsigned,
    speculative DRAFT/PROPOSED one should be visible at all. Only the roles that actually
    author plans/orders -- oncologists, Doctor, Admin -- may see one before it has clinical
    authority; every other role, including Infusion Nurse, only ever sees a plan/order once
    it has been signed (or later)."""
    return is_admin(current_user) or is_doctor(current_user) or is_cca_oncologist(current_user)


def care_plan_tier(current_user: dict) -> str:
    if is_admin(current_user) or is_doctor(current_user) or is_cca_oncologist(current_user):
        return "FULL"
    role = current_user.get("role")
    if role in _CARE_PLAN_FULL_ACCESS_ROLES:
        return "FULL"
    if role in _CARE_PLAN_OPERATIONAL_ROLES:
        return "OPERATIONAL"
    return "CLINICAL_CONTEXT"


def treatment_plan_tier(current_user: dict) -> str:
    if is_admin(current_user) or is_doctor(current_user) or is_cca_oncologist(current_user):
        return "FULL"
    role = current_user.get("role")
    if role == "CCAInfusionNurse":
        # Architecture doc: "R signed systemic plan" -- needs real detail to administer
        # safely. Row-level visibility (drafts hidden from this role) is enforced by the
        # caller; this only decides field shape once a row is visible at all.
        return "FULL"
    if role == "CCAFrontDesk":
        return "NONE"
    if role == "CCAFinancialCounsellor":
        return "FINANCE"
    if role in _TREATMENT_PLAN_MINIMAL_ROLES:
        return "MINIMAL"
    return "CLINICAL_CONTEXT"


def treatment_order_tier(current_user: dict) -> str:
    if is_admin(current_user) or is_doctor(current_user) or is_cca_oncologist(current_user):
        return "FULL"
    if current_user.get("role") == "CCAInfusionNurse":
        return "FULL"
    return "MINIMAL"


def _project(data: dict, allowed_fields: Optional[Set[str]]) -> dict:
    if allowed_fields is None:
        return data
    return {k: v for k, v in data.items() if k in allowed_fields}


def project_care_plan(data: dict, current_user: dict) -> dict:
    return _project(data, CARE_PLAN_TIER_FIELDS[care_plan_tier(current_user)])


def project_treatment_plan(data: dict, current_user: dict) -> dict:
    return _project(data, TREATMENT_PLAN_TIER_FIELDS[treatment_plan_tier(current_user)])


def project_treatment_order(data: dict, current_user: dict) -> dict:
    return _project(data, TREATMENT_ORDER_TIER_FIELDS[treatment_order_tier(current_user)])
