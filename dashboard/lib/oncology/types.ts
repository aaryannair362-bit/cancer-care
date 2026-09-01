/**
 * Canonical oncology treatment-execution domain model.
 *
 * This is the single model every clinical screen (Treatment Plan, Medical Oncology
 * Order, Pharmacy, Day Care / MAR, Radiation, Surgical, MDT, Toxicity, Response)
 * reads and writes through — see PDF Master To-Do List item 26. No screen may keep
 * its own private shadow copy of an order, a dose, or a status.
 *
 * Dose handling (explicit product decision, not an oversight): there is exactly one
 * dose per ordered drug line — `orderedDose`, entered and authorized by the treating
 * oncologist (and, where relevant, the MDT). It is carried forward unchanged through
 * pharmacy verification, preparation, dispensing, and administration. This module
 * intentionally contains no dose-calculation, dose-threshold, or cumulative-dose-limit
 * logic — those stay clinician-decided. See `state-machine.ts` for what the system
 * *does* enforce (sequencing and authorization gates, not clinical correctness).
 */

// ───────────────────────── Shared primitives ─────────────────────────

export type ActorRef = {
  userId: string
  name: string
  roleLabel: string
}

/** Every clinically significant change appends one of these. Never edited, never deleted. */
export type AuditEntry = {
  id: string
  entityType: string
  entityId: string
  action: string
  actor: ActorRef
  timestamp: string
  reason?: string
  previousValue?: string
  newValue?: string
}

/** A coded reference value: display text a clinician reads, plus the standard code behind it. */
export type CodedValue = {
  code: string
  system: CodeSystem
  display: string
}

export type CodeSystem =
  | 'ICD-O-3' | 'ICD-10' | 'SNOMED-CT' | 'LOINC' | 'RxNorm'
  | 'CTCAE-5' | 'RECIST-1.1' | 'AJCC' | 'NCI-Thesaurus' | 'local'

// ───────────────────────── Unified status vocabulary (item 15) ─────────────────────────

export const TREATMENT_STATUSES = [
  'draft', 'proposed', 'mdt_recommended', 'clinician_approved', 'ordered',
  'verification_pending', 'verified', 'preparation_pending', 'prepared',
  'dispensed', 'ready_for_administration', 'in_progress', 'administered',
  'held', 'delayed', 'cancelled', 'completed',
] as const
export type TreatmentStatus = (typeof TREATMENT_STATUSES)[number]

export const RT_SUB_STATUSES = [
  'prescribed', 'simulation_pending', 'simulation_complete', 'contouring',
  'planning', 'physics_qa', 'physician_approved', 'treatment_ready',
  'on_treatment', 'interrupted', 'completed',
] as const
export type RtSubStatus = (typeof RT_SUB_STATUSES)[number]

export const SURGICAL_SUB_STATUSES = [
  'recommended', 'surgeon_reviewed', 'planned', 'pre_op_ready',
  'scheduled', 'performed', 'post_op', 'histopathology_available',
] as const
export type SurgicalSubStatus = (typeof SURGICAL_SUB_STATUSES)[number]

// ───────────────────────── Care Plan → Treatment Plan hierarchy (item 2) ─────────────────────────

export type TreatmentIntent = 'curative' | 'palliative' | 'neoadjuvant' | 'adjuvant' | 'definitive' | 'diagnostic' | 'salvage'
export type Modality = 'systemic' | 'radiation' | 'surgical' | 'combined_modality' | 'supportive'

/** The overall multidisciplinary strategy. Not a treatment order — never executable on its own. */
export type CarePlan = {
  id: string
  patientId: string
  status: 'active' | 'superseded' | 'closed'
  intent: TreatmentIntent
  diagnosisSummary: string
  originatingMdtCaseId?: string
  version: number
  supersedes?: string
  changeReason?: string
  createdBy: ActorRef
  createdAt: string
}

