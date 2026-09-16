"""
Real-browser regression tests for the OPD "Start Consulting -> speak -> Stop Consulting"
flow (frontend/opd.html). These caught two real production bugs during manual investigation
of a bug report ("data not getting filled in after Stop Consulting"):

  1. opd.html's apiRequest() declared `const accessToken` but reassigned it after a token
     refresh -- a guaranteed `TypeError: Assignment to constant variable` on the very first
     API call made after the 15-minute access token expires, silently killing the request
     that would have saved/displayed the consultation. A real consultation (see the multi-page
     transcripts in tests/from_data/fixtures.py) very plausibly outlasts 15 minutes.
  2. The live transcript accumulator kept two variables (`accumulatedTranscript`,
     `currentFinal`) that were always incremented identically and then concatenated together
     for display/submission -- doubling every "final" chunk of speech in what actually got
     sent to the AI scribe. Voice capture has since moved to MediaRecorder + server-side
     Whisper transcription (see frontend/js/voice-capture.js), which has no interim/live
     accumulation at all -- this specific bug class is now structurally impossible, so the
     duplication test below is re-anchored to guard against an upload-level duplicate instead.

Both original bugs are fixed in opd.html; these tests pin the fixes down so neither regresses.
"""
import pytest

from tests.e2e.conftest import (
    mint_expired_access_token,
    mint_tokens,
    queue_chunk_transcription_results,
    queue_transcription_result,
    set_tokens_in_browser,
)

pytestmark = pytest.mark.e2e


@pytest.fixture
def opd_patient(make_user, db_session):
    from datetime import date
    from app.models import Patient, QueueToken

    doctor = make_user(email="doctor@e2e-opd.com", role="Doctor")
    patient = Patient(name="E2E OPD Patient", age=30, gender="M", ward="OPD",
                       organization_id=doctor.organization_id, created_by=doctor.id)
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    # opd.html's #patient-select is populated exclusively from GET /api/queue/tokens
    # (loadOpdQueue(), see frontend/opd.html) -- a raw Patient row alone never appears there. A
    # Doctor caller's own query is additionally filtered to QueueToken.doctor_id ==
    # current_user["id"] (routers/appointments.py's list_queue), so the token must be issued to
    # THIS specific doctor, not just the same organization, to show up in the dropdown at all --
    # matching how a real walk-in patient actually reaches this screen (Front Desk check-in).
    db_session.add(QueueToken(
        organization_id=doctor.organization_id, patient_id=patient.id, doctor_id=doctor.id,
        token_number=1, token_date=date.today(), status="Waiting", issued_by=doctor.id,
    ))
    db_session.commit()
    return doctor, patient


def _record_and_stop(js_page, live_server_url, patient_id):
    """Drives Start Consulting -> (mocked recording) -> Stop Consulting. The canned
    transcript scribe.transcribe_audio returns must already be queued via
    queue_transcription_result before calling this."""
    js_page.goto(f"{live_server_url}/opd.html")
    js_page.wait_for_selector("#patient-select")
    js_page.wait_for_function("document.querySelector('#patient-select').options.length > 1")
    js_page.select_option("#patient-select", str(patient_id))
    js_page.click("#start-consult-btn")
    js_page.wait_for_timeout(150)
    js_page.click("#stop-consult-btn")
    js_page.wait_for_timeout(1200)


def test_voice_consultation_populates_draft_with_fresh_token(
    js_page, live_server_url, opd_patient, monkeypatch
):
    import app.main as app_main
    doctor, patient = opd_patient
    monkeypatch.setattr(app_main.scribe, "_call_groq_api",
                         lambda *a, **k: '{"chiefComplaint": "Fever and cough"}')
    queue_transcription_result(monkeypatch, app_main, "Doctor: patient reports fever and cough for three days")

    tokens = mint_tokens(doctor)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])

    _record_and_stop(js_page, live_server_url, patient.id)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"
    chief_complaint = js_page.eval_on_selector("#chief-complaint", "el => el.value")
    assert chief_complaint == "Fever and cough"


