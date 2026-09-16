"""
Unit tests for app.scribe.ScribeEngine's deterministic plumbing: JSON-fence stripping,
JSON parsing, the regex-based fallback extractor, and default-key backfilling.

These tests instantiate ScribeEngine directly and monkeypatch `_call_groq_api` -- no network
calls are made, and this does NOT use the app-wide `scribe` singleton from app.main, so it's
independent of the FastAPI app/DB fixtures in conftest.py.
"""
import json

import pytest

from app.scribe import ScribeEngine


DEFAULT_KEYS = {"chiefComplaint", "hpi", "primaryDiagnosis", "differentialDiagnosis",
                "medications", "advice", "labTests"}


@pytest.fixture
def engine():
    return ScribeEngine()


def _stub_call(engine, raw_return=None, raise_exc=None):
    def _fake(prompt, system=None, temperature=0.3, max_tokens=3000, **kwargs):
        if raise_exc is not None:
            raise raise_exc
        return raw_return
    engine._call_groq_api = _fake


def test_generate_json_parses_clean_json(engine):
    payload = {"chiefComplaint": "fever", "medications": []}
    _stub_call(engine, raw_return=json.dumps(payload))
    result = engine._generate_json("prompt")
    assert result == payload


def test_generate_json_strips_json_fenced_markdown(engine):
    payload = {"chiefComplaint": "fever"}
    raw = "```json\n" + json.dumps(payload) + "\n```"
    _stub_call(engine, raw_return=raw)
    result = engine._generate_json("prompt")
    assert result == payload


def test_generate_json_strips_bare_fenced_markdown(engine):
    payload = {"chiefComplaint": "cough"}
    raw = "```\n" + json.dumps(payload) + "\n```"
    _stub_call(engine, raw_return=raw)
    result = engine._generate_json("prompt")
    assert result == payload


def test_generate_json_extracts_json_embedded_in_prose(engine):
    """Regression: verified live that some models wrap the JSON in explanatory prose despite
    the prompt asking for pure JSON ("Based on the transcript, here's..." before it, "Note
    that the hpi field is empty because..." after it). The fence-stripping above only handles
    a response that IS the JSON (optionally fenced) -- not JSON embedded inside other text."""
    payload = {"chiefComplaint": "fever", "medications": []}
    raw = (
        "Based on the provided transcript, here's the structured JSON object:\n\n"
        f"```json\n{json.dumps(payload)}\n```\n\n"
        "Note that the hpi field is empty because no clinical findings were mentioned."
    )
    _stub_call(engine, raw_return=raw)
    result = engine._generate_json("prompt")
    assert result == payload


def test_generate_json_returns_empty_dict_on_malformed_json_with_no_fallback(engine):
    """Regression: _generate_json used to hand EVERY caller the scribe-note-shaped
    _fallback_extract dict on a parse failure, regardless of what shape that caller actually
    expected -- classify_and_extract_page (page_type/confidence/facts),
    extract_clinical_facts ({"facts": [...]}) and generate_discharge_summary
    (admissionSummary/hospitalCourse/...) would silently get a dict with none of their keys
    and treat it as "the model said nothing", instead of the caller's own, correct empty-dict
    handling. `fallback` must now be opt-in per call site; with none supplied, a malformed
    response degrades to {} instead of a wrong-shaped dict."""
    _stub_call(engine, raw_return="This is not JSON at all { broken")
    result = engine._generate_json("prompt")
    assert result == {}


def test_generate_json_uses_supplied_fallback_on_malformed_json(engine):
    """The scribe-note callers (_extract_note_fields, translate_prescription) explicitly pass
    fallback=self._fallback_extract and must still get the regex-recovered dict back."""
    _stub_call(engine, raw_return="This is not JSON at all { broken")
    result = engine._generate_json("prompt", fallback=engine._fallback_extract)
    assert DEFAULT_KEYS.issubset(result.keys())
    assert isinstance(result["medications"], list)
    assert isinstance(result["labTests"], list)


