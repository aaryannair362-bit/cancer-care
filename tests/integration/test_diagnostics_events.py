"""
Tests that finalizing an imaging/pathology/lab result publishes a durable DomainEvent, not
just the pre-existing CCAJourneyEvent -- closing a gap in the event-dispatch coverage
flagged during the P0/P1 work (backend/app/routers/cca_diagnostics.py).
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CCAOrder, DomainEvent


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@diagevents.com", role="CCAMedicalOncologist")


@pytest.fixture
def radiologist(make_user, oncologist):
    return make_user(email="radiologist@diagevents.com", role="CCARadiologist", organization_id=oncologist.organization_id)


@pytest.fixture
def pathologist(make_user, oncologist):
    return make_user(email="pathologist@diagevents.com", role="CCAPathologist", organization_id=oncologist.organization_id)


@pytest.fixture
def lab_tech(make_user, oncologist):
    return make_user(email="lab@diagevents.com", role="CCALabPhlebotomy", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def _raise_order(db_session, patient_id, order_type):
    order = CCAOrder(
        patient_id=patient_id, order_type=order_type, item_name=f"Test {order_type}",
        clinical_indication="Staging workup.", requested_by="onc@diagevents.com", status="RAISED",
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order.id


def test_lab_result_publishes_domain_event(client, auth_headers, db_session, oncologist, lab_tech):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id = _raise_order(db_session, patient_id, "LAB")

    res = client.post(f"/api/cca/lab/orders/{order_id}/result", headers=auth_headers(lab_tech), json={"findings_text": "WBC 6.2"})
    assert res.status_code == 200

    event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "LAB_RESULT_FINALIZED").first()
    assert event is not None
    assert event.patient_id == patient_id


def test_imaging_and_pathology_finalize_publish_domain_events(client, auth_headers, db_session, oncologist, radiologist, pathologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)

    imaging_order_id = _raise_order(db_session, patient_id, "RADIOLOGY")
    radiologist_headers = auth_headers(radiologist)
    imaging_result_id = client.post(f"/api/cca/imaging/orders/{imaging_order_id}/report", headers=radiologist_headers, json={"findings_text": "No acute findings."}).json()["result"]["id"]
    imaging_res = client.post(f"/api/cca/imaging/results/{imaging_result_id}/finalize", headers=radiologist_headers)
    assert imaging_res.status_code == 200
    assert db_session.query(DomainEvent).filter(DomainEvent.event_type == "IMAGING_REPORT_FINALIZED").count() == 1

    path_order_id = _raise_order(db_session, patient_id, "PATHOLOGY")
    pathologist_headers = auth_headers(pathologist)
    path_result_id = client.post(f"/api/cca/pathology/orders/{path_order_id}/report", headers=pathologist_headers, json={"findings_text": "Invasive ductal carcinoma."}).json()["result"]["id"]
    path_res = client.post(f"/api/cca/pathology/results/{path_result_id}/finalize", headers=pathologist_headers)
    assert path_res.status_code == 200
    assert db_session.query(DomainEvent).filter(DomainEvent.event_type == "PATHOLOGY_REPORT_FINALIZED").count() == 1
