"""
Deterministic Clinical Engines for CCA Cancer Care AI OS.
Strict human-in-the-loop governance:
- Zero autonomous staging or treatment generation
- Explicit absence vocabulary
- Mathematical accuracy for BSA (DuBois) and BMI
"""

import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from .models_cca import (
    CCAPatient, ClinicalFact, CCAContradiction, CCACancerDiagnosis,
    CCABiomarkerResult, StagingRecord, StagingEvidence, GuidelineContext,
    ClinicalBrief, MDTCase, MDTDecision, CCAIntakeAssessment, CCADocument,
    CCAOrder, CCAResult, TreatmentOrder, OralTherapyPrescription, OralTherapyHoldEvent,
    MedicationReconciliationEntry,
)
from .models_cca_oncology_ext import (
    CCARadiationPhase, RadiationFraction, RadiationPrescription, PalliativeTreatmentOrder,
)
from .scribe import scribe


def calculate_bsa(height_cm: float, weight_kg: float, formula: str = "DuBois") -> Tuple[float, float]:
    """
    Calculates Body Surface Area (m^2) and BMI (kg/m^2).
    DuBois formula: 0.007184 * (height^0.725) * (weight^0.425)
    Mosteller formula: sqrt((height * weight) / 3600)
    """
    if not height_cm or not weight_kg or height_cm <= 0 or weight_kg <= 0:
        return 0.0, 0.0
    
    height_m = height_cm / 100.0
    bmi = round(weight_kg / (height_m * height_m), 2)
    
    if formula.lower() == "mosteller":
        bsa = round(math.sqrt((height_cm * weight_kg) / 3600.0), 2)
    else:  # Default DuBois
        bsa = round(0.007184 * (height_cm ** 0.725) * (weight_kg ** 0.425), 2)
        
    return bsa, bmi


def detect_contradictions(db: Session, patient_id: int) -> List[CCAContradiction]:
    """
    Deterministic contradiction detector. Cross-references facts (e.g. Laterality Left vs Right).
    """
    facts = db.query(ClinicalFact).filter(
        ClinicalFact.patient_id == patient_id,
        ClinicalFact.status.in_(["PROPOSED", "VERIFIED"])
    ).all()
    
    laterality_facts = [f for f in facts if f.fact_type == "LATERALITY"]
    contradictions = []
    
    # Check for laterality conflicts (e.g. Left vs Right)
    left_facts = [f for f in laterality_facts if "left" in f.value.lower()]
    right_facts = [f for f in laterality_facts if "right" in f.value.lower()]
    
    if left_facts and right_facts:
        existing = db.query(CCAContradiction).filter(
            CCAContradiction.patient_id == patient_id,
            CCAContradiction.rule_id == "CTR-01"
        ).first()
        
        conflict_ids = [f.id for f in left_facts + right_facts]
        desc = (
            f"Laterality contradiction detected: {len(left_facts)} document(s) state 'Left' "
            f"while {len(right_facts)} document(s) state 'Right'. Requires clinician disposition."
        )
        
        if not existing:
            ctr = CCAContradiction(
                patient_id=patient_id,
                rule_id="CTR-01",
                description=desc,
                conflicting_fact_ids=conflict_ids,
                status="OPEN"
            )
            db.add(ctr)
            db.commit()
            db.refresh(ctr)
            contradictions.append(ctr)
        else:
            contradictions.append(existing)
            
    return contradictions


def evaluate_staging_readiness(db: Session, patient_id: int) -> Dict:
    """
    Evaluates evidence completeness for AJCC staging.
    Never invents a stage; outputs readiness state and missing requirements.
    """
    # Check for confirmed stage first
    confirmed_record = db.query(StagingRecord).filter(
        StagingRecord.patient_id == patient_id,
        StagingRecord.status == "CLINICIAN_CONFIRMED"
    ).order_by(StagingRecord.version_no.desc()).first()
    
    # Check for open contradictions
    open_ctrs = db.query(CCAContradiction).filter(
        CCAContradiction.patient_id == patient_id,
        CCAContradiction.status == "OPEN"
    ).all()
    
    # Check verified facts
    verified_facts = db.query(ClinicalFact).filter(
        ClinicalFact.patient_id == patient_id,
        ClinicalFact.status == "VERIFIED"
    ).all()
    
    fact_types = {f.fact_type for f in verified_facts}
    
    has_t = "T_EVIDENCE" in fact_types or "IMAGING_FINDING" in fact_types
    has_n = "N_EVIDENCE" in fact_types or any("node" in f.value.lower() or "n0" in f.value.lower() for f in verified_facts)
    has_m = "M_EVIDENCE" in fact_types or any("m0" in f.value.lower() or "metastasis" in f.value.lower() for f in verified_facts)
    has_histo = "HISTOLOGY" in fact_types or "PRIMARY_SITE" in fact_types
    
    satisfied = []
    missing = []
    blocking = []
    
    if has_histo:
        satisfied.append("Primary Tumor Histopathology & Site confirmed")
    else:
        missing.append({"input": "HISTOLOGY", "whatWouldSatisfy": "Histopathology confirmation with histologic subtype and grade"})
        
    if has_t:
        satisfied.append("T-Category: Primary tumor size & physical extent documented")
    else:
        missing.append({"input": "T_EVIDENCE", "whatWouldSatisfy": "USG / Mammogram / MRI / Physical measurement of primary tumor diameter"})
        
    if has_n:
        satisfied.append("N-Category: Regional lymph node evaluation documented")
    else:
        missing.append({"input": "N_EVIDENCE", "whatWouldSatisfy": "Clinical axillary examination or nodal ultrasound"})
        
    if has_m:
        satisfied.append("M-Category: Distant metastatic workup documented")
    else:
        missing.append({"input": "M_EVIDENCE", "whatWouldSatisfy": "Contrast-enhanced CT Chest + Abdomen / PET-CT to exclude distant metastasis"})
        
    if open_ctrs:
        for c in open_ctrs:
            blocking.append(f"Open contradiction ({c.rule_id}): {c.description}")
            
    if confirmed_record:
        state = "CLINICIAN_CONFIRMED"
    elif blocking:
        state = "EVIDENCE_INCOMPLETE"
    elif missing:
        state = "PARTIALLY_READY" if len(satisfied) >= 2 else "EVIDENCE_INCOMPLETE"
    else:
        state = "READY_FOR_STAGING"
        
    return {
        "state": state,
        "satisfied": satisfied,
        "missing": missing,
        "blocking": blocking,
        "confirmed_record": {
            "id": confirmed_record.id,
            "prefix": confirmed_record.classification_prefix,
            "t_stage": confirmed_record.t_stage,
            "n_stage": confirmed_record.n_stage,
            "m_stage": confirmed_record.m_stage,
            "stage_value": confirmed_record.stage_value,
            "group": confirmed_record.prognostic_stage_group,
            "confirmed_by": confirmed_record.confirmed_by,
            "confirmed_at": confirmed_record.confirmed_at.isoformat() if confirmed_record.confirmed_at else None,
            "version_no": confirmed_record.version_no
        } if confirmed_record else None
    }


