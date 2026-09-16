"""
Radiation Oncology / Radiation Physicist missing-development round, Batch 3: the Treatment
Planning Workspace (plan identity/version/calculation record/three-person reviewer chain,
supersede-not-overwrite versioning), structured dosimetric review, patient-specific QA
(the real measurement record backing the physics_qa_checklist's "patient_specific_qa_review"
attestation), and Radiation Prescription Verification with its mismatch/return-to-RO workflow.

Previously CCARadiationPhase itself stood in for "the plan" with no version identity, no
numeric dosimetric review, no patient-specific QA measurement record, and nothing requiring
the physicist to explicitly attest the planning request matches the signed prescription.

Never tests a computed dose/MU/tolerance value -- every metric/measurement field here is
physicist-typed reference text, matching this repo's standing rule.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="ro@rt-planning-workspace-test.com", role="CCARadiationOncologist")


@pytest.fixture
def physicist(make_user, oncologist):
    return make_user(email="physicist@rt-planning-workspace-test.com", role="CCARadiationPhysicist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def physicist_headers(auth_headers, physicist):
    return auth_headers(physicist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="RT-PLANWS-0001", name="RT Planning Workspace Test Patient", age=55, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _phase_ready_for_planning(client, onc_headers, physicist_headers, patient_id):
    """Advances a fresh phase to "contouring" -- the point right before the planning gate --
    via simulation-ready + structure-set-approved, same as Batch 2's own test helper."""
    client.post(f"/api/cca/patients/{patient_id}/radiation-consultations", headers=onc_headers, json={"cied_present": False})
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "diagnosis": "Cervical Cancer", "intent": "Curative", "technique": "VMAT",
    }).json()["radiation_prescription"]["id"]
    phase_id = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/phases", headers=onc_headers, json={
        "label": "Pelvis", "treatment_site": "Pelvis", "total_prescribed_dose_gy": 45,
        "dose_per_fraction_gy": 1.8, "number_of_fractions": 25,
    }).json()["phase"]["id"]
    sim_id = client.post(f"/api/cca/radiation-phases/{phase_id}/simulation", headers=physicist_headers, json={}).json()["simulation"]["id"]
    client.post(f"/api/cca/radiation-simulation-records/{sim_id}/review", headers=physicist_headers, json={"readiness_status": "Ready"})
    structure_set_id = client.post(f"/api/cca/radiation-phases/{phase_id}/structure-sets", headers=physicist_headers, json={
        "target_volumes": {"PTV": "defined"},
    }).json()["structure_set"]["id"]
    client.post(f"/api/cca/radiation-structure-sets/{structure_set_id}/review", headers=physicist_headers, json={"status": "Approved"})
    for status in ("simulation_pending", "simulation_complete", "contouring"):
        r = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": status})
        assert r.status_code == 200, r.text
    return phase_id


def test_planning_blocked_without_verified_prescription(client, onc_headers, physicist_headers, patient):
    phase_id = _phase_ready_for_planning(client, onc_headers, physicist_headers, patient.id)

    blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    assert blocked.status_code == 409

    missing_comments = client.post(f"/api/cca/radiation-phases/{phase_id}/prescription-verification", headers=physicist_headers, json={"outcome": "Mismatch"})
    assert missing_comments.status_code == 422

    mismatch = client.post(f"/api/cca/radiation-phases/{phase_id}/prescription-verification", headers=physicist_headers, json={
        "outcome": "Mismatch", "comments": "Planned laterality does not match the signed prescription.",
    })
    assert mismatch.status_code == 201, mismatch.text
    verification_id = mismatch.json()["prescription_verification"]["id"]
    assert mismatch.json()["prescription_verification"]["returned_to_ro"] is True

    still_blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    assert still_blocked.status_code == 409  # unresolved mismatch

    missing_note = client.post(f"/api/cca/radiation-prescription-verifications/{verification_id}/resolve", headers=onc_headers, json={})
    assert missing_note.status_code == 422

    resolved = client.post(f"/api/cca/radiation-prescription-verifications/{verification_id}/resolve", headers=onc_headers, json={
        "resolved_note": "Re-confirmed laterality with the treating oncologist -- prescription is correct as signed.",
    })
    assert resolved.status_code == 200, resolved.text

    advanced = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    assert advanced.status_code == 200, advanced.text


