"""
Concrete subscribers for the domain events published from routers/cca.py's Treatment Plan /
Care Plan / Treatment Order / MDT endpoints. Importing this module registers every
subscriber (via events.subscribe's decorator) -- see routers/cca.py's `import
event_subscribers` for where that happens; it must run before any request is served.

Each subscriber implements exactly the "Consumers / effect" column of the architecture
doc's events table for the event it's registered against. The "Triggered by" half of that
table -- the actual state transition -- stays in the endpoint that calls publish(); only the
downstream consequence lives here. This is also where a couple of real, pre-existing gaps
get fixed as a side effect of finally having somewhere to put the consequence:
`TreatmentPlan.completed_sessions` was never incremented and only session #1 was ever
created (see _on_treatment_administered), and an MDT recommendation had no way to produce a
review task for the treating clinician when it (routinely) precedes any Care Plan existing
yet (see _on_mdt_recommendation_finalized and CarePlanTask.care_plan_id's now-nullable
docstring in models_cca.py).
"""
from datetime import datetime, timedelta

from .events import publish, subscribe
from .models_cca import CCAPatient, TreatmentPlan, TreatmentSession, CarePlanTask


def _get_patient(db, patient_id):
    if patient_id is None:
        return None
    return db.query(CCAPatient).filter(CCAPatient.id == patient_id).first()


@subscribe("CARE_PLAN_ACTIVATED")
def _on_care_plan_activated(db, patient_id=None, **_payload):
    """Architecture doc: 'All role work queues refresh relevant milestones'. No real-time
    push/worklist layer exists in this codebase to refresh (CarePlanTask rows already are
    the work queue, read directly by each role's endpoints) -- the one concrete, durable
    effect this system can honestly claim is updating the patient's journey state, which
    Patient Summary reads."""
    patient = _get_patient(db, patient_id)
    if patient:
        patient.journey_state = "PlanApproved"


@subscribe("TREATMENT_PLAN_SIGNED")
def _on_treatment_plan_signed(db, patient_id=None, actor=None, role=None,
                               treatment_plan_id=None, supersedes_id=None, **_payload):
    """Architecture doc: 'Care Plan can instantiate approved treatment milestones; Patient
    Summary updates'. Care Plan instantiation stays an explicit clinician action (see
    create_care_plan) rather than an automatic side effect of signing -- a Treatment Plan
    being signed does not by itself mean the multidisciplinary team has convened around it.
    This updates the journey state Patient Summary reads, and -- if this plan explicitly
    supersedes a prior one -- performs the supersession and raises TREATMENT_PLAN_REVISED,
    since a plan is only actually replaced once its replacement takes effect (is signed),
    never at draft time."""
    patient = _get_patient(db, patient_id)
    if patient:
        patient.journey_state = "TreatmentPlanSigned"

    if not supersedes_id:
        return
    prior = db.query(TreatmentPlan).filter(TreatmentPlan.id == supersedes_id).first()
    if prior and prior.status == "ACTIVE":
        prior.status = "SUPERSEDED"
        publish(
            db, "TREATMENT_PLAN_REVISED", patient_id=patient_id, actor=actor, role=role,
            title=f"Treatment Plan superseded: {prior.modality}",
            description=f"Treatment Plan #{prior.id} (v{prior.version_no}) superseded by #{treatment_plan_id} on signing.",
            category="TREATMENT_PLAN",
            prior_treatment_plan_id=prior.id, new_treatment_plan_id=treatment_plan_id,
        )


@subscribe("MDT_RECOMMENDATION_FINALIZED")
def _on_mdt_recommendation_finalized(db, patient_id=None, mdt_case_id=None, mdt_decision_id=None, **_payload):
    """Architecture doc: 'Treating clinician receives review task'. CarePlanTask.care_plan_id
    is now nullable (see its docstring in models_cca.py) specifically so this task can exist
    before any Care Plan does -- an MDT recommendation routinely precedes the first Care Plan
    entirely. owner_name falls back to the patient's primary_oncologist string field, the
    only "who is treating this patient" signal this codebase has (there is no per-patient
    treating-clinician FK); it is a display string, not a resolvable user id, which is why
    owner_id is left blank rather than guessed."""
    patient = _get_patient(db, patient_id)
    if not patient:
        return
    patient.journey_state = "MDTRecommendationFinalized"
    db.add(CarePlanTask(
        care_plan_id=None, patient_id=patient_id,
        description=f"Review MDT recommendation (case #{mdt_case_id}, decision #{mdt_decision_id}) and decide Treatment Plan direction.",
        owner_id="", owner_name=patient.primary_oncologist or "Unassigned",
        due_date=datetime.utcnow() + timedelta(days=3),
        status="OPEN",
    ))