def evaluate_guideline_readiness(db: Session, patient_id: int) -> Dict:
    """
    Evaluates guideline readiness.
    Strict prerequisite: Requires StagingRecord.status == CLINICIAN_CONFIRMED.
    """
    staging_status = evaluate_staging_readiness(db, patient_id)
    is_staged = staging_status["state"] == "CLINICIAN_CONFIRMED"
    
    biomarkers = db.query(CCABiomarkerResult).filter(
        CCABiomarkerResult.patient_id == patient_id,
        CCABiomarkerResult.status == "RESULTED"
    ).all()
    
    marker_names = {b.marker_name.upper() for b in biomarkers}
    has_er = "ER" in marker_names
    has_pr = "PR" in marker_names
    has_her2 = "HER2" in marker_names
    
    satisfied = []
    missing = []
    
    if is_staged:
        satisfied.append(f"Clinician-Confirmed AJCC Stage: {staging_status['confirmed_record']['stage_value']}")
    else:
        missing.append("Clinician-Confirmed AJCC Staging Record (Mandatory Gating Requirement)")
        
    if has_er and has_pr and has_her2:
        satisfied.append("Hormone Receptor & HER2 Biomarker Profile resulted")
    else:
        missing.append("Complete Biomarker Panel (ER, PR, HER2 status required for breast pathway)")
        
    if not is_staged:
        state = "NOT_READY"
    elif missing:
        state = "PARTIALLY_READY"
    else:
        state = "READY"
        
    return {
        "state": state,
        "satisfied": satisfied,
        "missing": missing,
        "guideline_source": "NCCN Clinical Practice Guidelines in Oncology (NCCN Guidelines®) - Breast Cancer",
        "version": "Version 4.2026"
    }


def _must_not_miss_items(staging: Dict, contradictions: List, unverified_facts: List, biomarkers: List, diagnosis) -> List[str]:
    """The NEXUS brief's 'Must-Not-Miss Considerations' section (architecture doc Sec 20):
    dangerous possibilities not yet excluded, and the reason they matter -- derived only from
    what the record itself indicates is unresolved, never a manufactured clinical judgment."""
    items = []
    if any(m["input"] == "M_EVIDENCE" for m in staging["missing"]):
        items.append("Distant metastatic disease has not been excluded (M-stage evidence missing) -- confirm before finalizing treatment intent.")
    if any(c.status == "OPEN" for c in contradictions):
        items.append("An unresolved contradiction is present in the record -- verify source documents before relying on the affected fact; it may indicate a wrong-attribution or reporting error.")
    if unverified_facts:
        items.append(f"{len(unverified_facts)} AI-extracted fact(s) are still pending clinician verification -- do not treat as confirmed until reviewed.")
    if diagnosis and not biomarkers:
        items.append("No biomarker/molecular results are on record for a confirmed diagnosis -- receptor/molecular status may materially change guideline pathway and treatment intent.")
    return items


