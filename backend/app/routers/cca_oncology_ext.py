"""
CCA Oncology OS -- Radiation Oncology, Surgical Oncology, and the Regimen library
(PDF Master To-Do List items 6, 11, 12, 13), plus a demo-patient resolver used by the
dashboard/ (Next.js) client to attach its one hardcoded demo persona to a real,
organization-scoped CCAPatient row.

Sibling of routers/cca.py and routers/cca_coordination.py -- same prefix, same
tenancy/actor helpers, imported rather than redefined (the established pattern in this
codebase, see cca_coordination.py's own header).

No dose-calculation or clinical-safety-threshold logic here (standing repo rule) --
every write below is a structured capture or a workflow-sequencing transition, never a
computed clinical judgment.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import (
    get_current_user, is_admin, is_cca_radiation_physicist, is_cca_radiologist,
    is_cca_surgical_nurse, is_cca_surgical_oncologist, is_cca_medical_oncologist,
    is_cca_radiation_oncologist, is_cca_palliative_care_specialist,
)
from ..models_cca import CCAPatient, DomainEvent, MDTCase
from ..models_cca import ResponseAssessment, ToxicityEvent, TreatmentPlan
from ..models_cca_oncology_ext import (
    CCARadiationPhase, OncologyRecordExtension, RadiationFraction, RadiationPrescription,
    RadiationInterruption, RadiationOnTreatmentVisit,
    RadiationDiscrepancyRecord, RadiationPreTreatmentVerification,
    RadiationTreatmentUnit, RadiationEquipmentQARecord, RadiationEquipmentIssue,
    RadiationInVivoDosimetry, RadiationOncologyConsultation,
    Regimen, RegimenDrugLine, SurgicalPlan, TreatmentPlanPhase,
    SurgicalIntraOpMonitoring, SurgicalOperativeNote, SurgicalSpecimen, SurgicalBloodTransfusion,
    ClinicalProcedureNote, PalliativeTreatmentOrder,
)
from ..events import publish
from .cca import (
    _actor, _check_patient_in_org, _get_org_patient, _org_id,
    _require_clinical_or_nursing_role, _require_clinician, _require_modality_signer,
    get_cca_db,
)

router = APIRouter(prefix="/api/cca", tags=["CCA Oncology Extensions"])

DEMO_ONCOLOGY_MRN = "CCA-ONC-DEMO-001"

RT_SUB_STATUS_ORDER = [
    "prescribed", "simulation_pending", "simulation_complete", "contouring", "planning",
    "physics_qa", "physician_approved", "treatment_ready", "on_treatment", "completed",
]
# Which role predicate is authorized to drive a phase INTO each status (i.e. the check runs
# against the target status, not the current one) -- Oncology Review Results PDF item 20:
# RT planning (simulation through physics QA) is the Physicist's action; prescribing the
# phase and giving final treatment approval are the Radiation Oncologist's. Steps not listed
# (treatment_ready, on_treatment, completed) stay Radiation-Oncologist-gated, matching the
# original single-gate behavior for the parts of the pipeline that were never the gap here.
_RT_PHASE_STEP_ROLE = {
    "simulation_pending": "physicist", "simulation_complete": "physicist", "contouring": "physicist",
    "planning": "physicist", "physics_qa": "physicist", "physician_approved": "radiation_oncologist",
}
# Physics QA checklist (Product 1 vs Product 2 gap report, Batch 4) -- Product 1's real
# physics_qa is one holistic decision + a mandatory note; we decompose it into a small
# attestation checklist (never a computed pass/fail), mirroring PharmacyVerification's own
# pattern from Batch 2. The physicist confirms they personally reviewed each item.
_PHYSICS_QA_CHECKLIST_KEYS = [
    "prescription_plan_concordance", "dose_volume_constraint_review",
    "target_oar_coverage_review", "machine_deliverability_review",
]
_PHYSICS_QA_DECISIONS = ("Approved", "Rejected / Replan Required")
# Safety/dataflow-critical follow-up round (reference SCR-PHY-007/008).
_DISCREPANCY_SEVERITIES = ("Minor", "Major", "Critical")
# RT Delivery (Product 1 vs Product 2 gap report, Batch 5) -- Product 1's own interruption
# category is a free, unvalidated string; we validate against the gap report's own named
# categories to keep the field meaningful without inventing an unbacked taxonomy.
_INTERRUPTION_CATEGORIES = ("Clinical/Operational", "Toxicity/Condition", "Machine Issue", "Other")
_FRACTION_STATUSES = ("delivered", "missed", "rescheduled", "cancelled")


def _require_rt_delivery_or_ro(current_user: dict):
    """Interruption may be recorded/resumed by either the treating Radiation Oncologist or
    the Radiation Technologist at the machine (Product 1: rt_record_interruption is gated to
    either role). Radiation Technologist and Radiologist are the same login in this
    hospital's role structure (no separate CCARadiationTechnologist role exists), matching
    record_radiation_fraction_event's own gate below."""
    if is_cca_radiologist(current_user) or is_admin(current_user):
        return
    _require_modality_signer(current_user, "radiation")
SURGICAL_STATUS_ORDER = [
    "recommended", "surgeon_reviewed", "planned", "pre_op_ready", "scheduled", "performed",
    "post_op", "histopathology_available",
]


def _get_org_radiation_prescription(db: Session, prescription_id: int, org_id: int) -> RadiationPrescription:
    rx = db.query(RadiationPrescription).filter(RadiationPrescription.id == prescription_id).first()
    if not rx:
        raise HTTPException(404, "Radiation prescription not found")
    _check_patient_in_org(db, rx.patient_id, org_id)
    return rx


def _get_org_radiation_phase(db: Session, phase_id: int, org_id: int) -> tuple[CCARadiationPhase, RadiationPrescription]:
    phase = db.query(CCARadiationPhase).filter(CCARadiationPhase.id == phase_id).first()
    if not phase:
        raise HTTPException(404, "Radiation phase not found")
    rx = _get_org_radiation_prescription(db, phase.prescription_id, org_id)
    return phase, rx


def _require_rt_phase_step_signer(current_user: dict, target_status: str):
    """See _RT_PHASE_STEP_ROLE above. Admin never bypasses this (matching
    _require_modality_signer's own rule) -- Admin/Operations cannot author clinical steps,
    only observe."""
    who = _RT_PHASE_STEP_ROLE.get(target_status, "radiation_oncologist")
    if who == "physicist":
        if not is_cca_radiation_physicist(current_user):
            raise HTTPException(403, "Only the Radiation Physicist may perform this planning/physics step")
    else:
        _require_modality_signer(current_user, "radiation")


def _get_org_surgical_plan(db: Session, plan_id: int, org_id: int) -> SurgicalPlan:
    plan = db.query(SurgicalPlan).filter(SurgicalPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, "Surgical plan not found")
    _check_patient_in_org(db, plan.patient_id, org_id)
    return plan


def _require_surgical_team(current_user: dict):
    """Gate for the OR documentation items (Gap Analysis PDF items 24-27): the operating
    Surgical Oncologist or the Surgical Nurse present in theatre -- deliberately narrower than
    _require_clinical_or_nursing_role (which admits Day Care's Infusion Nurse and general
    Doctor, neither of whom document an operation)."""
    if not (is_cca_surgical_oncologist(current_user) or is_cca_surgical_nurse(current_user)):
        raise HTTPException(403, "Only the Surgical Oncologist or Surgical Nurse may perform this action")


def _rt_prescription_out(rx: RadiationPrescription) -> dict:
    return {
        "id": rx.id, "patient_id": rx.patient_id, "mdt_case_id": rx.mdt_case_id,
        "diagnosis": rx.diagnosis, "intent": rx.intent, "modality": rx.modality, "technique": rx.technique,
        "concurrent_systemic_treatment": rx.concurrent_systemic_treatment,
        "special_instructions": rx.special_instructions, "dicom_rt_plan_ref": rx.dicom_rt_plan_ref,
        "signer_email": rx.signer_email, "signer_role": rx.signer_role,
        "signed_at": rx.signed_at.isoformat() if rx.signed_at else None, "created_by": rx.created_by,
    }


def _rt_phase_out(p: CCARadiationPhase) -> dict:
    return {
        "id": p.id, "prescription_id": p.prescription_id, "phase_number": p.phase_number, "label": p.label,
        "treatment_site": p.treatment_site, "laterality": p.laterality,
        "target_volumes": p.target_volumes, "organs_at_risk": p.organs_at_risk,
        "total_prescribed_dose_gy": p.total_prescribed_dose_gy, "dose_per_fraction_gy": p.dose_per_fraction_gy,
        "number_of_fractions": p.number_of_fractions, "frequency": p.frequency,
        "simulation_required": p.simulation_required, "immobilization": p.immobilization,
        "image_guidance_required": p.image_guidance_required, "bolus": p.bolus,
        "rt_sub_status": p.rt_sub_status,
        "physicist_signer_email": p.physicist_signer_email, "physicist_signer_role": p.physicist_signer_role,
        "physicist_signed_at": p.physicist_signed_at.isoformat() if p.physicist_signed_at else None,
        "physics_qa_checklist": p.physics_qa_checklist, "physics_qa_decision": p.physics_qa_decision,
        "physics_qa_note": p.physics_qa_note, "physics_qa_waived_items": p.physics_qa_waived_items or [],
        "physics_qa_decided_by": p.physics_qa_decided_by,
        "physics_qa_decided_at": p.physics_qa_decided_at.isoformat() if p.physics_qa_decided_at else None,
        "physician_signer_email": p.physician_signer_email, "physician_signer_role": p.physician_signer_role,
        "physician_signed_at": p.physician_signed_at.isoformat() if p.physician_signed_at else None,
        "physician_approval_note": p.physician_approval_note,
        "created_by": p.created_by,
    }


def _rt_fraction_out(f: RadiationFraction) -> dict:
    return {
        "id": f.id, "phase_id": f.phase_id, "fraction_number": f.fraction_number,
        "scheduled_date": f.scheduled_date.isoformat() if f.scheduled_date else None,
        "treatment_unit_id": f.treatment_unit_id,
        "status": f.status, "delivered_dose_gy": f.delivered_dose_gy,
        "interruption_reason": f.interruption_reason, "on_treatment_review_note": f.on_treatment_review_note,
        "variance_or_toxicity": f.variance_or_toxicity,
        "image_guidance_performed": f.image_guidance_performed, "setup_variation": f.setup_variation,
        "verified_by": f.verified_by, "dose_match_confirmed": f.dose_match_confirmed,
        "dose_mismatch_note": f.dose_mismatch_note, "toxicity_event_id": f.toxicity_event_id,
        "recorded_by": f.recorded_by, "recorded_at": f.recorded_at.isoformat() if f.recorded_at else None,
    }


def _rt_interruption_out(i: RadiationInterruption) -> dict:
    return {
        "id": i.id, "phase_id": i.phase_id, "reason": i.reason, "category": i.category,
        "start_at": i.start_at.isoformat() if i.start_at else None,
        "end_at": i.end_at.isoformat() if i.end_at else None,
        "compensation_plan": i.compensation_plan,
        "recorded_by": i.recorded_by, "recorded_at": i.recorded_at.isoformat() if i.recorded_at else None,
    }


def _rt_otv_out(o: RadiationOnTreatmentVisit) -> dict:
    return {
        "id": o.id, "phase_id": o.phase_id, "after_fraction_number": o.after_fraction_number,
        "assessment": o.assessment, "toxicity_summary": o.toxicity_summary, "plan": o.plan,
        "weight_kg": o.weight_kg, "performance_status": o.performance_status,
        "signed_by": o.signed_by, "signed_at": o.signed_at.isoformat() if o.signed_at else None,
    }


def _surgical_plan_out(p: SurgicalPlan) -> dict:
    return {
        "id": p.id, "patient_id": p.patient_id, "mdt_case_id": p.mdt_case_id, "procedure": p.procedure,
        "indication": p.indication, "intent": p.intent, "anatomical_site": p.anatomical_site,
        "laterality": p.laterality, "proposed_extent": p.proposed_extent, "approach": p.approach,
        "nodal_procedure": p.nodal_procedure, "reconstruction": p.reconstruction,
        "planned_date": p.planned_date.isoformat() if p.planned_date else None, "priority": p.priority,
        "pre_op_requirements": p.pre_op_requirements, "required_imaging_pathology": p.required_imaging_pathology,
        "anaesthesia_clearance": p.anaesthesia_clearance, "blood_requirement": p.blood_requirement,
        "special_instructions": p.special_instructions, "status": p.status,
        "performed_procedure": p.performed_procedure,
        "performed_date": p.performed_date.isoformat() if p.performed_date else None,
        "histopathology_summary": p.histopathology_summary,
        "fed_back_to_mdt_case_id": p.fed_back_to_mdt_case_id,
        "signer_email": p.signer_email, "signer_role": p.signer_role,
        "signed_at": p.signed_at.isoformat() if p.signed_at else None, "created_by": p.created_by,
    }


