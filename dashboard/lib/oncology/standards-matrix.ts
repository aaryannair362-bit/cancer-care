/**
 * Standards & Interoperability Matrix (PDF item 40) — kept as real data, not a slide,
 * so it can back an actual settings screen and stay honest as the system changes.
 *
 * `status` is one of:
 *  - 'not_started'   nothing in the codebase addresses this standard
 *  - 'structural'     the internal data model/UI is shaped for it, nothing external is connected
 *  - 'partial'        some real content exists (e.g. seeded vocabulary) but is unvalidated/incomplete
 *  - 'blocked'        cannot proceed without a license, a live counterpart system, or a clinical decision
 * Never mark anything 'compliant' or 'certified' here — those claims require external
 * testing this repository cannot perform on its own (see each row's `evidence`).
 */

export type StandardsMatrixRow = {
  area: string
  standardReference: string
  howImplemented: string
  status: 'not_started' | 'structural' | 'partial' | 'blocked'
  evidence: string
}

export const standardsMatrix: StandardsMatrixRow[] = [
  {
    area: 'NABH Digital Health Oncology',
    standardReference: 'NABH Annexure for Cancer Care and Management — Oncology for Digital Health / HIS-EMR systems',
    howImplemented: 'Not yet mapped. The canonical treatment-order model, status state machine, and audit-trail scaffolding built in this phase are the kind of structural evidence an annexure walkthrough would ask for, but no clause-by-clause checklist exists yet.',
    status: 'not_started',
    evidence: 'Obtain the published annexure and its test cases; run them item 24 style (Standard -> Screen -> Supported? -> Gap -> Owner -> Test result) before the next CCA session.',
  },
  {
    area: 'ABDM / FHIR R4',
    standardReference: 'ABDM FHIR Implementation Guide 6.5.0 (published); 7.0.0 in preview',
    howImplemented: 'lib/oncology/adapters.ts exposes getAbdmLinkStatus() returning an honest not_configured state with a PRODUCTION INTEGRATION REQUIRED badge, ready for the patient-identity screen to render instead of the plain placeholder string it showed before.',
    status: 'structural',
    evidence: 'lib/oncology/adapters.ts (AbdmLinkStatus). No ABHA verification, HIP/HIU flow, or consent artefact exchange is implemented — architecture-ready, not connected.',
  },
  {
    area: 'HL7 FHIR',
    standardReference: 'HL7 FHIR R4',
    howImplemented: 'toFhirMedicationRequest / toFhirMedicationAdministration / toFhirCarePlan / toFhirRadiotherapyCourseSummary / toFhirSurgicalProcedure project the internal domain model into FHIR-shaped objects for future export.',
    status: 'structural',
    evidence: 'lib/oncology/adapters.ts. No live FHIR server is called; these are pure, testable projection functions.',
  },
  {
    area: 'mCODE',
    standardReference: 'HL7 mCODE 4.0',
    howImplemented: 'The domain model in lib/oncology/types.ts follows mCODE\'s shape for the profiles it names — Cancer Stage/diagnosis (TreatmentPlan), Cancer-Related Medication Request (TreatmentOrder), Medication Administration (MARDrugAdministration), Surgical Procedure (SurgicalPlan), Radiotherapy Course Summary (RadiationPrescription), Tumor Marker Test / Cancer Disease Status (ResponseAssessment) — and adapters.ts maps each to its FHIR projection (toFhirMedicationRequest, toFhirMedicationAdministration, toFhirCarePlan, toFhirRadiotherapyCourseSummary, toFhirSurgicalProcedure, toFhirResponseObservation, toFhirConsent).',
    status: 'structural',
    evidence: 'lib/oncology/types.ts + adapters.ts. mCODE does not itself define the chemo order-entry workflow (dose fields, verification, dispensing) — that safety/order model is this codebase\'s own addition, per the PDF\'s own caution.',
  },
  {
    area: 'DICOM / DICOM RT',
    standardReference: 'DICOM RT Plan, RT Dose, RT Structure Set, RT treatment records',
    howImplemented: 'RadiationPrescription.dicomRtPlanRef is a reference field — this app records that a plan exists in an external planning/OIS system and its identifier, never the plan content itself.',
    status: 'structural',
    evidence: 'lib/oncology/types.ts (RadiationPrescription). No DICOM objects are parsed, stored, or rendered — by design, this is a workflow/order layer, not a treatment-planning system.',
  },
  {
    area: 'ASCO/ONS antineoplastic administration safety',
    standardReference: '2024 ASCO/ONS Standards for chemotherapy/immunotherapy administration',
    howImplemented: 'TreatmentOrder + VerificationCheckpoint + PreAdministrationChecklist + MARDrugAdministration together carry every field the standard names for order information: diagnosis, regimen/cycle, criteria-to-treat (EligibilityCheck), allergies, patient variables (height/weight/BSA), route/rate/schedule, duration, supportive therapy and drug sequence — deliberately excluding calculation methodology and cumulative-dose enforcement, which stay clinician-decided per this build\'s explicit scope. The independent pharmacy verification checklist (/pharmacy) and pre-administration checklist (/treatment-day) are the double-check workflow the standard names; a pre-cycle Treatment Readiness screen (/treatment-readiness) now captures the labs/toxicity/performance-status review and Proceed/Hold/Delay/Stop decision the standard expects before each cycle.',
    status: 'partial',
    evidence: 'lib/oncology/types.ts (VerificationCheckpoint, PreAdministrationChecklist, TreatmentReadinessAssessment) + app/pharmacy, app/treatment-day, app/treatment-readiness. Structurally complete for order documentation and pre-cycle/pre-administration double-checks; still no licensed cumulative-dose/threshold enforcement, by design.',
  },
  {
    area: 'CTCAE',
    standardReference: 'CTCAE v5.0 adverse-event terminology and grading',
    howImplemented: 'terminology.ts seeds a seeded reference term list (System Organ Class-spanning) and the five generic, term-independent grade definitions for the ToxicityEvent type.',
    status: 'partial',
    evidence: 'lib/oncology/terminology.ts (CTCAE_TERMS, CTCAE_GENERIC_GRADES). ~20 reference terms, not the full ~800-term v5.0 library; no licence/terms-of-use position has been taken on the complete set.',
  },
  {
    area: 'RECIST',
    standardReference: 'RECIST 1.1',
    howImplemented: 'ResponseAssessment models baseline/follow-up lesions and a controlled CR/PR/SD/PD/not_evaluable category list with the standard definitions, replacing free-text "better/worse" language. A dedicated screen (/response-assessment) captures per-lesion measurements and the response category, and every entry is visible in the patient\'s assessment history.',
    status: 'structural',
    evidence: 'lib/oncology/types.ts (ResponseAssessment, Lesion) + terminology.ts (RECIST_CATEGORIES) + app/response-assessment/page.tsx + adapters.ts (toFhirResponseObservation). Category is clinician-selected from the list, never computed by the system from the lesion measurements.',
  },
  {
    area: 'NCCN',
    standardReference: 'NCCN Guidelines / Compendium (licensed pathway content)',
    howImplemented: 'Not embedded — this is the same position the existing internal spec already takes, and remains correct: NCCN content requires a licence. This is also the standard\'s own answer to "where does a recommendation engine plug in": every screen where a guideline suggestion belongs (Treatment Order, Radiation Prescription, Surgical Plan, Treatment Readiness, Response Assessment) already renders <RecommendationPanel>, which calls adapters.ts\'s fetchNexusRecommendation() and today always resolves to an honest not_connected state. No screen needs to change to connect a real engine later — only that one function\'s body, once NCCN or another licensed source is in place. Per item 29, nothing in this codepath is permitted to write a TreatmentOrder/DoseModification/VerificationCheckpoint/MARDrugAdministration — a recommendation is always data for a named, authorized human to accept, modify or dismiss.',
    status: 'blocked',
    evidence: 'lib/oncology/adapters.ts (nexusIntegration, fetchNexusRecommendation) + components/oncology/recommendation-panel.tsx, consumed by app/treatment-order, app/radiation-oncology, app/surgical-oncology, app/treatment-readiness, app/response-assessment. Blocked on licensing, not engineering — the integration seam is built and ready.',
  },
  {
    area: 'ICD / SNOMED / LOINC / controlled terminologies',
    standardReference: 'ICD-O-3, SNOMED CT, LOINC, RxNorm',
    howImplemented: 'CodedValue and CodeSystem types formalise the "display text + standard code" shape across the model; ADMINISTRATION_ROUTES and CANCER_SITES are seeded against SNOMED-CT / ICD-O-3 codes as a working example of the pattern.',
    status: 'partial',
    evidence: 'lib/oncology/types.ts (CodedValue) + terminology.ts. Seeded lists are illustrative starter sets, explicitly flagged for clinical-owner review (item 36), not production-complete code lists.',
  },
  {
    area: 'Hospital formulary',
    standardReference: 'Institution-specific drug formulary',
    howImplemented: 'Regimen.drugSequence references genericDrugName as free text today; the Regimen type is where a real formulary lookup (drug -> available strengths/formulations) would attach once the hospital\'s formulary source is identified.',
    status: 'not_started',
    evidence: 'No formulary data source has been supplied yet — needs a decision on where the hospital\'s formulary lives (pharmacy system export, manual seed list, or a live feed) before this can move past "not started".',
  },
  {
    area: 'MOSAIQ integration',
    standardReference: 'Elekta MOSAIQ (HL7/FHIR/DICOM interfaces, version- and licence-dependent)',
    howImplemented: 'mosaiqIntegrationMapping enumerates all 11 entities the PDF names (patient identity through results) with our-model <-> MOSAIQ-concept pairs and an explicit not_yet_determined data-direction field.',
    status: 'structural',
    evidence: 'lib/oncology/adapters.ts (mosaiqIntegrationMapping). No live MOSAIQ endpoint is called. The replace/coexist/bidirectional-sync decision this mapping depends on has not been made with CCA yet — see the PDF\'s own item 25 questions.',
  },
]
