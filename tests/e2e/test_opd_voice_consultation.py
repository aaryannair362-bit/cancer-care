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
    queue_transcription_result,
    set_tokens_in_browser,
)

pytestmark = pytest.mark.e2e


@pytest.fixture
def opd_patient(make_user, db_session):
    from app.models import Patient

    doctor = make_user(email="doctor@e2e-opd.com", role="Doctor")
    patient = Patient(name="E2E OPD Patient", age=30, gender="M", ward="OPD",
                       organization_id=doctor.organization_id, created_by=doctor.id)
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
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

    def _capture(prompt, system=None, temperature=0.3, max_tokens=3000):
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
