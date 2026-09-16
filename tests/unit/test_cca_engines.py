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
    extract_clinical_facts, extract_clinical_facts_with_identity, check_patient_identity_mismatch,
    group_document_pages_into_sections,
    build_medication_lists, build_results_from_document_facts,
    _slice_text_by_bytes, _biomarker_marker_key,
)
from backend.app import gemini_client
from datetime import date, timedelta


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


@pytest.mark.parametrize("value,expected", [
    ("ER: Positive (80%, Allred 8/8)", "ER"),
    ("HER2/neu: Negative (1+)", "HER2/NEU"),
    ("ki-67: 18%", "KI-67"),
    ("no colon at all here", "NO COLON AT ALL HERE"),  # malformed value never crashes grouping
])
def test_biomarker_marker_key_parses_and_normalizes(value, expected):
    assert _biomarker_marker_key(value) == expected


def test_biomarker_marker_key_truncates_to_fit_the_rule_id_column():
    """rule_id is VARCHAR(50) ("CTR-02:" + this key) -- a malformed value with no colon must
    never carry the fact's full (up to 500-char) value straight into it."""
    key = _biomarker_marker_key("x" * 500)
    assert len(f"CTR-02:{key}") <= 50


def test_contradiction_detection_flags_biomarker_conflict(db_session):
    """OCR gap review P0: two documents reporting different values for the SAME biomarker (e.g.
    ER positive from one report, ER negative from another) must be flagged for a clinician to
    reconcile, not sit as two undifferentiated chips."""
    patient = CCAPatient(mrn="TEST-MRN-02", name="Test Patient", age=55, sex="F", organization_id=1)
    db_session.add(patient)
    db_session.commit()

    er_positive = ClinicalFact(
        patient_id=patient.id, fact_type="BIOMARKER_RESULT",
        value="ER: Positive (80%, Allred 8/8)", status="VERIFIED",
    )
    er_negative = ClinicalFact(
        patient_id=patient.id, fact_type="BIOMARKER_RESULT",
        value="ER: Negative (<1%)", status="PROPOSED",
    )
    pr_positive = ClinicalFact(  # a different, non-conflicting marker must never be swept in
        patient_id=patient.id, fact_type="BIOMARKER_RESULT",
        value="PR: Positive (65%, Allred 7/8)", status="VERIFIED",
    )
    db_session.add_all([er_positive, er_negative, pr_positive])
    db_session.commit()

    ctrs = detect_contradictions(db_session, patient.id)
    er_ctrs = [c for c in ctrs if c.rule_id == "CTR-02:ER"]
    assert len(er_ctrs) == 1
    assert er_ctrs[0].status == "OPEN"
    assert set(er_ctrs[0].conflicting_fact_ids) == {er_positive.id, er_negative.id}
    assert not any(c.rule_id == "CTR-02:PR" for c in ctrs)


def test_contradiction_detection_does_not_flag_agreeing_biomarker_facts(db_session):
    patient = CCAPatient(mrn="TEST-MRN-03", name="Test Patient", age=55, sex="F", organization_id=1)
    db_session.add(patient)
    db_session.commit()
    db_session.add_all([
        ClinicalFact(patient_id=patient.id, fact_type="BIOMARKER_RESULT", value="HER2: Negative (1+)", status="VERIFIED"),
        ClinicalFact(patient_id=patient.id, fact_type="BIOMARKER_RESULT", value="HER2: Negative (1+)", status="PROPOSED"),
    ])
    db_session.commit()
    ctrs = detect_contradictions(db_session, patient.id)
    assert not any(c.rule_id.startswith("CTR-02:") for c in ctrs)


def test_contradiction_detection_flags_grade_conflict(db_session):
    patient = CCAPatient(mrn="TEST-MRN-04", name="Test Patient", age=55, sex="F", organization_id=1)
    db_session.add(patient)
    db_session.commit()
    fact_g2 = ClinicalFact(patient_id=patient.id, fact_type="GRADE", value="Grade 2", status="VERIFIED")
    fact_g3 = ClinicalFact(patient_id=patient.id, fact_type="GRADE", value="Grade 3", status="PROPOSED")
    db_session.add_all([fact_g2, fact_g3])
    db_session.commit()

    ctrs = detect_contradictions(db_session, patient.id)
    grade_ctrs = [c for c in ctrs if c.rule_id == "CTR-03:GRADE"]
    assert len(grade_ctrs) == 1
    assert set(grade_ctrs[0].conflicting_fact_ids) == {fact_g2.id, fact_g3.id}