def test_voice_consultation_survives_access_token_expiring_mid_session(
    js_page, live_server_url, opd_patient, monkeypatch
):
    """
    Regression test for the `const accessToken` crash: mints a token that's already expired,
    so apiRequest()'s very first call (loading the patient dropdown) must hit the 401 ->
    refresh path. Before the fix this threw `TypeError: Assignment to constant variable` and
    the patient dropdown never populated, let alone the consultation draft.
    """
    import app.main as app_main
    doctor, patient = opd_patient
    monkeypatch.setattr(app_main.scribe, "_call_groq_api",
                         lambda *a, **k: '{"chiefComplaint": "Fever and cough"}')
    queue_transcription_result(monkeypatch, app_main, "Doctor: patient reports fever and cough for three days")

    tokens = mint_tokens(doctor)
    expired_access = mint_expired_access_token(doctor)
    set_tokens_in_browser(js_page, live_server_url, expired_access, tokens["refresh_token"])

    _record_and_stop(js_page, live_server_url, patient.id)

    assert js_page.js_errors == [], (
        f"apiRequest crashed on token refresh: {js_page.js_errors}"
    )
    chief_complaint = js_page.eval_on_selector("#chief-complaint", "el => el.value")
    assert chief_complaint == "Fever and cough", (
        "draft was not populated after an access-token refresh mid-session"
    )


def test_transcript_is_not_duplicated_across_multiple_utterances(
    js_page, live_server_url, opd_patient, monkeypatch
):
    """
    Regression test for the old accumulatedTranscript/currentFinal double-counting bug --
    re-anchored (see module docstring) to guard against the new architecture's analogous
    failure mode: the uploaded transcript reaching the scribe prompt more than once.
    """
    import app.main as app_main
    doctor, patient = opd_patient
    captured_prompts = []

    def _capture(prompt, system=None, temperature=0.3, max_tokens=3000, **kwargs):
        captured_prompts.append(prompt)
        return '{"chiefComplaint": "test"}'

    monkeypatch.setattr(app_main.scribe, "_call_groq_api", _capture)
    queue_transcription_result(
        monkeypatch, app_main,
        "Doctor: first thing said. Patient: second thing said",
    )

    tokens = mint_tokens(doctor)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])

    _record_and_stop(js_page, live_server_url, patient.id)

    transcript_value = js_page.eval_on_selector("#transcript-input", "el => el.value")
    assert transcript_value.count("first thing said") == 1, (
        f"transcript duplicated: {transcript_value!r}"
    )
    assert transcript_value.count("second thing said") == 1, (
        f"transcript duplicated: {transcript_value!r}"
    )
    assert captured_prompts, "scribe was never called"
    assert captured_prompts[0].count("first thing said") == 1


def test_long_recording_uploads_chunks_periodically_and_joins_the_transcript(
    js_page, live_server_url, opd_patient, monkeypatch
):
    """
    Regression test for a real production bug: a long consultation recording made ZERO
    backend requests between Start and Stop (MediaRecorder captures entirely client-side),
    which let Render's free-tier service scale to zero mid-recording after 15 minutes of no
    traffic -- confirmed from real production logs. voice-capture.js now restarts the
    MediaRecorder every chunkIntervalMs (3 min in production, not overridable from opd.html's
    own call site) and uploads each finished chunk immediately via
    POST /api/transcribe-audio-chunk, so real traffic flows throughout a long recording
    instead of only once at Stop.

    Drives this via Playwright's virtual clock (page.clock) rather than actually waiting real
    minutes or modifying opd.html to accept a test-only short interval -- fast-forwarding
    virtual time fires the real 3-minute setInterval exactly as production would, with zero
    production code changes needed for testability. Two rotations (6+ minutes of virtual time)
    plus the final Stop-triggered chunk = 3 total chunk uploads, each transcribed independently
    and joined server-side in order.
    """
    import app.main as app_main
    doctor, patient = opd_patient
    monkeypatch.setattr(app_main.scribe, "_call_groq_api",
                         lambda *a, **k: '{"chiefComplaint": "Long consultation"}')
    queue_chunk_transcription_results(
        monkeypatch, app_main, "first chunk of the conversation", "second chunk of the conversation",
        "final short chunk",
    )

    tokens = mint_tokens(doctor)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])

    js_page.goto(f"{live_server_url}/opd.html")
    js_page.wait_for_selector("#patient-select")
    js_page.wait_for_function("document.querySelector('#patient-select').options.length > 1")
    js_page.select_option("#patient-select", str(patient.id))

    # Installed AFTER navigation (so the page's own initial load/auth calls run on real time)
    # but BEFORE Start Consulting is clicked, so the rotation timer voice-capture.js sets up
    # inside start() is the one actually being advanced.
    js_page.clock.install()
    js_page.click("#start-consult-btn")
    js_page.wait_for_timeout(150)  # let the first MediaRecorder actually start (real time, unaffected by the fake clock)

    js_page.clock.fast_forward("03:05")  # past one 3-minute rotation
    js_page.wait_for_timeout(150)  # let the rotation's chunk upload actually reach the (real) server
    js_page.clock.fast_forward("03:05")  # past a second rotation
    js_page.wait_for_timeout(150)

    js_page.click("#stop-consult-btn")
    js_page.wait_for_timeout(1200)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"
    transcript_value = js_page.eval_on_selector("#transcript-input", "el => el.value")
    assert transcript_value == "first chunk of the conversation second chunk of the conversation final short chunk", (
        f"chunks were not uploaded/joined in order: {transcript_value!r}"
    )
    chief_complaint = js_page.eval_on_selector("#chief-complaint", "el => el.value")
    assert chief_complaint == "Long consultation"


