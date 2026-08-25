"""
Tests for MDT Coordinator scheduling, External MDT Specialist access/opinions, Financial
Counsellor, Patient Liaison/Care Coordination, and Admin Operations
(backend/app/routers/cca_coordination.py).
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, MDTCase


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@coordhosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def mdt_coordinator(make_user, oncologist):
    return make_user(email="mdtcoord@coordhosp.com", role="CCAMDTCoordinator", organization_id=oncologist.organization_id)


@pytest.fixture
def external_specialist(make_user, oncologist):
    return make_user(email="external.expert@partner-hospital.com", role="CCAExternalMDTSpecialist", organization_id=oncologist.organization_id)


@pytest.fixture
def financial_counsellor(make_user, oncologist):
    return make_user(email="finance@coordhosp.com", role="CCAFinancialCounsellor", organization_id=oncologist.organization_id)


@pytest.fixture
def patient_liaison(make_user, oncologist):
    return make_user(email="liaison@coordhosp.com", role="CCAPatientLiaison", organization_id=oncologist.organization_id)


@pytest.fixture
def admin(make_user, oncologist):
    return make_user(email="admin@coordhosp.com", role="Admin", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id).first().id


def _make_mdt_case(client, headers, patient_id):
    res = client.post("/api/cca/mdt/cases", headers=headers, json={"patient_id": patient_id, "question": "Sequencing?"})
    return res.json()["mdt_case"]["id"]


def test_mdt_coordinator_scheduling_and_participants(client, auth_headers, db_session, oncologist, mdt_coordinator):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    onc_headers = auth_headers(oncologist)
    coord_headers = auth_headers(mdt_coordinator)
    case_id = _make_mdt_case(client, onc_headers, patient_id)

    queue = client.get("/api/cca/mdt/referral-queue", headers=coord_headers).json()["queue"]
    assert any(c["id"] == case_id for c in queue)
    assert "readiness" in queue[0]

    denied = client.patch(f"/api/cca/mdt/cases/{case_id}/schedule", headers=onc_headers, json={"board_date": "2026-09-10"})
    assert denied.status_code == 403

    sched = client.patch(f"/api/cca/mdt/cases/{case_id}/schedule", headers=coord_headers, json={
        "board_date": "2026-09-10", "start_time": "14:00", "meeting_type": "Virtual", "meeting_link": "https://meet.example/x",
    })
    assert sched.status_code == 200
    assert sched.json()["case"]["meeting_type"] == "Virtual"
    assert sched.json()["case"]["status"] == "SCHEDULED"

    add_p = client.post(f"/api/cca/mdt/cases/{case_id}/participants", headers=coord_headers, json={
        "specialist_name": "Dr. Nair", "specialist_role": "Surgical Oncologist",
    })
    assert add_p.status_code == 201
    participant_id = add_p.json()["participant"]["id"]

    update_p = client.patch(f"/api/cca/mdt/participants/{participant_id}", headers=coord_headers, json={"invitation_status": "Accepted"})
    assert update_p.status_code == 200
    assert update_p.json()["participant"]["invitation_status"] == "Accepted"

    state = client.patch(f"/api/cca/mdt/cases/{case_id}/state", headers=coord_headers, json={"status": "DISCUSSED"})
    assert state.status_code == 200
    assert state.json()["case"]["status"] == "DISCUSSED"

    bad_state = client.patch(f"/api/cca/mdt/cases/{case_id}/state", headers=coord_headers, json={"status": "NOT_A_REAL_STATE"})
    assert bad_state.status_code == 422


def test_external_specialist_case_scoped_access_and_opinion(client, auth_headers, db_session, oncologist, mdt_coordinator, external_specialist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    onc_headers = auth_headers(oncologist)
    coord_headers = auth_headers(mdt_coordinator)
    ext_headers = auth_headers(external_specialist)
    case_id = _make_mdt_case(client, onc_headers, patient_id)

    # Before any grant, the external specialist sees nothing and cannot open the case.
    empty = client.get("/api/cca/mdt/assigned-cases", headers=ext_headers).json()["assigned_cases"]
    assert empty == []
    denied = client.get(f"/api/cca/mdt/assigned-cases/{case_id}", headers=ext_headers)
    assert denied.status_code == 404

    # A non-coordinator cannot grant access.
    denied_grant = client.post(f"/api/cca/mdt/cases/{case_id}/external-access", headers=onc_headers, json={
        "specialist_name": "Dr. External", "specialist_email": "external.expert@partner-hospital.com",
    })
    assert denied_grant.status_code == 403

    grant = client.post(f"/api/cca/mdt/cases/{case_id}/external-access", headers=coord_headers, json={
        "specialist_name": "Dr. External", "specialist_email": "external.expert@partner-hospital.com",
    })
    assert grant.status_code == 201
    access_id = grant.json()["access"]["id"]

    assigned = client.get("/api/cca/mdt/assigned-cases", headers=ext_headers).json()["assigned_cases"]
    assert any(c["id"] == case_id for c in assigned)

    opinion = client.post(f"/api/cca/mdt/cases/{case_id}/opinions", headers=ext_headers, json={
        "recommendation": "Consider neoadjuvant therapy first.", "certainty": "Moderate",
    })
    assert opinion.status_code == 201
    assert opinion.json()["opinion"]["specialist_name"] == "Dr. External"

    # Revoke -> access and opinion visibility both stop working for the external specialist.
    revoke = client.patch(f"/api/cca/mdt/external-access/{access_id}", headers=coord_headers, json={"revoke": True})
    assert revoke.status_code == 200
    after_revoke = client.get(f"/api/cca/mdt/assigned-cases/{case_id}", headers=ext_headers)
    assert after_revoke.status_code == 404


def test_financial_counselling_full_flow(client, auth_headers, db_session, oncologist, financial_counsellor, patient_liaison):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    fc_headers = auth_headers(financial_counsellor)
    liaison_headers = auth_headers(patient_liaison)

    create = client.post("/api/cca/financial/cases", headers=fc_headers, json={"patient_id": patient_id})
    assert create.status_code == 201
    case_id = create.json()["case"]["id"]

    counselling = client.patch(f"/api/cca/financial/cases/{case_id}/counselling", headers=fc_headers, json={
        "counselling_status": "Completed", "counselling_notes": "Explained treatment cost and options.",
        "patient_decision": "Proceeding",
    })
    assert counselling.status_code == 200
    assert counselling.json()["case"]["counselling_status"] == "Completed"

    estimate = client.patch(f"/api/cca/financial/cases/{case_id}/estimate", headers=fc_headers, json={
        "estimate": {"components": [{"item": "Chemotherapy (8 cycles)", "amount": 320000}], "total": 320000},
        "estimate_status": "Shared",
    })
    assert estimate.status_code == 200
    assert estimate.json()["case"]["estimate"]["total"] == 320000

    insurance = client.patch(f"/api/cca/financial/cases/{case_id}/insurance", headers=fc_headers, json={
        "payer_route": "PrivateInsurance", "insurance_status": "AuthorizationSubmitted",
    })
    assert insurance.status_code == 200

    clearance = client.patch(f"/api/cca/financial/cases/{case_id}/clearance", headers=fc_headers, json={"financial_clearance_status": "Cleared"})
    assert clearance.status_code == 200
    assert clearance.json()["case"]["financial_clearance_status"] == "Cleared"

    bad_clearance = client.patch(f"/api/cca/financial/cases/{case_id}/clearance", headers=fc_headers, json={"financial_clearance_status": "NotAValidState"})
    assert bad_clearance.status_code == 422

    # Patient Liaison has read-only visibility, not write.
    read = client.get(f"/api/cca/financial/cases/{case_id}", headers=liaison_headers)
    assert read.status_code == 200
    denied_write = client.patch(f"/api/cca/financial/cases/{case_id}/clearance", headers=liaison_headers, json={"financial_clearance_status": "Cleared"})
    assert denied_write.status_code == 403


def test_care_coordination_milestones_and_barriers(client, auth_headers, db_session, oncologist, patient_liaison):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    liaison_headers = auth_headers(patient_liaison)

    create = client.post("/api/cca/coordination/cases", headers=liaison_headers, json={"patient_id": patient_id})
    assert create.status_code == 201
    case_id = create.json()["case"]["id"]

    detail = client.get(f"/api/cca/coordination/cases/{case_id}", headers=liaison_headers).json()
    assert detail["care_milestones"]["nurse_intake_completed"] is True  # demo patient already has an intake

    contact = client.patch(f"/api/cca/coordination/cases/{case_id}/contact", headers=liaison_headers, json={
        "communication_status": "Reached", "preferred_contact_method": "Phone",
    })
    assert contact.status_code == 200

    barrier = client.post(f"/api/cca/coordination/cases/{case_id}/barriers", headers=liaison_headers, json={
        "type": "TransportationIssue", "notes": "No transport for next appointment.",
    })
    assert barrier.status_code == 200
    assert len(barrier.json()["case"]["barriers"]) == 1

    next_action = client.patch(f"/api/cca/coordination/cases/{case_id}/next-action", headers=liaison_headers, json={
        "next_action": "Arrange hospital transport", "next_action_owner": "liaison@coordhosp.com",
        "next_action_due": "2026-09-05", "next_action_status": "InProgress",
    })
    assert next_action.status_code == 200
    assert next_action.json()["case"]["next_action_status"] == "InProgress"


def test_admin_operations_dashboard_and_audit(client, auth_headers, db_session, oncologist, admin):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    onc_headers = auth_headers(oncologist)
    admin_headers = auth_headers(admin)

    denied = client.get("/api/cca/admin/operations-dashboard", headers=onc_headers)
    assert denied.status_code == 403

    dashboard = client.get("/api/cca/admin/operations-dashboard", headers=admin_headers)
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["patients_total"] >= 1
    assert "mdt_cases_pending" in body

    audit = client.get(f"/api/cca/admin/audit?patient_id={patient_id}", headers=admin_headers)
    assert audit.status_code == 200
    assert len(audit.json()["events"]) > 0

    denied_audit = client.get("/api/cca/admin/audit", headers=onc_headers)
    assert denied_audit.status_code == 403


def test_coordination_module_is_org_scoped(client, auth_headers, make_user, db_session, oncologist, mdt_coordinator):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    onc_headers = auth_headers(oncologist)
    coord_headers = auth_headers(mdt_coordinator)
    case_id = _make_mdt_case(client, onc_headers, patient_id)

    other_coordinator = make_user(email="other.coord@rivalhosp.com", role="CCAMDTCoordinator")
    other_headers = auth_headers(other_coordinator)

    denied = client.patch(f"/api/cca/mdt/cases/{case_id}/schedule", headers=other_headers, json={"board_date": "2026-09-10"})
    assert denied.status_code == 404

    queue = client.get("/api/cca/mdt/referral-queue", headers=other_headers).json()["queue"]
    assert all(c["id"] != case_id for c in queue)
