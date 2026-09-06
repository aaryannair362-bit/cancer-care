"""
Integration tests for the Day Care / Infusion Nurse treatment-day workspace
(Gap Analysis PDF, 30 Aug 2026): queue, pre-treatment safety check, vascular
access, pharmacy readiness, per-medication administration, monitoring,
hold/resume, reaction, extravasation, and completion.

These hit the real /api/cca endpoints end-to-end (no mocking), the same style as
test_cca_api_workflow.py's `_create_and_sign_treatment_plan` helper, which this
file reuses the shape of to get from a bare CCAPatient to a SIGNED Treatment
Order -- driving the oncologist's own (unmodified) API, never the nurse's code
under test, to build that fixture state.
"""
from datetime import date

import pytest

from app.models_cca import CCAPatient, InfusionMedicationAdministration


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@infusion-workspace-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def nurse(make_user, oncologist):
    return make_user(email="nurse@infusion-workspace-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@infusion-workspace-test.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


@pytest.fixture
def front_desk_headers(auth_headers, front_desk):
    return auth_headers(front_desk)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(
        mrn="INFUSION-WS-0001", name="Infusion Workspace Test Patient", age=58, sex="Female",
        organization_id=oncologist.organization_id, journey_state="On Treatment",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_signed_order(client, onc_headers, patient_id):
    """Drives the oncologist's own unmodified /api/cca endpoints to reach a
    SIGNED Treatment Order -- signing the plan also auto-creates the first
    PLANNED TreatmentSession with planned_on=today (routers/cca.py sign_treatment_plan),
    which is what makes it show up in the nurse's /treatment/queue for today."""
    draft = client.post("/api/cca/treatment-plans", headers=onc_headers, json={"patient_id": patient_id})
    assert draft.status_code == 200, draft.text
    plan_id = draft.json()["treatment_plan"]["id"]

    signed_plan = client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})
    assert signed_plan.status_code == 200, signed_plan.text

    order_draft = client.post("/api/cca/treatment-orders", headers=onc_headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id,
        "instructions": {"text": "Doxorubicin 60mg/m2 IV + Cyclophosphamide 600mg/m2 IV, day 1"},
    })
    assert order_draft.status_code == 200, order_draft.text
    order_id = order_draft.json()["treatment_order"]["id"]

    order_signed = client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=onc_headers, json={})
    assert order_signed.status_code == 200, order_signed.text
    return order_id


def test_queue_lists_todays_session_and_arrival_updates(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    res = client.get(f"/api/cca/treatment/queue?date={date.today().isoformat()}", headers=nurse_headers)
    assert res.status_code == 200, res.text
    rows = res.json()["results"]
    row = next(r for r in rows if r["patient_id"] == patient.id)
    assert row["order_id"] == order_id
    assert row["order_status"] == "SIGNED"
    assert row["arrival_status"] == "Scheduled"

    arrival = client.patch(
        f"/api/cca/treatment/queue/{row['session_id']}/arrival", headers=nurse_headers,
        json={"arrival_status": "Arrived", "chair_bed": "Chair 4", "expected_duration_minutes": 240},
    )
    assert arrival.status_code == 200, arrival.text
    assert arrival.json()["arrival_status"] == "Arrived"
    assert arrival.json()["chair_bed"] == "Chair 4"
    assert arrival.json()["arrived_at"] is not None


def test_safety_check_upsert_updates_in_place(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    first = client.post("/api/cca/treatment/safety-check", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id,
        "identity_verified": True, "identity_method": "Name + MRN",
        "order_cycle_confirmed": True, "allergy_review_done": True,
        "allergy_review_notes": "No known drug allergies reported by patient today.",
        "labs_reviewed": True, "labs_review_notes": "Reviewed in lab system per current placeholder.",
    })
    assert first.status_code == 200, first.text
    check_id = first.json()["safety_check"]["id"]

    second = client.post("/api/cca/treatment/safety-check", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "symptom_review_notes": "Mild fatigue, no fever.",
    })
    assert second.status_code == 200, second.text
    assert second.json()["safety_check"]["id"] == check_id  # upsert, not a second row
    assert second.json()["safety_check"]["identity_verified"] is True  # earlier fields preserved
    assert second.json()["safety_check"]["symptom_review_notes"] == "Mild fatigue, no fever."

    fetched = client.get(f"/api/cca/treatment/{order_id}/safety-check?patient_id={patient.id}", headers=nurse_headers)
    assert fetched.status_code == 200
    assert fetched.json()["safety_check"]["id"] == check_id