/** The intended course of cancer treatment derived from a Care Plan. Still not executable. */
export type TreatmentPlan = {
  id: string
  carePlanId: string
  patientId: string
  status: 'active' | 'superseded' | 'closed'
  diagnosis: string
  stage: string
  histology: string
  biomarkers: string[]
  intent: TreatmentIntent
  lineOfTherapy: string
  currentDiseaseStatus: string
  responsibleSpecialty: 'medical_oncology' | 'radiation_oncology' | 'surgical_oncology' | 'combined'
  mdtCaseId?: string
  phases: TreatmentPhase[]
  version: number
  supersedes?: string
  changeReason?: string
  createdBy: ActorRef
  createdAt: string
}

export type TreatmentPhase = {
  id: string
  sequence: number
  modality: Modality
  label: string
  regimenOrProcedureRef?: string
  plannedStart?: string
  durationDescription: string
  status: TreatmentStatus
  responsibleClinician: ActorRef
}

// ───────────────────────── MDT (item 3) ─────────────────────────

export type MDTParticipant = ActorRef & { specialty: string; attendance: 'present' | 'remote' | 'apologies' }

export type MDTCase = {
  id: string
  patientId: string
  status: 'scheduled' | 'discussed' | 'recommendation_recorded' | 'plan_created'
  mdtDate: string
  cancerDiagnosis: string
  stage: string
  performanceStatus: string
  pathologyBiomarkers: string[]
  treatmentIntent: TreatmentIntent
  recommendation: string
  specialtyResponsible: 'medical_oncology' | 'radiation_oncology' | 'surgical_oncology' | 'combined'
  alternativeOptionsDiscussed: string
  rationale: string
  participants: MDTParticipant[]
  finalConsensus: string
  outstandingInvestigations: string[]
  proposedBy: ActorRef
  approvedBy?: ActorRef
  approvedAt?: string
  linkedPlanIds: {
    medicalOncology?: string
    radiationOncology?: string
    surgical?: string
    combined?: string
  }
  createdAt: string
}

// ───────────────────────── Regimen library (item 6) — reference content only ─────────────────────────
// Everything here is descriptive/reference text a pharmacist or oncologist authored and versioned.
// Nothing in this type computes a dose from patient variables.

export type RegimenDrugLine = {
  sequence: number
  genericDrugName: string
  doseBasisDescription: string // e.g. "60 mg/m^2" — reference text, not a formula
  route: string
  diluent?: string
  infusionRate?: string
  infusionDuration?: string
  isPremedication?: boolean
  isSupportive?: boolean
}

export type Regimen = {
  id: string
  name: string
  cancerIndication: string
  intentSetting: TreatmentIntent
  drugSequence: RegimenDrugLine[]
  scheduleDescription: string
  plannedCycles: number
  premedications: string[]
  hydration: string[]
  supportiveTherapy: string[]
  treatmentHoldParameterReferences: string[] // descriptive reference text (e.g. "ANC and platelet count"), no thresholds
  references: string[]
  version: number
  effectiveDate: string
  approvedBy: ActorRef
  status: 'active' | 'retired'
}

// ───────────────────────── Treatment Order → OrderedItem (items 5, 26) ─────────────────────────

export type OrderedDrugLine = {
  id: string
  sequence: number
  genericDrugName: string
  doseBasisDescription: string
  /** The single dose that carries forward through the whole chain. Entered by the ordering clinician. */
  orderedDose: string
  route: string
  diluent?: string
  diluentVolume?: string
  infusionRate?: string
  infusionDuration?: string
  administrationDateTime?: string
  isPremedication?: boolean
  isSupportive?: boolean
  doseModifications: DoseModification[]
}

export type TreatmentOrder = {
  id: string
  patientId: string
  treatmentPlanId: string
  status: TreatmentStatus
  regimenId?: string
  regimenName: string
  diagnosis: string
  treatmentIntent: TreatmentIntent
  lineOfTherapy: string
  cycleNumber: number
  day: number
  plannedNumberOfCycles: number
  protocolReferenceVersion?: string
  drugLines: OrderedDrugLine[]
  eligibilityParametersChecked: EligibilityCheck[]
  heightCm?: number
  weightKg?: number
  bsaM2?: string
  allergiesAcknowledged: boolean
  orderingClinician: ActorRef
  authorizedAt?: string
  createdAt: string
}

