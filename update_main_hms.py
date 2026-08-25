with open(r'backend/app/main.py', 'r', encoding='utf-8') as f:
    code = f.read()

# 1. Update imports
old_imports = '''from .models import (
    Base, User, Organization, Consultation, PasswordHistory, Patient, NurseAssignment, Vital,
    Task, NursingNote, DischargeSummary, Ward, NurseShift,
    Drug, DrugBatch, DispensingRecord, ControlledDrugRegisterEntry,
)'''

new_imports = '''from .models import (
    Base, User, Organization, Consultation, PasswordHistory, Patient, NurseAssignment, Vital,
    Task, NursingNote, DischargeSummary, Ward, NurseShift,
    Drug, DrugBatch, DispensingRecord, ControlledDrugRegisterEntry,
    ProcedureRecord, PreAuthorizationRequest,
)'''
code = code.replace(old_imports, new_imports)

old_auth_import = '''from .auth import (
    get_current_user, get_password_hash, verify_password,
    validate_password_complexity, create_access_token, create_refresh_token,
    decode_token, log_audit, is_admin, is_head_nurse, is_nursing_station, is_nurse, is_pharmacist
)'''

new_auth_import = '''from .auth import (
    get_current_user, get_password_hash, verify_password,
    validate_password_complexity, create_access_token, create_refresh_token,
    decode_token, log_audit, is_admin, is_head_nurse, is_nursing_station, is_nurse, is_pharmacist,
    is_tpa, is_billing_staff, is_inventory_manager, is_doctor
)'''
code = code.replace(old_auth_import, new_auth_import)

old_router_import = '''from .routers import cca
from .cca_seed import seed_cca_database'''

new_router_import = '''from .routers.pharmacy import router as pharmacy_router
from .routers.inventory import router as inventory_router
from .routers.billing import router as billing_router
from .routers.patients import router as patients_router
from .routers.appointments import router as appointments_router
from .routers.nursing_charting import router as nursing_charting_router
from .routers.procedures import router as procedures_router
from .routers.nursing_assessments import router as nursing_assessments_router
from .routers.mar import router as mar_router
from .routers import cca
from .cca_seed import seed_cca_database'''
code = code.replace(old_router_import, new_router_import)

# 2. Router inclusions
old_includes = '''app.include_router(cca.router)'''
new_includes = '''app.include_router(pharmacy_router)
app.include_router(inventory_router)
app.include_router(billing_router)
app.include_router(patients_router)
app.include_router(appointments_router)
app.include_router(nursing_charting_router)
app.include_router(procedures_router)
app.include_router(nursing_assessments_router)
app.include_router(mar_router)
app.include_router(cca.router)'''
code = code.replace(old_includes, new_includes)

# 3. Add Pre-authorization and Admin delete endpoints at bottom
extra_endpoints = '''

@app.delete("/api/admin/patients/{patient_id}")
def admin_delete_patient(patient_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if not is_admin(current_user):
        raise HTTPException(403, "Only Admin can delete patients")
    patient = db.query(Patient).filter(Patient.id == patient_id, Patient.organization_id == current_user.get("organization_id")).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    patient.status = "Deleted"
    db.commit()
    log_audit(db, current_user["id"], current_user["email"], current_user.get("organization_id"), "soft_delete_patient", f"patients/{patient_id}", "Success", f"patient {patient_id}")
    return {"status": "success", "message": "Patient soft deleted"}

@app.post("/api/pre-authorizations")
async def create_pre_authorization(request: Request, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    if not is_tpa(current_user):
        raise HTTPException(403, "Only TPA can submit pre-authorization requests")
    body = await request.json()
    patient_id = body.get("patient_id")
    if not patient_id:
        raise HTTPException(400, "patient_id is required")
    patient = db.query(Patient).filter(
        Patient.id == patient_id, Patient.organization_id == current_user.get("organization_id")
    ).first()
    if not patient:
        raise HTTPException(404, "Patient not found")

    consultations = db.query(Consultation).filter(Consultation.patient_id == patient_id).order_by(Consultation.created_at.desc()).all()
    procedures = db.query(ProcedureRecord).filter(ProcedureRecord.patient_id == patient_id).order_by(ProcedureRecord.performed_at.desc()).all()

    snapshot = {
        "patient": {
            "id": patient.id, "mrn": patient.mrn, "name": patient.name, "age": patient.age, "gender": patient.gender,
            "phone": patient.phone, "diagnosis": patient.diagnosis, "status": patient.status,
            "admission_type": patient.admission_type, "ward": patient.ward, "bed": patient.bed,
            "allergies": patient.allergies,
            "admission_date": patient.admission_date.isoformat() if patient.admission_date else None,
        },
        "consultations": [{
            "id": c.id, "created_at": c.created_at.isoformat() if c.created_at else None, "visit_type": c.visit_type,
            "chief_complaint": c.chief_complaint, "primary_diagnosis": c.primary_diagnosis,
            "differential_diagnosis": c.differential_diagnosis, "medications": c.medications,
            "lab_tests": c.lab_tests, "advice": c.advice,
        } for c in consultations],
        "procedures": [{
            "id": p.id, "procedure_name": p.procedure_name, "notes": p.notes,
            "performed_at": p.performed_at.isoformat() if p.performed_at else None,
        } for p in procedures],
    }

    pre_auth = PreAuthorizationRequest(
        organization_id=current_user.get("organization_id"), patient_id=patient_id,
        requested_by=current_user["id"], status="Submitted", clinical_snapshot=snapshot,
    )
    db.add(pre_auth)
    db.commit()
    db.refresh(pre_auth)
    log_audit(db, current_user["id"], current_user["email"], current_user.get("organization_id"),
              "submit_pre_authorization", f"pre_authorizations/{pre_auth.id}", "Success", f"patient {patient_id}")
    return {
        "id": pre_auth.id, "patient_id": pre_auth.patient_id, "status": pre_auth.status,
        "submitted_at": pre_auth.submitted_at.isoformat() if pre_auth.submitted_at else None,
    }

@app.get("/api/pre-authorizations")
def list_pre_authorizations(
    patient_id: Optional[int] = None,
    current_user: dict = Depends(get_current_user), db: Session = Depends(get_db),
):
    if not (is_tpa(current_user) or is_admin(current_user) or is_billing_staff(current_user)):
        raise HTTPException(403, "Permission denied")
    org_id = current_user.get("organization_id")
    query = db.query(PreAuthorizationRequest).filter(PreAuthorizationRequest.organization_id == org_id)
    if is_tpa(current_user):
        query = query.filter(PreAuthorizationRequest.requested_by == current_user["id"])
    if patient_id:
        query = query.filter(PreAuthorizationRequest.patient_id == patient_id)
    requests = query.order_by(PreAuthorizationRequest.submitted_at.desc()).all()
    return [{
        "id": r.id, "patient_id": r.patient_id, "status": r.status,
        "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
        "clinical_snapshot": r.clinical_snapshot,
    } for r in requests]
'''

if 'create_pre_authorization' not in code:
    code = code + extra_endpoints

with open(r'backend/app/main.py', 'w', encoding='utf-8') as f:
    f.write(code)

print('Updated main.py with full HMS routers and TPA endpoints')
