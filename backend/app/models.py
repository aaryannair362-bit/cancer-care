from sqlalchemy import Column, Integer, String, DateTime, Date, Boolean, Text, ForeignKey, Float, Numeric, JSON, UniqueConstraint, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    device_limit = Column(Integer, default=5)
    created_at = Column(DateTime, default=datetime.utcnow)
    users = relationship("User", back_populates="organization")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(200), unique=True, nullable=False)
    phone = Column(String(20))
    password_hash = Column(String(200))
    role = Column(String(50), default="User")
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    status = Column(String(20), default="Active")
    must_change_password = Column(Boolean, default=False)
    password_changed_at = Column(DateTime)
    trial_expires_at = Column(DateTime)
    failed_login_attempts = Column(Integer, default=0)
    lock_until = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime)
    browser = Column(String(100))
    device = Column(String(100))
    ip = Column(String(50))
    organization = relationship("Organization", back_populates="users")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    email = Column(String(200), nullable=False)
    ip_address = Column(String(50))
    browser = Column(String(100))
    device = Column(String(100))
    action = Column(String(100), nullable=False)
    resource = Column(String(200))
    result = Column(String(20), nullable=False)
    details = Column(Text)

class PasswordHistory(Base):
    __tablename__ = "password_histories"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    password_hash = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Consultation(Base):
    __tablename__ = "consultations"
    id = Column(Integer, primary_key=True)
    case_id = Column(String(50), unique=True)
    patient_name = Column(String(200))
    patient_age = Column(String(20))
    patient_gender = Column(String(20))
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    chief_complaint = Column(Text)
    hpi = Column(Text)
    # SOAP Notes (Clinical Workflows): chief_complaint/hpi together are the Subjective section;
    # primary_diagnosis/differential_diagnosis are Assessment; medications/advice/lab_tests are
    # Plan. objective_findings is the one section that had no field at all before this column --
    # a doctor's own physical-exam findings, distinct from what the patient reports. Added as its
    # own column rather than folding exam findings into hpi, since Subjective (what the patient
    # says) and Objective (what the doctor observes/measures) are clinically distinct categories,
    # and NursingNote already uses this same four-part split (main.py's create_nursing_note).
    objective_findings = Column(Text, nullable=True)
    primary_diagnosis = Column(Text)
    differential_diagnosis = Column(Text)
    medications = Column(JSON)
    lab_tests = Column(JSON)
    advice = Column(Text)
    raw_transcript = Column(Text)
    gemini_latency = Column(Float)
    drug_match_time = Column(Float)
    total_tokens = Column(Integer)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    visit_type = Column(String(20), default="OPD")
    admission_day = Column(Integer, nullable=True)
    interaction_warnings = Column(JSON, nullable=True)
    # Clinical Decision Support: same shape/lifecycle as interaction_warnings (computed and
    # persisted at finalize time, re-run on every re-finalize), but checking the patient's
    # Patient.allergies list against this consultation's medications rather than checking
    # medications against each other. A separate column, not folded into interaction_warnings,
    # since an allergy conflict and a drug-drug interaction are different clinical concerns with
    # different downstream handling (e.g. an allergy warning should block dispensing more firmly
    # than an interaction warning) even though today both are advisory-only.
    allergy_warnings = Column(JSON, nullable=True)
    finalized_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Follow-up Consultation (Clinical Workflows): self-referencing, nullable -- a consultation
    # explicitly marked as following up on an earlier one, distinct from just sharing the same
    # patient_id (which every OPD walk-in already did with no formal relationship). Set at
    # creation time via POST /api/consultations' optional follow_up_of_id field.
    follow_up_of_id = Column(Integer, ForeignKey("consultations.id"), nullable=True)