/** Presence/acknowledgement only — never a computed pass/fail against a threshold. */
export type EligibilityCheck = {
  parameter: string // e.g. "CBC", "ANC", "Renal function"
  valuePresent: boolean
  clinicianReviewed: boolean
  note?: string
}

export type DoseModificationType =
  | 'dose_reduction' | 'dose_escalation' | 'treatment_delay' | 'drug_omission'
  | 'drug_substitution' | 'cycle_postponement' | 'treatment_discontinuation'

export type DoseModification = {
  id: string
  type: DoseModificationType
  reason: string
  toxicityRef?: string
  relevantLabRef?: string
  clinicalJustification: string
  originalDose: string
  modifiedDose?: string
  percentChange?: string
  approvedBy: ActorRef
  timestamp: string
}

// ───────────────────────── Verification → Dispense (item 7, 9) ─────────────────────────

export type VerificationCheckpoint = {
  id: string
  orderId: string
  patientIdentityConfirmed: boolean
  drugConfirmed: boolean
  doseConfirmed: boolean
  routeConfirmed: boolean
  sequenceConfirmed: boolean
  cycleDayConfirmed: boolean
  allergiesReviewed: boolean
  requiredLabsPresent: boolean
  expiryChecked: boolean
  verifiedBy: ActorRef
  verifiedAt: string
  outcome: 'verified' | 'query_raised' | 'rejected'
  queryReason?: string
}

export type PharmacyDispenseStatus = 'pending_review' | 'verified' | 'preparation' | 'prepared' | 'dispensed' | 'held' | 'rejected' | 'cancelled'

export type DispenseRecord = {
  id: string
  orderId: string
  drugLineId: string
  patientId: string
  status: PharmacyDispenseStatus
  availableFormulationStrength?: string
  quantityRequired?: string
  diluent?: string
  volume?: string
  preparationInstructions?: string
  batchLot?: string
  expiry?: string
  preparedBy?: ActorRef
  preparedAt?: string
  verifiedBy?: ActorRef
  verifiedAt?: string
  dispensedAt?: string
  destination?: string
  wastageRecorded?: { quantity: string; reason: string; recordedBy: ActorRef; at: string }
  holdOrQueryReason?: string
}

// ───────────────────────── Administration / MAR (item 8) ─────────────────────────

export type PreAdministrationChecklist = {
  orderId: string
  twoPatientIdentifiersConfirmed: boolean
  orderVerified: boolean
  consentConfirmed: boolean
  allergyVerified: boolean
  preTreatmentVitalsRecorded: boolean
  requiredLabsAvailable: boolean
  venousAccessConfirmed: boolean
  pharmacyPreparedMedicationConfirmed: boolean
  confirmedBy: ActorRef
  confirmedAt: string
}

export type MARDrugAdministration = {
  id: string
  orderId: string
  drugLineId: string
  sequence: number
  drug: string
  doseGiven: string // should equal orderedDose unless a DoseModification exists — displayed, never auto-corrected
  startTime?: string
  endTime?: string
  rate?: string
  route: string
  lineAccess?: string
  administeredBy: ActorRef
  infusionStatus: 'not_started' | 'in_progress' | 'paused' | 'completed' | 'held' | 'discontinued'
  reactionOrToxicity?: string
  intervention?: string
  varianceFromOrder?: string
}

export type PostAdministrationRecord = {
  orderId: string
  completionStatus: 'completed' | 'partially_completed' | 'held' | 'deferred' | 'discontinued'
  postTreatmentVitals?: string
  dischargeInstructions?: string
  nextCycleDate?: string
  recordedBy: ActorRef
  recordedAt: string
}

// ───────────────────────── Toxicity — CTCAE-shaped (item 16) ─────────────────────────

