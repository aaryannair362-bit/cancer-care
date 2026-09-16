"""
Break-testing pass for the CCA document-ingestion pipeline (routers/cca.py's upload_document,
ocr_service.py, cca_engine.py, document_pages.py) against documents the system has never seen
before -- see tests/doc_fixtures.py for how each one is built.

Runs entirely under the normal (fast, free) pytest regime: OCR_PROVIDER is pinned to "local"
and real Groq calls are blocked by tests/conftest.py's autouse fixtures, so every case here
exercises real pypdf/RapidOCR extraction and the real deterministic classifier/lab-value
scanner, with the LLM legs either mocked explicitly or (by default) failing closed -- which is
itself part of what's under test: nothing here may ever 500 or hang regardless of whether the
LLM call succeeds.

Central regression this file exists to catch: classify_and_extract_page (cca_engine.py) used to
skip fact extraction entirely whenever the deterministic keyword classifier confidently named a
page's type -- exactly the case for an obvious lab report. test_confidently_classified_pages_now_
get_real_fact_extraction pins the fix (every page/chunk gets a real extraction attempt now), and
would fail against the pre-fix code (zero calls for a confidently-classified page).
"""
import pytest

from app.models_cca import ClinicalFact, CCADocumentPage
from tests import doc_fixtures as df

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def uploader(make_user):
    return make_user(email="frontdesk@breaktest.aivana", role="CCAFrontDesk")


@pytest.fixture
def headers(auth_headers, uploader):
    return auth_headers(uploader)


