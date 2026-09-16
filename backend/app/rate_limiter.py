"""
Proactive request pacing for outgoing Groq API calls -- built after live evidence
(tests/scale/results/run.log, a real load-test run against production Groq) showed that
concurrent doctors/nurses in OPD+IPD could push several requests into the same window, get
several 429s back together, and then each independently sit in scribe.py's reactive
retry-with-backoff loop (up to 3 retries, up to 20s each) AT THE SAME TIME -- one logged stretch
completed only 15 cases in 34.5 minutes, almost entirely retry waiting. Switching GROQ_MODEL to
llama-3.1-8b-instant raised the DAILY request quota (~1000/day on the old reasoning model to
~14,400/day) but did nothing for this: it's not a quota-size problem, it's that nothing paces
requests before they leave, so a burst can still exceed the per-minute limit even with a large
daily budget. This module is that pacing, applied BEFORE a request is sent (see scribe.py's
_call_groq_api), so the app queues fairly under load instead of firing blind and hoping.

Deliberately NOT applied to scribe.transcribe_audio (the Whisper /audio/translations call):
that's a different model with no empirically-measured rate limit the way llama-3.1-8b-instant's
was (see TokenBucket instances below) -- pacing it would mean guessing numbers again, the exact
mistake that broke transcription in production once already this session (see git history:
GROQ_AUDIO_MODEL's turbo revert). If audio-specific rate-limit evidence shows up later, it gets
its own calibrated bucket, not a borrowed guess from the text model's numbers.

Single-process scope: bucket state lives in process memory, not shared across multiple Render
worker processes/dynos. If this deployment ever runs more than one process against the same
Groq key, each process paces independently and the EFFECTIVE combined rate multiplies by the
process count -- acceptable for now (this is a single web service), but worth knowing if that
ever changes.
"""
import threading
import time
from typing import Callable, Optional


class TokenBucket:
    """
    Generic token-bucket rate limiter: `capacity` tokens available up front, refilling
    continuously at `rate_per_sec`. consume(amount) blocks the calling thread until enough
    tokens are available, then spends them -- callers naturally queue in the order they
    arrive rather than firing simultaneously and finding out from Groq's 429 that they
    shouldn't have. Thread-safe (a real threading.Lock, not asyncio) because FastAPI's
    run_in_threadpool means concurrent requests reach this from real OS threads, not
    coroutines on one event loop.

    Used for BOTH request-count pacing (amount=1 per call) and token-count pacing
    (amount=estimated tokens per call) -- same math either way, just a different unit and a
    differently-calibrated instance (see scribe.py's _REQUEST_BUCKET / _TOKEN_BUCKET). Two
    dimensions matter independently: Groq enforces a per-model requests-per-day/minute limit
    AND a separate tokens-per-minute limit, and hitting either one produces the same 429 --
    verified live in tests/scale/runner.py's development history, where pacing only the
    request count still 429'd because the token-rate budget was independently exhausted.

    `time_fn`/`sleep_fn` are injectable purely for fast, deterministic unit tests (no real
    sleeping) -- production code never passes them, so real wall-clock time and real
    time.sleep are always what actually runs.
    """

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.rate = rate_per_sec
        self.capacity = capacity
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn
        self.tokens = capacity
        self._last = time_fn()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._time_fn()
        elapsed = now - self._last
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self._last = now

    def consume(self, amount: float = 1.0, max_wait_sec: float = 30.0) -> None:
        """
        Blocks until `amount` tokens are available, then spends them. A single consume()
        call sleeps in bounded slices (never more than `max_wait_sec` per slice) rather than
        one long uninterrupted sleep, so a request that genuinely needs to wait longer than
        that still eventually gets its turn instead of sleeping past a caller-side timeout
        with no visibility into how much longer is left.
        """
        if amount > self.capacity:
            # Would never succeed (can never accumulate more than `capacity`) -- this is a
            # caller bug (asking for more than the bucket can ever hold), not a rate-limit
            # wait, so fail fast instead of blocking forever.
            raise ValueError(
                f"requested amount ({amount}) exceeds bucket capacity ({self.capacity}) -- "
                "can never be satisfied"
            )
        with self._lock:
            while True:
                self._refill()
                if self.tokens >= amount:
                    self.tokens -= amount
                    return
                shortfall = amount - self.tokens
                self._sleep_fn(min(shortfall / self.rate, max_wait_sec))

    def true_up(self, actual_amount: float, estimated_amount: float) -> None:
        """
        Corrects bucket state AFTER a call completes, using real usage a provider reports back
        (e.g. Groq's response `usage.total_tokens`) instead of the pre-call estimate that was
        actually consume()'d before dispatch. Found necessary live: a reasoning-capable Groq
        model (openai/gpt-oss-120b) spends hidden "reasoning tokens" that never appear in the
        visible completion text (they're moved out via `reasoning_format: "hidden"`, see
        scribe.py's _call_groq_api) but still count fully toward the account's real
        tokens-per-minute limit -- confirmed live via the API's own `usage.completion_tokens_
        details.reasoning_tokens`. No amount of tuning estimate_tokens()'s char-count-based
        completion guess can predict that (it depends on how much the model "thinks", not on
        prompt length), so real per-call usage regularly exceeded the estimate this bucket was
        paced against, letting calls through faster than the account's actual budget allowed
        and causing real 429s under sustained load -- exactly what this method fixes.

        Only ever deducts the shortfall (actual - estimated) when actual > estimated -- if the
        estimate overshot, the bucket is NOT refunded the difference. Erring toward "the bucket
        believes less capacity is available than it really has" is the safe direction; erring
        the other way is what caused the problem this method exists to correct. May drive
        `tokens` negative, which is fine: the next consume() naturally waits out the deficit via
        its existing refill math, same as if a large amount had been requested outright.
        """
        shortfall = actual_amount - estimated_amount
        if shortfall <= 0:
            return
        with self._lock:
            self._refill()
            self.tokens -= shortfall