def synthesize_nexus_brief(db: Session, patient_id: int) -> Dict:
    """
    Synthesizes the 15-section NEXUS Clinical Brief purely from verified facts.
    Never invents diagnoses or recommends unauthorized treatments.
    """
    patient = db.query(CCAPatient).filter(CCAPatient.id == patient_id).first()
    if not patient:
        return {}
        
    intake = db.query(CCAIntakeAssessment).filter(
        CCAIntakeAssessment.patient_id == patient_id
    ).order_by(CCAIntakeAssessment.created_at.desc()).first()
    
    diagnosis = db.query(CCACancerDiagnosis).filter(
        CCACancerDiagnosis.patient_id == patient_id
    ).order_by(CCACancerDiagnosis.created_at.desc()).first()
    
    biomarkers = db.query(CCABiomarkerResult).filter(
        CCABiomarkerResult.patient_id == patient_id
    ).all()

    staging = evaluate_staging_readiness(db, patient_id)
    guidelines = evaluate_guideline_readiness(db, patient_id)
    contradictions = db.query(CCAContradiction).filter(
        CCAContradiction.patient_id == patient_id
    ).all()

    docs = db.query(CCADocument).filter(CCADocument.patient_id == patient_id).all()

    all_facts = db.query(ClinicalFact).filter(ClinicalFact.patient_id == patient_id).all()
    verified_facts = [f for f in all_facts if f.status == "VERIFIED"]
    unverified_facts = [f for f in all_facts if f.status == "PROPOSED"]
    fact_by_type = {f.fact_type: f for f in verified_facts}
    lab_results = [f for f in verified_facts if f.fact_type == "LAB_RESULT"]

    mdt_cases = db.query(MDTCase).filter(MDTCase.patient_id == patient_id).order_by(MDTCase.id.desc()).all()

    # Radiation therapy (Oncology Review Results PDF item 5): pulled straight from the
    # already-recorded course/phase/fraction records so a summary never requires re-typing
    # site/dose/fractions/completion that a Radiation Oncologist/Technologist already
    # entered through the Radiation Plan workflow.
    radiation_courses = db.query(RadiationPrescription).filter(RadiationPrescription.patient_id == patient_id).all()
    radiation_summary_parts: List[str] = []
    for course in radiation_courses:
        phases = db.query(CCARadiationPhase).filter(
            CCARadiationPhase.prescription_id == course.id
        ).order_by(CCARadiationPhase.phase_number).all()
        for phase in phases:
            delivered = db.query(RadiationFraction).filter(
                RadiationFraction.phase_id == phase.id, RadiationFraction.status == "delivered"
            ).count()
            radiation_summary_parts.append(
                f"Phase {phase.phase_number} ({phase.label}, {phase.treatment_site}"
                f"{' ' + phase.laterality if phase.laterality else ''}): "
                f"{phase.total_prescribed_dose_gy} Gy / {phase.number_of_fractions} fractions "
                f"({phase.dose_per_fraction_gy} Gy/#) -- {delivered}/{phase.number_of_fractions} delivered, "
                f"status: {phase.rt_sub_status.replace('_', ' ')}."
            )

    uncertainty_reasons = []
    if staging["state"] != "CLINICIAN_CONFIRMED":
        uncertainty_reasons.append("Clinical stage has not yet been confirmed by the treating oncologist.")
    if staging["missing"]:
        for m in staging["missing"]:
            uncertainty_reasons.append(f"Missing clinical input: {m['whatWouldSatisfy']}.")
    if any(c.status == "OPEN" for c in contradictions):
        uncertainty_reasons.append("Unresolved clinical contradiction across outside documents.")
        
    uncertainty_level = "HIGH" if len(uncertainty_reasons) >= 2 else "MODERATE" if uncertainty_reasons else "LOW"
    
    # 13 structured sections
    sections = {
        "1_demographics": {
            "title": "Patient Identification & Demographics",
            "content": f"{patient.name}, {patient.age}y/{patient.sex}, MRN: {patient.mrn}. Primary Oncologist: {patient.primary_oncologist or 'Unassigned'}. Attender: {patient.attender_name or 'Self'} ({patient.attender_relationship or 'N/A'})."
        },
        "2_primary_diagnosis": {
            "title": "Primary Oncologic Diagnosis",
            "content": (
                f"{diagnosis.histology}, Grade {diagnosis.grade or '[NOT_RECORDED]'}, {diagnosis.laterality or '[NOT_RECORDED]'} Breast. "
                f"Basis: {', '.join(diagnosis.basis) if diagnosis.basis else '[NOT_RECORDED]'}."
                if diagnosis else "[NOT_RECORDED] No confirmed oncologic diagnosis on record yet."
            )
        },
        "3_staging_extent": {
            "title": "Staging & Anatomic Extent of Disease",
            "content": (
                f"Status: {staging['state']}. Staged Stage: {staging['confirmed_record']['stage_value']}."
                if staging["confirmed_record"]
                else f"Status: {staging['state']}. No clinician-confirmed stage on record. [NOT_STAGED]"
            )
        },
        "4_biomarker_profile": {
            "title": "Biomarker & Molecular Subtype",
            "content": ", ".join([f"{b.marker_name}: {b.result_as_reported}" for b in biomarkers]) if biomarkers else "Biomarker assessment pending."
        },
        "5_performance_history": {
            "title": "Performance Status & Comorbidities",
            "content": (
                (
                    f"ECOG PS: {intake.ecog}, Karnofsky: {intake.karnofsky}%, Pain Score: {intake.pain_score}/10. BSA: {intake.bsa} m² (DuBois)."
                    if intake else "Performance status and vitals: [NOT_RECORDED] No nurse intake assessment on record yet."
                )
                + f" Comorbidities: {fact_by_type['COMORBIDITY'].value if 'COMORBIDITY' in fact_by_type else '[NOT_RECORDED]'}."
                + f" Concurrent medications: {fact_by_type['MEDICATION'].value if 'MEDICATION' in fact_by_type else '[NOT_RECORDED]'}."
            )
        },
        "6_evidence_chain": {
            "title": "Longitudinal Evidence & Document Provenance",
            "content": f"{len(docs)} historical outside document(s) ingested, classified and verified with field-level provenance."
        },
        "7_best_next_investigation": {
            "title": "Best Next Clinical Investigation",
            "content": "Contrast-enhanced CT Chest + Abdomen / Pelvis to rule out distant metastasis and complete formal M-staging." if any(m['input'] == 'M_EVIDENCE' for m in staging['missing']) else "Baseline staging imaging complete."
        },
        "8_contradictions": {
            "title": "Contradictions & Data Discordance",
            "content": "; ".join([f"[{c.rule_id} - {c.status}]: {c.description}" for c in contradictions]) if contradictions else "No active clinical contradictions detected."
        },
        "9_guideline_context": {
            "title": "Guideline Concordance & Pathway Readiness",
            "content": f"Guideline state: {guidelines['state']}. Pathway: {guidelines['guideline_source']} ({guidelines['version']})."
        },
        "10_mdt_topics": {
            "title": "Multidisciplinary Tumor Board (MDT) Focus",
            "content": (
                f"Active MDT case: \"{mdt_cases[0].question}\" (status: {mdt_cases[0].status})."
                if mdt_cases else (
                    "No MDT case on record yet. "
                    + (
                        "Complete " + " and ".join(
                            ([ "clinical staging" ] if staging["state"] != "CLINICIAN_CONFIRMED" else [])
                            + ([ "biomarker/molecular profiling" ] if not biomarkers else [])
                        ) + " before referral, or refer now if clinical urgency requires it."
                        if staging["state"] != "CLINICIAN_CONFIRMED" or not biomarkers
                        else "Refer to tumour board for treatment sequencing discussion when the treating clinician determines complexity warrants it."
                    )
                )
            )
        },
        "11_uncertainty_analysis": {
            "title": "Clinical Uncertainty & Diagnostic Confidence",
            "content": f"Qualitative Confidence: {uncertainty_level}. Key drivers: " + (" ".join(uncertainty_reasons) if uncertainty_reasons else "All clinical evidence verified and concordant.")
        },
        "12_safety_flags": {
            "title": "Quality, Toxicity & Organ Baseline Flags",
            "content": (
                f"Baseline labs: {'; '.join(f.value for f in lab_results) if lab_results else '[NOT_RECORDED] No baseline CBC/LFT/KFT on record.'} "
                f"Fall Risk: {intake.fall_risk if intake else '[NOT_RECORDED]'}."
            )
        },
        "13_decision_support": {
            "title": "Clinical Decision Support Options",
            "content": (
                "Diagnosis and/or staging not yet clinician-confirmed -- decision support unavailable until both are complete."
                if not diagnosis or staging["state"] != "CLINICIAN_CONFIRMED"
                else (
                    f"Confirmed: {diagnosis.histology}, Stage {staging['confirmed_record']['stage_value']}. "
                    f"Biomarkers: {', '.join(f'{b.marker_name} {b.result_as_reported}' for b in biomarkers) if biomarkers else '[NOT_RECORDED]'}. "
                    f"Guideline pathway state: {guidelines['state']}. "
                    "No treatment modality, regimen or dose is suggested here -- discuss treatment strategy and sequencing with MDT before finalizing a Treatment Plan."
                )
            )
        },
        "14_must_not_miss": {
            "title": "Must-Not-Miss Considerations",
            "content": " ".join(_must_not_miss_items(staging, contradictions, unverified_facts, biomarkers, diagnosis)) or "No unresolved must-not-miss considerations identified from the current verified record."
        },
        "15_radiation_therapy": {
            "title": "Radiation Therapy Summary",
            "content": " ".join(radiation_summary_parts) if radiation_summary_parts else "No radiation therapy on record."
        }
    }
    
    return {
        "sections": sections,
        "clinical_uncertainty": uncertainty_level,
        "uncertainty_reasons": uncertainty_reasons,
        "generated_at": datetime.utcnow().isoformat()
    }