@subscribe("TREATMENT_ADMINISTERED")
def _on_treatment_administered(db, patient_id=None, treatment_plan_id=None,
                                treatment_session_id=None, **_payload):
    """Architecture doc: 'Care milestone completes; next step becomes due'. Advances the
    plan's completed-session count and opens the next planned session if more remain."""
    plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == treatment_plan_id).first()
    session = db.query(TreatmentSession).filter(TreatmentSession.id == treatment_session_id).first()
    if not plan or not session:
        return
    plan.completed_sessions = (plan.completed_sessions or 0) + 1
    if plan.completed_sessions < (plan.planned_sessions or 0):
        db.add(TreatmentSession(
            treatment_plan_id=plan.id, patient_id=patient_id,
            session_no=session.session_no + 1, cycle_no=session.cycle_no + 1, day_no=1,
            planned_on=(datetime.utcnow() + timedelta(days=14)).date(),
            status="PLANNED",
        ))


@subscribe("PATIENT_NO_SHOW")
def _on_patient_no_show(db, patient_id=None, coordination_case_id=None, context=None, **_payload):
    """Architecture doc: 'Care Coordinator receives recovery task'."""
    db.add(CarePlanTask(
        care_plan_id=None, patient_id=patient_id,
        description=f"Recovery follow-up after no-show." + (f" {context}" if context else ""),
        owner_id="", owner_name="Patient Liaison / Care Coordinator",
        due_date=datetime.utcnow() + timedelta(days=2),
        status="OPEN",
    ))


@subscribe("CARE_PLAN_TASK_BLOCKED")
def _on_care_plan_task_blocked(db, patient_id=None, barrier_type=None, barrier_notes=None, **_payload):
    """Architecture doc: 'Care Coordinator + treating team see blocker'."""
    db.add(CarePlanTask(
        care_plan_id=None, patient_id=patient_id,
        description=f"Escalated barrier ({barrier_type}): {barrier_notes or 'see coordination case for detail'}",
        owner_id="", owner_name="Treating Team",
        due_date=datetime.utcnow() + timedelta(days=1),
        status="OPEN",
    ))


@subscribe("TREATMENT_HELD")
def _on_treatment_held(db, patient_id=None, actor=None, care_plan_id=None,
                        reason=None, decision=None, owner_id=None, **_payload):
    """Architecture doc: 'Care Plan blocked/hold status; clinician reassessment'. Creates the
    reassessment task whether or not a Care Plan exists yet for this patient (care_plan_id is
    passed through as-is, possibly None) -- a signed Treatment Order/clearance decision does
    not require a Care Plan to exist first, so this must not silently drop the task in that
    case."""
    db.add(CarePlanTask(
        care_plan_id=care_plan_id, patient_id=patient_id,
        description=f"Reassessment following Treatment {(decision or 'Hold').title()}: {reason}",
        owner_id=str(owner_id) if owner_id is not None else (actor or ""),
        owner_name=actor or "",
        due_date=datetime.utcnow() + timedelta(days=7),
        status="OPEN",
    ))


def _on_diagnostic_result_finalized(db, patient_id, order_id, result_id, is_critical, kind):
    """Architecture doc P0 priority: 'Closed-loop diagnostics: Orders and final results
    resolve Care Plan milestones and create review-required events.' Two effects, both
    conditional on real state rather than guessed:
      1. If raise_order (routers/cca.py) created a milestone CarePlanTask linked to this
         order (only happens when the patient already has an active Care Plan -- see its
         docstring), that milestone is resolved now that the result exists. No-op otherwise.
      2. A critical/abnormal result additionally opens a review-required task for the
         treating clinician (architecture doc: 'abnormal threshold can trigger clinician
         review') -- this is a routing task, not a clinical judgment, so it carries no
         dosage/threshold logic of its own, only the is_critical flag the reporting
         clinician (Radiologist/Pathologist/Lab) already set on the CCAResult itself.
    """
    if order_id:
        milestone = db.query(CarePlanTask).filter(
            CarePlanTask.linked_order_id == order_id, CarePlanTask.status == "OPEN"
        ).first()
        if milestone:
            milestone.status = "RESOLVED"

    if not is_critical:
        return
    patient = _get_patient(db, patient_id)
    db.add(CarePlanTask(
        care_plan_id=None, patient_id=patient_id,
        description=f"Review critical {kind} result (order #{order_id}, result #{result_id}).",
        owner_id="", owner_name=(patient.primary_oncologist if patient else None) or "Unassigned",
        due_date=datetime.utcnow() + timedelta(days=1),
        status="OPEN", owner_role="TREATING_ONCOLOGIST", category="CLINICAL_REVIEW",
        linked_order_id=order_id, linked_result_id=result_id,
    ))


@subscribe("IMAGING_REPORT_FINALIZED")
def _on_imaging_report_finalized(db, patient_id=None, order_id=None, result_id=None,
                                  is_critical=False, **_payload):
    _on_diagnostic_result_finalized(db, patient_id, order_id, result_id, is_critical, "imaging")


@subscribe("PATHOLOGY_REPORT_FINALIZED")
def _on_pathology_report_finalized(db, patient_id=None, order_id=None, result_id=None,
                                    is_critical=False, **_payload):
    _on_diagnostic_result_finalized(db, patient_id, order_id, result_id, is_critical, "pathology")


@subscribe("LAB_RESULT_FINALIZED")
def _on_lab_result_finalized(db, patient_id=None, order_id=None, result_id=None,
                              is_critical=False, **_payload):
    _on_diagnostic_result_finalized(db, patient_id, order_id, result_id, is_critical, "lab")
