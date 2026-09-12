"""
Radiation feature completion round: the remaining Radiation (C.16/C.17) gaps found
verifying functional/dataflow parity against the reference spec -- Treatment Unit entity +
Schedule (SCR-RTT-001), Equipment QA Register (SCR-PHY-009), Machine/Equipment Issue
(SCR-RTT-008), and In-Vivo Dosimetry (SCR-PHY-010).

Never tests a computed dosimetric comparison -- QA next_due_date is plain date arithmetic,
issue downtime_minutes is plain elapsed-time arithmetic, and in-vivo dosimetry's
expected/measured/outcome stay physicist-typed values, never compared by this repo.
"""
from datetime import datetime

import pytest

from app.models_cca import CCAPatient

_FULL_PHYSICS_QA_CHECKLIST = {
    "prescription_plan_concordance": True, "dose_volume_constraint_review": True,
    "target_oar_coverage_review": True, "machine_deliverability_review": True,
}


@pytest.fixture
def oncologist(make_user):
    return make_user(email="ro@rt-feature-test.com", role="CCARadiationOncologist")


@pytest.fixture
def physicist(make_user, oncologist):
    return make_user(email="physicist@rt-feature-test.com", role="CCARadiationPhysicist", organization_id=oncologist.organization_id)


@pytest.fixture
def radiologist(make_user, oncologist):
    return make_user(email="rtt@rt-feature-test.com", role="CCARadiologist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def physicist_headers(auth_headers, physicist):
    return auth_headers(physicist)


@pytest.fixture
def rtt_headers(auth_headers, radiologist):
    return auth_headers(radiologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="RT-FEATURE-0001", name="Radiation Feature Test Patient", age=61, sex="Male", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _phase_treatment_ready(client, onc_headers, physicist_headers, patient_id, number_of_fractions=2):
    # RO Consultation gate (gap review item 9) -- required before a course can be prescribed.
    client.post(f"/api/cca/patients/{patient_id}/radiation-consultations", headers=onc_headers, json={"cied_present": False})
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "diagnosis": "Lung Cancer", "intent": "Curative", "technique": "VMAT",
    }).json()["radiation_prescription"]["id"]
    phase_id = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/phases", headers=onc_headers, json={
        "label": "Lung", "treatment_site": "Lung", "total_prescribed_dose_gy": 40,
        "dose_per_fraction_gy": 20, "number_of_fractions": number_of_fractions,
    }).json()["phase"]["id"]
    for status in ("simulation_pending", "simulation_complete", "contouring", "planning", "physics_qa"):
        client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": status})
    client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_PHYSICS_QA_CHECKLIST, "note": "All clear.",
    })
    for status in ("physician_approved", "treatment_ready"):
        client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": status})
    return phase_id


# ---------------------------------------------------------------------------
# Treatment Unit + Schedule
# ---------------------------------------------------------------------------

def test_treatment_unit_registration_and_schedule(client, onc_headers, physicist_headers, rtt_headers, patient):
    forbidden = client.post("/api/cca/radiation-units", headers=onc_headers, json={"name": "Linac 1"})
    assert forbidden.status_code == 403

    unit = client.post("/api/cca/radiation-units", headers=physicist_headers, json={
        "name": "Linac 1", "unit_type": "Linear Accelerator",
    })
    assert unit.status_code == 201, unit.text
    unit_id = unit.json()["unit"]["id"]

    listed = client.get("/api/cca/radiation-units", headers=onc_headers)
    assert listed.status_code == 200
    assert any(u["id"] == unit_id for u in listed.json()["units"])

    phase_id = _phase_treatment_ready(client, onc_headers, physicist_headers, patient.id, number_of_fractions=2)
    fractions = client.get(f"/api/cca/radiation-phases/{phase_id}/fractions", headers=onc_headers).json()["fractions"]

    empty_schedule = client.get(f"/api/cca/radiation-units/{unit_id}/schedule?date=2026-09-15", headers=onc_headers)
    assert empty_schedule.status_code == 200
    assert empty_schedule.json()["slots"] == []

    sched = client.post(f"/api/cca/radiation-fractions/{fractions[0]['id']}/schedule", headers=rtt_headers, json={
        "treatment_unit_id": unit_id, "scheduled_date": "2026-09-15",
    })
    assert sched.status_code == 200, sched.text
    assert sched.json()["fraction"]["treatment_unit_id"] == unit_id

    schedule = client.get(f"/api/cca/radiation-units/{unit_id}/schedule?date=2026-09-15", headers=onc_headers)
    assert schedule.status_code == 200
    slots = schedule.json()["slots"]
    assert len(slots) == 1
    assert slots[0]["patient_id"] == patient.id
    assert slots[0]["treatment_site"] == "Lung"
    assert schedule.json()["alerts"]["unit_qa_overdue"] is False


# ---------------------------------------------------------------------------
# Equipment QA Register
# ---------------------------------------------------------------------------

def test_equipment_qa_register_and_overdue_flag(client, physicist_headers, onc_headers):
    unit_id = client.post("/api/cca/radiation-units", headers=physicist_headers, json={"name": "Linac 2"}).json()["unit"]["id"]

    bad_freq = client.post(f"/api/cca/radiation-units/{unit_id}/qa-records", headers=physicist_headers, json={
        "test_name": "Output Constancy", "frequency": "Hourly",
    })
    assert bad_freq.status_code == 422

    overdue = client.post(f"/api/cca/radiation-units/{unit_id}/qa-records", headers=physicist_headers, json={
        "test_name": "Output Constancy", "frequency": "Daily", "last_performed_date": "2020-01-01",
        "result": "Within tolerance.", "pass_fail": "Pass",
    })
    assert overdue.status_code == 201, overdue.text
    assert overdue.json()["record"]["overdue"] is True
    assert overdue.json()["record"]["next_due_date"] == "2020-01-02"

    forbidden = client.post(f"/api/cca/radiation-units/{unit_id}/qa-records", headers=onc_headers, json={
        "test_name": "Mechanical Isocenter", "frequency": "Monthly",
    })
    assert forbidden.status_code == 403

    listed = client.get(f"/api/cca/radiation-units/{unit_id}/qa-records", headers=onc_headers)
    assert listed.status_code == 200
    assert len(listed.json()["records"]) == 1

    register = client.get("/api/cca/radiation-units/qa-register", headers=onc_headers)
    assert register.status_code == 200
    assert unit_id in register.json()["overdue_unit_ids"]


