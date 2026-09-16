"""
Radiation Oncology / Radiation Physicist missing-development round, Batch 4: physics worklist
ergonomics (priority/ownership/due-dates), the release-readiness checklist aggregation, the
physics operational dashboard, and machine-availability enforcement at fraction scheduling.

Never tests a computed dose/variance value -- every new field here is a plain attribute, a
structural readiness check, or a count, matching this repo's standing rule.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="ro@rt-dashboard-test.com", role="CCARadiationOncologist")


@pytest.fixture
def physicist(make_user, oncologist):
    return make_user(email="physicist@rt-dashboard-test.com", role="CCARadiationPhysicist", organization_id=oncologist.organization_id)


@pytest.fixture
def other_physicist(make_user, oncologist):
    return make_user(email="physicist2@rt-dashboard-test.com", role="CCARadiationPhysicist", organization_id=oncologist.organization_id)


@pytest.fixture
def rtt(make_user, oncologist):
    return make_user(email="rtt@rt-dashboard-test.com", role="CCARadiationTechnologist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def physicist_headers(auth_headers, physicist):
    return auth_headers(physicist)


@pytest.fixture
def other_physicist_headers(auth_headers, other_physicist):
    return auth_headers(other_physicist)


@pytest.fixture
def rtt_headers(auth_headers, rtt):
    return auth_headers(rtt)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="RT-DASH-0001", name="RT Dashboard Test Patient", age=63, sex="Male", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_phase(client, onc_headers, patient_id):
    client.post(f"/api/cca/patients/{patient_id}/radiation-consultations", headers=onc_headers, json={"cied_present": False})
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "diagnosis": "Lung Cancer", "intent": "Curative", "technique": "VMAT",
    }).json()["radiation_prescription"]["id"]
    phase = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/phases", headers=onc_headers, json={
        "label": "Lung", "treatment_site": "Lung", "total_prescribed_dose_gy": 40,
        "dose_per_fraction_gy": 20, "number_of_fractions": 2,
    }).json()["phase"]
    return phase["id"]


def test_worklist_priority_assignment_and_due_dates(client, onc_headers, physicist_headers, patient):
    phase_id = _create_phase(client, onc_headers, patient.id)
    phase = client.get(f"/api/cca/radiation-phases/{phase_id}", headers=onc_headers).json()["phase"]
    assert phase["priority"] == "Routine"
    assert phase["assigned_physicist"] is None

    updated = client.patch(f"/api/cca/radiation-phases/{phase_id}/worklist", headers=physicist_headers, json={
        "priority": "Urgent", "assign_to_self": True, "physics_review_due_date": "2026-09-20", "treatment_start_due_date": "2026-09-25",
    })
    assert updated.status_code == 200, updated.text
    body = updated.json()["phase"]
    assert body["priority"] == "Urgent"
    assert body["assigned_physicist"]
    assert body["physics_review_due_date"] == "2026-09-20"
    assert body["treatment_start_due_date"] == "2026-09-25"

    forbidden = client.patch(f"/api/cca/radiation-phases/{phase_id}/worklist", headers=onc_headers, json={"priority": "Urgent"})
    assert forbidden.status_code == 403


def test_worklist_filters_by_priority_and_mine(client, onc_headers, physicist_headers, other_physicist_headers, patient):
    urgent_id = _create_phase(client, onc_headers, patient.id)
    routine_id = _create_phase(client, onc_headers, patient.id)
    client.patch(f"/api/cca/radiation-phases/{urgent_id}/worklist", headers=physicist_headers, json={"priority": "Urgent", "assign_to_self": True})

    urgent_only = client.get("/api/cca/radiation-phases/worklist", headers=physicist_headers, params={"priority": "Urgent"}).json()["worklist"]
    assert all(p["priority"] == "Urgent" for p in urgent_only)
    assert any(p["id"] == urgent_id for p in urgent_only)
    assert not any(p["id"] == routine_id for p in urgent_only)

    mine = client.get("/api/cca/radiation-phases/worklist", headers=physicist_headers, params={"mine": True}).json()["worklist"]
    assert any(p["id"] == urgent_id for p in mine)

    other_mine = client.get("/api/cca/radiation-phases/worklist", headers=other_physicist_headers, params={"mine": True}).json()["worklist"]
    assert not any(p["id"] == urgent_id for p in other_mine)


def test_release_readiness_reflects_actual_signals(client, onc_headers, physicist_headers, patient):
    phase_id = _create_phase(client, onc_headers, patient.id)
    empty = client.get(f"/api/cca/radiation-phases/{phase_id}/release-readiness", headers=onc_headers)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["overall_ready"] is False
    assert body["prescription_verified"] is False
    assert body["simulation_verified"] is False

    client.post(f"/api/cca/radiation-phases/{phase_id}/prescription-verification", headers=physicist_headers, json={"outcome": "Verified"})
    after_verify = client.get(f"/api/cca/radiation-phases/{phase_id}/release-readiness", headers=onc_headers).json()
    assert after_verify["prescription_verified"] is True
    assert after_verify["overall_ready"] is False  # still missing simulation/contours/plan/QA/machine


def test_physics_dashboard_requires_physicist_or_admin_and_returns_counts(client, onc_headers, physicist_headers, patient):
    forbidden = client.get("/api/cca/radiation-physics-dashboard", headers=onc_headers)
    assert forbidden.status_code == 403

    phase_id = _create_phase(client, onc_headers, patient.id)
    dashboard = client.get("/api/cca/radiation-physics-dashboard", headers=physicist_headers)
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    for key in ("planning_queue_count", "qa_queue_count", "release_queue_count", "machines", "open_equipment_issues_count", "recent_audit"):
        assert key in body
    assert isinstance(body["recent_audit"], list)
    assert any(e["event_type"] == "RADIATION_PHASE_CREATED" for e in body["recent_audit"])


def test_fraction_scheduling_blocked_on_inactive_or_qa_overdue_unit(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase(client, onc_headers, patient.id)
    # Advance the phase to treatment_ready so fractions exist to schedule (mirrors other
    # radiation test files' own _phase_treatment_ready helpers).
    sim_id = client.post(f"/api/cca/radiation-phases/{phase_id}/simulation", headers=physicist_headers, json={}).json()["simulation"]["id"]
    client.post(f"/api/cca/radiation-simulation-records/{sim_id}/review", headers=physicist_headers, json={"readiness_status": "Ready"})
    structure_set_id = client.post(f"/api/cca/radiation-phases/{phase_id}/structure-sets", headers=physicist_headers, json={}).json()["structure_set"]["id"]
    client.post(f"/api/cca/radiation-structure-sets/{structure_set_id}/review", headers=physicist_headers, json={"status": "Approved"})
    client.post(f"/api/cca/radiation-phases/{phase_id}/prescription-verification", headers=physicist_headers, json={"outcome": "Verified"})
    for status in ("simulation_pending", "simulation_complete", "contouring", "planning"):
        client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": status})
    plan_version_id = client.post(f"/api/cca/radiation-phases/{phase_id}/plan-versions", headers=physicist_headers, json={}).json()["plan_version"]["id"]
    client.post(f"/api/cca/radiation-plan-versions/{plan_version_id}/check", headers=physicist_headers)
    client.post(f"/api/cca/radiation-plan-versions/{plan_version_id}/approve", headers=physicist_headers)
    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "physics_qa"})
    client.post(f"/api/cca/radiation-phases/{phase_id}/patient-specific-qa", headers=physicist_headers, json={"outcome": "Pass"})
    full_checklist = {
        "prescription_plan_concordance": True, "dose_volume_constraint_review": True,
        "target_oar_coverage_review": True, "machine_deliverability_review": True,
        "patient_specific_qa_review": True, "independent_dose_calc_verified": True,
    }
    client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": full_checklist, "note": "Cleared.",
    })
    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": "physician_approved"})
    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": "treatment_ready"})

    fraction_id = client.get(f"/api/cca/radiation-phases/{phase_id}/fractions", headers=onc_headers).json()["fractions"][0]["id"]

    inactive_unit_id = client.post("/api/cca/radiation-units", headers=physicist_headers, json={"name": "Linac Down", "status": "Out of Service"}).json()["unit"]["id"]
    blocked_inactive = client.post(f"/api/cca/radiation-fractions/{fraction_id}/schedule", headers=rtt_headers, json={
        "treatment_unit_id": inactive_unit_id, "scheduled_date": "2026-09-22",
    })
    assert blocked_inactive.status_code == 409

    overdue_unit_id = client.post("/api/cca/radiation-units", headers=physicist_headers, json={"name": "Linac Overdue"}).json()["unit"]["id"]
    client.post(f"/api/cca/radiation-units/{overdue_unit_id}/qa-records", headers=physicist_headers, json={
        "test_name": "Daily Output Check", "frequency": "Daily", "last_performed_date": "2020-01-01", "pass_fail": "Pass",
    })
    blocked_overdue = client.post(f"/api/cca/radiation-fractions/{fraction_id}/schedule", headers=rtt_headers, json={
        "treatment_unit_id": overdue_unit_id, "scheduled_date": "2026-09-22",
    })
    assert blocked_overdue.status_code == 409

    active_unit_id = client.post("/api/cca/radiation-units", headers=physicist_headers, json={"name": "Linac Up"}).json()["unit"]["id"]
    allowed = client.post(f"/api/cca/radiation-fractions/{fraction_id}/schedule", headers=rtt_headers, json={
        "treatment_unit_id": active_unit_id, "scheduled_date": "2026-09-22",
    })
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["fraction"]["treatment_unit_id"] == active_unit_id
