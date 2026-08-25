"""
Front Desk real-browser coverage: patient registration, appointment scheduling, and walk-in
token issuance/call/complete through the actual frontdesk.html form -- the front-of-house
flow nothing in this session had any UI for before this pass.
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture
def nursing_station(make_user):
    return make_user(email="frontdesk@e2e-fd.com", role="NursingStation")


@pytest.fixture
def doctor(make_user, nursing_station):
    return make_user(email="doctor@e2e-fd.com", role="Doctor", organization_id=nursing_station.organization_id)


def test_register_search_schedule_and_issue_token(js_page, live_server_url, nursing_station, doctor):
    login_as(js_page, live_server_url, nursing_station)
    js_page.wait_for_selector("#register-form")

    js_page.fill("#reg-name", "Priya Sharma")
    js_page.fill("#reg-age", "34")
    js_page.select_option("#reg-gender", "Female")
    js_page.fill("#reg-phone", "9876543210")
    js_page.click("#register-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Registered')")

    js_page.wait_for_function("document.querySelectorAll('#search-results tr').length > 0")
    row_text = js_page.locator("#search-results tr").first.inner_text()
    assert "Priya Sharma" in row_text
    assert "MRN-" in row_text

    # Grab the patient id from the roster search result via API for the appointment step,
    # since the row only displays the MRN, not the numeric id.
    patient_id = js_page.evaluate(
        """async () => {
            const res = await fetch('/api/patients/search?q=Priya', {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const rows = await res.json();
            return rows[0].id;
        }"""
    )

    js_page.click(".tab[data-tab='appointments']")
    js_page.wait_for_selector("#appt-doctor option", state="attached")
    js_page.fill("#appt-patient-id", str(patient_id))
    js_page.select_option("#appt-doctor", label=doctor.email)
    next_hour = js_page.evaluate("() => { const d = new Date(Date.now() + 3600000); return d.toISOString().slice(0, 16); }")
    js_page.fill("#appt-time", next_hour)
    js_page.click("#schedule-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('scheduled')")
    js_page.wait_for_function("document.querySelectorAll('#appt-rows tr[data-id], #appt-rows tr').length > 0")
    assert "Scheduled" in js_page.locator("#appt-rows").inner_text()

    js_page.click(".tab[data-tab='queue']")
    js_page.fill("#token-patient-id", str(patient_id))
    js_page.click("#token-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Token')")
    js_page.wait_for_function("document.querySelectorAll('#queue-rows tr').length > 0")
    assert "Waiting" in js_page.locator("#queue-rows").inner_text()

    js_page.click("#queue-rows button[data-call]")
    js_page.wait_for_selector("#queue-rows:has-text('InProgress')")
    js_page.click("#queue-rows button[data-complete]")
    js_page.wait_for_selector("#queue-rows:has-text('Completed')")

    assert js_page.js_errors == []


def test_doctor_has_read_only_frontdesk_view(js_page, live_server_url, doctor):
    login_as(js_page, live_server_url, doctor, landing_path="/frontdesk.html")
    js_page.wait_for_selector("#topnav")
    assert js_page.locator("#register-form").count() == 0
    assert js_page.locator("#schedule-form").count() == 0
    assert js_page.locator("#token-form").count() == 0
    assert js_page.js_errors == []
