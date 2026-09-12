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
from ..models_cca import CCAOrder, CCAResult, CCABiomarkerResult, CCAJourneyEvent, CCAPatient, PathologySpecimenAccession
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
    actor = _actor(current_user)
    publish(
        db, "IMAGING_ORDER_SCHEDULED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Imaging scheduled: {order.item_name}", category="INVESTIGATION",
        description=f"{actor} scheduled {order.item_name} for {order.scheduled_at.isoformat()}.",
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
    accession = PathologySpecimenAccession(
        order_id=order_id, patient_id=order.patient_id, accession_number=accession_number,
        container_count=body.get("container_count"), condition_on_receipt=condition,
        labelling_concordant=labelling_concordant, discrepancy_note=discrepancy_note or None,
        status="QUARANTINED" if has_discrepancy else "ACCEPTED", received_by=_actor(current_user),
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

    test = CCABiomarkerResult(
        patient_id=patient_id, marker_name=marker_name, result_as_reported="Pending",
        method=body.get("method"), specimen=body.get("specimen"), status="PENDING",
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
# ---------------------------------------------------------------------------

@router.get("/lab/worklist")
def lab_worklist(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_diagnostics_read(current_user, is_cca_lab_phlebotomy)
    return {"worklist": _worklist(db, _org_id(current_user), "LAB")}


@router.post("/lab/orders/{order_id}/collect")
async def collect_specimen(order_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_lab_phlebotomy(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Lab/Phlebotomy staff or Admin may record specimen collection")
    order = _get_org_order(db, order_id, _org_id(current_user))
    if order.order_type != "LAB":
        raise HTTPException(404, "Not a lab order")
    body = await request.json()
    actor = _actor(current_user)
    order.collected_by = actor
    order.collected_at = datetime.utcnow()
    order.specimen_container = body.get("specimen_container")
    order.workflow_state = "Collected"
    order.status = "IN_PROGRESS"
    publish(
        db, "SPECIMEN_COLLECTED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Specimen collected: {order.item_name}", category="INVESTIGATION",
        description=f"{actor} collected the specimen for {order.item_name}.",
        order_id=order.id,
    )
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
    actor = _actor(current_user)
    publish(
        db, "SPECIMEN_REJECTED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Specimen rejected: {order.item_name}", category="INVESTIGATION",
        description=f"{actor} rejected the specimen for {order.item_name}: {reason}",
        order_id=order.id,
    )
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
    db.flush()
    order.status = "RESULTED"
    order.workflow_state = "ResultAvailable"
    # No journey/domain event existed for lab results at all before this -- unlike imaging
    # and pathology, which always had one. Lab results finalize immediately (no separate
    # draft step), so this is the only point to publish from.
    actor = _actor(current_user)
    publish(
        db, "LAB_RESULT_FINALIZED", patient_id=order.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Lab Result Finalized: {result.title}", category="INVESTIGATION",
        description=f"{actor} recorded the lab result.",
        result_id=result.id, order_id=order.id, is_critical=result.is_critical,
    )
    db.commit()
    db.refresh(result)
    return {"status": "success", "result": _result_out(result)}