def _regimen_out(r: Regimen, lines: list[RegimenDrugLine]) -> dict:
    return {
        "id": r.id, "name": r.name, "cancer_indication": r.cancer_indication,
        "intent_setting": r.intent_setting, "schedule": r.schedule, "number_of_cycles": r.number_of_cycles,
        "premedications": r.premedications, "hydration": r.hydration, "supportive_therapy": r.supportive_therapy,
        "hold_parameters": r.hold_parameters, "reference_notes": r.reference_notes, "version": r.version,
        "effective_date": r.effective_date.isoformat() if r.effective_date else None,
        "approved_by": r.approved_by, "created_by": r.created_by,
        "drug_lines": [
            {
                "id": l.id, "sequence_number": l.sequence_number, "generic_name": l.generic_name,
                "dose_basis": l.dose_basis, "standard_protocol_dose": l.standard_protocol_dose,
                "route": l.route, "notes": l.notes,
            }
            for l in sorted(lines, key=lambda l: l.sequence_number or 0)
        ],
    }


# ---------------------------------------------------------------------------
# Demo patient resolver
# ---------------------------------------------------------------------------

@router.get("/oncology-ext/demo-patient")
def get_or_create_demo_patient(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Idempotent get-or-create of the one demo patient the dashboard/ oncology module
    is seeded around (see dashboard/lib/oncology/seed-data.ts). Scoped to the caller's own
    organization -- never returns or creates a patient outside it -- so every demo role
    signing in against the same organization resolves to the same real, integer patient id
    with no manual seeding step."""
    org_id = _org_id(current_user)
    patient = db.query(CCAPatient).filter(
        CCAPatient.mrn == DEMO_ONCOLOGY_MRN, CCAPatient.organization_id == org_id,
    ).first()
    if not patient:
        patient = CCAPatient(
            mrn=DEMO_ONCOLOGY_MRN, name="Sunita Patil", age=52, sex="Female",
            journey_state="Medical Oncology", primary_oncologist="Dr. Sarah Varma (Medical Oncology)",
            organization_id=org_id, demo_flag=True,
        )
        db.add(patient)
        db.commit()
        db.refresh(patient)
    return {
        "status": "success",
        "patient": {
            "id": patient.id, "mrn": patient.mrn, "name": patient.name, "age": patient.age,
            "sex": patient.sex, "journey_state": patient.journey_state,
        },
    }


# ---------------------------------------------------------------------------
# Radiation Oncology
# ---------------------------------------------------------------------------

def _rt_consultation_out(c: RadiationOncologyConsultation) -> dict:
    return {
        "id": c.id, "patient_id": c.patient_id, "cied_present": c.cied_present, "cied_type": c.cied_type,
        "cied_management_plan": c.cied_management_plan, "prior_rt_received": c.prior_rt_received,
        "prior_rt_site": c.prior_rt_site, "prior_rt_summary": c.prior_rt_summary,
        "cumulative_prior_oar_dose_note": c.cumulative_prior_oar_dose_note,
        "contraindications_checklist": c.contraindications_checklist or {}, "contraindications_note": c.contraindications_note,
        "consulted_by": c.consulted_by, "consulted_at": c.consulted_at.isoformat() if c.consulted_at else None,
    }


@router.post("/patients/{patient_id}/radiation-consultations", status_code=201)
async def create_radiation_consultation(patient_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """RO Consultation (SCR-RO-002, gap review item 9) -- captures CIED/pacemaker status and
    prior-RT/re-irradiation history before a course can be prescribed; see
    create_radiation_prescription's gate below."""
    _require_modality_signer(current_user, "radiation")
    _get_org_patient(db, patient_id, _org_id(current_user))
    body = await request.json()
    cied_present = body.get("cied_present")
    if cied_present and not (body.get("cied_management_plan") or "").strip():
        raise HTTPException(422, "cied_management_plan is required when cied_present is true")
    consultation = RadiationOncologyConsultation(
        patient_id=patient_id, cied_present=cied_present, cied_type=body.get("cied_type"),
        cied_management_plan=body.get("cied_management_plan"), prior_rt_received=body.get("prior_rt_received"),
        prior_rt_site=body.get("prior_rt_site"), prior_rt_summary=body.get("prior_rt_summary"),
        cumulative_prior_oar_dose_note=body.get("cumulative_prior_oar_dose_note"),
        contraindications_checklist=body.get("contraindications_checklist"),
        contraindications_note=body.get("contraindications_note"), consulted_by=_actor(current_user),
    )
    db.add(consultation)
    db.commit()
    db.refresh(consultation)
    return {"status": "success", "consultation": _rt_consultation_out(consultation)}


@router.get("/patients/{patient_id}/radiation-consultations")
def list_radiation_consultations(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(RadiationOncologyConsultation).filter(RadiationOncologyConsultation.patient_id == patient_id).order_by(RadiationOncologyConsultation.id.desc()).all()
    return {"consultations": [_rt_consultation_out(c) for c in rows]}


@router.post("/radiation-prescriptions", status_code=201)
async def create_radiation_prescription(request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Creates the COURSE shell only -- site/dose/fractions are added per-phase via
    POST /radiation-prescriptions/{id}/phases below (Oncology Review Results PDF item 3:
    one course may contain more than one dose phase, each with its own target/dose/
    fractions; a single-phase course is just a course with one phase, no special-casing)."""
    _require_modality_signer(current_user, "radiation")
    body = await request.json()
    patient_id = body.get("patient_id")
    if patient_id is None:
        raise HTTPException(422, "patient_id is required")
    _get_org_patient(db, patient_id, _org_id(current_user))

    # RO Consultation gate (gap review item 9) -- a course cannot be prescribed until a
    # consultation has addressed CIED/pacemaker management, matching this codebase's other
    # precondition gates (e.g. Pre-Treatment Verification before a fraction can deliver).
    consultation = db.query(RadiationOncologyConsultation).filter(
        RadiationOncologyConsultation.patient_id == patient_id
    ).order_by(RadiationOncologyConsultation.id.desc()).first()
    if not consultation:
        raise HTTPException(409, "A Radiation Oncology Consultation (CIED status, prior-RT history) is required before prescribing a course")
    if consultation.cied_present and not consultation.cied_management_plan:
        raise HTTPException(409, "The patient's CIED management plan must be documented before prescribing a course")

    rx = RadiationPrescription(
        patient_id=patient_id, mdt_case_id=body.get("mdt_case_id"), diagnosis=body.get("diagnosis"),
        intent=body.get("intent"), modality=body.get("modality"), technique=body.get("technique"),
        concurrent_systemic_treatment=bool(body.get("concurrent_systemic_treatment", False)),
        special_instructions=body.get("special_instructions"), dicom_rt_plan_ref=body.get("dicom_rt_plan_ref"),
        signer_email=current_user.get("email"), signer_role=current_user.get("role"), signed_at=datetime.utcnow(),
        created_by=_actor(current_user),
    )
    db.add(rx)
    db.flush()
    publish(
        db, "RADIATION_PRESCRIPTION_CREATED", patient_id=patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Radiation course created", category="TREATMENT",
        description=f"{_actor(current_user)} started a radiation course for {rx.diagnosis or 'the patient'}.",
        prescription_id=rx.id,
    )
    db.commit()
    db.refresh(rx)
    return {"status": "success", "radiation_prescription": _rt_prescription_out(rx)}


@router.get("/patients/{patient_id}/radiation-prescriptions")
def list_radiation_prescriptions(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(RadiationPrescription).filter(RadiationPrescription.patient_id == patient_id).order_by(RadiationPrescription.id.desc()).all()
    return {"radiation_prescriptions": [_rt_prescription_out(r) for r in rows]}


@router.post("/radiation-prescriptions/{prescription_id}/phases", status_code=201)
async def create_radiation_phase(prescription_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Only the treating Radiation Oncologist prescribes a phase's site/dose/fractions --
    matches who was authorized to create the (now-removed) course-level dose fields."""
    _require_modality_signer(current_user, "radiation")
    rx = _get_org_radiation_prescription(db, prescription_id, _org_id(current_user))
    body = await request.json()
    for field in ("label", "treatment_site", "total_prescribed_dose_gy", "dose_per_fraction_gy", "number_of_fractions"):
        if body.get(field) in (None, ""):
            raise HTTPException(422, f"{field} is required")
    existing_count = db.query(CCARadiationPhase).filter(CCARadiationPhase.prescription_id == rx.id).count()

    phase = CCARadiationPhase(
        prescription_id=rx.id, phase_number=body.get("phase_number", existing_count + 1), label=body["label"],
        treatment_site=body["treatment_site"], laterality=body.get("laterality"),
        target_volumes=body.get("target_volumes"), organs_at_risk=body.get("organs_at_risk"),
        total_prescribed_dose_gy=body["total_prescribed_dose_gy"], dose_per_fraction_gy=body["dose_per_fraction_gy"],
        number_of_fractions=body["number_of_fractions"], frequency=body.get("frequency"),
        simulation_required=bool(body.get("simulation_required", True)), immobilization=body.get("immobilization"),
        image_guidance_required=bool(body.get("image_guidance_required", True)), bolus=body.get("bolus"),
        created_by=_actor(current_user),
    )
    db.add(phase)
    db.flush()
    publish(
        db, "RADIATION_PHASE_CREATED", patient_id=rx.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Radiation phase prescribed", category="TREATMENT",
        description=f"{_actor(current_user)} prescribed phase {phase.phase_number} ({phase.label}): "
                     f"{phase.total_prescribed_dose_gy} Gy / {phase.number_of_fractions} fractions to {phase.treatment_site}.",
        prescription_id=rx.id, phase_id=phase.id,
    )
    db.commit()
    db.refresh(phase)
    return {"status": "success", "phase": _rt_phase_out(phase)}


@router.get("/radiation-prescriptions/{prescription_id}/phases")
def list_radiation_phases(prescription_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    rx = _get_org_radiation_prescription(db, prescription_id, _org_id(current_user))
    rows = db.query(CCARadiationPhase).filter(CCARadiationPhase.prescription_id == rx.id).order_by(CCARadiationPhase.phase_number).all()
    return {"phases": [_rt_phase_out(p) for p in rows]}


@router.get("/radiation-phases/worklist")
def radiation_phase_worklist(status: str = None, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Cross-patient worklist for the Radiation Physicist (planning/physics-QA-pending
    phases) and Radiologist -- who also covers Radiation Technologist duties (treatment-ready/
    on-treatment phases) in this hospital's role structure -- these roles work off "what needs
    me next" across the whole organization's caseload, not one already-open patient."""
    org_id = _org_id(current_user)
    q = (
        db.query(CCARadiationPhase, RadiationPrescription, CCAPatient)
        .join(RadiationPrescription, CCARadiationPhase.prescription_id == RadiationPrescription.id)
        .join(CCAPatient, RadiationPrescription.patient_id == CCAPatient.id)
        .filter(CCAPatient.organization_id == org_id)
    )
    if status:
        q = q.filter(CCARadiationPhase.rt_sub_status == status)
    rows = q.order_by(CCARadiationPhase.created_at.desc()).all()
    return {"worklist": [
        {
            **_rt_phase_out(phase), "patient_id": patient.id, "patient_name": patient.name,
            "patient_mrn": patient.mrn, "special_instructions": rx.special_instructions,
        }
        for phase, rx, patient in rows
    ]}


@router.get("/radiation-phases/{phase_id}")
def get_radiation_phase(phase_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    phase, rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    return {"phase": _rt_phase_out(phase), "prescription": _rt_prescription_out(rx)}


@router.get("/radiation-phases/{phase_id}/physics-qa")
def get_physics_qa(phase_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    phase, _rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    return {
        "physics_qa": {
            "checklist": phase.physics_qa_checklist, "decision": phase.physics_qa_decision,
            "note": phase.physics_qa_note, "waived_items": phase.physics_qa_waived_items or [],
            "decided_by": phase.physics_qa_decided_by,
            "decided_at": phase.physics_qa_decided_at.isoformat() if phase.physics_qa_decided_at else None,
        }
    }


def _discrepancy_out(d: RadiationDiscrepancyRecord) -> dict:
    return {
        "id": d.id, "phase_id": d.phase_id, "category": d.category, "severity": d.severity,
        "description": d.description, "root_cause": d.root_cause, "resolution": d.resolution,
        "status": d.status, "raised_by": d.raised_by, "raised_at": d.raised_at.isoformat() if d.raised_at else None,
        "closed_by": d.closed_by, "closed_at": d.closed_at.isoformat() if d.closed_at else None,
    }


@router.get("/radiation-phases/{phase_id}/discrepancies")
def list_discrepancies(phase_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    phase, _rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    rows = db.query(RadiationDiscrepancyRecord).filter(RadiationDiscrepancyRecord.phase_id == phase.id).order_by(RadiationDiscrepancyRecord.id.desc()).all()
    return {"discrepancies": [_discrepancy_out(d) for d in rows]}


@router.post("/radiation-phases/{phase_id}/discrepancies", status_code=201)
async def raise_discrepancy(phase_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Physics QA Discrepancy Record (SCR-PHY-007) -- safety/dataflow-critical follow-up
    round. An OPEN discrepancy blocks record_physics_qa's Approved decision above."""
    if not is_cca_radiation_physicist(current_user):
        raise HTTPException(403, "Only the Radiation Physicist may raise a discrepancy")
    phase, rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    body = await request.json()
    category = (body.get("category") or "").strip()
    severity = body.get("severity")
    description = (body.get("description") or "").strip()
    if not category:
        raise HTTPException(422, "category is required")
    if severity not in _DISCREPANCY_SEVERITIES:
        raise HTTPException(422, f"severity must be one of {_DISCREPANCY_SEVERITIES}")
    if not description:
        raise HTTPException(422, "description is required")
    discrepancy = RadiationDiscrepancyRecord(
        phase_id=phase.id, category=category, severity=severity, description=description,
        root_cause=body.get("root_cause"), raised_by=_actor(current_user),
    )
    db.add(discrepancy)
    publish(
        db, "RADIATION_DISCREPANCY_RAISED", patient_id=rx.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Physics QA discrepancy raised", category="TREATMENT",
        description=f"{_actor(current_user)} raised a {severity} discrepancy on phase {phase.phase_number}: {category}.",
        prescription_id=rx.id, phase_id=phase.id,
    )
    db.commit()
    db.refresh(discrepancy)
    return {"status": "success", "discrepancy": _discrepancy_out(discrepancy)}


@router.post("/radiation-discrepancies/{id}/close")
async def close_discrepancy(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not is_cca_radiation_physicist(current_user):
        raise HTTPException(403, "Only the Radiation Physicist may close a discrepancy")
    discrepancy = db.query(RadiationDiscrepancyRecord).filter(RadiationDiscrepancyRecord.id == id).first()
    if not discrepancy:
        raise HTTPException(404, "Discrepancy not found")
    _get_org_radiation_phase(db, discrepancy.phase_id, _org_id(current_user))
    if discrepancy.status == "CLOSED":
        raise HTTPException(409, "This discrepancy is already closed")
    body = await request.json()
    resolution = (body.get("resolution") or "").strip()
    if not resolution:
        raise HTTPException(422, "resolution is required to close a discrepancy")
    discrepancy.resolution = resolution
    discrepancy.status = "CLOSED"
    discrepancy.closed_by = _actor(current_user)
    discrepancy.closed_at = datetime.utcnow()
    db.commit()
    db.refresh(discrepancy)
    return {"status": "success", "discrepancy": _discrepancy_out(discrepancy)}


@router.post("/radiation-phases/{phase_id}/physics-qa")
async def record_physics_qa(phase_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """The Radiation Physicist's actual Physics QA decision (Product 1 vs Product 2 gap
    report, Batch 4) -- previously the physics_qa step was a bare signature stamped by
    transition_radiation_phase with no decision, checklist, or note captured at all.

    Recording a decision here does NOT itself move rt_sub_status -- matches Product 1's real
    behavior (rejecting doesn't auto-revert the phase; staff must address the issue and a
    physicist re-submits). The actual forward transition to physician_approved is gated
    separately below on decision == 'Approved', which is the real safety-relevant gap this
    closes: today any Radiation Oncologist can approve a phase for treatment with physics QA
    never having been performed at all."""
    if not is_cca_radiation_physicist(current_user):
        raise HTTPException(403, "Only the Radiation Physicist may record a Physics QA decision")
    phase, rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    if phase.rt_sub_status != "physics_qa":
        raise HTTPException(409, f"Physics QA is not open for this phase (currently {phase.rt_sub_status})")

    body = await request.json()
    decision = body.get("decision")
    if decision not in _PHYSICS_QA_DECISIONS:
        raise HTTPException(422, f"decision must be one of {_PHYSICS_QA_DECISIONS}")
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(422, "A note is required to record a Physics QA decision")
    checklist = body.get("checklist") or {}
    waived_items = body.get("waived_items") or []
    if decision == "Approved":
        # Discrepancy Record gate (SCR-PHY-007) -- QA cannot pass while an open discrepancy
        # exists on this phase, safety/dataflow-critical follow-up round.
        open_discrepancy = db.query(RadiationDiscrepancyRecord).filter(
            RadiationDiscrepancyRecord.phase_id == phase.id, RadiationDiscrepancyRecord.status == "OPEN"
        ).first()
        if open_discrepancy:
            raise HTTPException(409, f"Cannot approve Physics QA while discrepancy #{open_discrepancy.id} is still OPEN")
        waived_keys = {w.get("item") for w in waived_items if w.get("item")}
        for w in waived_items:
            if not (w.get("item") and w.get("waived_by") and w.get("reason")):
                raise HTTPException(422, "Each waived item requires item, waived_by and reason")
        missing = [k for k in _PHYSICS_QA_CHECKLIST_KEYS if not checklist.get(k) and k not in waived_keys]
        if missing:
            raise HTTPException(422, f"All checklist items must be confirmed or explicitly waived to approve -- missing: {', '.join(missing)}")

    phase.physics_qa_checklist = checklist
    phase.physics_qa_decision = decision
    phase.physics_qa_note = note
    phase.physics_qa_waived_items = waived_items
    phase.physics_qa_decided_by = _actor(current_user)
    phase.physics_qa_decided_at = datetime.utcnow()
    publish(
        db, "RADIATION_PHYSICS_QA_RECORDED", patient_id=rx.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Physics QA recorded", category="TREATMENT",
        description=f"{_actor(current_user)} recorded Physics QA for phase {phase.phase_number} ({phase.label}): {decision}.",
        prescription_id=rx.id, phase_id=phase.id, decision=decision,
    )
    db.commit()
    db.refresh(phase)
    return {"status": "success", "phase": _rt_phase_out(phase)}


@router.post("/radiation-phases/{phase_id}/transition")
async def transition_radiation_phase(phase_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Structural sequencing only -- validates the target status is the very next step in
    RT_SUB_STATUS_ORDER, never a clinical judgment about whether the step is warranted.
    Each step is gated to the role actually authorized to perform it (PDF item 20: planning/
    physics QA steps are the Radiation Physicist's; prescribing and final approval are the
    Radiation Oncologist's) -- see _require_rt_phase_step_signer. `interrupted` is a
    side-state off `on_treatment`, not part of the main linear sequence -- a phase can be
    interrupted and resumed without that counting as forward progress."""
    phase, rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    body = await request.json()
    target = body.get("status")
    if target not in RT_SUB_STATUS_ORDER and target != "interrupted":
        raise HTTPException(422, f"status must be one of {[*RT_SUB_STATUS_ORDER, 'interrupted']}")

    if target == "interrupted":
        _require_rt_delivery_or_ro(current_user)
        if phase.rt_sub_status != "on_treatment":
            raise HTTPException(409, f"Cannot interrupt from {phase.rt_sub_status}")
        # Real, append-only interruption record (Batch 5) -- previously `interrupted` was a
        # bare status flip with zero captured detail (no reason, no category, no plan to
        # make up the missed treatment time).
        reason = (body.get("reason") or "").strip()
        if not reason:
            raise HTTPException(422, "reason is required to interrupt treatment")
        category = body.get("category") or "Clinical/Operational"
        if category not in _INTERRUPTION_CATEGORIES:
            raise HTTPException(422, f"category must be one of {_INTERRUPTION_CATEGORIES}")
        phase.rt_sub_status = "interrupted"
        db.add(RadiationInterruption(
            phase_id=phase.id, reason=reason, category=category,
            compensation_plan=body.get("compensation_plan"), recorded_by=_actor(current_user),
        ))
        publish(
            db, "RADIATION_PHASE_INTERRUPTED", patient_id=rx.patient_id, actor=_actor(current_user),
            role=current_user.get("role"), title="Radiation treatment interrupted", category="TREATMENT",
            description=f"{_actor(current_user)} interrupted phase {phase.phase_number} ({phase.label}): {reason}.",
            prescription_id=rx.id, phase_id=phase.id,
        )
        db.commit()
        db.refresh(phase)
        return {"status": "success", "phase": _rt_phase_out(phase)}
    if phase.rt_sub_status == "interrupted":
        _require_rt_delivery_or_ro(current_user)
        if target != "on_treatment":
            raise HTTPException(409, "An interrupted phase may only resume to on_treatment")
        open_interruption = db.query(RadiationInterruption).filter(
            RadiationInterruption.phase_id == phase.id, RadiationInterruption.end_at.is_(None)
        ).order_by(RadiationInterruption.id.desc()).first()
        if open_interruption:
            open_interruption.end_at = datetime.utcnow()
        phase.rt_sub_status = "on_treatment"
        db.commit()
        db.refresh(phase)
        return {"status": "success", "phase": _rt_phase_out(phase)}

    _require_rt_phase_step_signer(current_user, target)
    current_index = RT_SUB_STATUS_ORDER.index(phase.rt_sub_status) if phase.rt_sub_status in RT_SUB_STATUS_ORDER else -1
    target_index = RT_SUB_STATUS_ORDER.index(target)
    if target_index != current_index + 1:
        raise HTTPException(409, f"Cannot move from {phase.rt_sub_status} directly to {target}")
    # Real safety-relevant gap closed here (Batch 4): previously any Radiation Oncologist
    # could approve a phase for treatment even if Physics QA was never performed, or was
    # rejected -- see record_physics_qa above.
    if target == "physician_approved" and phase.physics_qa_decision != "Approved":
        raise HTTPException(409, "Cannot advance to physician approval until Physics QA has been Approved")
    phase.rt_sub_status = target
    if target == "physics_qa":
        phase.physicist_signer_email = current_user.get("email")
        phase.physicist_signer_role = current_user.get("role")
        phase.physicist_signed_at = datetime.utcnow()
    if target == "physician_approved":
        phase.physician_signer_email = current_user.get("email")
        phase.physician_signer_role = current_user.get("role")
        phase.physician_signed_at = datetime.utcnow()
        if body.get("note"):
            phase.physician_approval_note = body["note"]
    if target == "treatment_ready":
        # Always exactly `number_of_fractions` rows -- a schedule count that could drift
        # from the prescribed count is precisely the "screens show contradictory values"
        # failure item 26 exists to prevent. Changing the fraction count means amending
        # the phase, not overriding the schedule here.
        for n in range(1, phase.number_of_fractions + 1):
            db.add(RadiationFraction(phase_id=phase.id, fraction_number=n, status="scheduled"))
    publish(
        db, "RADIATION_PHASE_TRANSITIONED", patient_id=rx.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Radiation phase updated", category="TREATMENT",
        description=f"{_actor(current_user)} moved phase {phase.phase_number} ({phase.label}) to {target}.",
        prescription_id=rx.id, phase_id=phase.id, status=target,
    )
    db.commit()
    db.refresh(phase)
    return {"status": "success", "phase": _rt_phase_out(phase)}


@router.get("/radiation-phases/{phase_id}/fractions")
def list_radiation_fractions(phase_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    phase, _rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    rows = db.query(RadiationFraction).filter(RadiationFraction.phase_id == phase.id).order_by(RadiationFraction.fraction_number).all()
    return {"fractions": [_rt_fraction_out(f) for f in rows]}


@router.get("/radiation-phases/{phase_id}/cumulative-delivery")
def get_cumulative_delivery(phase_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Cumulative Delivery Dashboard (reference SCR-RTT-009, worklist/dashboard follow-up
    round) -- pure summation/bookkeeping over data that already exists (fraction count,
    each fraction's own delivered_dose_gy, the phase's own total_prescribed_dose_gy), never
    a tolerance/threshold comparison against those totals (standing repo rule)."""
    phase, rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    fractions = db.query(RadiationFraction).filter(RadiationFraction.phase_id == phase.id).order_by(RadiationFraction.fraction_number).all()
    delivered = [f for f in fractions if f.status == "delivered"]
    missed = [f for f in fractions if f.status == "missed"]
    cumulative_delivered_dose_gy = sum(f.delivered_dose_gy for f in delivered if f.delivered_dose_gy is not None)
    interruptions = db.query(RadiationInterruption).filter(RadiationInterruption.phase_id == phase.id).all()
    open_interruptions = [i for i in interruptions if i.end_at is None]
    return {
        "phase_id": phase.id, "patient_id": rx.patient_id, "phase_label": phase.label,
        "fractions_total": phase.number_of_fractions, "fractions_delivered": len(delivered),
        "fractions_missed": len(missed), "fractions_remaining": max(phase.number_of_fractions - len(delivered) - len(missed), 0),
        "total_prescribed_dose_gy": phase.total_prescribed_dose_gy,
        "cumulative_delivered_dose_gy": cumulative_delivered_dose_gy,
        "interruption_count": len(interruptions), "open_interruption_count": len(open_interruptions),
        "dose_mismatches": [f.id for f in delivered if f.dose_match_confirmed is False],
        "rt_sub_status": phase.rt_sub_status,
    }


@router.get("/radiation-phases/{phase_id}/interruptions")
def list_radiation_interruptions(phase_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    phase, _rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    rows = db.query(RadiationInterruption).filter(RadiationInterruption.phase_id == phase.id).order_by(RadiationInterruption.start_at.desc()).all()
    return {"interruptions": [_rt_interruption_out(i) for i in rows]}


@router.get("/radiation-phases/{phase_id}/otv")
def list_radiation_otv(phase_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    phase, _rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    rows = db.query(RadiationOnTreatmentVisit).filter(RadiationOnTreatmentVisit.phase_id == phase.id).order_by(RadiationOnTreatmentVisit.signed_at.desc()).all()
    return {"otv": [_rt_otv_out(o) for o in rows]}


@router.post("/radiation-phases/{phase_id}/otv", status_code=201)
async def record_radiation_otv(phase_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """The Radiation Oncologist's periodic On-Treatment Visit (Product 1 vs Product 2 gap
    report, Batch 5) -- a real, separate, multiple-per-course signed clinical review,
    distinct from any single fraction's own notes. Gated on at least one delivered fraction
    existing, matching Product 1's real precondition."""
    _require_modality_signer(current_user, "radiation")
    phase, rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    delivered = db.query(RadiationFraction).filter(
        RadiationFraction.phase_id == phase.id, RadiationFraction.status == "delivered"
    ).order_by(RadiationFraction.fraction_number.desc()).first()
    if not delivered:
        raise HTTPException(409, "At least one delivered fraction is required before an on-treatment visit can be recorded")

    body = await request.json()
    assessment = (body.get("assessment") or "").strip()
    toxicity_summary = (body.get("toxicity_summary") or "").strip()
    plan = (body.get("plan") or "").strip()
    if not (assessment and toxicity_summary and plan):
        raise HTTPException(422, "assessment, toxicity_summary and plan are all required")

    otv = RadiationOnTreatmentVisit(
        phase_id=phase.id, after_fraction_number=body.get("after_fraction_number", delivered.fraction_number),
        assessment=assessment, toxicity_summary=toxicity_summary, plan=plan,
        weight_kg=body.get("weight_kg"), performance_status=body.get("performance_status"),
        signed_by=_actor(current_user),
    )
    db.add(otv)
    db.flush()
    publish(
        db, "RADIATION_OTV_RECORDED", patient_id=rx.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="On-treatment visit recorded", category="TREATMENT",
        description=f"{_actor(current_user)} recorded an on-treatment visit for phase {phase.phase_number} ({phase.label}).",
        prescription_id=rx.id, phase_id=phase.id,
    )
    db.commit()
    db.refresh(otv)
    return {"status": "success", "otv": _rt_otv_out(otv)}


def _pretreatment_verification_out(v: RadiationPreTreatmentVerification) -> dict:
    return {
        "id": v.id, "fraction_id": v.fraction_id, "identity_reverified": bool(v.identity_reverified),
        "site_laterality_confirmed": bool(v.site_laterality_confirmed),
        "expected_fraction_number": v.expected_fraction_number, "confirmed_fraction_number": v.confirmed_fraction_number,
        "fraction_number_mismatch": bool(v.fraction_number_mismatch), "mismatch_note": v.mismatch_note,
        "verified_by": v.verified_by, "verified_at": v.verified_at.isoformat() if v.verified_at else None,
    }


@router.post("/radiation-fractions/{fraction_id}/pretreatment-verification", status_code=201)
async def record_pretreatment_verification(fraction_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Pre-Treatment Verification (reference SCR-RTT-002) -- the daily gate before a fraction
    may be recorded as delivered: identity/site re-check plus an explicit expected-vs-
    confirmed fraction-number check (RTT-020's hard stop is enforced here on a mismatch,
    unless explicitly overridden with a note -- a count comparison, not a computed dose
    check). Safety/dataflow-critical follow-up round."""
    if not (is_cca_radiologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiologist may record a pre-treatment verification")
    fraction = db.query(RadiationFraction).filter(RadiationFraction.id == fraction_id).first()
    if not fraction:
        raise HTTPException(404, "Radiation fraction not found")
    _get_org_radiation_phase(db, fraction.phase_id, _org_id(current_user))
    if fraction.status == "delivered":
        raise HTTPException(409, "This fraction is already delivered")
    body = await request.json()
    identity_reverified = bool(body.get("identity_reverified", False))
    site_confirmed = bool(body.get("site_laterality_confirmed", False))
    confirmed_number = body.get("confirmed_fraction_number")
    if confirmed_number is None:
        raise HTTPException(422, "confirmed_fraction_number is required")
    mismatch = int(confirmed_number) != fraction.fraction_number
    if mismatch and not (body.get("mismatch_note") or "").strip():
        raise HTTPException(422, "mismatch_note is required when confirmed_fraction_number does not match the expected fraction number")
    if not (identity_reverified and site_confirmed):
        raise HTTPException(422, "identity_reverified and site_laterality_confirmed must both be true before delivery")
    verification = RadiationPreTreatmentVerification(
        fraction_id=fraction.id, identity_reverified=identity_reverified, site_laterality_confirmed=site_confirmed,
        expected_fraction_number=fraction.fraction_number, confirmed_fraction_number=confirmed_number,
        fraction_number_mismatch=mismatch, mismatch_note=body.get("mismatch_note"), verified_by=_actor(current_user),
    )
    db.add(verification)
    db.commit()
    db.refresh(verification)
    return {"status": "success", "verification": _pretreatment_verification_out(verification)}


@router.post("/radiation-fractions/{fraction_id}/event")
async def record_radiation_fraction_event(fraction_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Recording an actual delivered/missed/rescheduled fraction is the Radiation
    Technologist's action (PDF item 20/21) -- previously any clinical/nursing role could do
    this, which is the actual gap those items describe. Radiation Technologist and Radiologist
    are the same login in this hospital's role structure (no separate CCARadiationTechnologist
    role exists), so this gates on CCARadiologist."""
    if not (is_cca_radiologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiologist may record a fraction delivery event")
    fraction = db.query(RadiationFraction).filter(RadiationFraction.id == fraction_id).first()
    if not fraction:
        raise HTTPException(404, "Radiation fraction not found")
    phase, rx = _get_org_radiation_phase(db, fraction.phase_id, _org_id(current_user))
    body = await request.json()
    status_value = body.get("status")
    if status_value not in _FRACTION_STATUSES:
        raise HTTPException(422, f"status must be one of {_FRACTION_STATUSES}")
    # Duplicate-delivery guard (Batch 5) -- previously an already-delivered fraction could be
    # silently re-posted and overwritten.
    if fraction.status == "delivered" and status_value == "delivered":
        raise HTTPException(409, f"Fraction {fraction.fraction_number} is already recorded as delivered")
    if status_value == "delivered":
        # Pre-Treatment Verification gate (reference SCR-RTT-002, safety/dataflow-critical
        # follow-up round) -- a fraction cannot be recorded delivered without the daily
        # identity/site/fraction-number check having been performed first.
        verified = db.query(RadiationPreTreatmentVerification).filter(
            RadiationPreTreatmentVerification.fraction_id == fraction.id
        ).order_by(RadiationPreTreatmentVerification.id.desc()).first()
        if not verified:
            raise HTTPException(409, "Pre-treatment verification is required before this fraction can be recorded as delivered")
        # Product 1's rt_fraction_safety() computes and blocks on a delivered-vs-prescribed
        # dose tolerance -- standing repo rule forbids that. dose_match_confirmed is the
        # non-computed substitute: the RTT's own attestation, never a system comparison.
        dose_match = body.get("dose_match_confirmed")
        if dose_match is False and not body.get("dose_mismatch_note"):
            raise HTTPException(422, "dose_mismatch_note is required when dose_match_confirmed is false")
        fraction.dose_match_confirmed = dose_match
        fraction.dose_mismatch_note = body.get("dose_mismatch_note")
        fraction.delivered_dose_gy = body.get("delivered_dose_gy", phase.dose_per_fraction_gy)
        fraction.image_guidance_performed = body.get("image_guidance_performed")
    fraction.status = status_value
    if body.get("interruption_reason"):
        fraction.interruption_reason = body["interruption_reason"]
    if body.get("on_treatment_review_note"):
        fraction.on_treatment_review_note = body["on_treatment_review_note"]
    if body.get("variance_or_toxicity"):
        fraction.variance_or_toxicity = body["variance_or_toxicity"]
    # RTT-observed toxicity now feeds the shared longitudinal ToxicityEvent record (reference
    # RTT-050) rather than sitting only in the free-text field above -- safety/dataflow-
    # critical follow-up round. term/grade are the RTT's own typed observation, never
    # computed; baseline_value is a fixed literal, matching every other ToxicityEvent writer
    # in this codebase (e.g. cca_seed.py's own seed data).
    toxicity_term = body.get("toxicity_term")
    toxicity_grade = body.get("toxicity_grade")
    if toxicity_term and toxicity_grade is not None:
        tox = ToxicityEvent(
            patient_id=rx.patient_id, term=toxicity_term, grade=int(toxicity_grade),
            baseline_value="Grade 0 (Baseline)",
        )
        db.add(tox)
        db.flush()
        fraction.toxicity_event_id = tox.id
    if body.get("setup_variation"):
        fraction.setup_variation = body["setup_variation"]
    if body.get("verified_by"):
        fraction.verified_by = body["verified_by"]
    fraction.recorded_by = _actor(current_user)
    fraction.recorded_at = datetime.utcnow()
    if phase.rt_sub_status == "treatment_ready":
        phase.rt_sub_status = "on_treatment"
    publish(
        db, "RADIATION_FRACTION_RECORDED", patient_id=rx.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Radiation fraction recorded", category="TREATMENT",
        description=f"{_actor(current_user)} recorded fraction {fraction.fraction_number} of phase {phase.phase_number} as {status_value}.",
        prescription_id=rx.id, phase_id=phase.id, fraction_id=fraction.id,
    )
    db.commit()
    db.refresh(fraction)
    return {"status": "success", "fraction": _rt_fraction_out(fraction)}


@router.post("/radiation-phases/{phase_id}/complete")
def complete_radiation_phase(phase_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_modality_signer(current_user, "radiation")
    phase, rx = _get_org_radiation_phase(db, phase_id, _org_id(current_user))
    delivered = db.query(RadiationFraction).filter(RadiationFraction.phase_id == phase.id, RadiationFraction.status == "delivered").count()
    if delivered < phase.number_of_fractions:
        raise HTTPException(409, f"Only {delivered} of {phase.number_of_fractions} fractions delivered")
    phase.rt_sub_status = "completed"
    publish(
        db, "RADIATION_PHASE_COMPLETED", patient_id=rx.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Radiation phase completed", category="TREATMENT",
        description=f"{_actor(current_user)} marked phase {phase.phase_number} ({phase.label}) complete.",
        prescription_id=rx.id, phase_id=phase.id,
    )
    db.commit()
    db.refresh(phase)
    return {"status": "success", "phase": _rt_phase_out(phase)}


# ---------------------------------------------------------------------------
# Surgical Oncology
# ---------------------------------------------------------------------------

@router.post("/surgical-plans", status_code=201)
async def create_surgical_plan(request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_clinician(current_user)
    body = await request.json()
    patient_id = body.get("patient_id")
    if patient_id is None:
        raise HTTPException(422, "patient_id is required")
    _get_org_patient(db, patient_id, _org_id(current_user))
    if not body.get("procedure"):
        raise HTTPException(422, "procedure is required")

    plan = SurgicalPlan(
        patient_id=patient_id, mdt_case_id=body.get("mdt_case_id"), procedure=body["procedure"],
        indication=body.get("indication"), intent=body.get("intent"), anatomical_site=body.get("anatomical_site"),
        laterality=body.get("laterality"), proposed_extent=body.get("proposed_extent"), approach=body.get("approach"),
        nodal_procedure=body.get("nodal_procedure"), reconstruction=body.get("reconstruction"),
        planned_date=body.get("planned_date"), priority=body.get("priority"),
        pre_op_requirements=body.get("pre_op_requirements"), required_imaging_pathology=body.get("required_imaging_pathology"),
        anaesthesia_clearance=body.get("anaesthesia_clearance"), blood_requirement=body.get("blood_requirement"),
        special_instructions=body.get("special_instructions"), created_by=_actor(current_user),
    )
    db.add(plan)
    db.flush()
    publish(
        db, "SURGICAL_PLAN_CREATED", patient_id=patient_id, actor=_actor(current_user), role=current_user.get("role"),
        title="Surgical plan created", category="TREATMENT",
        description=f"{_actor(current_user)} recommended {plan.procedure}.", plan_id=plan.id,
    )
    db.commit()
    db.refresh(plan)
    return {"status": "success", "surgical_plan": _surgical_plan_out(plan)}


@router.get("/patients/{patient_id}/surgical-plans")
def list_surgical_plans(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(SurgicalPlan).filter(SurgicalPlan.patient_id == patient_id).order_by(SurgicalPlan.id.desc()).all()
    return {"surgical_plans": [_surgical_plan_out(p) for p in rows]}


@router.patch("/surgical-plans/{plan_id}")
async def transition_surgical_plan(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_modality_signer(current_user, "surgical")
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    body = await request.json()
    target = body.get("status")
    if target not in SURGICAL_STATUS_ORDER:
        raise HTTPException(422, f"status must be one of {SURGICAL_STATUS_ORDER}")
    current_index = SURGICAL_STATUS_ORDER.index(plan.status) if plan.status in SURGICAL_STATUS_ORDER else -1
    target_index = SURGICAL_STATUS_ORDER.index(target)
    if target_index != current_index + 1:
        raise HTTPException(409, f"Cannot move from {plan.status} directly to {target}")
    plan.status = target
    if target == "planned":
        plan.signer_email = current_user.get("email")
        plan.signer_role = current_user.get("role")
        plan.signed_at = datetime.utcnow()
    publish(
        db, "SURGICAL_PLAN_TRANSITIONED", patient_id=plan.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Surgical plan updated", category="TREATMENT",
        description=f"{_actor(current_user)} moved the surgical plan to {target}.", plan_id=plan.id, status=target,
    )
    db.commit()
    db.refresh(plan)
    return {"status": "success", "surgical_plan": _surgical_plan_out(plan)}


@router.post("/surgical-plans/{plan_id}/performed")
async def record_surgical_outcome(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Records what was actually done, distinct from `procedure` (what was planned) --
    never overwrites the planned field. Optionally links histopathology findings forward
    into a later MDT case (fed_back_to_mdt_case_id), closing the operative-findings loop."""
    _require_modality_signer(current_user, "surgical")
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    if plan.status not in ("scheduled", "performed", "post_op"):
        raise HTTPException(409, f"Surgery must be Scheduled before recording an outcome (currently {plan.status})")
    body = await request.json()
    if not body.get("performed_procedure"):
        raise HTTPException(422, "performed_procedure is required")
    plan.performed_procedure = body["performed_procedure"]
    plan.performed_date = body.get("performed_date")
    if body.get("histopathology_summary"):
        plan.histopathology_summary = body["histopathology_summary"]
    if body.get("fed_back_to_mdt_case_id") is not None:
        mdt_case_id = body["fed_back_to_mdt_case_id"]
        target_case = db.query(MDTCase).filter(MDTCase.id == mdt_case_id).first()
        if not target_case:
            raise HTTPException(404, "Target MDT case not found")
        plan.fed_back_to_mdt_case_id = mdt_case_id
    plan.status = "histopathology_available" if plan.histopathology_summary else "post_op"
    publish(
        db, "SURGICAL_OUTCOME_RECORDED", patient_id=plan.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Surgical outcome recorded", category="TREATMENT",
        description=f"{_actor(current_user)} recorded the performed procedure and post-operative findings.",
        plan_id=plan.id,
    )
    db.commit()
    db.refresh(plan)
    return {"status": "success", "surgical_plan": _surgical_plan_out(plan)}


# ---------------------------------------------------------------------------
# Surgical Oncology OR documentation (Gap Analysis PDF items 24-27): Intra-operative
# Monitoring, Intra-operative Notes, Specimen Labelling and Lab Handoff, Surgical Blood
# Transfusion Record. All keyed off an existing SurgicalPlan; available from "scheduled"
# onward (surgery imminent/underway) through "post_op", never gated tighter than that -- same
# reasoning as Day Care's monitoring/hold/reaction endpoints not gating on TreatmentOrder.status.
# ---------------------------------------------------------------------------

def _intraop_monitoring_out(o: SurgicalIntraOpMonitoring) -> dict:
    return {
        "id": o.id, "patient_id": o.patient_id, "surgical_plan_id": o.surgical_plan_id,
        "observation_time": o.observation_time.isoformat(), "vitals": o.vitals,
        "anaesthesia_status": o.anaesthesia_status, "blood_loss_estimate": o.blood_loss_estimate,
        "fluids_given": o.fluids_given, "events_complications": o.events_complications,
        "recorded_by": o.recorded_by, "recorded_at": o.recorded_at.isoformat(),
    }


@router.get("/surgical-plans/{plan_id}/intraop-monitoring")
def list_intraop_monitoring(plan_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_surgical_team(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    rows = db.query(SurgicalIntraOpMonitoring).filter(
        SurgicalIntraOpMonitoring.surgical_plan_id == plan.id
    ).order_by(SurgicalIntraOpMonitoring.observation_time.asc()).all()
    return {"results": [_intraop_monitoring_out(o) for o in rows]}


@router.post("/surgical-plans/{plan_id}/intraop-monitoring", status_code=201)
async def record_intraop_monitoring(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_surgical_team(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    body = await request.json()
    record = SurgicalIntraOpMonitoring(
        patient_id=plan.patient_id, surgical_plan_id=plan.id, vitals=body.get("vitals"),
        anaesthesia_status=body.get("anaesthesia_status"), blood_loss_estimate=body.get("blood_loss_estimate"),
        fluids_given=body.get("fluids_given"), events_complications=body.get("events_complications"),
        recorded_by=_actor(current_user),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"status": "success", "observation": _intraop_monitoring_out(record)}


def _operative_note_out(n: SurgicalOperativeNote) -> dict:
    return {
        "id": n.id, "patient_id": n.patient_id, "surgical_plan_id": n.surgical_plan_id,
        "pre_op_diagnosis": n.pre_op_diagnosis, "post_op_diagnosis": n.post_op_diagnosis,
        "procedure_performed": n.procedure_performed, "findings": n.findings, "technique": n.technique,
        "complications": n.complications, "closure": n.closure, "surgeon": n.surgeon,
        "assistants": n.assistants, "anaesthesia_type": n.anaesthesia_type,
        "estimated_blood_loss": n.estimated_blood_loss,
        "authored_by": n.authored_by, "authored_at": n.authored_at.isoformat(),
    }


@router.get("/surgical-plans/{plan_id}/operative-notes")
def list_operative_notes(plan_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_surgical_team(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    rows = db.query(SurgicalOperativeNote).filter(
        SurgicalOperativeNote.surgical_plan_id == plan.id
    ).order_by(SurgicalOperativeNote.authored_at.desc()).all()
    return {"results": [_operative_note_out(n) for n in rows]}


@router.post("/surgical-plans/{plan_id}/operative-notes", status_code=201)
async def record_operative_note(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Deliberately never overwrites SurgicalPlan.performed_procedure -- this is the fuller
    narrative note, that field stays the short summary. See SurgicalOperativeNote's docstring."""
    _require_surgical_team(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    body = await request.json()
    if not body.get("procedure_performed"):
        raise HTTPException(422, "procedure_performed is required")

    actor = _actor(current_user)
    note = SurgicalOperativeNote(
        patient_id=plan.patient_id, surgical_plan_id=plan.id, pre_op_diagnosis=body.get("pre_op_diagnosis"),
        post_op_diagnosis=body.get("post_op_diagnosis"), procedure_performed=body["procedure_performed"],
        findings=body.get("findings"), technique=body.get("technique"), complications=body.get("complications"),
        closure=body.get("closure"), surgeon=body.get("surgeon"), assistants=body.get("assistants"),
        anaesthesia_type=body.get("anaesthesia_type"), estimated_blood_loss=body.get("estimated_blood_loss"),
        authored_by=actor,
    )
    db.add(note)
    db.flush()
    publish(
        db, "SURGICAL_OPERATIVE_NOTE_RECORDED", patient_id=plan.patient_id, actor=actor, role=current_user.get("role"),
        title="Operative note recorded", category="TREATMENT",
        description=f"{actor} recorded the operative note for {plan.procedure}.", plan_id=plan.id,
    )
    db.commit()
    db.refresh(note)
    return {"status": "success", "operative_note": _operative_note_out(note)}


def _specimen_out(s: SurgicalSpecimen) -> dict:
    return {
        "id": s.id, "patient_id": s.patient_id, "surgical_plan_id": s.surgical_plan_id,
        "specimen_label": s.specimen_label, "specimen_type": s.specimen_type, "site": s.site,
        "container_type": s.container_type, "fixative": s.fixative,
        "collected_by": s.collected_by, "collected_at": s.collected_at.isoformat() if s.collected_at else None,
        "handed_off_to": s.handed_off_to, "handed_off_at": s.handed_off_at.isoformat() if s.handed_off_at else None,
        "lab_accession_number": s.lab_accession_number, "received_by_lab": s.received_by_lab,
        "received_at": s.received_at.isoformat() if s.received_at else None, "status": s.status,
        "notes": s.notes,
    }


@router.get("/surgical-plans/{plan_id}/specimens")
def list_specimens(plan_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_surgical_team(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    rows = db.query(SurgicalSpecimen).filter(SurgicalSpecimen.surgical_plan_id == plan.id).order_by(SurgicalSpecimen.id.desc()).all()
    return {"results": [_specimen_out(s) for s in rows]}


@router.post("/surgical-plans/{plan_id}/specimens", status_code=201)
async def add_specimen(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_surgical_team(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    body = await request.json()
    label = (body.get("specimen_label") or "").strip()
    if not label:
        raise HTTPException(422, "specimen_label is required")

    actor = _actor(current_user)
    specimen = SurgicalSpecimen(
        patient_id=plan.patient_id, surgical_plan_id=plan.id, specimen_label=label,
        specimen_type=body.get("specimen_type"), site=body.get("site"), container_type=body.get("container_type"),
        fixative=body.get("fixative"), collected_by=actor, collected_at=datetime.utcnow(),
        notes=body.get("notes"), created_by=actor,
    )
    db.add(specimen)
    db.flush()
    publish(
        db, "SURGICAL_SPECIMEN_COLLECTED", patient_id=plan.patient_id, actor=actor, role=current_user.get("role"),
        title="Specimen collected", category="TREATMENT",
        description=f"{actor} labelled and collected specimen \"{label}\".", plan_id=plan.id,
    )
    db.commit()
    db.refresh(specimen)
    return {"status": "success", "specimen": _specimen_out(specimen)}


# Workflow-sequencing only -- chain-of-custody progression, not a clinical rule.
_SPECIMEN_TRANSITIONS = {
    "HandedOff": {"Collected"},
    "ReceivedByLab": {"HandedOff"},
}


@router.post("/specimens/{specimen_id}/event")
async def record_specimen_event(specimen_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Advances a specimen's chain-of-custody by one step. HandedOff requires handed_off_to;
    ReceivedByLab requires received_by_lab -- both the actual named recipient, never defaulted."""
    _require_surgical_team(current_user)
    org_id = _org_id(current_user)
    specimen = db.query(SurgicalSpecimen).filter(SurgicalSpecimen.id == specimen_id).first()
    if not specimen:
        raise HTTPException(404, "Specimen not found")
    _check_patient_in_org(db, specimen.patient_id, org_id)

    body = await request.json()
    target = body.get("status")
    allowed_from = _SPECIMEN_TRANSITIONS.get(target)
    if not allowed_from:
        raise HTTPException(422, f"status must be one of {sorted(_SPECIMEN_TRANSITIONS)}")
    if specimen.status not in allowed_from:
        raise HTTPException(409, f"Cannot move specimen from {specimen.status} to {target}")

    now = datetime.utcnow()
    if target == "HandedOff":
        if not body.get("handed_off_to"):
            raise HTTPException(422, "handed_off_to is required")
        specimen.handed_off_to = body["handed_off_to"]
        specimen.handed_off_at = now
    if target == "ReceivedByLab":
        if not body.get("received_by_lab"):
            raise HTTPException(422, "received_by_lab is required")
        specimen.received_by_lab = body["received_by_lab"]
        specimen.received_at = now
        if body.get("lab_accession_number"):
            specimen.lab_accession_number = body["lab_accession_number"]
    specimen.status = target
    db.flush()
    publish(
        db, "SURGICAL_SPECIMEN_" + target.upper(), patient_id=specimen.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title=f"Specimen {target}", category="TREATMENT",
        description=f"{_actor(current_user)} recorded specimen \"{specimen.specimen_label}\" as {target}.",
        plan_id=specimen.surgical_plan_id,
    )
    db.commit()
    db.refresh(specimen)
    return {"status": "success", "specimen": _specimen_out(specimen)}


def _surgical_blood_out(b: SurgicalBloodTransfusion) -> dict:
    return {
        "id": b.id, "patient_id": b.patient_id, "surgical_plan_id": b.surgical_plan_id,
        "product_type": b.product_type, "unit_id": b.unit_id, "blood_group": b.blood_group,
        "crossmatch_confirmed": b.crossmatch_confirmed, "crossmatch_reference": b.crossmatch_reference,
        "volume": b.volume, "indication": b.indication, "reaction_occurred": b.reaction_occurred,
        "reaction_notes": b.reaction_notes, "administered_by": b.administered_by,
        "administered_at": b.administered_at.isoformat() if b.administered_at else None,
    }


@router.get("/surgical-plans/{plan_id}/blood-transfusions")
def list_surgical_blood_transfusions(plan_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_surgical_team(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    rows = db.query(SurgicalBloodTransfusion).filter(
        SurgicalBloodTransfusion.surgical_plan_id == plan.id
    ).order_by(SurgicalBloodTransfusion.id.desc()).all()
    return {"results": [_surgical_blood_out(b) for b in rows]}


@router.post("/surgical-plans/{plan_id}/blood-transfusions", status_code=201)
async def record_surgical_blood_transfusion(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_surgical_team(current_user)
    plan = _get_org_surgical_plan(db, plan_id, _org_id(current_user))
    body = await request.json()
    product_type = (body.get("product_type") or "").strip()
    unit_id = (body.get("unit_id") or "").strip()
    if not product_type or not unit_id:
        raise HTTPException(422, "product_type and unit_id are required")

    actor = _actor(current_user)
    record = SurgicalBloodTransfusion(
        patient_id=plan.patient_id, surgical_plan_id=plan.id, product_type=product_type, unit_id=unit_id,
        blood_group=body.get("blood_group"), crossmatch_confirmed=bool(body.get("crossmatch_confirmed", False)),
        crossmatch_reference=body.get("crossmatch_reference"), volume=body.get("volume"),
        indication=body.get("indication"), reaction_occurred=bool(body.get("reaction_occurred", False)),
        reaction_notes=body.get("reaction_notes"), administered_by=actor, administered_at=datetime.utcnow(),
        created_by=actor,
    )
    db.add(record)
    db.flush()
    publish(
        db, "SURGICAL_BLOOD_TRANSFUSION_RECORDED", patient_id=plan.patient_id, actor=actor, role=current_user.get("role"),
        title="Surgical blood transfusion recorded", category="TREATMENT",
        description=f"{actor} recorded {product_type} unit {unit_id} transfused intra-operatively.",
        plan_id=plan.id,
    )
    db.commit()
    db.refresh(record)
    return {"status": "success", "blood_transfusion": _surgical_blood_out(record)}


# ---------------------------------------------------------------------------
# Procedures & Notes (Gap Analysis PDF items 31-33: Palliative, Medical Oncology, Radiation
# Oncology) and Palliative Treatment Orders (item 30). See ClinicalProcedureNote and
# PalliativeTreatmentOrder's docstrings for why these are separate from SurgicalOperativeNote
# and the chemo TreatmentOrder pipeline respectively.
# ---------------------------------------------------------------------------

_PROCEDURE_NOTE_ROLES = {
    "CCAMedicalOncologist": is_cca_medical_oncologist,
    "CCARadiationOncologist": is_cca_radiation_oncologist,
    "CCAPalliativeCareSpecialist": is_cca_palliative_care_specialist,
}


def _require_procedure_note_author(current_user: dict) -> str:
    """Any of the three specialties this item set covers may author their own procedure note --
    returns the caller's own role, always self-declared from the session, never entered
    independently (see ClinicalProcedureNote's docstring)."""
    role = current_user.get("role")
    if role not in _PROCEDURE_NOTE_ROLES or not _PROCEDURE_NOTE_ROLES[role](current_user):
        raise HTTPException(403, "Only the Medical Oncologist, Radiation Oncologist, or Palliative Care Specialist may record a procedure note")
    return role


def _procedure_note_out(n: ClinicalProcedureNote) -> dict:
    return {
        "id": n.id, "patient_id": n.patient_id, "performed_by_role": n.performed_by_role,
        "procedure_name": n.procedure_name, "indication": n.indication, "findings": n.findings,
        "technique": n.technique, "complications": n.complications,
        "performed_at": n.performed_at.isoformat(), "performed_by": n.performed_by,
    }


@router.get("/patients/{patient_id}/procedure-notes")
def list_procedure_notes(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(ClinicalProcedureNote).filter(
        ClinicalProcedureNote.patient_id == patient_id
    ).order_by(ClinicalProcedureNote.performed_at.desc()).all()
    return {"results": [_procedure_note_out(n) for n in rows]}


@router.post("/patients/{patient_id}/procedure-notes", status_code=201)
async def record_procedure_note(patient_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    role = _require_procedure_note_author(current_user)
    _get_org_patient(db, patient_id, _org_id(current_user))
    body = await request.json()
    procedure_name = (body.get("procedure_name") or "").strip()
    if not procedure_name:
        raise HTTPException(422, "procedure_name is required")

    actor = _actor(current_user)
    note = ClinicalProcedureNote(
        patient_id=patient_id, performed_by_role=role, procedure_name=procedure_name,
        indication=body.get("indication"), findings=body.get("findings"), technique=body.get("technique"),
        complications=body.get("complications"), performed_by=actor, created_by=actor,
    )
    db.add(note)
    db.flush()
    publish(
        db, "PROCEDURE_NOTE_RECORDED", patient_id=patient_id, actor=actor, role=current_user.get("role"),
        title=f"Procedure note: {procedure_name}", category="TREATMENT",
        description=f"{actor} recorded a procedure note for {procedure_name}.",
    )
    db.commit()
    db.refresh(note)
    return {"status": "success", "procedure_note": _procedure_note_out(note)}


_PALLIATIVE_ORDER_STATUS_ORDER = ["Draft", "Signed", "Active", "Discontinued"]


def _palliative_order_out(o: PalliativeTreatmentOrder) -> dict:
    return {
        "id": o.id, "patient_id": o.patient_id, "order_type": o.order_type, "instructions": o.instructions,
        "status": o.status, "signer_email": o.signer_email, "signer_role": o.signer_role,
        "signed_at": o.signed_at.isoformat() if o.signed_at else None,
        "discontinued_reason": o.discontinued_reason, "created_by": o.created_by,
    }


@router.get("/patients/{patient_id}/palliative-orders")
def list_palliative_orders(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(PalliativeTreatmentOrder).filter(
        PalliativeTreatmentOrder.patient_id == patient_id
    ).order_by(PalliativeTreatmentOrder.id.desc()).all()
    return {"results": [_palliative_order_out(o) for o in rows]}


@router.post("/patients/{patient_id}/palliative-orders", status_code=201)
async def create_palliative_order(patient_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not is_cca_palliative_care_specialist(current_user):
        raise HTTPException(403, "Only the Palliative Care Specialist may author a palliative treatment order")
    _get_org_patient(db, patient_id, _org_id(current_user))
    body = await request.json()
    order_type = (body.get("order_type") or "").strip()
    instructions = (body.get("instructions") or "").strip()
    if not order_type or not instructions:
        raise HTTPException(422, "order_type and instructions are required")

    actor = _actor(current_user)
    order = PalliativeTreatmentOrder(
        patient_id=patient_id, order_type=order_type, instructions=instructions, created_by=actor,
    )
    db.add(order)
    db.flush()
    publish(
        db, "PALLIATIVE_ORDER_CREATED", patient_id=patient_id, actor=actor, role=current_user.get("role"),
        title=f"Palliative order: {order_type}", category="TREATMENT",
        description=f"{actor} created a palliative treatment order ({order_type}).",
    )
    db.commit()
    db.refresh(order)
    return {"status": "success", "palliative_order": _palliative_order_out(order)}


@router.patch("/palliative-orders/{order_id}")
async def transition_palliative_order(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Structural sequencing (Draft -> Signed -> Active -> Discontinued) only -- Discontinued
    is reachable from Signed or Active (a comfort-care order can be stopped from either), never
    a clinical judgment about whether stopping is warranted."""
    if not is_cca_palliative_care_specialist(current_user):
        raise HTTPException(403, "Only the Palliative Care Specialist may update a palliative treatment order")
    org_id = _org_id(current_user)
    order = db.query(PalliativeTreatmentOrder).filter(PalliativeTreatmentOrder.id == order_id).first()
    if not order:
        raise HTTPException(404, "Palliative treatment order not found")
    _check_patient_in_org(db, order.patient_id, org_id)

    body = await request.json()
    target = body.get("status")
    if target not in _PALLIATIVE_ORDER_STATUS_ORDER:
        raise HTTPException(422, f"status must be one of {_PALLIATIVE_ORDER_STATUS_ORDER}")
    if target == "Discontinued":
        if order.status not in ("Signed", "Active"):
            raise HTTPException(409, f"Cannot discontinue an order that is {order.status}")
        if not body.get("discontinued_reason"):
            raise HTTPException(422, "discontinued_reason is required")
        order.discontinued_reason = body["discontinued_reason"]
    else:
        current_index = _PALLIATIVE_ORDER_STATUS_ORDER.index(order.status) if order.status in _PALLIATIVE_ORDER_STATUS_ORDER else -1
        target_index = _PALLIATIVE_ORDER_STATUS_ORDER.index(target)
        if target_index != current_index + 1:
            raise HTTPException(409, f"Cannot move from {order.status} directly to {target}")
    order.status = target
    if target == "Signed":
        order.signer_email = current_user.get("email")
        order.signer_role = current_user.get("role")
        order.signed_at = datetime.utcnow()
    publish(
        db, "PALLIATIVE_ORDER_TRANSITIONED", patient_id=order.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Palliative order updated", category="TREATMENT",
        description=f"{_actor(current_user)} moved the palliative order to {target}.",
    )
    db.commit()
    db.refresh(order)
    return {"status": "success", "palliative_order": _palliative_order_out(order)}


# ---------------------------------------------------------------------------
# Regimen library
# ---------------------------------------------------------------------------

@router.post("/regimens", status_code=201)
async def create_regimen(request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Regimen and dose decisions remain clinician-controlled (PDF item 6) -- a pharmacist
    may review/dispense against a regimen but does not author one."""
    _require_clinician(current_user)
    body = await request.json()
    if not body.get("name"):
        raise HTTPException(422, "name is required")
    drug_lines_in = body.get("drug_lines") or []
    if not isinstance(drug_lines_in, list) or not drug_lines_in:
        raise HTTPException(422, "drug_lines (a non-empty list) is required")

    regimen = Regimen(
        organization_id=_org_id(current_user), name=body["name"], cancer_indication=body.get("cancer_indication"),
        intent_setting=body.get("intent_setting"), schedule=body.get("schedule"),
        number_of_cycles=body.get("number_of_cycles"), premedications=body.get("premedications"),
        hydration=body.get("hydration"), supportive_therapy=body.get("supportive_therapy"),
        hold_parameters=body.get("hold_parameters"), reference_notes=body.get("reference_notes"),
        version=body.get("version", "1.0"), effective_date=body.get("effective_date"),
        approved_by=body.get("approved_by") or _actor(current_user), created_by=_actor(current_user),
    )
    db.add(regimen)
    db.flush()
    lines = []
    for i, line in enumerate(drug_lines_in):
        if not line.get("generic_name"):
            raise HTTPException(422, f"drug_lines[{i}].generic_name is required")
        drug_line = RegimenDrugLine(
            regimen_id=regimen.id, sequence_number=line.get("sequence_number", i + 1),
            generic_name=line["generic_name"], dose_basis=line.get("dose_basis"),
            standard_protocol_dose=line.get("standard_protocol_dose"), route=line.get("route"),
            notes=line.get("notes"),
        )
        db.add(drug_line)
        lines.append(drug_line)
    db.commit()
    db.refresh(regimen)
    for l in lines:
        db.refresh(l)
    return {"status": "success", "regimen": _regimen_out(regimen, lines)}


@router.get("/regimens")
def list_regimens(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    org_id = _org_id(current_user)
    regimens = db.query(Regimen).filter(Regimen.organization_id == org_id).order_by(Regimen.name).all()
    regimen_ids = [r.id for r in regimens]
    lines_by_regimen: dict[int, list] = {rid: [] for rid in regimen_ids}
    if regimen_ids:
        for line in db.query(RegimenDrugLine).filter(RegimenDrugLine.regimen_id.in_(regimen_ids)).all():
            lines_by_regimen.setdefault(line.regimen_id, []).append(line)
    return {"regimens": [_regimen_out(r, lines_by_regimen.get(r.id, [])) for r in regimens]}


# ---------------------------------------------------------------------------
# Treatment Plan phases (item 4) -- TreatmentPlan itself has no phases array
# ---------------------------------------------------------------------------

def _get_org_treatment_plan(db: Session, plan_id: int, org_id: int) -> TreatmentPlan:
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, "Treatment plan not found")
    _check_patient_in_org(db, plan.patient_id, org_id)
    return plan


def _phase_out(p: TreatmentPlanPhase) -> dict:
    return {
        "id": p.id, "treatment_plan_id": p.treatment_plan_id, "sequence": p.sequence, "modality": p.modality,
        "label": p.label, "regimen_or_procedure_ref": p.regimen_or_procedure_ref,
        "planned_start": p.planned_start.isoformat() if p.planned_start else None,
        "duration_description": p.duration_description, "status": p.status,
        "responsible_clinician_name": p.responsible_clinician_name, "responsible_clinician_role": p.responsible_clinician_role,
    }


@router.get("/treatment-plans/{plan_id}/phases")
def list_treatment_plan_phases(plan_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_treatment_plan(db, plan_id, _org_id(current_user))
    rows = db.query(TreatmentPlanPhase).filter(TreatmentPlanPhase.treatment_plan_id == plan_id).order_by(TreatmentPlanPhase.sequence).all()
    return {"phases": [_phase_out(p) for p in rows]}


@router.put("/treatment-plans/{plan_id}/phases")
async def replace_treatment_plan_phases(plan_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Wholesale replace, matching how the dashboard's Treatment Plan screen edits the
    phase list as one unit. Any treating oncologist may update phases -- a combined-modality
    plan's phase list legitimately spans more than one modality's clinician, so this is
    intentionally looser than _require_modality_signer (which still gates signing the plan
    itself, unaffected by this endpoint)."""
    _require_clinician(current_user)
    plan = _get_org_treatment_plan(db, plan_id, _org_id(current_user))
    body = await request.json()
    phases_in = body.get("phases")
    if not isinstance(phases_in, list):
        raise HTTPException(422, "phases (a list) is required")

    db.query(TreatmentPlanPhase).filter(TreatmentPlanPhase.treatment_plan_id == plan_id).delete()
    rows = []
    for i, phase in enumerate(phases_in):
        if not phase.get("modality") or not phase.get("label"):
            raise HTTPException(422, f"phases[{i}].modality and phases[{i}].label are required")
        row = TreatmentPlanPhase(
            treatment_plan_id=plan_id, sequence=phase.get("sequence", i + 1), modality=phase["modality"],
            label=phase["label"], regimen_or_procedure_ref=phase.get("regimen_or_procedure_ref"),
            planned_start=phase.get("planned_start"), duration_description=phase.get("duration_description"),
            status=phase.get("status", "draft"), responsible_clinician_name=phase.get("responsible_clinician_name"),
            responsible_clinician_role=phase.get("responsible_clinician_role"),
        )
        db.add(row)
        rows.append(row)
    publish(
        db, "TREATMENT_PLAN_PHASES_UPDATED", patient_id=plan.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Treatment plan phases updated", category="TREATMENT",
        description=f"{_actor(current_user)} updated the treatment plan's phase sequence.", plan_id=plan_id,
    )
    db.commit()
    for r in rows:
        db.refresh(r)
    return {"status": "success", "phases": [_phase_out(r) for r in rows]}


# ---------------------------------------------------------------------------
# Toxicity / Response history reads -- routers/cca.py has POST /treatment/toxicity and
# POST /response-assessments but no corresponding GET list; the dashboard's Toxicity
# history and Treatment Readiness (item 16) and Response Assessment history (item 17)
# screens need one, so it lives here rather than expanding cca.py's own surface.
# ---------------------------------------------------------------------------

@router.get("/patients/{patient_id}/toxicity-events")
def list_toxicity_events(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(ToxicityEvent).filter(ToxicityEvent.patient_id == patient_id).order_by(ToxicityEvent.id.desc()).all()
    return {"toxicity_events": [
        {
            "id": t.id, "patient_id": t.patient_id, "term": t.term, "grade": t.grade,
            "baseline_value": t.baseline_value, "grading_standard": t.grading_standard,
            "onset_date": t.onset_date.isoformat() if t.onset_date else None, "ongoing": t.ongoing,
        }
        for t in rows
    ]}


@router.get("/patients/{patient_id}/response-assessments")
def list_response_assessments(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(ResponseAssessment).filter(ResponseAssessment.patient_id == patient_id).order_by(ResponseAssessment.id.desc()).all()
    return {"response_assessments": [
        {
            "id": r.id, "patient_id": r.patient_id, "framework": r.framework, "framework_version": r.framework_version,
            "response_category": r.response_category, "confirmed": r.confirmed, "lesions": r.lesions,
            "imaging_reference": r.imaging_reference, "recorded_by": r.recorded_by,
            "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
        }
        for r in rows
    ]}


# ---------------------------------------------------------------------------
# Domain events (PDF item 20/32 audit trail) -- every screen's "Audit trail" panel used to
# read only dashboard/lib/oncology/store.tsx's client-side, localStorage-only log, which
# meant a reload (or a different browser/device) silently lost the entire history even
# though the real history was durably sitting in cca_domain_events all along (every
# publish() call in cca.py/cca_oncology_ext.py already persists one there). This is a
# plain read of that existing durable stream -- no new write path, no new event.
# ---------------------------------------------------------------------------

@router.get("/patients/{patient_id}/domain-events")
def list_domain_events(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(DomainEvent).filter(DomainEvent.patient_id == patient_id).order_by(DomainEvent.created_at.asc()).all()
    return {"domain_events": [
        {"id": e.id, "event_type": e.event_type, "payload": e.payload, "created_at": e.created_at.isoformat() if e.created_at else None}
        for e in rows
    ]}


# ---------------------------------------------------------------------------
# Generic record extension (supplementary fields with no column on an existing table)
# ---------------------------------------------------------------------------

@router.get("/oncology-ext/extension")
def get_record_extension(entity_table: str, entity_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    row = db.query(OncologyRecordExtension).filter(
        OncologyRecordExtension.organization_id == _org_id(current_user),
        OncologyRecordExtension.entity_table == entity_table, OncologyRecordExtension.entity_id == entity_id,
    ).first()
    return {"extension": {"entity_table": entity_table, "entity_id": entity_id, "payload": row.payload if row else None}}


@router.put("/oncology-ext/extension")
async def put_record_extension(request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Any clinical/nursing role may attach supplementary descriptive fields to a record
    they're otherwise permitted to read -- this never carries an authorization decision of
    its own (no dose, no status, no sign-off lives in `payload`).

    Every other write in this router publishes a DomainEvent, which is what
    GET /patients/{id}/domain-events (and every frontend "Audit trail" panel reading through
    it) is built on -- this endpoint used to be the one exception, which meant a dose
    modification or the Day Care pre-administration checklist (both stored here, on the
    cca_treatment_orders entity_table) recorded real data but left no durable trace of the
    *action* once the browser session that made it ended. `patient_id` is optional only
    for backward compatibility with any caller that predates it; every current frontend
    call site supplies it."""
    _require_clinical_or_nursing_role(current_user)
    body = await request.json()
    entity_table = body.get("entity_table")
    entity_id = body.get("entity_id")
    if not entity_table or entity_id is None:
        raise HTTPException(422, "entity_table and entity_id are required")
    org_id = _org_id(current_user)
    row = db.query(OncologyRecordExtension).filter(
        OncologyRecordExtension.organization_id == org_id,
        OncologyRecordExtension.entity_table == entity_table, OncologyRecordExtension.entity_id == entity_id,
    ).first()
    if not row:
        row = OncologyRecordExtension(organization_id=org_id, entity_table=entity_table, entity_id=entity_id)
        db.add(row)
    row.payload = body.get("payload") or {}
    row.updated_by = _actor(current_user)
    row.updated_at = datetime.utcnow()
    patient_id = body.get("patient_id")
    if patient_id is not None:
        _check_patient_in_org(db, patient_id, org_id)
        publish(
            db, "RECORD_EXTENSION_UPDATED", patient_id=patient_id, actor=_actor(current_user), role=current_user.get("role"),
            title=f"{entity_table.replace('cca_', '').replace('_', ' ').title()} updated", category="TREATMENT",
            description=f"{_actor(current_user)} updated supplementary details for {entity_table}#{entity_id}.",
            entity_table=entity_table, entity_id=entity_id,
        )
    db.commit()
    db.refresh(row)
    return {"status": "success", "extension": {"entity_table": row.entity_table, "entity_id": row.entity_id, "payload": row.payload}}


# ---------------------------------------------------------------------------
# Radiation feature completion round -- Treatment Unit / Equipment QA Register /
# Machine-Equipment Issue / In-Vivo Dosimetry (reference SCR-PHY-009, SCR-PHY-010,
# SCR-RTT-001, SCR-RTT-008). RadiationTreatmentUnit/RadiationEquipmentQARecord/
# RadiationEquipmentIssue are organization-level equipment master data (like Regimen),
# not patient records -- every read below is scoped by organization_id, not patient_id.
#
# No dose-calculation or clinical-safety-threshold logic here either: In-Vivo Dosimetry's
# reference "deviation [DERIVED]" is exactly the kind of computed dosimetric comparison
# already excluded from physics QA and fraction delivery above -- expected_dose/
# measured_dose stay physicist-typed reference values, never compared by this endpoint;
# outcome is the physicist's own attestation, mirroring RadiationFraction.dose_match_confirmed.
# ---------------------------------------------------------------------------

_QA_FREQUENCY_DAYS = {"Daily": 1, "Weekly": 7, "Monthly": 30, "Annual": 365}
_ISSUE_CATEGORIES = ("Interlock", "Mechanical", "Imaging", "Dosimetry", "Software", "Accessory", "Environmental")


def _require_rt_reporter_or_physicist(current_user: dict):
    """Who may report/notify on a machine issue -- the RTT at the console (Radiologist login,
    same reasoning as _require_rt_delivery_or_ro above), the treating Radiation Oncologist, or
    the Radiation Physicist. Admin never substitutes for clinical/technical staff here."""
    if is_cca_radiologist(current_user) or is_cca_radiation_physicist(current_user) or is_cca_radiation_oncologist(current_user):
        return
    raise HTTPException(403, "Only Radiology/RTT, Radiation Oncologist, or Radiation Physicist staff may report an equipment issue")


def _get_org_treatment_unit(db: Session, unit_id: int, org_id: int) -> RadiationTreatmentUnit:
    unit = db.query(RadiationTreatmentUnit).filter(
        RadiationTreatmentUnit.id == unit_id, RadiationTreatmentUnit.organization_id == org_id
    ).first()
    if not unit:
        raise HTTPException(404, "Radiation treatment unit not found")
    return unit


def _treatment_unit_out(u: RadiationTreatmentUnit) -> dict:
    return {
        "id": u.id, "name": u.name, "unit_type": u.unit_type, "status": u.status,
        "created_by": u.created_by, "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _qa_next_due_date(q: RadiationEquipmentQARecord):
    if not q.last_performed_date or q.frequency not in _QA_FREQUENCY_DAYS:
        return None
    return q.last_performed_date + timedelta(days=_QA_FREQUENCY_DAYS[q.frequency])


def _qa_record_out(q: RadiationEquipmentQARecord) -> dict:
    next_due = _qa_next_due_date(q)
    return {
        "id": q.id, "treatment_unit_id": q.treatment_unit_id, "test_name": q.test_name,
        "frequency": q.frequency, "tolerance_note": q.tolerance_note,
        "last_performed_date": q.last_performed_date.isoformat() if q.last_performed_date else None,
        "result": q.result, "pass_fail": q.pass_fail, "action_on_failure": q.action_on_failure,
        "downtime_recorded": q.downtime_recorded,
        "next_due_date": next_due.isoformat() if next_due else None,
        "overdue": bool(next_due and next_due < datetime.utcnow().date()),
        "performed_by": q.performed_by, "performed_at": q.performed_at.isoformat() if q.performed_at else None,
    }


def _affected_patients_for_issue(db: Session, issue: RadiationEquipmentIssue) -> list:
    """Patients affected [DERIVED] (reference SCR-RTT-008) -- read from the existing fraction
    schedule for this unit on the issue's day, never a stored/duplicated list."""
    day = issue.time_started.date() if issue.time_started else datetime.utcnow().date()
    rows = db.query(RadiationFraction, CCARadiationPhase, CCAPatient).join(
        CCARadiationPhase, RadiationFraction.phase_id == CCARadiationPhase.id
    ).join(
        RadiationPrescription, CCARadiationPhase.prescription_id == RadiationPrescription.id
    ).join(
        CCAPatient, RadiationPrescription.patient_id == CCAPatient.id
    ).filter(
        RadiationFraction.treatment_unit_id == issue.treatment_unit_id,
        RadiationFraction.scheduled_date == day,
        RadiationFraction.status == "scheduled",
    ).all()
    return [
        {"patient_id": patient.id, "patient_name": patient.name, "fraction_id": frac.id,
         "phase_id": phase.id, "phase_label": phase.label}
        for frac, phase, patient in rows
    ]


def _equipment_issue_out(db: Session, i: RadiationEquipmentIssue) -> dict:
    downtime_minutes = None
    if i.resolved_at and i.time_started:
        downtime_minutes = int((i.resolved_at - i.time_started).total_seconds() // 60)
    return {
        "id": i.id, "treatment_unit_id": i.treatment_unit_id, "description": i.description,
        "category": i.category, "time_started": i.time_started.isoformat() if i.time_started else None,
        "physics_notified_name": i.physics_notified_name,
        "physics_notified_at": i.physics_notified_at.isoformat() if i.physics_notified_at else None,
        "action_taken": i.action_taken, "resolution": i.resolution,
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        "return_to_service_by": i.return_to_service_by, "return_to_service_checks": i.return_to_service_checks,
        "incident_reference": i.incident_reference, "status": i.status,
        "reported_by": i.reported_by, "created_at": i.created_at.isoformat() if i.created_at else None,
        "downtime_minutes": downtime_minutes,
        "affected_patients": _affected_patients_for_issue(db, i),
    }


def _invivo_dosimetry_out(d: RadiationInVivoDosimetry) -> dict:
    return {
        "id": d.id, "fraction_id": d.fraction_id, "required": bool(d.required), "method": d.method,
        "detector_calibration": d.detector_calibration, "expected_dose": d.expected_dose,
        "measured_dose": d.measured_dose, "outcome": d.outcome,
        "action_on_out_of_tolerance": d.action_on_out_of_tolerance,
        "performed_by": d.performed_by, "reviewed_by": d.reviewed_by,
        "performed_at": d.performed_at.isoformat() if d.performed_at else None,
    }


@router.post("/radiation-units", status_code=201)
async def create_treatment_unit(request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Equipment master data -- the Radiation Physicist (or Admin, matching the
    ClinicalMaster/Regimen precedent for org-level configuration) registers a unit once,
    then it's referenced by fraction scheduling and the QA register below."""
    if not (is_cca_radiation_physicist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiation Physicist or Admin may register a treatment unit")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name is required")
    unit = RadiationTreatmentUnit(
        organization_id=_org_id(current_user), name=name, unit_type=body.get("unit_type"),
        status=body.get("status", "Active"), created_by=_actor(current_user),
    )
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return {"status": "success", "unit": _treatment_unit_out(unit)}


@router.get("/radiation-units")
def list_treatment_units(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    rows = db.query(RadiationTreatmentUnit).filter(
        RadiationTreatmentUnit.organization_id == _org_id(current_user)
    ).order_by(RadiationTreatmentUnit.name).all()
    return {"units": [_treatment_unit_out(u) for u in rows]}


@router.get("/radiation-units/qa-register")
def qa_register(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Equipment QA Register (reference SCR-PHY-009) -- every unit's QA history in one place,
    with the overdue-QA banner data (RTT unit schedule alert, PHY-050) computed alongside it."""
    org_id = _org_id(current_user)
    units = db.query(RadiationTreatmentUnit).filter(RadiationTreatmentUnit.organization_id == org_id).all()
    unit_ids = [u.id for u in units]
    rows = db.query(RadiationEquipmentQARecord).filter(
        RadiationEquipmentQARecord.treatment_unit_id.in_(unit_ids)
    ).order_by(RadiationEquipmentQARecord.performed_at.desc()).all() if unit_ids else []
    records = [_qa_record_out(q) for q in rows]
    overdue_unit_ids = sorted({r["treatment_unit_id"] for r in records if r["overdue"]})
    return {"records": records, "overdue_unit_ids": overdue_unit_ids}


@router.get("/radiation-units/{unit_id}/schedule")
def treatment_unit_schedule(unit_id: int, date: str = None, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Treatment Unit Schedule (reference SCR-RTT-001) -- one day's slot list for a unit,
    joined from the existing fraction/phase/prescription/patient records, plus the screen's
    two derivable alerts (unit QA overdue, plan not yet released for treatment)."""
    org_id = _org_id(current_user)
    unit = _get_org_treatment_unit(db, unit_id, org_id)
    day = datetime.strptime(date, "%Y-%m-%d").date() if date else datetime.utcnow().date()
    rows = db.query(RadiationFraction, CCARadiationPhase, RadiationPrescription, CCAPatient).join(
        CCARadiationPhase, RadiationFraction.phase_id == CCARadiationPhase.id
    ).join(
        RadiationPrescription, CCARadiationPhase.prescription_id == RadiationPrescription.id
    ).join(
        CCAPatient, RadiationPrescription.patient_id == CCAPatient.id
    ).filter(
        RadiationFraction.treatment_unit_id == unit.id, RadiationFraction.scheduled_date == day,
    ).order_by(RadiationFraction.fraction_number).all()
    slots = [
        {
            "fraction_id": frac.id, "patient_id": patient.id, "patient_name": patient.name, "mrn": patient.mrn,
            "treatment_site": phase.treatment_site, "laterality": phase.laterality, "phase_label": phase.label,
            "fraction_number": frac.fraction_number, "fractions_total": phase.number_of_fractions,
            "prescribed_dose_per_fraction_gy": phase.dose_per_fraction_gy,
            "plan_release_status": phase.rt_sub_status, "status": frac.status,
        }
        for frac, phase, rx, patient in rows
    ]
    qa_rows = db.query(RadiationEquipmentQARecord).filter(RadiationEquipmentQARecord.treatment_unit_id == unit.id).all()
    qa_overdue = any(_qa_record_out(q)["overdue"] for q in qa_rows)
    plan_not_released = any(s["plan_release_status"] not in ("treatment_ready", "on_treatment", "completed") for s in slots)
    return {
        "unit": _treatment_unit_out(unit), "date": day.isoformat(), "slots": slots,
        "alerts": {"unit_qa_overdue": qa_overdue, "plan_not_released": plan_not_released},
    }


@router.post("/radiation-units/{unit_id}/qa-records", status_code=201)
async def create_qa_record(unit_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not is_cca_radiation_physicist(current_user):
        raise HTTPException(403, "Only the Radiation Physicist may record an equipment QA test")
    unit = _get_org_treatment_unit(db, unit_id, _org_id(current_user))
    body = await request.json()
    test_name = (body.get("test_name") or "").strip()
    frequency = body.get("frequency")
    if not test_name:
        raise HTTPException(422, "test_name is required")
    if frequency not in _QA_FREQUENCY_DAYS:
        raise HTTPException(422, f"frequency must be one of {list(_QA_FREQUENCY_DAYS)}")
    last_performed = datetime.strptime(body["last_performed_date"], "%Y-%m-%d").date() if body.get("last_performed_date") else datetime.utcnow().date()
    record = RadiationEquipmentQARecord(
        treatment_unit_id=unit.id, test_name=test_name, frequency=frequency,
        tolerance_note=body.get("tolerance_note"), last_performed_date=last_performed,
        result=body.get("result"), pass_fail=body.get("pass_fail"), action_on_failure=body.get("action_on_failure"),
        downtime_recorded=body.get("downtime_recorded"), performed_by=_actor(current_user),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"status": "success", "record": _qa_record_out(record)}


@router.get("/radiation-units/{unit_id}/qa-records")
def list_qa_records(unit_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    unit = _get_org_treatment_unit(db, unit_id, _org_id(current_user))
    rows = db.query(RadiationEquipmentQARecord).filter(
        RadiationEquipmentQARecord.treatment_unit_id == unit.id
    ).order_by(RadiationEquipmentQARecord.performed_at.desc()).all()
    return {"records": [_qa_record_out(q) for q in rows]}


@router.post("/radiation-units/{unit_id}/issues", status_code=201)
async def report_equipment_issue(unit_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Machine/Equipment Issue (reference SCR-RTT-008)."""
    _require_rt_reporter_or_physicist(current_user)
    unit = _get_org_treatment_unit(db, unit_id, _org_id(current_user))
    body = await request.json()
    description = (body.get("description") or "").strip()
    category = body.get("category")
    if not description:
        raise HTTPException(422, "description is required")
    if category not in _ISSUE_CATEGORIES:
        raise HTTPException(422, f"category must be one of {_ISSUE_CATEGORIES}")
    issue = RadiationEquipmentIssue(
        treatment_unit_id=unit.id, description=description, category=category,
        physics_notified_name=body.get("physics_notified_name"),
        physics_notified_at=datetime.utcnow() if body.get("physics_notified_name") else None,
        action_taken=body.get("action_taken"), incident_reference=body.get("incident_reference"),
        reported_by=_actor(current_user),
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)
    return {"status": "success", "issue": _equipment_issue_out(db, issue)}


@router.get("/radiation-units/{unit_id}/issues")
def list_equipment_issues(unit_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    unit = _get_org_treatment_unit(db, unit_id, _org_id(current_user))
    rows = db.query(RadiationEquipmentIssue).filter(
        RadiationEquipmentIssue.treatment_unit_id == unit.id
    ).order_by(RadiationEquipmentIssue.created_at.desc()).all()
    return {"issues": [_equipment_issue_out(db, i) for i in rows]}


def _get_org_equipment_issue(db: Session, issue_id: int, org_id: int) -> RadiationEquipmentIssue:
    issue = db.query(RadiationEquipmentIssue).join(
        RadiationTreatmentUnit, RadiationEquipmentIssue.treatment_unit_id == RadiationTreatmentUnit.id
    ).filter(RadiationEquipmentIssue.id == issue_id, RadiationTreatmentUnit.organization_id == org_id).first()
    if not issue:
        raise HTTPException(404, "Equipment issue not found")
    return issue


@router.post("/radiation-issues/{issue_id}/notify-physics")
async def notify_physics_on_issue(issue_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_rt_reporter_or_physicist(current_user)
    issue = _get_org_equipment_issue(db, issue_id, _org_id(current_user))
    body = await request.json()
    name = (body.get("physics_notified_name") or "").strip()
    if not name:
        raise HTTPException(422, "physics_notified_name is required")
    issue.physics_notified_name = name
    issue.physics_notified_at = datetime.utcnow()
    db.commit()
    db.refresh(issue)
    return {"status": "success", "issue": _equipment_issue_out(db, issue)}


@router.post("/radiation-issues/{issue_id}/resolve")
async def resolve_equipment_issue(issue_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Return-to-service authorisation is the Radiation Physicist's action (matches the
    Physics QA approval gate above) -- resolving an issue without physics sign-off is exactly
    the gap this screen exists to close."""
    if not is_cca_radiation_physicist(current_user):
        raise HTTPException(403, "Only the Radiation Physicist may authorise return-to-service")
    issue = _get_org_equipment_issue(db, issue_id, _org_id(current_user))
    if issue.status == "RESOLVED":
        raise HTTPException(409, "This issue is already resolved")
    body = await request.json()
    resolution = (body.get("resolution") or "").strip()
    checks = (body.get("return_to_service_checks") or "").strip()
    if not (resolution and checks):
        raise HTTPException(422, "resolution and return_to_service_checks are both required")
    issue.resolution = resolution
    issue.return_to_service_checks = checks
    issue.return_to_service_by = _actor(current_user)
    issue.resolved_at = datetime.utcnow()
    issue.status = "RESOLVED"
    if body.get("action_taken"):
        issue.action_taken = body["action_taken"]
    db.commit()
    db.refresh(issue)
    return {"status": "success", "issue": _equipment_issue_out(db, issue)}


@router.post("/radiation-fractions/{fraction_id}/schedule")
async def schedule_radiation_fraction(fraction_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Assigns a scheduled fraction to a treatment unit/day (RTT-001's 'unit lead; scheduler'
    role) -- previously RadiationFraction.scheduled_date had no writer at all, so the
    Treatment Unit Schedule view above had nothing to show."""
    _require_rt_delivery_or_ro(current_user)
    fraction = db.query(RadiationFraction).filter(RadiationFraction.id == fraction_id).first()
    if not fraction:
        raise HTTPException(404, "Radiation fraction not found")
    org_id = _org_id(current_user)
    _get_org_radiation_phase(db, fraction.phase_id, org_id)
    body = await request.json()
    unit_id = body.get("treatment_unit_id")
    if unit_id is not None:
        _get_org_treatment_unit(db, unit_id, org_id)
        fraction.treatment_unit_id = unit_id
    if body.get("scheduled_date"):
        fraction.scheduled_date = datetime.strptime(body["scheduled_date"], "%Y-%m-%d").date()
    db.commit()
    db.refresh(fraction)
    return {"status": "success", "fraction": _rt_fraction_out(fraction)}


@router.post("/radiation-fractions/{fraction_id}/invivo-dosimetry", status_code=201)
async def record_invivo_dosimetry(fraction_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """In-Vivo Dosimetry (reference SCR-PHY-010) -- see the module docstring above for why
    expected/measured stay physicist-typed reference values with no computed deviation."""
    if not is_cca_radiation_physicist(current_user):
        raise HTTPException(403, "Only the Radiation Physicist may record in-vivo dosimetry")
    fraction = db.query(RadiationFraction).filter(RadiationFraction.id == fraction_id).first()
    if not fraction:
        raise HTTPException(404, "Radiation fraction not found")
    _get_org_radiation_phase(db, fraction.phase_id, _org_id(current_user))
    body = await request.json()
    outcome = body.get("outcome")
    if outcome is not None and outcome not in ("Within Tolerance", "Out of Tolerance"):
        raise HTTPException(422, "outcome must be one of ('Within Tolerance', 'Out of Tolerance')")
    if outcome == "Out of Tolerance" and not (body.get("action_on_out_of_tolerance") or "").strip():
        raise HTTPException(422, "action_on_out_of_tolerance is required when outcome is Out of Tolerance")
    record = RadiationInVivoDosimetry(
        fraction_id=fraction.id, required=bool(body.get("required", False)), method=body.get("method"),
        detector_calibration=body.get("detector_calibration"), expected_dose=body.get("expected_dose"),
        measured_dose=body.get("measured_dose"), outcome=outcome,
        action_on_out_of_tolerance=body.get("action_on_out_of_tolerance"),
        performed_by=_actor(current_user), reviewed_by=body.get("reviewed_by"),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"status": "success", "dosimetry": _invivo_dosimetry_out(record)}


@router.get("/radiation-fractions/{fraction_id}/invivo-dosimetry")
def list_invivo_dosimetry(fraction_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    fraction = db.query(RadiationFraction).filter(RadiationFraction.id == fraction_id).first()
    if not fraction:
        raise HTTPException(404, "Radiation fraction not found")
    _get_org_radiation_phase(db, fraction.phase_id, _org_id(current_user))
    rows = db.query(RadiationInVivoDosimetry).filter(
        RadiationInVivoDosimetry.fraction_id == fraction_id
    ).order_by(RadiationInVivoDosimetry.performed_at.desc()).all()
    return {"dosimetry": [_invivo_dosimetry_out(d) for d in rows]}
