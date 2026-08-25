"""
Stock-manipulation primitives shared by the Pharmacy and Inventory routers -- both operate on
the same DrugBatch stock (pharmacy dispenses from it, inventory receives into it and transfers
it between locations), and the atomic-claim logic here is the actual concurrency-safety
mechanism for all of that. Kept in one place so it's never duplicated/diverged between routers.
"""
from datetime import date

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from .models import DrugBatch


def on_hand(db: Session, drug_id: int) -> int:
    """Sum of quantity_on_hand across all *non-expired* batches for a drug, across every
    location -- expired stock is never dispensable/transferable even though it's still
    physically on a shelf until Expiry Monitoring gets it written off."""
    total = db.query(func.coalesce(func.sum(DrugBatch.quantity_on_hand), 0)).filter(
        DrugBatch.drug_id == drug_id, DrugBatch.expiry_date >= date.today()
    ).scalar()
    return int(total or 0)


def claim_batch_stock(db: Session, batch_id: int, want: int, max_attempts: int = 20) -> int:
    """
    Atomically claims up to `want` units from one batch via a single conditional UPDATE
    (`SET quantity_on_hand = quantity_on_hand - :n WHERE id = :id AND quantity_on_hand >= :n`).

    Safe under real concurrency on any SQL backend, including SQLite: a single atomic UPDATE's
    WHERE clause is evaluated by the database against current committed state, not a
    Python-side snapshot that can go stale between a SELECT and a later write -- unlike
    `.with_for_update()`, which only gives real row-level locking on Postgres (confirmed live by
    the pharmacy dispense-race concurrency test, which oversold 20 units against 10 on hand
    under an earlier with_for_update()-based implementation of this same logic).

    If the UPDATE matches zero rows, a concurrent claim changed the batch between our last read
    and this attempt -- re-read the live quantity and retry for the smaller amount (or stop if
    it's now 0). Bounded by max_attempts as a safety valve; in practice this converges in one or
    two iterations even under heavy contention, since `want` only ever shrinks.
    """
    for _ in range(max_attempts):
        if want <= 0:
            return 0
        result = db.execute(
            update(DrugBatch)
            .where(DrugBatch.id == batch_id, DrugBatch.quantity_on_hand >= want)
            .values(quantity_on_hand=DrugBatch.quantity_on_hand - want)
        )
        if result.rowcount == 1:
            return want
        current = db.query(DrugBatch.quantity_on_hand).filter(DrugBatch.id == batch_id).scalar()
        if not current:
            return 0
        want = min(want, current)
    return 0
