"""
CCA Oncology OS -- Radiation Oncology, Surgical Oncology, and the Regimen library.

These three domains previously had no backend representation at all (the dashboard/
Next.js prototype modeled them client-side only, in localStorage). This module is
purely additive: new tables, created automatically by Base.metadata.create_all
(main.py) on both local SQLite and the deployed Postgres -- no migration file needed,
following backend/app/migrations.py's own documented rule that only a column added to
an *existing* table needs a migrations.py entry.

No dose-calculation, dose-threshold, or other clinical-safety-check logic lives here
(standing rule for this repo) -- every field below is a structured capture (a
clinician-entered value, a status, a linkage), never a computed clinical judgment.
"""

from datetime import datetime
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from .models import Base


class RadiationPrescription(Base):
    """A radiation oncologist's COURSE-level record, distinct from a Medical Oncology
    TreatmentPlan/TreatmentOrder (Oncology Review Results PDF item 2). Authorization to
    sign this course is gated the same way a TreatmentPlan is -- by the matching
    modality's oncologist (routers/cca.py's _require_modality_signer, reused here).

    Site/dose/fraction-count fields do NOT live here -- a course can contain more than one
    dose phase (PDF item 3, e.g. "Phase 1: Breast + Nodes, 40Gy/15#" then "Phase 2: Breast
    Cavity boost, 10Gy/5#"), each with its own site/dose/fractions and its own progression
    through the RT_SUB_STATUS_ORDER pipeline -- see CCARadiationPhase below. A single-phase
    course is simply a course with one CCARadiationPhase row; nothing here special-cases it.

    Old columns below (treatment_site through rt_sub_status) are retained, unused, nullable
    cruft from before this restructuring -- this table was never wired to any frontend page
    (confirmed dead: no real data depended on the old single-phase shape), so restructuring
    it was safe, but dropping columns outright isn't expressible as one portable statement
    across SQLite/Postgres (see migrations.py's own "additive-only" rule) and isn't worth a
    bespoke DROP COLUMN migration for columns nothing ever read.
    """
    __tablename__ = "cca_radiation_prescriptions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    mdt_case_id = Column(Integer, ForeignKey("cca_mdt_cases.id"), nullable=True)
    diagnosis = Column(String(255))
    intent = Column(String(50))
    modality = Column(String(100))
    technique = Column(String(100))
    concurrent_systemic_treatment = Column(Boolean, default=False)
    # Visible to the Radiation Technologist throughout fraction delivery (PDF item 21) --
    # kept course-level (not per-phase) since it's typically course-wide guidance (e.g. a
    # pacemaker precaution, a positioning note), not something that changes phase to phase.
    special_instructions = Column(Text, nullable=True)
    dicom_rt_plan_ref = Column(String(255), nullable=True)  # external planning/OIS system reference only
    signer_email = Column(String(200), nullable=True)
    signer_role = Column(String(50), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)
    # --- unused legacy columns, see class docstring ---
    treatment_site = Column(String(200), nullable=True)
    laterality = Column(String(20), nullable=True)
    treatment_phase = Column(String(100))
    total_prescribed_dose_gy = Column(Float, nullable=True)
    dose_per_fraction_gy = Column(Float, nullable=True)
    number_of_fractions = Column(Integer, nullable=True)
    frequency = Column(String(100))
    start_date = Column(Date, nullable=True)
    target_volumes = Column(JSON, nullable=True)
    organs_at_risk = Column(JSON, nullable=True)
    simulation_required = Column(Boolean, default=True)
    immobilization = Column(String(200), nullable=True)
    image_guidance_required = Column(Boolean, default=True)
    bolus = Column(String(100), nullable=True)
    rt_sub_status = Column(String(30), default="prescribed")


