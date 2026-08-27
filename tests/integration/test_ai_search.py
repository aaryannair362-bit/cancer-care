"""
Tests for AI Search (backend/app/routers/cca.py's search_patient_evidence /
propose_task_from_search) -- deterministic, source-cited retrieval over a patient's own
verified data, and the explicit-confirmation-only path to turning a result into a task.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CarePlanTask, DomainEvent


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@searchhosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@searchhosp.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def test_search_finds_a_treatment_plan_with_source_metadata(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "protocol_name": "Dose-dense AC-T"
    }).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})

    res = client.get(f"/api/cca/patients/{patient_id}/search?q=AC-T", headers=headers)
    assert res.status_code == 200
    hits = [r for r in res.json()["results"] if r["type"] == "TreatmentPlan" and r["id"] == plan_id]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["source_date"] is not None
    assert hit["source_author"] == oncologist.email
    assert hit["view_source"] == f"/api/cca/treatment-plans/{plan_id}"


def test_search_finds_an_mdt_recommendation(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    case_id = client.post("/api/cca/mdt/cases", headers=headers, json={
        "patient_id": patient_id, "question": "Sequencing?"
    }).json()["mdt_case"]["id"]
    client.post(f"/api/cca/mdt/cases/{case_id}/recommendation", headers=headers, json={
        "recommendation": "Dose-dense doxorubicin-cyclophosphamide recommended."
    })

    res = client.get(f"/api/cca/patients/{patient_id}/search?q=doxorubicin", headers=headers)
    assert any(r["type"] == "MDTDecision" for r in res.json()["results"])


def test_search_rejects_short_queries_and_non_clinical_roles(client, auth_headers, db_session, oncologist, front_desk):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    headers = auth_headers(oncologist)

    too_short = client.get(f"/api/cca/patients/{patient_id}/search?q=a", headers=headers)
    assert too_short.status_code == 422

    denied = client.get(f"/api/cca/patients/{patient_id}/search?q=chemotherapy", headers=auth_headers(front_desk))
    assert denied.status_code == 403


def test_propose_task_requires_explicit_clinician_confirmation(client, auth_headers, db_session, oncologist, front_desk):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    headers = auth_headers(oncologist)

    denied = client.post(f"/api/cca/patients/{patient_id}/search/propose-task", headers=auth_headers(front_desk), json={
        "description": "Should not be allowed."
    })
    assert denied.status_code == 403

    missing_description = client.post(f"/api/cca/patients/{patient_id}/search/propose-task", headers=headers, json={})
    assert missing_description.status_code == 422

    res = client.post(f"/api/cca/patients/{patient_id}/search/propose-task", headers=headers, json={
        "description": "Follow up on renal function trend before next cycle.",
        "source_reference": {"type": "ClinicalFact", "id": 1},
    })
    assert res.status_code == 200
    task_data = res.json()["task"]
    assert task_data["source"] == "AI_SEARCH_PROPOSED"

    task = db_session.query(CarePlanTask).filter(CarePlanTask.id == task_data["id"]).first()
    assert task.source == "AI_SEARCH_PROPOSED"
    assert task.care_plan_id is None

    event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "AI_SEARCH_TASK_PROPOSED").first()
    assert event is not None
    assert event.payload["task_id"] == task.id
