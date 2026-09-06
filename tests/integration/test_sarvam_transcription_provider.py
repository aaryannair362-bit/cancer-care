"""
Integration tests for the TRANSCRIPTION_PROVIDER="sarvam" path through
POST /api/transcribe-audio (multi-chunk upload, see main.py's transcribe_audio_endpoint
docstring) and GET /api/transcription-provider (the frontend's way of finding out which
recording strategy to use -- see voice-capture.js's module comment).

Existing tests/integration/test_transcribe_audio_endpoint.py already covers the default
"whisper" path (single file, unchanged since before this feature existed) -- these tests focus
specifically on what's NEW: multi-file handling, the provider-selection endpoint, and that
switching TRANSCRIPTION_PROVIDER can never accidentally make the whisper path try to consume
more than one file.
"""
import pytest

from app import main as app_main
from app import sarvam_transcriber


@pytest.fixture
def doctor(make_user):
    return make_user(email="doctor@sarvam-provider.com", role="Doctor")


@pytest.fixture
def sarvam_provider(monkeypatch):
    monkeypatch.setattr(app_main.settings, "TRANSCRIPTION_PROVIDER", "sarvam")


def test_provider_endpoint_reports_whisper_by_default(client, doctor, auth_headers):
    resp = client.get("/api/transcription-provider", headers=auth_headers(doctor))
    assert resp.status_code == 200
    assert resp.json() == {"provider": "whisper"}


def test_provider_endpoint_reports_sarvam_when_configured(client, doctor, auth_headers, sarvam_provider):
    resp = client.get("/api/transcription-provider", headers=auth_headers(doctor))
    assert resp.json() == {"provider": "sarvam"}


def test_provider_endpoint_requires_authentication(client):
    resp = client.get("/api/transcription-provider")
    assert resp.status_code in (401, 403)


def test_sarvam_mode_transcribes_multiple_chunks_and_joins_them(client, doctor, auth_headers, monkeypatch, sarvam_provider):
    seen_chunks = []

    def _fake_transcribe_chunks(chunks):
        seen_chunks.extend(chunks)
        return "patient has fever for three days prescribe paracetamol"

    monkeypatch.setattr(sarvam_transcriber, "transcribe_chunks", _fake_transcribe_chunks)

    resp = client.post(
        "/api/transcribe-audio",
        files=[
            ("audio", ("chunk_0.webm", b"fake-audio-0", "audio/webm")),
            ("audio", ("chunk_1.webm", b"fake-audio-1", "audio/webm")),
        ],
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 200
    assert resp.json() == {"transcript": "patient has fever for three days prescribe paracetamol"}
    assert len(seen_chunks) == 2
    assert seen_chunks[0] == (b"fake-audio-0", "audio/webm", "chunk_0.webm")
    assert seen_chunks[1] == (b"fake-audio-1", "audio/webm", "chunk_1.webm")


def test_sarvam_mode_single_chunk_still_works(client, doctor, auth_headers, monkeypatch, sarvam_provider):
    """A short recording that never needed a rotation still arrives as exactly one file --
    the sarvam path must handle a 1-element list the same as a many-element one."""
    monkeypatch.setattr(sarvam_transcriber, "transcribe_chunks", lambda chunks: "ok")
    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("chunk_0.webm", b"fake-audio", "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 200
    assert resp.json() == {"transcript": "ok"}


def test_sarvam_mode_skips_empty_chunks_rather_than_sending_them(client, doctor, auth_headers, monkeypatch, sarvam_provider):
    """A rolling-restart rotation that happens to land exactly at stop() can produce a
    genuinely empty final chunk -- must be filtered out before reaching Sarvam (which would
    just reject an empty file), not treated as a fatal error."""
    seen_chunks = []
    monkeypatch.setattr(sarvam_transcriber, "transcribe_chunks", lambda chunks: seen_chunks.extend(chunks) or "ok")

    resp = client.post(
        "/api/transcribe-audio",
        files=[
            ("audio", ("chunk_0.webm", b"fake-audio", "audio/webm")),
            ("audio", ("chunk_1.webm", b"", "audio/webm")),
        ],
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 200
    assert len(seen_chunks) == 1
    assert seen_chunks[0][2] == "chunk_0.webm"


def test_sarvam_mode_all_chunks_empty_returns_400(client, doctor, auth_headers, monkeypatch, sarvam_provider):
    monkeypatch.setattr(sarvam_transcriber, "transcribe_chunks", lambda chunks: "should never be reached")
    resp = client.post(
        "/api/transcribe-audio",
        files=[("audio", ("chunk_0.webm", b"", "audio/webm"))],
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 400


def test_sarvam_mode_failure_returns_generic_error_not_exception_detail(client, doctor, auth_headers, monkeypatch, sarvam_provider):
    def _raise(chunks):
        raise RuntimeError("upstream said: patient John Doe has HIV")

    monkeypatch.setattr(sarvam_transcriber, "transcribe_chunks", _raise)
    resp = client.post(
        "/api/transcribe-audio",
        files=[("audio", ("chunk_0.webm", b"fake-audio", "audio/webm"))],
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 502
    assert "John Doe" not in resp.text
    assert "HIV" not in resp.text


def test_too_many_chunks_rejected(client, doctor, auth_headers, monkeypatch, sarvam_provider):
    monkeypatch.setattr(app_main, "MAX_AUDIO_CHUNKS", 2)
    monkeypatch.setattr(sarvam_transcriber, "transcribe_chunks", lambda chunks: "should never be reached")
    resp = client.post(
        "/api/transcribe-audio",
        files=[
            ("audio", ("chunk_0.webm", b"a", "audio/webm")),
            ("audio", ("chunk_1.webm", b"b", "audio/webm")),
            ("audio", ("chunk_2.webm", b"c", "audio/webm")),
        ],
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 413


def test_switching_to_sarvam_does_not_break_whisper_default_single_file_contract(client, doctor, auth_headers, monkeypatch):
    """Explicitly pins the safety property main.py's docstring claims: with
    TRANSCRIPTION_PROVIDER left at its "whisper" default, only audio[0] is ever read, so this
    endpoint's behavior for the default path is identical whether one or several files happen
    to be posted under the "audio" field."""
    def _fake_transcribe_audio(audio_bytes, content_type, filename):
        assert audio_bytes == b"first-file-only"
        return "whisper transcript"

    monkeypatch.setattr(app_main.scribe, "transcribe_audio", _fake_transcribe_audio)

    resp = client.post(
        "/api/transcribe-audio",
        files=[
            ("audio", ("chunk_0.webm", b"first-file-only", "audio/webm")),
            ("audio", ("chunk_1.webm", b"second-file-ignored", "audio/webm")),
        ],
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 200
    assert resp.json() == {"transcript": "whisper transcript"}
