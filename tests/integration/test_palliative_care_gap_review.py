"""
Palliative Care missing-development round (Palliative_Care_Missing_Development_Only.pdf):
comprehensive assessment, pain/symptom management, goals of care, advance care planning, and
multidisciplinary referrals, plus a palliative-owned closed-loop follow-up task surface.

Every severity/score field is the clinician's own typed assessment -- no clinical scoring,
threshold, or safety-decision logic is computed anywhere here (standing repo rule).
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def palliative(make_user):
    return make_user(email="palliative@pc-gap-test.com", role="CCAPalliativeCareSpecialist")


@pytest.fixture
def med_onc(make_user, palliative):
    return make_user(email="medonc@pc-gap-test.com", role="CCAMedicalOncologist", organization_id=palliative.organization_id)


@pytest.fixture
def palliative_headers(auth_headers, palliative):
    return auth_headers(palliative)


@pytest.fixture
def med_onc_headers(auth_headers, med_onc):
    return auth_headers(med_onc)


@pytest.fixture
def patient(db_session, palliative):
    p = CCAPatient(mrn="PC-GAP-0001", name="Palliative Care Test Patient", age=71, sex="Female", organization_id=palliative.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def test_palliative_assessment_is_get_or_create_singleton(client, palliative_headers, med_onc_headers, patient):
    empty = client.get(f"/api/cca/patients/{patient.id}/palliative-assessment", headers=palliative_headers)
    assert empty.status_code == 200
    assert empty.json()["palliative_assessment"] is None

    forbidden = client.post(f"/api/cca/patients/{patient.id}/palliative-assessment", headers=med_onc_headers, json={
        "referral_reason": "Symptom burden",
    })
    assert forbidden.status_code == 403

    created = client.post(f"/api/cca/patients/{patient.id}/palliative-assessment", headers=palliative_headers, json={
        "referral_reason": "Uncontrolled pain and dyspnoea",
        "performance_status_scale": "ECOG", "performance_status_value": "2",
        "caregiver_name": "Daughter (Priya)", "caregiver_relationship": "Daughter",
    })
    assert created.status_code == 200, created.text
    assert created.json()["palliative_assessment"]["performance_status_value"] == "2"

    updated = client.post(f"/api/cca/patients/{patient.id}/palliative-assessment", headers=palliative_headers, json={
        "performance_status_value": "3",
    })
    assert updated.status_code == 200
    body = updated.json()["palliative_assessment"]
    assert body["performance_status_value"] == "3"
    assert body["referral_reason"] == "Uncontrolled pain and dyspnoea"  # untouched fields survive a partial update

    listed = client.get(f"/api/cca/patients/{patient.id}/palliative-assessment", headers=palliative_headers)
    assert listed.json()["palliative_assessment"]["performance_status_value"] == "3"


def test_pain_assessment_history_is_append_only(client, palliative_headers, patient):
    first = client.post(f"/api/cca/patients/{patient.id}/palliative-pain-assessments", headers=palliative_headers, json={
        "site": "Right hip", "severity": "7", "pain_type": "Nociceptive",
        "current_analgesia": "Morphine SR 15mg BD", "plan": "Increase to 30mg BD",
    })
    assert first.status_code == 201, first.text

    second = client.post(f"/api/cca/patients/{patient.id}/palliative-pain-assessments", headers=palliative_headers, json={
        "site": "Right hip", "severity": "4", "is_reassessment": True,
        "plan": "Continue current regimen",
    })
    assert second.status_code == 201, second.text

    listed = client.get(f"/api/cca/patients/{patient.id}/palliative-pain-assessments", headers=palliative_headers)
    rows = listed.json()["pain_assessments"]
    assert len(rows) == 2
    assert rows[0]["severity"] == "4"  # most recent first
    assert rows[1]["severity"] == "7"


def test_symptom_assessment_requires_known_type(client, palliative_headers, patient):
    bad = client.post(f"/api/cca/patients/{patient.id}/palliative-symptom-assessments", headers=palliative_headers, json={
        "symptom_type": "Made Up Symptom", "severity": "3",
    })
    assert bad.status_code == 422

    good = client.post(f"/api/cca/patients/{patient.id}/palliative-symptom-assessments", headers=palliative_headers, json={
        "symptom_type": "Dyspnoea", "severity": "moderate", "description": "Worse on exertion",
        "intervention": "Low-dose morphine, positioning advice",
    })
    assert good.status_code == 201, good.text

    listed = client.get(f"/api/cca/patients/{patient.id}/palliative-symptom-assessments", headers=palliative_headers)
    assert listed.json()["symptom_assessments"][0]["symptom_type"] == "Dyspnoea"


def test_goals_of_care_and_advance_care_plan_are_singletons(client, palliative_headers, patient):
    goals = client.post(f"/api/cca/patients/{patient.id}/palliative-goals-of-care", headers=palliative_headers, json={
        "patient_goals": "Stay at home as long as possible", "treatment_goals": "ComfortFocused",
        "decision_maker_name": "Priya (daughter)",
    })
    assert goals.status_code == 200, goals.text
    assert goals.json()["goals_of_care"]["treatment_goals"] == "ComfortFocused"

    bad_status = client.post(f"/api/cca/patients/{patient.id}/palliative-advance-care-plan", headers=palliative_headers, json={
        "acp_status": "Bogus",
    })
    assert bad_status.status_code == 422

    acp = client.post(f"/api/cca/patients/{patient.id}/palliative-advance-care-plan", headers=palliative_headers, json={
        "acp_status": "Documented", "code_status": "DNR", "preferred_place_of_death": "Home",
        "discussion_date": "2026-09-15",
    })
    assert acp.status_code == 200, acp.text
    assert acp.json()["advance_care_plan"]["acp_status"] == "Documented"
    assert acp.json()["advance_care_plan"]["discussion_date"] == "2026-09-15"

    fetched_goals = client.get(f"/api/cca/patients/{patient.id}/palliative-goals-of-care", headers=palliative_headers)
    assert fetched_goals.json()["goals_of_care"]["patient_goals"] == "Stay at home as long as possible"


def test_referral_lifecycle(client, palliative_headers, patient):
    bad_service = client.post(f"/api/cca/patients/{patient.id}/palliative-referrals", headers=palliative_headers, json={
        "service": "Astrology",
    })
    assert bad_service.status_code == 422

    referral = client.post(f"/api/cca/patients/{patient.id}/palliative-referrals", headers=palliative_headers, json={
        "service": "Psychology", "reason": "Anticipatory grief, patient-reported distress",
    })
    assert referral.status_code == 201, referral.text
    referral_id = referral.json()["referral"]["id"]
    assert referral.json()["referral"]["status"] == "Requested"

    accepted = client.patch(f"/api/cca/palliative-referrals/{referral_id}", headers=palliative_headers, json={
        "status": "Accepted",
    })
    assert accepted.status_code == 200, accepted.text

    bad_transition = client.patch(f"/api/cca/palliative-referrals/{referral_id}", headers=palliative_headers, json={
        "status": "NotARealStatus",
    })
    assert bad_transition.status_code == 422

    listed = client.get(f"/api/cca/patients/{patient.id}/palliative-referrals", headers=palliative_headers)
    assert listed.json()["referrals"][0]["status"] == "Accepted"


def test_palliative_followup_task_resolved_by_palliative_not_generic_clinician(client, palliative_headers, med_onc_headers, patient):
    created = client.post(f"/api/cca/patients/{patient.id}/palliative-followup-tasks", headers=palliative_headers, json={
        "description": "Reassess pain control after opioid titration", "due_date": "2026-09-22T09:00:00",
    })
    assert created.status_code == 201, created.text
    task_id = created.json()["task"]["id"]
    assert created.json()["task"]["owner_role"] == "PALLIATIVE_CARE"

    resolved = client.post(f"/api/cca/tasks/{task_id}/resolve", headers=palliative_headers)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["task"]["status"] == "RESOLVED"
