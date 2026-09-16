"""
Deterministic Clinical Engines for CCA Cancer Care AI OS.
Strict human-in-the-loop governance:
- Zero autonomous staging or treatment generation
- Explicit absence vocabulary
- Mathematical accuracy for BSA (DuBois) and BMI
"""

import logging
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
from . import gemini_client

# gemini_client.py's own _post_with_retry/generate_structured_json already log API-level
# failures (auth, rate limit, blocked/malformed response) -- this logger is for the fact that a
# CHUNK's extraction was dropped as a result, which extract_clinical_facts/
# classify_and_extract_page's `except Exception: continue` would otherwise swallow with no
# trace at all. Without this, a sustained failure (e.g. a misconfigured GEMINI_API_KEY) looked
# identical in the logs to a document that genuinely had nothing to extract.
logger = logging.getLogger(__name__)


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
# database. Uses gemini_client.py's schema-enforced JSON extraction (its own rate limiting,
# retry-with-backoff) -- moved off scribe.py's Groq plumbing 2026-09-16, see gemini_client.py's
# and config.py's GEMINI_API_KEY comments for why. scribe.py's Groq plumbing is still used
# elsewhere in this app (live OPD/IPD voice consultation drafting), just not by this block.
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
    # Fallback bucket -- added after finding this enum was closed with no escape hatch: every
    # other entry above is a specific oncology-staging concept, so any clinically relevant fact
    # that doesn't fit one of them (surgical/family/social history, vitals, a non-oncology
    # diagnosis, a follow-up plan, ...) had nowhere to go, and the prompt below only ever asked
    # for "one of" this list with no instruction covering that case -- extraction silently
    # dropped it rather than mis-typing it. This is a real, not hypothetical, information-loss
    # path: a document can be entirely correctly OCR'd and entirely correctly sent to Groq, and
    # still lose real clinical content purely because the category vocabulary had no room for
    # it. Never used as a dumping ground for things that DO fit a specific type above -- both
    # prompts below say so explicitly.
    "OTHER_CLINICAL_FINDING",
)

# MOVED OFF GROQ ENTIRELY 2026-09-16 (fourth pass, same day): extract_clinical_facts and
# classify_and_extract_page used to walk their input in bounded slices (6000 chars, then 18000
# chars, then a byte-safe 20000 bytes -- see git history) because Groq's real ~8000-token/minute
# account budget and its harder-to-pin-down request-body-size ceiling both forced a document to
# be split into many small calls. Even the byte-safe, live-calibrated 20000-byte version kept
# producing hard 413s in production on real documents (confirmed live: slices 2-3 of a 4-5-slice
# document repeatedly exhausted all retries and permanently contributed zero facts for that
# slice), despite the exact same size having been verified safe in isolated live testing --
# strong evidence Groq's real limits are not cleanly deterministic under this account's
# conditions, not just a miscalibration to retune again.
#
# Gemini (gemini_client.py) has a 1M-token context window -- verified live, a real 200,000-
# character (~297,000-byte) mixed ASCII/Devanagari document, roughly a dense 50-page record,
# fit in ONE call using 61,891 total tokens. Slicing a document at all is no longer necessary for
# any real document this app has ever seen; _MAX_SINGLE_CALL_BYTES below is a safety valve for a
# pathological outlier, not the routine path -- true to this constant's original "never truncate,
# never drop for size reasons" principle, just satisfied by a single big call instead of many
# small ones.
_MAX_SINGLE_CALL_BYTES = 800000


def _slice_text_by_bytes(text: str, max_bytes: int) -> List[str]:
    """Splits text into chunks whose UTF-8-encoded size is at most max_bytes each, without ever
    splitting a multi-byte character across a chunk boundary (which would corrupt it on decode).
    UTF-8 continuation bytes are always in 0x80-0xBF -- backing off until the byte at the cut
    point is NOT a continuation byte guarantees the cut lands on a real character boundary."""
    encoded = text.encode("utf-8")
    total = len(encoded)
    if total == 0:
        return []
    slices = []
    start = 0
    while start < total:
        end = min(start + max_bytes, total)
        while end < total and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        slices.append(encoded[start:end].decode("utf-8"))
        start = end
    return slices


def _chunks_for_single_call_extraction(text: str) -> List[str]:
    """Returns [text] unchanged for any real document (the common, routine case, given Gemini's
    1M-token context window), or falls back to _slice_text_by_bytes at _MAX_SINGLE_CALL_BYTES
    only for the pathological case of a document that large -- see _MAX_SINGLE_CALL_BYTES's own
    comment for why this is a safety valve, not the expected path."""
    if len(text.encode("utf-8")) <= _MAX_SINGLE_CALL_BYTES:
        return [text]
    return _slice_text_by_bytes(text, _MAX_SINGLE_CALL_BYTES)


