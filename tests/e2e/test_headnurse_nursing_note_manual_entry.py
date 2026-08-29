"""
Real-browser regression test for HeadNurse's own copy of the "Nursing Notes" modal
(frontend/headnurse.html) after the dead voice/Process UI was stripped out -- mirrors
tests/e2e/test_ipd_nursing_note_manual_entry.py's ipd.html coverage. headnurse.html has its
own separate, previously near-duplicate copy of this modal's markup and handlers (HeadNurse
never reaches ipd.html itself -- see that file's own redirect), so it needed the identical fix
applied independently, and needs its own regression test.
"""
import pytest

from tests.e2e.conftest import mint_tokens, set_tokens_in_browser

pytestmark = pytest.mark.e2e


@pytest.fixture
def headnurse_patient(make_user, db_session):
    from app.models import Patient

    head_nurse = make_user(email="head@e2e-hn-manual.com", role="HeadNurse")
    patient = Patient(name="E2E HeadNurse Manual Nursing Patient", age=58, gender="F", ward="General",
                       organization_id=head_nurse.organization_id, created_by=head_nurse.id)
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return head_nurse, patient


def test_headnurse_nursing_notes_modal_editor_visible_and_save_works_without_process(
    js_page, live_server_url, headnurse_patient
):
    head_nurse, patient = headnurse_patient
    js_page.on("dialog", lambda d: d.accept())  # the Save handler alert()s on success

    tokens = mint_tokens(head_nurse)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])
    js_page.goto(f"{live_server_url}/headnurse.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openNursingConsult({patient.id})")
    js_page.wait_for_timeout(300)

    assert js_page.eval_on_selector("#nursing-extracted", "el => getComputedStyle(el).display") != "none", (
        "vitals/labs/note editor is still hidden by default -- a HeadNurse has no way to add a note"
    )
    assert js_page.query_selector("#nursing-voice-btn") is None, "dead mic button still present"
    assert js_page.query_selector("#nursing-process-btn") is None, "dead Process button still present"

    add_vital_btn = js_page.query_selector("#nursing-extracted button:has-text('Add')")
    add_vital_btn.click()
    js_page.wait_for_timeout(100)
    vital_inputs = js_page.query_selector_all("#vital-list input")
    vital_inputs[0].fill("Heart Rate")
    vital_inputs[1].fill("92")
    vital_inputs[2].fill("bpm")

    js_page.fill("#nursing-note-text", "Subjective: Patient resting comfortably.\nObjective: HR 92.\nAssessment: Stable.\nPlan: Continue observation.")

    js_page.click("#nursing-save-btn")
    js_page.wait_for_timeout(1000)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models import NursingNote, Vital

    db = SessionLocal()
    try:
        note = db.query(NursingNote).filter(NursingNote.patient_id == patient.id).order_by(NursingNote.created_at.desc()).first()
        assert note is not None, "no NursingNote row was persisted"
        assert "resting comfortably" in note.notes

        vital = db.query(Vital).filter(Vital.patient_id == patient.id).order_by(Vital.recorded_at.desc()).first()
        assert vital is not None, "no Vital row was persisted"
        assert vital.heart_rate == 92
    finally:
        db.close()
