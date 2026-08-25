"""
Patient Administration tests: standalone registration (decoupled from IPD admission), MRN
generation, and patient search/MRN lookup. Mirrors the structure of the pharmacy/inventory/
billing test suites.
"""
import pytest


@pytest.fixture
def nursing_station(make_user):
    return make_user(email="frontdesk@reghosp.com", role="NursingStation")


@pytest.fixture
def admin(make_user, nursing_station):
    return make_user(email="admin@reghosp.com", role="Admin", organization_id=nursing_station.organization_id)


@pytest.fixture
def doctor(make_user, nursing_station):
    return make_user(email="doctor@reghosp.com", role="Doctor", organization_id=nursing_station.organization_id)


@pytest.fixture
def nurse(make_user, nursing_station):
    return make_user(email="nurse@reghosp.com", role="Nurse", organization_id=nursing_station.organization_id)


def _register(client, headers, **overrides):
    payload = {"name": "Walk-in Patient", "age": 34, "gender": "M", "phone": "9876543210"}
    payload.update(overrides)
    resp = client.post("/api/patients/register", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Role permissions
# ---------------------------------------------------------------------------

def test_front_desk_and_admin_can_register(client, nursing_station, admin, auth_headers):
    for i, user in enumerate((nursing_station, admin)):
        resp = client.post("/api/patients/register", json={"name": f"P{i}", "age": 30}, headers=auth_headers(user))
        assert resp.status_code == 201


def test_nurse_cannot_register_or_search(client, nurse, auth_headers):
    headers = auth_headers(nurse)
    assert client.post("/api/patients/register", json={"name": "X", "age": 30}, headers=headers).status_code == 403
    assert client.get("/api/patients/search", headers=headers).status_code == 403


def test_doctor_can_search_but_not_register(client, doctor, auth_headers):
    headers = auth_headers(doctor)
    assert client.post("/api/patients/register", json={"name": "X", "age": 30}, headers=headers).status_code == 403
    assert client.get("/api/patients/search", headers=headers).status_code == 200


# ---------------------------------------------------------------------------
# Registration correctness
# ---------------------------------------------------------------------------

def test_registration_requires_age_or_dob(client, nursing_station, auth_headers):
    resp = client.post("/api/patients/register", json={"name": "No Age"}, headers=auth_headers(nursing_station))
    assert resp.status_code == 400


def test_registration_accepts_date_of_birth_without_age(client, nursing_station, auth_headers):
    resp = client.post("/api/patients/register", json={"name": "DOB Only", "date_of_birth": "1990-05-15"}, headers=auth_headers(nursing_station))
    assert resp.status_code == 201
    assert resp.json()["date_of_birth"] == "1990-05-15"


def test_mrn_is_generated_and_unique(client, nursing_station, auth_headers):
    headers = auth_headers(nursing_station)
    p1 = _register(client, headers, name="Patient One")
    p2 = _register(client, headers, name="Patient Two")
    assert p1["mrn"] is not None
    assert p2["mrn"] is not None
    assert p1["mrn"] != p2["mrn"]
    assert str(nursing_station.organization_id) in p1["mrn"]


def test_registered_patient_status_does_not_affect_ward_occupancy(client, nursing_station, auth_headers, db_session):
    """A standalone-registered patient must not be counted as an active IPD admission --
    Registered is a distinct status from Active, which ward-occupancy queries filter on."""
    from app.models import Patient

    _register(client, auth_headers(nursing_station), name="Not Admitted")
    active_count = db_session.query(Patient).filter(
        Patient.organization_id == nursing_station.organization_id, Patient.status == "Active"
    ).count()
    assert active_count == 0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def test_search_by_partial_name(client, nursing_station, auth_headers):
    headers = auth_headers(nursing_station)
    _register(client, headers, name="Rajesh Kumar")
    _register(client, headers, name="Anita Sharma")
    results = client.get("/api/patients/search", params={"q": "Raj"}, headers=headers).json()
    assert {r["name"] for r in results} == {"Rajesh Kumar"}


def test_search_by_phone(client, nursing_station, auth_headers):
    headers = auth_headers(nursing_station)
    _register(client, headers, name="Phone Match", phone="9111122223")
    results = client.get("/api/patients/search", params={"q": "9111122223"}, headers=headers).json()
    assert len(results) == 1
    assert results[0]["name"] == "Phone Match"


def test_search_by_exact_mrn(client, nursing_station, auth_headers):
    headers = auth_headers(nursing_station)
    patient = _register(client, headers, name="MRN Lookup")
    results = client.get("/api/patients/search", params={"q": patient["mrn"]}, headers=headers).json()
    assert len(results) == 1
    assert results[0]["id"] == patient["id"]


def test_get_by_mrn_endpoint(client, nursing_station, auth_headers):
    headers = auth_headers(nursing_station)
    patient = _register(client, headers, name="Direct MRN")
    resp = client.get(f"/api/patients/by-mrn/{patient['mrn']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == patient["id"]

    resp = client.get("/api/patients/by-mrn/MRN-9999-999999", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def two_org_stations(make_user):
    return {
        "a": make_user(email="fd.a@reg-a.com", role="NursingStation"),
        "b": make_user(email="fd.b@reg-b.com", role="NursingStation"),
    }


def test_search_scoped_per_org(client, two_org_stations, auth_headers):
    headers_a = auth_headers(two_org_stations["a"])
    headers_b = auth_headers(two_org_stations["b"])
    _register(client, headers_a, name="Org A Patient")
    _register(client, headers_b, name="Org B Patient")

    results_a = client.get("/api/patients/search", params={"q": "Patient"}, headers=headers_a).json()
    assert {r["name"] for r in results_a} == {"Org A Patient"}


def test_by_mrn_does_not_leak_across_orgs(client, two_org_stations, auth_headers):
    headers_a = auth_headers(two_org_stations["a"])
    headers_b = auth_headers(two_org_stations["b"])
    patient_a = _register(client, headers_a, name="Cross Org")

    resp = client.get(f"/api/patients/by-mrn/{patient_a['mrn']}", headers=headers_b)
    assert resp.status_code == 404