def test_vascular_access_requires_device_type(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    missing = client.post("/api/cca/treatment/vascular-access", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id,
    })
    assert missing.status_code == 422

    ok = client.post("/api/cca/treatment/vascular-access", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "device_type": "Peripheral IV",
        "site": "Left forearm", "gauge": "20G", "patency_confirmed": True, "access_ready": True,
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["vascular_access"]["access_ready"] is True

    listing = client.get(f"/api/cca/treatment/{order_id}/vascular-access?patient_id={patient.id}", headers=nurse_headers)
    assert listing.status_code == 200
    assert len(listing.json()["results"]) == 1


def test_pharmacy_readiness_progresses_and_captures_receipt(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    bad_status = client.post("/api/cca/treatment/pharmacy-readiness", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "status": "NotARealStatus",
    })
    assert bad_status.status_code == 422

    ready = client.post("/api/cca/treatment/pharmacy-readiness", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "status": "Ready",
    })
    assert ready.status_code == 200, ready.text
    row_id = ready.json()["pharmacy_readiness"]["id"]

    received = client.post("/api/cca/treatment/pharmacy-readiness", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "status": "Received",
        "product_verified": True, "expiry_checked": True, "second_checker_name": "Nurse B. Rao",
    })
    assert received.status_code == 200, received.text
    data = received.json()["pharmacy_readiness"]
    assert data["id"] == row_id  # same row updated in place, not a new one
    assert data["status"] == "Received"
    assert data["product_verified"] is True
    assert data["received_at"] is not None


def test_medication_administration_lifecycle_and_illegal_transitions(client, nurse_headers, onc_headers, db_session, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    added = client.post("/api/cca/treatment/medications", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "medication_name": "Doxorubicin",
        "category": "Antineoplastic", "dose": "60mg/m2", "route": "IV", "sequence_no": 1,
    })
    assert added.status_code == 200, added.text
    admin_id = added.json()["medication"]["id"]
    assert added.json()["medication"]["status"] == "Pending"

    verified = client.post(f"/api/cca/treatment/medications/{admin_id}/verify", headers=nurse_headers, json={
        "product_label_verified": True, "expiry_integrity_checked": True, "second_verifier_name": "Nurse C. Iyer",
    })
    assert verified.status_code == 200, verified.text
    assert verified.json()["medication"]["product_label_verified"] is True

    # Cannot complete straight from Pending -- must start first.
    illegal = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "COMPLETE"})
    assert illegal.status_code == 409

    start = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert start.status_code == 200, start.text
    assert start.json()["medication"]["status"] == "InProgress"
    assert start.json()["medication"]["start_time"] is not None

    pause = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "PAUSE"})
    assert pause.status_code == 200
    assert pause.json()["medication"]["status"] == "Paused"

    resume = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "RESUME"})
    assert resume.status_code == 200
    assert resume.json()["medication"]["status"] == "InProgress"

    complete = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "COMPLETE", "actual_rate": "as ordered", "actual_volume": "250mL",
    })
    assert complete.status_code == 200, complete.text
    assert complete.json()["medication"]["status"] == "Completed"
    assert complete.json()["medication"]["administered_by"] == "nurse@infusion-workspace-test.com"

    events = db_session.query(InfusionMedicationAdministration).filter(
        InfusionMedicationAdministration.id == admin_id
    ).first()
    assert events.status == "Completed"

    # A second medication line, omitted with a documented reason.
    second = client.post("/api/cca/treatment/medications", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "medication_name": "Cyclophosphamide",
        "category": "Antineoplastic", "dose": "600mg/m2", "route": "IV", "sequence_no": 2,
    })
    second_id = second.json()["medication"]["id"]

    omit_without_reason = client.post(f"/api/cca/treatment/medications/{second_id}/event", headers=nurse_headers, json={"event_type": "OMIT"})
    assert omit_without_reason.status_code == 422

    omit = client.post(f"/api/cca/treatment/medications/{second_id}/event", headers=nurse_headers, json={
        "event_type": "OMIT", "notes": "Held on oncologist verbal instruction pending repeat counts.",
    })
    assert omit.status_code == 200, omit.text
    assert omit.json()["medication"]["status"] == "Omitted"
    assert omit.json()["medication"]["omission_reason"]


def test_monitoring_observation_baseline_and_during(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    baseline = client.post("/api/cca/treatment/monitoring", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "phase": "Baseline",
        "vitals": {"temp": "36.8", "bp": "118/76", "pulse": "82", "spo2": "98%"},
        "symptoms": "No acute symptoms.",
    })
    assert baseline.status_code == 200, baseline.text

    during = client.post("/api/cca/treatment/monitoring", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "phase": "During",
        "vitals": {"bp": "120/78"}, "symptoms": "Tolerating infusion, no new complaints.",
    })
    assert during.status_code == 200

    listing = client.get(f"/api/cca/treatment/{order_id}/monitoring?patient_id={patient.id}", headers=nurse_headers)
    assert listing.status_code == 200
    phases = [o["phase"] for o in listing.json()["results"]]
    assert phases == ["Baseline", "During"]


