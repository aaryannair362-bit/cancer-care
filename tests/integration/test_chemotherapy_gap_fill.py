"""
Core Oncology 4 Sections gap-fill, Chemotherapy items 3.1 (protocol version snapshot),
3.2 (real day_no capture), 3.5 (emergency-standby instructions), 3.6 (per-drug extravasation
attribution), and 3.10 (read-only pharmacy-readiness indicator).

Never tests dose computation -- planned_dose/patient_calculated_dose/actual_dose are always
clinician-typed strings, stored verbatim (standing repo rule).
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CCAOrder, CCAResult


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@chemo-gap-test.com", role="CCAMedicalOncologist")


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


@pytest.fixture
def headers(auth_headers, oncologist):
    return auth_headers(oncologist)


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def _create_regimen(client, headers, name="FOLFOX Gap-Fill"):
    created = client.post("/api/cca/regimens", headers=headers, json={
        "name": name, "cancer_indication": "Colorectal Cancer", "number_of_cycles": 6,
        "emergency_standby_instructions": "Hydrocortisone 100mg IV and diphenhydramine 25mg IV on standby for hypersensitivity.",
        "drug_lines": [{"generic_name": "Oxaliplatin", "dose_basis": "mg_m2", "standard_protocol_dose": "85 mg/m2", "route": "IV"}],
    })
    assert created.status_code == 201, created.text
    return created.json()["regimen"]["id"], created.json()["regimen"]


def _signed_plan(client, headers, patient_id, **overrides):
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id, **overrides}).json()["treatment_plan"]["id"]
    sign_res = client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    assert sign_res.status_code == 200, sign_res.text
    return plan_id


def test_regimen_carries_emergency_standby_instructions_through_to_order(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    regimen_id, regimen = _create_regimen(client, headers)
    assert regimen["emergency_standby_instructions"].startswith("Hydrocortisone")

    plan_id = _signed_plan(client, headers, patient_id, regimen_id=regimen_id)
    order = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id})
    assert order.status_code == 200, order.text
    supportive_care = order.json()["treatment_order"]["supportive_care"]
    assert supportive_care["emergency_standby_instructions"].startswith("Hydrocortisone")


def test_order_drug_line_snapshots_protocol_version(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    regimen_id, regimen = _create_regimen(client, headers)
    assert regimen["version"] == "1.0"
    plan_id = _signed_plan(client, headers, patient_id, regimen_id=regimen_id)

    order = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id})
    line = order.json()["treatment_order"]["drug_lines"][0]
    assert line["protocol_version"] == "1.0"


def test_drug_line_accepts_patient_calculated_dose_and_order_time_fields(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    regimen_id, _ = _create_regimen(client, headers)
    plan_id = _signed_plan(client, headers, patient_id, regimen_id=regimen_id)
    order = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id}).json()["treatment_order"]
    line_id = order["drug_lines"][0]["id"]

    updated = client.patch(f"/api/cca/treatment-orders/{order['id']}/drug-lines/{line_id}", headers=headers, json={
        "patient_calculated_dose": "153mg (BSA 1.8 m2 x 85mg/m2)",
        "diluent": "D5W", "volume": "250mL", "concentration": "0.6 mg/mL",
        "infusion_rate_duration": "2 hours", "special_instructions": "Protect from light.",
        "dose_rounding_note": "Rounded down to nearest 5mg per pharmacy convention.",
    })
    assert updated.status_code == 200, updated.text
    line = updated.json()["drug_line"]
    assert line["patient_calculated_dose"] == "153mg (BSA 1.8 m2 x 85mg/m2)"
    assert line["diluent"] == "D5W"
    assert line["dose_rounding_note"].startswith("Rounded down")


def test_day_no_defaults_to_one_but_accepts_override_on_sign(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    signed = client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={"day_no": 8})
    assert signed.status_code == 200, signed.text

    assessment = client.get(f"/api/cca/treatment/day-assessment?patient_id={patient_id}", headers=headers)
    assert assessment.status_code == 200
    # cycle_info reads off completed_sessions + 1, not day_no directly -- the real assertion
    # that day_no actually persisted is the session row itself.
    from app.models_cca import TreatmentSession
    session = db_session.query(TreatmentSession).filter(TreatmentSession.treatment_plan_id == plan_id).first()
    assert session.day_no == 8


def test_extravasation_can_be_attributed_to_a_specific_administration(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    regimen_id, _ = _create_regimen(client, headers)
    plan_id = _signed_plan(client, headers, patient_id, regimen_id=regimen_id)
    order = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id}).json()["treatment_order"]
    order_id = order["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers, json={})

    admin_res = client.post("/api/cca/treatment/medications", headers=headers, json={
        "patient_id": patient_id, "order_id": order_id, "medication_name": "Oxaliplatin", "dose": "85mg/m2",
    })
    assert admin_res.status_code == 200, admin_res.text
    admin_id = admin_res.json()["medication"]["id"]

    extrav = client.post("/api/cca/treatment/extravasation", headers=headers, json={
        "patient_id": patient_id, "order_id": order_id, "administration_id": admin_id,
        "agent": "Oxaliplatin", "symptoms": "Swelling and pain at IV site.",
    })
    assert extrav.status_code == 200, extrav.text
    assert extrav.json()["extravasation"]["administration_id"] == admin_id


def test_pharmacy_readiness_indicator_is_informational_only(client, headers, db_session, oncologist):
    """Item 3.10 -- read-only, never gates record_clearance_decision (user's explicit
    instruction to leave that flow's status semantics unchanged)."""
    patient_id = _patient_id(db_session, oncologist.organization_id)
    regimen_id, _ = _create_regimen(client, headers)
    plan_id = _signed_plan(client, headers, patient_id, regimen_id=regimen_id)
    order = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id}).json()["treatment_order"]
    order_id = order["id"]
    sign_res = client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers, json={})
    assert sign_res.status_code == 200, sign_res.text

    assessment = client.get(f"/api/cca/treatment/day-assessment?patient_id={patient_id}", headers=headers)
    assert assessment.status_code == 200, assessment.text
    readiness = assessment.json()["pharmacy_readiness"]
    assert len(readiness) == 1
    assert readiness[0]["status"] == "NotStarted"
    assert readiness[0]["generic_name"] == "Oxaliplatin"

    # Clearance decision still succeeds regardless of pharmacy readiness -- no gate added.
    clearance = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "CLEARED", "reason": "Labs within range.",
    })
    assert clearance.status_code == 200, clearance.text


