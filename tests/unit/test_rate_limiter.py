"""
Tests for backend/app/rate_limiter.py -- the proactive Groq call pacing added after live
evidence (tests/scale/results/run.log) that concurrent OPD+IPD callers could 429 together and
each independently sit in scribe.py's reactive retry loop at the same time. Uses an injectable
fake clock/sleep (see TokenBucket's time_fn/sleep_fn params) throughout so these run fast and
deterministically -- no test here waits on real wall-clock time.
"""
import threading

import pytest

from app.rate_limiter import TokenBucket, estimate_tokens, sarvam_doc_ai_request_bucket


class FakeClock:
    """Deterministic, manually-advanced clock + sleep for testing TokenBucket without any
    real wall-clock delay. sleep(seconds) advances the same clock consume() reads from, so a
    bucket's internal "how much time passed" bookkeeping stays self-consistent under test."""

    def __init__(self):
        self.now = 0.0
        self.sleep_calls = []

    def time_fn(self):
        return self.now

    def sleep_fn(self, seconds):
        self.sleep_calls.append(seconds)
        self.now += seconds


def test_consume_within_capacity_never_sleeps():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=1.0, capacity=10.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn)
    bucket.consume(5)
    bucket.consume(5)
    assert clock.sleep_calls == []
    assert bucket.tokens == 0.0


def test_consume_beyond_capacity_blocks_until_refilled():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=2.0, capacity=10.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn)
    bucket.consume(10)  # drains it fully, no sleep yet
    assert clock.sleep_calls == []
    bucket.consume(4)  # needs 4 more at 2/sec -> should sleep ~2s total (possibly in slices)
    assert sum(clock.sleep_calls) == pytest.approx(2.0)
    assert bucket.tokens == pytest.approx(0.0, abs=1e-9)


def test_consume_sleeps_in_bounded_slices_not_one_long_sleep():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=1.0, capacity=1.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn)
    bucket.consume(1)  # drain
    bucket.consume(1, max_wait_sec=2.0)  # needs 1s more, but max_wait_sec caps each slice at 2s (won't matter here)
    # A longer required wait should be split into multiple <=max_wait_sec slices.
    clock2 = FakeClock()
    bucket2 = TokenBucket(rate_per_sec=0.1, capacity=1.0, time_fn=clock2.time_fn, sleep_fn=clock2.sleep_fn)
    bucket2.consume(1)  # drain (needs 10s/token at this rate)
    bucket2.consume(1, max_wait_sec=3.0)  # would need 10s total; each slice capped at 3s
    assert all(s <= 3.0 for s in clock2.sleep_calls)
    assert len(clock2.sleep_calls) > 1


def test_request_amount_larger_than_capacity_raises_immediately_not_blocks_forever():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=1.0, capacity=5.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn)
    with pytest.raises(ValueError):
        bucket.consume(6)
    assert clock.sleep_calls == []  # failed fast, never tried to wait


@pytest.mark.parametrize("bad_kwargs", [{"rate_per_sec": 0}, {"rate_per_sec": -1}, {"capacity": 0}, {"capacity": -1}])
def test_construction_rejects_non_positive_rate_or_capacity(bad_kwargs):
    kwargs = {"rate_per_sec": 1.0, "capacity": 1.0, **bad_kwargs}
    with pytest.raises(ValueError):
        TokenBucket(**kwargs)


def test_refill_is_capped_at_capacity_not_unbounded():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=100.0, capacity=10.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn)
    clock.now += 1000  # a huge amount of elapsed time
    bucket.consume(10)  # should succeed (capped refill still covers this) without sleeping
    assert clock.sleep_calls == []
    assert bucket.tokens == 0.0


