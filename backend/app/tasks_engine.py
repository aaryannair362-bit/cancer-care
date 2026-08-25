"""
Turns a doctor's finalized consultation (OPD visit or IPD ward round) directly into the
nurse's task list -- no manual task-creation step required for anything the consultation
already ordered. Also aggregates a patient's whole active medication regimen (across every
OPD consult and IPD round linked to them) so drug-interaction checks see the full picture,
not just what's being prescribed today.
"""

from datetime import datetime

from .models import Task, Patient, Consultation, NurseAssignment


def _format_medication(med: dict) -> str:
    drug = med.get("drugName") or "Unknown drug"
    parts = [drug]
    for key in ("dose", "route", "frequency", "duration"):
        val = med.get(key)
        if val:
            parts.append(str(val))
    return " - ".join(parts)


def generate_tasks_from_consultation(db, consultation: Consultation, created_by_user_id: int) -> list:
    """
    Idempotent per consultation: replaces only the previously auto-generated tasks tied to
    this consultation_id (never manually-created ones), so re-finalizing after an edit
    doesn't pile up duplicates.
    """
    if not consultation.patient_id:
        return []
    patient = db.query(Patient).filter(
        Patient.id == consultation.patient_id, Patient.status == "Active"
    ).first()
    if not patient:
        return []
    # Defense-in-depth against cross-tenant task injection (this codebase has had a
    # critical cross-tenant leak before, per CHANGELOG.md): never spawn tasks on a
    # patient outside the consultation's own organization, even if patient_id was
    # supplied unvalidated by whatever created the consultation.
    if consultation.organization_id and patient.organization_id != consultation.organization_id:
        return []

    db.query(Task).filter(
        Task.consultation_id == consultation.id, Task.source == "Auto"
    ).delete(synchronize_session=False)

    # Auto-created tasks must land on whichever nurse is actually assigned to this patient
    # right now -- left unassigned, only a HeadNurse can act on them (the Nurse-role
    # permission check on PATCH /api/ipd/tasks/{id} requires task.nurse_id == self), which
    # would silently block the bedside nurse caring for this patient from ever completing
    # the very tasks this pipeline exists to hand them.
    active_assignment = db.query(NurseAssignment).filter(
        NurseAssignment.patient_id == patient.id, NurseAssignment.status == "Active"
    ).first()
    assigned_nurse_id = active_assignment.nurse_id if active_assignment else None

    new_tasks = []
    for test in (consultation.lab_tests or []):
        test_name = test if isinstance(test, str) else (test.get("test") or test.get("name") if isinstance(test, dict) else None)
        if not test_name or not str(test_name).strip():
            continue
        new_tasks.append(Task(
            patient_id=patient.id, nurse_id=assigned_nurse_id, assigned_by=created_by_user_id,
            description=f"Lab: collect/perform {test_name}", status="Pending",
            task_type="Lab", source="Auto", consultation_id=consultation.id,
        ))

    for med in (consultation.medications or []):
        if not isinstance(med, dict) or med.get("discontinued") or not med.get("drugName"):
            continue
        new_tasks.append(Task(
            patient_id=patient.id, nurse_id=assigned_nurse_id, assigned_by=created_by_user_id,
            description=f"Administer: {_format_medication(med)}", status="Pending",
            task_type="Medication", source="Auto", consultation_id=consultation.id,
        ))

    advice = (consultation.advice or "").strip()
    if advice and consultation.visit_type == "IPD_ROUND":
        new_tasks.append(Task(
            patient_id=patient.id, nurse_id=assigned_nurse_id, assigned_by=created_by_user_id,
            description=f"Round follow-up: {advice}", status="Pending",
            task_type="Observation", source="Auto", consultation_id=consultation.id,
        ))

    for t in new_tasks:
        db.add(t)
    db.commit()
    for t in new_tasks:
        db.refresh(t)
    return new_tasks


def get_active_medications(db, patient_id: int) -> list:
    """
    Last-write-wins per drug name across every consultation linked to this patient, in
    chronological order; a later entry for the same drug name with "discontinued": true
    removes it from the active set rather than replacing it.
    """
    consultations = db.query(Consultation).filter(
        Consultation.patient_id == patient_id
    ).order_by(Consultation.created_at.asc(), Consultation.id.asc()).all()

    active = {}
    for c in consultations:
        for med in (c.medications or []):
            if not isinstance(med, dict):
                continue
            name = (med.get("drugName") or "").strip()
            if not name:
                continue
            key = name.lower()
            if med.get("discontinued"):
                active.pop(key, None)
            else:
                active[key] = med
    return list(active.values())


def compute_admission_day(patient: Patient) -> int:
    if not patient.admission_date:
        return 1
    delta = datetime.utcnow().date() - patient.admission_date.date()
    return max(delta.days + 1, 1)


