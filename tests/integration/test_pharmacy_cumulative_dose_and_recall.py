"""
Safety/dataflow-critical follow-up round (functional/dataflow-parity verification against
the reference spec, not a new gap-report batch): Batch 2 (Oncology Pharmacy, C.11) was
missing a Cumulative Dose Surveillance registry (SCR-PHA-009) and a Wastage/Returns/Recalls
workflow beyond inline prep-time wastage fields (SCR-PHA-010).

Never tests a computed ceiling/threshold -- dose_value is a typed string, and no comparison
against any limit is ever computed here (standing repo rule). The recall's affected-patient
trace is a plain join over PharmacyPreparation.batch_number, not a clinical judgment.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@cumdose-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def pharmacist(make_user, oncologist):
    return make_user(email="pharm@cumdose-test.com", role="CCAPharmacist", organization_id=oncologist.organization_id)


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


_FULL_CHECKLIST = {k: True for k in [
    "patient_identity", "allergy", "regimen_version", "cycle_day", "dose_basis", "calculated_dose",
    "ordered_dose", "dose_variance", "renal_adjustment", "hepatic_adjustment", "cumulative_dose",
    "interaction", "duplication", "route", "diluent", "final_concentration", "stock", "expiry",
]}


def _create_regimen(client, headers):
    created = client.post("/api/cca/regimens", headers=headers, json={
        "name": "Cumulative Dose Test Regimen", "cancer_indication": "Breast Cancer",
        "drug_lines": [{"generic_name": "Doxorubicin", "standard_protocol_dose": "60 mg/m2", "route": "IV"}],
    })
    return created.json()["regimen"]["id"]


def _signed_order_with_drug_lines(client, onc_headers, patient_id):
    regimen_id = _create_regimen(client, onc_headers)
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={
        "patient_id": patient_id, "regimen_id": regimen_id,
    }).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})
    order = client.post("/api/cca/treatment-orders", headers=onc_headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id,
    }).json()["treatment_order"]
    order_id = order["id"]
    line_ids = [l["id"] for l in order["drug_lines"]]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=onc_headers)
    return order_id, line_ids


def test_cumulative_dose_registry_internal_and_external(client, onc_headers, pharm_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)

    external = client.post(f"/api/cca/patients/{patient_id}/cumulative-dose-records", headers=onc_headers, json={
        "agent": "Doxorubicin", "source": "External/Prior", "dose_value": "as documented",
        "unit": "mg/m2 (cumulative, prior institution)", "external_source_detail": "Prior institution, 2024, 4 cycles.",
    })
    assert external.status_code == 200, external.text
    assert external.json()["record"]["source"] == "External/Prior"

    internal = client.post(f"/api/cca/patients/{patient_id}/cumulative-dose-records", headers=pharm_headers, json={
        "agent": "Doxorubicin", "dose_value": "as recorded on administration", "unit": "mg/m2", "cycle_reference": "Cycle 3",
    })
    assert internal.status_code == 200, internal.text
    record_id = internal.json()["record"]["id"]

    listed = client.get(f"/api/cca/patients/{patient_id}/cumulative-dose-records?agent=Doxorubicin", headers=onc_headers)
    assert listed.status_code == 200
    assert len(listed.json()["records"]) == 2

    flagged = client.post(f"/api/cca/cumulative-dose-records/{record_id}/flag", headers=onc_headers, json={
        "flagged_reason": "Approaching institution's monitoring threshold -- clinician review requested.",
    })
    assert flagged.status_code == 200
    assert flagged.json()["record"]["flagged_for_review"] is True


def test_pharmacy_return_event(client, onc_headers, pharm_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, line_ids = _signed_order_with_drug_lines(client, onc_headers, patient_id)

    ret = client.post(f"/api/cca/patients/{patient_id}/pharmacy-returns", headers=pharm_headers, json={
        "treatment_order_drug_line_id": line_ids[0], "batch_number": "BATCH-002",
        "quantity_returned": "1 vial (unused)", "reason": "Treatment cancelled", "disposition": "Destroyed",
    })
    assert ret.status_code == 200, ret.text
    assert ret.json()["return_event"]["disposition"] == "Destroyed"

    forbidden = client.post(f"/api/cca/patients/{patient_id}/pharmacy-returns", headers=onc_headers, json={"reason": "x"})
    assert forbidden.status_code == 403


def test_recall_traces_affected_patients_by_batch(client, onc_headers, pharm_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, line_ids = _signed_order_with_drug_lines(client, onc_headers, patient_id)
    client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Verified", "checklist": _FULL_CHECKLIST,
    })
    client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-preparation", headers=pharm_headers, json={
        "batch_number": "RECALL-LOT-001", "expiry_date": "2027-01-01",
    })

    recall = client.post("/api/cca/pharmacy-recalls", headers=pharm_headers, json={
        "drug_name": "Doxorubicin", "batch_number": "RECALL-LOT-001",
        "recall_reason": "Manufacturer sterility concern.", "recall_level": "Class II",
    })
    assert recall.status_code == 200, recall.text
    recall_id = recall.json()["recall"]["id"]
    assert recall.json()["recall"]["status"] == "OPEN"

    traced = client.get(f"/api/cca/pharmacy-recalls/{recall_id}/affected-patients", headers=pharm_headers)
    assert traced.status_code == 200, traced.text
    affected_ids = [p["id"] for p in traced.json()["affected_patients"]]
    assert patient_id in affected_ids

    listed = client.get("/api/cca/pharmacy-recalls", headers=onc_headers)
    assert listed.status_code == 200
    assert any(r["id"] == recall_id for r in listed.json()["recalls"])

    close = client.post(f"/api/cca/pharmacy-recalls/{recall_id}/close", headers=pharm_headers)
    assert close.status_code == 200
    assert close.json()["recall"]["status"] == "CLOSED"

    double_close = client.post(f"/api/cca/pharmacy-recalls/{recall_id}/close", headers=pharm_headers)
    assert double_close.status_code == 409


def test_recall_requires_pharmacy_or_admin(client, onc_headers):
    forbidden = client.post("/api/cca/pharmacy-recalls", headers=onc_headers, json={
        "drug_name": "X", "batch_number": "Y", "recall_reason": "Z",
    })
    assert forbidden.status_code == 403
