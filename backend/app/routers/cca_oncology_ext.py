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

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..models_cca import CCAPatient, DomainEvent, MDTCase
from ..models_cca import ResponseAssessment, ToxicityEvent, TreatmentPlan
from ..models_cca_oncology_ext import (
    OncologyRecordExtension, RadiationFraction, RadiationPrescription, Regimen,
    RegimenDrugLine, SurgicalPlan, TreatmentPlanPhase,
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


def _get_org_surgical_plan(db: Session, plan_id: int, org_id: int) -> SurgicalPlan:
    plan = db.query(SurgicalPlan).filter(SurgicalPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(404, "Surgical plan not found")
    _check_patient_in_org(db, plan.patient_id, org_id)
    return plan


def _rt_prescription_out(rx: RadiationPrescription) -> dict:
    return {
        "id": rx.id, "patient_id": rx.patient_id, "mdt_case_id": rx.mdt_case_id,
        "diagnosis": rx.diagnosis, "treatment_site": rx.treatment_site, "laterality": rx.laterality,
        "intent": rx.intent, "modality": rx.modality, "technique": rx.technique,
        "treatment_phase": rx.treatment_phase, "total_prescribed_dose_gy": rx.total_prescribed_dose_gy,
        "dose_per_fraction_gy": rx.dose_per_fraction_gy, "number_of_fractions": rx.number_of_fractions,
        "frequency": rx.frequency, "start_date": rx.start_date.isoformat() if rx.start_date else None,
        "concurrent_systemic_treatment": rx.concurrent_systemic_treatment,
        "target_volumes": rx.target_volumes, "organs_at_risk": rx.organs_at_risk,
        "simulation_required": rx.simulation_required, "immobilization": rx.immobilization,
        "image_guidance_required": rx.image_guidance_required, "bolus": rx.bolus,
        "special_instructions": rx.special_instructions, "dicom_rt_plan_ref": rx.dicom_rt_plan_ref,
        "rt_sub_status": rx.rt_sub_status, "signer_email": rx.signer_email, "signer_role": rx.signer_role,
        "signed_at": rx.signed_at.isoformat() if rx.signed_at else None, "created_by": rx.created_by,
    }


def _rt_fraction_out(f: RadiationFraction) -> dict:
    return {
        "id": f.id, "prescription_id": f.prescription_id, "fraction_number": f.fraction_number,
        "scheduled_date": f.scheduled_date.isoformat() if f.scheduled_date else None,
        "status": f.status, "delivered_dose_gy": f.delivered_dose_gy,
        "interruption_reason": f.interruption_reason, "on_treatment_review_note": f.on_treatment_review_note,
        "recorded_by": f.recorded_by, "recorded_at": f.recorded_at.isoformat() if f.recorded_at else None,
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

@router.post("/radiation-prescriptions", status_code=201)
async def create_radiation_prescription(request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_clinician(current_user)
    body = await request.json()
    patient_id = body.get("patient_id")
    if patient_id is None:
        raise HTTPException(422, "patient_id is required")
    _get_org_patient(db, patient_id, _org_id(current_user))
    for field in ("treatment_site", "total_prescribed_dose_gy", "dose_per_fraction_gy", "number_of_fractions"):
        if body.get(field) in (None, ""):
            raise HTTPException(422, f"{field} is required")

    rx = RadiationPrescription(
        patient_id=patient_id, mdt_case_id=body.get("mdt_case_id"), diagnosis=body.get("diagnosis"),
        treatment_site=body["treatment_site"], laterality=body.get("laterality"), intent=body.get("intent"),
        modality=body.get("modality"), technique=body.get("technique"), treatment_phase=body.get("treatment_phase"),
        total_prescribed_dose_gy=body["total_prescribed_dose_gy"], dose_per_fraction_gy=body["dose_per_fraction_gy"],
        number_of_fractions=body["number_of_fractions"], frequency=body.get("frequency"),
        start_date=body.get("start_date"), concurrent_systemic_treatment=bool(body.get("concurrent_systemic_treatment", False)),
        target_volumes=body.get("target_volumes"), organs_at_risk=body.get("organs_at_risk"),
        simulation_required=bool(body.get("simulation_required", True)), immobilization=body.get("immobilization"),
        image_guidance_required=bool(body.get("image_guidance_required", True)), bolus=body.get("bolus"),
        special_instructions=body.get("special_instructions"), dicom_rt_plan_ref=body.get("dicom_rt_plan_ref"),
        created_by=_actor(current_user),
    )
    db.add(rx)
    db.flush()
    publish(
        db, "RADIATION_PRESCRIPTION_CREATED", patient_id=patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Radiation prescription created", category="TREATMENT",
        description=f"{_actor(current_user)} prescribed {rx.total_prescribed_dose_gy} Gy / {rx.number_of_fractions} fractions to {rx.treatment_site}.",
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


@router.post("/radiation-prescriptions/{prescription_id}/transition")
async def transition_radiation_prescription(prescription_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Structural sequencing only -- validates the target status is the very next step in
    RT_SUB_STATUS_ORDER, never a clinical judgment about whether the step is warranted.
    `interrupted` is a side-state off `on_treatment` (matching dashboard/lib/oncology/
    types.ts's RT_SUB_STATUSES), not part of the main linear sequence -- a course can be
    interrupted and resumed without that counting as forward progress through the course."""
    _require_modality_signer(current_user, "radiation")
    rx = _get_org_radiation_prescription(db, prescription_id, _org_id(current_user))
    body = await request.json()
    target = body.get("status")
    if target not in RT_SUB_STATUS_ORDER and target != "interrupted":
        raise HTTPException(422, f"status must be one of {[*RT_SUB_STATUS_ORDER, 'interrupted']}")

    if target == "interrupted":
        if rx.rt_sub_status != "on_treatment":
            raise HTTPException(409, f"Cannot interrupt from {rx.rt_sub_status}")
        rx.rt_sub_status = "interrupted"
        db.commit()
        db.refresh(rx)
        return {"status": "success", "radiation_prescription": _rt_prescription_out(rx)}
    if rx.rt_sub_status == "interrupted":
        if target != "on_treatment":
            raise HTTPException(409, "An interrupted course may only resume to on_treatment")
        rx.rt_sub_status = "on_treatment"
        db.commit()
        db.refresh(rx)
        return {"status": "success", "radiation_prescription": _rt_prescription_out(rx)}

    current_index = RT_SUB_STATUS_ORDER.index(rx.rt_sub_status) if rx.rt_sub_status in RT_SUB_STATUS_ORDER else -1
    target_index = RT_SUB_STATUS_ORDER.index(target)
    if target_index != current_index + 1:
        raise HTTPException(409, f"Cannot move from {rx.rt_sub_status} directly to {target}")
    rx.rt_sub_status = target
    if target == "physician_approved":
        rx.signer_email = current_user.get("email")
        rx.signer_role = current_user.get("role")
        rx.signed_at = datetime.utcnow()
    if target == "treatment_ready":
        # Always exactly `number_of_fractions` rows -- a schedule count that could drift
        # from the prescribed count is precisely the "screens show contradictory values"
        # failure item 26 exists to prevent. Changing the fraction count means amending
        # the prescription, not overriding the schedule here.
        for n in range(1, rx.number_of_fractions + 1):
            db.add(RadiationFraction(prescription_id=rx.id, fraction_number=n, status="scheduled"))
    publish(
        db, "RADIATION_PRESCRIPTION_TRANSITIONED", patient_id=rx.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Radiation prescription updated", category="TREATMENT",
        description=f"{_actor(current_user)} moved the radiation prescription to {target}.",
        prescription_id=rx.id, status=target,
    )
    db.commit()
    db.refresh(rx)
    return {"status": "success", "radiation_prescription": _rt_prescription_out(rx)}


@router.get("/radiation-prescriptions/{prescription_id}/fractions")
def list_radiation_fractions(prescription_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    rx = _get_org_radiation_prescription(db, prescription_id, _org_id(current_user))
    rows = db.query(RadiationFraction).filter(RadiationFraction.prescription_id == rx.id).order_by(RadiationFraction.fraction_number).all()
    return {"fractions": [_rt_fraction_out(f) for f in rows]}


@router.post("/radiation-fractions/{fraction_id}/event")
async def record_radiation_fraction_event(fraction_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_clinical_or_nursing_role(current_user)
    fraction = db.query(RadiationFraction).filter(RadiationFraction.id == fraction_id).first()
    if not fraction:
        raise HTTPException(404, "Radiation fraction not found")
    rx = _get_org_radiation_prescription(db, fraction.prescription_id, _org_id(current_user))
    body = await request.json()
    status_value = body.get("status")
    if status_value not in ("delivered", "missed", "rescheduled"):
        raise HTTPException(422, "status must be one of delivered, missed, rescheduled")
    fraction.status = status_value
    if status_value == "delivered":
        fraction.delivered_dose_gy = body.get("delivered_dose_gy", rx.dose_per_fraction_gy)
    if body.get("interruption_reason"):
        fraction.interruption_reason = body["interruption_reason"]
    if body.get("on_treatment_review_note"):
        fraction.on_treatment_review_note = body["on_treatment_review_note"]
    fraction.recorded_by = _actor(current_user)
    fraction.recorded_at = datetime.utcnow()
    if rx.rt_sub_status == "treatment_ready":
        rx.rt_sub_status = "on_treatment"
    publish(
        db, "RADIATION_FRACTION_RECORDED", patient_id=rx.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Radiation fraction recorded", category="TREATMENT",
        description=f"{_actor(current_user)} recorded fraction {fraction.fraction_number} as {status_value}.",
        prescription_id=rx.id, fraction_id=fraction.id,
    )
    db.commit()
    db.refresh(fraction)
    return {"status": "success", "fraction": _rt_fraction_out(fraction)}


@router.post("/radiation-prescriptions/{prescription_id}/complete")
def complete_radiation_course(prescription_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_modality_signer(current_user, "radiation")
    rx = _get_org_radiation_prescription(db, prescription_id, _org_id(current_user))
    delivered = db.query(RadiationFraction).filter(RadiationFraction.prescription_id == rx.id, RadiationFraction.status == "delivered").count()
    if delivered < rx.number_of_fractions:
        raise HTTPException(409, f"Only {delivered} of {rx.number_of_fractions} fractions delivered")
    rx.rt_sub_status = "completed"
    publish(
        db, "RADIATION_COURSE_COMPLETED", patient_id=rx.patient_id, actor=_actor(current_user),
        role=current_user.get("role"), title="Radiation course completed", category="TREATMENT",
        description=f"{_actor(current_user)} marked the radiation course complete.", prescription_id=rx.id,
    )
    db.commit()
    db.refresh(rx)
    return {"status": "success", "radiation_prescription": _rt_prescription_out(rx)}


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
