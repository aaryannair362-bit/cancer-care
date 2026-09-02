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
    """A radiation oncologist's prescription, distinct from a Medical Oncology
    TreatmentPlan/TreatmentOrder (PDF item 11). Authorization to move this prescription
    through its workflow is gated the same way a TreatmentPlan is -- by the matching
    modality's oncologist (routers/cca.py's _require_modality_signer, reused here)."""
    __tablename__ = "cca_radiation_prescriptions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    mdt_case_id = Column(Integer, ForeignKey("cca_mdt_cases.id"), nullable=True)
    diagnosis = Column(String(255))
    treatment_site = Column(String(200), nullable=False)
    laterality = Column(String(20), nullable=True)
    intent = Column(String(50))
    modality = Column(String(100))
    technique = Column(String(100))
    treatment_phase = Column(String(100))
    total_prescribed_dose_gy = Column(Float, nullable=False)
    dose_per_fraction_gy = Column(Float, nullable=False)
    number_of_fractions = Column(Integer, nullable=False)
    frequency = Column(String(100))
    start_date = Column(Date, nullable=True)
    concurrent_systemic_treatment = Column(Boolean, default=False)
    target_volumes = Column(JSON, nullable=True)
    organs_at_risk = Column(JSON, nullable=True)
    simulation_required = Column(Boolean, default=True)
    immobilization = Column(String(200), nullable=True)
    image_guidance_required = Column(Boolean, default=True)
    bolus = Column(String(100), nullable=True)
    special_instructions = Column(Text, nullable=True)
    dicom_rt_plan_ref = Column(String(255), nullable=True)  # external planning/OIS system reference only
    # Mirrors dashboard/lib/oncology/types.ts RtSubStatus / state-machine.ts RT_SUB_STATUSES:
    # prescribed -> simulation_pending -> simulation_complete -> contouring -> planning
    # -> physics_qa -> physician_approved -> treatment_ready -> on_treatment
    # -> interrupted -> completed
    rt_sub_status = Column(String(30), default="prescribed")
    signer_email = Column(String(200), nullable=True)
    signer_role = Column(String(50), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class RadiationFraction(Base):
    """One scheduled/delivered fraction of a RadiationPrescription's course (PDF item 12).
    interruption_reason and on_treatment_review_note exist here specifically because the
    dashboard prototype's equivalent fields were never written by any UI -- this is the
    real, persisted home for both."""
    __tablename__ = "cca_radiation_fractions"
    id = Column(Integer, primary_key=True)
    prescription_id = Column(Integer, ForeignKey("cca_radiation_prescriptions.id"), nullable=False)
    fraction_number = Column(Integer, nullable=False)
    scheduled_date = Column(Date, nullable=True)
    status = Column(String(20), default="scheduled")  # scheduled, delivered, missed, rescheduled
    delivered_dose_gy = Column(Float, nullable=True)
    interruption_reason = Column(Text, nullable=True)
    on_treatment_review_note = Column(Text, nullable=True)
    recorded_by = Column(String(200), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)


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
