"""
Real login flow (through the actual form, unlike every other e2e test in this suite which
seeds localStorage directly for speed) plus role-based navigation and logout -- the one
surface every other page's test implicitly depends on being correct.
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture
def doctor(make_user):
    return make_user(email="doctor@e2e-login.com", role="Doctor")


def test_login_redirects_to_role_home_and_nav_renders(js_page, live_server_url, doctor):
    js_page.goto(f"{live_server_url}/index.html")
    js_page.fill("#login-email", doctor.email)
    js_page.fill("#login-password", "Str0ng!Passw0rd#1")
    js_page.click("#login-form button[type=submit]")
    js_page.wait_for_url(f"{live_server_url}/opd.html")

    assert js_page.locator(".nav-link--active").inner_text() == "OPD"
    assert js_page.locator(".nav-link", has_text="Ward").count() == 1
    assert js_page.locator(".nav-link", has_text="Admin").count() == 0
    assert js_page.js_errors == []


def test_invalid_login_shows_error_without_navigating(js_page, live_server_url, doctor):
    js_page.goto(f"{live_server_url}/index.html")
    js_page.fill("#login-email", doctor.email)
    js_page.fill("#login-password", "wrong-password-entirely")
    js_page.click("#login-form button[type=submit]")
    js_page.wait_for_selector("#login-error:has-text('Invalid credentials')")
    assert js_page.url.endswith("/index.html")


def test_page_redirects_to_login_when_not_authenticated(js_page, live_server_url):
    js_page.goto(f"{live_server_url}/opd.html")
    js_page.wait_for_url(f"{live_server_url}/index.html")


def test_wrong_role_redirected_to_own_home(js_page, live_server_url, doctor):
    login_as(js_page, live_server_url, doctor, landing_path="/pharmacy.html")
    js_page.wait_for_url(f"{live_server_url}/opd.html")


def test_logout_clears_session(js_page, live_server_url, doctor):
    login_as(js_page, live_server_url, doctor)
    js_page.wait_for_selector("#nav-logout-btn")
    js_page.click("#nav-logout-btn")
    js_page.wait_for_url(f"{live_server_url}/index.html")
    token = js_page.evaluate("() => localStorage.getItem('access_token')")
    assert token is None
