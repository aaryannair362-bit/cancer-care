"""
The auto-task pipeline: a doctor's consultation (OPD or IPD ward round) should spawn the
nurse's tasks with zero manual task-creation, those tasks should land on whichever nurse is
actually assigned to the patient (so they can act on them, not just a HeadNurse), and the
drug-interaction check should see the patient's whole active regimen -- not just what's new
today. Consultations are plain structured-form entries now (no voice/AI, see main.py's
create_consultation docstring); drug interactions are a static curated lookup
(tasks_engine.KNOWN_INTERACTIONS) -- Warfarin/Aspirin is one of the pairs in that table, used
below as a real, deterministic interaction rather than a mocked one.
"""
import pytest


@pytest.fixture
def doctor(make_user):
    return make_user(email="doctor@auto-task.com", role="Doctor")


@pytest.fixture
def head_nurse(make_user, doctor):
    return make_user(email="head@auto-task.com", role="HeadNurse", organization_id=doctor.organization_id)


@pytest.fixture
def nurse(make_user, doctor):
    return make_user(email="nurse@auto-task.com", role="Nurse", organization_id=doctor.organization_id)


@pytest.fixture
def nursing_station(make_user, doctor):
    return make_user(email="station@auto-task.com", role="NursingStation", organization_id=doctor.organization_id)


@pytest.fixture
def admitted_patient(client, nursing_station, head_nurse, nurse, auth_headers):
    resp = client.post("/api/ipd/patients", headers=auth_headers(nursing_station),
                        json={"name": "Auto Task Patient", "age": 50, "gender": "Female", "ward": "General", "bed": "B1"})
    patient_id = resp.json()["id"]
    client.post("/api/ipd/assign", headers=auth_headers(head_nurse),
                json={"patient_id": patient_id, "nurse_id": nurse.id})
    return patient_id


CONSULTATION_BODY = {
    "chief_complaint": "Fever", "hpi": "Fever for 3 days", "primary_diagnosis": "Viral fever",
    "differential_diagnosis": "", "advice": "Rest and fluids",
    "medications": [{"drugName": "Paracetamol", "dose": "500mg", "frequency": "TID", "route": "Oral", "duration": "5 days"}],
    "lab_tests": ["CBC"],
}


def test_consultation_auto_creates_tasks_for_linked_patient(client, doctor, admitted_patient, auth_headers):
    resp = client.post("/api/consultations", headers=auth_headers(doctor),
                        json={**CONSULTATION_BODY, "patient_id": admitted_patient})
    assert resp.status_code == 200
    assert resp.json()["tasks_created"] == 2  # 1 medication + 1 lab

    tasks = client.get(f"/api/ipd/tasks/{admitted_patient}", headers=auth_headers(doctor)).json()
    assert len(tasks) == 2
    types = {t["task_type"] for t in tasks}
    assert types == {"Medication", "Lab"}
    assert all(t["source"] == "Auto" for t in tasks)


def test_auto_created_tasks_are_assigned_to_the_patients_active_nurse(client, doctor, nurse, admitted_patient, auth_headers):
    """Regression: auto tasks used to leave nurse_id null, which meant the PATCH
    /api/ipd/tasks/{id} Nurse-role check (task.nurse_id == self) silently blocked the very
    nurse caring for the patient from ever completing them -- only a HeadNurse could."""
    client.post("/api/consultations", headers=auth_headers(doctor), json={**CONSULTATION_BODY, "patient_id": admitted_patient})

    tasks = client.get(f"/api/ipd/tasks/{admitted_patient}", headers=auth_headers(nurse)).json()
    assert len(tasks) == 2
    assert all(t["nurse_id"] == nurse.id for t in tasks)

    complete = client.patch(f"/api/ipd/tasks/{tasks[0]['id']}", headers=auth_headers(nurse), json={"status": "Completed"})
    assert complete.status_code == 200


def test_consultation_does_not_create_tasks_for_unlinked_walkin(client, doctor, auth_headers):
    """An OPD walk-in with no patient_id has no nurse to hand tasks to -- this must be a
    silent no-op, not an error, and not create phantom tasks."""
    resp = client.post("/api/consultations", headers=auth_headers(doctor), json=CONSULTATION_BODY)
    assert resp.status_code == 200
    assert resp.json()["tasks_created"] == 0


