"""
Product 1 vs Product 2 Functional Gap Report, Batch 7: Treatment Completion (C.23).

Before this batch, this codebase had no end-of-treatment reconciliation at all --
TreatmentDayCompletion (existing) only closes a single day-care session, not a whole
course of care. Covers the DRAFT -> SIGNED lifecycle, modality reconciliation and
cumulative exposure child records, completion handoff + acceptance, and the Cancer
Treatment Summary's draft -> finalize -> distribute -> acknowledge chain.

Never tests a computed dose, threshold, or clinical judgment -- every value here is
either a clinician-typed string or a plain read-time aggregation of already-recorded
data (see _treatment_summary_derived's docstring in routers/cca.py).
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@completion-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def other_oncologist(make_user):
    """Deliberately a different organization -- the cross-org isolation test below relies
    on this NOT sharing oncologist's org, unlike front_desk (same-org, different role)."""
    return make_user(email="onc2@completion-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="fd@completion-test.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def other_onc_headers(auth_headers, other_oncologist):
    return auth_headers(other_oncologist)


@pytest.fixture
def fd_headers(auth_headers, front_desk):
    return auth_headers(front_desk)


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id).first().id


def test_create_requires_reason_and_clinician(client, onc_headers, fd_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)

    no_reason = client.post("/api/cca/treatment-completions", headers=onc_headers, json={"patient_id": patient_id})
    assert no_reason.status_code == 422

    non_clinician = client.post("/api/cca/treatment-completions", headers=fd_headers, json={"patient_id": patient_id, "reason": "Course finished."})
    assert non_clinician.status_code == 403

    ok = client.post("/api/cca/treatment-completions", headers=onc_headers, json={
        "patient_id": patient_id, "reason": "Planned 6 cycles delivered in full.",
        "completion_type": "Completed as Planned", "next_care_phase": "Surveillance",
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["treatment_completion"]["status"] == "DRAFT"


def test_full_completion_lifecycle(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    completion_id = client.post("/api/cca/treatment-completions", headers=onc_headers, json={
        "patient_id": patient_id, "reason": "Planned course completed.",
        "completion_type": "Completed as Planned", "next_care_phase": "Surveillance",
    }).json()["treatment_completion"]["id"]

    # Modality reconciliation
    mr = client.post(f"/api/cca/treatment-completions/{completion_id}/modality-records", headers=onc_headers, json={
        "modality": "Systemic", "planned_value": "6 cycles", "actual_value": "6 cycles", "completed": True,
    })
    assert mr.status_code == 200, mr.text

    # Cumulative exposure
    er = client.post(f"/api/cca/treatment-completions/{completion_id}/exposure-records", headers=onc_headers, json={
        "agent_or_modality": "Doxorubicin", "actual_cumulative_exposure": "as recorded on administration record",
        "unit": "mg/m2 (cumulative, clinician-read)", "late_effect_domain": "Cardiac",
    })
    assert er.status_code == 200, er.text

    detail = client.get(f"/api/cca/treatment-completions/{completion_id}", headers=onc_headers).json()
    assert len(detail["modality_records"]) == 1
    assert len(detail["exposure_records"]) == 1

    # Sign
    sign = client.post(f"/api/cca/treatment-completions/{completion_id}/sign", headers=onc_headers)
    assert sign.status_code == 200, sign.text
    assert sign.json()["treatment_completion"]["status"] == "SIGNED"

    # Cannot sign twice
    double_sign = client.post(f"/api/cca/treatment-completions/{completion_id}/sign", headers=onc_headers)
    assert double_sign.status_code == 409

    # Handoff
    handoff = client.post(f"/api/cca/treatment-completions/{completion_id}/handoffs", headers=onc_headers, json={
        "destination": "Surveillance", "handoff_summary": "Handing off to surveillance follow-up.",
        "owner": "onc@completion-test.com",
    })
    assert handoff.status_code == 200, handoff.text
    handoff_id = handoff.json()["handoff"]["id"]
    assert handoff.json()["handoff"]["acceptance_status"] == "Pending"

    accept = client.post(f"/api/cca/treatment-completion-handoffs/{handoff_id}/accept", headers=onc_headers)
    assert accept.status_code == 200
    assert accept.json()["handoff"]["acceptance_status"] == "Accepted"


def test_treatment_summary_finalize_and_distribution(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    completion_id = client.post("/api/cca/treatment-completions", headers=onc_headers, json={
        "patient_id": patient_id, "reason": "Course completed.",
    }).json()["treatment_completion"]["id"]

    # First call to /summary both creates and returns the draft (get-or-create).
    empty_draft = client.post(f"/api/cca/treatment-completions/{completion_id}/summary", headers=onc_headers, json={})
    assert empty_draft.status_code == 200, empty_draft.text
    summary_id = empty_draft.json()["treatment_summary"]["id"]
    assert empty_draft.json()["treatment_summary"]["status"] == "DRAFT"
    # Derived fields are present (read-time aggregation), not stored a second time.
    assert "diagnosis_staging_snapshot" in empty_draft.json()["treatment_summary"]

    # Finalize blocked without a synthesis narrative.
    blocked = client.post(f"/api/cca/treatment-summaries/{summary_id}/finalize", headers=onc_headers)
    assert blocked.status_code == 409

    updated = client.post(f"/api/cca/treatment-completions/{completion_id}/summary", headers=onc_headers, json={
        "clinician_synthesis": "Patient completed planned systemic therapy without major complication.",
    })
    assert updated.status_code == 200
    # Same summary row updated, not a duplicate.
    assert updated.json()["treatment_summary"]["id"] == summary_id

    # Distribution blocked before finalize.
    early_dist = client.post(f"/api/cca/treatment-summaries/{summary_id}/distributions", headers=onc_headers, json={"recipient": "Dr. Referring GP"})
    assert early_dist.status_code == 409

    finalize = client.post(f"/api/cca/treatment-summaries/{summary_id}/finalize", headers=onc_headers)
    assert finalize.status_code == 200, finalize.text
    assert finalize.json()["treatment_summary"]["status"] == "FINALIZED"

    # Cannot edit or re-finalize after finalization.
    edit_after_finalize = client.post(f"/api/cca/treatment-completions/{completion_id}/summary", headers=onc_headers, json={"clinician_synthesis": "changed"})
    assert edit_after_finalize.status_code == 409
    refinalize = client.post(f"/api/cca/treatment-summaries/{summary_id}/finalize", headers=onc_headers)
    assert refinalize.status_code == 409

    dist = client.post(f"/api/cca/treatment-summaries/{summary_id}/distributions", headers=onc_headers, json={
        "recipient": "Dr. Referring GP", "recipient_role": "Primary Care", "method": "Referral Letter",
        "acknowledgement_required": True,
    })
    assert dist.status_code == 200, dist.text
    assert dist.json()["distribution"]["status"] == "Sent"

    ack = client.post(f"/api/cca/treatment-summary-distributions/{dist.json()['distribution']['id']}/acknowledge", headers=onc_headers)
    assert ack.status_code == 200


def test_cross_org_isolation(client, onc_headers, other_onc_headers, db_session, oncologist, other_oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    completion_id = client.post("/api/cca/treatment-completions", headers=onc_headers, json={
        "patient_id": patient_id, "reason": "Course completed.",
    }).json()["treatment_completion"]["id"]

    cross_org = client.get(f"/api/cca/treatment-completions/{completion_id}", headers=other_onc_headers)
    assert cross_org.status_code == 404
