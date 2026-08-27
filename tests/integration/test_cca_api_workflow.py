"""
Comprehensive Integration Tests for CCA Cancer Care AI OS.
Tests the complete 9-Act Live Demo Workflow through the FastAPI API Gateway:
- Act 1: Patient Header, Contextual Summary, Digital Consent
- Act 2: Document Ingestion, Extractions, 2-Click Provenance & Contradiction Resolution
- Act 3: Nurse Intake with DuBois BSA & ECOG
- Act 4: Doctor OPD Consultation & Note Finalisation
- Act 5: Investigation Orders, Result Simulation, Acknowledgement & Staging Attachment
- Act 6: Clinician Stage Confirmation & Guideline Readiness Flip
- Act 7: NEXUS 13-Section Clinical Brief & 1-Click MDT Package Formulation
- Act 8: Live Care Plan Pre-population, Versioning & Change Reason Validation
- Act 9: Treatment-Day Assessment, Toxicity Baseline Guard & 5 Clearance Exits
- Act 10: RECIST 1.1 Response Assessment & Demo Controller Controls

Every endpoint under /api/cca requires authentication and is scoped to the caller's
organization (see backend/app/routers/cca.py) -- all requests below carry a Doctor's
auth_headers, and a dedicated isolation test (test_cca_endpoints_require_auth_and_are_org_scoped)
pins down the two properties that used to be entirely missing: no token -> rejected,
another organization's patient -> 404, never leaked.
"""

from datetime import datetime

import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CCADocument, ClinicalFact, CCAEncounter, CCAOrder, CCAContradiction
from tests.conftest import mock_groq_json


@pytest.fixture
def doctor(make_user):
    return make_user(email="onc.doctor@ccahosp.com", role="Doctor")


@pytest.fixture
def admin(make_user, doctor):
    return make_user(email="onc.admin@ccahosp.com", role="Admin", organization_id=doctor.organization_id)


@pytest.fixture
def nurse(make_user, doctor):
    return make_user(email="onc.nurse@ccahosp.com", role="Nurse", organization_id=doctor.organization_id)


@pytest.fixture
def headers(auth_headers, doctor):
    return auth_headers(doctor)


@pytest.fixture
def admin_headers(auth_headers, admin):
    return auth_headers(admin)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, doctor):
    seed_cca_database(db_session, force_reset=False, organization_id=doctor.organization_id)
    db_session.commit()


def _demo_patient_id(db_session, org_id):
    patient = db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first()
    return patient.id


def _create_and_sign_treatment_plan(client, headers, patient_id, **overrides):
    """A Care Plan can only be built from an already-signed Treatment Plan (see
    routers/cca.py's create_care_plan) -- this drafts and signs one with the default
    systemic-chemotherapy modality, which routes through the Medical Oncologist/Doctor
    signer gate the `doctor` fixture satisfies."""
    body = {"patient_id": patient_id, **overrides}
    draft = client.post("/api/cca/treatment-plans", headers=headers, json=body)
    assert draft.status_code == 200, draft.text
    plan_id = draft.json()["treatment_plan"]["id"]
    signed = client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    assert signed.status_code == 200, signed.text
    return plan_id


