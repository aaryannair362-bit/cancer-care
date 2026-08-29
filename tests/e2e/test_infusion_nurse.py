"""
Real-browser regression test for the Infusion Nurse "Treatment Day / Infusion" screen
(frontend/infusion_nurse.html): toxicity recording (allowed for this role), and the
"Decide Treatment Clearance" flow -- the exact screen/action from the original bug report
this session started with. Confirms live, through the real UI, that clearance correctly
shows the backend's clean 403 ("Only a treating oncologist may perform this action") as an
error toast rather than crashing -- the role gate is working as designed, not a 500 bug.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def infusion_patient(make_user, db_session):
    from app.models_cca import CCAPatient

    nurse = make_user(email="infusion@e2e-cca.com", role="CCAInfusionNurse")
    patient = CCAPatient(
        mrn="E2E-INFUSION-0001", name="E2E Infusion Nurse Test Patient", age=52, sex="Female",
        organization_id=nurse.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return nurse, patient


def test_infusion_nurse_records_toxicity(js_page, live_server_url, infusion_patient):
    nurse, patient = infusion_patient
    login_as(js_page, live_server_url, nurse, landing_path="/infusion_nurse.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(400)

    js_page.fill("#tox-term", "Fatigue")
    js_page.select_option("#tox-grade", "1")
    js_page.fill("#tox-baseline", "Grade 1 fatigue, mild and intermittent.")
    js_page.click('button[onclick="submitToxicity()"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import ToxicityEvent

    db = SessionLocal()
    try:
        event = db.query(ToxicityEvent).filter(ToxicityEvent.patient_id == patient.id).first()
        assert event is not None
        assert event.term == "Fatigue"
        assert event.grade == 1
    finally:
        db.close()


def test_infusion_nurse_clearance_shows_clean_permission_error_not_a_crash(js_page, live_server_url, infusion_patient):
    """The original session bug report: clicking Confirm Decision here appeared to fail with
    'Request failed (500)'. Confirmed earlier via curl that current code returns a clean 403;
    this pins that down through the actual UI the report was filed against."""
    nurse, patient = infusion_patient
    login_as(js_page, live_server_url, nurse, landing_path="/infusion_nurse.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(400)

    js_page.fill("#clearance-reason", "Pre-treatment labs reviewed, no Grade 2+ toxicity, cleared for standard dose.")
    js_page.click('button[onclick="submitClearance()"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    status_text = js_page.eval_on_selector("#clearance-status", "el => el.textContent")
    assert "recorded" not in status_text.lower(), "clearance should have been rejected, not recorded"

    toast_text = js_page.eval_on_selector("#hms-toast", "el => el.textContent")
    assert toast_text == "Only a treating oncologist may perform this action", (
        f"expected the clean backend permission message, got: {toast_text!r}"
    )
    toast_class = js_page.eval_on_selector("#hms-toast", "el => el.className")
    assert "hms-toast--error" in toast_class
