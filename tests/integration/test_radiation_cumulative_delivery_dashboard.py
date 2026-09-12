"""
Worklist/dashboard follow-up round: Batches 4-5 (Radiation Physics QA / RT Delivery, C.16/
C.17) had no Cumulative Delivery Dashboard (reference SCR-RTT-009) even though the
underlying data (fraction count, delivered_dose_gy per fraction, prescribed total) already
existed -- this is pure summation over already-recorded rows, never a tolerance/threshold
comparison against those totals.
"""
import pytest

from app.models_cca import CCAPatient

_FULL_PHYSICS_QA_CHECKLIST = {
    "prescription_plan_concordance": True, "dose_volume_constraint_review": True,
    "target_oar_coverage_review": True, "machine_deliverability_review": True,
}


@pytest.fixture
def oncologist(make_user):
    return make_user(email="ro@cumdelivery-test.com", role="CCARadiationOncologist")


@pytest.fixture
def physicist(make_user, oncologist):
    return make_user(email="physicist@cumdelivery-test.com", role="CCARadiationPhysicist", organization_id=oncologist.organization_id)


@pytest.fixture
def radiologist(make_user, oncologist):
    return make_user(email="rtt@cumdelivery-test.com", role="CCARadiologist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def physicist_headers(auth_headers, physicist):
    return auth_headers(physicist)


@pytest.fixture
def rtt_headers(auth_headers, radiologist):
    return auth_headers(radiologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="CUMDELIVERY-0001", name="Cumulative Delivery Test Patient", age=64, sex="Male", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _phase_treatment_ready(client, onc_headers, physicist_headers, patient_id, number_of_fractions=3):
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "diagnosis": "Prostate Cancer", "intent": "Curative", "technique": "VMAT",
    }).json()["radiation_prescription"]["id"]
    phase_id = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/phases", headers=onc_headers, json={
        "label": "Prostate", "treatment_site": "Prostate", "total_prescribed_dose_gy": 60,
        "dose_per_fraction_gy": 20, "number_of_fractions": number_of_fractions,
    }).json()["phase"]["id"]
    for status in ("simulation_pending", "simulation_complete", "contouring", "planning", "physics_qa"):
        client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": status})
    client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_PHYSICS_QA_CHECKLIST, "note": "All clear.",
    })
    for status in ("physician_approved", "treatment_ready"):
        client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": status})
    return phase_id


def _verify_and_deliver(client, rtt_headers, fraction_id, fraction_number, **extra):
    client.post(f"/api/cca/radiation-fractions/{fraction_id}/pretreatment-verification", headers=rtt_headers, json={
        "identity_reverified": True, "site_laterality_confirmed": True, "confirmed_fraction_number": fraction_number,
    })
    body = {"status": "delivered"}
    body.update(extra)
    r = client.post(f"/api/cca/radiation-fractions/{fraction_id}/event", headers=rtt_headers, json=body)
    assert r.status_code == 200, r.text


def test_dashboard_reflects_delivered_missed_and_cumulative_dose(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _phase_treatment_ready(client, onc_headers, physicist_headers, patient.id, number_of_fractions=3)
    fractions = client.get(f"/api/cca/radiation-phases/{phase_id}/fractions", headers=onc_headers).json()["fractions"]

    empty = client.get(f"/api/cca/radiation-phases/{phase_id}/cumulative-delivery", headers=onc_headers)
    assert empty.status_code == 200, empty.text
    assert empty.json()["fractions_delivered"] == 0
    assert empty.json()["fractions_remaining"] == 3

    _verify_and_deliver(client, rtt_headers, fractions[0]["id"], fractions[0]["fraction_number"], delivered_dose_gy=20)
    _verify_and_deliver(client, rtt_headers, fractions[1]["id"], fractions[1]["fraction_number"], delivered_dose_gy=20)
    client.post(f"/api/cca/radiation-fractions/{fractions[2]['id']}/event", headers=rtt_headers, json={
        "status": "missed", "interruption_reason": "Patient unwell.",
    })

    dashboard = client.get(f"/api/cca/radiation-phases/{phase_id}/cumulative-delivery", headers=onc_headers)
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    assert body["fractions_delivered"] == 2
    assert body["fractions_missed"] == 1
    assert body["fractions_remaining"] == 0
    assert body["cumulative_delivered_dose_gy"] == 40
    assert body["total_prescribed_dose_gy"] == 60
    assert body["dose_mismatches"] == []


def test_dashboard_flags_dose_mismatch_and_interruptions(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _phase_treatment_ready(client, onc_headers, physicist_headers, patient.id, number_of_fractions=2)
    fractions = client.get(f"/api/cca/radiation-phases/{phase_id}/fractions", headers=onc_headers).json()["fractions"]

    _verify_and_deliver(client, rtt_headers, fractions[0]["id"], fractions[0]["fraction_number"],
                         dose_match_confirmed=False, dose_mismatch_note="Machine output slightly low.")

    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={
        "status": "interrupted", "reason": "Linac fault.", "category": "Machine Issue",
    })

    dashboard = client.get(f"/api/cca/radiation-phases/{phase_id}/cumulative-delivery", headers=onc_headers)
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    assert fractions[0]["id"] in body["dose_mismatches"]
    assert body["interruption_count"] == 1
    assert body["open_interruption_count"] == 1
