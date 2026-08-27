"""
Tests for the diagnostics closed loop (architecture doc P0 priority: "Orders and final
results resolve Care Plan milestones and create review-required events"), implemented in
backend/app/event_subscribers.py's _on_diagnostic_result_finalized (registered against
IMAGING_REPORT_FINALIZED / PATHOLOGY_REPORT_FINALIZED / LAB_RESULT_FINALIZED) plus
raise_order's milestone-task creation and acknowledge_result's review-task resolution in
backend/app/routers/cca.py.

Before this, a finalized report/result published its event but nothing consumed it: no Care
Plan milestone was ever resolved and no review-required task was ever created for a critical
result -- the loop never closed.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CarePlanTask


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@diagclosedloop.com", role="CCAMedicalOncologist")


@pytest.fixture
def radiologist(make_user, oncologist):
    return make_user(email="rad@diagclosedloop.com", role="CCARadiologist", organization_id=oncologist.organization_id)


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@diagclosedloop.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def _make_active_care_plan(client, headers, patient_id):
    plan_id = client.post("/api/cca/treatment-plans", headers=headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=headers, json={})
    return client.post("/api/cca/care-plans", headers=headers, json={
        "patient_id": patient_id, "treatment_plan_ids": [plan_id], "intent": "Curative"
    }).json()["care_plan"]["id"]


def _raise_imaging_order(client, headers, patient_id):
    res = client.post("/api/cca/orders", headers=headers, json={
        "patient_id": patient_id, "order_type": "RADIOLOGY", "item_name": "Response MRI",
        "clinical_indication": "Response assessment after Cycle 4",
    })
    assert res.status_code == 200
    return res.json()["order"]["id"]


def test_order_raised_without_active_care_plan_creates_no_milestone_task(client, auth_headers, db_session, oncologist):
    headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order_id = _raise_imaging_order(client, headers, patient_id)

    linked = db_session.query(CarePlanTask).filter(CarePlanTask.linked_order_id == order_id).first()
    assert linked is None


def test_order_raised_with_active_care_plan_creates_milestone_resolved_on_finalize(client, auth_headers, db_session, oncologist, radiologist):
    headers = auth_headers(oncologist)
    rad_headers = auth_headers(radiologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    _make_active_care_plan(client, headers, patient_id)

    order_id = _raise_imaging_order(client, headers, patient_id)
    milestone = db_session.query(CarePlanTask).filter(CarePlanTask.linked_order_id == order_id).first()
    assert milestone is not None
    assert milestone.status == "OPEN"

    draft = client.post(f"/api/cca/imaging/orders/{order_id}/report", headers=rad_headers, json={
        "findings_text": "No new lesions.", "impression": "Stable disease.", "is_critical": False,
    })
    assert draft.status_code == 200
    result_id = draft.json()["result"]["id"]

    finalize = client.post(f"/api/cca/imaging/results/{result_id}/finalize", headers=rad_headers)
    assert finalize.status_code == 200

    db_session.refresh(milestone)
    assert milestone.status == "RESOLVED"


def test_critical_result_creates_review_task_resolved_on_acknowledge(client, auth_headers, db_session, oncologist, radiologist):
    headers = auth_headers(oncologist)
    rad_headers = auth_headers(radiologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    order_id = _raise_imaging_order(client, headers, patient_id)
    draft = client.post(f"/api/cca/imaging/orders/{order_id}/report", headers=rad_headers, json={
        "findings_text": "New hepatic lesion suspicious for metastasis.",
        "impression": "Concerning for disease progression.", "is_critical": True,
    })
    result_id = draft.json()["result"]["id"]
    finalize = client.post(f"/api/cca/imaging/results/{result_id}/finalize", headers=rad_headers)
    assert finalize.status_code == 200

    review_task = db_session.query(CarePlanTask).filter(
        CarePlanTask.linked_result_id == result_id, CarePlanTask.category == "CLINICAL_REVIEW"
    ).first()
    assert review_task is not None
    assert review_task.status == "OPEN"

    ack = client.post(f"/api/cca/results/{result_id}/acknowledge", headers=headers)
    assert ack.status_code == 200

    db_session.refresh(review_task)
    assert review_task.status == "RESOLVED"


def test_non_critical_result_creates_no_review_task(client, auth_headers, db_session, oncologist, radiologist):
    headers = auth_headers(oncologist)
    rad_headers = auth_headers(radiologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    order_id = _raise_imaging_order(client, headers, patient_id)
    draft = client.post(f"/api/cca/imaging/orders/{order_id}/report", headers=rad_headers, json={
        "findings_text": "Unremarkable.", "impression": "No evidence of disease.", "is_critical": False,
    })
    result_id = draft.json()["result"]["id"]
    client.post(f"/api/cca/imaging/results/{result_id}/finalize", headers=rad_headers)

    review_task = db_session.query(CarePlanTask).filter(
        CarePlanTask.linked_result_id == result_id, CarePlanTask.category == "CLINICAL_REVIEW"
    ).first()
    assert review_task is None


def test_acknowledge_result_requires_clinician(client, auth_headers, db_session, oncologist, radiologist, front_desk):
    headers = auth_headers(oncologist)
    rad_headers = auth_headers(radiologist)
    fd_headers = auth_headers(front_desk)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    order_id = _raise_imaging_order(client, headers, patient_id)
    draft = client.post(f"/api/cca/imaging/orders/{order_id}/report", headers=rad_headers, json={
        "findings_text": "x", "impression": "y", "is_critical": False,
    })
    result_id = draft.json()["result"]["id"]
    client.post(f"/api/cca/imaging/results/{result_id}/finalize", headers=rad_headers)

    denied = client.post(f"/api/cca/results/{result_id}/acknowledge", headers=fd_headers)
    assert denied.status_code == 403


def test_raise_order_requires_clinician(client, auth_headers, db_session, oncologist, front_desk):
    fd_headers = auth_headers(front_desk)
    patient_id = _patient_id(db_session, oncologist.organization_id)

    res = client.post("/api/cca/orders", headers=fd_headers, json={
        "patient_id": patient_id, "order_type": "RADIOLOGY", "item_name": "CT",
        "clinical_indication": "staging",
    })
    assert res.status_code == 403


def test_imaging_worklist_not_readable_by_unrelated_roles(client, auth_headers, db_session, oncologist, front_desk):
    fd_headers = auth_headers(front_desk)
    denied = client.get("/api/cca/imaging/worklist", headers=fd_headers)
    assert denied.status_code == 403


def test_imaging_worklist_readable_by_radiologist_and_oncologist(client, auth_headers, db_session, oncologist, radiologist):
    headers = auth_headers(oncologist)
    rad_headers = auth_headers(radiologist)
    assert client.get("/api/cca/imaging/worklist", headers=headers).status_code == 200
    assert client.get("/api/cca/imaging/worklist", headers=rad_headers).status_code == 200
