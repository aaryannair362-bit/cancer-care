"""
Nursing Workflows gap closure: Admission Assessment, Pain Assessment, Fall Risk Assessment
(Morse Fall Scale), Pressure Ulcer Assessment (Braden Scale). Mirrors the access-control shape
of test_nursing_charting.py (Nurse must be assigned, HeadNurse can act on any patient, Doctor
cannot chart).
"""
import pytest


@pytest.fixture
def head_nurse(make_user):
    return make_user(email="head@assesshosp.com", role="HeadNurse")


@pytest.fixture
def nurse(make_user, head_nurse):
    return make_user(email="nurse@assesshosp.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def unassigned_nurse(make_user, head_nurse):
    return make_user(email="unassigned@assesshosp.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def doctor(make_user, head_nurse):
    return make_user(email="doctor@assesshosp.com", role="Doctor", organization_id=head_nurse.organization_id)


@pytest.fixture
def patient(head_nurse, nurse, db_session):
    from app.models import NurseAssignment, Patient

    p = Patient(name="Assess Patient", age=60, gender="F", ward="Medical", bed="3",
                organization_id=head_nurse.organization_id, created_by=head_nurse.id, status="Active")
    db_session.add(p)
    db_session.flush()
    db_session.add(NurseAssignment(patient_id=p.id, nurse_id=nurse.id, assigned_by=head_nurse.id, status="Active"))
    db_session.commit()
    db_session.refresh(p)
    return p


# ---------------------------------------------------------------------------
# Admission Assessment -- one per admission
# ---------------------------------------------------------------------------

def test_create_and_get_admission_assessment(client, nurse, patient, auth_headers):
    headers = auth_headers(nurse)
    resp = client.post("/api/ipd/admission-assessments", json={
        "patient_id": patient.id, "presenting_complaint": "Shortness of breath",
        "known_allergies": "None known", "functional_status": "Independent",
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["presenting_complaint"] == "Shortness of breath"

    resp = client.get(f"/api/ipd/admission-assessments/{patient.id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["functional_status"] == "Independent"


def test_second_admission_assessment_rejected(client, nurse, patient, auth_headers):
    headers = auth_headers(nurse)
    client.post("/api/ipd/admission-assessments", json={"patient_id": patient.id}, headers=headers)
    resp = client.post("/api/ipd/admission-assessments", json={"patient_id": patient.id}, headers=headers)
    assert resp.status_code == 400


def test_get_admission_assessment_missing_returns_404(client, nurse, patient, auth_headers):
    resp = client.get(f"/api/ipd/admission-assessments/{patient.id}", headers=auth_headers(nurse))
    assert resp.status_code == 404


def test_unassigned_nurse_cannot_create_admission_assessment(client, unassigned_nurse, patient, auth_headers):
    resp = client.post("/api/ipd/admission-assessments", json={"patient_id": patient.id}, headers=auth_headers(unassigned_nurse))
    assert resp.status_code == 403


def test_doctor_cannot_create_admission_assessment(client, doctor, patient, auth_headers):
    resp = client.post("/api/ipd/admission-assessments", json={"patient_id": patient.id}, headers=auth_headers(doctor))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Pain Assessment
# ---------------------------------------------------------------------------

def test_record_and_list_pain_assessments(client, nurse, patient, auth_headers):
    headers = auth_headers(nurse)
    resp = client.post("/api/ipd/pain-assessments", json={
        "patient_id": patient.id, "pain_score": 7, "location": "Lower back", "character": "Throbbing",
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["pain_score"] == 7

    resp = client.get("/api/ipd/pain-assessments", params={"patient_id": patient.id}, headers=headers)
    assert len(resp.json()) == 1


def test_pain_score_out_of_range_rejected(client, nurse, patient, auth_headers):
    resp = client.post("/api/ipd/pain-assessments", json={"patient_id": patient.id, "pain_score": 11}, headers=auth_headers(nurse))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Fall Risk Assessment -- Morse Fall Scale scoring
# ---------------------------------------------------------------------------

def test_fall_risk_score_and_level_low(client, nurse, patient, auth_headers):
    resp = client.post("/api/ipd/fall-risk-assessments", json={
        "patient_id": patient.id, "history_of_falling": False, "secondary_diagnosis": False,
        "ambulatory_aid": "None", "iv_therapy": False, "gait": "Normal", "mental_status": "OrientedToOwnAbility",
    }, headers=auth_headers(nurse))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["risk_score"] == 0
    assert body["risk_level"] == "Low"


def test_fall_risk_score_and_level_high(client, nurse, patient, auth_headers):
    resp = client.post("/api/ipd/fall-risk-assessments", json={
        "patient_id": patient.id, "history_of_falling": True, "secondary_diagnosis": True,
        "ambulatory_aid": "Furniture", "iv_therapy": True, "gait": "Impaired", "mental_status": "OverestimatesForgets",
    }, headers=auth_headers(nurse))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # 25 + 15 + 30 + 20 + 20 + 15 = 125
    assert body["risk_score"] == 125
    assert body["risk_level"] == "High"


def test_fall_risk_invalid_ambulatory_aid_rejected(client, nurse, patient, auth_headers):
    resp = client.post("/api/ipd/fall-risk-assessments", json={"patient_id": patient.id, "ambulatory_aid": "Rollerblades"}, headers=auth_headers(nurse))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Pressure Ulcer Assessment -- Braden Scale scoring
# ---------------------------------------------------------------------------

def test_pressure_ulcer_score_and_level_severe(client, nurse, patient, auth_headers):
    resp = client.post("/api/ipd/pressure-ulcer-assessments", json={
        "patient_id": patient.id, "sensory_perception": 1, "moisture": 1, "activity": 1,
        "mobility": 1, "nutrition": 1, "friction_shear": 1,
    }, headers=auth_headers(nurse))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["risk_score"] == 6
    assert body["risk_level"] == "Severe"


def test_pressure_ulcer_score_and_level_none(client, nurse, patient, auth_headers):
    resp = client.post("/api/ipd/pressure-ulcer-assessments", json={
        "patient_id": patient.id, "sensory_perception": 4, "moisture": 4, "activity": 4,
        "mobility": 4, "nutrition": 4, "friction_shear": 3,
    }, headers=auth_headers(nurse))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["risk_score"] == 23
    assert body["risk_level"] == "None"


def test_pressure_ulcer_friction_shear_out_of_range_rejected(client, nurse, patient, auth_headers):
    resp = client.post("/api/ipd/pressure-ulcer-assessments", json={
        "patient_id": patient.id, "sensory_perception": 2, "moisture": 2, "activity": 2,
        "mobility": 2, "nutrition": 2, "friction_shear": 4,
    }, headers=auth_headers(nurse))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------

def test_cannot_assess_another_orgs_patient(client, nurse, auth_headers, make_user, db_session):
    from app.models import Patient

    foreign_head = make_user(email="foreign.head@otherassess.com", role="HeadNurse")
    foreign_patient = Patient(name="Foreign", age=40, gender="M", organization_id=foreign_head.organization_id,
                               created_by=foreign_head.id, status="Active")
    db_session.add(foreign_patient)
    db_session.commit()
    db_session.refresh(foreign_patient)

    resp = client.post("/api/ipd/pain-assessments", json={"patient_id": foreign_patient.id, "pain_score": 5}, headers=auth_headers(nurse))
    assert resp.status_code == 404
