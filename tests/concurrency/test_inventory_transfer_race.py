"""
Real-concurrency test for stock transfer: a second call site into stock_utils.claim_batch_stock
(inventory.py) besides pharmacy dispensing. Confirms the shared atomic-claim primitive holds
under real simultaneous transfer requests too, not just dispensing.
"""
import concurrent.futures
from datetime import date, timedelta

import requests

from tests._voice_helpers import mint_tokens


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_concurrent_transfers_of_last_units_never_oversell(live_server_url, make_user, db_session):
    from app.models import Drug, DrugBatch

    pharmacist = make_user(email="transfer-race@pharmacy.com", role="Pharmacist")
    token = mint_tokens(pharmacist)["access_token"]
    headers = _auth_headers(token)

    drug = Drug(organization_id=pharmacist.organization_id, name="TransferRaceDrug", unit_price=5.0, created_by=pharmacist.id)
    db_session.add(drug)
    db_session.flush()
    batch = DrugBatch(drug_id=drug.id, batch_number="TXRACE", received_quantity=10, quantity_on_hand=10,
                       expiry_date=date.today() + timedelta(days=90), received_by=pharmacist.id, location="Main Store")
    db_session.add(batch)
    db_session.commit()
    batch_id = batch.id

    # 10 units on hand, 10 concurrent transfer requests for 2 units each = 20 requested.
    def _transfer(_):
        return requests.post(
            f"{live_server_url}/api/inventory/stock-transfers",
            json={"from_batch_id": batch_id, "to_location": "Ward Race Store", "quantity": 2},
            headers=headers, timeout=10,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_transfer, range(10)))

    assert all(r.status_code in (201, 400) for r in results), [r.status_code for r in results]
    succeeded = [r for r in results if r.status_code == 201]
    total_transferred = sum(r.json()["quantity"] for r in succeeded)

    assert total_transferred <= 10, (
        f"oversold: {total_transferred} units transferred against 10 on hand"
    )

    db_session.expire_all()
    remaining = db_session.query(DrugBatch).filter(DrugBatch.id == batch_id).first().quantity_on_hand
    assert remaining == 10 - total_transferred
