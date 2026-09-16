"""Gemini API client for document OCR AI-extraction (cca_engine.py's extract_clinical_facts /
classify_and_extract_page) -- see config.py's GEMINI_API_KEY comment for why this replaced Groq
for this specific workload (real, live-confirmed 413 Payload Too Large failures on Groq that
permanently dropped a slice's facts, recurring even after repeated live-calibrated tuning
attempts on the same day). NOT used by scribe.py's live OPD/IPD voice consultation drafting,
which stays on Groq -- that workload never showed this failure mode.

Uses generateContent with a JSON response schema (responseMimeType/responseSchema), not just a
JSON-shaped prompt -- Gemini enforces the schema server-side, which eliminates the
malformed-JSON/hallucinated-enum-value class of problem this app otherwise has to defend
against (see cca_engine.py's own `if fact_type not in FACT_TYPES` filtering, still kept as a
defense-in-depth belt-and-suspenders check, not because the schema is expected to fail).

Doctor-patient document text is PHI. Raw request/response content is only ever emitted at
DEBUG (off by default), matching scribe.py's own PHI-safety pattern -- only someone who
deliberately enables DEBUG logging ever sees it.
"""
import json
import logging
import re
import time
from typing import Optional

import requests

from . import rate_limiter
from .config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Lower than scribe.py's Groq retry count (5) and each wait is longer -- live-confirmed real
# retry windows on this free tier can be ~40-60s (a 429's own message says "Please retry in
# 42.0s" for a 5-RPM-limit project), so a full retry cycle here already risks adding minutes of
# latency to a synchronous document upload request. Kept low deliberately: under normal
# single-document-at-a-time usage the proactive rate_limiter.gemini_request_bucket pacing below
# should prevent ever reaching a real 429 in the first place -- this retry budget is a safety
# net for genuine bursts (several Front Desk users uploading around the same time), not the
# expected path.
MAX_RATE_LIMIT_RETRIES = 3
_RETRY_WAIT_CAP_SEC = 60.0

_RETRY_SECONDS_PATTERN = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)


def _post_with_retry(url: str, api_key: str, payload: dict, _retry: int = 0) -> dict:
    """Shared POST-with-retry-backoff for Gemini's generateContent, mirroring scribe.py's
    _post_with_retry contract/shape for the equivalent Groq call. Returns the parsed JSON body."""
    try:
        response = requests.post(
            url, headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload, timeout=90,
        )
        if response.status_code == 429 and _retry < MAX_RATE_LIMIT_RETRIES:
            # Gemini has no Retry-After header (verified live) -- the wait is embedded in the
            # error message text itself ("...Please retry in 42.008816016s."). Falls back to
            # the same exponential schedule Groq's equivalent retry uses when the message
            # doesn't parse (a different error shape than the one this was built against).
            wait = _RETRY_WAIT_CAP_SEC
            try:
                body_text = response.text
                match = _RETRY_SECONDS_PATTERN.search(body_text)
                if match:
                    wait = min(float(match.group(1)), _RETRY_WAIT_CAP_SEC)
                else:
                    wait = min(3 * (2 ** _retry), _RETRY_WAIT_CAP_SEC)
            except Exception:
                wait = min(3 * (2 ** _retry), _RETRY_WAIT_CAP_SEC)
            logger.warning(
                "Gemini 429 rate limited, retrying in %.1fs (attempt %d/%d)",
                wait, _retry + 1, MAX_RATE_LIMIT_RETRIES,
            )
            time.sleep(wait)
            return _post_with_retry(url, api_key, payload, _retry=_retry + 1)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error("Gemini API error: %s", e)
        if hasattr(e, "response") and e.response is not None:
            status_code = e.response.status_code
            # Same PHI convention as scribe.py's Groq _post_with_retry (see
            # tests/unit/test_scribe_audio_transcription.py::
            # test_transcribe_audio_error_logs_generic_message_not_response_body and this
            # module's own docstring): an upstream error body CAN echo request content back
            # (a 400 schema-validation error, for instance, may quote the offending field's
            # value), so the raw body stays DEBUG-only -- unchanged below. What's new here is
            # pulling out ONLY `error.status`, one of a small fixed set of Google RPC enum
            # strings (e.g. "PERMISSION_DENIED", "RESOURCE_EXHAUSTED") that is never derived
            # from our request content, so it's safe to surface at ERROR without the PHI risk
            # of the message/body around it.
            reason = None
            try:
                reason = (e.response.json().get("error") or {}).get("status")
            except Exception:
                pass
            logger.error("Gemini API error response: status_code=%s reason=%s", status_code, reason)
            if status_code == 403:
                logger.error(
                    "Gemini 403 Forbidden is not this app's rate limiter (that would be a 429, "
                    "logged separately, and would reset on process restart) -- check the "
                    "GEMINI_API_KEY project in Google AI Studio/Cloud Console for exhausted "
                    "daily quota, billing status, or a revoked/restricted key."
                )
            logger.debug("Response body: %s", e.response.text)
        raise


def generate_structured_json(
    prompt: str,
    system: str,
    response_schema: dict,
    temperature: float = 0.0,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """Calls Gemini's generateContent with a JSON response schema, paced against
    rate_limiter.gemini_request_bucket/gemini_token_bucket the same way scribe.py's
    _call_groq_api paces against its own buckets before dispatch. Returns the parsed JSON dict
    matching response_schema's shape. Raises on any failure (network, rate-limit exhaustion,
    non-2xx, malformed/blocked response) -- callers (extract_clinical_facts,
    classify_and_extract_page) already treat any exception from their model call as "this call
    contributed nothing," matching this repo's standing "AI enrichment failing must never fail
    the document" contract; this function itself makes no such allowance."""
    api_key = api_key or settings.GEMINI_API_KEY
    model = model or settings.GEMINI_MODEL
    if not api_key:
        raise ValueError("Gemini API key not configured. Set GEMINI_API_KEY in environment.")

    url = f"{_BASE_URL}/{model}:generateContent"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
        },
    }

    rate_limiter.gemini_request_bucket.consume(1)
    estimated_tokens = rate_limiter.estimate_gemini_tokens(prompt + system)
    rate_limiter.gemini_token_bucket.consume(estimated_tokens)

    data = _post_with_retry(url, api_key, payload)

    usage = data.get("usageMetadata") or {}
    actual_total = usage.get("totalTokenCount")
    if isinstance(actual_total, (int, float)):
        rate_limiter.gemini_token_bucket.true_up(actual_total, estimated_tokens)

    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini returned no candidates (likely blocked by a safety filter)")
    finish_reason = candidates[0].get("finishReason")
    if finish_reason not in ("STOP", None):
        raise ValueError(f"Gemini did not complete normally (finishReason={finish_reason})")

    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    text_parts = [p.get("text", "") for p in parts if "text" in p]
    if not text_parts:
        raise ValueError("Gemini response had no text content")

    return json.loads("".join(text_parts))
