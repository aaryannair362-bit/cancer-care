"""
OPD real-browser coverage: creating a structured consultation through the actual form
(medication row builder, lab test textarea), seeing it in history/search, opening the detail
modal, and follow-up-consultation linking.
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture
def doctor(make_user):
    return make_user(email="doctor@e2e-opd.com", role="Doctor")


def test_create_consultation_and_view_in_history(js_page, live_server_url, doctor):
    login_as(js_page, live_server_url, doctor)
    js_page.wait_for_selector("#consult-form")

    js_page.fill("#c-patient-name", "Arjun Mehta")
    js_page.fill("#c-patient-age", "45")
    js_page.select_option("#c-patient-gender", "Male")
    js_page.fill("#c-chief-complaint", "Fever and cough for 3 days")
    js_page.fill("#c-objective", "Bilateral crepitations, temp 38.6C")
    js_page.fill("#c-primary-dx", "Acute bronchitis")

    js_page.fill(".med-row .med-name", "Azithromycin")
    js_page.fill(".med-row .med-dose", "500mg")
    js_page.fill(".med-row .med-frequency", "OD")

    js_page.fill("#c-lab-tests", "CBC\nChest X-ray")
    js_page.click("#consult-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Saved')")

    js_page.click(".tab[data-tab='history']")
    js_page.wait_for_function("document.querySelectorAll('#hist-rows tr[data-id]').length > 0")
    assert "Arjun Mehta" in js_page.locator("#hist-rows").inner_text()

    js_page.click("#hist-rows tr[data-id]")
    js_page.wait_for_selector("#detail-modal.modal--open")
    detail_text = js_page.locator("#detail-body").inner_text()
    assert "Arjun Mehta" in detail_text
    assert "Acute bronchitis" in detail_text
    assert "Azithromycin" in detail_text
    assert "Bilateral crepitations" in detail_text

    assert js_page.js_errors == []


def test_follow_up_link_shows_on_prior_consultation(js_page, live_server_url, doctor):
    login_as(js_page, live_server_url, doctor)
    js_page.wait_for_selector("#consult-form")

    js_page.fill("#c-patient-name", "Kavita Rao")
    js_page.fill("#c-chief-complaint", "Initial visit - joint pain")
    js_page.click("#consult-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Saved')")

    prior_id = js_page.evaluate(
        """async () => {
            const res = await fetch('/api/consultations?search=Kavita', {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const data = await res.json();
            return data.consultations[0].id;
        }"""
    )

    js_page.fill("#c-followup", str(prior_id))
    js_page.fill("#c-patient-name", "Kavita Rao")
    js_page.fill("#c-chief-complaint", "Follow-up - improved but still stiff")
    js_page.click("#consult-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Saved')")

    js_page.click(".tab[data-tab='history']")
    js_page.wait_for_function("document.querySelectorAll('#hist-rows tr[data-id]').length >= 2")
    rows = js_page.locator("#hist-rows tr[data-id]")
    # Most recent (the follow-up we just linked) is first; the prior visit is second.
    js_page.locator("#hist-rows tr[data-id]").nth(1).click()
    js_page.wait_for_selector("#detail-modal.modal--open")
    detail_text = js_page.locator("#detail-body").inner_text()
    assert "Follow-up consultations" in detail_text

    assert js_page.js_errors == []