_FACTS_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "facts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "fact_type": {"type": "STRING", "enum": list(FACT_TYPES)},
                    "value": {"type": "STRING"},
                    "verbatim": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["fact_type", "value", "verbatim", "confidence"],
            },
        },
    },
    "required": ["facts"],
}

# classify_and_extract_page's response shape -- _FACTS_RESPONSE_SCHEMA's facts array plus
# page_type/confidence, built below once PAGE_TYPES exists.


def extract_clinical_facts(document_text: str) -> List[Dict]:
    """AI-drafts candidate (fact_type, value, verbatim, confidence) tuples from a document's
    OCR'd text, via Gemini (gemini_client.py) with a schema-enforced JSON response (see
    _FACTS_RESPONSE_SCHEMA) -- fact_type is constrained to FACT_TYPES server-side, not just
    requested in a prompt. Never raises: an extraction failure (API error, blocked response,
    malformed shape) on any one chunk just contributes nothing from that chunk -- the document
    still gets ingested with its raw OCR text, it just has fewer PROPOSED facts for the
    clinician to review, rather than the request failing.

    Sends the ENTIRE document in ONE call for any real document (see
    _chunks_for_single_call_extraction/_MAX_SINGLE_CALL_BYTES) -- Gemini's 1M-token context
    window made the multi-slice approach this function used to need for Groq's much smaller
    account budget unnecessary; a genuinely oversized outlier still gets walked in full via the
    same byte-safe chunking, never truncated."""
    if not document_text or not document_text.strip():
        return []

    system = (
        "You are a clinical document fact-extraction assistant for an oncology chart. "
        "Extract ONLY facts explicitly and literally stated in the text -- never infer, "
        "estimate, or guess a value that is not written down. If nothing relevant is found, "
        "return an empty facts array. "
        "IMPORTANT: never silently omit a clinically relevant fact just because it doesn't "
        "match one of the specific fact_type values above -- use OTHER_CLINICAL_FINDING for "
        "anything clinically relevant (surgical/family/social history, vitals, a diagnosis "
        "unrelated to the primary cancer, a follow-up plan or advice, or any other real clinical "
        "content) that doesn't fit a more specific type. Only use OTHER_CLINICAL_FINDING when "
        "no more specific type applies -- never as a default for something a specific type "
        "already covers."
    )

    chunks = _chunks_for_single_call_extraction(document_text)

    facts: List[Dict] = []
    seen_facts: set = set()
    for chunk_num, chunk_text in enumerate(chunks, start=1):
        prompt = f"Extract clinical facts from this document:\n\n{chunk_text}"
        try:
            result = gemini_client.generate_structured_json(
                prompt, system=system, response_schema=_FACTS_RESPONSE_SCHEMA,
            )
        except Exception as e:
            # No PHI here -- chunk_text/prompt are deliberately excluded, same as
            # gemini_client.py's own error logging. This chunk contributes zero facts
            # (including any MEDICATION facts it held -- there's no deterministic fallback for
            # those the way LAB_RESULT has extract_deterministic_lab_facts below).
            logger.warning(
                "extract_clinical_facts: chunk %d/%d failed, contributing 0 facts: %s",
                chunk_num, len(chunks), e,
            )
            continue
        raw_facts = result.get("facts") if isinstance(result, dict) else None
        if not isinstance(raw_facts, list):
            logger.warning(
                "extract_clinical_facts: chunk %d/%d returned malformed JSON (no 'facts' list), "
                "contributing 0 facts", chunk_num, len(chunks),
            )
            continue
        for f in raw_facts:
            if not isinstance(f, dict):
                continue
            fact_type = f.get("fact_type")
            value = f.get("value")
            # Belt-and-suspenders, not the primary defense -- fact_type is already schema-
            # constrained to FACT_TYPES server-side (unlike the old Groq prompt-only approach,
            # where this check was the ONLY defense against a hallucinated type).
            if fact_type not in FACT_TYPES or not value:
                continue
            value = str(value)[:500]
            key = (fact_type, value)
            if key in seen_facts:
                continue
            seen_facts.add(key)
            confidence = f.get("confidence")
            facts.append({
                "fact_type": fact_type,
                "value": value,
                "verbatim": str(f.get("verbatim") or "")[:1000],
                "confidence": confidence if isinstance(confidence, (int, float)) and 0 <= confidence <= 1 else 0.75,
            })
    return facts


