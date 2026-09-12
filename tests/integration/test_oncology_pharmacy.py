"""
Product 1 vs Product 2 Functional Gap Report, Batch 2: Oncology Pharmacy --
verification/query/reject, preparation/compounding, independent double-check/release, and
the Day Care gate that requires a full pharmacy release before administration can start.

Never tests dose computation -- every checklist item is a pharmacist attestation (boolean),
never a system-computed comparison or threshold.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@pharmacy-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def pharmacist_a(make_user, oncologist):
    return make_user(email="pharma@pharmacy-test.com", role="CCAPharmacist", organization_id=oncologist.organization_id)


@pytest.fixture
def pharmacist_b(make_user, oncologist):
    return make_user(email="pharmb@pharmacy-test.com", role="CCAPharmacist", organization_id=oncologist.organization_id)


@pytest.fixture
def nurse(make_user, oncologist):
    return make_user(email="nurse@pharmacy-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def pharm_a_headers(auth_headers, pharmacist_a):
    return auth_headers(pharmacist_a)


@pytest.fixture
def pharm_b_headers(auth_headers, pharmacist_b):
    return auth_headers(pharmacist_b)


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def _create_regimen(client, headers):
    created = client.post("/api/cca/regimens", headers=headers, json={
        "name": "Pharmacy Test Regimen", "cancer_indication": "Breast Cancer",
        "drug_lines": [
            {"generic_name": "Doxorubicin", "standard_protocol_dose": "60 mg/m2", "route": "IV"},
            {"generic_name": "Cyclophosphamide", "standard_protocol_dose": "600 mg/m2", "route": "IV"},
        ],
    })
    assert created.status_code == 201, created.text
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


_FULL_CHECKLIST = {k: True for k in [
    "patient_identity", "allergy", "regimen_version", "cycle_day", "dose_basis", "calculated_dose",
    "ordered_dose", "dose_variance", "renal_adjustment", "hepatic_adjustment", "cumulative_dose",
    "interaction", "duplication", "route", "diluent", "final_concentration", "stock", "expiry",
]}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def test_verification_requires_every_checklist_item_to_verify(client, onc_headers, pharm_a_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, _ = _signed_order_with_drug_lines(client, onc_headers, patient_id)

    incomplete = dict(_FULL_CHECKLIST)
    incomplete["allergy"] = False
    rejected = client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_a_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Verified", "checklist": incomplete,
    })
    assert rejected.status_code == 422
    assert "allergy" in rejected.text


def test_verification_verified_succeeds_and_updates_pharmacy_readiness(client, onc_headers, pharm_a_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, _ = _signed_order_with_drug_lines(client, onc_headers, patient_id)

    verified = client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_a_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Verified", "checklist": _FULL_CHECKLIST,
    })
    assert verified.status_code == 201, verified.text

    readiness = client.get(f"/api/cca/treatment/{order_id}/pharmacy-readiness?patient_id={patient_id}", headers=pharm_a_headers)
    assert readiness.json()["pharmacy_readiness"]["status"] == "Verified"


def test_only_pharmacist_may_verify(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, _ = _signed_order_with_drug_lines(client, onc_headers, patient_id)

    denied = client.post("/api/cca/treatment/pharmacy-verification", headers=onc_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Verified", "checklist": _FULL_CHECKLIST,
    })
    assert denied.status_code == 403


def test_query_requires_reason_code_and_message(client, onc_headers, pharm_a_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, _ = _signed_order_with_drug_lines(client, onc_headers, patient_id)

    missing_reason = client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_a_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Query", "message": "Please clarify dose.",
    })
    assert missing_reason.status_code == 422

    queried = client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_a_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Query",
        "reason_code": "Dose clarification", "message": "Please confirm the cyclophosphamide dose.",
    })
    assert queried.status_code == 201, queried.text
    readiness = client.get(f"/api/cca/treatment/{order_id}/pharmacy-readiness?patient_id={patient_id}", headers=pharm_a_headers)
    assert readiness.json()["pharmacy_readiness"]["status"] == "Queried"


def test_oncologist_responds_to_query(client, onc_headers, pharm_a_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, _ = _signed_order_with_drug_lines(client, onc_headers, patient_id)
    verification_id = client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_a_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Query",
        "reason_code": "Allergy", "message": "Patient has a documented allergy on file -- please confirm.",
    }).json()["verification"]["id"]

    missing_action = client.post(f"/api/cca/treatment/pharmacy-verification/{verification_id}/respond", headers=onc_headers, json={})
    assert missing_action.status_code == 422

    responded = client.post(f"/api/cca/treatment/pharmacy-verification/{verification_id}/respond", headers=onc_headers, json={
        "response_action": "Confirmed no true allergy, documentation error.", "response_note": "Verified with patient directly.",
    })
    assert responded.status_code == 200, responded.text
    assert responded.json()["verification"]["resolved"] is True

    already_resolved = client.post(f"/api/cca/treatment/pharmacy-verification/{verification_id}/respond", headers=onc_headers, json={
        "response_action": "n/a",
    })
    assert already_resolved.status_code == 409


# ---------------------------------------------------------------------------
# Preparation
# ---------------------------------------------------------------------------

def test_preparation_requires_verified_decision_first(client, onc_headers, pharm_a_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, line_ids = _signed_order_with_drug_lines(client, onc_headers, patient_id)

    too_early = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-preparation", headers=pharm_a_headers, json={})
    assert too_early.status_code == 409

    client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_a_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Verified", "checklist": _FULL_CHECKLIST,
    })
    prepared = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-preparation", headers=pharm_a_headers, json={
        "batch_number": "BATCH-001", "expiry_date": "2027-01-01", "diluent": "Normal saline", "actual_volume": "250",
        "actual_volume_unit": "mL", "final_concentration": "2mg/mL", "stability_hours": 24,
    })
    assert prepared.status_code == 201, prepared.text
    assert prepared.json()["preparation"]["beyond_use_at"] is not None

    duplicate = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-preparation", headers=pharm_a_headers, json={})
    assert duplicate.status_code == 409


def test_wastage_requires_reason(client, onc_headers, pharm_a_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, line_ids = _signed_order_with_drug_lines(client, onc_headers, patient_id)
    client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_a_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Verified", "checklist": _FULL_CHECKLIST,
    })

    missing_reason = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-preparation", headers=pharm_a_headers, json={
        "wastage_amount": "5", "wastage_unit": "mL",
    })
    assert missing_reason.status_code == 422

    with_reason = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-preparation", headers=pharm_a_headers, json={
        "wastage_amount": "5", "wastage_unit": "mL", "wastage_reason": "Partial vial",
    })
    assert with_reason.status_code == 201, with_reason.text


# ---------------------------------------------------------------------------
# Double-check / Release
# ---------------------------------------------------------------------------

def test_release_requires_a_different_pharmacist_than_preparer(client, onc_headers, pharm_a_headers, pharm_b_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, line_ids = _signed_order_with_drug_lines(client, onc_headers, patient_id)
    client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_a_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Verified", "checklist": _FULL_CHECKLIST,
    })
    client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-preparation", headers=pharm_a_headers, json={})

    too_early = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[1]}/pharmacy-release", headers=pharm_a_headers, json={"label_verified": True})
    assert too_early.status_code == 409  # not prepared yet

    same_pharmacist = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-release", headers=pharm_a_headers, json={"label_verified": True})
    assert same_pharmacist.status_code == 409

    missing_label = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-release", headers=pharm_b_headers, json={})
    assert missing_label.status_code == 422

    released = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-release", headers=pharm_b_headers, json={
        "label_verified": True, "dispensed_to": "Day Care Chair 3", "manifest_no": "MAN-001",
    })
    assert released.status_code == 201, released.text
    assert released.json()["release"]["second_check_by"] == "pharmb@pharmacy-test.com"

    duplicate = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-release", headers=pharm_b_headers, json={"label_verified": True})
    assert duplicate.status_code == 409


def test_pharmacy_readiness_reaches_dispensed_only_once_all_lines_released(client, onc_headers, pharm_a_headers, pharm_b_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, line_ids = _signed_order_with_drug_lines(client, onc_headers, patient_id)
    assert len(line_ids) == 2
    client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_a_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Verified", "checklist": _FULL_CHECKLIST,
    })
    for line_id in line_ids:
        client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_id}/pharmacy-preparation", headers=pharm_a_headers, json={})

    client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-release", headers=pharm_b_headers, json={"label_verified": True})
    partial = client.get(f"/api/cca/treatment/{order_id}/pharmacy-readiness?patient_id={patient_id}", headers=pharm_a_headers)
    assert partial.json()["pharmacy_readiness"]["status"] == "Preparing"

    client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[1]}/pharmacy-release", headers=pharm_b_headers, json={"label_verified": True})
    complete = client.get(f"/api/cca/treatment/{order_id}/pharmacy-readiness?patient_id={patient_id}", headers=pharm_a_headers)
    assert complete.json()["pharmacy_readiness"]["status"] == "Dispensed"


# ---------------------------------------------------------------------------
# Day Care gate
# ---------------------------------------------------------------------------

def test_day_care_cannot_start_medication_until_pharmacy_fully_releases_the_order(client, onc_headers, pharm_a_headers, pharm_b_headers, nurse_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id, line_ids = _signed_order_with_drug_lines(client, onc_headers, patient_id)

    added = client.post("/api/cca/treatment/medications", headers=nurse_headers, json={
        "patient_id": patient_id, "order_id": order_id, "medication_name": "Doxorubicin", "sequence_no": 1,
        "category": "Premedication",  # category is irrelevant to this order-level gate; avoids the
                                       # separate per-drug independent-verification gate (Batch 3)
    })
    admin_id = added.json()["medication"]["id"]

    blocked = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert blocked.status_code == 422
    assert "pharmacy" in blocked.text.lower()

    client.post("/api/cca/treatment/pharmacy-verification", headers=pharm_a_headers, json={
        "patient_id": patient_id, "order_id": order_id, "decision": "Verified", "checklist": _FULL_CHECKLIST,
    })
    for line_id in line_ids:
        client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_id}/pharmacy-preparation", headers=pharm_a_headers, json={})

    # Still blocked -- only one of two lines released so far.
    client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[0]}/pharmacy-release", headers=pharm_b_headers, json={"label_verified": True})
    still_blocked = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert still_blocked.status_code == 422

    client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_ids[1]}/pharmacy-release", headers=pharm_b_headers, json={"label_verified": True})
    allowed = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["medication"]["status"] == "InProgress"


def test_day_care_ungated_for_orders_with_no_drug_lines(client, onc_headers, nurse_headers, db_session, oncologist):
    """Backward compatibility: an order with no regimen linked (Batch 1's optional path) has
    no drug lines and must behave exactly as before Batch 2 -- no pharmacy gate at all."""
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})
    order_id = client.post("/api/cca/treatment-orders", headers=onc_headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id,
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=onc_headers)

    admin_id = client.post("/api/cca/treatment/medications", headers=nurse_headers, json={
        "patient_id": patient_id, "order_id": order_id, "medication_name": "Paracetamol", "sequence_no": 1,
        "category": "Premedication",
    }).json()["medication"]["id"]

    started = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert started.status_code == 200, started.text
