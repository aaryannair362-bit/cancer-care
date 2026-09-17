"""
Surgical Nurse missing-development round (Surgical_Nurse_Missing_Development_Only.pdf):
pre-operative nursing verification, OR counts, post-operative nursing handoff, and a
nursing-owned closed-loop task surface, plus the specimen chain-of-custody's
Accepted/Exception states this role now exercises directly.

Pure structured attestation throughout -- no clinical scoring, threshold, or safety-decision
logic is computed anywhere (standing repo rule).
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def surgeon(make_user):
    return make_user(email="surgeon@sn-test.com", role="CCASurgicalOncologist")


@pytest.fixture
def surgeon_headers(auth_headers, surgeon):
    return auth_headers(surgeon)


@pytest.fixture
def nurse(make_user, surgeon):
    return make_user(email="nurse@sn-test.com", role="CCASurgicalNurse", organization_id=surgeon.organization_id)


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


@pytest.fixture
def patient(db_session, surgeon):
    p = CCAPatient(mrn="SN-TEST-0001", name="Surgical Nurse Test Patient", age=64, sex="Male", organization_id=surgeon.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def plan_id(client, surgeon_headers, patient):
    return client.post("/api/cca/surgical-plans", headers=surgeon_headers, json={
        "patient_id": patient.id, "procedure": "Right Hemicolectomy",
    }).json()["surgical_plan"]["id"]


def test_preop_nursing_verification_lifecycle(client, nurse_headers, plan_id):
    empty = client.get(f"/api/cca/surgical-plans/{plan_id}/preop-nursing-verification", headers=nurse_headers)
    assert empty.status_code == 200
    assert empty.json()["preop_nursing_verification"] is None

    draft = client.post(f"/api/cca/surgical-plans/{plan_id}/preop-nursing-verification", headers=nurse_headers, json={
        "identity_verified": True, "procedure_site_laterality_confirmed": True,
        "allergy_status": "NKDA", "consent_status": "Documented", "site_marking_status": "Confirmed",
        "npo_status": "NPO since midnight", "iv_access": "18G left forearm",
    })
    assert draft.status_code == 200, draft.text
    assert draft.json()["preop_nursing_verification"]["verified_at"] is None

    finalized = client.post(f"/api/cca/surgical-plans/{plan_id}/preop-nursing-verification/finalize", headers=nurse_headers)
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["preop_nursing_verification"]["verified_at"] is not None
    assert finalized.json()["preop_nursing_verification"]["verified_by"]

    locked = client.post(f"/api/cca/surgical-plans/{plan_id}/preop-nursing-verification", headers=nurse_headers, json={
        "identity_verified": False,
    })
    assert locked.status_code == 409

    twice = client.post(f"/api/cca/surgical-plans/{plan_id}/preop-nursing-verification/finalize", headers=nurse_headers)
    assert twice.status_code == 409


def test_or_counts_and_discrepancy_resolution(client, nurse_headers, plan_id):
    bad_phase = client.post(f"/api/cca/surgical-plans/{plan_id}/or-counts", headers=nurse_headers, json={
        "phase": "Bogus", "count_type": "Sponge", "count_value": "10",
    })
    assert bad_phase.status_code == 422

    correct = client.post(f"/api/cca/surgical-plans/{plan_id}/or-counts", headers=nurse_headers, json={
        "phase": "Initial", "count_type": "Sponge", "count_value": "10",
    })
    assert correct.status_code == 201, correct.text
    assert correct.json()["or_count"]["discrepancy"] is False

    mismatched = client.post(f"/api/cca/surgical-plans/{plan_id}/or-counts", headers=nurse_headers, json={
        "phase": "Final", "count_type": "Sponge", "count_value": "9",
        "discrepancy": True, "discrepancy_notes": "One sponge unaccounted for at final count.",
    })
    assert mismatched.status_code == 201, mismatched.text
    count_id = mismatched.json()["or_count"]["id"]

    no_notes = client.post(f"/api/cca/or-counts/{count_id}/resolve-discrepancy", headers=nurse_headers, json={})
    assert no_notes.status_code == 422

    resolved = client.post(f"/api/cca/or-counts/{count_id}/resolve-discrepancy", headers=nurse_headers, json={
        "resolution_notes": "Sponge found on floor drape, recount confirmed correct.",
    })
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["or_count"]["discrepancy_resolved"] is True

    already_resolved = client.post(f"/api/cca/or-counts/{count_id}/resolve-discrepancy", headers=nurse_headers, json={
        "resolution_notes": "again",
    })
    assert already_resolved.status_code == 409

    listed = client.get(f"/api/cca/surgical-plans/{plan_id}/or-counts", headers=nurse_headers)
    assert len(listed.json()["or_counts"]) == 2


def test_postop_nursing_handoff_requires_named_receiver(client, nurse_headers, plan_id):
    missing_receiver = client.post(f"/api/cca/surgical-plans/{plan_id}/postop-nursing-handoff", headers=nurse_headers, json={
        "disposition": "PACU",
    })
    assert missing_receiver.status_code == 422

    handoff = client.post(f"/api/cca/surgical-plans/{plan_id}/postop-nursing-handoff", headers=nurse_headers, json={
        "disposition": "PACU", "handoff_receiver": "S. Rao", "handoff_receiver_role": "PACU Nurse",
        "pain_assessment": "3/10, controlled", "wound_status": "Clean, dry, intact",
    })
    assert handoff.status_code == 201, handoff.text
    assert handoff.json()["postop_nursing_handoff"]["handed_off_by"]

    listed = client.get(f"/api/cca/surgical-plans/{plan_id}/postop-nursing-handoff", headers=nurse_headers)
    assert len(listed.json()["postop_nursing_handoffs"]) == 1


def test_nursing_task_created_and_resolved_by_nurse_not_clinician(client, nurse_headers, surgeon_headers, plan_id, patient):
    created = client.post(f"/api/cca/surgical-plans/{plan_id}/nursing-tasks", headers=nurse_headers, json={
        "description": "Confirm blood availability before transfer", "due_date": "2026-09-20T10:00:00",
    })
    assert created.status_code == 201, created.text
    task = created.json()["task"]
    assert task["owner_role"] == "NURSING"
    assert "OR:" in task["description"]

    listed = client.get(f"/api/cca/patients/{patient.id}/tasks?owner_role=NURSING", headers=nurse_headers)
    assert listed.status_code == 200
    assert len(listed.json()["tasks"]) == 1

    resolved = client.post(f"/api/cca/tasks/{task['id']}/resolve", headers=nurse_headers)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["task"]["status"] == "RESOLVED"


def test_specimen_accept_and_exception_states(client, nurse_headers, plan_id):
    specimen = client.post(f"/api/cca/surgical-plans/{plan_id}/specimens", headers=nurse_headers, json={
        "specimen_label": "Right colon segment", "specimen_type": "Resection specimen",
    })
    assert specimen.status_code == 201, specimen.text
    specimen_id = specimen.json()["specimen"]["id"]

    client.post(f"/api/cca/specimens/{specimen_id}/event", headers=nurse_headers, json={
        "status": "HandedOff", "handed_off_to": "Lab Courier",
    })
    client.post(f"/api/cca/specimens/{specimen_id}/event", headers=nurse_headers, json={
        "status": "ReceivedByLab", "received_by_lab": "Pathology Reception",
    })

    accepted = client.post(f"/api/cca/specimens/{specimen_id}/event", headers=nurse_headers, json={
        "status": "Accepted",
    })
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["specimen"]["status"] == "Accepted"

    # A second specimen taking the Exception branch instead, requiring a reason.
    specimen2 = client.post(f"/api/cca/surgical-plans/{plan_id}/specimens", headers=nurse_headers, json={
        "specimen_label": "Sentinel node",
    }).json()["specimen"]
    client.post(f"/api/cca/specimens/{specimen2['id']}/event", headers=nurse_headers, json={
        "status": "HandedOff", "handed_off_to": "Lab Courier",
    })
    client.post(f"/api/cca/specimens/{specimen2['id']}/event", headers=nurse_headers, json={
        "status": "ReceivedByLab", "received_by_lab": "Pathology Reception",
    })
    missing_reason = client.post(f"/api/cca/specimens/{specimen2['id']}/event", headers=nurse_headers, json={
        "status": "Exception",
    })
    assert missing_reason.status_code == 422

    exception = client.post(f"/api/cca/specimens/{specimen2['id']}/event", headers=nurse_headers, json={
        "status": "Exception", "exception_reason": "Label illegible on arrival.",
    })
    assert exception.status_code == 200, exception.text
    assert exception.json()["specimen"]["exception_reason"] == "Label illegible on arrival."