def test_contradiction_detection_reuses_existing_biomarker_conflict_row(db_session):
    """Matches CTR-01's own long-standing behavior: calling detect_contradictions again must
    reuse the existing row for the same rule_id, not create a duplicate."""
    patient = CCAPatient(mrn="TEST-MRN-05", name="Test Patient", age=55, sex="F", organization_id=1)
    db_session.add(patient)
    db_session.commit()
    db_session.add_all([
        ClinicalFact(patient_id=patient.id, fact_type="BIOMARKER_RESULT", value="Ki-67: 18%", status="VERIFIED"),
        ClinicalFact(patient_id=patient.id, fact_type="BIOMARKER_RESULT", value="Ki-67: 45%", status="VERIFIED"),
    ])
    db_session.commit()

    first_pass = [c.id for c in detect_contradictions(db_session, patient.id) if c.rule_id == "CTR-02:KI-67"]
    second_pass = [c.id for c in detect_contradictions(db_session, patient.id) if c.rule_id == "CTR-02:KI-67"]
    assert first_pass == second_pass
    assert len(first_pass) == 1


def test_slice_text_by_bytes_never_splits_a_multibyte_character():
    """Regression: this codebase explicitly handles Hindi/Devanagari content (one character = 3
    UTF-8 bytes), so a naive byte-offset cut can land mid-character and produce invalid Unicode
    on decode. Devanagari text repeated past several byte-boundary crossings must reconstruct
    byte-for-byte identical to the original, and no slice may exceed the requested byte budget."""
    devanagari = "रोगी को तीन दिन से बुख़ार और खांसी है। " * 50
    slices = _slice_text_by_bytes(devanagari, max_bytes=37)  # deliberately NOT a multiple of any char's byte width

    assert "".join(slices) == devanagari
    for s in slices:
        assert len(s.encode("utf-8")) <= 37


def test_slice_text_by_bytes_matches_char_slicing_for_pure_ascii():
    text = "Hemoglobin: 11.2 g/dL. Creatinine: 0.9 mg/dL. " * 300
    slices = _slice_text_by_bytes(text, max_bytes=100)

    assert "".join(slices) == text
    assert all(len(s) <= 100 for s in slices[:-1])  # every slice but the last is exactly full


def test_slice_text_by_bytes_handles_empty_and_short_text():
    assert _slice_text_by_bytes("", max_bytes=100) == []
    assert _slice_text_by_bytes("short", max_bytes=100) == ["short"]


def test_extract_clinical_facts_parses_real_shaped_response(monkeypatch):
    def _fake_generate_structured_json(prompt, system=None, response_schema=None, **kwargs):
        return {"facts": [
            {"fact_type": "PRIMARY_SITE", "value": "Breast", "verbatim": "Breast carcinoma", "confidence": 0.95},
            {"fact_type": "NOT_A_REAL_TYPE", "value": "should be dropped"},
        ]}

    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake_generate_structured_json)
    facts = extract_clinical_facts("Diagnosis: Breast carcinoma")
    assert len(facts) == 1
    assert facts[0]["fact_type"] == "PRIMARY_SITE"
    assert facts[0]["value"] == "Breast"


def test_extract_clinical_facts_accepts_the_other_clinical_finding_fallback(monkeypatch):
    """Regression: FACT_TYPES used to be a closed, oncology-staging-specific enum (PRIMARY_SITE/
    LATERALITY/HISTOLOGY/.../ALLERGY) with no fallback -- a real fact the model correctly read
    off the page (e.g. surgical/family/social history, a non-oncology diagnosis) had no valid
    fact_type to be assigned, so extraction silently dropped it even though OCR, preprocessing,
    and the model all worked correctly. OTHER_CLINICAL_FINDING closes that gap; this pins that a
    fact using it is accepted, not filtered out the way an actually-invalid type is."""
    def _fake_generate_structured_json(prompt, system=None, response_schema=None, **kwargs):
        return {"facts": [
            {"fact_type": "OTHER_CLINICAL_FINDING", "value": "Appendectomy in 2018", "verbatim": "s/p appendectomy 2018", "confidence": 0.9},
        ]}

    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake_generate_structured_json)
    facts = extract_clinical_facts("Past surgical history: s/p appendectomy 2018")
    assert len(facts) == 1
    assert facts[0]["fact_type"] == "OTHER_CLINICAL_FINDING"
    assert facts[0]["value"] == "Appendectomy in 2018"


