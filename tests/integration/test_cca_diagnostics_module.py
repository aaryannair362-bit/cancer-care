"""
Tests for the CCA Radiology / Pathology / Lab / Molecular Diagnostics worklists
(backend/app/routers/cca_diagnostics.py) -- covers 06_Radiologist.pdf, 07_Radiology_Coordinator.pdf,
08_Pathologist_Molecular_Diagnostics.pdf, 09_Lab_Phlebotomy.pdf.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CCAOrder


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@diaghosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def radiologist(make_user, oncologist):
    return make_user(email="radiologist@diaghosp.com", role="CCARadiologist", organization_id=oncologist.organization_id)


@pytest.fixture
def rad_coordinator(make_user, oncologist):
    return make_user(email="radcoord@diaghosp.com", role="CCARadiologyCoordinator", organization_id=oncologist.organization_id)


@pytest.fixture
def pathologist(make_user, oncologist):
    return make_user(email="pathologist@diaghosp.com", role="CCAPathologist", organization_id=oncologist.organization_id)


@pytest.fixture
def lab_tech(make_user, oncologist):
    return make_user(email="labtech@diaghosp.com", role="CCALabPhlebotomy", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id).first().id


def _make_order(db_session, patient_id, order_type, **overrides):
    order = CCAOrder(
        patient_id=patient_id, order_type=order_type, item_name=overrides.pop("item_name", "Test Order"),
        clinical_indication="Staging workup.", requested_by="onc@diaghosp.com", **overrides,
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def test_radiology_full_workflow(client, auth_headers, db_session, oncologist, radiologist, rad_coordinator):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "RADIOLOGY", item_name="CT Chest/Abdomen")

    onc_headers = auth_headers(oncologist)
    coord_headers = auth_headers(rad_coordinator)
    rad_headers = auth_headers(radiologist)

    worklist = client.get("/api/cca/imaging/worklist", headers=onc_headers).json()["worklist"]
    assert any(o["id"] == order.id for o in worklist)

    # Only Radiology Coordinator (or Admin) may schedule.
    denied = client.post(f"/api/cca/imaging/orders/{order.id}/schedule", headers=onc_headers, json={"scheduled_at": "2026-09-01T10:00:00"})
    assert denied.status_code == 403
    sched = client.post(f"/api/cca/imaging/orders/{order.id}/schedule", headers=coord_headers, json={"scheduled_at": "2026-09-01T10:00:00", "location": "CT Suite 2"})
    assert sched.status_code == 200
    assert sched.json()["order"]["status"] == "SCHEDULED"

    prep = client.patch(f"/api/cca/imaging/orders/{order.id}/preparation", headers=coord_headers, json={"preparation_status": "Completed", "preparation_notes": "Fasting confirmed."})
    assert prep.status_code == 200
    assert prep.json()["order"]["preparation_status"] == "Completed"

    # Only Radiologist (or Admin) may draft/finalize a report.
    denied_report = client.post(f"/api/cca/imaging/orders/{order.id}/report", headers=coord_headers, json={"findings_text": "x"})
    assert denied_report.status_code == 403

    draft = client.post(f"/api/cca/imaging/orders/{order.id}/report", headers=rad_headers, json={
        "technique": "CECT Chest/Abdomen", "findings_text": "No suspicious lesion.", "impression": "No metastasis.",
        "structured_report": {"measurements": []},
    })
    assert draft.status_code == 200
    result_id = draft.json()["result"]["id"]
    assert draft.json()["result"]["report_status"] == "Draft"

    finalize = client.post(f"/api/cca/imaging/results/{result_id}/finalize", headers=rad_headers)
    assert finalize.status_code == 200
    assert finalize.json()["result"]["report_status"] == "Finalized"
    assert finalize.json()["result"]["finalized_by"] == "radiologist@diaghosp.com"

    order_after = client.get(f"/api/cca/imaging/orders/{order.id}", headers=onc_headers).json()
    assert order_after["order"]["status"] == "RESULTED"

    journey = client.get(f"/api/cca/patients/{patient_id}/journey", headers=onc_headers).json()["journey_events"]
    assert any(e["event_type"] == "IMAGING_REPORT_FINALIZED" for e in journey)


def test_pathology_report_and_finalize(client, auth_headers, db_session, oncologist, pathologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "PATHOLOGY", item_name="Core Biopsy")
    path_headers = auth_headers(pathologist)

    draft = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "Invasive ductal carcinoma.",
        "structured_report": {"histologic_type": "IDC", "grade": "2", "margins": "Clear"},
    })
    assert draft.status_code == 200
    result_id = draft.json()["result"]["id"]

    finalize = client.post(f"/api/cca/pathology/results/{result_id}/finalize", headers=path_headers)
    assert finalize.status_code == 200
    assert finalize.json()["result"]["report_status"] == "Finalized"

    # A non-pathologist oncologist cannot finalize.
    onc_headers = auth_headers(oncologist)
    denied = client.post(f"/api/cca/pathology/results/{result_id}/finalize", headers=onc_headers)
    assert denied.status_code == 403


def test_molecular_diagnostics_order_and_result(client, auth_headers, db_session, oncologist, pathologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    onc_headers = auth_headers(oncologist)
    path_headers = auth_headers(pathologist)

    order = client.post("/api/cca/molecular/tests", headers=onc_headers, json={"patient_id": patient_id, "marker_name": "PD-L1"})
    assert order.status_code == 201
    test_id = order.json()["test"]["id"]
    assert order.json()["test"]["status"] == "PENDING"

    result = client.patch(f"/api/cca/molecular/tests/{test_id}", headers=path_headers, json={
        "result_as_reported": "Positive (TPS 40%)", "confirmatory_required": "no",
    })
    assert result.status_code == 200
    assert result.json()["test"]["status"] == "RESULTED"

    listing = client.get(f"/api/cca/molecular/tests?patient_id={patient_id}", headers=onc_headers).json()["tests"]
    assert any(t["id"] == test_id and t["result_as_reported"] == "Positive (TPS 40%)" for t in listing)


def test_lab_collection_rejection_and_result(client, auth_headers, db_session, oncologist, lab_tech):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "LAB", item_name="CBC with ANC")
    lab_headers = auth_headers(lab_tech)

    collect = client.post(f"/api/cca/lab/orders/{order.id}/collect", headers=lab_headers, json={"specimen_container": "EDTA tube"})
    assert collect.status_code == 200
    assert collect.json()["order"]["collected_by"] == "labtech@diaghosp.com"
    assert collect.json()["order"]["status"] == "IN_PROGRESS"

    result = client.post(f"/api/cca/lab/orders/{order.id}/result", headers=lab_headers, json={
        "findings_text": "Hemoglobin 11.2 g/dL, ANC 4100/uL", "is_critical": False,
    })
    assert result.status_code == 200
    assert result.json()["result"]["report_status"] == "Finalized"

    order2 = _make_order(db_session, patient_id, "LAB", item_name="Renal Function")
    reject = client.post(f"/api/cca/lab/orders/{order2.id}/reject", headers=lab_headers, json={"reason": "Hemolysed sample"})
    assert reject.status_code == 200
    assert reject.json()["order"]["workflow_state"] == "RecollectionRequired"

    missing_reason = client.post(f"/api/cca/lab/orders/{order2.id}/reject", headers=lab_headers, json={})
    assert missing_reason.status_code == 422


def test_diagnostics_worklists_are_org_scoped(client, auth_headers, make_user, db_session, oncologist, radiologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "RADIOLOGY")

    other_radiologist = make_user(email="other.radiologist@rivalhosp.com", role="CCARadiologist")
    other_headers = auth_headers(other_radiologist)

    worklist = client.get("/api/cca/imaging/worklist", headers=other_headers).json()["worklist"]
    assert all(o["id"] != order.id for o in worklist)

    denied = client.get(f"/api/cca/imaging/orders/{order.id}", headers=other_headers)
    assert denied.status_code == 404

    no_auth = client.get("/api/cca/imaging/worklist")
    assert no_auth.status_code in (401, 403)
