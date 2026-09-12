"""
Final gap-closing round, item 14: Finance back-office lifecycle -- preauthorisation +
denial/appeal, charge capture, high-cost-drug approval, claim tracking, refund/credit
note. Previously CCAFinancialCase only ever covered pre-treatment estimate/counselling.

Never computes a monetary amount -- every amount is always the payer's/billing staff's own
typed figure.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def counsellor(make_user):
    return make_user(email="fc@finance-test.com", role="CCAFinancialCounsellor")


@pytest.fixture
def fc_headers(auth_headers, counsellor):
    return auth_headers(counsellor)


@pytest.fixture
def patient(db_session, counsellor):
    p = CCAPatient(mrn="FINANCE-TEST-0001", name="Finance Test Patient", age=57, sex="Male", organization_id=counsellor.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def case_id(client, fc_headers, patient):
    return client.post("/api/cca/financial/cases", headers=fc_headers, json={"patient_id": patient.id}).json()["case"]["id"]


def test_preauthorization_denial_and_appeal(client, fc_headers, case_id):
    missing_services = client.post(f"/api/cca/financial/cases/{case_id}/preauthorizations", headers=fc_headers, json={})
    assert missing_services.status_code == 422

    preauth = client.post(f"/api/cca/financial/cases/{case_id}/preauthorizations", headers=fc_headers, json={
        "services_requested": "6 cycles AC-T chemotherapy", "requested_amount": "250000",
    })
    assert preauth.status_code == 201, preauth.text
    preauth_id = preauth.json()["preauthorization"]["id"]

    missing_reason = client.post(f"/api/cca/preauthorizations/{preauth_id}/decide", headers=fc_headers, json={"status": "Denied"})
    assert missing_reason.status_code == 422

    denied = client.post(f"/api/cca/preauthorizations/{preauth_id}/decide", headers=fc_headers, json={
        "status": "Denied", "denial_reason": "Not covered under current policy tier.",
    })
    assert denied.status_code == 200, denied.text
    assert denied.json()["preauthorization"]["status"] == "Denied"

    appeal = client.post(f"/api/cca/preauthorizations/{preauth_id}/appeal", headers=fc_headers, json={
        "appeal_reason": "Policy documentation shows oncology coverage should apply.",
    })
    assert appeal.status_code == 200, appeal.text
    assert appeal.json()["preauthorization"]["appeal_status"] == "Pending"

    outcome = client.post(f"/api/cca/preauthorizations/{preauth_id}/appeal-outcome", headers=fc_headers, json={
        "appeal_status": "Overturned", "appeal_outcome_notes": "Appeal successful, preauth reversed.",
    })
    assert outcome.status_code == 200, outcome.text
    assert outcome.json()["preauthorization"]["appeal_status"] == "Overturned"

    listed = client.get(f"/api/cca/financial/cases/{case_id}/preauthorizations", headers=fc_headers)
    assert len(listed.json()["preauthorizations"]) == 1


def test_billable_events_and_high_cost_drug_approval(client, fc_headers, case_id):
    event = client.post(f"/api/cca/financial/cases/{case_id}/billable-events", headers=fc_headers, json={
        "service_description": "Chemotherapy administration, cycle 1", "amount": "45000",
    })
    assert event.status_code == 201, event.text
    listed = client.get(f"/api/cca/financial/cases/{case_id}/billable-events", headers=fc_headers)
    assert len(listed.json()["billable_events"]) == 1

    hcd = client.post(f"/api/cca/financial/cases/{case_id}/high-cost-drug-approvals", headers=fc_headers, json={
        "drug_name": "Trastuzumab", "estimated_cost": "180000",
    })
    assert hcd.status_code == 201, hcd.text
    hcd_id = hcd.json()["approval"]["id"]

    decided = client.post(f"/api/cca/high-cost-drug-approvals/{hcd_id}/decide", headers=fc_headers, json={
        "approval_status": "Approved", "approving_body": "Insurance Medical Board", "approval_reference": "APV-2026-001",
    })
    assert decided.status_code == 200, decided.text
    assert decided.json()["approval"]["approval_status"] == "Approved"


def test_claim_and_refund_lifecycle(client, fc_headers, case_id):
    claim = client.post(f"/api/cca/financial/cases/{case_id}/claims", headers=fc_headers, json={
        "claim_number": "CLM-001", "payer_name": "Star Health Insurance", "submitted_amount": "300000",
    })
    assert claim.status_code == 201, claim.text
    claim_id = claim.json()["claim"]["id"]

    bad_status = client.post(f"/api/cca/claims/{claim_id}/update-status", headers=fc_headers, json={"status": "Nope"})
    assert bad_status.status_code == 422

    paid = client.post(f"/api/cca/claims/{claim_id}/update-status", headers=fc_headers, json={
        "status": "Paid", "paid_amount": "280000", "payer_reference": "PAY-REF-001",
    })
    assert paid.status_code == 200, paid.text
    assert paid.json()["claim"]["status"] == "Paid"

    refund = client.post(f"/api/cca/financial/cases/{case_id}/refunds", headers=fc_headers, json={
        "amount": "5000", "reason": "Overpayment on deposit.",
    })
    assert refund.status_code == 201, refund.text
    refund_id = refund.json()["refund"]["id"]

    issued = client.post(f"/api/cca/refunds/{refund_id}/issue", headers=fc_headers, json={})
    assert issued.status_code == 200, issued.text
    assert issued.json()["refund"]["status"] == "Issued"

    already_issued = client.post(f"/api/cca/refunds/{refund_id}/issue", headers=fc_headers, json={})
    assert already_issued.status_code == 409
