"""
Billing module: tariffs, invoices built from manual/tariff/package/pharmacy/bed-day line items,
discount approval, payment collection, refunds, and a lightweight insurance/corporate claim
record. Covers every bullet under Basic HMS.pdf's Billing Workflow section (Service Capture,
Tariff Application, Package Billing, Insurance Billing, Corporate Billing, Discount Approval,
Payment Collection, Invoice Generation, Refund Management) -- deliberately NOT the full 8-step
Insurance/TPA claims pipeline (Insurance Verification, Pre-Authorization, Document Upload,
Approval Tracking, Claim Submission, Query Resolution, Final Settlement, Rejection Management),
which is its own separate module in the fuller All Modules.pdf and out of the Basic HMS.pdf
scope this build is targeting. `BillingClaim` here is the same lightweight
"a third party is footing some/all of this bill" record for both Insurance and Corporate
payers, since Basic HMS.pdf treats them as two bullets under one Billing Workflow, not two
separate systems.

Requires a new `Billing` role (a cashier/billing-desk user) -- same free-text-role pattern as
`Pharmacist` before it, added via `auth.is_billing_staff`. Discount and Refund actions are
Admin-only: applying a discount or approving a refund is an approval action in its own right in
this design (no separate request/approve two-step, unlike Inventory's Purchase Request --
simpler here since there's no "requester" distinct from "the person doing the billing"),
inferred from the source doc naming "Discount Approval" as its own capability, not stated in
more detail than that.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_admin, is_billing_staff, log_audit
from ..database import get_db
from ..models import (
    BillingClaim, BillingPackage, Consultation, DispensingRecord, Invoice, InvoiceLine, Patient,
    Payment, PreAuthorizationRequest, Refund, Tariff,
)

router = APIRouter(prefix="/api/billing", tags=["billing"])


def _require_billing_staff(user: dict) -> None:
    if not (is_admin(user) or is_billing_staff(user)):
        raise HTTPException(403, "Only Admin or Billing staff can access the billing module")


def _require_admin(user: dict) -> None:
    if not is_admin(user):
        raise HTTPException(403, "Only Admin can approve a discount or refund")


def _get_org_invoice(db: Session, invoice_id: int, org_id: int) -> Invoice:
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.organization_id == org_id).first()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    return invoice


def _get_org_patient(db: Session, patient_id: int, org_id: int) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id, Patient.organization_id == org_id).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    return patient


def _recompute_totals(invoice: Invoice) -> None:
    """Subtotal is always the live sum of line totals; total_amount applies the invoice's own
    discount/tax on top. Called after every line/discount change, before the invoice is
    finalized -- once Finalized, lines are immutable (see finalize_invoice), so this only ever
    runs against a Draft invoice."""
    invoice.subtotal = round(sum(l.line_total for l in invoice.lines), 2)
    invoice.total_amount = round(invoice.subtotal - invoice.discount_amount + invoice.tax_amount, 2)


def _line_out(l: InvoiceLine) -> dict:
    return {
        "id": l.id, "description": l.description, "tariff_id": l.tariff_id, "package_id": l.package_id,
        "quantity": l.quantity, "unit_price": l.unit_price, "line_total": l.line_total,
        "source_type": l.source_type, "source_id": l.source_id,
    }


def _invoice_out(invoice: Invoice) -> dict:
    return {
        "id": invoice.id, "patient_id": invoice.patient_id, "consultation_id": invoice.consultation_id,
        "status": invoice.status, "payer_type": invoice.payer_type,
        "subtotal": invoice.subtotal, "discount_amount": invoice.discount_amount,
        "discount_reason": invoice.discount_reason, "tax_amount": invoice.tax_amount,
        "total_amount": invoice.total_amount, "amount_paid": invoice.amount_paid,
        "amount_refunded": invoice.amount_refunded,
        "balance_due": round(invoice.total_amount - invoice.amount_paid, 2),
        "created_at": invoice.created_at.isoformat(),
        "finalized_at": invoice.finalized_at.isoformat() if invoice.finalized_at else None,
        "lines": [_line_out(l) for l in invoice.lines],
    }


class InvoiceCreate(BaseModel):
    patient_id: int
    consultation_id: Optional[int] = None
    payer_type: str = Field(default="Self")  # Self | Insurance | Corporate


class ManualLineCreate(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: int = Field(default=1, gt=0)
    unit_price: Decimal = Field(ge=0)


class TariffLineCreate(BaseModel):
    tariff_id: int
    quantity: int = Field(default=1, gt=0)


class PackageLineCreate(BaseModel):
    package_id: int


class PharmacyCaptureRequest(BaseModel):
    dispensing_record_ids: list[int] = Field(min_length=1)


class BedDayCaptureRequest(BaseModel):
    tariff_id: int
    days: int = Field(gt=0)


class DiscountApply(BaseModel):
    amount: Decimal = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class PaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    method: str = Field(min_length=1, max_length=30)  # Cash | Card | UPI | BankTransfer | Insurance
    reference_number: Optional[str] = None


class RefundCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)
    payment_id: Optional[int] = None


class TariffCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)  # Consultation | BedDay | Procedure | Lab | Other
    unit_price: Decimal = Field(ge=0)


class PackageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    price: Decimal = Field(ge=0)


class ClaimCreate(BaseModel):
    insurer_or_corporate_name: str = Field(min_length=1, max_length=200)
    policy_or_account_number: Optional[str] = None
    claim_amount: Decimal = Field(ge=0)
    # Links this claim to the TPA pre-authorization that cleared it (Insurance payer_type only
    # -- see PreAuthorizationRequest/BillingClaim.pre_authorization_id in models.py). Optional:
    # a claim can still be filed without one, matching this codebase's existing "pre-auth isn't
    # a hard prerequisite" stance, but when supplied it must actually be Approved.
    pre_authorization_id: Optional[int] = None


class ClaimStatusUpdate(BaseModel):
    status: str  # Submitted | Approved | Rejected | Settled


# ---------------------------------------------------------------------------
# Tariffs and Packages (Tariff Application / Package Billing)
# ---------------------------------------------------------------------------

@router.post("/tariffs", status_code=201)
def create_tariff(payload: TariffCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    existing = db.query(Tariff).filter(Tariff.organization_id == org_id, Tariff.code == payload.code).first()
    if existing:
        raise HTTPException(400, "A tariff with this code already exists")
    tariff = Tariff(organization_id=org_id, created_by=current_user["id"], is_active=True, **payload.model_dump())
    db.add(tariff)
    db.commit()
    db.refresh(tariff)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_tariff", f"tariffs/{tariff.id}", "Success")
    return {"id": tariff.id, "code": tariff.code, "name": tariff.name, "category": tariff.category,
            "unit_price": tariff.unit_price, "is_active": tariff.is_active}


@router.get("/tariffs")
def list_tariffs(
    category: Optional[str] = None, active_only: bool = True,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    q = db.query(Tariff).filter(Tariff.organization_id == org_id)
    if category:
        q = q.filter(Tariff.category == category)
    if active_only:
        q = q.filter(Tariff.is_active == True)  # noqa: E712
    return [{"id": t.id, "code": t.code, "name": t.name, "category": t.category,
             "unit_price": t.unit_price, "is_active": t.is_active} for t in q.order_by(Tariff.name).all()]


@router.post("/packages", status_code=201)
def create_package(payload: PackageCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    pkg = BillingPackage(organization_id=org_id, created_by=current_user["id"], is_active=True, **payload.model_dump())
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_package", f"billing_packages/{pkg.id}", "Success")
    return {"id": pkg.id, "name": pkg.name, "description": pkg.description, "price": pkg.price, "is_active": pkg.is_active}


@router.get("/packages")
def list_packages(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    pkgs = db.query(BillingPackage).filter(BillingPackage.organization_id == org_id, BillingPackage.is_active == True).all()  # noqa: E712
    return [{"id": p.id, "name": p.name, "description": p.description, "price": p.price} for p in pkgs]


# ---------------------------------------------------------------------------
# Invoice: creation and line capture (Service Capture)
# ---------------------------------------------------------------------------

@router.post("/invoices", status_code=201)
def create_invoice(payload: InvoiceCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    _get_org_patient(db, payload.patient_id, org_id)
    if payload.consultation_id is not None:
        consultation = db.query(Consultation).filter(
            Consultation.id == payload.consultation_id, Consultation.organization_id == org_id
        ).first()
        if not consultation:
            raise HTTPException(404, "Consultation not found")
    if payload.payer_type not in ("Self", "Insurance", "Corporate"):
        raise HTTPException(400, "payer_type must be Self, Insurance, or Corporate")

    invoice = Invoice(
        organization_id=org_id, patient_id=payload.patient_id, consultation_id=payload.consultation_id,
        payer_type=payload.payer_type, status="Draft", subtotal=0, discount_amount=0, tax_amount=0,
        total_amount=0, amount_paid=0, amount_refunded=0, created_by=current_user["id"],
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_invoice", f"invoices/{invoice.id}", "Success")
    return _invoice_out(invoice)


@router.get("/invoices")
def list_invoices(
    patient_id: Optional[int] = None, status: Optional[str] = None,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    q = db.query(Invoice).filter(Invoice.organization_id == org_id)
    if patient_id is not None:
        q = q.filter(Invoice.patient_id == patient_id)
    if status:
        q = q.filter(Invoice.status == status)
    return [_invoice_out(i) for i in q.order_by(Invoice.created_at.desc()).all()]


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    invoice = _get_org_invoice(db, invoice_id, current_user.get("organization_id"))
    return _invoice_out(invoice)


def _require_draft(invoice: Invoice) -> None:
    if invoice.status != "Draft":
        raise HTTPException(400, f"Invoice is {invoice.status} -- lines can only be added to a Draft invoice")


@router.post("/invoices/{invoice_id}/lines/manual", status_code=201)
def add_manual_line(invoice_id: int, payload: ManualLineCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    invoice = _get_org_invoice(db, invoice_id, org_id)
    _require_draft(invoice)
    line = InvoiceLine(
        invoice_id=invoice.id, description=payload.description, quantity=payload.quantity,
        unit_price=payload.unit_price, line_total=round(payload.quantity * payload.unit_price, 2),
        source_type="Manual",
    )
    db.add(line)
    db.flush()
    _recompute_totals(invoice)
    db.commit()
    return _invoice_out(invoice)


@router.post("/invoices/{invoice_id}/lines/tariff", status_code=201)
def add_tariff_line(invoice_id: int, payload: TariffLineCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    invoice = _get_org_invoice(db, invoice_id, org_id)
    _require_draft(invoice)
    tariff = db.query(Tariff).filter(Tariff.id == payload.tariff_id, Tariff.organization_id == org_id).first()
    if not tariff:
        raise HTTPException(404, "Tariff not found")
    line = InvoiceLine(
        invoice_id=invoice.id, description=tariff.name, tariff_id=tariff.id, quantity=payload.quantity,
        unit_price=tariff.unit_price, line_total=round(payload.quantity * tariff.unit_price, 2),
        source_type="Tariff", source_id=tariff.id,
    )
    db.add(line)
    db.flush()
    _recompute_totals(invoice)
    db.commit()
    return _invoice_out(invoice)


@router.post("/invoices/{invoice_id}/lines/package", status_code=201)
def add_package_line(invoice_id: int, payload: PackageLineCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    invoice = _get_org_invoice(db, invoice_id, org_id)
    _require_draft(invoice)
    pkg = db.query(BillingPackage).filter(BillingPackage.id == payload.package_id, BillingPackage.organization_id == org_id).first()
    if not pkg:
        raise HTTPException(404, "Package not found")
    line = InvoiceLine(
        invoice_id=invoice.id, description=pkg.name, package_id=pkg.id, quantity=1,
        unit_price=pkg.price, line_total=pkg.price, source_type="Package", source_id=pkg.id,
    )
    db.add(line)
    db.flush()
    _recompute_totals(invoice)
    db.commit()
    return _invoice_out(invoice)


@router.post("/invoices/{invoice_id}/capture-pharmacy", status_code=201)
def capture_pharmacy_charges(invoice_id: int, payload: PharmacyCaptureRequest, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Pulls already-dispensed drug charges onto this invoice at their snapshotted
    DispensingRecord.total_amount -- never re-priced. A dispensing record can only ever be
    captured onto one invoice; already-billed records are rejected by name so a caller finds
    out exactly which id double-billed, rather than a silent skip."""
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    invoice = _get_org_invoice(db, invoice_id, org_id)
    _require_draft(invoice)

    already_billed = {
        row.source_id for row in db.query(InvoiceLine.source_id)
        .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
        .filter(Invoice.organization_id == org_id, InvoiceLine.source_type == "Pharmacy",
                 InvoiceLine.source_id.in_(payload.dispensing_record_ids)).all()
    }
    if already_billed:
        raise HTTPException(400, f"Dispensing record(s) already billed on another invoice: {sorted(already_billed)}")

    records = db.query(DispensingRecord).filter(
        DispensingRecord.id.in_(payload.dispensing_record_ids), DispensingRecord.organization_id == org_id
    ).all()
    if len(records) != len(set(payload.dispensing_record_ids)):
        raise HTTPException(404, "One or more dispensing records not found")

    for record in records:
        db.add(InvoiceLine(
            invoice_id=invoice.id, description=f"Pharmacy dispense #{record.id}", quantity=record.quantity,
            unit_price=record.unit_price_at_dispense, line_total=record.total_amount,
            source_type="Pharmacy", source_id=record.id,
        ))
    db.flush()
    _recompute_totals(invoice)
    db.commit()
    return _invoice_out(invoice)


