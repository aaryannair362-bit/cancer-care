"""
Medication Administration Record (MAR) -- Nursing Workflows. Mirrors the access-control shape
of test_nursing_charting.py and test_nursing_assessments.py.
"""
import pytest


@pytest.fixture
def head_nurse(make_user):
    return make_user(email="head@marhosp.com", role="HeadNurse")


@pytest.fixture
def nurse(make_user, head_nurse):
    return make_user(email="nurse@marhosp.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def unassigned_nurse(make_user, head_nurse):
    return make_user(email="unassigned@marhosp.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def doctor(make_user, head_nurse):
    return make_user(email="doctor@marhosp.com", role="Doctor", organization_id=head_nurse.organization_id)


@pytest.fixture
def patient(head_nurse, nurse, db_session):
    from app.models import NurseAssignment, Patient

    p = Patient(name="MAR Patient", age=45, gender="M", ward="Medical", bed="7",
                organization_id=head_nurse.organization_id, created_by=head_nurse.id, status="Active")
    db_session.add(p)
    db_session.flush()
    db_session.add(NurseAssignment(patient_id=p.id, nurse_id=nurse.id, assigned_by=head_nurse.id, status="Active"))
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def task(patient, nurse, head_nurse, db_session):
    from app.models import Task

    t = Task(patient_id=patient.id, nurse_id=nurse.id, assigned_by=head_nurse.id,
              description="Administer: Paracetamol 500mg", task_type="Medication", source="Auto")
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


def test_record_medication_administration(client, nurse, patient, auth_headers):
    resp = client.post("/api/ipd/medication-administrations", json={
        "patient_id": patient.id, "drug_name": "Paracetamol", "dose": "500mg", "route": "Oral", "status": "Given",
    }, headers=auth_headers(nurse))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["drug_name"] == "Paracetamol"
    assert body["status"] == "Given"
    assert body["task_id"] is None


def test_record_medication_administration_linked_to_task(client, nurse, patient, task, auth_headers):
    resp = client.post("/api/ipd/medication-administrations", json={
        "patient_id": patient.id, "task_id": task.id, "drug_name": "Paracetamol", "status": "Refused",
        "notes": "Patient declined",
    }, headers=auth_headers(nurse))
    assert resp.status_code == 201, resp.text
    assert resp.json()["task_id"] == task.id
    assert resp.json()["status"] == "Refused"


def test_task_id_from_another_patient_rejected(client, nurse, patient, task, auth_headers, make_user, head_nurse, db_session):
    from app.models import NurseAssignment, Patient

    other_patient = Patient(name="Other Patient", organization_id=head_nurse.organization_id,
                             created_by=head_nurse.id, status="Active")
    db_session.add(other_patient)
    db_session.flush()
    db_session.add(NurseAssignment(patient_id=other_patient.id, nurse_id=nurse.id, assigned_by=head_nurse.id, status="Active"))
    db_session.commit()
    db_session.refresh(other_patient)

    resp = client.post("/api/ipd/medication-administrations", json={
        "patient_id": other_patient.id, "task_id": task.id, "drug_name": "Paracetamol",
    }, headers=auth_headers(nurse))
    assert resp.status_code == 404


def test_invalid_status_rejected(client, nurse, patient, auth_headers):
    resp = client.post("/api/ipd/medication-administrations", json={
        "patient_id": patient.id, "drug_name": "Paracetamol", "status": "Eaten",
    }, headers=auth_headers(nurse))
    assert resp.status_code == 422


def test_list_medication_administrations_most_recent_first(client, nurse, patient, auth_headers):
    headers = auth_headers(nurse)
    client.post("/api/ipd/medication-administrations", json={"patient_id": patient.id, "drug_name": "Amoxicillin"}, headers=headers)
    client.post("/api/ipd/medication-administrations", json={"patient_id": patient.id, "drug_name": "Ibuprofen"}, headers=headers)

    resp = client.get("/api/ipd/medication-administrations", params={"patient_id": patient.id}, headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["drug_name"] == "Ibuprofen"


def test_unassigned_nurse_cannot_record(client, unassigned_nurse, patient, auth_headers):
    resp = client.post("/api/ipd/medication-administrations", json={"patient_id": patient.id, "drug_name": "X"}, headers=auth_headers(unassigned_nurse))
    assert resp.status_code == 403


def test_doctor_cannot_record(client, doctor, patient, auth_headers):
    resp = client.post("/api/ipd/medication-administrations", json={"patient_id": patient.id, "drug_name": "X"}, headers=auth_headers(doctor))
    assert resp.status_code == 403


def test_cannot_record_for_another_orgs_patient(client, nurse, auth_headers, make_user, db_session):
    from app.models import Patient

    foreign_head = make_user(email="foreign.head@othermar.com", role="HeadNurse")
    foreign_patient = Patient(name="Foreign", organization_id=foreign_head.organization_id,
                               created_by=foreign_head.id, status="Active")
    db_session.add(foreign_patient)
    db_session.commit()
    db_session.refresh(foreign_patient)

    resp = client.post("/api/ipd/medication-administrations", json={"patient_id": foreign_patient.id, "drug_name": "X"}, headers=auth_headers(nurse))
    assert resp.status_code == 404
