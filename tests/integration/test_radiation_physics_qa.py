"""
Product 1 vs Product 2 Functional Gap Report, Batch 4: Radiation Physics QA.

Previously the physics_qa step in CCARadiationPhase's rt_sub_status pipeline was a bare
signature stamped by transition_radiation_phase -- no decision, no checklist, no note, and
no way to reject. Any Radiation Oncologist could approve a phase for treatment even if
physics QA was never actually performed. This closes that gap: a real Physics QA decision
(checklist + Approved/Rejected decision + mandatory note) now gates the phase's advance to
physician_approved.

Never tests dose/MU computation -- every checklist item is a physicist attestation
(boolean), never a system-computed pass/fail, matching Product 1's own real physics_qa
shape (a holistic decision + note, not a computed threshold).
"""
import pytest

from app.models_cca import CCAPatient

_FULL_CHECKLIST = {
    "prescription_plan_concordance": True, "dose_volume_constraint_review": True,
    "target_oar_coverage_review": True, "machine_deliverability_review": True,
}


@pytest.fixture
def oncologist(make_user):
    return make_user(email="ro@radiation-physics-qa-test.com", role="CCARadiationOncologist")


@pytest.fixture
def physicist(make_user, oncologist):
    return make_user(email="physicist@radiation-physics-qa-test.com", role="CCARadiationPhysicist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def physicist_headers(auth_headers, physicist):
    return auth_headers(physicist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(
        mrn="RT-PHYSICS-QA-0001", name="Radiation Physics QA Test Patient", age=57, sex="Female",
        organization_id=oncologist.organization_id, journey_state="On Treatment",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_phase(client, onc_headers, patient_id):
    # RO Consultation gate (gap review item 9) -- required before a course can be prescribed.
    client.post(f"/api/cca/patients/{patient_id}/radiation-consultations", headers=onc_headers, json={"cied_present": False})
    rx = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "diagnosis": "Breast Cancer", "intent": "Curative", "technique": "IMRT",
    })
    assert rx.status_code == 201, rx.text
    rx_id = rx.json()["radiation_prescription"]["id"]

    phase = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/phases", headers=onc_headers, json={
        "label": "Breast + Nodes", "treatment_site": "Left breast", "total_prescribed_dose_gy": 40,
        "dose_per_fraction_gy": 2.67, "number_of_fractions": 15,
    })
    assert phase.status_code == 201, phase.text
    return phase.json()["phase"]["id"]


def _advance_to(client, headers, phase_id, status):
    res = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=headers, json={"status": status})
    assert res.status_code == 200, res.text
    return res.json()["phase"]


def _create_phase_at_physics_qa(client, onc_headers, physicist_headers, patient_id):
    phase_id = _create_phase(client, onc_headers, patient_id)
    for status in ("simulation_pending", "simulation_complete", "contouring", "planning", "physics_qa"):
        _advance_to(client, physicist_headers, phase_id, status)
    return phase_id


def test_physics_qa_requires_phase_to_be_at_physics_qa_step(client, onc_headers, physicist_headers, patient):
    phase_id = _create_phase(client, onc_headers, patient.id)  # still "prescribed"
    res = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_CHECKLIST, "note": "Looks good.",
    })
    assert res.status_code == 409


def test_physics_qa_role_gate_rejects_non_physicist(client, onc_headers, physicist_headers, patient):
    phase_id = _create_phase_at_physics_qa(client, onc_headers, physicist_headers, patient.id)
    res = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=onc_headers, json={
        "decision": "Approved", "checklist": _FULL_CHECKLIST, "note": "Looks good.",
    })
    assert res.status_code == 403


def test_physics_qa_decision_and_note_validation(client, onc_headers, physicist_headers, patient):
    phase_id = _create_phase_at_physics_qa(client, onc_headers, physicist_headers, patient.id)

    bad_decision = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Looks fine", "checklist": _FULL_CHECKLIST, "note": "note",
    })
    assert bad_decision.status_code == 422

    missing_note = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_CHECKLIST,
    })
    assert missing_note.status_code == 422


def test_physics_qa_approve_requires_full_checklist(client, onc_headers, physicist_headers, patient):
    phase_id = _create_phase_at_physics_qa(client, onc_headers, physicist_headers, patient.id)
    partial = dict(_FULL_CHECKLIST)
    partial["machine_deliverability_review"] = False

    rejected = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": partial, "note": "Reviewed everything.",
    })
    assert rejected.status_code == 422

    approved = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_CHECKLIST, "note": "Reviewed everything, plan is deliverable.",
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["phase"]["physics_qa_decision"] == "Approved"
    assert approved.json()["phase"]["physics_qa_decided_by"] == "physicist@radiation-physics-qa-test.com"


def test_physics_qa_reject_records_decision_without_advancing_phase(client, onc_headers, physicist_headers, patient):
    """Matches Product 1's real behavior: rejecting does not auto-revert/advance the phase --
    staff must address the issue and a physicist re-submits."""
    phase_id = _create_phase_at_physics_qa(client, onc_headers, physicist_headers, patient.id)

    rejected = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Rejected / Replan Required", "checklist": {}, "note": "Dose-volume constraints not met for the cord.",
    })
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["phase"]["rt_sub_status"] == "physics_qa"
    assert rejected.json()["phase"]["physics_qa_decision"] == "Rejected / Replan Required"

    fetched = client.get(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers)
    assert fetched.status_code == 200
    assert fetched.json()["physics_qa"]["decision"] == "Rejected / Replan Required"

    # A physicist may re-submit a fresh decision later (upsert, not one-shot).
    approved = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_CHECKLIST, "note": "Replanned; constraints now met.",
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["phase"]["physics_qa_decision"] == "Approved"


def test_physician_approval_blocked_until_physics_qa_approved(client, onc_headers, physicist_headers, patient):
    phase_id = _create_phase_at_physics_qa(client, onc_headers, physicist_headers, patient.id)

    blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": "physician_approved"})
    assert blocked.status_code == 409
    assert "physics qa" in blocked.text.lower()

    client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Rejected / Replan Required", "checklist": {}, "note": "Not ready.",
    })
    still_blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": "physician_approved"})
    assert still_blocked.status_code == 409

    client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_CHECKLIST, "note": "All clear.",
    })
    allowed = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={
        "status": "physician_approved", "note": "Reviewed plan and physics QA, approved for treatment.",
    })
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["phase"]["rt_sub_status"] == "physician_approved"
    assert allowed.json()["phase"]["physician_approval_note"] == "Reviewed plan and physics QA, approved for treatment."


def test_treatment_ready_still_requires_full_pipeline(client, onc_headers, physicist_headers, patient):
    """Regression: the existing linear-sequencing behavior for the rest of the pipeline
    (untouched by this batch) still holds once physician_approved is reached."""
    phase_id = _create_phase_at_physics_qa(client, onc_headers, physicist_headers, patient.id)
    client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_CHECKLIST, "note": "All clear.",
    })
    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": "physician_approved"})

    ready = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": "treatment_ready"})
    assert ready.status_code == 200, ready.text
    assert ready.json()["phase"]["rt_sub_status"] == "treatment_ready"

    fractions = client.get(f"/api/cca/radiation-phases/{phase_id}/fractions", headers=onc_headers)
    assert len(fractions.json()["fractions"]) == 15
