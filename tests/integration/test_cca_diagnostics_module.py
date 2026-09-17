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


@pytest.fixture
def rad_tech(make_user, oncologist):
    return make_user(email="radtech@diaghosp.com", role="CCARadiologyTechnician", organization_id=oncologist.organization_id)


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


def test_imaging_reschedule_records_prior_value_and_scheduled_by(client, auth_headers, db_session, oncologist, rad_coordinator):
    """Gap review: calling /schedule a second time is a RESCHEDULE, not a silent overwrite --
    the prior scheduled_at is preserved in a distinct journey event, and scheduled_by records
    who did it (previously untracked)."""
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "RADIOLOGY", item_name="MRI Brain")
    coord_headers = auth_headers(rad_coordinator)

    first = client.post(f"/api/cca/imaging/orders/{order.id}/schedule", headers=coord_headers,
                         json={"scheduled_at": "2026-09-01T10:00:00", "location": "MRI Suite 1"})
    assert first.status_code == 200
    assert first.json()["order"]["scheduled_by"] == "radcoord@diaghosp.com"

    second = client.post(f"/api/cca/imaging/orders/{order.id}/schedule", headers=coord_headers,
                          json={"scheduled_at": "2026-09-02T14:00:00", "location": "MRI Suite 1"})
    assert second.status_code == 200
    assert second.json()["order"]["scheduled_at"].startswith("2026-09-02T14:00:00")

    journey = client.get(f"/api/cca/patients/{patient_id}/journey", headers=coord_headers).json()["journey_events"]
    assert any(e["event_type"] == "IMAGING_ORDER_RESCHEDULED" and "2026-09-01T10:00:00" in e["description"] for e in journey)


def test_imaging_double_booking_same_slot_is_rejected(client, auth_headers, db_session, oncologist, rad_coordinator):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order1 = _make_order(db_session, patient_id, "RADIOLOGY", item_name="CT Chest")
    order2 = _make_order(db_session, patient_id, "RADIOLOGY", item_name="CT Abdomen")
    coord_headers = auth_headers(rad_coordinator)

    ok = client.post(f"/api/cca/imaging/orders/{order1.id}/schedule", headers=coord_headers,
                      json={"scheduled_at": "2026-09-01T10:00:00", "location": "CT Suite 1"})
    assert ok.status_code == 200

    conflict = client.post(f"/api/cca/imaging/orders/{order2.id}/schedule", headers=coord_headers,
                            json={"scheduled_at": "2026-09-01T10:00:00", "location": "CT Suite 1"})
    assert conflict.status_code == 409

    # A different location at the same time is not a conflict.
    different_room = client.post(f"/api/cca/imaging/orders/{order2.id}/schedule", headers=coord_headers,
                                  json={"scheduled_at": "2026-09-01T10:00:00", "location": "CT Suite 2"})
    assert different_room.status_code == 200


def test_imaging_arrival_and_no_show_require_scheduled_state(client, auth_headers, db_session, oncologist, rad_coordinator):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "RADIOLOGY", item_name="X-Ray Chest")
    coord_headers = auth_headers(rad_coordinator)

    too_early = client.post(f"/api/cca/imaging/orders/{order.id}/arrive", headers=coord_headers)
    assert too_early.status_code == 409

    client.post(f"/api/cca/imaging/orders/{order.id}/schedule", headers=coord_headers,
                json={"scheduled_at": "2026-09-01T10:00:00", "location": "X-Ray Room 1"})

    arrived = client.post(f"/api/cca/imaging/orders/{order.id}/arrive", headers=coord_headers)
    assert arrived.status_code == 200
    assert arrived.json()["order"]["workflow_state"] == "Arrived"
    assert arrived.json()["order"]["arrived_by"] == "radcoord@diaghosp.com"

    # Can't record a no-show once already Arrived.
    late_no_show = client.post(f"/api/cca/imaging/orders/{order.id}/no-show", headers=coord_headers)
    assert late_no_show.status_code == 409


