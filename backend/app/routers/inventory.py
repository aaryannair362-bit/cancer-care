"""
Inventory & Stores module: Vendor Management, the Purchase Request -> Purchase Order ->
Goods Receipt procurement chain, and Stock Transfer between locations. Feeds the same
Drug/DrugBatch stock the Pharmacy router (routers/pharmacy.py) dispenses from -- goods receipt
here creates DrugBatch rows exactly like Pharmacy's own direct-receive endpoint does, just with
a purchase_order_line_id back-reference for traceability, and Stock Management/Consumption
Tracking/Expiry Monitoring (also named in the source requirements doc's Inventory & Stores
section) are already covered by Pharmacy's existing drug-listing and expiry-alerts endpoints --
not duplicated here.

Approval authority is split from general pharmacy-staff access: creating a Purchase Request or
Purchase Order, receiving goods, and transferring stock only requires Admin/Pharmacist (same
gate as the pharmacy router); approving/rejecting a Purchase Request is Admin-only, so a
Pharmacist can't approve their own request. This wasn't stated anywhere in the source
requirements doc -- flagged here as an inferred design choice, not a confirmed requirement,
consistent with how Billing Workflow's "Discount Approval" implies the same kind of
requester/approver split elsewhere in the same source document.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_admin, is_pharmacist, is_inventory_manager, log_audit
from ..database import get_db
from ..models import Drug, DrugBatch, PurchaseOrder, PurchaseOrderLine, PurchaseRequest, StockTransfer, Vendor
from ..stock_utils import claim_batch_stock

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


def _require_pharmacy_staff(user: dict) -> None:
    # InventoryManager added alongside Admin/Pharmacist -- frontend/inventory.html and
    # js/api.js's ROLE_HOME/NAV_ITEMS already route this role here (that page's own
    # Auth.requirePage() previously omitted it too, a separate frontend-only bug fixed
    # earlier -- this is the backend half: the role reached the page but every call 403'd).
    if not (is_admin(user) or is_pharmacist(user) or is_inventory_manager(user)):
        raise HTTPException(403, "Only Admin, Pharmacist, or Inventory Manager can access the inventory module")


def _require_admin(user: dict) -> None:
    if not is_admin(user):
        raise HTTPException(403, "Only Admin can approve or reject a purchase request")


def _get_org_vendor(db: Session, vendor_id: int, org_id: int) -> Vendor:
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.organization_id == org_id).first()
    if not vendor:
        raise HTTPException(404, "Vendor not found")
    return vendor


def _get_org_drug(db: Session, drug_id: int, org_id: int) -> Drug:
    drug = db.query(Drug).filter(Drug.id == drug_id, Drug.organization_id == org_id).first()
    if not drug:
        raise HTTPException(404, "Drug not found")
    return drug


def _get_org_purchase_order(db: Session, po_id: int, org_id: int) -> PurchaseOrder:
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id, PurchaseOrder.organization_id == org_id).first()
    if not po:
        raise HTTPException(404, "Purchase order not found")
    return po


def _vendor_out(v: Vendor) -> dict:
    return {
        "id": v.id, "name": v.name, "contact_person": v.contact_person, "phone": v.phone,
        "email": v.email, "gstin": v.gstin, "address": v.address, "is_active": v.is_active,
    }


def _pr_out(pr: PurchaseRequest) -> dict:
    return {
        "id": pr.id, "drug_id": pr.drug_id, "requested_quantity": pr.requested_quantity,
        "status": pr.status, "notes": pr.notes, "requested_by": pr.requested_by,
        "requested_at": pr.requested_at.isoformat(), "decided_by": pr.decided_by,
        "decided_at": pr.decided_at.isoformat() if pr.decided_at else None,
    }


def _po_line_out(line: PurchaseOrderLine) -> dict:
    return {
        "id": line.id, "drug_id": line.drug_id, "quantity_ordered": line.quantity_ordered,
        "unit_price": line.unit_price, "quantity_received": line.quantity_received,
    }


def _po_out(po: PurchaseOrder) -> dict:
    return {
        "id": po.id, "vendor_id": po.vendor_id, "purchase_request_id": po.purchase_request_id,
        "status": po.status,
        "expected_delivery_date": po.expected_delivery_date.isoformat() if po.expected_delivery_date else None,
        "created_by": po.created_by, "created_at": po.created_at.isoformat(),
        "lines": [_po_line_out(l) for l in po.lines],
    }


def _transfer_out(t: StockTransfer) -> dict:
    return {
        "id": t.id, "drug_id": t.drug_id, "from_batch_id": t.from_batch_id, "to_batch_id": t.to_batch_id,
        "to_location": t.to_location, "quantity": t.quantity, "notes": t.notes,
        "transferred_by": t.transferred_by, "transferred_at": t.transferred_at.isoformat(),
    }


class VendorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    contact_person: Optional[str] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    email: Optional[str] = None
    gstin: Optional[str] = Field(default=None, max_length=20)
    address: Optional[str] = None


class VendorUpdate(BaseModel):
    contact_person: Optional[str] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    email: Optional[str] = None
    gstin: Optional[str] = Field(default=None, max_length=20)
    address: Optional[str] = None
    is_active: Optional[bool] = None


class PurchaseRequestCreate(BaseModel):
    drug_id: int
    requested_quantity: int = Field(gt=0)
    notes: Optional[str] = None


class POLineIn(BaseModel):
    drug_id: int
    quantity_ordered: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class PurchaseOrderCreate(BaseModel):
    vendor_id: int
    purchase_request_id: Optional[int] = None
    expected_delivery_date: Optional[date] = None
    lines: list[POLineIn] = Field(min_length=1)


class ReceiveLineIn(BaseModel):
    po_line_id: int
    quantity_received: int = Field(gt=0)
    batch_number: Optional[str] = None
    expiry_date: date
    location: str = Field(default="Main Store", max_length=100)


class ReceiveRequest(BaseModel):
    lines: list[ReceiveLineIn] = Field(min_length=1)


class StockTransferCreate(BaseModel):
    from_batch_id: int
    to_location: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0)
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Vendor Management
# ---------------------------------------------------------------------------

@router.post("/vendors", status_code=201)
def create_vendor(payload: VendorCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    existing = db.query(Vendor).filter(Vendor.organization_id == org_id, Vendor.name == payload.name).first()
    if existing:
        raise HTTPException(400, "A vendor with this name already exists")
    vendor = Vendor(organization_id=org_id, created_by=current_user["id"], **payload.model_dump())
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_vendor", f"vendors/{vendor.id}", "Success")
    return _vendor_out(vendor)


@router.get("/vendors")
def list_vendors(
    active_only: bool = False, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    q = db.query(Vendor).filter(Vendor.organization_id == org_id)
    if active_only:
        q = q.filter(Vendor.is_active == True)  # noqa: E712
    return [_vendor_out(v) for v in q.order_by(Vendor.name).all()]


@router.patch("/vendors/{vendor_id}")
def update_vendor(vendor_id: int, payload: VendorUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    vendor = _get_org_vendor(db, vendor_id, org_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(vendor, field, value)
    db.commit()
    log_audit(db, current_user["id"], current_user["email"], org_id, "update_vendor", f"vendors/{vendor_id}", "Success")
    return _vendor_out(vendor)


# ---------------------------------------------------------------------------
# Purchase Request (indent) -- requester/approver split
# ---------------------------------------------------------------------------

@router.post("/purchase-requests", status_code=201)
def create_purchase_request(payload: PurchaseRequestCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    _get_org_drug(db, payload.drug_id, org_id)
    pr = PurchaseRequest(
        organization_id=org_id, drug_id=payload.drug_id, requested_quantity=payload.requested_quantity,
        notes=payload.notes, requested_by=current_user["id"],
    )
    db.add(pr)
    db.commit()
    db.refresh(pr)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_purchase_request", f"purchase_requests/{pr.id}", "Success")
    return _pr_out(pr)


@router.get("/purchase-requests")
def list_purchase_requests(
    status: Optional[str] = None, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    q = db.query(PurchaseRequest).filter(PurchaseRequest.organization_id == org_id)
    if status:
        q = q.filter(PurchaseRequest.status == status)
    return [_pr_out(pr) for pr in q.order_by(PurchaseRequest.requested_at.desc()).all()]


def _decide_purchase_request(db: Session, pr_id: int, org_id: int, new_status: str, current_user: dict) -> PurchaseRequest:
    pr = db.query(PurchaseRequest).filter(PurchaseRequest.id == pr_id, PurchaseRequest.organization_id == org_id).first()
    if not pr:
        raise HTTPException(404, "Purchase request not found")
    if pr.status != "Pending":
        raise HTTPException(400, f"Purchase request is already {pr.status}, cannot decide again")
    pr.status = new_status
    pr.decided_by = current_user["id"]
    pr.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(pr)
    return pr


@router.patch("/purchase-requests/{pr_id}/approve")
def approve_purchase_request(pr_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    org_id = current_user.get("organization_id")
    pr = _decide_purchase_request(db, pr_id, org_id, "Approved", current_user)
    log_audit(db, current_user["id"], current_user["email"], org_id, "approve_purchase_request", f"purchase_requests/{pr_id}", "Success")
    return _pr_out(pr)


@router.patch("/purchase-requests/{pr_id}/reject")
def reject_purchase_request(pr_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    org_id = current_user.get("organization_id")
    pr = _decide_purchase_request(db, pr_id, org_id, "Rejected", current_user)
    log_audit(db, current_user["id"], current_user["email"], org_id, "reject_purchase_request", f"purchase_requests/{pr_id}", "Success")
    return _pr_out(pr)


# ---------------------------------------------------------------------------
# Purchase Order + Goods Receipt
# ---------------------------------------------------------------------------

@router.post("/purchase-orders", status_code=201)
def create_purchase_order(payload: PurchaseOrderCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    vendor = _get_org_vendor(db, payload.vendor_id, org_id)
    if not vendor.is_active:
        raise HTTPException(400, "Cannot place a purchase order against an inactive vendor")

    linked_pr = None
    if payload.purchase_request_id is not None:
        linked_pr = db.query(PurchaseRequest).filter(
            PurchaseRequest.id == payload.purchase_request_id, PurchaseRequest.organization_id == org_id
        ).first()
        if not linked_pr:
            raise HTTPException(404, "Purchase request not found")
        if linked_pr.status != "Approved":
            raise HTTPException(400, f"Purchase request must be Approved before ordering (currently {linked_pr.status})")

    for line in payload.lines:
        _get_org_drug(db, line.drug_id, org_id)

    po = PurchaseOrder(
        organization_id=org_id, vendor_id=vendor.id, purchase_request_id=payload.purchase_request_id,
        expected_delivery_date=payload.expected_delivery_date, created_by=current_user["id"],
    )
    db.add(po)
    db.flush()
    for line in payload.lines:
        db.add(PurchaseOrderLine(
            purchase_order_id=po.id, drug_id=line.drug_id,
            quantity_ordered=line.quantity_ordered, unit_price=line.unit_price,
        ))
    if linked_pr is not None:
        linked_pr.status = "Ordered"
    db.commit()
    db.refresh(po)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_purchase_order", f"purchase_orders/{po.id}",
               "Success", f"vendor_id={vendor.id} lines={len(payload.lines)}")
    return _po_out(po)


@router.get("/purchase-orders")
def list_purchase_orders(
    status: Optional[str] = None, vendor_id: Optional[int] = None,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    q = db.query(PurchaseOrder).filter(PurchaseOrder.organization_id == org_id)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    if vendor_id:
        q = q.filter(PurchaseOrder.vendor_id == vendor_id)
    return [_po_out(po) for po in q.order_by(PurchaseOrder.created_at.desc()).all()]


@router.get("/purchase-orders/{po_id}")
def get_purchase_order(po_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    po = _get_org_purchase_order(db, po_id, current_user.get("organization_id"))
    return _po_out(po)


@router.post("/purchase-orders/{po_id}/cancel")
def cancel_purchase_order(po_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    po = _get_org_purchase_order(db, po_id, org_id)
    if po.status not in ("Sent",):
        raise HTTPException(400, f"Cannot cancel a purchase order that is {po.status}")
    if any(line.quantity_received > 0 for line in po.lines):
        raise HTTPException(400, "Cannot cancel a purchase order that has already received goods")
    po.status = "Cancelled"
    db.commit()
    log_audit(db, current_user["id"], current_user["email"], org_id, "cancel_purchase_order", f"purchase_orders/{po_id}", "Success")
    return _po_out(po)


@router.post("/purchase-orders/{po_id}/receive", status_code=201)
def receive_purchase_order(po_id: int, payload: ReceiveRequest, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Goods Receipt against a Purchase Order: creates a DrugBatch per receive line (same
    dispensable stock Pharmacy's own /drugs/{id}/batches endpoint creates), traceable back to
    the PO line via purchase_order_line_id."""
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    po = _get_org_purchase_order(db, po_id, org_id)
    if po.status not in ("Sent", "PartiallyReceived"):
        raise HTTPException(400, f"Cannot receive goods against a purchase order that is {po.status}")

    lines_by_id = {line.id: line for line in po.lines}
    created_batches = []
    for receive_line in payload.lines:
        po_line = lines_by_id.get(receive_line.po_line_id)
        if po_line is None:
            raise HTTPException(404, f"Purchase order line {receive_line.po_line_id} not found on this order")
        remaining = po_line.quantity_ordered - po_line.quantity_received
        if receive_line.quantity_received > remaining:
            raise HTTPException(400, f"Line {po_line.id}: only {remaining} units remain to be received (ordered {po_line.quantity_ordered})")
        if receive_line.expiry_date <= date.today():
            raise HTTPException(400, "expiry_date must be in the future -- a batch received already expired is not dispensable stock")

        batch = DrugBatch(
            drug_id=po_line.drug_id, batch_number=receive_line.batch_number,
            received_quantity=receive_line.quantity_received, quantity_on_hand=receive_line.quantity_received,
            expiry_date=receive_line.expiry_date, received_by=current_user["id"],
            location=receive_line.location, purchase_order_line_id=po_line.id,
        )
        db.add(batch)
        po_line.quantity_received += receive_line.quantity_received
        created_batches.append(batch)

    db.flush()
    if all(line.quantity_received >= line.quantity_ordered for line in po.lines):
        po.status = "Received"
        if po.purchase_request_id is not None:
            linked_pr = db.query(PurchaseRequest).filter(PurchaseRequest.id == po.purchase_request_id).first()
            if linked_pr is not None:
                linked_pr.status = "Fulfilled"
    else:
        po.status = "PartiallyReceived"

    db.commit()
    for b in created_batches:
        db.refresh(b)
    log_audit(db, current_user["id"], current_user["email"], org_id, "receive_purchase_order", f"purchase_orders/{po_id}",
               "Success", f"batches_created={len(created_batches)}")
    return {
        "purchase_order": _po_out(po),
        "batches_created": [
            {"id": b.id, "drug_id": b.drug_id, "quantity_on_hand": b.quantity_on_hand,
             "expiry_date": b.expiry_date.isoformat(), "location": b.location}
            for b in created_batches
        ],
    }


