from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..auth import get_current_user, is_admin, is_head_nurse, is_nursing_station, is_tpa, log_audit
from ..config import settings
from ..database import get_db
from ..models import Consultation, NursingNote, Patient, PatientDocument, ProcedureRecord, Vital
from ..ocr_service import extract_document

router = APIRouter(prefix="/api/patients", tags=["patient-documents"])
ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/tiff"}


def _patient(db: Session, patient_id: int, user: dict) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id, Patient.organization_id == user.get("organization_id")).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    return patient


def _can_upload(user: dict) -> bool:
    return is_admin(user) or is_head_nurse(user) or is_nursing_station(user)


def _can_read(user: dict) -> bool:
    # TPA is deliberately read-only here: insurance reviewers need the same evidence package
    # the doctor reviewed before they can submit a defensible pre-authorization request.
    return _can_upload(user) or user.get("role") == "Doctor" or is_tpa(user)


def _document_out(doc: PatientDocument) -> dict:
    return {
        "id": doc.id, "patient_id": doc.patient_id, "filename": doc.filename,
        "content_type": doc.content_type, "file_size": doc.file_size,
        "document_type": doc.document_type, "document_date": doc.document_date.isoformat() if doc.document_date else None,
        "source_hospital": doc.source_hospital, "ocr_status": doc.ocr_status,
        "ocr_engine": doc.ocr_engine, "page_count": doc.page_count,
        "extracted_data": doc.extracted_data or {}, "ocr_error": doc.ocr_error,
        "uploaded_at": doc.uploaded_at.isoformat(), "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
        "file_url": f"/api/patients/{doc.patient_id}/documents/{doc.id}/file",
    }