@pytest.fixture
def patient_id(client, headers):
    res = client.post(
        "/api/cca/patients",
        json={"name": "Break Test Patient", "age": 55, "sex": "Female", "mobile": "+91 90000 00000"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["patient"]["id"]


def _upload(client, headers, patient_id, filename, content, content_type):
    return client.post(
        f"/api/cca/documents?patient_id={patient_id}",
        files={"file": (filename, content, content_type)},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Size/emptiness gates -- enforced before any parsing is even attempted.
# ---------------------------------------------------------------------------

def test_oversized_file_rejected_with_413(client, headers, patient_id):
    res = _upload(client, headers, patient_id, "huge.pdf", df.build("oversized"), "application/pdf")
    assert res.status_code == 413, res.text


def test_zero_byte_file_rejected_with_400(client, headers, patient_id):
    res = _upload(client, headers, patient_id, "empty.pdf", df.build("zero_byte"), "application/pdf")
    assert res.status_code == 400, res.text


# ---------------------------------------------------------------------------
# Malformed/unreadable documents -- must degrade to a saved, OCR_FAILED document,
# never a 500 and never an unhandled exception out of the background task.
# ---------------------------------------------------------------------------

def test_corrupted_pdf_is_saved_as_ocr_failed_not_500(client, headers, patient_id):
    res = _upload(client, headers, patient_id, "corrupted.pdf", df.build("corrupted"), "application/pdf")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["document"]["status"] == "OCR_FAILED"
    assert body["ocr_warning"]


def test_password_protected_pdf_gives_the_clear_error_message(client, headers, patient_id):
    """Regression: pypdf's decrypt("") does NOT raise for a real (non-empty) password -- it
    silently returns a falsy PasswordType and leaves the reader encrypted, so a bare try/except
    around the decrypt() call alone never actually caught this case; execution used to fall
    through into page.extract_text(), which raises pypdf's own FileNotDecryptedError, leaking a
    confusing internal message into ocr_error instead of the intended clear one. Fixed in
    ocr_service._extract_local by checking decrypt()'s return value."""
    res = _upload(client, headers, patient_id, "protected.pdf", df.build("password_protected"), "application/pdf")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["document"]["status"] == "OCR_FAILED"
    assert body["ocr_warning"] == "Password-protected PDFs are not supported"


# ---------------------------------------------------------------------------
# Structurally unusual but readable documents -- must extract successfully.
# ---------------------------------------------------------------------------

def test_multiframe_tiff_all_frames_are_captured(client, headers, patient_id, monkeypatch):
    """Isolates ocr_service._extract_local's per-frame Image.seek(i) loop from RapidOCR's own
    text-detection accuracy on a hand-drawn fixture image (real RapidOCR in this sandbox
    environment doesn't reliably detect text on synthetic PIL-drawn pages -- the same "No
    readable text was found" failure mode already occurs on unmodified `main` against this
    repo's own pre-existing real-image OCR fixtures, unrelated to this change). Mocking
    _run_ocr's return value keeps this test about frame counting, not OCR accuracy."""
    import app.ocr_service as ocr_service_module

    monkeypatch.setattr(ocr_service_module, "_run_ocr", lambda image: "Scanned page placeholder text")

    content = df.build("multiframe_tiff")
    res = _upload(client, headers, patient_id, "scan.tiff", content, "image/tiff")
    assert res.status_code == 201, res.text
    assert res.json()["document"]["page_count"] == 4


def test_mixed_image_and_text_document_does_not_crash(client, headers, patient_id):
    res = _upload(client, headers, patient_id, "mixed.pdf", df.build("mixed_image_and_text"), "application/pdf")
    assert res.status_code == 201, res.text
    assert res.json()["document"]["status"] in ("EXTRACTED", "OCR_FAILED")


def test_devanagari_scan_does_not_crash(client, headers, patient_id):
    """Out-of-scope-language OCR (see ocr_service.py's module docstring) is an expected
    degradation, not a bug -- the only hard requirement is that it never crashes the upload."""
    res = _upload(client, headers, patient_id, "devanagari.png", df.build("devanagari_scan"), "image/png")
    assert res.status_code == 201, res.text


# ---------------------------------------------------------------------------
# Tier 3: deterministic lab-value scanner (ocr_service._clinical_signals's "lab_values" key,
# bridged by cca_engine.extract_deterministic_lab_facts) -- these run over the FULL raw text
# with no LLM involved, so they must produce facts even though tests/conftest.py blocks every
# real Groq call by default.
# ---------------------------------------------------------------------------

def test_dense_lab_report_yields_many_deterministic_lab_facts_with_no_llm(client, headers, patient_id, db_session):
    res = _upload(client, headers, patient_id, "dense_labs.pdf", df.build("dense_single_page_lab_report"), "application/pdf")
    assert res.status_code == 201, res.text

    facts = db_session.query(ClinicalFact).filter(
        ClinicalFact.document_id == res.json()["document"]["id"], ClinicalFact.fact_type == "LAB_RESULT",
    ).all()
    values = {f.value for f in facts}
    # At least several distinct known aliases must have fired -- proves alias coverage, not just
    # a single lucky match. (Exact set intentionally not pinned -- this is a fixture-content
    # detail, not part of the contract.)
    assert len(values) >= 10, f"expected broad deterministic lab-value coverage, got: {values}"
    assert any(v.startswith("Hemoglobin:") for v in values)
    assert any(v.startswith("Creatinine:") for v in values)


def test_medications_line_yields_a_deterministic_medication_fact_with_no_llm(client, headers, patient_id, db_session):
    """Regression pin for the "labs extracted, medications silently dropped" production bug:
    unlike LAB_RESULT (extract_deterministic_lab_facts), MEDICATION facts used to come ONLY from
    the LLM pass (extract_clinical_facts) -- with real Groq calls blocked/failing-closed by
    default in this test module (see module docstring), a document with both a "Medications:"
    line and a dense lab panel used to yield LAB_RESULT facts but zero MEDICATION facts, even
    though the medications line was sitting right there in ocr_service._clinical_signals's
    output the whole time. extract_deterministic_medication_facts fixes that -- this must now
    produce at least one MEDICATION fact from the raw regex scan alone, no LLM involved."""
    res = _upload(client, headers, patient_id, "meds_and_labs.pdf", df.build("medications_and_labs_report"), "application/pdf")
    assert res.status_code == 201, res.text

    facts = db_session.query(ClinicalFact).filter(
        ClinicalFact.document_id == res.json()["document"]["id"],
    ).all()
    med_facts = [f for f in facts if f.fact_type == "MEDICATION"]
    lab_facts = [f for f in facts if f.fact_type == "LAB_RESULT"]
    assert med_facts, f"expected at least one deterministic MEDICATION fact, got fact_types: {[f.fact_type for f in facts]}"
    assert "Metformin" in med_facts[0].value
    # Labs must still be present too -- this document has both, same as the real one that
    # surfaced the bug.
    assert lab_facts, "expected deterministic LAB_RESULT facts alongside the medication fact"


def test_huge_90_page_document_extracts_lab_facts_from_pages_the_llm_pass_never_truncated_reach(
    client, headers, patient_id, db_session,
):
    """extract_clinical_facts (the whole-document LLM pass) is blocked entirely in this suite
    (real Groq calls are blocked by default -- see this file's module docstring), so it
    contributes nothing here regardless of document length. The deterministic scanner has no
    such dependency (it runs over the full raw OCR'd text, no LLM involved), so it must still
    find lab values placed deep in a 90-page document. This makes the test an unambiguous check
    of the deterministic path alone."""
    res = _upload(client, headers, patient_id, "huge_labs.pdf", df.build("huge_lab_report"), "application/pdf")
    assert res.status_code == 201, res.text
    assert res.json()["document"]["page_count"] == 90

    facts = db_session.query(ClinicalFact).filter(
        ClinicalFact.document_id == res.json()["document"]["id"], ClinicalFact.fact_type == "LAB_RESULT",
    ).all()
    assert len(facts) > 0, "deterministic lab-value scan produced nothing for a 90-page lab report"


# ---------------------------------------------------------------------------
# Tier 2: classify_and_extract_page must now attempt fact extraction on EVERY page/chunk, even
# one the deterministic classifier confidently named -- the exact gate this fix removed.
# ---------------------------------------------------------------------------

def test_confidently_classified_pages_now_get_real_fact_extraction(client, headers, patient_id, db_session, monkeypatch):
    """huge_lab_report_pdf's pages are all confidently keyword-classified as LAB (every page is
    full of "Hemoglobin:", "Creatinine:" etc. -- see cca_engine._DOCUMENT_CLASS_KEYWORDS["LAB"]).
    Before this fix, classify_and_extract_page returned early with facts=[] for every one of
    these 90 pages -- zero LLM calls, zero page-attributed facts, regardless of document length.
    This test mocks the LLM leg directly (scribe._generate_json) with a call-counting stub and
    asserts close to 90 real calls happened -- a number that was exactly 0 before this fix."""
    import app.scribe as scribe_module

    call_count = {"n": 0}

    def _fake_generate_json(prompt, system=None, max_tokens=None, **kwargs):
        if max_tokens == 6000:
            return {"facts": []}  # the whole-document extract_clinical_facts call -- not under test here
        call_count["n"] += 1
        return {
            "page_type": "LAB_REPORT", "confidence": 0.9,
            "facts": [{"fact_type": "COMORBIDITY", "value": f"synthetic-per-chunk-marker-{call_count['n']}", "verbatim": "n/a", "confidence": 0.9}],
        }

    monkeypatch.setattr(scribe_module.scribe, "_generate_json", _fake_generate_json)

    res = _upload(client, headers, patient_id, "huge_labs.pdf", df.build("huge_lab_report"), "application/pdf")
    assert res.status_code == 201, res.text
    doc_id = res.json()["document"]["id"]

    pages = db_session.query(CCADocumentPage).filter(CCADocumentPage.document_id == doc_id).all()
    assert len(pages) == 90
    # The LLM is authoritative for page_type (the stub above returns "LAB_REPORT" -- same value
    # the deterministic keyword classifier would also reach for this fixture, so this doesn't by
    # itself distinguish the two; test_llm_classification_wins_over_deterministic_keyword_guess
    # below pins the actual precedence).
    assert all(p.page_type == "LAB_REPORT" for p in pages)

    marker_facts = db_session.query(ClinicalFact).filter(
        ClinicalFact.document_id == doc_id, ClinicalFact.fact_type == "COMORBIDITY",
        ClinicalFact.value.like("synthetic-per-chunk-marker-%"),
    ).all()
    assert len(marker_facts) == 90, (
        f"expected one real LLM fact-extraction call per page (90), got {len(marker_facts)} -- "
        "classify_and_extract_page is still skipping fact extraction for confidently-classified pages"
    )
    max_page_number = max(f.page_number for f in marker_facts)
    assert max_page_number == 90, "the last page of a 90-page confidently-classified document never got its own extraction pass"


def test_large_page_chunk_text_is_extracted_in_slices_not_truncated(monkeypatch):
    """Regression: classify_and_extract_page used to send only text[:6000] to the LLM for a
    single page/chunk, silently dropping the rest. This matters most for the Sarvam path, where
    one "chunk" bundles up to 10 pages of markdown into ONE text blob (see ocr_service.
    _SARVAM_DOC_AI_MAX_PAGES_PER_JOB) -- a dense multi-page chunk routinely exceeds 6000
    characters, so everything past the first slice was getting zero AI-drafted facts. Pins the
    fix directly (no upload/OCR involved): the full text is now walked in bounded slices, one
    real extraction call per slice, with facts from every slice merged."""
    import app.scribe as scribe_module
    from app.cca_engine import classify_and_extract_page

    calls = []

    def _fake_generate_json(prompt, system=None, max_tokens=None, **kwargs):
        calls.append(prompt)
        return {
            "page_type": "LAB_REPORT", "confidence": 0.9,
            "facts": [{"fact_type": "LAB_RESULT", "value": f"slice-{len(calls)}-marker", "verbatim": "n/a", "confidence": 0.9}],
        }

    monkeypatch.setattr(scribe_module.scribe, "_generate_json", _fake_generate_json)

    # Confidently LAB-classifiable (keyword deterministic classifier), and well past two full
    # 6000-character slices.
    long_text = "Hemoglobin: 11.2 g/dL. Creatinine: 0.9 mg/dL. " * 350
    assert len(long_text) > 12000

    result = classify_and_extract_page(long_text, is_image_heavy=False)

    assert result["page_type"] == "LAB_REPORT"  # from the (mocked) LLM here -- see precedence test below
    assert len(calls) >= 3, f"expected one LLM call per ~6000-char slice, got {len(calls)}"
    values = {f["value"] for f in result["facts"]}
    assert len(values) == len(calls), "facts from every slice should be merged, not just the first"


def test_llm_classification_wins_over_deterministic_keyword_guess(monkeypatch):
    """Regression: page_type used to be decided by the free deterministic keyword classifier
    whenever it was confident, with the LLM's own page_type only ever filling in for a page the
    keyword classifier couldn't name at all. The LLM is now authoritative -- it sees the real
    page text, not a fixed keyword list -- so its answer must win even when the keyword
    classifier is ALSO confident, just about a different type."""
    import app.scribe as scribe_module
    from app.cca_engine import classify_and_extract_page

    # Confidently keyword-classified as LAB ("hemoglobin"/"creatinine" -- see
    # _DOCUMENT_CLASS_KEYWORDS["LAB"]), but the LLM (mocked) reads it as a pathology report.
    text = "Hemoglobin: 11.2 g/dL. Creatinine: 0.9 mg/dL. Final impression follows below."

    def _fake_generate_json(prompt, system=None, max_tokens=None, **kwargs):
        return {"page_type": "PATHOLOGY_REPORT", "confidence": 0.88, "facts": []}

    monkeypatch.setattr(scribe_module.scribe, "_generate_json", _fake_generate_json)

    result = classify_and_extract_page(text, is_image_heavy=False)

    assert result["page_type"] == "PATHOLOGY_REPORT", (
        f"expected the LLM's classification to win over the deterministic keyword guess (LAB), "
        f"got {result['page_type']!r}"
    )
    assert result["confidence"] == 0.88


def test_deterministic_classifier_is_fallback_when_llm_unavailable(monkeypatch):
    """The deterministic keyword classifier must still provide a best-effort page_type when
    every LLM call for a page fails outright (network error, Groq outage, malformed response) --
    AI enrichment failing must never collapse a keyword-obvious page to UNCLASSIFIED."""
    import app.scribe as scribe_module
    from app.cca_engine import classify_and_extract_page

    def _raise(*a, **k):
        raise RuntimeError("simulated Groq outage")

    monkeypatch.setattr(scribe_module.scribe, "_generate_json", _raise)

    text = "Hemoglobin: 11.2 g/dL. Creatinine: 0.9 mg/dL."
    result = classify_and_extract_page(text, is_image_heavy=False)

    assert result["page_type"] == "LAB_REPORT"
    assert result["facts"] == []
