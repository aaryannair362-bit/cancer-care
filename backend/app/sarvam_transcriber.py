"""
Speech-to-English transcription via Sarvam AI's Saaras v3 model (/speech-to-text, mode=translate)
-- an alternative to Groq Whisper (scribe.transcribe_audio), purpose-built and benchmarked for
Hindi/English code-switched speech specifically. Verified live against the real API (not
guessed) before this module was written: confirmed the request/response contract, confirmed a
<=30s chunk round-trips in ~1.4s, and confirmed the documented 30-second-per-request cap with a
real 400 response ("Audio duration exceeds the maximum limit of 30 seconds. Please use the
batch API for longer audio files.").

Gated behind settings.TRANSCRIPTION_PROVIDER ("whisper" by default, "sarvam" to opt in) -- see
config.py's comment for why this is env-overridable rather than a code-level swap.

This module does NOT itself split long audio into <=30s pieces -- transcribe_chunks() expects
the CALLER to already have done that (see frontend/js/voice-capture.js's provider="sarvam"
recording path, which restarts MediaRecorder on a rolling ~25s cadence so each resulting chunk
is a complete, independently-valid audio file by construction, not a byte-sliced fragment of a
longer one -- WebM chunks produced by MediaRecorder's timeslice option are NOT reliably
independently decodable in every browser, which is why restart-based chunking was chosen over
that alternative).
"""
import logging
import time

import requests

from .config import settings

logger = logging.getLogger(__name__)

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_MODEL = "saaras:v3"

# Bounded retry for transient failures (network blip, momentary 5xx) -- NOT rate-limit pacing:
# verified live, Sarvam's Starter-tier limit is 60 requests/minute and a real chunk round-trips
# in ~1.4s, so a real consultation's sequential chunk count clears that with headroom on its
# own; this doesn't need the kind of proactive limiter Groq's calls needed (see rate_limiter.py)
# unless/until real evidence says otherwise.
MAX_RETRIES = 2
RETRY_DELAY_SEC = 1.0


def _normalize_content_type(content_type: str) -> str:
    """
    Sarvam's file-type allowlist matches MIME types exactly and does NOT include
    parameterized variants like "audio/webm;codecs=opus" -- verified live: a real
    browser-recorded chunk (Chrome's MediaRecorder produces exactly that content-type by
    default, see voice-capture.js's MIME_CANDIDATES) was rejected with a 400 "Invalid file
    type: audio/webm;codecs=opus", even though the bare "audio/webm" it reduces to IS on
    Sarvam's own documented allowlist. Strip any ";parameter" suffix before sending -- the
    audio bytes/encoding are unaffected, only the declared MIME type string changes.
    """
    if not content_type:
        return content_type
    return content_type.split(";")[0].strip()


def _transcribe_one_chunk(audio_bytes: bytes, content_type: str, filename: str) -> str:
    """
    Translates ONE audio chunk (<=30s -- enforced by Sarvam's API itself, see module
    docstring) to English text. Raises on failure after retries exhausted -- callers decide
    how to surface that. Never logs audio_bytes or the returned transcript text above DEBUG
    (same PHI-safety convention scribe.py already follows -- see its module docstring).
    """
    if not settings.SARVAM_API_KEY:
        raise ValueError("Sarvam API key not configured. Set SARVAM_API_KEY in environment.")

    headers = {"api-subscription-key": settings.SARVAM_API_KEY}
    files = {"file": (filename, audio_bytes, _normalize_content_type(content_type))}
    data = {"model": SARVAM_MODEL, "mode": "translate", "language_code": "unknown"}

    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(SARVAM_STT_URL, headers=headers, files=files, data=data, timeout=30)
            resp.raise_for_status()
            body = resp.json()
            return (body.get("transcript") or "").strip()
        except requests.exceptions.RequestException as e:
            last_exc = e
            logger.warning("Sarvam transcription attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES + 1, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SEC)
    logger.error("Sarvam transcription failed after %d attempts: %s", MAX_RETRIES + 1, last_exc)
    raise last_exc


def transcribe_chunks(chunks: list) -> str:
    """
    chunks: list of (audio_bytes, content_type, filename) tuples, each already <=30s (the
    caller's responsibility -- see module docstring). Transcribes each SEQUENTIALLY (see
    MAX_RETRIES comment for why sequential is fine here) and joins the resulting English text
    with a space, in order.

    A chunk that fails after retries is skipped (logged, not fatal) rather than failing the
    whole consultation -- losing one ~25s segment's text to a transient failure is better than
    losing the entire transcript over it. If EVERY chunk fails, that's not a partial-content
    situation anymore -- it's raised as a real error, so the caller (main.py's endpoint)
    surfaces an actual failure instead of silently returning "" and letting a downstream
    "transcript too short" message confuse what actually went wrong.
    """
    if not chunks:
        return ""
    parts = []
    last_exc = None
    for i, (audio_bytes, content_type, filename) in enumerate(chunks):
        try:
            text = _transcribe_one_chunk(audio_bytes, content_type, filename)
            if text:
                parts.append(text)
        except Exception as e:
            last_exc = e
            logger.warning("Chunk %d/%d failed to transcribe, skipping: %s", i + 1, len(chunks), e)
    if not parts and last_exc is not None:
        raise last_exc
    return " ".join(parts).strip()
