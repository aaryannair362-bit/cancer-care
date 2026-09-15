"""
POST /api/transcribe-audio -- server-side speech-to-text backing all 4 voice-input flows
(replaces the browser's SpeechRecognition). Stateless (no DB/patient/org involvement), so
these tests focus on auth, upload validation, and PHI-safe error handling -- the actual
Groq call is mocked via scribe.transcribe_audio (see tests/unit/test_scribe_audio_transcription.py
for the Groq-call-shape tests), following the same mock_groq_json-style convention
test_voice_input_robustness.py uses for the JSON-based voice endpoints.
"""
import pytest

from app import main as app_main


def _mock_transcribe(monkeypatch, text=None, raise_exc=None):
    def _fake(audio_bytes, content_type, filename):
        if raise_exc is not None:
            raise raise_exc
        return text

    monkeypatch.setattr(app_main.scribe, "transcribe_audio", _fake)


@pytest.fixture
def doctor(make_user):
    return make_user(email="doctor@transcribe-audio.com", role="Doctor")


def test_requires_authentication(client):
    resp = client.post("/api/transcribe-audio", files={"audio": ("r.webm", b"fake-audio", "audio/webm")})
    assert resp.status_code in (401, 403)


def test_returns_transcript_on_success(client, doctor, auth_headers, monkeypatch):
    _mock_transcribe(monkeypatch, text="Patient has acidity for five days, prescribe Pantop 40mg tablet OD")
    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("r.webm", b"fake-audio-bytes", "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 200
    assert resp.json() == {"transcript": "Patient has acidity for five days, prescribe Pantop 40mg tablet OD"}


def test_empty_audio_rejected(client, doctor, auth_headers, monkeypatch):
    _mock_transcribe(monkeypatch, text="should never be reached")
    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("r.webm", b"", "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 400


def test_oversized_audio_rejected(client, doctor, auth_headers, monkeypatch):
    monkeypatch.setattr(app_main, "MAX_AUDIO_UPLOAD_BYTES", 10)
    _mock_transcribe(monkeypatch, text="should never be reached")
    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("r.webm", b"x" * 100, "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 413


def test_groq_failure_returns_generic_error_not_exception_detail(client, doctor, auth_headers, monkeypatch):
    """Same rule /api/scribe follows: a Groq-side error message can echo request/PHI content
    back, so the client must only ever see a generic message, never str(exception)."""
    _mock_transcribe(monkeypatch, raise_exc=RuntimeError("upstream said: patient John Doe has HIV"))
    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("r.webm", b"fake-audio-bytes", "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 502
    assert "John Doe" not in resp.text
    assert "HIV" not in resp.text


def test_missing_groq_api_key_returns_generic_error(client, doctor, auth_headers, monkeypatch):
    _mock_transcribe(monkeypatch, raise_exc=ValueError("Groq API key not configured. Set GROQ_API_KEY in environment."))
    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("r.webm", b"fake-audio-bytes", "audio/webm")},
        headers=auth_headers(doctor),
    )
    assert resp.status_code == 502


def test_any_authenticated_role_can_call_it(client, make_user, auth_headers, monkeypatch):
    """Stateless utility endpoint used by Doctor (OPD), Nurse/NursingStation (IPD), and
    HeadNurse -- no role gate, matching /api/scribe and /api/clinical-helper."""
    _mock_transcribe(monkeypatch, text="ok")
    nurse = make_user(email="nurse@transcribe-audio.com", role="Nurse")
    resp = client.post(
        "/api/transcribe-audio",
        files={"audio": ("r.webm", b"fake-audio-bytes", "audio/webm")},
        headers=auth_headers(nurse),
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/transcribe-audio-chunk -- periodic-chunk leg of the same flow (see
# frontend/js/voice-capture.js's module docstring for the production Render-sleep bug this
# exists to fix). Each call transcribes one chunk via the same _transcribe_one_audio_file this
# endpoint's sibling above already uses; a real session accumulates several before is_final.
# ---------------------------------------------------------------------------

def _upload_chunk(client, headers, session_id, chunk_index, is_final, audio=b"fake-audio-bytes"):
    return client.post(
        "/api/transcribe-audio-chunk",
        files={"audio": (f"chunk_{chunk_index}.webm", audio, "audio/webm")},
        data={"session_id": session_id, "chunk_index": str(chunk_index), "is_final": "true" if is_final else "false"},
        headers=headers,
    )


def test_single_chunk_session_is_final_immediately(client, doctor, auth_headers, monkeypatch):
    """A short recording that never rotates -- exactly one chunk, uploaded as is_final=true --
    matches the old /api/transcribe-audio single-shot behavior."""
    _mock_transcribe(monkeypatch, text="Patient has fever for two days")
    resp = _upload_chunk(client, auth_headers(doctor), "sess-1", 0, is_final=True)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"transcript": "Patient has fever for two days", "transcriptPartial": False}


def test_multiple_chunks_join_in_order_regardless_of_arrival_order(client, doctor, auth_headers, monkeypatch):
    """Chunk uploads can race over the network -- the server must join by explicit chunk_index,
    not the order requests happen to arrive in."""
    texts = {0: "first chunk", 1: "second chunk", 2: "third chunk"}
    headers = auth_headers(doctor)

    def _fake(audio_bytes, content_type, filename):
        # filename encodes which chunk this call is for (see _upload_chunk) -- lets the mock
        # return the RIGHT text regardless of call order, same as the real per-chunk transcribe
        # call would based on that chunk's own audio content.
        idx = int(filename.split("_")[1].split(".")[0])
        return texts[idx]

    monkeypatch.setattr(app_main.scribe, "transcribe_audio", _fake)

    # Upload out of order: 1, then 0, then 2 (final).
    r1 = _upload_chunk(client, headers, "sess-order", 1, is_final=False)
    assert r1.status_code == 200, r1.text
    assert r1.json() == {"status": "accepted", "chunk_index": 1}
    r0 = _upload_chunk(client, headers, "sess-order", 0, is_final=False)
    assert r0.status_code == 200, r0.text
    r2 = _upload_chunk(client, headers, "sess-order", 2, is_final=True)
    assert r2.status_code == 200, r2.text
    assert r2.json() == {"transcript": "first chunk second chunk third chunk", "transcriptPartial": False}


def test_a_failed_chunk_is_dropped_and_flags_transcript_partial(client, doctor, auth_headers, monkeypatch):
    """A chunk whose transcription genuinely fails (Groq/Sarvam error) must not abort the whole
    session -- it's dropped, and the final response flags transcriptPartial so the doctor gets
    a visible signal instead of a silently-incomplete transcript (same convention as
    scribe.py's noteExtractionFailed)."""
    headers = auth_headers(doctor)

    def _fake(audio_bytes, content_type, filename):
        if "chunk_1" in filename:
            raise RuntimeError("simulated transient failure on this chunk")
        return "ok chunk"

    monkeypatch.setattr(app_main.scribe, "transcribe_audio", _fake)

    r0 = _upload_chunk(client, headers, "sess-partial", 0, is_final=False)
    assert r0.status_code == 200, r0.text
    r1 = _upload_chunk(client, headers, "sess-partial", 1, is_final=False)
    assert r1.status_code == 200, r1.text  # the chunk upload itself still succeeds -- failure is per-chunk, not per-request
    r2 = _upload_chunk(client, headers, "sess-partial", 2, is_final=True)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["transcript"] == "ok chunk ok chunk"  # chunk 1's text is missing, not "" inserted mid-string
    assert body["transcriptPartial"] is True


def test_session_state_is_cleared_after_finalizing(client, doctor, auth_headers, monkeypatch):
    """A finalized session must not leak into a later session that happens to reuse chunk
    indices -- confirms the per-session dict entry is actually removed, not just marked done."""
    _mock_transcribe(monkeypatch, text="session one")
    first = _upload_chunk(client, auth_headers(doctor), "sess-reuse", 0, is_final=True)
    assert first.json()["transcript"] == "session one"

    _mock_transcribe(monkeypatch, text="session two")
    second = _upload_chunk(client, auth_headers(doctor), "sess-reuse", 0, is_final=True)
    assert second.json()["transcript"] == "session two"  # not "session one session two"


def test_chunk_session_requires_authentication(client):
    resp = client.post(
        "/api/transcribe-audio-chunk",
        files={"audio": ("chunk_0.webm", b"fake-audio", "audio/webm")},
        data={"session_id": "sess-noauth", "chunk_index": "0", "is_final": "true"},
    )
    assert resp.status_code in (401, 403)


def test_oversized_chunk_rejected(client, doctor, auth_headers, monkeypatch):
    monkeypatch.setattr(app_main, "MAX_AUDIO_UPLOAD_BYTES", 10)
    _mock_transcribe(monkeypatch, text="should never be reached")
    resp = _upload_chunk(client, auth_headers(doctor), "sess-oversized", 0, is_final=True, audio=b"x" * 100)
    assert resp.status_code == 413


def test_stale_chunk_sessions_are_purged(client, doctor, auth_headers, monkeypatch):
    """A recording abandoned mid-way (browser closed before Stop ever sends is_final) must not
    leak its entry in the in-memory session dict forever -- the class of unbounded-growth
    failure the design explicitly guards against (see main.py's
    _purge_stale_audio_chunk_sessions)."""
    _mock_transcribe(monkeypatch, text="abandoned")
    headers = auth_headers(doctor)
    stale = _upload_chunk(client, headers, "sess-stale", 0, is_final=False)
    assert stale.status_code == 200, stale.text
    assert "sess-stale" in app_main._audio_chunk_sessions

    monkeypatch.setattr(
        app_main, "_audio_chunk_session_started_at",
        {"sess-stale": app_main._audio_chunk_session_started_at["sess-stale"] - app_main._AUDIO_CHUNK_SESSION_MAX_AGE_SEC - 1},
    )

    # Any subsequent chunk upload (a different, live session) triggers the purge sweep.
    _upload_chunk(client, headers, "sess-fresh", 0, is_final=False)
    assert "sess-stale" not in app_main._audio_chunk_sessions