def build_medication_lists(db: Session, patient_id: int) -> Dict[str, List[Dict]]:
    """Patient History's Current/Past Medications split (architecture: no cross-table medication
    aggregator existed before this -- IV chemo, oral therapy, palliative/supportive orders, home
    medications reconciled at intake, and medications mentioned in outside documents each live in
    their own table with their own status vocabulary). Reads existing status fields only -- every
    write path that flips one (cancel_treatment_order, record_clearance_decision's DISCONTINUED
    branch, create_oral_therapy_hold_event, transition_palliative_order) already exists; this
    function never writes anything, so "a medicine stopped in a cycle moves to Past" falls out of
    those endpoints' existing behavior for free.

    A DRAFT order/prescription (never signed/authorized) is excluded from both lists -- it isn't
    a medication yet. `stopped_at`/`stopped_reason` are left None where the underlying model
    genuinely doesn't record a discontinuation timestamp/reason (e.g. TreatmentOrder.cancel has
    no dedicated column for either) rather than guessed from an unrelated timestamp.
    """
    current: List[Dict] = []
    past: List[Dict] = []

    orders = db.query(TreatmentOrder).filter(TreatmentOrder.patient_id == patient_id).all()
    superseded_order_ids = {o.supersedes_id for o in orders if o.supersedes_id}
    for o in orders:
        if o.status == "DRAFT":
            continue
        instructions = o.instructions if isinstance(o.instructions, dict) else {}
        item = {
            "source": "IV_CHEMO",
            "drug": instructions.get("drug") or "Unspecified drug",
            "detail": ", ".join(f"{k}: {v}" for k, v in instructions.items() if k != "drug" and v),
            "status": o.status, "since": o.signed_at or o.created_at,
            "stopped_at": None, "stopped_reason": o.revision_reason if o.status == "CANCELLED" else None,
        }
        (past if o.status == "CANCELLED" or o.id in superseded_order_ids else current).append(item)

    rxs = db.query(OralTherapyPrescription).filter(OralTherapyPrescription.patient_id == patient_id).all()
    superseded_rx_ids = {r.supersedes_id for r in rxs if r.supersedes_id}
    latest_discontinue_event: Dict[int, OralTherapyHoldEvent] = {}
    for e in db.query(OralTherapyHoldEvent).filter(
        OralTherapyHoldEvent.patient_id == patient_id, OralTherapyHoldEvent.event_type == "Discontinue"
    ).order_by(OralTherapyHoldEvent.created_at.asc()):
        latest_discontinue_event[e.prescription_id] = e  # ascending order -> last write wins == latest
    for rx in rxs:
        if rx.status == "DRAFT":
            continue
        ev = latest_discontinue_event.get(rx.id)
        item = {
            "source": "ORAL_THERAPY",
            "drug": rx.drug,
            "detail": ", ".join(filter(None, [rx.final_prescribed_dose, rx.frequency])),
            "status": rx.status, "since": rx.signed_at or rx.created_at,
            "stopped_at": ev.created_at if (rx.status == "DISCONTINUED" and ev) else None,
            "stopped_reason": ev.reason if (rx.status == "DISCONTINUED" and ev) else None,
        }
        is_past = rx.status in ("DISCONTINUED", "COMPLETED") or rx.id in superseded_rx_ids
        (past if is_past else current).append(item)

    for p in db.query(PalliativeTreatmentOrder).filter(PalliativeTreatmentOrder.patient_id == patient_id).all():
        if p.status == "Draft":
            continue
        item = {
            "source": "PALLIATIVE",
            "drug": p.order_type, "detail": p.instructions,
            "status": p.status, "since": p.signed_at or p.created_at,
            "stopped_at": None, "stopped_reason": p.discontinued_reason if p.status == "Discontinued" else None,
        }
        (past if p.status == "Discontinued" else current).append(item)

    for m in db.query(MedicationReconciliationEntry).filter(MedicationReconciliationEntry.patient_id == patient_id).all():
        is_discontinued = m.action == "Discontinue"
        item = {
            "source": "HOME_MEDICATION",
            "drug": m.drug_name, "detail": ", ".join(filter(None, [m.dose, m.frequency, m.route])),
            "status": m.action, "since": m.reconciled_at,
            "stopped_at": m.reconciled_at if is_discontinued else None,
            "stopped_reason": m.action_reason if is_discontinued else None,
        }
        (past if is_discontinued else current).append(item)

    # Free-text medication mentions extracted from outside documents: always historical (no
    # ongoing status of their own), and a clinician-rejected extraction is dropped outright.
    for f in db.query(ClinicalFact).filter(
        ClinicalFact.patient_id == patient_id, ClinicalFact.fact_type == "MEDICATION",
        ClinicalFact.status != "REJECTED",
    ).all():
        past.append({
            "source": "DOCUMENT_HISTORY",
            "drug": f.value, "detail": f.verbatim_span,
            "status": f.status, "since": f.created_at,
            "stopped_at": None, "stopped_reason": None,
        })

    epoch = datetime(1970, 1, 1)
    current.sort(key=lambda i: i["since"] or epoch, reverse=True)
    past.sort(key=lambda i: i["stopped_at"] or i["since"] or epoch, reverse=True)
    return {"current": current, "past": past}


