from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Date, Boolean, Text, ForeignKey, Float, JSON, UniqueConstraint,
    LargeBinary,
)
from sqlalchemy.orm import relationship
from .models import Base

class CCAPatient(Base):
    __tablename__ = "cca_patients"
    id = Column(Integer, primary_key=True)
    hms_patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    mrn = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    dob = Column(String(20))
    age = Column(Integer)
    sex = Column(String(10))
    phone = Column(String(30))
    address = Column(Text)
    photo_url = Column(String(255))
    journey_state = Column(String(50), default="Registered")
    primary_oncologist = Column(String(200))
    attender_name = Column(String(200))
    attender_phone = Column(String(30))
    attender_relationship = Column(String(50))
    # Identity verification, captured by Front Desk during registration (frontend/frontdesk.html
    # "Identity Verification" section). Previously entered into the form and never sent to the
    # backend at all -- these columns plus the matching fields in register_cca_patient() are what
    # actually persist them.
    id_proof_type = Column(String(50))
    id_proof_number = Column(String(100))
    id_proof_name = Column(String(200))
    id_proof_dob = Column(String(20))
    id_proof_verification_status = Column(String(30))
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    demo_flag = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class CCAConsent(Base):
    __tablename__ = "cca_consents"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    consent_types = Column(JSON)  # ["treatment", "ai_assistance", "audio_recording", "data_sharing"]
    signatory = Column(String(200), nullable=False)
    signatory_reason = Column(String(255))
    document_id = Column(Integer, nullable=True)
    captured_by = Column(String(200))
    valid_from = Column(DateTime, default=datetime.utcnow)
    status = Column(String(30), default="ACTIVE")
    created_at = Column(DateTime, default=datetime.utcnow)

class PatientAccount(Base):
    """Patient self-service login identity -- deliberately NOT a role on the staff `User`
    table (models.py): patients need different fields, policies, and blast-radius than
    hospital staff, and a patient token must never be able to reach a staff endpoint or
    vice versa (see patient_auth.py's create_patient_access_token / get_current_patient,
    which use a distinct token shape+type from staff tokens, checked by a dependency
    staff endpoints never use).

    Provisioning is via a one-time activation code issued in person by a staff member with
    patient-contact responsibility (see routers/patient_portal.py's
    issue_patient_activation_code), gated behind an already-captured
    "patient_portal_access" consent -- not open self-signup, and not gated on an SMS/email
    OTP channel this codebase has no infrastructure for yet. Swapping in phone/email OTP
    later only touches activate_patient_account's verification step, not this model or the
    token/auth layer."""
    __tablename__ = "cca_patient_accounts"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False, unique=True)
    is_activated = Column(Boolean, default=False)
    activation_code_hash = Column(String(200), nullable=True)
    activation_code_expires_at = Column(DateTime, nullable=True)
    activated_at = Column(DateTime, nullable=True)
    issued_by = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CCAQueueEvent(Base):
    __tablename__ = "cca_queue_events"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    location = Column(String(100), nullable=False)
    entered_at = Column(DateTime, default=datetime.utcnow)
    exited_at = Column(DateTime, nullable=True)
    waiting_for = Column(String(100))
    status = Column(String(30), default="ACTIVE")

class CCAEncounter(Base):
    __tablename__ = "cca_encounters"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    encounter_type = Column(String(50), default="OPD_CONSULTATION")
    specialty = Column(String(100), default="Medical Oncology")
    clinician = Column(String(200))
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    template_id = Column(String(50), default="ONC_BREAST_OPD_v1")
    status = Column(String(30), default="OPEN")  # OPEN, CLOSED, CANCELLED
    note_status = Column(String(30), default="AI_DRAFT")  # TRANSCRIPT, AI_DRAFT, DOCTOR_EDITED, FINAL, AMENDED
    note_content = Column(JSON, nullable=True)
    raw_transcript = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class CCAIntakeAssessment(Base):
    __tablename__ = "cca_intake_assessments"
    id = Column(Integer, primary_key=True)
    encounter_id = Column(Integer, ForeignKey("cca_encounters.id"), nullable=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    height_cm = Column(Float)
    weight_kg = Column(Float)
    bmi = Column(Float)
    bsa = Column(Float)
    bsa_formula = Column(String(50), default="DuBois")
    bp_systolic = Column(Integer)
    bp_diastolic = Column(Integer)
    heart_rate = Column(Integer)
    temperature_c = Column(Float)
    oxygen_sat = Column(Integer)
    respiratory_rate = Column(Integer)
    ecog = Column(Integer, default=0)
    karnofsky = Column(Integer, default=100)
    pain_score = Column(Integer, default=0)
    fall_risk = Column(String(30), default="Low")
    vitals_json = Column(JSON, nullable=True)
    handoff_note = Column(Text)
    recorded_by = Column(String(200))
    status = Column(String(30), default="COMPLETED")
    created_at = Column(DateTime, default=datetime.utcnow)

class CCADocument(Base):
    __tablename__ = "cca_documents"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), default="application/pdf")
    page_count = Column(Integer, default=1)
    file_hash = Column(String(100))
    storage_path = Column(String(500))
    # Front-desk-declared type at upload time (the "Previous pathology report / Previous
    # radiology report / Insurance card / Previous case file / Other" dropdown in
    # frontend/frontdesk.html's Documents & Consent step) -- distinct from
    # classification_class below, which is the system's own auto-detected content type. The
    # dropdown selection used to be captured client-side and silently dropped (never sent in
    # the upload request, and this column didn't exist), so it was lost entirely.
    document_type = Column(String(100), nullable=True)
    classification_class = Column(String(100))  # REFERRAL, IMAGING, HISTOPATHOLOGY, PATHOLOGY, LAB, CONSULT_NOTE
    classification_confidence = Column(Float, default=0.95)
    ocr_text = Column(Text)
    # Raw uploaded bytes -- nullable because the seeded demo documents (cca_seed.py) have no
    # real file behind them, only synthetic ocr_text. A real upload (POST /api/cca/documents)
    # always populates this, matching PatientDocument's (the general HMS module) same pattern.
    file_content = Column(LargeBinary, nullable=True)
    uploaded_by = Column(String(200))
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(30), default="EXTRACTED")  # UPLOADED, OCR_COMPLETE, CLASSIFIED, EXTRACTED, VERIFIED

