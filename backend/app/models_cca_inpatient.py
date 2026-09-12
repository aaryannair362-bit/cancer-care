"""
Inpatient Oncology (C.21) -- Product 1 vs Product 2 gap report finding: no CCA-specific
representation at all (the largest single module gap found in the final gap-closing round).
The generic HMS ward module (models.py's Patient.ward/bed/admission_date, Ward, Vital,
NursingNote, DischargeSummary) has no live linkage to CCAPatient -- CCAPatient.hms_patient_id
exists but is never populated anywhere in this codebase (the same observation
CCAAppointmentCoordination's own docstring already makes about a different module) -- so,
following that same precedent, this module stays entirely within CCA's own patient identity
space rather than bridging to the base Patient table.

Consolidates the reference's ~17 Inpatient Oncology screens (admission/bed request,
oncology H&P, problem list, systemic-therapy-linked MAR, ward-round notes, nursing
flowsheet, deterioration/escalation, goals-of-care, transfer/handover, oncology discharge
summary, death documentation) into 11 focused tables hanging off one InpatientAdmission,
matching this codebase's own precedent for a similarly complex module (Day Care/MAR's
~10-table set).

No dose-calculation, dose-threshold, or other clinical-safety-check/scoring logic lives
here (standing repo rule) -- every field is a structured capture or a clinician's own typed
judgment (e.g. early_warning_score is whatever score a nurse read off a chart or calculated
by hand and typed in; this system never computes or aggregates one from recorded vitals),
never a computed clinical decision.
"""

from datetime import datetime
from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, JSON, String, Text
from .models import Base


