"""
Real-browser regression test for the IPD "Nursing Notes" modal after the dead voice/Process
UI (POST /api/ipd/nurse-consult, removed per 2026-08-25 CHANGELOG decision) was stripped out.
Before this fix, #nursing-extracted (vitals list, labs list, note textarea) was display:none
by default and only ever revealed by a successful Process call -- since Process could only
ever 404, a nurse opening this modal had NO way to add a nursing note or vitals at all. This
pins down the plain-form fix: the editor must be visible immediately, and manual Save must work
end to end with no voice/Process step involved.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def ipd_patient(make_user, db_session):
    from app.models import NurseAssignment, Patient

    head_nurse = make_user(email="head@e2e-ipd-manual.com", role="HeadNurse")
    nurse = make_user(email="nurse@e2e-ipd-manual.com", role="Nurse", organization_id=head_nurse.organization_id)
    patient = Patient(name="E2E Manual Nursing Patient", age=61, gender="M", ward="General",
                       organization_id=nurse.organization_id, created_by=head_nurse.id)
    db_session.add(patient)
    db_session.flush()
    db_session.add(NurseAssignment(patient_id=patient.id, nurse_id=nurse.id, assigned_by=head_nurse.id))
    db_session.commit()
    db_session.refresh(patient)
    return nurse, patient


def test_nursing_notes_modal_editor_visible_and_save_works_without_process(
    js_page, live_server_url, ipd_patient
):
    from app.main import SessionLocal
    from app.models import NursingNote, Vital

    nurse, patient = ipd_patient
    js_page.on("dialog", lambda d: d.accept())  # the Save handler alert()s on success

    login_as(js_page, live_server_url, nurse, landing_path="/ipd.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openNursingConsult({patient.id})")
    js_page.wait_for_timeout(300)

    # The editor must be visible immediately -- no mic/Process step exists or is needed.
    assert js_page.eval_on_selector("#nursing-extracted", "el => getComputedStyle(el).display") != "none", (
        "vitals/labs/note editor is still hidden by default -- a nurse has no way to add a note"
    )
    assert js_page.query_selector("#nursing-voice-btn") is None, "dead mic button still present"
    assert js_page.query_selector("#nursing-process-btn") is None, "dead Process button still present"

    add_vital_btn = js_page.query_selector("#nursing-extracted button:has-text('Add')")
    add_vital_btn.click()
    add_vital_btn.click()  # two rows: BP and Heart Rate
    js_page.wait_for_timeout(100)
    vital_inputs = js_page.query_selector_all("#vital-list input")
    vital_inputs[0].fill("BP")
    vital_inputs[1].fill("128/82")
    vital_inputs[2].fill("mmHg")
    vital_inputs[3].fill("Heart Rate")
    vital_inputs[4].fill("88")
    vital_inputs[5].fill("bpm")

    js_page.fill("#nursing-note-text", "Subjective: Patient comfortable.\nObjective: HR 88.\nAssessment: Stable.\nPlan: Continue monitoring.")

    js_page.click("#nursing-save-btn")
    js_page.wait_for_timeout(1000)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    db = SessionLocal()
    try:
        note = db.query(NursingNote).filter(NursingNote.patient_id == patient.id).order_by(NursingNote.created_at.desc()).first()
        assert note is not None, "no NursingNote row was persisted"
        assert "Patient comfortable" in note.notes

        vital = db.query(Vital).filter(Vital.patient_id == patient.id).order_by(Vital.recorded_at.desc()).first()
        assert vital is not None, "no Vital row was persisted"
        assert vital.heart_rate == 88
        # Structured-columns regression coverage (see CHANGELOG.md): these must be the real
        # numeric columns, not null with everything crammed into free-text notes.
        assert vital.bp_systolic == 128
        assert vital.bp_diastolic == 82
    finally:
        db.close()