def test_finalize_regenerates_tasks_without_duplicating(client, doctor, admitted_patient, auth_headers):
    create = client.post("/api/consultations", headers=auth_headers(doctor), json={**CONSULTATION_BODY, "patient_id": admitted_patient})
    consultation_id = create.json()["id"]
    assert create.json()["tasks_created"] == 2

    # Doctor edits the draft: drops the lab, adds a second medication.
    finalize = client.patch(f"/api/consultations/{consultation_id}/finalize", headers=auth_headers(doctor), json={
        "medications": [
            {"drugName": "Paracetamol", "dose": "500mg", "frequency": "TID", "route": "Oral", "duration": "5 days"},
            {"drugName": "Ibuprofen", "dose": "400mg", "frequency": "BID", "route": "Oral", "duration": "3 days"},
        ],
        "labTests": [],
    })
    assert finalize.status_code == 200
    assert finalize.json()["tasks_created"] == 2  # 2 medications, 0 labs now

    tasks = client.get(f"/api/ipd/tasks/{admitted_patient}", headers=auth_headers(doctor)).json()
    auto_tasks = [t for t in tasks if t["source"] == "Auto"]
    assert len(auto_tasks) == 2  # old Auto tasks replaced, not accumulated
    assert {t["task_type"] for t in auto_tasks} == {"Medication"}


def test_finalize_does_not_touch_manually_created_tasks(client, doctor, head_nurse, admitted_patient, auth_headers):
    manual = client.post("/api/ipd/tasks", headers=auth_headers(head_nurse),
                          json={"patient_id": admitted_patient, "description": "Change IV line"})
    assert manual.status_code == 200

    create = client.post("/api/consultations", headers=auth_headers(doctor), json={**CONSULTATION_BODY, "patient_id": admitted_patient})
    client.patch(f"/api/consultations/{create.json()['id']}/finalize", headers=auth_headers(doctor), json={"medications": [], "labTests": []})

    tasks = client.get(f"/api/ipd/tasks/{admitted_patient}", headers=auth_headers(doctor)).json()
    manual_tasks = [t for t in tasks if t["source"] == "Manual"]
    assert len(manual_tasks) == 1
    assert manual_tasks[0]["description"] == "Change IV line"


def test_drug_interactions_endpoint_returns_real_interactions(client, doctor, auth_headers):
    resp = client.post("/api/drug-interactions", headers=auth_headers(doctor), json={
        "medications": [{"drugName": "Warfarin"}, {"drugName": "Aspirin"}]
    })
    assert resp.status_code == 200
    assert len(resp.json()["interactions"]) == 1
    assert resp.json()["interactions"][0]["severity"] == "Severe"
    assert "Warfarin" in resp.json()["interactions"][0]["drug_pair"]


def test_drug_interactions_endpoint_reports_none_for_unrelated_drugs(client, doctor, auth_headers):
    resp = client.post("/api/drug-interactions", headers=auth_headers(doctor), json={
        "medications": [{"drugName": "Paracetamol"}, {"drugName": "Cetirizine"}]
    })
    assert resp.status_code == 200
    assert resp.json()["interactions"] == []


def test_ipd_round_requires_doctor_role(client, head_nurse, admitted_patient, auth_headers):
    resp = client.post("/api/ipd/rounds", headers=auth_headers(head_nurse),
                        json={"patient_id": admitted_patient, "chief_complaint": "Reviewing patient today"})
    assert resp.status_code == 403


