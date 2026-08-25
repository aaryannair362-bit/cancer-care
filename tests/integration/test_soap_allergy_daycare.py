"""
Closes three Basic HMS.pdf gaps in one file since they touch the same consultation/admission
endpoints:
  - SOAP Notes (Clinical Workflows): Consultation.objective_findings, the "Objective" section
    that had no field at all before this pass.
  - Clinical Decision Support (Clinical Workflows): allergy-conflict checking
    (tasks_engine.check_allergy_conflicts) against Patient.allergies, computed the same way
    interaction_warnings already is.
  - Day Care Admission (Patient Administration): Patient.admission_type.
"""
import pytest

from app.models import Patient


@pytest.fixture
def doctor(make_user):
    return make_user(email="doctor@soaphosp.com", role="Doctor")


@pytest.fixture
def head_nurse(make_user, doctor):
    return make_user(email="head@soaphosp.com", role="HeadNurse", organization_id=doctor.organization_id)


@pytest.fixture
def allergic_patient(doctor, db_session):
    p = Patient(name="Allergic Patient", age=30, gender="F", organization_id=doctor.organization_id,
                created_by=doctor.id, status="Active", allergies=["Penicillin"])
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


# ---------------------------------------------------------------------------
# SOAP Notes -- objective_findings
# ---------------------------------------------------------------------------

def test_objective_findings_persisted_on_create(client, doctor, auth_headers):
    resp = client.post("/api/consultations", json={
        "chief_complaint": "Cough", "hpi": "3 days", "objective_findings": "Crepitations in right lower lobe",
    }, headers=auth_headers(doctor))
    assert resp.status_code == 200, resp.text
    assert resp.json()["objective_findings"] == "Crepitations in right lower lobe"

    consult_id = resp.json()["id"]
    detail = client.get(f"/api/consultations/{consult_id}", headers=auth_headers(doctor)).json()
    assert detail["objective_findings"] == "Crepitations in right lower lobe"


def test_objective_findings_settable_at_finalize(client, doctor, auth_headers):
    created = client.post("/api/consultations", json={"chief_complaint": "Fever"}, headers=auth_headers(doctor)).json()
    resp = client.patch(f"/api/consultations/{created['id']}/finalize",
                         json={"objectiveFindings": "Temp 38.9C, tachycardic"}, headers=auth_headers(doctor))
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/api/consultations/{created['id']}", headers=auth_headers(doctor)).json()
    assert detail["objective_findings"] == "Temp 38.9C, tachycardic"


# ---------------------------------------------------------------------------
# Clinical Decision Support -- allergy conflict checking
# ---------------------------------------------------------------------------

def test_allergy_conflict_flagged_on_create(client, doctor, allergic_patient, auth_headers):
    resp = client.post("/api/consultations", json={
        "patient_id": allergic_patient.id, "chief_complaint": "Sore throat",
        "medications": [{"drugName": "Penicillin V", "dose": "500mg"}],
    }, headers=auth_headers(doctor))
    assert resp.status_code == 200, resp.text
    warnings = resp.json()["allergy_warnings"]
    assert len(warnings) == 1
    assert warnings[0]["allergy"] == "Penicillin"
    assert warnings[0]["drug"] == "Penicillin V"


def test_no_allergy_conflict_for_unrelated_drug(client, doctor, allergic_patient, auth_headers):
    resp = client.post("/api/consultations", json={
        "patient_id": allergic_patient.id, "chief_complaint": "Headache",
        "medications": [{"drugName": "Paracetamol", "dose": "500mg"}],
    }, headers=auth_headers(doctor))
    assert resp.status_code == 200, resp.text
    assert resp.json()["allergy_warnings"] == []


def test_allergy_conflict_recomputed_and_persisted_on_finalize(client, doctor, allergic_patient, auth_headers):
    created = client.post("/api/consultations", json={
        "patient_id": allergic_patient.id, "chief_complaint": "Ear infection",
        "medications": [{"drugName": "Amoxicillin-Penicillin combo"}],
    }, headers=auth_headers(doctor)).json()

    resp = client.patch(f"/api/consultations/{created['id']}/finalize", json={}, headers=auth_headers(doctor))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["allergy_warnings"]) == 1

    detail = client.get(f"/api/consultations/{created['id']}", headers=auth_headers(doctor)).json()
    assert len(detail["allergy_warnings"]) == 1


def test_no_allergy_warnings_when_patient_has_no_allergies_on_file(client, doctor, db_session, auth_headers):
    patient = Patient(name="No Allergy Patient", organization_id=doctor.organization_id,
                       created_by=doctor.id, status="Active")
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)

    resp = client.post("/api/consultations", json={
        "patient_id": patient.id, "chief_complaint": "Cold",
        "medications": [{"drugName": "Penicillin V"}],
    }, headers=auth_headers(doctor))
    assert resp.status_code == 200
    assert resp.json()["allergy_warnings"] == []


# ---------------------------------------------------------------------------
# Day Care Admission
# ---------------------------------------------------------------------------

def test_admission_defaults_to_ipd(client, head_nurse, auth_headers):
    resp = client.post("/api/ipd/patients", json={"name": "Default Admit", "ward": "General"}, headers=auth_headers(head_nurse))
    assert resp.status_code == 200, resp.text
    assert resp.json()["admission_type"] == "IPD"


def test_day_care_admission(client, head_nurse, auth_headers, db_session):
    resp = client.post("/api/ipd/patients", json={
        "name": "Day Care Admit", "ward": "General", "admission_type": "DayCare",
        "allergies": ["Latex"],
    }, headers=auth_headers(head_nurse))
    assert resp.status_code == 200, resp.text
    assert resp.json()["admission_type"] == "DayCare"

    patient = db_session.query(Patient).filter(Patient.id == resp.json()["id"]).first()
    assert patient.admission_type == "DayCare"
    assert patient.allergies == ["Latex"]


def test_invalid_admission_type_rejected(client, head_nurse, auth_headers):
    resp = client.post("/api/ipd/patients", json={"name": "X", "ward": "General", "admission_type": "Outpatient"}, headers=auth_headers(head_nurse))
    assert resp.status_code == 400


def test_allergies_must_be_a_list(client, head_nurse, auth_headers):
    resp = client.post("/api/ipd/patients", json={"name": "X", "ward": "General", "allergies": "Penicillin"}, headers=auth_headers(head_nurse))
    assert resp.status_code == 400


def test_update_patient_allergies(client, head_nurse, auth_headers, db_session):
    admit = client.post("/api/ipd/patients", json={"name": "Update Allergy", "ward": "General"}, headers=auth_headers(head_nurse)).json()
    resp = client.put(f"/api/patients/{admit['id']}", json={"allergies": ["Iodine", "Shellfish"]}, headers=auth_headers(head_nurse))
    assert resp.status_code == 200, resp.text

    patient = db_session.query(Patient).filter(Patient.id == admit["id"]).first()
    assert patient.allergies == ["Iodine", "Shellfish"]
