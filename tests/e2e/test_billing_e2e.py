"""
Billing real-browser coverage: invoice creation, manual/tariff line capture, finalize, and
payment collection through the actual invoice-detail modal -- the most stateful UI in this
codebase (multiple nested forms gated by invoice status).
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture
def billing_staff(make_user):
    return make_user(email="billing@e2e-billing.com", role="Billing")


@pytest.fixture
def patient(db_session, billing_staff):
    from app.models import Patient

    p = Patient(name="Billing Test Patient", age=38, gender="F", organization_id=billing_staff.organization_id, created_by=billing_staff.id, status="Registered")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def test_create_invoice_add_lines_finalize_and_pay(js_page, live_server_url, billing_staff, patient):
    login_as(js_page, live_server_url, billing_staff)
    js_page.wait_for_selector("#invoice-form")

    js_page.fill("#inv-patient", str(patient.id))
    js_page.click("#invoice-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('created')")
    js_page.wait_for_selector("#invoice-modal.modal--open")

    js_page.fill("#lm-desc", "Consultation fee")
    js_page.fill("#lm-price", "500")
    js_page.click("#line-manual-form button[type=submit]")
    js_page.wait_for_selector("#invoice-modal-body :text('Consultation fee')")

    js_page.click("#finalize-invoice-btn")
    js_page.wait_for_selector(".hms-toast--visible:has-text('finalized')")
    js_page.wait_for_selector("#payment-form")

    js_page.fill("#pay-amount", "500")
    js_page.select_option("#pay-method", "Cash")
    js_page.click("#payment-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('collected')")
    js_page.wait_for_selector("#invoice-modal-body :text('0.00')")

    assert js_page.js_errors == []


def test_tariff_and_package_catalog(js_page, live_server_url, billing_staff):
    login_as(js_page, live_server_url, billing_staff)
    js_page.click(".tab[data-tab='catalog']")

    js_page.fill("#tar-code", "CONS-E2E")
    js_page.fill("#tar-name", "General Consultation E2E")
    js_page.fill("#tar-price", "300")
    js_page.click("#tariff-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Tariff added')")
    js_page.wait_for_selector("#tariff-rows :text('CONS-E2E')")

    js_page.fill("#pkg-name", "Maternity Package E2E")
    js_page.fill("#pkg-price", "25000")
    js_page.click("#package-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Package added')")
    js_page.wait_for_selector("#package-rows :text('Maternity Package E2E')")

    assert js_page.js_errors == []


def test_admin_only_discount_hidden_for_billing_staff(js_page, live_server_url, billing_staff, patient):
    login_as(js_page, live_server_url, billing_staff)
    js_page.wait_for_selector("#invoice-form")
    js_page.fill("#inv-patient", str(patient.id))
    js_page.click("#invoice-form button[type=submit]")
    js_page.wait_for_selector("#invoice-modal.modal--open")
    assert js_page.locator("#discount-form").count() == 0
    assert js_page.js_errors == []


def test_insurance_claim_flow(js_page, live_server_url, billing_staff, patient):
    login_as(js_page, live_server_url, billing_staff)
    js_page.wait_for_selector("#invoice-form")
    js_page.fill("#inv-patient", str(patient.id))
    js_page.select_option("#inv-payer", "Insurance")
    js_page.click("#invoice-form button[type=submit]")
    js_page.wait_for_selector("#invoice-modal.modal--open")
    js_page.wait_for_selector("#claim-form")
    js_page.fill("#claim-name", "Star Health E2E")
    js_page.fill("#claim-amount", "10000")
    js_page.click("#claim-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Claim submitted')")
    js_page.wait_for_selector("#claim-body :text('Star Health E2E')")
    assert js_page.js_errors == []