def test_verified_outcome_unblocks_planning_directly(client, onc_headers, physicist_headers, patient):
    phase_id = _phase_ready_for_planning(client, onc_headers, physicist_headers, patient.id)
    verified = client.post(f"/api/cca/radiation-phases/{phase_id}/prescription-verification", headers=physicist_headers, json={"outcome": "Verified"})
    assert verified.status_code == 201, verified.text
    advanced = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    assert advanced.status_code == 200, advanced.text


def test_plan_version_lifecycle_and_supersede_chain(client, onc_headers, physicist_headers, patient):
    phase_id = _phase_ready_for_planning(client, onc_headers, physicist_headers, patient.id)
    client.post(f"/api/cca/radiation-phases/{phase_id}/prescription-verification", headers=physicist_headers, json={"outcome": "Verified"})
    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})

    v1 = client.post(f"/api/cca/radiation-phases/{phase_id}/plan-versions", headers=physicist_headers, json={
        "plan_name": "Initial VMAT plan", "technique": "VMAT", "calculation_algorithm": "AcurosXB",
    })
    assert v1.status_code == 201, v1.text
    v1_id = v1.json()["plan_version"]["id"]
    assert v1.json()["plan_version"]["version_no"] == 1
    assert v1.json()["plan_version"]["status"] == "Draft"

    blocked_qa = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "physics_qa"})
    assert blocked_qa.status_code == 409  # no Approved plan version yet

    approve_before_check = client.post(f"/api/cca/radiation-plan-versions/{v1_id}/approve", headers=physicist_headers)
    assert approve_before_check.status_code == 409

    checked = client.post(f"/api/cca/radiation-plan-versions/{v1_id}/check", headers=physicist_headers)
    assert checked.status_code == 200
    assert checked.json()["plan_version"]["status"] == "Checked"

    approved = client.post(f"/api/cca/radiation-plan-versions/{v1_id}/approve", headers=physicist_headers)
    assert approved.status_code == 200
    assert approved.json()["plan_version"]["status"] == "Approved"

    active = client.get(f"/api/cca/radiation-phases/{phase_id}/plan-versions/active", headers=physicist_headers)
    assert active.json()["active_plan_version"]["id"] == v1_id

    now_ready_for_qa = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "physics_qa"})
    assert now_ready_for_qa.status_code == 200, now_ready_for_qa.text

    # A revised plan (v2) supersedes v1 rather than overwriting it.
    v2_id = client.post(f"/api/cca/radiation-phases/{phase_id}/plan-versions", headers=physicist_headers, json={
        "plan_name": "Revised VMAT plan after re-simulation",
    }).json()["plan_version"]["id"]
    client.post(f"/api/cca/radiation-plan-versions/{v2_id}/check", headers=physicist_headers)
    v2_approved = client.post(f"/api/cca/radiation-plan-versions/{v2_id}/approve", headers=physicist_headers)
    assert v2_approved.status_code == 200
    assert v2_approved.json()["plan_version"]["supersedes_id"] == v1_id

    versions = client.get(f"/api/cca/radiation-phases/{phase_id}/plan-versions", headers=physicist_headers).json()["plan_versions"]
    assert len(versions) == 2
    v1_row = next(v for v in versions if v["id"] == v1_id)
    assert v1_row["status"] == "Superseded"

    active_after = client.get(f"/api/cca/radiation-phases/{phase_id}/plan-versions/active", headers=physicist_headers)
    assert active_after.json()["active_plan_version"]["id"] == v2_id


