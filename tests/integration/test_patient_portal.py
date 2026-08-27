"""
Tests for the patient self-service portal shell (backend/app/routers/patient_portal.py,
backend/app/patient_auth.py) -- structural isolation from staff auth, consent-gated
provisioning, single-use activation codes, and the IDOR-closed patient-scoped summary.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CCAConsent, PatientAccount


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@portalhosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@portalhosp.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture
def lab_tech(make_user, oncologist):
    return make_user(email="lab@portalhosp.com", role="CCALabPhlebotomy", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first()


def _grant_portal_consent(db_session, patient_id):
    db_session.add(CCAConsent(
        patient_id=patient_id, consent_types=["treatment", "patient_portal_access"],
        signatory="Patient", captured_by="frontdesk@portalhosp.com", status="ACTIVE",
    ))
    db_session.commit()


def test_activation_code_requires_consent_and_patient_contact_role(client, auth_headers, db_session, oncologist, front_desk, lab_tech):
    patient = _patient(db_session, oncologist.organization_id)

    no_consent = client.post(f"/api/cca/patients/{patient.id}/issue-activation-code", headers=auth_headers(front_desk))
    assert no_consent.status_code == 422

    _grant_portal_consent(db_session, patient.id)

    wrong_role = client.post(f"/api/cca/patients/{patient.id}/issue-activation-code", headers=auth_headers(lab_tech))
    assert wrong_role.status_code == 403

    issued = client.post(f"/api/cca/patients/{patient.id}/issue-activation-code", headers=auth_headers(front_desk))
    assert issued.status_code == 200
    assert len(issued.json()["activation_code"]) == 6


def test_activation_flow_and_single_use_code(client, auth_headers, db_session, oncologist, front_desk):
    patient = _patient(db_session, oncologist.organization_id)
    _grant_portal_consent(db_session, patient.id)
    code = client.post(f"/api/cca/patients/{patient.id}/issue-activation-code", headers=auth_headers(front_desk)).json()["activation_code"]

    wrong_code = client.post("/api/cca/patient-portal/activate", json={
        "mrn": patient.mrn, "date_of_birth": patient.dob, "activation_code": "000000"
    })
    assert wrong_code.status_code == 401

    activated = client.post("/api/cca/patient-portal/activate", json={
        "mrn": patient.mrn, "date_of_birth": patient.dob, "activation_code": code
    })
    assert activated.status_code == 200
    assert "access_token" in activated.json()

    account = db_session.query(PatientAccount).filter(PatientAccount.patient_id == patient.id).first()
    assert account.is_activated is True

    replay = client.post("/api/cca/patient-portal/activate", json={
        "mrn": patient.mrn, "date_of_birth": patient.dob, "activation_code": code
    })
    assert replay.status_code == 401


def test_patient_summary_is_scoped_by_token_and_closed_to_staff_tokens(client, auth_headers, db_session, oncologist, front_desk):
    patient = _patient(db_session, oncologist.organization_id)
    _grant_portal_consent(db_session, patient.id)
    code = client.post(f"/api/cca/patients/{patient.id}/issue-activation-code", headers=auth_headers(front_desk)).json()["activation_code"]
    token = client.post("/api/cca/patient-portal/activate", json={
        "mrn": patient.mrn, "date_of_birth": patient.dob, "activation_code": code
    }).json()["access_token"]
    patient_headers = {"Authorization": f"Bearer {token}"}

    # Not yet approved for patient-facing view -- expected default state.
    res = client.get("/api/cca/patient-portal/me/summary", headers=patient_headers)
    assert res.status_code == 200
    assert res.json()["available"] is False

    # A staff token must never be accepted here.
    staff_denied = client.get("/api/cca/patient-portal/me/summary", headers=auth_headers(oncologist))
    assert staff_denied.status_code == 401

    # No token at all.
    assert client.get("/api/cca/patient-portal/me/summary").status_code in (401, 403)
