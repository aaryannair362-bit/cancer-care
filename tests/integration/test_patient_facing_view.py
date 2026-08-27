"""
Tests for the P2 patient-facing plan view (backend/app/routers/cca.py's
approve_patient_facing_view / revoke_patient_facing_view / set_task_patient_visible_note /
get_patient_facing_summary) -- gated behind explicit clinical review, never exposing raw
internal task descriptions or clinical reasoning by default.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CarePlanTask


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@patientviewhosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def patient_liaison(make_user, oncologist):
    return make_user(email="liaison@patientviewhosp.com", role="CCAPatientLiaison", organization_id=oncologist.organization_id)


@pytest.fixture
def financial_counsellor(make_user, oncologist):
    return make_user(email="finance@patientviewhosp.com", role="CCAFinancialCounsellor", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def _active_care_plan(client, headers, patient_id):
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    care_plan_id = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan_id], "intent": "Curative"
    }).json()["care_plan"]["id"]
    return care_plan_id


def test_summary_unavailable_before_approval(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    _active_care_plan(client, headers, patient_id)

    res = client.get(f"/api/cca/patients/{patient_id}/patient-facing-summary", headers=headers)
    assert res.status_code == 200
    assert res.json()["available"] is False


def test_approval_requires_clinician_and_only_shows_explicitly_authored_notes(client, auth_headers, db_session, oncologist, patient_liaison, financial_counsellor):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    care_plan_id = _active_care_plan(client, headers, patient_id)

    denied = client.post(f"/api/cca/care-plans/{care_plan_id}/approve-patient-facing", headers=auth_headers(patient_liaison))
    assert denied.status_code == 403

    approved = client.post(f"/api/cca/care-plans/{care_plan_id}/approve-patient-facing", headers=headers)
    assert approved.status_code == 200

    # Financial Counsellor has no patient-contact role for this view.
    fc_denied = client.get(f"/api/cca/patients/{patient_id}/patient-facing-summary", headers=auth_headers(financial_counsellor))
    assert fc_denied.status_code == 403

    # No tasks with a patient_visible_note yet -- next_steps must be empty.
    summary = client.get(f"/api/cca/patients/{patient_id}/patient-facing-summary", headers=auth_headers(patient_liaison)).json()
    assert summary["available"] is True
    assert summary["next_steps"] == []

    # Create an internal task (via AI Search propose-task, reused here as a task-creation path) with clinical-sounding detail...
    task_id = client.post(f"/api/cca/patients/{patient_id}/search/propose-task", headers=headers, json={
        "description": "Reassessment following Grade 3 neutropenia -- hold next cycle."
    }).json()["task"]["id"]

    # ...it must NOT appear until a clinician writes patient-safe wording for it.
    still_empty = client.get(f"/api/cca/patients/{patient_id}/patient-facing-summary", headers=auth_headers(patient_liaison)).json()
    assert still_empty["next_steps"] == []

    note_denied = client.patch(f"/api/cca/tasks/{task_id}/patient-visible-note", headers=auth_headers(patient_liaison), json={"note": "Come in for your next visit."})
    assert note_denied.status_code == 403

    client.patch(f"/api/cca/tasks/{task_id}/patient-visible-note", headers=headers, json={"note": "Please come in for a follow-up blood test."})
    with_note = client.get(f"/api/cca/patients/{patient_id}/patient-facing-summary", headers=auth_headers(patient_liaison)).json()
    assert len(with_note["next_steps"]) == 1
    assert with_note["next_steps"][0]["note"] == "Please come in for a follow-up blood test."
    # Never the raw internal description.
    assert "neutropenia" not in str(with_note)


def test_amendment_revokes_prior_approval(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    care_plan_id = _active_care_plan(client, headers, patient_id)
    client.post(f"/api/cca/care-plans/{care_plan_id}/approve-patient-facing", headers=headers)

    client.put(f"/api/cca/care-plans/{care_plan_id}", headers=headers, json={
        "intent": "Curative Neoadjuvant", "change_reason": "Updated after MDT."
    })

    summary = client.get(f"/api/cca/patients/{patient_id}/patient-facing-summary", headers=headers).json()
    assert summary["available"] is False


def test_revoke_endpoint(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    care_plan_id = _active_care_plan(client, headers, patient_id)
    client.post(f"/api/cca/care-plans/{care_plan_id}/approve-patient-facing", headers=headers)

    revoked = client.post(f"/api/cca/care-plans/{care_plan_id}/revoke-patient-facing", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["patient_facing_approved"] is False

    summary = client.get(f"/api/cca/patients/{patient_id}/patient-facing-summary", headers=headers).json()
    assert summary["available"] is False