def test_ipd_round_creates_consultation_with_admission_day_and_tasks(client, doctor, admitted_patient, auth_headers):
    resp = client.post("/api/ipd/rounds", headers=auth_headers(doctor),
                        json={**CONSULTATION_BODY, "patient_id": admitted_patient, "chief_complaint": "Reviewing patient on day 1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["admission_day"] == 1
    assert data["tasks_created"] == 3  # 1 medication + 1 lab + 1 observation (advice is non-empty on an IPD_ROUND)


def test_ipd_round_interaction_check_sees_prior_medications_not_just_todays(client, doctor, admitted_patient, auth_headers):
    """The whole point of a multi-day cycle: today's new drug must be checked against
    everything the patient is already on, not just against itself. Warfarin (day 1) +
    Aspirin (day 2) is a real pair in tasks_engine.KNOWN_INTERACTIONS."""
    client.post("/api/ipd/rounds", headers=auth_headers(doctor), json={
        "patient_id": admitted_patient, "chief_complaint": "Starting anticoagulation",
        "medications": [{"drugName": "Warfarin", "dose": "5mg", "frequency": "OD", "route": "Oral", "duration": "ongoing"}],
    })

    resp = client.post("/api/ipd/rounds", headers=auth_headers(doctor), json={
        "patient_id": admitted_patient, "chief_complaint": "Adding cardioprotection",
        "medications": [{"drugName": "Aspirin", "dose": "75mg", "frequency": "OD", "route": "Oral", "duration": "ongoing"}],
    })
    assert resp.status_code == 200
    assert len(resp.json()["interaction_warnings"]) == 1
    assert "Severe" == resp.json()["interaction_warnings"][0]["severity"]


def test_discontinued_medication_drops_out_of_active_regimen(client, doctor, admitted_patient, auth_headers):
    round1 = client.post("/api/ipd/rounds", headers=auth_headers(doctor), json={
        "patient_id": admitted_patient, "chief_complaint": "Starting Ibuprofen",
        "medications": [{"drugName": "Ibuprofen", "dose": "400mg", "frequency": "BID", "route": "Oral", "duration": "5 days"}],
    })
    consultation_id = round1.json()["id"]

    finalize = client.patch(f"/api/consultations/{consultation_id}/finalize", headers=auth_headers(doctor), json={
        "medications": [{"drugName": "Ibuprofen", "discontinued": True}],
    })
    assert finalize.status_code == 200

    from app.tasks_engine import get_active_medications
    import app.main as app_main
    db = app_main.SessionLocal()
    try:
        active = get_active_medications(db, admitted_patient)
    finally:
        db.close()
    assert active == []


def test_cross_org_patient_id_does_not_get_linked(client, doctor, make_user, auth_headers):
    other_doctor = make_user(email="other-doctor@auto-task.com", role="Doctor")
    other_ns = make_user(email="other-station@auto-task.com", role="NursingStation", organization_id=other_doctor.organization_id)
    other_patient = client.post("/api/ipd/patients", headers=auth_headers(other_ns),
                                 json={"name": "Other Org Patient", "age": 40, "gender": "Male", "ward": "W", "bed": "B"}).json()["id"]

    resp = client.post("/api/consultations", headers=auth_headers(doctor), json={**CONSULTATION_BODY, "patient_id": other_patient})
    assert resp.status_code == 404


def test_aggregated_tasks_endpoint_scopes_nurse_to_assigned_patients(client, doctor, head_nurse, nurse, make_user, auth_headers):
    other_nurse = make_user(email="other-nurse@auto-task.com", role="Nurse", organization_id=doctor.organization_id)

    p1 = client.post("/api/ipd/patients", headers=auth_headers(head_nurse),
                      json={"name": "P1", "age": 30, "gender": "Male", "ward": "W", "bed": "1"}).json()["id"]
    p2 = client.post("/api/ipd/patients", headers=auth_headers(head_nurse),
                      json={"name": "P2", "age": 30, "gender": "Male", "ward": "W", "bed": "2"}).json()["id"]
    client.post("/api/ipd/assign", headers=auth_headers(head_nurse), json={"patient_id": p1, "nurse_id": nurse.id})
    client.post("/api/ipd/assign", headers=auth_headers(head_nurse), json={"patient_id": p2, "nurse_id": other_nurse.id})

    client.post("/api/consultations", headers=auth_headers(doctor), json={**CONSULTATION_BODY, "patient_id": p1})
    client.post("/api/consultations", headers=auth_headers(doctor), json={**CONSULTATION_BODY, "patient_id": p2})

    nurse_tasks = client.get("/api/ipd/tasks", headers=auth_headers(nurse)).json()
    assert all(t["patient_name"] == "P1" for t in nurse_tasks)
    assert len(nurse_tasks) == 2

    hn_tasks = client.get("/api/ipd/tasks", headers=auth_headers(head_nurse)).json()
    assert len(hn_tasks) == 4