# Curated starter set of well-known, clinically significant drug-drug interaction pairs --
# deliberately NOT a comprehensive clinical interaction database (there is no AI/LLM call
# backing this anymore, and building/licensing a real interaction database is a separate,
# much larger undertaking than this pass). Matched case-insensitively as a substring against
# each prescribed drug's name, so "Warfarin 5mg Tablet" still matches "warfarin". Flagged here,
# not silently presented as exhaustive: treat this as a safety-net for common, high-severity
# combinations, not a substitute for pharmacist review.
KNOWN_INTERACTIONS = [
    ("warfarin", "aspirin", "Severe", "Additive anticoagulant/antiplatelet effect increases bleeding risk", "Avoid combination; if unavoidable, monitor INR and watch closely for bleeding"),
    ("warfarin", "ibuprofen", "Severe", "NSAIDs increase bleeding risk and can displace warfarin from protein binding", "Avoid NSAIDs in patients on warfarin; consider paracetamol instead"),
    ("warfarin", "diclofenac", "Severe", "NSAIDs increase bleeding risk and can displace warfarin from protein binding", "Avoid NSAIDs in patients on warfarin; consider paracetamol instead"),
    ("aspirin", "ibuprofen", "Moderate", "Ibuprofen can interfere with aspirin's antiplatelet (cardioprotective) effect", "Separate dosing by several hours if both are clinically necessary"),
    ("methotrexate", "ibuprofen", "Severe", "NSAIDs reduce methotrexate renal clearance, raising toxicity risk", "Avoid NSAIDs with methotrexate, especially at higher methotrexate doses"),
    ("ace inhibitor", "spironolactone", "Severe", "Combined risk of dangerous hyperkalemia", "Monitor serum potassium closely; consider dose reduction"),
    ("enalapril", "spironolactone", "Severe", "Combined risk of dangerous hyperkalemia", "Monitor serum potassium closely; consider dose reduction"),
    ("lisinopril", "spironolactone", "Severe", "Combined risk of dangerous hyperkalemia", "Monitor serum potassium closely; consider dose reduction"),
    ("metformin", "contrast", "Moderate", "Risk of lactic acidosis if renal function is impaired after iodinated contrast", "Hold metformin around contrast studies per local renal-function protocol"),
    ("simvastatin", "clarithromycin", "Severe", "Strong CYP3A4 inhibition raises statin levels, risk of rhabdomyolysis", "Avoid combination or suspend statin during macrolide course"),
    ("simvastatin", "erythromycin", "Severe", "CYP3A4 inhibition raises statin levels, risk of rhabdomyolysis", "Avoid combination or suspend statin during macrolide course"),
    ("digoxin", "furosemide", "Moderate", "Diuretic-induced hypokalemia increases digoxin toxicity risk", "Monitor serum potassium and digoxin levels"),
    ("digoxin", "amiodarone", "Severe", "Amiodarone raises digoxin levels significantly", "Reduce digoxin dose and monitor levels closely"),
    ("ssri", "tramadol", "Severe", "Combined serotonergic effect raises serotonin syndrome risk", "Avoid combination or monitor closely for serotonin syndrome signs"),
    ("sertraline", "tramadol", "Severe", "Combined serotonergic effect raises serotonin syndrome risk", "Avoid combination or monitor closely for serotonin syndrome signs"),
    ("fluoxetine", "tramadol", "Severe", "Combined serotonergic effect raises serotonin syndrome risk", "Avoid combination or monitor closely for serotonin syndrome signs"),
    ("clopidogrel", "omeprazole", "Moderate", "Omeprazole (strong CYP2C19 inhibitor) can reduce clopidogrel's antiplatelet effect", "Consider pantoprazole or an H2-blocker instead"),
    ("phenytoin", "warfarin", "Moderate", "Complex bidirectional interaction affecting both drugs' metabolism", "Monitor INR and phenytoin levels closely on initiation/discontinuation"),
    ("insulin", "beta blocker", "Moderate", "Beta blockers can mask hypoglycemia symptoms", "Counsel patient on non-adrenergic hypoglycemia signs (sweating may still occur)"),
    ("metronidazole", "alcohol", "Moderate", "Disulfiram-like reaction (flushing, nausea, tachycardia)", "Advise complete alcohol avoidance during and 48h after treatment"),
]


def check_allergy_conflicts(allergies: list, medications: list) -> list:
    """
    Clinical Decision Support: flags a prescribed medication whose name contains (or is
    contained by) a patient-reported allergen -- same case-insensitive substring approach as
    check_drug_interactions below, for the same reason (no formulary-linked allergen taxonomy
    exists to match against exactly, so substring matching against free-text names is the
    honest, deterministic option available). Shared by OPD/IPD consultation finalize; called
    with the patient's Patient.allergies and the consultation's own medications list (not the
    full active regimen -- an allergy conflict matters for what's being newly prescribed here,
    unlike a drug-drug interaction which needs the whole active picture).
    """
    if not allergies or not medications:
        return []
    lowered_allergies = [(a, str(a).lower()) for a in allergies if a and str(a).strip()]
    if not lowered_allergies:
        return []

    conflicts = []
    for med in medications:
        if not isinstance(med, dict) or med.get("discontinued"):
            continue
        drug = (med.get("drugName") or "").strip()
        if not drug:
            continue
        low_drug = drug.lower()
        for allergy, low_allergy in lowered_allergies:
            if low_allergy in low_drug or low_drug in low_allergy:
                conflicts.append({
                    "drug": drug, "allergy": allergy,
                    "message": f"Patient has a reported allergy to '{allergy}', which conflicts with prescribed '{drug}'",
                })
    return conflicts


def check_drug_interactions(drug_names: list) -> list:
    """
    Shared by POST /api/drug-interactions (OPD, on-demand) and the IPD round/finalize flow
    (automatic, against the full active regimen). Static lookup against KNOWN_INTERACTIONS --
    no external AI/network call, deterministic, works offline.
    """
    names = [n for n in drug_names if n]
    if len(names) < 2:
        return []

    found = []
    lowered = [(n, n.lower()) for n in names]
    for i, (name_a, low_a) in enumerate(lowered):
        for name_b, low_b in lowered[i + 1:]:
            for term_a, term_b, severity, reason, recommendation in KNOWN_INTERACTIONS:
                match = (term_a in low_a and term_b in low_b) or (term_a in low_b and term_b in low_a)
                if match:
                    found.append({
                        "drug_pair": f"{name_a} - {name_b}",
                        "severity": severity,
                        "reason": reason,
                        "recommendation": recommendation,
                    })
    return found
