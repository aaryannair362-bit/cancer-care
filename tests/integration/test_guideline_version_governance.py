"""
Tests for guideline version governance (backend/app/routers/cca.py's
publish_guideline_version / acknowledge_guideline_review) -- the flagged gap fix for the
architecture doc's non-negotiable: a new guideline version must never silently rewrite a
signed Treatment Plan, only flag it for clinician review.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, TreatmentPlan, TreatmentPlanGuidelineLink, GuidelineRegistry

GUIDELINE_SOURCE = "NCCN Clinical Practice Guidelines in Oncology (NCCN Guidelines®) - Breast Cancer"
CURRENT_VERSION = "Version 4.2026"


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@guidelinehosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def admin(make_user, oncologist):
    return make_user(email="admin@guidelinehosp.com", role="Admin", organization_id=oncologist.organization_id)


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@guidelinehosp.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def test_signing_snapshots_the_current_guideline_version(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]

    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})

    link = db_session.query(TreatmentPlanGuidelineLink).filter(TreatmentPlanGuidelineLink.treatment_plan_id == plan_id).first()
    assert link is not None
    assert link.guideline_source == GUIDELINE_SOURCE
    assert link.version_at_signing == CURRENT_VERSION


def test_publish_guideline_version_is_admin_only(client, auth_headers, db_session, oncologist, front_desk):
    body = {"guideline_source": GUIDELINE_SOURCE, "new_version": "Version 5.2027"}
    assert client.post("/api/cca/admin/guidelines/publish-version", headers=auth_headers(oncologist), json=body).status_code == 403
    assert client.post("/api/cca/admin/guidelines/publish-version", headers=auth_headers(front_desk), json=body).status_code == 403


def test_new_guideline_version_flags_active_plan_without_changing_its_content(client, auth_headers, db_session, oncologist, admin):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "protocol_name": "AC-T", "modality": "Systemic Chemotherapy"
    }).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})

    publish_res = client.post("/api/cca/admin/guidelines/publish-version", headers=auth_headers(admin), json={
        "guideline_source": GUIDELINE_SOURCE, "new_version": "Version 5.2027", "change_note": "Updated systemic therapy sequencing."
    })
    assert publish_res.status_code == 200
    assert plan_id in publish_res.json()["flagged_treatment_plan_ids"]
    assert publish_res.json()["old_version"] is None  # registry didn't exist before this call

    registry = db_session.query(GuidelineRegistry).filter(GuidelineRegistry.guideline_source == GUIDELINE_SOURCE).first()
    assert registry.current_version == "Version 5.2027"

    plan = db_session.query(TreatmentPlan).filter(TreatmentPlan.id == plan_id).first()
    assert plan.guideline_review_required is True
    assert "Version 5.2027" in plan.guideline_review_reason
    # Never silently rewritten -- content is exactly what the clinician signed.
    assert plan.protocol_name == "AC-T"
    assert plan.modality == "Systemic Chemotherapy"
    assert plan.status == "ACTIVE"


def test_draft_and_cancelled_plans_are_not_flagged(client, auth_headers, db_session, oncologist, admin):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    # A DRAFT plan has no guideline snapshot at all (only sign_treatment_plan writes one),
    # so it can never be flagged -- confirms flagging only ever touches signed plans.
    draft_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]

    cancelled_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{cancelled_id}/sign", headers=headers, json={})
    client.post(f"/api/cca/treatment-plans/{cancelled_id}/discontinue", headers=headers, json={"reason": "No longer needed."})

    publish_res = client.post("/api/cca/admin/guidelines/publish-version", headers=auth_headers(admin), json={
        "guideline_source": GUIDELINE_SOURCE, "new_version": "Version 5.2027"
    })
    assert draft_id not in publish_res.json()["flagged_treatment_plan_ids"]
    assert cancelled_id not in publish_res.json()["flagged_treatment_plan_ids"]


def test_republishing_the_same_version_does_not_reflag(client, auth_headers, db_session, oncologist, admin):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})

    same_version = client.post("/api/cca/admin/guidelines/publish-version", headers=auth_headers(admin), json={
        "guideline_source": GUIDELINE_SOURCE, "new_version": CURRENT_VERSION
    })
    assert plan_id not in same_version.json()["flagged_treatment_plan_ids"]


def test_acknowledge_guideline_review_requires_matching_modality_and_clears_the_flag(client, auth_headers, db_session, oncologist, admin, make_user):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    client.post("/api/cca/admin/guidelines/publish-version", headers=auth_headers(admin), json={
        "guideline_source": GUIDELINE_SOURCE, "new_version": "Version 5.2027"
    })

    no_flag_yet = client.post(f"/api/cca/treatment-plans/999999/acknowledge-guideline-review", headers=headers, json={})
    assert no_flag_yet.status_code == 404

    surgical_oncologist = make_user(email="surgonc@guidelinehosp.com", role="CCASurgicalOncologist", organization_id=oncologist.organization_id)
    wrong_specialist = client.post(f"/api/cca/treatment-plans/{plan_id}/acknowledge-guideline-review", headers=auth_headers(surgical_oncologist), json={})
    assert wrong_specialist.status_code == 403

    ack = client.post(f"/api/cca/treatment-plans/{plan_id}/acknowledge-guideline-review", headers=headers, json={"note": "Regimen remains appropriate."})
    assert ack.status_code == 200
    assert ack.json()["treatment_plan"]["guideline_review_required"] is False

    already_clear = client.post(f"/api/cca/treatment-plans/{plan_id}/acknowledge-guideline-review", headers=headers, json={})
    assert already_clear.status_code == 409
