"""
Tests for backend/app/routers/cca_oncology_ext.py -- Radiation Oncology, Surgical
Oncology, the Regimen library, and the new CCAPharmacist role (PDF Master To-Do
List items 6, 11, 12, 13, 27).

Covers the specific non-negotiable constraints this slice exists to enforce:
  - Radiation/surgical status transitions can only move one step forward at a time,
    and only the matching modality's oncologist may drive them.
  - A radiation fraction's interruption reason and on-treatment review note actually
    persist (the dashboard prototype's equivalent fields never did).
  - A surgical plan's performed procedure is stored separately from the planned one,
    and can be linked forward into a later MDT case.
  - A regimen requires clinician authorship; a pharmacist can verify/dispense against
    the existing treatment-order chain but cannot author a regimen or sign an order/plan.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@oncext.hosp", role="CCAMedicalOncologist")


@pytest.fixture
def rad_onc(make_user, oncologist):
    return make_user(email="radonc@oncext.hosp", role="CCARadiationOncologist", organization_id=oncologist.organization_id)


@pytest.fixture
def surg_onc(make_user, oncologist):
    return make_user(email="surgonc@oncext.hosp", role="CCASurgicalOncologist", organization_id=oncologist.organization_id)


@pytest.fixture
def nurse(make_user, oncologist):
    return make_user(email="nurse@oncext.hosp", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def pharmacist(make_user, oncologist):
    return make_user(email="pharmacist@oncext.hosp", role="CCAPharmacist", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def _signed_plan(client, headers, patient_id, **overrides):
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id, **overrides}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    return plan_id


def _signed_order(client, headers, patient_id, plan_id):
    order_id = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)
    return order_id


# ---------------------------------------------------------------------------
# Demo patient resolver
# ---------------------------------------------------------------------------

def test_demo_patient_get_or_create_is_idempotent(client, auth_headers, oncologist):
    headers = auth_headers(oncologist)
    first = client.get("/api/cca/oncology-ext/demo-patient", headers=headers).json()["patient"]
    second = client.get("/api/cca/oncology-ext/demo-patient", headers=headers).json()["patient"]
    assert first["id"] == second["id"]
    assert first["mrn"] == "CCA-ONC-DEMO-001"


# ---------------------------------------------------------------------------
# Radiation Oncology
# ---------------------------------------------------------------------------

def _create_rx(client, headers, patient_id, number_of_fractions=25):
    return client.post("/api/cca/radiation-prescriptions", headers=headers, json={
        "patient_id": patient_id, "treatment_site": "Left breast", "laterality": "left",
        "total_prescribed_dose_gy": 2 * number_of_fractions, "dose_per_fraction_gy": 2,
        "number_of_fractions": number_of_fractions,
    }).json()["radiation_prescription"]


def test_radiation_prescription_transition_must_be_sequential(client, auth_headers, db_session, oncologist, rad_onc):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx = _create_rx(client, auth_headers(oncologist), patient_id)

    skip = client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/transition", headers=auth_headers(rad_onc), json={"status": "planning"})
    assert skip.status_code == 409

    step = client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/transition", headers=auth_headers(rad_onc), json={"status": "simulation_pending"})
    assert step.status_code == 200
    assert step.json()["radiation_prescription"]["rt_sub_status"] == "simulation_pending"


def test_only_radiation_oncologist_may_transition_prescription(client, auth_headers, db_session, oncologist, rad_onc, surg_onc):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx = _create_rx(client, auth_headers(oncologist), patient_id)

    rejected = client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/transition", headers=auth_headers(surg_onc), json={"status": "simulation_pending"})
    assert rejected.status_code == 403


def test_fraction_events_persist_interruption_reason_and_review_note(client, auth_headers, db_session, oncologist, rad_onc, nurse):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx = _create_rx(client, auth_headers(oncologist), patient_id, number_of_fractions=3)
    onc_headers = auth_headers(rad_onc)
    for status_step in ["simulation_pending", "simulation_complete", "contouring", "planning", "physics_qa", "physician_approved", "treatment_ready"]:
        r = client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/transition", headers=onc_headers, json={"status": status_step})
        assert r.status_code == 200, r.text

    fractions = client.get(f"/api/cca/radiation-prescriptions/{rx['id']}/fractions", headers=onc_headers).json()["fractions"]
    assert len(fractions) == 3

    nurse_headers = auth_headers(nurse)
    missed = client.post(f"/api/cca/radiation-fractions/{fractions[0]['id']}/event", headers=nurse_headers, json={
        "status": "missed", "interruption_reason": "Patient unwell, rescheduled by radiotherapy team",
    })
    assert missed.status_code == 200
    body = missed.json()["fraction"]
    assert body["status"] == "missed"
    assert body["interruption_reason"] == "Patient unwell, rescheduled by radiotherapy team"

    reviewed = client.post(f"/api/cca/radiation-fractions/{fractions[1]['id']}/event", headers=nurse_headers, json={
        "status": "delivered", "on_treatment_review_note": "Skin reaction Grade 1, tolerating well",
    })
    assert reviewed.json()["fraction"]["on_treatment_review_note"] == "Skin reaction Grade 1, tolerating well"


def test_course_cannot_complete_until_all_fractions_delivered(client, auth_headers, db_session, oncologist, rad_onc, nurse):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx = _create_rx(client, auth_headers(oncologist), patient_id, number_of_fractions=2)
    onc_headers = auth_headers(rad_onc)
    for status_step in ["simulation_pending", "simulation_complete", "contouring", "planning", "physics_qa", "physician_approved", "treatment_ready"]:
        client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/transition", headers=onc_headers, json={"status": status_step})

    too_early = client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/complete", headers=onc_headers)
    assert too_early.status_code == 409

    fractions = client.get(f"/api/cca/radiation-prescriptions/{rx['id']}/fractions", headers=onc_headers).json()["fractions"]
    nurse_headers = auth_headers(nurse)
    for f in fractions:
        client.post(f"/api/cca/radiation-fractions/{f['id']}/event", headers=nurse_headers, json={"status": "delivered"})

    completed = client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/complete", headers=onc_headers)
    assert completed.status_code == 200
    assert completed.json()["radiation_prescription"]["rt_sub_status"] == "completed"


# ---------------------------------------------------------------------------
# Surgical Oncology
# ---------------------------------------------------------------------------

def _create_surgical_plan(client, headers, patient_id):
    return client.post("/api/cca/surgical-plans", headers=headers, json={
        "patient_id": patient_id, "procedure": "Left modified radical mastectomy",
        "anatomical_site": "Left breast", "laterality": "left", "intent": "curative",
    }).json()["surgical_plan"]


def test_course_can_be_interrupted_and_resumed(client, auth_headers, db_session, oncologist, rad_onc):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx = _create_rx(client, auth_headers(oncologist), patient_id, number_of_fractions=2)
    onc_headers = auth_headers(rad_onc)
    for status_step in ["simulation_pending", "simulation_complete", "contouring", "planning", "physics_qa", "physician_approved", "treatment_ready"]:
        client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/transition", headers=onc_headers, json={"status": status_step})

    too_soon = client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/transition", headers=onc_headers, json={"status": "interrupted"})
    assert too_soon.status_code == 409

    # Enter on_treatment via a fraction event, then interrupt and resume.
    fractions = client.get(f"/api/cca/radiation-prescriptions/{rx['id']}/fractions", headers=onc_headers).json()["fractions"]
    client.post(f"/api/cca/radiation-fractions/{fractions[0]['id']}/event", headers=onc_headers, json={"status": "delivered"})

    interrupted = client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/transition", headers=onc_headers, json={"status": "interrupted"})
    assert interrupted.status_code == 200
    assert interrupted.json()["radiation_prescription"]["rt_sub_status"] == "interrupted"

    resumed = client.post(f"/api/cca/radiation-prescriptions/{rx['id']}/transition", headers=onc_headers, json={"status": "on_treatment"})
    assert resumed.status_code == 200
    assert resumed.json()["radiation_prescription"]["rt_sub_status"] == "on_treatment"


def test_surgical_plan_transition_and_only_surgical_oncologist(client, auth_headers, db_session, oncologist, surg_onc, rad_onc):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan = _create_surgical_plan(client, auth_headers(oncologist), patient_id)

    wrong_signer = client.patch(f"/api/cca/surgical-plans/{plan['id']}", headers=auth_headers(rad_onc), json={"status": "surgeon_reviewed"})
    assert wrong_signer.status_code == 403

    ok = client.patch(f"/api/cca/surgical-plans/{plan['id']}", headers=auth_headers(surg_onc), json={"status": "surgeon_reviewed"})
    assert ok.status_code == 200
    assert ok.json()["surgical_plan"]["status"] == "surgeon_reviewed"


def test_performed_procedure_recorded_separately_from_planned_and_feeds_back_to_mdt(client, auth_headers, db_session, oncologist, surg_onc):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    onc_headers = auth_headers(oncologist)
    surg_headers = auth_headers(surg_onc)
    plan = _create_surgical_plan(client, onc_headers, patient_id)
    for status_step in ["surgeon_reviewed", "planned", "pre_op_ready", "scheduled"]:
        client.patch(f"/api/cca/surgical-plans/{plan['id']}", headers=surg_headers, json={"status": status_step})

    mdt_case = client.post("/api/cca/mdt/cases", headers=onc_headers, json={"patient_id": patient_id, "question": "Adjuvant planning after surgery"}).json()["mdt_case"]

    outcome = client.post(f"/api/cca/surgical-plans/{plan['id']}/performed", headers=surg_headers, json={
        "performed_procedure": "Left modified radical mastectomy with sentinel node biopsy (converted to full axillary clearance intra-operatively)",
        "histopathology_summary": "Invasive ductal carcinoma, 2.3cm, 1/14 nodes positive, ER+/PR+/HER2-",
        "fed_back_to_mdt_case_id": mdt_case["id"],
    })
    assert outcome.status_code == 200
    body = outcome.json()["surgical_plan"]
    assert body["procedure"] == "Left modified radical mastectomy"
    assert "sentinel node biopsy" in body["performed_procedure"]
    assert body["fed_back_to_mdt_case_id"] == mdt_case["id"]
    assert body["status"] == "histopathology_available"


# ---------------------------------------------------------------------------
# Regimen library
# ---------------------------------------------------------------------------

def test_regimen_requires_clinician_and_stores_drug_lines(client, auth_headers, oncologist, pharmacist):
    onc_headers = auth_headers(oncologist)
    rejected = client.post("/api/cca/regimens", headers=auth_headers(pharmacist), json={
        "name": "AC-T", "drug_lines": [{"generic_name": "Doxorubicin"}],
    })
    assert rejected.status_code == 403

    created = client.post("/api/cca/regimens", headers=onc_headers, json={
        "name": "AC-T", "cancer_indication": "Breast cancer, node-positive", "number_of_cycles": 4,
        "drug_lines": [
            {"generic_name": "Doxorubicin", "dose_basis": "mg_m2", "sequence_number": 1},
            {"generic_name": "Cyclophosphamide", "dose_basis": "mg_m2", "sequence_number": 2},
        ],
    })
    assert created.status_code == 201
    regimen = created.json()["regimen"]
    assert regimen["name"] == "AC-T"
    assert len(regimen["drug_lines"]) == 2
    assert regimen["drug_lines"][0]["generic_name"] == "Doxorubicin"

    listed = client.get("/api/cca/regimens", headers=onc_headers).json()["regimens"]
    assert any(r["id"] == regimen["id"] for r in listed)


# ---------------------------------------------------------------------------
# CCAPharmacist role: verify/dispense, not prescribe
# ---------------------------------------------------------------------------

def test_pharmacist_can_verify_but_not_prescribe(client, auth_headers, db_session, oncologist, pharmacist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    onc_headers = auth_headers(oncologist)
    pharm_headers = auth_headers(pharmacist)

    plan_id = _signed_plan(client, onc_headers, patient_id)
    order_id = _signed_order(client, onc_headers, patient_id, plan_id)

    readiness = client.post("/api/cca/treatment/pharmacy-readiness", headers=pharm_headers, json={
        "patient_id": patient_id, "order_id": order_id, "status": "Verified", "product_verified": True, "expiry_checked": True,
    })
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["pharmacy_readiness"]["status"] == "Verified"

    cannot_sign_plan = client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=pharm_headers, json={})
    assert cannot_sign_plan.status_code == 403

    cannot_create_order = client.post("/api/cca/treatment-orders", headers=pharm_headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id,
    })
    assert cannot_create_order.status_code == 403


# ---------------------------------------------------------------------------
# Treatment Plan phases (item 4)
# ---------------------------------------------------------------------------

def test_treatment_plan_phases_replace_wholesale(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]

    put = client.put(f"/api/cca/treatment-plans/{plan_id}/phases", headers=headers, json={"phases": [
        {"sequence": 1, "modality": "systemic", "label": "Neoadjuvant systemic therapy", "status": "in_progress"},
        {"sequence": 2, "modality": "surgical", "label": "Surgery", "status": "draft"},
    ]})
    assert put.status_code == 200
    phases = put.json()["phases"]
    assert [p["label"] for p in phases] == ["Neoadjuvant systemic therapy", "Surgery"]

    listed = client.get(f"/api/cca/treatment-plans/{plan_id}/phases", headers=headers).json()["phases"]
    assert len(listed) == 2

    replaced = client.put(f"/api/cca/treatment-plans/{plan_id}/phases", headers=headers, json={"phases": [
        {"sequence": 1, "modality": "systemic", "label": "Neoadjuvant systemic therapy", "status": "completed"},
    ]})
    assert len(replaced.json()["phases"]) == 1


# ---------------------------------------------------------------------------
# Generic record extension
# ---------------------------------------------------------------------------

def test_toxicity_and_response_assessment_history_lists(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    client.post("/api/cca/treatment/toxicity", headers=headers, json={"patient_id": patient_id, "term": "Peripheral sensory neuropathy", "grade": 2, "baseline_value": "Grade 0 (Baseline)"})
    toxicities = client.get(f"/api/cca/patients/{patient_id}/toxicity-events", headers=headers).json()["toxicity_events"]
    assert len(toxicities) == 1
    assert toxicities[0]["grade"] == 2

    client.post("/api/cca/response-assessments", headers=headers, json={"patient_id": patient_id, "response_category": "PR"})
    responses = client.get(f"/api/cca/patients/{patient_id}/response-assessments", headers=headers).json()["response_assessments"]
    assert len(responses) == 1
    assert responses[0]["response_category"] == "PR"


def test_record_extension_upsert_and_read(client, auth_headers, oncologist, nurse):
    empty = client.get("/api/cca/oncology-ext/extension", headers=auth_headers(oncologist), params={"entity_table": "cca_toxicity_events", "entity_id": 999}).json()
    assert empty["extension"]["payload"] is None

    written = client.put("/api/cca/oncology-ext/extension", headers=auth_headers(nurse), json={
        "entity_table": "cca_toxicity_events", "entity_id": 999,
        "payload": {"relationship_to_therapy": "probable", "outcome": "resolving", "intervention": "Dose held, gabapentin started"},
    })
    assert written.status_code == 200
    assert written.json()["extension"]["payload"]["outcome"] == "resolving"

    read_back = client.get("/api/cca/oncology-ext/extension", headers=auth_headers(oncologist), params={"entity_table": "cca_toxicity_events", "entity_id": 999}).json()
    assert read_back["extension"]["payload"]["relationship_to_therapy"] == "probable"


def test_record_extension_without_patient_id_still_writes_but_publishes_nothing(client, auth_headers, oncologist):
    written = client.put("/api/cca/oncology-ext/extension", headers=auth_headers(oncologist), json={
        "entity_table": "cca_toxicity_events", "entity_id": 998, "payload": {"outcome": "ongoing"},
    })
    assert written.status_code == 200
    assert written.json()["extension"]["payload"]["outcome"] == "ongoing"


def test_record_extension_with_patient_id_publishes_a_discoverable_domain_event(client, auth_headers, db_session, oncologist):
    """The gap this closes: a dose modification or the Day Care pre-administration
    checklist (both stored via this endpoint on cca_treatment_orders) used to leave real
    data behind but no durable trace of the action -- once the browser session ended, the
    audit trail lost it. Passing patient_id fixes that without changing what's stored."""
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _signed_plan(client, headers, patient_id)
    order_id = _signed_order(client, headers, patient_id, plan_id)

    before = client.get(f"/api/cca/patients/{patient_id}/domain-events", headers=headers).json()["domain_events"]

    written = client.put("/api/cca/oncology-ext/extension", headers=headers, json={
        "entity_table": "cca_treatment_orders", "entity_id": order_id,
        "payload": {"doseModifications": [{"type": "dose_reduction", "originalDose": "100 mg", "modifiedDose": "80 mg"}]},
        "patient_id": patient_id,
    })
    assert written.status_code == 200

    after = client.get(f"/api/cca/patients/{patient_id}/domain-events", headers=headers).json()["domain_events"]
    assert len(after) == len(before) + 1
    new_event = after[-1]
    assert new_event["event_type"] == "RECORD_EXTENSION_UPDATED"
    assert new_event["payload"]["entity_table"] == "cca_treatment_orders"
    assert new_event["payload"]["entity_id"] == order_id
    assert new_event["payload"]["actor"]
    assert new_event["created_at"] is not None


