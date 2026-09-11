"""
Oncology Review Results PDF item 23: Hospital-wide Appointment Coordination -- the Patient
Liaison's own cross-department appointment record (backend/app/routers/cca_coordination.py),
deliberately separate from the general HMS Appointment/doctor-queue system since CCAPatient has
no live linkage to the general `patients` table (CCAPatient.hms_patient_id exists on the model
but is never populated anywhere in this codebase).
"""
from datetime import datetime, timedelta

import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def liaison(make_user):
    return make_user(email="liaison@appt-coord-test.com", role="CCAPatientLiaison")


@pytest.fixture
def front_desk(make_user, liaison):
    return make_user(email="frontdesk@appt-coord-test.com", role="CCAFrontDesk", organization_id=liaison.organization_id)


@pytest.fixture
def liaison_headers(auth_headers, liaison):
    return auth_headers(liaison)


@pytest.fixture
def front_desk_headers(auth_headers, front_desk):
    return auth_headers(front_desk)


@pytest.fixture
def patient(db_session, liaison):
    p = CCAPatient(
        mrn="APPT-COORD-0001", name="Appointment Coordination Test Patient", age=49, sex="Female",
        organization_id=liaison.organization_id, journey_state="On Treatment",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _future_iso(days=3):
    return (datetime.utcnow() + timedelta(days=days)).isoformat()


def test_create_appointment_requires_department_and_scheduled_at(client, liaison_headers, patient):
    missing = client.post("/api/cca/coordination/appointments", headers=liaison_headers, json={
        "patient_id": patient.id,
    })
    assert missing.status_code == 422


def test_liaison_creates_and_lists_appointment(client, liaison_headers, patient):
    created = client.post("/api/cca/coordination/appointments", headers=liaison_headers, json={
        "patient_id": patient.id, "department": "Radiology", "purpose": "CT chest/abdomen/pelvis",
        "scheduled_at": _future_iso(), "location": "Radiology Suite 2",
    })
    assert created.status_code == 201, created.text
    assert created.json()["appointment"]["status"] == "Scheduled"
    assert created.json()["appointment"]["department"] == "Radiology"

    listed = client.get(f"/api/cca/coordination/appointments?patient_id={patient.id}", headers=liaison_headers)
    assert listed.status_code == 200
    assert len(listed.json()["results"]) == 1
    assert listed.json()["results"][0]["patient_name"] == patient.name


def test_front_desk_cannot_create_coordination_appointment(client, front_desk_headers, patient):
    denied = client.post("/api/cca/coordination/appointments", headers=front_desk_headers, json={
        "patient_id": patient.id, "department": "Lab", "scheduled_at": _future_iso(),
    })
    assert denied.status_code == 403


def test_update_appointment_status_and_invalid_status_rejected(client, liaison_headers, patient):
    created = client.post("/api/cca/coordination/appointments", headers=liaison_headers, json={
        "patient_id": patient.id, "department": "Surgery", "scheduled_at": _future_iso(),
    })
    appt_id = created.json()["appointment"]["id"]

    bad = client.patch(f"/api/cca/coordination/appointments/{appt_id}", headers=liaison_headers, json={"status": "NotAStatus"})
    assert bad.status_code == 422

    confirmed = client.patch(f"/api/cca/coordination/appointments/{appt_id}", headers=liaison_headers, json={
        "status": "Confirmed", "transport_arranged": True,
    })
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["appointment"]["status"] == "Confirmed"
    assert confirmed.json()["appointment"]["transport_arranged"] is True


def test_missed_appointment_is_excluded_from_upcoming_only_filter(client, liaison_headers, patient):
    created = client.post("/api/cca/coordination/appointments", headers=liaison_headers, json={
        "patient_id": patient.id, "department": "Medical Oncology OPD", "scheduled_at": _future_iso(days=1),
    })
    appt_id = created.json()["appointment"]["id"]

    upcoming_before = client.get(f"/api/cca/coordination/appointments?upcoming_only=true", headers=liaison_headers)
    assert appt_id in [a["id"] for a in upcoming_before.json()["results"]]

    client.patch(f"/api/cca/coordination/appointments/{appt_id}", headers=liaison_headers, json={"status": "Missed"})

    upcoming_after = client.get(f"/api/cca/coordination/appointments?upcoming_only=true", headers=liaison_headers)
    assert appt_id not in [a["id"] for a in upcoming_after.json()["results"]]


def test_appointment_can_link_to_a_coordination_case(client, liaison_headers, patient):
    case = client.post("/api/cca/coordination/cases", headers=liaison_headers, json={"patient_id": patient.id})
    case_id = case.json()["case"]["id"]

    created = client.post("/api/cca/coordination/appointments", headers=liaison_headers, json={
        "patient_id": patient.id, "department": "Lab", "scheduled_at": _future_iso(),
        "coordination_case_id": case_id,
    })
    assert created.status_code == 201, created.text
    assert created.json()["appointment"]["coordination_case_id"] == case_id


def test_cross_org_patient_appointment_not_visible(client, liaison_headers, patient, make_user, auth_headers):
    client.post("/api/cca/coordination/appointments", headers=liaison_headers, json={
        "patient_id": patient.id, "department": "Radiology", "scheduled_at": _future_iso(),
    })
    other_org_liaison = make_user(email="other-org-liaison@appt-coord-test.com", role="CCAPatientLiaison")
    other_headers = auth_headers(other_org_liaison)

    listed = client.get("/api/cca/coordination/appointments", headers=other_headers)
    assert listed.status_code == 200
    assert listed.json()["results"] == []
