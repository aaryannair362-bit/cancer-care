"""
Real-browser regression test for the Front Desk CCA patient-registration wizard
(frontend/frontdesk.html). Drives the actual 5-step wizard UI (jumping to the Review step,
since the mandatory fields it validates all live in Step 1 and remain readable by the submit
handler once populated, regardless of which step is currently visible) rather than calling
POST /api/cca/patients directly, to catch the class of bug this suite exists for (wrong field
id, wrong endpoint, a JS error breaking the submit handler).
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def frontdesk_user(make_user):
    return make_user(email="frontdesk@e2e-cca.com", role="CCAFrontDesk")


def test_new_patient_registration_wizard_creates_a_real_cca_patient(js_page, live_server_url, frontdesk_user):
    login_as(js_page, live_server_url, frontdesk_user, landing_path="/frontdesk.html")
    js_page.wait_for_timeout(500)

    js_page.click("#nav-registration")
    js_page.wait_for_timeout(300)
    js_page.click("button:has-text('New Patient')")
    js_page.wait_for_timeout(500)

    js_page.fill("#wiz-fullname", "E2E Frontdesk Wizard Patient")
    js_page.fill("#wiz-dob", "1968-11-20")
    js_page.select_option("#wiz-sex", "Female")
    js_page.fill("#wiz-mobile", "+91 90000 22222")
    js_page.fill("#wiz-address", "45 Residency Road")

    js_page.evaluate("goToWizardStep(5)")
    js_page.wait_for_timeout(200)

    js_page.click('button[onclick="submitFullRegistration()"]')
    js_page.wait_for_timeout(1500)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    # The real point: does a real CCAPatient row now exist with what was typed.
    from app.main import SessionLocal
    from app.models_cca import CCAPatient

    db = SessionLocal()
    try:
        patient = db.query(CCAPatient).filter(CCAPatient.name == "E2E Frontdesk Wizard Patient").first()
        assert patient is not None, "no CCAPatient row was created by the registration wizard"
        assert patient.sex == "Female"
        assert patient.phone == "+91 90000 22222"
        assert patient.mrn and patient.mrn != "PENDING"
    finally:
        db.close()