def test_imaging_no_show_from_scheduled(client, auth_headers, db_session, oncologist, rad_coordinator):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "RADIOLOGY", item_name="Mammography")
    coord_headers = auth_headers(rad_coordinator)
    client.post(f"/api/cca/imaging/orders/{order.id}/schedule", headers=coord_headers,
                json={"scheduled_at": "2026-09-01T10:00:00", "location": "Mammo Suite"})

    no_show = client.post(f"/api/cca/imaging/orders/{order.id}/no-show", headers=coord_headers)
    assert no_show.status_code == 200
    assert no_show.json()["order"]["workflow_state"] == "NoShow"


def test_imaging_cancel_requires_reason_and_blocks_further_scheduling(client, auth_headers, db_session, oncologist, rad_coordinator, radiologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "RADIOLOGY", item_name="PET-CT")
    coord_headers = auth_headers(rad_coordinator)

    missing_reason = client.post(f"/api/cca/imaging/orders/{order.id}/cancel", headers=coord_headers, json={})
    assert missing_reason.status_code == 422

    cancelled = client.post(f"/api/cca/imaging/orders/{order.id}/cancel", headers=coord_headers,
                             json={"reason": "Patient requested cancellation"})
    assert cancelled.status_code == 200
    assert cancelled.json()["order"]["workflow_state"] == "Cancelled"
    assert cancelled.json()["order"]["cancelled_by"] == "radcoord@diaghosp.com"
    assert cancelled.json()["order"]["cancellation_reason"] == "Patient requested cancellation"

    # A cancelled order cannot be rescheduled directly -- a new order must be raised.
    reschedule_attempt = client.post(f"/api/cca/imaging/orders/{order.id}/schedule", headers=coord_headers,
                                      json={"scheduled_at": "2026-09-05T10:00:00"})
    assert reschedule_attempt.status_code == 409

    # A radiologist result already finalized blocks cancellation too.
    order2 = _make_order(db_session, patient_id, "RADIOLOGY", item_name="Bone Scan")
    rad_headers = auth_headers(radiologist)
    draft = client.post(f"/api/cca/imaging/orders/{order2.id}/report", headers=rad_headers,
                         json={"findings_text": "No abnormality.", "impression": "Normal."})
    client.post(f"/api/cca/imaging/results/{draft.json()['result']['id']}/finalize", headers=rad_headers)
    already_resulted = client.post(f"/api/cca/imaging/orders/{order2.id}/cancel", headers=coord_headers,
                                    json={"reason": "test"})
    assert already_resulted.status_code == 409


def test_pathology_report_and_finalize(client, auth_headers, db_session, oncologist, pathologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "PATHOLOGY", item_name="Core Biopsy")
    path_headers = auth_headers(pathologist)
    # Specimen Receipt & Accession (safety/dataflow-critical follow-up round, reference
    # SCR-PAT-002) is now a hard precondition for drafting a pathology report.
    accession = client.post(f"/api/cca/pathology/orders/{order.id}/accession", headers=path_headers, json={
        "accession_number": "S26-DIAGMOD-001", "condition_on_receipt": "Intact",
    })
    assert accession.status_code == 201, accession.text

    draft = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "Invasive ductal carcinoma.",
        "structured_report": {
            "site": "Left breast", "specimen": "Core needle biopsy", "histology": "IDC",
            "grade": "2", "margin_status": "Clear",
        },
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


def test_lab_collection_requires_positive_identity_confirmation(client, auth_headers, db_session, oncologist, lab_tech):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "LAB", item_name="CBC with ANC")
    lab_headers = auth_headers(lab_tech)

    missing_confirmation = client.post(f"/api/cca/lab/orders/{order.id}/collect", headers=lab_headers, json={"specimen_container": "EDTA tube"})
    assert missing_confirmation.status_code == 422

    collect = client.post(f"/api/cca/lab/orders/{order.id}/collect", headers=lab_headers, json={
        "identity_confirmed": True, "specimen_container": "EDTA tube", "collection_site": "Left antecubital vein",
    })
    assert collect.status_code == 200
    body = collect.json()["order"]
    assert body["collected_by"] == "labtech@diaghosp.com"
    assert body["status"] == "IN_PROGRESS"
    assert body["identity_confirmed"] is True
    assert body["workflow_state"] == "AwaitingLabReceipt"
    assert body["specimen_accession_id"] and body["specimen_accession_id"].startswith(f"LAB-{order.id}-")

    # Cannot collect the same order twice.
    twice = client.post(f"/api/cca/lab/orders/{order.id}/collect", headers=lab_headers, json={"identity_confirmed": True})
    assert twice.status_code == 409