def test_extract_clinical_facts_prompt_instructs_the_model_not_to_drop_unfitting_facts(monkeypatch):
    """Pins the actual instruction text reaches the model -- the fallback fact_type existing is
    useless if the prompt never tells the model when to use it."""
    captured = {}

    def _fake_generate_structured_json(prompt, system=None, response_schema=None, **kwargs):
        captured["system"] = system
        return {"facts": []}

    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake_generate_structured_json)
    extract_clinical_facts("Diagnosis: Breast carcinoma")
    assert "OTHER_CLINICAL_FINDING" in captured["system"]
    assert "never silently omit" in captured["system"]


def test_extract_clinical_facts_response_schema_constrains_fact_type_enum(monkeypatch):
    """Unlike Groq's prompt-only JSON request, Gemini's response_schema enforces fact_type
    server-side -- pins that the schema actually passed to generate_structured_json lists every
    FACT_TYPES value (including OTHER_CLINICAL_FINDING) as its enum, so a future FACT_TYPES edit
    can't silently drift out of sync with what the model is constrained to return."""
    from backend.app.cca_engine import FACT_TYPES
    captured = {}

    def _fake_generate_structured_json(prompt, system=None, response_schema=None, **kwargs):
        captured["schema"] = response_schema
        return {"facts": []}

    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake_generate_structured_json)
    extract_clinical_facts("Diagnosis: Breast carcinoma")
    enum = captured["schema"]["properties"]["facts"]["items"]["properties"]["fact_type"]["enum"]
    assert set(enum) == set(FACT_TYPES)


# ---------------------------------------------------------------------------
# OCR gap review Phase 1: document-upload patient-identity validation
# ---------------------------------------------------------------------------

def test_extract_clinical_facts_with_identity_returns_both_facts_and_names(monkeypatch):
    def _fake_generate_structured_json(prompt, system=None, response_schema=None, **kwargs):
        return {
            "facts": [{"fact_type": "PRIMARY_SITE", "value": "Breast", "verbatim": "Breast", "confidence": 0.9}],
            "patient_names_mentioned": ["Meera Sharma", "Kavita Rao", "Meera Sharma"],
        }
    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake_generate_structured_json)
    result = extract_clinical_facts_with_identity("some document text")
    assert len(result["facts"]) == 1
    # deduped case-insensitively -- "Meera Sharma" appeared twice in the raw response
    assert result["patient_names_mentioned"] == ["Meera Sharma", "Kavita Rao"]


def test_extract_clinical_facts_with_identity_defaults_names_to_empty_when_absent(monkeypatch):
    """Every EXISTING test/caller (predating this field) mocks a bare {"facts": [...]} with no
    "patient_names_mentioned" key at all -- this must degrade to an empty list, never raise,
    so every one of those tests keeps working unchanged."""
    def _fake_generate_structured_json(prompt, system=None, response_schema=None, **kwargs):
        return {"facts": []}
    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake_generate_structured_json)
    result = extract_clinical_facts_with_identity("some document text")
    assert result["patient_names_mentioned"] == []


def test_extract_clinical_facts_backward_compatible_wrapper_ignores_names(monkeypatch):
    """extract_clinical_facts (the pre-existing function every other caller/test already
    depends on) must keep returning exactly the facts list, dropping patient_names_mentioned
    entirely -- it is a thin wrapper over extract_clinical_facts_with_identity now."""
    def _fake_generate_structured_json(prompt, system=None, response_schema=None, **kwargs):
        return {
            "facts": [{"fact_type": "HISTOLOGY", "value": "IDC", "verbatim": "IDC", "confidence": 0.9}],
            "patient_names_mentioned": ["Someone Else"],
        }
    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake_generate_structured_json)
    facts = extract_clinical_facts("some document text")
    assert facts == [{"fact_type": "HISTOLOGY", "value": "IDC", "verbatim": "IDC", "confidence": 0.9}]


def test_extract_clinical_facts_schema_requires_patient_names_mentioned(monkeypatch):
    from backend.app.cca_engine import _FACTS_RESPONSE_SCHEMA
    captured = {}

    def _fake_generate_structured_json(prompt, system=None, response_schema=None, **kwargs):
        captured["schema"] = response_schema
        captured["system"] = system
        return {"facts": [], "patient_names_mentioned": []}

    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake_generate_structured_json)
    extract_clinical_facts_with_identity("Diagnosis: Breast carcinoma")
    assert "patient_names_mentioned" in captured["schema"]["properties"]
    assert "patient_names_mentioned" in captured["schema"]["required"]
    assert captured["schema"] is _FACTS_RESPONSE_SCHEMA
    assert "THE PATIENT" in captured["system"]


