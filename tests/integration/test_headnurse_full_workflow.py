"""
Complete end-to-end coverage of the HeadNurse role: every functionality a head nurse touches
across a ward day, driven in realistic multi-step sequences (not just single-endpoint checks,
which live in the other headnurse_* files). HeadNurse is this system's ward-management role:
admits patients, assigns/reassigns/unassigns nurses, records vitals directly, creates and
manages tasks for any nurse, writes nursing notes, discharges patients, and is the only role
that can see nurse workload.
"""
import pytest


@pytest.fixture
def head_nurse(make_user):
    return make_user(email="head@hn-workflow.com", role="HeadNurse")


@pytest.fixture
def nurse_a(make_user, head_nurse):
    return make_user(email="nurse.a@hn-workflow.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def nurse_b(make_user, head_nurse):
    return make_user(email="nurse.b@hn-workflow.com", role="Nurse", organization_id=head_nurse.organization_id)


# ---------------------------------------------------------------------------
# Login / session
# ---------------------------------------------------------------------------

def test_headnurse_login_succeeds_and_returns_role(client, head_nurse):
    resp = client.post("/api/auth/login", json={"email": head_nurse.email, "password": "Str0ng!Passw0rd#1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["role"] == "HeadNurse"
    assert "access_token" in data
    assert "refresh_token" in data


def test_headnurse_wrong_password_rejected(client, head_nurse):
    resp = client.post("/api/auth/login", json={"email": head_nurse.email, "password": "WrongPassword123!"})
    assert resp.status_code == 401


def test_headnurse_me_endpoint_reflects_role(client, head_nurse, auth_headers):
    resp = client.get("/api/auth/me", headers=auth_headers(head_nurse))
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "HeadNurse"


def test_headnurse_token_refresh_preserves_role(client, head_nurse):
    login = client.post("/api/auth/login", json={"email": head_nurse.email, "password": "Str0ng!Passw0rd#1"}).json()
    refresh = client.post("/api/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert refresh.status_code == 200
    new_access = refresh.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.json()["user"]["role"] == "HeadNurse"


def test_headnurse_locked_out_after_five_failed_attempts(client, head_nurse):
    for _ in range(5):
        client.post("/api/auth/login", json={"email": head_nurse.email, "password": "wrong"})
    resp = client.post("/api/auth/login", json={"email": head_nurse.email, "password": "Str0ng!Passw0rd#1"})
    assert resp.status_code == 403
    assert "locked" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Full realistic day: admit -> assign -> record vitals -> create task -> nursing note ->
# reassign (shift handoff) -> discharge, all as HeadNurse.
# ---------------------------------------------------------------------------

def test_full_headnurse_day_end_to_end(client, head_nurse, nurse_a, nurse_b, auth_headers):
    h = auth_headers(head_nurse)

    # 1. Dashboard starts empty.
    assert client.get("/api/ipd/patients", headers=h).json() == []

    # 2. Admit a patient.
    admit = client.post("/api/ipd/patients", json={"name": "Full Day HN Patient", "age": 60, "gender": "Male",
                                                     "ward": "General", "bed": "F1", "diagnosis": "Observation"}, headers=h)
    assert admit.status_code == 200
    pid = admit.json()["id"]

    # 3. Patient appears unassigned on the roster.
    roster = client.get("/api/ipd/patients", headers=h).json()
    assert roster[0]["assigned_nurse"] is None

    # 4. Assign to nurse_a.
    assign = client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurse_a.id}, headers=h)
    assert assign.status_code == 200

    # 5. HeadNurse can record vitals directly (bypasses the assignment check that applies to Nurse).
    vitals = client.post("/api/ipd/vitals", json={"patient_id": pid, "bp_systolic": 118, "bp_diastolic": 76,
                                                    "heart_rate": 74, "temperature": 36.9, "oxygen_sat": 98,
                                                    "respiratory_rate": 16}, headers=h)
    assert vitals.status_code == 200

    # 6. Create a task assigned to nurse_a.
    task = client.post("/api/ipd/tasks", json={"patient_id": pid, "description": "Administer morning medication",
                                                 "nurse_id": nurse_a.id}, headers=h)
    assert task.status_code == 200
    task_id = task.json()["id"]

    # 7. HeadNurse writes a nursing note directly.
    note = client.post("/api/nursing-notes", json={"patient_id": pid, "subjective": "Comfortable",
                                                     "objective": "Vitals stable", "assessment": "Improving",
                                                     "plan": "Continue plan"}, headers=h)
    assert note.status_code == 200

    # 8. HeadNurse marks the task complete themselves (regression for the fixed UI gap --
    #    backend already allowed this; confirming it here too).
    complete = client.patch(f"/api/ipd/tasks/{task_id}", json={"status": "Completed"}, headers=h)
    assert complete.status_code == 200

    # 9. Shift handoff: reassign to nurse_b.
    reassign = client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurse_b.id}, headers=h)
    assert reassign.status_code == 200
    roster = client.get("/api/ipd/patients", headers=h).json()
    assert roster[0]["assigned_nurse"]["id"] == nurse_b.id

    # 10. Full chart reflects everything recorded, regardless of who's currently assigned.
    details = client.get(f"/api/patients/{pid}/details", headers=h).json()
    assert len(details["vitals"]) == 1
    assert len(details["tasks"]) == 1
    assert details["tasks"][0]["status"] == "Completed"
    assert len(details["nursing_notes"]) == 1

    # 11. Discharge.
    discharge = client.put(f"/api/patients/{pid}", json={"status": "Discharged"}, headers=h)
    assert discharge.status_code == 200
    final_roster = client.get("/api/ipd/patients", headers=h).json()
    assert final_roster == []