# CORRECTED 2026-09-16: the "x-ratelimit-limit-requests: 1000" this was originally calibrated
# against is this account's REQUESTS-PER-DAY limit, not requests-per-minute -- confirmed against
# Groq's own published rate-limits page, which lists openai/gpt-oss-120b's free-tier limits as
# RPM 30 / RPD 1,000 / TPM 8,000 / TPD 200,000. The live header this file reads back doesn't
# distinguish RPM from RPD by name, and 1000 happens to be the RPD figure, so the request bucket
# was paced at ~60 requests/minute (_REQUEST_RATE_PER_SEC=1.0) -- roughly DOUBLE the real 30 RPM
# ceiling -- for as long as this calibration stood. Any real burst (e.g. a multi-slice document
# upload) could blow through the actual per-minute cap well before this bucket ever throttled it,
# independent of token size or slice size. Paced at 0.4/sec (~24 RPM) with a small burst, leaving
# headroom below the documented 30 RPM rather than pacing right up against it.
#
# Token side (TPM 8,000) was already correctly calibrated and is unchanged.
_REQUEST_RATE_PER_SEC = 0.4
_REQUEST_BURST_CAPACITY = 5.0
_TOKEN_RATE_PER_SEC = 120.0
_TOKEN_BURST_CAPACITY = 8000.0

request_bucket = TokenBucket(rate_per_sec=_REQUEST_RATE_PER_SEC, capacity=_REQUEST_BURST_CAPACITY)
token_bucket = TokenBucket(rate_per_sec=_TOKEN_RATE_PER_SEC, capacity=_TOKEN_BURST_CAPACITY)


# REMOVED 2026-09-16 (later same day): this section used to hold a second, genuinely-separate-
# account pair of buckets (ocr_extraction_request_bucket/ocr_extraction_token_bucket) dedicated
# to document-OCR AI-extraction on its own Groq key (GROQ_API_KEY_OCR), kept apart from
# request_bucket/token_bucket above specifically so a document-OCR burst could never starve a
# live consultation's own budget. Deleted once cca_engine.py's extract_clinical_facts/
# classify_and_extract_page moved off Groq onto Gemini entirely (see gemini_client.py and
# config.py's GEMINI_API_KEY comment) -- nothing calls these anymore, and GROQ_API_KEY_OCR was
# removed from config.py the same way.
#
# One durable, generally-useful finding from calibrating that now-removed pair is worth keeping
# even though the buckets themselves are gone: to verify whether two API keys draw from the SAME
# account/budget or genuinely separate ones, a single small probe call on each key, read
# sequentially, is NOT reliable -- both a token bucket (~120-133 tokens/sec) and a request bucket
# refill continuously and fast enough that the network+inference latency between two sequential
# tiny calls (e.g. a "hi" prompt, max_tokens=1) is enough for the budget to partially or fully
# refill in between, so two calls on the SAME account can read back identical remaining_* values
# -- indistinguishable from two calls on genuinely separate accounts each starting fresh. What
# actually works: a concurrent BURST of several real-sized calls on one key, immediately
# followed by a probe on the other -- if the other key's remaining budget is untouched, the
# accounts are genuinely separate; if it dropped too, they share one budget. Use the burst
# method, not the sequential-probe method, for any future key-account verification.


