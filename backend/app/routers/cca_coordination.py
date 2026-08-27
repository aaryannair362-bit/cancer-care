"""
CCA Oncology OS -- MDT Coordinator scheduling, External MDT Specialist case-scoped access,
Financial Counsellor / Estimates & Clearance, Patient Liaison / Care Coordination, and the
Admin/Operations cross-department dashboard.

Covers 11_MDT_Coordinator.pdf, 12_External_MDT_Specialist.pdf,
14_Financial_Counsellor_Patient_Financial_Services.pdf,
13_Patient_Liaison_Care_Coordinator.pdf, 15_Admin_Operations.pdf.

Admin/Operations deliberately does NOT rebuild Users & Roles or a general audit log --
backend/app/main.py's /api/auth/users* endpoints and AuditLog already cover user management
(15_Admin_Operations.pdf: "Use existing role architecture; do not build enterprise IAM"); this
module's /admin/operations-dashboard and /admin/audit surface CCA-specific operational metrics
and the CCAJourneyEvent provenance trail on top of that, not a second identity system.
"""

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import (
    get_current_user, is_admin, is_cca_oncologist, is_cca_mdt_coordinator,
    is_cca_external_mdt_specialist, is_cca_financial_counsellor, is_cca_patient_liaison,
    is_cca_front_desk,
)
from ..models_cca import (
    CCAPatient, MDTCase, MDTDecision, MDTParticipant, CCAExternalAccess, CCAExternalOpinion,
    CCAFinancialCase, CCACoordinationCase, CCAIntakeAssessment, CCAEncounter, CCAOrder,
    CarePlan, StagingRecord, CCAJourneyEvent, TreatmentPlan, CarePlanTask, DomainEvent,
)
from ..events import publish
from ..cca_product_decisions import EXTERNAL_SPECIALIST_CAN_SIGN_RECOMMENDATIONS
from .cca import get_cca_db, _org_id, _actor, _get_org_patient, _check_patient_in_org

router = APIRouter(prefix="/api/cca", tags=["CCA Coordination & Ops"])


def _get_org_case(db: Session, case_id: int, org_id: int) -> MDTCase:
    case = db.query(MDTCase).filter(MDTCase.id == case_id).first()
    if not case:
        raise HTTPException(404, "MDT case not found")
    _check_patient_in_org(db, case.patient_id, org_id)
    return case


def _require_mdt_coordinator(user: dict):
    if not (is_cca_mdt_coordinator(user) or is_admin(user)):
        raise HTTPException(403, "Only the MDT Coordinator or Admin may perform this action")


# ---------------------------------------------------------------------------
# MDT Coordinator: referral queue, scheduling, participants
# ---------------------------------------------------------------------------