class CCARadiationPhase(Base):
    """One dose phase within a RadiationPrescription course (PDF item 3). Carries its own
    site/target, dose, and fraction count, and progresses independently through the same
    RT_SUB_STATUS_ORDER pipeline the course used to own directly -- matching real sequential
    RT practice (a boost phase is planned/approved only once the preceding phase is well
    underway or complete, not both phases planned as a single blob up front).

    physicist_signer_*/physician_signer_* are separate (not one shared signer field) because
    they are two different gated approvals in the pipeline (PDF item 20: RT planning/physics
    QA is the Physicist's action; final treatment approval is the Radiation Oncologist's) --
    collapsing them into one field would lose which of the two actually happened.
    """
    __tablename__ = "cca_radiation_phases"
    id = Column(Integer, primary_key=True)
    prescription_id = Column(Integer, ForeignKey("cca_radiation_prescriptions.id"), nullable=False)
    phase_number = Column(Integer, nullable=False)
    label = Column(String(200), nullable=False)
    treatment_site = Column(String(200), nullable=False)
    laterality = Column(String(20), nullable=True)
    target_volumes = Column(JSON, nullable=True)
    organs_at_risk = Column(JSON, nullable=True)
    total_prescribed_dose_gy = Column(Float, nullable=False)
    dose_per_fraction_gy = Column(Float, nullable=False)
    number_of_fractions = Column(Integer, nullable=False)
    frequency = Column(String(100), nullable=True)
    simulation_required = Column(Boolean, default=True)
    immobilization = Column(String(200), nullable=True)
    image_guidance_required = Column(Boolean, default=True)
    bolus = Column(String(100), nullable=True)
    # Mirrors dashboard/lib/oncology/types.ts RtSubStatus / state-machine.ts RT_SUB_STATUSES:
    # prescribed -> simulation_pending -> simulation_complete -> contouring -> planning
    # -> physics_qa -> physician_approved -> treatment_ready -> on_treatment
    # -> interrupted -> completed
    rt_sub_status = Column(String(30), default="prescribed")
    physicist_signer_email = Column(String(200), nullable=True)
    physicist_signer_role = Column(String(50), nullable=True)
    physicist_signed_at = Column(DateTime, nullable=True)
    # Physics QA (Product 1 vs Product 2 gap report, Batch 4) -- Product 1's real physics_qa
    # is a single holistic decision + mandatory note; we decompose it into a small attestation
    # checklist (never a computed pass/fail) mirroring PharmacyVerification's own pattern.
    # Recording a decision does NOT itself move rt_sub_status (matches Product 1: rejecting
    # doesn't auto-revert) -- see record_physics_qa/transition_radiation_phase.
    physics_qa_checklist = Column(JSON, nullable=True)
    physics_qa_decision = Column(String(30), nullable=True)  # Approved, Rejected / Replan Required
    physics_qa_note = Column(Text, nullable=True)
    physics_qa_decided_by = Column(String(200), nullable=True)
    physics_qa_decided_at = Column(DateTime, nullable=True)
    physician_signer_email = Column(String(200), nullable=True)
    physician_signer_role = Column(String(50), nullable=True)
    physician_signed_at = Column(DateTime, nullable=True)
    physician_approval_note = Column(Text, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class RadiationFraction(Base):
    """One scheduled/delivered fraction of a CCARadiationPhase (PDF item 4). Links to the
    phase (not the course) it belongs to, since dose/fraction-count -- and therefore what
    "fraction 1 of N" even means -- is now a phase-level concept. interruption_reason and
    on_treatment_review_note exist here specifically because the dashboard prototype's
    equivalent fields were never written by any UI -- this is the real, persisted home for
    both."""
    __tablename__ = "cca_radiation_fractions"
    id = Column(Integer, primary_key=True)
    phase_id = Column(Integer, ForeignKey("cca_radiation_phases.id"), nullable=False)
    fraction_number = Column(Integer, nullable=False)
    scheduled_date = Column(Date, nullable=True)
    status = Column(String(20), default="scheduled")  # scheduled, delivered, missed, rescheduled
    delivered_dose_gy = Column(Float, nullable=True)
    interruption_reason = Column(Text, nullable=True)
    on_treatment_review_note = Column(Text, nullable=True)
    # PDF item 4's explicit "variance/toxicity where applicable" bullet -- distinct from
    # interruption_reason (why a fraction was skipped/rescheduled) and
    # on_treatment_review_note (the RO's periodic on-treatment review), this is what the
    # Radiation Technologist notes about THIS delivery (a setup variance, an observed
    # toxicity) at the point of delivery.
    variance_or_toxicity = Column(Text, nullable=True)
    # Product 1 vs Product 2 gap report, Batch 5 (RT Delivery) -- image guidance and setup
    # variance are two distinct real fields in Product 1's delivery record, not one merged
    # variance_or_toxicity string. verified_by is an independent second-check attestation,
    # deliberately separate from recorded_by (who performed the delivery).
    image_guidance_performed = Column(Boolean, nullable=True)
    setup_variation = Column(Text, nullable=True)
    verified_by = Column(String(200), nullable=True)
    # Product 1's rt_fraction_safety() computes and BLOCKS on a delivered-dose-vs-prescription
    # tolerance check and a projected-cumulative-vs-total-dose check -- standing repo rule
    # forbids porting that computation. This is the non-computed substitute: the RTT's own
    # attestation that the delivered dose matches what was prescribed, never a system
    # comparison. dose_mismatch_note is required only when the RTT flags a mismatch.
    dose_match_confirmed = Column(Boolean, nullable=True)
    dose_mismatch_note = Column(Text, nullable=True)
    recorded_by = Column(String(200), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)


class RadiationInterruption(Base):
    """A real, append-only interruption record for a CCARadiationPhase's on-treatment course
    (Product 1 vs Product 2 gap report, Batch 5) -- previously `interrupted` was a bare
    rt_sub_status flip with zero captured detail. Mirrors Product 1's real `interruptions`
    list: multiple interruptions can occur across a course, each with its own reason/category/
    compensation plan, and its own resume (end_at). Deliberately its own table, not a reuse
    of Day Care's TreatmentHoldEvent (a different workspace/workflow) and not a mere status
    flag."""
    __tablename__ = "cca_radiation_interruptions"
    id = Column(Integer, primary_key=True)
    phase_id = Column(Integer, ForeignKey("cca_radiation_phases.id"), nullable=False)
    reason = Column(Text, nullable=False)
    category = Column(String(50), nullable=True)  # Clinical/Operational, Toxicity/Condition, Machine Issue, Other
    start_at = Column(DateTime, default=datetime.utcnow)
    end_at = Column(DateTime, nullable=True)
    compensation_plan = Column(Text, nullable=True)
    recorded_by = Column(String(200), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)


class RadiationOnTreatmentVisit(Base):
    """The Radiation Oncologist's periodic On-Treatment Visit (Product 1 vs Product 2 gap
    report, Batch 5) -- a real, separate, multiple-per-course signed clinical review distinct
    from any single fraction's own notes (previously conflated into on_treatment_review_note,
    a single string on whichever fraction row happened to receive it)."""
    __tablename__ = "cca_radiation_otvs"
    id = Column(Integer, primary_key=True)
    phase_id = Column(Integer, ForeignKey("cca_radiation_phases.id"), nullable=False)
    after_fraction_number = Column(Integer, nullable=True)
    assessment = Column(Text, nullable=False)
    toxicity_summary = Column(Text, nullable=False)
    plan = Column(Text, nullable=False)
    weight_kg = Column(Float, nullable=True)
    performance_status = Column(String(50), nullable=True)
    signed_by = Column(String(200), nullable=True)
    signed_at = Column(DateTime, default=datetime.utcnow)


class SurgicalPlan(Base):
    """Surgical Oncology's treatment order/plan (PDF item 13). performed_procedure is a
    field distinct from `procedure` on purpose -- what was planned must never be silently
    overwritten by what actually happened; fed_back_to_mdt_case_id links post-op findings
    back into a (later) MDT case, closing the operative-findings-to-MDT feedback loop."""
    __tablename__ = "cca_surgical_plans"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    mdt_case_id = Column(Integer, ForeignKey("cca_mdt_cases.id"), nullable=True)
    procedure = Column(String(255), nullable=False)
    indication = Column(Text, nullable=True)
    intent = Column(String(50))
    anatomical_site = Column(String(200))
    laterality = Column(String(20), nullable=True)
    proposed_extent = Column(String(255), nullable=True)
    approach = Column(String(100), nullable=True)
    nodal_procedure = Column(String(200), nullable=True)
    reconstruction = Column(String(200), nullable=True)
    planned_date = Column(Date, nullable=True)
    priority = Column(String(30), nullable=True)
    pre_op_requirements = Column(Text, nullable=True)
    required_imaging_pathology = Column(Text, nullable=True)
    anaesthesia_clearance = Column(String(100), nullable=True)
    blood_requirement = Column(String(100), nullable=True)
    special_instructions = Column(Text, nullable=True)
    # recommended -> surgeon_reviewed -> planned -> pre_op_ready -> scheduled -> performed
    # -> post_op -> histopathology_available
    status = Column(String(30), default="recommended")
    performed_procedure = Column(Text, nullable=True)
    performed_date = Column(Date, nullable=True)
    histopathology_summary = Column(Text, nullable=True)
    fed_back_to_mdt_case_id = Column(Integer, ForeignKey("cca_mdt_cases.id"), nullable=True)
    signer_email = Column(String(200), nullable=True)
    signer_role = Column(String(50), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class SurgicalIntraOpMonitoring(Base):
    """Intra-operative Monitoring (Gap Analysis PDF item 24) -- vitals/status observations
    recorded at intervals during the operation, the OR analogue of Day Care's
    InfusionMonitoringObservation (models_cca.py). Pure documentation, never computed or
    thresholded."""
    __tablename__ = "cca_surgical_intraop_monitoring"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    surgical_plan_id = Column(Integer, ForeignKey("cca_surgical_plans.id"), nullable=False)
    observation_time = Column(DateTime, default=datetime.utcnow)
    vitals = Column(JSON, nullable=True)
    anaesthesia_status = Column(String(100), nullable=True)
    blood_loss_estimate = Column(String(50), nullable=True)
    fluids_given = Column(Text, nullable=True)
    events_complications = Column(Text, nullable=True)
    recorded_by = Column(String(200))
    recorded_at = Column(DateTime, default=datetime.utcnow)


class SurgicalOperativeNote(Base):
    """Intra-operative Notes (Gap Analysis PDF item 25) -- the full operative note, deliberately
    a separate table from SurgicalPlan.performed_procedure (a short summary field on the plan
    itself) so the plan's planned/performed fields are never overwritten by the fuller narrative
    recorded here."""
    __tablename__ = "cca_surgical_operative_notes"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    surgical_plan_id = Column(Integer, ForeignKey("cca_surgical_plans.id"), nullable=False)
    pre_op_diagnosis = Column(Text, nullable=True)
    post_op_diagnosis = Column(Text, nullable=True)
    procedure_performed = Column(Text, nullable=False)
    findings = Column(Text, nullable=True)
    technique = Column(Text, nullable=True)
    complications = Column(Text, nullable=True)
    closure = Column(Text, nullable=True)
    surgeon = Column(String(200), nullable=True)
    assistants = Column(Text, nullable=True)
    anaesthesia_type = Column(String(100), nullable=True)
    estimated_blood_loss = Column(String(50), nullable=True)
    authored_by = Column(String(200))
    authored_at = Column(DateTime, default=datetime.utcnow)


class SurgicalSpecimen(Base):
    """Specimen Labelling and Lab Handoff (Gap Analysis PDF item 26) -- chain-of-custody from
    OR to pathology lab. collected_by/handed_off_to/received_by_lab are the actual named
    individuals at each step, never inferred or defaulted."""
    __tablename__ = "cca_surgical_specimens"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    surgical_plan_id = Column(Integer, ForeignKey("cca_surgical_plans.id"), nullable=False)
    specimen_label = Column(String(200), nullable=False)
    specimen_type = Column(String(200), nullable=True)
    site = Column(String(200), nullable=True)
    container_type = Column(String(100), nullable=True)
    fixative = Column(String(100), nullable=True)
    collected_by = Column(String(200), nullable=True)
    collected_at = Column(DateTime, nullable=True)
    handed_off_to = Column(String(200), nullable=True)
    handed_off_at = Column(DateTime, nullable=True)
    lab_accession_number = Column(String(100), nullable=True)
    received_by_lab = Column(String(200), nullable=True)
    received_at = Column(DateTime, nullable=True)
    status = Column(String(30), default="Collected")  # Collected, HandedOff, ReceivedByLab
    notes = Column(Text, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class SurgicalBloodTransfusion(Base):
    """Surgical Blood Transfusion Record (Gap Analysis PDF item 27) -- the same documentation
    shape as Day Care's BloodProductAdministration (models_cca.py), kept as its own table
    because the parent context here is a SurgicalPlan, not a TreatmentOrder: the two care
    settings (operating theatre vs. day-care infusion chair) stay independent so a change to
    one workflow can never affect the other."""
    __tablename__ = "cca_surgical_blood_transfusions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    surgical_plan_id = Column(Integer, ForeignKey("cca_surgical_plans.id"), nullable=False)
    product_type = Column(String(50), nullable=False)
    unit_id = Column(String(100), nullable=False)
    blood_group = Column(String(20), nullable=True)
    crossmatch_confirmed = Column(Boolean, default=False)
    crossmatch_reference = Column(String(200), nullable=True)
    volume = Column(String(100), nullable=True)
    indication = Column(Text, nullable=True)
    reaction_occurred = Column(Boolean, default=False)
    reaction_notes = Column(Text, nullable=True)
    administered_by = Column(String(200), nullable=True)
    administered_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class ClinicalProcedureNote(Base):
    """Procedures & Notes (Gap Analysis PDF items 31-33: Palliative, Medical Oncology, and
    Radiation Oncology Procedures & Notes) -- a bedside/outpatient procedure performed by a
    treating oncologist outside the operating theatre (e.g. bone marrow biopsy, lumbar
    puncture, nerve block, paracentesis, brachytherapy applicator placement), distinct from
    SurgicalOperativeNote above (which covers OR surgery under a SurgicalPlan). One shared
    table across all three specialties rather than three near-identical ones --
    performed_by_role records which specialty performed it, for display grouping only, never a
    permission boundary of its own (it's always the authenticated caller's own role, never
    entered independently)."""
    __tablename__ = "cca_clinical_procedure_notes"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    performed_by_role = Column(String(50), nullable=False)
    procedure_name = Column(String(255), nullable=False)
    indication = Column(Text, nullable=True)
    findings = Column(Text, nullable=True)
    technique = Column(Text, nullable=True)
    complications = Column(Text, nullable=True)
    performed_at = Column(DateTime, default=datetime.utcnow)
    performed_by = Column(String(200))
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class PalliativeTreatmentOrder(Base):
    """Palliative Treatment Orders (Gap Analysis PDF item 30) -- supportive/comfort-care orders
    (pain management, symptom control, ...), distinct from the oncology TreatmentOrder pipeline
    (models_cca.py), which is specifically the signed chemo/systemic-therapy administration
    chain feeding Day Care. `instructions` is free text authored by the clinician, the same
    reasoning TreatmentOrder.instructions already documents -- never a dose-calculation or
    other computed field (standing repo rule)."""
    __tablename__ = "cca_palliative_treatment_orders"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    order_type = Column(String(100), nullable=False)  # e.g. Pain Management, Symptom Control, Comfort Care
    instructions = Column(Text, nullable=False)
    status = Column(String(30), default="Draft")  # Draft, Signed, Active, Discontinued
    signer_email = Column(String(200), nullable=True)
    signer_role = Column(String(50), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    discontinued_reason = Column(Text, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class Regimen(Base):
    """Controlled regimen library (PDF item 6) -- a first-class clinical object, not a
    UI shortcut. Drug lines (below) carry no dose-calculation logic; `standard_protocol_dose`
    is a clinician-authored reference description, never computed here."""
    __tablename__ = "cca_regimens"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(255), nullable=False)
    cancer_indication = Column(String(255))
    intent_setting = Column(String(100))
    schedule = Column(String(200))
    number_of_cycles = Column(Integer, nullable=True)
    premedications = Column(Text, nullable=True)
    hydration = Column(Text, nullable=True)
    supportive_therapy = Column(Text, nullable=True)
    hold_parameters = Column(Text, nullable=True)
    reference_notes = Column(Text, nullable=True)
    version = Column(String(30), default="1.0")
    effective_date = Column(Date, nullable=True)
    approved_by = Column(String(200), nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class RegimenDrugLine(Base):
    __tablename__ = "cca_regimen_drug_lines"
    id = Column(Integer, primary_key=True)
    regimen_id = Column(Integer, ForeignKey("cca_regimens.id"), nullable=False)
    sequence_number = Column(Integer, default=1)
    generic_name = Column(String(200), nullable=False)
    dose_basis = Column(String(30), nullable=True)  # fixed, mg_kg, mg_m2, auc
    standard_protocol_dose = Column(String(200), nullable=True)  # descriptive reference text, not computed
    route = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)


class TreatmentOrderDrugLine(Base):
    """Structured per-drug dosing panel on a TreatmentOrder (Product 1 vs Product 2 gap report,
    Batch 1: "Systemic Treatment/Chemotherapy Orders" -- regimen-driven order lines, supportive
    care, five-value dosing panel). Auto-seeded from the parent order's TreatmentPlan.regimen's
    RegimenDrugLines at order creation, then clinician-edited before signing.
    standard_protocol_dose is copied reference text from the regimen line (what the library
    says); planned_dose is what the clinician actually orders for this cycle -- both are
    clinician-authored strings, never computed, matching RegimenDrugLine.standard_protocol_dose's
    own documented reasoning (standing repo rule: no dose-calculation logic)."""
    __tablename__ = "cca_treatment_order_drug_lines"
    id = Column(Integer, primary_key=True)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=False)
    sequence_number = Column(Integer, default=1)
    generic_name = Column(String(200), nullable=False)
    category = Column(String(30), default="Antineoplastic")  # Premedication, Antineoplastic, Other
    dose_basis = Column(String(30), nullable=True)  # reference only, e.g. fixed, mg_kg, mg_m2, auc
    standard_protocol_dose = Column(String(200), nullable=True)  # reference text copied from the regimen line
    planned_dose = Column(String(200), nullable=True)  # clinician-typed for this specific order, never computed
    route = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class TreatmentPlanPhase(Base):
    """TreatmentPlan (models_cca.py) is one row per plan with a single `modality` string --
    it has no concept of sequenced phases (PDF item 4: "Neoadjuvant systemic therapy ->
    Surgery -> Adjuvant RT -> Endocrine therapy"). Rather than add a column to that
    existing, live table, phases live in this new, purely additive child table instead."""
    __tablename__ = "cca_treatment_plan_phases"
    id = Column(Integer, primary_key=True)
    treatment_plan_id = Column(Integer, ForeignKey("cca_treatment_plans.id"), nullable=False)
    sequence = Column(Integer, default=1)
    modality = Column(String(30), nullable=False)  # systemic, radiation, surgical, combined_modality, supportive
    label = Column(String(200), nullable=False)
    regimen_or_procedure_ref = Column(String(255), nullable=True)
    planned_start = Column(Date, nullable=True)
    duration_description = Column(String(200), nullable=True)
    status = Column(String(30), default="draft")  # dashboard's unified TreatmentStatus vocabulary
    responsible_clinician_name = Column(String(200), nullable=True)
    responsible_clinician_role = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class OncologyRecordExtension(Base):
    """A handful of descriptive dashboard-model fields (e.g. ToxicityEvent's
    relationship-to-therapy/outcome/intervention, ResponseAssessment's disease status,
    ConsentRecord's discussed-topics/document-title) have no column on their corresponding
    existing backend table, and adding one to a live, shared table for a handful of
    UI-descriptive fields isn't worth the risk. This single additive table holds that
    supplementary payload for any backend entity, keyed by (entity_table, entity_id) --
    one small extension point instead of a bespoke new table per entity."""
    __tablename__ = "cca_oncology_record_extensions"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    entity_table = Column(String(100), nullable=False)  # e.g. "cca_toxicity_events"
    entity_id = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=True)
    updated_by = Column(String(200), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)