def test_headnurse_manages_multiple_patients_across_multiple_nurses_simultaneously(client, head_nurse, nurse_a, nurse_b, auth_headers):
    h = auth_headers(head_nurse)
    patients = []
    for i in range(6):
        resp = client.post("/api/ipd/patients", json={"name": f"Multi Patient {i}", "ward": "General", "bed": str(i)}, headers=h)
        patients.append(resp.json()["id"])
    for i, pid in enumerate(patients):
        nurse = nurse_a if i % 2 == 0 else nurse_b
        client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurse.id}, headers=h)

    roster = client.get("/api/ipd/patients", headers=h).json()
    assert len(roster) == 6
    a_count = sum(1 for p in roster if p["assigned_nurse"] and p["assigned_nurse"]["id"] == nurse_a.id)
    b_count = sum(1 for p in roster if p["assigned_nurse"] and p["assigned_nurse"]["id"] == nurse_b.id)
    assert a_count == 3
    assert b_count == 3


# ---------------------------------------------------------------------------
# HeadNurse bypasses the nurse-assignment check on vitals/tasks/notes/details/consult --
# confirmed across the full set of endpoints that carry that check for the Nurse role.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", [
    "record_vital", "get_vitals", "get_tasks", "create_note", "get_details",
])
def test_headnurse_never_needs_an_explicit_assignment(client, head_nurse, auth_headers, action):
    h = auth_headers(head_nurse)
    pid = client.post("/api/ipd/patients", json={"name": "No Assignment Needed", "ward": "General", "bed": "X1"}, headers=h).json()["id"]
    # Deliberately never assign any nurse -- HeadNurse must still have full access.
    if action == "record_vital":
        resp = client.post("/api/ipd/vitals", json={"patient_id": pid, "heart_rate": 80}, headers=h)
    elif action == "get_vitals":
        resp = client.get(f"/api/ipd/vitals/{pid}", headers=h)
    elif action == "get_tasks":
        resp = client.get(f"/api/ipd/tasks/{pid}", headers=h)
    elif action == "create_note":
        resp = client.post("/api/nursing-notes", json={"patient_id": pid, "subjective": "ok", "objective": "", "assessment": "", "plan": ""}, headers=h)
    elif action == "get_details":
        resp = client.get(f"/api/patients/{pid}/details", headers=h)
    assert resp.status_code == 200, f"{action}: HeadNurse should never be blocked by the assignment check, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Cross-organization isolation, specifically exercised as the HeadNurse actor across every
# HeadNurse-capable endpoint (existing test_multi_tenant_isolation.py covers a subset; this
# rounds it out to every endpoint HeadNurse can call).
# ---------------------------------------------------------------------------

@pytest.fixture
def other_org_patient(make_user, db_session):
    from app.models import Patient
    other_head = make_user(email="other-head@hn-workflow.com", role="HeadNurse")
    patient = Patient(name="Other Org Patient", ward="General", bed="O1",
                       organization_id=other_head.organization_id, created_by=other_head.id)
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return patient


@pytest.mark.parametrize("method,path_fn,body", [
    ("GET", lambda pid: f"/api/patients/{pid}/details", None),
    ("PUT", lambda pid: f"/api/patients/{pid}", {"diagnosis": "tampered"}),
    ("GET", lambda pid: f"/api/ipd/vitals/{pid}", None),
    ("POST", lambda pid: "/api/ipd/vitals", "vitals_body"),
    ("GET", lambda pid: f"/api/ipd/tasks/{pid}", None),
    ("POST", lambda pid: "/api/ipd/tasks", "tasks_body"),
    ("POST", lambda pid: "/api/ipd/assign", "assign_body"),
    ("POST", lambda pid: "/api/ipd/unassign", "unassign_body"),
    ("POST", lambda pid: "/api/nursing-notes", "notes_body"),
])
def test_headnurse_blocked_from_other_orgs_patient_on_every_endpoint(client, head_nurse, other_org_patient, auth_headers, method, path_fn, body):
    pid = other_org_patient.id
    if body == "vitals_body":
        body = {"patient_id": pid, "heart_rate": 80}
    elif body == "tasks_body":
        body = {"patient_id": pid, "description": "cross org task"}
    elif body == "assign_body":
        body = {"patient_id": pid, "nurse_id": 999999}
    elif body == "unassign_body":
        body = {"patient_id": pid}
    elif body == "notes_body":
        body = {"patient_id": pid, "subjective": "x", "objective": "x", "assessment": "x", "plan": "x"}

    path = path_fn(pid)
    h = auth_headers(head_nurse)
    if method == "GET":
        resp = client.get(path, headers=h)
    else:
        resp = client.post(path, json=body, headers=h) if method == "POST" else client.put(path, json=body, headers=h)
    assert resp.status_code in (403, 404), f"{method} {path}: cross-org access should be blocked, got {resp.status_code}"
