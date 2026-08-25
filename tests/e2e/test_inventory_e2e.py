"""
Inventory & Stores real-browser coverage: vendor creation, the purchase-request approval
split (Admin-only), and the purchase-order -> goods-receipt chain feeding real stock.
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture
def admin(make_user):
    return make_user(email="admin@e2e-inv.com", role="Admin")


@pytest.fixture
def pharmacist(make_user, admin):
    return make_user(email="pharm@e2e-inv.com", role="Pharmacist", organization_id=admin.organization_id)


@pytest.fixture
def drug(db_session, admin):
    from app.models import Drug

    d = Drug(name="Metformin E2E", form="Tablet", strength="500mg", unit_price=5.0, organization_id=admin.organization_id, created_by=admin.id)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


def test_add_vendor(js_page, live_server_url, pharmacist):
    login_as(js_page, live_server_url, pharmacist, landing_path="/inventory.html")
    js_page.wait_for_selector("#vendor-form")
    js_page.fill("#ven-name", "MedSupply Co E2E")
    js_page.fill("#ven-contact", "Ravi Kumar")
    js_page.fill("#ven-phone", "9998887777")
    js_page.click("#vendor-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Vendor added')")
    js_page.wait_for_selector("#vendor-rows :text('MedSupply Co E2E')")
    assert js_page.js_errors == []


def test_purchase_request_approval_and_order_receipt(js_page, live_server_url, pharmacist, admin, drug, db_session):
    from app.models import Vendor

    vendor = Vendor(name="Full Chain Vendor E2E", organization_id=admin.organization_id, created_by=admin.id, is_active=True)
    db_session.add(vendor)
    db_session.commit()

    login_as(js_page, live_server_url, pharmacist, landing_path="/inventory.html")
    js_page.click(".tab[data-tab='requests']")
    js_page.wait_for_selector("#pr-drug option", state="attached")
    js_page.locator("#pr-drug").select_option(index=0)
    js_page.fill("#pr-qty", "50")
    js_page.click("#pr-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('request created')")
    js_page.wait_for_selector("#pr-rows :text('Pending')")

    # Pharmacist cannot approve their own request
    assert js_page.locator("#pr-rows button[data-approve-pr]").count() == 0

    login_as(js_page, live_server_url, admin, landing_path="/inventory.html")
    js_page.click(".tab[data-tab='requests']")
    js_page.wait_for_selector("#pr-rows button[data-approve-pr]")
    js_page.click("#pr-rows button[data-approve-pr]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Approved')")
    js_page.wait_for_selector("#pr-rows :text('Approved')")

    js_page.click(".tab[data-tab='orders']")
    js_page.wait_for_selector("#po-vendor option", state="attached")
    js_page.select_option("#po-vendor", label="Full Chain Vendor E2E")
    js_page.click("#po-add-line")
    js_page.locator(".po-line-drug").select_option(index=0)
    js_page.fill(".po-line-qty", "50")
    js_page.fill(".po-line-price", "5.50")
    js_page.click("#po-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('order created')")
    js_page.wait_for_selector("#po-rows tr[data-id]")

    js_page.click("#po-rows tr[data-id]")
    js_page.wait_for_selector("#po-modal.modal--open")
    js_page.fill(".recv-qty", "50")
    js_page.fill(".recv-batch", "BATCH-E2E-1")
    future_date = js_page.evaluate("() => { const d = new Date(); d.setFullYear(d.getFullYear() + 1); return d.toISOString().slice(0, 10); }")
    js_page.fill(".recv-expiry", future_date)
    js_page.click("#receive-po-btn")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Goods received')")
    js_page.wait_for_selector("#po-rows :text('Received')")

    assert js_page.js_errors == []
