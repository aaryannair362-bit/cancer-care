"""
Worklist/dashboard follow-up round: Batch 7 (Treatment Completion, C.23) only had a
per-patient list of completions -- no org-wide Treatment Completion Worklist (reference
SCR-CMP-001), the screen a Medical Oncology service actually uses to see every patient
needing a completion review/summary/distribution across the whole unit.

summary_status/distribution_status are read straight off the linked TreatmentSummary/
TreatmentSummaryDistribution rows -- never computed.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@completion-worklist-test.com", role="CCAMedicalOncologist")


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id).first().id


def test_worklist_reflects_summary_and_distribution_status(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    completion_id = client.post("/api/cca/treatment-completions", headers=onc_headers, json={
        "patient_id": patient_id, "reason": "Planned course completed.", "completion_type": "Completed as Planned",
    }).json()["treatment_completion"]["id"]

    worklist_before = client.get("/api/cca/treatment-completions/worklist", headers=onc_headers)
    assert worklist_before.status_code == 200, worklist_before.text
    row = next(r for r in worklist_before.json()["worklist"] if r["completion_id"] == completion_id)
    assert row["patient_name"] is not None
    assert row["summary_status"] == "Not started"
    assert row["distribution_status"] == "Not sent"

    client.post(f"/api/cca/treatment-completions/{completion_id}/summary", headers=onc_headers, json={
        "clinician_synthesis": "Patient completed planned systemic therapy without major complication.",
    })
    summary_id = client.post(f"/api/cca/treatment-completions/{completion_id}/summary", headers=onc_headers, json={}).json()["treatment_summary"]["id"]
    client.post(f"/api/cca/treatment-summaries/{summary_id}/finalize", headers=onc_headers)

    worklist_after_finalize = client.get("/api/cca/treatment-completions/worklist", headers=onc_headers)
    row_after = next(r for r in worklist_after_finalize.json()["worklist"] if r["completion_id"] == completion_id)
    assert row_after["summary_status"] == "FINALIZED"
    assert row_after["distribution_status"] == "Not sent"

    dist = client.post(f"/api/cca/treatment-summaries/{summary_id}/distributions", headers=onc_headers, json={"recipient": "Dr. Referring GP"})
    dist_id = dist.json()["distribution"]["id"]

    worklist_pending = client.get("/api/cca/treatment-completions/worklist", headers=onc_headers)
    row_pending = next(r for r in worklist_pending.json()["worklist"] if r["completion_id"] == completion_id)
    assert row_pending["distribution_status"] == "Pending"

    client.post(f"/api/cca/treatment-summary-distributions/{dist_id}/acknowledge", headers=onc_headers)
    worklist_ack = client.get("/api/cca/treatment-completions/worklist", headers=onc_headers)
    row_ack = next(r for r in worklist_ack.json()["worklist"] if r["completion_id"] == completion_id)
    assert row_ack["distribution_status"] == "Acknowledged"


def test_worklist_is_org_scoped(client, onc_headers, db_session, oncologist, make_user, auth_headers):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    client.post("/api/cca/treatment-completions", headers=onc_headers, json={"patient_id": patient_id, "reason": "x"})

    other_onc = make_user(email="onc2@completion-worklist-test.com", role="CCAMedicalOncologist")
    other_headers = auth_headers(other_onc)
    other_worklist = client.get("/api/cca/treatment-completions/worklist", headers=other_headers)
    assert other_worklist.status_code == 200
    assert other_worklist.json()["worklist"] == []
