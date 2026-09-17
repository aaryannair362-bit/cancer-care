"""
CCA Oncology OS -- Radiology, Pathology, Lab/Phlebotomy, and Molecular Diagnostics worklists.

Split out from routers/cca.py (which was already large) rather than folded into it. Reuses that
module's auth/org-scoping helpers (_org_id, _actor, _get_org_patient, get_cca_db) rather than
duplicating them -- both modules are part of the same package and share the same tenancy model.

Covers 06_Radiologist.pdf, 07_Radiology_Coordinator.pdf, 08_Pathologist_Molecular_Diagnostics.pdf,
09_Lab_Phlebotomy.pdf. All four roles' "worklist" concept maps onto the existing CCAOrder table
(order_type=RADIOLOGY|PATHOLOGY|LAB); their "Reports"/results concept maps onto CCAResult.
Nothing here duplicates CCAOrder/CCAResult -- it adds role-specific operations on top of the
same rows routers/cca.py's generic /orders and /results endpoints already read.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import (
    get_current_user, is_admin, is_cca_oncologist, is_cca_pathologist,
    is_cca_radiologist, is_cca_radiology_coordinator, is_cca_lab_phlebotomy,
    is_cca_radiology_technician,
)
from ..models_cca import (
    CCAOrder, CCAResult, CCABiomarkerResult, CCAJourneyEvent, CCAPatient, PathologySpecimenAccession,
    TreatmentPlan, TreatmentOrder,
    PathologyBlockSlide, PathologyCustodyEvent, PathologyFrozenSection, PathologySecondOpinion,
    PathologyMdtReviewNote,
    CancerEpisode,
)
from ..models_cca_oncology_ext import SurgicalSpecimen
from ..events import publish
from .cca import get_cca_db, _org_id, _actor, _get_org_patient, _check_patient_in_org

router = APIRouter(prefix="/api/cca", tags=["CCA Diagnostics"])


def _get_org_order(db: Session, order_id: int, org_id: int) -> CCAOrder:
    order = db.query(CCAOrder).filter(CCAOrder.id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    _check_patient_in_org(db, order.patient_id, org_id)
    return order


def _get_org_result(db: Session, result_id: int, org_id: int) -> CCAResult:
    result = db.query(CCAResult).filter(CCAResult.id == result_id).first()
    if not result:
        raise HTTPException(404, "Result not found")
    _check_patient_in_org(db, result.patient_id, org_id)
    return result


def _order_out(o: CCAOrder, patient_name: str = None, patient_mrn: str = None) -> dict:
    return {
        "id": o.id, "patient_id": o.patient_id, "patient_name": patient_name, "patient_mrn": patient_mrn,
        "order_type": o.order_type, "item_name": o.item_name, "item_code": o.item_code,
        "clinical_indication": o.clinical_indication, "priority": o.priority,
        "staging_relevant": o.staging_relevant, "status": o.status, "workflow_state": o.workflow_state,
        "requested_by": o.requested_by, "ordered_at": o.ordered_at.isoformat() if o.ordered_at else None,
        "scheduled_at": o.scheduled_at.isoformat() if o.scheduled_at else None, "location": o.location,
        "preparation_status": o.preparation_status, "preparation_notes": o.preparation_notes,
        "collected_by": o.collected_by, "collected_at": o.collected_at.isoformat() if o.collected_at else None,
        "specimen_container": o.specimen_container, "rejection_reason": o.rejection_reason,
        "acquisition_status": o.acquisition_status, "acquisition_modality": o.acquisition_modality,
        "acquisition_protocol": o.acquisition_protocol, "contrast_used": o.contrast_used,
        "contrast_notes": o.contrast_notes, "technical_notes": o.technical_notes,
        "technical_issue": o.technical_issue, "acquired_by": o.acquired_by,
        "acquired_at": o.acquired_at.isoformat() if o.acquired_at else None,
        "identity_confirmed": bool(o.identity_confirmed), "specimen_accession_id": o.specimen_accession_id,
        "collection_site": o.collection_site, "specimen_count": o.specimen_count,
        "received_by": o.received_by, "received_at": o.received_at.isoformat() if o.received_at else None,
        "recollection_of_order_id": o.recollection_of_order_id, "recollection_number": o.recollection_number,
        "scheduled_by": o.scheduled_by,
        "arrived_at": o.arrived_at.isoformat() if o.arrived_at else None, "arrived_by": o.arrived_by,
        "cancelled_at": o.cancelled_at.isoformat() if o.cancelled_at else None, "cancelled_by": o.cancelled_by,
        "cancellation_reason": o.cancellation_reason,
    }


def _result_out(r: CCAResult) -> dict:
    return {
        "id": r.id, "order_id": r.order_id, "patient_id": r.patient_id, "result_type": r.result_type,
        "title": r.title, "findings_text": r.findings_text, "technique": r.technique,
        "comparison": r.comparison, "impression": r.impression, "structured_report": r.structured_report,
        "extracted_values": r.extracted_values, "is_critical": r.is_critical, "status": r.status,
        "report_status": r.report_status,
        "entered_by": r.entered_by, "entered_at": r.entered_at.isoformat() if r.entered_at else None,
        "finalized_by": r.finalized_by,
        "finalized_at": r.finalized_at.isoformat() if r.finalized_at else None,
        "acknowledged_by": r.acknowledged_by,
        "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
        "critical_acknowledged_by": r.critical_acknowledged_by,
        "critical_notified_to": r.critical_notified_to, "critical_notification_method": r.critical_notification_method,
        "critical_escalation_required": bool(r.critical_escalation_required), "critical_escalated_to": r.critical_escalated_to,
        "resulted_at": r.resulted_at.isoformat() if r.resulted_at else None,
        "supersedes_id": r.supersedes_id, "superseded_by_id": r.superseded_by_id,
        "amendment_reason": r.amendment_reason, "amended_by": r.amended_by,
        "amended_at": r.amended_at.isoformat() if r.amended_at else None,
    }


def _require_diagnostics_read(current_user: dict, *role_checks):
    """Least-privilege read for a specialty worklist/order (architecture doc: 'Each role sees
    only the detail necessary for its work'). Previously these reads had no role check at all
    -- any authenticated org member (Front Desk, Patient Liaison, Financial Counsellor...)
    could read a Radiology/Pathology/Lab worklist. Oncologists and Admin always pass, on top
    of whichever specialty-specific predicates the caller supplies."""
    if is_admin(current_user) or is_cca_oncologist(current_user):
        return
    if not any(check(current_user) for check in role_checks):
        raise HTTPException(403, "This role does not have read access to this diagnostics worklist")


def _worklist(db: Session, org_id: int, order_type: str) -> list:
    rows = db.query(CCAOrder, CCAPatient).join(
        CCAPatient, CCAOrder.patient_id == CCAPatient.id
    ).filter(CCAPatient.organization_id == org_id, CCAOrder.order_type == order_type).order_by(
        CCAOrder.ordered_at.desc()
    ).all()
    return [_order_out(o, p.name, p.mrn) for o, p in rows]


# ---------------------------------------------------------------------------
# Radiology: Imaging Worklist (Radiologist) + Imaging Coordination (Radiology Coordinator)
# ---------------------------------------------------------------------------

@router.get("/imaging/worklist")
def imaging_worklist(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_diagnostics_read(current_user, is_cca_radiologist, is_cca_radiology_coordinator)
    return {"worklist": _worklist(db, _org_id(current_user), "RADIOLOGY")}


@router.get("/imaging/orders/{order_id}")
def get_imaging_order(order_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_diagnostics_read(current_user, is_cca_radiologist, is_cca_radiology_coordinator)
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "RADIOLOGY":
        raise HTTPException(404, "Not an imaging order")
    results = db.query(CCAResult).filter(CCAResult.order_id == order.id).order_by(CCAResult.resulted_at.desc()).all()
    prior_count = db.query(CCAOrder).filter(
        CCAOrder.patient_id == order.patient_id, CCAOrder.order_type == "RADIOLOGY", CCAOrder.id != order.id
    ).count()
    return {"order": _order_out(order), "results": [_result_out(r) for r in results], "prior_study_available": prior_count > 0}


@router.post("/imaging/orders/{order_id}/schedule")
async def schedule_imaging_order(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Radiology Coordinator scheduling -- appointment date/time and scanner/room location.
    Gap review (Scheduling Workflow -- Critical): also serves as the RESCHEDULE path (calling
    this again on an already-scheduled order updates date/time/location and records a distinct
    IMAGING_ORDER_RESCHEDULED audit event with the prior value, rather than silently overwriting
    it with no trace) and blocks a same-location/same-slot double-booking against another
    non-cancelled RADIOLOGY order."""
    if not (is_cca_radiology_coordinator(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiology Coordinator or Admin may schedule imaging")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.workflow_state == "Cancelled":
        raise HTTPException(409, "This order was cancelled -- a coordinator must raise a new order to reschedule it")
    body = await request.json()
    scheduled_at = body.get("scheduled_at")
    if not scheduled_at:
        raise HTTPException(422, "scheduled_at is required")
    new_scheduled_at = datetime.fromisoformat(scheduled_at)
    location = body.get("location")

    # "Prevent double-booking of the same scanner/time slot" -- an exact-slot collision check
    # against another live (non-cancelled) RADIOLOGY order at the same location, since this
    # model has no appointment-duration field to reason about true overlap with.
    if location:
        conflict = db.query(CCAOrder).join(CCAPatient, CCAOrder.patient_id == CCAPatient.id).filter(
            CCAPatient.organization_id == _org_id(current_user), CCAOrder.order_type == "RADIOLOGY",
            CCAOrder.id != order.id, CCAOrder.location == location, CCAOrder.scheduled_at == new_scheduled_at,
            CCAOrder.workflow_state != "Cancelled",
        ).first()
        if conflict:
            raise HTTPException(409, f"{location} is already booked for {new_scheduled_at.isoformat()} (order #{conflict.id})")

    previous_scheduled_at = order.scheduled_at
    is_reschedule = previous_scheduled_at is not None
    order.scheduled_at = new_scheduled_at
    order.location = location
    order.workflow_state = "Scheduled"
    if order.status == "RAISED":
        order.status = "SCHEDULED"
    actor = _actor(current_user)
    order.scheduled_by = actor
    if is_reschedule:
        publish(
            db, "IMAGING_ORDER_RESCHEDULED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
            title=f"Imaging rescheduled: {order.item_name}", category="INVESTIGATION",
            description=f"{actor} rescheduled {order.item_name} from {previous_scheduled_at.isoformat()} to {new_scheduled_at.isoformat()}.",
            order_id=order.id,
        )
    else:
        publish(
            db, "IMAGING_ORDER_SCHEDULED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
            title=f"Imaging scheduled: {order.item_name}", category="INVESTIGATION",
            description=f"{actor} scheduled {order.item_name} for {order.scheduled_at.isoformat()}.",
            order_id=order.id,
        )
    db.commit()
    return {"status": "success", "order": _order_out(order)}


@router.post("/imaging/orders/{order_id}/cancel")
async def cancel_imaging_order(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Rescheduling / Cancellation (gap review): requires a documented reason -- unlike a
    rejected lab specimen, there is no physical specimen to re-collect for an imaging order, so
    this is a terminal state (the coordinator raises a fresh order if the study is still
    needed), not a recollection chain."""
    if not (is_cca_radiology_coordinator(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiology Coordinator or Admin may cancel an imaging order")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "RADIOLOGY":
        raise HTTPException(404, "Not an imaging order")
    if order.status == "RESULTED":
        raise HTTPException(409, "Cannot cancel an order that already has a result")
    if order.workflow_state == "Cancelled":
        raise HTTPException(409, "This order is already cancelled")
    body = await request.json()
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(422, "reason is required to cancel an imaging order")
    actor = _actor(current_user)
    order.workflow_state = "Cancelled"
    order.status = "CANCELLED"
    order.cancelled_by = actor
    order.cancelled_at = datetime.utcnow()
    order.cancellation_reason = reason
    publish(
        db, "IMAGING_ORDER_CANCELLED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Imaging cancelled: {order.item_name}", category="INVESTIGATION",
        description=f"{actor} cancelled {order.item_name}: {reason}",
        order_id=order.id,
    )
    db.commit()
    return {"status": "success", "order": _order_out(order)}


@router.post("/imaging/orders/{order_id}/arrive")
async def record_imaging_arrival(order_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Patient Arrival & Check-In (gap review): an operational check-in distinct from the
    Radiology Technician's own identity/acquisition verification -- "allow coordinator to
    update operational status without changing the clinical order"."""
    if not (is_cca_radiology_coordinator(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiology Coordinator or Admin may record patient arrival")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "RADIOLOGY":
        raise HTTPException(404, "Not an imaging order")
    if order.workflow_state != "Scheduled":
        raise HTTPException(409, f"Order must be Scheduled before arrival is recorded (workflow_state={order.workflow_state})")
    actor = _actor(current_user)
    order.arrived_at = datetime.utcnow()
    order.arrived_by = actor
    order.workflow_state = "Arrived"
    publish(
        db, "IMAGING_PATIENT_ARRIVED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Patient arrived: {order.item_name}", category="INVESTIGATION",
        description=f"{actor} recorded patient arrival/check-in for {order.item_name}.",
        order_id=order.id,
    )
    db.commit()
    return {"status": "success", "order": _order_out(order)}


@router.post("/imaging/orders/{order_id}/no-show")
async def record_imaging_no_show(order_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Study Lifecycle Tracking (gap review): a scheduled patient who never arrived -- distinct
    from a coordinator-initiated cancellation, so the reason for the empty slot stays traceable."""
    if not (is_cca_radiology_coordinator(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiology Coordinator or Admin may record a no-show")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "RADIOLOGY":
        raise HTTPException(404, "Not an imaging order")
    if order.workflow_state != "Scheduled":
        raise HTTPException(409, f"Order must be Scheduled to record a no-show (workflow_state={order.workflow_state})")
    actor = _actor(current_user)
    order.workflow_state = "NoShow"
    publish(
        db, "IMAGING_PATIENT_NO_SHOW", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Patient no-show: {order.item_name}", category="INVESTIGATION",
        description=f"{actor} recorded a no-show for {order.item_name} (was scheduled for {order.scheduled_at.isoformat() if order.scheduled_at else 'unknown'}).",
        order_id=order.id,
    )
    db.commit()
    return {"status": "success", "order": _order_out(order)}


@router.patch("/imaging/orders/{order_id}/preparation")
async def update_imaging_preparation(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Radiology Coordinator tracks fasting/contrast/renal-function prerequisites -- 'tracks
    requirements and escalates clinical questions; does not clinically interpret them' (spec)."""
    if not (is_cca_radiology_coordinator(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiology Coordinator or Admin may update preparation status")
    order = _get_org_order(db, order_id, _org_id(current_user))
    body = await request.json()
    prep_status = body.get("preparation_status")
    if prep_status not in ("NotRequired", "Pending", "Completed", "NeedsReview"):
        raise HTTPException(422, "preparation_status must be NotRequired, Pending, Completed, or NeedsReview")
    order.preparation_status = prep_status
    order.preparation_notes = body.get("preparation_notes")
    if prep_status == "NeedsReview":
        # 'escalates clinical questions; does not clinically interpret them' (spec) -- this is
        # the one preparation-status value that means something needs a clinician's attention,
        # so it's the one worth an audit event rather than routine logistics noise.
        actor = _actor(current_user)
        publish(
            db, "IMAGING_PREPARATION_NEEDS_REVIEW", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
            title=f"Imaging preparation needs review: {order.item_name}", category="INVESTIGATION",
            description=f"{actor} flagged preparation for {order.item_name} as needing clinical review." + (f" {order.preparation_notes}" if order.preparation_notes else ""),
            order_id=order.id,
        )
    db.commit()
    return {"status": "success", "order": _order_out(order)}


_ACQUISITION_STATUSES = ("NotStarted", "InProgress", "Completed", "Aborted")


@router.get("/imaging/technical-worklist")
def imaging_technical_worklist(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Radiology Technician's own worklist (7 Role/Module Updates developer handoff) --
    imaging orders not yet acquisition-complete, the technical-execution step between
    Radiology Coordinator's scheduling/preparation and the Radiologist's interpretation."""
    if not (is_cca_radiology_technician(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiology Technician may view the technical worklist")
    rows = db.query(CCAOrder, CCAPatient).join(
        CCAPatient, CCAOrder.patient_id == CCAPatient.id
    ).filter(
        CCAPatient.organization_id == _org_id(current_user), CCAOrder.order_type == "RADIOLOGY",
        CCAOrder.acquisition_status != "Completed",
    ).order_by(CCAOrder.ordered_at.desc()).all()
    return {"worklist": [_order_out(o, p.name, p.mrn) for o, p in rows]}


@router.patch("/imaging/orders/{order_id}/acquisition")
async def update_imaging_acquisition(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Records the technical imaging-acquisition step -- identity/study verification (attested
    by the technician, never system-checked), modality/protocol/contrast capture, and technical
    completion/issues. Owned entirely by the Radiology Technician: does NOT touch CCAResult or
    any interpretation field -- that stays exclusively the Radiologist's (draft/finalize below),
    matching the separation requirement in the 7 Role/Module Updates developer handoff."""
    if not (is_cca_radiology_technician(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiology Technician may record imaging acquisition")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "RADIOLOGY":
        raise HTTPException(404, "Not an imaging order")
    body = await request.json()
    status_value = body.get("acquisition_status")
    if status_value not in _ACQUISITION_STATUSES:
        raise HTTPException(422, f"acquisition_status must be one of {_ACQUISITION_STATUSES}")
    order.acquisition_status = status_value
    order.acquisition_modality = body.get("acquisition_modality")
    order.acquisition_protocol = body.get("acquisition_protocol")
    order.contrast_used = body.get("contrast_used")
    order.contrast_notes = body.get("contrast_notes")
    order.technical_notes = body.get("technical_notes")
    order.technical_issue = body.get("technical_issue")
    actor = _actor(current_user)
    if status_value == "Completed":
        order.acquired_by = actor
        order.acquired_at = datetime.utcnow()
        publish(
            db, "IMAGING_ACQUISITION_COMPLETED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
            title=f"Imaging acquired: {order.item_name}", category="INVESTIGATION",
            description=f"{actor} completed technical acquisition for {order.item_name}, ready for radiologist review.",
            order_id=order.id,
        )
    db.commit()
    return {"status": "success", "order": _order_out(order)}


@router.post("/imaging/orders/{order_id}/report")
async def draft_imaging_report(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Radiologist drafts/edits a structured report (Technique/Findings/Measurements/
    Comparison/Impression). One Draft CCAResult per order -- calling again while still Draft
    updates it in place; a Finalized report is closed (see finalize endpoint below)."""
    if not (is_cca_radiologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiologist or Admin may draft an imaging report")
    org_id = _org_id(current_user)
    order = _get_org_order(db, order_id, org_id)
    if order.order_type != "RADIOLOGY":
        raise HTTPException(404, "Not an imaging order")
    body = await request.json()

    result = db.query(CCAResult).filter(CCAResult.order_id == order.id, CCAResult.report_status == "Draft").first()
    if not result:
        result = CCAResult(order_id=order.id, patient_id=order.patient_id, result_type="IMAGING", title=order.item_name)
        db.add(result)

    result.technique = body.get("technique")
    result.findings_text = body.get("findings_text")
    result.comparison = body.get("comparison")
    result.impression = body.get("impression")
    result.structured_report = body.get("structured_report")  # measurements, staging-relevant summary, response summary
    result.is_critical = bool(body.get("is_critical", False))
    result.status = "PENDING_REVIEW" if result.is_critical else "NEW"
    result.report_status = "Draft"
    db.commit()
    db.refresh(result)
    return {"status": "success", "result": _result_out(result)}


@router.post("/imaging/results/{result_id}/finalize")
def finalize_imaging_report(result_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Sign/finalize -- 'No autonomous final report' (spec): a report only ever finalizes on
    an explicit Radiologist action, never automatically."""
    if not (is_cca_radiologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiologist or Admin may finalize an imaging report")
    org_id = _org_id(current_user)
    result = _get_org_result(db, result_id, org_id)
    if result.result_type != "IMAGING":
        raise HTTPException(404, "Not an imaging result")
    actor = _actor(current_user)
    result.report_status = "Finalized"
    result.finalized_by = actor
    result.finalized_at = datetime.utcnow()

    order = db.query(CCAOrder).filter(CCAOrder.id == result.order_id).first()
    if order:
        order.status = "RESULTED"
        order.workflow_state = "ReportFinalized"

    # event_type kept as the pre-existing "IMAGING_REPORT_FINALIZED" (not the architecture
    # doc's suggested "RADIOLOGY_REPORT_FINAL") since it's already what the journey timeline
    # and tests/integration/test_cca_diagnostics_module.py look for -- the spec itself notes
    # its event names are "implementation-oriented" suggestions to adapt to the existing
    # naming standard, not literal strings to match.
    publish(
        db, "IMAGING_REPORT_FINALIZED", patient_id=result.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Imaging Report Finalized: {result.title}", category="INVESTIGATION",
        description=f"{actor} finalized the imaging report.",
        result_id=result.id, order_id=result.order_id, is_critical=result.is_critical,
    )
    db.commit()
    db.refresh(result)
    return {"status": "success", "result": _result_out(result)}


# ---------------------------------------------------------------------------
# Pathology / Molecular Diagnostics
# ---------------------------------------------------------------------------

@router.get("/pathology/worklist")
def pathology_worklist(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_diagnostics_read(current_user, is_cca_pathologist)
    return {"worklist": _worklist(db, _org_id(current_user), "PATHOLOGY")}


@router.get("/pathology/quality-dashboard")
def pathology_quality_dashboard(overdue_hours: int = 72, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Pathology Quality / TAT Dashboard (reference SCR-PAT-018, worklist/dashboard
    follow-up round) -- volume by status, turn-around-time, overdue reports and the
    critical-result/amendment counts, all plain aggregation over CCAOrder/CCAResult rows
    that already exist. overdue_hours is an operational TAT threshold the caller can adjust
    for display filtering (default 72h) -- not a clinical/dosage judgment, the same class of
    "elapsed time since an operational event" arithmetic already used by the Live Infusion
    Board's observation_overdue flag."""
    _require_diagnostics_read(current_user, is_cca_pathologist)
    org_id = _org_id(current_user)
    now = datetime.utcnow()
    orders = db.query(CCAOrder).join(
        CCAPatient, CCAPatient.id == CCAOrder.patient_id
    ).filter(CCAPatient.organization_id == org_id, CCAOrder.order_type == "PATHOLOGY").all()

    by_status = {}
    overdue = []
    for o in orders:
        by_status[o.status] = by_status.get(o.status, 0) + 1
        has_final = db.query(CCAResult.id).filter(CCAResult.order_id == o.id, CCAResult.report_status == "Finalized").first()
        if not has_final and o.ordered_at and (now - o.ordered_at).total_seconds() / 3600 > overdue_hours:
            overdue.append({"order_id": o.id, "patient_id": o.patient_id, "item_name": o.item_name, "hours_open": round((now - o.ordered_at).total_seconds() / 3600, 1)})

    # CCAResult has no organization_id of its own -- scope via this org's own pathology
    # orders above rather than a second cross-table lookup.
    order_ids = [o.id for o in orders]
    finalized_results = db.query(CCAResult).filter(
        CCAResult.order_id.in_(order_ids), CCAResult.report_status == "Finalized"
    ).all() if order_ids else []
    tat_hours = [
        (r.finalized_at - r.resulted_at).total_seconds() / 3600
        for r in finalized_results if r.finalized_at and r.resulted_at
    ]
    amendment_count = sum(1 for r in finalized_results if r.amendment_reason)
    critical_results = [r for r in finalized_results if r.is_critical]
    critical_notified = sum(1 for r in critical_results if r.critical_notified_to)

    return {
        "total_orders": len(orders), "orders_by_status": by_status,
        "average_tat_hours": round(sum(tat_hours) / len(tat_hours), 1) if tat_hours else None,
        "overdue_reports": overdue, "overdue_count": len(overdue), "overdue_threshold_hours": overdue_hours,
        "amendment_count": amendment_count,
        "critical_result_count": len(critical_results), "critical_notified_count": critical_notified,
    }


@router.get("/pathology/orders/{order_id}")
def get_pathology_order(order_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_diagnostics_read(current_user, is_cca_pathologist)
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "PATHOLOGY":
        raise HTTPException(404, "Not a pathology order")
    results = db.query(CCAResult).filter(CCAResult.order_id == order.id).order_by(CCAResult.resulted_at.desc()).all()
    accession = db.query(PathologySpecimenAccession).filter(PathologySpecimenAccession.order_id == order.id).order_by(PathologySpecimenAccession.id.desc()).first()
    return {"order": _order_out(order), "results": [_result_out(r) for r in results], "accession": _accession_out(accession) if accession else None}


_ACCESSION_CONDITIONS = ("Intact", "Leaking", "Damaged Packaging", "Fixative Insufficient", "Other")


def _accession_out(a: PathologySpecimenAccession) -> dict:
    return {
        "id": a.id, "order_id": a.order_id, "accession_number": a.accession_number,
        "container_count": a.container_count, "condition_on_receipt": a.condition_on_receipt,
        "labelling_concordant": bool(a.labelling_concordant), "discrepancy_note": a.discrepancy_note,
        "status": a.status, "received_by": a.received_by, "received_at": a.received_at.isoformat() if a.received_at else None,
        "surgical_specimen_id": a.surgical_specimen_id,
    }


@router.post("/pathology/orders/{order_id}/accession", status_code=201)
async def accession_specimen(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Specimen Receipt & Accession (reference SCR-PAT-002, safety/dataflow-critical
    follow-up round) -- the gate draft_pathology_report now checks before a report can be
    started. A discrepancy (bad condition or labelling mismatch) quarantines the specimen
    instead of accepting it, and requires a documented note."""
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may accession a specimen")
    org_id = _org_id(current_user)
    order = _get_org_order(db, order_id, org_id)
    if order.order_type != "PATHOLOGY":
        raise HTTPException(404, "Not a pathology order")
    body = await request.json()
    accession_number = (body.get("accession_number") or "").strip()
    if not accession_number:
        raise HTTPException(422, "accession_number is required")
    condition = body.get("condition_on_receipt")
    if condition is not None and condition not in _ACCESSION_CONDITIONS:
        raise HTTPException(422, f"condition_on_receipt must be one of {_ACCESSION_CONDITIONS}")
    labelling_concordant = bool(body.get("labelling_concordant", True))
    has_discrepancy = (not labelling_concordant) or (condition not in (None, "Intact"))
    discrepancy_note = (body.get("discrepancy_note") or "").strip()
    if has_discrepancy and not discrepancy_note:
        raise HTTPException(422, "discrepancy_note is required when condition_on_receipt is not Intact or labelling is discordant")
    # Surgical-oncologist missing-development round: optional traceability link back to the
    # OR-side SurgicalSpecimen this accession corresponds to -- previously zero linkage existed
    # between the two (every Pathology* model keyed off order_id alone). Purely a link; this
    # accession's own ACCEPTED/QUARANTINED status stays independent of the surgical team's own
    # chain-of-custody status on SurgicalSpecimen (same separation-of-concerns precedent as
    # AnaesthesiaIntraOpRecord staying independent of SurgicalOperativeNote).
    surgical_specimen_id = body.get("surgical_specimen_id")
    if surgical_specimen_id is not None:
        linked_specimen = db.query(SurgicalSpecimen).filter(
            SurgicalSpecimen.id == surgical_specimen_id, SurgicalSpecimen.patient_id == order.patient_id,
        ).first()
        if not linked_specimen:
            raise HTTPException(422, "surgical_specimen_id does not reference a specimen for this patient")
    accession = PathologySpecimenAccession(
        order_id=order_id, patient_id=order.patient_id, accession_number=accession_number,
        container_count=body.get("container_count"), condition_on_receipt=condition,
        labelling_concordant=labelling_concordant, discrepancy_note=discrepancy_note or None,
        status="QUARANTINED" if has_discrepancy else "ACCEPTED", received_by=_actor(current_user),
        surgical_specimen_id=surgical_specimen_id,
    )
    db.add(accession)
    db.commit()
    db.refresh(accession)
    return {"status": "success", "accession": _accession_out(accession)}


# Structured report fields the pathologist personally types (Product 1 vs Product 2 gap
# report, Batch 6) -- gross/microscopic description, histologic type/grade, tumour extent,
# margins, lymph nodes, pathological TNM/stage grouping. stage_group is pathologist-typed
# free text in every real Product 1 sample, never derived from path_t/path_n/path_m by
# code -- this repo's standing rule against computed clinical judgments forbids deriving it
# here either.
_PATHOLOGY_REQUIRED_FOR_FINALIZE = ("site", "specimen", "histology")


def _check_node_coherence(structured_report: dict):
    """Plain arithmetic sanity check on two counts the pathologist already typed -- nodes
    positive cannot exceed nodes examined. Not a clinical judgment or computed threshold,
    the same class of check as validating a percentage is between 0 and 100."""
    if not structured_report:
        return
    examined, positive = structured_report.get("nodes_examined"), structured_report.get("nodes_positive")
    if examined is not None and positive is not None:
        try:
            if float(positive) > float(examined):
                raise HTTPException(422, "nodes_positive cannot exceed nodes_examined")
        except (TypeError, ValueError):
            pass


@router.post("/pathology/orders/{order_id}/report")
async def draft_pathology_report(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Pathologist drafts a structured report: gross/microscopic description, histologic
    type/grade, tumour extent, margins, lymph nodes, pathological staging evidence -- all held
    in structured_report since these vary by tumour type/specimen (spec: 'context dependent').

    Once the current report is Finalized, this becomes immutable (Batch 6) -- a further call
    must carry `amendment_reason` and creates a NEW, separately-finalizable result linked back
    to the original via supersedes_id/superseded_by_id, rather than mutating finalized content
    in place."""
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may draft a pathology report")
    org_id = _org_id(current_user)
    order = _get_org_order(db, order_id, org_id)
    if order.order_type != "PATHOLOGY":
        raise HTTPException(404, "Not a pathology order")
    # Specimen Receipt & Accession gate (reference SCR-PAT-002, safety/dataflow-critical
    # follow-up round) -- a report cannot be drafted until the specimen has been accessioned
    # and accepted (not quarantined for a discrepancy).
    accession = db.query(PathologySpecimenAccession).filter(
        PathologySpecimenAccession.order_id == order_id
    ).order_by(PathologySpecimenAccession.id.desc()).first()
    if not accession:
        raise HTTPException(409, "Cannot draft a report: this specimen has not been accessioned yet")
    if accession.status != "ACCEPTED":
        raise HTTPException(409, f"Cannot draft a report: specimen accession is {accession.status}, not ACCEPTED")
    body = await request.json()
    structured_report = body.get("structured_report")
    _check_node_coherence(structured_report)

    # "Current" = not yet superseded by a later amendment, whether Draft or Finalized.
    result = db.query(CCAResult).filter(
        CCAResult.order_id == order.id, CCAResult.superseded_by_id.is_(None)
    ).order_by(CCAResult.id.desc()).first()

    if result and result.report_status == "Finalized":
        amendment_reason = (body.get("amendment_reason") or "").strip()
        if not amendment_reason:
            raise HTTPException(409, "A finalized pathology report is immutable. Provide amendment_reason to create a linked amendment.")
        amendment = CCAResult(
            order_id=order.id, patient_id=order.patient_id, result_type="PATHOLOGY", title=order.item_name,
            supersedes_id=result.id, amendment_reason=amendment_reason, amended_by=_actor(current_user),
            amended_at=datetime.utcnow(),
        )
        db.add(amendment)
        db.flush()
        result.superseded_by_id = amendment.id
        result.report_status = "Superseded"
        result = amendment
    elif not result:
        result = CCAResult(order_id=order.id, patient_id=order.patient_id, result_type="PATHOLOGY", title=order.item_name)
        db.add(result)

    result.findings_text = body.get("findings_text")  # final diagnosis / comment-interpretation
    result.impression = body.get("impression")
    result.structured_report = structured_report  # gross/microscopic/histology/grade/margins/nodes/staging evidence
    result.is_critical = bool(body.get("is_critical", False))
    result.status = "PENDING_REVIEW" if result.is_critical else "NEW"
    result.report_status = "Draft"
    db.commit()
    db.refresh(result)
    return {"status": "success", "result": _result_out(result)}


@router.post("/pathology/results/{result_id}/finalize")
def finalize_pathology_report(result_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may finalize a pathology report")
    org_id = _org_id(current_user)
    result = _get_org_result(db, result_id, org_id)
    if result.result_type != "PATHOLOGY":
        raise HTTPException(404, "Not a pathology result")
    if result.report_status == "Finalized":
        raise HTTPException(409, "This report is already finalized")
    structured = result.structured_report or {}
    missing = [f for f in _PATHOLOGY_REQUIRED_FOR_FINALIZE if not structured.get(f)]
    if missing:
        raise HTTPException(409, f"Cannot finalize: missing required fields -- {', '.join(missing)}")
    actor = _actor(current_user)
    result.report_status = "Finalized"
    result.finalized_by = actor
    result.finalized_at = datetime.utcnow()

    order = db.query(CCAOrder).filter(CCAOrder.id == result.order_id).first()
    if order:
        order.status = "RESULTED"
        order.workflow_state = "ReportFinalized"

    publish(
        db, "PATHOLOGY_REPORT_FINALIZED", patient_id=result.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Pathology Report Finalized: {result.title}", category="INVESTIGATION",
        description=f"{actor} finalized the pathology report.",
        result_id=result.id, order_id=result.order_id, is_critical=result.is_critical,
    )
    db.commit()
    db.refresh(result)
    return {"status": "success", "result": _result_out(result)}


@router.get("/pathology/orders/{order_id}/neoadjuvant-context")
def get_neoadjuvant_context(order_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """The Pathological Treatment Response screen's (reference SCR-PAT-008) "read-only/
    derived" section -- feature completion round. No new table needed: response_grading_
    system/response_grade_category/pathologic_complete_response are pathologist-typed
    keys within CCAResult.structured_report, the same free-form JSON every other synoptic
    field already uses (nothing restricts which keys may be stored there). This endpoint
    supplies only the neoadjuvant-therapy context, computed at read time from existing
    TreatmentPlan/TreatmentOrder rows, never a second copy of that data."""
    _require_diagnostics_read(current_user, is_cca_pathologist)
    order = _get_org_order(db, order_id, _org_id(current_user))
    plans = db.query(TreatmentPlan).filter(TreatmentPlan.patient_id == order.patient_id, TreatmentPlan.intent.ilike("%neoadjuvant%")).order_by(TreatmentPlan.id.desc()).all()
    if not plans:
        return {"neoadjuvant_therapy_received": False, "therapy_completion_date": None, "interval_to_specimen_days": None, "neoadjuvant_treatment_summary": None}
    latest_plan = plans[0]
    last_order = db.query(TreatmentOrder).filter(TreatmentOrder.treatment_plan_id == latest_plan.id).order_by(TreatmentOrder.id.desc()).first()
    completion_date = last_order.signed_at.date() if last_order and last_order.signed_at else None
    interval_days = (order.ordered_at.date() - completion_date).days if (completion_date and order.ordered_at) else None
    return {
        "neoadjuvant_therapy_received": True,
        "neoadjuvant_treatment_summary": f"{latest_plan.modality} ({latest_plan.protocol_name or 'protocol not recorded'})",
        "therapy_completion_date": completion_date.isoformat() if completion_date else None,
        "interval_to_specimen_days": interval_days,
    }


# ---------------------------------------------------------------------------
# Feature completion round: Block/Slide registry, Archive/Custody, Frozen Section, Second
# Opinion/External Review, Pathology MDT Review Note (reference SCR-PAT-004/017/012/013/016).
# ---------------------------------------------------------------------------

def _block_slide_out(b: PathologyBlockSlide) -> dict:
    return {
        "id": b.id, "order_id": b.order_id, "patient_id": b.patient_id, "item_type": b.item_type,
        "block_or_slide_id": b.block_or_slide_id, "tissue": b.tissue, "processing_status": b.processing_status,
        "stain": b.stain, "qc_status": b.qc_status, "location": b.location, "assigned_to": b.assigned_to,
    }


_BLOCK_SLIDE_PROCESSING_STATUSES = ("Pending", "Processing", "Cut", "Stained", "QC", "Ready", "Archived")


@router.post("/pathology/orders/{order_id}/block-slides", status_code=201)
async def add_block_slide(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may register a block/slide")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "PATHOLOGY":
        raise HTTPException(404, "Not a pathology order")
    body = await request.json()
    item_type = body.get("item_type")
    block_or_slide_id = (body.get("block_or_slide_id") or "").strip()
    if item_type not in ("Block", "Slide"):
        raise HTTPException(422, "item_type must be one of Block, Slide")
    if not block_or_slide_id:
        raise HTTPException(422, "block_or_slide_id is required")
    row = PathologyBlockSlide(
        order_id=order_id, patient_id=order.patient_id, item_type=item_type, block_or_slide_id=block_or_slide_id,
        tissue=body.get("tissue"), stain=body.get("stain"), location=body.get("location"),
        assigned_to=body.get("assigned_to"), created_by=_actor(current_user),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "success", "block_slide": _block_slide_out(row)}


@router.get("/pathology/orders/{order_id}/block-slides")
def list_block_slides(order_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_diagnostics_read(current_user, is_cca_pathologist)
    order = _get_org_order(db, order_id, _org_id(current_user))
    rows = db.query(PathologyBlockSlide).filter(PathologyBlockSlide.order_id == order.id).order_by(PathologyBlockSlide.id.asc()).all()
    return {"block_slides": [_block_slide_out(b) for b in rows]}


@router.get("/pathology/processing-queue")
def pathology_processing_queue(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Block / Slide Management & Processing Queue (reference SCR-PAT-004) -- every block/
    slide not yet Archived, org-wide."""
    _require_diagnostics_read(current_user, is_cca_pathologist)
    org_id = _org_id(current_user)
    rows = db.query(PathologyBlockSlide).join(
        CCAPatient, CCAPatient.id == PathologyBlockSlide.patient_id
    ).filter(CCAPatient.organization_id == org_id, PathologyBlockSlide.processing_status != "Archived").order_by(PathologyBlockSlide.id.desc()).all()
    results = []
    for b in rows:
        patient = db.query(CCAPatient).filter(CCAPatient.id == b.patient_id).first()
        results.append({**_block_slide_out(b), "patient_name": patient.name if patient else None, "mrn": patient.mrn if patient else None})
    return {"queue": results, "total": len(results)}


@router.post("/pathology/block-slides/{id}/update")
async def update_block_slide(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may update a block/slide")
    row = db.query(PathologyBlockSlide).filter(PathologyBlockSlide.id == id).first()
    if not row:
        raise HTTPException(404, "Block/slide not found")
    _check_patient_in_org(db, row.patient_id, _org_id(current_user))
    body = await request.json()
    if "processing_status" in body:
        if body["processing_status"] not in _BLOCK_SLIDE_PROCESSING_STATUSES:
            raise HTTPException(422, f"processing_status must be one of {_BLOCK_SLIDE_PROCESSING_STATUSES}")
        row.processing_status = body["processing_status"]
    for field in ("stain", "qc_status", "location", "assigned_to"):
        if field in body:
            setattr(row, field, body[field])
    db.commit()
    db.refresh(row)
    return {"status": "success", "block_slide": _block_slide_out(row)}


def _custody_event_out(c: PathologyCustodyEvent) -> dict:
    return {
        "id": c.id, "block_slide_id": c.block_slide_id, "custody_status": c.custody_status,
        "location": c.location, "released_to": c.released_to, "released_at": c.released_at.isoformat() if c.released_at else None,
        "expected_return": c.expected_return.isoformat() if c.expected_return else None,
        "returned_at": c.returned_at.isoformat() if c.returned_at else None, "disposition": c.disposition,
        "recorded_by": c.recorded_by, "recorded_at": c.recorded_at.isoformat() if c.recorded_at else None,
    }


@router.post("/pathology/block-slides/{id}/custody-events", status_code=201)
async def add_custody_event(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may record a custody event")
    block_slide = db.query(PathologyBlockSlide).filter(PathologyBlockSlide.id == id).first()
    if not block_slide:
        raise HTTPException(404, "Block/slide not found")
    _check_patient_in_org(db, block_slide.patient_id, _org_id(current_user))
    body = await request.json()
    custody_status = body.get("custody_status")
    if custody_status not in ("Archived", "Loaned", "Returned", "Disposed"):
        raise HTTPException(422, "custody_status must be one of Archived, Loaned, Returned, Disposed")

    def _parse_date(key):
        value = body.get(key)
        return datetime.fromisoformat(value).date() if value else None

    row = PathologyCustodyEvent(
        block_slide_id=id, patient_id=block_slide.patient_id, custody_status=custody_status,
        location=body.get("location"), released_to=body.get("released_to"),
        released_at=datetime.utcnow() if custody_status == "Loaned" else None,
        expected_return=_parse_date("expected_return"),
        returned_at=datetime.utcnow() if custody_status == "Returned" else None,
        disposition=body.get("disposition"), recorded_by=_actor(current_user),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "success", "custody_event": _custody_event_out(row)}


@router.get("/pathology/custody-inventory")
def pathology_custody_inventory(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Specimen / Block / Slide Archive & Custody (reference SCR-PAT-017) -- current custody
    status (most recent event) for every block/slide item, org-wide."""
    _require_diagnostics_read(current_user, is_cca_pathologist)
    org_id = _org_id(current_user)
    items = db.query(PathologyBlockSlide).join(
        CCAPatient, CCAPatient.id == PathologyBlockSlide.patient_id
    ).filter(CCAPatient.organization_id == org_id).all()
    results = []
    for b in items:
        patient = db.query(CCAPatient).filter(CCAPatient.id == b.patient_id).first()
        latest_event = db.query(PathologyCustodyEvent).filter(PathologyCustodyEvent.block_slide_id == b.id).order_by(PathologyCustodyEvent.id.desc()).first()
        results.append({
            **_block_slide_out(b), "patient_name": patient.name if patient else None, "mrn": patient.mrn if patient else None,
            "custody_status": latest_event.custody_status if latest_event else "Not Archived",
            "current_location": latest_event.location if latest_event else b.location,
        })
    return {"inventory": results, "total": len(results)}


def _frozen_section_out(f: PathologyFrozenSection) -> dict:
    return {
        "id": f.id, "order_id": f.order_id, "patient_id": f.patient_id, "theatre": f.theatre,
        "question_from_surgeon": f.question_from_surgeon,
        "specimen_received_at": f.specimen_received_at.isoformat() if f.specimen_received_at else None,
        "frozen_impression": f.frozen_impression, "communicated_to": f.communicated_to,
        "communication_method": f.communication_method,
        "communicated_at": f.communicated_at.isoformat() if f.communicated_at else None,
        "acknowledged_by": f.acknowledged_by, "acknowledged_at": f.acknowledged_at.isoformat() if f.acknowledged_at else None,
        "permanent_result_concordance": f.permanent_result_concordance, "permanent_result_id": f.permanent_result_id,
        "created_by": f.created_by, "created_at": f.created_at.isoformat() if f.created_at else None,
    }


@router.post("/patients/{patient_id}/frozen-sections", status_code=201)
async def create_frozen_section(patient_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Frozen Section / Intra-operative Pathology (reference SCR-PAT-012) -- a real,
    time-critical intraoperative consultation, entirely absent before this round."""
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may record a frozen section consultation")
    _get_org_patient(db, patient_id, _org_id(current_user))
    body = await request.json()
    question = (body.get("question_from_surgeon") or "").strip()
    if not question:
        raise HTTPException(422, "question_from_surgeon is required")
    row = PathologyFrozenSection(
        patient_id=patient_id, order_id=body.get("order_id"), theatre=body.get("theatre"),
        question_from_surgeon=question, specimen_received_at=datetime.utcnow(),
        frozen_impression=body.get("frozen_impression"), created_by=_actor(current_user),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "success", "frozen_section": _frozen_section_out(row)}


@router.post("/frozen-sections/{id}/communicate")
async def communicate_frozen_section(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    row = db.query(PathologyFrozenSection).filter(PathologyFrozenSection.id == id).first()
    if not row:
        raise HTTPException(404, "Frozen section not found")
    _check_patient_in_org(db, row.patient_id, _org_id(current_user))
    body = await request.json()
    communicated_to = (body.get("communicated_to") or "").strip()
    method = (body.get("communication_method") or "").strip()
    if not (communicated_to and method):
        raise HTTPException(422, "communicated_to and communication_method are required")
    if not (row.frozen_impression or body.get("frozen_impression")):
        raise HTTPException(409, "frozen_impression must be recorded before it can be communicated")
    if body.get("frozen_impression"):
        row.frozen_impression = body["frozen_impression"]
    row.communicated_to = communicated_to
    row.communication_method = method
    row.communicated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"status": "success", "frozen_section": _frozen_section_out(row)}


@router.post("/frozen-sections/{id}/acknowledge")
async def acknowledge_frozen_section(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    row = db.query(PathologyFrozenSection).filter(PathologyFrozenSection.id == id).first()
    if not row:
        raise HTTPException(404, "Frozen section not found")
    _check_patient_in_org(db, row.patient_id, _org_id(current_user))
    if not row.communicated_at:
        raise HTTPException(409, "Cannot acknowledge before the impression has been communicated")
    body = await request.json()
    row.acknowledged_by = (body.get("acknowledged_by") or _actor(current_user))
    row.acknowledged_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"status": "success", "frozen_section": _frozen_section_out(row)}


@router.post("/frozen-sections/{id}/reconcile")
async def reconcile_frozen_section(id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Permanent result concordance -- the pathologist's own conclusion once the permanent
    (paraffin) report is finalized, never a computed text-diff against the frozen impression."""
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may reconcile a frozen section")
    row = db.query(PathologyFrozenSection).filter(PathologyFrozenSection.id == id).first()
    if not row:
        raise HTTPException(404, "Frozen section not found")
    _check_patient_in_org(db, row.patient_id, _org_id(current_user))
    body = await request.json()
    concordance = body.get("permanent_result_concordance")
    if concordance not in ("Concordant", "Discordant", "Pending"):
        raise HTTPException(422, "permanent_result_concordance must be one of Concordant, Discordant, Pending")
    row.permanent_result_concordance = concordance
    row.permanent_result_id = body.get("permanent_result_id")
    db.commit()
    db.refresh(row)
    return {"status": "success", "frozen_section": _frozen_section_out(row)}


@router.get("/patients/{patient_id}/frozen-sections")
def list_frozen_sections(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(PathologyFrozenSection).filter(PathologyFrozenSection.patient_id == patient_id).order_by(PathologyFrozenSection.id.desc()).all()
    return {"frozen_sections": [_frozen_section_out(f) for f in rows]}


def _second_opinion_out(s: PathologySecondOpinion) -> dict:
    return {
        "id": s.id, "patient_id": s.patient_id, "order_id": s.order_id, "external_institution": s.external_institution,
        "external_accession": s.external_accession, "material_received": s.material_received or [],
        "prior_diagnosis": s.prior_diagnosis, "review_diagnosis": s.review_diagnosis, "concordance": s.concordance,
        "clinical_impact": s.clinical_impact, "reviewed_by": s.reviewed_by,
        "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
    }


@router.post("/patients/{patient_id}/second-opinions", status_code=201)
async def create_second_opinion(patient_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Second Opinion / External Pathology Review (reference SCR-PAT-013)."""
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may record a second opinion review")
    _get_org_patient(db, patient_id, _org_id(current_user))
    body = await request.json()
    institution = (body.get("external_institution") or "").strip()
    prior_diagnosis = (body.get("prior_diagnosis") or "").strip()
    review_diagnosis = (body.get("review_diagnosis") or "").strip()
    concordance = body.get("concordance")
    if not (institution and prior_diagnosis and review_diagnosis):
        raise HTTPException(422, "external_institution, prior_diagnosis and review_diagnosis are required")
    if concordance not in ("Concordant", "Minor Discrepancy", "Major Discrepancy"):
        raise HTTPException(422, "concordance must be one of Concordant, Minor Discrepancy, Major Discrepancy")
    row = PathologySecondOpinion(
        patient_id=patient_id, order_id=body.get("order_id"), external_institution=institution,
        external_accession=body.get("external_accession"), material_received=body.get("material_received"),
        prior_diagnosis=prior_diagnosis, review_diagnosis=review_diagnosis, concordance=concordance,
        clinical_impact=body.get("clinical_impact"), reviewed_by=_actor(current_user),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "success", "second_opinion": _second_opinion_out(row)}


@router.get("/patients/{patient_id}/second-opinions")
def list_second_opinions(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(PathologySecondOpinion).filter(PathologySecondOpinion.patient_id == patient_id).order_by(PathologySecondOpinion.id.desc()).all()
    return {"second_opinions": [_second_opinion_out(s) for s in rows]}


def _pathology_mdt_note_out(n: PathologyMdtReviewNote) -> dict:
    return {
        "id": n.id, "patient_id": n.patient_id, "mdt_case_id": n.mdt_case_id, "order_id": n.order_id,
        "material_reviewed": n.material_reviewed, "key_findings": n.key_findings,
        "diagnostic_staging_statement": n.diagnostic_staging_statement,
        "uncertainty_limitations": n.uncertainty_limitations, "recommendation": n.recommendation,
        "authored_by": n.authored_by, "authored_at": n.authored_at.isoformat() if n.authored_at else None,
    }


@router.post("/patients/{patient_id}/pathology-mdt-notes", status_code=201)
async def create_pathology_mdt_note(patient_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Pathology MDT Review Note (reference SCR-PAT-016)."""
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may author a pathology MDT review note")
    _get_org_patient(db, patient_id, _org_id(current_user))
    body = await request.json()
    material_reviewed = (body.get("material_reviewed") or "").strip()
    key_findings = (body.get("key_findings") or "").strip()
    diagnostic_statement = (body.get("diagnostic_staging_statement") or "").strip()
    if not (material_reviewed and key_findings and diagnostic_statement):
        raise HTTPException(422, "material_reviewed, key_findings and diagnostic_staging_statement are required")
    row = PathologyMdtReviewNote(
        patient_id=patient_id, mdt_case_id=body.get("mdt_case_id"), order_id=body.get("order_id"),
        material_reviewed=material_reviewed, key_findings=key_findings,
        diagnostic_staging_statement=diagnostic_statement, uncertainty_limitations=body.get("uncertainty_limitations"),
        recommendation=body.get("recommendation"), authored_by=_actor(current_user),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "success", "pathology_mdt_note": _pathology_mdt_note_out(row)}


@router.get("/patients/{patient_id}/pathology-mdt-notes")
def list_pathology_mdt_notes(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _get_org_patient(db, patient_id, _org_id(current_user))
    rows = db.query(PathologyMdtReviewNote).filter(PathologyMdtReviewNote.patient_id == patient_id).order_by(PathologyMdtReviewNote.id.desc()).all()
    return {"pathology_mdt_notes": [_pathology_mdt_note_out(n) for n in rows]}


def _biomarker_out(b: CCABiomarkerResult) -> dict:
    return {
        "id": b.id, "patient_id": b.patient_id, "marker_name": b.marker_name,
        "result_as_reported": b.result_as_reported, "method": b.method, "platform": b.platform,
        "specimen": b.specimen, "adequacy": b.adequacy, "lab_name": b.lab_name,
        "reported_on": b.reported_on.isoformat() if b.reported_on else None, "status": b.status,
        "confirmatory_required": b.confirmatory_required, "episode_id": b.episode_id,
    }


@router.get("/molecular/tests")
def list_molecular_tests(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_diagnostics_read(current_user, is_cca_pathologist)
    _get_org_patient(db, patient_id, _org_id(current_user))
    tests = db.query(CCABiomarkerResult).filter(CCABiomarkerResult.patient_id == patient_id).order_by(CCABiomarkerResult.reported_on.desc()).all()
    return {"tests": [_biomarker_out(t) for t in tests]}


@router.post("/molecular/tests", status_code=201)
async def order_molecular_test(request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_pathologist(current_user) or is_cca_oncologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist, a treating oncologist, or Admin may order a molecular test")
    org_id = _org_id(current_user)
    body = await request.json()
    patient_id = body.get("patient_id")
    marker_name = body.get("marker_name")
    if not patient_id or not marker_name:
        raise HTTPException(422, "patient_id and marker_name are required")
    _get_org_patient(db, patient_id, org_id)

    # Cancer Episode link (gap report item 5) -- optional; a molecular test works unchanged
    # without one.
    episode_id = body.get("episode_id")
    if episode_id is not None:
        episode = db.query(CancerEpisode).filter(CancerEpisode.id == episode_id, CancerEpisode.patient_id == patient_id).first()
        if not episode:
            raise HTTPException(422, "episode_id does not reference a Cancer Episode for this patient")

    test = CCABiomarkerResult(
        patient_id=patient_id, marker_name=marker_name, result_as_reported="Pending",
        method=body.get("method"), specimen=body.get("specimen"), status="PENDING",
        episode_id=episode_id,
    )
    db.add(test)
    db.flush()
    actor = _actor(current_user)
    publish(
        db, "MOLECULAR_TEST_ORDERED", patient_id=patient_id, actor=actor, role=current_user.get("role"),
        title=f"Molecular test ordered: {marker_name}", category="INVESTIGATION",
        description=f"{actor} ordered a {marker_name} molecular test.",
        test_id=test.id,
    )
    db.commit()
    db.refresh(test)
    return {"status": "success", "test": _biomarker_out(test)}


@router.patch("/molecular/tests/{test_id}")
async def record_molecular_result(test_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may record a molecular result")
    org_id = _org_id(current_user)
    test = db.query(CCABiomarkerResult).filter(CCABiomarkerResult.id == test_id).first()
    if not test:
        raise HTTPException(404, "Test not found")
    _check_patient_in_org(db, test.patient_id, org_id)

    body = await request.json()
    result_value = body.get("result_as_reported")
    if not result_value:
        raise HTTPException(422, "result_as_reported is required")
    test.result_as_reported = result_value
    test.method = body.get("method", test.method)
    test.platform = body.get("platform", test.platform)
    test.status = body.get("status", "RESULTED")
    test.confirmatory_required = body.get("confirmatory_required")
    actor = _actor(current_user)
    publish(
        db, "MOLECULAR_RESULT_RECORDED", patient_id=test.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Molecular result recorded: {test.marker_name}", category="INVESTIGATION",
        description=f"{actor} recorded {test.marker_name}: {result_value}.",
        test_id=test.id,
    )
    db.commit()
    db.refresh(test)
    return {"status": "success", "test": _biomarker_out(test)}


# ---------------------------------------------------------------------------
# Lab / Phlebotomy
#
# Order lifecycle (gap review, "Laboratory Order Lifecycle -- Critical" / "Result Verification &
# Release -- Critical"): Raised -> collect (identity confirmed, specimen accessioned) ->
# AwaitingLabReceipt -> receive -> Received -> draft a result (Entered, entered_by/at recorded)
# -> verify (Finalized, finalized_by/at recorded, order.status becomes RESULTED). A rejected
# specimen moves to RecollectionRequired and can never itself be resulted -- a NEW linked order
# is created via /recollect instead, exactly mirroring how a finalized pathology report is
# amended via a new linked CCAResult rather than mutated in place (see
# draft_pathology_report/finalize_pathology_report above).
# ---------------------------------------------------------------------------

# Structured rejection reasons (gap review "Specimen Rejection -- Critical") -- replaces the
# previous free-text `reason` field, which accepted anything typed. Codes, not the PDF's exact
# prose, to match this file's existing enum-like conventions (CCAOrder.status/order_type);
# frontend maps each code to a human label for the dropdown.
LAB_REJECTION_REASONS = (
    "HEMOLYSED", "CLOTTED", "INSUFFICIENT_QUANTITY", "WRONG_CONTAINER", "WRONG_SPECIMEN",
    "LEAKED_DAMAGED", "MISLABELED", "EXPIRED_TRANSPORT_ISSUE", "OTHER",
)


@router.get("/lab/worklist")
def lab_worklist(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_diagnostics_read(current_user, is_cca_lab_phlebotomy)
    worklist = _worklist(db, _org_id(current_user), "LAB")
    # The frontend needs to know whether an order's result is still a Draft (show "Verify &
    # Release") or already Finalized (show "Resulted") -- attach the CURRENT (not superseded)
    # LAB result's id/report_status per order rather than making the frontend fetch each one
    # individually.
    order_ids = [o["id"] for o in worklist]
    latest_results = {}
    if order_ids:
        for r in db.query(CCAResult).filter(
            CCAResult.order_id.in_(order_ids), CCAResult.result_type == "LAB", CCAResult.superseded_by_id.is_(None)
        ):
            latest_results[r.order_id] = {"id": r.id, "report_status": r.report_status}
    for o in worklist:
        o["latest_result"] = latest_results.get(o["id"])
    return {"worklist": worklist}


@router.get("/other-diagnostics/worklist")
def other_diagnostics_worklist(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """ECG and similar investigations that are neither a lab/specimen test nor an imaging
    study (oncologist feedback: these were previously mis-routed into Lab/Phlebotomy purely
    because nothing else classified them -- see ORDER_TYPE_KEYWORDS in the oncologist HTML
    pages and apply_order_set's old silent "LAB" default in cca.py). No dedicated role owns
    this category yet -- readable by Lab/Phlebotomy and Radiology Coordinator (the two existing
    roles closest to bedside/ancillary diagnostics) on top of _require_diagnostics_read's
    always-on oncologist/Admin access, same least-privilege pattern as every other worklist
    here."""
    _require_diagnostics_read(current_user, is_cca_lab_phlebotomy, is_cca_radiology_coordinator)
    return {"worklist": _worklist(db, _org_id(current_user), "OTHER_DIAGNOSTIC")}


@router.post("/lab/orders/{order_id}/collect")
async def collect_specimen(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Specimen collection -- gap review "Patient & Specimen Identification"/"Specimen
    Collection Workflow" (Critical): positive patient identification is a hard gate here, not
    just a recorded field, and a unique specimen/accession identifier is generated server-side
    (never client-supplied) rather than left for a barcode system that doesn't exist yet."""
    if not (is_cca_lab_phlebotomy(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Lab/Phlebotomy staff or Admin may record specimen collection")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "LAB":
        raise HTTPException(404, "Not a lab order")
    if order.workflow_state in ("RecollectionRequired", "RecollectionInitiated"):
        raise HTTPException(409, "This specimen was rejected -- use the recollection workflow to raise a new collection")
    if order.collected_at:
        raise HTTPException(409, "This order's specimen has already been collected")
    body = await request.json()
    if not body.get("identity_confirmed"):
        raise HTTPException(422, "identity_confirmed must be true -- positive patient identification is required before collection")
    actor = _actor(current_user)
    now = datetime.utcnow()
    order.identity_confirmed = True
    order.collected_by = actor
    order.collected_at = now
    order.specimen_container = body.get("specimen_container")
    order.collection_site = body.get("collection_site")
    order.specimen_count = body.get("specimen_count") or 1
    # Server-generated, never client-supplied -- the identifier a physical specimen label/
    # barcode would carry. Format is an internal convention, not a real accessioning standard;
    # good enough to be unique and human-traceable back to this exact order/collection event.
    order.specimen_accession_id = f"LAB-{order.id}-{now.strftime('%Y%m%d%H%M%S')}"
    order.workflow_state = "AwaitingLabReceipt"
    order.status = "IN_PROGRESS"
    publish(
        db, "SPECIMEN_COLLECTED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Specimen collected: {order.item_name}", category="INVESTIGATION",
        description=f"{actor} collected the specimen for {order.item_name} (accession {order.specimen_accession_id}).",
        order_id=order.id,
    )
    db.commit()
    db.refresh(order)
    return {"status": "success", "order": _order_out(order)}


@router.post("/lab/orders/{order_id}/receive")
async def receive_specimen(order_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Sample Routing & Laboratory Receipt -- records the lab actually taking custody of the
    specimen, distinct from collect_specimen's phlebotomist-side event. A result cannot be
    drafted until this has happened (see record_lab_result's own gate)."""
    if not (is_cca_lab_phlebotomy(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Lab/Phlebotomy staff or Admin may record specimen receipt")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "LAB":
        raise HTTPException(404, "Not a lab order")
    if not order.collected_at:
        raise HTTPException(409, "Cannot receive a specimen that has not been collected yet")
    if order.received_at:
        raise HTTPException(409, "This specimen has already been received")
    actor = _actor(current_user)
    order.received_by = actor
    order.received_at = datetime.utcnow()
    order.workflow_state = "Received"
    publish(
        db, "SPECIMEN_RECEIVED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Specimen received: {order.item_name}", category="INVESTIGATION",
        description=f"{actor} recorded laboratory receipt of the specimen for {order.item_name}.",
        order_id=order.id,
    )
    db.commit()
    db.refresh(order)
    return {"status": "success", "order": _order_out(order)}


@router.post("/lab/orders/{order_id}/reject")
async def reject_specimen(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_lab_phlebotomy(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Lab/Phlebotomy staff or Admin may reject a specimen")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.status == "RESULTED":
        raise HTTPException(409, "Cannot reject a specimen that already has a result")
    body = await request.json()
    reason = body.get("reason")
    if reason not in LAB_REJECTION_REASONS:
        raise HTTPException(422, f"reason must be one of: {', '.join(LAB_REJECTION_REASONS)}")
    order.rejection_reason = reason
    order.workflow_state = "RecollectionRequired"
    actor = _actor(current_user)
    # "Notify relevant clinical/ordering team when recollection is required" -- this journey
    # event/publish() call IS this app's existing notification mechanism (the same one every
    # other role's actions surface through); no separate notification channel exists to build.
    publish(
        db, "SPECIMEN_REJECTED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Specimen rejected: {order.item_name}", category="INVESTIGATION",
        description=f"{actor} rejected the specimen for {order.item_name}: {reason}. Recollection required.",
        order_id=order.id,
    )
    db.commit()
    return {"status": "success", "order": _order_out(order)}


@router.post("/lab/orders/{order_id}/recollect")
async def recollect_specimen(order_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Creates a NEW CCAOrder for a fresh collection against a rejected specimen's order --
    never reuses or resets the original, so its own collection/rejection history stays exactly
    as it happened (gap review: "Rejected specimen should not simply disappear... Link the new
    specimen to the original order and rejection event... Track recollection count"). The
    original is marked RecollectionInitiated (not left at RecollectionRequired) so this can't be
    called twice for the same rejection."""
    if not (is_cca_lab_phlebotomy(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Lab/Phlebotomy staff or Admin may initiate a recollection")
    original = _get_org_order(db, order_id, _org_id(current_user))
    if original.order_type != "LAB":
        raise HTTPException(404, "Not a lab order")
    if original.workflow_state != "RecollectionRequired":
        raise HTTPException(409, f"Order is not pending recollection (workflow_state={original.workflow_state})")

    new_order = CCAOrder(
        patient_id=original.patient_id, encounter_id=original.encounter_id, order_type="LAB",
        item_name=original.item_name, item_code=original.item_code,
        clinical_indication=original.clinical_indication, priority=original.priority,
        staging_relevant=original.staging_relevant, requested_by=original.requested_by,
        order_set_master_id=original.order_set_master_id,
        recollection_of_order_id=original.id, recollection_number=(original.recollection_number or 0) + 1,
    )
    db.add(new_order)
    original.workflow_state = "RecollectionInitiated"
    db.flush()

    actor = _actor(current_user)
    publish(
        db, "SPECIMEN_RECOLLECTION_INITIATED", patient_id=original.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Recollection raised: {original.item_name}", category="INVESTIGATION",
        description=f"{actor} raised a new collection (order #{new_order.id}) for {original.item_name} following rejection of order #{original.id}.",
        order_id=new_order.id,
    )
    db.commit()
    db.refresh(new_order)
    return {"status": "success", "order": _order_out(new_order)}


@router.post("/lab/orders/{order_id}/result")
async def record_lab_result(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Technical result ENTRY -- gap review "Result Verification & Release" (Critical): this no
    longer finalizes a result on its own. It creates/updates a Draft CCAResult (entered_by/at
    recorded); a separate call to /lab/results/{id}/verify is required before the result counts
    as released. Mirrors draft_pathology_report's own draft/amend shape exactly: editing an
    already-Finalized result requires `amendment_reason` and creates a new linked CCAResult via
    supersedes_id rather than mutating finalized content in place."""
    if not (is_cca_lab_phlebotomy(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Lab/Phlebotomy staff or Admin may record a lab result")
    org_id = _org_id(current_user)
    order = _get_org_order(db, order_id, org_id)
    if order.order_type != "LAB":
        raise HTTPException(404, "Not a lab order")
    if order.workflow_state in ("RecollectionRequired", "RecollectionInitiated"):
        raise HTTPException(409, "Cannot enter a result for a rejected specimen -- use the recollection workflow")
    if not order.received_at:
        raise HTTPException(409, "Cannot enter a result: this specimen has not been received by the laboratory yet")
    body = await request.json()
    findings = body.get("findings_text")
    if not findings:
        raise HTTPException(422, "findings_text is required")
    actor = _actor(current_user)

    # "Current" = not yet superseded by a later amendment, whether Draft or Finalized -- same
    # lookup draft_pathology_report uses.
    result = db.query(CCAResult).filter(
        CCAResult.order_id == order.id, CCAResult.result_type == "LAB", CCAResult.superseded_by_id.is_(None)
    ).order_by(CCAResult.id.desc()).first()

    if result and result.report_status == "Finalized":
        amendment_reason = (body.get("amendment_reason") or "").strip()
        if not amendment_reason:
            raise HTTPException(409, "This lab result is already verified and locked. Provide amendment_reason to create a linked correction.")
        amendment = CCAResult(
            order_id=order.id, patient_id=order.patient_id, result_type="LAB", title=order.item_name,
            supersedes_id=result.id, amendment_reason=amendment_reason, amended_by=actor, amended_at=datetime.utcnow(),
        )
        db.add(amendment)
        db.flush()
        result.superseded_by_id = amendment.id
        result.report_status = "Superseded"
        result = amendment
    elif not result:
        result = CCAResult(order_id=order.id, patient_id=order.patient_id, result_type="LAB", title=order.item_name)
        db.add(result)

    result.findings_text = findings
    result.extracted_values = body.get("extracted_values")
    result.is_critical = bool(body.get("is_critical", False))
    result.status = "PENDING_REVIEW" if result.is_critical else "NEW"
    result.report_status = "Draft"
    result.entered_by = actor
    result.entered_at = datetime.utcnow()
    order.workflow_state = "ResultEntered"
    db.commit()
    db.refresh(result)
    return {"status": "success", "result": _result_out(result)}


@router.post("/lab/results/{result_id}/verify")
def verify_lab_result(result_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Verification & release -- the "Authorized verifier" step (gap review section 15's
    permission table). Reuses the same Lab/Phlebotomy-or-Admin gate as entry for now (this
    codebase has no separate "lab verifier" role yet); a stricter "must not be the same person
    who entered it" rule would need that role to exist first, so it isn't enforced here. Locks
    the result against ordinary editing -- see record_lab_result's amendment path for what
    happens to a post-verification correction."""
    if not (is_cca_lab_phlebotomy(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Lab/Phlebotomy staff or Admin may verify and release a lab result")
    org_id = _org_id(current_user)
    result = _get_org_result(db, result_id, org_id)
    if result.result_type != "LAB":
        raise HTTPException(404, "Not a lab result")
    if result.report_status == "Finalized":
        raise HTTPException(409, "This result is already verified and released")
    actor = _actor(current_user)
    result.report_status = "Finalized"
    result.finalized_by = actor
    result.finalized_at = datetime.utcnow()

    order = db.query(CCAOrder).filter(CCAOrder.id == result.order_id).first()
    if order:
        order.status = "RESULTED"
        order.workflow_state = "ResultAvailable"

    publish(
        db, "LAB_RESULT_VERIFIED", patient_id=result.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Lab Result Verified: {result.title}", category="INVESTIGATION",
        description=f"{actor} verified and released the lab result (entered by {result.entered_by or 'unknown'}).",
        result_id=result.id, order_id=result.order_id, is_critical=result.is_critical,
    )
    db.commit()
    db.refresh(result)
    return {"status": "success", "result": _result_out(result)}