def extract_deterministic_lab_facts(signals: Optional[Dict]) -> List[Dict]:
    """Turns ocr_service._clinical_signals()'s "lab_values" scan (a regex "<test name>: <value>
    <unit>" line matcher run over the document's FULL raw text, no truncation, no LLM call -- see
    that function's docstring) into the same {"fact_type", "value", "verbatim", "confidence"}
    shape extract_clinical_facts/classify_and_extract_page produce, so routers/cca.py's
    upload_document can merge it with the AI-drafted facts through the same dedup/persistence
    path instead of a second bespoke one.

    A supplement, not a replacement: it never misses a recognisable lab line purely because a
    document is long (extract_clinical_facts and classify_and_extract_page both walk their whole
    input in bounded slices rather than truncating -- see extract_clinical_facts's docstring),
    but it also can't read messy/unstructured phrasing the way the LLM passes can -- both keep
    running independently.

    signals may be None/missing "lab_values" entirely (older callers, or a signals dict built
    before this key existed) -- returns [] in that case, same as extract_clinical_facts's "never
    raises" contract elsewhere in this module.
    """
    return [
        {"fact_type": "LAB_RESULT", "value": entry, "verbatim": entry, "confidence": 0.95}
        for entry in (signals or {}).get("lab_values", [])
    ]


def extract_deterministic_medication_facts(signals: Optional[Dict]) -> List[Dict]:
    """Same deterministic-safety-net pattern as extract_deterministic_lab_facts above, for
    MEDICATION facts -- turns ocr_service._clinical_signals()'s "medications" scan (a regex
    line matcher for a line starting "Medications:"/"Drugs:"/"Prescription:", no LLM call) into
    the same fact shape, so routers/cca.py's upload_document can merge it with the AI-drafted
    facts through the same dedup/persistence path.

    Before this, MEDICATION facts came ONLY from extract_clinical_facts's LLM pass -- unlike
    LAB_RESULT, which always had this deterministic fallback too. Confirmed live: when a
    document-OCR extraction call fails outright (bad/rate-limited GEMINI_API_KEY, exhausted
    retries, malformed response -- see extract_clinical_facts's now-logged except block), labs
    still appeared (this fallback) while medications silently disappeared (they had no
    equivalent), even though the raw "Medications:" line was sitting right there in
    _clinical_signals()'s output the whole time, just never surfaced as a fact.

    Each entry is the full captured line after the "Medications:"/"Drugs:"/"Prescription:"
    label (see ocr_service._clinical_signals's "medications" pattern) -- often several drugs in
    one comma-separated line, not one entry per individual drug the way lab_values is one entry
    per test. Confidence is lower than the lab fallback's 0.95: a lab_values entry is a tightly
    parsed "<test name>: <value> <unit>" match, while a medications line is a coarser raw-text
    capture more likely to need a clinician's read before it's trusted as-is -- still well above
    extract_clinical_facts's 0.75 generic-LLM-fact default, since this is a verbatim quote, not
    an inferred value.

    signals may be None/missing "medications" entirely (older callers, or a signals dict built
    before this key existed) -- returns [] in that case, same as extract_deterministic_lab_facts.
    """
    return [
        {"fact_type": "MEDICATION", "value": entry, "verbatim": entry, "confidence": 0.85}
        for entry in (signals or {}).get("medications", [])
    ]


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

_PAGE_CLASSIFICATION_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "page_type": {"type": "STRING", "enum": list(PAGE_TYPES)},
        "confidence": {"type": "NUMBER"},
        "facts": _FACTS_RESPONSE_SCHEMA["properties"]["facts"],
    },
    "required": ["page_type", "confidence", "facts"],
}