def test_lab_result_entry_requires_verification_before_release(client, auth_headers, db_session, oncologist, lab_tech):
    """Result Verification & Release -- Critical: entering a result must NOT finalize it. A
    separate verify call is required before it counts as released."""
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "LAB", item_name="CBC with ANC")
    lab_headers = auth_headers(lab_tech)

    # Cannot enter a result before the specimen is even collected/received.
    too_early = client.post(f"/api/cca/lab/orders/{order.id}/result", headers=lab_headers, json={"findings_text": "x"})
    assert too_early.status_code == 409

    client.post(f"/api/cca/lab/orders/{order.id}/collect", headers=lab_headers, json={"identity_confirmed": True})
    still_not_received = client.post(f"/api/cca/lab/orders/{order.id}/result", headers=lab_headers, json={"findings_text": "x"})
    assert still_not_received.status_code == 409

    receive = client.post(f"/api/cca/lab/orders/{order.id}/receive", headers=lab_headers, json={})
    assert receive.status_code == 200
    assert receive.json()["order"]["received_by"] == "labtech@diaghosp.com"
    assert receive.json()["order"]["workflow_state"] == "Received"

    entered = client.post(f"/api/cca/lab/orders/{order.id}/result", headers=lab_headers, json={
        "findings_text": "Hemoglobin 11.2 g/dL, ANC 4100/uL", "is_critical": False,
    })
    assert entered.status_code == 200
    result_body = entered.json()["result"]
    assert result_body["report_status"] == "Draft"
    assert result_body["entered_by"] == "labtech@diaghosp.com"

    # The order itself is not yet RESULTED -- only a verified/released result completes it.
    order_after_entry = client.get(f"/api/cca/lab/worklist", headers=lab_headers).json()["worklist"]
    this_order = next(o for o in order_after_entry if o["id"] == order.id)
    assert this_order["status"] != "RESULTED"
    assert this_order["latest_result"] == {"id": result_body["id"], "report_status": "Draft"}

    verify = client.post(f"/api/cca/lab/results/{result_body['id']}/verify", headers=lab_headers)
    assert verify.status_code == 200
    assert verify.json()["result"]["report_status"] == "Finalized"
    assert verify.json()["result"]["finalized_by"] == "labtech@diaghosp.com"

    # Cannot verify twice.
    verify_again = client.post(f"/api/cca/lab/results/{result_body['id']}/verify", headers=lab_headers)
    assert verify_again.status_code == 409

    # A post-verification correction requires amendment_reason and creates a NEW linked result.
    no_reason = client.post(f"/api/cca/lab/orders/{order.id}/result", headers=lab_headers, json={"findings_text": "corrected value"})
    assert no_reason.status_code == 409
    amended = client.post(f"/api/cca/lab/orders/{order.id}/result", headers=lab_headers, json={
        "findings_text": "corrected value", "amendment_reason": "Transcription error in original entry",
    })
    assert amended.status_code == 200
    amended_body = amended.json()["result"]
    assert amended_body["id"] != result_body["id"]
    assert amended_body["report_status"] == "Draft"
    assert amended_body["supersedes_id"] == result_body["id"]


