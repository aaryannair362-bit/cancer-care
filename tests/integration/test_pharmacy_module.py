"""
Pharmacy module tests: role permissions, multi-tenant isolation, FEFO dispensing correctness,
and the NDPS controlled-drug register ledger. Mirrors the structure of
test_role_permission_matrix.py and test_multi_tenant_isolation.py, extended to the new
/api/pharmacy/* routes (backend/app/routers/pharmacy.py).
"""
from datetime import date, timedelta

import pytest


@pytest.fixture
def pharmacist(make_user):
    return make_user(email="pharmacist@hosp.com", role="Pharmacist")


@pytest.fixture
def admin(make_user, pharmacist):
    return make_user(email="admin@hosp.com", role="Admin", organization_id=pharmacist.organization_id)


@pytest.fixture
def other_roles(make_user, pharmacist):
    org_id = pharmacist.organization_id
    return {
        "Doctor": make_user(email="doctor@hosp.com", role="Doctor", organization_id=org_id),
        "Nurse": make_user(email="nurse@hosp.com", role="Nurse", organization_id=org_id),
        "HeadNurse": make_user(email="headnurse@hosp.com", role="HeadNurse", organization_id=org_id),
        "NursingStation": make_user(email="station@hosp.com", role="NursingStation", organization_id=org_id),
    }


