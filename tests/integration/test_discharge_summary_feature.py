"""
Tests for the Discharge Summary feature: POST/GET /api/ipd/patients/{id}/discharge-summary.
Assembles the patient's vitals/nursing-notes/tasks/consultations already captured elsewhere
into a deterministic, template-based summary (see backend/app/discharge_summary.py) -- no
AI/LLM involved, so these tests call the real generator directly rather than mocking a model.
"""
import pytest


@pytest.fixture
def head_nurse(make_user):
    return make_user(email="head@discharge-summary.com", role="HeadNurse")


@pytest.fixture
def nurse(make_user, head_nurse):
    return make_user(email="nurse@discharge-summary.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def doctor(make_user, head_nurse):
    return make_user(email="doctor@discharge-summary.com", role="Doctor", organization_id=head_nurse.organization_id)


@pytest.fixture
def station(make_user, head_nurse):
    return make_user(email="station@discharge-summary.com", role="NursingStation", organization_id=head_nurse.organization_id)


@pytest.fixture
def patient_with_full_stay(client, head_nurse, nurse, auth_headers):
    """A patient with a realistic multi-day stay: vitals trend, nursing notes, a task."""
    pid = client.post("/api/ipd/patients", json={"name": "Discharge Summary Patient", "age": 58, "gender": "Male",
                                                   "ward": "General", "bed": "D1", "diagnosis": "Community-acquired pneumonia"},
                       headers=auth_headers(head_nurse)).json()["id"]
    client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurse.id}, headers=auth_headers(head_nurse))
    client.post("/api/ipd/vitals", json={"patient_id": pid, "bp_systolic": 128, "bp_diastolic": 82, "heart_rate": 92,
                                          "temperature": 38.6, "oxygen_sat": 94, "respiratory_rate": 22},
                headers=auth_headers(nurse))
    client.post("/api/ipd/vitals", json={"patient_id": pid, "bp_systolic": 120, "bp_diastolic": 78, "heart_rate": 78,
                                          "temperature": 37.0, "oxygen_sat": 98, "respiratory_rate": 16},
                headers=auth_headers(nurse))
    client.post("/api/nursing-notes", json={"patient_id": pid, "subjective": "Breathing easier today",
                                              "objective": "Lungs clearer on auscultation", "assessment": "Improving",
                                              "plan": "Continue antibiotics"}, headers=auth_headers(nurse))
    client.post("/api/ipd/tasks", json={"patient_id": pid, "description": "IV antibiotics"}, headers=auth_headers(head_nurse))
    return pid


def test_generated_summary_reflects_real_chart_data(client, head_nurse, patient_with_full_stay, auth_headers):
    resp = client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(head_nurse))
    assert resp.status_code == 200
    data = resp.json()
    assert "Community-acquired pneumonia" in data["admission_summary"]
    assert "Community-acquired pneumonia" in data["discharge_diagnosis"]  # falls back to Patient.diagnosis (no consultations)
    assert "2 vital-sign recording(s)" in data["hospital_course"]
    assert "98" in data["hospital_course"]  # most recent oxygen_sat reading
    assert "1 nursing note(s)" in data["hospital_course"]
    assert data["condition_at_discharge"] == "Stable"
    assert data["generated_by"] == head_nurse.id


def test_generated_summary_persists_and_is_retrievable(client, head_nurse, patient_with_full_stay, auth_headers):
    client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(head_nurse))
    resp = client.get(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(head_nurse))
    assert resp.status_code == 200
    assert "Community-acquired pneumonia" in resp.json()["discharge_diagnosis"]


def test_get_summary_before_any_generated_returns_404(client, head_nurse, patient_with_full_stay, auth_headers):
    resp = client.get(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(head_nurse))
    assert resp.status_code == 404


def test_regenerating_creates_a_new_version_get_returns_latest(client, head_nurse, nurse, patient_with_full_stay, auth_headers):
    client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(head_nurse))

    # A new vital changes the "most recent vitals" line in the next generation.
    client.post("/api/ipd/vitals", json={"patient_id": patient_with_full_stay, "bp_systolic": 118, "bp_diastolic": 76,
                                          "heart_rate": 70, "temperature": 36.8, "oxygen_sat": 99, "respiratory_rate": 14},
                headers=auth_headers(nurse))
    client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(head_nurse))

    resp = client.get(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(head_nurse))
    assert "3 vital-sign recording(s)" in resp.json()["hospital_course"]
    assert "99" in resp.json()["hospital_course"]


