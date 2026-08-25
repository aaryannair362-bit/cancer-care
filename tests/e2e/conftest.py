"""
Fixtures for real-browser (Playwright) end-to-end tests. These drive the actual
frontend/*.html + JS against a real listening HTTP server (Playwright cannot talk to an
in-process ASGI TestClient), which is the only way to catch pure-frontend bugs -- broken
element wiring, a JS reference error, a form that posts the wrong field name -- that no
backend-only pytest test can ever exercise.

Reuses the SAME already-imported `app.main` module (and its throwaway SQLite engine) that the
top-level tests/conftest.py sets up -- Python caches that import, so there is no way to get a
second independently-configured copy within one pytest process, and there's no need to: the
top-level `_clean_database` autouse fixture already resets tables before every test, e2e
included.
"""
import pytest

from tests._voice_helpers import (  # noqa: F401 - compatibility re-exports
    MOCK_MEDIA_RECORDER_INIT_SCRIPT,
    mint_tokens,
    mock_transcription_network_failure,
    queue_transcription_result,
    set_tokens_in_browser,
    start_live_server,
)

REQUIRES_BROWSER = pytest.mark.e2e


@pytest.fixture(scope="session")
def live_server_url():
    from app import main as app_main

    base_url, stop = start_live_server(app_main.app)
    yield base_url
    stop()


@pytest.fixture
def browser_context_args(browser_context_args):
    return {**browser_context_args, "permissions": ["microphone"]}


@pytest.fixture
def js_page(page):
    """A Playwright `page` with JS errors captured so tests can assert the app never throws
    -- exactly the class of bug this suite exists to catch."""
    page.add_init_script(MOCK_MEDIA_RECORDER_INIT_SCRIPT)
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.js_errors = errors
    return page


def mint_expired_access_token(user):
    """Mint an already-expired access token to exercise the refresh path."""
    from datetime import datetime, timedelta

    from jose import jwt as jose_jwt

    from app.config import settings

    token_data = {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "organization_id": user.organization_id,
    }
    return jose_jwt.encode(
        {**token_data, "exp": datetime.utcnow() - timedelta(minutes=1), "type": "access"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def login_as(page, base_url, user, landing_path=None):
    """
    Signs `user` in without driving the actual login form (deterministic, no dependency on
    password-complexity fixtures) by minting real tokens and seeding the exact localStorage
    shape frontend/js/api.js's Auth helper expects: access_token/refresh_token plus hms_user
    (id/email/role/organization_id), matching what a real POST /api/auth/login response's
    `user` object contains. Then navigates to `landing_path` (defaults to that role's home
    page, mirroring index.html's own post-login redirect).
    """
    tokens = mint_tokens(user)
    page.goto(f"{base_url}/index.html")
    page.evaluate(
        """([access, refresh, hmsUser]) => {
            localStorage.setItem('access_token', access);
            localStorage.setItem('refresh_token', refresh);
            localStorage.setItem('hms_user', JSON.stringify(hmsUser));
        }""",
        [
            tokens["access_token"],
            tokens["refresh_token"],
            {"id": user.id, "email": user.email, "role": user.role, "organization_id": user.organization_id},
        ],
    )
    path = landing_path or _home_for(user.role)
    page.goto(f"{base_url}{path}")
    return page


def _home_for(role: str) -> str:
    return {
        "Admin": "/admin.html",
        "Doctor": "/opd.html",
        "HeadNurse": "/headnurse.html",
        "Nurse": "/ipd.html",
        "NursingStation": "/frontdesk.html",
        "Pharmacist": "/pharmacy.html",
        "Billing": "/billing.html",
        "TPA": "/tpa.html",
    }.get(role, "/index.html")
