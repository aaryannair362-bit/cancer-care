"""
Tests for the P2 analytics endpoint (backend/app/routers/cca_coordination.py's
treatment_analytics) -- built on top of the domain event log (DomainEvent) the P0
event-dispatch slice introduced.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@analyticshosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def admin(make_user, oncologist):
    return make_user(email="admin@analyticshosp.com", role="Admin", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def test_analytics_is_admin_only(client, auth_headers, oncologist):
    res = client.get("/api/cca/admin/analytics/treatment-metrics", headers=auth_headers(oncologist))
    assert res.status_code == 403


def test_analytics_reflects_a_full_treatment_flow(client, auth_headers, db_session, oncologist, admin):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "planned_sessions": 2
    }).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    client.post("/api/cca/care-plans", headers=headers, json={"patient_id": patient_id, "treatment_plan_ids": [plan_id]})

    order_id = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)
    client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "CLEARED", "reason": "Labs normal."
    })

    res = client.get("/api/cca/admin/analytics/treatment-metrics", headers=auth_headers(admin))
    assert res.status_code == 200
    data = res.json()

    assert data["sample_sizes"]["plan_draft_to_sign"] >= 1
    assert data["avg_days_plan_draft_to_sign"] is not None
    assert data["sample_sizes"]["plan_activation_to_treatment_start"] >= 1
    assert data["avg_days_plan_activation_to_treatment_start"] is not None
    assert data["avg_days_plan_activation_to_treatment_start"] >= 0
    # 1 of 2 planned sessions completed.
    assert data["sessions_completion_rate"] == 0.5


def test_analytics_counts_revisions_holds_and_no_shows(client, auth_headers, db_session, oncologist, admin, make_user):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    plan_a_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_a_id}/sign", headers=headers, json={})
    plan_b_id = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "supersedes_id": plan_a_id
    }).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_b_id}/sign", headers=headers, json={})

    order_id = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_b_id
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)
    client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "HELD", "reason": "Grade 3 neutropenia."
    })

    patient_liaison = make_user(email="liaison@analyticshosp.com", role="CCAPatientLiaison", organization_id=oncologist.organization_id)
    front_desk = make_user(email="frontdesk@analyticshosp.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)
    liaison_headers = auth_headers(patient_liaison)
    case_id = client.post("/api/cca/coordination/cases", headers=liaison_headers, json={"patient_id": patient_id}).json()["case"]["id"]
    client.post(f"/api/cca/coordination/cases/{case_id}/no-show", headers=auth_headers(front_desk), json={})

    res = client.get("/api/cca/admin/analytics/treatment-metrics", headers=auth_headers(admin))
    data = res.json()
    assert data["treatment_plan_revisions"] >= 1
    assert data["treatment_holds"] >= 1
    assert data["patient_no_shows"] >= 1