export type ToxicityEvent = {
  id: string
  patientId: string
  orderId?: string
  term: CodedValue // from CTCAE_TERMS in terminology.ts
  grade: 1 | 2 | 3 | 4 | 5
  onset: string
  relationshipToTherapy: 'unrelated' | 'unlikely' | 'possible' | 'probable' | 'definite'
  intervention?: string
  treatmentModificationId?: string
  outcome: 'resolved' | 'resolving' | 'ongoing' | 'resolved_with_sequelae' | 'fatal'
  recordedBy: ActorRef
  recordedAt: string
}

// ───────────────────────── Response assessment — RECIST-shaped (item 17) ─────────────────────────

export type Lesion = {
  id: string
  site: string
  type: 'target' | 'non_target' | 'new'
  baselineMeasurementMm?: string
  followUpMeasurementMm?: string
}

export type ResponseAssessment = {
  id: string
  patientId: string
  frameworkName: 'RECIST 1.1' | 'clinical_assessment'
  assessmentDate: string
  imagingDate?: string
  lesions: Lesion[]
  responseCategory: 'CR' | 'PR' | 'SD' | 'PD' | 'not_evaluable'
  diseaseStatus: string
  relevantBiomarkers: string[]
  assessedBy: ActorRef
}

// ───────────────────────── Radiation Oncology (items 11, 12) ─────────────────────────

export type RadiationPrescription = {
  id: string
  patientId: string
  treatmentPlanId: string
  status: TreatmentStatus
  rtSubStatus: RtSubStatus
  diagnosis: string
  treatmentSite: string
  laterality?: string
  intent: TreatmentIntent
  modality: string
  technique: string
  treatmentPhase: string
  totalPrescribedDoseGy: string
  dosePerFractionGy: string
  numberOfFractions: number
  frequency: string
  startDate?: string
  concurrentSystemicTreatment?: string
  targetVolumes: string[]
  organsAtRisk: string[]
  simulationRequired: boolean
  immobilization?: string
  imageGuidanceRequired: boolean
  bolus?: string
  specialInstructions?: string
  dicomRtPlanRef?: string // reference to an external planning/OIS system record, not stored here
  physicianApprovedBy?: ActorRef
  physicianApprovedAt?: string
  createdBy: ActorRef
  createdAt: string
}

export type RadiationFraction = {
  id: string
  prescriptionId: string
  fractionNumber: number
  scheduledDate: string
  status: 'scheduled' | 'delivered' | 'missed' | 'rescheduled'
  deliveredDoseGy?: string
  deliveredAt?: string
  deliveredBy?: ActorRef
  interruptionReason?: string
  onTreatmentReviewNote?: string
}

// ───────────────────────── Surgical Oncology (item 13) ─────────────────────────

export type SurgicalPlan = {
  id: string
  patientId: string
  treatmentPlanId: string
  status: TreatmentStatus
  surgicalSubStatus: SurgicalSubStatus
  procedure: string
  indication: string
  intent: string
  anatomicalSite: string
  laterality?: string
  proposedExtent: string
  approach: string
  nodalProcedure?: string
  reconstruction?: string
  plannedDate?: string
  priority: 'routine' | 'urgent' | 'emergency'
  preOpRequirements: string[]
  requiredImagingPathology: string[]
  anaesthesiaClearance?: string
  bloodRequirement?: string
  specialInstructions?: string
  performedProcedure?: string // distinct from `procedure` (planned) once surgery happens
  performedAt?: string
  operativeFindings?: string
  histopathologyAvailable: boolean
  histopathologySummary?: string
  fedBackToMdtCaseId?: string
  recommendedBy: ActorRef
  createdAt: string
}

// ───────────────────────── Journey (item 1) ─────────────────────────

export const JOURNEY_DEPARTMENTS = [
  'registration', 'nurse_intake', 'medical_oncology', 'radiology', 'pathology',
  'surgical_oncology', 'radiation_oncology', 'mdt_tumour_board', 'day_care_infusion',
  'pharmacy', 'surgery', 'radiation_treatment', 'follow_up',
] as const
export type JourneyDepartment = (typeof JOURNEY_DEPARTMENTS)[number]

export type JourneyMilestone = {
  id: string
  patientId: string
  department: JourneyDepartment
  label: string
  date: string
  clinician?: ActorRef
  status: TreatmentStatus | 'in_progress' | 'complete'
  isCurrent: boolean
}

