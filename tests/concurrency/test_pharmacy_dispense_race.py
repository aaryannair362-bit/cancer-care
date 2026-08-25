"""
Real-concurrency test for the pharmacy dispensing race: two truly simultaneous requests both
try to dispense the same, nearly-exhausted stock. _consume_fefo (routers/pharmacy.py) checks
total availability BEFORE mutating anything and uses .with_for_update() when locking batch rows
-- this is where that gets a genuine answer against a live server instead of a guess, the same
way test_concurrent_requests.py does for the pre-existing nurse-assignment race.

Real row-level locking only exists on Postgres; SQLite (this test's DB, like the rest of the
suite) has no row-level locking at all, so a pass here is evidence for SQLite's behavior under
this code path specifically, not a substitute for testing under Postgres locking in production
-- the same documented caveat TEST_NOTES.md already carries for the nurse-assignment race.
"""
import concurrent.futures
from datetime import date, timedelta

import requests

from tests._voice_helpers import mint_tokens


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_concurrent_dispense_of_last_units_never_oversells(live_server_url, make_user, db_session):
    from app.models import Drug, DrugBatch

    pharmacist = make_user(email="race@pharmacy.com", role="Pharmacist")
    token = mint_tokens(pharmacist)["access_token"]
    headers = _auth_headers(token)

    drug = Drug(organization_id=pharmacist.organization_id, name="RaceDrug", unit_price=5.0, created_by=pharmacist.id)
    db_session.add(drug)
    db_session.flush()
    batch = DrugBatch(drug_id=drug.id, batch_number="RACE", received_quantity=10, quantity_on_hand=10,
                       expiry_date=date.today() + timedelta(days=90), received_by=pharmacist.id)
    db_session.add(batch)
    db_session.commit()
    drug_id = drug.id

    # 10 units on hand, 10 concurrent requests for 2 units each = 20 requested against 10
    # available. Exactly 5 should succeed (200) and 5 should fail clean (400 insufficient
    # stock) -- never a mix that sums to more than 10 dispensed, and never a 500.
    def _dispense(_):
        return requests.post(
            f"{live_server_url}/api/pharmacy/dispense",
            json={"drug_id": drug_id, "quantity": 2}, headers=headers, timeout=10,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_dispense, range(10)))

    assert all(r.status_code in (201, 400) for r in results), [r.status_code for r in results]
    succeeded = [r for r in results if r.status_code == 201]
    total_dispensed = sum(r.json()["quantity"] for r in succeeded)

    assert total_dispensed <= 10, (
        f"oversold: {total_dispensed} units dispensed against 10 on hand -- the FEFO "
        f"availability check has a real race under concurrent load"
    )

    db_session.expire_all()
    remaining = db_session.query(DrugBatch).filter(DrugBatch.id == batch.id).first().quantity_on_hand
    assert remaining == 10 - total_dispensed