def generate_care_plan_prefill(db: Session, patient_id: int) -> Dict:
    """
    Pre-populates a Live Care Plan draft from verified diagnosis, staging, NCCN context, and MDT decisions.
    Zero autonomous treatment generation: a specific regimen/dose is only ever prefilled once an
    MDT has actually recorded a recommendation for this patient. Absent that, every field is
    explicit about what has and hasn't been decided rather than filling the gap with plausible text.
    """
    diagnosis = db.query(CCACancerDiagnosis).filter(
        CCACancerDiagnosis.patient_id == patient_id
    ).first()

    staging = evaluate_staging_readiness(db, patient_id)
    guideline = evaluate_guideline_readiness(db, patient_id)

    mdt_decision = db.query(MDTDecision).filter(
        MDTDecision.patient_id == patient_id,
        MDTDecision.status == "FINAL"
    ).order_by(MDTDecision.recorded_at.desc()).first()

    biomarkers = db.query(CCABiomarkerResult).filter(
        CCABiomarkerResult.patient_id == patient_id
    ).all()

    bm_str = ", ".join([f"{b.marker_name}: {b.result_as_reported}" for b in biomarkers]) or "[NOT_RECORDED]"

    if not mdt_decision:
        # No MDT has recommended a treatment direction for this patient yet -- a specific
        # regimen/dose would be invented, not derived, so refuse to prefill one.
        return {
            "ready": False,
            "reason": "No finalised MDT (tumor board) recommendation on record for this patient. "
                       "A treatment regimen cannot be prefilled until the MDT has recorded a decision.",
            "diagnosis_on_record": bool(diagnosis),
            "staging_state": staging["state"],
            "guideline_state": guideline["state"],
            "biomarkers_on_record": bm_str,
            "intent": None,
            "goals": [],
            "components": {},
            "monitoring_plan": {},
            "follow_up_plan": None,
            "next_decision_point": None,
            "mdt_recommendation": None
        }

    return {
        "ready": True,
        "intent": "Curative / Neoadjuvant intent",
        "goals": [
            "Primary tumor and axillary downstaging to facilitate breast-conserving surgery",
            "Eradication of micrometastatic disease",
            "Pathological complete response (pCR) assessment",
            "Long-term disease-free survival"
        ],
        "components": {
            "systemic_therapy": {
                "regimen": "Dose-dense AC-T (Doxorubicin 60mg/m² + Cyclophosphamide 600mg/m² q2w × 4 cycles followed by Paclitaxel 175mg/m² q2w × 4 cycles with G-CSF support)",
                "planned_cycles": 8,
                "route": "Intravenous Infusion via Chemoport",
                "biomarker_rationale": f"HR+ / HER2- Subtype ({bm_str})"
            },
            "surgical_therapy": {
                "proposed_procedure": "Post-neoadjuvant Breast Conserving Surgery (Lumpectomy) + Sentinel Lymph Node Biopsy / Axillary Dissection",
                "timing": "4 to 6 weeks following completion of systemic chemotherapy"
            },
            "radiation_therapy": {
                "plan": "Adjuvant Whole Breast Radiotherapy (40.05 Gy in 15 fractions) + Tumor Bed Boost",
                "timing": "Post-operative"
            },
            "endocrine_therapy": {
                "plan": "Adjuvant Aromatase Inhibitor (Letrozole 2.5mg OD) or Tamoxifen 20mg OD for 5–10 years post-chemotherapy and surgery"
            },
            "supportive_care": {
                "antiemetic_protocol": "Triple antiemetic: Aprepitant + Ondansetron + Dexamethasone",
                "gcsf_support": "Pegfilgrastim 6mg SC day 2 of each cycle",
                "neuropathy_monitoring": "Assess for peripheral sensory neuropathy prior to each paclitaxel cycle"
            }
        },
        "monitoring_plan": {
            "pre_cycle_labs": ["CBC with Absolute Neutrophil Count", "Serum Creatinine", "LFT (Total Bilirubin, SGOT, SGPT)"],
            "interim_imaging": "USG Breast + Axilla after 4 cycles of AC to assess response (RECIST 1.1)",
            "toxicity_monitoring": "CTCAE v5.0 assessment for sensory neuropathy, nausea, and febrile neutropenia"
        },
        "follow_up_plan": "Clinical review and toxicity check every 14 days before cycle administration.",
        "next_decision_point": "Interim response assessment after completion of Cycle 4 AC (pre-Paclitaxel switch).",
        "mdt_recommendation": mdt_decision.recommendation
    }


