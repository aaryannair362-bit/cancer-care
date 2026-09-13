"""
R10 Anaesthetist module (Core Oncology 4 Sections + 11 Additional Modules Detailed Developer
Handoffs) -- pre-operative evaluation/clearance, intra-operative anaesthesia record, and
post-anaesthesia recovery documentation, all keyed off an existing SurgicalPlan.

Covers the specific non-negotiable constraints this module exists to enforce:
  - A surgical plan cannot move to pre_op_ready without a Finalized, Cleared (or
    Cleared-with-conditions) anaesthesia pre-op evaluation (the cross-module "must link to
    the surgery" requirement).
  - A Finalized pre-op evaluation is immutable except via an amendment (amendment_reason
    required, pre-amendment content snapshotted) -- signed records are not silently
    overwritten.
  - Role boundaries: only the Anaesthetist (or Admin) may write anaesthesia records; an
    Anaesthetist cannot touch SurgicalPlan.procedure or SurgicalOperativeNote content; the
    surgical team can read anaesthesia records but not write them.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def surgeon(make_user):
    return make_user(email="surgeon@anaesthesia-test.com", role="CCASurgicalOncologist")


@pytest.fixture
def anaesthetist(make_user, surgeon):
    return make_user(email="anaesthetist@anaesthesia-test.com", role="CCAAnaesthetist", organization_id=surgeon.organization_id)


@pytest.fixture
def other_anaesthetist(make_user, surgeon):
    return make_user(email="anaesthetist2@anaesthesia-test.com", role="CCAAnaesthetist", organization_id=surgeon.organization_id)


@pytest.fixture
def surgical_nurse(make_user, surgeon):
    return make_user(email="ornurse@anaesthesia-test.com", role="CCASurgicalNurse", organization_id=surgeon.organization_id)


@pytest.fixture
def surgeon_headers(auth_headers, surgeon):
    return auth_headers(surgeon)


@pytest.fixture
def anaesthetist_headers(auth_headers, anaesthetist):
    return auth_headers(anaesthetist)


@pytest.fixture
def other_anaesthetist_headers(auth_headers, other_anaesthetist):
    return auth_headers(other_anaesthetist)


@pytest.fixture
def or_nurse_headers(auth_headers, surgical_nurse):
    return auth_headers(surgical_nurse)


@pytest.fixture
def patient(db_session, surgeon):
    p = CCAPatient(
        mrn="ANAES-0001", name="Anaesthesia Test Patient", age=61, sex="Female",
        organization_id=surgeon.organization_id, journey_state="Surgical Oncology",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_plan(client, surgeon_headers, patient_id):
    created = client.post("/api/cca/surgical-plans", headers=surgeon_headers, json={
        "patient_id": patient_id, "procedure": "Right hemicolectomy", "intent": "Curative",
        "anatomical_site": "Right colon",
    })
    assert created.status_code == 201, created.text
    return created.json()["surgical_plan"]["id"]


def test_pre_op_ready_blocked_without_anaesthesia_clearance(client, surgeon_headers, patient):
    plan_id = _create_plan(client, surgeon_headers, patient.id)
    client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "surgeon_reviewed"})
    client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "planned"})

    blocked = client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "pre_op_ready"})
    assert blocked.status_code == 409
    assert "anaesthetist" in blocked.text.lower()


def test_pre_op_ready_allowed_once_finalized_and_cleared(client, surgeon_headers, anaesthetist_headers, patient):
    plan_id = _create_plan(client, surgeon_headers, patient.id)
    client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "surgeon_reviewed"})
    client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "planned"})

    created = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={
        "diagnosis": "Colon adenocarcinoma", "asa_grade": "II", "medical_clearance_status": "Cleared",
        "anaesthetic_plan": "General anaesthesia with epidural analgesia.",
    })
    assert created.status_code == 201, created.text
    evaluation_id = created.json()["evaluation"]["id"]
    assert created.json()["evaluation"]["status"] == "Draft"

    # Cannot finalize before the surgery moves, but finalize doesn't depend on plan status --
    # it only needs asa_grade + a decided clearance status, both already set above.
    finalized = client.post(f"/api/cca/anaesthesia/pre-op/{evaluation_id}/finalize", headers=anaesthetist_headers)
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["evaluation"]["status"] == "Finalized"

    allowed = client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "pre_op_ready"})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["surgical_plan"]["status"] == "pre_op_ready"


def test_pre_op_ready_blocked_when_not_cleared(client, surgeon_headers, anaesthetist_headers, patient):
    plan_id = _create_plan(client, surgeon_headers, patient.id)
    client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "surgeon_reviewed"})
    client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "planned"})

    created = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={
        "asa_grade": "IV", "medical_clearance_status": "NotCleared",
    })
    evaluation_id = created.json()["evaluation"]["id"]
    client.post(f"/api/cca/anaesthesia/pre-op/{evaluation_id}/finalize", headers=anaesthetist_headers)

    blocked = client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "pre_op_ready"})
    assert blocked.status_code == 409


def test_finalize_requires_asa_grade_and_decided_clearance(client, surgeon_headers, anaesthetist_headers, patient):
    plan_id = _create_plan(client, surgeon_headers, patient.id)
    created = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={})
    evaluation_id = created.json()["evaluation"]["id"]

    missing_asa = client.post(f"/api/cca/anaesthesia/pre-op/{evaluation_id}/finalize", headers=anaesthetist_headers)
    assert missing_asa.status_code == 422

    client.patch(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={"asa_grade": "I"})
    still_pending = client.post(f"/api/cca/anaesthesia/pre-op/{evaluation_id}/finalize", headers=anaesthetist_headers)
    assert still_pending.status_code == 422  # medical_clearance_status still defaults to Pending

    client.patch(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={"medical_clearance_status": "Cleared"})
    ok = client.post(f"/api/cca/anaesthesia/pre-op/{evaluation_id}/finalize", headers=anaesthetist_headers)
    assert ok.status_code == 200, ok.text


def test_finalized_evaluation_requires_amendment_reason_to_change(client, surgeon_headers, anaesthetist_headers, patient):
    plan_id = _create_plan(client, surgeon_headers, patient.id)
    created = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={
        "asa_grade": "II", "medical_clearance_status": "Cleared", "anaesthetic_plan": "Original plan.",
    })
    evaluation_id = created.json()["evaluation"]["id"]
    client.post(f"/api/cca/anaesthesia/pre-op/{evaluation_id}/finalize", headers=anaesthetist_headers)

    missing_reason = client.patch(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={
        "anaesthetic_plan": "Revised plan.",
    })
    assert missing_reason.status_code == 400

    amended = client.patch(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={
        "anaesthetic_plan": "Revised plan.", "amendment_reason": "Patient's medication list updated after evaluation.",
    })
    assert amended.status_code == 200, amended.text
    assert amended.json()["evaluation"]["anaesthetic_plan"] == "Revised plan."
    # Still finalized -- an amendment doesn't demote it back to Draft.
    assert amended.json()["evaluation"]["status"] == "Finalized"


def test_create_pre_op_evaluation_is_one_per_plan(client, surgeon_headers, anaesthetist_headers, patient):
    plan_id = _create_plan(client, surgeon_headers, patient.id)
    first = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={})
    assert first.status_code == 201

    duplicate = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={})
    assert duplicate.status_code == 409


def test_only_anaesthetist_may_write_but_surgical_team_may_read(client, surgeon_headers, anaesthetist_headers, or_nurse_headers, patient):
    plan_id = _create_plan(client, surgeon_headers, patient.id)

    denied_surgeon = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=surgeon_headers, json={})
    assert denied_surgeon.status_code == 403

    denied_nurse = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=or_nurse_headers, json={})
    assert denied_nurse.status_code == 403

    created = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={
        "asa_grade": "III",
    })
    assert created.status_code == 201, created.text

    read_by_surgeon = client.get(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=surgeon_headers)
    assert read_by_surgeon.status_code == 200
    read_by_nurse = client.get(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=or_nurse_headers)
    assert read_by_nurse.status_code == 200


def test_anaesthetist_cannot_touch_surgical_plan_or_operative_note(client, surgeon_headers, anaesthetist_headers, patient):
    """Cross-module requirement: never alter surgeon-owned diagnosis/operative findings."""
    plan_id = _create_plan(client, surgeon_headers, patient.id)
    denied_transition = client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=anaesthetist_headers, json={"status": "surgeon_reviewed"})
    assert denied_transition.status_code == 403

    denied_note = client.post(f"/api/cca/surgical-plans/{plan_id}/operative-notes", headers=anaesthetist_headers, json={
        "procedure_performed": "Should not be allowed",
    })
    assert denied_note.status_code == 403


def test_intraop_and_recovery_records(client, surgeon_headers, anaesthetist_headers, or_nurse_headers, patient):
    plan_id = _create_plan(client, surgeon_headers, patient.id)

    denied = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/intra-op", headers=or_nurse_headers, json={
        "anaesthesia_type": "General",
    })
    assert denied.status_code == 403

    intraop = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/intra-op", headers=anaesthetist_headers, json={
        "anaesthesia_type": "General", "airway_management": "Endotracheal tube, grade 1 view.",
        "monitoring_notes": "Stable throughout.",
    })
    assert intraop.status_code == 201, intraop.text

    listed = client.get(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/intra-op", headers=or_nurse_headers)
    assert listed.status_code == 200
    assert len(listed.json()["results"]) == 1

    bad_destination = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/recovery", headers=anaesthetist_headers, json={
        "post_op_destination": "Home",
    })
    assert bad_destination.status_code == 422

    recovery = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/recovery", headers=anaesthetist_headers, json={
        "pain_score": 3, "post_op_destination": "HDU", "readiness_for_discharge_confirmed": False,
    })
    assert recovery.status_code == 201, recovery.text
    assert recovery.json()["record"]["post_op_destination"] == "HDU"


def test_worklist_lists_surgical_plans_with_evaluation_status(client, surgeon_headers, anaesthetist_headers, patient):
    plan_id = _create_plan(client, surgeon_headers, patient.id)
    client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "surgeon_reviewed"})
    client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": "planned"})

    worklist = client.get("/api/cca/surgical-plans/worklist", headers=anaesthetist_headers)
    assert worklist.status_code == 200, worklist.text
    row = next(r for r in worklist.json()["worklist"] if r["id"] == plan_id)
    assert row["pre_op_evaluation"] is None

    client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={"asa_grade": "II"})
    worklist_after = client.get("/api/cca/surgical-plans/worklist", headers=anaesthetist_headers)
    row_after = next(r for r in worklist_after.json()["worklist"] if r["id"] == plan_id)
    assert row_after["pre_op_evaluation"]["asa_grade"] == "II"


def test_cross_org_pre_op_evaluation_is_not_found(client, surgeon_headers, anaesthetist_headers, patient, make_user, auth_headers):
    plan_id = _create_plan(client, surgeon_headers, patient.id)
    created = client.post(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=anaesthetist_headers, json={})
    evaluation_id = created.json()["evaluation"]["id"]

    other_org_anaesthetist = make_user(email="other-org-anaesthetist@anaesthesia-test.com", role="CCAAnaesthetist")
    other_headers = auth_headers(other_org_anaesthetist)

    res = client.get(f"/api/cca/surgical-plans/{plan_id}/anaesthesia/pre-op", headers=other_headers)
    assert res.status_code == 404

    finalize_res = client.post(f"/api/cca/anaesthesia/pre-op/{evaluation_id}/finalize", headers=other_headers)
    assert finalize_res.status_code == 404