def test_hold_and_resume(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    hold = client.post("/api/cca/treatment/hold", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "reason": "Patient reports dizziness, holding infusion for review.",
        "hold_type": "Safety Hold", "escalated_to": "Dr. Menon, on-call oncologist",
    })
    assert hold.status_code == 200, hold.text
    hold_id = hold.json()["hold"]["id"]
    assert hold.json()["hold"]["resumed"] is False

    double_resume_guard = client.post(f"/api/cca/treatment/hold/{hold_id}/resume", headers=nurse_headers, json={"resume_notes": "Cleared by Dr. Menon to resume."})
    assert double_resume_guard.status_code == 200, double_resume_guard.text
    assert double_resume_guard.json()["hold"]["resumed"] is True

    already_resumed = client.post(f"/api/cca/treatment/hold/{hold_id}/resume", headers=nurse_headers, json={})
    assert already_resumed.status_code == 409


def test_reaction_and_extravasation_are_separate_records(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    reaction = client.post("/api/cca/treatment/reaction", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "medication_running": "Doxorubicin",
        "symptoms": "Facial flushing and mild dyspnea.", "infusion_action": "Paused",
        "informed_person": "Dr. Menon", "interventions": "IV fluids per standing order, O2 by nasal cannula.",
        "patient_response": "Symptoms resolving.", "physician_disposition": "Restart", "directed_by": "Dr. Menon (phone)",
    })
    assert reaction.status_code == 200, reaction.text

    missing_symptoms = client.post("/api/cca/treatment/reaction", headers=nurse_headers, json={"patient_id": patient.id})
    assert missing_symptoms.status_code == 422

    extravasation = client.post("/api/cca/treatment/extravasation", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "agent": "Doxorubicin", "site": "Right hand",
        "symptoms": "Swelling and pain at infusion site.", "line_status": "Stopped, cannula left in situ",
        "immediate_actions": "Infusion stopped, aspirated residual drug, elevated limb.",
    })
    assert extravasation.status_code == 200, extravasation.text

    reactions = client.get(f"/api/cca/treatment/{order_id}/reactions?patient_id={patient.id}", headers=nurse_headers)
    assert len(reactions.json()["results"]) == 1
    extravasations = client.get(f"/api/cca/treatment/{order_id}/extravasations?patient_id={patient.id}", headers=nurse_headers)
    assert len(extravasations.json()["results"]) == 1


def test_completion_gated_on_every_medication_having_a_final_status(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    no_meds_yet = client.post("/api/cca/treatment/completion", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id,
    })
    assert no_meds_yet.status_code == 422

    added = client.post("/api/cca/treatment/medications", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "medication_name": "Doxorubicin", "sequence_no": 1,
    })
    admin_id = added.json()["medication"]["id"]

    still_pending = client.post("/api/cca/treatment/completion", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id,
    })
    assert still_pending.status_code == 422
    assert "Doxorubicin" in still_pending.json()["detail"]

    client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "COMPLETE"})

    done = client.post("/api/cca/treatment/completion", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "disposition": "Stable, tolerated infusion well.",
        "access_status": "Removed", "red_flags_given": True, "next_treatment_date": "2026-09-20",
        "next_labs_required": "CBC, renal/liver function before next cycle.",
    })
    assert done.status_code == 200, done.text
    assert done.json()["completion"]["disposition"] == "Stable, tolerated infusion well."

    already_locked = client.post("/api/cca/treatment/completion", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id,
    })
    assert already_locked.status_code == 409


def test_front_desk_cannot_write_to_the_infusion_workspace(client, front_desk_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    denied = client.post("/api/cca/treatment/vascular-access", headers=front_desk_headers, json={
        "patient_id": patient.id, "order_id": order_id, "device_type": "Peripheral IV",
    })
    assert denied.status_code == 403


def test_cross_org_patient_is_not_found(client, nurse_headers, onc_headers, patient, make_user, auth_headers):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    other_org_nurse = make_user(email="other-org-nurse@infusion-workspace-test.com", role="CCAInfusionNurse")
    other_headers = auth_headers(other_org_nurse)

    res = client.get(f"/api/cca/treatment/{order_id}/vascular-access?patient_id={patient.id}", headers=other_headers)
    assert res.status_code == 404
