"""
Unit tests for the 5 new CCA role predicates added by the 7 Role/Module Updates developer
handoff (backend/app/auth.py). Pure function tests -- no DB, no client -- covering that each
predicate matches only its own role string and is registered in CCA_ROLES.
"""
from app.auth import (
    CCA_ROLES,
    is_cca_radiation_technologist, is_cca_radiology_technician, is_cca_biller,
    is_cca_patient_relations_executive, is_cca_inpatient_oncology_nurse,
)

_NEW_PREDICATES = {
    "CCARadiationTechnologist": is_cca_radiation_technologist,
    "CCARadiologyTechnician": is_cca_radiology_technician,
    "CCABiller": is_cca_biller,
    "CCAPatientRelationsExecutive": is_cca_patient_relations_executive,
    "CCAInpatientOncologyNurse": is_cca_inpatient_oncology_nurse,
}


def test_new_roles_are_registered_in_cca_roles():
    for role in _NEW_PREDICATES:
        assert role in CCA_ROLES, f"{role} must be in CCA_ROLES"


def test_each_predicate_matches_only_its_own_role():
    for role, predicate in _NEW_PREDICATES.items():
        assert predicate({"role": role}) is True
        for other_role in _NEW_PREDICATES:
            if other_role != role:
                assert predicate({"role": other_role}) is False, f"{predicate.__name__} must not match {other_role}"
        assert predicate({"role": "CCARadiologist"}) is False
        assert predicate({"role": "CCAFinancialCounsellor"}) is False
        assert predicate({"role": "Admin"}) is False


def test_predicates_tolerate_a_missing_role_key():
    for predicate in _NEW_PREDICATES.values():
        assert predicate({}) is False