def test_day_assessment_lab_parameters_reflects_real_verified_lab_results(client, headers, db_session, oncologist):
    """Day Care/Infusion gap review (Laboratory Integration): this used to always return an
    empty lab_parameters array plus a hardcoded 'not yet connected' placeholder, regardless of
    whether real verified lab results existed. Only a Finalized (verified), non-superseded LAB
    result should appear -- a Draft one must not."""
    patient_id = _patient_id(db_session, oncologist.organization_id)

    no_labs_yet = client.get(f"/api/cca/treatment/day-assessment?patient_id={patient_id}", headers=headers)
    assert no_labs_yet.status_code == 200
    assert no_labs_yet.json()["lab_parameters"] == []
    assert "no verified laboratory results" in no_labs_yet.json()["lab_parameters_note"].lower()

    order = CCAOrder(patient_id=patient_id, order_type="LAB", item_name="CBC", clinical_indication="Pre-treatment workup.")
    db_session.add(order)
    db_session.flush()
    draft_result = CCAResult(order_id=order.id, patient_id=patient_id, result_type="LAB", title="CBC", findings_text="Draft, not yet verified.", report_status="Draft")
    finalized_result = CCAResult(
        order_id=order.id, patient_id=patient_id, result_type="LAB", title="Hemoglobin",
        findings_text="Hb 11.2 g/dL - within acceptable range.", report_status="Finalized",
        finalized_by="labtech@x.com", is_critical=False,
    )
    db_session.add_all([draft_result, finalized_result])
    db_session.commit()

    with_labs = client.get(f"/api/cca/treatment/day-assessment?patient_id={patient_id}", headers=headers)
    assert with_labs.status_code == 200
    params = with_labs.json()["lab_parameters"]
    assert len(params) == 1
    assert params[0]["test_name"] == "Hemoglobin"
    assert params[0]["verified_by"] == "labtech@x.com"
    assert with_labs.json()["lab_parameters_note"] is None