def test_act_1_patient_header_and_contextual_summary(client, headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    res = client.get(f"/api/cca/patients/{patient_id}", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["patient"]["mrn"] == "CCA-2026-004417"
    assert data["patient"]["name"] == "Meera S. Nair"

    # Persistent Patient Header (96px) Engine Pills
    header = data["header"]
    pills = header["engine_pills"]
    assert "summary" in pills
    assert "journey" in pills
    assert "staging" in pills
    assert "guideline" in pills
    assert "nexus" in pills
    assert "care_plan" in pills
    assert header["open_contradictions_count"] == 1  # CTR-01 active

    # Contextual Summary (SCR-10) with Absence Vocabulary
    summary_res = client.get(f"/api/cca/patients/{patient_id}/summary?context=initial_consult", headers=headers)
    assert summary_res.status_code == 200
    blocks = summary_res.json()["blocks"]
    assert any(b["absenceState"] == "CONTRADICTED" for b in blocks)


def test_act_2_documents_extractions_provenance_and_contradiction_resolution(client, headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    # List documents
    docs_res = client.get(f"/api/cca/documents?patient_id={patient_id}", headers=headers)
    assert docs_res.status_code == 200
    docs = docs_res.json()["documents"]
    assert len(docs) >= 6

    # Get extractions for referral letter (Doc 1)
    doc_1_id = docs[0]["id"]
    ext_res = client.get(f"/api/cca/extractions/{doc_1_id}", headers=headers)
    assert ext_res.status_code == 200
    facts = ext_res.json()["facts"]
    assert len(facts) >= 1

    # 2-Click View Source Provenance Check (DSC-03)
    fact_id = facts[0]["id"]
    prov_res = client.get(f"/api/cca/clinical-facts/{fact_id}/provenance", headers=headers)
    assert prov_res.status_code == 200
    prov = prov_res.json()
    assert "bounding_box" in prov
    assert "document" in prov
    assert prov["document"]["filename"] == "referral_letter.pdf"

    # Check open contradiction CTR-01
    ctrs_res = client.get(f"/api/cca/patients/{patient_id}/contradictions", headers=headers)
    assert ctrs_res.status_code == 200
    ctrs = ctrs_res.json()["contradictions"]
    assert len(ctrs) == 1
    ctr_id = ctrs[0]["id"]
    assert ctrs[0]["status"] == "OPEN"

    # Attempting to accept a conflicted fact should fail with 422
    accept_blocked = client.post(f"/api/cca/verification/{fact_id}/accept", headers=headers)
    assert accept_blocked.status_code == 422

    # Resolve contradiction CTR-01 (Pathology confirms Right laterality)
    resolve_res = client.post(f"/api/cca/contradictions/{ctr_id}/disposition", headers=headers, json={
        "disposition": "CONFIRMED_RIGHT_LATERALITY",
        "note": "Right breast mass confirmed by surgical biopsy. Left laterality on referral noted as transcription error."
    })
    assert resolve_res.status_code == 200
    assert resolve_res.json()["contradiction"]["status"] == "RESOLVED"
    # Attribution is the real caller, never a hardcoded demo name.
    assert resolve_res.json()["contradiction"]["disposition"] == "CONFIRMED_RIGHT_LATERALITY"
    ctr_after = client.get(f"/api/cca/patients/{patient_id}/contradictions", headers=headers).json()["contradictions"][0]
    assert ctr_after["dispositioned_by"] == "onc.doctor@ccahosp.com"


def test_act_3_nurse_intake_and_bsa(client, headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)
    encounter = db_session.query(CCAEncounter).filter(CCAEncounter.patient_id == patient_id).first()
    encounter_id = encounter.id

    res = client.post(f"/api/cca/encounters/{encounter_id}/intake", headers=headers, json={
        "patient_id": patient_id,
        "height_cm": 158.0,
        "weight_kg": 64.0,
        "bp_systolic": 130,
        "bp_diastolic": 82,
        "heart_rate": 74,
        "temperature_c": 36.8,
        "oxygen_sat": 99,
        "ecog": 1,
        "karnofsky": 80,
        "pain_score": 0,
        "handoff_note": "Intake completed for Meera S. Nair."
    })
    assert res.status_code == 200
    intake = res.json()["intake"]
    assert intake["bsa"] == 1.65  # Exact DuBois formula
    assert intake["bmi"] == 25.64


def test_act_3_intake_rejects_missing_patient_id_and_non_numeric_vitals(client, headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)
    encounter = db_session.query(CCAEncounter).filter(CCAEncounter.patient_id == patient_id).first()

    missing_patient = client.post(f"/api/cca/encounters/{encounter.id}/intake", headers=headers, json={
        "height_cm": 158.0, "weight_kg": 64.0
    })
    assert missing_patient.status_code == 422

    bad_vitals = client.post(f"/api/cca/encounters/{encounter.id}/intake", headers=headers, json={
        "patient_id": patient_id, "bp_systolic": "not-a-number"
    })
    assert bad_vitals.status_code == 400


def test_act_4_doctor_consultation_and_note_finalisation(client, headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)
    encounter = db_session.query(CCAEncounter).filter(CCAEncounter.patient_id == patient_id).first()
    encounter_id = encounter.id

    res = client.post(f"/api/cca/encounters/{encounter_id}/note/finalise", headers=headers, json={"confirm": True})
    assert res.status_code == 200
    assert res.json()["encounter"]["note_status"] == "FINAL"


def test_open_encounter_is_idempotent_and_reuses_existing_open_encounter(client, headers, db_session, doctor):
    """Regression coverage for a real gap: /encounters/{id}/intake and /note/finalise both
    require an encounter id to already exist, but before this endpoint the only place an
    encounter was ever created was cca_seed.py's demo seeder -- Nurse Intake/Doctor OPD were
    non-functional for any patient beyond the one seeded demo one."""
    patient_id = _demo_patient_id(db_session, doctor.organization_id)
    existing = db_session.query(CCAEncounter).filter(CCAEncounter.patient_id == patient_id).first()

    res = client.post(f"/api/cca/patients/{patient_id}/encounters", headers=headers, json={})
    assert res.status_code == 201
    assert res.json()["encounter"]["id"] == existing.id  # reuses the seeded OPEN encounter

    again = client.post(f"/api/cca/patients/{patient_id}/encounters", headers=headers, json={})
    assert again.json()["encounter"]["id"] == existing.id


def test_open_encounter_creates_new_encounter_when_none_open(client, headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)
    db_session.query(CCAEncounter).filter(CCAEncounter.patient_id == patient_id).update({"status": "CLOSED"})
    db_session.commit()

    res = client.post(f"/api/cca/patients/{patient_id}/encounters", headers=headers, json={
        "encounter_type": "OPD_CONSULTATION", "specialty": "Surgical Oncology"
    })
    assert res.status_code == 201
    new_encounter = res.json()["encounter"]
    assert new_encounter["status"] == "OPEN"

    created = db_session.query(CCAEncounter).filter(CCAEncounter.id == new_encounter["id"]).first()
    assert created.specialty == "Surgical Oncology"
    assert created.clinician == "onc.doctor@ccahosp.com"

    journey = client.get(f"/api/cca/patients/{patient_id}/journey", headers=headers).json()["journey_events"]
    assert any(e["event_type"] == "ENCOUNTER_OPENED" for e in journey)


def test_open_encounter_is_org_scoped(client, make_user, auth_headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)
    other_doctor = make_user(email="other.org.encounter@rivalhosp.com", role="Doctor")
    res = client.post(f"/api/cca/patients/{patient_id}/encounters", headers=auth_headers(other_doctor), json={})
    assert res.status_code == 404


def test_act_5_orders_result_simulation_and_acknowledgement(client, headers, admin_headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    # Resolve CTR-01 contradiction so staging is not blocked by open contradiction
    ctr = db_session.query(CCAContradiction).filter(CCAContradiction.patient_id == patient_id).first()
    if ctr:
        ctr.status = "RESOLVED"
        db_session.commit()

    # Raise order (indication required)
    fail_order = client.post("/api/cca/orders", headers=headers, json={
        "patient_id": patient_id,
        "item_name": "CECT Chest Abdomen"
    })
    assert fail_order.status_code == 422  # clinical_indication missing

    order_res = client.post("/api/cca/orders", headers=headers, json={
        "patient_id": patient_id,
        "item_name": "CECT Chest & Abdomen",
        "item_code": "RAD-CT-CAP-01",
        "clinical_indication": "Complete distant metastatic staging workup."
    })
    assert order_res.status_code == 200

    # Presenter Demo Control: Simulate Result Arrival (CT returns cM0) -- Admin only.
    denied_sim = client.post(f"/api/cca/demo/simulate-result?patient_id={patient_id}", headers=headers)
    assert denied_sim.status_code == 403
    sim_res = client.post(f"/api/cca/demo/simulate-result?patient_id={patient_id}", headers=admin_headers)
    assert sim_res.status_code == 200
    assert sim_res.json()["status"] == "success"

    # Clinician Acknowledgement in Results Inbox
    results = client.get(f"/api/cca/results?patient_id={patient_id}", headers=headers).json()["results"]
    assert len(results) >= 1
    res_id = results[0]["id"]

    ack_res = client.post(f"/api/cca/results/{res_id}/acknowledge", headers=headers)
    assert ack_res.status_code == 200
    assert ack_res.json()["result"]["status"] == "ACKNOWLEDGED"

    # Check Staging Readiness flipped to READY_FOR_STAGING
    staging_readiness = client.get(f"/api/cca/patients/{patient_id}/staging/readiness", headers=headers).json()
    assert staging_readiness["state"] == "READY_FOR_STAGING"
    assert len(staging_readiness["missing"]) == 0


def test_act_6_clinician_staging_confirmation_and_guideline_flip(client, headers, nurse, auth_headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    # Precondition: Guideline readiness must be NOT_READY prior to confirmed stage
    g_pre = client.get(f"/api/cca/patients/{patient_id}/guidelines/readiness", headers=headers).json()
    assert g_pre["state"] == "NOT_READY"

    # A non-clinician (Nurse) may not confirm a stage.
    nurse_headers = auth_headers(nurse)
    nurse_attempt = client.post(f"/api/cca/patients/{patient_id}/staging/confirm", headers=nurse_headers, json={
        "stage_value": "cT2 cN0 cM0 - Stage IIA", "t_stage": "cT2", "n_stage": "cN0",
        "m_stage": "cM0", "stage_group": "Stage IIA"
    })
    assert nurse_attempt.status_code == 403

    # Clinician Confirmation Gate (staging.confirm)
    confirm_res = client.post(f"/api/cca/patients/{patient_id}/staging/confirm", headers=headers, json={
        "stage_value": "cT2 cN0 cM0 - Stage IIA",
        "classification_prefix": "c",
        "t_stage": "cT2",
        "n_stage": "cN0",
        "m_stage": "cM0",
        "stage_group": "Stage IIA",
        "change_reason": "CECT confirms absence of distant metastasis."
    })
    assert confirm_res.status_code == 200
    assert confirm_res.json()["staging_record"]["status"] == "CLINICIAN_CONFIRMED"

    # Guideline Readiness immediately flips to READY (WOW 4)
    g_post = client.get(f"/api/cca/patients/{patient_id}/guidelines/readiness", headers=headers).json()
    assert g_post["state"] == "READY"

    # NCCN Context is now viewable
    nccn_res = client.get(f"/api/cca/patients/{patient_id}/guidelines/context", headers=headers)
    assert nccn_res.status_code == 200
    assert "Invasive Breast Cancer: Stage IIA" in nccn_res.json()["pathway_node"]


def test_act_7_nexus_clinical_brief_and_mdt_package(client, headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    # NEXUS 13-Section Clinical Brief
    brief_res = client.get(f"/api/cca/patients/{patient_id}/clinical-brief", headers=headers)
    assert brief_res.status_code == 200
    brief = brief_res.json()
    sections = brief["sections"]
    assert len(sections) == 13
    assert "1_demographics" in sections
    assert "3_staging_extent" in sections
    assert "4_biomarker_profile" in sections
    assert "7_best_next_investigation" in sections
    assert "13_decision_support" in sections

    # 1-Click MDT Case Package (WOW 6)
    mdt_res = client.post("/api/cca/mdt/cases", headers=headers, json={
        "patient_id": patient_id,
        "question": "Review neoadjuvant systemic chemotherapy vs upfront breast conserving surgery."
    })
    assert mdt_res.status_code == 200
    case_id = mdt_res.json()["mdt_case"]["id"]

    # Record MDT Recommendation
    rec_res = client.post(f"/api/cca/mdt/cases/{case_id}/recommendation", headers=headers, json={
        "recommendation": "Neoadjuvant dose-dense AC-T chemotherapy recommended."
    })
    assert rec_res.status_code == 200
    assert rec_res.json()["decision"]["status"] == "FINAL"


def test_act_8_live_care_plan_prefill_and_versioning(client, headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    # Before any MDT has recommended a direction, the engine must not fabricate a regimen.
    early_prefill = client.get(f"/api/cca/care-plans/prefill?patient_id={patient_id}", headers=headers).json()
    assert early_prefill["ready"] is False
    assert early_prefill["components"] == {}

    # Record a real MDT recommendation first (the prefill's actual data source).
    mdt_res = client.post("/api/cca/mdt/cases", headers=headers, json={
        "patient_id": patient_id, "question": "Sequencing for Stage IIA HR+/HER2- disease."
    })
    case_id = mdt_res.json()["mdt_case"]["id"]
    client.post(f"/api/cca/mdt/cases/{case_id}/recommendation", headers=headers, json={
        "recommendation": "Neoadjuvant dose-dense AC-T chemotherapy recommended."
    })

    # Pre-population payload (WOW 7) -- now grounded in the MDT decision above.
    prefill = client.get(f"/api/cca/care-plans/prefill?patient_id={patient_id}", headers=headers).json()
    assert prefill["ready"] is True
    assert "Dose-dense AC-T" in prefill["components"]["systemic_therapy"]["regimen"]
    assert prefill["mdt_recommendation"] == "Neoadjuvant dose-dense AC-T chemotherapy recommended."

    # A Care Plan can only reference an already-signed Treatment Plan.
    tx_plan_id = _create_and_sign_treatment_plan(client, headers, patient_id)

    # Create Care Plan v1.0
    plan_res = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [tx_plan_id], **prefill
    })
    assert plan_res.status_code == 200
    plan_id = plan_res.json()["care_plan"]["id"]

    # Updating Care Plan without change_reason must fail with 422 (E-36)
    bad_update = client.put(f"/api/cca/care-plans/{plan_id}", headers=headers, json={"intent": "Curative"})
    assert bad_update.status_code == 422

    # Updating with change_reason increments version
    good_update = client.put(f"/api/cca/care-plans/{plan_id}", headers=headers, json={
        "intent": "Curative Neoadjuvant",
        "change_reason": "Updated following patient financial and toxicity counseling."
    })
    assert good_update.status_code == 200
    assert good_update.json()["version"] == 2


def test_care_plans_current_returns_null_then_the_active_plan(client, headers, db_session, doctor):
    """Regression coverage for a real gap: the Live Care Plan Hub had no way to read back an
    already-created plan's id/version -- only /care-plans/prefill (a pre-MDT draft generator)
    and POST/PUT existed, so a real Amend Care Plan action had nothing to target."""
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    before = client.get(f"/api/cca/care-plans/current?patient_id={patient_id}", headers=headers)
    assert before.status_code == 200
    assert before.json()["care_plan"] is None

    mdt_res = client.post("/api/cca/mdt/cases", headers=headers, json={
        "patient_id": patient_id, "question": "Sequencing for Stage IIA HR+/HER2- disease."
    })
    case_id = mdt_res.json()["mdt_case"]["id"]
    client.post(f"/api/cca/mdt/cases/{case_id}/recommendation", headers=headers, json={
        "recommendation": "Neoadjuvant dose-dense AC-T chemotherapy recommended."
    })
    prefill = client.get(f"/api/cca/care-plans/prefill?patient_id={patient_id}", headers=headers).json()
    tx_plan_id = _create_and_sign_treatment_plan(client, headers, patient_id)
    plan_id = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [tx_plan_id], **prefill
    }).json()["care_plan"]["id"]

    after = client.get(f"/api/cca/care-plans/current?patient_id={patient_id}", headers=headers).json()["care_plan"]
    assert after["id"] == plan_id
    assert after["version_no"] == 1
    assert after["status"] == "ACTIVE"

    client.put(f"/api/cca/care-plans/{plan_id}", headers=headers, json={
        "components": {"supportive": "Ondansetron PRN"},
        "change_reason": "Added antiemetic support."
    })
    amended = client.get(f"/api/cca/care-plans/current?patient_id={patient_id}", headers=headers).json()["care_plan"]
    assert amended["version_no"] == 2
    assert amended["components"]["supportive"] == "Ondansetron PRN"


def test_care_plans_current_is_org_scoped(client, make_user, auth_headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)
    other_doctor = make_user(email="other.org.careplan@rivalhosp.com", role="Doctor")
    res = client.get(f"/api/cca/care-plans/current?patient_id={patient_id}", headers=auth_headers(other_doctor))
    assert res.status_code == 404


def test_act_9_treatment_day_clearance_and_toxicity(client, headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    # A treatment session must exist before a clearance decision can be recorded --
    # created here via the same MDT -> care-plan path as Act 8.
    mdt_res = client.post("/api/cca/mdt/cases", headers=headers, json={
        "patient_id": patient_id, "question": "Sequencing for Stage IIA HR+/HER2- disease."
    })
    case_id = mdt_res.json()["mdt_case"]["id"]
    client.post(f"/api/cca/mdt/cases/{case_id}/recommendation", headers=headers, json={"recommendation": "AC-T recommended."})
    prefill = client.get(f"/api/cca/care-plans/prefill?patient_id={patient_id}", headers=headers).json()
    tx_plan_id = _create_and_sign_treatment_plan(client, headers, patient_id)
    client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [tx_plan_id], **prefill
    })

    # Day-Care requires a signed Treatment Order to act against -- not the Plan/Care Plan alone.
    order_res = client.post("/api/cca/treatment-orders", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_id": tx_plan_id
    })
    assert order_res.status_code == 200, order_res.text
    order_id = order_res.json()["treatment_order"]["id"]
    sign_order_res = client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=headers)
    assert sign_order_res.status_code == 200, sign_order_res.text

    # Toxicity recording requires baseline_value (NOT NULL guard)
    bad_tox = client.post("/api/cca/treatment/toxicity", headers=headers, json={
        "patient_id": patient_id,
        "term": "Peripheral Sensory Neuropathy",
        "grade": 2
    })
    assert bad_tox.status_code == 422  # baseline_value required

    # Valid toxicity recording with baseline
    tox_res = client.post("/api/cca/treatment/toxicity", headers=headers, json={
        "patient_id": patient_id,
        "term": "Peripheral Sensory Neuropathy",
        "grade": 2,
        "baseline_value": "Grade 0"
    })
    assert tox_res.status_code == 200

    # Clearance requires an explicit decision + reason -- no silent "CLEARED" default.
    missing_decision = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "reason": "Routine."
    })
    assert missing_decision.status_code == 422

    # Clearance Decision: HELD (Must create mandatory reassessment task)
    clear_res = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id,
        "decision": "HELD",
        "reason": "Grade 2 neuropathy - hold cycle 1 administration for 7 days."
    })
    assert clear_res.status_code == 200
    assert clear_res.json()["clearance"]["decision"] == "HELD"


