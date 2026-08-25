"""
Everything NursingStation must NOT be able to do. This is the most important coverage for this
role: NursingStation is deliberately narrow (front-desk/admin duties only), so most of its
"functionality" is actually about what it's correctly denied. Confirmed against
backend/app/main.py: NursingStation is excluded from record_vital, create_task, update_task,
create_nursing_note, assign_patient, unassign_patient, nurse_workload, get_users, and every
Admin-only endpoint.
"""
import pytest


@pytest.fixture
def station(make_user):
    return make_user(email="station@ns-boundaries.com", role="NursingStation")


@pytest.fixture
def head_nurse(make_user, station):
    return make_user(email="head@ns-boundaries.com", role="HeadNurse", organization_id=station.organization_id)


@pytest.fixture
def nurse(make_user, station):
    return make_user(email="nurse@ns-boundaries.com", role="Nurse", organization_id=station.organization_id)


@pytest.fixture
def patient_id(client, station, auth_headers):
    resp = client.post("/api/ipd/patients", json={"name": "Boundary Patient", "ward": "General", "bed": "B1"},
                        headers=auth_headers(station))
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Clinical recording -- NursingStation cannot record any clinical data
# ---------------------------------------------------------------------------

def test_station_cannot_record_vitals(client, station, patient_id, auth_headers):
    resp = client.post("/api/ipd/vitals", json={"patient_id": patient_id, "heart_rate": 80}, headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_cannot_create_nursing_note(client, station, patient_id, auth_headers):
    resp = client.post("/api/nursing-notes", json={"patient_id": patient_id, "subjective": "x", "objective": "",
                                                     "assessment": "", "plan": ""}, headers=auth_headers(station))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Task management -- NursingStation cannot create or update tasks (but CAN read them, tested
# separately in test_nursingstation_dashboard_and_read_access.py)
# ---------------------------------------------------------------------------

def test_station_cannot_create_task(client, station, patient_id, auth_headers):
    resp = client.post("/api/ipd/tasks", json={"patient_id": patient_id, "description": "Give medication"},
                        headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_cannot_update_a_task_even_one_it_can_see(client, station, head_nurse, patient_id, auth_headers):
    task_id = client.post("/api/ipd/tasks", json={"patient_id": patient_id, "description": "x"},
                           headers=auth_headers(head_nurse)).json()["id"]
    resp = client.patch(f"/api/ipd/tasks/{task_id}", json={"status": "Completed"}, headers=auth_headers(station))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Nurse-assignment management -- entirely HeadNurse-exclusive
# ---------------------------------------------------------------------------

def test_station_cannot_assign_a_nurse(client, station, nurse, patient_id, auth_headers):
    resp = client.post("/api/ipd/assign", json={"patient_id": patient_id, "nurse_id": nurse.id}, headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_cannot_unassign_a_nurse(client, station, head_nurse, nurse, patient_id, auth_headers):
    client.post("/api/ipd/assign", json={"patient_id": patient_id, "nurse_id": nurse.id}, headers=auth_headers(head_nurse))
    resp = client.post("/api/ipd/unassign", json={"patient_id": patient_id}, headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_cannot_view_nurse_workload(client, station, auth_headers):
    resp = client.get("/api/ipd/nurse-workload", headers=auth_headers(station))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Admin-only actions
# ---------------------------------------------------------------------------

def test_station_cannot_view_users_list(client, station, auth_headers):
    """Unlike HeadNurse, NursingStation has no carve-out for GET /api/auth/users."""
    resp = client.get("/api/auth/users", headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_cannot_change_a_users_role(client, station, nurse, auth_headers):
    resp = client.patch(f"/api/auth/users/{nurse.id}", json={"role": "HeadNurse"}, headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_cannot_reset_a_users_password(client, station, nurse, auth_headers):
    resp = client.patch(f"/api/auth/users/{nurse.id}/password", json={"new_password": "NewStr0ng!Passw0rd9"},
                         headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_cannot_create_users(client, station, auth_headers):
    resp = client.post("/api/auth/admin/create-user", json={"email": "sneaky@ns-boundaries.com",
                                                              "password": "NewStr0ng!Passw0rd9", "role": "Nurse"},
                        headers=auth_headers(station))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Session / token edge cases
# ---------------------------------------------------------------------------

def test_expired_station_access_token_rejected(client, station):
    from datetime import datetime, timedelta
    from jose import jwt as jose_jwt
    from app.config import settings

    token_data = {"user_id": station.id, "email": station.email, "role": "NursingStation",
                  "organization_id": station.organization_id}
    expired = jose_jwt.encode({**token_data, "exp": datetime.utcnow() - timedelta(minutes=1), "type": "access"},
                               settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    resp = client.get("/api/ipd/patients", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_forged_admin_role_claim_on_station_token_rejected(client, station):
    from jose import jwt as jose_jwt

    forged = jose_jwt.encode(
        {"user_id": station.id, "email": station.email, "role": "Admin",
         "organization_id": station.organization_id, "type": "access"},
        "wrong-secret-key", algorithm="HS256",
    )
    resp = client.get("/api/auth/users", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Malformed / adversarial input specifically as NursingStation, across the endpoints it can
# actually reach
# ---------------------------------------------------------------------------

def test_station_admit_missing_name_rejected(client, station, auth_headers):
    resp = client.post("/api/ipd/patients", json={"ward": "General"}, headers=auth_headers(station))
    assert resp.status_code == 400


def test_station_admit_missing_ward_rejected(client, station, auth_headers):
    resp = client.post("/api/ipd/patients", json={"name": "No Ward"}, headers=auth_headers(station))
    assert resp.status_code == 400


def test_station_update_nonexistent_patient_404(client, station, auth_headers):
    resp = client.put("/api/patients/999999", json={"diagnosis": "x"}, headers=auth_headers(station))
    assert resp.status_code == 404


def test_station_admit_malformed_json_body_rejected_cleanly(client, station, auth_headers):
    resp = client.post("/api/ipd/patients", content=b"{not valid json",
                        headers={**auth_headers(station), "Content-Type": "application/json"})
    assert resp.status_code == 400


@pytest.mark.parametrize("bad_id", [0, -1, "abc", None, [], {}])
def test_station_get_details_malformed_patient_id_never_500(client, station, auth_headers, bad_id):
    resp = client.get(f"/api/patients/{bad_id}/details", headers=auth_headers(station))
    assert resp.status_code in (400, 404, 422), f"patient_id={bad_id!r} produced {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Denials must hold even when the target patient/task genuinely exists and belongs to the
# station's own organization -- confirming these are pure role checks, not a side effect of
# some other (org/assignment) check firing first.
# ---------------------------------------------------------------------------

def test_station_cannot_record_vitals_even_for_a_patient_it_admitted_itself(client, station, patient_id, auth_headers):
    resp = client.post("/api/ipd/vitals", json={"patient_id": patient_id, "heart_rate": 80}, headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_cannot_create_task_even_for_a_patient_it_admitted_itself(client, station, patient_id, auth_headers):
    resp = client.post("/api/ipd/tasks", json={"patient_id": patient_id, "description": "x"}, headers=auth_headers(station))
    assert resp.status_code == 403
