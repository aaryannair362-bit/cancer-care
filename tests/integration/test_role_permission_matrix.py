"""
Full role x endpoint permission matrix for the daily ward workflow.

The ward has five roles (Admin, HeadNurse, NursingStation, Nurse, Doctor). Each IPD/auth
endpoint allows a different subset of them. This file pins down, endpoint by endpoint, exactly
which roles can call it -- so a future change that accidentally widens or narrows a permission
check (e.g. a typo'd role string, a missing `is_x(current_user)` check) shows up as a single
failing parametrized case instead of a silent authorization regression.

Five actors share one organization for every case: Admin, HeadNurse, NursingStation, a Nurse
who IS actively assigned to the patient under test, and a Doctor. Using an *assigned* nurse
(rather than an arbitrary one) means the matrix tests the role check itself, not the separate
assignment check (that's covered by test_ward_daily_scenarios.py's unassigned-nurse cases).
"""
import pytest


@pytest.fixture
def actors(make_user):
    admin = make_user(email="admin@matrix.com", role="Admin")
    org_id = admin.organization_id
    return {
        "Admin": admin,
        "HeadNurse": make_user(email="head@matrix.com", role="HeadNurse", organization_id=org_id),
        "NursingStation": make_user(email="station@matrix.com", role="NursingStation", organization_id=org_id),
        "Nurse": make_user(email="nurse@matrix.com", role="Nurse", organization_id=org_id),
        "Doctor": make_user(email="doctor@matrix.com", role="Doctor", organization_id=org_id),
    }


@pytest.fixture
def ward_id(client, actors, auth_headers):
    resp = client.post(
        "/api/wards", json={"name": "Matrix Ward", "bed_capacity": 10}, headers=auth_headers(actors["HeadNurse"]),
    )
    assert resp.status_code == 200
    return resp.json()["id"]


@pytest.fixture
def patient_id(client, actors, auth_headers):
    resp = client.post(
        "/api/ipd/patients",
        json={"name": "Matrix Patient", "ward": "General", "bed": "M1"},
        headers=auth_headers(actors["HeadNurse"]),
    )
    assert resp.status_code == 200
    pid = resp.json()["id"]
    # Assign the Nurse actor so the matrix exercises the role check, not the assignment check.
    resp2 = client.post(
        "/api/ipd/assign",
        json={"patient_id": pid, "nurse_id": actors["Nurse"].id},
        headers=auth_headers(actors["HeadNurse"]),
    )
    assert resp2.status_code == 200
    return pid