def test_act_9_clearance_without_a_treatment_session_is_rejected(client, headers, db_session, doctor):
    """No care plan / treatment session has been created for this patient yet --
    clearance must be refused rather than silently attached to an unrelated session."""
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    res = client.post("/api/cca/treatment/clearance", headers=headers, json={
        "patient_id": patient_id, "decision": "CLEARED", "reason": "Labs normal."
    })
    assert res.status_code == 422


def test_act_10_response_assessment_and_demo_controller(client, headers, admin_headers, db_session, doctor):
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    # Record RECIST 1.1 Response Assessment
    resp_res = client.post("/api/cca/response-assessments", headers=headers, json={
        "patient_id": patient_id,
        "response_category": "PR"
    })
    assert resp_res.status_code == 200
    assert resp_res.json()["response"]["response_category"] == "PR"

    # Demo Clock Advancement -- Admin only.
    denied_clock = client.post("/api/cca/demo/advance-clock", headers=headers, json={"target_day": "D+21", "patient_id": patient_id})
    assert denied_clock.status_code == 403
    clock_res = client.post("/api/cca/demo/advance-clock", headers=admin_headers, json={"target_day": "D+21", "patient_id": patient_id})
    assert clock_res.status_code == 200
    assert clock_res.json()["current_stage"] == "D+21"

    # Verify complete timeline events
    journey_res = client.get(f"/api/cca/patients/{patient_id}/journey", headers=headers)
    assert journey_res.status_code == 200
    events = journey_res.json()["journey_events"]
    assert len(events) >= 6


