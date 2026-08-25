"""
Clinical Workflows gap closure: Procedure Documentation (new ProcedureRecord model/router) and
Follow-up Consultation linking (Consultation.follow_up_of_id, Appointment.follow_up_of_consultation_id).
Mirrors the access-control shape of the other new-module suites (Doctor creates clinical
documentation; Doctor/HeadNurse/NursingStation/assigned-Nurse can read it).
"""
import pytest

from app.models import Consultation, NurseAssignment, Patient


@pytest.fixture
def doctor(make_user):
    return make_user(email="doctor@procedures.com", role="Doctor")


@pytest.fixture
def other_doctor(make_user, doctor):
    return make_user(email="doctor2@procedures.com", role="Doctor", organization_id=doctor.organization_id)


@pytest.fixture
def head_nurse(make_user, doctor):
    return make_user(email="head@procedures.com", role="HeadNurse", organization_id=doctor.organization_id)


@pytest.fixture
def nursing_station(make_user, doctor):
    return make_user(email="frontdesk@procedures.com", role="NursingStation", organization_id=doctor.organization_id)


@pytest.fixture
def assigned_nurse(make_user, doctor):
    return make_user(email="nurse@procedures.com", role="Nurse", organization_id=doctor.organization_id)


@pytest.fixture
def unassigned_nurse(make_user, doctor):
    return make_user(email="unassigned@procedures.com", role="Nurse", organization_id=doctor.organization_id)


@pytest.fixture
def patient(doctor, assigned_nurse, db_session):
    p = Patient(name="Procedure Patient", age=50, gender="M", organization_id=doctor.organization_id,
                created_by=doctor.id, status="Active")
    db_session.add(p)
    db_session.flush()
    db_session.add(NurseAssignment(patient_id=p.id, nurse_id=assigned_nurse.id, assigned_by=doctor.id, status="Active"))
    db_session.commit()
    db_session.refresh(p)
    return p


def _make_consultation(db_session, doctor, case_id, **overrides):
    fields = dict(
        case_id=case_id, patient_name="Procedure Patient", organization_id=doctor.organization_id,
        user_id=doctor.id, chief_complaint="Follow-up needed", primary_diagnosis="Observation",
    )
    fields.update(overrides)
    c = Consultation(**fields)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


def _create_procedure(client, headers, **overrides):
    payload = {"procedure_name": "Wound Dressing"}
    payload.update(overrides)
    return client.post("/api/procedures", json=payload, headers=headers)


# ---------------------------------------------------------------------------
# Procedure Documentation -- creation
# ---------------------------------------------------------------------------

def test_doctor_can_document_procedure(client, doctor, patient, auth_headers):
    resp = _create_procedure(client, auth_headers(doctor), patient_id=patient.id, notes="Clean and redressed")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["procedure_name"] == "Wound Dressing"
    assert body["patient_id"] == patient.id
    assert body["performed_by"] == doctor.id
    assert body["notes"] == "Clean and redressed"


def test_non_doctor_cannot_document_procedure(client, head_nurse, nursing_station, assigned_nurse, patient, auth_headers):
    for user in (head_nurse, nursing_station, assigned_nurse):
        resp = _create_procedure(client, auth_headers(user), patient_id=patient.id)
        assert resp.status_code == 403


def test_procedure_requires_org_scoped_patient(client, doctor, auth_headers):
    resp = _create_procedure(client, auth_headers(doctor), patient_id=999999)
    assert resp.status_code == 404


def test_procedure_requires_org_scoped_consultation(client, doctor, db_session, auth_headers, make_user):
    foreign_doctor = make_user(email="foreign2.doc@other.com", role="Doctor")
    foreign_consult = _make_consultation(db_session, foreign_doctor, "foreign-case-1")
    resp = _create_procedure(client, auth_headers(doctor), consultation_id=foreign_consult.id)
    assert resp.status_code == 404


def test_procedure_can_link_to_consultation(client, doctor, patient, db_session, auth_headers):
    consult = _make_consultation(db_session, doctor, "case-proc-1", patient_id=patient.id)
    resp = _create_procedure(client, auth_headers(doctor), patient_id=patient.id, consultation_id=consult.id)
    assert resp.status_code == 201
    assert resp.json()["consultation_id"] == consult.id


# ---------------------------------------------------------------------------
# Procedure Documentation -- read access
# ---------------------------------------------------------------------------

