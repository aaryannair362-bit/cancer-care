"""
Inventory & Stores tests: Vendor Management, the Purchase Request -> Purchase Order -> Goods
Receipt procurement chain, and Stock Transfer. Mirrors test_pharmacy_module.py's structure.
"""
from datetime import date, timedelta

import pytest


@pytest.fixture
def pharmacist(make_user):
    return make_user(email="pharmacist@invhosp.com", role="Pharmacist")


@pytest.fixture
def admin(make_user, pharmacist):
    return make_user(email="admin@invhosp.com", role="Admin", organization_id=pharmacist.organization_id)


@pytest.fixture
def other_roles(make_user, pharmacist):
    org_id = pharmacist.organization_id
    return {
        "Doctor": make_user(email="doctor@invhosp.com", role="Doctor", organization_id=org_id),
        "Nurse": make_user(email="nurse@invhosp.com", role="Nurse", organization_id=org_id),
    }


def _create_vendor(client, headers, **overrides):
    payload = {"name": "MedSupply Co", "contact_person": "R. Singh", "phone": "9999999999"}
    payload.update(overrides)
    resp = client.post("/api/inventory/vendors", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_drug(client, headers, **overrides):
    payload = {"name": "Ibuprofen", "form": "Tablet", "strength": "400mg", "unit_price": 3.0}
    payload.update(overrides)
    resp = client.post("/api/pharmacy/drugs", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_po(client, headers, vendor_id, drug_id, quantity=50, unit_price=2.0, **overrides):
    payload = {
        "vendor_id": vendor_id,
        "lines": [{"drug_id": drug_id, "quantity_ordered": quantity, "unit_price": unit_price}],
    }
    payload.update(overrides)
    resp = client.post("/api/inventory/purchase-orders", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Role permissions
# ---------------------------------------------------------------------------

def test_pharmacist_and_admin_can_manage_vendors(client, pharmacist, admin, auth_headers):
    for i, user in enumerate((pharmacist, admin)):
        resp = client.post("/api/inventory/vendors", json={"name": f"Vendor {i}"}, headers=auth_headers(user))
        assert resp.status_code == 201


def test_other_roles_cannot_access_inventory(client, other_roles, auth_headers):
    for role, user in other_roles.items():
        resp = client.get("/api/inventory/vendors", headers=auth_headers(user))
        assert resp.status_code == 403, f"{role} should not have inventory access"


def test_only_admin_can_approve_purchase_request(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers)
    pr = client.post("/api/inventory/purchase-requests", json={"drug_id": drug["id"], "requested_quantity": 20}, headers=headers).json()
    resp = client.patch(f"/api/inventory/purchase-requests/{pr['id']}/approve", headers=headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Vendor management
# ---------------------------------------------------------------------------

def test_duplicate_vendor_name_rejected(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    _create_vendor(client, headers, name="DupVendor")
    resp = client.post("/api/inventory/vendors", json={"name": "DupVendor"}, headers=headers)
    assert resp.status_code == 400


def test_deactivate_vendor_blocks_new_purchase_orders(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    vendor = _create_vendor(client, headers, name="ToDeactivate")
    drug = _create_drug(client, headers)
    client.patch(f"/api/inventory/vendors/{vendor['id']}", json={"is_active": False}, headers=headers)

    resp = client.post(
        "/api/inventory/purchase-orders",
        json={"vendor_id": vendor["id"], "lines": [{"drug_id": drug["id"], "quantity_ordered": 5, "unit_price": 1.0}]},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "inactive vendor" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Purchase Request -> Purchase Order linkage
# ---------------------------------------------------------------------------

def test_purchase_order_requires_approved_request(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    vendor = _create_vendor(client, headers)
    drug = _create_drug(client, headers)
    pr = client.post("/api/inventory/purchase-requests", json={"drug_id": drug["id"], "requested_quantity": 20}, headers=headers).json()

    resp = client.post(
        "/api/inventory/purchase-orders",
        json={"vendor_id": vendor["id"], "purchase_request_id": pr["id"],
              "lines": [{"drug_id": drug["id"], "quantity_ordered": 20, "unit_price": 2.0}]},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "must be Approved" in resp.json()["detail"]


def test_approved_request_becomes_ordered_then_fulfilled(client, pharmacist, admin, auth_headers):
    headers = auth_headers(pharmacist)
    admin_headers = auth_headers(admin)
    vendor = _create_vendor(client, headers)
    drug = _create_drug(client, headers, name="TrackedDrug")
    pr = client.post("/api/inventory/purchase-requests", json={"drug_id": drug["id"], "requested_quantity": 20}, headers=headers).json()
    client.patch(f"/api/inventory/purchase-requests/{pr['id']}/approve", headers=admin_headers)

    po = _create_po(client, headers, vendor["id"], drug["id"], quantity=20, purchase_request_id=pr["id"])
    assert po["status"] == "Sent"

    pr_after_order = client.get("/api/inventory/purchase-requests", headers=headers).json()[0]
    assert pr_after_order["status"] == "Ordered"

    receive_resp = client.post(
        f"/api/inventory/purchase-orders/{po['id']}/receive",
        json={"lines": [{"po_line_id": po["lines"][0]["id"], "quantity_received": 20,
                         "expiry_date": (date.today() + timedelta(days=180)).isoformat()}]},
        headers=headers,
    )
    assert receive_resp.status_code == 201, receive_resp.text
    assert receive_resp.json()["purchase_order"]["status"] == "Received"

    pr_after_receive = client.get("/api/inventory/purchase-requests", headers=headers).json()[0]
    assert pr_after_receive["status"] == "Fulfilled"


def test_reapproving_already_decided_request_rejected(client, pharmacist, admin, auth_headers):
    headers = auth_headers(pharmacist)
    admin_headers = auth_headers(admin)
    drug = _create_drug(client, headers)
    pr = client.post("/api/inventory/purchase-requests", json={"drug_id": drug["id"], "requested_quantity": 5}, headers=headers).json()
    client.patch(f"/api/inventory/purchase-requests/{pr['id']}/approve", headers=admin_headers)
    resp = client.patch(f"/api/inventory/purchase-requests/{pr['id']}/reject", headers=admin_headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Goods receipt correctness
# ---------------------------------------------------------------------------

def test_partial_receipt_keeps_po_partially_received(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    vendor = _create_vendor(client, headers)
    drug = _create_drug(client, headers, name="PartialDrug")
    po = _create_po(client, headers, vendor["id"], drug["id"], quantity=100)

    resp = client.post(
        f"/api/inventory/purchase-orders/{po['id']}/receive",
        json={"lines": [{"po_line_id": po["lines"][0]["id"], "quantity_received": 40,
                         "expiry_date": (date.today() + timedelta(days=90)).isoformat()}]},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["purchase_order"]["status"] == "PartiallyReceived"
    assert resp.json()["purchase_order"]["lines"][0]["quantity_received"] == 40


def test_cannot_over_receive_beyond_quantity_ordered(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    vendor = _create_vendor(client, headers)
    drug = _create_drug(client, headers, name="OverReceiveDrug")
    po = _create_po(client, headers, vendor["id"], drug["id"], quantity=10)

    resp = client.post(
        f"/api/inventory/purchase-orders/{po['id']}/receive",
        json={"lines": [{"po_line_id": po["lines"][0]["id"], "quantity_received": 15,
                         "expiry_date": (date.today() + timedelta(days=90)).isoformat()}]},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "only 10 units remain" in resp.json()["detail"]


def test_received_batch_appears_in_pharmacy_stock_with_po_traceability(client, pharmacist, auth_headers, db_session):
    from app.models import DrugBatch

    headers = auth_headers(pharmacist)
    vendor = _create_vendor(client, headers)
    drug = _create_drug(client, headers, name="TraceableDrug")
    po = _create_po(client, headers, vendor["id"], drug["id"], quantity=30)
    client.post(
        f"/api/inventory/purchase-orders/{po['id']}/receive",
        json={"lines": [{"po_line_id": po["lines"][0]["id"], "quantity_received": 30, "batch_number": "PO-BATCH-1",
                         "expiry_date": (date.today() + timedelta(days=90)).isoformat(), "location": "Central Store"}]},
        headers=headers,
    )

    drug_detail = client.get(f"/api/pharmacy/drugs/{drug['id']}", headers=headers).json()
    assert drug_detail["quantity_on_hand"] == 30
    batch = drug_detail["batches"][0]
    assert batch["location"] == "Central Store"

    batch_row = db_session.query(DrugBatch).filter(DrugBatch.id == batch["id"]).first()
    assert batch_row.purchase_order_line_id == po["lines"][0]["id"]


def test_cannot_cancel_po_after_receiving(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    vendor = _create_vendor(client, headers)
    drug = _create_drug(client, headers, name="NoCancelDrug")
    po = _create_po(client, headers, vendor["id"], drug["id"], quantity=10)
    client.post(
        f"/api/inventory/purchase-orders/{po['id']}/receive",
        json={"lines": [{"po_line_id": po["lines"][0]["id"], "quantity_received": 5,
                         "expiry_date": (date.today() + timedelta(days=90)).isoformat()}]},
        headers=headers,
    )
    resp = client.post(f"/api/inventory/purchase-orders/{po['id']}/cancel", headers=headers)
    assert resp.status_code == 400


def test_cancel_po_before_any_receipt(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    vendor = _create_vendor(client, headers)
    drug = _create_drug(client, headers, name="CancelableDrug")
    po = _create_po(client, headers, vendor["id"], drug["id"], quantity=10)
    resp = client.post(f"/api/inventory/purchase-orders/{po['id']}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "Cancelled"


# ---------------------------------------------------------------------------
# Stock Transfer
# ---------------------------------------------------------------------------

def test_stock_transfer_moves_quantity_and_preserves_batch_identity(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="TransferDrug")
    batch = client.post(
        f"/api/pharmacy/drugs/{drug['id']}/batches",
        json={"batch_number": "XFER1", "received_quantity": 50, "expiry_date": (date.today() + timedelta(days=90)).isoformat()},
        headers=headers,
    ).json()

    resp = client.post(
        "/api/inventory/stock-transfers",
        json={"from_batch_id": batch["id"], "to_location": "Ward A Store", "quantity": 20},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["quantity"] == 20

    detail = client.get(f"/api/pharmacy/drugs/{drug['id']}", headers=headers).json()
    assert detail["quantity_on_hand"] == 50  # total across locations unchanged
    locations = {b["location"]: b["quantity_on_hand"] for b in detail["batches"]}
    assert locations["Main Store"] == 30
    assert locations["Ward A Store"] == 20


def test_stock_transfer_insufficient_stock_rejected(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="ShortTransferDrug")
    batch = client.post(
        f"/api/pharmacy/drugs/{drug['id']}/batches",
        json={"received_quantity": 5, "expiry_date": (date.today() + timedelta(days=90)).isoformat()},
        headers=headers,
    ).json()

    resp = client.post(
        "/api/inventory/stock-transfers",
        json={"from_batch_id": batch["id"], "to_location": "Ward B Store", "quantity": 10},
        headers=headers,
    )
    assert resp.status_code == 400

    # No partial mutation on failure.
    detail = client.get(f"/api/pharmacy/drugs/{drug['id']}", headers=headers).json()
    assert detail["quantity_on_hand"] == 5


def test_repeated_transfer_to_same_location_accumulates_in_one_batch(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="AccumDrug")
    batch = client.post(
        f"/api/pharmacy/drugs/{drug['id']}/batches",
        json={"batch_number": "ACC1", "received_quantity": 50, "expiry_date": (date.today() + timedelta(days=90)).isoformat()},
        headers=headers,
    ).json()

    client.post("/api/inventory/stock-transfers", json={"from_batch_id": batch["id"], "to_location": "Ward C Store", "quantity": 10}, headers=headers)
    client.post("/api/inventory/stock-transfers", json={"from_batch_id": batch["id"], "to_location": "Ward C Store", "quantity": 5}, headers=headers)

    detail = client.get(f"/api/pharmacy/drugs/{drug['id']}", headers=headers).json()
    ward_c_batches = [b for b in detail["batches"] if b["location"] == "Ward C Store"]
    assert len(ward_c_batches) == 1, "expected the two transfers to accumulate into one destination batch, not two"
    assert ward_c_batches[0]["quantity_on_hand"] == 15


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def two_org_pharmacists(make_user):
    return {
        "a": make_user(email="pharm.a@inv-a.com", role="Pharmacist"),
        "b": make_user(email="pharm.b@inv-b.com", role="Pharmacist"),
    }


def test_vendor_list_scoped_per_org(client, two_org_pharmacists, auth_headers):
    headers_a = auth_headers(two_org_pharmacists["a"])
    headers_b = auth_headers(two_org_pharmacists["b"])
    _create_vendor(client, headers_a, name="OrgAVendor")
    _create_vendor(client, headers_b, name="OrgBVendor")
    names_a = {v["name"] for v in client.get("/api/inventory/vendors", headers=headers_a).json()}
    assert names_a == {"OrgAVendor"}


def test_cannot_order_against_another_orgs_vendor(client, two_org_pharmacists, auth_headers):
    headers_a = auth_headers(two_org_pharmacists["a"])
    headers_b = auth_headers(two_org_pharmacists["b"])
    vendor_a = _create_vendor(client, headers_a, name="CrossOrgVendor")
    drug_b = _create_drug(client, headers_b, name="OrgBDrug")

    resp = client.post(
        "/api/inventory/purchase-orders",
        json={"vendor_id": vendor_a["id"], "lines": [{"drug_id": drug_b["id"], "quantity_ordered": 5, "unit_price": 1.0}]},
        headers=headers_b,
    )
    assert resp.status_code == 404


def test_cannot_transfer_another_orgs_batch(client, two_org_pharmacists, auth_headers):
    headers_a = auth_headers(two_org_pharmacists["a"])
    headers_b = auth_headers(two_org_pharmacists["b"])
    drug_a = _create_drug(client, headers_a, name="CrossOrgTransferDrug")
    batch_a = client.post(
        f"/api/pharmacy/drugs/{drug_a['id']}/batches",
        json={"received_quantity": 20, "expiry_date": (date.today() + timedelta(days=90)).isoformat()},
        headers=headers_a,
    ).json()

    resp = client.post(
        "/api/inventory/stock-transfers",
        json={"from_batch_id": batch_a["id"], "to_location": "Somewhere", "quantity": 5},
        headers=headers_b,
    )
    assert resp.status_code == 404
