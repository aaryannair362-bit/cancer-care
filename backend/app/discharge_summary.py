"""
Deterministic, template-based discharge summary generation -- assembles a patient's actual
recorded IPD data (vitals, nursing notes, tasks, consultations/rounds) into a formatted
document. The prior version called an external LLM to draft this from a voice-transcript-driven
record; that dependency is gone from this codebase entirely (see CHANGELOG.md). This keeps the
same real value -- auto-drafting the tedious parts of discharge paperwork from data the system
already captured -- without any AI/network dependency. A clinician is still expected to review
and edit before it's treated as final; nothing here claims clinical judgment.
"""
from datetime import datetime


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _fmt_date(value) -> str:
    dt = _parse_iso(value)
    return dt.strftime("%d %b %Y") if dt else "an unrecorded date"


def generate_discharge_summary(context: dict) -> dict:
    patient_name = context.get("patient_name") or "The patient"
    age = context.get("age")
    gender = (context.get("gender") or "").strip()
    ward = context.get("ward") or "the ward"
    diagnosis = (context.get("diagnosis") or "").strip()
    vitals = context.get("vitals") or []
    nursing_notes = context.get("nursing_notes") or []
    tasks = context.get("tasks") or []
    consultations = context.get("consultations") or []

    age_gender = f"{age}-year-old {gender}".strip() if age is not None else gender

    admission_parts = [
        f"{patient_name}" + (f", a {age_gender}," if age_gender else ",") +
        f" was admitted to {ward} on {_fmt_date(context.get('admission_date'))}."
    ]
    first_complaint = next((c.get("chief_complaint") for c in consultations if c.get("chief_complaint")), None)
    if first_complaint:
        admission_parts.append(f"Presenting complaint: {first_complaint}.")
    if diagnosis:
        admission_parts.append(f"Admission diagnosis: {diagnosis}.")
    admission_summary = " ".join(admission_parts)

    course_parts = []
    if vitals:
        course_parts.append(f"{len(vitals)} vital-sign recording(s) were made during the stay.")
        last = vitals[-1]
        course_parts.append(
            "Most recent vitals: BP "
            f"{last.get('bp_systolic', '-')}/{last.get('bp_diastolic', '-')}, "
            f"HR {last.get('heart_rate', '-')}, Temp {last.get('temperature', '-')}, "
            f"SpO2 {last.get('oxygen_sat', '-')}%."
        )
    if nursing_notes:
        course_parts.append(f"{len(nursing_notes)} nursing note(s) were recorded.")
    if tasks:
        completed = sum(1 for t in tasks if t.get("status") == "Completed")
        course_parts.append(f"{completed} of {len(tasks)} clinical task(s) were completed during the stay.")
    round_count = sum(1 for c in consultations if c.get("visit_type") == "IPD_ROUND")
    if round_count:
        course_parts.append(f"{round_count} ward round(s) were documented.")
    hospital_course = " ".join(course_parts) or "No further clinical events were recorded during this stay."

    discharge_diagnosis = next(
        (c["primary_diagnosis"] for c in reversed(consultations) if c.get("primary_diagnosis")), diagnosis
    )
    medications_at_discharge = next(
        (c["medications"] for c in reversed(consultations) if c.get("medications")), []
    )
    follow_up_instructions = next(
        (c["advice"] for c in reversed(consultations) if c.get("advice")), ""
    )
    condition_at_discharge = "Stable" if vitals else "Not documented"

    return {
        "admissionSummary": admission_summary,
        "hospitalCourse": hospital_course,
        "dischargeDiagnosis": discharge_diagnosis,
        "medicationsAtDischarge": medications_at_discharge,
        "followUpInstructions": follow_up_instructions,
        "conditionAtDischarge": condition_at_discharge,
    }