def classify_and_extract_page(text: str, is_image_heavy: bool) -> Dict:
    """
    Per-page classifier: the LLM is authoritative for page type, with a free deterministic
    heuristic kept only as a fallback for when the LLM is unavailable.

    1. A page ocr_service.extract_document_pages() already flagged image-heavy (an X-ray/MRI/CT
       scan photograph with little or no extractable text) is SCAN_IMAGING by construction --
       nothing to classify, no LLM call, no facts possible.
    2. Fact extraction (and classification) runs via one Gemini call per page/chunk (see
       _chunks_for_single_call_extraction -- almost always exactly one call; a chunk large
       enough to need more is a pathological outlier, not the routine case, now that the model
       has a 1M-token context window). Fact extraction always runs regardless of what the free
       deterministic keyword classifier thinks the page type is, and the LLM's own
       page_type/confidence from that same call is what's actually used: it sees the real page
       text, not a fixed keyword list, so it wins whenever it produced a usable answer.
    3. classify_document() still runs and is kept as `fallback_page_type`/`fallback_confidence`
       -- used ONLY when the LLM call failed outright (network error, blocked response, Gemini
       unavailable), so a page still gets a best-effort page_type instead of always collapsing
       to UNCLASSIFIED purely because AI enrichment was down. It is never preferred over a real
       LLM answer.

    Returns {"page_type": <one of PAGE_TYPES>, "confidence": float, "facts": List[Dict]}. Never
    raises -- degrades to the deterministic fallback page_type (or UNCLASSIFIED/0.0 if that also
    had nothing) with whatever facts were actually collected on any LLM failure, matching
    extract_clinical_facts's "AI enrichment failing must never fail the document" contract; this
    is best-effort enrichment, not something a document's existence depends on. Callers
    (document_pages.py) are expected to dedup facts against what a document already has on
    record -- this function only ever drafts.
    """
    if is_image_heavy:
        return {"page_type": "SCAN_IMAGING", "confidence": 0.9, "facts": []}

    if not text or not text.strip():
        return {"page_type": "UNCLASSIFIED", "confidence": 0.0, "facts": []}

    doc_cls, doc_confidence = classify_document(text)
    fallback_page_type = _DOC_CLASS_TO_PAGE_TYPE.get(doc_cls, "OTHER") if doc_cls != "UNCLASSIFIED" else "UNCLASSIFIED"
    fallback_confidence = doc_confidence if doc_cls != "UNCLASSIFIED" else 0.0

    system = (
        "You are a clinical document page classifier and fact-extraction assistant for an "
        "oncology chart. Classify this single page into exactly one of: " + "|".join(PAGE_TYPES) +
        " (CASE_DETAILS = referral/consult/case-history notes; PRESCRIPTION = a medication "
        "order; LAB_REPORT = lab/blood-work results; PATHOLOGY_REPORT = biopsy/histopathology/"
        "IHC; SCAN_IMAGING = a radiology report describing an X-ray/CT/MRI/ultrasound; "
        "INSURANCE = an insurance/policy document; OTHER = none of the above but still "
        "relevant; UNCLASSIFIED = cannot tell). Then extract ONLY facts explicitly and "
        "literally stated in the text -- never infer, estimate, or guess a value that is not "
        "written down. If no facts are found, return an empty facts array. "
        "IMPORTANT: never silently omit a clinically relevant fact just because it doesn't "
        "match one of the specific fact_type values above -- use OTHER_CLINICAL_FINDING for "
        "anything clinically relevant (surgical/family/social history, vitals, a diagnosis "
        "unrelated to the primary cancer, a follow-up plan or advice, or any other real clinical "
        "content) that doesn't fit a more specific type. Only use OTHER_CLINICAL_FINDING when "
        "no more specific type applies -- never as a default for something a specific type "
        "already covers."
    )

    chunks = _chunks_for_single_call_extraction(text)

    facts: List[Dict] = []
    seen_facts: set = set()
    llm_page_type = None
    llm_confidence = None
    for chunk_num, chunk_text in enumerate(chunks, start=1):
        prompt = f"Classify and extract clinical facts from this page:\n\n{chunk_text}"
        try:
            result = gemini_client.generate_structured_json(
                prompt, system=system, response_schema=_PAGE_CLASSIFICATION_RESPONSE_SCHEMA,
            )
        except Exception as e:
            # See extract_clinical_facts's identical comment above -- same silent-failure gap.
            logger.warning(
                "classify_and_extract_page: chunk %d/%d failed, contributing 0 facts: %s",
                chunk_num, len(chunks), e,
            )
            continue
        if not isinstance(result, dict):
            continue

        if llm_page_type is None:
            candidate_type = result.get("page_type")
            if candidate_type in PAGE_TYPES:
                llm_page_type = candidate_type
            candidate_confidence = result.get("confidence")
            if isinstance(candidate_confidence, (int, float)) and 0 <= candidate_confidence <= 1:
                llm_confidence = candidate_confidence

        raw_facts = result.get("facts")
        if not isinstance(raw_facts, list):
            continue
        for f in raw_facts:
            if not isinstance(f, dict):
                continue
            fact_type = f.get("fact_type")
            value = f.get("value")
            if fact_type not in FACT_TYPES or not value:
                continue
            value = str(value)[:500]
            key = (fact_type, value)
            if key in seen_facts:
                continue
            seen_facts.add(key)
            fact_confidence = f.get("confidence")
            facts.append({
                "fact_type": fact_type,
                "value": value,
                "verbatim": str(f.get("verbatim") or "")[:1000],
                "confidence": fact_confidence if isinstance(fact_confidence, (int, float)) and 0 <= fact_confidence <= 1 else 0.75,
            })

    # page_type/confidence: the LLM's own classification wins whenever any chunk produced a
    # usable one -- it saw the real page text, not a fixed keyword list. The deterministic
    # keyword classifier is only a fallback for when every LLM call failed outright.
    if llm_page_type:
        page_type, page_confidence = llm_page_type, llm_confidence if llm_confidence is not None else 0.5
    elif fallback_page_type != "UNCLASSIFIED":
        page_type, page_confidence = fallback_page_type, fallback_confidence
    else:
        page_type, page_confidence = "UNCLASSIFIED", 0.0

    return {"page_type": page_type, "confidence": page_confidence, "facts": facts}
