"""
Product 1 vs Product 2 Functional Gap Report, Batch 8: Surveillance / Survivorship (C.24).

Completely missing before this batch. Covers SurveillancePlan draft->activate, a signed
follow-up visit, a surveillance investigation, a late-effect record, the Patient
Survivorship Care Plan document's draft->issue lifecycle (with its derived section reusing
Batch 7's diagnosis/treatment aggregation), a recurrence-suspicion event actioned into
re-entry (flips CCAPatient.journey_state, a display label only -- never a strict enum),
and the lost-to-follow-up recall queue's contact-attempt log.

Never tests a computed dose, threshold, or clinical judgment.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@survivorship-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def navigator(make_user, oncologist):
    return make_user(email="nav@survivorship-test.com", role="CCANurseNavigator", organization_id=oncologist.organization_id)


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


def _make_plan(client, headers, patient_id):
    res = client.post("/api/cca/surveillance-plans", headers=headers, json={
        "patient_id": patient_id, "surveillance_intent": "Routine post-treatment surveillance.",
        "follow_up_frequency": "Every 3 months", "recurrence_red_flags": "New lump, unexplained weight loss.",
        "responsible_clinician": "onc@survivorship-test.com",
    })
    assert res.status_code == 200, res.text
    return res.json()["surveillance_plan"]["id"]


def test_plan_activate_and_supersede(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _make_plan(client, onc_headers, patient_id)
    assert client.get(f"/api/cca/surveillance-plans/{plan_id}", headers=onc_headers).json()["surveillance_plan"]["status"] == "DRAFT"

    activate = client.post(f"/api/cca/surveillance-plans/{plan_id}/activate", headers=onc_headers)
    assert activate.status_code == 200, activate.text
    assert activate.json()["surveillance_plan"]["status"] == "ACTIVE"

    # A second plan activating supersedes the first.
    plan_id_2 = _make_plan(client, onc_headers, patient_id)
    client.post(f"/api/cca/surveillance-plans/{plan_id_2}/activate", headers=onc_headers)
    first = client.get(f"/api/cca/surveillance-plans/{plan_id}", headers=onc_headers).json()["surveillance_plan"]
    assert first["status"] == "SUPERSEDED"

    # Cannot re-activate an already-active plan.
    already_active = client.post(f"/api/cca/surveillance-plans/{plan_id_2}/activate", headers=onc_headers)
    assert already_active.status_code == 409


def test_visit_investigation_and_late_effect(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _make_plan(client, onc_headers, patient_id)

    visit = client.post(f"/api/cca/surveillance-plans/{plan_id}/visits", headers=onc_headers, json={
        "interval_history": "No new symptoms.", "examination": "Unremarkable.",
        "disease_status": "No evidence of disease", "next_review_interval": "3 months",
    })
    assert visit.status_code == 200, visit.text
    visit_id = visit.json()["visit"]["id"]
    assert visit.json()["visit"]["status"] == "DRAFT"

    sign = client.post(f"/api/cca/surveillance-visits/{visit_id}/sign", headers=onc_headers)
    assert sign.status_code == 200
    assert sign.json()["visit"]["status"] == "SIGNED"

    inv = client.post(f"/api/cca/surveillance-plans/{plan_id}/investigations", headers=onc_headers, json={
        "investigation": "Mammogram", "rationale": "Annual surveillance imaging.", "frequency": "Annual",
    })
    assert inv.status_code == 200, inv.text
    inv_id = inv.json()["investigation"]["id"]

    update = client.post(f"/api/cca/surveillance-investigations/{inv_id}/update", headers=onc_headers, json={
        "status": "Resulted", "result_summary": "No suspicious findings.",
    })
    assert update.status_code == 200
    assert update.json()["investigation"]["status"] == "Resulted"

    late_effect = client.post(f"/api/cca/surveillance-plans/{plan_id}/late-effects", headers=onc_headers, json={
        "late_effect": "Chemotherapy-induced peripheral neuropathy", "severity_grade": "1", "attribution": "Probable",
    })
    assert late_effect.status_code == 200, late_effect.text
    le_id = late_effect.json()["late_effect"]["id"]

    review = client.post(f"/api/cca/late-effect-records/{le_id}/review", headers=onc_headers, json={"status": "Monitoring"})
    assert review.status_code == 200
    assert review.json()["late_effect"]["status"] == "Monitoring"
    assert review.json()["late_effect"]["last_reviewed"] is not None


def test_survivorship_document_draft_and_issue(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _make_plan(client, onc_headers, patient_id)

    draft = client.post(f"/api/cca/surveillance-plans/{plan_id}/survivorship-document", headers=onc_headers, json={
        "language": "English", "template_version": "v1",
    })
    assert draft.status_code == 200, draft.text
    doc_id = draft.json()["survivorship_document"]["id"]
    assert draft.json()["survivorship_document"]["status"] == "DRAFT"
    # Derived section present (read-time aggregation, reusing Batch 7's helper).
    assert "diagnosis_and_treatment_summary" in draft.json()["survivorship_document"]

    # Cannot issue without education_delivered recorded.
    blocked = client.post(f"/api/cca/survivorship-documents/{doc_id}/issue", headers=onc_headers)
    assert blocked.status_code == 409

    client.post(f"/api/cca/surveillance-plans/{plan_id}/survivorship-document", headers=onc_headers, json={
        "education_delivered": "Full", "comprehension_teach_back": "Confirmed",
    })
    issue = client.post(f"/api/cca/survivorship-documents/{doc_id}/issue", headers=onc_headers)
    assert issue.status_code == 200, issue.text

    # Cannot edit or re-issue after issuance.
    edit_after_issue = client.post(f"/api/cca/surveillance-plans/{plan_id}/survivorship-document", headers=onc_headers, json={"language": "Hindi"})
    assert edit_after_issue.status_code == 409


def test_recurrence_suspicion_reentry(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    event = client.post(f"/api/cca/patients/{patient_id}/recurrence-suspicion", headers=onc_headers, json={
        "trigger": "Imaging Finding", "trigger_detail": "New enhancing lesion on surveillance CT.",
        "urgency": "Urgent", "re_entry_destination": "Medical Oncology",
    })
    assert event.status_code == 200, event.text
    event_id = event.json()["recurrence_event"]["id"]
    assert event.json()["recurrence_event"]["status"] == "OPEN"

    action = client.post(f"/api/cca/recurrence-suspicion-events/{event_id}/action", headers=onc_headers)
    assert action.status_code == 200, action.text
    assert action.json()["recurrence_event"]["status"] == "ACTIONED"

    patient = client.get(f"/api/cca/patients/{patient_id}", headers=onc_headers)
    assert patient.json()["header"]["journey_state"] == "UnderInvestigation"

    double_action = client.post(f"/api/cca/recurrence-suspicion-events/{event_id}/action", headers=onc_headers)
    assert double_action.status_code == 409


def test_recall_queue_contact_log(client, onc_headers, nav_headers, db_session, oncologist):
    """Recall-queue management (SCR-SURV-008) is a Nurse Navigator responsibility per the
    gap report's own role list for this screen -- plan creation still requires a clinician
    (the oncologist here), but logging contact attempts does not."""
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _make_plan(client, onc_headers, patient_id)

    entry = client.post(f"/api/cca/surveillance-plans/{plan_id}/recall-entries", headers=nav_headers, json={
        "risk_priority": "Medium", "preferred_contact": "Phone", "owner": "nav@survivorship-test.com",
    })
    assert entry.status_code == 200, entry.text
    entry_id = entry.json()["recall_entry"]["id"]
    assert entry.json()["recall_entry"]["contact_attempts"] == []

    log1 = client.post(f"/api/cca/recall-entries/{entry_id}/log-attempt", headers=nav_headers, json={
        "method": "Phone", "outcome": "No answer", "next_attempt_date": "2026-10-01",
    })
    assert log1.status_code == 200, log1.text
    assert len(log1.json()["recall_entry"]["contact_attempts"]) == 1

    log2 = client.post(f"/api/cca/recall-entries/{entry_id}/log-attempt", headers=nav_headers, json={
        "method": "SMS", "outcome": "Reached -- rebooked",
    })
    assert log2.status_code == 200
    assert len(log2.json()["recall_entry"]["contact_attempts"]) == 2

    close = client.post(f"/api/cca/recall-entries/{entry_id}/close", headers=nav_headers, json={"outcome": "Rebooked"})
    assert close.status_code == 200
    assert close.json()["recall_entry"]["status"] == "Closed"


def test_survivorship_referral(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _make_plan(client, onc_headers, patient_id)

    referral = client.post(f"/api/cca/surveillance-plans/{plan_id}/referrals", headers=onc_headers, json={
        "domain": "Psychosocial", "need_reason": "Anxiety related to surveillance scans.",
        "service_provider": "Clinical Psychology", "priority": "Routine",
    })
    assert referral.status_code == 200, referral.text
    referral_id = referral.json()["referral"]["id"]
    assert referral.json()["referral"]["status"] == "Referred"

    update = client.post(f"/api/cca/survivorship-referrals/{referral_id}/update-status", headers=onc_headers, json={
        "status": "Attended", "outcome": "Two sessions completed, anxiety improved.",
    })
    assert update.status_code == 200
    assert update.json()["referral"]["status"] == "Attended"


def test_cross_org_isolation(client, onc_headers, db_session, oncologist, make_user, auth_headers):
    other_onc = make_user(email="onc2@survivorship-test.com", role="CCAMedicalOncologist")
    other_headers = auth_headers(other_onc)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _make_plan(client, onc_headers, patient_id)

    cross_org = client.get(f"/api/cca/surveillance-plans/{plan_id}", headers=other_headers)
    assert cross_org.status_code == 404
