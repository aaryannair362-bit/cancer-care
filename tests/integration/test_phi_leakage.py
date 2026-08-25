"""
PHI/secret leakage checks: error responses returned to the client must never echo plaintext
passwords back, even when something fails downstream -- what would show up in browser
devtools, API gateways, or a support ticket screenshot.
"""
import pytest


def test_login_failure_response_never_contains_submitted_password(client, make_user):
    secret_password = "Sup3rSecret!Passw0rd#Marker"
    make_user(email="phi-login@example.com", password=secret_password)
    resp = client.post("/api/auth/login", json={
        "email": "phi-login@example.com", "password": "WrongOne123!",
    })
    assert secret_password not in resp.text


def test_password_reset_response_never_echoes_new_password(client, make_user, auth_headers):
    admin = make_user(email="phi-admin@example.com", role="Admin")
    new_password = "Br4nd#NewMarkerPw9"
    resp = client.patch(
        f"/api/auth/users/{admin.id}/password",
        json={"new_password": new_password},
        headers=auth_headers(admin),
    )
    assert resp.status_code == 200
    assert new_password not in resp.text


def test_register_weak_password_error_does_not_echo_the_password_itself(client):
    weak_password = "weakpw123markerXYZ"
    resp = client.post("/api/auth/register", json={
        "email": "weakpw@example.com", "password": weak_password,
    })
    assert resp.status_code == 400
    assert weak_password not in resp.text