def test_generate_json_retries_once_on_parse_failure_before_falling_back(engine):
    """A JSON parse failure gets one same-prompt retry before giving up -- verified live, a
    reasoning-capable model occasionally emits almost-valid JSON with a stray formatting slip
    that a retry often just doesn't repeat."""
    calls = []

    def _fake(prompt, system=None, temperature=0.3, max_tokens=3000, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "not json { broken"
        return json.dumps({"page_type": "LAB", "confidence": 0.9, "facts": []})

    engine._call_groq_api = _fake
    result = engine._generate_json("prompt")
    assert len(calls) == 2
    assert result == {"page_type": "LAB", "confidence": 0.9, "facts": []}


def test_generate_json_gives_up_after_one_retry(engine):
    calls = []

    def _fake(prompt, system=None, temperature=0.3, max_tokens=3000, **kwargs):
        calls.append(1)
        return "still not json { broken"

    engine._call_groq_api = _fake
    result = engine._generate_json("prompt")
    assert len(calls) == 2  # original attempt + exactly one retry, then gives up
    assert result == {}


def test_generate_json_returns_failure_sentinel_when_groq_call_raises(engine):
    """
    A Groq/network failure inside _call_groq_api degrades to {"__ai_call_failed__": True}
    rather than propagating -- never a bare 500 to the OPD user. Previously this was a bare {},
    indistinguishable from "the model looked at the transcript and legitimately found nothing" --
    verified live via a real duration-scaling test run (5-60 min synthetic consultations against
    the live Groq API) that this is not a theoretical concern: a transcript well under
    _MAX_TRANSCRIPT_CHARS_FOR_SCRIBING's single-call threshold still sometimes exhausted
    MAX_RATE_LIMIT_RETRIES on a spurious 413, and a long (50-60 min) consultation's own burst of
    sequential chunk calls sometimes exhausted retries on real 429s -- both landed the doctor a
    blank draft with zero indication anything failed. The sentinel is popped and turned into an
    explicit flag by every caller that reaches a doctor/patient-facing result (scribe_transcript's
    noteExtractionFailed, translate_prescription's translationFailed,
    generate_discharge_summary's dischargeSummaryFailed) -- see their own tests.
    """
    _stub_call(engine, raise_exc=RuntimeError("simulated network failure"))
    result = engine._generate_json("prompt")
    assert result == {"__ai_call_failed__": True}


def test_fallback_extract_never_raises_on_arbitrary_text(engine):
    weird_inputs = [
        "",
        "   ",
        "random text with no headings whatsoever",
        "Chief Complaint: fever and cough\nMedications: Paracetamol 500mg\n",
        "नमस्ते unicode Hinglish स्वास्थ्य",
    ]
    for text in weird_inputs:
        result = engine._fallback_extract(text)
        assert DEFAULT_KEYS.issubset(result.keys())
        assert isinstance(result["medications"], list)
        assert isinstance(result["labTests"], list)


def test_scribe_transcript_backfills_missing_keys(engine):
    _stub_call(engine, raw_return=json.dumps({"chiefComplaint": "headache"}))
    result = engine.scribe_transcript("some transcript text long enough")
    assert result["chiefComplaint"] == "headache"
    for key in DEFAULT_KEYS - {"chiefComplaint"}:
        assert key in result
    assert result["medications"] == []
    assert result["labTests"] == []
    assert result["hpi"] == ""


def test_scribe_transcript_chunks_very_long_transcripts_instead_of_truncating(engine):
    """Regression test for a real bug found live: Groq's real account-level limit is 8000
    tokens/minute (verified against live response headers, on both the standard and
    higher-tier "Prod" key) -- a single Groq call for a genuinely long consultation needs more
    tokens than that in ONE request, which token_bucket.consume() can never satisfy. That was
    previously "fixed" by truncating the transcript before it ever reached the prompt --
    correctly flagged, but still silently dropping real content past the cap. Now a long
    transcript is instead split into multiple chunks (each comfortably under the per-call
    limit), each extracted with its own call, and merged -- no content is discarded, just
    processed across more than one paced request."""
    prompt_lens = []

    def _fake(prompt, system=None, temperature=0.3, max_tokens=3000, **kwargs):
        prompt_lens.append(len(prompt))
        return json.dumps({"chiefComplaint": "fever"})

    engine._call_groq_api = _fake
    long_transcript = "Doctor: how are you feeling today. Patient: not well. " * 1000  # far over one chunk's cap
    result = engine.scribe_transcript(long_transcript)

    from app.scribe import _MAX_TRANSCRIPT_CHARS_FOR_SCRIBING
    assert len(prompt_lens) > 1  # more than one Groq call -- chunked, not a single truncated call
    for prompt_len in prompt_lens:
        assert prompt_len < _MAX_TRANSCRIPT_CHARS_FOR_SCRIBING + 2000  # each call's own chunk + prompt overhead
    assert result["transcriptChunked"] is True
    assert result["chiefComplaint"] == "fever"


def test_scribe_transcript_does_not_flag_a_normal_length_transcript(engine):
    _stub_call(engine, raw_return=json.dumps({"chiefComplaint": "cough"}))
    result = engine.scribe_transcript("Doctor: how are you. Patient: I have a cough for two days.")
    assert result["transcriptChunked"] is False


def test_scribe_transcript_backfills_explicit_null_values():
    """JSON `null` for a key (parsed as Python None) must be treated as missing, not kept as None."""
    engine = ScribeEngine()
    _stub_call(engine, raw_return=json.dumps({
        "chiefComplaint": "cough", "medications": None, "labTests": None,
    }))
    result = engine.scribe_transcript("some transcript text long enough")
    assert result["medications"] == []
    assert result["labTests"] == []


def test_scribe_transcript_coerces_list_shaped_differential_diagnosis_to_string(engine):
    """Regression: verified live against the real model that it sometimes returns
    differentialDiagnosis as a JSON array instead of the prompted comma-separated string.
    Consultation.differential_diagnosis is a Text column -- persisting a raw Python list
    there crashes with sqlite3.ProgrammingError: type 'list' is not supported. Every string
    field must come out of scribe_transcript as an actual string regardless of what shape the
    model returned."""
    _stub_call(engine, raw_return=json.dumps({
        "chiefComplaint": "fever", "differentialDiagnosis": ["Viral fever", "Dengue", "Typhoid"],
    }))
    result = engine.scribe_transcript("some transcript text long enough")
    assert isinstance(result["differentialDiagnosis"], str)
    assert result["differentialDiagnosis"] == "Viral fever, Dengue, Typhoid"


def test_scribe_transcript_coerces_non_string_scalar_fields_to_string(engine):
    _stub_call(engine, raw_return=json.dumps({"primaryDiagnosis": 42}))
    result = engine.scribe_transcript("some transcript text long enough")
    assert result["primaryDiagnosis"] == "42"


def test_scribe_transcript_preserves_provided_medications_list(engine):
    """Confirms the medications array structurally survives JSON parsing + default-backfill
    intact (right key, right length, other fields untouched) -- drugName itself is expected to
    go through drug_matcher's real correction (including its bare-name path, see
    tests/unit/test_drug_matcher.py for that behavior in detail), not verbatim passthrough."""
    meds = [{"drugName": "Paracetamol", "dose": "650mg", "frequency": "SOS", "route": "Oral", "duration": "5 days"}]
    _stub_call(engine, raw_return=json.dumps({"medications": meds}))
    result = engine.scribe_transcript("some transcript text long enough")
    assert len(result["medications"]) == 1
    corrected = result["medications"][0]
    assert corrected["dose"] == "650mg"
    assert corrected["frequency"] == "SOS"
    assert corrected["route"] == "Oral"
    assert corrected["duration"] == "5 days"
    assert "paracetamol" in corrected["drugName"].lower()


def test_system_prompt_forbids_medications_in_hpi_or_chief_complaint(engine):
    """Regression: verified live that the model folded medication names/doses into the hpi
    field (doctor says "for the vomiting I gave him Ofloxil" -> hpi ended up containing
    "...vomiting, paracetamol 250 mg, Ofloxil" verbatim, duplicating what's already in the
    medications array) because the old prompt only said clinical findings MUST be included in
    hpi, with nothing excluding medications -- the model treated a just-extracted drug name as
    a "finding". Pins down that both the system prompt and the per-call field descriptions
    explicitly forbid this now, rather than re-deriving the exact wording."""
    assert "medication" in engine.system_prompt.lower()
    assert "never" in engine.system_prompt.lower()


def test_scribe_transcript_prompt_field_descriptions_exclude_medications(engine):
    captured = {}

    def _fake(prompt, system=None, temperature=0.3, max_tokens=3000, **kwargs):
        captured["prompt"] = prompt
        return json.dumps({})

    engine._call_groq_api = _fake
    engine.scribe_transcript("some transcript text long enough")
    prompt_lower = captured["prompt"].lower()
    assert "never" in prompt_lower
    assert "medication" in prompt_lower


def test_translate_prescription_english_is_passthrough_no_llm_call(engine):
    called = {"count": 0}

    def _fake(*args, **kwargs):
        called["count"] += 1
        return "{}"

    engine._call_groq_api = _fake
    draft = {"chiefComplaint": "fever", "medications": []}
    result = engine.translate_prescription(draft, "English")
    assert result == draft
    assert called["count"] == 0


def test_translate_prescription_non_english_calls_llm_and_backfills(engine):
    _stub_call(engine, raw_return=json.dumps({"chiefComplaint": "बुखार"}))
    draft = {"chiefComplaint": "fever", "medications": [{"drugName": "Paracetamol"}]}
    result = engine.translate_prescription(draft, "Hindi")
    assert result["chiefComplaint"] == "बुखार"
    assert "medications" in result  # backfilled to [] since translated response omitted it
    assert result["translationFailed"] is False


def test_scribe_transcript_flags_note_extraction_failed_on_real_api_failure(engine):
    """A real API failure (see test_generate_json_returns_failure_sentinel_when_groq_call_raises)
    must be distinguishable from the model legitimately finding nothing -- both used to produce
    the exact same all-blank draft with zero signal to the doctor that anything went wrong.
    Fields still backfill to blank either way (never raise/500 the OPD user); only the new flag
    tells the two cases apart."""
    _stub_call(engine, raise_exc=RuntimeError("simulated network failure"))
    result = engine.scribe_transcript("some transcript text long enough")
    assert result["noteExtractionFailed"] is True
    assert result["chiefComplaint"] == ""
    assert result["medications"] == []


def test_scribe_transcript_does_not_flag_extraction_failed_on_legitimate_empty_result(engine):
    """The model successfully returning a real (if sparse) response -- e.g. a very short
    consultation with genuinely little to extract -- must NOT be flagged as a failure."""
    _stub_call(engine, raw_return=json.dumps({"chiefComplaint": "", "medications": []}))
    result = engine.scribe_transcript("Doctor: anything else? Patient: no, that's all.")
    assert result["noteExtractionFailed"] is False


def test_scribe_transcript_flags_note_extraction_failed_if_any_chunk_fails(engine):
    """Chunked (long) transcripts: if even ONE chunk's own Groq call genuinely fails, the merged
    draft is missing whatever that chunk actually contained -- the whole consultation's draft
    must be flagged, not just silently merged as if every chunk succeeded."""
    calls = {"n": 0}

    def _fake(prompt, system=None, temperature=0.3, max_tokens=3000, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated network failure on the second chunk")
        return json.dumps({"chiefComplaint": "fever"})

    engine._call_groq_api = _fake
    long_transcript = "Doctor: how are you feeling today. Patient: not well. " * 1000
    result = engine.scribe_transcript(long_transcript)
    assert result["transcriptChunked"] is True
    assert result["noteExtractionFailed"] is True


def test_translate_prescription_flags_translation_failed_and_preserves_original_draft(engine):
    """A real translation-API failure must never discard the already-correct English draft in
    favor of an all-blank one -- the doctor still has a usable (untranslated) prescription,
    with an explicit flag so the UI can show it wasn't actually translated."""
    _stub_call(engine, raise_exc=RuntimeError("simulated network failure"))
    draft = {"chiefComplaint": "fever", "medications": [{"drugName": "Paracetamol"}]}
    result = engine.translate_prescription(draft, "Hindi")
    assert result["translationFailed"] is True
    assert result["chiefComplaint"] == "fever"  # original English content preserved, not blanked
    assert result["medications"] == [{"drugName": "Paracetamol"}]


def test_generate_discharge_summary_flags_failure_on_real_api_failure(engine):
    _stub_call(engine, raise_exc=RuntimeError("simulated network failure"))
    result = engine.generate_discharge_summary({"patient_name": "Test"})
    assert result["dischargeSummaryFailed"] is True
    assert result["admissionSummary"] == ""


def test_generate_discharge_summary_does_not_flag_failure_on_success(engine):
    _stub_call(engine, raw_return=json.dumps({"admissionSummary": "Admitted for observation"}))
    result = engine.generate_discharge_summary({"patient_name": "Test"})
    assert result["dischargeSummaryFailed"] is False
    assert result["admissionSummary"] == "Admitted for observation"


def test_call_groq_api_retries_on_429_and_succeeds(monkeypatch, engine):
    """Regression: a 429 used to fall straight through to the caller, silently degrading a
    real consultation to an empty draft on a transient rate-limit blip -- verified live
    against the real API, this was happening frequently enough to noticeably hurt extraction
    quality. Must retry (honoring Retry-After if present) instead of giving up immediately."""
    engine.api_key = "some-key"
    calls = []

    class _RateLimited:
        status_code = 429
        headers = {"retry-after": "0.01"}

    class _Success:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"chiefComplaint": "ok"}'}}]}

    def _fake_post(*a, **k):
        calls.append(1)
        return _RateLimited() if len(calls) == 1 else _Success()

    monkeypatch.setattr("app.scribe.requests.post", _fake_post)
    monkeypatch.setattr("app.scribe.time.sleep", lambda *a, **k: None)

    result = engine._call_groq_api("some prompt")
    assert result == '{"chiefComplaint": "ok"}'
    assert len(calls) == 2  # first call 429'd, second succeeded


@pytest.mark.parametrize("status", [413, 502, 503, 504])
def test_call_groq_api_retries_on_transient_error_statuses(monkeypatch, engine, status):
    """Regression: found live -- a genuinely small (~27KB) request got a 413 back from Groq,
    and replaying the identical payload seconds later succeeded, confirming it was a transient
    hiccup, not a real payload-size problem. Before this fix, any non-429 error status fell
    straight through to the caller with no retry at all, silently degrading to an empty draft
    exactly like the old unretried-429 bug this file already regression-tests above."""
    engine.api_key = "some-key"
    calls = []

    class _Transient:
        def __init__(self):
            self.status_code = status
            self.headers = {}

    class _Success:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"chiefComplaint": "ok"}'}}]}

    def _fake_post(*a, **k):
        calls.append(1)
        return _Transient() if len(calls) == 1 else _Success()

    monkeypatch.setattr("app.scribe.requests.post", _fake_post)
    monkeypatch.setattr("app.scribe.time.sleep", lambda *a, **k: None)

    result = engine._call_groq_api("some prompt")
    assert result == '{"chiefComplaint": "ok"}'
    assert len(calls) == 2