def test_cca_endpoints_require_auth_and_are_org_scoped(client, headers, auth_headers, make_user, db_session, doctor):
    """Regression coverage for the CCA module's most severe finding: every route used to run
    with no authentication and no organization scoping at all. Pin down both properties."""
    patient_id = _demo_patient_id(db_session, doctor.organization_id)

    # No token at all -> rejected, not served.
    no_auth = client.get(f"/api/cca/patients/{patient_id}")
    assert no_auth.status_code in (401, 403)

    no_auth_list = client.get("/api/cca/patients")
    assert no_auth_list.status_code in (401, 403)

    # A user in a different organization must not be able to see or resolve this patient --
    # 404, not 403, so existence of another org's patient is never confirmed.
    other_doctor = make_user(email="other.org.doctor@rivalhosp.com", role="Doctor")
    other_headers = auth_headers(other_doctor)

    cross_org_get = client.get(f"/api/cca/patients/{patient_id}", headers=other_headers)
    assert cross_org_get.status_code == 404

    cross_org_list = client.get("/api/cca/patients", headers=other_headers).json()
    assert all(p["mrn"] != "CCA-2026-004417" for p in cross_org_list["results"])

    # The unauthenticated database-reset endpoint must require Admin, not just any token.
    doctor_reset_attempt = client.post("/api/cca/demo/reset", headers=headers)
    assert doctor_reset_attempt.status_code == 403

    no_auth_reset = client.post("/api/cca/demo/reset")
    assert no_auth_reset.status_code in (401, 403)


