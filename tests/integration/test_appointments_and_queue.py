"""
Appointment Scheduling and Queue/Token Management tests, including the emergency queue-jump
("ER Workflow" minimal implementation). Mirrors the structure of the other new-module suites.
"""
from datetime import date, datetime, timedelta

import pytest


@pytest.fixture
def nursing_station(make_user):
    return make_user(email="frontdesk@apthosp.com", role="NursingStation")


@pytest.fixture
def doctor(make_user, nursing_station):
    return make_user(email="doctor@apthosp.com", role="Doctor", organization_id=nursing_station.organization_id)


@pytest.fixture
def other_doctor(make_user, nursing_station):
    return make_user(email="doctor2@apthosp.com", role="Doctor", organization_id=nursing_station.organization_id)


@pytest.fixture
def nurse(make_user, nursing_station):
    return make_user(email="nurse@apthosp.com", role="Nurse", organization_id=nursing_station.organization_id)


@pytest.fixture
def patient(nursing_station, db_session):
    from app.models import Patient

    p = Patient(name="Appt Patient", age=40, gender="M", organization_id=nursing_station.organization_id,
                created_by=nursing_station.id, status="Registered")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_appointment(client, headers, patient_id, doctor_id, **overrides):
    payload = {"patient_id": patient_id, "doctor_id": doctor_id,
               "scheduled_at": (datetime.utcnow() + timedelta(hours=1)).isoformat()}
    payload.update(overrides)
    resp = client.post("/api/appointments", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Role permissions
# ---------------------------------------------------------------------------

def test_front_desk_can_book_nurse_cannot(client, nursing_station, nurse, doctor, patient, auth_headers):
    resp = _create_appointment(client, auth_headers(nursing_station), patient.id, doctor.id)
    assert resp["status"] == "Scheduled"

    resp = client.post("/api/appointments", json={"patient_id": patient.id, "doctor_id": doctor.id,
                        "scheduled_at": datetime.utcnow().isoformat()}, headers=auth_headers(nurse))
    assert resp.status_code == 403


def test_doctor_sees_only_own_appointments_by_default(client, nursing_station, doctor, other_doctor, patient, auth_headers):
    headers = auth_headers(nursing_station)
    _create_appointment(client, headers, patient.id, doctor.id)
    _create_appointment(client, headers, patient.id, other_doctor.id)

    own = client.get("/api/appointments", headers=auth_headers(doctor)).json()
    assert all(a["doctor_id"] == doctor.id for a in own)
    assert len(own) == 1


# ---------------------------------------------------------------------------
# Appointment lifecycle
# ---------------------------------------------------------------------------

def test_list_doctors_for_scheduling(client, nursing_station, doctor, other_doctor, nurse, auth_headers):
    resp = client.get("/api/appointments/doctors", headers=auth_headers(nursing_station))
    assert resp.status_code == 200
    emails = {d["email"] for d in resp.json()}
    assert emails == {doctor.email, other_doctor.email}


def test_list_doctors_for_scheduling_denied_for_nurse(client, nurse, auth_headers):
    resp = client.get("/api/appointments/doctors", headers=auth_headers(nurse))
    assert resp.status_code == 403


def test_list_doctors_for_scheduling_org_scoped(client, nursing_station, doctor, auth_headers, make_user):
    foreign_doctor = make_user(email="foreign.doc@other-hosp.com", role="Doctor")
    resp = client.get("/api/appointments/doctors", headers=auth_headers(nursing_station))
    assert resp.status_code == 200
    emails = {d["email"] for d in resp.json()}
    assert emails == {doctor.email}
    assert foreign_doctor.email not in emails


def test_appointment_requires_org_scoped_doctor(client, nursing_station, patient, auth_headers, make_user):
    foreign_doctor = make_user(email="foreign.doc@other.com", role="Doctor")
    resp = client.post("/api/appointments", json={"patient_id": patient.id, "doctor_id": foreign_doctor.id,
                        "scheduled_at": datetime.utcnow().isoformat()}, headers=auth_headers(nursing_station))
    assert resp.status_code == 404


def test_cancel_appointment(client, nursing_station, doctor, patient, auth_headers):
    headers = auth_headers(nursing_station)
    appt = _create_appointment(client, headers, patient.id, doctor.id)
    resp = client.post(f"/api/appointments/{appt['id']}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "Cancelled"


def test_cannot_cancel_already_cancelled_appointment(client, nursing_station, doctor, patient, auth_headers):
    headers = auth_headers(nursing_station)
    appt = _create_appointment(client, headers, patient.id, doctor.id)
    client.post(f"/api/appointments/{appt['id']}/cancel", headers=headers)
    resp = client.post(f"/api/appointments/{appt['id']}/cancel", headers=headers)
    assert resp.status_code == 400


def test_check_in_issues_a_token(client, nursing_station, doctor, patient, auth_headers):
    headers = auth_headers(nursing_station)
    appt = _create_appointment(client, headers, patient.id, doctor.id)
    resp = client.post(f"/api/appointments/{appt['id']}/check-in", headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["appointment"]["status"] == "CheckedIn"
    assert body["token"]["token_number"] == 1
    assert body["token"]["status"] == "Waiting"
    assert body["token"]["appointment_id"] == appt["id"]


def test_cannot_check_in_twice(client, nursing_station, doctor, patient, auth_headers):
    headers = auth_headers(nursing_station)
    appt = _create_appointment(client, headers, patient.id, doctor.id)
    client.post(f"/api/appointments/{appt['id']}/check-in", headers=headers)
    resp = client.post(f"/api/appointments/{appt['id']}/check-in", headers=headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Walk-in queue / tokens
# ---------------------------------------------------------------------------

def test_walkin_tokens_sequential_per_day(client, nursing_station, patient, auth_headers, db_session):
    from app.models import Patient

    headers = auth_headers(nursing_station)
    patient2 = Patient(name="Second", age=25, gender="F", organization_id=nursing_station.organization_id, created_by=nursing_station.id)
    db_session.add(patient2)
    db_session.commit()
    db_session.refresh(patient2)

    t1 = client.post("/api/queue/tokens", json={"patient_id": patient.id}, headers=headers).json()
    t2 = client.post("/api/queue/tokens", json={"patient_id": patient2.id}, headers=headers).json()
    assert t1["token_number"] == 1
    assert t2["token_number"] == 2


def test_emergency_token_jumps_the_queue(client, nursing_station, patient, auth_headers, db_session):
    from app.models import Patient

    headers = auth_headers(nursing_station)
    patient2 = Patient(name="Emergency Case", age=60, gender="M", organization_id=nursing_station.organization_id, created_by=nursing_station.id)
    db_session.add(patient2)
    db_session.commit()
    db_session.refresh(patient2)

    normal = client.post("/api/queue/tokens", json={"patient_id": patient.id}, headers=headers).json()
    emergency = client.post("/api/queue/tokens", json={"patient_id": patient2.id, "is_emergency": True}, headers=headers).json()
    assert emergency["token_number"] > normal["token_number"]  # issued later, higher number

    queue = client.get("/api/queue/tokens", headers=headers).json()
    assert queue[0]["id"] == emergency["id"], "emergency token must be first in queue order despite a higher token number"
    assert queue[1]["id"] == normal["id"]


def test_token_lifecycle_call_complete(client, nursing_station, patient, auth_headers):
    headers = auth_headers(nursing_station)
    token = client.post("/api/queue/tokens", json={"patient_id": patient.id}, headers=headers).json()

    resp = client.patch(f"/api/queue/tokens/{token['id']}/call", headers=headers)
    assert resp.json()["status"] == "InProgress"
    assert resp.json()["called_at"] is not None

    resp = client.patch(f"/api/queue/tokens/{token['id']}/complete", headers=headers)
    assert resp.json()["status"] == "Completed"
    assert resp.json()["completed_at"] is not None


def test_cannot_complete_token_that_was_never_called(client, nursing_station, patient, auth_headers):
    headers = auth_headers(nursing_station)
    token = client.post("/api/queue/tokens", json={"patient_id": patient.id}, headers=headers).json()
    resp = client.patch(f"/api/queue/tokens/{token['id']}/complete", headers=headers)
    assert resp.status_code == 400


def test_skip_token(client, nursing_station, patient, auth_headers):
    headers = auth_headers(nursing_station)
    token = client.post("/api/queue/tokens", json={"patient_id": patient.id}, headers=headers).json()
    resp = client.patch(f"/api/queue/tokens/{token['id']}/skip", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "Skipped"


def test_doctor_filtered_queue_view(client, nursing_station, doctor, other_doctor, patient, auth_headers, db_session):
    from app.models import Patient

    headers = auth_headers(nursing_station)
    patient2 = Patient(name="Other Doc Patient", age=50, gender="F", organization_id=nursing_station.organization_id, created_by=nursing_station.id)
    db_session.add(patient2)
    db_session.commit()
    db_session.refresh(patient2)

    client.post("/api/queue/tokens", json={"patient_id": patient.id, "doctor_id": doctor.id}, headers=headers)
    client.post("/api/queue/tokens", json={"patient_id": patient2.id, "doctor_id": other_doctor.id}, headers=headers)

    own_queue = client.get("/api/queue/tokens", headers=auth_headers(doctor)).json()
    assert all(t["doctor_id"] == doctor.id for t in own_queue)
    assert len(own_queue) == 1


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def two_org_stations(make_user):
    return {
        "a": make_user(email="fd.a@apt-a.com", role="NursingStation"),
        "b": make_user(email="fd.b@apt-b.com", role="NursingStation"),
    }


def test_queue_scoped_per_org(client, two_org_stations, auth_headers, db_session):
    from app.models import Patient

    org_a, org_b = two_org_stations["a"], two_org_stations["b"]
    patient_a = Patient(name="Org A Q", age=30, gender="M", organization_id=org_a.organization_id, created_by=org_a.id)
    patient_b = Patient(name="Org B Q", age=30, gender="M", organization_id=org_b.organization_id, created_by=org_b.id)
    db_session.add_all([patient_a, patient_b])
    db_session.commit()
    db_session.refresh(patient_a)
    db_session.refresh(patient_b)

    client.post("/api/queue/tokens", json={"patient_id": patient_a.id}, headers=auth_headers(org_a))
    client.post("/api/queue/tokens", json={"patient_id": patient_b.id}, headers=auth_headers(org_b))

    queue_a = client.get("/api/queue/tokens", headers=auth_headers(org_a)).json()
    assert len(queue_a) == 1
    assert queue_a[0]["patient_id"] == patient_a.id


def test_cannot_call_another_orgs_token(client, two_org_stations, auth_headers, db_session):
    from app.models import Patient

    org_a, org_b = two_org_stations["a"], two_org_stations["b"]
    patient_a = Patient(name="Org A Call", age=30, gender="M", organization_id=org_a.organization_id, created_by=org_a.id)
    db_session.add(patient_a)
    db_session.commit()
    db_session.refresh(patient_a)

    token = client.post("/api/queue/tokens", json={"patient_id": patient_a.id}, headers=auth_headers(org_a)).json()
    resp = client.patch(f"/api/queue/tokens/{token['id']}/call", headers=auth_headers(org_b))
    assert resp.status_code == 404
