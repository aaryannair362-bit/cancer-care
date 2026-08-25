"""
Billing module tests: tariffs/packages, invoice line capture (manual/tariff/package/pharmacy/
bed-day), discount approval, finalize, payment collection, refunds, insurance/corporate claims,
role permissions, multi-tenant isolation. Mirrors test_pharmacy_module.py's structure.
"""
from datetime import date, timedelta

import pytest


@pytest.fixture
def billing(make_user):
    return make_user(email="billing@billhosp.com", role="Billing")


@pytest.fixture
def admin(make_user, billing):
    return make_user(email="admin@billhosp.com", role="Admin", organization_id=billing.organization_id)


@pytest.fixture
def pharmacist(make_user, billing):
    return make_user(email="pharmacist@billhosp.com", role="Pharmacist", organization_id=billing.organization_id)


@pytest.fixture
def other_roles(make_user, billing):
    org_id = billing.organization_id
    return {
        "Doctor": make_user(email="doctor@billhosp.com", role="Doctor", organization_id=org_id),
        "Nurse": make_user(email="nurse@billhosp.com", role="Nurse", organization_id=org_id),
    }


@pytest.fixture
def patient(billing, db_session):
    from app.models import Patient

    p = Patient(name="Bill Patient", age=45, gender="F", organization_id=billing.organization_id, created_by=billing.id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_tariff(client, headers, **overrides):
    payload = {"code": "CONSULT-GEN", "name": "General Consultation", "category": "Consultation", "unit_price": 500.0}
    payload.update(overrides)
    resp = client.post("/api/billing/tariffs", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_invoice(client, headers, patient_id, **overrides):
    payload = {"patient_id": patient_id}
    payload.update(overrides)
    resp = client.post("/api/billing/invoices", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _finalized_invoice_with_line(client, headers, patient_id, amount=1000.0):
    invoice = _create_invoice(client, headers, patient_id)
    client.post(f"/api/billing/invoices/{invoice['id']}/lines/manual",
                json={"description": "Test charge", "unit_price": amount}, headers=headers)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/finalize", headers=headers)
    return resp.json()


# ---------------------------------------------------------------------------
# Role permissions
# ---------------------------------------------------------------------------

def test_billing_and_admin_can_create_invoices(client, billing, admin, patient, auth_headers):
    for user in (billing, admin):
        resp = client.post("/api/billing/invoices", json={"patient_id": patient.id}, headers=auth_headers(user))
        assert resp.status_code == 201


def test_other_roles_cannot_access_billing(client, other_roles, auth_headers):
    for role, user in other_roles.items():
        resp = client.get("/api/billing/invoices", headers=auth_headers(user))
        assert resp.status_code == 403, f"{role} should not have billing access"


def test_billing_staff_cannot_apply_discount_or_refund(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    invoice = _finalized_invoice_with_line(client, headers, patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/discount", json={"amount": 10, "reason": "x"}, headers=headers)
    assert resp.status_code == 403

    client.post(f"/api/billing/invoices/{invoice['id']}/payments", json={"amount": 100, "method": "Cash"}, headers=headers)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/refunds", json={"amount": 50, "reason": "x"}, headers=headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tariffs and packages
# ---------------------------------------------------------------------------

def test_duplicate_tariff_code_rejected(client, billing, auth_headers):
    headers = auth_headers(billing)
    _create_tariff(client, headers, code="DUP1")
    resp = client.post("/api/billing/tariffs", json={"code": "DUP1", "name": "X", "category": "Other", "unit_price": 1.0}, headers=headers)
    assert resp.status_code == 400


def test_tariff_category_filter(client, billing, auth_headers):
    headers = auth_headers(billing)
    _create_tariff(client, headers, code="C1", category="Consultation")
    _create_tariff(client, headers, code="B1", category="BedDay", name="General Ward", unit_price=1200.0)
    beds = client.get("/api/billing/tariffs", params={"category": "BedDay"}, headers=headers).json()
    assert {t["code"] for t in beds} == {"B1"}


def test_create_and_list_package(client, billing, auth_headers):
    headers = auth_headers(billing)
    resp = client.post("/api/billing/packages", json={"name": "Maternity Package", "price": 45000.0}, headers=headers)
    assert resp.status_code == 201
    names = {p["name"] for p in client.get("/api/billing/packages", headers=headers).json()}
    assert "Maternity Package" in names


# ---------------------------------------------------------------------------
# Invoice line capture (Service Capture)
# ---------------------------------------------------------------------------

def test_manual_and_tariff_lines_recompute_totals(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    tariff = _create_tariff(client, headers, unit_price=500.0)
    invoice = _create_invoice(client, headers, patient.id)

    resp = client.post(f"/api/billing/invoices/{invoice['id']}/lines/manual",
                        json={"description": "Bandage", "quantity": 2, "unit_price": 50.0}, headers=headers)
    assert resp.json()["subtotal"] == 100.0

    resp = client.post(f"/api/billing/invoices/{invoice['id']}/lines/tariff",
                        json={"tariff_id": tariff["id"], "quantity": 1}, headers=headers)
    assert resp.json()["subtotal"] == 600.0
    assert resp.json()["total_amount"] == 600.0


def test_package_line_uses_package_price_not_component_sum(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    pkg = client.post("/api/billing/packages", json={"name": "Day Care Package", "price": 8000.0}, headers=headers).json()
    invoice = _create_invoice(client, headers, patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/lines/package", json={"package_id": pkg["id"]}, headers=headers)
    assert resp.json()["total_amount"] == 8000.0
    assert len(resp.json()["lines"]) == 1


def test_cannot_add_lines_after_finalize(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    invoice = _finalized_invoice_with_line(client, headers, patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/lines/manual",
                        json={"description": "Late add", "unit_price": 10.0}, headers=headers)
    assert resp.status_code == 400


def test_cannot_finalize_empty_invoice(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    invoice = _create_invoice(client, headers, patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/finalize", headers=headers)
    assert resp.status_code == 400


def test_capture_pharmacy_charges_uses_snapshotted_dispense_price(client, billing, pharmacist, patient, auth_headers):
    ph_headers = auth_headers(pharmacist)
    bill_headers = auth_headers(billing)

    drug = client.post("/api/pharmacy/drugs", json={"name": "BillDrug", "unit_price": 20.0}, headers=ph_headers).json()
    client.post(f"/api/pharmacy/drugs/{drug['id']}/batches",
                json={"received_quantity": 10, "expiry_date": (date.today() + timedelta(days=90)).isoformat()},
                headers=ph_headers)
    dispense = client.post("/api/pharmacy/dispense",
                            json={"drug_id": drug["id"], "quantity": 3, "patient_id": patient.id}, headers=ph_headers).json()
    dispensing_record_id = dispense["lines"][0]["id"]

    invoice = _create_invoice(client, bill_headers, patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/capture-pharmacy",
                        json={"dispensing_record_ids": [dispensing_record_id]}, headers=bill_headers)
    assert resp.status_code == 201
    assert resp.json()["subtotal"] == 60.0  # 3 * 20.0, from the dispense record, not re-priced


def test_capture_pharmacy_rejects_double_billing_same_dispense(client, billing, pharmacist, patient, auth_headers):
    ph_headers = auth_headers(pharmacist)
    bill_headers = auth_headers(billing)
    drug = client.post("/api/pharmacy/drugs", json={"name": "DoubleBillDrug", "unit_price": 10.0}, headers=ph_headers).json()
    client.post(f"/api/pharmacy/drugs/{drug['id']}/batches",
                json={"received_quantity": 10, "expiry_date": (date.today() + timedelta(days=90)).isoformat()},
                headers=ph_headers)
    dispense = client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 1, "patient_id": patient.id}, headers=ph_headers).json()
    dr_id = dispense["lines"][0]["id"]

    invoice1 = _create_invoice(client, bill_headers, patient.id)
    client.post(f"/api/billing/invoices/{invoice1['id']}/capture-pharmacy", json={"dispensing_record_ids": [dr_id]}, headers=bill_headers)

    invoice2 = _create_invoice(client, bill_headers, patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice2['id']}/capture-pharmacy", json={"dispensing_record_ids": [dr_id]}, headers=bill_headers)
    assert resp.status_code == 400
    assert str(dr_id) in resp.json()["detail"]


def test_capture_bed_days(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    tariff = _create_tariff(client, headers, code="BED-GEN", name="General Ward", category="BedDay", unit_price=1200.0)
    invoice = _create_invoice(client, headers, patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/capture-bed-days",
                        json={"tariff_id": tariff["id"], "days": 4}, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["subtotal"] == 4800.0


# ---------------------------------------------------------------------------
# Discount (Admin-only)
# ---------------------------------------------------------------------------

def test_admin_can_apply_discount(client, admin, billing, patient, auth_headers):
    bill_headers = auth_headers(billing)
    invoice = _create_invoice(client, bill_headers, patient.id)
    client.post(f"/api/billing/invoices/{invoice['id']}/lines/manual", json={"description": "X", "unit_price": 1000.0}, headers=bill_headers)

    resp = client.post(f"/api/billing/invoices/{invoice['id']}/discount",
                        json={"amount": 100.0, "reason": "loyalty"}, headers=auth_headers(admin))
    assert resp.status_code == 200
    assert resp.json()["total_amount"] == 900.0


def test_discount_cannot_exceed_subtotal(client, admin, billing, patient, auth_headers):
    bill_headers = auth_headers(billing)
    invoice = _create_invoice(client, bill_headers, patient.id)
    client.post(f"/api/billing/invoices/{invoice['id']}/lines/manual", json={"description": "X", "unit_price": 100.0}, headers=bill_headers)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/discount",
                        json={"amount": 200.0, "reason": "too much"}, headers=auth_headers(admin))
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Payment collection
# ---------------------------------------------------------------------------

def test_cannot_pay_before_finalize(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    invoice = _create_invoice(client, headers, patient.id)
    client.post(f"/api/billing/invoices/{invoice['id']}/lines/manual", json={"description": "X", "unit_price": 100.0}, headers=headers)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/payments", json={"amount": 50, "method": "Cash"}, headers=headers)
    assert resp.status_code == 400


def test_partial_then_full_payment_updates_status(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    invoice = _finalized_invoice_with_line(client, headers, patient.id, amount=1000.0)

    r1 = client.post(f"/api/billing/invoices/{invoice['id']}/payments", json={"amount": 400, "method": "Cash"}, headers=headers)
    assert r1.json()["invoice"]["status"] == "PartiallyPaid"

    r2 = client.post(f"/api/billing/invoices/{invoice['id']}/payments", json={"amount": 600, "method": "Card"}, headers=headers)
    assert r2.json()["invoice"]["status"] == "Paid"
    assert r2.json()["invoice"]["balance_due"] == 0.0


def test_overpayment_rejected(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    invoice = _finalized_invoice_with_line(client, headers, patient.id, amount=100.0)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/payments", json={"amount": 150, "method": "Cash"}, headers=headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Refund (Admin-only)
# ---------------------------------------------------------------------------

def test_refund_reduces_refundable_balance_and_flips_status(client, admin, billing, patient, auth_headers):
    bill_headers = auth_headers(billing)
    admin_headers = auth_headers(admin)
    invoice = _finalized_invoice_with_line(client, bill_headers, patient.id, amount=500.0)
    client.post(f"/api/billing/invoices/{invoice['id']}/payments", json={"amount": 500, "method": "Cash"}, headers=bill_headers)

    resp = client.post(f"/api/billing/invoices/{invoice['id']}/refunds", json={"amount": 500, "reason": "cancelled procedure"}, headers=admin_headers)
    assert resp.status_code == 201
    assert resp.json()["invoice"]["status"] == "Refunded"


def test_refund_cannot_exceed_paid_amount(client, admin, billing, patient, auth_headers):
    bill_headers = auth_headers(billing)
    admin_headers = auth_headers(admin)
    invoice = _finalized_invoice_with_line(client, bill_headers, patient.id, amount=500.0)
    client.post(f"/api/billing/invoices/{invoice['id']}/payments", json={"amount": 200, "method": "Cash"}, headers=bill_headers)

    resp = client.post(f"/api/billing/invoices/{invoice['id']}/refunds", json={"amount": 300, "reason": "too much"}, headers=admin_headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Insurance / Corporate claims
# ---------------------------------------------------------------------------

def test_claim_requires_non_self_payer_type(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    invoice = _create_invoice(client, headers, patient.id, payer_type="Self")
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/claim",
                        json={"insurer_or_corporate_name": "ACME Insurance", "claim_amount": 1000.0}, headers=headers)
    assert resp.status_code == 400


def test_claim_lifecycle_to_settled(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    invoice = _create_invoice(client, headers, patient.id, payer_type="Insurance")
    claim = client.post(f"/api/billing/invoices/{invoice['id']}/claim",
                         json={"insurer_or_corporate_name": "ACME Insurance", "policy_or_account_number": "POL123",
                               "claim_amount": 5000.0}, headers=headers).json()
    assert claim["status"] == "Submitted"

    resp = client.patch(f"/api/billing/claims/{claim['id']}/status", json={"status": "Settled"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "Settled"
    assert resp.json()["settled_at"] is not None


def test_duplicate_claim_on_same_invoice_rejected(client, billing, patient, auth_headers):
    headers = auth_headers(billing)
    invoice = _create_invoice(client, headers, patient.id, payer_type="Corporate")
    client.post(f"/api/billing/invoices/{invoice['id']}/claim",
                json={"insurer_or_corporate_name": "BigCorp", "claim_amount": 2000.0}, headers=headers)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/claim",
                        json={"insurer_or_corporate_name": "BigCorp", "claim_amount": 2000.0}, headers=headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def two_org_billing(make_user):
    return {
        "a": make_user(email="bill.a@bill-a.com", role="Billing"),
        "b": make_user(email="bill.b@bill-b.com", role="Billing"),
    }


def test_invoice_list_scoped_per_org(client, two_org_billing, auth_headers, db_session):
    from app.models import Patient

    headers_a = auth_headers(two_org_billing["a"])
    headers_b = auth_headers(two_org_billing["b"])
    patient_a = Patient(name="A", age=30, gender="M", organization_id=two_org_billing["a"].organization_id, created_by=two_org_billing["a"].id)
    db_session.add(patient_a)
    db_session.commit()
    db_session.refresh(patient_a)

    invoice_a = _create_invoice(client, headers_a, patient_a.id)
    invoices_b = client.get("/api/billing/invoices", headers=headers_b).json()
    assert invoice_a["id"] not in {i["id"] for i in invoices_b}


def test_cannot_pay_another_orgs_invoice(client, two_org_billing, auth_headers, db_session):
    from app.models import Patient

    headers_a = auth_headers(two_org_billing["a"])
    headers_b = auth_headers(two_org_billing["b"])
    patient_a = Patient(name="A2", age=30, gender="M", organization_id=two_org_billing["a"].organization_id, created_by=two_org_billing["a"].id)
    db_session.add(patient_a)
    db_session.commit()
    db_session.refresh(patient_a)

    invoice = _finalized_invoice_with_line(client, headers_a, patient_a.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/payments", json={"amount": 10, "method": "Cash"}, headers=headers_b)
    assert resp.status_code == 404