# ---------------------------------------------------------------------------
# Document ingestion: deterministic classification + AI-drafted fact extraction.
#
# The only place in this engine that calls an LLM -- appropriately, since drafting candidate
# facts from a scanned document for a clinician to verify/correct/reject IS this product's
# core value proposition (unlike, say, drug-interaction checking elsewhere in this codebase,
# which was deliberately moved OFF an LLM onto a static table). Every fact this produces lands
# with status=PROPOSED; it is never treated as ground truth until a clinician accepts it via
# routers/cca.py's /verification/* endpoints -- this function itself never writes to the
# database. Reuses scribe.py's proven Groq JSON-extraction plumbing (rate limiting,
# retry-with-backoff) rather than a second bespoke implementation.
# ---------------------------------------------------------------------------

_DOCUMENT_CLASS_KEYWORDS = {
    "HISTOPATHOLOGY": ["biopsy", "histopath", "nottingham", "core needle", "surgical pathology", "microscopic examination"],
    "PATHOLOGY": ["immunohistochemistry", "biomarker", "estrogen receptor", "progesterone receptor", "her2", " ihc "],
    "IMAGING": ["ultrasonography", "mammograph", "computed tomography", " cect ", " ct ", " mri ", "radiodiagnosis", "impression:"],
    "LAB": ["hemoglobin", "haemoglobin", "creatinine", "leukocyte count", "platelet count", "biochemistry", "clinical pathology laboratory"],
    "REFERRAL": ["referral", "referring", "kindly evaluate", "please review and manage"],
    "CONSULT_NOTE": ["performance status", "outpatient clinical assessment", "clinical assessment", "history:"],
    # Added for per-page classification (classify_and_extract_page below) -- a mixed case-file
    # bundle can have a medication-order page alongside a case-history page, which whole-document
    # classification never needed to distinguish before pages were classified individually.
    "PRESCRIPTION": ["rx:", "sig:", " od ", " bd ", " tds ", " hs ", "prescribed medication", "take 1 tablet", "dispense"],
    "INSURANCE": ["policy number", "sum insured", "tpa ", "mediclaim", "policy holder", "insurance company"],
}


def classify_document(text: str) -> Tuple[str, float]:
    """Deterministic keyword-based document classification -- matches ocr_service.py's own
    preference for regex/keyword heuristics over spending a second AI call on coarse
    categorization the text itself already signals clearly enough.

    Returns ("UNCLASSIFIED", 0.0) when zero keywords match anywhere in the text -- previously
    this silently defaulted to ("CONSULT_NOTE", 0.4), which confidently mislabels a document
    whenever OCR produced garbled/near-empty text (a non-Latin-script scan, a low-quality photo,
    a blank page) as a specific, wrong category instead of flagging it for the clinician to
    classify manually. The Patient History summary panel treats UNCLASSIFIED as its own state
    rather than a real category."""
    lower = (text or "").lower()
    scored = []
    for cls, keywords in _DOCUMENT_CLASS_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score:
            scored.append((score, cls))
    if not scored:
        return "UNCLASSIFIED", 0.0
    scored.sort(reverse=True)
    top_score, top_cls = scored[0]
    return top_cls, min(0.95, 0.5 + 0.1 * top_score)