# ---------------------------------------------------------------------------
# Machine / Equipment Issue
# ---------------------------------------------------------------------------

def test_equipment_issue_lifecycle_and_affected_patients(client, physicist_headers, onc_headers, rtt_headers, patient):
    unit_id = client.post("/api/cca/radiation-units", headers=physicist_headers, json={"name": "Linac 3"}).json()["unit"]["id"]
    phase_id = _phase_treatment_ready(client, onc_headers, physicist_headers, patient.id, number_of_fractions=1)
    fractions = client.get(f"/api/cca/radiation-phases/{phase_id}/fractions", headers=onc_headers).json()["fractions"]
    today = datetime.utcnow().date().isoformat()
    client.post(f"/api/cca/radiation-fractions/{fractions[0]['id']}/schedule", headers=rtt_headers, json={
        "treatment_unit_id": unit_id, "scheduled_date": today,
    })

    bad_category = client.post(f"/api/cca/radiation-units/{unit_id}/issues", headers=rtt_headers, json={
        "description": "Beam interlock tripped.", "category": "Weather",
    })
    assert bad_category.status_code == 422

    issue = client.post(f"/api/cca/radiation-units/{unit_id}/issues", headers=rtt_headers, json={
        "description": "Beam interlock tripped.", "category": "Interlock",
    })
    assert issue.status_code == 201, issue.text
    issue_id = issue.json()["issue"]["id"]
    affected = issue.json()["issue"]["affected_patients"]
    assert any(a["patient_id"] == patient.id for a in affected)

    notified = client.post(f"/api/cca/radiation-issues/{issue_id}/notify-physics", headers=rtt_headers, json={
        "physics_notified_name": "Dr. Physicist",
    })
    assert notified.status_code == 200
    assert notified.json()["issue"]["physics_notified_at"] is not None

    forbidden_resolve = client.post(f"/api/cca/radiation-issues/{issue_id}/resolve", headers=rtt_headers, json={
        "resolution": "Interlock reset.", "return_to_service_checks": "Beam output verified.",
    })
    assert forbidden_resolve.status_code == 403

    missing_fields = client.post(f"/api/cca/radiation-issues/{issue_id}/resolve", headers=physicist_headers, json={})
    assert missing_fields.status_code == 422

    resolved = client.post(f"/api/cca/radiation-issues/{issue_id}/resolve", headers=physicist_headers, json={
        "resolution": "Interlock reset and beam re-qualified.", "return_to_service_checks": "Output/flatness re-verified within tolerance.",
    })
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()["issue"]
    assert body["status"] == "RESOLVED"
    assert body["downtime_minutes"] is not None
    assert body["downtime_minutes"] >= 0

    listed = client.get(f"/api/cca/radiation-units/{unit_id}/issues", headers=onc_headers)
    assert listed.status_code == 200
    assert len(listed.json()["issues"]) == 1


# ---------------------------------------------------------------------------
# In-Vivo Dosimetry
# ---------------------------------------------------------------------------

def test_invivo_dosimetry_attestation(client, physicist_headers, onc_headers, rtt_headers, patient):
    phase_id = _phase_treatment_ready(client, onc_headers, physicist_headers, patient.id, number_of_fractions=1)
    fraction_id = client.get(f"/api/cca/radiation-phases/{phase_id}/fractions", headers=onc_headers).json()["fractions"][0]["id"]

    forbidden = client.post(f"/api/cca/radiation-fractions/{fraction_id}/invivo-dosimetry", headers=rtt_headers, json={})
    assert forbidden.status_code == 403

    bad_outcome = client.post(f"/api/cca/radiation-fractions/{fraction_id}/invivo-dosimetry", headers=physicist_headers, json={
        "outcome": "Roughly Fine",
    })
    assert bad_outcome.status_code == 422

    missing_action = client.post(f"/api/cca/radiation-fractions/{fraction_id}/invivo-dosimetry", headers=physicist_headers, json={
        "required": True, "method": "OSLD", "expected_dose": "2.0 Gy", "measured_dose": "2.4 Gy",
        "outcome": "Out of Tolerance",
    })
    assert missing_action.status_code == 422

    ok = client.post(f"/api/cca/radiation-fractions/{fraction_id}/invivo-dosimetry", headers=physicist_headers, json={
        "required": True, "method": "OSLD", "detector_calibration": "Cal-2026-08", "expected_dose": "2.0 Gy",
        "measured_dose": "2.4 Gy", "outcome": "Out of Tolerance", "action_on_out_of_tolerance": "Re-measured, replanned next fraction.",
        "reviewed_by": "Dr. Physicist Lead",
    })
    assert ok.status_code == 201, ok.text
    assert ok.json()["dosimetry"]["outcome"] == "Out of Tolerance"

    listed = client.get(f"/api/cca/radiation-fractions/{fraction_id}/invivo-dosimetry", headers=onc_headers)
    assert listed.status_code == 200
    assert len(listed.json()["dosimetry"]) == 1