def _mdt_case_out(c: MDTCase) -> dict:
    return {
        "id": c.id, "patient_id": c.patient_id, "question": c.question, "priority": c.priority,
        "tumor_board": c.tumor_board, "status": c.status, "requested_by": c.requested_by,
        "referring_department": c.referring_department, "referring_clinician": c.referring_clinician,
        "board_date": c.board_date.isoformat() if c.board_date else None, "start_time": c.start_time,
        "meeting_type": c.meeting_type, "location": c.location, "meeting_link": c.meeting_link,
        "agenda_position": c.agenda_position,
        "scheduled_for": c.scheduled_for.isoformat() if c.scheduled_for else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/mdt/referral-queue")
def mdt_referral_queue(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    org_id = _org_id(current_user)
    rows = db.query(MDTCase, CCAPatient).join(CCAPatient, MDTCase.patient_id == CCAPatient.id).filter(
        CCAPatient.organization_id == org_id
    ).order_by(MDTCase.created_at.desc()).all()
    out = []
    for case, patient in rows:
        readiness = _case_readiness(db, case.patient_id)
        out.append({**_mdt_case_out(case), "patient_name": patient.name, "patient_mrn": patient.mrn, "readiness": readiness})
    return {"queue": out}


def _case_readiness(db: Session, patient_id: int) -> dict:
    """Case Completeness / MDT Readiness (spec): per-item Available/Pending/NotRequired,
    computed live from existing state rather than a separately-maintained checklist."""
    diagnosis = db.query(StagingRecord).filter(StagingRecord.patient_id == patient_id).first()
    staging_confirmed = db.query(StagingRecord).filter(
        StagingRecord.patient_id == patient_id, StagingRecord.status == "CLINICIAN_CONFIRMED"
    ).first() is not None
    pathology = db.query(CCAOrder).filter(CCAOrder.patient_id == patient_id, CCAOrder.order_type == "PATHOLOGY", CCAOrder.status == "RESULTED").first()
    imaging = db.query(CCAOrder).filter(CCAOrder.patient_id == patient_id, CCAOrder.order_type == "RADIOLOGY", CCAOrder.status == "RESULTED").first()
    items = {
        "clinical_summary": "Available",
        "pathology": "Available" if pathology else "Pending",
        "imaging": "Available" if imaging else "Pending",
        "staging": "Available" if staging_confirmed else "Pending",
    }
    missing = [k for k, v in items.items() if v != "Available"]
    overall = "ReadyForMDT" if not missing else ("AwaitingInformation" if len(missing) < len(items) else "Incomplete")
    return {"items": items, "missing": missing, "overall": overall}


@router.get("/mdt/cases/{case_id}/readiness")
def get_mdt_case_readiness(case_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    case = _get_org_case(db, case_id, _org_id(current_user))
    return _case_readiness(db, case.patient_id)


@router.patch("/mdt/cases/{case_id}/schedule")
async def schedule_mdt_case(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_mdt_coordinator(current_user)
    case = _get_org_case(db, case_id, _org_id(current_user))
    body = await request.json()
    if body.get("board_date"):
        case.board_date = date.fromisoformat(body["board_date"])
    case.start_time = body.get("start_time", case.start_time)
    if body.get("meeting_type") and body["meeting_type"] not in ("InPerson", "Virtual", "Hybrid"):
        raise HTTPException(422, "meeting_type must be InPerson, Virtual, or Hybrid")
    case.meeting_type = body.get("meeting_type", case.meeting_type)
    case.location = body.get("location", case.location)
    case.meeting_link = body.get("meeting_link", case.meeting_link)
    case.agenda_position = body.get("agenda_position", case.agenda_position)
    case.referring_department = body.get("referring_department", case.referring_department)
    case.referring_clinician = body.get("referring_clinician", case.referring_clinician)
    case.status = "SCHEDULED"
    actor = _actor(current_user)
    publish(
        db, "MDT_CASE_SCHEDULED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
        title=f"MDT case scheduled: {case.tumor_board}", category="MDT",
        description=f"{actor} scheduled case #{case.id} for {case.board_date or 'a date TBD'}.",
        mdt_case_id=case.id,
    )
    db.commit()
    return {"status": "success", "case": _mdt_case_out(case)}


@router.patch("/mdt/cases/{case_id}/state")
async def update_mdt_case_state(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """During-MDT state transitions (case opened/discussed/deferred/...). Coordinator "does not
    author final clinical recommendation" (spec) -- that stays on MDTDecision via
    routers/cca.py's /mdt/cases/{id}/recommendation, Doctor/Oncologist/Admin-only."""
    _require_mdt_coordinator(current_user)
    case = _get_org_case(db, case_id, _org_id(current_user))
    body = await request.json()
    new_status = body.get("status")
    allowed = ("PROPOSED", "PREPARED", "SCHEDULED", "DISCUSSED", "RECOMMENDED", "RETURNED_TO_RECORD", "ACTIONED_BY_CLINICIAN", "WITHDRAWN")
    if new_status not in allowed:
        raise HTTPException(422, f"status must be one of {', '.join(allowed)}")
    old_status = case.status
    case.status = new_status
    actor = _actor(current_user)
    publish(
        db, "MDT_CASE_STATE_CHANGED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
        title=f"MDT case {old_status} -> {new_status}", category="MDT",
        description=f"{actor} moved case #{case.id} from {old_status} to {new_status}.",
        mdt_case_id=case.id, previous_status=old_status, new_status=new_status,
    )
    db.commit()
    return {"status": "success", "case": _mdt_case_out(case)}


def _participant_out(p: MDTParticipant) -> dict:
    return {
        "id": p.id, "case_id": p.case_id, "specialist_name": p.specialist_name,
        "specialist_role": p.specialist_role, "invitation_status": p.invitation_status,
        "attendance_status": p.attendance_status,
    }


@router.get("/mdt/cases/{case_id}/participants")
def list_participants(case_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    case = _get_org_case(db, case_id, _org_id(current_user))
    rows = db.query(MDTParticipant).filter(MDTParticipant.case_id == case.id).all()
    return {"participants": [_participant_out(p) for p in rows]}


@router.post("/mdt/cases/{case_id}/participants", status_code=201)
async def add_participant(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_mdt_coordinator(current_user)
    case = _get_org_case(db, case_id, _org_id(current_user))
    body = await request.json()
    name, role = body.get("specialist_name"), body.get("specialist_role")
    if not name or not role:
        raise HTTPException(422, "specialist_name and specialist_role are required")
    participant = MDTParticipant(case_id=case.id, specialist_name=name, specialist_role=role, invitation_status="Invited")
    db.add(participant)
    db.flush()
    actor = _actor(current_user)
    publish(
        db, "MDT_PARTICIPANT_INVITED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
        title=f"MDT participant invited: {name} ({role})", category="MDT",
        description=f"{actor} invited {name} ({role}) to case #{case.id}.",
        mdt_case_id=case.id, participant_id=participant.id,
    )
    db.commit()
    db.refresh(participant)
    return {"status": "success", "participant": _participant_out(participant)}


@router.patch("/mdt/participants/{participant_id}")
async def update_participant(participant_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_mdt_coordinator(current_user)
    participant = db.query(MDTParticipant).filter(MDTParticipant.id == participant_id).first()
    if not participant:
        raise HTTPException(404, "Participant not found")
    _check_patient_in_org(db, _get_org_case(db, participant.case_id, _org_id(current_user)).patient_id, _org_id(current_user))
    body = await request.json()
    if "invitation_status" in body:
        if body["invitation_status"] not in ("NotInvited", "Invited", "Accepted", "Declined", "Pending"):
            raise HTTPException(422, "invalid invitation_status")
        participant.invitation_status = body["invitation_status"]
    if "attendance_status" in body:
        if body["attendance_status"] not in ("Present", "Absent", "JoinedRemotely", None):
            raise HTTPException(422, "invalid attendance_status")
        participant.attendance_status = body["attendance_status"]
    if participant.invitation_status == "Declined":
        # A needed specialist declining to attend is the one participant-state change worth an
        # audit event -- routine invite acceptance / in-meeting attendance marking is not.
        case = _get_org_case(db, participant.case_id, _org_id(current_user))
        actor = _actor(current_user)
        publish(
            db, "MDT_PARTICIPANT_DECLINED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
            title=f"MDT participant declined: {participant.specialist_name}", category="MDT",
            description=f"{participant.specialist_name} ({participant.specialist_role}) declined case #{case.id}.",
            mdt_case_id=case.id, participant_id=participant.id,
        )
    db.commit()
    return {"status": "success", "participant": _participant_out(participant)}


# ---------------------------------------------------------------------------
# External MDT Specialist: case-scoped access grants + separately-attributable opinions
# ---------------------------------------------------------------------------

def _access_out(a: CCAExternalAccess) -> dict:
    return {
        "id": a.id, "case_id": a.case_id, "specialist_name": a.specialist_name,
        "specialist_email": a.specialist_email, "access_status": a.access_status,
        "granted_by": a.granted_by, "granted_at": a.granted_at.isoformat() if a.granted_at else None,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
    }


def _live_access_status(a: CCAExternalAccess) -> str:
    """Access may have passed its expires_at without anyone touching the row -- compute the
    effective status at read time rather than relying on a background job to flip it."""
    if a.access_status == "Revoked":
        return "Revoked"
    if a.expires_at and a.expires_at < datetime.utcnow():
        return "Expired"
    return a.access_status


@router.post("/mdt/cases/{case_id}/external-access", status_code=201)
async def grant_external_access(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_mdt_coordinator(current_user)
    case = _get_org_case(db, case_id, _org_id(current_user))
    body = await request.json()
    name, email = body.get("specialist_name"), body.get("specialist_email")
    if not name or not email:
        raise HTTPException(422, "specialist_name and specialist_email are required")
    expires_at = datetime.fromisoformat(body["expires_at"]) if body.get("expires_at") else datetime.utcnow() + timedelta(days=14)
    access = CCAExternalAccess(
        case_id=case.id, specialist_name=name, specialist_email=email, access_status="Active",
        granted_by=_actor(current_user), expires_at=expires_at,
    )
    db.add(access)
    db.flush()
    actor = _actor(current_user)
    publish(
        db, "EXTERNAL_ACCESS_GRANTED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
        title=f"External access granted to {name}", category="MDT",
        description=f"{actor} granted case-scoped external access to {name} ({email}), expiring {expires_at.isoformat()}.",
        mdt_case_id=case.id, access_id=access.id,
    )
    db.commit()
    db.refresh(access)
    return {"status": "success", "access": _access_out(access)}


@router.patch("/mdt/external-access/{access_id}")
async def update_external_access(access_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_mdt_coordinator(current_user)
    access = db.query(CCAExternalAccess).filter(CCAExternalAccess.id == access_id).first()
    if not access:
        raise HTTPException(404, "Access grant not found")
    case = db.query(MDTCase).filter(MDTCase.id == access.case_id).first()
    _check_patient_in_org(db, case.patient_id, _org_id(current_user))
    body = await request.json()
    if body.get("revoke"):
        access.access_status = "Revoked"
        access.revoked_at = datetime.utcnow()
        actor = _actor(current_user)
        publish(
            db, "EXTERNAL_ACCESS_REVOKED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
            title=f"External access revoked for {access.specialist_name}", category="MDT",
            description=f"{actor} revoked external access for {access.specialist_name} ({access.specialist_email}).",
            mdt_case_id=case.id, access_id=access.id,
        )
    db.commit()
    return {"status": "success", "access": _access_out(access)}


def _external_access_for(db: Session, case_id: int, email: str) -> Optional[CCAExternalAccess]:
    access = db.query(CCAExternalAccess).filter(
        CCAExternalAccess.case_id == case_id, CCAExternalAccess.specialist_email == email
    ).order_by(CCAExternalAccess.granted_at.desc()).first()
    if access and _live_access_status(access) == "Active":
        return access
    return None


@router.get("/mdt/assigned-cases")
def assigned_cases(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """External MDT Specialist's own view: ONLY cases explicitly shared with their account
    email, never the org's full patient/MDT population (spec: 'must not expose the full
    hospital patient population')."""
    if not is_cca_external_mdt_specialist(current_user):
        raise HTTPException(403, "Only an External MDT Specialist has an assigned-cases view")
    email = current_user.get("email")
    grants = db.query(CCAExternalAccess).filter(CCAExternalAccess.specialist_email == email).all()
    live = [g for g in grants if _live_access_status(g) == "Active"]
    case_ids = {g.case_id for g in live}
    cases = db.query(MDTCase).filter(MDTCase.id.in_(case_ids)).all() if case_ids else []
    return {"assigned_cases": [_mdt_case_out(c) for c in cases]}


@router.get("/mdt/assigned-cases/{case_id}")
def get_assigned_case(case_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not is_cca_external_mdt_specialist(current_user):
        raise HTTPException(403, "Only an External MDT Specialist has an assigned-cases view")
    access = _external_access_for(db, case_id, current_user.get("email"))
    if not access:
        raise HTTPException(404, "Case not found or access not active")
    case = db.query(MDTCase).filter(MDTCase.id == case_id).first()
    return {"case": _mdt_case_out(case), "access": _access_out(access)}


def _opinion_out(o: CCAExternalOpinion) -> dict:
    return {
        "id": o.id, "case_id": o.case_id, "specialist_name": o.specialist_name,
        "recommendation": o.recommendation, "rationale": o.rationale,
        "supporting_evidence": o.supporting_evidence, "concerns": o.concerns,
        "information_required": o.information_required, "certainty": o.certainty, "status": o.status,
        "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
    }


@router.get("/mdt/cases/{case_id}/opinions")
def list_opinions(case_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if is_cca_external_mdt_specialist(current_user):
        access = _external_access_for(db, case_id, current_user.get("email"))
        if not access:
            raise HTTPException(404, "Case not found or access not active")
        rows = db.query(CCAExternalOpinion).filter(
            CCAExternalOpinion.case_id == case_id, CCAExternalOpinion.specialist_name == access.specialist_name
        ).all()
    else:
        case = _get_org_case(db, case_id, _org_id(current_user))
        rows = db.query(CCAExternalOpinion).filter(CCAExternalOpinion.case_id == case.id).all()
    return {"opinions": [_opinion_out(o) for o in rows]}


@router.post("/mdt/cases/{case_id}/opinions", status_code=201)
async def submit_opinion(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Ratified decision (cca_product_decisions.EXTERNAL_SPECIALIST_CAN_SIGN_RECOMMENDATIONS
    = False): this writes a CCAExternalOpinion, never an MDTDecision -- an External MDT
    Specialist's contribution is separately-attributable input, not a binding recommendation.
    There is deliberately no sign/finalize endpoint for CCAExternalOpinion; see that module
    for why. Do not add one without revisiting that decision first."""
    assert not EXTERNAL_SPECIALIST_CAN_SIGN_RECOMMENDATIONS  # this endpoint's behavior only makes sense under this decision
    if not is_cca_external_mdt_specialist(current_user):
        raise HTTPException(403, "Only an External MDT Specialist submits an opinion here")
    access = _external_access_for(db, case_id, current_user.get("email"))
    if not access:
        raise HTTPException(404, "Case not found or access not active")
    body = await request.json()
    recommendation = body.get("recommendation")
    if not recommendation:
        raise HTTPException(422, "recommendation is required")
    certainty = body.get("certainty")
    if certainty not in ("High", "Moderate", "Low", None):
        raise HTTPException(422, "certainty must be High, Moderate, or Low")
    opinion = CCAExternalOpinion(
        case_id=case_id, specialist_name=access.specialist_name, recommendation=recommendation,
        rationale=body.get("rationale"), supporting_evidence=body.get("supporting_evidence"),
        concerns=body.get("concerns"), information_required=body.get("information_required"),
        certainty=certainty, status="Submitted", submitted_at=datetime.utcnow(),
    )
    db.add(opinion)
    case = db.query(MDTCase).filter(MDTCase.id == case_id).first()
    db.add(CCAJourneyEvent(
        patient_id=case.patient_id, event_type="EXTERNAL_OPINION_SUBMITTED",
        event_title=f"External Opinion Submitted by {access.specialist_name}", event_category="MDT",
        description=recommendation[:255], actor_name=access.specialist_name, actor_role="CCAExternalMDTSpecialist",
    ))
    db.commit()
    db.refresh(opinion)
    return {"status": "success", "opinion": _opinion_out(opinion)}


# ---------------------------------------------------------------------------
# Financial Counsellor / Patient Financial Services
# ---------------------------------------------------------------------------

def _require_financial_write(user: dict):
    if not (is_cca_financial_counsellor(user) or is_admin(user)):
        raise HTTPException(403, "Only the Financial Counsellor or Admin may edit financial records")


def _financial_out(f: CCAFinancialCase) -> dict:
    return {
        "id": f.id, "patient_id": f.patient_id,
        "referral_date": f.referral_date.isoformat() if f.referral_date else None,
        "counselling_status": f.counselling_status,
        "counselling_date": f.counselling_date.isoformat() if f.counselling_date else None,
        "counsellor": f.counsellor, "counselling_notes": f.counselling_notes,
        "counselling_outcome": f.counselling_outcome, "patient_decision": f.patient_decision,
        "estimate": f.estimate, "estimate_status": f.estimate_status, "payer_route": f.payer_route,
        "insurance_status": f.insurance_status, "scheme_status": f.scheme_status,
        "financial_clearance_status": f.financial_clearance_status,
        "next_action": f.next_action, "next_action_owner": f.next_action_owner,
        "next_action_due": f.next_action_due.isoformat() if f.next_action_due else None,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _get_org_financial_case(db: Session, case_id: int, org_id: int) -> CCAFinancialCase:
    case = db.query(CCAFinancialCase).filter(CCAFinancialCase.id == case_id).first()
    if not case:
        raise HTTPException(404, "Financial case not found")
    _check_patient_in_org(db, case.patient_id, org_id)
    return case


@router.get("/financial/queue")
def financial_queue(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    org_id = _org_id(current_user)
    rows = db.query(CCAFinancialCase, CCAPatient).join(CCAPatient, CCAFinancialCase.patient_id == CCAPatient.id).filter(
        CCAPatient.organization_id == org_id
    ).order_by(CCAFinancialCase.created_at.desc()).all()
    return {"queue": [{**_financial_out(f), "patient_name": p.name, "patient_mrn": p.mrn} for f, p in rows]}


@router.post("/financial/cases", status_code=201)
async def create_financial_case(request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_financial_counsellor(current_user) or is_cca_patient_liaison(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Financial Counsellor, Patient Liaison, or Admin may refer a financial case")
    org_id = _org_id(current_user)
    body = await request.json()
    patient_id = body.get("patient_id")
    if not patient_id:
        raise HTTPException(422, "patient_id is required")
    _get_org_patient(db, patient_id, org_id)
    case = CCAFinancialCase(patient_id=patient_id, created_by=_actor(current_user))
    db.add(case)
    db.commit()
    db.refresh(case)
    return {"status": "success", "case": _financial_out(case)}


@router.get("/financial/cases/{case_id}")
def get_financial_case(case_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Read access is intentionally broader than write -- Patient Liaison/oncologists need
    'limited visibility/handoff only' (13_Patient_Liaison...pdf) into financial status."""
    case = _get_org_financial_case(db, case_id, _org_id(current_user))
    return {"case": _financial_out(case)}


@router.patch("/financial/cases/{case_id}/counselling")
async def update_counselling(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_financial_write(current_user)
    case = _get_org_financial_case(db, case_id, _org_id(current_user))
    body = await request.json()
    case.counselling_status = body.get("counselling_status", case.counselling_status)
    case.counselling_date = datetime.utcnow()
    case.counsellor = _actor(current_user)
    case.counselling_notes = body.get("counselling_notes", case.counselling_notes)
    case.counselling_outcome = body.get("counselling_outcome", case.counselling_outcome)
    case.patient_decision = body.get("patient_decision", case.patient_decision)
    actor = _actor(current_user)
    publish(
        db, "FINANCIAL_COUNSELLING_UPDATED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Financial counselling: {case.counselling_status}", category="FINANCIAL",
        description=f"{actor} updated financial counselling to {case.counselling_status}." + (f" Patient decision: {case.patient_decision}." if case.patient_decision else ""),
        financial_case_id=case.id,
    )
    db.commit()
    return {"status": "success", "case": _financial_out(case)}


@router.patch("/financial/cases/{case_id}/estimate")
async def update_estimate(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_financial_write(current_user)
    case = _get_org_financial_case(db, case_id, _org_id(current_user))
    body = await request.json()
    if "estimate" in body:
        case.estimate = body["estimate"]
    status_val = body.get("estimate_status")
    if status_val and status_val not in ("NotStarted", "Draft", "Ready", "Shared", "Revised", "Accepted", "Expired"):
        raise HTTPException(422, "invalid estimate_status")
    case.estimate_status = status_val or case.estimate_status
    if status_val in ("Ready", "Shared", "Revised", "Accepted"):
        # The estimate states worth an audit trail for -- a draft-in-progress isn't yet a
        # decision point, but the patient seeing/accepting a number is.
        actor = _actor(current_user)
        publish(
            db, "FINANCIAL_ESTIMATE_UPDATED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
            title=f"Financial estimate: {case.estimate_status}", category="FINANCIAL",
            description=f"{actor} moved the financial estimate to {case.estimate_status}.",
            financial_case_id=case.id,
        )
    db.commit()
    return {"status": "success", "case": _financial_out(case)}


@router.patch("/financial/cases/{case_id}/insurance")
async def update_insurance(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_financial_write(current_user)
    case = _get_org_financial_case(db, case_id, _org_id(current_user))
    body = await request.json()
    case.payer_route = body.get("payer_route", case.payer_route)
    case.insurance_status = body.get("insurance_status", case.insurance_status)
    case.scheme_status = body.get("scheme_status", case.scheme_status)
    actor = _actor(current_user)
    publish(
        db, "FINANCIAL_INSURANCE_UPDATED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Insurance/scheme status updated ({case.payer_route or 'route not set'})", category="FINANCIAL",
        description=f"{actor} updated insurance status to {case.insurance_status or '—'}, scheme status to {case.scheme_status or '—'}.",
        financial_case_id=case.id,
    )
    db.commit()
    return {"status": "success", "case": _financial_out(case)}


@router.patch("/financial/cases/{case_id}/clearance")
async def update_clearance(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_financial_write(current_user)
    case = _get_org_financial_case(db, case_id, _org_id(current_user))
    body = await request.json()
    status_val = body.get("financial_clearance_status")
    allowed = ("NotStarted", "PendingDocuments", "InsuranceApprovalPending", "PatientContributionPending", "PartiallyCleared", "Cleared", "NotCleared", "Deferred")
    if status_val not in allowed:
        raise HTTPException(422, f"financial_clearance_status must be one of {', '.join(allowed)}")
    case.financial_clearance_status = status_val
    actor = _actor(current_user)
    publish(
        db, "FINANCIAL_CLEARANCE_UPDATED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
        title=f"Financial clearance: {status_val}", category="FINANCIAL",
        description=f"{actor} set financial clearance to {status_val}.",
        financial_case_id=case.id, financial_clearance_status=status_val,
    )
    db.commit()
    return {"status": "success", "case": _financial_out(case)}


@router.patch("/financial/cases/{case_id}/next-action")
async def update_financial_next_action(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    _require_financial_write(current_user)
    case = _get_org_financial_case(db, case_id, _org_id(current_user))
    body = await request.json()
    case.next_action = body.get("next_action")
    case.next_action_owner = body.get("next_action_owner")
    case.next_action_due = date.fromisoformat(body["next_action_due"]) if body.get("next_action_due") else None
    db.commit()
    return {"status": "success", "case": _financial_out(case)}


# ---------------------------------------------------------------------------
# Patient Liaison / Care Coordination
# ---------------------------------------------------------------------------

def _coordination_out(c: CCACoordinationCase) -> dict:
    return {
        "id": c.id, "patient_id": c.patient_id, "communication_status": c.communication_status,
        "preferred_contact_method": c.preferred_contact_method,
        "last_contact_at": c.last_contact_at.isoformat() if c.last_contact_at else None,
        "barriers": c.barriers or [], "next_action": c.next_action, "next_action_owner": c.next_action_owner,
        "next_action_due": c.next_action_due.isoformat() if c.next_action_due else None,
        "next_action_status": c.next_action_status,
    }


def _care_milestones(db: Session, patient_id: int) -> dict:
    """Computed live from existing state, not stored -- see CCACoordinationCase's docstring."""
    intake = db.query(CCAIntakeAssessment).filter(CCAIntakeAssessment.patient_id == patient_id).first()
    encounter = db.query(CCAEncounter).filter(CCAEncounter.patient_id == patient_id, CCAEncounter.status == "CLOSED").first()
    orders = db.query(CCAOrder).filter(CCAOrder.patient_id == patient_id).count()
    orders_resulted = db.query(CCAOrder).filter(CCAOrder.patient_id == patient_id, CCAOrder.status == "RESULTED").count()
    mdt = db.query(MDTCase).filter(MDTCase.patient_id == patient_id).first()
    care_plan = db.query(CarePlan).filter(CarePlan.patient_id == patient_id).first()
    financial = db.query(CCAFinancialCase).filter(CCAFinancialCase.patient_id == patient_id).first()
    return {
        "registration_completed": True,
        "nurse_intake_completed": intake is not None,
        "oncology_consultation_completed": encounter is not None,
        "investigations_ordered": orders > 0,
        "investigations_status": "Completed" if orders and orders == orders_resulted else ("Pending" if orders else "NotOrdered"),
        "mdt_status": mdt.status if mdt else "NotReferred",
        "treatment_plan_available": care_plan is not None,
        "financial_counselling_status": financial.counselling_status if financial else "NotReferred",
    }


@router.get("/coordination/queue")
def coordination_queue(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    org_id = _org_id(current_user)
    rows = db.query(CCACoordinationCase, CCAPatient).join(CCAPatient, CCACoordinationCase.patient_id == CCAPatient.id).filter(
        CCAPatient.organization_id == org_id
    ).order_by(CCACoordinationCase.updated_at.desc()).all()
    return {"queue": [{**_coordination_out(c), "patient_name": p.name, "patient_mrn": p.mrn} for c, p in rows]}


@router.post("/coordination/cases", status_code=201)
async def create_coordination_case(request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_patient_liaison(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Patient Liaison or Admin may open a coordination case")
    org_id = _org_id(current_user)
    body = await request.json()
    patient_id = body.get("patient_id")
    if not patient_id:
        raise HTTPException(422, "patient_id is required")
    _get_org_patient(db, patient_id, org_id)
    case = CCACoordinationCase(patient_id=patient_id, created_by=_actor(current_user))
    db.add(case)
    db.commit()
    db.refresh(case)
    return {"status": "success", "case": _coordination_out(case)}


@router.get("/coordination/cases/{case_id}")
def get_coordination_case(case_id: int, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    case = db.query(CCACoordinationCase).filter(CCACoordinationCase.id == case_id).first()
    if not case:
        raise HTTPException(404, "Coordination case not found")
    _check_patient_in_org(db, case.patient_id, _org_id(current_user))
    return {"case": _coordination_out(case), "care_milestones": _care_milestones(db, case.patient_id)}


def _get_org_coordination_case(db: Session, case_id: int, org_id: int) -> CCACoordinationCase:
    case = db.query(CCACoordinationCase).filter(CCACoordinationCase.id == case_id).first()
    if not case:
        raise HTTPException(404, "Coordination case not found")
    _check_patient_in_org(db, case.patient_id, org_id)
    return case


@router.patch("/coordination/cases/{case_id}/contact")
async def update_contact_status(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_patient_liaison(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Patient Liaison or Admin may update contact status")
    case = _get_org_coordination_case(db, case_id, _org_id(current_user))
    body = await request.json()
    status_val = body.get("communication_status")
    allowed = ("NotContacted", "ContactAttempted", "Reached", "UnableToReach", "CallbackRequired")
    if status_val not in allowed:
        raise HTTPException(422, f"communication_status must be one of {', '.join(allowed)}")
    case.communication_status = status_val
    case.preferred_contact_method = body.get("preferred_contact_method", case.preferred_contact_method)
    case.last_contact_at = datetime.utcnow()
    if status_val in ("UnableToReach", "CallbackRequired"):
        # The two contact outcomes that mean something needs follow-up, not routine logistics.
        actor = _actor(current_user)
        publish(
            db, "COORDINATION_CONTACT_UPDATED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
            title=f"Contact status: {status_val}", category="COORDINATION",
            description=f"{actor} recorded contact attempt outcome: {status_val}.",
            coordination_case_id=case.id,
        )
    db.commit()
    return {"status": "success", "case": _coordination_out(case)}


@router.post("/coordination/cases/{case_id}/barriers")
async def add_barrier(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_patient_liaison(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Patient Liaison or Admin may record a barrier")
    case = _get_org_coordination_case(db, case_id, _org_id(current_user))
    body = await request.json()
    barrier_type = body.get("type")
    if not barrier_type:
        raise HTTPException(422, "type is required")
    barriers = list(case.barriers or [])
    barriers.append({
        "type": barrier_type, "notes": body.get("notes", ""), "status": "Open",
        "owner": body.get("owner") or _actor(current_user),
    })
    case.barriers = barriers
    db.commit()
    return {"status": "success", "case": _coordination_out(case)}


@router.patch("/coordination/cases/{case_id}/barriers/{index}")
async def update_barrier_status(case_id: int, index: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Barriers could previously only ever be added, never resolved or escalated -- the
    architecture doc's CARE_PLAN_TASK_BLOCKED event ('Care Coordinator + treating team see
    blocker') had nothing to actually trigger it. Escalating one now opens a review task for
    the treating team via the same patient-scoped task queue used elsewhere (see
    event_subscribers.py's _on_care_plan_task_blocked)."""
    if not (is_cca_patient_liaison(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Patient Liaison or Admin may update a barrier")
    case = _get_org_coordination_case(db, case_id, _org_id(current_user))
    barriers = list(case.barriers or [])
    if index < 0 or index >= len(barriers):
        raise HTTPException(404, "Barrier not found")
    body = await request.json()
    new_status = body.get("status")
    if new_status not in ("Open", "Resolved", "Escalated"):
        raise HTTPException(422, "status must be one of Open, Resolved, Escalated")
    barriers[index] = {**barriers[index], "status": new_status}
    case.barriers = barriers

    actor = _actor(current_user)
    if new_status == "Escalated":
        publish(
            db, "CARE_PLAN_TASK_BLOCKED", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
            title="Care barrier escalated", category="COORDINATION",
            description=f"{actor} escalated barrier '{barriers[index].get('type')}': {barriers[index].get('notes', '')}",
            barrier_type=barriers[index].get("type"), barrier_notes=barriers[index].get("notes"),
        )
    db.commit()
    return {"status": "success", "case": _coordination_out(case)}


@router.post("/coordination/cases/{case_id}/no-show")
async def record_no_show(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Architecture doc event: PATIENT_NO_SHOW, triggered by Front Desk / coordinator ->
    'Care Coordinator receives recovery task'. Also recorded as a barrier on the
    coordination case for visibility alongside every other barrier."""
    if not (is_cca_front_desk(current_user) or is_cca_patient_liaison(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only Front Desk, Patient Liaison, or Admin may record a no-show")
    case = _get_org_coordination_case(db, case_id, _org_id(current_user))
    body = await request.json()
    context = body.get("context", "")

    barriers = list(case.barriers or [])
    barriers.append({"type": "NoShow", "notes": context, "status": "Open", "owner": _actor(current_user)})
    case.barriers = barriers

    actor = _actor(current_user)
    publish(
        db, "PATIENT_NO_SHOW", patient_id=case.patient_id, actor=actor, role=current_user.get("role"),
        title="Patient no-show recorded", category="COORDINATION",
        description=f"{actor} recorded a no-show." + (f" {context}" if context else ""),
        coordination_case_id=case.id, context=context,
    )
    db.commit()
    return {"status": "success", "case": _coordination_out(case)}


@router.patch("/coordination/cases/{case_id}/next-action")
async def update_coordination_next_action(case_id: int, request: Request, db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    if not (is_cca_patient_liaison(current_user) or is_admin(current_user)):
        raise HTTPException(403, "Only the Patient Liaison or Admin may update the follow-up task")
    case = _get_org_coordination_case(db, case_id, _org_id(current_user))
    body = await request.json()
    case.next_action = body.get("next_action")
    case.next_action_owner = body.get("next_action_owner")
    case.next_action_due = date.fromisoformat(body["next_action_due"]) if body.get("next_action_due") else None
    status_val = body.get("next_action_status", "Pending")
    if status_val not in ("Pending", "InProgress", "Completed", "Overdue"):
        raise HTTPException(422, "invalid next_action_status")
    case.next_action_status = status_val
    db.commit()
    return {"status": "success", "case": _coordination_out(case)}


# ---------------------------------------------------------------------------
# Admin / Operations dashboard
# ---------------------------------------------------------------------------

@router.get("/admin/operations-dashboard")
def operations_dashboard(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Cross-department operational metrics -- Admin is explicitly 'not a clinical
    super-user' (spec): every number here is an operational count, never a clinical
    interpretation, diagnosis, or treatment decision."""
    if not is_admin(current_user):
        raise HTTPException(403, "Only Admin has the Operations Dashboard")
    org_id = _org_id(current_user)

    def _count(model, *filters):
        q = db.query(model).join(CCAPatient, model.patient_id == CCAPatient.id).filter(CCAPatient.organization_id == org_id)
        for f in filters:
            q = q.filter(f)
        return q.count()

    total_patients = db.query(CCAPatient).filter(CCAPatient.organization_id == org_id).count()

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "patients_total": total_patients,
        "nurse_intake_pending": total_patients - db.query(CCAIntakeAssessment).join(
            CCAPatient, CCAIntakeAssessment.patient_id == CCAPatient.id
        ).filter(CCAPatient.organization_id == org_id).distinct(CCAIntakeAssessment.patient_id).count(),
        "mdt_cases_pending": db.query(MDTCase).join(CCAPatient, MDTCase.patient_id == CCAPatient.id).filter(
            CCAPatient.organization_id == org_id, MDTCase.status.in_(["PROPOSED", "PREPARED", "SCHEDULED"])
        ).count(),
        "radiology_pending": _count(CCAOrder, CCAOrder.order_type == "RADIOLOGY", CCAOrder.status.in_(["RAISED", "SCHEDULED", "IN_PROGRESS"])),
        "pathology_pending": _count(CCAOrder, CCAOrder.order_type == "PATHOLOGY", CCAOrder.status.in_(["RAISED", "SCHEDULED", "IN_PROGRESS"])),
        "lab_pending": _count(CCAOrder, CCAOrder.order_type == "LAB", CCAOrder.status.in_(["RAISED", "SCHEDULED", "IN_PROGRESS"])),
        "financial_clearance_pending": db.query(CCAFinancialCase).join(CCAPatient, CCAFinancialCase.patient_id == CCAPatient.id).filter(
            CCAPatient.organization_id == org_id, CCAFinancialCase.financial_clearance_status != "Cleared"
        ).count(),
        "coordination_overdue_tasks": db.query(CCACoordinationCase).join(CCAPatient, CCACoordinationCase.patient_id == CCAPatient.id).filter(
            CCAPatient.organization_id == org_id, CCACoordinationCase.next_action_status == "Overdue"
        ).count(),
    }


@router.get("/admin/analytics/treatment-metrics")
def treatment_analytics(db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user)):
    """Historical/timing metrics computed from the domain event log (see events.py's
    DomainEvent -- the P0 event-dispatch slice is what makes this possible at all) and
    current TreatmentPlan/CarePlanTask state, distinct from operations_dashboard's live
    point-in-time snapshot above. Every number here is an operational/timing metric, never
    a clinical interpretation -- same restriction as operations_dashboard."""
    if not is_admin(current_user):
        raise HTTPException(403, "Only Admin has analytics")
    org_id = _org_id(current_user)

    def _events(event_type):
        return db.query(DomainEvent).join(CCAPatient, DomainEvent.patient_id == CCAPatient.id).filter(
            CCAPatient.organization_id == org_id, DomainEvent.event_type == event_type
        ).order_by(DomainEvent.created_at.asc()).all()

    def _avg_days(deltas):
        return round(sum(deltas) / len(deltas), 2) if deltas else None

    # Time from Care Plan activation to first treatment administration, per patient.
    activated_at_by_patient = {}
    for e in _events("CARE_PLAN_ACTIVATED"):
        activated_at_by_patient.setdefault(e.patient_id, e.created_at)
    activation_to_treatment_days = []
    for e in _events("TREATMENT_ADMINISTERED"):
        activated_at = activated_at_by_patient.get(e.patient_id)
        if activated_at and e.created_at >= activated_at:
            activation_to_treatment_days.append((e.created_at - activated_at).total_seconds() / 86400)

    # Draft-to-sign turnaround, per Treatment Plan.
    drafted_at_by_plan = {
        e.payload.get("treatment_plan_id"): e.created_at for e in _events("TREATMENT_PLAN_DRAFTED") if e.payload
    }
    draft_to_sign_days = []
    for e in _events("TREATMENT_PLAN_SIGNED"):
        plan_id = e.payload.get("treatment_plan_id") if e.payload else None
        drafted_at = drafted_at_by_plan.get(plan_id)
        if drafted_at and e.created_at >= drafted_at:
            draft_to_sign_days.append((e.created_at - drafted_at).total_seconds() / 86400)

    plans = db.query(TreatmentPlan).join(CCAPatient, TreatmentPlan.patient_id == CCAPatient.id).filter(
        CCAPatient.organization_id == org_id, TreatmentPlan.status.in_(["ACTIVE", "COMPLETED"])
    ).all()
    total_planned = sum(p.planned_sessions or 0 for p in plans)
    total_completed = sum(p.completed_sessions or 0 for p in plans)

    open_tasks = db.query(CarePlanTask).join(CCAPatient, CarePlanTask.patient_id == CCAPatient.id).filter(
        CCAPatient.organization_id == org_id, CarePlanTask.status.in_(["OPEN", "ESCALATED"])
    ).count()

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "avg_days_plan_activation_to_treatment_start": _avg_days(activation_to_treatment_days),
        "avg_days_plan_draft_to_sign": _avg_days(draft_to_sign_days),
        "open_or_escalated_tasks": open_tasks,
        "treatment_plan_revisions": len(_events("TREATMENT_PLAN_REVISED")),
        "treatment_holds": len(_events("TREATMENT_HELD")),
        "patient_no_shows": len(_events("PATIENT_NO_SHOW")),
        "sessions_completion_rate": round(total_completed / total_planned, 3) if total_planned else None,
        "sample_sizes": {
            "plan_activation_to_treatment_start": len(activation_to_treatment_days),
            "plan_draft_to_sign": len(draft_to_sign_days),
        },
    }


@router.get("/admin/audit")
def operations_audit(
    limit: int = 100, patient_id: Optional[int] = None, category: Optional[str] = None,
    db: Session = Depends(get_cca_db), current_user: dict = Depends(get_current_user),
):
    """Audit & Activity screen: the existing CCAJourneyEvent provenance trail, filtered/paginated
    -- not a second audit system. 'Do not expose hidden reasoning or chain-of-thought; only
    application audit events' (spec) -- exactly what CCAJourneyEvent already records."""
    if not is_admin(current_user):
        raise HTTPException(403, "Only Admin has the Audit & Activity view")
    org_id = _org_id(current_user)
    limit = max(1, min(limit, 500))
    q = db.query(CCAJourneyEvent, CCAPatient).join(CCAPatient, CCAJourneyEvent.patient_id == CCAPatient.id).filter(
        CCAPatient.organization_id == org_id
    )
    if patient_id is not None:
        q = q.filter(CCAJourneyEvent.patient_id == patient_id)
    if category:
        q = q.filter(CCAJourneyEvent.event_category == category)
    rows = q.order_by(CCAJourneyEvent.timestamp.desc()).limit(limit).all()
    return {"events": [
        {
            "id": e.id, "patient_id": e.patient_id, "patient_name": p.name, "event_type": e.event_type,
            "event_title": e.event_title, "event_category": e.event_category, "description": e.description,
            "actor_name": e.actor_name, "actor_role": e.actor_role,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        }
        for e, p in rows
    ]}