# ---------------------------------------------------------------------------
# Stock Transfer
# ---------------------------------------------------------------------------

@router.post("/stock-transfers", status_code=201)
def create_stock_transfer(payload: StockTransferCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")

    from_batch = db.query(DrugBatch).filter(DrugBatch.id == payload.from_batch_id).first()
    if not from_batch or from_batch.drug.organization_id != org_id:
        raise HTTPException(404, "Source batch not found")

    claimed = claim_batch_stock(db, from_batch.id, payload.quantity)
    if claimed < payload.quantity:
        db.rollback()
        raise HTTPException(400, f"Insufficient stock in source batch: {claimed} available, {payload.quantity} requested")

    destination = db.query(DrugBatch).filter(
        DrugBatch.drug_id == from_batch.drug_id, DrugBatch.batch_number == from_batch.batch_number,
        DrugBatch.expiry_date == from_batch.expiry_date, DrugBatch.location == payload.to_location,
    ).first()
    if destination is not None:
        db.execute(
            update(DrugBatch).where(DrugBatch.id == destination.id)
            .values(quantity_on_hand=DrugBatch.quantity_on_hand + claimed)
        )
    else:
        destination = DrugBatch(
            drug_id=from_batch.drug_id, batch_number=from_batch.batch_number,
            received_quantity=claimed, quantity_on_hand=claimed,
            expiry_date=from_batch.expiry_date, received_by=current_user["id"],
            location=payload.to_location,
        )
        db.add(destination)
    db.flush()

    transfer = StockTransfer(
        organization_id=org_id, drug_id=from_batch.drug_id, from_batch_id=from_batch.id,
        to_batch_id=destination.id, to_location=payload.to_location, quantity=claimed,
        notes=payload.notes, transferred_by=current_user["id"],
    )
    db.add(transfer)
    db.commit()
    db.refresh(transfer)
    log_audit(db, current_user["id"], current_user["email"], org_id, "stock_transfer", f"drug_batches/{from_batch.id}",
               "Success", f"to={payload.to_location} qty={claimed}")
    return _transfer_out(transfer)


@router.get("/stock-transfers")
def list_stock_transfers(
    drug_id: Optional[int] = None, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_pharmacy_staff(current_user)
    org_id = current_user.get("organization_id")
    q = db.query(StockTransfer).filter(StockTransfer.organization_id == org_id)
    if drug_id is not None:
        q = q.filter(StockTransfer.drug_id == drug_id)
    return [_transfer_out(t) for t in q.order_by(StockTransfer.transferred_at.desc()).all()]
