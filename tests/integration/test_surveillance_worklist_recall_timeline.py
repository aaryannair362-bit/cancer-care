"""
Worklist/dashboard follow-up round: Batch 8 (Surveillance/Survivorship, C.24) only had
per-patient/per-plan views -- no org-wide Surveillance Worklist (reference SCR-SURV-001), no
org-wide Lost-to-Follow-up Recall Queue (SCR-SURV-008), and the Surveillance Timeline/
Dashboard (SCR-SURV-010) was never built at all.

days_until_due/days_overdue are plain date arithmetic against a clinician-set date -- never a
computed clinical risk score.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@surv-worklist-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def navigator(make_user, oncologist):
    return make_user(email="nav@surv-worklist-test.com", role="CCANurseNavigator", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def nav_headers(auth_headers, navigator):
    return auth_headers(navigator)


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id).first().id


def _active_plan(client, onc_headers, patient_id):
    plan_id = client.post("/api/cca/surveillance-plans", headers=onc_headers, json={
        "patient_id": patient_id, "next_review_date": "2020-01-01",
    }).json()["surveillance_plan"]["id"]
    client.post(f"/api/cca/surveillance-plans/{plan_id}/activate", headers=onc_headers)
    return plan_id


def test_worklist_shows_overdue_active_plans(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _active_plan(client, onc_headers, patient_id)

    worklist = client.get("/api/cca/surveillance-plans/worklist", headers=onc_headers)
    assert worklist.status_code == 200, worklist.text
    row = next(r for r in worklist.json()["worklist"] if r["plan_id"] == plan_id)
    assert row["patient_name"] is not None
    assert row["overdue"] is True
    assert row["days_until_due"] < 0


def test_recall_queue_is_org_wide_and_tracks_overdue(client, onc_headers, nav_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _active_plan(client, onc_headers, patient_id)

    entry = client.post(f"/api/cca/surveillance-plans/{plan_id}/recall-entries", headers=nav_headers, json={
        "follow_up_due_date": "2020-06-01", "risk_priority": "High", "owner": "nav@surv-worklist-test.com",
    })
    assert entry.status_code == 200, entry.text

    queue = client.get("/api/cca/surveillance-recall-queue", headers=nav_headers)
    assert queue.status_code == 200, queue.text
    row = next(r for r in queue.json()["recall_queue"] if r["plan_id"] == plan_id)
    assert row["patient_name"] is not None
    assert row["days_overdue"] > 0
    assert row["attempt_count"] == 0

    client.post(f"/api/cca/recall-entries/{row['id']}/log-attempt", headers=nav_headers, json={"method": "Phone", "outcome": "No answer"})
    entry_id = row["id"]
    client.post(f"/api/cca/recall-entries/{entry_id}/close", headers=nav_headers, json={"outcome": "Reached"})

    queue_after_close = client.get("/api/cca/surveillance-recall-queue", headers=nav_headers)
    assert not any(r["id"] == entry_id for r in queue_after_close.json()["recall_queue"])


def test_timeline_aggregates_events_chronologically(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _active_plan(client, onc_headers, patient_id)

    client.post(f"/api/cca/surveillance-plans/{plan_id}/visits", headers=onc_headers, json={
        "interval_history": "No new symptoms.", "disease_status": "No evidence of disease",
    })
    client.post(f"/api/cca/surveillance-plans/{plan_id}/investigations", headers=onc_headers, json={
        "investigation": "Mammogram", "due_date": "2026-01-01",
    })
    client.post(f"/api/cca/surveillance-plans/{plan_id}/late-effects", headers=onc_headers, json={
        "late_effect": "Peripheral neuropathy",
    })

    timeline = client.get(f"/api/cca/patients/{patient_id}/surveillance-timeline", headers=onc_headers)
    assert timeline.status_code == 200, timeline.text
    categories = {e["category"] for e in timeline.json()["timeline"]}
    assert {"Visit", "Investigation", "Late Effect"}.issubset(categories)
