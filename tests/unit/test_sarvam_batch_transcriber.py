"""
Unit tests for app.sarvam_batch_transcriber -- the Sarvam Batch Speech-to-Text integration
backing the TRANSCRIPTION_PROVIDER="sarvam" audio path (see main.py's transcribe_audio_endpoint
and sarvam_batch_transcriber.py's own module docstring for why this replaced the old <=30s
per-chunk REST approach). The real `sarvamai` SDK is never invoked here -- `SarvamAI` itself is
replaced with a fake factory that mimics the real SDK's job lifecycle (create_job -> upload_files
-> start -> wait_until_complete -> download_outputs, writing a `{filename}.json` per the real
SDK's own convention, verified by reading its installed source during development) without any
network calls.
"""
import json
import os
import types

import pytest

from app import sarvam_batch_transcriber as batch


class _FakeStatus:
    def __init__(self, job_state):
        self.job_state = job_state


class _FakeJob:
    """Mimics sarvamai's SpeechToTextJob -- see the real SDK's speech_to_text_job/job.py."""

    def __init__(self, job_id="job-123", output_payload=None, final_state="Completed", write_output=True):
        self.job_id = job_id
        self._output_payload = output_payload if output_payload is not None else {"transcript": "hello world"}
        self._final_state = final_state
        self._write_output = write_output
        self.create_kwargs = {}
        self.uploaded_paths = None
        self.started = False

    def upload_files(self, file_paths):
        self.uploaded_paths = list(file_paths)
        return True

    def start(self):
        self.started = True
        return _FakeStatus("Running")

    def wait_until_complete(self, poll_interval=5, timeout=1800):
        return _FakeStatus(self._final_state)

    def download_outputs(self, output_dir):
        if not self._write_output:
            return True
        os.makedirs(output_dir, exist_ok=True)
        in_name = os.path.basename(self.uploaded_paths[0])
        with open(os.path.join(output_dir, f"{in_name}.json"), "w", encoding="utf-8") as f:
            json.dump(self._output_payload, f)
        return True


def _install_fake_sdk(monkeypatch, job):
    def _fake_sarvam_ai(*, api_subscription_key):
        assert api_subscription_key == "test-key"
        job_client = types.SimpleNamespace(create_job=lambda **kwargs: (job.create_kwargs.update(kwargs), job)[1])
        return types.SimpleNamespace(speech_to_text_job=job_client)

    monkeypatch.setattr(batch, "SarvamAI", _fake_sarvam_ai)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(batch.settings, "SARVAM_API_KEY", "test-key")


def test_transcribe_long_audio_returns_transcript_on_success(monkeypatch):
    job = _FakeJob(output_payload={"transcript": "patient has fever for three days"})
    _install_fake_sdk(monkeypatch, job)

    result = batch.transcribe_long_audio(b"fake-audio-bytes", "audio/webm", "recording.webm")

    assert result == "patient has fever for three days"
    assert job.started is True
    assert job.uploaded_paths and os.path.basename(job.uploaded_paths[0]) == "recording.webm"


def test_transcribe_long_audio_uses_saaras_v3_translate_mode(monkeypatch):
    """Pins model/mode/language_code exactly -- verified against the installed SDK's own
    SpeechToTextJobParametersParams docstring that mode="translate" only takes effect for
    saaras:v3/v4, not the SDK's own default model."""
    job = _FakeJob()
    _install_fake_sdk(monkeypatch, job)

    batch.transcribe_long_audio(b"x", "audio/webm", "recording.webm")

    assert job.create_kwargs == {"model": "saaras:v3", "mode": "translate", "language_code": "unknown"}


def test_transcribe_long_audio_raises_without_api_key(monkeypatch):
    monkeypatch.setattr(batch.settings, "SARVAM_API_KEY", "")
    with pytest.raises(ValueError):
        batch.transcribe_long_audio(b"x", "audio/webm", "recording.webm")


def test_transcribe_long_audio_raises_when_job_does_not_complete(monkeypatch):
    job = _FakeJob(final_state="Failed")
    _install_fake_sdk(monkeypatch, job)

    with pytest.raises(RuntimeError):
        batch.transcribe_long_audio(b"x", "audio/webm", "recording.webm")


def test_transcribe_long_audio_raises_when_no_output_file_produced(monkeypatch):
    job = _FakeJob(write_output=False)
    _install_fake_sdk(monkeypatch, job)

    with pytest.raises(RuntimeError):
        batch.transcribe_long_audio(b"x", "audio/webm", "recording.webm")


def test_transcribe_long_audio_raises_on_error_payload(monkeypatch):
    job = _FakeJob(output_payload={"error_message": "internal processing error", "transcript": ""})
    _install_fake_sdk(monkeypatch, job)

    with pytest.raises(RuntimeError):
        batch.transcribe_long_audio(b"x", "audio/webm", "recording.webm")


def test_transcribe_long_audio_silent_recording_returns_empty_string_not_error(monkeypatch):
    """A genuinely silent/near-silent recording transcribing to "" with no error field present
    is not a failure -- matches the old per-chunk contract's "skip empty content" behavior."""
    job = _FakeJob(output_payload={"transcript": ""})
    _install_fake_sdk(monkeypatch, job)

    assert batch.transcribe_long_audio(b"x", "audio/webm", "recording.webm") == ""
