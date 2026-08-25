"""
TPA / Insurance Pre-Authorization real-browser coverage: the full cross-role journey through
the actual UI -- Front Desk registers a patient, Doctor records a consultation, TPA searches
for the patient with "AI Search", opens the full record, submits it for pre-authorization and
marks it Approved, then Billing opens an Insurance invoice and links the now-Approved
pre-authorization to a claim. Mirrors test_combined_cross_module_flow.py's role-switching style.
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture
def org_users(make_user):
    nursing_station = make_user(email="frontdesk@e2e-tpa.com", role="NursingStation")
    org_id = nursing_station.organization_id
    return {
        "nursing_station": nursing_station,
        "doctor": make_user(email="doctor@e2e-tpa.com", role="Doctor", organization_id=org_id),
        "tpa": make_user(email="tpa@e2e-tpa.com", role="TPA", organization_id=org_id),
        "billing": make_user(email="billing@e2e-tpa.com", role="Billing", organization_id=org_id),
    }


def test_tpa_search_view_and_submit_pre_authorization(js_page, live_server_url, org_users):
    # -- Front desk: register the patient --
    login_as(js_page, live_server_url, org_users["nursing_station"])
    js_page.wait_for_selector("#register-form")
    js_page.fill("#reg-name", "TPA Flow Patient")
    js_page.fill("#reg-age", "47")
    js_page.select_option("#reg-gender", "Female")
    js_page.fill("#reg-phone", "9812345670")
    js_page.click("#register-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Registered')")

    patient_id = js_page.evaluate(
        """async () => {
            const res = await fetch('/api/patients/search?q=TPA%20Flow%20Patient', {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const rows = await res.json();
            return rows[0].id;
        }"""
    )
    assert patient_id

    # -- Doctor: record a consultation with a diagnosis and medication --
    login_as(js_page, live_server_url, org_users["doctor"])
    js_page.wait_for_selector("#consult-form")
    js_page.fill("#c-patient-id", str(patient_id))
    js_page.fill("#c-patient-name", "TPA Flow Patient")
    js_page.fill("#c-chief-complaint", "Knee pain after fall")
    js_page.fill("#c-primary-dx", "Meniscus tear")
    js_page.fill(".med-row .med-name", "Azithromycin")
    js_page.fill(".med-row .med-dose", "400mg")
    js_page.click("#consult-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Saved')")

    # -- TPA: AI Search finds the patient, opens the full record --
    login_as(js_page, live_server_url, org_users["tpa"])
    js_page.wait_for_selector("#search-q")
    js_page.fill("#search-q", "TPA Flow Patient")
    js_page.click("#search-btn")
    js_page.wait_for_selector("#search-results button[data-view]")
    js_page.click("#search-results button[data-view]")
    js_page.wait_for_selector("#view-detail")
    js_page.wait_for_selector("#pv-consults :text('Meniscus tear')")
    js_page.wait_for_selector("#pv-consults :text('Azithromycin')")

    # -- TPA: process for pre-authorization, then approve it --
    js_page.click("#process-preauth-btn")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Submitted for pre-authorization')")
    js_page.click("#back-btn")
    js_page.wait_for_selector("#submission-rows :text('TPA Flow Patient')")
    js_page.wait_for_selector("#submission-rows button[data-approve]")
    js_page.click("#submission-rows button[data-approve]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('approved')")
    js_page.wait_for_selector("#submission-rows .badge:has-text('Approved')")

    assert js_page.js_errors == []


def test_billing_sees_and_links_approved_pre_authorization(js_page, live_server_url, org_users):
    login_as(js_page, live_server_url, org_users["nursing_station"])
    js_page.wait_for_selector("#register-form")
    js_page.fill("#reg-name", "TPA Billing Link Patient")
    js_page.fill("#reg-age", "52")
    js_page.click("#register-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Registered')")

    # -- TPA: search, submit, and approve a pre-authorization --
    login_as(js_page, live_server_url, org_users["tpa"])
    js_page.wait_for_selector("#search-q")
    js_page.fill("#search-q", "TPA Billing Link Patient")
    js_page.click("#search-btn")
    js_page.wait_for_selector("#search-results button[data-view]")
    js_page.click("#search-results button[data-view]")
    js_page.wait_for_selector("#view-detail")
    js_page.click("#process-preauth-btn")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Submitted for pre-authorization')")
    js_page.click("#back-btn")
    js_page.wait_for_selector("#submission-rows button[data-approve]")
    js_page.click("#submission-rows button[data-approve]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('approved')")

    patient_id = js_page.evaluate(
        """async () => {
            const res = await fetch('/api/patients/search?q=TPA%20Billing%20Link', {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const rows = await res.json();
            return rows[0].id;
        }"""
    )
    assert patient_id

    # -- Billing: open an Insurance invoice for the same patient and see the approved pre-auth --
    login_as(js_page, live_server_url, org_users["billing"])
    js_page.wait_for_selector("#invoice-form")
    js_page.fill("#inv-patient", str(patient_id))
    js_page.select_option("#inv-payer", "Insurance")
    js_page.click("#invoice-form button[type=submit]")
    js_page.wait_for_selector("#invoice-modal.modal--open")
    js_page.wait_for_selector("#invoice-modal-body :text('Pre-Authorization')")
    js_page.wait_for_selector("#invoice-modal-body .badge:has-text('Approved')")

    # -- Billing: submit a claim, linking the approved pre-authorization --
    js_page.wait_for_selector("#claim-form")
    js_page.wait_for_selector("#claim-preauth")
    js_page.fill("#claim-name", "Star Health TPA E2E")
    js_page.fill("#claim-amount", "18000")
    js_page.select_option("#claim-preauth", index=1)
    js_page.click("#claim-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Claim submitted')")
    js_page.wait_for_selector("#claim-body :text('Pre-authorization: #')")
    js_page.wait_for_selector("#claim-body .badge:has-text('Approved')")

    assert js_page.js_errors == []