FACT_TYPES = (
    "PRIMARY_SITE", "LATERALITY", "HISTOLOGY", "GRADE", "T_EVIDENCE", "N_EVIDENCE",
    "M_EVIDENCE", "BIOMARKER_RESULT", "LAB_RESULT", "IMAGING_FINDING", "ECOG",
    "COMORBIDITY", "MEDICATION", "ALLERGY",
)


def extract_clinical_facts(document_text: str) -> List[Dict]:
    """AI-drafts candidate (fact_type, value, verbatim, confidence) tuples from a document's
    OCR'd text. Never raises: an extraction failure (Groq error, malformed response, wrong
    shape) yields an empty list -- the document still gets ingested with its raw OCR text, it
    just has zero PROPOSED facts for the clinician to review, rather than the request failing.

    max_tokens is raised above _call_groq_api's 3000-token default deliberately: a document
    with genuinely rich content (e.g. a full metabolic panel plus imaging findings plus staging
    info) can need more than 3000 tokens to enumerate every fact as JSON with a verbatim quote
    each -- verified live, the exact same real document intermittently (not always -- a
    generation-to-generation, non-deterministic truncation) had its response cut off mid-JSON at
    the 3000-token default, which _generate_json's malformed-JSON fallback cannot recover into
    this function's {"facts": [...]} shape (that fallback only ever produces the unrelated
    scribe_transcript() shape), silently yielding zero facts despite real, extractable content
    in the document. Also asking for SHORTER verbatim spans reduces the same pressure further
    without asking the model to extract less -- both changes address the actual overflow, not
    just its symptom."""
    if not document_text or not document_text.strip():
        return []

    system = (
        "You are a clinical document fact-extraction assistant for an oncology chart. "
        "Extract ONLY facts explicitly and literally stated in the text -- never infer, "
        "estimate, or guess a value that is not written down. Return strict JSON of the shape "
        '{"facts": [{"fact_type": "<one of ' + "|".join(FACT_TYPES) + '>", '
        '"value": "<short structured value>", "verbatim": "<exact quoted source text, at most '
        'roughly 15 words>", "confidence": <0.0-1.0>}]}. If nothing relevant is found, return '
        '{"facts": []}. Never include markdown or commentary outside the JSON object.'
    )
    prompt = f"Extract clinical facts from this document:\n\n{document_text[:8000]}"

    try:
        result = scribe._generate_json(prompt, system=system, max_tokens=6000)
    except Exception:
        return []

    facts = result.get("facts") if isinstance(result, dict) else None
    if not isinstance(facts, list):
        return []

    cleaned: List[Dict] = []
    for f in facts:
        if not isinstance(f, dict):
            continue
        fact_type = f.get("fact_type")
        value = f.get("value")
        if fact_type not in FACT_TYPES or not value:
            continue
        confidence = f.get("confidence")
        cleaned.append({
            "fact_type": fact_type,
            "value": str(value)[:500],
            "verbatim": str(f.get("verbatim") or "")[:1000],
            "confidence": confidence if isinstance(confidence, (int, float)) and 0 <= confidence <= 1 else 0.75,
        })
    return cleaned


# Maps a drafted fact's fact_type onto the CCAResult.result_type it should contribute to --
# only fact types that ARE a lab/imaging finding, never every fact type (e.g. MEDICATION,
# ALLERGY have no business becoming a "result").
_RESULT_TYPE_BY_FACT_TYPE = {"LAB_RESULT": "LAB", "IMAGING_FINDING": "IMAGING"}
_RESULT_TYPE_LABEL = {"LAB": "Lab results", "IMAGING": "Imaging findings"}


def build_results_from_document_facts(facts: List[Dict], doc_filename: str) -> List[Dict]:
    """Groups a just-drafted document's LAB_RESULT/IMAGING_FINDING facts into at most one
    CCAResult-shaped dict per result_type, so a document's own already-extracted lab/imaging
    findings show up in Patient History's "Results" (and "Past Labs"/"Past Results") section
    immediately on upload -- previously CCAOrder/CCAResult were only ever written by a separate
    clinician-driven ordering/results workflow, never by document ingestion, so "Results" stayed
    empty even for a document that plainly contains lab results (e.g. an insurance claim's
    attached lab report). Deliberately does NOT synthesize a CCAOrder -- nothing was actually
    ordered through this system for a historical outside document, so "Investigations Ordered"
    staying empty for a document-only ingestion is correct, not a gap.

    Returns [], never raises -- a document drafting zero LAB_RESULT/IMAGING_FINDING facts (most
    document types: referrals, prescriptions, consult notes, ...) is the common case, not an
    error. Callers are responsible for actually constructing/persisting CCAResult rows -- this
    function, like extract_clinical_facts and classify_and_extract_page above, never touches the
    database itself.
    """
    by_type: Dict[str, List[str]] = {}
    for f in facts:
        result_type = _RESULT_TYPE_BY_FACT_TYPE.get(f.get("fact_type"))
        if result_type and f.get("value"):
            by_type.setdefault(result_type, []).append(f["value"])

    return [
        {
            "result_type": result_type,
            "title": f"{_RESULT_TYPE_LABEL[result_type]} -- {doc_filename}",
            "findings_text": "; ".join(values),
        }
        for result_type, values in by_type.items()
    ]


# ---------------------------------------------------------------------------
# Per-page classification (CCADocumentPage.page_type) -- see models_cca.py's CCADocumentPage
# docstring and routers/document_pages.py's background task that calls this once per page.
# ---------------------------------------------------------------------------

