"""
Integration tests for the TRANSCRIPTION_PROVIDER="sarvam" path through
POST /api/transcribe-audio (see main.py's transcribe_audio_endpoint docstring) and
GET /api/transcription-provider (the frontend's way of finding out which backend to use --
see voice-capture.js's module comment).

Since the Batch API migration (sarvam_batch_transcriber.py), the sarvam path reads exactly one
audio file, same as the default "whisper" path -- tests/integration/test_transcribe_audio_endpoint.py
already covers the shared upload-validation/PHI-safe-error behavior for that shape; these tests
focus on what's specific to sarvam: the provider-selection endpoint and that
sarvam_batch_transcriber.transcribe_long_audio (not scribe.transcribe_audio) is the one actually
called when TRANSCRIPTION_PROVIDER="sarvam".
"""
import pytest

from app import main as app_main
from app import sarvam_batch_transcriber


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


def test_sarvam_mode_transcribes_the_single_uploaded_file(client, doctor, auth_headers, monkeypatch, sarvam_provider):
    seen = {}

    def _fake_transcribe_long_audio(audio_bytes, content_type, filename):
        seen["args"] = (audio_bytes, content_type, filename)
        return "patient has fever for three days prescribe paracetamol"

    monkeypatch.setattr(sarvam_batch_transcriber, "transcribe_long_audio", _fake_transcribe_long_audio)

    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("recording.webm", b"fake-audio-bytes", "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 200
    assert resp.json() == {"transcript": "patient has fever for three days prescribe paracetamol"}
    assert seen["args"] == (b"fake-audio-bytes", "audio/webm", "recording.webm")


def test_sarvam_mode_empty_audio_rejected(client, doctor, auth_headers, monkeypatch, sarvam_provider):
    monkeypatch.setattr(
        sarvam_batch_transcriber, "transcribe_long_audio",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should never be reached")),
    )
    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("recording.webm", b"", "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 400


def test_sarvam_mode_failure_returns_generic_error_not_exception_detail(client, doctor, auth_headers, monkeypatch, sarvam_provider):
    def _raise(audio_bytes, content_type, filename):
        raise RuntimeError("upstream said: patient John Doe has HIV")

    monkeypatch.setattr(sarvam_batch_transcriber, "transcribe_long_audio", _raise)
    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("recording.webm", b"fake-audio", "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 502
    assert "John Doe" not in resp.text
    assert "HIV" not in resp.text


def test_sarvam_mode_a_long_recording_is_not_rejected_for_size_alone(client, doctor, auth_headers, monkeypatch, sarvam_provider):
    """Regression guard for the bug this migration fixes: a long consultation must reach
    transcribe_long_audio rather than being rejected outright just for existing as one file --
    there is no more MAX_AUDIO_CHUNKS-style ceiling on the sarvam path (see main.py's old
    docstring/this endpoint's history: a >~16.7 minute consultation used to lose its entire
    transcript to a 413 before this migration)."""
    monkeypatch.setattr(sarvam_batch_transcriber, "transcribe_long_audio", lambda *a, **k: "ok")
    long_audio = b"x" * (2 * 1024 * 1024)  # a plausible size for a real multi-minute recording
    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("recording.webm", long_audio, "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 200
    assert resp.json() == {"transcript": "ok"}


def test_switching_to_sarvam_does_not_break_whisper_default_single_file_contract(client, doctor, auth_headers, monkeypatch):
    """Pins the safety property main.py's docstring claims: with TRANSCRIPTION_PROVIDER left at
    its "whisper" default, scribe.transcribe_audio (not the sarvam batch transcriber) handles
    the single uploaded file."""
    def _fake_transcribe_audio(audio_bytes, content_type, filename):
        assert audio_bytes == b"the-only-file"
        return "whisper transcript"

    monkeypatch.setattr(app_main.scribe, "transcribe_audio", _fake_transcribe_audio)

    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("recording.webm", b"the-only-file", "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 200
    assert resp.json() == {"transcript": "whisper transcript"}