@router.post("/{patient_id}/documents", status_code=201)
async def upload_document(
    patient_id: int, file: UploadFile = File(...), document_type: Optional[str] = Form(None),
    document_date: Optional[date] = Form(None), source_hospital: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    if not _can_upload(current_user):
        raise HTTPException(403, "Only registration staff can upload patient documents")
    _patient(db, patient_id, current_user)
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Upload a PDF, JPEG, PNG, or TIFF file")
    content = await file.read(settings.MAX_PATIENT_DOCUMENT_MB * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(400, "Uploaded file is empty")
    if len(content) > settings.MAX_PATIENT_DOCUMENT_MB * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {settings.MAX_PATIENT_DOCUMENT_MB} MB limit")
    filename = re.sub(r"[^A-Za-z0-9._() -]", "_", file.filename or "patient-document")[:255]
    digest = hashlib.sha256(content).hexdigest()
    duplicate = db.query(PatientDocument).filter(
        PatientDocument.patient_id == patient_id, PatientDocument.organization_id == current_user.get("organization_id"),
        PatientDocument.sha256 == digest,
    ).first()
    if duplicate:
        raise HTTPException(409, f"This file was already uploaded as document #{duplicate.id}")

    doc = PatientDocument(
        patient_id=patient_id, organization_id=current_user["organization_id"], uploaded_by=current_user["id"],
        filename=filename, content_type=content_type, file_size=len(content), sha256=digest, file_content=content,
        document_type=(document_type or "External medical record")[:80], document_date=document_date,
        source_hospital=(source_hospital or "")[:200] or None, ocr_status="Processing",
    )
    db.add(doc)
    db.flush()
    try:
        result = extract_document(content, content_type)
        doc.extracted_text = result["text"]
        doc.page_text = result["pages"]
        doc.page_count = result["page_count"]
        doc.ocr_engine = result["engine"]
        doc.extracted_data = result["signals"]
        doc.processed_at = result["processed_at"]
        doc.ocr_status = "Completed"
    except Exception as exc:
        doc.ocr_status = "NeedsReview"
        doc.ocr_error = str(exc)[:2000]
        doc.processed_at = datetime.utcnow()
    db.commit()
    db.refresh(doc)
    log_audit(db, current_user["id"], current_user["email"], current_user["organization_id"], "upload_patient_document", f"patient-documents/{doc.id}", doc.ocr_status)
    return _document_out(doc)


@router.get("/{patient_id}/documents")
def list_documents(patient_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if not _can_read(current_user):
        raise HTTPException(403, "Not permitted to read patient documents")
    _patient(db, patient_id, current_user)
    docs = db.query(PatientDocument).filter(PatientDocument.patient_id == patient_id, PatientDocument.organization_id == current_user["organization_id"]).order_by(PatientDocument.uploaded_at.desc()).all()
    return [_document_out(doc) for doc in docs]


@router.get("/{patient_id}/documents/{document_id}/file")
def view_document(patient_id: int, document_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if not _can_read(current_user):
        raise HTTPException(403, "Not permitted to read patient documents")
    _patient(db, patient_id, current_user)
    doc = db.query(PatientDocument).filter(PatientDocument.id == document_id, PatientDocument.patient_id == patient_id, PatientDocument.organization_id == current_user["organization_id"]).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    safe_name = doc.filename.replace('"', "")
    return Response(doc.file_content, media_type=doc.content_type, headers={"Content-Disposition": f'inline; filename="{safe_name}"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/{patient_id}/case-summary")
def case_summary(patient_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if not _can_read(current_user):
        raise HTTPException(403, "Not permitted to read this case summary")
    patient = _patient(db, patient_id, current_user)
    docs = db.query(PatientDocument).filter(PatientDocument.patient_id == patient_id, PatientDocument.organization_id == current_user["organization_id"]).order_by(PatientDocument.document_date, PatientDocument.uploaded_at).all()
    consultations = db.query(Consultation).filter(Consultation.patient_id == patient_id, Consultation.organization_id == current_user["organization_id"]).order_by(Consultation.created_at.desc()).all()
    vitals = db.query(Vital).filter(Vital.patient_id == patient_id).order_by(Vital.recorded_at.desc()).limit(20).all()
    notes = db.query(NursingNote).filter(NursingNote.patient_id == patient_id).order_by(NursingNote.created_at.desc()).limit(20).all()
    procedures = db.query(ProcedureRecord).filter(ProcedureRecord.patient_id == patient_id).order_by(ProcedureRecord.performed_at.desc()).all()
    doc_signals = {key: [] for key in ("diagnoses", "medications", "allergies", "investigations", "procedures")}
    for doc in docs:
        for key in doc_signals:
            for value in (doc.extracted_data or {}).get(key, []):
                if value not in doc_signals[key]: doc_signals[key].append(value)
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "patient": {"id": patient.id, "mrn": patient.mrn, "name": patient.name, "age": patient.age, "gender": patient.gender, "allergies": patient.allergies or [], "diagnosis": patient.diagnosis, "status": patient.status},
        "overview": {"document_count": len(docs), "completed_ocr_count": sum(d.ocr_status == "Completed" for d in docs), "consultation_count": len(consultations), "latest_visit": consultations[0].created_at.isoformat() if consultations else None},
        "imported_record_findings": doc_signals,
        "consultations": [{"id": c.id, "date": c.created_at.isoformat(), "visit_type": c.visit_type, "chief_complaint": c.chief_complaint, "diagnosis": c.primary_diagnosis, "medications": c.medications or [], "lab_tests": c.lab_tests or [], "advice": c.advice} for c in consultations[:20]],
        "recent_vitals": [{"recorded_at": v.recorded_at.isoformat(), "bp": f"{v.bp_systolic}/{v.bp_diastolic}" if v.bp_systolic is not None else None, "heart_rate": v.heart_rate, "temperature": v.temperature, "oxygen_sat": v.oxygen_sat} for v in vitals],
        "nursing_notes": [{"created_at": n.created_at.isoformat(), "assessment": n.assessment, "plan": n.plan} for n in notes],
        "procedures": [{"name": p.procedure_name, "performed_at": p.performed_at.isoformat(), "notes": p.notes} for p in procedures],
        "documents": [_document_out(d) for d in docs],
        "disclaimer": "This summary combines hospital records with OCR-derived text. Verify clinical decisions against the original documents.",
    }
