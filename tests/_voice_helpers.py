"""
Shared "simulate voice input" mechanics, used by BOTH the pytest e2e suite (tests/e2e/) and
the standalone scale-test runner (tests/scale/runner.py).

As of the MediaRecorder/Groq-Whisper migration (replacing the browser's SpeechRecognition,
which mis-transcribed Hindi/Hinglish speech into English-phonetic nonsense), the mock point
moved from the browser's speech-recognition object to the Python method boundary: the browser
never talks to Groq directly, it talks to THIS APP'S OWN `POST /api/transcribe-audio`
endpoint, which calls `scribe.transcribe_audio`. So the convention here matches every other
Groq mock in this suite (`monkeypatch.setattr(app_main.scribe, "_call_groq_api", ...)`,
`tests/conftest.py`'s `mock_groq_json`): stub `scribe.transcribe_audio` in-process and let the
real request flow all the way through -- real browser `fetch`+`FormData`, a real network hop
to the real live server, real FastAPI multipart parsing, real auth -- with only the actual
outbound Groq HTTP call stubbed. `MOCK_MEDIA_RECORDER_INIT_SCRIPT` only needs to produce SOME
non-empty audio blob for the upload to carry; its content is never inspected by anything,
since Groq is never actually called in tests.
"""
import socket
import threading
import time

import requests

MOCK_MEDIA_RECORDER_INIT_SCRIPT = """
window.__mockRecorderInstances = [];
class MockMediaRecorder {
    constructor(stream, options) {
        this.stream = stream;
        this.mimeType = (options && options.mimeType) || 'audio/webm';
        this.ondataavailable = null;
        this.onstart = null;
        this.onstop = null;
        this.onerror = null;
        window.__mockRecorderInstances.push(this);
    }
    start() {
        setTimeout(() => { if (this.onstart) this.onstart(); }, 0);
    }
    stop() {
        setTimeout(() => {
            if (this.ondataavailable) {
                this.ondataavailable({ data: new Blob(['mock-audio'], { type: this.mimeType }) });
            }
            if (this.onstop) this.onstop();
        }, 0);
    }
}
MockMediaRecorder.isTypeSupported = () => true;
window.MediaRecorder = MockMediaRecorder;
if (!navigator.mediaDevices) { navigator.mediaDevices = {}; }
navigator.mediaDevices.getUserMedia = () => Promise.resolve({ getTracks: () => [] });
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_live_server(app, host="127.0.0.1", port=None, health_path="/api/health"):
    """
    Boots a real uvicorn.Server for `app` on a background thread and waits until it answers.
    Returns (base_url, stop_fn). Playwright needs a real listening HTTP server -- it cannot
    talk to an in-process ASGI TestClient the way httpx-based tests do.
    """
    import uvicorn

    port = port or free_port()
    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    last_error = None
    for _ in range(100):
        try:
            requests.get(f"{base_url}{health_path}", timeout=2)
            break
        except requests.exceptions.RequestException as exc:
            last_error = exc
            time.sleep(0.2)
    else:
        raise RuntimeError(f"live server did not start in time: {last_error}")

    def stop():
        server.should_exit = True
        thread.join(timeout=5)

    return base_url, stop


def mint_tokens(user):
    """Real access+refresh token strings for a user, bypassing the HTTP login endpoint."""
    from app import auth as app_auth

    token_data = {
        "user_id": user.id, "email": user.email, "role": user.role,
        "organization_id": user.organization_id,
    }
    return {
        "access_token": app_auth.create_access_token(token_data),
        "refresh_token": app_auth.create_refresh_token(token_data),
    }


def set_tokens_in_browser(page, base_url, access_token, refresh_token):
    page.goto(f"{base_url}/index.html")
    page.evaluate(
        """([access, refresh]) => {
            localStorage.setItem('access_token', access);
            localStorage.setItem('refresh_token', refresh);
        }""",
        [access_token, refresh_token],
    )


def queue_transcription_result(monkeypatch, app_main, *texts):
    """
    Monkeypatches app_main.scribe.transcribe_audio to pop one canned transcript string off
    `texts` (in call order) each time a page's mic Start/Stop cycle uploads a recording,
    raising AssertionError if called more times than queued -- this still catches a
    duplicate-upload regression the same way the old fire_speech_result-based duplication
    test did.

    Replaces both the old fire_speech_result (a single canned value) and speak_utterances
    (several fired incrementally): there's no more incremental delivery in the
    MediaRecorder/Whisper model, only one full transcript returned per Stop click, so a
    "multi-utterance conversation" is just one pre-joined string passed as a single queued
    result, e.g. queue_transcription_result(monkeypatch, app_main, " ".join(utterances)).
    Call this BEFORE the page's Start/Stop click pair that should receive it.
    """
    remaining = list(texts)

    def _fake(audio_bytes, content_type, filename):
        if not remaining:
            raise AssertionError(
                "scribe.transcribe_audio was called more times than "
                "queue_transcription_result was given canned results for"
            )
        return remaining.pop(0)

    monkeypatch.setattr(app_main.scribe, "transcribe_audio", _fake)


def queue_chunk_transcription_results(monkeypatch, app_main, *texts):
    """
    Chunked-upload equivalent of queue_transcription_result -- monkeypatches
    app_main.scribe.transcribe_audio (the same method the "whisper" provider path calls; see
    main.py's _transcribe_one_audio_file, shared by both /api/transcribe-audio and
    /api/transcribe-audio-chunk) to pop one canned transcript per call, in call order. One call
    happens per uploaded chunk (both mid-recording rotations and the final is_final upload), so
    for N expected chunks pass N canned strings, e.g.
    queue_chunk_transcription_results(monkeypatch, app_main, "first chunk text", "second chunk text")
    for a recording that rotates once before Stop. The server joins them with a space and
    returns that as the final transcript -- assert on the space-joined result, not any one
    chunk's text alone.
    """
    remaining = list(texts)

    def _fake(audio_bytes, content_type, filename):
        if not remaining:
            raise AssertionError(
                "scribe.transcribe_audio was called more times than "
                "queue_chunk_transcription_results was given canned results for"
            )
        return remaining.pop(0)

    monkeypatch.setattr(app_main.scribe, "transcribe_audio", _fake)


def mock_transcription_network_failure(page):
    """
    Secondary helper for the small number of tests that specifically want a NETWORK-level
    failure of POST /api/transcribe-audio (the request never reaches the real FastAPI
    endpoint at all) as distinct from a Groq-level failure (queue_transcription_result's
    sibling: monkeypatch scribe.transcribe_audio to raise, which DOES reach the real
    endpoint and exercises its error handling). Not the default mechanism -- see the module
    docstring for why in-process mocking is preferred everywhere else.
    """
    page.route("**/api/transcribe-audio", lambda route: route.abort())