# Calibrated against Sarvam's own documented rate limit for Document Intelligence / Vision
# (docs.sarvam.ai/api-reference-docs/ratelimits): 10 requests/minute, uniform across every plan
# tier (upgrading the plan does not raise this) -- a vendor-stated number, not a guess. This
# matters here specifically: ocr_service.py's per-document-chunk job lifecycle (create_job,
# upload_file, start, one poll per _SARVAM_DOC_AI_POLL_INTERVAL_SEC inside wait_until_complete,
# download_output) can easily spend most of that budget on a SINGLE chunk job, and a multi-chunk
# PDF (>10 pages) or concurrent uploads from different Front Desk users can blow through it in
# seconds with no pacing at all -- confirmed as the cause of per-page/per-chunk Sarvam OCR calls
# silently failing (and being silently dropped -- see extract_document_pages()) for real
# multi-page scanned hospital records. Paced at 8/min, not the full 10, to leave headroom for
# wait_until_complete's own internal polling cadence, which this bucket does not individually
# gate call-by-call.
_SARVAM_DOC_AI_REQUEST_RATE_PER_SEC = 8.0 / 60.0
_SARVAM_DOC_AI_REQUEST_BURST_CAPACITY = 8.0

sarvam_doc_ai_request_bucket = TokenBucket(
    rate_per_sec=_SARVAM_DOC_AI_REQUEST_RATE_PER_SEC, capacity=_SARVAM_DOC_AI_REQUEST_BURST_CAPACITY
)


# Gemini (gemini_client.py) -- document OCR AI-extraction's replacement for Groq (see
# config.py's GEMINI_API_KEY comment for why). Calibrated against a live 429 response body on
# this exact free-tier project/model, not a guess or a generic web figure: "Quota exceeded for
# metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 5,
# model: gemini-3.6-flash" -- confirmed the REAL free-tier request limit for this model is 5
# requests/minute, notably lower than a generic "Gemini 3 Flash" figure found via web search
# (10 RPM) for what turned out to be a different point release. Paced at 4/min, not the full 5,
# for the same reason every other bucket in this file leaves headroom rather than pacing right
# at a documented ceiling.
#
# This is the PRIMARY throttle for this workload, unlike Groq's buckets where the TOKEN budget
# was the real bottleneck -- Gemini's 1M-token context window means a whole document (verified
# live: a real 200,000-character, ~62,000-token mixed-script document) fits in ONE call, so
# request COUNT, not token volume, is what a multi-document burst actually competes over.
_GEMINI_REQUEST_RATE_PER_SEC = 4.0 / 60.0
_GEMINI_REQUEST_BURST_CAPACITY = 2.0
# Token side is a generous safety backstop, not a tightly-calibrated throttle the way Groq's
# was -- real free-tier TPM for this model wasn't independently confirmed (RPM=5 is the binding
# constraint at any realistic single-document call size, so a precise TPM figure matters far
# less here). 200,000/min gives wide headroom above the one real large-document measurement
# (61,891 total tokens for a single call) without ever meaningfully throttling ahead of the
# request-count bucket above.
_GEMINI_TOKEN_RATE_PER_SEC = 3333.0
_GEMINI_TOKEN_BURST_CAPACITY = 200000.0

gemini_request_bucket = TokenBucket(rate_per_sec=_GEMINI_REQUEST_RATE_PER_SEC, capacity=_GEMINI_REQUEST_BURST_CAPACITY)
gemini_token_bucket = TokenBucket(rate_per_sec=_GEMINI_TOKEN_RATE_PER_SEC, capacity=_GEMINI_TOKEN_BURST_CAPACITY)


