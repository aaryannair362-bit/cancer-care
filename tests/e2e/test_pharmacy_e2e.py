"""
Pharmacy real-browser coverage: adding a drug to the formulary, receiving a batch, dispensing
against it (FEFO), and the expiry-alerts view.
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture
def pharmacist(make_user):
    return make_user(email="pharm@e2e-pharmacy.com", role="Pharmacist")


@pytest.fixture
def doctor(make_user, pharmacist):
    return make_user(email="doctor@e2e-pharmacy.com", role="Doctor", organization_id=pharmacist.organization_id)


def test_add_drug_receive_batch_and_dispense(js_page, live_server_url, pharmacist):
    login_as(js_page, live_server_url, pharmacist)
    js_page.wait_for_selector("#drug-form")

    js_page.fill("#d-name", "Amoxicillin E2E")
    js_page.fill("#d-price", "12.50")
    js_page.fill("#d-form", "Capsule")
    js_page.fill("#d-strength", "250mg")
    js_page.click("#drug-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Add')")

    js_page.wait_for_selector("#drug-rows :text('Amoxicillin E2E')")
    js_page.click("#drug-rows tr[data-id]")
    js_page.wait_for_selector("#drug-modal.modal--open")

    js_page.fill("#b-qty", "100")
    future_date = js_page.evaluate("() => { const d = new Date(); d.setFullYear(d.getFullYear() + 1); return d.toISOString().slice(0, 10); }")
    js_page.fill("#b-expiry", future_date)
    js_page.click("#batch-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Batch received')")
    js_page.wait_for_selector("#drug-modal-body :text('100')")
    js_page.click("#drug-modal .modal__close")

    js_page.click(".tab[data-tab='dispense']")
    js_page.wait_for_selector("#disp-drug option", state="attached")
    js_page.locator("#disp-drug").select_option(index=0)
    js_page.fill("#disp-qty", "10")
    js_page.click("#dispense-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Dispensed')")
    js_page.wait_for_selector("#dispense-result")

    js_page.click(".tab[data-tab='records']")
    js_page.click("#rec-filter-btn")
    js_page.wait_for_function("document.querySelectorAll('#rec-rows tr').length > 0")

    assert js_page.js_errors == []


def test_pending_prescriptions_tab_and_quick_dispense(js_page, live_server_url, pharmacist, doctor):
    login_as(js_page, live_server_url, doctor)
    js_page.wait_for_selector("#consult-form")
    js_page.fill("#c-patient-name", "Pending Rx Patient")
    js_page.fill("#c-chief-complaint", "Skin infection")
    js_page.fill(".med-row .med-name", "PendingRxTestDrug")
    js_page.click("#consult-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Saved')")

    # drug_matcher may rewrite the typed name to a canonical reference-dataset form -- read back
    # whatever actually got persisted rather than assuming it survives unchanged.
    persisted_name = js_page.evaluate(
        """async () => {
            const res = await fetch('/api/consultations?search=Pending%20Rx', {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const data = await res.json();
            return data.consultations[0].id;
        }"""
    )

    login_as(js_page, live_server_url, pharmacist)
    drug_name = js_page.evaluate(
        """async (consultId) => {
            const res = await fetch(`/api/pharmacy/pending-prescriptions?days=1`, {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const rows = await res.json();
            return rows.find(r => r.consultation_id === consultId).medications[0].drug_name;
        }""",
        persisted_name,
    )
    js_page.fill("#d-name", drug_name)
    js_page.fill("#d-price", "5.00")
    js_page.click("#drug-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Add')")

    js_page.click(".tab[data-tab='pending']")
    js_page.click("#pending-refresh")
    js_page.wait_for_selector("#pending-list :text('Pending Rx Patient')")
    assert drug_name in js_page.locator("#pending-list").inner_text()

    js_page.click("[data-quick-dispense]")
    js_page.wait_for_selector(".tab-panel--active#panel-dispense")
    assert js_page.js_errors == []


def test_expiry_alerts_tab(js_page, live_server_url, pharmacist):
    login_as(js_page, live_server_url, pharmacist)
    js_page.click(".tab[data-tab='expiry']")
    js_page.click("#expiry-refresh")
    js_page.wait_for_selector("#expiry-rows tr")
    assert js_page.js_errors == []
