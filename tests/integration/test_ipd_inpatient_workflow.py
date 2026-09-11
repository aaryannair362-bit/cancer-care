"""
Oncology Review Results PDF items 14, 17, 18 -- Medication Administration Record timestamps,
Ward Round consultant-review tagging, and the nurse Raise Request (lab) workflow.
"""
import pytest


@pytest.fixture
def head_nurse(make_user):
    return make_user(email="head@ipd-inpatient.com", role="HeadNurse")


@pytest.fixture
def nurse(make_user, head_nurse):
    return make_user(email="nurse@ipd-inpatient.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def other_nurse(make_user, head_nurse):
    return make_user(email="other-nurse@ipd-inpatient.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def doctor(make_user, head_nurse):
    return make_user(email="doctor@ipd-inpatient.com", role="Doctor", organization_id=head_nurse.organization_id)


@pytest.fixture
def patient_id(client, head_nurse, auth_headers):
    resp = client.post(
        "/api/ipd/patients", json={"name": "Inpatient Workflow Test Patient", "ward": "General", "bed": "B1"},
        headers=auth_headers(head_nurse),
    )
    assert resp.status_code == 200
    return resp.json()["id"]


@pytest.fixture
def assigned_patient_id(client, head_nurse, nurse, auth_headers, patient_id):
    resp = client.post("/api/ipd/assign", json={"patient_id": patient_id, "nurse_id": nurse.id}, headers=auth_headers(head_nurse))
    assert resp.status_code == 200
    return patient_id


# ---------------------------------------------------------------------------
# Item 14: Medication Administration Record (real per-dose timestamp)
# ---------------------------------------------------------------------------

def test_mar_records_timestamped_administration(client, auth_headers, nurse, assigned_patient_id):
    resp = client.post("/api/ipd/medication-administrations", headers=auth_headers(nurse), json={
        "patient_id": assigned_patient_id, "drug_name": "Paracetamol", "dose": "500mg", "route": "Oral", "status": "Given",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["drug_name"] == "Paracetamol"
    assert body["status"] == "Given"
    assert body["administered_at"]

    listed = client.get(f"/api/ipd/medication-administrations?patient_id={assigned_patient_id}", headers=auth_headers(nurse)).json()
    assert len(listed) == 1
    assert listed[0]["drug_name"] == "Paracetamol"


def test_mar_requires_nurse_assignment(client, auth_headers, other_nurse, assigned_patient_id):
    """assigned_patient_id is assigned to `nurse`, not `other_nurse`."""
    resp = client.post("/api/ipd/medication-administrations", headers=auth_headers(other_nurse), json={
        "patient_id": assigned_patient_id, "drug_name": "Paracetamol", "status": "Given",
    })
    assert resp.status_code == 403


def test_mar_rejects_invalid_status(client, auth_headers, nurse, assigned_patient_id):
    resp = client.post("/api/ipd/medication-administrations", headers=auth_headers(nurse), json={
        "patient_id": assigned_patient_id, "drug_name": "Paracetamol", "status": "MadeUpStatus",
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Item 17: Ward Round -- distinct Consultant Review vs. routine Clinician Round
# ---------------------------------------------------------------------------

def _round_payload(patient_id, **overrides):
    payload = {
        "patient_id": patient_id, "chief_complaint": "Stable post-op day 2",
        "hpi": "No new complaints overnight.", "objective_findings": "Afebrile, wound clean and dry.",
        "primary_diagnosis": "Post-operative recovery, uncomplicated",
    }
    payload.update(overrides)
    return payload


def test_ward_round_defaults_to_clinician_round_visit_type(client, auth_headers, doctor, patient_id):
    resp = client.post("/api/ipd/rounds", headers=auth_headers(doctor), json=_round_payload(patient_id))
    assert resp.status_code == 200, resp.text
    consultation_id = resp.json()["id"]

    details = client.get(f"/api/patients/{patient_id}/details", headers=auth_headers(doctor)).json()
    match = next(c for c in details["consultations"] if c["id"] == consultation_id)
    assert match["visit_type"] == "IPD_ROUND"


def test_ward_round_consultant_review_gets_distinct_visit_type(client, auth_headers, doctor, patient_id):
    resp = client.post("/api/ipd/rounds", headers=auth_headers(doctor), json=_round_payload(patient_id, is_consultant_review=True))
    assert resp.status_code == 200, resp.text
    consultation_id = resp.json()["id"]

    details = client.get(f"/api/patients/{patient_id}/details", headers=auth_headers(doctor)).json()
    match = next(c for c in details["consultations"] if c["id"] == consultation_id)
    assert match["visit_type"] == "IPD_CONSULTANT"
    # Never merged with the routine round type.
    assert match["visit_type"] != "IPD_ROUND"


def test_ward_round_requires_chief_complaint(client, auth_headers, doctor, patient_id):
    resp = client.post("/api/ipd/rounds", headers=auth_headers(doctor), json={"patient_id": patient_id})
    assert resp.status_code == 400


def test_ward_round_only_doctor(client, auth_headers, nurse, patient_id):
    resp = client.post("/api/ipd/rounds", headers=auth_headers(nurse), json=_round_payload(patient_id))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Item 18: Raise Request for Nurses (lab-related, open to Nurse AND HeadNurse)
# ---------------------------------------------------------------------------

def test_nurse_can_raise_lab_request_not_just_head_nurse(client, auth_headers, nurse, assigned_patient_id):
    """The general POST /api/ipd/tasks is deliberately HeadNurse-only -- this new endpoint is
    the actual gap item 18 describes: a plain floor Nurse must be able to raise one too."""
    resp = client.post("/api/ipd/lab-requests", headers=auth_headers(nurse), json={
        "patient_id": assigned_patient_id, "request_type": "Blood Sample", "details": "CBC, LFT pre-chemo clearance",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["task_type"] == "Lab"
    assert body["status"] == "Pending"
    assert "Blood Sample" in body["description"]

    tasks = client.get("/api/ipd/tasks", headers=auth_headers(nurse)).json()
    lab_tasks = [t for t in tasks if t["task_type"] == "Lab"]
    assert len(lab_tasks) == 1
    assert lab_tasks[0]["id"] == body["id"]


def test_head_nurse_can_also_raise_lab_request(client, auth_headers, head_nurse, patient_id):
    resp = client.post("/api/ipd/lab-requests", headers=auth_headers(head_nurse), json={
        "patient_id": patient_id, "request_type": "Urine Sample",
    })
    assert resp.status_code == 201, resp.text


def test_lab_request_requires_patient_id_and_request_type(client, auth_headers, nurse, assigned_patient_id):
    missing_type = client.post("/api/ipd/lab-requests", headers=auth_headers(nurse), json={"patient_id": assigned_patient_id})
    assert missing_type.status_code == 400

    missing_patient = client.post("/api/ipd/lab-requests", headers=auth_headers(nurse), json={"request_type": "Blood Sample"})
    assert missing_patient.status_code == 400


def test_doctor_cannot_raise_lab_request(client, auth_headers, doctor, patient_id):
    resp = client.post("/api/ipd/lab-requests", headers=auth_headers(doctor), json={
        "patient_id": patient_id, "request_type": "Blood Sample",
    })
    assert resp.status_code == 403


def test_nurse_can_update_status_of_own_raised_request(client, auth_headers, nurse, assigned_patient_id):
    """Reuses the existing PATCH /api/ipd/tasks/{id} unchanged -- a nurse may already update a
    task assigned to themselves, which self-assignment at creation time satisfies."""
    created = client.post("/api/ipd/lab-requests", headers=auth_headers(nurse), json={
        "patient_id": assigned_patient_id, "request_type": "Blood Sample",
    }).json()

    updated = client.patch(f"/api/ipd/tasks/{created['id']}", headers=auth_headers(nurse), json={"status": "Completed"})
    assert updated.status_code == 200

    tasks = client.get("/api/ipd/tasks", headers=auth_headers(nurse)).json()
    match = next(t for t in tasks if t["id"] == created["id"])
    assert match["status"] == "Completed"