def _endpoints(patient_id, actors, ward_id):
    nurse_id = actors["Nurse"].id
    target_id = actors["NursingStation"].id
    return [
        dict(name="create_patient", method="POST", path="/api/ipd/patients",
             body={"name": "New Admit", "ward": "ICU", "bed": "I1"},
             allowed={"HeadNurse", "NursingStation"}, ok_status={200}),
        dict(name="assign_patient", method="POST", path="/api/ipd/assign",
             body={"patient_id": patient_id, "nurse_id": nurse_id},
             allowed={"HeadNurse"}, ok_status={200}),
        dict(name="record_vital", method="POST", path="/api/ipd/vitals",
             body={"patient_id": patient_id, "bp_systolic": 120, "bp_diastolic": 80,
                   "heart_rate": 75, "temperature": 37.0, "oxygen_sat": 98, "respiratory_rate": 16},
             allowed={"HeadNurse", "Nurse"}, ok_status={200}),
        dict(name="get_vitals", method="GET", path=f"/api/ipd/vitals/{patient_id}",
             body=None, allowed={"HeadNurse", "NursingStation", "Nurse", "Doctor"}, ok_status={200}),
        dict(name="create_task", method="POST", path="/api/ipd/tasks",
             body={"patient_id": patient_id, "description": "Check vitals"},
             allowed={"HeadNurse"}, ok_status={200}),
        dict(name="get_tasks", method="GET", path=f"/api/ipd/tasks/{patient_id}",
             body=None, allowed={"HeadNurse", "NursingStation", "Nurse", "Doctor"}, ok_status={200}),
        dict(name="create_nursing_note", method="POST", path="/api/nursing-notes",
             body={"patient_id": patient_id, "subjective": "s", "objective": "o", "assessment": "a", "plan": "p"},
             allowed={"HeadNurse", "Nurse"}, ok_status={200}),
        dict(name="update_patient", method="PUT", path=f"/api/patients/{patient_id}",
             body={"diagnosis": "Updated diagnosis"},
             allowed={"HeadNurse", "NursingStation"}, ok_status={200}),
        dict(name="get_patient_details", method="GET", path=f"/api/patients/{patient_id}/details",
             body=None, allowed={"HeadNurse", "NursingStation", "Nurse", "Doctor"}, ok_status={200}),
        dict(name="get_ipd_patients", method="GET", path="/api/ipd/patients",
             body=None, allowed={"HeadNurse", "NursingStation", "Nurse", "Doctor"}, ok_status={200}),
        dict(name="get_users", method="GET", path="/api/auth/users",
             body=None, allowed={"Admin", "HeadNurse"}, ok_status={200}),
        dict(name="update_user_role", method="PATCH", path=f"/api/auth/users/{target_id}",
             body={"role": "Nurse"}, allowed={"Admin"}, ok_status={200}),
        dict(name="reset_password", method="PATCH", path=f"/api/auth/users/{target_id}/password",
             body={"new_password": "NewStr0ng!Passw0rd9"}, allowed={"Admin"}, ok_status={200}),
        dict(name="admin_create_user", method="POST", path="/api/auth/admin/create-user",
             body={"email": "brandnew@matrix.com", "password": "NewStr0ng!Passw0rd9", "role": "Nurse"},
             allowed={"Admin"}, ok_status={200}),
        dict(name="drug_interactions", method="POST", path="/api/drug-interactions",
             body={"medications": [{"drugName": "Aspirin"}, {"drugName": "Warfarin"}]},
             allowed={"Admin", "HeadNurse", "NursingStation", "Nurse", "Doctor"}, ok_status={200}),
        dict(name="list_wards", method="GET", path="/api/wards",
             body=None, allowed={"HeadNurse", "Admin"}, ok_status={200}),
        dict(name="create_ward", method="POST", path="/api/wards",
             body={"name": "Second Matrix Ward", "bed_capacity": 5},
             allowed={"HeadNurse", "Admin"}, ok_status={200}),
        dict(name="update_ward", method="PATCH", path=f"/api/wards/{ward_id}",
             body={"bed_capacity": 12}, allowed={"HeadNurse", "Admin"}, ok_status={200}),
        dict(name="delete_ward", method="DELETE", path=f"/api/wards/{ward_id}",
             body=None, allowed={"HeadNurse", "Admin"}, ok_status={200}),
        dict(name="get_shifts", method="GET", path="/api/ipd/shifts",
             body=None, allowed={"HeadNurse"}, ok_status={200}),
        dict(name="set_shift", method="PUT", path="/api/ipd/shifts",
             body={"nurse_id": nurse_id, "shift_date": "2026-01-05", "shift_type": "Morning"},
             allowed={"HeadNurse"}, ok_status={200}),
        dict(name="get_reports", method="GET", path="/api/ipd/reports",
             body=None, allowed={"HeadNurse"}, ok_status={200}),
        dict(name="get_dashboard_summary", method="GET", path="/api/ipd/dashboard-summary",
             body=None, allowed={"HeadNurse"}, ok_status={200}),
        dict(name="get_alerts", method="GET", path="/api/ipd/alerts",
             body=None, allowed={"HeadNurse", "NursingStation", "Nurse", "Doctor"}, ok_status={200}),
        dict(name="get_consultation_analytics", method="GET", path="/api/consultations/analytics",
             body=None, allowed={"Doctor"}, ok_status={200}),
    ]