def test_concurrent_consumers_never_overspend_the_bucket_below_zero():
    """Real threads (not the fake clock -- this uses actual time.sleep via the default args)
    hammering consume() concurrently must never drive the bucket negative or let combined
    consumption exceed what a correctly-serialized set of calls would allow."""
    bucket = TokenBucket(rate_per_sec=1000.0, capacity=20.0)
    errors = []

    def worker():
        try:
            for _ in range(5):
                bucket.consume(1)
        except Exception as exc:  # pragma: no cover -- would only fire on a real bug
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
    assert bucket.tokens >= -1e-9  # never went negative (floating point epsilon aside)


def test_sarvam_doc_ai_bucket_paced_under_the_documented_10_per_minute_limit():
    """sarvam_doc_ai_request_bucket must stay under Sarvam's own documented Document
    Intelligence ceiling (10 requests/minute, uniform across every plan tier -- see
    ocr_service.py's usage for the incident this was added to fix), with real headroom rather
    than being calibrated right up against it."""
    assert sarvam_doc_ai_request_bucket.rate * 60 < 10
    assert sarvam_doc_ai_request_bucket.capacity <= 10


def test_estimate_tokens_scales_with_prompt_length_and_includes_max_tokens():
    short = estimate_tokens("hi", max_tokens=100)
    longer = estimate_tokens("hi " * 1000, max_tokens=100)
    assert short < longer
    assert short >= 100  # at minimum, the max_tokens completion budget is included


def test_estimate_tokens_completion_cap_reflects_real_reasoning_token_cost():
    """Regression test: the completion-token estimate cap was previously 800 -- calibrated for
    a structured JSON response's visible text alone, before it was known that the configured
    reasoning model (GROQ_MODEL) spends hidden reasoning tokens on top of that, which still
    count fully against the real tokens-per-minute budget. Verified live via
    tests/scale/runner.py's own measured calibration (~1945 real completion tokens for a
    comparable call, 1733 of it hidden reasoning) and a real duration-scaling test run where
    undercounting this caused a long consultation's own burst of rapid sequential calls to
    repeatedly exhaust retries on real 429s. Pinned here so it can't silently regress back down
    -- a caller requesting a large max_tokens (e.g. extract_clinical_facts's 6000) must estimate
    meaningfully above the old 800 cap, not be silently clipped back down to it."""
    estimate = estimate_tokens("short prompt", max_tokens=6000)
    assert estimate >= 1945  # at least the one real measured value this was calibrated against


# ---------------------------------------------------------------------------
# TokenBucket.true_up -- post-call reconciliation against a provider's REAL reported usage.
# Added after live evidence (a real Selenium end-to-end run against Sarvam+Groq) that a
# reasoning-capable Groq model spends hidden "reasoning tokens" estimate_tokens() has no way
# to predict from prompt length alone, which regularly exceeded the pre-call estimate this
# bucket was paced against and caused real 429s under sustained multi-document load.
# ---------------------------------------------------------------------------

def test_true_up_deducts_the_shortfall_when_actual_exceeds_estimate():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=1.0, capacity=100.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn)
    bucket.consume(20)  # pre-call estimate
    assert bucket.tokens == 80.0
    bucket.true_up(actual_amount=50, estimated_amount=20)  # real usage was higher
    assert bucket.tokens == 50.0  # 80 - (50 - 20) shortfall


def test_true_up_does_nothing_when_actual_does_not_exceed_estimate():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=1.0, capacity=100.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn)
    bucket.consume(20)
    bucket.true_up(actual_amount=20, estimated_amount=20)  # exact match
    assert bucket.tokens == 80.0
    bucket.true_up(actual_amount=5, estimated_amount=20)  # estimate overshot -- never refunded
    assert bucket.tokens == 80.0


def test_true_up_can_drive_tokens_negative_and_next_consume_waits_it_out():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_sec=1.0, capacity=100.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn)
    bucket.consume(10)
    bucket.true_up(actual_amount=110, estimated_amount=10)  # 100-token real shortfall
    assert bucket.tokens == pytest.approx(-10.0)
    bucket.consume(1)  # needs 11 tokens' worth of refill at 1/sec from -10
    assert sum(clock.sleep_calls) == pytest.approx(11.0)
