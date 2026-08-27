"""
Tests for the Care Plan status lifecycle (backend/app/routers/cca.py's
update_care_plan_status), added to close a real gap: CarePlan.status was previously
write-once ("ACTIVE" at creation, never changed by any endpoint), so the architecture
doc's BLOCKED/ON_HOLD/COMPLETED/CANCELLED states could never be represented.

Covers:
  - Legal transitions succeed, version, and audit (CarePlanVersion + DomainEvent).
  - Illegal transitions (e.g. ACTIVE -> ACTIVE, or out of a terminal state) are rejected.
  - A reason is mandatory for every transition.
  - Only an authorized clinician (or Admin) may transition -- Front Desk cannot.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CarePlanVersion, DomainEvent


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@careplanlifecycle.com", role="CCAMedicalOncologist")


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@careplanlifecycle.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def _make_active_care_plan(client, headers, patient_id):
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    care_plan = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan_id], "intent": "Curative"
    }).json()["care_plan"]
    return care_plan["id"]


def test_active_can_transition_to_blocked_with_reason_and_versions(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    care_plan_id = _make_active_care_plan(client, headers, patient_id)

    res = client.post(f"/api/cca/care-plans/{care_plan_id}/status", headers=headers, json={
        "status": "BLOCKED", "reason": "Awaiting cardiology clearance before Cycle 1."
    })
    assert res.status_code == 200
    body = res.json()["care_plan"]
    assert body["status"] == "BLOCKED"
    assert body["version_no"] == 2

    versions = db_session.query(CarePlanVersion).filter(CarePlanVersion.care_plan_id == care_plan_id).order_by(CarePlanVersion.version_no).all()
    assert len(versions) == 2
    assert versions[-1].change_reason == "Awaiting cardiology clearance before Cycle 1."
    assert versions[-1].snapshot["previous_status"] == "ACTIVE"
    assert versions[-1].snapshot["status"] == "BLOCKED"

    event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "CARE_PLAN_STATUS_CHANGED").first()
    assert event is not None
    assert event.payload["new_status"] == "BLOCKED"


def test_status_transition_requires_reason(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    care_plan_id = _make_active_care_plan(client, headers, patient_id)

    res = client.post(f"/api/cca/care-plans/{care_plan_id}/status", headers=headers, json={"status": "ON_HOLD"})
    assert res.status_code == 422


def test_illegal_transition_is_rejected(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    care_plan_id = _make_active_care_plan(client, headers, patient_id)

    # ACTIVE -> ACTIVE is not a real transition.
    res = client.post(f"/api/cca/care-plans/{care_plan_id}/status", headers=headers, json={
        "status": "ACTIVE", "reason": "no-op"
    })
    assert res.status_code == 409

    # Move to a terminal state, then confirm nothing transitions out of it.
    completed = client.post(f"/api/cca/care-plans/{care_plan_id}/status", headers=headers, json={
        "status": "COMPLETED", "reason": "Planned course completed."
    })
    assert completed.status_code == 200

    reopen = client.post(f"/api/cca/care-plans/{care_plan_id}/status", headers=headers, json={
        "status": "ACTIVE", "reason": "attempt to reopen"
    })
    assert reopen.status_code == 409


def test_on_hold_can_resume_to_active(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    care_plan_id = _make_active_care_plan(client, headers, patient_id)

    held = client.post(f"/api/cca/care-plans/{care_plan_id}/status", headers=headers, json={
        "status": "ON_HOLD", "reason": "Patient hospitalized for unrelated illness."
    })
    assert held.status_code == 200

    resumed = client.post(f"/api/cca/care-plans/{care_plan_id}/status", headers=headers, json={
        "status": "ACTIVE", "reason": "Patient discharged and cleared to resume."
    })
    assert resumed.status_code == 200
    assert resumed.json()["care_plan"]["status"] == "ACTIVE"


def test_front_desk_cannot_transition_care_plan_status(client, auth_headers, db_session, oncologist, front_desk):
    headers = auth_headers(oncologist)
    fd_headers = auth_headers(front_desk)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    care_plan_id = _make_active_care_plan(client, headers, patient_id)

    res = client.post(f"/api/cca/care-plans/{care_plan_id}/status", headers=fd_headers, json={
        "status": "BLOCKED", "reason": "should not be permitted"
    })
    assert res.status_code == 403
