"""
Tests for the PRE / Patient Relations Executive role (7 Role/Module Updates developer
handoff) -- a strictly non-clinical, operational role: appointment coordination, its own
coordination-task feed, and a hard 403 on the clinical case summary (data/specs/
CCA_Demo_Spec_v1.0.md's "PRE / Patient Navigation": "No clinical detail is visible").
"""
from datetime import datetime

import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CarePlanTask


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@prehosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def pre(make_user, oncologist):
    return make_user(email="pre@prehosp.com", role="CCAPatientRelationsExecutive", organization_id=oncologist.organization_id)


@pytest.fixture
def patient_liaison(make_user, oncologist):
    return make_user(email="liaison@prehosp.com", role="CCAPatientLiaison", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id).first().id


def test_pre_cannot_see_clinical_case_summary(client, auth_headers, db_session, oncologist, pre):
    """Checklist item 09 / AC-17: PRE gets a hard 403 on the clinical case summary, the same
    way Front Desk does -- not a narrower projection, no clinical detail at all."""
    patient_id = _patient_id(db_session, oncologist.organization_id)
    resp = client.get(f"/api/cca/patients/{patient_id}/case-summary", headers=auth_headers(pre))
    assert resp.status_code == 403


def test_pre_can_coordinate_appointments(client, auth_headers, db_session, oncologist, pre):
    """PRE gets the same hospital-wide appointment-coordination write access as Patient
    Liaison (checklist item 09)."""
    patient_id = _patient_id(db_session, oncologist.organization_id)
    pre_headers = auth_headers(pre)

    created = client.post("/api/cca/coordination/appointments", headers=pre_headers, json={
        "patient_id": patient_id, "department": "Radiology", "purpose": "Follow-up CT",
        "scheduled_at": "2026-10-01T09:00:00",
    })
    assert created.status_code == 201, created.text
    appt_id = created.json()["appointment"]["id"]

    listed = client.get("/api/cca/coordination/appointments", headers=pre_headers).json()["results"]
    assert any(a["id"] == appt_id for a in listed)

    updated = client.patch(f"/api/cca/coordination/appointments/{appt_id}", headers=pre_headers, json={"status": "Confirmed"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["appointment"]["status"] == "Confirmed"


def test_pre_task_feed_shows_only_care_coordination_owned_tasks(client, auth_headers, db_session, oncologist, pre):
    """PRE's/Patient Liaison's coordination task feed (cca_coordination.py's coordination_tasks)
    filters to owner_role=CARE_COORDINATION by default -- a clinical-team-owned task (e.g. the
    CARE_PLAN_TASK_BLOCKED escalation) must not surface there."""
    patient_id = _patient_id(db_session, oncologist.organization_id)
    coordination_task = CarePlanTask(
        patient_id=patient_id, description="Confirm transport for next appointment",
        owner_id="", owner_name="Patient Relations", due_date=datetime.utcnow(), status="OPEN",
        owner_role="CARE_COORDINATION", category="COORDINATION",
    )
    clinical_task = CarePlanTask(
        patient_id=patient_id, description="Review escalated barrier",
        owner_id="", owner_name="Treating Team", due_date=datetime.utcnow(), status="OPEN",
        owner_role="TREATING_ONCOLOGIST", category="CLINICAL_REVIEW",
    )
    db_session.add_all([coordination_task, clinical_task])
    db_session.commit()

    pre_headers = auth_headers(pre)
    feed = client.get("/api/cca/coordination/tasks", headers=pre_headers).json()["tasks"]
    ids = {t["id"] for t in feed}
    assert coordination_task.id in ids
    assert clinical_task.id not in ids


def test_pre_can_resolve_its_own_coordination_task_but_not_a_clinical_one(client, auth_headers, db_session, oncologist, pre):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    coordination_task = CarePlanTask(
        patient_id=patient_id, description="Confirm transport", owner_id="", owner_name="Patient Relations",
        due_date=datetime.utcnow(), status="OPEN", owner_role="CARE_COORDINATION", category="COORDINATION",
    )
    clinical_task = CarePlanTask(
        patient_id=patient_id, description="Review escalated barrier", owner_id="", owner_name="Treating Team",
        due_date=datetime.utcnow(), status="OPEN", owner_role="TREATING_ONCOLOGIST", category="CLINICAL_REVIEW",
    )
    db_session.add_all([coordination_task, clinical_task])
    db_session.commit()
    pre_headers = auth_headers(pre)

    resolved = client.post(f"/api/cca/tasks/{coordination_task.id}/resolve", headers=pre_headers)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["task"]["status"] == "RESOLVED"

    forbidden = client.post(f"/api/cca/tasks/{clinical_task.id}/resolve", headers=pre_headers)
    assert forbidden.status_code == 403


def test_pre_denied_patient_liaisons_deeper_coordination_case_write_actions(client, auth_headers, db_session, oncologist, pre):
    """PRE's scope is deliberately narrower than Patient Liaison's -- barrier/contact-log/
    education-record/next-action writes on a coordination case stay Patient-Liaison-only."""
    patient_id = _patient_id(db_session, oncologist.organization_id)
    pre_headers = auth_headers(pre)
    forbidden = client.post("/api/cca/coordination/cases", headers=pre_headers, json={"patient_id": patient_id})
    assert forbidden.status_code == 403