class InpatientAdmission(Base):
    __tablename__ = "cca_inpatient_admissions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("cca_patients.id"), nullable=False)
    episode_id = Column(Integer, ForeignKey("cca_cancer_episodes.id"), nullable=True)
    admission_reason = Column(Text, nullable=False)
    admitting_diagnosis = Column(String(300), nullable=True)
    admission_type = Column(String(30), default="Elective")  # Elective, Emergency, Direct
    ward = Column(String(100), nullable=True)
    bed = Column(String(20), nullable=True)
    admitting_clinician = Column(String(200), nullable=True)
    status = Column(String(30), default="BED_REQUESTED")  # BED_REQUESTED, ADMITTED, TRANSFERRED, DISCHARGED, DECEASED
    admitted_at = Column(DateTime, nullable=True)
    discharged_at = Column(DateTime, nullable=True)
    requested_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class InpatientHistoryAndPhysical(Base):
    """Oncology-specific admission H&P -- distinct from a general ward H&P template because
    it foregrounds active-cancer-treatment context (current regimen, performance status)
    that template has no field for."""
    __tablename__ = "cca_inpatient_history_physicals"
    id = Column(Integer, primary_key=True)
    admission_id = Column(Integer, ForeignKey("cca_inpatient_admissions.id"), nullable=False)
    chief_complaint = Column(Text, nullable=False)
    history_of_present_illness = Column(Text, nullable=True)
    past_oncologic_history = Column(Text, nullable=True)
    current_treatment_summary = Column(Text, nullable=True)
    performance_status = Column(String(50), nullable=True)
    allergies = Column(Text, nullable=True)
    medications_on_admission = Column(JSON, nullable=True)
    physical_exam = Column(Text, nullable=True)
    assessment_and_plan = Column(Text, nullable=True)
    status = Column(String(30), default="DRAFT")  # DRAFT, SIGNED
    authored_by = Column(String(200))
    signed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class InpatientProblemListItem(Base):
    __tablename__ = "cca_inpatient_problem_list_items"
    id = Column(Integer, primary_key=True)
    admission_id = Column(Integer, ForeignKey("cca_inpatient_admissions.id"), nullable=False)
    problem = Column(String(300), nullable=False)
    status = Column(String(30), default="Active")  # Active, Resolved, Chronic
    priority = Column(String(20), default="Routine")  # Routine, Priority
    onset_date = Column(Date, nullable=True)
    resolved_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class InpatientMedicationAdministration(Base):
    """Bedside/inpatient administration record -- optionally linked to the patient's
    existing outpatient TreatmentOrder when the medication is their systemic cancer therapy
    continuing during this admission (reference's "systemic-therapy-linked MAR"), but also
    usable for any other inpatient medication. dose_administered is always the nurse's own
    typed record of what was actually given, never computed."""
    __tablename__ = "cca_inpatient_medication_administrations"
    id = Column(Integer, primary_key=True)
    admission_id = Column(Integer, ForeignKey("cca_inpatient_admissions.id"), nullable=False)
    treatment_order_id = Column(Integer, ForeignKey("cca_treatment_orders.id"), nullable=True)
    drug_name = Column(String(200), nullable=False)
    dose_administered = Column(String(100), nullable=True)
    route = Column(String(50), nullable=True)
    scheduled_time = Column(DateTime, nullable=True)
    administered_time = Column(DateTime, nullable=True)
    status = Column(String(30), default="Scheduled")  # Scheduled, Given, Held, Refused, Omitted
    hold_reason = Column(Text, nullable=True)
    administered_by = Column(String(200), nullable=True)
    witnessed_by = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class InpatientWardRoundNote(Base):
    __tablename__ = "cca_inpatient_ward_round_notes"
    id = Column(Integer, primary_key=True)
    admission_id = Column(Integer, ForeignKey("cca_inpatient_admissions.id"), nullable=False)
    round_datetime = Column(DateTime, default=datetime.utcnow)
    department = Column(String(100), nullable=True)  # Oncology, Palliative Care, ICU Liaison...
    subjective = Column(Text, nullable=True)
    objective = Column(Text, nullable=True)
    assessment = Column(Text, nullable=True)
    plan = Column(Text, nullable=True)
    performance_status_today = Column(String(50), nullable=True)
    author = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class InpatientNursingFlowsheet(Base):
    __tablename__ = "cca_inpatient_nursing_flowsheets"
    id = Column(Integer, primary_key=True)
    admission_id = Column(Integer, ForeignKey("cca_inpatient_admissions.id"), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    vitals = Column(JSON, nullable=True)  # {temp, heart_rate, bp_systolic, bp_diastolic, resp_rate, spo2}
    intake_output = Column(JSON, nullable=True)  # {oral_intake_ml, iv_intake_ml, urine_output_ml, other_output_ml}
    pain_score = Column(Integer, nullable=True)  # nurse-assessed 0-10, never computed
    mobility_status = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    recorded_by = Column(String(200))


class InpatientDeteriorationEvent(Base):
    """Deterioration/escalation -- early_warning_score is whatever score the nurse read off
    the chart or calculated by hand and typed in; this system never computes or aggregates a
    score from recorded vitals (standing repo rule against clinical-safety-threshold
    logic)."""
    __tablename__ = "cca_inpatient_deterioration_events"
    id = Column(Integer, primary_key=True)
    admission_id = Column(Integer, ForeignKey("cca_inpatient_admissions.id"), nullable=False)
    detected_at = Column(DateTime, default=datetime.utcnow)
    trigger = Column(Text, nullable=False)
    early_warning_score = Column(Integer, nullable=True)
    rapid_response_called = Column(Boolean, default=False)
    escalated_to = Column(String(200), nullable=True)
    escalation_time = Column(DateTime, nullable=True)
    response_action = Column(Text, nullable=True)
    outcome = Column(String(100), nullable=True)
    reported_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class InpatientGoalsOfCare(Base):
    __tablename__ = "cca_inpatient_goals_of_care"
    id = Column(Integer, primary_key=True)
    admission_id = Column(Integer, ForeignKey("cca_inpatient_admissions.id"), nullable=False)
    code_status = Column(String(50), nullable=True)  # Full Code, DNR, DNI, Comfort Care
    goals_discussed_with = Column(String(300), nullable=True)  # patient/family names present
    discussion_date = Column(Date, nullable=True)
    summary = Column(Text, nullable=True)
    documented_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class InpatientTransferHandover(Base):
    __tablename__ = "cca_inpatient_transfer_handovers"
    id = Column(Integer, primary_key=True)
    admission_id = Column(Integer, ForeignKey("cca_inpatient_admissions.id"), nullable=False)
    transfer_type = Column(String(50), nullable=True)  # Ward-to-Ward, ICU, Step-down, Discharge-pending
    from_location = Column(String(100), nullable=True)
    to_location = Column(String(100), nullable=True)
    handover_summary = Column(Text, nullable=False)
    handed_over_by = Column(String(200), nullable=True)
    received_by = Column(String(200), nullable=True)
    transfer_datetime = Column(DateTime, default=datetime.utcnow)


class InpatientDischargeSummary(Base):
    __tablename__ = "cca_inpatient_discharge_summaries"
    id = Column(Integer, primary_key=True)
    admission_id = Column(Integer, ForeignKey("cca_inpatient_admissions.id"), nullable=False)
    discharge_diagnosis = Column(Text, nullable=True)
    hospital_course = Column(Text, nullable=True)
    procedures_during_stay = Column(Text, nullable=True)
    medications_at_discharge = Column(JSON, nullable=True)
    follow_up_plan = Column(Text, nullable=True)
    pending_results = Column(Text, nullable=True)
    discharge_disposition = Column(String(50), nullable=True)  # Home, SNF, Hospice, Transferred, AMA
    condition_at_discharge = Column(String(100), nullable=True)
    status = Column(String(30), default="DRAFT")  # DRAFT, SIGNED
    signed_by = Column(String(200), nullable=True)
    signed_at = Column(DateTime, nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class InpatientDeathDocumentation(Base):
    __tablename__ = "cca_inpatient_death_documentation"
    id = Column(Integer, primary_key=True)
    admission_id = Column(Integer, ForeignKey("cca_inpatient_admissions.id"), nullable=False)
    date_of_death = Column(Date, nullable=False)
    time_of_death = Column(String(10), nullable=True)
    immediate_cause = Column(Text, nullable=True)
    contributing_factors = Column(Text, nullable=True)
    certifying_clinician = Column(String(200), nullable=True)
    family_notified = Column(Boolean, default=False)
    family_notified_by = Column(String(200), nullable=True)
    autopsy_requested = Column(Boolean, default=False)
    autopsy_consent = Column(Boolean, nullable=True)
    death_certificate_number = Column(String(100), nullable=True)
    created_by = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)