def _fake_ocr_result(*_args, **_kwargs):
    return {
        "text": "Histopathology Report\nDiagnosis: Invasive Ductal Carcinoma, Grade 2\nHistologic type: IDC",
        "pages": [{"page": 1, "text": "Histopathology Report", "method": "embedded_text"}],
        "page_count": 1, "engine": "pypdf",
        "signals": {"diagnoses": [], "medications": [], "allergies": [], "investigations": [], "procedures": [], "dates_mentioned": [], "text_preview": ""},
        "processed_at": datetime.utcnow(),
    }


def test_document_upload_extracts_facts_and_creates_journey_event(client, headers, db_session, doctor, monkeypatch):
    """Regression coverage for a real gap found while assessing CCA readiness: there was no
    way to upload a document at all -- every ClinicalFact in the system came from cca_seed.py's
    hardcoded demo data. This is what makes the verification workspace work for a real patient."""
    from app.routers import cca as cca_router

    patient_id = _demo_patient_id(db_session, doctor.organization_id)
    monkeypatch.setattr(cca_router, "extract_document", _fake_ocr_result)
    mock_groq_json(monkeypatch, {"facts": [
        {"fact_type": "HISTOLOGY", "value": "Invasive Ductal Carcinoma, Grade 2", "verbatim": "Diagnosis: Invasive Ductal Carcinoma, Grade 2", "confidence": 0.97}
    ]})

    res = client.post(
        f"/api/cca/documents?patient_id={patient_id}",
        files={"file": ("new_biopsy.pdf", b"%PDF-1.4 fake biopsy report", "application/pdf")},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["document"]["classification"] == "HISTOPATHOLOGY"
    assert body["facts_drafted"] == 1

    doc_id = body["document"]["id"]
    facts = client.get(f"/api/cca/extractions/{doc_id}", headers=headers).json()["facts"]
    assert len(facts) == 1
    assert facts[0]["type"] == "HISTOLOGY"
    assert facts[0]["status"] == "PROPOSED"

    journey = client.get(f"/api/cca/patients/{patient_id}/journey", headers=headers).json()["journey_events"]
    assert any(e["event_type"] == "DOC_INGESTION" and "new_biopsy.pdf" in e["event_title"] for e in journey)


def test_document_upload_rejects_disallowed_file_type_and_duplicate(client, headers, db_session, doctor, monkeypatch):
    from app.routers import cca as cca_router

    patient_id = _demo_patient_id(db_session, doctor.organization_id)
    bad_type = client.post(
        f"/api/cca/documents?patient_id={patient_id}",
        files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
        headers=headers,
    )
    assert bad_type.status_code == 415

    monkeypatch.setattr(cca_router, "extract_document", _fake_ocr_result)
    mock_groq_json(monkeypatch, {"facts": []})
    content = b"%PDF-1.4 duplicate report"
    first = client.post(
        f"/api/cca/documents?patient_id={patient_id}",
        files={"file": ("dup.pdf", content, "application/pdf")}, headers=headers,
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/cca/documents?patient_id={patient_id}",
        files={"file": ("dup.pdf", content, "application/pdf")}, headers=headers,
    )
    assert second.status_code == 409


def test_document_upload_is_org_scoped(client, headers, auth_headers, make_user, db_session, doctor, monkeypatch):
    from app.routers import cca as cca_router

    patient_id = _demo_patient_id(db_session, doctor.organization_id)
    other_doctor = make_user(email="other.org.uploader@rivalhosp.com", role="Doctor")

    monkeypatch.setattr(cca_router, "extract_document", _fake_ocr_result)
    mock_groq_json(monkeypatch, {"facts": []})
    res = client.post(
        f"/api/cca/documents?patient_id={patient_id}",
        files={"file": ("x.pdf", b"%PDF-1.4 x", "application/pdf")},
        headers=auth_headers(other_doctor),
    )
    assert res.status_code == 404
