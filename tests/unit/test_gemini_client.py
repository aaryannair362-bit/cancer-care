"""
Unit tests for app.gemini_client._post_with_retry -- the Gemini generateContent error-handling
path backing extract_clinical_facts_with_identity/classify_and_extract_page (cca_engine.py).

Mirrors tests/unit/test_scribe_audio_transcription.py's PHI-safety test for the equivalent Groq
_post_with_retry: an upstream error body can echo request content back, so the raw body must
only ever be logged at DEBUG. Added alongside the 2026-09-16 production incident where a Gemini
403 (survived a full process restart, so not this app's own rate limiter) was previously
indistinguishable in ERROR-level logs from a generic network failure -- this locks in that a
403/other non-429 failure now surfaces its status code and a fixed, content-free Google RPC
reason code (e.g. "PERMISSION_DENIED") at ERROR, without ever promoting the raw response body.
"""
import json
import logging

import pytest
import requests

from app import gemini_client


class _JsonErrorResponse:
    def __init__(self, status_code, error_status, message):
        self.status_code = status_code
        self.text = json.dumps({"error": {"code": status_code, "status": error_status, "message": message}})

    def raise_for_status(self):
        raise requests.exceptions.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return json.loads(self.text)


class _PlainTextErrorResponse:
    status_code = 500
    text = "upstream HTML error page that could echo request content back"

    def raise_for_status(self):
        raise requests.exceptions.HTTPError("500 error", response=self)

    def json(self):
        raise ValueError("not json")


def test_403_forbidden_logs_status_and_reason_not_response_body(monkeypatch, caplog):
    resp = _JsonErrorResponse(403, "PERMISSION_DENIED", "leaked-request-content-marker")
    monkeypatch.setattr(gemini_client.requests, "post", lambda *a, **k: resp)
    caplog.set_level(logging.ERROR, logger="app.gemini_client")

    with pytest.raises(requests.exceptions.HTTPError):
        gemini_client._post_with_retry("https://example.invalid/x", "fake-key", {})

    assert "status_code=403" in caplog.text
    assert "reason=PERMISSION_DENIED" in caplog.text
    assert "GEMINI_API_KEY project in Google AI Studio" in caplog.text
    assert "leaked-request-content-marker" not in caplog.text


def test_non_json_error_body_stays_generic_at_error_level(monkeypatch, caplog):
    monkeypatch.setattr(gemini_client.requests, "post", lambda *a, **k: _PlainTextErrorResponse())
    caplog.set_level(logging.ERROR, logger="app.gemini_client")

    with pytest.raises(requests.exceptions.HTTPError):
        gemini_client._post_with_retry("https://example.invalid/x", "fake-key", {})

    assert "status_code=500" in caplog.text
    assert "reason=None" in caplog.text
    assert "upstream HTML error page" not in caplog.text


def test_403_full_body_still_available_at_debug_for_deliberate_deep_debugging(monkeypatch, caplog):
    resp = _JsonErrorResponse(403, "PERMISSION_DENIED", "leaked-request-content-marker")
    monkeypatch.setattr(gemini_client.requests, "post", lambda *a, **k: resp)
    caplog.set_level(logging.DEBUG, logger="app.gemini_client")

    with pytest.raises(requests.exceptions.HTTPError):
        gemini_client._post_with_retry("https://example.invalid/x", "fake-key", {})

    assert "leaked-request-content-marker" in caplog.text
