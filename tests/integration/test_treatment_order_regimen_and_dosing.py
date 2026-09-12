"""
Product 1 vs Product 2 Functional Gap Report, Batch 1: Systemic Treatment/Chemotherapy
Orders -- Regimen selection on TreatmentPlan, the structured per-drug dosing panel
(TreatmentOrderDrugLine) auto-seeded from the regimen at order creation, supportive-care
carry-through, and order revision/dose-modification linkage.

Never tests dose computation -- there is none; planned_dose is always a clinician-typed
string, stored verbatim.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@regimen-dosing-test.com", role="CCAMedicalOncologist")


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


def _create_regimen(client, headers, name="AC-T Dose-Dense"):
    created = client.post("/api/cca/regimens", headers=headers, json={
        "name": name, "cancer_indication": "Breast Cancer", "number_of_cycles": 4,
        "premedications": "Ondansetron 8mg IV, Dexamethasone 12mg IV",
        "hydration": "Normal saline 500mL pre-infusion",
        "supportive_therapy": "Pegfilgrastim 6mg SC day 2",
        "drug_lines": [
            {"generic_name": "Doxorubicin", "dose_basis": "mg_m2", "standard_protocol_dose": "60 mg/m2", "route": "IV"},
            {"generic_name": "Cyclophosphamide", "dose_basis": "mg_m2", "standard_protocol_dose": "600 mg/m2", "route": "IV"},
        ],
    })
    assert created.status_code == 201, created.text
    return created.json()["regimen"]["id"]


def _signed_plan(client, headers, patient_id, **overrides):
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id, **overrides}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    return plan_id


# ---------------------------------------------------------------------------
# Regimen selection on TreatmentPlan
# ---------------------------------------------------------------------------

def test_plan_created_with_regimen_prefills_protocol_name(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    regimen_id = _create_regimen(client, headers)

    plan = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "regimen_id": regimen_id,
    })
    assert plan.status_code == 200, plan.text
    body = plan.json()["treatment_plan"]
    assert body["regimen_id"] == regimen_id
    assert body["regimen_name"] == "AC-T Dose-Dense"
    assert body["protocol_name"] == "AC-T Dose-Dense"


def test_explicit_protocol_name_overrides_regimen_prefill(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    regimen_id = _create_regimen(client, headers)

    plan = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "regimen_id": regimen_id, "protocol_name": "Custom Protocol Label",
    })
    assert plan.json()["treatment_plan"]["protocol_name"] == "Custom Protocol Label"


def test_plan_without_regimen_still_works(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "protocol_name": "Free text only",
    })
    assert plan.status_code == 200, plan.text
    assert plan.json()["treatment_plan"]["regimen_id"] is None


def test_invalid_regimen_id_rejected(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rejected = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "regimen_id": 999999,
    })
    assert rejected.status_code == 422


# ---------------------------------------------------------------------------
# Structured dosing panel auto-seeded from the regimen
# ---------------------------------------------------------------------------

def test_order_auto_seeds_drug_lines_from_plan_regimen(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    regimen_id = _create_regimen(client, headers)
    plan_id = _signed_plan(client, headers, patient_id, regimen_id=regimen_id)

    order = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id})
    assert order.status_code == 200, order.text
    order_id = order.json()["treatment_order"]["id"]
    drug_lines = order.json()["treatment_order"]["drug_lines"]
    assert len(drug_lines) == 2
    names = [l["generic_name"] for l in drug_lines]
    assert "Doxorubicin" in names and "Cyclophosphamide" in names
    doxo = next(l for l in drug_lines if l["generic_name"] == "Doxorubicin")
    assert doxo["standard_protocol_dose"] == "60 mg/m2"
    assert doxo["planned_dose"] is None  # never pre-filled with a computed value

    supportive_care = order.json()["treatment_order"]["supportive_care"]
    assert supportive_care["premedications"] == "Ondansetron 8mg IV, Dexamethasone 12mg IV"
    assert supportive_care["hydration"] == "Normal saline 500mL pre-infusion"


def test_order_without_regimen_has_no_drug_lines(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id)

    order = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id})
    assert order.json()["treatment_order"]["drug_lines"] == []
    assert order.json()["treatment_order"]["supportive_care"] is None


def test_clinician_can_edit_planned_dose_and_add_remove_lines_while_draft(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    regimen_id = _create_regimen(client, headers)
    plan_id = _signed_plan(client, headers, patient_id, regimen_id=regimen_id)
    order = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id}).json()["treatment_order"]
    order_id = order["id"]
    line_id = order["drug_lines"][0]["id"]

    updated = client.patch(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_id}", headers=headers, json={
        "planned_dose": "115mg (dose reduced per clinician judgment)",
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["drug_line"]["planned_dose"] == "115mg (dose reduced per clinician judgment)"

    added = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines", headers=headers, json={
        "generic_name": "Ondansetron", "category": "Premedication", "planned_dose": "8mg IV",
    })
    assert added.status_code == 201, added.text
    new_line_id = added.json()["drug_line"]["id"]

    listed = client.get(f"/api/cca/treatment-orders/{order_id}/drug-lines", headers=headers)
    assert len(listed.json()["results"]) == 3

    deleted = client.delete(f"/api/cca/treatment-orders/{order_id}/drug-lines/{new_line_id}", headers=headers)
    assert deleted.status_code == 200
    listed_after = client.get(f"/api/cca/treatment-orders/{order_id}/drug-lines", headers=headers)
    assert len(listed_after.json()["results"]) == 2


def test_drug_lines_locked_once_order_is_signed(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    regimen_id = _create_regimen(client, headers)
    plan_id = _signed_plan(client, headers, patient_id, regimen_id=regimen_id)
    order = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id}).json()["treatment_order"]
    order_id = order["id"]
    line_id = order["drug_lines"][0]["id"]

    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)

    locked_edit = client.patch(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_id}", headers=headers, json={"planned_dose": "999mg"})
    assert locked_edit.status_code == 409

    locked_add = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines", headers=headers, json={"generic_name": "Extra Drug"})
    assert locked_add.status_code == 409

    locked_delete = client.delete(f"/api/cca/treatment-orders/{order_id}/drug-lines/{line_id}", headers=headers)
    assert locked_delete.status_code == 409

    # Reading is still allowed once signed.
    still_readable = client.get(f"/api/cca/treatment-orders/{order_id}/drug-lines", headers=headers)
    assert still_readable.status_code == 200
    assert len(still_readable.json()["results"]) == 2


def test_drug_line_requires_generic_name(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id)
    order_id = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id}).json()["treatment_order"]["id"]

    rejected = client.post(f"/api/cca/treatment-orders/{order_id}/drug-lines", headers=headers, json={})
    assert rejected.status_code == 422


# ---------------------------------------------------------------------------
# Order revision / dose-modification linkage
# ---------------------------------------------------------------------------

def test_order_revision_linkage(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id, planned_sessions=2)
    first_order = client.post("/api/cca/treatment-orders", headers=headers, json={"patient_id": patient_id, "treatment_plan_id": plan_id}).json()["treatment_order"]
    first_order_id = first_order["id"]
    client.post(f"/api/cca/treatment-orders/{first_order_id}/sign", headers=headers)
    client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "order_id": first_order_id, "decision": "CLEARED_DOSE_REDUCTION",
        "reason": "Grade 2 neuropathy, reduce dose for cycle 2.",
    })

    second_order = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id,
        "supersedes_id": first_order_id, "revision_reason": "Dose reduced for Grade 2 neuropathy.",
        "dose_modification_percent": "75%",
    })
    assert second_order.status_code == 200, second_order.text
    body = second_order.json()["treatment_order"]
    assert body["supersedes_id"] == first_order_id
    assert body["revision_reason"] == "Dose reduced for Grade 2 neuropathy."
    assert body["dose_modification_percent"] == "75%"


def test_invalid_supersedes_id_rejected(client, headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id)

    rejected = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id, "supersedes_id": 999999,
    })
    assert rejected.status_code == 422
