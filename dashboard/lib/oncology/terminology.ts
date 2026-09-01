/**
 * Controlled clinical terminology (PDF item 21).
 *
 * Every clinical dropdown in the oncology workflow reads from here — nobody hand-types
 * a new option into a screen's JSX. Each list carries a code system reference where a
 * real one applies, and an `editable` flag stating who owns changing it.
 *
 * Content status: this is a *reference starter set*, not a licensed terminology feed.
 * CTCAE term names and the five generic grade definitions are the NCI's own public,
 * standard wording (structural vocabulary — not a dosing rule). The site/histology list
 * is illustrative only. Both are marked `CLINICAL VALIDATION REQUIRED` and are the exact
 * kind of list PDF item 36 wants reviewed with a Medical Oncologist, Radiation Oncologist,
 * Surgical Oncologist, Oncology Pharmacist and Oncology Nurse before being trusted as the
 * production formulary/vocabulary. Swap or extend the arrays below in one place; every
 * screen in the app already reads through them.
 */

import type { CodeSystem, TreatmentIntent } from './types'

export type TerminologyEntry = {
  code: string
  display: string
  system: CodeSystem
  /** Who owns editing this entry outside engineering. */
  clinicalOwner: string
  editable: boolean
}

// ───────────────────────── Treatment intent ─────────────────────────

export const TREATMENT_INTENTS: { value: TreatmentIntent; display: string }[] = [
  { value: 'curative', display: 'Curative' },
  { value: 'palliative', display: 'Palliative' },
  { value: 'neoadjuvant', display: 'Neoadjuvant' },
  { value: 'adjuvant', display: 'Adjuvant' },
  { value: 'definitive', display: 'Definitive' },
  { value: 'diagnostic', display: 'Diagnostic' },
  { value: 'salvage', display: 'Salvage' },
]

// ───────────────────────── Routes of administration ─────────────────────────

export const ADMINISTRATION_ROUTES: TerminologyEntry[] = [
  { code: '47625008', display: 'Intravenous', system: 'SNOMED-CT', clinicalOwner: 'Oncology Pharmacist', editable: true },
  { code: '26643006', display: 'Oral', system: 'SNOMED-CT', clinicalOwner: 'Oncology Pharmacist', editable: true },
  { code: '78421000', display: 'Intramuscular', system: 'SNOMED-CT', clinicalOwner: 'Oncology Pharmacist', editable: true },
  { code: '34206005', display: 'Subcutaneous', system: 'SNOMED-CT', clinicalOwner: 'Oncology Pharmacist', editable: true },
  { code: '445775006', display: 'Intrathecal', system: 'SNOMED-CT', clinicalOwner: 'Oncology Pharmacist', editable: true },
  { code: '447122006', display: 'Intraperitoneal', system: 'SNOMED-CT', clinicalOwner: 'Oncology Pharmacist', editable: true },
  { code: '54471007', display: 'Topical', system: 'SNOMED-CT', clinicalOwner: 'Oncology Pharmacist', editable: true },
]

// ───────────────────────── Dose basis (descriptive — not a calculation) ─────────────────────────

export const DOSE_BASIS_DESCRIPTORS: TerminologyEntry[] = [
  { code: 'fixed', display: 'Fixed dose', system: 'local', clinicalOwner: 'Oncology Pharmacist', editable: true },
  { code: 'mg-per-kg', display: 'mg/kg', system: 'local', clinicalOwner: 'Oncology Pharmacist', editable: true },
  { code: 'mg-per-m2', display: 'mg/m²', system: 'local', clinicalOwner: 'Oncology Pharmacist', editable: true },
  { code: 'auc', display: 'AUC', system: 'local', clinicalOwner: 'Oncology Pharmacist', editable: true },
  { code: 'units', display: 'Units', system: 'local', clinicalOwner: 'Oncology Pharmacist', editable: true },
]

// ───────────────────────── Dose modification reasons ─────────────────────────

export const DOSE_MODIFICATION_TYPES: { value: string; display: string }[] = [
  { value: 'dose_reduction', display: 'Dose reduction' },
  { value: 'dose_escalation', display: 'Dose escalation (where protocol permits)' },
  { value: 'treatment_delay', display: 'Treatment delay' },
  { value: 'drug_omission', display: 'Drug omission' },
  { value: 'drug_substitution', display: 'Drug substitution' },
  { value: 'cycle_postponement', display: 'Cycle postponement' },
  { value: 'treatment_discontinuation', display: 'Treatment discontinuation' },
]

// ───────────────────────── CTCAE (item 16) ─────────────────────────
// Generic grade definitions are NCI CTCAE v5.0's own term-independent wording — structural
// severity vocabulary, not a numeric lab threshold or dosing rule.

export const CTCAE_GENERIC_GRADES: { grade: 1 | 2 | 3 | 4 | 5; label: string; definition: string }[] = [
  { grade: 1, label: 'Grade 1 — Mild', definition: 'Asymptomatic or mild symptoms; clinical or diagnostic observations only; intervention not indicated.' },
  { grade: 2, label: 'Grade 2 — Moderate', definition: 'Minimal, local or noninvasive intervention indicated; limiting age-appropriate instrumental activities of daily living.' },
  { grade: 3, label: 'Grade 3 — Severe', definition: 'Severe or medically significant but not immediately life-threatening; hospitalization or prolongation of hospitalization indicated; disabling; limiting self-care activities of daily living.' },
  { grade: 4, label: 'Grade 4 — Life-threatening', definition: 'Life-threatening consequences; urgent intervention indicated.' },
  { grade: 5, label: 'Grade 5 — Death', definition: 'Death related to the adverse event.' },
]

