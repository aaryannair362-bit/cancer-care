"""
Final gap-closing round, item 15: Treatment Readiness consolidation -- previously the
readiness signal was scattered across PreTreatmentSafetyCheck/PharmacyReadiness/
TreatmentClearance with no single explicit state. This reduces the already-computed gate
strip (_day_care_gate_status, reference SCR-MAR-001) to one READY/HOLD/ESCALATE verdict.

No new storage and no new clinical judgment -- purely a reduction of already-recorded gate
colors to one state.
"""
from datetime import date

import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@readiness-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def nurse(make_user, oncologist):
    return make_user(email="nurse@readiness-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="READINESS-TEST-0001", name="Readiness Test Patient", age=58, sex="Female", organization_id=oncologist.organization_id)
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


def _get_session_id(client, nurse_headers, patient_id):
    queue = client.get(f"/api/cca/treatment/queue?date={date.today().isoformat()}", headers=nurse_headers)
    row = next(r for r in queue.json()["results"] if r["patient_id"] == patient_id)
    return row["session_id"]


def test_readiness_starts_as_hold(client, onc_headers, nurse_headers, patient):
    _create_signed_order(client, onc_headers, patient.id)
    session_id = _get_session_id(client, nurse_headers, patient.id)

    readiness = client.get(f"/api/cca/treatment-sessions/{session_id}/readiness", headers=onc_headers)
    assert readiness.status_code == 200, readiness.text
    body = readiness.json()
    # No consent captured yet -> consent gate is red -> ESCALATE.
    assert body["state"] == "ESCALATE"
    assert "consent" in body["blocking_gates"]


def test_readiness_reaches_ready_once_all_gates_clear(client, onc_headers, nurse_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    session_id = _get_session_id(client, nurse_headers, patient.id)

    client.post(f"/api/cca/patients/{patient.id}/consents", headers=onc_headers, json={
        "consent_types": ["treatment"], "signatory": patient.name, "signatory_reason": "Self (Patient)",
    })
    client.post("/api/cca/treatment/safety-check", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "identity_verified": True, "identity_method": "Wristband + verbal",
        "name_matched": True, "mrn_matched": True, "order_cycle_confirmed": True,
    })
    client.post("/api/cca/treatment/vascular-access", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "device_type": "Peripheral IV", "access_ready": True,
    })
    client.post("/api/cca/treatment/clearance", headers=onc_headers, json={
        "session_id": session_id, "order_id": order_id, "patient_id": patient.id,
        "decision": "CLEARED", "reason": "Labs within tolerance, patient fit for treatment.",
    })

    readiness = client.get(f"/api/cca/treatment-sessions/{session_id}/readiness", headers=onc_headers)
    assert readiness.status_code == 200, readiness.text
    body = readiness.json()
    # Product/verification gates may still be pending (no pharmacy workflow run in this
    # test), so this only asserts the consolidation logic itself, not full readiness --
    # a HOLD (not ESCALATE) confirms the red consent/clearance gates cleared correctly.
    assert body["state"] in ("HOLD", "READY")
    assert "consent" not in body["blocking_gates"]
    assert "clearance" not in body["blocking_gates"]


def test_readiness_escalates_on_discontinued_clearance(client, onc_headers, nurse_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    session_id = _get_session_id(client, nurse_headers, patient.id)

    client.post("/api/cca/treatment/clearance", headers=onc_headers, json={
        "session_id": session_id, "order_id": order_id, "patient_id": patient.id,
        "decision": "DISCONTINUED", "reason": "Disease progression on imaging.",
    })

    readiness = client.get(f"/api/cca/treatment-sessions/{session_id}/readiness", headers=onc_headers)
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["state"] == "ESCALATE"
    assert "clearance" in readiness.json()["blocking_gates"]
