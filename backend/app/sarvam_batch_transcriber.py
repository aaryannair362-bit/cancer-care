"""
Long-form speech-to-English transcription via Sarvam's Batch Speech-to-Text API
(model=saaras:v3, mode=translate) -- replaces sarvam_transcriber.py's <=30s-chunk REST loop for
the TRANSCRIPTION_PROVIDER="sarvam" audio path (see main.py's transcribe_audio_endpoint and
voice-capture.js's single-continuous-recording "sarvam" mode).

Why batch instead of the old REST-per-chunk approach: Sarvam's REST /speech-to-text endpoint is
hard-capped by Sarvam itself at 30s/request -- its own 400 error text says "use the batch API
for longer audio files". This app used to work around that by chunking a consultation into ~25s
pieces client-side and uploading all of them together in one request at Stop() -- but that meant
main.py's old MAX_AUDIO_CHUNKS=40 cap rejected the ENTIRE upload (losing 100% of the transcript,
not just the tail) for any consultation over ~16.7 minutes, and even under that cap, tens of
sequential REST calls fired at once risked Sarvam's per-minute rate limit under concurrent
OPD+IPD usage. The Batch API (speech_to_text_job) accepts ONE file up to 2 hours long and is one
job per consultation -- create, start, poll, download: a handful of API calls total instead of
dozens -- so voice-capture.js's "sarvam" mode is now a single continuous recording, exactly like
"whisper" mode.

Uses the official `sarvamai` SDK (pip install sarvamai) rather than hand-rolling the upload
transport: job creation returns Azure Blob SAS upload URLs that the SDK PUTs the file to
internally. Verified by reading the SDK's own installed source
(site-packages/sarvamai/speech_to_text_job/job.py) during development -- Sarvam's public docs
describe the high-level flow but not this transport detail, so the source was the ground truth,
not a guess.

model="saaras:v3" is required, not the SDK's own default ("saarika:v2.5"): verified against the
installed SDK's SpeechToTextJobParametersParams docstring that mode="translate" only has effect
"for saaras:v3 or saaras:v4" models. mode="translate"/language_code="unknown" mirror the old
sarvam_transcriber.py REST call exactly, for output parity with what this app already relied on
(always-English output for Hinglish speech -- see that retired module's docstring for why).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile

from sarvamai import SarvamAI

from .config import settings

logger = logging.getLogger(__name__)

MODEL = "saaras:v3"
MODE = "translate"
LANGUAGE_CODE = "unknown"
# Batch STT processes well under real-time, but this is a generous ceiling for a full-length
# consultation (Sarvam's own batch file-length ceiling is 2 hours) -- raise if real evidence
# ever shows a legitimate job taking longer than this to finish.
POLL_INTERVAL_SEC = 5
JOB_TIMEOUT_SEC = 1800


def transcribe_long_audio(audio_bytes: bytes, content_type: str, filename: str) -> str:
    """
    Translates one audio recording (any length up to Sarvam's 2-hour batch ceiling) to English
    text via Sarvam's Batch Speech-to-Text API. Raises on failure -- callers decide how to
    surface that, same contract the old sarvam_transcriber.transcribe_chunks had. Never logs
    audio_bytes or the returned transcript above DEBUG (same PHI convention as scribe.py and
    the retired sarvam_transcriber.py).
    """
    if not settings.SARVAM_API_KEY:
        raise ValueError("Sarvam API key not configured. Set SARVAM_API_KEY in environment.")

    client = SarvamAI(api_subscription_key=settings.SARVAM_API_KEY)
    # get_upload_links()/upload_file() key off the file's basename+extension (both to name the
    # blob and, client-side, to guess a Content-Type for the PUT) -- keep whatever extension the
    # browser's recording actually produced (webm/mp4) rather than inventing one.
    safe_name = os.path.basename(filename) or "recording.webm"

    with tempfile.TemporaryDirectory(prefix="sarvam_stt_") as tmp_dir:
        in_path = os.path.join(tmp_dir, safe_name)
        with open(in_path, "wb") as f:
            f.write(audio_bytes)

        job = client.speech_to_text_job.create_job(
            model=MODEL, mode=MODE, language_code=LANGUAGE_CODE,
        )
        job.upload_files([in_path])
        job.start()
        status = job.wait_until_complete(poll_interval=POLL_INTERVAL_SEC, timeout=JOB_TIMEOUT_SEC)
        if status.job_state.lower() != "completed":
            logger.error("Sarvam batch STT job %s ended in state %r", job.job_id, status.job_state)
            raise RuntimeError(f"Sarvam batch STT job did not complete (state={status.job_state})")

        out_dir = os.path.join(tmp_dir, "out")
        job.download_outputs(output_dir=out_dir)
        out_path = os.path.join(out_dir, f"{safe_name}.json")
        if not os.path.exists(out_path):
            raise RuntimeError("Sarvam batch STT job completed but produced no output file")

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        transcript = (data.get("transcript") or "").strip()
        if not transcript and ("error" in data or "error_message" in data or "error_code" in data):
            logger.error("Sarvam batch STT returned an error payload for job %s", job.job_id)
            raise RuntimeError("Sarvam batch STT returned an error instead of a transcript")
        # An empty transcript with no error is a genuinely silent/near-silent recording, not a
        # failure -- return "" rather than raise, matching the old per-chunk contract's
        # "skip empty content, don't fail the whole consultation over it" behavior.
        return transcript
