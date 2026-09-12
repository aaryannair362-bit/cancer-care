"""
Final gap-closing round, item 11: Investigations -- unified order-set/panel layer (reusing
the existing ClinicalMaster/ClinicalMasterItem pair with a new ORDER_SET master_type) and a
result-trend/overdue view.

Never computes a turnaround SLA -- "overdue" is always a plain comparison against
CCAOrder.expected_result_by, a clinician-set expectation, never a hardcoded day-count.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def admin(make_user):
    return make_user(email="admin@investigations-test.com", role="Admin")


@pytest.fixture
def oncologist(make_user, admin):
    return make_user(email="onc@investigations-test.com", role="CCAMedicalOncologist", organization_id=admin.organization_id)


@pytest.fixture
def admin_headers(auth_headers, admin):
    return auth_headers(admin)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def patient(db_session, admin):
    p = CCAPatient(mrn="INVEST-TEST-0001", name="Investigations Test Patient", age=52, sex="Male", organization_id=admin.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def published_panel_id(client, admin_headers):
    master_id = client.post("/api/cca/clinical-masters", headers=admin_headers, json={
        "master_type": "ORDER_SET", "name": "Pre-Chemo Baseline Panel",
    }).json()["master"]["id"]
    client.post(f"/api/cca/clinical-masters/{master_id}/items", headers=admin_headers, json={
        "sequence_number": 1, "fields": {"order_type": "LAB", "item_name": "CBC", "item_code": "LAB-CBC"},
    })
    client.post(f"/api/cca/clinical-masters/{master_id}/items", headers=admin_headers, json={
        "sequence_number": 2, "fields": {"order_type": "LAB", "item_name": "LFT", "item_code": "LAB-LFT"},
    })
    client.post(f"/api/cca/clinical-masters/{master_id}/publish", headers=admin_headers)
    return master_id


def test_apply_order_set_creates_one_order_per_item(client, onc_headers, admin_headers, patient, published_panel_id):
    draft_master_id = client.post("/api/cca/clinical-masters", headers=admin_headers, json={
        "master_type": "ORDER_SET", "name": "Unpublished Panel",
    }).json()["master"]["id"]
    blocked = client.post(f"/api/cca/order-sets/{draft_master_id}/apply", headers=onc_headers, json={
        "patient_id": patient.id, "clinical_indication": "Baseline workup.",
    })
    assert blocked.status_code == 409

    missing_indication = client.post(f"/api/cca/order-sets/{published_panel_id}/apply", headers=onc_headers, json={"patient_id": patient.id})
    assert missing_indication.status_code == 422

    applied = client.post(f"/api/cca/order-sets/{published_panel_id}/apply", headers=onc_headers, json={
        "patient_id": patient.id, "clinical_indication": "Baseline workup before chemotherapy.",
        "expected_result_by": "2020-01-01",
    })
    assert applied.status_code == 201, applied.text
    orders = applied.json()["orders"]
    assert len(orders) == 2
    assert {o["item_name"] for o in orders} == {"CBC", "LFT"}


def test_investigations_trend_and_overdue(client, onc_headers, patient, published_panel_id):
    client.post(f"/api/cca/order-sets/{published_panel_id}/apply", headers=onc_headers, json={
        "patient_id": patient.id, "clinical_indication": "Baseline workup.", "expected_result_by": "2020-01-01",
    })

    trend = client.get(f"/api/cca/patients/{patient.id}/investigations-trend", headers=onc_headers)
    assert trend.status_code == 200, trend.text
    body = trend.json()["trend"]
    assert any(item["item_name"] == "CBC" for item in body)
    cbc_entries = next(item for item in body if item["item_name"] == "CBC")["entries"]
    assert cbc_entries[0]["overdue"] is True

    overdue = client.get("/api/cca/investigations/overdue", headers=onc_headers)
    assert overdue.status_code == 200, overdue.text
    assert any(o["patient_id"] == patient.id and o["item_name"] == "CBC" for o in overdue.json()["overdue"])


def test_adhoc_order_with_expected_result_by(client, onc_headers, patient):
    order = client.post("/api/cca/orders", headers=onc_headers, json={
        "patient_id": patient.id, "order_type": "LAB", "item_name": "Serum Creatinine",
        "clinical_indication": "Renal function check.", "expected_result_by": "2099-01-01",
    })
    assert order.status_code == 200, order.text

    trend = client.get(f"/api/cca/patients/{patient.id}/investigations-trend", headers=onc_headers)
    entries = next(item for item in trend.json()["trend"] if item["item_name"] == "Serum Creatinine")["entries"]
    assert entries[0]["overdue"] is False