@router.post("/invoices/{invoice_id}/capture-bed-days", status_code=201)
def capture_bed_day_charges(invoice_id: int, payload: BedDayCaptureRequest, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    invoice = _get_org_invoice(db, invoice_id, org_id)
    _require_draft(invoice)
    tariff = db.query(Tariff).filter(
        Tariff.id == payload.tariff_id, Tariff.organization_id == org_id, Tariff.category == "BedDay"
    ).first()
    if not tariff:
        raise HTTPException(404, "BedDay tariff not found")
    line = InvoiceLine(
        invoice_id=invoice.id, description=f"{tariff.name} ({payload.days} day(s))", tariff_id=tariff.id,
        quantity=payload.days, unit_price=tariff.unit_price, line_total=round(payload.days * tariff.unit_price, 2),
        source_type="BedDay", source_id=tariff.id,
    )
    db.add(line)
    db.flush()
    _recompute_totals(invoice)
    db.commit()
    return _invoice_out(invoice)


# ---------------------------------------------------------------------------
# Discount (Admin-only approval), Finalize (Invoice Generation)
# ---------------------------------------------------------------------------

@router.post("/invoices/{invoice_id}/discount")
def apply_discount(invoice_id: int, payload: DiscountApply, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    org_id = current_user.get("organization_id")
    invoice = _get_org_invoice(db, invoice_id, org_id)
    _require_draft(invoice)
    if payload.amount > invoice.subtotal:
        raise HTTPException(400, f"Discount ({payload.amount}) cannot exceed subtotal ({invoice.subtotal})")
    invoice.discount_amount = payload.amount
    invoice.discount_reason = payload.reason
    invoice.discount_approved_by = current_user["id"]
    _recompute_totals(invoice)
    db.commit()
    log_audit(db, current_user["id"], current_user["email"], org_id, "apply_discount", f"invoices/{invoice_id}",
               "Success", f"amount={payload.amount}")
    return _invoice_out(invoice)


@router.post("/invoices/{invoice_id}/finalize")
def finalize_invoice(invoice_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    invoice = _get_org_invoice(db, invoice_id, org_id)
    _require_draft(invoice)
    if not invoice.lines:
        raise HTTPException(400, "Cannot finalize an invoice with no line items")
    invoice.status = "Finalized"
    invoice.finalized_at = datetime.utcnow()
    db.commit()
    log_audit(db, current_user["id"], current_user["email"], org_id, "finalize_invoice", f"invoices/{invoice_id}", "Success")
    return _invoice_out(invoice)


# ---------------------------------------------------------------------------
# Payment Collection
# ---------------------------------------------------------------------------

@router.post("/invoices/{invoice_id}/payments", status_code=201)
def collect_payment(invoice_id: int, payload: PaymentCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    invoice = _get_org_invoice(db, invoice_id, org_id)
    if invoice.status not in ("Finalized", "PartiallyPaid"):
        raise HTTPException(400, f"Cannot collect payment on an invoice that is {invoice.status} -- finalize it first")
    balance_due = round(invoice.total_amount - invoice.amount_paid, 2)
    if payload.amount > balance_due:
        raise HTTPException(400, f"Payment ({payload.amount}) exceeds balance due ({balance_due})")

    payment = Payment(
        invoice_id=invoice.id, organization_id=org_id, amount=payload.amount, method=payload.method,
        reference_number=payload.reference_number, collected_by=current_user["id"],
    )
    db.add(payment)
    invoice.amount_paid = round(invoice.amount_paid + payload.amount, 2)
    invoice.status = "Paid" if invoice.amount_paid >= invoice.total_amount else "PartiallyPaid"
    db.commit()
    db.refresh(payment)
    log_audit(db, current_user["id"], current_user["email"], org_id, "collect_payment", f"invoices/{invoice_id}",
               "Success", f"amount={payload.amount} method={payload.method}")
    return {
        "payment_id": payment.id, "amount": payment.amount, "method": payment.method,
        "collected_at": payment.collected_at.isoformat(), "invoice": _invoice_out(invoice),
    }


@router.get("/invoices/{invoice_id}/payments")
def list_payments(invoice_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    _get_org_invoice(db, invoice_id, org_id)
    payments = db.query(Payment).filter(Payment.invoice_id == invoice_id, Payment.organization_id == org_id).all()
    return [{"id": p.id, "amount": p.amount, "method": p.method, "reference_number": p.reference_number,
             "collected_by": p.collected_by, "collected_at": p.collected_at.isoformat()} for p in payments]


# ---------------------------------------------------------------------------
# Refund Management (Admin-only approval)
# ---------------------------------------------------------------------------

@router.post("/invoices/{invoice_id}/refunds", status_code=201)
def create_refund(invoice_id: int, payload: RefundCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    org_id = current_user.get("organization_id")
    invoice = _get_org_invoice(db, invoice_id, org_id)
    refundable = round(invoice.amount_paid - invoice.amount_refunded, 2)
    if payload.amount > refundable:
        raise HTTPException(400, f"Refund ({payload.amount}) exceeds refundable balance ({refundable})")
    if payload.payment_id is not None:
        payment = db.query(Payment).filter(Payment.id == payload.payment_id, Payment.invoice_id == invoice.id).first()
        if not payment:
            raise HTTPException(404, "Payment not found on this invoice")

    refund = Refund(
        invoice_id=invoice.id, organization_id=org_id, payment_id=payload.payment_id, amount=payload.amount,
        reason=payload.reason, approved_by=current_user["id"], status="Completed",
    )
    db.add(refund)
    invoice.amount_refunded = round(invoice.amount_refunded + payload.amount, 2)
    if invoice.amount_refunded >= invoice.amount_paid:
        invoice.status = "Refunded"
    db.commit()
    db.refresh(refund)
    log_audit(db, current_user["id"], current_user["email"], org_id, "refund", f"invoices/{invoice_id}",
               "Success", f"amount={payload.amount}")
    return {
        "refund_id": refund.id, "amount": refund.amount, "reason": refund.reason,
        "refunded_at": refund.refunded_at.isoformat(), "invoice": _invoice_out(invoice),
    }


@router.get("/invoices/{invoice_id}/refunds")
def list_refunds(invoice_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    _get_org_invoice(db, invoice_id, org_id)
    refunds = db.query(Refund).filter(Refund.invoice_id == invoice_id, Refund.organization_id == org_id).all()
    return [{"id": r.id, "amount": r.amount, "reason": r.reason, "payment_id": r.payment_id,
             "approved_by": r.approved_by, "refunded_at": r.refunded_at.isoformat()} for r in refunds]


# ---------------------------------------------------------------------------
# Insurance / Corporate claim (Insurance Billing, Corporate Billing)
# ---------------------------------------------------------------------------

@router.post("/invoices/{invoice_id}/claim", status_code=201)
def create_claim(invoice_id: int, payload: ClaimCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    invoice = _get_org_invoice(db, invoice_id, org_id)
    if invoice.payer_type not in ("Insurance", "Corporate"):
        raise HTTPException(400, "Invoice payer_type must be Insurance or Corporate to attach a claim")
    existing = db.query(BillingClaim).filter(BillingClaim.invoice_id == invoice.id).first()
    if existing:
        raise HTTPException(400, "This invoice already has a claim")

    if payload.pre_authorization_id is not None:
        if invoice.payer_type != "Insurance":
            raise HTTPException(400, "Pre-authorization can only be attached to an Insurance claim, not Corporate")
        pre_auth = db.query(PreAuthorizationRequest).filter(
            PreAuthorizationRequest.id == payload.pre_authorization_id,
            PreAuthorizationRequest.organization_id == org_id,
            PreAuthorizationRequest.patient_id == invoice.patient_id,
        ).first()
        if not pre_auth:
            raise HTTPException(404, "Pre-authorization request not found for this patient")
        if pre_auth.status != "Approved":
            raise HTTPException(400, f"Pre-authorization is {pre_auth.status}, not Approved -- cannot attach to a claim")

    claim = BillingClaim(
        invoice_id=invoice.id, organization_id=org_id, payer_type=invoice.payer_type,
        insurer_or_corporate_name=payload.insurer_or_corporate_name,
        policy_or_account_number=payload.policy_or_account_number, claim_amount=payload.claim_amount,
        status="Submitted", pre_authorization_id=payload.pre_authorization_id, created_by=current_user["id"],
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    log_audit(db, current_user["id"], current_user["email"], org_id, "create_claim", f"billing_claims/{claim.id}", "Success")
    return _claim_out(claim)


def _claim_out(c: BillingClaim) -> dict:
    return {
        "id": c.id, "invoice_id": c.invoice_id, "payer_type": c.payer_type,
        "insurer_or_corporate_name": c.insurer_or_corporate_name,
        "policy_or_account_number": c.policy_or_account_number, "claim_amount": c.claim_amount,
        "status": c.status, "pre_authorization_id": c.pre_authorization_id,
        "created_at": c.created_at.isoformat(),
        "settled_at": c.settled_at.isoformat() if c.settled_at else None,
    }


@router.patch("/claims/{claim_id}/status")
def update_claim_status(claim_id: int, payload: ClaimStatusUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    claim = db.query(BillingClaim).filter(BillingClaim.id == claim_id, BillingClaim.organization_id == org_id).first()
    if not claim:
        raise HTTPException(404, "Claim not found")
    if payload.status not in ("Submitted", "Approved", "Rejected", "Settled"):
        raise HTTPException(400, "status must be Submitted, Approved, Rejected, or Settled")
    claim.status = payload.status
    if payload.status == "Settled":
        claim.settled_at = datetime.utcnow()
    db.commit()
    log_audit(db, current_user["id"], current_user["email"], org_id, "update_claim_status", f"billing_claims/{claim_id}",
               "Success", f"status={payload.status}")
    return _claim_out(claim)


@router.get("/invoices/{invoice_id}/claim")
def get_claim(invoice_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_billing_staff(current_user)
    org_id = current_user.get("organization_id")
    _get_org_invoice(db, invoice_id, org_id)
    claim = db.query(BillingClaim).filter(BillingClaim.invoice_id == invoice_id, BillingClaim.organization_id == org_id).first()
    if not claim:
        raise HTTPException(404, "No claim on this invoice")
    return _claim_out(claim)
