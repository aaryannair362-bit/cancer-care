"""
Unit tests for CCA Cancer Care AI OS Deterministic Engines.
Tests DuBois BSA, Contradiction Detection, Staging Readiness, Guideline Readiness, and NEXUS Brief.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base
from backend.app.models_cca import (
    CCAPatient, ClinicalFact, CCAContradiction, CCACancerDiagnosis,
    CCABiomarkerResult, StagingRecord, StagingEvidence, TreatmentOrder,
    OralTherapyPrescription, OralTherapyHoldEvent, MedicationReconciliationEntry,
)
from backend.app.models_cca_oncology_ext import PalliativeTreatmentOrder
from backend.app.cca_engine import (
    calculate_bsa, detect_contradictions, evaluate_staging_readiness,
    evaluate_guideline_readiness, synthesize_nexus_brief, generate_care_plan_prefill,
    extract_clinical_facts, build_medication_lists, build_results_from_document_facts,
)
from backend.app.scribe import scribe


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_dubois_bsa_and_bmi_calculations():
    # Test case: 158 cm, 64 kg (Meera S. Nair) -> 1.65 m^2 (DuBois), 25.64 kg/m^2 (BMI)
    bsa, bmi = calculate_bsa(height_cm=158.0, weight_kg=64.0, formula="DuBois")
    assert bsa == 1.65
    assert bmi == 25.64
    
    # Test case: Mosteller formula -> sqrt((158 * 64)/3600) = 1.68
    bsa_m, _ = calculate_bsa(height_cm=158.0, weight_kg=64.0, formula="Mosteller")
    assert bsa_m == 1.68
    
    # Test zero/negative inputs
    assert calculate_bsa(0, 60) == (0.0, 0.0)
    assert calculate_bsa(160, -5) == (0.0, 0.0)


def test_contradiction_detection_engine(db_session):
    patient = CCAPatient(mrn="TEST-MRN-01", name="Test Patient", age=55, sex="F", organization_id=1)
    db_session.add(patient)
    db_session.commit()
    
    # Add conflicting laterality facts
    fact_left = ClinicalFact(
        patient_id=patient.id,
        fact_type="LATERALITY",
        value="Left Breast (Referral)",
        status="PROPOSED"
    )
    fact_right = ClinicalFact(
        patient_id=patient.id,
        fact_type="LATERALITY",
        value="Right Breast 10 o'clock mass (USG)",
        status="VERIFIED"
    )
    db_session.add_all([fact_left, fact_right])
    db_session.commit()
    
    ctrs = detect_contradictions(db_session, patient.id)
    assert len(ctrs) == 1
    assert ctrs[0].rule_id == "CTR-01"
    assert ctrs[0].status == "OPEN"
    assert fact_left.id in ctrs[0].conflicting_fact_ids
    assert fact_right.id in ctrs[0].conflicting_fact_ids


def test_extract_clinical_facts_requests_a_higher_token_budget_than_the_generic_default(monkeypatch):
    """Regression test for a real, reproducible bug: found by running an actual dense oncology
    document (a CT staging report plus a full metabolic lab panel) through the live pipeline
    repeatedly -- Groq's response was intermittently (not always -- generation-to-generation
    non-determinism) truncated mid-JSON at _call_groq_api's 3000-token default, which
    extract_clinical_facts() has no way to recover from (the shared _generate_json malformed-
    JSON fallback only ever produces scribe_transcript()'s unrelated shape), silently yielding
    zero facts despite real, extractable content. Verified fixed by requesting max_tokens=6000
    instead and re-running the same real document 5 times with zero failures (was previously
    ~50% failure rate on that document). This test pins the higher budget at the unit level so
    it can't silently regress back to the shared default."""
    captured = {}

    def _fake_generate_json(prompt, system=None, temperature=0.3, max_tokens=3000):
        captured["max_tokens"] = max_tokens
        return {"facts": []}

    monkeypatch.setattr(scribe, "_generate_json", _fake_generate_json)
    extract_clinical_facts("Diagnosis: Breast carcinoma")
    assert captured["max_tokens"] > 3000


def test_extract_clinical_facts_parses_real_shaped_response(monkeypatch):
    def _fake_generate_json(prompt, system=None, temperature=0.3, max_tokens=3000):
        return {"facts": [
            {"fact_type": "PRIMARY_SITE", "value": "Breast", "verbatim": "Breast carcinoma", "confidence": 0.95},
            {"fact_type": "NOT_A_REAL_TYPE", "value": "should be dropped"},
        ]}

    monkeypatch.setattr(scribe, "_generate_json", _fake_generate_json)
    facts = extract_clinical_facts("Diagnosis: Breast carcinoma")
    assert len(facts) == 1
    assert facts[0]["fact_type"] == "PRIMARY_SITE"
    assert facts[0]["value"] == "Breast"


def test_staging_readiness_state_machine(db_session):
    patient = CCAPatient(mrn="TEST-MRN-02", name="Staging Patient", age=60, sex="F", organization_id=1)
    db_session.add(patient)
    db_session.commit()
    
    # Step 1: Initial state (No facts) -> EVIDENCE_INCOMPLETE
    readiness = evaluate_staging_readiness(db_session, patient.id)
    assert readiness["state"] == "EVIDENCE_INCOMPLETE"
    assert len(readiness["missing"]) >= 3
    
    # Step 2: Add T, N, and Histology facts
    f_t = ClinicalFact(patient_id=patient.id, fact_type="T_EVIDENCE", value="cT2: 2.8cm mass", status="VERIFIED")
    f_n = ClinicalFact(patient_id=patient.id, fact_type="N_EVIDENCE", value="cN0: Axilla clear", status="VERIFIED")
    f_h = ClinicalFact(patient_id=patient.id, fact_type="HISTOLOGY", value="Invasive Ductal Carcinoma", status="VERIFIED")
    db_session.add_all([f_t, f_n, f_h])
    db_session.commit()
    
    readiness_mid = evaluate_staging_readiness(db_session, patient.id)
    assert readiness_mid["state"] == "PARTIALLY_READY"
    assert any(m["input"] == "M_EVIDENCE" for m in readiness_mid["missing"])
    
    # Step 3: Add M fact (CECT negative) -> READY_FOR_STAGING
    f_m = ClinicalFact(patient_id=patient.id, fact_type="M_EVIDENCE", value="cM0: No distant metastasis", status="VERIFIED")
    db_session.add(f_m)
    db_session.commit()
    
    readiness_ready = evaluate_staging_readiness(db_session, patient.id)
    assert readiness_ready["state"] == "READY_FOR_STAGING"
    assert len(readiness_ready["missing"]) == 0
    
    # Step 4: Clinician confirms stage -> CLINICIAN_CONFIRMED
    stage_rec = StagingRecord(
        patient_id=patient.id,
        classification_prefix="c",
        t_stage="cT2",
        n_stage="cN0",
        m_stage="cM0",
        stage_value="cT2 cN0 cM0 - Stage IIA",
        status="CLINICIAN_CONFIRMED",
        confirmed_by="Dr. Sarah Varma"
    )
    db_session.add(stage_rec)
    db_session.commit()
    
    readiness_confirmed = evaluate_staging_readiness(db_session, patient.id)
    assert readiness_confirmed["state"] == "CLINICIAN_CONFIRMED"
    assert readiness_confirmed["confirmed_record"]["stage_value"] == "cT2 cN0 cM0 - Stage IIA"


def test_guideline_readiness_gating(db_session):
    patient = CCAPatient(mrn="TEST-MRN-03", name="Guideline Patient", age=50, sex="F", organization_id=1)
    db_session.add(patient)
    db_session.commit()
    
    # Prerequisite check: Unconfirmed stage blocks guideline readiness (Rule G-5)
    g_readiness = evaluate_guideline_readiness(db_session, patient.id)
    assert g_readiness["state"] == "NOT_READY"
    assert "Clinician-Confirmed AJCC Staging Record" in g_readiness["missing"][0]
    
    # Confirm stage
    stage_rec = StagingRecord(
        patient_id=patient.id,
        stage_value="cT2 cN0 cM0 - Stage IIA",
        status="CLINICIAN_CONFIRMED",
        confirmed_by="Dr. Sarah Varma"
    )
    db_session.add(stage_rec)
    
    # Add biomarkers
    bm_er = CCABiomarkerResult(patient_id=patient.id, marker_name="ER", result_as_reported="Positive", status="RESULTED")
    bm_pr = CCABiomarkerResult(patient_id=patient.id, marker_name="PR", result_as_reported="Positive", status="RESULTED")
    bm_her2 = CCABiomarkerResult(patient_id=patient.id, marker_name="HER2", result_as_reported="Negative", status="RESULTED")
    db_session.add_all([bm_er, bm_pr, bm_her2])
    db_session.commit()
    
    g_readiness_ready = evaluate_guideline_readiness(db_session, patient.id)
    assert g_readiness_ready["state"] == "READY"
    assert len(g_readiness_ready["missing"]) == 0


def test_nexus_brief_never_fabricates_missing_clinical_data(db_session):
    """synthesize_nexus_brief's own docstring promises it never invents diagnoses -- for a
    patient with no diagnosis, intake, or labs on record, every section must say so explicitly
    rather than filling the gap with a plausible-looking default (a real prior bug: the
    diagnosis/stage/comorbidity/lab sections used to always render specific invented content
    such as a fixed 'Hypertension (controlled on Amlodipine 5mg OD)' comorbidity regardless of
    what, if anything, was actually recorded for the patient)."""
    patient = CCAPatient(mrn="TEST-MRN-04", name="Bare Patient", age=45, sex="M", organization_id=1)
    db_session.add(patient)
    db_session.commit()

    brief = synthesize_nexus_brief(db_session, patient.id)
    sections = brief["sections"]

    assert "[NOT_RECORDED]" in sections["2_primary_diagnosis"]["content"]
    assert "Invasive Breast Carcinoma, NOS" not in sections["2_primary_diagnosis"]["content"]

    assert "[NOT_STAGED]" in sections["3_staging_extent"]["content"]
    assert "Provisional Stage IIA" not in sections["3_staging_extent"]["content"]

    assert "[NOT_RECORDED]" in sections["5_performance_history"]["content"]
    assert "Amlodipine" not in sections["5_performance_history"]["content"]

    assert "[NOT_RECORDED]" in sections["12_safety_flags"]["content"]
    assert "Baseline CBC/LFT/KFT normal" not in sections["12_safety_flags"]["content"]


def test_care_plan_prefill_refuses_to_invent_a_regimen_without_an_mdt_decision(db_session):
    """generate_care_plan_prefill used to always return a fully-dosed AC-T chemotherapy regimen
    regardless of the patient's actual diagnosis or whether any tumor board had recommended
    that direction. It must now refuse (ready=False, no drug/dose content) until a real,
    finalised MDTDecision exists for the patient."""
    patient = CCAPatient(mrn="TEST-MRN-05", name="No MDT Patient", age=52, sex="F", organization_id=1)
    db_session.add(patient)
    db_session.commit()

    prefill = generate_care_plan_prefill(db_session, patient.id)
    assert prefill["ready"] is False
    assert prefill["components"] == {}
    assert prefill["mdt_recommendation"] is None


def test_build_medication_lists_buckets_current_vs_past_across_all_sources(db_session):
    """One representative row per medication source (Patient History's Current/Past Medications
    tabs), covering the status that should land it in each bucket -- a signed/active order or
    prescription is Current; a cancelled/discontinued one is Past; a document-derived MEDICATION
    fact is always Past since it carries no ongoing status of its own."""
    patient = CCAPatient(mrn="TEST-MRN-06", name="Med List Patient", age=58, sex="F", organization_id=1)
    db_session.add(patient)
    db_session.commit()

    current_order = TreatmentOrder(
        patient_id=patient.id, treatment_plan_id=1, treatment_session_id=1,
        instructions={"drug": "Doxorubicin", "dose": "60mg/m2"}, status="SIGNED",
    )
    past_order = TreatmentOrder(
        patient_id=patient.id, treatment_plan_id=1, treatment_session_id=1,
        instructions={"drug": "Cyclophosphamide"}, status="CANCELLED",
    )
    current_rx = OralTherapyPrescription(patient_id=patient.id, drug="Letrozole", status="ACTIVE")
    past_rx = OralTherapyPrescription(patient_id=patient.id, drug="Capecitabine", status="DISCONTINUED")
    current_pal = PalliativeTreatmentOrder(
        patient_id=patient.id, order_type="Pain Management", instructions="Morphine SR 10mg BD", status="Active",
    )
    past_pal = PalliativeTreatmentOrder(
        patient_id=patient.id, order_type="Symptom Control", instructions="Ondansetron",
        status="Discontinued", discontinued_reason="Symptom resolved",
    )
    current_home_med = MedicationReconciliationEntry(
        intake_assessment_id=1, patient_id=patient.id, drug_name="Metformin", action="Continue",
    )
    past_home_med = MedicationReconciliationEntry(
        intake_assessment_id=1, patient_id=patient.id, drug_name="Aspirin", action="Discontinue",
        action_reason="Bleeding risk before surgery",
    )
    doc_history_fact = ClinicalFact(
        patient_id=patient.id, fact_type="MEDICATION", value="Metoprolol 25mg OD", status="VERIFIED",
    )
    db_session.add_all([
        current_order, past_order, current_rx, past_rx, current_pal, past_pal,
        current_home_med, past_home_med, doc_history_fact,
    ])
    db_session.commit()
    past_rx_discontinue_event = OralTherapyHoldEvent(
        prescription_id=past_rx.id, patient_id=patient.id, event_type="Discontinue",
        reason="Hand-foot syndrome grade 3",
    )
    db_session.add(past_rx_discontinue_event)
    db_session.commit()

    result = build_medication_lists(db_session, patient.id)
    current_drugs = {m["drug"] for m in result["current"]}
    past_drugs = {m["drug"] for m in result["past"]}

    assert current_drugs == {"Doxorubicin", "Letrozole", "Pain Management", "Metformin"}
    assert past_drugs == {
        "Cyclophosphamide", "Capecitabine", "Symptom Control", "Aspirin", "Metoprolol 25mg OD",
    }

    past_rx_item = next(m for m in result["past"] if m["drug"] == "Capecitabine")
    assert past_rx_item["stopped_reason"] == "Hand-foot syndrome grade 3"
    past_home_med_item = next(m for m in result["past"] if m["drug"] == "Aspirin")
    assert past_home_med_item["stopped_reason"] == "Bleeding risk before surgery"


def test_build_medication_lists_excludes_drafts_and_moves_superseded_to_past(db_session):
    """A DRAFT order/prescription was never actually authorized -- it isn't a medication on the
    patient's record yet, so it must appear in neither list. A SIGNED order that has since been
    superseded by a newer one (supersedes_id) is no longer what the patient is actually on, even
    though its own status column was never flipped to CANCELLED -- it must count as Past too."""
    patient = CCAPatient(mrn="TEST-MRN-07", name="Draft Supersede Patient", age=47, sex="M", organization_id=1)
    db_session.add(patient)
    db_session.commit()

    draft_order = TreatmentOrder(
        patient_id=patient.id, treatment_plan_id=1, treatment_session_id=1,
        instructions={"drug": "Should Not Appear"}, status="DRAFT",
    )
    original_order = TreatmentOrder(
        patient_id=patient.id, treatment_plan_id=1, treatment_session_id=1,
        instructions={"drug": "Paclitaxel Standard"}, status="SIGNED",
    )
    db_session.add_all([draft_order, original_order])
    db_session.commit()
    revised_order = TreatmentOrder(
        patient_id=patient.id, treatment_plan_id=1, treatment_session_id=1,
        instructions={"drug": "Paclitaxel Reduced"}, status="SIGNED", supersedes_id=original_order.id,
    )
    db_session.add(revised_order)
    db_session.commit()

    result = build_medication_lists(db_session, patient.id)
    all_drugs = {m["drug"] for m in result["current"] + result["past"]}
    assert "Should Not Appear" not in all_drugs
    assert "Paclitaxel Standard" in {m["drug"] for m in result["past"]}
    assert "Paclitaxel Reduced" in {m["drug"] for m in result["current"]}


def test_build_results_from_document_facts_groups_lab_and_imaging_only():
    facts = [
        {"fact_type": "LAB_RESULT", "value": "Hemoglobin 11.2 g/dL"},
        {"fact_type": "LAB_RESULT", "value": "WBC 6800/uL"},
        {"fact_type": "IMAGING_FINDING", "value": "No metastatic lesions on CT chest"},
        {"fact_type": "MEDICATION", "value": "Tamoxifen 20mg OD"},
    ]
    results = build_results_from_document_facts(facts, "report.pdf")
    by_type = {r["result_type"]: r for r in results}

    assert set(by_type) == {"LAB", "IMAGING"}
    assert "Hemoglobin 11.2 g/dL" in by_type["LAB"]["findings_text"]
    assert "WBC 6800/uL" in by_type["LAB"]["findings_text"]
    assert by_type["IMAGING"]["findings_text"] == "No metastatic lesions on CT chest"
    assert "report.pdf" in by_type["LAB"]["title"]


def test_build_results_from_document_facts_returns_empty_for_no_matching_facts():
    facts = [{"fact_type": "PRIMARY_SITE", "value": "Breast"}, {"fact_type": "ALLERGY", "value": "Penicillin"}]
    assert build_results_from_document_facts(facts, "referral.pdf") == []