class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    age = Column(Integer)
    gender = Column(String(20))
    admission_date = Column(DateTime, default=datetime.utcnow)
    ward = Column(String(100))
    bed = Column(String(20))
    diagnosis = Column(Text)
    status = Column(String(20), default="Active")
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    # Master Patient Index fields (routers/patients.py) -- all nullable, no backfill for rows
    # that predate this: mrn is generated server-side only at standalone-registration time
    # (f"MRN-{organization_id}-{id:06d}", derived from the row's own already-unique id, so it's
    # collision-free by construction with no separate sequence to track), so a patient admitted
    # via the pre-existing IPD-admission-as-registration path simply has mrn=None until they're
    # looked up/re-registered through the new flow -- consistent with this codebase's established
    # additive-migration convention (e.g. DrugBatch.location) of leaving legacy rows alone.
    mrn = Column(String(30))
    phone = Column(String(20))
    date_of_birth = Column(Date)
    address = Column(Text)
    id_proof_type = Column(String(50))
    id_proof_number = Column(String(100))
    # Day Care Admission (Patient Administration): distinguishes a same-day-discharge stay from
    # a regular multi-day IPD admission. Deliberately just a label on the same Patient/ward/bed
    # flow rather than a parallel admission subsystem -- the source doc names this as a single
    # bullet with no further detail (unlike a real day-care/short-stay unit's own bed pool,
    # which isn't described anywhere), so a type flag on the existing admission is the honest
    # scope for what was actually asked for. Defaults to "IPD" so every pre-existing row (and
    # every caller that doesn't pass it) keeps today's behavior unchanged.
    admission_type = Column(String(20), default="IPD")
    # Clinical Decision Support: patient-reported allergens (drug/substance names, free text per
    # entry), checked against a consultation's medications at finalize time
    # (tasks_engine.check_allergy_conflicts) the same way active-medication interaction checking
    # already works. Nullable JSON list, not a dedicated Allergy table -- this is a short,
    # low-cardinality list per patient with no need for its own lifecycle/audit trail distinct
    # from the patient record itself.
    allergies = Column(JSON, nullable=True)

class PatientDocument(Base):
    """Original external record plus its verifiable OCR/extraction output.

    The source bytes are retained in the database deliberately: a clinical summary is an aid,
    never a replacement for the report that supports it.  organization_id is duplicated from
    Patient so every file read can enforce tenant isolation without trusting a client-supplied
    patient relationship.
    """
    __tablename__ = "patient_documents"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    file_content = Column(LargeBinary, nullable=False)
    document_type = Column(String(80), nullable=True)
    document_date = Column(Date, nullable=True)
    source_hospital = Column(String(200), nullable=True)
    ocr_status = Column(String(30), default="Pending", nullable=False)
    ocr_engine = Column(String(80), nullable=True)
    page_count = Column(Integer, default=1)
    extracted_text = Column(Text, nullable=True)
    page_text = Column(JSON, nullable=True)
    extracted_data = Column(JSON, nullable=True)
    ocr_error = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)

class NurseAssignment(Base):
    __tablename__ = "nurse_assignments"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    nurse_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_by = Column(Integer, ForeignKey("users.id"))
    assigned_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="Active")

class Vital(Base):
    __tablename__ = "vitals"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    nurse_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    bp_systolic = Column(Integer)
    bp_diastolic = Column(Integer)
    heart_rate = Column(Integer)
    temperature = Column(Float)
    oxygen_sat = Column(Integer)
    respiratory_rate = Column(Integer)
    notes = Column(Text)

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    nurse_id = Column(Integer, ForeignKey("users.id"))
    assigned_by = Column(Integer, ForeignKey("users.id"))
    description = Column(Text, nullable=False)
    status = Column(String(20), default="Pending")
    due_date = Column(DateTime)
    completed_at = Column(DateTime)
    notes = Column(Text)
    task_type = Column(String(20), default="General")
    source = Column(String(20), default="Manual")
    consultation_id = Column(Integer, ForeignKey("consultations.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class NursingNote(Base):
    __tablename__ = "nursing_notes"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    nurse_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    notes = Column(Text)
    voice_transcript = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class DischargeSummary(Base):
    __tablename__ = "discharge_summaries"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    generated_by = Column(Integer, ForeignKey("users.id"))
    admission_summary = Column(Text)
    hospital_course = Column(Text)
    discharge_diagnosis = Column(Text)
    medications_at_discharge = Column(JSON)
    follow_up_instructions = Column(Text)
    condition_at_discharge = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Ward(Base):
    """
    Per-organization bed capacity config, keyed by (organization_id, name) matched
    case-insensitively against the free-text Patient.ward field -- deliberately not an FK from
    Patient.ward, so existing patient rows never need a backfill/migration and admission still
    works normally for any org that hasn't configured a Ward row for a given name yet (the
    capacity check is simply skipped in that case). No separate Bed entity either -- occupancy
    is a live COUNT of Active patients in that ward, not individually tracked bed identities.
    """
    __tablename__ = "wards"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(100), nullable=False)
    bed_capacity = Column(Integer, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

class Drug(Base):
    """
    Pharmacy formulary entry -- the catalog of what THIS organization's pharmacy actually
    stocks/dispenses, each at its own price/reorder level. Deliberately separate from
    drug_matcher.py's ~249k-name reference dataset (that's a name-correction lookup applied to
    AI-extracted prescription text, not an inventory of real stock) -- a hospital pharmacy only
    ever stocks a small fraction of all Indian medicine names.
    """
    __tablename__ = "drugs"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(200), nullable=False)
    form = Column(String(50))  # Tablet/Syrup/Injection/... -- same vocabulary as drug_matcher's form words
    strength = Column(String(50))  # e.g. "500mg"
    barcode = Column(String(100))
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)
    is_controlled = Column(Boolean, default=False, nullable=False)  # NDPS-scheduled substance
    reorder_level = Column(Integer, default=0, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("organization_id", "name", "form", "strength", name="uq_drug_org_name_form_strength"),
    )
    batches = relationship("DrugBatch", back_populates="drug")

