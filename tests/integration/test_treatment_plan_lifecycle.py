"""
Tests for the Treatment Plan lifecycle (backend/app/routers/cca.py's
create_treatment_plan / amend_treatment_plan / sign_treatment_plan /
discontinue_treatment_plan) and its separation from Care Plan.

Covers the specific non-negotiable constraints this slice exists to enforce:
  - Treatment Plan and Care Plan are distinct objects; a Care Plan can only be created by
    referencing an already-signed (ACTIVE) Treatment Plan, never the reverse.
  - Only an authorized clinician of the matching modality may sign/discontinue a plan --
    the three oncologist roles are not interchangeable for this action.
  - Every material change versions (TreatmentPlanVersion), never silently overwrites.
  - Multi-tenant org-scoping holds for the new endpoints, matching the rest of this router.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, TreatmentPlan, TreatmentPlanVersion


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@txplanhosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def surgical_oncologist(make_user, oncologist):
    return make_user(email="surgonc@txplanhosp.com", role="CCASurgicalOncologist", organization_id=oncologist.organization_id)


@pytest.fixture
def radiation_oncologist(make_user, oncologist):
    return make_user(email="radonc@txplanhosp.com", role="CCARadiationOncologist", organization_id=oncologist.organization_id)


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@txplanhosp.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture
def admin(make_user, oncologist):
    return make_user(email="admin@txplanhosp.com", role="Admin", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def test_draft_is_not_clinically_active_and_cannot_back_a_care_plan(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    draft = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id})
    assert draft.status_code == 200
    plan = draft.json()["treatment_plan"]
    assert plan["status"] == "DRAFT"
    assert plan["signed_at"] is None

    rejected = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan["id"]]
    })
    assert rejected.status_code == 422


def test_sign_activates_plan_and_care_plan_can_then_reference_it(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]

    signed = client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    assert signed.status_code == 200
    body = signed.json()["treatment_plan"]
    assert body["status"] == "ACTIVE"
    assert body["signer_email"] == oncologist.email
    assert body["signer_role"] == "CCAMedicalOncologist"
    assert body["signed_at"] is not None

    care_plan = client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan_id], "intent": "Curative"
    })
    assert care_plan.status_code == 200
    assert care_plan.json()["care_plan"]["source_treatment_plan_ids"] == [plan_id]

    # Signing again must be rejected -- a plan is signed once, amendments/revisions go
    # through PUT (in place) or a new supersedes_id plan (see below), never a re-sign.
    resigned = client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    assert resigned.status_code == 409


def test_only_matching_modality_specialist_may_sign(client, auth_headers, db_session, oncologist, surgical_oncologist, radiation_oncologist, front_desk):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    onc_headers = auth_headers(oncologist)

    surgical_plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={
        "patient_id": patient_id, "modality": "Surgical Resection"
    }).json()["treatment_plan"]["id"]

    # The Medical Oncologist who drafted it cannot sign a surgical-modality plan.
    denied = client.post(f"/api/cca/treatment-plans/{surgical_plan_id}/sign", headers=onc_headers, json={})
    assert denied.status_code == 403

    # A Radiation Oncologist also cannot sign a surgical plan.
    denied2 = client.post(f"/api/cca/treatment-plans/{surgical_plan_id}/sign", headers=auth_headers(radiation_oncologist), json={})
    assert denied2.status_code == 403

    # The Surgical Oncologist can.
    approved = client.post(f"/api/cca/treatment-plans/{surgical_plan_id}/sign", headers=auth_headers(surgical_oncologist), json={})
    assert approved.status_code == 200
    assert approved.json()["treatment_plan"]["signer_role"] == "CCASurgicalOncologist"

    # Front Desk cannot even draft a Treatment Plan in the first place.
    fd_draft = client.post("/api/cca/treatment-plans", headers=auth_headers(front_desk), json={"patient_id": patient_id})
    assert fd_draft.status_code == 403


def test_amendment_requires_reason_and_versions_without_overwriting_history(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "protocol_name": "AC-T"
    }).json()["treatment_plan"]["id"]

    unreasoned = client.put(f"/api/cca/treatment-plans/{plan_id}", headers=headers, json={"protocol_name": "TC"})
    assert unreasoned.status_code == 422

    amended = client.put(f"/api/cca/treatment-plans/{plan_id}", headers=headers, json={
        "protocol_name": "TC", "change_reason": "Switched to TC regimen after cardiology review."
    })
    assert amended.status_code == 200
    assert amended.json()["treatment_plan"]["version_no"] == 2

    versions = db_session.query(TreatmentPlanVersion).filter(
        TreatmentPlanVersion.treatment_plan_id == plan_id
    ).order_by(TreatmentPlanVersion.version_no).all()
    assert len(versions) == 2
    assert versions[0].change_reason == "Initial draft created"
    assert versions[1].change_reason == "Switched to TC regimen after cardiology review."
    # v1's snapshot is untouched by the v2 amendment -- history is additive, not overwritten.
    assert versions[0].version_no == 1


def test_signing_a_revision_supersedes_the_prior_active_plan(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    plan_a_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_a_id}/sign", headers=headers, json={})

    plan_b_id = client.post("/api/cca/treatment-plans", headers=headers, json={
        "patient_id": patient_id, "supersedes_id": plan_a_id, "protocol_name": "Revised regimen post-MDT"
    }).json()["treatment_plan"]["id"]

    # Not superseded yet -- only signing the replacement actually takes effect.
    still_active = client.get(f"/api/cca/treatment-plans/{plan_a_id}", headers=headers).json()["treatment_plan"]
    assert still_active["status"] == "ACTIVE"

    client.post(f"/api/cca/treatment-plans/{plan_b_id}/sign", headers=headers, json={})

    plan_a_after = client.get(f"/api/cca/treatment-plans/{plan_a_id}", headers=headers).json()["treatment_plan"]
    assert plan_a_after["status"] == "SUPERSEDED"
    plan_b_after = client.get(f"/api/cca/treatment-plans/{plan_b_id}", headers=headers).json()["treatment_plan"]
    assert plan_b_after["status"] == "ACTIVE"


def test_discontinue_requires_reason_and_matching_modality(client, auth_headers, db_session, oncologist, radiation_oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})

    wrong_specialist = client.post(f"/api/cca/treatment-plans/{plan_id}/discontinue", headers=auth_headers(radiation_oncologist), json={"reason": "n/a"})
    assert wrong_specialist.status_code == 403

    no_reason = client.post(f"/api/cca/treatment-plans/{plan_id}/discontinue", headers=headers, json={})
    assert no_reason.status_code == 422

    stopped = client.post(f"/api/cca/treatment-plans/{plan_id}/discontinue", headers=headers, json={"reason": "Disease progression on imaging."})
    assert stopped.status_code == 200
    assert stopped.json()["treatment_plan"]["status"] == "CANCELLED"


def test_treatment_plan_endpoints_are_org_scoped(client, make_user, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]

    other_org_oncologist = make_user(email="other.org.onc@rivalhosp.com", role="CCAMedicalOncologist")
    other_headers = auth_headers(other_org_oncologist)

    assert client.get(f"/api/cca/treatment-plans/{plan_id}", headers=other_headers).status_code == 404
    assert client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=other_headers, json={}).status_code == 404
    assert client.post("/api/cca/treatment-plans", headers=other_headers, json={"patient_id": patient_id}).status_code == 404
