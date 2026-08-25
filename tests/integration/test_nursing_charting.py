"""
IV Fluid Management and Intake/Output Charting tests. Mirrors the access-control shape of the
existing vitals/nursing-notes tests (Nurse must be assigned, HeadNurse can act on any patient).
"""
import pytest


@pytest.fixture
def head_nurse(make_user):
    return make_user(email="head@charthosp.com", role="HeadNurse")


@pytest.fixture
def nurse(make_user, head_nurse):
    return make_user(email="nurse@charthosp.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def unassigned_nurse(make_user, head_nurse):
    return make_user(email="unassigned@charthosp.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def doctor(make_user, head_nurse):
    return make_user(email="doctor@charthosp.com", role="Doctor", organization_id=head_nurse.organization_id)


@pytest.fixture
def patient(head_nurse, nurse, db_session):
    from app.models import NurseAssignment, Patient

    p = Patient(name="Chart Patient", age=55, gender="F", ward="ICU", bed="1",
                organization_id=head_nurse.organization_id, created_by=head_nurse.id, status="Active")
    db_session.add(p)
    db_session.flush()
    db_session.add(NurseAssignment(patient_id=p.id, nurse_id=nurse.id, assigned_by=head_nurse.id, status="Active"))
    db_session.commit()
    db_session.refresh(p)
    return p


def _start_iv(client, headers, patient_id, **overrides):
    payload = {"patient_id": patient_id, "fluid_type": "Normal Saline 0.9%", "volume_ml": 1000, "rate_ml_per_hr": 100}
    payload.update(overrides)
    resp = client.post("/api/ipd/iv-fluids", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Role / assignment permissions
# ---------------------------------------------------------------------------

def test_assigned_nurse_and_head_nurse_can_start_iv(client, nurse, head_nurse, patient, auth_headers):
    for user in (nurse, head_nurse):
        resp = client.post("/api/ipd/iv-fluids",
                            json={"patient_id": patient.id, "fluid_type": "Ringer Lactate", "volume_ml": 500},
                            headers=auth_headers(user))
        assert resp.status_code == 201


def test_unassigned_nurse_cannot_chart(client, unassigned_nurse, patient, auth_headers):
    headers = auth_headers(unassigned_nurse)
    resp = client.post("/api/ipd/iv-fluids", json={"patient_id": patient.id, "fluid_type": "D5W", "volume_ml": 500}, headers=headers)
    assert resp.status_code == 403
    resp = client.post("/api/ipd/intake-output", json={"patient_id": patient.id, "entry_type": "Intake", "category": "Oral", "volume_ml": 200}, headers=headers)
    assert resp.status_code == 403


def test_doctor_cannot_chart(client, doctor, patient, auth_headers):
    resp = client.post("/api/ipd/iv-fluids", json={"patient_id": patient.id, "fluid_type": "D5W", "volume_ml": 500}, headers=auth_headers(doctor))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# IV Fluid lifecycle
# ---------------------------------------------------------------------------

def test_start_and_stop_iv_fluid(client, nurse, patient, auth_headers):
    headers = auth_headers(nurse)
    order = _start_iv(client, headers, patient.id)
    assert order["status"] == "Active"

    resp = client.patch(f"/api/ipd/iv-fluids/{order['id']}/stop", json={"notes": "Completed uneventfully"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "Completed"
    assert resp.json()["stopped_at"] is not None


def test_cannot_stop_already_completed_order(client, nurse, patient, auth_headers):
    headers = auth_headers(nurse)
    order = _start_iv(client, headers, patient.id)
    client.patch(f"/api/ipd/iv-fluids/{order['id']}/stop", json={}, headers=headers)
    resp = client.patch(f"/api/ipd/iv-fluids/{order['id']}/stop", json={}, headers=headers)
    assert resp.status_code == 400


def test_list_iv_fluids_filters_by_status(client, nurse, patient, auth_headers):
    headers = auth_headers(nurse)
    active_order = _start_iv(client, headers, patient.id, fluid_type="NS 0.9%")
    completed_order = _start_iv(client, headers, patient.id, fluid_type="RL")
    client.patch(f"/api/ipd/iv-fluids/{completed_order['id']}/stop", json={}, headers=headers)

    active = client.get("/api/ipd/iv-fluids", params={"patient_id": patient.id, "status": "Active"}, headers=headers).json()
    assert {o["id"] for o in active} == {active_order["id"]}


# ---------------------------------------------------------------------------
# Intake/Output charting and balance
# ---------------------------------------------------------------------------

def test_intake_output_balance_computed_correctly(client, nurse, patient, auth_headers):
    headers = auth_headers(nurse)
    client.post("/api/ipd/intake-output", json={"patient_id": patient.id, "entry_type": "Intake", "category": "Oral", "volume_ml": 300}, headers=headers)
    client.post("/api/ipd/intake-output", json={"patient_id": patient.id, "entry_type": "Intake", "category": "IV", "volume_ml": 500}, headers=headers)
    client.post("/api/ipd/intake-output", json={"patient_id": patient.id, "entry_type": "Output", "category": "Urine", "volume_ml": 400}, headers=headers)

    resp = client.get("/api/ipd/intake-output", params={"patient_id": patient.id}, headers=headers)
    body = resp.json()
    assert body["total_intake_ml"] == 800
    assert body["total_output_ml"] == 400
    assert body["balance_ml"] == 400
    assert len(body["entries"]) == 3


def test_intake_output_window_excludes_old_entries(client, nurse, patient, auth_headers, db_session):
    from datetime import datetime, timedelta
    from app.models import IntakeOutputRecord

    old = IntakeOutputRecord(
        organization_id=patient.organization_id, patient_id=patient.id, entry_type="Intake",
        category="Oral", volume_ml=1000, recorded_by=nurse.id,
        recorded_at=datetime.utcnow() - timedelta(hours=48),
    )
    db_session.add(old)
    db_session.commit()

    headers = auth_headers(nurse)
    client.post("/api/ipd/intake-output", json={"patient_id": patient.id, "entry_type": "Intake", "category": "Oral", "volume_ml": 200}, headers=headers)

    resp = client.get("/api/ipd/intake-output", params={"patient_id": patient.id, "hours": 24}, headers=headers)
    assert resp.json()["total_intake_ml"] == 200  # the 48h-old entry is excluded


def test_invalid_entry_type_rejected(client, nurse, patient, auth_headers):
    resp = client.post("/api/ipd/intake-output",
                        json={"patient_id": patient.id, "entry_type": "Sideways", "category": "Oral", "volume_ml": 100},
                        headers=auth_headers(nurse))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Multi-tenant / cross-patient isolation
# ---------------------------------------------------------------------------

def test_cannot_chart_another_orgs_patient(client, nurse, auth_headers, make_user, db_session):
    from app.models import Patient

    foreign_head = make_user(email="foreign.head@other.com", role="HeadNurse")
    foreign_patient = Patient(name="Foreign", age=40, gender="M", organization_id=foreign_head.organization_id,
                               created_by=foreign_head.id, status="Active")
    db_session.add(foreign_patient)
    db_session.commit()
    db_session.refresh(foreign_patient)

    resp = client.post("/api/ipd/iv-fluids",
                        json={"patient_id": foreign_patient.id, "fluid_type": "NS", "volume_ml": 500},
                        headers=auth_headers(nurse))
    assert resp.status_code == 404
