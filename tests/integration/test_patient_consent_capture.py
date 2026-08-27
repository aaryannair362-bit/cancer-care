"""
Tests for POST/GET /api/cca/patients/{id}/consents (backend/app/routers/cca.py's
capture_consent / list_consents) -- previously CCAConsent had no create endpoint at all, only
cca_seed.py's hardcoded demo rows, so the patient portal's 'patient_portal_access' consent
gate (routers/patient_portal.py) could never actually be satisfied outside the demo seed.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CCAConsent


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@consentcapture.com", role="CCAMedicalOncologist")


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@consentcapture.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture
def financial_counsellor(make_user, oncologist):
    return make_user(email="finance@consentcapture.com", role="CCAFinancialCounsellor", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def test_front_desk_can_capture_consent(client, auth_headers, db_session, oncologist, front_desk):
    fd_headers = auth_headers(front_desk)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    res = client.post(f"/api/cca/patients/{patient_id}/consents", headers=fd_headers, json={
        "consent_types": ["treatment", "patient_portal_access"],
        "signatory": "Patient",
    })
    assert res.status_code == 201
    body = res.json()["consent"]
    assert body["status"] == "ACTIVE"
    assert "patient_portal_access" in body["consent_types"]

    listed = client.get(f"/api/cca/patients/{patient_id}/consents", headers=fd_headers)
    assert listed.status_code == 200
    assert any("patient_portal_access" in c["consent_types"] for c in listed.json()["consents"])


def test_consent_capture_requires_patient_contact_role(client, auth_headers, db_session, oncologist, financial_counsellor):
    fin_headers = auth_headers(financial_counsellor)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    res = client.post(f"/api/cca/patients/{patient_id}/consents", headers=fin_headers, json={
        "consent_types": ["treatment"], "signatory": "Patient",
    })
    assert res.status_code == 403


def test_consent_capture_requires_types_and_signatory(client, auth_headers, db_session, oncologist, front_desk):
    fd_headers = auth_headers(front_desk)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    missing_types = client.post(f"/api/cca/patients/{patient_id}/consents", headers=fd_headers, json={"signatory": "Patient"})
    assert missing_types.status_code == 422

    missing_signatory = client.post(f"/api/cca/patients/{patient_id}/consents", headers=fd_headers, json={"consent_types": ["treatment"]})
    assert missing_signatory.status_code == 422


def test_captured_consent_unblocks_portal_activation_code_issuance(client, auth_headers, db_session, oncologist, front_desk):
    fd_headers = auth_headers(front_desk)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    blocked = client.post(f"/api/cca/patients/{patient_id}/issue-activation-code", headers=fd_headers)
    assert blocked.status_code == 422

    client.post(f"/api/cca/patients/{patient_id}/consents", headers=fd_headers, json={
        "consent_types": ["patient_portal_access"], "signatory": "Patient",
    })

    issued = client.post(f"/api/cca/patients/{patient_id}/issue-activation-code", headers=fd_headers)
    assert issued.status_code == 200
    assert "activation_code" in issued.json()
