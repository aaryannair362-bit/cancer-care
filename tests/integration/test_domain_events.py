"""
Integration tests proving the domain event bus (backend/app/events.py,
backend/app/event_subscribers.py) is actually wired into the live Treatment Plan / Care
Plan / Treatment Order / Treatment Clearance / MDT endpoints -- i.e. that a real HTTP call
produces a durable DomainEvent row and triggers the concrete subscriber side effect the
architecture doc's events table names, not just that the pure bus mechanics work in
isolation (see tests/unit/test_events.py for that).
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, DomainEvent, CarePlanTask, TreatmentPlan, TreatmentSession


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@eventshosp.com", role="CCAMedicalOncologist")


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def test_signing_a_treatment_plan_publishes_a_durable_event_and_updates_journey_state(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]

    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})

    event = db_session.query(DomainEvent).filter(
        DomainEvent.event_type == "TREATMENT_PLAN_SIGNED", DomainEvent.patient_id == patient_id
    ).first()
    assert event is not None
    assert event.payload["treatment_plan_id"] == plan_id

    patient = db_session.query(CCAPatient).filter(CCAPatient.id == patient_id).first()
    assert patient.journey_state == "TreatmentPlanSigned"


def test_signing_a_revision_publishes_treatment_plan_revised_via_the_subscriber(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_a_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_a_id}/sign", headers=headers, json={})
    plan_b_id = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "supersedes_id": plan_a_id
    }).json()["treatment_plan"]["id"]

    client.post(f"/api/cca/treatment-plans/{plan_b_id}/sign", headers=headers, json={})

    revised_event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "TREATMENT_PLAN_REVISED").first()
    assert revised_event is not None
    assert revised_event.payload["prior_treatment_plan_id"] == plan_a_id
    assert revised_event.payload["new_treatment_plan_id"] == plan_b_id
    assert db_session.query(TreatmentPlan).filter(TreatmentPlan.id == plan_a_id).first().status == "SUPERSEDED"


def test_care_plan_activation_publishes_event_and_sets_journey_state(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})

    client.post("/api/cca/care-plans", headers=headers, json={"patient_id": patient_id, "treatment_plan_ids": [plan_id]})

    event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "CARE_PLAN_ACTIVATED").first()
    assert event is not None
    assert event.payload["treatment_plan_ids"] == [plan_id]
    assert db_session.query(CCAPatient).filter(CCAPatient.id == patient_id).first().journey_state == "PlanApproved"


def test_mdt_recommendation_finalized_publishes_event_and_updates_journey_state(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    case_id = client.post("/api/cca/mdt/cases", headers=headers, json={
        "patient_id": patient_id, "question": "Sequencing?"
    }).json()["mdt_case"]["id"]

    client.post(f"/api/cca/mdt/cases/{case_id}/recommendation", headers=headers, json={"recommendation": "AC-T recommended."})

    event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "MDT_RECOMMENDATION_FINALIZED").first()
    assert event is not None
    assert event.payload["mdt_case_id"] == case_id
    assert db_session.query(CCAPatient).filter(CCAPatient.id == patient_id).first().journey_state == "MDTRecommendationFinalized"


def test_treatment_administered_event_advances_sessions_via_subscriber(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "planned_sessions": 3
    }).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    order_id = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)

    client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "CLEARED", "reason": "Labs normal."
    })

    event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "TREATMENT_ADMINISTERED").first()
    assert event is not None
    assert event.payload["treatment_order_id"] == order_id

    plan = db_session.query(TreatmentPlan).filter(TreatmentPlan.id == plan_id).first()
    assert plan.completed_sessions == 1
    sessions = db_session.query(TreatmentSession).filter(TreatmentSession.treatment_plan_id == plan_id).order_by(TreatmentSession.session_no).all()
    assert [s.session_no for s in sessions] == [1, 2]


def test_treatment_held_event_opens_a_reassessment_task_via_subscriber(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    order_id = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)
    client.post("/api/cca/care-plans", headers=headers, json={"patient_id": patient_id, "treatment_plan_ids": [plan_id]})

    client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "HELD", "reason": "Grade 3 neutropenia."
    })

    event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "TREATMENT_HELD").first()
    assert event is not None
    task = db_session.query(CarePlanTask).filter(CarePlanTask.patient_id == patient_id).first()
    assert task is not None
    assert "Grade 3 neutropenia" in task.description
