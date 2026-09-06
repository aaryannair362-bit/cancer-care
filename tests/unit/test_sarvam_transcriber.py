"""
Unit tests for app.sarvam_transcriber -- the Sarvam Saaras v3 speech-to-text-translate call
backing the opt-in TRANSCRIPTION_PROVIDER="sarvam" audio path (see main.py's
transcribe_audio_endpoint and config.py's TRANSCRIPTION_PROVIDER comment). Mirrors
test_scribe_audio_transcription.py's style: fake requests.post response objects, no network
calls, real contract details (endpoint, auth header, model/mode fields) verified live against
the actual Sarvam API before this module was written -- see sarvam_transcriber.py's docstring.
"""
import logging

import pytest

from app import sarvam_transcriber


class _Success:
    status_code = 200

    def __init__(self, transcript, language_code="hi-IN", language_probability=0.9):
        self._body = {
            "request_id": "test-request-id",
            "transcript": transcript,
            "language_code": language_code,
            "language_probability": language_probability,
        }

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _ServerError:
    status_code = 500
    text = "upstream detail that could echo request content back"

    def raise_for_status(self):
        import requests
        raise requests.exceptions.HTTPError("500 error", response=self)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(sarvam_transcriber.settings, "SARVAM_API_KEY", "test-key")


def test_transcribe_one_chunk_posts_correct_contract(monkeypatch):
    """Verified live against the real API before this was written: endpoint URL, the
    api-subscription-key header (not Authorization: Bearer, which every other provider in
    this app uses -- easy to get wrong by habit), and model=saaras:v3/mode=translate fields."""
    seen = {}

    def _fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["files"] = files
        seen["data"] = data
        seen["timeout"] = timeout
        return _Success("patient has fever for three days")

    monkeypatch.setattr(sarvam_transcriber.requests, "post", _fake_post)

    result = sarvam_transcriber._transcribe_one_chunk(b"fake-audio-bytes", "audio/webm", "chunk_0.webm")

    assert result == "patient has fever for three days"
    assert seen["url"] == "https://api.sarvam.ai/speech-to-text"
    assert seen["headers"] == {"api-subscription-key": "test-key"}
    filename, content, content_type = seen["files"]["file"]
    assert filename == "chunk_0.webm"
    assert content == b"fake-audio-bytes"
    assert content_type == "audio/webm"
    assert seen["data"] == {"model": "saaras:v3", "mode": "translate", "language_code": "unknown"}


@pytest.mark.parametrize("browser_content_type,expected", [
    ("audio/webm;codecs=opus", "audio/webm"),
    ("audio/webm; codecs=opus", "audio/webm"),
    ("audio/mp4;codecs=mp4a.40.2", "audio/mp4"),
    ("audio/webm", "audio/webm"),
    ("", ""),
    (None, None),
])
def test_normalize_content_type_strips_codec_parameter(browser_content_type, expected):
    assert sarvam_transcriber._normalize_content_type(browser_content_type) == expected


def test_transcribe_one_chunk_strips_codec_parameter_before_sending(monkeypatch):
    """
    Regression test for a real, live-confirmed production bug: Chrome's MediaRecorder
    produces "audio/webm;codecs=opus" by default (voice-capture.js's MIME_CANDIDATES picks
    it first), but Sarvam's file-type allowlist matches MIME types exactly and rejects that
    parameterized form with a 400 "Invalid file type" -- even though the bare "audio/webm" it
    reduces to IS on Sarvam's own allowlist. Reproduced end-to-end with a real browser
    (Playwright + Chromium's fake-audio-capture, hitting the live Sarvam API) before this fix;
    confirmed fixed the same way afterward. This test pins the fix at the unit level so it
    can't silently regress.
    """
    seen = {}

    def _fake_post(url, headers=None, files=None, data=None, timeout=None):
        seen["files"] = files
        return _Success("ok")

    monkeypatch.setattr(sarvam_transcriber.requests, "post", _fake_post)

    sarvam_transcriber._transcribe_one_chunk(b"real-webm-bytes", "audio/webm;codecs=opus", "chunk_0.webm")

    _, _, sent_content_type = seen["files"]["file"]
    assert sent_content_type == "audio/webm"


def test_transcribe_one_chunk_strips_whitespace(monkeypatch):
    monkeypatch.setattr(sarvam_transcriber.requests, "post", lambda *a, **k: _Success("  hello there  "))
    assert sarvam_transcriber._transcribe_one_chunk(b"x", "audio/webm", "c.webm") == "hello there"


def test_transcribe_one_chunk_missing_transcript_key_returns_empty_string(monkeypatch):
    class _NoTranscript:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"request_id": "x"}

    monkeypatch.setattr(sarvam_transcriber.requests, "post", lambda *a, **k: _NoTranscript())
    assert sarvam_transcriber._transcribe_one_chunk(b"x", "audio/webm", "c.webm") == ""


