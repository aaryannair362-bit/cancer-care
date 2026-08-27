"""
Integration tests proving the RBAC projection layer (backend/app/rbac_projection.py) is
actually wired into the live endpoints, not just correct in isolation -- i.e. that a real
HTTP call from a non-clinical role gets back a reduced payload, and a real DRAFT plan/order
is invisible to roles with no legitimate reason to see one at all.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="medonc@rbachosp.com", role="CCAMedicalOncologist")


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@rbachosp.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture
def financial_counsellor(make_user, oncologist):
    return make_user(email="finance@rbachosp.com", role="CCAFinancialCounsellor", organization_id=oncologist.organization_id)


@pytest.fixture
def infusion_nurse(make_user, oncologist):
    return make_user(email="infusion@rbachosp.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def mdt_coordinator(make_user, oncologist):
    return make_user(email="mdtcoord@rbachosp.com", role="CCAMDTCoordinator", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(
        CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id
    ).first().id


def test_front_desk_gets_minimal_treatment_plan_and_care_plan_fields(client, auth_headers, db_session, oncologist, front_desk):
    onc_headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={
        "patient_id": patient_id, "protocol_name": "AC-T", "intent": "Curative"
    }).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})
    client.post("/api/cca/care-plans", headers=onc_headers, json={"patient_id": patient_id, "treatment_plan_ids": [plan_id], "intent": "Curative"})

    fd_headers = auth_headers(front_desk)

    tx_view = client.get(f"/api/cca/treatment-plans/{plan_id}", headers=fd_headers).json()["treatment_plan"]
    assert tx_view == {"id": plan_id, "status": "ACTIVE"}
    assert "protocol_name" not in tx_view and "intent" not in tx_view and "modality" not in tx_view

    care_view = client.get(f"/api/cca/care-plans/current?patient_id={patient_id}", headers=fd_headers).json()["care_plan"]
    assert set(care_view.keys()) == {"id", "status", "version_no"}
    assert "components" not in care_view and "intent" not in care_view


def test_financial_counsellor_gets_finance_field_set(client, auth_headers, db_session, oncologist, financial_counsellor):
    onc_headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={
        "patient_id": patient_id, "protocol_name": "AC-T", "planned_sessions": 8
    }).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})

    finance_view = client.get(f"/api/cca/treatment-plans/{plan_id}", headers=auth_headers(financial_counsellor)).json()["treatment_plan"]
    assert set(finance_view.keys()) == {"id", "modality", "protocol_name", "planned_sessions", "start_date", "status"}
    assert finance_view["protocol_name"] == "AC-T"
    assert finance_view["planned_sessions"] == 8


def test_draft_treatment_plan_is_invisible_to_non_clinical_roles(client, auth_headers, db_session, oncologist, front_desk, infusion_nurse):
    onc_headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    draft_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]

    assert client.get(f"/api/cca/treatment-plans/{draft_id}", headers=auth_headers(front_desk)).status_code == 404
    assert client.get(f"/api/cca/treatment-plans/{draft_id}", headers=auth_headers(infusion_nurse)).status_code == 404

    listing = client.get(f"/api/cca/patients/{patient_id}/treatment-plans", headers=auth_headers(front_desk)).json()["treatment_plans"]
    assert draft_id not in [p["id"] for p in listing]

    # The authoring oncologist can still see their own draft.
    assert client.get(f"/api/cca/treatment-plans/{draft_id}", headers=onc_headers).status_code == 200


def test_infusion_nurse_gets_full_fields_on_a_signed_plan(client, auth_headers, db_session, oncologist, infusion_nurse):
    onc_headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={"patient_id": patient_id, "protocol_name": "AC-T"}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})

    nurse_view = client.get(f"/api/cca/treatment-plans/{plan_id}", headers=auth_headers(infusion_nurse)).json()["treatment_plan"]
    assert nurse_view["protocol_name"] == "AC-T"
    assert nurse_view["status"] == "ACTIVE"


def test_care_plan_prefill_is_restricted_to_clinicians(client, auth_headers, db_session, oncologist, front_desk, financial_counsellor):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    assert client.get(f"/api/cca/care-plans/prefill?patient_id={patient_id}", headers=auth_headers(front_desk)).status_code == 403
    assert client.get(f"/api/cca/care-plans/prefill?patient_id={patient_id}", headers=auth_headers(financial_counsellor)).status_code == 403
    assert client.get(f"/api/cca/care-plans/prefill?patient_id={patient_id}", headers=auth_headers(oncologist)).status_code == 200


def test_treatment_order_instructions_hidden_from_non_clinical_roles_but_not_from_infusion_nurse(client, auth_headers, db_session, oncologist, front_desk, infusion_nurse, mdt_coordinator):
    onc_headers = auth_headers(oncologist)
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})
    order_id = client.post("/api/cca/treatment-orders", headers=onc_headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id, "instructions": {"drug": "Doxorubicin", "dose": "60mg/m2"}
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=onc_headers)

    for user in (front_desk, mdt_coordinator):
        view = client.get(f"/api/cca/treatment-orders/{order_id}", headers=auth_headers(user)).json()["treatment_order"]
        assert view == {"id": order_id, "status": "SIGNED"}

    nurse_view = client.get(f"/api/cca/treatment-orders/{order_id}", headers=auth_headers(infusion_nurse)).json()["treatment_order"]
    assert nurse_view["instructions"] == {"drug": "Doxorubicin", "dose": "60mg/m2"}