@pytest.mark.parametrize("detected,patient_name,expect_mismatch", [
    # The real reported case: a 14-page bundle mixing two patients.
    (["Kavita Rao"], "Meera Sharma", True),
    (["Meera Sharma", "Kavita Rao"], "Meera Sharma", True),  # one matches, one doesn't -> still flagged
    (["Meera Sharma"], "Meera Sharma", False),
    ([], "Meera Sharma", False),  # nothing to compare -- never manufacture a mismatch from silence
    (["Meera Sharma3"], "Meera Sharma", False),  # OCR noise (stray digit) on the real patient's own name
    (["Sharma Meera"], "Meera Sharma", False),  # word-order variation, same person
    (["Mera Sharma"], "Meera Sharma", False),  # minor OCR spelling drift
])
def test_check_patient_identity_mismatch(detected, patient_name, expect_mismatch):
    result = check_patient_identity_mismatch(detected, patient_name)
    if expect_mismatch:
        assert result, f"expected {detected} to be flagged against patient {patient_name!r}"
    else:
        assert result == [], f"expected no mismatch for {detected} against patient {patient_name!r}, got {result}"


def test_check_patient_identity_mismatch_returns_only_the_mismatched_names():
    result = check_patient_identity_mismatch(["Meera Sharma", "Kavita Rao"], "Meera Sharma")
    assert result == ["Kavita Rao"]


# ---------------------------------------------------------------------------
# OCR gap review P0: source-date tracking (extract_clinical_facts_with_identity's document_date)
# ---------------------------------------------------------------------------

def test_extract_clinical_facts_with_identity_parses_document_date(monkeypatch):
    def _fake(prompt, system=None, response_schema=None, **kwargs):
        return {"facts": [], "patient_names_mentioned": [], "document_date": "2024-03-12"}
    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake)
    result = extract_clinical_facts_with_identity("some document text")
    assert result["document_date"] == date(2024, 3, 12)


def test_extract_clinical_facts_with_identity_defaults_document_date_to_none_when_absent(monkeypatch):
    """Every EXISTING test/caller (predating this field) mocks a bare {"facts": [...]} with no
    "document_date" key at all -- must degrade to None, never raise."""
    def _fake(prompt, system=None, response_schema=None, **kwargs):
        return {"facts": []}
    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake)
    result = extract_clinical_facts_with_identity("some document text")
    assert result["document_date"] is None


def test_extract_clinical_facts_with_identity_defaults_document_date_to_none_when_blank(monkeypatch):
    def _fake(prompt, system=None, response_schema=None, **kwargs):
        return {"facts": [], "patient_names_mentioned": [], "document_date": ""}
    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake)
    result = extract_clinical_facts_with_identity("some document text")
    assert result["document_date"] is None


@pytest.mark.parametrize("raw_date", [
    "12th March 2024",   # not the requested YYYY-MM-DD format
    "2024-13-40",         # not a real calendar date
    "not a date",
])
def test_extract_clinical_facts_with_identity_discards_unparseable_document_date(monkeypatch, raw_date):
    def _fake(prompt, system=None, response_schema=None, **kwargs):
        return {"facts": [], "patient_names_mentioned": [], "document_date": raw_date}
    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake)
    result = extract_clinical_facts_with_identity("some document text")
    assert result["document_date"] is None


def test_extract_clinical_facts_with_identity_discards_a_future_document_date(monkeypatch):
    """A future date is never a real report date -- almost always the model defaulting to
    "today" despite the prompt's explicit instruction not to guess."""
    future = (date.today() + timedelta(days=30)).isoformat()

    def _fake(prompt, system=None, response_schema=None, **kwargs):
        return {"facts": [], "patient_names_mentioned": [], "document_date": future}
    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake)
    result = extract_clinical_facts_with_identity("some document text")
    assert result["document_date"] is None


def test_extract_clinical_facts_with_identity_schema_requires_document_date(monkeypatch):
    from backend.app.cca_engine import _FACTS_RESPONSE_SCHEMA
    captured = {}

    def _fake(prompt, system=None, response_schema=None, **kwargs):
        captured["schema"] = response_schema
        return {"facts": [], "patient_names_mentioned": [], "document_date": ""}

    monkeypatch.setattr(gemini_client, "generate_structured_json", _fake)
    extract_clinical_facts_with_identity("Diagnosis: Breast carcinoma")
    assert "document_date" in captured["schema"]["properties"]
    assert "document_date" in captured["schema"]["required"]
    assert captured["schema"] is _FACTS_RESPONSE_SCHEMA