def test_read_access_doctor_headnurse_nursingstation(client, doctor, head_nurse, nursing_station, patient, auth_headers):
    _create_procedure(client, auth_headers(doctor), patient_id=patient.id)
    for user in (doctor, head_nurse, nursing_station):
        resp = client.get("/api/procedures", params={"patient_id": patient.id}, headers=auth_headers(user))
        assert resp.status_code == 200
        assert len(resp.json()) == 1


def test_assigned_nurse_can_read_unassigned_nurse_cannot(client, doctor, assigned_nurse, unassigned_nurse, patient, auth_headers):
    _create_procedure(client, auth_headers(doctor), patient_id=patient.id)

    resp = client.get("/api/procedures", params={"patient_id": patient.id}, headers=auth_headers(assigned_nurse))
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp2 = client.get("/api/procedures", params={"patient_id": patient.id}, headers=auth_headers(unassigned_nurse))
    assert resp2.status_code == 403


def test_unassigned_nurse_without_patient_filter_denied(client, doctor, unassigned_nurse, patient, auth_headers):
    _create_procedure(client, auth_headers(doctor), patient_id=patient.id)
    resp = client.get("/api/procedures", headers=auth_headers(unassigned_nurse))
    assert resp.status_code == 403


def test_procedures_are_multi_tenant_isolated(client, doctor, patient, auth_headers, make_user):
    _create_procedure(client, auth_headers(doctor), patient_id=patient.id)
    foreign_doctor = make_user(email="foreign3.doc@other.com", role="Doctor")
    resp = client.get("/api/procedures", headers=auth_headers(foreign_doctor))
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Follow-up Consultation linking
# ---------------------------------------------------------------------------

def test_consultation_can_link_follow_up_of(client, doctor, db_session, auth_headers):
    prior = _make_consultation(db_session, doctor, "case-prior-1")
    resp = client.post("/api/consultations", json={
        "chief_complaint": "Recheck", "follow_up_of_id": prior.id,
    }, headers=auth_headers(doctor))
    assert resp.status_code == 200, resp.text
    assert resp.json()["follow_up_of_id"] == prior.id


def test_follow_up_of_id_must_be_same_org(client, doctor, db_session, auth_headers, make_user):
    foreign_doctor = make_user(email="foreign4.doc@other.com", role="Doctor")
    foreign_consult = _make_consultation(db_session, foreign_doctor, "foreign-case-2")
    resp = client.post("/api/consultations", json={
        "chief_complaint": "Recheck", "follow_up_of_id": foreign_consult.id,
    }, headers=auth_headers(doctor))
    assert resp.status_code == 404


def test_get_consultation_reports_reverse_follow_ups(client, doctor, db_session, auth_headers):
    prior = _make_consultation(db_session, doctor, "case-prior-2")
    resp = client.post("/api/consultations", json={
        "chief_complaint": "Recheck visit", "follow_up_of_id": prior.id,
    }, headers=auth_headers(doctor))
    assert resp.status_code == 200
    follow_up_id = resp.json()["id"]

    detail = client.get(f"/api/consultations/{prior.id}", headers=auth_headers(doctor))
    assert detail.status_code == 200
    body = detail.json()
    assert body["follow_up_of_id"] is None
    assert len(body["follow_ups"]) == 1
    assert body["follow_ups"][0]["id"] == follow_up_id


def test_appointment_can_link_follow_up_of_consultation(client, nursing_station, doctor, patient, db_session, auth_headers):
    from datetime import datetime, timedelta

    prior = _make_consultation(db_session, doctor, "case-prior-3", patient_id=patient.id)
    resp = client.post("/api/appointments", json={
        "patient_id": patient.id, "doctor_id": doctor.id,
        "scheduled_at": (datetime.utcnow() + timedelta(days=7)).isoformat(),
        "follow_up_of_consultation_id": prior.id,
    }, headers=auth_headers(nursing_station))
    assert resp.status_code == 201, resp.text
    assert resp.json()["follow_up_of_consultation_id"] == prior.id


def test_appointment_follow_up_of_consultation_must_be_same_org(client, nursing_station, doctor, patient, auth_headers, make_user):
    from datetime import datetime, timedelta

    foreign_doctor = make_user(email="foreign5.doc@other.com", role="Doctor")
    resp = client.post("/api/appointments", json={
        "patient_id": patient.id, "doctor_id": doctor.id,
        "scheduled_at": (datetime.utcnow() + timedelta(days=7)).isoformat(),
        "follow_up_of_consultation_id": 999999,
    }, headers=auth_headers(nursing_station))
    assert resp.status_code == 404