def test_call_groq_api_drops_reasoning_format_when_model_rejects_it(monkeypatch, engine):
    """Regression: verified live that swapping GROQ_MODEL to a non-reasoning model
    (llama-3.1-8b-instant) made every call 400 with `reasoning_format is not supported with
    this model` -- reasoning_format was being sent unconditionally. Must detect that specific
    rejection once and stop sending the param for the rest of this engine's lifetime, rather
    than fail every single call forever."""
    engine.api_key = "some-key"
    seen_payloads = []

    class _Rejected:
        status_code = 400
        text = '{"error":{"message":"`reasoning_format` is not supported with this model","param":"reasoning_format"}}'

        def raise_for_status(self):
            import requests
            raise requests.exceptions.HTTPError(self.text, response=self)

    class _Success:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        seen_payloads.append(json)
        return _Rejected() if "reasoning_format" in json else _Success()

    monkeypatch.setattr("app.scribe.requests.post", _fake_post)

    assert engine._call_groq_api("prompt one") == "ok"
    assert "reasoning_format" in seen_payloads[0]
    assert "reasoning_format" not in seen_payloads[1]
    assert engine._reasoning_format_supported is False

    # A second, independent call must not even attempt reasoning_format anymore.
    seen_payloads.clear()
    assert engine._call_groq_api("prompt two") == "ok"
    assert len(seen_payloads) == 1
    assert "reasoning_format" not in seen_payloads[0]