def test_group_document_pages_into_sections_collapses_consecutive_same_type():
    pages = [
        {"page_number": 1, "page_type": "CASE_DETAILS"},
        {"page_number": 2, "page_type": "CASE_DETAILS"},
        {"page_number": 3, "page_type": "LAB_REPORT"},
        {"page_number": 4, "page_type": "PATHOLOGY_REPORT"},
        {"page_number": 5, "page_type": "PATHOLOGY_REPORT"},
        {"page_number": 6, "page_type": "SCAN_IMAGING"},
    ]
    sections = group_document_pages_into_sections(pages)
    assert sections == [
        {"page_type": "CASE_DETAILS", "start_page": 1, "end_page": 2, "page_count": 2},
        {"page_type": "LAB_REPORT", "start_page": 3, "end_page": 3, "page_count": 1},
        {"page_type": "PATHOLOGY_REPORT", "start_page": 4, "end_page": 5, "page_count": 2},
        {"page_type": "SCAN_IMAGING", "start_page": 6, "end_page": 6, "page_count": 1},
    ]


def test_group_document_pages_into_sections_never_merges_non_consecutive_runs():
    """Two separate SCAN_IMAGING runs with a different type in between (e.g. a duplicate scan
    filed after an unrelated page) must stay two sections, not collapse into one."""
    pages = [
        {"page_number": 1, "page_type": "SCAN_IMAGING"},
        {"page_number": 2, "page_type": "CASE_DETAILS"},
        {"page_number": 3, "page_type": "SCAN_IMAGING"},
    ]
    sections = group_document_pages_into_sections(pages)
    assert len(sections) == 3


def test_group_document_pages_into_sections_empty_input():
    assert group_document_pages_into_sections([]) == []


def test_group_document_pages_into_sections_sorts_out_of_order_input():
    pages = [
        {"page_number": 2, "page_type": "LAB_REPORT"},
        {"page_number": 1, "page_type": "CASE_DETAILS"},
    ]
    sections = group_document_pages_into_sections(pages)
    assert [s["page_type"] for s in sections] == ["CASE_DETAILS", "LAB_REPORT"]


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


def test_nexus_brief_and_care_plan_prefill_never_show_a_pending_biomarker_as_resulted(db_session):
    """Real gap found via cross-reference against the Pathologist/Molecular Diagnostics gap
    report: synthesize_nexus_brief and generate_care_plan_prefill both used to query EVERY
    CCABiomarkerResult regardless of status, so a PENDING test (result_as_reported defaults to
    a placeholder string, e.g. "Pending") rendered inline as e.g. "HER2: Pending" with no visual
    distinction from a real, finalised result -- and, since a non-empty (but entirely PENDING)
    biomarker list made `not biomarkers` False, this also silently suppressed the NEXUS brief's
    "no biomarker/molecular results are on record" must-not-miss warning even though nothing was
    actually resulted yet. Both functions must now only ever consume status="RESULTED" rows,
    matching evaluate_guideline_readiness's existing (already-correct) filter on the same
    table."""
    patient = CCAPatient(mrn="TEST-MRN-BM01", name="Pending Biomarker Patient", age=49, sex="F", organization_id=1)
    db_session.add(patient)
    db_session.commit()
    # _must_not_miss_items' biomarker-gap warning is gated on `diagnosis and not biomarkers` --
    # needs a confirmed diagnosis on record for that branch to be reachable at all.
    diagnosis = CCACancerDiagnosis(patient_id=patient.id, primary_site="Breast", status="CONFIRMED")
    pending = CCABiomarkerResult(
        patient_id=patient.id, marker_name="HER2", result_as_reported="Pending", status="PENDING",
    )
    db_session.add_all([diagnosis, pending])
    db_session.commit()

    brief = synthesize_nexus_brief(db_session, patient.id)
    assert "Pending" not in brief["sections"]["4_biomarker_profile"]["content"]
    assert brief["sections"]["4_biomarker_profile"]["content"] == "Biomarker assessment pending."
    assert any("No biomarker/molecular results are on record" in item for item in brief["sections"]["14_must_not_miss"]["content"].split(". "))

    prefill_bm_str = ", ".join(f"{b.marker_name}: {b.result_as_reported}" for b in db_session.query(CCABiomarkerResult))
    assert "Pending" in prefill_bm_str  # sanity: the pending row genuinely exists and would have leaked through unfiltered


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