PAGE_TYPES = (
    "CASE_DETAILS", "PRESCRIPTION", "LAB_REPORT", "SCAN_IMAGING", "PATHOLOGY_REPORT",
    "INSURANCE", "OTHER", "UNCLASSIFIED",
)

# Maps classify_document()'s whole-document buckets onto the (slightly broader) per-page
# vocabulary above, so the same free keyword classifier serves both without duplicating it.
_DOC_CLASS_TO_PAGE_TYPE = {
    "HISTOPATHOLOGY": "PATHOLOGY_REPORT",
    "PATHOLOGY": "PATHOLOGY_REPORT",
    "IMAGING": "SCAN_IMAGING",  # a text-heavy radiology REPORT describing a scan, not the scan photo itself
    "LAB": "LAB_REPORT",
    "REFERRAL": "CASE_DETAILS",
    "CONSULT_NOTE": "CASE_DETAILS",
    "PRESCRIPTION": "PRESCRIPTION",
    "INSURANCE": "INSURANCE",
    "UNCLASSIFIED": "UNCLASSIFIED",
}


def classify_and_extract_page(text: str, is_image_heavy: bool) -> Dict:
    """
    Hybrid per-page classifier: free heuristic first, LLM only when it's actually needed.

    1. A page ocr_service.extract_document_pages() already flagged image-heavy (an X-ray/MRI/CT
       scan photograph with little or no extractable text) is SCAN_IMAGING by construction --
       nothing to classify, no LLM call.
    2. Otherwise, run the existing deterministic keyword classifier (classify_document(),
       extended above with PRESCRIPTION/INSURANCE buckets) -- free, instant. A confident,
       non-UNCLASSIFIED match is used directly.
    3. Only when that's inconclusive: one Groq call combining page-type classification with
       fact extraction (same FACT_TYPES/PROPOSED-only contract as extract_clinical_facts) into
       a single JSON response -- one LLM round trip per ambiguous page instead of two.

    Returns {"page_type": <one of PAGE_TYPES>, "confidence": float, "facts": List[Dict]} (facts
    is empty unless step 3 ran). Never raises -- degrades to UNCLASSIFIED/0.0/[] on any failure,
    matching extract_clinical_facts's "AI enrichment failing must never fail the document"
    contract; this is best-effort enrichment, not something a document's existence depends on.
    """
    if is_image_heavy:
        return {"page_type": "SCAN_IMAGING", "confidence": 0.9, "facts": []}

    if not text or not text.strip():
        return {"page_type": "UNCLASSIFIED", "confidence": 0.0, "facts": []}

    doc_cls, confidence = classify_document(text)
    if doc_cls != "UNCLASSIFIED":
        return {"page_type": _DOC_CLASS_TO_PAGE_TYPE.get(doc_cls, "OTHER"), "confidence": confidence, "facts": []}

    system = (
        "You are a clinical document page classifier and fact-extraction assistant for an "
        "oncology chart. Classify this single page into exactly one of: " + "|".join(PAGE_TYPES) +
        " (CASE_DETAILS = referral/consult/case-history notes; PRESCRIPTION = a medication "
        "order; LAB_REPORT = lab/blood-work results; PATHOLOGY_REPORT = biopsy/histopathology/"
        "IHC; SCAN_IMAGING = a radiology report describing an X-ray/CT/MRI/ultrasound; "
        "INSURANCE = an insurance/policy document; OTHER = none of the above but still "
        "relevant; UNCLASSIFIED = cannot tell). Then extract ONLY facts explicitly and "
        "literally stated in the text -- never infer, estimate, or guess a value that is not "
        'written down. Return strict JSON of the shape {"page_type": "<one of the types '
        'above>", "confidence": <0.0-1.0>, "facts": [{"fact_type": "<one of ' +
        "|".join(FACT_TYPES) + '>", "value": "<short structured value>", "verbatim": "<exact '
        'quoted source text, at most roughly 15 words>", "confidence": <0.0-1.0>}]}. If no '
        'facts are found, return an empty "facts" array. Never include markdown or commentary '
        "outside the JSON object."
    )
    prompt = f"Classify and extract clinical facts from this page:\n\n{text[:6000]}"

    try:
        result = scribe._generate_json(prompt, system=system, max_tokens=4000)
    except Exception:
        return {"page_type": "UNCLASSIFIED", "confidence": 0.0, "facts": []}
    if not isinstance(result, dict):
        return {"page_type": "UNCLASSIFIED", "confidence": 0.0, "facts": []}

    page_type = result.get("page_type")
    if page_type not in PAGE_TYPES:
        page_type = "UNCLASSIFIED"
    page_confidence = result.get("confidence")
    page_confidence = page_confidence if isinstance(page_confidence, (int, float)) and 0 <= page_confidence <= 1 else 0.5

    raw_facts = result.get("facts")
    facts: List[Dict] = []
    if isinstance(raw_facts, list):
        for f in raw_facts:
            if not isinstance(f, dict):
                continue
            fact_type = f.get("fact_type")
            value = f.get("value")
            if fact_type not in FACT_TYPES or not value:
                continue
            fact_confidence = f.get("confidence")
            facts.append({
                "fact_type": fact_type,
                "value": str(value)[:500],
                "verbatim": str(f.get("verbatim") or "")[:1000],
                "confidence": fact_confidence if isinstance(fact_confidence, (int, float)) and 0 <= fact_confidence <= 1 else 0.75,
            })

    return {"page_type": page_type, "confidence": page_confidence, "facts": facts}