def estimate_gemini_tokens(text: str) -> float:
    """Bytes/4.5 estimate for Gemini prompt cost -- same reasoning and same conservative margin
    as estimate_tokens() above (see its docstring): live-measured ~4.8 real bytes/token on a
    200,000-character mixed ASCII/Devanagari document (296,762 bytes -> 61,309 prompt tokens),
    close enough to Groq's own separately-measured ~5.0-6.2 bytes/token that the same formula
    and margin apply. Only estimates the PROMPT side -- unlike Groq's estimate_tokens(), there's
    no separate completion-token term to add here, since Gemini's structured-output schema
    (responseSchema) bounds the completion shape far more tightly than a free-text JSON prompt
    ever could, and completion cost is comparatively small next to a large document's prompt
    cost anyway."""
    return len(text.encode("utf-8")) / 4.5


def estimate_tokens(prompt: str, max_tokens: int) -> float:
    """
    Cheap, tokenizer-free estimate of a call's total token cost, used to pace the token
    bucket BEFORE the real usage is known.

    Prompt term is BYTES/4.5, not characters/4.0 (the prior formula) -- calibrated live against
    Groq's own accounting, not guessed: deliberately over-budget calls against the real API
    (reading the exact "Requested N tokens" figure back from the resulting 429 error body, which
    costs nothing since the call is rejected before generating anything) measured ~5.0-5.5 real
    tokens per UTF-8 byte across pure-ASCII, pure-Devanagari, and mixed content alike. Character
    count is NOT a stable proxy across scripts -- this codebase explicitly handles Hindi/
    Devanagari content where one character is 3 UTF-8 bytes, so characters/4.0 could silently
    undercount a non-Latin-heavy prompt by a large factor while looking fine for an English one.
    Byte count turned out to be stable across scripts, so it's used directly; 4.5 (below the
    measured 5.0-5.5 bytes/token) errs toward overestimating, the safe direction, same reasoning
    as token_bucket.true_up's own "never refund an overshoot" rule below.

    Completion estimate is capped well above what a plain structured-JSON response's visible
    text would need, because the configured model (GROQ_MODEL, a reasoning model) spends hidden
    "reasoning tokens" that never appear in the completion text but count fully against the real
    tokens-per-minute budget (see token_bucket.true_up's docstring).

    The cap here was previously 800 -- calibrated for the visible JSON alone, before this
    hidden-reasoning-token cost was known to matter. Verified live via tests/scale/runner.py's
    own measured calibration (a real, comparable extraction call: ~1945 completion tokens,
    1733 of it hidden reasoning) AND a real duration-scaling test run (audio_duration_runner.py,
    5-60 min synthetic consultations against the live API): a long consultation's own burst of
    several rapid sequential chunk-extraction calls repeatedly exhausted MAX_RATE_LIMIT_RETRIES
    on real 429s. Undercounting each call's real cost by ~1100+ tokens means the proactive
    pacer lets a burst through faster than the account's real budget allows, every time, and
    true_up() only corrects the bucket AFTER a call completes -- too late to protect the very
    next call in the same burst, which is exactly the failure pattern observed.

    The TOTAL estimate (not just the completion term) is capped below token_bucket's own
    capacity, not just raised outright -- found necessary by the same live test run, the hard
    way: naively adding a flat 2000-token completion estimate on top of a large prompt (a
    scribe chunk near _MAX_TRANSCRIPT_CHARS_FOR_SCRIBING's ~26,000-char ceiling estimates to
    ~6500+ prompt tokens alone) pushed the total estimate PAST _TOKEN_BURST_CAPACITY (8000,
    Groq's own real per-minute ceiling for this account -- verified live via response headers).
    TokenBucket.consume() deliberately raises rather than block forever on an amount it can
    never satisfy -- correct for a call that truly cannot fit, but that comment's own citation
    (a real 25,382-character transcript worked untruncated) proves calls at this size DO
    complete successfully in practice, so a raw additive estimate exceeding the account's own
    ceiling is the estimate being too pessimistic for a large prompt, not evidence the call is
    infeasible -- a large prompt's real completion (reasoning included) evidently isn't also
    maximal just because the prompt is long. Capping the total leaves every large, real, working
    call estimatable (paced at the account's practical max instead of failing before ever being
    attempted) while a genuinely small prompt still gets the full, realistic completion
    allowance above.
    """
    estimated_prompt_tokens = len(prompt.encode("utf-8")) / 4.5
    estimated_completion_tokens = min(float(max_tokens), 2000.0)
    total = estimated_prompt_tokens + estimated_completion_tokens
    return min(total, _TOKEN_BURST_CAPACITY - 500.0)
