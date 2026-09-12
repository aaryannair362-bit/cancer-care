"""
Worklist/dashboard follow-up round: Batch 6 (Pathology, C.19) had no Quality/TAT Dashboard
(reference SCR-PAT-018) -- volume by status, turn-around-time, overdue reports and the
critical-result/amendment counts, all plain aggregation over CCAOrder/CCAResult rows that
already exist. overdue_hours is an adjustable operational display threshold (elapsed-time
arithmetic, the same class already used by the Live Infusion Board's observation_overdue
flag), never a clinical/dosage judgment.
"""
import pytest
from datetime import datetime, timedelta

from app.models_cca import CCAPatient, CCAOrder, CCAResult


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@path-dashboard-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def pathologist(make_user, oncologist):
    return make_user(email="pathologist@path-dashboard-test.com", role="CCAPathologist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def path_headers(auth_headers, pathologist):
    return auth_headers(pathologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="PATH-DASHBOARD-0001", name="Pathology Dashboard Test Patient", age=50, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _make_pathology_order(db_session, patient_id, ordered_at=None):
    order = CCAOrder(
        patient_id=patient_id, order_type="PATHOLOGY", item_name="Core Needle Biopsy",
        clinical_indication="Suspicious mass.", requested_by="onc@path-dashboard-test.com",
        ordered_at=ordered_at or datetime.utcnow(),
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def _accession(client, path_headers, order_id, accession_number):
    r = client.post(f"/api/cca/pathology/orders/{order_id}/accession", headers=path_headers, json={
        "accession_number": accession_number, "condition_on_receipt": "Intact",
    })
    assert r.status_code == 201, r.text


def test_dashboard_counts_status_tat_and_overdue(client, path_headers, db_session, patient):
    # An order still pending, raised long enough ago to be flagged overdue.
    stale_order = _make_pathology_order(db_session, patient.id, ordered_at=datetime.utcnow() - timedelta(hours=200))
    _accession(client, path_headers, stale_order.id, "S26-STALE-001")

    # A second order, finalized promptly -- contributes to TAT and volume-by-status.
    fresh_order = _make_pathology_order(db_session, patient.id)
    _accession(client, path_headers, fresh_order.id, "S26-FRESH-001")
    draft = client.post(f"/api/cca/pathology/orders/{fresh_order.id}/report", headers=path_headers, json={
        "findings_text": "x", "structured_report": {"site": "Breast", "specimen": "Core biopsy", "histology": "IDC"},
    })
    result_id = draft.json()["result"]["id"]
    client.post(f"/api/cca/pathology/results/{result_id}/finalize", headers=path_headers)

    dashboard = client.get("/api/cca/pathology/quality-dashboard", headers=path_headers)
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    assert body["total_orders"] >= 2
    assert body["average_tat_hours"] is not None
    assert any(o["order_id"] == stale_order.id for o in body["overdue_reports"])
    assert not any(o["order_id"] == fresh_order.id for o in body["overdue_reports"])
    assert body["overdue_threshold_hours"] == 72

    tighter = client.get("/api/cca/pathology/quality-dashboard?overdue_hours=1", headers=path_headers)
    assert tighter.json()["overdue_count"] >= body["overdue_count"]


def test_dashboard_counts_amendments_and_critical_notification(client, path_headers, db_session, patient):
    order = _make_pathology_order(db_session, patient.id)
    _accession(client, path_headers, order.id, "S26-CRIT-001")
    draft = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "x", "is_critical": True,
        "structured_report": {"site": "Breast", "specimen": "Core biopsy", "histology": "IDC"},
    })
    result_id = draft.json()["result"]["id"]
    client.post(f"/api/cca/pathology/results/{result_id}/finalize", headers=path_headers)
    client.post(f"/api/cca/results/{result_id}/notify-critical", headers=path_headers, json={
        "notified_to": "Dr. Referring Physician", "notification_method": "Phone",
    })

    amended = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "amendment_reason": "Grade revised after additional stains.", "findings_text": "x (amended)",
        "is_critical": True,  # carried forward explicitly -- an amendment does not inherit is_critical automatically
        "structured_report": {"site": "Breast", "specimen": "Core biopsy", "histology": "IDC", "grade": "3"},
    })
    amended_id = amended.json()["result"]["id"]
    client.post(f"/api/cca/pathology/results/{amended_id}/finalize", headers=path_headers)
    client.post(f"/api/cca/results/{amended_id}/notify-critical", headers=path_headers, json={
        "notified_to": "Dr. Referring Physician", "notification_method": "Phone",
    })

    dashboard = client.get("/api/cca/pathology/quality-dashboard", headers=path_headers)
    body = dashboard.json()
    assert body["amendment_count"] >= 1
    assert body["critical_result_count"] >= 1
    assert body["critical_notified_count"] >= 1


def test_dashboard_org_scoped(client, path_headers, db_session, patient, make_user, auth_headers):
    order = _make_pathology_order(db_session, patient.id)
    _accession(client, path_headers, order.id, "S26-ORG-001")

    other_onc = make_user(email="onc2@path-dashboard-test.com", role="CCAMedicalOncologist")
    other_path = make_user(email="path2@path-dashboard-test.com", role="CCAPathologist", organization_id=other_onc.organization_id)
    other_headers = auth_headers(other_path)

    other_dashboard = client.get("/api/cca/pathology/quality-dashboard", headers=other_headers)
    assert other_dashboard.status_code == 200
    assert other_dashboard.json()["total_orders"] == 0