class DrugBatch(Base):
    """
    One received batch of a Drug. Tracked per-batch (not a single quantity on Drug) because
    expiry must be per-batch -- dispensing needs to consume the earliest-expiring eligible
    batch first (FEFO: first-expired-first-out) and Expiry Monitoring needs real per-batch
    dates, not one mixed-batch number. `received_quantity` is immutable (the statutory record
    of what came in); `quantity_on_hand` is mutated downward as dispensing consumes it -- kept
    separate so the controlled-drug register can reconstruct a true receipts-vs-dispensed
    ledger instead of relying on a single number that both receiving and dispensing mutate.
    """
    __tablename__ = "drug_batches"
    id = Column(Integer, primary_key=True)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    batch_number = Column(String(100))
    received_quantity = Column(Integer, nullable=False)
    quantity_on_hand = Column(Integer, nullable=False)
    expiry_date = Column(Date, nullable=False)
    received_by = Column(Integer, ForeignKey("users.id"))
    received_at = Column(DateTime, default=datetime.utcnow)
    # Added for Inventory & Stores: informational only today -- FEFO dispensing (pharmacy
    # router) deliberately still draws from a drug's total stock across all locations, not
    # scoped to one location. Making dispensing location-aware is a real future change, not
    # done here since it wasn't asked for and would touch already-tested dispensing behavior.
    location = Column(String(100), default="Main Store")
    # Set when this batch originated from a Purchase Order goods receipt (routers/inventory.py);
    # null for batches received directly via the pharmacy router's own receive-stock endpoint,
    # or created as the destination side of a stock transfer.
    purchase_order_line_id = Column(Integer, ForeignKey("purchase_order_lines.id"), nullable=True)
    drug = relationship("Drug", back_populates="batches")

class DispensingRecord(Base):
    """
    One dispensed line item. Deliberately independent of the free-text `Consultation.medications`
    JSON (the doctor's ORDER) -- this is proof of what pharmacy actually gave out, which can
    legitimately differ (partial dispensing, substitution, OTC counter sale with no consultation
    at all). `patient_id`/`consultation_id` are nullable for the same reason
    `Consultation.patient_id` is: an OTC/walk-in counter sale has no formal Patient/Consultation
    row, matching this codebase's existing OPD-walk-in convention (see main.py's scribe endpoint).
    `unit_price_at_dispense`/`total_amount` are snapshotted at dispense time since Drug.unit_price
    can change later -- a historical bill line must never silently reprice itself.
    """
    __tablename__ = "dispensing_records"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    consultation_id = Column(Integer, ForeignKey("consultations.id"), nullable=True)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("drug_batches.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price_at_dispense = Column(Numeric(12, 2), nullable=False)
    total_amount = Column(Numeric(12, 2), nullable=False)
    dispensed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    counselling_notes = Column(Text)
    refill_of_id = Column(Integer, ForeignKey("dispensing_records.id"), nullable=True)
    dispensed_at = Column(DateTime, default=datetime.utcnow)

class ControlledDrugRegisterEntry(Base):
    """
    Append-only statutory register for NDPS Act (Narcotic Drugs and Psychotropic Substances)
    scheduled substances -- every dispensing of an is_controlled Drug gets one of these, in
    addition to (never instead of) its normal DispensingRecord. No stored running-balance
    column: the ledger view (main.py get_controlled_drug_register) reconstructs balance by
    replaying DrugBatch receipts and register entries in chronological order, so it can never
    silently drift from the actual receipt/dispense history the way a mutable stored total could.
    """
    __tablename__ = "controlled_drug_register_entries"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    dispensing_record_id = Column(Integer, ForeignKey("dispensing_records.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    prescriber_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    quantity_dispensed = Column(Integer, nullable=False)
    dispensed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)

class NurseShift(Base):
    __tablename__ = "nurse_shifts"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    nurse_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    shift_date = Column(Date, nullable=False)
    shift_type = Column(String(20), nullable=False)  # Morning | Evening | Night | Off
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("nurse_id", "shift_date", name="uq_nurse_shift_date"),)

class Vendor(Base):
    """Inventory & Stores' Vendor Management -- a supplier this org can raise Purchase Orders
    against. Deactivation (is_active=False) is preferred over deletion so historical POs keep
    a valid vendor reference."""
    __tablename__ = "vendors"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(200), nullable=False)
    contact_person = Column(String(200))
    phone = Column(String(20))
    email = Column(String(200))
    gstin = Column(String(20))
    address = Column(Text)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_vendor_org_name"),)