class CCADocumentPage(Base):
    """
    True per-page breakdown of a CCADocument -- CCADocument.ocr_text/classification_class are
    whole-document (one blob, one bucket), which can't tell a doctor "page 3 of this bundle is
    an X-ray report" vs "page 1 is the referral letter". Populated asynchronously after upload
    (see routers/cca.py's upload_document + the background task in document_pages.py) because
    getting true (not just job-batched) per-page text out of Sarvam Document AI means one OCR
    job per page for a multi-page PDF, which is too slow to do inline within the upload request.
    """
    __tablename__ = "cca_document_pages"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("cca_documents.id"), nullable=False)
    page_number = Column(Integer, nullable=False)
    text = Column(Text)
    # CASE_DETAILS, PRESCRIPTION, LAB_REPORT, SCAN_IMAGING, PATHOLOGY_REPORT, INSURANCE, OTHER,
    # UNCLASSIFIED -- see cca_engine.classify_and_extract_page for the authoritative list.
    page_type = Column(String(30))
    classification_confidence = Column(Float, default=0.0)
    # True when this page is mostly an embedded image with little/no extractable text (an X-ray
    # film, MRI/CT printout, mammogram, or other scan photograph) -- the free heuristic half of
    # the hybrid classifier, computed before any LLM call.
    is_image_heavy = Column(Boolean, default=False)
    image_content = Column(LargeBinary, nullable=True)  # only populated when is_image_heavy
    image_mime_type = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class ClinicalFact(Base):
    __tablename__ = "cca_clinical_facts"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("cca_documents.id"), nullable=True)
    parent_fact_id = Column(Integer, ForeignKey("cca_clinical_facts.id"), nullable=True)
    version_no = Column(Integer, default=1)
    fact_type = Column(String(100), nullable=False)  # see cca_engine.FACT_TYPES for the authoritative, current list
    value = Column(String(500), nullable=False)
    verbatim_span = Column(Text)
    page_number = Column(Integer, default=1)
    bounding_box = Column(JSON, nullable=True)  # {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.05}
    confidence = Column(Float, default=0.92)
    status = Column(String(30), default="PROPOSED")  # PROPOSED, VERIFIED, CORRECTED, REJECTED, SUPERSEDED
    original_value = Column(String(500), nullable=True)
    reject_reason = Column(String(255), nullable=True)
    verified_by = Column(String(200), nullable=True)
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class CCAContradiction(Base):
    __tablename__ = "cca_contradictions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    rule_id = Column(String(50), default="CTR-01")
    description = Column(Text, nullable=False)
    conflicting_fact_ids = Column(JSON)  # [fact_id_1, fact_id_2]
    status = Column(String(30), default="OPEN")  # OPEN, RESOLVED, ACCEPTED_VARIATION
    disposition = Column(String(100), nullable=True)
    disposition_note = Column(Text, nullable=True)
    dispositioned_by = Column(String(200), nullable=True)
    dispositioned_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class CCACancerDiagnosis(Base):
    __tablename__ = "cca_cancer_diagnoses"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    primary_site = Column(String(200), nullable=False)
    laterality = Column(String(50))
    histology = Column(String(200))
    icd_o_3 = Column(String(50))
    icd_10 = Column(String(50))
    grade = Column(String(50))
    diagnosed_on = Column(Date, default=datetime.utcnow)
    basis = Column(JSON)  # ["Histology of primary", "Biomarker testing", "Clinical exam"]
    evidence_ids = Column(JSON)  # [fact_id_1, fact_id_2]
    clinical_setting = Column(String(100), default="Curative Intent / Early Stage")
    status = Column(String(30), default="SUSPECTED")  # SUSPECTED, CONFIRMED, EXCLUDED, AMENDED
    confirmed_by = Column(String(200), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class CCABiomarkerResult(Base):
    __tablename__ = "cca_biomarker_results"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    marker_name = Column(String(100), nullable=False)  # ER, PR, HER2, Ki-67
    result_as_reported = Column(String(200), nullable=False)
    method = Column(String(100), default="IHC")
    platform = Column(String(100), default="Ventana Benchmark Ultra")
    specimen = Column(String(200), default="Core needle biopsy")
    adequacy = Column(String(50), default="Adequate")
    lab_name = Column(String(200))
    reported_on = Column(Date, default=datetime.utcnow)
    status = Column(String(30), default="RESULTED")  # RESULTED, PENDING, INSUFFICIENT
    confirmatory_required = Column(String(20), nullable=True)  # yes|no|pending
    created_at = Column(DateTime, default=datetime.utcnow)

class CCAOrder(Base):
    __tablename__ = "cca_orders"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    encounter_id = Column(Integer, ForeignKey("cca_encounters.id"), nullable=True)
    order_type = Column(String(50), nullable=False)  # LAB, RADIOLOGY, PATHOLOGY
    item_name = Column(String(200), nullable=False)
    item_code = Column(String(100))
    clinical_indication = Column(Text, nullable=False)
    priority = Column(String(30), default="ROUTINE")  # ROUTINE, URGENT, STAT
    staging_relevant = Column(Boolean, default=True)
    status = Column(String(30), default="RAISED")  # RAISED, SCHEDULED, IN_PROGRESS, RESULTED, ACKNOWLEDGED, CLOSED, CANCELLED
    requested_by = Column(String(200))
    ordered_at = Column(DateTime, default=datetime.utcnow)
    # Operational/logistics fields used by Radiology Coordinator (scheduling/preparation) and
    # Lab/Phlebotomy (specimen collection). Nullable/unused for order_type=PATHOLOGY, which has
    # no analogous operational workflow in the role specs. `workflow_state` is deliberately a
    # free-text detailed sub-state (each role spec defines its own vocabulary -- "Preparation
    # pending" for imaging, "Awaiting collection" for lab) layered on top of the existing coarse
    # `status` enum above, rather than forcing one rigid enum to cover every module.
    workflow_state = Column(String(50), nullable=True)
    scheduled_at = Column(DateTime, nullable=True)
    location = Column(String(100), nullable=True)  # scanner/room/lab bay
    preparation_status = Column(String(30), default="NotRequired")  # NotRequired|Pending|Completed|NeedsReview
    preparation_notes = Column(Text, nullable=True)
    collected_by = Column(String(200), nullable=True)
    collected_at = Column(DateTime, nullable=True)
    specimen_container = Column(String(100), nullable=True)
    rejection_reason = Column(String(255), nullable=True)

class CCAResult(Base):
    __tablename__ = "cca_results"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("cca_orders.id"), nullable=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    result_type = Column(String(50), nullable=False)  # IMAGING, LAB, PATHOLOGY
    title = Column(String(200), nullable=False)
    findings_text = Column(Text)
    extracted_values = Column(JSON, nullable=True)
    document_id = Column(Integer, ForeignKey("cca_documents.id"), nullable=True)
    is_critical = Column(Boolean, default=False)
    status = Column(String(30), default="NEW")  # NEW, PENDING_REVIEW, ACKNOWLEDGED, ACTIONED
    acknowledged_by = Column(String(200), nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    resulted_at = Column(DateTime, default=datetime.utcnow)
    # Structured report fields (Radiologist/Pathologist Reports screens). `structured_report`
    # holds specialty-varying sub-fields as JSON (e.g. radiology: measurements/lesion sites;
    # pathology: gross/microscopic description, histologic type/grade, margins, lymph nodes) --
    # one flexible column rather than a wide table of mostly-null specialty-specific columns,
    # matching this codebase's existing pattern for varying structured data (CarePlan.components).
    technique = Column(Text, nullable=True)
    comparison = Column(Text, nullable=True)
    impression = Column(Text, nullable=True)
    structured_report = Column(JSON, nullable=True)
    report_status = Column(String(30), default="Draft")  # Draft|Finalized|Superseded -- "No autonomous final report"
    finalized_by = Column(String(200), nullable=True)
    finalized_at = Column(DateTime, nullable=True)
    critical_acknowledged_by = Column(String(200), nullable=True)
    critical_acknowledged_at = Column(DateTime, nullable=True)
    # Amendment/immutability (Product 1 vs Product 2 gap report, Batch 6: Pathology) -- once
    # Finalized, a report is immutable; a further edit must go through the amendment path
    # instead, which creates a NEW linked row rather than mutating the finalized one, so the
    # original finalized content is never silently lost. supersedes_id/superseded_by_id point
    # in opposite directions along the same chain (old->new and new->old are both stored so
    # either end can be found without a reverse query).
    supersedes_id = Column(Integer, ForeignKey("cca_results.id"), nullable=True)
    superseded_by_id = Column(Integer, nullable=True)
    amendment_reason = Column(Text, nullable=True)
    amended_by = Column(String(200), nullable=True)
    amended_at = Column(DateTime, nullable=True)

class StagingRecord(Base):
    __tablename__ = "cca_staging_records"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    staging_system = Column(String(100), default="AJCC Cancer Staging Manual")
    system_version = Column(String(50), default="8th Edition")
    classification_prefix = Column(String(10), default="c")  # c, p, y, r
    t_stage = Column(String(20))
    n_stage = Column(String(20))
    m_stage = Column(String(20))
    stage_value = Column(String(50))
    prognostic_stage_group = Column(String(50))
    status = Column(String(50), default="EVIDENCE_INCOMPLETE")  # NOT_STARTED, EVIDENCE_INCOMPLETE, PARTIALLY_READY, READY_FOR_STAGING, CLINICIAN_CONFIRMED, REQUIRES_REVIEW, SUPERSEDED
    confirmed_by = Column(String(200), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    version_no = Column(Integer, default=1)
    previous_id = Column(Integer, nullable=True)
    change_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class StagingEvidence(Base):
    __tablename__ = "cca_staging_evidence"
    id = Column(Integer, primary_key=True)
    staging_record_id = Column(Integer, ForeignKey("cca_staging_records.id"), nullable=False)
    category = Column(String(50), nullable=False)  # T, N, M, PATHOLOGY, IMAGING, BIOMARKER
    fact_id = Column(Integer, ForeignKey("cca_clinical_facts.id"), nullable=True)
    excerpt = Column(Text, nullable=False)
    added_by = Column(String(200))
    added_at = Column(DateTime, default=datetime.utcnow)

class GuidelineContext(Base):
    __tablename__ = "cca_guideline_contexts"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    guideline_source = Column(String(100), default="NCCN Guidelines for Breast Cancer")
    version = Column(String(50), default="Version 4.2026")
    pathway_name = Column(String(200))
    variables_used = Column(JSON)
    content_slot = Column(JSON)
    readiness_state = Column(String(50), default="NOT_READY")  # NOT_READY, PARTIALLY_READY, READY
    viewed_by = Column(String(200), nullable=True)
    viewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class GuidelineRegistry(Base):
    """The canonical, current version of a named guideline pathway -- distinct from
    GuidelineContext (a per-patient readiness snapshot) and from
    TreatmentPlanGuidelineLink (what version a specific signed plan was authorized under).
    One row per (guideline_source, pathway_name). Publishing a new version here (Admin-only,
    see routers/cca.py's publish_guideline_version) flags already-signed plans that used an
    older version for clinician review -- it never rewrites them; see the architecture doc's
    non-negotiable on guideline updates."""
    __tablename__ = "cca_guideline_registry"
    id = Column(Integer, primary_key=True)
    guideline_source = Column(String(100), nullable=False)
    pathway_name = Column(String(200), nullable=True)
    current_version = Column(String(50), nullable=False)
    published_by = Column(String(200))
    published_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("guideline_source", "pathway_name", name="uq_guideline_registry_source_pathway"),)


class TreatmentPlanGuidelineLink(Base):
    """What guideline version a signed Treatment Plan was authorized under, snapshotted at
    signing time and never updated retroactively -- a later guideline version update flags
    the plan (TreatmentPlan.guideline_review_required) instead of changing this record or
    the plan's content. Created only when the patient actually has a guideline context to
    snapshot (see sign_treatment_plan) -- a plan authored without ever consulting a
    guideline pathway has no link, which is expected, not a bug."""
    __tablename__ = "cca_treatment_plan_guideline_links"
    id = Column(Integer, primary_key=True)
    treatment_plan_id = Column(Integer, ForeignKey("cca_treatment_plans.id"), nullable=False)
    guideline_source = Column(String(100), nullable=False)
    pathway_name = Column(String(200), nullable=True)
    version_at_signing = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ClinicalBrief(Base):
    __tablename__ = "cca_clinical_briefs"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    sections = Column(JSON, nullable=False)
    clinical_uncertainty = Column(String(50), default="LOW")
    uncertainty_reasons = Column(JSON, nullable=True)
    best_next_investigation = Column(JSON, nullable=True)
    status = Column(String(30), default="CURRENT")  # CURRENT, SUPERSEDED
    disposition = Column(String(100), nullable=True)
    disposition_by = Column(String(200), nullable=True)
    disposition_note = Column(Text, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)

class MDTCase(Base):
    __tablename__ = "cca_mdt_cases"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    question = Column(Text, nullable=False)
    priority = Column(String(30), default="STANDARD")  # STANDARD, EXPEDITED, URGENT
    tumor_board = Column(String(100), default="Breast Oncology Tumor Board")
    package_data = Column(JSON, nullable=True)
    status = Column(String(50), default="PROPOSED")  # PROPOSED, PREPARED, SCHEDULED, DISCUSSED, RECOMMENDED, RETURNED_TO_RECORD, ACTIONED_BY_CLINICIAN, WITHDRAWN
    requested_by = Column(String(200))
    scheduled_for = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # MDT Coordinator scheduling fields (11_MDT_Coordinator.pdf).
    referring_department = Column(String(200), nullable=True)
    referring_clinician = Column(String(200), nullable=True)
    board_date = Column(Date, nullable=True)
    start_time = Column(String(20), nullable=True)
    meeting_type = Column(String(20), default="InPerson")  # InPerson|Virtual|Hybrid
    location = Column(String(200), nullable=True)
    meeting_link = Column(String(500), nullable=True)
    agenda_position = Column(Integer, nullable=True)

class MDTParticipant(Base):
    """One invited/attending specialist on an MDTCase (11_MDT_Coordinator.pdf's Participants
    section). Deliberately not a join against the User table -- MDT participants in this
    system's demo scope are named by role (e.g. "Radiologist") rather than resolved to a real
    login account, matching how MDTDecision.attendees already stores free-text name/role pairs."""
    __tablename__ = "cca_mdt_participants"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cca_mdt_cases.id"), nullable=False)
    specialist_name = Column(String(200), nullable=False)
    specialist_role = Column(String(100), nullable=False)
    invitation_status = Column(String(20), default="NotInvited")  # NotInvited|Invited|Accepted|Declined|Pending
    attendance_status = Column(String(20), nullable=True)  # Present|Absent|JoinedRemotely
    added_at = Column(DateTime, default=datetime.utcnow)

class CCAExternalAccess(Base):
    """Case-scoped, time-bounded access grant for an External MDT Specialist
    (12_External_MDT_Specialist.pdf) -- this role must never browse the full patient
    population, only cases explicitly shared with it."""
    __tablename__ = "cca_external_access"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cca_mdt_cases.id"), nullable=False)
    specialist_name = Column(String(200), nullable=False)
    specialist_email = Column(String(200), nullable=False)
    access_status = Column(String(20), default="Invited")  # Invited|Active|Expiring|Expired|Revoked
    granted_by = Column(String(200), nullable=False)
    granted_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

class CCAExternalOpinion(Base):
    """An External MDT Specialist's submitted opinion on a shared case -- separately
    attributable from the tumor board's own MDTDecision, per spec ("External contribution
    remains separately attributable... does not independently own final MDT treatment
    decision by default")."""
    __tablename__ = "cca_external_opinions"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cca_mdt_cases.id"), nullable=False)
    specialist_name = Column(String(200), nullable=False)
    recommendation = Column(Text, nullable=False)
    rationale = Column(Text, nullable=True)
    supporting_evidence = Column(Text, nullable=True)
    concerns = Column(Text, nullable=True)
    information_required = Column(Text, nullable=True)
    certainty = Column(String(20), nullable=True)  # High|Moderate|Low
    status = Column(String(20), default="Draft")  # Draft|Submitted|Signed|Superseded
    submitted_at = Column(DateTime, nullable=True)
    signed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class CCAFinancialCase(Base):
    """Financial Counsellor / Patient Financial Services workflow
    (14_Financial_Counsellor_Patient_Financial_Services.pdf) -- deliberately separate from the
    general HMS billing module (backend/app/routers/billing.py): CCA's financial counselling is
    pre-treatment estimate/clearance workflow for a specific oncology plan, not invoicing for
    services already rendered, and the spec explicitly calls for "manual/demo estimate...no
    complex billing engine" and to "keep financial clearance separate from clinical treatment
    clearance."""
    __tablename__ = "cca_financial_cases"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    referral_date = Column(DateTime, default=datetime.utcnow)
    counselling_status = Column(String(30), default="Pending")  # Pending|InProgress|Completed|FollowUpRequired
    counselling_date = Column(DateTime, nullable=True)
    counsellor = Column(String(200), nullable=True)
    counselling_notes = Column(Text, nullable=True)
    counselling_outcome = Column(String(50), nullable=True)
    patient_decision = Column(String(50), nullable=True)  # Proceeding|DecisionPending|FinancialDifficulty|...
    estimate = Column(JSON, nullable=True)  # {components: [...], total: n}
    estimate_status = Column(String(30), default="NotStarted")  # NotStarted|Draft|Ready|Shared|Revised|Accepted
    payer_route = Column(String(50), nullable=True)  # SelfPay|PrivateInsurance|CorporateTPA|GovernmentScheme|Assistance
    insurance_status = Column(String(50), nullable=True)
    scheme_status = Column(String(50), nullable=True)
    financial_clearance_status = Column(String(30), default="NotStarted")
    next_action = Column(String(255), nullable=True)
    next_action_owner = Column(String(200), nullable=True)
    next_action_due = Column(Date, nullable=True)
    created_by = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CCACoordinationCase(Base):
    """Patient Liaison / Care Coordinator workflow (13_Patient_Liaison_Care_Coordinator.pdf) --
    contact/appointment-navigation and drop-off-risk tracking. Care Milestones (Registration
    completed, Nurse intake completed, ...) are deliberately NOT stored here -- they're computed
    live from existing state (CCAPatient.journey_state, CCAIntakeAssessment, MDTCase, CarePlan,
    CCAFinancialCase, ...) the same way staging/guideline readiness already is, per the spec's
    "do not recreate a separate Patient Journey module" instruction."""
    __tablename__ = "cca_coordination_cases"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    communication_status = Column(String(30), default="NotContacted")  # NotContacted|ContactAttempted|Reached|UnableToReach|CallbackRequired
    preferred_contact_method = Column(String(30), nullable=True)
    last_contact_at = Column(DateTime, nullable=True)
    barriers = Column(JSON, nullable=True)  # [{type, notes, status, owner}]
    next_action = Column(String(255), nullable=True)
    next_action_owner = Column(String(200), nullable=True)
    next_action_due = Column(Date, nullable=True)
    next_action_status = Column(String(30), default="Pending")  # Pending|InProgress|Completed|Overdue
    created_by = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CCAAppointmentCoordination(Base):
    """Hospital-wide Appointment Coordination (Gap Analysis PDF item 23) -- the Patient
    Liaison's own record of an appointment in any department (Radiology, Surgery, Lab,
    another OPD consult, ...) they are helping the patient navigate to. Deliberately separate
    from the general HMS Appointment/doctor-queue system (models.py's Appointment) -- that
    system's patient_id targets the general `patients` table, which has no live linkage to
    CCAPatient (CCAPatient.hms_patient_id exists on the model but is not populated anywhere in
    this codebase); this stays entirely within CCA's own patient identity space, matching the
    reasoning that already keeps SurgicalBloodTransfusion (models_cca_oncology_ext.py) separate
    from Day Care's BloodProductAdministration rather than force-sharing a table across two
    unrelated parent identities."""
    __tablename__ = "cca_appointment_coordination"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    coordination_case_id = Column(Integer, ForeignKey("cca_coordination_cases.id"), nullable=True)
    department = Column(String(100), nullable=False)
    purpose = Column(Text, nullable=True)
    scheduled_at = Column(DateTime, nullable=False)
    location = Column(String(200), nullable=True)
    status = Column(String(30), default="Scheduled")  # Scheduled, Confirmed, Completed, Missed, Rescheduled, Cancelled
    transport_arranged = Column(Boolean, default=False)
    reminder_sent = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class MDTDecision(Base):
    __tablename__ = "cca_mdt_decisions"
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cca_mdt_cases.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    recommendation = Column(Text, nullable=False)
    modality_direction = Column(String(100))  # Neoadjuvant Chemotherapy vs Upfront Surgery
    rationale = Column(Text)
    outstanding_items = Column(JSON, nullable=True)
    attendees = Column(JSON, nullable=True)  # [{"name": "Dr. Aris", "role": "Surgical Oncologist"}]
    status = Column(String(30), default="FINAL")  # FINAL -> APPROVED / PARTIALLY_APPROVED / REJECTED
    # The treating clinician's explicit disposition on this recommendation (architecture doc
    # Sec 18: "Accept / partially accept / reject with reason" -- not a bare approve/deny).
    # Required whenever the disposition isn't a full ACCEPT; see approve_mdt_recommendation.
    disposition_reason = Column(Text, nullable=True)
    recorded_by = Column(String(200))
    recorded_at = Column(DateTime, default=datetime.utcnow)

class CarePlan(Base):
    __tablename__ = "cca_care_plans"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    intent = Column(String(100), default="Curative")
    goals = Column(JSON)
    components = Column(JSON)  # systemic, surgical, radiation, supportive -- a display rollup,
    # not the source of truth: the authoritative clinical strategy lives on the signed
    # TreatmentPlan row(s) referenced by source_treatment_plan_ids below. See
    # ARCHITECTURE_NOTES.md / the Care Plan & Treatment Plan architecture doc for why these
    # were previously conflated (one CarePlan row shown as "Treatment Plan" to oncologists).
    source_treatment_plan_ids = Column(JSON, nullable=True)  # [treatment_plan_id, ...]
    # Patient-facing view is gated behind explicit clinical/consent review (architecture doc:
    # "Only after clinical/consent review... never expose internal reasoning by default") --
    # see routers/cca.py's approve_patient_facing_view / get_patient_facing_summary. Any
    # material amendment (update_care_plan) resets this so a stale approval can never keep
    # showing outdated content.
    patient_facing_approved = Column(Boolean, default=False)
    patient_facing_approved_by = Column(String(200), nullable=True)
    patient_facing_approved_at = Column(DateTime, nullable=True)
    monitoring_plan = Column(JSON)
    follow_up_plan = Column(JSON)
    next_decision_point = Column(String(255))
    version_no = Column(Integer, default=1)
    # DRAFT, PROPOSED, ACTIVE, BLOCKED, ON_HOLD, COMPLETED, CANCELLED -- see
    # routers/cca.py's _CARE_PLAN_STATUS_TRANSITIONS for the allowed transition graph.
    # create_care_plan defaults new plans straight to ACTIVE; DRAFT/PROPOSED exist for the
    # (currently opt-in) MDT-recommendation-originated draft path. No SUPERSEDED here, unlike
    # TreatmentPlan: this model is amended in place (version_no bumps on the same row) rather
    # than chained across rows via a supersedes_id, so there is nothing for a status of
    # SUPERSEDED to point to yet -- adding the label without that chaining would be a fake
    # completion, not a fix.
    status = Column(String(30), default="ACTIVE")
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)

class CarePlanVersion(Base):
    __tablename__ = "cca_care_plan_versions"
    id = Column(Integer, primary_key=True)
    care_plan_id = Column(Integer, ForeignKey("cca_care_plans.id"), nullable=False)
    version_no = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    change_reason = Column(Text, nullable=False)
    changed_sections = Column(JSON, nullable=True)
    # Field-level before/after -- the architecture doc's PlanAuditEvent explicitly asks for
    # "before/after hash or equivalent audit reference", not just a full after-state snapshot
    # (which `snapshot` above already is). See routers/cca.py's _diff_dicts.
    before_after_diff = Column(JSON, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)

class CarePlanTask(Base):
    """A patient-scoped review/reassessment task. care_plan_id is optional -- a task is
    patient-scoped first and a Care Plan link is added when one is available, since real
    triggers (e.g. an MDT recommendation finalizing) routinely happen before any Care Plan
    exists yet. Previously NOT NULL, which meant event_subscribers.py's
    'treating clinician receives review task' effect for MDT_RECOMMENDATION_FINALIZED could
    not be implemented at all for a patient without an active Care Plan -- see
    migrations.py's run_constraint_migrations for how an existing Postgres deployment picks
    up this relaxation."""
    __tablename__ = "cca_care_plan_tasks"
    id = Column(Integer, primary_key=True)
    care_plan_id = Column(Integer, ForeignKey("cca_care_plans.id"), nullable=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    description = Column(Text, nullable=False)
    owner_id = Column(String(100), nullable=False)
    owner_name = Column(String(200), nullable=False)
    due_date = Column(DateTime, nullable=False)
    status = Column(String(30), default="OPEN")  # OPEN, ACKNOWLEDGED, RESOLVED, ESCALATED, BLOCKED
    # AI proposes, clinician decides (architecture doc non-negotiable): AI_SEARCH_PROPOSED
    # tasks are created only by an explicit clinician action confirming a search result (see
    # routers/cca.py's propose_task_from_search) -- never automatically. SYSTEM is this
    # codebase's own event subscribers (event_subscribers.py); MANUAL is direct clinician entry.
    source = Column(String(30), default="SYSTEM")  # SYSTEM, AI_SEARCH_PROPOSED, MANUAL
    # A patient-safe rewrite of `description`, set only by explicit clinician action (see
    # routers/cca.py's set_task_patient_visible_note). NULL by default -- a task is excluded
    # from the patient-facing summary unless a clinician has deliberately authored
    # patient-appropriate wording for it, never by exposing the raw internal `description`
    # (which routinely names clinical detail like toxicity grade or an MDT case number).
    patient_visible_note = Column(Text, nullable=True)
    # owner_role is a coarse ownership *category* (TREATING_ONCOLOGIST, CARE_COORDINATION,
    # NURSING, MDT_COORDINATION), not one of the 15 literal CCA role strings -- several roles
    # can share a category (e.g. all three oncologist personas are TREATING_ONCOLOGIST), and
    # this is what a role-scoped "my tasks" view filters on (see list_patient_tasks's
    # `owner_role` query param). owner_id/owner_name above remain the specific person, when
    # known -- this is the class of person, always known at creation time.
    owner_role = Column(String(30), nullable=True)
    category = Column(String(30), nullable=True)  # CLINICAL_REVIEW, MDT_REVIEW, COORDINATION, ADMINISTRATIVE
    # CarePlanItem-style linkage (architecture doc data model, §27): what this task is
    # blocked on. Purely additive/optional -- most tasks created so far (MDT review,
    # treatment-hold reassessment, no-show recovery) have no natural upstream order/result to
    # link and leave these null.
    dependency_ids = Column(JSON, nullable=True)  # [care_plan_task_id, ...]
    linked_order_id = Column(Integer, ForeignKey("cca_orders.id"), nullable=True)
    linked_result_id = Column(Integer, ForeignKey("cca_results.id"), nullable=True)
    blocker_reason = Column(Text, nullable=True)  # required when status is set to BLOCKED
    created_at = Column(DateTime, default=datetime.utcnow)

class TreatmentPlan(Base):
    """The clinician-owned cancer treatment strategy -- distinct from CarePlan (the
    multidisciplinary execution layer). A TreatmentPlan is drafted, then signed by an
    authorized clinician of the matching modality (see auth.py's is_cca_medical_oncologist /
    is_cca_surgical_oncologist / is_cca_radiation_oncologist and
    routers/cca.py's _require_modality_signer), which is what moves it to ACTIVE. A CarePlan
    is only ever created by referencing an already-ACTIVE (signed) TreatmentPlan -- see
    CarePlan.source_treatment_plan_ids -- never the other way around.
    care_plan_id is a convenience back-reference, set once a CarePlan is generated from this
    plan; it is not the ownership direction."""
    __tablename__ = "cca_treatment_plans"
    id = Column(Integer, primary_key=True)
    care_plan_id = Column(Integer, ForeignKey("cca_care_plans.id"), nullable=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    mdt_decision_id = Column(Integer, ForeignKey("cca_mdt_decisions.id"), nullable=True)
    # Explicit, doctor-set choice at draft time: does this case go through MDT/Tumour Board
    # review before authorization, or does the treating clinician develop and sign it directly?
    # When True, sign_treatment_plan additionally requires mdt_decision_id to reference an
    # APPROVED/PARTIALLY_APPROVED MDTDecision -- see POST /treatment-plans/{id}/link-mdt-decision.
    # When False (default), signing behaves exactly as it always has: the treating clinician's
    # own signature is the sole authorization step, no MDT involvement required.
    requires_mdt = Column(Boolean, default=False)
    intent = Column(String(100), default="Curative")
    modality = Column(String(100), default="Systemic Chemotherapy")
    protocol_name = Column(String(200), default="AC-T (Doxorubicin/Cyclophosphamide followed by Paclitaxel)")
    # Links to the controlled Regimen library (models_cca_oncology_ext.py's Regimen, PDF item 6)
    # -- optional convenience prefill for protocol_name/drug lines, never a hard lock; a plan
    # can still be drafted with protocol_name alone (Product 1 gap report item 9: "Regimen
    # selection"). See TreatmentOrderDrugLine for how this seeds an order's dosing panel.
    regimen_id = Column(Integer, ForeignKey("cca_regimens.id"), nullable=True)
    planned_sessions = Column(Integer, default=8)
    completed_sessions = Column(Integer, default=0)
    start_date = Column(Date, default=datetime.utcnow)
    version_no = Column(Integer, default=1)
    # DRAFT -> PROPOSED -> ACTIVE (signed) -> ON_HOLD / COMPLETED / SUPERSEDED / CANCELLED.
    # Previously this defaulted straight to "ACTIVE" at creation with no signature step at
    # all -- see the architecture doc's non-negotiable "only authorized clinicians sign an
    # active Treatment Plan" rule.
    status = Column(String(30), default="DRAFT")
    supersedes_id = Column(Integer, ForeignKey("cca_treatment_plans.id"), nullable=True)
    signer_email = Column(String(200), nullable=True)
    signer_role = Column(String(50), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    # Never silently rewritten by a guideline version change (architecture doc
    # non-negotiable) -- a newer version only sets these two fields via
    # routers/cca.py's publish_guideline_version; clearing them requires an explicit
    # clinician action (acknowledge_guideline_review), never an automatic content change.
    guideline_review_required = Column(Boolean, default=False)
    guideline_review_reason = Column(Text, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class TreatmentPlanVersion(Base):
    """Append-only version history for TreatmentPlan, mirroring CarePlanVersion's pattern
    (the one existing versioning convention in this codebase with a mandatory reason and a
    full snapshot -- chosen over StagingRecord's self-referencing-pointer style for
    consistency with CarePlan, since Care Plan and Treatment Plan should behave identically
    from an audit standpoint)."""
    __tablename__ = "cca_treatment_plan_versions"
    id = Column(Integer, primary_key=True)
    treatment_plan_id = Column(Integer, ForeignKey("cca_treatment_plans.id"), nullable=False)
    version_no = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    change_reason = Column(Text, nullable=False)
    status_at_version = Column(String(30), nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class TreatmentPlanCoSignature(Base):
    """Scaffolding for future multimodality co-signing (e.g. a Surgical Oncologist
    co-signing the surgical component of a plan a Medical Oncologist authored). Deliberately
    unenforced by any endpoint in this slice -- the architecture doc's open decision on
    multimodality signing defaulted to 'primary signer now, co-signature structure reserved
    for later' rather than guessing at enforcement rules. Do not wire this up without
    revisiting that decision first."""
    __tablename__ = "cca_treatment_plan_cosignatures"
    id = Column(Integer, primary_key=True)
    treatment_plan_id = Column(Integer, ForeignKey("cca_treatment_plans.id"), nullable=False)
    modality = Column(String(100), nullable=False)
    signer_email = Column(String(200), nullable=False)
    signer_role = Column(String(50), nullable=False)
    signed_at = Column(DateTime, default=datetime.utcnow)

class TreatmentSession(Base):
    __tablename__ = "cca_treatment_sessions"
    id = Column(Integer, primary_key=True)
    treatment_plan_id = Column(Integer, ForeignKey("cca_treatment_plans.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    session_no = Column(Integer, default=1)
    cycle_no = Column(Integer, default=1)
    day_no = Column(Integer, default=1)
    planned_on = Column(Date, default=datetime.utcnow)
    administered_on = Column(DateTime, nullable=True)
    administered_by = Column(String(200), nullable=True)
    status = Column(String(50), default="PLANNED")  # PLANNED, ASSESSED, ADMINISTERED, HELD, DEFERRED, CANCELLED
    created_at = Column(DateTime, default=datetime.utcnow)
    # Day Care / Infusion Nurse queue logistics (Gap Analysis PDF, 30 Aug 2026) -- nurse-owned,
    # deliberately separate from `status` above (owned by the clearance-decision flow). See
    # migrations.py's matching ADDITIVE_COLUMNS entries.
    arrival_status = Column(String(30), default="Scheduled")  # Scheduled, Arrived, Cancelled
    arrived_at = Column(DateTime, nullable=True)
    chair_bed = Column(String(50), nullable=True)
    expected_duration_minutes = Column(Integer, nullable=True)

class TreatmentOrder(Base):
    """The executable instruction for one specific TreatmentSession, distinct from both
    TreatmentPlan (the strategy) and TreatmentEvent (what actually happened). Day-Care must
    never act on a TreatmentPlan or CarePlan directly -- only on a TreatmentOrder that is
    itself SIGNED, by a clinician of the plan's own modality (see
    routers/cca.py's _require_modality_signer, reused here). Each cycle/session gets its own
    Order rather than reusing/editing a prior one, so a dose change is always a new,
    separately authorized instruction, never a silent edit to what was already ordered."""
    __tablename__ = "cca_treatment_orders"
    id = Column(Integer, primary_key=True)
    treatment_plan_id = Column(Integer, ForeignKey("cca_treatment_plans.id"), nullable=False)
    treatment_session_id = Column(Integer, ForeignKey("cca_treatment_sessions.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    instructions = Column(JSON, nullable=True)  # e.g. {"drug":..., "dose":..., "route":..., "rate":...}
    version_no = Column(Integer, default=1)
    status = Column(String(30), default="DRAFT")  # DRAFT, SIGNED, EXECUTED, HELD, CANCELLED
    # Order revision / dose-modification linkage (Product 1 gap report item 9) -- mirrors
    # TreatmentPlan.supersedes_id's own pattern. dose_modification_percent is a clinician-chosen
    # label (e.g. "75%"), never computed -- see TreatmentOrderDrugLine's docstring for why no
    # dose is ever calculated in this repo.
    supersedes_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=True)
    revision_reason = Column(Text, nullable=True)
    dose_modification_percent = Column(String(20), nullable=True)
    signer_email = Column(String(200), nullable=True)
    signer_role = Column(String(50), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class TreatmentEvent(Base):
    """What actually happened, as distinct from what was ordered. Always tied to a specific
    signed TreatmentOrder (never to a Plan or Care Plan directly) -- an event with no valid
    order reference should not be constructible through this API; see
    routers/cca.py's record_clearance_decision, the only writer of this table."""
    __tablename__ = "cca_treatment_events"
    id = Column(Integer, primary_key=True)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    event_type = Column(String(30), nullable=False)  # ADMINISTERED, HELD, DEFERRED, DISCONTINUED
    outcome = Column(String(100), nullable=True)  # e.g. "Standard Dose (100%)", "Dose Reduced 75%"
    reason = Column(Text, nullable=True)
    performed_by = Column(String(200), nullable=True)
    performed_role = Column(String(50), nullable=True)
    performed_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class DomainEvent(Base):
    """Durable record of every domain event published through events.py's bus -- the
    architecture doc's named event stream (CARE_PLAN_ACTIVATED, TREATMENT_PLAN_SIGNED,
    TREATMENT_ADMINISTERED, ...), distinct from CCAJourneyEvent (a human-readable per-patient
    timeline written from the same publish() call, not a second source of truth for the
    same data)."""
    __tablename__ = "cca_domain_events"
    id = Column(Integer, primary_key=True)
    event_type = Column(String(50), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ToxicityEvent(Base):
    __tablename__ = "cca_toxicity_events"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("cca_treatment_sessions.id"), nullable=True)
    term = Column(String(200), nullable=False)  # Peripheral sensory neuropathy, Nausea, Neutropenia
    grade = Column(Integer, nullable=False)  # 0 to 5
    baseline_value = Column(String(50), nullable=False)  # Grade 0 (Baseline) - NOT NULL enforced
    grading_standard = Column(String(100), default="CTCAE v5.0")
    standard_version = Column(String(50), default="5.0")
    onset_date = Column(Date, default=datetime.utcnow)
    ongoing = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class TreatmentClearance(Base):
    __tablename__ = "cca_treatment_clearances"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("cca_treatment_sessions.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    decision = Column(String(50), nullable=False)  # CLEARED, CLEARED_DOSE_REDUCTION, HELD, DEFERRED, PENDING_REASSESSMENT, DISCONTINUED
    reason = Column(Text, nullable=False)
    reassess_on = Column(Date, nullable=True)
    task_owner_id = Column(String(100), nullable=True)
    decided_by = Column(String(200), nullable=False)
    decided_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

class ResponseAssessment(Base):
    __tablename__ = "cca_response_assessments"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    framework = Column(String(50), default="RECIST")
    framework_version = Column(String(50), default="1.1")
    response_category = Column(String(50), nullable=False)  # CR, PR, SD, PD, NE
    confirmed = Column(Boolean, default=True)
    lesions = Column(JSON, nullable=True)
    imaging_reference = Column(String(255))
    recorded_by = Column(String(200))
    recorded_at = Column(DateTime, default=datetime.utcnow)

class CCAJourneyEvent(Base):
    __tablename__ = "cca_journey_events"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    event_type = Column(String(100), nullable=False)
    event_title = Column(String(200), nullable=False)
    event_category = Column(String(100), nullable=False)  # INTAKE, CONSULTATION, INVESTIGATION, STAGING, MDT, CARE_PLAN, TREATMENT, FOLLOW_UP
    description = Column(Text)
    actor_name = Column(String(200))
    actor_role = Column(String(100))
    provenance_fact_id = Column(Integer, nullable=True)
    provenance_doc_id = Column(Integer, nullable=True)
    event_metadata = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Day Care / Infusion Nurse -- treatment-day nursing workspace (Gap Analysis PDF,
# 30 Aug 2026). Sits entirely on top of the existing TreatmentOrder/TreatmentEvent
# lifecycle -- none of these tables are ever read by the clearance-decision flow in
# routers/cca.py, and none of them write to TreatmentOrder.status or
# TreatmentSession.status (those stay owned by record_clearance_decision). This is
# the nursing documentation layer the gap analysis found missing: pharmacy
# readiness, vascular access, per-medication administration, monitoring, hold/
# reaction/extravasation escalation, and treatment-day completion. Every field
# here is either a fixed-vocabulary choice the nurse picks or free-text
# documentation -- nothing computes or validates against a clinical/dosage
# threshold (see the "no dosage review logic anywhere" project rule).
# ---------------------------------------------------------------------------

class PreTreatmentSafetyCheck(Base):
    """One per Treatment Order -- the identity/order/allergy/labs review the gap
    analysis's Pre-Treatment Safety Checklist (SS5.3) calls for. allergy_review_done/
    _notes is the nurse's own attestation for this treatment day, not a new shared
    CCAPatient allergy field -- that would reach into Front Desk/registration, out
    of scope for this module."""
    __tablename__ = "cca_pretreatment_safety_checks"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=False)
    identity_verified = Column(Boolean, default=False)
    identity_method = Column(String(200), nullable=True)
    # 2-of-3 patient identifier match (Product 1 vs Product 2 gap report, Batch 3: Day
    # Care/MAR pre-administration assessment) -- structural count-gate the router enforces
    # (at least 2 of these 3 must be true before identity_verified may be set true), never a
    # computed identity-matching algorithm; each is the nurse's own attestation.
    name_matched = Column(Boolean, default=False)
    mrn_matched = Column(Boolean, default=False)
    dob_matched = Column(Boolean, default=False)
    order_cycle_confirmed = Column(Boolean, default=False)
    allergy_review_done = Column(Boolean, default=False)
    allergy_review_notes = Column(Text, nullable=True)
    symptom_review_notes = Column(Text, nullable=True)
    labs_reviewed = Column(Boolean, default=False)
    labs_review_notes = Column(Text, nullable=True)
    checked_by = Column(String(200))
    checked_at = Column(DateTime, default=datetime.utcnow)


class VascularAccessAssessment(Base):
    __tablename__ = "cca_vascular_access_assessments"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=False)
    device_type = Column(String(50), nullable=False)  # Peripheral IV, PICC, Port, CVC
    site = Column(String(200), nullable=True)
    gauge = Column(String(20), nullable=True)
    dressing_status = Column(String(100), nullable=True)
    site_condition = Column(String(200), nullable=True)
    patency_confirmed = Column(Boolean, default=False)
    blood_return = Column(Boolean, nullable=True)
    access_ready = Column(Boolean, default=False)
    problem_reported = Column(Boolean, default=False)
    problem_notes = Column(Text, nullable=True)
    assessed_by = Column(String(200))
    assessed_at = Column(DateTime, default=datetime.utcnow)


class PharmacyReadiness(Base):
    """One row per Treatment Order, status updated in place -- traceability comes
    from calling publish() on every transition (DomainEvent/CCAJourneyEvent), the
    same pattern TREATMENT_HELD/TREATMENT_ADMINISTERED already use, rather than a
    second history table. CCAPharmacist now exists (auth.CCA_ROLES) and drives the
    richer PharmacyVerification/PharmacyPreparation/PharmacyRelease workflow below
    (Product 1 vs Product 2 gap report, Batch 2) -- this row stays Day Care's own
    coarse status flag, auto-upserted as a side effect of that workflow so every
    existing reader of PharmacyReadiness keeps working unchanged."""
    __tablename__ = "cca_pharmacy_readiness"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=False)
    status = Column(String(30), default="Verified")  # Verified, Preparing, Ready, Dispensed, Received
    status_updated_by = Column(String(200), nullable=True)
    status_updated_at = Column(DateTime, default=datetime.utcnow)
    received_by = Column(String(200), nullable=True)
    received_at = Column(DateTime, nullable=True)
    product_verified = Column(Boolean, default=False)
    expiry_checked = Column(Boolean, default=False)
    second_checker_name = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PharmacyVerification(Base):
    """Oncology Pharmacy verification/query/reject (Product 1 vs Product 2 gap report,
    Batch 2, "SCR-PHA-002/003"). `checklist` is an 18-key JSON dict of pharmacist
    attestations (patient_identity, allergy, regimen_version, cycle_day, dose_basis,
    calculated_dose, ordered_dose, dose_variance, renal_adjustment, hepatic_adjustment,
    cumulative_dose, interaction, duplication, route, diluent, final_concentration, stock,
    expiry) -- every key is the pharmacist personally confirming they checked that item, never
    a system computation or threshold comparison (standing repo rule: no dose-calculation
    logic -- Product 1's own reference implementation does independently recompute/threshold
    several of these, which is exactly what we deliberately do NOT port).
    resolved/resolved_by/resolved_at/response_action/response_note are filled by the treating
    oncologist's query response (POST .../respond), which reopens the order for re-verification
    -- mirrors TreatmentClearance's own "decision + reason, clinician-authored" shape."""
    __tablename__ = "cca_pharmacy_verifications"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=False)
    checklist = Column(JSON, nullable=True)
    decision = Column(String(20), nullable=False)  # Verified, Query, Reject
    reason_code = Column(String(50), nullable=True)  # Dose clarification, Allergy, Interaction, Formulation, Stock, Expiry, Other
    message = Column(Text, nullable=True)
    resolved = Column(Boolean, default=False)
    resolved_by = Column(String(200), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    response_action = Column(Text, nullable=True)
    response_note = Column(Text, nullable=True)
    verified_by = Column(String(200))
    verified_at = Column(DateTime, default=datetime.utcnow)


class PharmacyPreparation(Base):
    """Oncology Pharmacy preparation/compounding record, one per TreatmentOrderDrugLine
    (models_cca_oncology_ext.py, Batch 1) -- Product 1 tracks preparation per drug item, and
    Batch 1 already gives us that granularity, so no new per-line concept is needed.
    final_concentration is pharmacist-typed, never computed from dose/volume (Product 1 does
    compute this; we deliberately don't -- standing repo rule). beyond_use_at is simple date
    arithmetic off a pharmacist-entered stability_hours reference (prepared_at + stability_hours),
    not a dosing decision. drug_batch_id is an optional link into the general HMS drug/batch
    master (models.py's DrugBatch) for real traceability without rebuilding inventory tracking."""
    __tablename__ = "cca_pharmacy_preparations"
    id = Column(Integer, primary_key=True)
    treatment_order_drug_line_id = Column(Integer, ForeignKey("cca_treatment_order_drug_lines.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=False)
    batch_number = Column(String(100), nullable=True)
    expiry_date = Column(Date, nullable=True)
    drug_batch_id = Column(Integer, ForeignKey("drug_batches.id"), nullable=True)
    diluent = Column(String(200), nullable=True)
    actual_volume = Column(String(50), nullable=True)
    actual_volume_unit = Column(String(20), nullable=True)
    final_concentration = Column(String(100), nullable=True)
    stability_hours = Column(Integer, nullable=True)
    beyond_use_at = Column(DateTime, nullable=True)
    wastage_amount = Column(String(50), nullable=True)
    wastage_unit = Column(String(20), nullable=True)
    wastage_reason = Column(String(50), nullable=True)
    prepared_by = Column(String(200))
    prepared_at = Column(DateTime, default=datetime.utcnow)


class PharmacyRelease(Base):
    """Independent double-check + label/release, 1:1 with PharmacyPreparation. Unlike Product 1
    (which free-types second_check_by and string-compares it against the preparer's typed
    name), second_check_by/released_by here are always the authenticated caller's own actor
    identity (this codebase's own established convention -- actor identity is never a
    self-typed name) -- the write endpoint rejects with 409 if it equals the preparation's
    prepared_by, so a real double-check requires two different logged-in pharmacist accounts."""
    __tablename__ = "cca_pharmacy_releases"
    id = Column(Integer, primary_key=True)
    treatment_order_drug_line_id = Column(Integer, ForeignKey("cca_treatment_order_drug_lines.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=False)
    second_check_by = Column(String(200))
    label_verified = Column(Boolean, default=False)
    dispensed_to = Column(String(200), nullable=True)
    manifest_no = Column(String(100), nullable=True)
    dispensed_at = Column(DateTime, nullable=True)
    released_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class InfusionMedicationAdministration(Base):
    """One row per medication line the nurse transcribes off the signed Treatment
    Order for this treatment day. dose/route/sequence_no/volume_diluent/
    rate_duration are what the nurse read off the order and are documenting, not a
    structured field the order itself provides (TreatmentOrder.instructions is
    free text authored in cca-app.js, out of scope here) -- never computed or
    checked against a threshold, purely a transcription of an already-authorized
    instruction, same epistemic status as a paper MAR entry."""
    __tablename__ = "cca_infusion_medication_administrations"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=False)
    medication_name = Column(String(200), nullable=False)
    category = Column(String(30), default="Antineoplastic")  # Premedication, Antineoplastic, Other
    dose = Column(String(100), nullable=True)
    route = Column(String(100), nullable=True)
    sequence_no = Column(Integer, default=1)
    volume_diluent = Column(String(100), nullable=True)
    rate_duration = Column(String(100), nullable=True)
    status = Column(String(30), default="Pending")  # Pending, InProgress, Paused, Stopped, Completed, Omitted
    product_label_verified = Column(Boolean, default=False)
    expiry_integrity_checked = Column(Boolean, default=False)
    second_verifier_name = Column(String(200), nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    actual_rate = Column(String(100), nullable=True)
    actual_volume = Column(String(100), nullable=True)
    omission_reason = Column(Text, nullable=True)
    administered_by = Column(String(200), nullable=True)
    administered_at = Column(DateTime, nullable=True)
    # Full MAR / partial administration / variance (Product 1 vs Product 2 gap report, Batch
    # 3) -- completion_status is a separate axis from `status` above (a completed line can
    # still have been only Partially Administered); variance_type/reason/note are the nurse's
    # own attestation that something differed from the order, never a computed percentage or
    # threshold (standing repo rule -- Product 1 itself computes a variance % and blocks past
    # 20%, which we deliberately do not port; see record_medication_event's docstring).
    completion_status = Column(String(30), nullable=True)  # Administered, Partially Administered, Held, Stopped
    variance_type = Column(String(30), nullable=True)  # None, Dose variance, Rate variance, Route variance, Timing variance, Sequence variance, Other
    variance_reason = Column(String(50), nullable=True)  # Clinician instruction, Infusion reaction, Access issue, Patient condition, Operational delay, Product issue, Other
    variance_note = Column(Text, nullable=True)
    # Mandatory reaction attestation on every MAR entry (Product 1's own design -- not an
    # optional afterthought), distinct from the richer InfusionReactionEvent this codebase
    # already has for the actual clinical detail once reaction_occurred is True.
    reaction_occurred = Column(Boolean, nullable=True)
    # Bedside label/barcode re-check -- a boolean attestation (matching Product 1's own
    # "Barcode/label match" checkbox, not real scanner integration), deliberately separate
    # from PharmacyRelease.label_verified (pharmacy's own check before dispensing).
    label_match_confirmed = Column(Boolean, default=False)
    label_verified_by = Column(String(200), nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class InfusionIndependentVerification(Base):
    """Independent chairside double-check before administering an Antineoplastic/Targeted
    Therapy line (Product 1 vs Product 2 gap report, Batch 3) -- the bedside equivalent of
    PharmacyRelease's independent double-check. `checklist` is a 9-key JSON dict of nurse
    attestations (drug, dose, volume_diluent, route, rate, expiry, physical_integrity,
    sequence, pump_settings), matching PharmacyVerification's own JSON-checklist shape. Must
    be performed by someone other than whoever ultimately starts the administration -- the
    write/START endpoints enforce this by actor identity, never a self-typed name (same
    convention as PharmacyRelease)."""
    __tablename__ = "cca_infusion_independent_verifications"
    id = Column(Integer, primary_key=True)
    administration_id = Column(Integer, ForeignKey("cca_infusion_medication_administrations.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    checklist = Column(JSON, nullable=True)
    verified_by = Column(String(200))
    verified_at = Column(DateTime, default=datetime.utcnow)


class InfusionAdministrationEvent(Base):
    """Append-only sub-log giving each administration row's start/pause/resume/
    stop/complete its own timestamped audit trail, distinct from the row's current
    `status` (a snapshot) the same way TreatmentEvent is distinct from
    TreatmentOrder.status."""
    __tablename__ = "cca_infusion_administration_events"
    id = Column(Integer, primary_key=True)
    administration_id = Column(Integer, ForeignKey("cca_infusion_medication_administrations.id"), nullable=False)
    event_type = Column(String(20), nullable=False)  # START, PAUSE, RESUME, STOP, COMPLETE, OMIT
    notes = Column(Text, nullable=True)
    performed_by = Column(String(200))
    performed_at = Column(DateTime, default=datetime.utcnow)


class InfusionMonitoringObservation(Base):
    """Baseline vitals (gap row "Baseline vitals / nursing assessment") is simply
    the first row with phase=Baseline, recorded from the Pre-Treatment tab -- no
    separate vitals table."""
    __tablename__ = "cca_infusion_monitoring_observations"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=True)
    phase = Column(String(20), default="During")  # Baseline, During, Post
    observation_time = Column(DateTime, default=datetime.utcnow)
    vitals = Column(JSON, nullable=True)  # whatever the nurse enters: temp/bp/pulse/rr/spo2
    symptoms = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    # Nurse-observed Pain Scale, 0 (none) - 5 (worst), and VIP (Visual Infusion Phlebitis) Score,
    # 0 (no symptoms) - 5 (advanced thrombophlebitis) -- both purely the nurse's own documented
    # observation at this monitoring point, never computed or checked against a threshold.
    pain_score = Column(Integer, nullable=True)
    vip_score = Column(Integer, nullable=True)
    recorded_by = Column(String(200))
    recorded_at = Column(DateTime, default=datetime.utcnow)


class TreatmentHoldEvent(Base):
    """The generic "nurse flags an unsafe situation and stops/escalates" action
    (gap row "Treatment hold / escalation") -- distinct from the richer
    InfusionReactionEvent/ExtravasationEvent below, which carry their own clinical
    documentation. Never touches TreatmentOrder.status/TreatmentSession.status:
    those stay owned by the oncologist-gated clearance-decision flow."""
    __tablename__ = "cca_treatment_hold_events"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=True)
    reason = Column(Text, nullable=False)
    hold_type = Column(String(30), default="Safety Hold")  # Safety Hold, Reaction, Extravasation, Other
    escalated_to = Column(String(200), nullable=True)
    resumed = Column(Boolean, default=False)
    resumed_by = Column(String(200), nullable=True)
    resumed_at = Column(DateTime, nullable=True)
    resume_notes = Column(Text, nullable=True)
    held_by = Column(String(200))
    held_at = Column(DateTime, default=datetime.utcnow)


class InfusionReactionEvent(Base):
    """physician_disposition/directed_by document a physician's verbal/phone
    instruction relayed to the nurse -- never a nurse-decided or system-computed
    outcome (same reasoning as TreatmentClearance staying clinician-gated)."""
    __tablename__ = "cca_infusion_reaction_events"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=True)
    onset_at = Column(DateTime, default=datetime.utcnow)
    # Precise per-drug attribution (Gap Analysis PDF item 11: "Reaction Per Drug") -- links to the
    # exact InfusionMedicationAdministration row that was infusing, when the nurse can identify
    # one from the treatment order's medication list. medication_running (free text) is kept as a
    # legacy/fallback field for cases where the drug isn't on the structured list.
    administration_id = Column(Integer, ForeignKey("cca_infusion_medication_administrations.id"), nullable=True)
    medication_running = Column(String(200), nullable=True)
    symptoms = Column(Text, nullable=False)
    vitals = Column(JSON, nullable=True)
    infusion_action = Column(String(20), nullable=True)  # Paused, Stopped
    informed_person = Column(String(200), nullable=True)
    interventions = Column(Text, nullable=True)
    patient_response = Column(Text, nullable=True)
    physician_disposition = Column(String(30), nullable=True)  # Restart, Modify, Discontinue, Transfer-Escalate
    directed_by = Column(String(200), nullable=True)
    reported_by = Column(String(200))
    reported_at = Column(DateTime, default=datetime.utcnow)


class ExtravasationEvent(Base):
    """Deliberately separate from InfusionReactionEvent -- the gap analysis (SS5.10)
    calls out suspected extravasation as its own workflow, distinct from a
    systemic infusion reaction."""
    __tablename__ = "cca_extravasation_events"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=True)
    agent = Column(String(200), nullable=False)
    site = Column(String(200), nullable=True)
    symptoms = Column(Text, nullable=False)
    approx_exposure_volume = Column(String(100), nullable=True)
    line_status = Column(String(100), nullable=True)
    immediate_actions = Column(Text, nullable=True)
    escalation_notes = Column(Text, nullable=True)
    follow_up_notes = Column(Text, nullable=True)
    reported_by = Column(String(200))
    reported_at = Column(DateTime, default=datetime.utcnow)


class TreatmentDayCompletion(Base):
    """One per Treatment Order; creating this row *is* the "lock the nursing
    record" action the gap analysis calls for (SS5.11). Deliberately does not
    touch TreatmentOrder.status/TreatmentSession.status -- those stay owned by the
    existing clearance-decision flow; this is the nursing documentation layer
    sitting on top of it."""
    __tablename__ = "cca_treatment_day_completions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=False)
    final_vitals = Column(JSON, nullable=True)
    final_symptoms = Column(Text, nullable=True)
    disposition = Column(String(200), nullable=True)
    # Overall tolerance of today's treatment (Product 1 vs Product 2 gap report, Batch 3) --
    # a nurse-chosen governed value, never derived/computed from the per-drug reaction or
    # variance data recorded above (standing repo rule: never compute a clinical judgment).
    tolerance = Column(String(30), nullable=True)  # Good, Mild symptoms, Significant reaction
    access_status = Column(String(50), nullable=True)  # Flushed, Removed, Locked, Left in situ
    patient_education_notes = Column(Text, nullable=True)
    red_flags_given = Column(Boolean, default=False)
    next_treatment_date = Column(Date, nullable=True)
    next_labs_required = Column(Text, nullable=True)
    completed_by = Column(String(200))
    completed_at = Column(DateTime, default=datetime.utcnow)


class BloodProductAdministration(Base):
    """Blood Bank/Transfusion (Gap Analysis PDF item 28: "Blood Product") -- documents a unit's
    transfusion the same way InfusionMedicationAdministration documents a chemo drug: what the
    nurse transcribed and verified off an already-authorized/crossmatched unit, never a clinical
    decision the system makes. Two-person crossmatch/compatibility verification (crossmatch_
    confirmed + second_verifier_name) mirrors the product_label_verified/second_verifier_name
    pattern already established for medications, since blood products carry the same
    verify-before-administer requirement. treatment_order_id is nullable the same way it is on
    InfusionReactionEvent/TreatmentHoldEvent -- available whenever a patient is open in the
    infusion workspace, not only once a signed chemo order exists."""
    __tablename__ = "cca_blood_product_administrations"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=True)
    product_type = Column(String(50), nullable=False)  # PRBC, Platelets, FFP, Cryoprecipitate, Other
    unit_id = Column(String(100), nullable=False)
    blood_group = Column(String(20), nullable=True)
    crossmatch_confirmed = Column(Boolean, default=False)
    crossmatch_reference = Column(String(200), nullable=True)
    consent_confirmed = Column(Boolean, default=False)
    second_verifier_name = Column(String(200), nullable=True)
    volume = Column(String(100), nullable=True)
    rate = Column(String(100), nullable=True)
    status = Column(String(30), default="Pending")  # Pending, InProgress, Paused, Stopped, Completed
    pre_transfusion_vitals = Column(JSON, nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    administered_by = Column(String(200), nullable=True)
    administered_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class TransfusionFeedback(Base):
    """Post-transfusion Feedback (Gap Analysis PDF item 29) -- the closing documentation for a
    blood product administration: how the patient tolerated it and whether a reaction occurred.
    Deliberately its own record (not folded into BloodProductAdministration) the same way
    TreatmentDayCompletion is separate from InfusionMedicationAdministration -- this is the
    nursing/clinical outcome layer sitting on top of the administration record. blood_product_id
    is nullable so feedback can still be recorded even if the unit wasn't logged through the
    structured Blood Product flow above."""
    __tablename__ = "cca_transfusion_feedback"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=True)
    blood_product_id = Column(Integer, ForeignKey("cca_blood_product_administrations.id"), nullable=True)
    reaction_occurred = Column(Boolean, default=False)
    reaction_type = Column(String(100), nullable=True)  # Allergic, Febrile Non-Hemolytic, Hemolytic, TRALI, TACO, Other
    symptoms = Column(Text, nullable=True)
    vitals = Column(JSON, nullable=True)
    action_taken = Column(Text, nullable=True)
    outcome = Column(Text, nullable=True)
    feedback_notes = Column(Text, nullable=True)
    reported_by = Column(String(200))
    reported_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Treatment Completion (Product 1 vs Product 2 gap report, Batch 7: C.23) -- the
# end-of-treatment reconciliation the report found "not meaningfully represented" in
# this codebase. One TreatmentCompletion per completed/discontinued course of care, with
# the Cancer Treatment Summary's many "read-only/derived" fields computed at read time
# (see routers/cca.py's _treatment_summary_dict) from the existing diagnosis/staging/
# treatment/toxicity/response tables rather than duplicated here -- one source of truth,
# per the gap report's own cross-module "Interoperability" requirement. Nothing here
# computes a dose, threshold, or clinical judgment.
# ---------------------------------------------------------------------------

class TreatmentCompletion(Base):
    """The End-of-Treatment Clinical Review (SCR-CMP-002) -- the clinician's own record
    that a course of cancer treatment has ended, why, and what comes next.
    cancer_episode_ref is a free-text/label reference: this codebase has no formal Cancer
    Episode entity yet (gap report item 5, out of scope for this batch), so there is
    nothing to foreign-key to."""
    __tablename__ = "cca_treatment_completions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    cancer_episode_ref = Column(String(200), nullable=True)
    treatment_plan_id = Column(Integer, ForeignKey("cca_treatment_plans.id"), nullable=True)
    treatment_intent = Column(String(50), nullable=True)
    treatment_start_date = Column(Date, nullable=True)
    treatment_end_date = Column(Date, nullable=True)
    # Completed as Planned, Discontinued -- Toxicity, Discontinued -- Progression,
    # Discontinued -- Patient Choice, Discontinued -- Other
    completion_type = Column(String(50), nullable=True)
    reason = Column(Text, nullable=False)
    disease_status_at_completion = Column(String(100), nullable=True)
    residual_toxicities = Column(Text, nullable=True)
    ongoing_supportive_needs = Column(Text, nullable=True)
    next_care_phase = Column(String(50), nullable=True)  # Surveillance, Survivorship, Palliative, Hospice, Transferred
    next_review_date = Column(Date, nullable=True)
    status = Column(String(30), default="DRAFT")  # DRAFT, SIGNED, AMENDED
    signed_by = Column(String(200), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class ModalityCompletionRecord(Base):
    """One row per treatment modality being reconciled at completion (SCR-CMP-004) --
    planned vs actually-delivered course for systemic/radiation/surgery/oral therapy.
    planned_value/actual_value are clinician-typed summaries (e.g. "6 cycles" / "5 cycles,
    cycle 6 omitted"), never computed off the underlying order/fraction tables."""
    __tablename__ = "cca_modality_completion_records"
    id = Column(Integer, primary_key=True)
    completion_id = Column(Integer, ForeignKey("cca_treatment_completions.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    modality = Column(String(50), nullable=False)  # Systemic, Radiation, Surgery, Oral/Continuous
    planned_value = Column(String(200), nullable=True)
    actual_value = Column(String(200), nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    completed = Column(Boolean, default=False)
    variance = Column(Text, nullable=True)
    authorized_modification_source = Column(String(200), nullable=True)
    unresolved_discrepancy = Column(Text, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class CumulativeExposureRecord(Base):
    """Cumulative Exposure & Late-Effect Baseline (SCR-CMP-005). actual_cumulative_exposure
    is always a clinician/pharmacy-sourced value typed in here (e.g. read off the existing
    administration/fraction records), never computed by this table (standing repo rule)."""
    __tablename__ = "cca_cumulative_exposure_records"
    id = Column(Integer, primary_key=True)
    completion_id = Column(Integer, ForeignKey("cca_treatment_completions.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    agent_or_modality = Column(String(200), nullable=False)
    exposure_metric = Column(String(100), nullable=True)
    actual_cumulative_exposure = Column(String(100), nullable=True)
    unit = Column(String(50), nullable=True)
    source_reference = Column(String(255), nullable=True)
    late_effect_domain = Column(String(100), nullable=True)
    monitoring_plan = Column(Text, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class TreatmentCompletionHandoff(Base):
    """Treatment Completion Handoff (SCR-CMP-006) -- to Surveillance, Survivorship,
    Palliative Care, primary care, or another service."""
    __tablename__ = "cca_treatment_completion_handoffs"
    id = Column(Integer, primary_key=True)
    completion_id = Column(Integer, ForeignKey("cca_treatment_completions.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    destination = Column(String(100), nullable=False)
    handoff_summary = Column(Text, nullable=False)
    outstanding_investigations = Column(Text, nullable=True)
    owner = Column(String(200), nullable=False)
    due_date = Column(Date, nullable=True)
    receiving_clinician = Column(String(200), nullable=True)
    acceptance_status = Column(String(30), default="Pending")  # Pending, Accepted
    accepted_by = Column(String(200), nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class TreatmentSummary(Base):
    """The Cancer Treatment Summary document (SCR-CMP-003) -- the clinician's synthesis
    narrative plus a status. Every "read-only/derived" field the reference spec lists
    (diagnosis/staging snapshot, systemic therapy planned-vs-actual, RT prescribed-vs-
    delivered, surgery planned-vs-actual, final pathology, response history, toxicities,
    cumulative exposure, ongoing medications, follow-up plan, care-team contacts) is
    computed at read time by routers/cca.py's _treatment_summary_dict from the existing
    diagnosis/staging/treatment/toxicity/response tables -- never duplicated here, so
    there is exactly one place any of that underlying data can ever be edited."""
    __tablename__ = "cca_treatment_summaries"
    id = Column(Integer, primary_key=True)
    completion_id = Column(Integer, ForeignKey("cca_treatment_completions.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    clinician_synthesis = Column(Text, nullable=True)
    outstanding_issues = Column(Text, nullable=True)
    status = Column(String(30), default="DRAFT")  # DRAFT, FINALIZED
    signed_by = Column(String(200), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    issued_to_patient_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class TreatmentSummaryDistribution(Base):
    """Treatment Summary Distribution & Acknowledgement (SCR-CMP-007) -- who the
    finalized summary was sent to and whether they acknowledged it."""
    __tablename__ = "cca_treatment_summary_distributions"
    id = Column(Integer, primary_key=True)
    summary_id = Column(Integer, ForeignKey("cca_treatment_summaries.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    recipient = Column(String(200), nullable=False)
    recipient_role = Column(String(100), nullable=True)
    document_version = Column(Integer, default=1)
    method = Column(String(50), nullable=True)  # Printed, Portal, Email, Referral Letter
    sent_at = Column(DateTime, default=datetime.utcnow)
    acknowledgement_required = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime, nullable=True)
    status = Column(String(30), default="Sent")  # Sent, Acknowledged, Reissued
    reissue_reason = Column(Text, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Surveillance / Survivorship (Product 1 vs Product 2 gap report, Batch 8: C.24) --
# completely missing before this batch. One SurveillancePlan per patient's follow-up
# programme after active treatment, with visits/investigations/late-effects/referrals
# hanging off it. Like TreatmentSummary above, the patient-facing
# SurvivorshipCarePlanDocument's "derived" fields are computed at read time (see
# routers/cca.py's _survivorship_derived) rather than duplicated. Nothing here computes a
# dose, threshold, or clinical judgment -- every status/grade/priority is a clinician's own
# typed choice.
# ---------------------------------------------------------------------------

class SurveillancePlan(Base):
    """Surveillance / Survivorship Care Plan (SCR-SURV-003) -- the governing record for a
    patient's post-treatment follow-up programme. cancer_episode_ref mirrors
    TreatmentCompletion's own free-text reference (no formal Cancer Episode entity yet,
    gap report item 5, out of scope for this batch)."""
    __tablename__ = "cca_surveillance_plans"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    cancer_episode_ref = Column(String(200), nullable=True)
    completion_id = Column(Integer, ForeignKey("cca_treatment_completions.id"), nullable=True)
    surveillance_intent = Column(Text, nullable=True)
    follow_up_frequency = Column(String(100), nullable=True)
    duration_of_surveillance = Column(String(100), nullable=True)
    late_effect_monitoring_plan = Column(Text, nullable=True)
    recurrence_red_flags = Column(Text, nullable=True)
    responsible_clinician = Column(String(200), nullable=True)
    primary_care_handoff_required = Column(Boolean, nullable=True)
    current_phase = Column(String(100), nullable=True)  # e.g. "Year 1", "Year 2-5", "Long-term"
    next_review_date = Column(Date, nullable=True)
    status = Column(String(30), default="DRAFT")  # DRAFT, ACTIVE, SUPERSEDED
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class SurveillanceVisit(Base):
    """Surveillance Follow-up Visit (SCR-SURV-002) -- one clinical encounter within a
    SurveillancePlan."""
    __tablename__ = "cca_surveillance_visits"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("cca_surveillance_plans.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    interval_history = Column(Text, nullable=True)
    red_flag_symptoms = Column(JSON, nullable=True)
    examination = Column(Text, nullable=True)
    late_effects_reviewed = Column(Text, nullable=True)
    results_reviewed = Column(Text, nullable=True)
    disease_status = Column(String(100), nullable=True)
    health_maintenance = Column(Text, nullable=True)
    next_review_interval = Column(String(100), nullable=True)
    status = Column(String(30), default="DRAFT")  # DRAFT, SIGNED
    signed_by = Column(String(200), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class SurveillanceInvestigation(Base):
    """Surveillance Investigation Planner row (SCR-SURV-004). Links to the existing unified
    CCAOrder/CCAResult tables when an investigation is actually raised/resulted, rather than
    duplicating that lifecycle here -- this table is only the surveillance-specific plan
    (what's due, when, why)."""
    __tablename__ = "cca_surveillance_investigations"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("cca_surveillance_plans.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    investigation = Column(String(200), nullable=False)
    rationale = Column(Text, nullable=True)
    frequency = Column(String(100), nullable=True)
    due_date = Column(Date, nullable=True)
    status = Column(String(30), default="Due")  # Due, Ordered, Resulted, Overdue, Cancelled
    linked_order_id = Column(Integer, ForeignKey("cca_orders.id"), nullable=True)
    linked_result_id = Column(Integer, ForeignKey("cca_results.id"), nullable=True)
    result_summary = Column(Text, nullable=True)
    next_due = Column(Date, nullable=True)
    owner = Column(String(200), nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class LateEffectRecord(Base):
    """Late Effects Register (SCR-SURV-005) -- longitudinal tracking of a late/chronic
    treatment effect, independent of any single visit. plan_id is nullable: a late effect
    can be logged before a formal SurveillancePlan exists yet."""
    __tablename__ = "cca_late_effect_records"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("cca_surveillance_plans.id"), nullable=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    late_effect = Column(String(200), nullable=False)
    onset_date = Column(Date, nullable=True)
    severity_grade = Column(String(50), nullable=True)
    attribution = Column(String(100), nullable=True)
    status = Column(String(30), default="Active")  # Active, Resolved, Monitoring
    intervention = Column(Text, nullable=True)
    owner = Column(String(200), nullable=True)
    last_reviewed = Column(Date, nullable=True)
    next_review = Column(Date, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class SurvivorshipCarePlanDocument(Base):
    """Patient Survivorship Care Plan (SCR-SURV-006) -- the patient-facing document. Its
    "derived" fields (diagnosis/treatment summary, care-team contacts, treatments received,
    ongoing medications, late effects to watch, follow-up schedule, planned tests, red-flag
    symptoms, next appointments) are computed at read time by routers/cca.py's
    _survivorship_derived, reusing the same underlying data as TreatmentSummary rather than
    re-entering it -- gap report's own "Interoperability" cross-module requirement."""
    __tablename__ = "cca_survivorship_care_plan_documents"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("cca_surveillance_plans.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    language = Column(String(50), nullable=True)
    template_version = Column(String(30), nullable=True)
    interpreter_governance_note = Column(Text, nullable=True)
    education_delivered = Column(String(50), nullable=True)  # Full, Partial, Not Yet
    comprehension_teach_back = Column(String(50), nullable=True)  # Confirmed, Partial, Not Confirmed
    date_issued = Column(Date, nullable=True)
    reissue_reason = Column(Text, nullable=True)
    status = Column(String(30), default="DRAFT")  # DRAFT, ISSUED
    issued_by = Column(String(200), nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class RecurrenceSuspicionEvent(Base):
    """Recurrence Suspicion / Re-entry (SCR-SURV-007) -- the trigger for the gap report's
    "re-entry into active oncology" requirement. Actioning this (see
    routers/cca.py's action_recurrence_suspicion) sets CCAPatient.journey_state back to an
    active-treatment label, the same lightweight display-label mechanism the demo clock
    endpoints already use (journey_state has never been a strict enum in this codebase --
    see routers/cca.py's existing demo_advance_clock)."""
    __tablename__ = "cca_recurrence_suspicion_events"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("cca_surveillance_plans.id"), nullable=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    trigger = Column(String(100), nullable=False)  # Symptom, Imaging Finding, Biomarker Rise, Clinical Exam, Patient-Reported, Other
    trigger_detail = Column(Text, nullable=False)
    date_identified = Column(Date, nullable=True)
    urgency = Column(String(30), nullable=True)  # Routine, Urgent, Emergency
    immediate_actions = Column(Text, nullable=True)
    re_entry_destination = Column(String(100), nullable=True)  # Medical Oncology, Surgical Oncology, Radiation Oncology, MDT
    same_episode_or_new_primary = Column(String(30), nullable=True)  # Same Episode, Possible New Primary, Undetermined
    status = Column(String(30), default="OPEN")  # OPEN, ACTIONED
    actioned_by = Column(String(200), nullable=True)
    actioned_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class SurveillanceRecallEntry(Base):
    """Lost-to-Follow-up / Recall Queue (SCR-SURV-008) -- structurally mirrors
    CCACoordinationCase's contact/communication_status shape (Nurse Navigation) rather than
    inventing a parallel convention, since both are "we need to reach this patient" workflows."""
    __tablename__ = "cca_surveillance_recall_entries"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("cca_surveillance_plans.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    follow_up_due_date = Column(Date, nullable=True)
    risk_priority = Column(String(30), nullable=True)  # Low, Medium, High
    preferred_contact = Column(String(100), nullable=True)
    contact_attempts = Column(JSON, nullable=True)  # [{date, method, outcome}, ...]
    barrier = Column(Text, nullable=True)
    next_attempt_date = Column(Date, nullable=True)
    escalation_level = Column(String(30), nullable=True)
    outcome = Column(String(100), nullable=True)
    status = Column(String(30), default="Open")  # Open, Closed
    owner = Column(String(200), nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class SurvivorshipReferral(Base):
    """Survivorship Referrals & Support (SCR-SURV-009)."""
    __tablename__ = "cca_survivorship_referrals"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("cca_surveillance_plans.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    domain = Column(String(100), nullable=False)  # Psychosocial, Nutrition, Rehabilitation, Fertility, Financial, Other
    need_reason = Column(Text, nullable=True)
    service_provider = Column(String(200), nullable=True)
    priority = Column(String(30), nullable=True)
    referral_date = Column(Date, nullable=True)
    appointment_date = Column(Date, nullable=True)
    status = Column(String(30), default="Referred")  # Referred, Scheduled, Attended, Declined, Cancelled
    outcome = Column(Text, nullable=True)
    follow_up_owner = Column(String(200), nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Oral / Continuous Anticancer Therapy (Product 1 vs Product 2 gap report, Batch 9: C.14)
# -- completely missing before this batch (self-administered therapy had no backend
# representation at all, unlike the infusion-chair TreatmentOrder/InfusionMedicationAdministration
# pipeline). Every dosing-adjacent field here is clinician/pharmacist-typed, never
# computed -- final_prescribed_dose mirrors TreatmentOrderDrugLine.planned_dose's own
# documented reasoning (standing repo rule: no dose-calculation logic). Explicitly NOT
# ported from the reference spec: the achievable-dose-from-strengths check, the
# days-supply/adherence-percentage/pill-count-discrepancy calculators, and the
# drug-interaction checker -- all computed/threshold logic this repo does not implement.
# ---------------------------------------------------------------------------

class OralTherapyPrescription(Base):
    """Oral Therapy Prescription (SCR-ORL-001) -- prescribed with the same rigour as an
    infusion order, recognising the administration itself happens unobserved at home.
    supersedes_id mirrors TreatmentOrder's own dose-change-is-a-new-version convention: a
    dose/schedule change is a new prescription row, never a silent edit to a signed one."""
    __tablename__ = "cca_oral_therapy_prescriptions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    regimen_id = Column(Integer, ForeignKey("cca_regimens.id"), nullable=True)
    drug = Column(String(200), nullable=False)
    formulation_strength = Column(String(100), nullable=True)  # clinician-typed, e.g. "150mg capsule" -- never dose-calculated
    dose_basis = Column(String(30), nullable=True)  # reference only: fixed, mg_kg, mg_m2, auc
    final_prescribed_dose = Column(String(200), nullable=True)  # clinician-typed, never computed
    frequency = Column(String(100), nullable=True)
    schedule_pattern = Column(String(100), nullable=True)  # Continuous, Cyclical, Intermittent, Loading then Maintenance
    cycle_length_days = Column(Integer, nullable=True)
    days_on_off = Column(String(50), nullable=True)  # e.g. "14 on / 7 off"
    start_date = Column(Date, nullable=True)
    planned_duration_or_cycles = Column(String(100), nullable=True)
    food_instruction = Column(String(100), nullable=True)
    handling_precautions = Column(Text, nullable=True)
    # Reference spec calls this out explicitly: a structured, drug-specific instruction,
    # never blank and never generic (ORL-020) -- enforced as required at sign time, not at
    # draft time, so a clinician can still start drafting before it's decided.
    missed_dose_instruction = Column(Text, nullable=True)
    vomited_dose_instruction = Column(Text, nullable=True)
    # DRAFT -> SIGNED -> DISPENSED -> ACTIVE -> ON_HOLD / DISCONTINUED / COMPLETED
    status = Column(String(30), default="DRAFT")
    supersedes_id = Column(Integer, ForeignKey("cca_oral_therapy_prescriptions.id"), nullable=True)
    signer_email = Column(String(200), nullable=True)
    signer_role = Column(String(50), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class OralTherapyCounselling(Base):
    """Oral Therapy Counselling (SCR-ORL-002) -- gates first dispensing (ORL-050), the same
    way PharmacyVerification gates PharmacyPreparation in the infusion pipeline. `checklist`
    is a list of {item, covered, material_version, understanding} dicts, one per counselling
    topic (dose/timing, food instruction, missed/vomited dose, storage, handling, side
    effects, red flags, monitoring, interactions, refill process, contact number,
    adherence) -- structured the same way PharmacyVerification/InfusionIndependentVerification
    already use a JSON checklist for a fixed set of attestations."""
    __tablename__ = "cca_oral_therapy_counsellings"
    id = Column(Integer, primary_key=True)
    prescription_id = Column(Integer, ForeignKey("cca_oral_therapy_prescriptions.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    checklist = Column(JSON, nullable=True)
    language = Column(String(50), nullable=True)
    interpreter_used = Column(Boolean, nullable=True)
    carer_present = Column(Boolean, nullable=True)
    adherence_aids_provided = Column(Text, nullable=True)
    counselled_by = Column(String(200))
    counselled_at = Column(DateTime, default=datetime.utcnow)


class OralTherapyDispensing(Base):
    """Dispensing & Refill (SCR-ORL-003). days_supply/returned_unused_quantity are
    pharmacist-typed values, never reconciled against a computed expected-remaining figure
    (reference spec's CALC-162 -- deliberately not ported, standing repo rule)."""
    __tablename__ = "cca_oral_therapy_dispensings"
    id = Column(Integer, primary_key=True)
    prescription_id = Column(Integer, ForeignKey("cca_oral_therapy_prescriptions.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    quantity_dispensed = Column(String(100), nullable=True)
    batch_lot = Column(String(100), nullable=True)
    expiry_date = Column(Date, nullable=True)
    days_supply = Column(String(50), nullable=True)  # pharmacist-typed, never computed
    dispensed_by = Column(String(200))
    checked_by = Column(String(200), nullable=True)
    collected_by = Column(String(200), nullable=True)
    returned_unused_quantity = Column(String(100), nullable=True)
    status = Column(String(30), default="Dispensed")
    dispensed_at = Column(DateTime, default=datetime.utcnow)


class OralTherapyReview(Base):
    """Adherence & Toxicity Review (SCR-ORL-004). adherence_narrative is the pharmacist/
    clinician's own written assessment, never a computed adherence percentage (reference
    spec's CALC-163 -- deliberately not ported)."""
    __tablename__ = "cca_oral_therapy_reviews"
    id = Column(Integer, primary_key=True)
    prescription_id = Column(Integer, ForeignKey("cca_oral_therapy_prescriptions.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    adherence_method = Column(String(100), nullable=True)  # Patient report, Pill count, Diary, Refill history, Electronic monitoring
    doses_reported_taken = Column(String(100), nullable=True)
    doses_missed = Column(String(100), nullable=True)
    doses_missed_reasons = Column(Text, nullable=True)
    adherence_narrative = Column(Text, nullable=True)
    toxicity_event_id = Column(Integer, ForeignKey("cca_toxicity_events.id"), nullable=True)
    monitoring_results_reviewed = Column(Text, nullable=True)
    # Continue Unchanged, Reduce, Interrupt, Restart, Discontinue -- a clinician's chosen
    # label, mirroring TreatmentOrder.dose_modification_percent's own documented reasoning.
    dose_decision = Column(String(50), nullable=True)
    next_review_date = Column(Date, nullable=True)
    reviewed_by = Column(String(200))
    reviewed_at = Column(DateTime, default=datetime.utcnow)


class OralTherapyHoldEvent(Base):
    """Oral Therapy Hold / Restart / Discontinue (SCR-ORL-005). Because the patient
    administers the drug themselves, a hold is not effective until they've actually been
    told -- patient_notification_status stays HOLD_NOT_COMMUNICATED until
    patient_contacted is recorded true (ORL-070), mirroring the reference spec's own
    reasoning without any computed logic involved."""
    __tablename__ = "cca_oral_therapy_hold_events"
    id = Column(Integer, primary_key=True)
    prescription_id = Column(Integer, ForeignKey("cca_oral_therapy_prescriptions.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    event_type = Column(String(30), nullable=False)  # Hold, Restart, Discontinue
    interruption_start_date = Column(Date, nullable=True)
    last_dose_taken_date = Column(Date, nullable=True)
    reason = Column(Text, nullable=False)
    restart_criteria = Column(Text, nullable=True)
    restart_date = Column(Date, nullable=True)
    restart_dose_label = Column(String(200), nullable=True)  # e.g. "Same dose" / "Reduced -- see new prescription version"
    remaining_supply_disposition = Column(String(100), nullable=True)
    patient_contacted = Column(Boolean, default=False)
    patient_notification_status = Column(String(30), default="HOLD_NOT_COMMUNICATED")  # HOLD_NOT_COMMUNICATED, COMMUNICATED
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Clinical Masters / Administration (Product 1 vs Product 2 gap report, Batch 10: C.26).
#
# The reference spec's 18 SCR-ADM-* screens all share one shape (identity/status/version/
# effective-dates/owner/change-reason + a repeatable-items table) -- rather than 18
# near-duplicate tables, this is one generic ClinicalMaster/ClinicalMasterItem pair keyed
# by a master_type discriminator, matching the pattern this codebase already uses for
# exactly this kind of "many small variants of one shape" problem (see
# OncologyRecordExtension's docstring in models_cca_oncology_ext.py).
#
# Deliberately NOT built here, all for the same reason (standing repo rule: no
# dose-calculation/threshold/rule-engine logic):
#   - Dose Modification & Rounding Rule Master (SCR-ADM-008) -- entirely a dose-threshold
#     rule engine, skipped outright rather than built hollow.
#   - The "Dose modification rules" and "Readiness rules" sub-tables of the Regimen Master
#     (SCR-ADM-005) and Treatment Readiness Rule Master (SCR-ADM-007) -- trigger/threshold/
#     action rule engines. Regimen identity fields already exist as Regimen/RegimenDrugLine.
#   - OAR constraint value/operator/threshold rows (SCR-ADM-011) and Alert/Escalation Rule
#     trigger-expression/hard-stop rows (SCR-ADM-017).
#   - User/Role/Permission Administration (SCR-ADM-001) -- already covered by this
#     codebase's existing RBAC (rbac_projection.py).
# ---------------------------------------------------------------------------

class ClinicalMaster(Base):
    """One master-data catalogue (Facility, Department, Clinician/Roster, Formulary, Lab
    Catalogue, Radiology Protocol, Surgery Template, Pathology Synoptic Template, Consent
    Template, Value Set, or Unit Normalisation -- see CLINICAL_MASTER_TYPES in
    routers/cca.py). Versioned the same way TreatmentPlan is: DRAFT -> PUBLISHED, with a
    new row (never an in-place edit to a published version) for any subsequent revision."""
    __tablename__ = "cca_clinical_masters"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    master_type = Column(String(50), nullable=False)
    name = Column(String(200), nullable=False)
    status = Column(String(30), default="DRAFT")  # DRAFT, PUBLISHED, RETIRED
    version = Column(Integer, default=1)
    supersedes_id = Column(Integer, ForeignKey("cca_clinical_masters.id"), nullable=True)
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)
    owner = Column(String(200), nullable=True)
    change_reason = Column(Text, nullable=True)
    published_by = Column(String(200), nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class ClinicalMasterItem(Base):
    """One repeatable row within a ClinicalMaster's items table (e.g. one location under a
    Facility Master, one drug under a Formulary Master). `fields` is a JSON blob rather
    than fixed columns because the repeatable-row schema differs per master_type -- a fixed
    schema per type would mean 11 more near-duplicate tables for the same reason
    ClinicalMaster itself avoids 18."""
    __tablename__ = "cca_clinical_master_items"
    id = Column(Integer, primary_key=True)
    master_id = Column(Integer, ForeignKey("cca_clinical_masters.id"), nullable=False)
    sequence_number = Column(Integer, default=1)
    fields = Column(JSON, nullable=False)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)
