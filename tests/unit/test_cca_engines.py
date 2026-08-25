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
    CCABiomarkerResult, StagingRecord, StagingEvidence
)
from backend.app.cca_engine import (
    calculate_bsa, detect_contradictions, evaluate_staging_readiness,
    evaluate_guideline_readiness, synthesize_nexus_brief, generate_care_plan_prefill
)


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