def _create_drug(client, headers, **overrides):
    payload = {"name": "Paracetamol", "form": "Tablet", "strength": "500mg", "unit_price": 2.5}
    payload.update(overrides)
    resp = client.post("/api/pharmacy/drugs", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _receive_batch(client, headers, drug_id, expiry_offset_days, quantity, batch_number="B1"):
    payload = {
        "batch_number": batch_number,
        "received_quantity": quantity,
        "expiry_date": (date.today() + timedelta(days=expiry_offset_days)).isoformat(),
    }
    resp = client.post(f"/api/pharmacy/drugs/{drug_id}/batches", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Role permissions
# ---------------------------------------------------------------------------

def test_pharmacist_and_admin_can_create_drugs(client, pharmacist, admin, auth_headers):
    for user in (pharmacist, admin):
        resp = client.post(
            "/api/pharmacy/drugs",
            json={"name": f"Drug for {user.role}", "unit_price": 1.0},
            headers=auth_headers(user),
        )
        assert resp.status_code == 201, resp.text


def test_other_roles_cannot_access_pharmacy(client, other_roles, auth_headers):
    for role, user in other_roles.items():
        resp = client.get("/api/pharmacy/drugs", headers=auth_headers(user))
        assert resp.status_code == 403, f"{role} should not have pharmacy access, got {resp.status_code}"
        resp = client.post("/api/pharmacy/drugs", json={"name": "X", "unit_price": 1.0}, headers=auth_headers(user))
        assert resp.status_code == 403, f"{role} should not be able to create drugs, got {resp.status_code}"


def test_unauthenticated_request_rejected(client):
    resp = client.get("/api/pharmacy/drugs")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Drug formulary CRUD
# ---------------------------------------------------------------------------

def test_create_drug_rejects_exact_duplicate(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    _create_drug(client, headers)
    resp = client.post(
        "/api/pharmacy/drugs",
        json={"name": "Paracetamol", "form": "Tablet", "strength": "500mg", "unit_price": 3.0},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


def test_create_drug_allows_same_name_different_strength(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    _create_drug(client, headers, strength="500mg")
    resp = client.post(
        "/api/pharmacy/drugs",
        json={"name": "Paracetamol", "form": "Tablet", "strength": "650mg", "unit_price": 3.0},
        headers=headers,
    )
    assert resp.status_code == 201


def test_negative_unit_price_rejected(client, pharmacist, auth_headers):
    resp = client.post(
        "/api/pharmacy/drugs", json={"name": "BadDrug", "unit_price": -5.0}, headers=auth_headers(pharmacist)
    )
    assert resp.status_code == 422


def test_list_drugs_search_and_low_stock_filter(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    d1 = _create_drug(client, headers, name="Amoxicillin", reorder_level=100)
    d2 = _create_drug(client, headers, name="Azithromycin", reorder_level=5)
    _receive_batch(client, headers, d1["id"], 365, quantity=10)   # below reorder_level=100
    _receive_batch(client, headers, d2["id"], 365, quantity=10)   # above reorder_level=5

    resp = client.get("/api/pharmacy/drugs", params={"search": "Amox"}, headers=headers)
    names = [d["name"] for d in resp.json()]
    assert names == ["Amoxicillin"]

    resp = client.get("/api/pharmacy/drugs", params={"low_stock": True}, headers=headers)
    low_stock_names = {d["name"] for d in resp.json()}
    assert "Amoxicillin" in low_stock_names
    assert "Azithromycin" not in low_stock_names


def test_barcode_lookup(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    _create_drug(client, headers, name="Cetrizine", barcode="ABC123")
    resp = client.get("/api/pharmacy/drugs/by-barcode/ABC123", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Cetrizine"
    resp = client.get("/api/pharmacy/drugs/by-barcode/NOPE", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Batch receiving / expiry
# ---------------------------------------------------------------------------

def test_receive_batch_rejects_already_expired(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers)
    resp = client.post(
        f"/api/pharmacy/drugs/{drug['id']}/batches",
        json={"received_quantity": 10, "expiry_date": (date.today() - timedelta(days=1)).isoformat()},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "expiry_date must be in the future" in resp.json()["detail"]


def test_expiry_alerts_returns_only_batches_within_window(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="ExpiryTest")
    _receive_batch(client, headers, drug["id"], expiry_offset_days=10, quantity=5, batch_number="SOON")
    _receive_batch(client, headers, drug["id"], expiry_offset_days=365, quantity=5, batch_number="LATER")

    resp = client.get("/api/pharmacy/expiry-alerts", params={"days": 30}, headers=headers)
    batch_numbers = {b["batch_number"] for b in resp.json()}
    assert "SOON" in batch_numbers
    assert "LATER" not in batch_numbers


# ---------------------------------------------------------------------------
# Dispensing: FEFO correctness, insufficient stock, financial correctness
# ---------------------------------------------------------------------------

def test_dispense_consumes_earliest_expiring_batch_first(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="FEFOTest", unit_price=10.0)
    later = _receive_batch(client, headers, drug["id"], expiry_offset_days=365, quantity=20, batch_number="LATER")
    sooner = _receive_batch(client, headers, drug["id"], expiry_offset_days=30, quantity=20, batch_number="SOON")

    resp = client.post(
        "/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 5}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["lines"]) == 1
    assert body["lines"][0]["batch_id"] == sooner["id"], "FEFO violated: should consume the sooner-expiring batch first"
    assert body["total_amount"] == 50.0


def test_dispense_spans_multiple_batches_when_first_is_insufficient(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="SpanTest", unit_price=4.0)
    sooner = _receive_batch(client, headers, drug["id"], expiry_offset_days=30, quantity=3, batch_number="SOON")
    later = _receive_batch(client, headers, drug["id"], expiry_offset_days=365, quantity=20, batch_number="LATER")

    resp = client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 5}, headers=headers)
    assert resp.status_code == 201, resp.text
    lines = resp.json()["lines"]
    assert len(lines) == 2
    by_batch = {l["batch_id"]: l["quantity"] for l in lines}
    assert by_batch[sooner["id"]] == 3
    assert by_batch[later["id"]] == 2
    assert resp.json()["total_amount"] == 20.0


def test_dispense_insufficient_stock_leaves_no_partial_mutation(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="ShortTest", unit_price=1.0)
    batch = _receive_batch(client, headers, drug["id"], expiry_offset_days=30, quantity=3)

    resp = client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 10}, headers=headers)
    assert resp.status_code == 400
    assert "Insufficient stock" in resp.json()["detail"]

    # The batch must be untouched -- a failed dispense must not partially consume stock.
    detail = client.get(f"/api/pharmacy/drugs/{drug['id']}", headers=headers).json()
    assert detail["batches"][0]["quantity_on_hand"] == 3


def test_dispense_ignores_expired_batches(client, pharmacist, auth_headers, db_session):
    """
    Can't create an already-expired batch through the API (POST .../batches rejects it by
    design) -- so this ages a batch past expiry directly via the ORM to simulate the realistic
    path: a batch that was valid on receipt and expired later while sitting on the shelf.
    """
    from app.models import DrugBatch

    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="ExpiredStock", unit_price=1.0)
    fresh = _receive_batch(client, headers, drug["id"], expiry_offset_days=30, quantity=2)
    stale = _receive_batch(client, headers, drug["id"], expiry_offset_days=60, quantity=10)

    stale_row = db_session.query(DrugBatch).filter(DrugBatch.id == stale["id"]).first()
    stale_row.expiry_date = date.today() - timedelta(days=1)
    db_session.commit()

    # Only the fresh batch's 2 units should count -- the stale batch's 10 units, despite still
    # showing quantity_on_hand > 0, must not be treated as dispensable stock.
    resp = client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 5}, headers=headers)
    assert resp.status_code == 400
    assert "Insufficient stock: 2 on hand" in resp.json()["detail"]

    resp = client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 2}, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["lines"][0]["batch_id"] == fresh["id"]


def test_dispense_requires_valid_org_scoped_patient(client, pharmacist, auth_headers, make_user, db_session):
    from app.models import Patient

    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="PatientScopeTest", unit_price=1.0)
    _receive_batch(client, headers, drug["id"], expiry_offset_days=30, quantity=10)

    other_pharmacist = make_user(email="other@otherhosp.com", role="Pharmacist")
    foreign_patient = Patient(name="Foreign", age=30, gender="M", organization_id=other_pharmacist.organization_id,
                               created_by=other_pharmacist.id)
    db_session.add(foreign_patient)
    db_session.commit()
    db_session.refresh(foreign_patient)

    resp = client.post(
        "/api/pharmacy/dispense",
        json={"drug_id": drug["id"], "quantity": 1, "patient_id": foreign_patient.id},
        headers=headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Controlled Drug Register (NDPS)
# ---------------------------------------------------------------------------

def test_controlled_drug_dispense_creates_register_entry(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="Morphine", unit_price=50.0, is_controlled=True)
    _receive_batch(client, headers, drug["id"], expiry_offset_days=180, quantity=10)

    client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 3}, headers=headers)

    resp = client.get("/api/pharmacy/controlled-drug-register", headers=headers)
    assert resp.status_code == 200
    ledger = resp.json()
    assert len(ledger) == 1
    assert ledger[0]["drug_name"] == "Morphine"
    assert ledger[0]["current_balance"] == 7  # 10 received - 3 dispensed
    types = [e["type"] for e in ledger[0]["entries"]]
    assert types == ["receipt", "dispense"]
    assert ledger[0]["entries"][-1]["running_balance"] == 7


def test_non_controlled_drug_never_appears_in_register(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="Vitamin C", unit_price=1.0, is_controlled=False)
    _receive_batch(client, headers, drug["id"], expiry_offset_days=180, quantity=10)
    client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 1}, headers=headers)

    resp = client.get("/api/pharmacy/controlled-drug-register", headers=headers)
    assert resp.json() == []