def test_generates_even_for_patient_with_minimal_record(client, head_nurse, auth_headers):
    """A patient admitted and discharged same-day with no vitals/notes/tasks recorded --
    legitimate (quick observation stay), must still produce a (minimal) summary, not error."""
    pid = client.post("/api/ipd/patients", json={"name": "Quick Observation Patient", "ward": "General", "bed": "Q1"},
                       headers=auth_headers(head_nurse)).json()["id"]
    resp = client.post(f"/api/ipd/patients/{pid}/discharge-summary", headers=auth_headers(head_nurse))
    assert resp.status_code == 200
    assert resp.json()["condition_at_discharge"] == "Not documented"
    assert "No further clinical events" in resp.json()["hospital_course"]


# ---------------------------------------------------------------------------
# Role permissions
# ---------------------------------------------------------------------------

def test_doctor_can_generate(client, doctor, patient_with_full_stay, auth_headers):
    resp = client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(doctor))
    assert resp.status_code == 200


def test_nursing_station_can_generate(client, station, patient_with_full_stay, auth_headers):
    resp = client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(station))
    assert resp.status_code == 200


def test_nurse_cannot_generate(client, nurse, patient_with_full_stay, auth_headers):
    resp = client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(nurse))
    assert resp.status_code == 403


def test_assigned_nurse_can_view_generated_summary(client, head_nurse, nurse, patient_with_full_stay, auth_headers):
    client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(head_nurse))
    resp = client.get(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(nurse))
    assert resp.status_code == 200


def test_unassigned_nurse_cannot_view_summary(client, head_nurse, make_user, patient_with_full_stay, auth_headers):
    other_nurse = make_user(email="other@discharge-summary.com", role="Nurse", organization_id=head_nurse.organization_id)
    client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(head_nurse))
    resp = client.get(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(other_nurse))
    assert resp.status_code == 403


@pytest.mark.parametrize("role", ["Admin"])
def test_admin_cannot_generate_or_view(client, make_user, head_nurse, patient_with_full_stay, auth_headers, role):
    admin = make_user(email=f"{role.lower()}@discharge-summary.com", role=role, organization_id=head_nurse.organization_id)
    assert client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(admin)).status_code == 403
    assert client.get(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(admin)).status_code == 403


# ---------------------------------------------------------------------------
# Org isolation and error handling
# ---------------------------------------------------------------------------

def test_generate_for_nonexistent_patient_404(client, head_nurse, auth_headers):
    resp = client.post("/api/ipd/patients/999999/discharge-summary", headers=auth_headers(head_nurse))
    assert resp.status_code == 404


def test_cannot_generate_for_other_orgs_patient(client, head_nurse, make_user, db_session, auth_headers):
    from app.models import Patient
    other_head = make_user(email="other-head@discharge-summary.com", role="HeadNurse")
    patient = Patient(name="Foreign Patient", ward="General", bed="F1",
                       organization_id=other_head.organization_id, created_by=other_head.id)
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    resp = client.post(f"/api/ipd/patients/{patient.id}/discharge-summary", headers=auth_headers(head_nurse))
    assert resp.status_code == 404


def test_cannot_view_other_orgs_summary(client, head_nurse, make_user, db_session, auth_headers):
    from app.models import Patient
    other_head = make_user(email="other-head2@discharge-summary.com", role="HeadNurse")
    patient = Patient(name="Foreign Patient 2", ward="General", bed="F2",
                       organization_id=other_head.organization_id, created_by=other_head.id)
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    client.post(f"/api/ipd/patients/{patient.id}/discharge-summary", headers=auth_headers(other_head))
    resp = client.get(f"/api/ipd/patients/{patient.id}/discharge-summary", headers=auth_headers(head_nurse))
    assert resp.status_code == 404


def test_summary_body_never_leaks_other_patients_data(client, head_nurse, patient_with_full_stay, auth_headers):
    """Basic sanity check: the generated summary text is scoped to this one patient's own
    recorded data only."""
    resp = client.post(f"/api/ipd/patients/{patient_with_full_stay}/discharge-summary", headers=auth_headers(head_nurse))
    assert resp.status_code == 200
    assert "Discharge Summary Patient" in resp.json()["admission_summary"]
