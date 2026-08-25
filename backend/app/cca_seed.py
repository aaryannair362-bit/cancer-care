"""
Seed Patient Data Loader & Scenario Harness for CCA Cancer Care AI OS.
Populates Meera S. Nair (CCA-2026-004417) + 7 Seed Documents + Contradiction + 12 Secondary Patients.
"""

from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from .models_cca import (
    CCAPatient, CCAConsent, CCAQueueEvent, CCAEncounter, CCAIntakeAssessment,
    CCADocument, ClinicalFact, CCAContradiction, CCACancerDiagnosis,
    CCABiomarkerResult, CCAOrder, CCAResult, StagingRecord, StagingEvidence,
    GuidelineContext, ClinicalBrief, MDTCase, MDTDecision, CarePlan,
    CarePlanTask, TreatmentPlan, TreatmentSession, ToxicityEvent,
    TreatmentClearance, ResponseAssessment, CCAJourneyEvent
)
from .cca_engine import calculate_bsa


def seed_cca_database(db: Session, force_reset: bool = False, organization_id: int = 1):
    """
    Idempotently seeds the CCA demo database for a given organization.
    """
    # MRN is globally unique, but the demo dataset's MRNs are hardcoded, so a
    # non-default org gets its MRNs suffixed to avoid colliding with org 1's seed.
    mrn_suffix = "" if organization_id == 1 else f"-O{organization_id}"
    primary_mrn = f"CCA-2026-004417{mrn_suffix}"

    existing_patient = db.query(CCAPatient).filter(
        CCAPatient.mrn == primary_mrn,
        CCAPatient.organization_id == organization_id
    ).first()
    if existing_patient and not force_reset:
        return existing_patient

    if force_reset:
        # Clear existing CCA tables for this organization only
        org_patient_ids = [
            row.id for row in db.query(CCAPatient.id).filter(
                CCAPatient.organization_id == organization_id
            ).all()
        ]
        child_models_by_patient = [
            CCAJourneyEvent, ResponseAssessment, TreatmentClearance, ToxicityEvent,
            TreatmentSession, TreatmentPlan, CarePlanTask, CarePlan, MDTDecision,
            MDTCase, ClinicalBrief, GuidelineContext, StagingRecord,
            CCAResult, CCAOrder, CCABiomarkerResult, CCACancerDiagnosis,
            CCAContradiction, ClinicalFact, CCADocument, CCAIntakeAssessment,
            CCAEncounter, CCAQueueEvent, CCAConsent
        ]
        if org_patient_ids:
            # StagingEvidence has no patient_id of its own -- delete it via its
            # parent StagingRecord ids before StagingRecord itself is deleted below.
            staging_record_ids = [
                row.id for row in db.query(StagingRecord.id).filter(
                    StagingRecord.patient_id.in_(org_patient_ids)
                ).all()
            ]
            if staging_record_ids:
                db.query(StagingEvidence).filter(
                    StagingEvidence.staging_record_id.in_(staging_record_ids)
                ).delete(synchronize_session=False)
            for model in child_models_by_patient:
                db.query(model).filter(model.patient_id.in_(org_patient_ids)).delete(synchronize_session=False)
        db.query(CCAPatient).filter(CCAPatient.organization_id == organization_id).delete(synchronize_session=False)
        db.commit()

    # 1. Primary Demo Patient: Meera S. Nair
    now = datetime.utcnow()
    patient = CCAPatient(
        mrn=primary_mrn,
        name="Meera S. Nair",
        dob="1968-05-14",
        age=58,
        sex="Female",
        phone="+91 98450 12345",
        address="14/280 Hill View Enclave, Kakkanad, Kochi, Kerala - 682030",
        photo_url="https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=150&auto=format&fit=crop&q=80",
        journey_state="UnderInvestigation",
        primary_oncologist="Dr. Sarah Varma (Medical Oncology)",
        attender_name="Suresh Nair",
        attender_phone="+91 98450 12346",
        attender_relationship="Husband",
        organization_id=organization_id,
        demo_flag=True,
        created_at=now - timedelta(days=14)
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)

    # 2. Digital Consent
    consent = CCAConsent(
        patient_id=patient.id,
        consent_types=["treatment", "ai_assistance", "audio_recording", "data_sharing"],
        signatory="Meera S. Nair",
        signatory_reason="Self (Patient)",
        captured_by="Nurse Rekha Menon",
        valid_from=now - timedelta(days=14),
        status="ACTIVE"
    )
    db.add(consent)

    # 3. Queue Event
    queue = CCAQueueEvent(
        patient_id=patient.id,
        location="OPD Consultation Suite 3",
        entered_at=now - timedelta(minutes=45),
        waiting_for="Doctor Consultation (Dr. Sarah Varma)",
        status="ACTIVE"
    )
    db.add(queue)

    # 4. Nurse Intake Assessment
    bsa, bmi = calculate_bsa(height_cm=158.0, weight_kg=64.0)
    intake = CCAIntakeAssessment(
        patient_id=patient.id,
        height_cm=158.0,
        weight_kg=64.0,
        bmi=bmi,
        bsa=bsa,
        bsa_formula="DuBois",
        bp_systolic=132,
        bp_diastolic=84,
        heart_rate=76,
        temperature_c=36.8,
        oxygen_sat=99,
        respiratory_rate=16,
        ecog=1,
        karnofsky=80,
        pain_score=0,
        fall_risk="Low",
        handoff_note="58F referred with right breast mass. Prior documents scanned. Vitals stable. ECOG 1. HTN on Amlodipine. Ready for Dr. Varma.",
        recorded_by="Nurse Rekha Menon",
        status="COMPLETED",
        created_at=now - timedelta(minutes=30)
    )
    db.add(intake)

    # 5. Seed Documents & Extracted Facts
    docs_data = [
        {
            "filename": "referral_letter.pdf",
            "class": "REFERRAL",
            "text": "District General Hospital Referral Letter\nDate: 14 days ago\nPatient: Meera S. Nair, 58/F\nReferring: Dr. K. Ramanathan, MD\nNotes: Patient presents with palpable, painless lump in Left breast upper quadrant noticed 2 months ago. Referred to CCA Oncology for specialized oncologic workup and staging.",
            "facts": [
                {"type": "LATERALITY", "value": "Left Breast (Referral Letter)", "verbatim": "palpable, painless lump in Left breast upper quadrant", "page": 1, "conf": 0.96, "status": "PROPOSED"}
            ]
        },
        {
            "filename": "usg_breast_report.pdf",
            "class": "IMAGING",
            "text": "High-Resolution Bilateral Breast Ultrasonography & Mammography Correlation\nDepartment of Radiodiagnosis\nFindings: Right Breast: At 10 o'clock position (Upper Outer Quadrant), 3 cm from nipple, there is an ill-defined, hypoechoic, taller-than-wide mass measuring 2.8 x 2.2 x 2.4 cm with acoustic shadowing and microcalcifications. Axillary lymph nodes: Cortical thickness normal, no pathologically enlarged lymph nodes.\nImpression: Highly suggestive of malignancy - BIRADS Category 4C (Right Breast).",
            "facts": [
                {"type": "PRIMARY_SITE", "value": "Right Breast", "verbatim": "Right Breast: At 10 o'clock position", "page": 1, "conf": 0.98, "status": "VERIFIED"},
                {"type": "LATERALITY", "value": "Right Breast", "verbatim": "BIRADS Category 4C (Right Breast)", "page": 1, "conf": 0.99, "status": "VERIFIED"},
                {"type": "T_EVIDENCE", "value": "2.8 x 2.2 cm primary lesion (cT2)", "verbatim": "mass measuring 2.8 x 2.2 x 2.4 cm", "page": 1, "conf": 0.95, "status": "VERIFIED"},
                {"type": "IMAGING_FINDING", "value": "BIRADS 4C Right Breast Mass", "verbatim": "BIRADS Category 4C (Right Breast)", "page": 1, "conf": 0.97, "status": "VERIFIED"}
            ]
        },
        {
            "filename": "core_biopsy_histopath.pdf",
            "class": "HISTOPATHOLOGY",
            "text": "Surgical Pathology & Histopathology Report\nSpecimen: USG-Guided Core Needle Biopsy, Right Breast 10 o'clock mass\nMicroscopic Examination: Multiple core biopsies showing cords and nests of atypical epithelial cells infiltrating desmoplastic stroma. Tubule formation <10% (3 points), moderate nuclear pleomorphism (2 points), 4 mitoses per 10 HPF (1 point). Total Nottingham Score: 6/9.\nDiagnosis: INVASIVE DUCTAL CARCINOMA (Invasive Carcinoma of No Special Type), MODIFIED NOTTINGHAM GRADE 2, RIGHT BREAST.\nNo lymphovascular invasion seen.",
            "facts": [
                {"type": "HISTOLOGY", "value": "Invasive Ductal Carcinoma, Grade 2", "verbatim": "INVASIVE DUCTAL CARCINOMA, MODIFIED NOTTINGHAM GRADE 2", "page": 1, "conf": 0.99, "status": "VERIFIED"},
                {"type": "GRADE", "value": "Grade 2 (Nottingham Score 6/9)", "verbatim": "MODIFIED NOTTINGHAM GRADE 2", "page": 1, "conf": 0.98, "status": "VERIFIED"},
                {"type": "N_EVIDENCE", "value": "cN0 (Clinically negative regional nodes)", "verbatim": "Axillary lymph nodes: Cortical thickness normal", "page": 1, "conf": 0.93, "status": "VERIFIED"}
            ]
        },
        {
            "filename": "biomarker_report.pdf",
            "class": "PATHOLOGY",
            "text": "Immunohistochemistry & Biomarker Evaluation Report\nSpecimen: Right Breast Core Biopsy Blocks (ID: H-2026-8812)\nResults:\n- Estrogen Receptor (ER): POSITIVE (80% strong nuclear staining, Allred Score 8/8)\n- Progesterone Receptor (PR): POSITIVE (65% moderate-to-strong nuclear staining, Allred Score 7/8)\n- HER2/neu Oncoprotein: NEGATIVE (Score 1+, faint incomplete membrane staining in <10% cells)\n- Ki-67 Labeling Index: 18% (Low-to-intermediate proliferation)",
            "facts": [
                {"type": "BIOMARKER_RESULT", "value": "ER: Positive (80%, Allred 8/8)", "verbatim": "Estrogen Receptor (ER): POSITIVE (80%)", "page": 1, "conf": 0.99, "status": "VERIFIED"},
                {"type": "BIOMARKER_RESULT", "value": "PR: Positive (65%, Allred 7/8)", "verbatim": "Progesterone Receptor (PR): POSITIVE (65%)", "page": 1, "conf": 0.99, "status": "VERIFIED"},
                {"type": "BIOMARKER_RESULT", "value": "HER2: Negative (1+)", "verbatim": "HER2/neu Oncoprotein: NEGATIVE (Score 1+)", "page": 1, "conf": 0.99, "status": "VERIFIED"},
                {"type": "BIOMARKER_RESULT", "value": "Ki-67: 18%", "verbatim": "Ki-67 Labeling Index: 18%", "page": 1, "conf": 0.96, "status": "VERIFIED"}
            ]
        },
        {
            "filename": "cbc_biochem.pdf",
            "class": "LAB",
            "text": "Clinical Pathology Laboratory Report\nHematology:\nHemoglobin: 11.8 g/dL (12.0-15.0)\nTotal Leukocyte Count: 6,800 /µL (4,000-11,000)\nPlatelet Count: 220,000 /µL (150,000-450,000)\nBiochemistry:\nSerum Creatinine: 0.9 mg/dL (0.6-1.2)\nTotal Bilirubin: 0.8 mg/dL (0.2-1.2)\nSGOT / AST: 24 U/L (10-40)\nSGPT / ALT: 28 U/L (10-40)",
            "facts": [
                {"type": "LAB_RESULT", "value": "Normal Baseline CBC & Renal/Liver Function", "verbatim": "Hb: 11.8, WBC: 6800, Plt: 220k, Creat: 0.9", "page": 1, "conf": 0.97, "status": "VERIFIED"}
            ]
        },
        {
            "filename": "outside_consult_note.pdf",
            "class": "CONSULT_NOTE",
            "text": "Prior Outpatient Clinical Assessment\nHistory: 58-year-old post-menopausal female. Known case of essential hypertension for 4 years on Tab Amlodipine 5mg OD. No prior surgeries. No known drug allergies. Non-smoker, non-alcoholic. Performance Status: ECOG 1.",
            "facts": [
                {"type": "ECOG", "value": "ECOG 1", "verbatim": "Performance Status: ECOG 1", "page": 1, "conf": 0.99, "status": "VERIFIED"},
                {"type": "COMORBIDITY", "value": "Essential Hypertension", "verbatim": "essential hypertension for 4 years", "page": 1, "conf": 0.97, "status": "VERIFIED"},
                {"type": "MEDICATION", "value": "Amlodipine 5mg OD", "verbatim": "Tab Amlodipine 5mg OD", "page": 1, "conf": 0.98, "status": "VERIFIED"}
            ]
        }
    ]

    created_facts = []
    for doc_info in docs_data:
        doc = CCADocument(
            patient_id=patient.id,
            filename=doc_info["filename"],
            mime_type="application/pdf",
            page_count=1,
            file_hash=f"hash_{doc_info['filename']}",
            classification_class=doc_info["class"],
            classification_confidence=0.98,
            ocr_text=doc_info["text"],
            uploaded_by="Nurse Rekha Menon",
            uploaded_at=now - timedelta(days=14),
            status="EXTRACTED"
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        
        for f_info in doc_info["facts"]:
            fact = ClinicalFact(
                patient_id=patient.id,
                document_id=doc.id,
                fact_type=f_info["type"],
                value=f_info["value"],
                verbatim_span=f_info["verbatim"],
                page_number=1,
                bounding_box={"x": 0.15, "y": 0.35, "w": 0.65, "h": 0.08},
                confidence=f_info["conf"],
                status=f_info["status"],
                verified_by="Dr. Sarah Varma" if f_info["status"] == "VERIFIED" else None,
                verified_at=now - timedelta(minutes=20) if f_info["status"] == "VERIFIED" else None,
                created_at=now - timedelta(days=14)
            )
            db.add(fact)
            db.commit()
            db.refresh(fact)
            created_facts.append(fact)

    # 6. Deliberate Contradiction (CTR-01: Referral Left vs USG/Biopsy Right)
    left_fact = [f for f in created_facts if "Left" in f.value][0]
    right_fact = [f for f in created_facts if f.fact_type == "LATERALITY" and "Right" in f.value][0]
    
    contradiction = CCAContradiction(
        patient_id=patient.id,
        rule_id="CTR-01",
        description="Laterality contradiction: Referral letter states 'Left Breast' while USG and Core Biopsy Histopathology report 'Right Breast 10 o'clock mass'.",
        conflicting_fact_ids=[left_fact.id, right_fact.id],
        status="OPEN",
        created_at=now - timedelta(minutes=20)
    )
    db.add(contradiction)

    # 7. Cancer Diagnosis
    diagnosis = CCACancerDiagnosis(
        patient_id=patient.id,
        primary_site="Right Breast",
        laterality="Right",
        histology="Invasive Ductal Carcinoma (No Special Type)",
        icd_o_3="8500/3",
        icd_10="C50.4",
        grade="Grade 2",
        diagnosed_on=(now - timedelta(days=10)).date(),
        basis=["Histopathology of primary tumor", "USG Breast Core Biopsy", "Biomarker profile"],
        evidence_ids=[right_fact.id],
        clinical_setting="Curative Intent / Early Stage",
        status="CONFIRMED",
        confirmed_by="Dr. Sarah Varma",
        confirmed_at=now - timedelta(minutes=15)
    )
    db.add(diagnosis)

    # 8. Biomarker Results
    bm_list = [
        ("ER", "Positive (80%, Allred 8/8)"),
        ("PR", "Positive (65%, Allred 7/8)"),
        ("HER2", "Negative (Score 1+)"),
        ("Ki-67", "18% (Low-Intermediate)")
    ]
    for m_name, m_res in bm_list:
        bm = CCABiomarkerResult(
            patient_id=patient.id,
            marker_name=m_name,
            result_as_reported=m_res,
            method="IHC",
            platform="Ventana Benchmark Ultra",
            specimen="Core needle biopsy",
            adequacy="Adequate",
            lab_name="Oncology Reference Labs",
            reported_on=(now - timedelta(days=8)).date(),
            status="RESULTED"
        )
        db.add(bm)

    # 9. Initial Staging Record (Evidence Incomplete due to missing M)
    staging = StagingRecord(
        patient_id=patient.id,
        staging_system="AJCC Cancer Staging Manual",
        system_version="8th Edition",
        classification_prefix="c",
        t_stage="cT2",
        n_stage="cN0",
        m_stage="cM_UNKNOWN",
        stage_value="cT2 cN0 (Staging Incomplete)",
        prognostic_stage_group="Incomplete",
        status="EVIDENCE_INCOMPLETE",
        version_no=1,
        created_at=now - timedelta(minutes=15)
    )
    db.add(staging)

    # 10. Open Consultation Encounter
    encounter = CCAEncounter(
        patient_id=patient.id,
        encounter_type="OPD_CONSULTATION",
        specialty="Medical Oncology",
        clinician="Dr. Sarah Varma",
        started_at=now - timedelta(minutes=15),
        template_id="ONC_BREAST_OPD_v1",
        status="OPEN",
        note_status="AI_DRAFT",
        note_content={
            "chief_complaint": "Referred with right breast mass discovered 2 months ago.",
            "hpi": "58-year-old post-menopausal female presents with a 2.8 cm painless lump in right upper outer quadrant. Outside core biopsy confirms Invasive Ductal Carcinoma Grade 2. ER+ 80%, PR+ 65%, HER2 Negative 1+, Ki-67 18%. No bone pain, cough, or weight loss.",
            "physical_exam": "Right Breast: 2.8 cm firm, non-tender mass at 10 o'clock, mobile. No skin retraction or nipple discharge. Axilla: No palpable lymph nodes bilaterally. Left Breast & Axilla: Normal.",
            "assessment": "Invasive Ductal Carcinoma of Right Breast, Grade 2, HR-Positive / HER2-Negative. Clinical Stage cT2 cN0.",
            "plan": "1. Order CECT Chest + Abdomen & Pelvis to complete M-staging.\n2. Multidisciplinary Tumor Board (MDT) presentation for sequencing.\n3. Baseline Echocardiogram / MUGA scan prior to anthracycline initiation."
        },
        raw_transcript="Patient Meera Nair, 58 female with right breast mass, 2.8 centimeters, upper outer quadrant. Biopsy shows invasive ductal carcinoma grade 2, hormone receptor positive, HER2 negative. Axilla is clinically clear. Need to get a CT chest abdomen to rule out distant metastases and present her case in Thursday's breast tumor board."
    )
    db.add(encounter)

    # 11. Initial Order: CECT Chest + Abdomen (Staging-relevant)
    order = CCAOrder(
        patient_id=patient.id,
        encounter_id=encounter.id,
        order_type="RADIOLOGY",
        item_name="CECT Chest, Abdomen & Pelvis (Staging)",
        item_code="RAD-CT-CAP-01",
        clinical_indication="Invasive ductal carcinoma of right breast (cT2 cN0) - complete distant metastatic staging workup.",
        priority="ROUTINE",
        staging_relevant=True,
        status="RAISED",
        requested_by="Dr. Sarah Varma",
        ordered_at=now - timedelta(minutes=10)
    )
    db.add(order)

    # 12. Secondary Background Patients (Command Centre realism)
    secondary_data = [
        ("CCA-2026-001092", "Ananya Sen", 46, "Female", "Awaiting Intake", "Breast Oncology (Pre-Op)"),
        ("CCA-2026-002341", "Rajesh Kumar", 62, "Male", "Results Pending", "Lung Oncology (EGFR+)"),
        ("CCA-2026-003890", "Deepak Verma", 54, "Male", "Staging Incomplete", "Head & Neck (Oral Cavity)"),
        ("CCA-2026-004112", "Sunita Sharma", 50, "Female", "MDT Scheduled", "Ovarian High-Grade Serous"),
        ("CCA-2026-004290", "Venkatesh Rao", 67, "Male", "In Treatment (Cycle 3)", "Colorectal (FOLFOX6)"),
        ("CCA-2026-004355", "Pooja Hegde", 41, "Female", "Treatment Day (Cycle 1)", "Triple Negative Breast"),
        ("CCA-2026-004380", "Mohammad Farooq", 59, "Male", "Reassessment Pending", "Prostate (Post-Radiotherapy)"),
        ("CCA-2026-004402", "Geeta Pillai", 52, "Female", "Follow-Up Due", "Breast (Adjuvant Endocrine)"),
        ("CCA-2026-004420", "Harish Patel", 65, "Male", "Under Investigation", "Gastric Adenocarcinoma"),
        ("CCA-2026-004435", "Lakshmi Bai", 70, "Female", "Care Plan Ready", "Endometrial Carcinoma"),
        ("CCA-2026-004448", "Karthik Subramanian", 38, "Male", "In Treatment (Cycle 4)", "Hodgkin Lymphoma (ABVD)"),
        ("CCA-2026-004455", "Fatima Zahra", 49, "Female", "Awaiting Consultation", "Cervical Squamous Cell")
    ]
    for mrn_code, p_name, p_age, p_sex, state, note in secondary_data:
        sec_p = CCAPatient(
            mrn=f"{mrn_code}{mrn_suffix}",
            name=p_name,
            age=p_age,
            sex=p_sex,
            phone="+91 98000 00000",
            address="Kerala, India",
            journey_state=state.replace(" ", ""),
            primary_oncologist="Oncology Care Team",
            organization_id=organization_id,
            demo_flag=True,
            created_at=now - timedelta(days=20)
        )
        db.add(sec_p)

    # 13. Initial Journey Events for Meera S. Nair
    events = [
        ("REGISTRATION", "Patient Registered & Consent Captured", "INTAKE", "Patient registered at reception. Digital consent signed for clinical care and AI transcription.", "Nurse Rekha Menon", "Nurse", now - timedelta(days=14)),
        ("DOC_INGESTION", "6 Outside Clinical Documents Ingested", "INVESTIGATION", "Referral letter, USG breast, core biopsy, biomarker IHC, CBC biochemistry, and outside clinic note ingested and classified.", "AI Document Ingestion", "System", now - timedelta(days=14)),
        ("FACT_EXTRACTION", "Candidate Facts Extracted & Verified", "INVESTIGATION", "14 clinical facts extracted. Contradiction CTR-01 flagged on laterality.", "Dr. Sarah Varma", "Doctor", now - timedelta(minutes=20)),
        ("NURSE_INTAKE", "Nurse Intake Assessment Completed", "INTAKE", f"Vitals stable, ECOG 1, BSA {bsa} m² (DuBois). Patient escorted to OPD Suite 3.", "Nurse Rekha Menon", "Nurse", now - timedelta(minutes=30)),
        ("OPD_ENCOUNTER", "OPD Consultation Opened", "CONSULTATION", "Dr. Sarah Varma initiated clinical consultation and physical examination.", "Dr. Sarah Varma", "Doctor", now - timedelta(minutes=15)),
        ("ORDER_RAISED", "Staging Investigation Ordered", "INVESTIGATION", "CECT Chest, Abdomen & Pelvis ordered to complete distant metastatic M-staging.", "Dr. Sarah Varma", "Doctor", now - timedelta(minutes=10))
    ]
    for ev_type, ev_title, ev_cat, ev_desc, a_name, a_role, ev_time in events:
        j_ev = CCAJourneyEvent(
            patient_id=patient.id,
            event_type=ev_type,
            event_title=ev_title,
            event_category=ev_cat,
            description=ev_desc,
            actor_name=a_name,
            actor_role=a_role,
            timestamp=ev_time
        )
        db.add(j_ev)

    db.commit()
    return patient


def simulate_ct_result(db: Session, patient_id: int):
    """
    Presenter Action: Simulates arrival of CECT Chest + Abdomen result.
    Closes the deliberate gap, adds M0 evidence, and sets Staging Readiness to READY_FOR_STAGING.
    """
    now = datetime.utcnow()
    patient = db.query(CCAPatient).filter(CCAPatient.id == patient_id).first()
    if not patient:
        return None
        
    order = db.query(CCAOrder).filter(
        CCAOrder.patient_id == patient_id,
        CCAOrder.item_code == "RAD-CT-CAP-01"
    ).first()
    if order:
        order.status = "RESULTED"
        
    # Create CECT Document
    doc = CCADocument(
        patient_id=patient.id,
        filename="ct_chest_abdomen_report.pdf",
        mime_type="application/pdf",
        page_count=1,
        file_hash="hash_ct_chest_abdomen_report",
        classification_class="IMAGING",
        classification_confidence=0.99,
        ocr_text="Contrast-Enhanced Computed Tomography (CECT) of Chest, Abdomen and Pelvis:\nLungs: Clear, no nodules or pleural effusion.\nLiver: Normal size and parenchymal attenuation, no focal lesions.\nLymph nodes: No mediastinal, retroperitoneal or pelvic lymphadenopathy.\nBones: No suspicious lytic or sclerotic osseous lesions.\nImpression: No evidence of distant metastatic disease (cM0).",
        uploaded_by="Imaging Integration Service",
        uploaded_at=now,
        status="VERIFIED"
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    
    # Create Verified M_EVIDENCE Fact
    m_fact = ClinicalFact(
        patient_id=patient.id,
        document_id=doc.id,
        fact_type="M_EVIDENCE",
        value="cM0: No distant metastasis on CECT Chest/Abdomen/Pelvis",
        verbatim_span="No evidence of distant metastatic disease (cM0)",
        page_number=1,
        bounding_box={"x": 0.12, "y": 0.78, "w": 0.76, "h": 0.08},
        confidence=0.99,
        status="VERIFIED",
        verified_by="Dr. Sarah Varma",
        verified_at=now,
        created_at=now
    )
    db.add(m_fact)
    
    # Create CCAResult
    result = CCAResult(
        order_id=order.id if order else None,
        patient_id=patient.id,
        result_type="IMAGING",
        title="CECT Chest, Abdomen & Pelvis (Staging)",
        findings_text="No evidence of pulmonary, hepatic, nodal, or osseous metastasis. Staging category: cM0.",
        extracted_values={"m_stage": "cM0", "metastasis": "None"},
        document_id=doc.id,
        is_critical=False,
        status="NEW",
        resulted_at=now
    )
    db.add(result)
    
    # Update Staging Record status to READY_FOR_STAGING
    staging = db.query(StagingRecord).filter(
        StagingRecord.patient_id == patient_id
    ).order_by(StagingRecord.version_no.desc()).first()
    if staging:
        staging.m_stage = "cM0"
        staging.status = "READY_FOR_STAGING"
        staging.stage_value = "cT2 cN0 cM0 (Ready for Clinician Confirmation)"
        
        # Add Staging Evidence link
        stg_ev = StagingEvidence(
            staging_record_id=staging.id,
            category="M",
            fact_id=m_fact.id,
            excerpt="No evidence of distant metastatic disease (cM0) on CECT Chest/Abdomen/Pelvis",
            added_by="Dr. Sarah Varma",
            added_at=now
        )
        db.add(stg_ev)

    # Journey Event
    j_ev = CCAJourneyEvent(
        patient_id=patient.id,
        event_type="RESULT_RECEIVED",
        event_title="CECT Staging Result Returned (cM0)",
        event_category="INVESTIGATION",
        description="CECT Chest/Abdomen returned negative for distant metastasis. Staging Readiness flipped to READY.",
        actor_name="Imaging Integration Service",
        actor_role="System",
        provenance_doc_id=doc.id,
        provenance_fact_id=m_fact.id,
        timestamp=now
    )
    db.add(j_ev)
    db.commit()
    return result
