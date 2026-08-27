"""
Tests for the ratified decision in backend/app/cca_product_decisions.py:
CARE_PLAN_IN_PROGRESS_STATUSES -- a Care Plan is one longitudinal object per patient, not one
per episode. backend/app/routers/cca.py's create_care_plan enforces this: a second Care Plan
cannot be created while an existing one is still ACTIVE/BLOCKED/ON_HOLD, but a new one may be
created once the prior one reaches a terminal state (COMPLETED/CANCELLED).
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@careplanlongitudinal.com", role="CCAMedicalOncologist")


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def _sign_treatment_plan(client, headers, patient_id):
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    return plan_id


def test_second_care_plan_rejected_while_first_is_in_progress(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _sign_treatment_plan(client, headers, patient_id)

    first = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan_id], "intent": "Curative"
    })
    assert first.status_code == 200

    plan_id_2 = _sign_treatment_plan(client, headers, patient_id)
    second = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan_id_2], "intent": "Curative"
    })
    assert second.status_code == 409
    assert "longitudinal" in second.json()["detail"].lower()


def test_new_care_plan_allowed_after_prior_one_completed(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _sign_treatment_plan(client, headers, patient_id)

    first = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan_id], "intent": "Curative"
    }).json()["care_plan"]
    completed = client.post(f"/api/cca/care-plans/{first['id']}/status", headers=headers, json={
        "status": "COMPLETED", "reason": "Planned course completed."
    })
    assert completed.status_code == 200

    plan_id_2 = _sign_treatment_plan(client, headers, patient_id)
    second = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan_id_2], "intent": "Curative"
    })
    assert second.status_code == 200
    assert second.json()["care_plan"]["id"] != first["id"]


def test_new_care_plan_allowed_after_prior_one_cancelled(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _sign_treatment_plan(client, headers, patient_id)

    first = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan_id], "intent": "Curative"
    }).json()["care_plan"]
    client.post(f"/api/cca/care-plans/{first['id']}/status", headers=headers, json={
        "status": "CANCELLED", "reason": "Plan no longer applicable."
    })

    plan_id_2 = _sign_treatment_plan(client, headers, patient_id)
    second = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan_id_2], "intent": "Curative"
    })
    assert second.status_code == 200
