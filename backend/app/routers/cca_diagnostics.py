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
)
from ..models_cca import CCAOrder, CCAResult, CCABiomarkerResult, CCAJourneyEvent, CCAPatient
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
    }


def _result_out(r: CCAResult) -> dict:
    return {
        "id": r.id, "order_id": r.order_id, "patient_id": r.patient_id, "result_type": r.result_type,
        "title": r.title, "findings_text": r.findings_text, "technique": r.technique,
        "comparison": r.comparison, "impression": r.impression, "structured_report": r.structured_report,
        "extracted_values": r.extracted_values, "is_critical": r.is_critical, "status": r.status,
        "report_status": r.report_status, "finalized_by": r.finalized_by,
        "finalized_at": r.finalized_at.isoformat() if r.finalized_at else None,
        "acknowledged_by": r.acknowledged_by,
        "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
        "critical_acknowledged_by": r.critical_acknowledged_by,
        "resulted_at": r.resulted_at.isoformat() if r.resulted_at else None,
    }


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
    return {"worklist": _worklist(db, _org_id(current_user), "RADIOLOGY")}


@router.get("/imaging/orders/{order_id}")
def get_imaging_order(order_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
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
    """Radiology Coordinator scheduling -- appointment date/time and scanner/room location."""
    if not (is_cca_radiology_coordinator(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Radiology Coordinator or Admin may schedule imaging")
    order = _get_org_order(db, order_id, _org_id(current_user))
    body = await request.json()
    scheduled_at = body.get("scheduled_at")
    if not scheduled_at:
        raise HTTPException(422, "scheduled_at is required")
    order.scheduled_at = datetime.fromisoformat(scheduled_at)
    order.location = body.get("location")
    order.workflow_state = "Scheduled"
    if order.status == "RAISED":
        order.status = "SCHEDULED"
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

    db.add(CCAJourneyEvent(
        patient_id=result.patient_id, event_type="IMAGING_REPORT_FINALIZED",
        event_title=f"Imaging Report Finalized: {result.title}", event_category="INVESTIGATION",
        description=f"{actor} finalized the imaging report.", actor_name=actor, actor_role=current_user.get("role"),
    ))
    db.commit()
    db.refresh(result)
    return {"status": "success", "result": _result_out(result)}


# ---------------------------------------------------------------------------
# Pathology / Molecular Diagnostics
# ---------------------------------------------------------------------------

@router.get("/pathology/worklist")
def pathology_worklist(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    return {"worklist": _worklist(db, _org_id(current_user), "PATHOLOGY")}


@router.get("/pathology/orders/{order_id}")
def get_pathology_order(order_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "PATHOLOGY":
        raise HTTPException(404, "Not a pathology order")
    results = db.query(CCAResult).filter(CCAResult.order_id == order.id).order_by(CCAResult.resulted_at.desc()).all()
    return {"order": _order_out(order), "results": [_result_out(r) for r in results]}


@router.post("/pathology/orders/{order_id}/report")
async def draft_pathology_report(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Pathologist drafts a structured report: gross/microscopic description, histologic
    type/grade, tumour extent, margins, lymph nodes, pathological staging evidence -- all held
    in structured_report since these vary by tumour type/specimen (spec: 'context dependent')."""
    if not (is_cca_pathologist(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Pathologist or Admin may draft a pathology report")
    org_id = _org_id(current_user)
    order = _get_org_order(db, order_id, org_id)
    if order.order_type != "PATHOLOGY":
        raise HTTPException(404, "Not a pathology order")
    body = await request.json()

    result = db.query(CCAResult).filter(CCAResult.order_id == order.id, CCAResult.report_status == "Draft").first()
    if not result:
        result = CCAResult(order_id=order.id, patient_id=order.patient_id, result_type="PATHOLOGY", title=order.item_name)
        db.add(result)

    result.findings_text = body.get("findings_text")  # final diagnosis / comment-interpretation
    result.impression = body.get("impression")
    result.structured_report = body.get("structured_report")  # gross/microscopic/histology/grade/margins/nodes/staging evidence
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
    actor = _actor(current_user)
    result.report_status = "Finalized"
    result.finalized_by = actor
    result.finalized_at = datetime.utcnow()

    order = db.query(CCAOrder).filter(CCAOrder.id == result.order_id).first()
    if order:
        order.status = "RESULTED"
        order.workflow_state = "ReportFinalized"

    db.add(CCAJourneyEvent(
        patient_id=result.patient_id, event_type="PATHOLOGY_REPORT_FINALIZED",
        event_title=f"Pathology Report Finalized: {result.title}", event_category="INVESTIGATION",
        description=f"{actor} finalized the pathology report.", actor_name=actor, actor_role=current_user.get("role"),
    ))
    db.commit()
    db.refresh(result)
    return {"status": "success", "result": _result_out(result)}


def _biomarker_out(b: CCABiomarkerResult) -> dict:
    return {
        "id": b.id, "patient_id": b.patient_id, "marker_name": b.marker_name,
        "result_as_reported": b.result_as_reported, "method": b.method, "platform": b.platform,
        "specimen": b.specimen, "adequacy": b.adequacy, "lab_name": b.lab_name,
        "reported_on": b.reported_on.isoformat() if b.reported_on else None, "status": b.status,
        "confirmatory_required": b.confirmatory_required,
    }


@router.get("/molecular/tests")
def list_molecular_tests(patient_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
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

    test = CCABiomarkerResult(
        patient_id=patient_id, marker_name=marker_name, result_as_reported="Pending",
        method=body.get("method"), specimen=body.get("specimen"), status="PENDING",
    )
    db.add(test)
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
    db.commit()
    db.refresh(test)
    return {"status": "success", "test": _biomarker_out(test)}


# ---------------------------------------------------------------------------
# Lab / Phlebotomy
# ---------------------------------------------------------------------------

@router.get("/lab/worklist")
def lab_worklist(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    return {"worklist": _worklist(db, _org_id(current_user), "LAB")}


@router.post("/lab/orders/{order_id}/collect")
async def collect_specimen(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_lab_phlebotomy(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Lab/Phlebotomy staff or Admin may record specimen collection")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "LAB":
        raise HTTPException(404, "Not a lab order")
    body = await request.json()
    order.collected_by = _actor(current_user)
    order.collected_at = datetime.utcnow()
    order.specimen_container = body.get("specimen_container")
    order.workflow_state = "Collected"
    order.status = "IN_PROGRESS"
    db.commit()
    return {"status": "success", "order": _order_out(order)}


@router.post("/lab/orders/{order_id}/reject")
async def reject_specimen(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_lab_phlebotomy(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Lab/Phlebotomy staff or Admin may reject a specimen")
    order = _get_org_order(db, order_id, _org_id(current_user))
    body = await request.json()
    reason = body.get("reason")
    if not reason:
        raise HTTPException(422, "reason is required to reject a specimen")
    order.rejection_reason = reason
    order.workflow_state = "RecollectionRequired"
    db.commit()
    return {"status": "success", "order": _order_out(order)}


@router.post("/lab/orders/{order_id}/result")
async def record_lab_result(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_lab_phlebotomy(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Lab/Phlebotomy staff or Admin may record a lab result")
    org_id = _org_id(current_user)
    order = _get_org_order(db, order_id, org_id)
    if order.order_type != "LAB":
        raise HTTPException(404, "Not a lab order")
    body = await request.json()
    findings = body.get("findings_text")
    if not findings:
        raise HTTPException(422, "findings_text is required")

    result = CCAResult(
        order_id=order.id, patient_id=order.patient_id, result_type="LAB", title=order.item_name,
        findings_text=findings, extracted_values=body.get("extracted_values"),
        is_critical=bool(body.get("is_critical", False)),
        status="PENDING_REVIEW" if body.get("is_critical") else "NEW", report_status="Finalized",
        finalized_by=_actor(current_user), finalized_at=datetime.utcnow(),
    )
    db.add(result)
    order.status = "RESULTED"
    order.workflow_state = "ResultAvailable"
    db.commit()
    db.refresh(result)
    return {"status": "success", "result": _result_out(result)}