export const CTCAE_RELATIONSHIP_TO_THERAPY: { value: string; display: string }[] = [
  { value: 'unrelated', display: 'Unrelated' },
  { value: 'unlikely', display: 'Unlikely related' },
  { value: 'possible', display: 'Possibly related' },
  { value: 'probable', display: 'Probably related' },
  { value: 'definite', display: 'Definitely related' },
]

/** Reference starter set spanning the System Organ Classes most relevant to systemic therapy. Extend via clinical governance, not by hand-editing screens. */
export const CTCAE_TERMS: TerminologyEntry[] = [
  { code: 'CTCAE-10013879', display: 'Neutrophil count decreased', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10005329', display: 'Platelet count decreased', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10018910', display: 'Anaemia', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10016256', display: 'Febrile neutropenia', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10047700', display: 'Nausea', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10047699', display: 'Vomiting', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10012735', display: 'Diarrhoea', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10009887', display: 'Constipation', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10000060', display: 'Abdominal pain', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10016256B', display: 'Mucositis oral', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10016256C', display: 'Fatigue', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10037660', display: 'Pyrexia', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10018687', display: 'Peripheral sensory neuropathy', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10039897', display: 'Alopecia', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10037844', display: 'Rash maculo-papular', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10015866', display: 'Extravasation', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10020751', display: 'Hypersensitivity / infusion reaction', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10038693', display: 'Renal function decreased', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10005329B', display: 'Alanine aminotransferase increased', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'CTCAE-10021881', display: 'Infection — pathogen unspecified', system: 'CTCAE-5', clinicalOwner: 'Medical Oncologist', editable: false },
]

// ───────────────────────── RECIST (item 17) ─────────────────────────
// Standard RECIST 1.1 response-category definitions — tumour-measurement classification,
// unrelated to drug dosing.

export const RECIST_CATEGORIES: { value: 'CR' | 'PR' | 'SD' | 'PD' | 'not_evaluable'; label: string; definition: string }[] = [
  { value: 'CR', label: 'Complete Response (CR)', definition: 'Disappearance of all target and non-target lesions.' },
  { value: 'PR', label: 'Partial Response (PR)', definition: 'At least a 30% decrease in the sum of diameters of target lesions, taking the baseline sum as reference.' },
  { value: 'SD', label: 'Stable Disease (SD)', definition: 'Neither sufficient shrinkage to qualify for PR nor sufficient increase to qualify for PD.' },
  { value: 'PD', label: 'Progressive Disease (PD)', definition: 'At least a 20% increase in the sum of diameters of target lesions, or the appearance of new lesions.' },
  { value: 'not_evaluable', label: 'Not evaluable', definition: 'Response cannot be assessed from the available evidence.' },
]

// ───────────────────────── Cancer site / histology — illustrative starter set ─────────────────────────

export const CANCER_SITES: TerminologyEntry[] = [
  { code: 'C50', display: 'Breast', system: 'ICD-O-3', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'C34', display: 'Lung and bronchus', system: 'ICD-O-3', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'C18', display: 'Colon', system: 'ICD-O-3', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'C61', display: 'Prostate gland', system: 'ICD-O-3', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'C16', display: 'Stomach', system: 'ICD-O-3', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'C53', display: 'Cervix uteri', system: 'ICD-O-3', clinicalOwner: 'Medical Oncologist', editable: false },
  { code: 'C81-C85', display: 'Lymphoma', system: 'ICD-O-3', clinicalOwner: 'Medical Oncologist', editable: false },
]

// ───────────────────────── Cumulative status vocabulary display labels (item 15) ─────────────────────────

export const TREATMENT_STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  proposed: 'Proposed',
  mdt_recommended: 'MDT Recommended',
  clinician_approved: 'Clinician Approved',
  ordered: 'Ordered',
  verification_pending: 'Verification Pending',
  verified: 'Verified',
  preparation_pending: 'Preparation Pending',
  prepared: 'Prepared',
  dispensed: 'Dispensed',
  ready_for_administration: 'Ready for Administration',
  in_progress: 'In Progress',
  administered: 'Administered',
  held: 'Held',
  delayed: 'Delayed',
  cancelled: 'Cancelled',
  completed: 'Completed',
}

export const RT_SUB_STATUS_LABELS: Record<string, string> = {
  prescribed: 'Prescribed',
  simulation_pending: 'Simulation Pending',
  simulation_complete: 'Simulation Complete',
  contouring: 'Contouring',
  planning: 'Planning',
  physics_qa: 'Physics QA',
  physician_approved: 'Physician Approved',
  treatment_ready: 'Treatment Ready',
  on_treatment: 'On Treatment',
  interrupted: 'Interrupted',
  completed: 'Completed',
}

export const SURGICAL_SUB_STATUS_LABELS: Record<string, string> = {
  recommended: 'Recommended',
  surgeon_reviewed: 'Surgeon Reviewed',
  planned: 'Planned',
  pre_op_ready: 'Pre-op Ready',
  scheduled: 'Scheduled',
  performed: 'Performed',
  post_op: 'Post-op',
  histopathology_available: 'Histopathology Available',
}