class PurchaseRequest(Base):
    """
    Inventory & Stores' Purchase Request -- an indent for more stock of one drug, raised by
    pharmacy/stores staff. Single-drug per request (matches the source requirements doc's
    "Purchase Request" as a distinct step from "Purchase Order", which can bundle several
    approved requests -- or none, for a direct order -- from one vendor). Lifecycle:
    Pending -> Approved|Rejected (admin decision) -> Ordered (a PO references it) -> Fulfilled
    (that PO is fully received).
    """
    __tablename__ = "purchase_requests"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    requested_quantity = Column(Integer, nullable=False)
    status = Column(String(20), default="Pending", nullable=False)
    notes = Column(Text)
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    requested_at = Column(DateTime, default=datetime.utcnow)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)

class PurchaseOrder(Base):
    """
    Inventory & Stores' Purchase Order -- what actually gets sent to a Vendor, one or more
    drug lines (PurchaseOrderLine). No separate Draft state: created directly as Sent, since
    this system has no vendor-facing portal to hold a draft against -- creating a PO here *is*
    placing the order. Status moves Sent -> PartiallyReceived -> Received as goods-receipt
    lines come in (routers/inventory.py's /receive endpoint), or -> Cancelled if nothing has
    been received yet.
    """
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    purchase_request_id = Column(Integer, ForeignKey("purchase_requests.id"), nullable=True)
    status = Column(String(20), default="Sent", nullable=False)
    expected_delivery_date = Column(Date, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    lines = relationship("PurchaseOrderLine", back_populates="purchase_order")

class PurchaseOrderLine(Base):
    """One drug line on a PurchaseOrder. `quantity_received` accumulates across possibly
    multiple partial goods-receipt events -- never exceeds `quantity_ordered` (enforced by the
    /receive endpoint, not a DB constraint, matching this codebase's existing convention of
    application-level invariant checks over DB-level ones for business rules)."""
    __tablename__ = "purchase_order_lines"
    id = Column(Integer, primary_key=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    quantity_ordered = Column(Integer, nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    quantity_received = Column(Integer, default=0, nullable=False)
    purchase_order = relationship("PurchaseOrder", back_populates="lines")

class StockTransfer(Base):
    """
    Inventory & Stores' Stock Transfer -- moves quantity from one existing DrugBatch to a
    (possibly new) batch row at a different `location`. `to_batch_id` records whichever batch
    ended up holding the transferred stock (an existing same-batch-number/expiry/location row
    if one existed, otherwise a freshly created one) -- see routers/inventory.py.
    """
    __tablename__ = "stock_transfers"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    drug_id = Column(Integer, ForeignKey("drugs.id"), nullable=False)
    from_batch_id = Column(Integer, ForeignKey("drug_batches.id"), nullable=False)
    to_batch_id = Column(Integer, ForeignKey("drug_batches.id"), nullable=True)
    to_location = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    notes = Column(Text)
    transferred_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    transferred_at = Column(DateTime, default=datetime.utcnow)

class Tariff(Base):
    """Billing's price list for billable services (consultation fees, bed-day rates,
    procedures, lab tests, ...) -- distinct from Drug.unit_price, which is the pharmacy
    selling price for a formulary item, not a general service charge."""
    __tablename__ = "tariffs"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    code = Column(String(50), nullable=False)
    name = Column(String(200), nullable=False)
    category = Column(String(50), nullable=False)  # Consultation | BedDay | Procedure | Lab | Other
    unit_price = Column(Numeric(12, 2), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_tariff_org_code"),)

class BillingPackage(Base):
    """A bundled fixed-price group of services (e.g. "Maternity Package") -- billed as one
    InvoiceLine at `price`, not exploded into its component tariffs, matching how a real
    hospital package quote works (one flat number, not itemized)."""
    __tablename__ = "billing_packages"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    price = Column(Numeric(12, 2), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

class Invoice(Base):
    """
    One patient bill. Draft while lines are being captured (Service Capture); Finalized locks
    the line items (Invoice Generation) -- discount can only be applied while Draft, so a
    finalized total is a real commitment, not something that silently reprices later, matching
    DispensingRecord's "a historical bill line must never silently reprice itself" precedent.
    `amount_paid`/`amount_refunded` are running totals updated by the Payment/Refund endpoints,
    not derived from summing Payment/Refund rows at read time -- deliberately mirrors the
    running-balance style already used for `DrugBatch.quantity_on_hand`, kept consistent with
    the individual Payment/Refund rows by construction (every mutation goes through one code
    path per action), not recomputed from history the way ControlledDrugRegisterEntry's ledger
    is -- a real, intentional difference: that ledger exists specifically to be reconstructable
    for statutory audit, while an invoice balance is an operational running total.
    """
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    consultation_id = Column(Integer, ForeignKey("consultations.id"), nullable=True)
    status = Column(String(20), default="Draft", nullable=False)  # Draft|Finalized|PartiallyPaid|Paid|Refunded
    payer_type = Column(String(20), default="Self", nullable=False)  # Self|Insurance|Corporate
    subtotal = Column(Numeric(12, 2), default=0, nullable=False)
    discount_amount = Column(Numeric(12, 2), default=0, nullable=False)
    discount_reason = Column(Text)
    discount_approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    tax_amount = Column(Numeric(12, 2), default=0, nullable=False)
    total_amount = Column(Numeric(12, 2), default=0, nullable=False)
    amount_paid = Column(Numeric(12, 2), default=0, nullable=False)
    amount_refunded = Column(Numeric(12, 2), default=0, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    finalized_at = Column(DateTime, nullable=True)
    lines = relationship("InvoiceLine", back_populates="invoice")

class InvoiceLine(Base):
    """One charge on an Invoice. `source_type`/`source_id` trace where the charge came from
    (Manual|Tariff|Package|Pharmacy|BedDay) -- for Pharmacy specifically, `source_id` is a
    DispensingRecord.id, and the billing router checks no DispensingRecord is ever captured
    onto more than one invoice (a double-billing guard, not a DB constraint, since it must
    check across *all* of an org's invoices, not just this one)."""
    __tablename__ = "invoice_lines"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    description = Column(String(500), nullable=False)
    tariff_id = Column(Integer, ForeignKey("tariffs.id"), nullable=True)
    package_id = Column(Integer, ForeignKey("billing_packages.id"), nullable=True)
    quantity = Column(Integer, default=1, nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    line_total = Column(Numeric(12, 2), nullable=False)
    source_type = Column(String(20), nullable=False)  # Manual|Tariff|Package|Pharmacy|BedDay
    source_id = Column(Integer, nullable=True)
    invoice = relationship("Invoice", back_populates="lines")

class Payment(Base):
    """One payment collected against an Invoice (Payment Collection). Immutable once created --
    a correction is a Refund, never an edit to a Payment row, so the payment history stays a
    true record of what was actually collected and when."""
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String(30), nullable=False)  # Cash|Card|UPI|BankTransfer|Insurance
    reference_number = Column(String(100), nullable=True)
    collected_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    collected_at = Column(DateTime, default=datetime.utcnow)

class Refund(Base):
    """Refund Management -- Admin-approved money returned against an Invoice, optionally
    against a specific prior Payment. Applying a refund *is* the approval action in this
    design (Admin-only endpoint, no separate pending-approval step), matching how Discount
    approval works elsewhere in this same router."""
    __tablename__ = "refunds"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    reason = Column(Text, nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(20), default="Completed", nullable=False)
    refunded_at = Column(DateTime, default=datetime.utcnow)

class BillingClaim(Base):
    """
    Lightweight Insurance Billing / Corporate Billing record -- one per Invoice whose
    payer_type is Insurance or Corporate. Deliberately NOT the full multi-step Insurance/TPA
    claims pipeline (verification, pre-authorization, document upload, query resolution, ...)
    named as its own separate module in the fuller All Modules.pdf requirements doc -- that's
    out of scope for the Basic HMS.pdf 6-module build this belongs to. Named generically
    (insurer_or_corporate_name, not a separate InsurancePolicy table) since Basic HMS.pdf
    treats Insurance and Corporate billing as two bullets under one Billing Workflow.

    pre_authorization_id links this claim back to the PreAuthorizationRequest a TPA cleared for
    the same patient (Insurance payer_type only -- Corporate claims have no TPA pre-auth step).
    Nullable: a claim can still be created without one (pre-authorization isn't a hard
    prerequisite in this codebase, matching Basic HMS.pdf's Billing Workflow scope), but when
    present, routers/billing.py.create_claim requires it to actually be Approved -- this is the
    interlink point where a TPA's "everything comes up fine" surfaces on the billing side.
    """
    __tablename__ = "billing_claims"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    payer_type = Column(String(20), nullable=False)  # Insurance|Corporate
    insurer_or_corporate_name = Column(String(200), nullable=False)
    policy_or_account_number = Column(String(100), nullable=True)
    claim_amount = Column(Numeric(12, 2), nullable=False)
    status = Column(String(20), default="Submitted", nullable=False)  # Submitted|Approved|Rejected|Settled
    pre_authorization_id = Column(Integer, ForeignKey("pre_authorization_requests.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    settled_at = Column(DateTime, nullable=True)

class PreAuthorizationRequest(Base):
    """
    Insurance/TPA pre-authorization submission -- the "pre-authorization" step of the 8-step
    Insurance/TPA claims pipeline named in All Modules.pdf and explicitly out of scope for
    BillingClaim (see that class's docstring and CHANGELOG 2026-08-09 pass 3: "Deliberately not
    built: the full 8-step Insurance/TPA claims pipeline (verification, pre-authorization,
    document upload, approval tracking, query resolution, rejection management)"). A TPA user
    searches the patient index, opens a patient's full record, and submits this snapshot for
    authorization. clinical_snapshot is a point-in-time copy (not a live reference) of the
    patient's demographics/diagnosis/allergies/consultations/procedures at submission time, so a
    later edit to the patient's record can't silently change what was actually submitted for
    authorization -- same reasoning as Consultation.medications being a JSON snapshot rather than
    a live join. Verification/approval/query-resolution/rejection-management (the rest of the
    8 steps) are a future review page, not built here -- status stays "Submitted" until that
    page exists to transition it.
    """
    __tablename__ = "pre_authorization_requests"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(20), default="Submitted", nullable=False)  # Submitted|Approved|Rejected
    clinical_snapshot = Column(JSON, nullable=False)
    submitted_at = Column(DateTime, default=datetime.utcnow)

class Appointment(Base):
    """Appointment Scheduling (routers/appointments.py) -- always references a real,
    already-registered Patient (via routers/patients.py's standalone registration), unlike
    Consultation.patient_id which deliberately allows an arbitrary/unvalidated walk-in id."""
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String(20), default="Scheduled", nullable=False)  # Scheduled|CheckedIn|Completed|Cancelled|NoShow
    reason = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Follow-up Consultation (Clinical Workflows), scheduling half: nullable -- set when this
    # appointment exists specifically because a doctor asked for a follow-up visit after an
    # earlier consultation, so the scheduled visit is traceable back to what prompted it.
    follow_up_of_consultation_id = Column(Integer, ForeignKey("consultations.id"), nullable=True)

class QueueToken(Base):
    """
    Queue/Token Management (routers/appointments.py) -- one sequential counter per
    (organization_id, token_date), not per-doctor (a numbered front-desk ticket, doctor
    assignment tracked separately on the token). is_emergency queue-jumps ahead of the regular
    queue in GET /api/queue/tokens's ordering -- this system's minimal, honest implementation of
    the "Emergency (ER) Workflow" bullet (Basic HMS.pdf names it as a single bullet with no
    further detail, unlike the fuller All Modules.pdf's dedicated 10-step ED module, which is
    out of scope here).
    """
    __tablename__ = "queue_tokens"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=True)
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    token_number = Column(Integer, nullable=False)
    token_date = Column(Date, nullable=False)
    is_emergency = Column(Boolean, default=False, nullable=False)
    status = Column(String(20), default="Waiting", nullable=False)  # Waiting|InProgress|Completed|Skipped
    issued_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    issued_at = Column(DateTime, default=datetime.utcnow)
    called_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("organization_id", "token_date", "token_number", name="uq_token_org_date_number"),)

class IVFluidOrder(Base):
    """IV Fluid Management (routers/nursing_charting.py) -- one of the two Nursing Workflows
    structured-data gaps the current-state audit named specifically (previously only possible
    as free text inside NursingNote.notes)."""
    __tablename__ = "iv_fluid_orders"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    fluid_type = Column(String(200), nullable=False)
    volume_ml = Column(Integer, nullable=False)
    rate_ml_per_hr = Column(Integer, nullable=True)
    route = Column(String(100), nullable=True)
    status = Column(String(20), default="Active", nullable=False)  # Active|Completed
    started_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    stopped_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    stopped_at = Column(DateTime, nullable=True)
    notes = Column(Text)

class IntakeOutputRecord(Base):
    """Intake/Output Charting (routers/nursing_charting.py) -- the second Nursing Workflows
    structured-data gap. One row per entry (either an Intake or an Output), summed live over a
    trailing window by the list endpoint rather than a stored running balance -- same
    never-let-a-displayed-total-silently-drift reasoning as ControlledDrugRegisterEntry's
    ledger, applied here for the same reason, not copied code."""
    __tablename__ = "intake_output_records"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    entry_type = Column(String(10), nullable=False)  # Intake|Output
    category = Column(String(50), nullable=False)  # Oral|IV|NG-tube|Urine|Stool|Vomit|Drain|Other...
    volume_ml = Column(Integer, nullable=False)
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text)

class AdmissionAssessment(Base):
    """
    Admission Assessment (Nursing Workflows) -- a one-time structured intake assessment
    distinct from an ongoing NursingNote: presenting complaint, allergies/history as understood
    by nursing at intake, baseline functional status, and initial risk screening, captured once
    when a patient is admitted. At most one per admission is the expected shape (a patient
    re-admitted later gets a fresh row against their new Patient record, matching this
    codebase's existing "no cross-admission identity beyond MRN" convention), enforced at the
    router level (reject a second assessment for the same still-Active admission) rather than a
    DB constraint, consistent with this codebase's established pattern of application-level
    business-rule checks over DB-level ones (see PurchaseOrderLine's own docstring for the same
    reasoning applied elsewhere).
    """
    __tablename__ = "admission_assessments"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    presenting_complaint = Column(Text)
    known_allergies = Column(Text)
    past_medical_history = Column(Text)
    current_medications = Column(Text)
    functional_status = Column(Text)
    risk_screening_notes = Column(Text)
    assessed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    assessed_at = Column(DateTime, default=datetime.utcnow)


class PainAssessment(Base):
    """Pain Assessment (Nursing Workflows) -- a 0-10 numeric pain rating scale (the standard
    bedside pain scale), recorded serially over a stay like Vital, not a one-time intake item
    like AdmissionAssessment."""
    __tablename__ = "pain_assessments"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    pain_score = Column(Integer, nullable=False)  # 0-10
    location = Column(String(200))
    character = Column(String(200))  # e.g. sharp/dull/throbbing/burning
    notes = Column(Text)
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)


class FallRiskAssessment(Base):
    """
    Fall Risk Assessment (Nursing Workflows) -- modeled on the Morse Fall Scale, the standard
    bedside instrument (six yes/no or graded items, each contributing points; a summed score
    maps to a Low/Moderate/High risk band). risk_score/risk_level are computed and stored at
    creation time (not recomputed at read time) so a later change to the scoring bands doesn't
    silently rewrite the clinical meaning of a historical assessment -- same
    snapshot-at-creation-time reasoning as DispensingRecord's unit_price_at_dispense.
    """
    __tablename__ = "fall_risk_assessments"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    history_of_falling = Column(Boolean, default=False, nullable=False)
    secondary_diagnosis = Column(Boolean, default=False, nullable=False)
    ambulatory_aid = Column(String(20), default="None", nullable=False)  # None|Crutches-Cane-Walker|Furniture
    iv_therapy = Column(Boolean, default=False, nullable=False)
    gait = Column(String(20), default="Normal", nullable=False)  # Normal|Weak|Impaired
    mental_status = Column(String(20), default="OrientedToOwnAbility", nullable=False)  # OrientedToOwnAbility|OverestimatesForgets
    risk_score = Column(Integer, nullable=False)
    risk_level = Column(String(20), nullable=False)  # Low|Moderate|High
    notes = Column(Text)
    assessed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    assessed_at = Column(DateTime, default=datetime.utcnow)


class PressureUlcerAssessment(Base):
    """
    Pressure Ulcer Assessment (Nursing Workflows) -- modeled on the Braden Scale, the standard
    bedside instrument (six subscales, each scored 1-4 except friction/shear which is 1-3; a
    summed score maps to a risk band, lower score = higher risk). Same
    computed-and-snapshotted-at-creation reasoning as FallRiskAssessment.
    """
    __tablename__ = "pressure_ulcer_assessments"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    sensory_perception = Column(Integer, nullable=False)  # 1-4
    moisture = Column(Integer, nullable=False)  # 1-4
    activity = Column(Integer, nullable=False)  # 1-4
    mobility = Column(Integer, nullable=False)  # 1-4
    nutrition = Column(Integer, nullable=False)  # 1-4
    friction_shear = Column(Integer, nullable=False)  # 1-3
    risk_score = Column(Integer, nullable=False)
    risk_level = Column(String(20), nullable=False)  # Low|Moderate|High|Severe
    notes = Column(Text)
    assessed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    assessed_at = Column(DateTime, default=datetime.utcnow)


class MedicationAdministration(Base):
    """
    Medication Administration (MAR) -- Nursing Workflows. Distinct from Task's generic
    completed_at (which only pins down that SOME action was taken on an auto-generated
    "Administer: <drug>" task): a real per-dose administration record with a status (a nurse can
    Hold or the patient can Refuse a dose, not just complete-or-not), an explicit route, and an
    optional free-text dose -- the fields a real MAR chart needs. task_id links back to the
    auto-generated Task when one exists (optional: a medication can also be charted here with no
    corresponding Task, e.g. a PRN/as-needed dose given on nursing judgment) so the ward view can
    show one unified timeline without forcing every administration through the task pipeline.
    """
    __tablename__ = "medication_administrations"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    drug_name = Column(String(200), nullable=False)
    dose = Column(String(100))
    route = Column(String(100))
    status = Column(String(20), default="Given", nullable=False)  # Given|Missed|Refused|Held
    notes = Column(Text)
    administered_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    administered_at = Column(DateTime, default=datetime.utcnow)


class ProcedureRecord(Base):
    """
    Procedure Documentation (Clinical Workflows) -- the one capability in that module the
    current-state audit found with no dedicated model/field at all (medications and lab tests
    live as JSON list columns on Consultation; procedures get their own real table instead of a
    third JSON blob, consistent with how every other module built this session -- Pharmacy,
    Billing, Patient Administration -- uses dedicated relational models, not blobs). Linked to a
    Consultation when documented during a visit, but consultation_id is nullable for the same
    reason Consultation.patient_id itself is: a procedure can be logged standalone against a
    patient without forcing a full consultation record to exist first.
    """
    __tablename__ = "procedure_records"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    consultation_id = Column(Integer, ForeignKey("consultations.id"), nullable=True)
    procedure_name = Column(String(200), nullable=False)
    notes = Column(Text)
    performed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    performed_at = Column(DateTime, default=datetime.utcnow)


class DemoCC(Base):
    """
    Demo logins table containing pre-seeded user accounts for all clinical and
    administrative roles across AIvana OS and CCA Oncology OS.
    """
    __tablename__ = "demo_cc"
    id = Column(Integer, primary_key=True)
    role = Column(String(50), nullable=False)
    email = Column(String(200), nullable=False, unique=True)
    password = Column(String(200), nullable=False)
    portal_name = Column(String(100), nullable=True)
    target_url = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# Import CCA Oncology OS Models into Base metadata
from .models_cca import (
    CCAPatient, CCAConsent, CCAQueueEvent, CCAEncounter, CCAIntakeAssessment,
    CCADocument, ClinicalFact, CCAContradiction, CCACancerDiagnosis,
    CCABiomarkerResult, CCAOrder, CCAResult, StagingRecord, StagingEvidence,
    GuidelineContext, ClinicalBrief, MDTCase, MDTDecision, MDTParticipant,
    CCAExternalAccess, CCAExternalOpinion, CCAFinancialCase, CCACoordinationCase,
    CarePlan, CarePlanVersion, CarePlanTask, TreatmentPlan, TreatmentPlanVersion,
    TreatmentPlanCoSignature, TreatmentSession, TreatmentOrder, TreatmentEvent,
    ToxicityEvent, TreatmentClearance, ResponseAssessment, CCAJourneyEvent, DomainEvent,
    GuidelineRegistry, TreatmentPlanGuidelineLink, PatientAccount,
)