def test_dosimetric_review_fail_blocks_physics_qa_approval(client, onc_headers, physicist_headers, patient):
    phase_id = _phase_ready_for_planning(client, onc_headers, physicist_headers, patient.id)
    client.post(f"/api/cca/radiation-phases/{phase_id}/prescription-verification", headers=physicist_headers, json={"outcome": "Verified"})
    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    plan_version_id = client.post(f"/api/cca/radiation-phases/{phase_id}/plan-versions", headers=physicist_headers, json={}).json()["plan_version"]["id"]
    client.post(f"/api/cca/radiation-plan-versions/{plan_version_id}/check", headers=physicist_headers)
    client.post(f"/api/cca/radiation-plan-versions/{plan_version_id}/approve", headers=physicist_headers)
    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "physics_qa"})

    missing_comments = client.post(f"/api/cca/radiation-phases/{phase_id}/dosimetric-review", headers=physicist_headers, json={
        "plan_version_id": plan_version_id, "outcome": "Fail",
    })
    assert missing_comments.status_code == 422

    failed = client.post(f"/api/cca/radiation-phases/{phase_id}/dosimetric-review", headers=physicist_headers, json={
        "plan_version_id": plan_version_id, "outcome": "Fail",
        "oar_dose_metrics": [{"structure": "Bladder", "metric": "V45", "value": "62%"}],
        "comments": "Bladder V45 exceeds institutional constraint -- replan required.",
    })
    assert failed.status_code == 201, failed.text

    full_checklist = {
        "prescription_plan_concordance": True, "dose_volume_constraint_review": True,
        "target_oar_coverage_review": True, "machine_deliverability_review": True,
        "patient_specific_qa_review": True, "independent_dose_calc_verified": True,
    }
    client.post(f"/api/cca/radiation-phases/{phase_id}/patient-specific-qa", headers=physicist_headers, json={
        "plan_version_id": plan_version_id, "outcome": "Pass",
    })
    blocked_qa = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": full_checklist, "note": "Attempting approval despite failed dosimetric review.",
    })
    assert blocked_qa.status_code == 409

    passed = client.post(f"/api/cca/radiation-phases/{phase_id}/dosimetric-review", headers=physicist_headers, json={
        "plan_version_id": plan_version_id, "outcome": "Pass",
    })
    assert passed.status_code == 201
    now_ok = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": full_checklist, "note": "Replan reviewed, constraints now met.",
    })
    assert now_ok.status_code == 200, now_ok.text


def test_patient_specific_qa_review_checklist_key_requires_a_passing_record(client, onc_headers, physicist_headers, patient):
    phase_id = _phase_ready_for_planning(client, onc_headers, physicist_headers, patient.id)
    client.post(f"/api/cca/radiation-phases/{phase_id}/prescription-verification", headers=physicist_headers, json={"outcome": "Verified"})
    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    plan_version_id = client.post(f"/api/cca/radiation-phases/{phase_id}/plan-versions", headers=physicist_headers, json={}).json()["plan_version"]["id"]
    client.post(f"/api/cca/radiation-plan-versions/{plan_version_id}/check", headers=physicist_headers)
    client.post(f"/api/cca/radiation-plan-versions/{plan_version_id}/approve", headers=physicist_headers)
    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "physics_qa"})

    full_checklist = {
        "prescription_plan_concordance": True, "dose_volume_constraint_review": True,
        "target_oar_coverage_review": True, "machine_deliverability_review": True,
        "patient_specific_qa_review": True, "independent_dose_calc_verified": True,
    }
    # No RadiationPatientSpecificQA record recorded at all yet.
    blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": full_checklist, "note": "No PSQA on file yet.",
    })
    assert blocked.status_code == 409

    # Waiving the key bypasses the requirement -- same as any other checklist item.
    waived = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved",
        "checklist": {**full_checklist, "patient_specific_qa_review": False},
        "waived_items": [{"item": "patient_specific_qa_review", "waived_by": "Chief Physicist", "reason": "Equipment unavailable, clinical urgency."}],
        "note": "Waived per department policy.",
    })
    assert waived.status_code == 200, waived.text


def test_patient_specific_qa_endpoint_requires_comments_on_non_pass(client, onc_headers, physicist_headers, patient):
    phase_id = _phase_ready_for_planning(client, onc_headers, physicist_headers, patient.id)
    missing_comments = client.post(f"/api/cca/radiation-phases/{phase_id}/patient-specific-qa", headers=physicist_headers, json={"outcome": "Fail"})
    assert missing_comments.status_code == 422
    ok = client.post(f"/api/cca/radiation-phases/{phase_id}/patient-specific-qa", headers=physicist_headers, json={
        "outcome": "Fail", "method": "ArcCHECK", "comments": "Gamma pass rate 88% -- below 95% threshold, plan under investigation.",
    })
    assert ok.status_code == 201, ok.text