def test_call_groq_api_caps_retry_after_instead_of_honoring_it_literally(monkeypatch, engine):
    """Regression: verified live that under sustained quota pressure Groq's Retry-After can
    be minutes (observed up to ~1600s / 26+ min). Honoring that literally would hang a single
    request for that long -- a doctor waiting on their consultation, or (worse, pre-
    threadpool-fix) the entire server for every concurrent user. Must be capped."""
    engine.api_key = "some-key"
    sleep_calls = []

    class _RateLimited:
        status_code = 429
        headers = {"retry-after": "1599"}

    class _Success:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    calls = []

    def _fake_post(*a, **k):
        calls.append(1)
        return _RateLimited() if len(calls) == 1 else _Success()

    monkeypatch.setattr("app.scribe.requests.post", _fake_post)
    monkeypatch.setattr("app.scribe.time.sleep", lambda s: sleep_calls.append(s))

    engine._call_groq_api("some prompt")
    assert sleep_calls == [20.0]  # capped, not the literal 1599s the server asked for


def test_call_groq_api_gives_up_after_max_retries(monkeypatch, engine):
    engine.api_key = "some-key"

    class _RateLimited:
        status_code = 429
        headers = {}

        def raise_for_status(self):
            import requests
            raise requests.exceptions.HTTPError("429 after retries")

    monkeypatch.setattr("app.scribe.requests.post", lambda *a, **k: _RateLimited())
    monkeypatch.setattr("app.scribe.time.sleep", lambda *a, **k: None)

    import pytest as _pytest
    import requests
    with _pytest.raises(requests.exceptions.HTTPError):
        engine._call_groq_api("some prompt")


def test_is_available_false_when_no_api_key():
    engine = ScribeEngine()
    engine.api_key = ""
    assert engine.is_available() is False


def test_is_available_true_when_groq_models_endpoint_ok(monkeypatch, engine):
    class _Resp:
        status_code = 200

    monkeypatch.setattr("app.scribe.requests.get", lambda *a, **k: _Resp())
    engine.api_key = "some-key"
    assert engine.is_available() is True


def test_is_available_false_on_request_exception(monkeypatch, engine):
    def _raise(*a, **k):
        raise Exception("network down")

    monkeypatch.setattr("app.scribe.requests.get", _raise)
    engine.api_key = "some-key"
    assert engine.is_available() is False