// ───────────────────────── Treatment Readiness (item 31) ─────────────────────────
// A clinician decision recorded before every cycle/session — distinct from the Day Care
// nurse's pre-administration safety checklist (PreAdministrationChecklist above), which
// happens later and confirms a single administration is safe to start. This is the earlier
// "should this cycle proceed at all" clinical decision.

export type TreatmentReadinessDecision = 'proceed' | 'proceed_modified' | 'hold' | 'delay' | 'stop'

export type TreatmentReadinessAssessment = {
  id: string
  patientId: string
  treatmentPlanId?: string
  cycleNumber: number
  assessmentDate: string
  labsReviewed: boolean
  labsSummary?: string
  performanceStatus: string
  weightKg?: number
  bsaM2?: string
  toxicitiesReviewed: boolean
  toxicitySummary?: string
  previousTreatmentTolerance?: string
  doseModificationPlanned?: string
  currentMedicationsReviewed: boolean
  allergiesReviewed: boolean
  infectionOrClinicalConcerns?: string
  decision: TreatmentReadinessDecision
  decisionReason?: string
  assessedBy: ActorRef
  createdAt: string
}

// ───────────────────────── Consent & patient education (item 33) ─────────────────────────

export type ConsentType =
  | 'general_treatment' | 'chemotherapy' | 'radiation' | 'surgical' | 'data_processing' | 'recording'
export type ConsentStatus = 'not_started' | 'discussed' | 'signed' | 'declined' | 'withdrawn'

export type ConsentRecord = {
  id: string
  patientId: string
  type: ConsentType
  status: ConsentStatus
  relatedOrderId?: string
  relatedRadiationPrescriptionId?: string
  relatedSurgicalPlanId?: string
  documentTitle: string
  discussedTopics: string[] // e.g. "Expected adverse effects", "Emergency/contact instructions"
  signedBy?: string // patient / guardian name — free text, not the ActorRef (patient isn't a system user)
  witnessedBy?: ActorRef
  signedAt?: string
  declinedReason?: string
  createdAt: string
}

// ───────────────────────── Patient-uploaded document (attachments & OCR) ─────────────────────────
// Client-side today — captured, stored and OCR'd entirely in the browser (see
// lib/documents/store.tsx). Field names deliberately mirror the real backend's
// PatientDocument/OCR contract (backend/app/routers/patient_documents.py) so swapping this
// for a real authenticated upload later is a data-access change, not a reshape.

export type DocumentOcrStatus = 'pending' | 'processing' | 'completed' | 'needs_review' | 'failed'

export type PatientDocumentRecord = {
  id: string
  patientId: string
  filename: string
  contentType: string
  fileSize: number
  documentType: string
  dataUrl: string // base64 data: URL — client-only storage; not a substitute for a real file store
  ocrStatus: DocumentOcrStatus
  ocrEngine?: string
  extractedText?: string
  extractedFields?: Record<string, string>
  ocrError?: string
  uploadedBy: ActorRef
  uploadedAt: string
  processedAt?: string
}

// ───────────────────────── Recommendation slot (NEXUS-ready, item 29/30) ─────────────────────────
// Structural contract only. No engine populates `recommendation` yet — every consumer must render
// the `not_connected` state honestly. See adapters.ts.

export type RecommendationAudience = 'clinician' | 'nurse' | 'pharmacist' | 'patient'

export type RecommendationSlot = {
  id: string
  patientId: string
  context: string // e.g. 'treatment-readiness', 'mdt', 'nexus-brief', 'care-plan'
  audience: RecommendationAudience
  connectionState: 'not_connected' | 'available' | 'error'
  source?: 'NEXUS'
  guidelinePathwayName?: string
  guidelineVersion?: string
  recommendationText?: string
  rationale?: string
  provenance?: string
  generatedAt?: string
  clinicianResponse?: {
    decision: 'accepted' | 'modified' | 'dismissed'
    actor: ActorRef
    at: string
    note?: string
  }
}
