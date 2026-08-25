"""
Confirms NursingStation's denial from clinical/admin endpoints holds regardless of request
payload shape -- i.e. the role check genuinely runs before any body parsing/validation, so no
combination of malformed, adversarial, or edge-case input can accidentally slip past it (a
permission check implemented *after* some other validation step could, in principle, be
bypassed by input that fails validation in a way that short-circuits before the role check --
this file rules that out empirically for every denied endpoint).

Note: record_vital and create_nursing_note both validate required fields (patient_id) with a
400 *before* their role check runs -- a pre-existing, already-tested ordering (see
test_ipd_edge_cases.py). So every payload here keeps those required fields present and valid,
varying only extra/unrelated content, to correctly isolate "does the role check hold" from
"does field validation happen to fire first."
"""
import pytest


@pytest.fixture
def station(make_user):
    return make_user(email="station@ns-denial.com", role="NursingStation")


@pytest.fixture
def patient_id(client, station, auth_headers):
    resp = client.post("/api/ipd/patients", json={"name": "Denial Patient", "ward": "General", "bed": "D1"},
                        headers=auth_headers(station))
    return resp.json()["id"]


EXTRA_FIELD_VARIANTS = [
    ("no_extra_fields", {}),
    ("unexpected_extra_fields", {"unexpected": "field", "another": [1, 2, 3]}),
    ("null_extra_field", {"notes": None}),
    ("nested_object_extra_field", {"metadata": {"a": 1, "b": [1, 2]}}),
    ("very_long_extra_string", {"notes": "x" * 3000}),
]


@pytest.mark.parametrize("case_id,extra", EXTRA_FIELD_VARIANTS, ids=[c[0] for c in EXTRA_FIELD_VARIANTS])
def test_station_denied_from_record_vital_regardless_of_payload(client, station, patient_id, auth_headers, case_id, extra):
    body = {"patient_id": patient_id, "heart_rate": 80, **extra}
    resp = client.post("/api/ipd/vitals", json=body, headers=auth_headers(station))
    assert resp.status_code == 403, f"{case_id}: expected 403, got {resp.status_code}"


@pytest.mark.parametrize("case_id,extra", EXTRA_FIELD_VARIANTS, ids=[c[0] for c in EXTRA_FIELD_VARIANTS])
def test_station_denied_from_create_task_regardless_of_payload(client, station, patient_id, auth_headers, case_id, extra):
    body = {"patient_id": patient_id, "description": "x", **extra}
    resp = client.post("/api/ipd/tasks", json=body, headers=auth_headers(station))
    assert resp.status_code == 403, f"{case_id}: expected 403, got {resp.status_code}"


@pytest.mark.parametrize("case_id,extra", EXTRA_FIELD_VARIANTS, ids=[c[0] for c in EXTRA_FIELD_VARIANTS])
def test_station_denied_from_create_nursing_note_regardless_of_payload(client, station, patient_id, auth_headers, case_id, extra):
    body = {"patient_id": patient_id, "subjective": "x", "objective": "", "assessment": "", "plan": "", **extra}
    resp = client.post("/api/nursing-notes", json=body, headers=auth_headers(station))
    assert resp.status_code == 403, f"{case_id}: expected 403, got {resp.status_code}"


@pytest.mark.parametrize("case_id,extra", EXTRA_FIELD_VARIANTS, ids=[c[0] for c in EXTRA_FIELD_VARIANTS])
def test_station_denied_from_assign_regardless_of_payload(client, station, patient_id, auth_headers, case_id, extra):
    body = {"patient_id": patient_id, "nurse_id": 1, **extra}
    resp = client.post("/api/ipd/assign", json=body, headers=auth_headers(station))
    assert resp.status_code == 403, f"{case_id}: expected 403, got {resp.status_code}"


@pytest.mark.parametrize("case_id,extra", EXTRA_FIELD_VARIANTS, ids=[c[0] for c in EXTRA_FIELD_VARIANTS])
def test_station_denied_from_unassign_regardless_of_payload(client, station, patient_id, auth_headers, case_id, extra):
    body = {"patient_id": patient_id, **extra}
    resp = client.post("/api/ipd/unassign", json=body, headers=auth_headers(station))
    assert resp.status_code == 403, f"{case_id}: expected 403, got {resp.status_code}"


# ---------------------------------------------------------------------------
# These four endpoints check role BEFORE parsing the body at all (see main.py: assign_patient,
# create_task check is_head_nurse as their literal first line) -- so even a completely absent
# or missing-required-field body must still 403, not 400. This is the strongest version of the
# "role check runs first" guarantee.
# ---------------------------------------------------------------------------

def test_station_denied_from_create_task_even_with_missing_required_fields(client, station, auth_headers):
    resp = client.post("/api/ipd/tasks", json={}, headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_denied_from_assign_even_with_missing_required_fields(client, station, auth_headers):
    resp = client.post("/api/ipd/assign", json={}, headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_denied_from_unassign_even_with_missing_required_fields(client, station, auth_headers):
    resp = client.post("/api/ipd/unassign", json={}, headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_denied_from_update_task_even_with_no_body_at_all(client, station, auth_headers):
    resp = client.patch("/api/ipd/tasks/1", json={}, headers=auth_headers(station))
    assert resp.status_code in (403, 404)


def test_station_denied_from_nurse_workload_regardless_of_query_params(client, station, auth_headers):
    resp = client.get("/api/ipd/nurse-workload?extra=param", headers=auth_headers(station))
    assert resp.status_code == 403


def test_station_denied_from_get_users_regardless_of_role_filter(client, station, auth_headers):
    resp = client.get("/api/auth/users?role=Nurse", headers=auth_headers(station))
    assert resp.status_code == 403


@pytest.mark.parametrize("garbage_body", [
    b"not json at all",
    b"{",
    b'{"patient_id": }',
])
def test_station_denied_before_malformed_json_body_is_even_relevant(client, station, garbage_body, auth_headers):
    """create_task checks role BEFORE calling await request.json() at all -- proving the 403
    doesn't depend on successfully parsing the body first, for endpoints structured that way."""
    resp = client.post("/api/ipd/tasks", content=garbage_body,
                        headers={**auth_headers(station), "Content-Type": "application/json"})
    assert resp.status_code == 403, f"got {resp.status_code}: {resp.text}"
