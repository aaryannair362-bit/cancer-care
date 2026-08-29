"""
Real-browser regression test for the CCA Medical Oncologist's AI Scribe consultation flow
(frontend/medical_oncologist.html): mic -> Stop & Transcribe -> Generate AI Draft -> review/
edit -> Finalise Consultation Note. No CCA role page had any real-browser test coverage before
this file -- every prior CCA test was API-level (TestClient), which cannot catch the class of
bug this suite exists for (broken element wiring, wrong endpoint paths, a JS reference error).

Mocks at the same LLM/transcription boundary as every other voice test in this suite
(tests/_voice_helpers.py's convention): the browser's mocked MediaRecorder produces a fake
audio blob, `sarvam_transcriber.transcribe_chunks` (the real code path for this repo's
configured TRANSCRIPTION_PROVIDER=sarvam default -- NOT scribe.transcribe_audio, which only
backs the "whisper" fallback) returns a canned transcript, and scribe._call_groq_api returns a
canned structured draft.
"""
import json

import pytest

from tests.e2e.conftest import login_as
from tests._voice_helpers import mint_tokens, set_tokens_in_browser

pytestmark = pytest.mark.e2e

DICTATION = (
    "Doctor: Follow-up visit for a 58 year old female with newly staged Stage two A invasive "
    "ductal carcinoma of the right breast, hormone receptor positive, HER2 negative. She "
    "reports mild fatigue and occasional nausea. Plan neoadjuvant systemic therapy. Start "
    "Ondansetron four milligrams twice daily as needed for nausea. Follow up in two weeks."
)


@pytest.fixture
def cca_patient(make_user, db_session):
    from app.models_cca import CCAPatient

    oncologist = make_user(email="medonc@e2e-cca.com", role="CCAMedicalOncologist")
    patient = CCAPatient(
        mrn="E2E-CCA-0001", name="E2E Voice Consult Patient", age=58, sex="Female",
        organization_id=oncologist.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return oncologist, patient


def test_medical_oncologist_voice_consultation_saves_and_is_readable_back(
    js_page, live_server_url, cca_patient, monkeypatch
):
    import app.main as app_main

    oncologist, patient = cca_patient

    def _fake_call_groq_api(prompt, system=None, temperature=0.3):
        return json.dumps({
            "chiefComplaint": "Mild fatigue, occasional nausea",
            "hpi": "58F with newly staged Stage IIA invasive ductal carcinoma, HR+/HER2-.",
            "primaryDiagnosis": "Stage IIA invasive ductal carcinoma, right breast, HR-positive, HER2-negative",
            "differentialDiagnosis": "",
            "medications": [{"drugName": "Ondansetron", "dose": "4mg", "frequency": "BD"}],
            "advice": "Continue current supportive care, follow up in 2 weeks.",
            "labTests": [],
        })

    monkeypatch.setattr(app_main.scribe, "_call_groq_api", _fake_call_groq_api)
    # tests/conftest.py pins TRANSCRIPTION_PROVIDER=whisper for the whole suite (real
    # production default is "sarvam", backend/.env) -- mock the endpoint's whisper-path
    # target, scribe.transcribe_audio, to match the test environment's actual code path.
    monkeypatch.setattr(app_main.scribe, "transcribe_audio", lambda audio_bytes, content_type, filename: DICTATION)

    login_as(js_page, live_server_url, oncologist, landing_path="/medical_oncologist.html")
    js_page.wait_for_timeout(500)

    js_page.evaluate(f"openPatientFromQueue({patient.id})")
    js_page.wait_for_timeout(500)

    assert js_page.eval_on_selector("#consult-form-area", "el => el.style.display") == "block", (
        "consultation form never became visible after opening the patient"
    )

    js_page.click("#scribe-start-btn")
    js_page.wait_for_timeout(150)
    js_page.click("#scribe-stop-btn", force=True)
    js_page.wait_for_timeout(500)
    transcript_val = js_page.eval_on_selector("#scribe-transcript", "el => el.value")
    assert transcript_val == DICTATION, f"transcript not populated correctly: {transcript_val!r}"

    js_page.click("button:has-text('Generate AI Draft')")
    js_page.wait_for_function(
        "document.getElementById('consult-cc').value.length > 0", timeout=10000
    )
    assert js_page.eval_on_selector("#consult-hpi", "el => el.value").startswith("58F")
    assert "Stage IIA" in js_page.eval_on_selector("#consult-diagnosis", "el => el.value")
    assert js_page.eval_on_selector("#consult-advice", "el => el.value")

    js_page.fill("#consult-exam", "No acute distress, vitals stable, right breast mass palpable.")

    js_page.click("button:has-text('Finalise Consultation Note')")
    js_page.wait_for_function(
        "document.getElementById('consult-status').textContent.includes('finalised')", timeout=10000
    )

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    # The actual point of the test: does the finalized output show up again when read back,
    # the same question a real oncologist reopening this patient tomorrow would be asking.
    tokens = mint_tokens(oncologist)
    r = js_page.request.get(
        f"{live_server_url}/api/cca/patients/{patient.id}/case-summary",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r.status == 200, f"case-summary failed after finalising a consultation: {r.status} {r.text()}"
    body = r.json()
    assert len(body["encounters"]) == 1
    enc = body["encounters"][0]
    assert enc["note_status"] == "FINAL"
    assert "Stage IIA" in enc["diagnosis"]
    # drug_matcher canonicalizes the bare name + dose against the real medicines dataset
    # (e.g. "Ondansetron 4mg Tablet") -- documented, intended behavior, not an exact echo.
    assert "Ondansetron" in enc["medications"][0]["drugName"]
