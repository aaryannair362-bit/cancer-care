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


# Calibrated against live Groq headers:
# x-ratelimit-limit-requests: 1000, x-ratelimit-limit-tokens: 8000 (tokens refill continuously in ~60s = ~133 TPM)
# Paced conservatively with headroom for concurrent OPD and IPD users.
_REQUEST_RATE_PER_SEC = 1.0
_REQUEST_BURST_CAPACITY = 10.0
_TOKEN_RATE_PER_SEC = 120.0
_TOKEN_BURST_CAPACITY = 8000.0

request_bucket = TokenBucket(rate_per_sec=_REQUEST_RATE_PER_SEC, capacity=_REQUEST_BURST_CAPACITY)
token_bucket = TokenBucket(rate_per_sec=_TOKEN_RATE_PER_SEC, capacity=_TOKEN_BURST_CAPACITY)


def estimate_tokens(prompt: str, max_tokens: int) -> float:
    """
    Cheap, tokenizer-free estimate of a call's total token cost, used to pace the token
    bucket BEFORE the real usage is known. ~4 characters/token for prompt, plus realistic
    completion estimate (capped at 800 for standard structured JSON responses) rather than
    assuming worst-case max_tokens every call.
    """
    estimated_prompt_tokens = len(prompt) / 4.0
    estimated_completion_tokens = min(float(max_tokens), 800.0)
    return estimated_prompt_tokens + estimated_completion_tokens
