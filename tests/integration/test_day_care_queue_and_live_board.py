"""
Worklist/dashboard follow-up round: Batch 3 (Day Care/MAR, C.12) had a Day Care queue
endpoint already (GET /treatment/queue) but it was missing the gate-status strip (reference
SCR-MAR-001 -- Identity/Consent/Clearance/Product/Access/Verification, the actual
safety-relevant part of that screen: what's blocking each patient from starting treatment,
visible unit-wide at a glance). There was also no Live Infusion Board at all (SCR-MAR-013).

Every gate/board field is read straight off an already-recorded row or is plain elapsed-time
arithmetic -- never a computed dose/threshold/clinical judgment.
"""
from datetime import date

import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@daycare-board-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def nurse(make_user, oncologist):
    return make_user(email="nurse@daycare-board-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def nurse_b(make_user, oncologist):
    return make_user(email="nurseb@daycare-board-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


@pytest.fixture
def nurse_b_headers(auth_headers, nurse_b):
    return auth_headers(nurse_b)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(
        mrn="DAYCARE-BOARD-0001", name="Day Care Board Test Patient", age=60, sex="Male",
        organization_id=oncologist.organization_id, journey_state="On Treatment",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_signed_order(client, onc_headers, patient_id):
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})
    order_id = client.post("/api/cca/treatment-orders", headers=onc_headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id,
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=onc_headers, json={})
    return order_id


def test_queue_gate_strip_reflects_recorded_state(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    before = client.get(f"/api/cca/treatment/queue?date={date.today().isoformat()}", headers=nurse_headers)
    row = next(r for r in before.json()["results"] if r["patient_id"] == patient.id)
    assert row["gates"]["identity"]["status"] == "grey"
    assert row["gates"]["consent"]["status"] == "red"  # no consent captured yet
    assert row["gates"]["clearance"]["status"] == "grey"

    consent = client.post(f"/api/cca/patients/{patient.id}/consents", headers=onc_headers, json={
        "consent_types": ["treatment"], "signatory": patient.name, "signatory_reason": "Self (Patient)",
    })
    assert consent.status_code == 201, consent.text

    safety_check = client.post("/api/cca/treatment/safety-check", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "identity_verified": True, "identity_method": "Wristband + verbal",
        "name_matched": True, "mrn_matched": True, "order_cycle_confirmed": True,
    })
    assert safety_check.status_code == 200, safety_check.text

    access = client.post("/api/cca/treatment/vascular-access", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "device_type": "Peripheral IV", "access_ready": True,
    })
    assert access.status_code == 200, access.text

    after = client.get(f"/api/cca/treatment/queue?date={date.today().isoformat()}", headers=nurse_headers)
    row_after = next(r for r in after.json()["results"] if r["patient_id"] == patient.id)
    assert row_after["gates"]["identity"]["status"] == "green"
    assert row_after["gates"]["access"]["status"] == "green"
    assert row_after["gates"]["consent"]["status"] == "green"


def test_live_board_shows_in_progress_infusion_with_elapsed_time(client, nurse_headers, nurse_b_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    added = client.post("/api/cca/treatment/medications", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "medication_name": "Doxorubicin",
        "category": "Antineoplastic", "sequence_no": 1,
    }).json()["medication"]
    admin_id = added["id"]
    client.post(f"/api/cca/treatment/medications/{admin_id}/independent-verification", headers=nurse_b_headers, json={
        "checklist": {k: True for k in ["drug", "dose", "volume_diluent", "route", "rate", "expiry", "physical_integrity", "sequence", "pump_settings"]},
    })

    empty_board = client.get("/api/cca/treatment/live-board", headers=nurse_headers)
    assert empty_board.status_code == 200
    assert not any(r["administration_id"] == admin_id for r in empty_board.json()["results"])

    client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})

    board = client.get("/api/cca/treatment/live-board", headers=nurse_headers)
    assert board.status_code == 200
    row = next(r for r in board.json()["results"] if r["administration_id"] == admin_id)
    assert row["status"] == "InProgress"
    assert row["mrn"] == patient.mrn
    assert row["elapsed_minutes"] is not None
    assert row["observation_overdue"] is False
    assert row["reaction_active"] is False
    assert row["interruption_active"] is False

    reaction = client.post("/api/cca/treatment/reaction", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "administration_id": admin_id, "symptoms": "Flushing.",
    })
    assert reaction.status_code == 200, reaction.text

    board_after = client.get("/api/cca/treatment/live-board", headers=nurse_headers)
    row_after = next(r for r in board_after.json()["results"] if r["administration_id"] == admin_id)
    assert row_after["reaction_active"] is True

    complete = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "COMPLETE", "completion_status": "Administered", "reaction_occurred": True,
    })
    assert complete.status_code == 200, complete.text
    board_final = client.get("/api/cca/treatment/live-board", headers=nurse_headers)
    assert not any(r["administration_id"] == admin_id for r in board_final.json()["results"])
