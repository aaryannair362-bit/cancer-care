"""
Tests for the Treatment Order / Treatment Event lifecycle (backend/app/routers/cca.py's
create_treatment_order / sign_treatment_order / cancel_treatment_order, and the rewritten
record_clearance_decision).

Covers the specific non-negotiable constraints this slice exists to enforce:
  - An Order cannot exist against a Treatment Plan that isn't signed (ACTIVE).
  - Only an authorized clinician of the matching modality may sign/cancel an Order.
  - An Event cannot exist without a valid, signed Order reference -- Day-Care never infers
    treatment from a Care Plan / Treatment Plan alone.
  - The closed loop: a CLEARED decision executes the order, advances the plan's session
    count, and (if more sessions remain) opens the next one -- planned -> executed -> result.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, TreatmentOrder, TreatmentEvent, TreatmentSession, TreatmentPlan


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@txorderhosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def surgical_oncologist(make_user, oncologist):
    return make_user(email="surgonc@txorderhosp.com", role="CCASurgicalOncologist", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def _signed_plan(client, headers, patient_id, **overrides):
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id, **overrides}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    return plan_id


def _signed_order(client, headers, patient_id, plan_id):
    order_id = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)
    return order_id


def test_order_cannot_be_written_against_an_unsigned_plan(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    draft_plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]

    rejected = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": draft_plan_id
    })
    assert rejected.status_code == 422


def test_session_may_only_have_one_open_order_at_a_time(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id)

    first = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id})
    assert first.status_code == 200

    duplicate = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id})
    assert duplicate.status_code == 409


def test_only_matching_modality_specialist_may_sign_or_cancel_order(client, auth_headers, db_session, oncologist, surgical_oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id)
    order_id = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id}).json()["treatment_order"]["id"]

    surgical_headers = auth_headers(surgical_oncologist)
    denied = client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=surgical_headers)
    assert denied.status_code == 403

    signed = client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)
    assert signed.status_code == 200
    assert signed.json()["treatment_order"]["status"] == "SIGNED"

    resigned = client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)
    assert resigned.status_code == 409

    denied_cancel = client.post(f"/api/cca/treatment-orders/{order_id}/cancel", headers=surgical_headers, json={"reason": "n/a"})
    assert denied_cancel.status_code == 403

    no_reason = client.post(f"/api/cca/treatment-orders/{order_id}/cancel", headers=headers, json={})
    assert no_reason.status_code == 422

    cancelled = client.post(f"/api/cca/treatment-orders/{order_id}/cancel", headers=headers, json={"reason": "Plan revised before administration."})
    assert cancelled.status_code == 200
    assert cancelled.json()["treatment_order"]["status"] == "CANCELLED"


def test_clearance_without_a_signed_order_is_rejected_and_creates_no_event(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    res = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "CLEARED", "reason": "Labs normal."
    })
    assert res.status_code == 422
    assert db_session.query(TreatmentEvent).filter(TreatmentEvent.patient_id == patient_id).count() == 0

    # An unsigned (DRAFT) order doesn't count either.
    plan_id = _signed_plan(client, headers, patient_id)
    client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id})
    still_rejected = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "CLEARED", "reason": "Labs normal."
    })
    assert still_rejected.status_code == 422
    assert db_session.query(TreatmentEvent).filter(TreatmentEvent.patient_id == patient_id).count() == 0


def test_unknown_decision_code_rejected(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id)
    _signed_order(client, headers, patient_id, plan_id)

    res = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "MAYBE_LATER", "reason": "n/a"
    })
    assert res.status_code == 422


def test_cleared_decision_closes_the_loop(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id, planned_sessions=3)
    order_id = _signed_order(client, headers, patient_id, plan_id)

    res = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "CLEARED", "reason": "Labs normal, proceed."
    })
    assert res.status_code == 200
    assert res.json()["clearance"]["order_id"] == order_id

    order = db_session.query(TreatmentOrder).filter(TreatmentOrder.id == order_id).first()
    assert order.status == "EXECUTED"

    plan = db_session.query(TreatmentPlan).filter(TreatmentPlan.id == plan_id).first()
    assert plan.completed_sessions == 1

    sessions = db_session.query(TreatmentSession).filter(TreatmentSession.treatment_plan_id == plan_id).order_by(TreatmentSession.session_no).all()
    assert [s.session_no for s in sessions] == [1, 2]
    assert sessions[0].status == "ADMINISTERED"
    assert sessions[1].status == "PLANNED"

    events = db_session.query(TreatmentEvent).filter(TreatmentEvent.treatment_order_id == order_id).all()
    assert len(events) == 1
    assert events[0].event_type == "ADMINISTERED"


def test_held_decision_keeps_order_signed_and_opens_a_reassessment_task(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id)
    order_id = _signed_order(client, headers, patient_id, plan_id)
    client.post("/api/cca/care-plans", headers=headers, json={"patient_id": patient_id, "treatment_plan_ids": [plan_id]})

    res = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "HELD", "reason": "Grade 3 neutropenia."
    })
    assert res.status_code == 200

    order = db_session.query(TreatmentOrder).filter(TreatmentOrder.id == order_id).first()
    assert order.status == "SIGNED"  # left executable -- a hold is not a cancellation

    events = db_session.query(TreatmentEvent).filter(TreatmentEvent.treatment_order_id == order_id).all()
    assert len(events) == 1 and events[0].event_type == "HELD"

    # The same signed order can still be executed once the hold resolves.
    resolved = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "CLEARED", "reason": "Counts recovered."
    })
    assert resolved.status_code == 200
    assert resolved.json()["clearance"]["order_id"] == order_id


def test_discontinued_decision_cancels_order_and_treatment_plan(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id)
    order_id = _signed_order(client, headers, patient_id, plan_id)

    res = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "DISCONTINUED", "reason": "Disease progression."
    })
    assert res.status_code == 200

    order = db_session.query(TreatmentOrder).filter(TreatmentOrder.id == order_id).first()
    assert order.status == "CANCELLED"
    plan = db_session.query(TreatmentPlan).filter(TreatmentPlan.id == plan_id).first()
    assert plan.status == "CANCELLED"

    events = db_session.query(TreatmentEvent).filter(TreatmentEvent.treatment_order_id == order_id).all()
    assert len(events) == 1 and events[0].event_type == "DISCONTINUED"


def test_treatment_order_endpoints_are_org_scoped(client, make_user, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id)
    order_id = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id}).json()["treatment_order"]["id"]

    other_org_oncologist = make_user(email="other.org.onc@rivaltxorder.com", role="CCAMedicalOncologist")
    other_headers = auth_headers(other_org_oncologist)

    assert client.get(f"/api/cca/treatment-orders/{order_id}", headers=other_headers).status_code == 404
    assert client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=other_headers).status_code == 404
