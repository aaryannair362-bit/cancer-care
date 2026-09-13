"""
Unit tests for app.scribe.ScribeEngine._call_groq_api's own request/response plumbing --
distinct from test_scribe_json_parsing.py, which monkeypatches _call_groq_api itself and never
exercises what's inside it. These mock `requests.post` (the only real network call) and use
fresh, injectable-clock rate_limiter buckets so nothing here waits on real wall-clock time or
touches the app-wide singleton buckets other tests/requests might be mid-use of.
"""
import pytest

from app import rate_limiter as rate_limiter_module
from app.rate_limiter import TokenBucket
from app.scribe import ScribeEngine


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def time_fn(self):
        return self.now

    def sleep_fn(self, seconds):
        self.now += seconds


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(response=self)


@pytest.fixture
def engine(monkeypatch):
    engine = ScribeEngine()
    engine.api_key = "test-key"
    # Fresh, fast, isolated buckets -- never the real module-level singletons other code paths
    # might be mid-use of, and no real sleeping even if a test does need to wait one out.
    clock = _FakeClock()
    monkeypatch.setattr(rate_limiter_module, "request_bucket", TokenBucket(rate_per_sec=1000.0, capacity=1000.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn))
    monkeypatch.setattr(rate_limiter_module, "token_bucket", TokenBucket(rate_per_sec=1000.0, capacity=1000.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn))
    return engine


def test_call_groq_api_true_ups_the_token_bucket_from_real_usage(engine, monkeypatch):
    """Regression: a reasoning-capable Groq model's hidden reasoning tokens (never present in
    the visible completion text, see _call_groq_api's reasoning_format="hidden") still count
    toward the account's real tokens-per-minute limit -- confirmed live via the API's own
    `usage.completion_tokens_details.reasoning_tokens`. The pre-call estimate
    (rate_limiter.estimate_tokens) has no way to predict this from prompt length alone, so
    real usage regularly exceeded it and under-paced the bucket. Pins that _call_groq_api
    reconciles the bucket against the response's real `usage.total_tokens` afterward."""
    import requests as requests_module

    def _fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(status_code=200, json_body={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 950, "total_tokens": 1000},
        })

    monkeypatch.setattr(requests_module, "post", _fake_post)

    tokens_before = rate_limiter_module.token_bucket.tokens
    result = engine._call_groq_api("a short prompt", max_tokens=100)

    assert result == "ok"
    estimated = rate_limiter_module.estimate_tokens("a short prompt", 100)
    assert estimated < 1000, "test fixture assumes the estimate undershoots real usage"
    expected_tokens_after = tokens_before - estimated - (1000 - estimated)  # == tokens_before - 1000
    assert rate_limiter_module.token_bucket.tokens == pytest.approx(expected_tokens_after)
    assert rate_limiter_module.token_bucket.tokens == pytest.approx(tokens_before - 1000)


def test_call_groq_api_does_not_true_up_when_estimate_already_covers_real_usage(engine, monkeypatch):
    """The opposite direction must NOT refund the bucket -- see TokenBucket.true_up's docstring
    for why erring toward "less capacity than reality" is the safe direction."""
    import requests as requests_module

    def _fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(status_code=200, json_body={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        })

    monkeypatch.setattr(requests_module, "post", _fake_post)

    tokens_before = rate_limiter_module.token_bucket.tokens
    engine._call_groq_api("a short prompt", max_tokens=100)
    estimated = rate_limiter_module.estimate_tokens("a short prompt", 100)
    assert estimated > 7  # the estimate comfortably covers the tiny real usage here
    assert rate_limiter_module.token_bucket.tokens == pytest.approx(tokens_before - estimated)


def test_call_groq_api_tolerates_a_response_with_no_usage_field(engine, monkeypatch):
    """Some responses/providers might omit `usage` entirely -- must not raise, and simply
    skips reconciliation (the pre-call estimate consumption already happened)."""
    import requests as requests_module

    def _fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(status_code=200, json_body={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(requests_module, "post", _fake_post)

    tokens_before = rate_limiter_module.token_bucket.tokens
    result = engine._call_groq_api("a short prompt", max_tokens=100)
    assert result == "ok"
    estimated = rate_limiter_module.estimate_tokens("a short prompt", 100)
    assert rate_limiter_module.token_bucket.tokens == pytest.approx(tokens_before - estimated)