def test_controlled_register_balance_reconstructs_across_multiple_dispenses(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="Fentanyl", unit_price=100.0, is_controlled=True)
    _receive_batch(client, headers, drug["id"], expiry_offset_days=180, quantity=20)

    client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 5}, headers=headers)
    client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 4}, headers=headers)

    ledger = client.get("/api/pharmacy/controlled-drug-register", headers=headers).json()
    assert ledger[0]["current_balance"] == 11  # 20 - 5 - 4


# ---------------------------------------------------------------------------
# Refill
# ---------------------------------------------------------------------------

def test_refill_reuses_original_quantity_and_links_lineage(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="RefillTest", unit_price=2.0)
    _receive_batch(client, headers, drug["id"], expiry_offset_days=180, quantity=20)

    first = client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 3}, headers=headers).json()
    original_id = first["lines"][0]["id"]

    resp = client.post(f"/api/pharmacy/refill/{original_id}", json={}, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["quantity"] == 3  # defaults to original quantity
    assert body["refill_of_id"] == original_id
    assert body["lines"][0]["refill_of_id"] == original_id


def test_refill_can_override_quantity(client, pharmacist, auth_headers):
    headers = auth_headers(pharmacist)
    drug = _create_drug(client, headers, name="RefillOverride", unit_price=2.0)
    _receive_batch(client, headers, drug["id"], expiry_offset_days=180, quantity=20)

    first = client.post("/api/pharmacy/dispense", json={"drug_id": drug["id"], "quantity": 3}, headers=headers).json()
    original_id = first["lines"][0]["id"]

    resp = client.post(f"/api/pharmacy/refill/{original_id}", json={"quantity": 7}, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["quantity"] == 7


# ---------------------------------------------------------------------------
# Multi-tenant isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def two_org_pharmacists(make_user):
    return {
        "a": make_user(email="pharm.a@hosp-a.com", role="Pharmacist"),
        "b": make_user(email="pharm.b@hosp-b.com", role="Pharmacist"),
    }


def test_drug_list_does_not_leak_across_orgs(client, two_org_pharmacists, auth_headers):
    headers_a = auth_headers(two_org_pharmacists["a"])
    headers_b = auth_headers(two_org_pharmacists["b"])
    _create_drug(client, headers_a, name="OrgADrug")
    _create_drug(client, headers_b, name="OrgBDrug")

    names_a = {d["name"] for d in client.get("/api/pharmacy/drugs", headers=headers_a).json()}
    assert names_a == {"OrgADrug"}


def test_cannot_dispense_another_orgs_drug(client, two_org_pharmacists, auth_headers):
    headers_a = auth_headers(two_org_pharmacists["a"])
    headers_b = auth_headers(two_org_pharmacists["b"])
    drug_a = _create_drug(client, headers_a, name="CrossOrgDrug")
    _receive_batch(client, headers_a, drug_a["id"], 30, quantity=10)

    resp = client.post("/api/pharmacy/dispense", json={"drug_id": drug_a["id"], "quantity": 1}, headers=headers_b)
    assert resp.status_code == 404, "Org B pharmacist dispensed Org A's drug -- cross-tenant leak"


def test_cannot_receive_batch_for_another_orgs_drug(client, two_org_pharmacists, auth_headers):
    headers_a = auth_headers(two_org_pharmacists["a"])
    headers_b = auth_headers(two_org_pharmacists["b"])
    drug_a = _create_drug(client, headers_a, name="CrossOrgBatch")

    resp = client.post(
        f"/api/pharmacy/drugs/{drug_a['id']}/batches",
        json={"received_quantity": 5, "expiry_date": (date.today() + timedelta(days=90)).isoformat()},
        headers=headers_b,
    )
    assert resp.status_code == 404


def test_controlled_drug_register_scoped_per_org(client, two_org_pharmacists, auth_headers):
    headers_a = auth_headers(two_org_pharmacists["a"])
    headers_b = auth_headers(two_org_pharmacists["b"])
    drug_a = _create_drug(client, headers_a, name="OrgAControlled", is_controlled=True)
    _receive_batch(client, headers_a, drug_a["id"], 90, quantity=10)
    client.post("/api/pharmacy/dispense", json={"drug_id": drug_a["id"], "quantity": 2}, headers=headers_a)

    ledger_b = client.get("/api/pharmacy/controlled-drug-register", headers=headers_b).json()
    assert ledger_b == [], "Org B can see Org A's controlled-drug register -- statutory-record cross-tenant leak"
