"""
Safety/dataflow-critical follow-up round: Batch 9 (Oral/Continuous Therapy, C.14) was
missing the monitoring-overdue refill gate the reference spec requires (ORL-060) --
previously a refill could be dispensed indefinitely with no check on whether required
monitoring (labs, clinical review) was ever done.

Never computes anything -- next_monitoring_due_date is a clinician-set date, and the gate is
a plain date comparison with an explicit override path, never a silent bypass.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@oral-monitoring-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def pharmacist(make_user, oncologist):
    return make_user(email="pharm@oral-monitoring-test.com", role="CCAPharmacist", organization_id=oncologist.organization_id)


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


def _make_signed_rx(client, onc_headers, pharm_headers, patient_id):
    rx = client.post("/api/cca/oral-therapy-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "drug": "Capecitabine", "missed_dose_instruction": "Skip the missed dose; never double up.",
    }).json()["prescription"]
    client.post(f"/api/cca/oral-therapy-prescriptions/{rx['id']}/sign", headers=onc_headers)
    client.post(f"/api/cca/oral-therapy-prescriptions/{rx['id']}/counselling", headers=pharm_headers, json={"checklist": []})
    return rx["id"]


def test_refill_blocked_when_monitoring_overdue(client, onc_headers, pharm_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx_id = _make_signed_rx(client, onc_headers, pharm_headers, patient_id)

    # No monitoring due date set yet -- dispensing proceeds normally.
    first_fill = client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/dispense", headers=pharm_headers, json={"quantity_dispensed": "56"})
    assert first_fill.status_code == 200, first_fill.text

    # A review sets an overdue monitoring due date (in the past).
    review = client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/reviews", headers=onc_headers, json={
        "adherence_narrative": "Tolerating well, routine bloods due before next cycle.",
        "next_monitoring_due_date": "2020-01-01",
    })
    assert review.status_code == 200, review.text
    rx = client.get(f"/api/cca/oral-therapy-prescriptions/{rx_id}", headers=onc_headers).json()["prescription"]
    assert rx["next_monitoring_due_date"] == "2020-01-01"

    blocked_refill = client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/dispense", headers=pharm_headers, json={"quantity_dispensed": "56"})
    assert blocked_refill.status_code == 409
    assert "overdue" in blocked_refill.text.lower()

    overridden = client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/dispense", headers=pharm_headers, json={
        "quantity_dispensed": "56", "monitoring_override_reason": "Patient travelling, bloods arranged locally, clinician aware.",
    })
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["dispensing"]["monitoring_override_reason"].startswith("Patient travelling")


def test_refill_proceeds_when_monitoring_due_date_in_future(client, onc_headers, pharm_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx_id = _make_signed_rx(client, onc_headers, pharm_headers, patient_id)

    client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/reviews", headers=onc_headers, json={
        "next_monitoring_due_date": "2099-01-01",
    })
    ok = client.post(f"/api/cca/oral-therapy-prescriptions/{rx_id}/dispense", headers=pharm_headers, json={"quantity_dispensed": "56"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["dispensing"]["monitoring_override_reason"] is None
