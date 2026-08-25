"""
Admin real-browser coverage: user creation, role change, ward configuration, and the
custom medicine/lab-test reference-data lists.
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture(autouse=True)
def _isolated_custom_data_files(tmp_path, monkeypatch):
    """
    backend/app/data/custom_medicines.csv and custom_lab_tests.csv are real, shared, plain-file
    "databases" with no per-test reset the way the SQLite test DB gets -- writing to them for
    real (as the admin.html form does) would permanently pollute the actual bundled reference
    dataset every other test/production run reads from. Isolated the same way
    tests/integration/test_custom_data_admin_endpoints.py is.
    """
    import app.drug_matcher as drug_matcher
    import app.lab_test_matcher as lab_test_matcher

    monkeypatch.setattr(drug_matcher, "CUSTOM_DATA_PATH", tmp_path / "custom_medicines.csv")
    monkeypatch.setattr(lab_test_matcher, "CUSTOM_DATA_PATH", tmp_path / "custom_lab_tests.csv")
    drug_matcher.invalidate_cache()
    lab_test_matcher.invalidate_cache()
    yield
    drug_matcher.invalidate_cache()
    lab_test_matcher.invalidate_cache()


@pytest.fixture
def admin(make_user):
    return make_user(email="admin@e2e-admin.com", role="Admin")


def test_create_user_and_change_role(js_page, live_server_url, admin):
    login_as(js_page, live_server_url, admin)
    js_page.wait_for_selector("#user-form")

    js_page.fill("#u-email", "newpharmacist@e2e-admin.com")
    js_page.fill("#u-password", "Str0ng!Passw0rd#1")
    js_page.select_option("#u-role", "Pharmacist")
    js_page.click("#user-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('User created')")

    js_page.wait_for_selector("#user-rows :text('newpharmacist@e2e-admin.com')")
    row = js_page.locator("tr", has=js_page.locator("td", has_text="newpharmacist@e2e-admin.com"))
    row.locator(".role-select").select_option("Billing")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Role updated')")

    assert js_page.js_errors == []


def test_ward_and_reference_data_management(js_page, live_server_url, admin):
    login_as(js_page, live_server_url, admin)

    js_page.click(".tab[data-tab='wards']")
    js_page.fill("#w-name", "Admin Ward E2E")
    js_page.fill("#w-capacity", "5")
    js_page.click("#ward-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Ward added')")
    js_page.wait_for_selector("#ward-rows :text('Admin Ward E2E')")

    js_page.click(".tab[data-tab='medicines']")
    js_page.fill("#m-name", "Custom Drug E2E")
    js_page.click("#medicine-form button[type=submit]")
    js_page.wait_for_selector("#medicine-rows :text('Custom Drug E2E')")

    js_page.click(".tab[data-tab='labtests']")
    js_page.fill("#lt-name", "Custom Test E2E")
    js_page.fill("#lt-dept", "Pathology")
    js_page.click("#labtest-form button[type=submit]")
    js_page.wait_for_selector("#labtest-rows :text('Custom Test E2E')")

    assert js_page.js_errors == []
