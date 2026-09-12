"""
Safety/dataflow-critical follow-up round: Batch 6 (Pathology, C.19) was missing a Specimen
Receipt & Accession gate (reference SCR-PAT-002 -- nothing verified the right specimen was
received before report drafting began) and an active critical-result communication chain
(the passive is_critical/acknowledged_by pair existed, but nothing recorded that someone was
actually notified or that an unactioned critical result was escalated).

Never tests a computed clinical judgment -- accession accept/quarantine is a structural
condition/labelling check, and notify/escalate are plain recorded attestations.
"""
import pytest

from app.models_cca import CCAPatient, CCAOrder, CCAResult


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@accession-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def pathologist(make_user, oncologist):
    return make_user(email="pathologist@accession-test.com", role="CCAPathologist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def path_headers(auth_headers, pathologist):
    return auth_headers(pathologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="ACCESSION-0001", name="Accession Test Patient", age=55, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _make_pathology_order(db_session, patient_id):
    order = CCAOrder(
        patient_id=patient_id, order_type="PATHOLOGY", item_name="Excisional Biopsy",
        clinical_indication="Palpable mass.", requested_by="onc@accession-test.com",
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def test_report_blocked_until_specimen_accessioned(client, path_headers, db_session, patient):
    order = _make_pathology_order(db_session, patient.id)

    blocked = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "x", "structured_report": {"site": "Breast", "specimen": "Excisional biopsy", "histology": "Pending"},
    })
    assert blocked.status_code == 409
    assert "accession" in blocked.text.lower()

    accepted = client.post(f"/api/cca/pathology/orders/{order.id}/accession", headers=path_headers, json={
        "accession_number": "S26-99001", "container_count": 1, "condition_on_receipt": "Intact",
    })
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["accession"]["status"] == "ACCEPTED"

    now_ok = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "x", "structured_report": {"site": "Breast", "specimen": "Excisional biopsy", "histology": "Pending"},
    })
    assert now_ok.status_code == 200, now_ok.text


def test_discrepancy_quarantines_and_still_blocks_report(client, path_headers, db_session, patient):
    order = _make_pathology_order(db_session, patient.id)

    missing_note = client.post(f"/api/cca/pathology/orders/{order.id}/accession", headers=path_headers, json={
        "accession_number": "S26-99002", "condition_on_receipt": "Leaking",
    })
    assert missing_note.status_code == 422

    quarantined = client.post(f"/api/cca/pathology/orders/{order.id}/accession", headers=path_headers, json={
        "accession_number": "S26-99002", "condition_on_receipt": "Leaking",
        "discrepancy_note": "Container leaking on arrival -- lab safety notified.",
    })
    assert quarantined.status_code == 201, quarantined.text
    assert quarantined.json()["accession"]["status"] == "QUARANTINED"

    still_blocked = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "x", "structured_report": {},
    })
    assert still_blocked.status_code == 409


def test_non_pathologist_cannot_accession(client, onc_headers, db_session, patient):
    order = _make_pathology_order(db_session, patient.id)
    forbidden = client.post(f"/api/cca/pathology/orders/{order.id}/accession", headers=onc_headers, json={"accession_number": "S26-99003"})
    assert forbidden.status_code == 403


# ---------------------------------------------------------------------------
# Active critical-result communication
# ---------------------------------------------------------------------------

def _make_critical_result(db_session, patient_id):
    result = CCAResult(patient_id=patient_id, result_type="PATHOLOGY", title="Critical finding", is_critical=True)
    db_session.add(result)
    db_session.commit()
    db_session.refresh(result)
    return result


def test_notify_and_escalate_critical_result(client, onc_headers, db_session, patient):
    result = _make_critical_result(db_session, patient.id)

    missing_fields = client.post(f"/api/cca/results/{result.id}/notify-critical", headers=onc_headers, json={"notified_to": "Dr. Referring Physician"})
    assert missing_fields.status_code == 422

    notified = client.post(f"/api/cca/results/{result.id}/notify-critical", headers=onc_headers, json={
        "notified_to": "Dr. Referring Physician", "notification_method": "Phone", "escalation_required": True,
    })
    assert notified.status_code == 200, notified.text
    assert notified.json()["result"]["critical_notification_method"] == "Phone"
    assert notified.json()["result"]["critical_escalation_required"] is True

    escalated = client.post(f"/api/cca/results/{result.id}/escalate-critical", headers=onc_headers, json={"escalated_to": "On-call Oncologist"})
    assert escalated.status_code == 200, escalated.text
    assert escalated.json()["result"]["critical_escalated_to"] == "On-call Oncologist"


def test_notify_requires_result_to_be_critical(client, onc_headers, db_session, patient):
    result = CCAResult(patient_id=patient.id, result_type="LAB", title="Routine CBC", is_critical=False)
    db_session.add(result)
    db_session.commit()
    db_session.refresh(result)

    blocked = client.post(f"/api/cca/results/{result.id}/notify-critical", headers=onc_headers, json={
        "notified_to": "x", "notification_method": "Phone",
    })
    assert blocked.status_code == 409