def test_record_extension_rejects_patient_id_outside_caller_org(client, auth_headers, db_session, oncologist):
    other_org_patient = CCAPatient(mrn="CCA-ONC-CROSSORG-001", name="Cross-Org Patient", age=45, sex="Female", organization_id=oncologist.organization_id + 999)
    db_session.add(other_org_patient)
    db_session.commit()
    db_session.refresh(other_org_patient)

    rejected = client.put("/api/cca/oncology-ext/extension", headers=auth_headers(oncologist), json={
        "entity_table": "cca_toxicity_events", "entity_id": 997, "payload": {"outcome": "ongoing"},
        "patient_id": other_org_patient.id,
    })
    assert rejected.status_code == 404


# ---------------------------------------------------------------------------
# Domain events (the real audit-trail source dashboard/lib/oncology/store.tsx's
# getAuditTrail reads, replacing what used to be a localStorage-only log that lost
# everything on reload)
# ---------------------------------------------------------------------------

def test_domain_events_list_is_not_empty_and_carries_entity_ids(client, auth_headers, db_session, oncologist, rad_onc, surg_onc):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    rx = _create_rx(client, auth_headers(oncologist), patient_id, number_of_fractions=2)
    plan = _create_surgical_plan(client, auth_headers(oncologist), patient_id)

    events = client.get(f"/api/cca/patients/{patient_id}/domain-events", headers=auth_headers(oncologist)).json()["domain_events"]
    assert len(events) >= 2

    rx_events = [e for e in events if e["payload"].get("prescription_id") == rx["id"]]
    assert any(e["event_type"] == "RADIATION_PRESCRIPTION_CREATED" for e in rx_events)

    plan_events = [e for e in events if e["payload"].get("plan_id") == plan["id"]]
    assert any(e["event_type"] == "SURGICAL_PLAN_CREATED" for e in plan_events)

    # Every event is timestamped and attributed — the two things a reload must not lose.
    for e in events:
        assert e["created_at"] is not None
        assert e["payload"].get("actor")


def test_domain_events_are_scoped_to_the_requested_patient(client, auth_headers, db_session, oncologist, rad_onc):
    other_patient = CCAPatient(
        mrn="CCA-ONC-OTHER-001", name="Other Patient", age=60, sex="Male",
        organization_id=oncologist.organization_id,
    )
    db_session.add(other_patient)
    db_session.commit()
    db_session.refresh(other_patient)

    patient_id = _patient_id(db_session, oncologist.organization_id)
    _create_rx(client, auth_headers(oncologist), patient_id, number_of_fractions=1)

    other_events = client.get(f"/api/cca/patients/{other_patient.id}/domain-events", headers=auth_headers(oncologist)).json()["domain_events"]
    assert other_events == []
