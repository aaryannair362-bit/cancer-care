"""
Tests for the P1 care-coordination event wiring added to backend/app/routers/cca_coordination.py:
barrier escalation (CARE_PLAN_TASK_BLOCKED), no-show recovery (PATIENT_NO_SHOW), and
financial clearance (FINANCIAL_CLEARANCE_UPDATED) -- each publishing a durable DomainEvent
and, where the architecture doc names a consumer, triggering the matching subscriber.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CarePlanTask, DomainEvent


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@coordeventshosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def patient_liaison(make_user, oncologist):
    return make_user(email="liaison@coordeventshosp.com", role="CCAPatientLiaison", organization_id=oncologist.organization_id)


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@coordeventshosp.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture
def financial_counsellor(make_user, oncologist):
    return make_user(email="finance@coordeventshosp.com", role="CCAFinancialCounsellor", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def test_no_show_flow_end_to_end(client, auth_headers, db_session, oncologist, patient_liaison, front_desk):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    liaison_headers = auth_headers(patient_liaison)
    case_id = client.post("/api/cca/coordination/cases", headers=liaison_headers, json={"patient_id": patient_id}).json()["case"]["id"]

    denied = client.post(f"/api/cca/coordination/cases/{case_id}/no-show", headers=auth_headers(oncologist), json={})
    assert denied.status_code == 403

    res = client.post(f"/api/cca/coordination/cases/{case_id}/no-show", headers=auth_headers(front_desk), json={"context": "Missed Cycle 3 appointment."})
    assert res.status_code == 200
    assert res.json()["case"]["barriers"][-1]["type"] == "NoShow"

    event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "PATIENT_NO_SHOW").first()
    assert event is not None
    assert event.payload["coordination_case_id"] == case_id

    task = db_session.query(CarePlanTask).filter(CarePlanTask.patient_id == patient_id).first()
    assert task is not None
    assert "no-show" in task.description.lower()


def test_barrier_escalation_publishes_event_and_creates_task_for_treating_team(client, auth_headers, db_session, oncologist, patient_liaison):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    liaison_headers = auth_headers(patient_liaison)
    case_id = client.post("/api/cca/coordination/cases", headers=liaison_headers, json={"patient_id": patient_id}).json()["case"]["id"]
    client.post(f"/api/cca/coordination/cases/{case_id}/barriers", headers=liaison_headers, json={"type": "Transport", "notes": "No ride to hospital."})

    bad_index = client.patch(f"/api/cca/coordination/cases/{case_id}/barriers/5", headers=liaison_headers, json={"status": "Escalated"})
    assert bad_index.status_code == 404

    bad_status = client.patch(f"/api/cca/coordination/cases/{case_id}/barriers/0", headers=liaison_headers, json={"status": "Whatever"})
    assert bad_status.status_code == 422

    denied = client.patch(f"/api/cca/coordination/cases/{case_id}/barriers/0", headers=auth_headers(oncologist), json={"status": "Escalated"})
    assert denied.status_code == 403

    escalated = client.patch(f"/api/cca/coordination/cases/{case_id}/barriers/0", headers=liaison_headers, json={"status": "Escalated"})
    assert escalated.status_code == 200
    assert escalated.json()["case"]["barriers"][0]["status"] == "Escalated"

    event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "CARE_PLAN_TASK_BLOCKED").first()
    assert event is not None
    task = db_session.query(CarePlanTask).filter(CarePlanTask.patient_id == patient_id).first()
    assert task is not None
    assert "Transport" in task.description

    # Resolving doesn't publish CARE_PLAN_TASK_BLOCKED again.
    resolved = client.patch(f"/api/cca/coordination/cases/{case_id}/barriers/0", headers=liaison_headers, json={"status": "Resolved"})
    assert resolved.status_code == 200
    assert db_session.query(DomainEvent).filter(DomainEvent.event_type == "CARE_PLAN_TASK_BLOCKED").count() == 1


def test_financial_clearance_publishes_a_domain_event(client, auth_headers, db_session, oncologist, financial_counsellor):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    fc_headers = auth_headers(financial_counsellor)
    case_id = client.post("/api/cca/financial/cases", headers=fc_headers, json={"patient_id": patient_id}).json()["case"]["id"]

    res = client.patch(f"/api/cca/financial/cases/{case_id}/clearance", headers=fc_headers, json={"financial_clearance_status": "Cleared"})
    assert res.status_code == 200

    event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "FINANCIAL_CLEARANCE_UPDATED").first()
    assert event is not None
    assert event.payload["financial_clearance_status"] == "Cleared"
    assert event.patient_id == patient_id