def _ids(endpoint_defs_and_roles):
    return [f"{e['name']}-{role}" for e, role in endpoint_defs_and_roles]


def _make_cases():
    """Build (endpoint_name, role) pairs; actual endpoint dicts are resolved inside the test
    since they depend on per-test fixture values (patient_id, actor ids)."""
    names = ["create_patient", "assign_patient", "record_vital", "get_vitals", "create_task",
             "get_tasks", "create_nursing_note", "update_patient",
             "get_patient_details", "get_ipd_patients", "get_users", "update_user_role",
             "reset_password", "admin_create_user", "drug_interactions",
             "list_wards", "create_ward", "update_ward", "delete_ward",
             "get_shifts", "set_shift", "get_reports", "get_dashboard_summary",
             "get_alerts", "get_consultation_analytics"]
    roles = ["Admin", "HeadNurse", "NursingStation", "Nurse", "Doctor"]
    return [(n, r) for n in names for r in roles]


@pytest.mark.parametrize("endpoint_name,role", _make_cases(),
                          ids=[f"{n}-{r}" for n, r in _make_cases()])
def test_permission_matrix(client, actors, patient_id, ward_id, auth_headers, monkeypatch, endpoint_name, role):
    endpoints_by_name = {e["name"]: e for e in _endpoints(patient_id, actors, ward_id)}
    endpoint = endpoints_by_name[endpoint_name]

    user = actors[role]
    headers = auth_headers(user)
    method = endpoint["method"]
    path = endpoint["path"]
    body = endpoint["body"]

    if method == "GET":
        resp = client.get(path, headers=headers)
    elif method == "POST":
        resp = client.post(path, json=body, headers=headers)
    elif method == "PUT":
        resp = client.put(path, json=body, headers=headers)
    elif method == "PATCH":
        resp = client.patch(path, json=body, headers=headers)
    elif method == "DELETE":
        resp = client.delete(path, headers=headers)
    else:
        raise AssertionError(f"unsupported method {method}")

    if role in endpoint["allowed"]:
        assert resp.status_code in endpoint["ok_status"], (
            f"{role} should be ALLOWED to call {endpoint_name} ({method} {path}) "
            f"but got {resp.status_code}: {resp.text}"
        )
    else:
        assert resp.status_code == 403, (
            f"{role} should be DENIED from {endpoint_name} ({method} {path}) "
            f"but got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# Unauthenticated access: every protected endpoint must reject requests with no bearer token
# at all (FastAPI's HTTPBearer default: 403 "Not authenticated"), not crash or leak data.
# ---------------------------------------------------------------------------

UNAUTH_ENDPOINTS = [
    ("GET", "/api/ipd/patients"),
    ("POST", "/api/ipd/patients"),
    ("POST", "/api/ipd/assign"),
    ("POST", "/api/ipd/vitals"),
    ("POST", "/api/ipd/tasks"),
    ("POST", "/api/nursing-notes"),
    ("GET", "/api/auth/users"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/consultations"),
    ("POST", "/api/drug-interactions"),
]


@pytest.mark.parametrize("method,path", UNAUTH_ENDPOINTS, ids=[f"{m}-{p}" for m, p in UNAUTH_ENDPOINTS])
def test_unauthenticated_request_rejected(client, method, path):
    resp = client.request(method, path, json={} if method == "POST" else None)
    assert resp.status_code in (401, 403), (
        f"{method} {path} with no Authorization header should be rejected, got {resp.status_code}"
    )


@pytest.mark.parametrize("method,path", UNAUTH_ENDPOINTS, ids=[f"{m}-{p}" for m, p in UNAUTH_ENDPOINTS])
def test_garbage_bearer_token_rejected(client, method, path):
    headers = {"Authorization": "Bearer this-is-not-a-real-jwt"}
    resp = client.request(method, path, json={} if method == "POST" else None, headers=headers)
    assert resp.status_code == 401
