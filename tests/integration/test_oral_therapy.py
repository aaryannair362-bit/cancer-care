"""
Product 1 vs Product 2 Functional Gap Report, Batch 9: Oral / Continuous Anticancer
Therapy (C.14). Completely missing before this batch -- self-administered therapy had no
backend representation at all, unlike the infusion-chair TreatmentOrder pipeline.

Covers: sign requires missed_dose_instruction (ORL-020); dispensing is blocked until
counselling is recorded (ORL-050); adherence/toxicity review; and a hold event that stays
HOLD_NOT_COMMUNICATED until the patient is actually contacted (ORL-070).

Never tests a computed dose, days-supply, adherence-percentage, or interaction-check --
those are deliberately not ported (see OralTherapyPrescription's docstring).
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@oral-therapy-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def pharmacist(make_user, oncologist):
    return make_user(email="pharm@oral-therapy-test.com", role="CCAPharmacist", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def pharm_headers(auth_headers, pharmacist):
    return auth_headers(pharmacist)


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id).first().id


def test_sign_requires_missed_dose_instruction(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx = client.post("/api/cca/oral-therapy-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "drug": "Capecitabine", "formulation_strength": "500mg tablet",
        "final_prescribed_dose": "as prescribed", "frequency": "Twice daily",
    }).json()["prescription"]
    assert rx["status"] == "DRAFT"

    blocked = client.post(f"/api/cca/oral-therapy-prescriptions/{rx['id']}/sign", headers=onc_headers)
    assert blocked.status_code == 409

    update = client.put(f"/api/cca/oral-therapy-prescriptions/{rx['id']}", headers=onc_headers, json={
        "missed_dose_instruction": "If a dose is missed by more than a few hours, skip it and take the next dose as scheduled -- never double up.",
    })
    assert update.status_code == 200

    signed = client.post(f"/api/cca/oral-therapy-prescriptions/{rx['id']}/sign", headers=onc_headers)
    assert signed.status_code == 200, signed.text
    assert signed.json()["prescription"]["status"] == "SIGNED"

    # Cannot edit once signed.
    edit_after_sign = client.put(f"/api/cca/oral-therapy-prescriptions/{rx['id']}", headers=onc_headers, json={"drug": "Changed"})
    assert edit_after_sign.status_code == 409


def _make_signed_rx(client, onc_headers, patient_id):
    rx = client.post("/api/cca/oral-therapy-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "drug": "Capecitabine",
        "missed_dose_instruction": "Skip the missed dose; never double up.",
    }).json()["prescription"]
    client.post(f"/api/cca/oral-therapy-prescriptions/{rx['id']}/sign", headers=onc_headers)
    return rx["id"]


def test_dispense_blocked_until_counselling_recorded(client, onc_headers, pharm_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx_id = _make_signed_rx(client, onc_headers, patient_id)

    blocked = client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/dispense", headers=pharm_headers, json={"quantity_dispensed": "56 tablets"})
    assert blocked.status_code == 409

    counsel = client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/counselling", headers=pharm_headers, json={
        "checklist": [{"item": "Dose and timing", "covered": True}], "language": "English",
    })
    assert counsel.status_code == 200, counsel.text

    dispense = client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/dispense", headers=pharm_headers, json={
        "quantity_dispensed": "56 tablets", "days_supply": "14",
    })
    assert dispense.status_code == 200, dispense.text

    rx = client.get(f"/api/cca/oral-therapy-prescriptions/{rx_id}", headers=pharm_headers).json()["prescription"]
    assert rx["status"] == "ACTIVE"


def test_review_and_hold_event_notification_gate(client, onc_headers, pharm_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx_id = _make_signed_rx(client, onc_headers, patient_id)
    client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/counselling", headers=pharm_headers, json={"checklist": []})
    client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/dispense", headers=pharm_headers, json={"quantity_dispensed": "56"})

    review = client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/reviews", headers=pharm_headers, json={
        "adherence_method": "Pill count", "doses_missed": "2", "adherence_narrative": "Mostly adherent, two missed doses due to nausea.",
        "dose_decision": "Continue Unchanged",
    })
    assert review.status_code == 200, review.text

    hold = client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/hold-events", headers=onc_headers, json={
        "event_type": "Hold", "reason": "Grade 3 hand-foot syndrome.",
    })
    assert hold.status_code == 200, hold.text
    assert hold.json()["hold_event"]["patient_notification_status"] == "HOLD_NOT_COMMUNICATED"
    hold_id = hold.json()["hold_event"]["id"]

    rx = client.get(f"/api/cca/oral-therapy-prescriptions/{rx_id}", headers=onc_headers).json()["prescription"]
    assert rx["status"] == "ON_HOLD"

    confirm = client.post(f"/api/cca/oral-therapy-hold-events/{hold_id}/confirm-contact", headers=onc_headers)
    assert confirm.status_code == 200
    assert confirm.json()["hold_event"]["patient_notification_status"] == "COMMUNICATED"


def test_cross_org_isolation(client, onc_headers, db_session, oncologist, make_user, auth_headers):
    other_onc = make_user(email="onc2@oral-therapy-test.com", role="CCAMedicalOncologist")
    other_headers = auth_headers(other_onc)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx_id = _make_signed_rx(client, onc_headers, patient_id)

    cross_org = client.get(f"/api/cca/oral-therapy-prescriptions/{rx_id}", headers=other_headers)
    assert cross_org.status_code == 404