def test_lab_rejection_requires_structured_reason_and_supports_recollection(client, auth_headers, db_session, oncologist, lab_tech):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "LAB", item_name="Renal Function")
    lab_headers = auth_headers(lab_tech)

    missing_reason = client.post(f"/api/cca/lab/orders/{order.id}/reject", headers=lab_headers, json={})
    assert missing_reason.status_code == 422

    free_text_reason_rejected = client.post(f"/api/cca/lab/orders/{order.id}/reject", headers=lab_headers, json={"reason": "Hemolysed sample"})
    assert free_text_reason_rejected.status_code == 422  # not one of the structured codes

    reject = client.post(f"/api/cca/lab/orders/{order.id}/reject", headers=lab_headers, json={"reason": "HEMOLYSED"})
    assert reject.status_code == 200
    assert reject.json()["order"]["workflow_state"] == "RecollectionRequired"
    assert reject.json()["order"]["rejection_reason"] == "HEMOLYSED"

    # A rejected specimen can never itself be collected or resulted -- must go through recollect.
    blocked_collect = client.post(f"/api/cca/lab/orders/{order.id}/collect", headers=lab_headers, json={"identity_confirmed": True})
    assert blocked_collect.status_code == 409
    blocked_result = client.post(f"/api/cca/lab/orders/{order.id}/result", headers=lab_headers, json={"findings_text": "x"})
    assert blocked_result.status_code == 409

    recollect = client.post(f"/api/cca/lab/orders/{order.id}/recollect", headers=lab_headers)
    assert recollect.status_code == 200
    new_order = recollect.json()["order"]
    assert new_order["id"] != order.id
    assert new_order["recollection_of_order_id"] == order.id
    assert new_order["recollection_number"] == 1
    assert new_order["item_name"] == "Renal Function"

    # Cannot recollect twice off the same rejection.
    recollect_again = client.post(f"/api/cca/lab/orders/{order.id}/recollect", headers=lab_headers)
    assert recollect_again.status_code == 409


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


def test_radiology_technician_acquisition_is_separate_from_radiologist_reporting(client, auth_headers, db_session, oncologist, radiologist, rad_tech):
    """7 Role/Module Updates developer handoff, checklist item 06: Radiology Technician
    performs acquisition; Radiologist performs interpretation/reporting -- neither may do the
    other's action."""
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_order(db_session, patient_id, "RADIOLOGY")
    rad_tech_headers = auth_headers(rad_tech)
    rad_headers = auth_headers(radiologist)

    radiologist_forbidden = client.patch(f"/api/cca/imaging/orders/{order.id}/acquisition", headers=rad_headers, json={
        "acquisition_status": "Completed",
    })
    assert radiologist_forbidden.status_code == 403, "Radiologist must not be able to record imaging acquisition"

    on_worklist = client.get("/api/cca/imaging/technical-worklist", headers=rad_tech_headers).json()["worklist"]
    assert any(o["id"] == order.id for o in on_worklist)

    bad_status = client.patch(f"/api/cca/imaging/orders/{order.id}/acquisition", headers=rad_tech_headers, json={
        "acquisition_status": "NotReal",
    })
    assert bad_status.status_code == 422

    completed = client.patch(f"/api/cca/imaging/orders/{order.id}/acquisition", headers=rad_tech_headers, json={
        "acquisition_status": "Completed", "acquisition_modality": "CT", "acquisition_protocol": "Contrast-enhanced chest",
        "contrast_used": True, "contrast_notes": "100mL iohexol, no reaction", "technical_notes": "Patient cooperative",
    })
    assert completed.status_code == 200, completed.text
    body = completed.json()["order"]
    assert body["acquisition_status"] == "Completed"
    assert body["acquisition_modality"] == "CT"
    assert body["contrast_used"] is True
    assert body["acquired_by"] == "radtech@diaghosp.com"
    assert body["acquired_at"] is not None

    now_off_worklist = client.get("/api/cca/imaging/technical-worklist", headers=rad_tech_headers).json()["worklist"]
    assert all(o["id"] != order.id for o in now_off_worklist), "a Completed order should drop off the technical worklist"

    rad_tech_forbidden = client.post(f"/api/cca/imaging/orders/{order.id}/report", headers=rad_tech_headers, json={
        "findings_text": "Should not be allowed", "impression": "N/A",
    })
    assert rad_tech_forbidden.status_code == 403, "Radiology Technician must not be able to draft an imaging report"

    report = client.post(f"/api/cca/imaging/orders/{order.id}/report", headers=rad_headers, json={
        "findings_text": "No acute findings.", "impression": "Normal study.",
    })
    assert report.status_code == 200, report.text
    # The acquisition fields the technician recorded are still visible to the Radiologist via
    # the same order -- shared record, not duplicated (checklist item 11).
    assert report.json()["result"]["order_id"] == order.id
    reloaded = client.get(f"/api/cca/imaging/orders/{order.id}", headers=rad_headers).json()["order"]
    assert reloaded["acquisition_modality"] == "CT"
