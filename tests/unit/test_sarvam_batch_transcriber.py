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


class _FakeTaskDetail:
    def __init__(self, error_message=None):
        self.error_message = error_message


class _FakeStatus:
    def __init__(self, job_state, successful_files_count=1, job_details=None):
        self.job_state = job_state
        self.successful_files_count = successful_files_count
        self.job_details = job_details


class _FakeJob:
    """Mimics sarvamai's SpeechToTextJob -- see the real SDK's speech_to_text_job/job.py."""

    def __init__(
        self, job_id="job-123", output_payload=None, final_state="Completed", write_output=True,
        successful_files_count=1, job_details=None, start_fail_count=0,
    ):
        self.job_id = job_id
        self._output_payload = output_payload if output_payload is not None else {"transcript": "hello world"}
        self._final_state = final_state
        self._write_output = write_output
        self._successful_files_count = successful_files_count
        self._job_details = job_details
        self._start_fail_count = start_fail_count
        self.create_kwargs = {}
        self.uploaded_paths = None
        self.started = False
        self.start_call_count = 0

    def upload_files(self, file_paths):
        self.uploaded_paths = list(file_paths)
        return True

    def start(self):
        self.start_call_count += 1
        if self.start_call_count <= self._start_fail_count:
            raise batch.BadRequestError(body={"error": {"message": "body.files : Value error, files list must not be empty"}})
        self.started = True
        return _FakeStatus("Running")

    def wait_until_complete(self, poll_interval=5, timeout=1800):
        return _FakeStatus(self._final_state, self._successful_files_count, self._job_details)

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


def test_transcribe_long_audio_raises_with_sarvams_own_reason_when_file_fails_to_process(monkeypatch):
    """Regression test for a real bug found live: a >7200s (2-hour) recording completes the
    JOB (job_state="Completed") while the single file inside fails outright
    (successful_files_count=0) -- checking only job_state would proceed to look for an output
    file that was never created and raise a generic "no output file" error instead of Sarvam's
    own specific, actionable reason (verified live: "400: Audio duration exceeds the maximum
    limit of 7200 seconds.")."""
    job = _FakeJob(
        successful_files_count=0,
        job_details=[_FakeTaskDetail(error_message="400: Audio duration exceeds the maximum limit of 7200 seconds.")],
    )
    _install_fake_sdk(monkeypatch, job)

    with pytest.raises(RuntimeError, match="Audio duration exceeds the maximum limit of 7200 seconds"):
        batch.transcribe_long_audio(b"x", "audio/webm", "recording.webm")


def test_transcribe_long_audio_retries_start_when_upload_registration_races(monkeypatch):
    """Regression test for a real race condition found live with a ~45MB/132-minute file:
    calling start() immediately after upload_files() can hit Sarvam's backend before it finishes
    registering the uploaded file, raising BadRequestError("...files list must not be empty...").
    Confirmed live that retrying start() after a short delay resolves it -- this pins that
    retry behavior rather than surfacing the transient error to the caller."""
    job = _FakeJob(start_fail_count=2)
    _install_fake_sdk(monkeypatch, job)
    monkeypatch.setattr(batch.time, "sleep", lambda *a, **k: None)

    result = batch.transcribe_long_audio(b"x", "audio/webm", "recording.webm")

    assert result == "hello world"
    assert job.start_call_count == 3


def test_transcribe_long_audio_gives_up_after_max_start_retries(monkeypatch):
    job = _FakeJob(start_fail_count=batch.START_RETRY_ATTEMPTS)
    _install_fake_sdk(monkeypatch, job)
    monkeypatch.setattr(batch.time, "sleep", lambda *a, **k: None)

    with pytest.raises(batch.BadRequestError):
        batch.transcribe_long_audio(b"x", "audio/webm", "recording.webm")


def test_transcribe_long_audio_does_not_retry_a_different_bad_request_error(monkeypatch):
    """Only the specific "files list must not be empty" race condition is retried -- any other
    400 (e.g. a genuinely malformed request) must surface immediately, not be masked by retries."""
    job = _FakeJob(start_fail_count=1)
    job._start_fail_count = 1

    def _start_raises_different_error():
        raise batch.BadRequestError(body={"error": {"message": "invalid model specified"}})

    job.start = _start_raises_different_error
    _install_fake_sdk(monkeypatch, job)
    monkeypatch.setattr(batch.time, "sleep", lambda *a, **k: None)

    with pytest.raises(batch.BadRequestError):
        batch.transcribe_long_audio(b"x", "audio/webm", "recording.webm")