def test_dropped_chunk_surfaces_partial_transcript_warning_to_the_doctor(
    js_page, live_server_url, opd_patient, monkeypatch
):
    """
    Regression test for a real bug found live in production: a mid-recording chunk upload that
    never reaches the server at all (a transient 5xx, not the 401 case voice-capture.js already
    retries) is dropped by design -- _rotateChunk()'s fire-and-forget upload swallows the
    failure so one bad chunk can't crash the recording. main.py's transcribe_audio_chunk_endpoint
    correctly detects the resulting gap (fewer chunk indices present than the final chunk_index
    implies) and returns transcriptPartial: true, and voice-capture.js's stop() correctly surfaces
    it via wasPartial() -- but NO calling page ever called wasPartial(), so a doctor whose
    consultation lost a real ~3-minute slice this way saw a plain "done" status with zero
    indication anything was missing. Confirmed live: a real ~20-minute test consultation's
    transcript came back 3000 characters shorter than an identical prior run, with a clean
    "Transcribed" status. Fixed across every page that uses voice-capture.js
    (opd.html/medical_oncologist.html/radiation_oncologist.html/surgical_oncologist.html) to
    check wasPartial() the same way wasInterrupted() already was; this test pins opd.html's fix.

    Simulates the dropped chunk at the SERVER side (the first chunk's transcription raises,
    matching transcribe_audio_chunk_endpoint's own except-and-drop path) rather than at the
    upload/network level, since that's what actually produces transcriptPartial: true --
    equivalent to (and simpler to simulate than) the real upload-level failure, which has the
    same server-observable effect: that chunk_index is never present in the session's dict.
    """
    import app.main as app_main
    doctor, patient = opd_patient
    monkeypatch.setattr(app_main.scribe, "_call_groq_api",
                         lambda *a, **k: '{"chiefComplaint": "Consultation with a dropped chunk"}')

    call_count = {"n": 0}

    def _fake_transcribe(audio_bytes, content_type, filename):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated transient transcription failure for the first chunk")
        return "second chunk of the conversation" if call_count["n"] == 2 else "final short chunk"

    monkeypatch.setattr(app_main.scribe, "transcribe_audio", _fake_transcribe)

    tokens = mint_tokens(doctor)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])

    js_page.goto(f"{live_server_url}/opd.html")
    js_page.wait_for_selector("#patient-select")
    js_page.wait_for_function("document.querySelector('#patient-select').options.length > 1")
    js_page.select_option("#patient-select", str(patient.id))

    js_page.clock.install()
    js_page.click("#start-consult-btn")
    js_page.wait_for_timeout(150)

    js_page.clock.fast_forward("03:05")  # rotation 1 -- this chunk's transcription will fail
    js_page.wait_for_timeout(150)
    js_page.clock.fast_forward("03:05")  # rotation 2 -- succeeds
    js_page.wait_for_timeout(150)

    js_page.click("#stop-consult-btn")
    js_page.wait_for_timeout(1200)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"
    transcript_value = js_page.eval_on_selector("#transcript-input", "el => el.value")
    # The first chunk's text is genuinely missing (not just re-ordered) -- this is the real
    # data loss the warning below must not silently hide.
    assert transcript_value == "second chunk of the conversation final short chunk"
    status_text = js_page.eval_on_selector("#analysis-status", "el => el.textContent")
    assert "missing slice" in status_text, (
        f"doctor was not warned about the dropped chunk: {status_text!r}"
    )
