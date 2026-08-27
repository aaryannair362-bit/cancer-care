"""
Tests for the patient task/review queue (backend/app/routers/cca.py's list_patient_tasks /
resolve_patient_task) and the flagged gap fix: a review task can now exist for a patient with
no Care Plan yet (CarePlanTask.care_plan_id is nullable -- see models_cca.py's docstring).
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CarePlanTask


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@taskshosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@taskshosp.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def test_mdt_recommendation_creates_a_task_without_any_care_plan(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    case_id = client.post("/api/cca/mdt/cases", headers=headers, json={
        "patient_id": patient_id, "question": "Sequencing?"
    }).json()["mdt_case"]["id"]
    client.post(f"/api/cca/mdt/cases/{case_id}/recommendation", headers=headers, json={"recommendation": "AC-T recommended."})

    task = db_session.query(CarePlanTask).filter(CarePlanTask.patient_id == patient_id).first()
    assert task is not None
    assert task.care_plan_id is None
    assert "MDT recommendation" in task.description

    listed = client.get(f"/api/cca/patients/{patient_id}/tasks", headers=headers).json()["tasks"]
    assert any(t["id"] == task.id and t["care_plan_id"] is None for t in listed)


def test_treatment_held_creates_a_task_even_without_a_care_plan(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    order_id = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)
    # Deliberately no POST /care-plans here -- the point is this must still work.

    res = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "HELD", "reason": "Grade 3 neutropenia."
    })
    assert res.status_code == 200

    task = db_session.query(CarePlanTask).filter(CarePlanTask.patient_id == patient_id).first()
    assert task is not None
    assert task.care_plan_id is None


def test_resolve_task_requires_clinician_and_is_idempotent_guarded(client, auth_headers, db_session, oncologist, front_desk):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    case_id = client.post("/api/cca/mdt/cases", headers=headers, json={
        "patient_id": patient_id, "question": "Sequencing?"
    }).json()["mdt_case"]["id"]
    client.post(f"/api/cca/mdt/cases/{case_id}/recommendation", headers=headers, json={"recommendation": "AC-T recommended."})
    task_id = db_session.query(CarePlanTask).filter(CarePlanTask.patient_id == patient_id).first().id

    denied = client.post(f"/api/cca/tasks/{task_id}/resolve", headers=auth_headers(front_desk))
    assert denied.status_code == 403

    resolved = client.post(f"/api/cca/tasks/{task_id}/resolve", headers=headers)
    assert resolved.status_code == 200
    assert resolved.json()["task"]["status"] == "RESOLVED"

    already = client.post(f"/api/cca/tasks/{task_id}/resolve", headers=headers)
    assert already.status_code == 409


def test_patient_tasks_are_org_scoped(client, make_user, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    case_id = client.post("/api/cca/mdt/cases", headers=headers, json={
        "patient_id": patient_id, "question": "Sequencing?"
    }).json()["mdt_case"]["id"]
    client.post(f"/api/cca/mdt/cases/{case_id}/recommendation", headers=headers, json={"recommendation": "AC-T recommended."})

    other_org_oncologist = make_user(email="other.org.onc@rivaltaskshosp.com", role="CCAMedicalOncologist")
    assert client.get(f"/api/cca/patients/{patient_id}/tasks", headers=auth_headers(other_org_oncologist)).status_code == 404
