"""
Final gap-closing round, item 3 (largest single gap found): Inpatient Oncology (C.21) --
previously zero CCA-specific representation. Covers the full admission lifecycle: bed
request -> admit -> H&P sign -> problem list -> systemic-therapy-linked MAR -> ward round
-> nursing flowsheet -> deterioration/escalation -> goals of care -> transfer -> discharge
summary sign -> discharge, plus death documentation as an alternate terminal path.

Never tests a computed clinical judgment or score -- early_warning_score/pain_score are
always whatever the nurse typed in, never computed by this system from other fields.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@ipd-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def nurse(make_user, oncologist):
    return make_user(email="nurse@ipd-test.com", role="Nurse", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="IPD-TEST-0001", name="Inpatient Oncology Test Patient", age=63, sex="Male", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def test_admission_bed_request_and_admit(client, onc_headers, patient):
    missing_reason = client.post("/api/cca/inpatient/admissions", headers=onc_headers, json={"patient_id": patient.id})
    assert missing_reason.status_code == 422

    created = client.post("/api/cca/inpatient/admissions", headers=onc_headers, json={
        "patient_id": patient.id, "admission_reason": "Neutropenic fever.", "admitting_diagnosis": "Febrile neutropenia post-chemo",
    })
    assert created.status_code == 201, created.text
    admission_id = created.json()["admission"]["id"]
    assert created.json()["admission"]["status"] == "BED_REQUESTED"

    missing_ward = client.post(f"/api/cca/inpatient/admissions/{admission_id}/admit", headers=onc_headers, json={})
    assert missing_ward.status_code == 422

    admitted = client.post(f"/api/cca/inpatient/admissions/{admission_id}/admit", headers=onc_headers, json={"ward": "Oncology Ward A", "bed": "12B"})
    assert admitted.status_code == 200, admitted.text
    assert admitted.json()["admission"]["status"] == "ADMITTED"

    double_admit = client.post(f"/api/cca/inpatient/admissions/{admission_id}/admit", headers=onc_headers, json={"ward": "X"})
    assert double_admit.status_code == 409

    listed = client.get(f"/api/cca/patients/{patient.id}/inpatient/admissions", headers=onc_headers)
    assert listed.status_code == 200
    assert len(listed.json()["admissions"]) == 1

    worklist = client.get("/api/cca/inpatient/admissions?status=ADMITTED", headers=onc_headers)
    assert worklist.status_code == 200
    assert any(a["id"] == admission_id for a in worklist.json()["admissions"])


@pytest.fixture
def admission_id(client, onc_headers, patient):
    created = client.post("/api/cca/inpatient/admissions", headers=onc_headers, json={
        "patient_id": patient.id, "admission_reason": "Neutropenic fever.",
    }).json()["admission"]["id"]
    client.post(f"/api/cca/inpatient/admissions/{created}/admit", headers=onc_headers, json={"ward": "Oncology Ward A", "bed": "12B"})
    return created


def test_history_and_physical_lifecycle(client, onc_headers, admission_id):
    missing_cc = client.post(f"/api/cca/inpatient/admissions/{admission_id}/history-physical", headers=onc_headers, json={})
    assert missing_cc.status_code == 422

    drafted = client.post(f"/api/cca/inpatient/admissions/{admission_id}/history-physical", headers=onc_headers, json={
        "chief_complaint": "Fever and chills.", "performance_status": "ECOG 1",
        "current_treatment_summary": "AC-T cycle 3, day 10.",
    })
    assert drafted.status_code == 201, drafted.text
    hp_id = drafted.json()["history_physical"]["id"]
    assert drafted.json()["history_physical"]["status"] == "DRAFT"

    updated = client.post(f"/api/cca/inpatient/admissions/{admission_id}/history-physical", headers=onc_headers, json={
        "chief_complaint": "Fever and chills (updated).",
    })
    assert updated.json()["history_physical"]["id"] == hp_id

    signed = client.post(f"/api/cca/inpatient/history-physicals/{hp_id}/sign", headers=onc_headers)
    assert signed.status_code == 200, signed.text
    assert signed.json()["history_physical"]["status"] == "SIGNED"

    locked = client.post(f"/api/cca/inpatient/admissions/{admission_id}/history-physical", headers=onc_headers, json={"chief_complaint": "Trying to edit."})
    assert locked.status_code == 409


def test_problem_list(client, onc_headers, admission_id):
    item = client.post(f"/api/cca/inpatient/admissions/{admission_id}/problems", headers=onc_headers, json={
        "problem": "Febrile neutropenia", "priority": "Priority",
    })
    assert item.status_code == 201, item.text
    item_id = item.json()["problem"]["id"]

    resolved = client.post(f"/api/cca/inpatient/problems/{item_id}/resolve", headers=onc_headers, json={"notes": "Afebrile x48h."})
    assert resolved.status_code == 200
    assert resolved.json()["problem"]["status"] == "Resolved"

    listed = client.get(f"/api/cca/inpatient/admissions/{admission_id}/problems", headers=onc_headers)
    assert len(listed.json()["problems"]) == 1


def test_medication_administration(client, onc_headers, nurse_headers, admission_id):
    scheduled = client.post(f"/api/cca/inpatient/admissions/{admission_id}/medications", headers=onc_headers, json={
        "drug_name": "Piperacillin-Tazobactam", "route": "IV",
    })
    assert scheduled.status_code == 201, scheduled.text
    med_id = scheduled.json()["medication"]["id"]

    forbidden = client.post(f"/api/cca/inpatient/medications/{med_id}/administer", headers=onc_headers, json={"status": "Given"})
    assert forbidden.status_code == 403

    bad_status = client.post(f"/api/cca/inpatient/medications/{med_id}/administer", headers=nurse_headers, json={"status": "Nope"})
    assert bad_status.status_code == 422

    missing_hold_reason = client.post(f"/api/cca/inpatient/medications/{med_id}/administer", headers=nurse_headers, json={"status": "Held"})
    assert missing_hold_reason.status_code == 422

    given = client.post(f"/api/cca/inpatient/medications/{med_id}/administer", headers=nurse_headers, json={
        "status": "Given", "dose_administered": "4.5g",
    })
    assert given.status_code == 200, given.text
    assert given.json()["medication"]["status"] == "Given"

    listed = client.get(f"/api/cca/inpatient/admissions/{admission_id}/medications", headers=onc_headers)
    assert len(listed.json()["medications"]) == 1


def test_ward_round_and_flowsheet(client, onc_headers, nurse_headers, admission_id):
    missing_fields = client.post(f"/api/cca/inpatient/admissions/{admission_id}/ward-rounds", headers=onc_headers, json={"assessment": "Improving"})
    assert missing_fields.status_code == 422

    note = client.post(f"/api/cca/inpatient/admissions/{admission_id}/ward-rounds", headers=onc_headers, json={
        "assessment": "Afebrile, improving.", "plan": "Continue antibiotics, reassess counts tomorrow.",
    })
    assert note.status_code == 201, note.text

    forbidden = client.post(f"/api/cca/inpatient/admissions/{admission_id}/flowsheet", headers=onc_headers, json={})
    assert forbidden.status_code == 403

    entry = client.post(f"/api/cca/inpatient/admissions/{admission_id}/flowsheet", headers=nurse_headers, json={
        "vitals": {"temp": 37.1, "heart_rate": 82, "bp_systolic": 118, "bp_diastolic": 76, "spo2": 98},
        "pain_score": 2, "mobility_status": "Ambulatory",
    })
    assert entry.status_code == 201, entry.text

    rounds = client.get(f"/api/cca/inpatient/admissions/{admission_id}/ward-rounds", headers=onc_headers)
    assert len(rounds.json()["ward_round_notes"]) == 1
    flow = client.get(f"/api/cca/inpatient/admissions/{admission_id}/flowsheet", headers=onc_headers)
    assert len(flow.json()["flowsheet_entries"]) == 1


def test_deterioration_and_goals_of_care(client, onc_headers, admission_id):
    missing_trigger = client.post(f"/api/cca/inpatient/admissions/{admission_id}/deterioration-events", headers=onc_headers, json={})
    assert missing_trigger.status_code == 422

    event = client.post(f"/api/cca/inpatient/admissions/{admission_id}/deterioration-events", headers=onc_headers, json={
        "trigger": "New hypotension, BP 82/50.", "early_warning_score": 7, "rapid_response_called": True,
        "escalated_to": "ICU Registrar",
    })
    assert event.status_code == 201, event.text
    assert event.json()["deterioration_event"]["escalation_time"] is not None

    bad_code_status = client.post(f"/api/cca/inpatient/admissions/{admission_id}/goals-of-care", headers=onc_headers, json={"code_status": "Maybe"})
    assert bad_code_status.status_code == 422

    goals = client.post(f"/api/cca/inpatient/admissions/{admission_id}/goals-of-care", headers=onc_headers, json={
        "code_status": "Full Code", "goals_discussed_with": "Patient and spouse", "summary": "Aggressive care desired.",
    })
    assert goals.status_code == 201, goals.text

    listed_events = client.get(f"/api/cca/inpatient/admissions/{admission_id}/deterioration-events", headers=onc_headers)
    assert len(listed_events.json()["deterioration_events"]) == 1


def test_transfer_and_discharge(client, onc_headers, admission_id):
    transfer = client.post(f"/api/cca/inpatient/admissions/{admission_id}/transfers", headers=onc_headers, json={
        "transfer_type": "Ward-to-Ward", "from_location": "Oncology Ward A", "to_location": "Oncology Ward B",
        "handover_summary": "Stable, transferred for isolation room availability.",
    })
    assert transfer.status_code == 201, transfer.text

    admission_after = client.get(f"/api/cca/inpatient/admissions/{admission_id}", headers=onc_headers).json()["admission"]
    assert admission_after["status"] == "TRANSFERRED"
    assert admission_after["ward"] == "Oncology Ward B"

    early_discharge = client.post(f"/api/cca/inpatient/admissions/{admission_id}/discharge", headers=onc_headers, json={})
    assert early_discharge.status_code == 409

    draft = client.post(f"/api/cca/inpatient/admissions/{admission_id}/discharge-summary", headers=onc_headers, json={
        "discharge_diagnosis": "Resolved febrile neutropenia",
    })
    assert draft.status_code == 201, draft.text
    summary_id = draft.json()["discharge_summary"]["id"]
    sign_fail = client.post(f"/api/cca/inpatient/discharge-summaries/{summary_id}/sign", headers=onc_headers)
    assert sign_fail.status_code == 422

    client.post(f"/api/cca/inpatient/admissions/{admission_id}/discharge-summary", headers=onc_headers, json={
        "discharge_diagnosis": "Resolved febrile neutropenia", "hospital_course": "Treated with IV antibiotics, afebrile x48h.",
        "follow_up_plan": "Follow up with oncology in 1 week.",
    })
    signed = client.post(f"/api/cca/inpatient/discharge-summaries/{summary_id}/sign", headers=onc_headers)
    assert signed.status_code == 200, signed.text

    discharged = client.post(f"/api/cca/inpatient/admissions/{admission_id}/discharge", headers=onc_headers, json={})
    assert discharged.status_code == 200, discharged.text
    assert discharged.json()["admission"]["status"] == "DISCHARGED"


def test_death_documentation(client, onc_headers, admission_id):
    missing_fields = client.post(f"/api/cca/inpatient/admissions/{admission_id}/death-documentation", headers=onc_headers, json={})
    assert missing_fields.status_code == 422

    doc = client.post(f"/api/cca/inpatient/admissions/{admission_id}/death-documentation", headers=onc_headers, json={
        "immediate_cause": "Septic shock", "certifying_clinician": "Dr. Oncologist",
        "family_notified": True, "family_notified_by": "Dr. Oncologist",
    })
    assert doc.status_code == 201, doc.text

    admission_after = client.get(f"/api/cca/inpatient/admissions/{admission_id}", headers=onc_headers).json()["admission"]
    assert admission_after["status"] == "DECEASED"

    duplicate = client.post(f"/api/cca/inpatient/admissions/{admission_id}/death-documentation", headers=onc_headers, json={
        "immediate_cause": "X", "certifying_clinician": "Y",
    })
    assert duplicate.status_code == 409