def test_transcribe_one_chunk_raises_without_api_key(monkeypatch):
    monkeypatch.setattr(sarvam_transcriber.settings, "SARVAM_API_KEY", "")
    with pytest.raises(ValueError):
        sarvam_transcriber._transcribe_one_chunk(b"x", "audio/webm", "c.webm")


def test_transcribe_one_chunk_retries_transient_failure_then_succeeds(monkeypatch):
    calls = []

    def _fake_post(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            return _ServerError()
        return _Success("ok")

    monkeypatch.setattr(sarvam_transcriber.requests, "post", _fake_post)
    monkeypatch.setattr(sarvam_transcriber.time, "sleep", lambda *a, **k: None)

    assert sarvam_transcriber._transcribe_one_chunk(b"x", "audio/webm", "c.webm") == "ok"
    assert len(calls) == 2


def test_transcribe_one_chunk_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(sarvam_transcriber.requests, "post", lambda *a, **k: _ServerError())
    monkeypatch.setattr(sarvam_transcriber.time, "sleep", lambda *a, **k: None)

    import requests
    with pytest.raises(requests.exceptions.HTTPError):
        sarvam_transcriber._transcribe_one_chunk(b"x", "audio/webm", "c.webm")


def test_transcribe_one_chunk_error_logs_generic_message_not_response_body(monkeypatch, caplog):
    """Same PHI convention as scribe.py: a Sarvam error response body could echo request
    content back, so ERROR-level logs (what a default-configured aggregator captures) must
    stay generic."""
    monkeypatch.setattr(sarvam_transcriber.requests, "post", lambda *a, **k: _ServerError())
    monkeypatch.setattr(sarvam_transcriber.time, "sleep", lambda *a, **k: None)
    caplog.set_level(logging.ERROR, logger="app.sarvam_transcriber")

    import requests
    with pytest.raises(requests.exceptions.HTTPError):
        sarvam_transcriber._transcribe_one_chunk(b"x", "audio/webm", "c.webm")

    assert "upstream detail" not in caplog.text


def test_transcribe_chunks_joins_results_in_order(monkeypatch):
    calls = []

    def _fake_one(audio_bytes, content_type, filename):
        calls.append(filename)
        return {"chunk_0.webm": "patient reports fever", "chunk_1.webm": "for three days"}[filename]

    monkeypatch.setattr(sarvam_transcriber, "_transcribe_one_chunk", _fake_one)

    chunks = [
        (b"a", "audio/webm", "chunk_0.webm"),
        (b"b", "audio/webm", "chunk_1.webm"),
    ]
    result = sarvam_transcriber.transcribe_chunks(chunks)

    assert result == "patient reports fever for three days"
    assert calls == ["chunk_0.webm", "chunk_1.webm"]  # sequential, in order


def test_transcribe_chunks_skips_a_failed_chunk_but_keeps_the_rest(monkeypatch):
    def _fake_one(audio_bytes, content_type, filename):
        if filename == "chunk_1.webm":
            raise RuntimeError("transient failure on this chunk")
        return {"chunk_0.webm": "hello", "chunk_2.webm": "world"}[filename]

    monkeypatch.setattr(sarvam_transcriber, "_transcribe_one_chunk", _fake_one)

    chunks = [
        (b"a", "audio/webm", "chunk_0.webm"),
        (b"b", "audio/webm", "chunk_1.webm"),
        (b"c", "audio/webm", "chunk_2.webm"),
    ]
    assert sarvam_transcriber.transcribe_chunks(chunks) == "hello world"


def test_transcribe_chunks_raises_when_every_chunk_fails(monkeypatch):
    """A total failure must surface as a real error, not silently return '' -- a caller
    downstream (main.py) treating '' the same as a genuine empty/short recording would
    produce a confusing 'transcript too short' message instead of the real cause."""
    def _always_fails(audio_bytes, content_type, filename):
        raise RuntimeError("boom")

    monkeypatch.setattr(sarvam_transcriber, "_transcribe_one_chunk", _always_fails)
    with pytest.raises(RuntimeError):
        sarvam_transcriber.transcribe_chunks([(b"a", "audio/webm", "chunk_0.webm")])


def test_transcribe_chunks_empty_list_returns_empty_string():
    assert sarvam_transcriber.transcribe_chunks([]) == ""


def test_transcribe_chunks_skips_empty_transcript_pieces(monkeypatch):
    """A chunk that transcribes successfully but to an empty string (e.g. pure silence) must
    not leave a stray double-space in the joined result."""
    def _fake_one(audio_bytes, content_type, filename):
        return {"chunk_0.webm": "hello", "chunk_1.webm": "", "chunk_2.webm": "world"}[filename]

    monkeypatch.setattr(sarvam_transcriber, "_transcribe_one_chunk", _fake_one)
    chunks = [
        (b"a", "audio/webm", "chunk_0.webm"),
        (b"b", "audio/webm", "chunk_1.webm"),
        (b"c", "audio/webm", "chunk_2.webm"),
    ]
    assert sarvam_transcriber.transcribe_chunks(chunks) == "hello world"
