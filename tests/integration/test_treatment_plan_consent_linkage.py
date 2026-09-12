"""
Final gap-closing round, item 13: Treatment consent linkage per TreatmentPlan. Previously
CCAConsent was only a generic patient-level record captured at registration, with no way
to tell which treatment strategy a given consent actually covers.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@consent-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="fd@consent-test.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def fd_headers(auth_headers, front_desk):
    return auth_headers(front_desk)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="CONSENT-TEST-0001", name="Consent Linkage Test Patient", age=50, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def test_consent_status_before_and_after_linkage(client, onc_headers, fd_headers, patient):
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={
        "patient_id": patient.id, "intent": "Curative", "protocol_name": "AC-T",
    }).json()["treatment_plan"]["id"]

    before = client.get(f"/api/cca/treatment-plans/{plan_id}/consent-status", headers=onc_headers)
    assert before.status_code == 200, before.text
    assert before.json()["consent_obtained"] is False

    wrong_plan = client.post(f"/api/cca/patients/{patient.id}/consents", headers=fd_headers, json={
        "consent_types": ["treatment"], "signatory": "Patient", "treatment_plan_id": 999999,
    })
    assert wrong_plan.status_code == 422

    linked = client.post(f"/api/cca/patients/{patient.id}/consents", headers=fd_headers, json={
        "consent_types": ["treatment"], "signatory": "Patient", "treatment_plan_id": plan_id,
    })
    assert linked.status_code == 201, linked.text
    assert linked.json()["consent"]["treatment_plan_id"] == plan_id

    after = client.get(f"/api/cca/treatment-plans/{plan_id}/consent-status", headers=onc_headers)
    assert after.status_code == 200
    assert after.json()["consent_obtained"] is True
    assert after.json()["consent"]["consent_types"] == ["treatment"]


def test_generic_consent_still_works_without_plan_link(client, fd_headers, patient):
    generic = client.post(f"/api/cca/patients/{patient.id}/consents", headers=fd_headers, json={
        "consent_types": ["ai_assistance"], "signatory": "Patient",
    })
    assert generic.status_code == 201, generic.text
    assert generic.json()["consent"]["treatment_plan_id"] is None
