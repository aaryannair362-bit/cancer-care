"""
Unit tests for backend/app/rbac_projection.py -- the tier assignment for every one of the
15 CCA roles (plus Doctor/Admin) on Care Plan / Treatment Plan / Treatment Order, and the
field-projection behavior itself. Deliberately exhaustive over every role so a future role
addition or renaming can't silently fall through to the wrong tier.
"""
from app.rbac_projection import (
    care_plan_tier, treatment_plan_tier, treatment_order_tier,
    project_care_plan, project_treatment_plan, project_treatment_order,
)

ALL_ROLES = [
    "Doctor", "Admin",
    "CCAFrontDesk", "CCANurseNavigator", "CCAMedicalOncologist", "CCASurgicalOncologist",
    "CCARadiationOncologist", "CCARadiologist", "CCARadiologyCoordinator", "CCAPathologist",
    "CCALabPhlebotomy", "CCAInfusionNurse", "CCAMDTCoordinator", "CCAExternalMDTSpecialist",
    "CCAPatientLiaison", "CCAFinancialCounsellor",
]


def _user(role):
    return {"role": role, "user_id": 1, "organization_id": 1}


def test_every_role_maps_to_a_known_care_plan_tier():
    expected_full = {"Doctor", "Admin", "CCAMedicalOncologist", "CCASurgicalOncologist", "CCARadiationOncologist", "CCANurseNavigator"}
    expected_operational = {"CCAFrontDesk", "CCAPatientLiaison", "CCAFinancialCounsellor"}
    for role in ALL_ROLES:
        tier = care_plan_tier(_user(role))
        if role in expected_full:
            assert tier == "FULL", role
        elif role in expected_operational:
            assert tier == "OPERATIONAL", role
        else:
            assert tier == "CLINICAL_CONTEXT", role


def test_every_role_maps_to_a_known_treatment_plan_tier():
    expected_full = {"Doctor", "Admin", "CCAMedicalOncologist", "CCASurgicalOncologist", "CCARadiationOncologist", "CCAInfusionNurse"}
    expected_none = {"CCAFrontDesk"}
    expected_finance = {"CCAFinancialCounsellor"}
    expected_minimal = {"CCARadiologyCoordinator", "CCALabPhlebotomy", "CCAPatientLiaison"}
    for role in ALL_ROLES:
        tier = treatment_plan_tier(_user(role))
        if role in expected_full:
            assert tier == "FULL", role
        elif role in expected_none:
            assert tier == "NONE", role
        elif role in expected_finance:
            assert tier == "FINANCE", role
        elif role in expected_minimal:
            assert tier == "MINIMAL", role
        else:
            assert tier == "CLINICAL_CONTEXT", role


def test_treatment_order_tier_is_full_only_for_treating_roles():
    expected_full = {"Doctor", "Admin", "CCAMedicalOncologist", "CCASurgicalOncologist", "CCARadiationOncologist", "CCAInfusionNurse"}
    for role in ALL_ROLES:
        tier = treatment_order_tier(_user(role))
        assert tier == ("FULL" if role in expected_full else "MINIMAL"), role


def test_project_care_plan_drops_clinical_content_for_front_desk():
    full = {"id": 1, "patient_id": 9, "intent": "Curative", "goals": ["x"], "components": {"systemic": "AC-T"},
            "monitoring_plan": {}, "follow_up_plan": "in 14 days", "next_decision_point": "post-cycle-4", "version_no": 2, "status": "ACTIVE"}
    projected = project_care_plan(full, _user("CCAFrontDesk"))
    assert projected == {"id": 1, "status": "ACTIVE", "version_no": 2}
    assert "components" not in projected and "goals" not in projected and "intent" not in projected


def test_project_treatment_plan_finance_tier_matches_spec_field_list():
    full = {"id": 5, "patient_id": 9, "care_plan_id": None, "mdt_decision_id": 3, "intent": "Curative",
            "modality": "Systemic Chemotherapy", "protocol_name": "AC-T", "planned_sessions": 8,
            "completed_sessions": 2, "start_date": "2026-09-01", "version_no": 1, "status": "ACTIVE",
            "supersedes_id": None, "signer_email": "onc@x.com", "signer_role": "CCAMedicalOncologist", "created_by": "onc@x.com"}
    projected = project_treatment_plan(full, _user("CCAFinancialCounsellor"))
    assert projected == {"id": 5, "modality": "Systemic Chemotherapy", "protocol_name": "AC-T", "planned_sessions": 8, "start_date": "2026-09-01", "status": "ACTIVE"}
    assert "signer_email" not in projected and "intent" not in projected and "completed_sessions" not in projected


def test_project_treatment_order_hides_instructions_from_non_clinical_roles():
    full = {"id": 7, "treatment_plan_id": 5, "treatment_session_id": 3, "patient_id": 9,
            "instructions": {"drug": "Doxorubicin", "dose": "60mg/m2"}, "version_no": 1,
            "status": "SIGNED", "signer_email": "onc@x.com", "signer_role": "CCAMedicalOncologist", "created_by": "onc@x.com"}
    for role in ("CCAFrontDesk", "CCAFinancialCounsellor", "CCAPatientLiaison", "CCAMDTCoordinator"):
        projected = project_treatment_order(full, _user(role))
        assert projected == {"id": 7, "status": "SIGNED"}, role
    onc_view = project_treatment_order(full, _user("CCAMedicalOncologist"))
    assert onc_view == full
    nurse_view = project_treatment_order(full, _user("CCAInfusionNurse"))
    assert nurse_view == full


def test_full_tier_projection_is_the_identity_function():
    full = {"id": 1, "anything": "goes", "here": {"nested": True}}
    for project_fn in (project_care_plan, project_treatment_plan, project_treatment_order):
        assert project_fn(full, _user("Doctor")) == full
        assert project_fn(full, _user("Admin")) == full
