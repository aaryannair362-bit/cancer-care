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

class ClinicalFact(Base):
    __tablename__ = "cca_clinical_facts"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("cca_documents.id"), nullable=True)
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
    report_status = Column(String(30), default="Draft")  # Draft|Finalized -- "No autonomous final report"
    finalized_by = Column(String(200), nullable=True)
    finalized_at = Column(DateTime, nullable=True)
    critical_acknowledged_by = Column(String(200), nullable=True)
    critical_acknowledged_at = Column(DateTime, nullable=True)

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
    status = Column(String(30), default="FINAL")
    recorded_by = Column(String(200))
    recorded_at = Column(DateTime, default=datetime.utcnow)

class CarePlan(Base):
    __tablename__ = "cca_care_plans"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    intent = Column(String(100), default="Curative")
    goals = Column(JSON)
    components = Column(JSON)  # systemic, surgical, radiation, supportive
    monitoring_plan = Column(JSON)
    follow_up_plan = Column(JSON)
    next_decision_point = Column(String(255))
    version_no = Column(Integer, default=1)
    status = Column(String(30), default="ACTIVE")  # DRAFT, ACTIVE, SUSPENDED, COMPLETED
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
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)

class CarePlanTask(Base):
    __tablename__ = "cca_care_plan_tasks"
    id = Column(Integer, primary_key=True)
    care_plan_id = Column(Integer, ForeignKey("cca_care_plans.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    description = Column(Text, nullable=False)
    owner_id = Column(String(100), nullable=False)
    owner_name = Column(String(200), nullable=False)
    due_date = Column(DateTime, nullable=False)
    status = Column(String(30), default="OPEN")  # OPEN, ACKNOWLEDGED, RESOLVED, ESCALATED
    created_at = Column(DateTime, default=datetime.utcnow)

class TreatmentPlan(Base):
    __tablename__ = "cca_treatment_plans"
    id = Column(Integer, primary_key=True)
    care_plan_id = Column(Integer, ForeignKey("cca_care_plans.id"), nullable=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    modality = Column(String(100), default="Systemic Chemotherapy")
    protocol_name = Column(String(200), default="AC-T (Doxorubicin/Cyclophosphamide followed by Paclitaxel)")
    planned_sessions = Column(Integer, default=8)
    completed_sessions = Column(Integer, default=0)
    start_date = Column(Date, default=datetime.utcnow)
    status = Column(String(30), default="ACTIVE")  # PLANNED, ACTIVE, COMPLETED, STOPPED
    created_at = Column(DateTime, default=datetime.utcnow)

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
