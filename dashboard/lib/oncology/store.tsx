'use client'

/**
 * OncologyProvider — the canonical treatment-execution store (PDF item 26).
 *
 * Every clinical screen that touches a Treatment Order, Pharmacy dispense, Day Care
 * administration, MDT case, Radiation prescription, Surgical plan, toxicity event, or
 * response assessment reads and writes through `useOncology()`.
 *
 * This now calls the real backend (backend/app, via ./backend-client.ts) instead of
 * localStorage. Readers stay synchronous getters over a cache fetched on mount and
 * refreshed after each mutation — no consuming component needs to poll or subscribe
 * differently than before. Mutators are now `async` (a real network write cannot be
 * synchronous) and only ever update the cache *after* the backend confirms them — never
 * optimistically — because showing a state the server hasn't confirmed is exactly the
 * "screens show contradictory values" failure item 26 exists to prevent. Callers that
 * used to read a mutator's return value synchronously now `await` it — see the page
 * components for the small `async`/`await` additions this required.
 *
 * Known, deliberate scope limits of this pass (documented rather than silently dropped):
 *  - Journey milestones and Treatment Readiness assessments have no natural backend
 *    entity yet and stay local (localStorage). The Day Care pre-administration
 *    checklist *is* now backend-persisted — see recordPreAdministrationChecklist below,
 *    which writes it as an OncologyRecordExtension on the order (same mechanism and the
 *    same trade-off as recordDoseModification: not fetched during the initial bulk load,
 *    populates in-session, blank again after a reload until re-confirmed).
 *  - CarePlan is synthesized client-side from the fetched MDTCase + TreatmentPlan rather
 *    than a separate backend CarePlan round trip — the two already carry everything
 *    CarePlan displays.
 *  - This pass supports exactly the one demo patient the dashboard has always been
 *    seeded around (see seed-data.ts) — every fetched record is attributed to
 *    `DEMO_PATIENT_ID` client-side, matching the backend's own single-demo-patient
 *    resolver (GET /api/cca/oncology-ext/demo-patient).
 *  - A handful of dashboard-only descriptive fields with no backend column (e.g.
 *    ToxicityEvent.relationshipToTherapy/outcome, ConsentRecord.discussedTopics, most of
 *    DispenseRecord's per-line prep detail) are carried in the backend's generic
 *    `OncologyRecordExtension` table rather than a schema change to an existing, live
 *    table — see backend-client.ts's apiGetExtension/apiPutExtension. Extension-only
 *    fields are not fetched during the initial bulk load (to keep that request count
 *    bounded); they populate correctly as soon as a screen writes them in-session, but
 *    a fresh page reload shows them blank until re-entered. This is a known trade-off,
 *    not a silent data loss of anything clinically load-bearing.
 *  - TreatmentOrder.status is *composed* from three backend sources (the order's own
 *    coarse status, PharmacyReadiness.status, and the medication administration
 *    aggregate) via status-map.ts's `composeOrderStatus` — see that file's header for
 *    why this is a best-effort projection, not a perfect inverse.
 *  - Dispensing is tracked one record per order (matching the backend's PharmacyReadiness
 *    shape), not one per drug line as the original localStorage-only model allowed.
 */

import * as React from 'react'

import {
  assertRtSubStatusTransition, assertSurgicalSubStatusTransition, assertTreatmentStatusTransition,
  isCancelledOrderAdministrationAttempt, isUnverifiedDispenseAttempt,
} from './state-machine'
import { DEMO_PATIENT_ID } from './seed-data'
import { useDemoAccess } from '@/components/demo-access-provider'
import type { RoleId } from '../demo-access'
import {
  BackendError, ROLE_TO_BACKEND, apiApproveMdtRecommendation, apiCancelTreatmentOrder,
  apiCreateMdtCase, apiCreateRadiationPrescription, apiCreateRegimen, apiCreateSurgicalPlan,
  apiCreateTreatmentOrder, apiCreateTreatmentPlan, apiGetExtension, apiGetGeneralPatientSummary,
  apiGetPatientTreatmentOrders, apiGetPatientTreatmentPlans, apiGetPharmacyReadiness,
  apiGetStagingHistory, apiListConsents, apiListDomainEvents, apiListMdtCases, apiListPatientResults,
  apiListRadiationFractions, apiListRadiationPrescriptions, apiListRegimens, apiListResponseAssessments,
  apiListSurgicalPlans, apiListToxicityEvents, apiListTreatmentPlanPhases, apiPostMedication,
  apiPostPharmacyReadiness, apiPutExtension, apiRecordConsent, apiRecordFractionEvent,
  apiRecordMdtRecommendation, apiRecordMedicationEvent, apiRecordResponseAssessment,
  apiRecordSurgicalOutcome, apiRecordToxicity, apiReplaceTreatmentPlanPhases, apiSignTreatmentOrder,
  apiSignTreatmentPlan, apiTransitionRadiationPrescription, apiTransitionSurgicalPlan, apiUpdateTreatmentPlan,
  fetchDemoPatient,
  type ApiDomainEvent, type ApiRadiationPrescription, type ApiRegimen, type ApiSurgicalPlan, type ApiTreatmentOrder,
  type ApiTreatmentPlan, type ApiTreatmentPlanPhase, type ApiToxicityEvent, type ApiResponseAssessment,
} from './backend-client'
import { composeOrderStatus, mapMdtCaseStatus, mapPlanStatus, pharmacyStatusForTarget } from './status-map'
import type {
  ActorRef, AuditEntry, CarePlan, ConsentRecord, ConsentStatus, DispenseRecord, DoseModification,
  JourneyMilestone, MARDrugAdministration, MDTCase, PostAdministrationRecord, PreAdministrationChecklist,
  RadiationFraction, RadiationPrescription, Regimen, ResponseAssessment, RtSubStatus,
  SurgicalPlan, SurgicalSubStatus, ToxicityEvent, TreatmentOrder, TreatmentPlan, TreatmentPhase,
  TreatmentReadinessAssessment, TreatmentStatus, VerificationCheckpoint,
} from './types'

const LOCAL_STORAGE_KEY = 'aivana-onco-local-state-v1' // only the not-yet-backend-wired slices — see header

// Which cca_domain_events payload field carries this entity's id, per AuditEntry.entityType
// string — matching the exact kwarg name every publish() call in cca.py / cca_oncology_ext.py
// already uses (see each router's publish() call sites). Entity types not listed here have no
// backend event stream yet (e.g. TreatmentReadinessAssessment) and stay local-only.
const DOMAIN_EVENT_ID_FIELDS: Partial<Record<string, string>> = {
  TreatmentOrder: 'treatment_order_id',
  TreatmentPlan: 'treatment_plan_id',
  MDTCase: 'mdt_case_id',
  CarePlan: 'care_plan_id',
  ConsentRecord: 'consent_id',
  RadiationPrescription: 'prescription_id',
  SurgicalPlan: 'plan_id',
}

// A RECORD_EXTENSION_UPDATED event (put_record_extension in cca_oncology_ext.py — dose
// modifications, the pre-administration checklist, MDT case fields, pharmacy checklists,
// toxicity/response detail, consent extras) carries entity_table/entity_id instead of a
// per-event id field, so it needs its own match: this is the entity_table each entityType's
// extension rows are actually stored under (see every apiPutExtension call site above).
const EXTENSION_ENTITY_TABLES: Partial<Record<string, string>> = {
  TreatmentOrder: 'cca_treatment_orders',
  MDTCase: 'cca_mdt_cases',
  ToxicityEvent: 'cca_toxicity_events',
  ResponseAssessment: 'cca_response_assessments',
  ConsentRecord: 'cca_consents',
}

type LocalState = {
  journeyMilestones: JourneyMilestone[]
  treatmentReadinessAssessments: TreatmentReadinessAssessment[]
  auditLog: AuditEntry[]
}

function seedLocalState(): LocalState {
  return {
    journeyMilestones: [
      { id: 'jm-1', patientId: DEMO_PATIENT_ID, department: 'registration', label: 'Registration', date: '2026-05-20', status: 'complete', isCurrent: false },
      { id: 'jm-2', patientId: DEMO_PATIENT_ID, department: 'medical_oncology', label: 'Medical Oncology', date: '2026-05-22', status: 'complete', isCurrent: false },
      { id: 'jm-3', patientId: DEMO_PATIENT_ID, department: 'mdt_tumour_board', label: 'MDT / Tumour Board', date: '2026-06-20', status: 'complete', isCurrent: false },
      { id: 'jm-4', patientId: DEMO_PATIENT_ID, department: 'surgery', label: 'Surgery', date: '2026-06-12', status: 'complete', isCurrent: false },
      { id: 'jm-5', patientId: DEMO_PATIENT_ID, department: 'medical_oncology', label: 'Medical Oncology', date: '2026-07-18', status: 'in_progress', isCurrent: true },
      { id: 'jm-6', patientId: DEMO_PATIENT_ID, department: 'pharmacy', label: 'Pharmacy', date: '', status: 'ordered', isCurrent: false },
      { id: 'jm-7', patientId: DEMO_PATIENT_ID, department: 'day_care_infusion', label: 'Day Care / Infusion', date: '', status: 'ordered', isCurrent: false },
      { id: 'jm-8', patientId: DEMO_PATIENT_ID, department: 'follow_up', label: 'Follow-up', date: '', status: 'draft', isCurrent: false },
    ],
    treatmentReadinessAssessments: [],
    auditLog: [],
  }
}

type OncologyState = {
  regimens: Regimen[]
  mdtCases: MDTCase[]
  carePlans: CarePlan[]
  treatmentPlans: TreatmentPlan[]
  treatmentOrders: TreatmentOrder[]
  verifications: VerificationCheckpoint[]
  dispenseRecords: DispenseRecord[]
  marEntries: MARDrugAdministration[]
  postAdministrationRecords: PostAdministrationRecord[]
  preAdministrationChecklists: PreAdministrationChecklist[]
  toxicityEvents: ToxicityEvent[]
  responseAssessments: ResponseAssessment[]
  radiationPrescriptions: RadiationPrescription[]
  radiationFractions: RadiationFraction[]
  surgicalPlans: SurgicalPlan[]
  consentRecords: ConsentRecord[]
  // Read-only supplements for Patient Summary (PDF item 18), sourced from the general CCA
  // patient record rather than the oncology-execution tables above — see backend-client.ts's
  // apiGetGeneralPatientSummary / apiGetStagingHistory / apiListPatientResults.
  comorbiditiesSummary: string
  stagingDetail: { stageValue: string; systemLabel: string; confirmedAt: string } | null
  recentImaging: Array<{ id: string; title: string; status: string; resultedAt: string }>
  // The real, durably-persisted event stream (cca_domain_events) behind every "Audit
  // trail" panel — see getAuditTrail below for how this is filtered per entity.
  domainEvents: ApiDomainEvent[]
} & LocalState

function emptyBackendState(): Omit<OncologyState, keyof LocalState> {
  return {
    regimens: [], mdtCases: [], carePlans: [], treatmentPlans: [], treatmentOrders: [],
    verifications: [], dispenseRecords: [], marEntries: [], postAdministrationRecords: [],
    preAdministrationChecklists: [],
    toxicityEvents: [], responseAssessments: [], radiationPrescriptions: [], radiationFractions: [],
    surgicalPlans: [], consentRecords: [],
    comorbiditiesSummary: '', stagingDetail: null, recentImaging: [],
    domainEvents: [],
  }
}

let idCounter = 0
function nextId(prefix: string) {
  idCounter += 1
  return `${prefix}-${Date.now()}-${idCounter}`
}
function nowIso() {
  return new Date().toISOString()
}
const UNKNOWN_ACTOR: ActorRef = { userId: 'unknown', name: 'Unknown', roleLabel: 'Unknown' }

// ─────────────────────────────────────────────────────────────────────────
// API -> dashboard-shape converters. Every backend integer id becomes a string at this
// boundary (String(id)); every write back to the API converts it back with Number(id).
// Raw API objects a mutator needs later (to preserve fields it isn't itself changing)
// are cached in `rawOrders`/`rawPharmacy` refs inside the provider, not smuggled onto
// the public types.
// ─────────────────────────────────────────────────────────────────────────

function actorFromName(name: string | null | undefined, roleLabel = 'Clinician', fallback = UNKNOWN_ACTOR): ActorRef {
  if (!name) return fallback
  return { userId: name, name, roleLabel }
}

function regimenFromApi(api: ApiRegimen): Regimen {
  return {
    id: String(api.id),
    name: api.name,
    cancerIndication: (api.cancer_indication as string) ?? '',
    intentSetting: ((api.intent_setting as string) ?? 'adjuvant') as Regimen['intentSetting'],
    drugSequence: api.drug_lines.map((line) => ({
      sequence: (line.sequence_number as number) ?? 1,
      genericDrugName: line.generic_name as string,
      doseBasisDescription: (line.standard_protocol_dose as string) ?? (line.dose_basis as string) ?? '',
      route: (line.route as string) ?? '',
    })),
    scheduleDescription: (api.schedule as string) ?? '',
    plannedCycles: (api.number_of_cycles as number) ?? 0,
    premedications: typeof api.premedications === 'string' ? (api.premedications as string).split(';').map((s) => s.trim()).filter(Boolean) : [],
    hydration: typeof api.hydration === 'string' ? (api.hydration as string).split(';').map((s) => s.trim()).filter(Boolean) : [],
    supportiveTherapy: typeof api.supportive_therapy === 'string' ? (api.supportive_therapy as string).split(';').map((s) => s.trim()).filter(Boolean) : [],
    treatmentHoldParameterReferences: typeof api.hold_parameters === 'string' ? (api.hold_parameters as string).split(';').map((s) => s.trim()).filter(Boolean) : [],
    references: typeof api.reference_notes === 'string' ? (api.reference_notes as string).split(';').map((s) => s.trim()).filter(Boolean) : [],
    version: Number(api.version) || 1,
    effectiveDate: (api.effective_date as string) ?? '',
    approvedBy: actorFromName(api.approved_by as string, 'Pharmacist'),
    status: 'active',
  }
}

function mdtCaseFromApi(
  api: { id: number; question: string; status: string },
  ext: Record<string, unknown>,
  proposedByFallback: ActorRef = UNKNOWN_ACTOR
): MDTCase {
  const linkedPlanIds = (ext.linkedPlanIds as MDTCase['linkedPlanIds']) ?? {}
  return {
    id: String(api.id),
    patientId: DEMO_PATIENT_ID,
    status: mapMdtCaseStatus(api.status, Object.keys(linkedPlanIds).length > 0),
    mdtDate: (ext.mdtDate as string) ?? '',
    cancerDiagnosis: (ext.cancerDiagnosis as string) ?? api.question,
    stage: (ext.stage as string) ?? '',
    performanceStatus: (ext.performanceStatus as string) ?? '',
    pathologyBiomarkers: (ext.pathologyBiomarkers as string[]) ?? [],
    treatmentIntent: ((ext.treatmentIntent as string) ?? 'adjuvant') as MDTCase['treatmentIntent'],
    recommendation: (ext.recommendation as string) ?? api.question,
    specialtyResponsible: ((ext.specialtyResponsible as string) ?? 'medical_oncology') as MDTCase['specialtyResponsible'],
    alternativeOptionsDiscussed: (ext.alternativeOptionsDiscussed as string) ?? '',
    rationale: (ext.rationale as string) ?? '',
    participants: (ext.participants as MDTCase['participants']) ?? [],
    finalConsensus: (ext.finalConsensus as string) ?? '',
    outstandingInvestigations: (ext.outstandingInvestigations as string[]) ?? [],
    proposedBy: (ext.proposedBy as ActorRef) ?? proposedByFallback,
    approvedBy: ext.approvedBy as ActorRef | undefined,
    approvedAt: ext.approvedAt as string | undefined,
    linkedPlanIds,
    createdAt: (ext.createdAt as string) ?? nowIso(),
  }
}

function phaseFromApi(api: ApiTreatmentPlanPhase): TreatmentPhase {
  return {
    id: String(api.id),
    sequence: api.sequence,
    modality: api.modality as TreatmentPhase['modality'],
    label: api.label,
    regimenOrProcedureRef: api.regimen_or_procedure_ref ?? undefined,
    plannedStart: api.planned_start ?? undefined,
    durationDescription: api.duration_description ?? '',
    status: api.status as TreatmentStatus,
    responsibleClinician: actorFromName(api.responsible_clinician_name, api.responsible_clinician_role ?? 'Clinician'),
  }
}

function treatmentPlanFromApi(api: ApiTreatmentPlan, phases: ApiTreatmentPlanPhase[], extra?: Partial<TreatmentPlan>): TreatmentPlan {
  return {
    id: String(api.id),
    carePlanId: api.care_plan_id ? String(api.care_plan_id) : '',
    patientId: DEMO_PATIENT_ID,
    status: mapPlanStatus(api.status),
    diagnosis: extra?.diagnosis ?? '',
    stage: extra?.stage ?? '',
    histology: extra?.histology ?? '',
    biomarkers: extra?.biomarkers ?? [],
    intent: (api.intent.toLowerCase() as TreatmentPlan['intent']) || 'curative',
    lineOfTherapy: extra?.lineOfTherapy ?? 'First line',
    currentDiseaseStatus: extra?.currentDiseaseStatus ?? '',
    responsibleSpecialty: extra?.responsibleSpecialty ?? 'medical_oncology',
    mdtCaseId: extra?.mdtCaseId,
    phases: phases.map(phaseFromApi),
    version: api.version_no,
    supersedes: api.supersedes_id ? String(api.supersedes_id) : undefined,
    createdBy: actorFromName(api.created_by, 'Clinician'),
    createdAt: nowIso(),
  }
}

function synthesizeCarePlan(plan: TreatmentPlan, mdtCase?: MDTCase): CarePlan {
  return {
    id: `care-${plan.id}`,
    patientId: plan.patientId,
    status: plan.status,
    intent: plan.intent,
    diagnosisSummary: mdtCase ? `${mdtCase.cancerDiagnosis}, ${mdtCase.stage}` : plan.diagnosis,
    originatingMdtCaseId: mdtCase?.id ?? plan.mdtCaseId,
    version: plan.version,
    createdBy: plan.createdBy,
    createdAt: plan.createdAt,
  }
}

function treatmentOrderFromApi(api: ApiTreatmentOrder, pharmacyStatus: string | null, medicationStatuses: string[] = [], previous?: TreatmentOrder): TreatmentOrder {
  const instructions = (api.instructions ?? {}) as Record<string, unknown>
  return {
    id: String(api.id),
    patientId: DEMO_PATIENT_ID,
    status: composeOrderStatus({ orderStatus: api.status, pharmacyStatus, medicationStatuses }),
    regimenId: instructions.regimenId as string | undefined,
    regimenName: (instructions.regimenName as string) ?? '',
    diagnosis: (instructions.diagnosis as string) ?? '',
    treatmentIntent: (instructions.treatmentIntent as TreatmentOrder['treatmentIntent']) ?? 'adjuvant',
    lineOfTherapy: (instructions.lineOfTherapy as string) ?? '',
    cycleNumber: (instructions.cycleNumber as number) ?? 1,
    day: (instructions.day as number) ?? 1,
    plannedNumberOfCycles: (instructions.plannedNumberOfCycles as number) ?? 1,
    protocolReferenceVersion: instructions.protocolReferenceVersion as string | undefined,
    drugLines: (instructions.drugLines as TreatmentOrder['drugLines']) ?? previous?.drugLines ?? [],
    eligibilityParametersChecked: (instructions.eligibilityParametersChecked as TreatmentOrder['eligibilityParametersChecked']) ?? [],
    heightCm: instructions.heightCm as number | undefined,
    weightKg: instructions.weightKg as number | undefined,
    bsaM2: instructions.bsaM2 as string | undefined,
    allergiesAcknowledged: Boolean(instructions.allergiesAcknowledged),
    orderingClinician: (instructions.orderingClinician as ActorRef) ?? UNKNOWN_ACTOR,
    treatmentPlanId: String(api.treatment_plan_id),
    authorizedAt: api.signed_at ?? undefined,
    createdAt: previous?.createdAt ?? nowIso(),
  }
}

function dispenseRecordFromReadiness(orderId: string, readiness: Record<string, unknown> | null, drugLineId: string): DispenseRecord | null {
  if (!readiness) return null
  const statusMap: Record<string, DispenseRecord['status']> = {
    Verified: 'verified', Preparing: 'preparation', Ready: 'prepared', Dispensed: 'dispensed', Received: 'dispensed',
  }
  return {
    id: `dispense-${readiness.id}`,
    orderId,
    drugLineId,
    patientId: DEMO_PATIENT_ID,
    status: statusMap[readiness.status as string] ?? 'pending_review',
    preparedBy: readiness.status_updated_by ? actorFromName(readiness.status_updated_by as string, 'Pharmacist') : undefined,
    preparedAt: readiness.status_updated_at as string | undefined,
    dispensedAt: (readiness.status === 'Dispensed' || readiness.status === 'Received') ? (readiness.status_updated_at as string) : undefined,
    destination: readiness.received_by ? 'Day Care' : undefined,
    holdOrQueryReason: readiness.notes as string | undefined,
  }
}

function toxicityFromApi(api: ApiToxicityEvent, ext: Record<string, unknown> = {}): ToxicityEvent {
  return {
    id: String(api.id),
    patientId: DEMO_PATIENT_ID,
    orderId: ext.orderId as string | undefined,
    term: { code: api.term, system: 'CTCAE-5', display: api.term },
    grade: (Math.max(1, api.grade) as ToxicityEvent['grade']),
    onset: api.onset_date ?? '',
    relationshipToTherapy: (ext.relationshipToTherapy as ToxicityEvent['relationshipToTherapy']) ?? 'possible',
    intervention: ext.intervention as string | undefined,
    treatmentModificationId: ext.treatmentModificationId as string | undefined,
    outcome: (ext.outcome as ToxicityEvent['outcome']) ?? (api.ongoing ? 'ongoing' : 'resolved'),
    recordedBy: UNKNOWN_ACTOR,
    recordedAt: nowIso(),
  }
}

function responseFromApi(api: ApiResponseAssessment, ext: Record<string, unknown> = {}): ResponseAssessment {
  return {
    id: String(api.id),
    patientId: DEMO_PATIENT_ID,
    frameworkName: 'RECIST 1.1',
    assessmentDate: api.recorded_at ?? nowIso(),
    imagingDate: api.imaging_reference ?? undefined,
    lesions: (api.lesions as ResponseAssessment['lesions']) ?? [],
    responseCategory: (api.response_category === 'NE' ? 'not_evaluable' : api.response_category) as ResponseAssessment['responseCategory'],
    diseaseStatus: (ext.diseaseStatus as string) ?? '',
    relevantBiomarkers: (ext.relevantBiomarkers as string[]) ?? [],
    assessedBy: actorFromName(api.recorded_by, 'Clinician'),
  }
}

function radiationPrescriptionFromApi(api: ApiRadiationPrescription, treatmentPlanId = '', createdBy: ActorRef = UNKNOWN_ACTOR, approvedBy?: ActorRef): RadiationPrescription {
  return {
    id: String(api.id),
    patientId: DEMO_PATIENT_ID,
    treatmentPlanId,
    status: api.rt_sub_status === 'completed' ? 'completed' : api.rt_sub_status === 'on_treatment' ? 'in_progress' : 'ordered',
    rtSubStatus: api.rt_sub_status as RtSubStatus,
    diagnosis: (api.diagnosis as string) ?? '',
    treatmentSite: api.treatment_site as string,
    laterality: api.laterality as string | undefined,
    intent: ((api.intent as string) ?? 'curative') as RadiationPrescription['intent'],
    modality: (api.modality as string) ?? '',
    technique: (api.technique as string) ?? '',
    treatmentPhase: (api.treatment_phase as string) ?? '',
    totalPrescribedDoseGy: String(api.total_prescribed_dose_gy),
    dosePerFractionGy: String(api.dose_per_fraction_gy),
    numberOfFractions: api.number_of_fractions,
    frequency: (api.frequency as string) ?? '',
    startDate: api.start_date as string | undefined,
    targetVolumes: (api.target_volumes as string[]) ?? [],
    organsAtRisk: (api.organs_at_risk as string[]) ?? [],
    simulationRequired: Boolean(api.simulation_required),
    immobilization: api.immobilization as string | undefined,
    imageGuidanceRequired: Boolean(api.image_guidance_required),
    bolus: api.bolus as string | undefined,
    specialInstructions: api.special_instructions as string | undefined,
    dicomRtPlanRef: api.dicom_rt_plan_ref as string | undefined,
    physicianApprovedBy: api.signer_email ? (approvedBy ?? actorFromName(api.signer_email as string, (api.signer_role as string) ?? 'Radiation Oncologist')) : undefined,
    physicianApprovedAt: api.signed_at as string | undefined,
    createdBy,
    createdAt: nowIso(),
  }
}

function radiationFractionFromApi(api: Record<string, unknown> & { id: number; fraction_number: number; status: string }, prescriptionId: string): RadiationFraction {
  return {
    id: String(api.id),
    prescriptionId,
    fractionNumber: api.fraction_number,
    scheduledDate: (api.scheduled_date as string) ?? '',
    status: api.status as RadiationFraction['status'],
    deliveredDoseGy: api.delivered_dose_gy ? String(api.delivered_dose_gy) : undefined,
    interruptionReason: api.interruption_reason as string | undefined,
    onTreatmentReviewNote: api.on_treatment_review_note as string | undefined,
    deliveredBy: api.recorded_by ? actorFromName(api.recorded_by as string, 'Radiation Therapist') : undefined,
    deliveredAt: api.recorded_at as string | undefined,
  }
}

function surgicalPlanFromApi(api: ApiSurgicalPlan, treatmentPlanId = '', recommendedBy: ActorRef = UNKNOWN_ACTOR, operativeFindings?: string): SurgicalPlan {
  return {
    id: String(api.id),
    patientId: DEMO_PATIENT_ID,
    treatmentPlanId,
    status: api.status === 'histopathology_available' ? 'completed' : api.status === 'performed' || api.status === 'post_op' ? 'in_progress' : 'ordered',
    surgicalSubStatus: api.status as SurgicalSubStatus,
    procedure: api.procedure,
    indication: (api.indication as string) ?? '',
    intent: (api.intent as string) ?? '',
    anatomicalSite: (api.anatomical_site as string) ?? '',
    laterality: api.laterality as string | undefined,
    proposedExtent: (api.proposed_extent as string) ?? '',
    approach: (api.approach as string) ?? '',
    nodalProcedure: api.nodal_procedure as string | undefined,
    reconstruction: api.reconstruction as string | undefined,
    plannedDate: api.planned_date as string | undefined,
    priority: ((api.priority as string) ?? 'routine') as SurgicalPlan['priority'],
    preOpRequirements: typeof api.pre_op_requirements === 'string' ? (api.pre_op_requirements as string).split(';').map((s) => s.trim()).filter(Boolean) : [],
    requiredImagingPathology: typeof api.required_imaging_pathology === 'string' ? (api.required_imaging_pathology as string).split(';').map((s) => s.trim()).filter(Boolean) : [],
    anaesthesiaClearance: api.anaesthesia_clearance as string | undefined,
    bloodRequirement: api.blood_requirement as string | undefined,
    specialInstructions: api.special_instructions as string | undefined,
    performedProcedure: api.performed_procedure as string | undefined,
    performedAt: api.performed_date as string | undefined,
    operativeFindings,
    histopathologyAvailable: Boolean(api.histopathology_summary),
    histopathologySummary: api.histopathology_summary as string | undefined,
    fedBackToMdtCaseId: api.fed_back_to_mdt_case_id ? String(api.fed_back_to_mdt_case_id) : undefined,
    recommendedBy,
    createdAt: nowIso(),
  }
}

function consentFromApi(api: { id: number; consent_types: string[]; signatory: string; status: string; valid_from: string | null }, ext: Record<string, unknown> = {}): ConsentRecord {
  return {
    id: String(api.id),
    patientId: DEMO_PATIENT_ID,
    type: (api.consent_types[0] as ConsentRecord['type']) ?? 'general_treatment',
    status: (ext.status as ConsentStatus) ?? 'signed',
    relatedOrderId: ext.relatedOrderId as string | undefined,
    relatedRadiationPrescriptionId: ext.relatedRadiationPrescriptionId as string | undefined,
    relatedSurgicalPlanId: ext.relatedSurgicalPlanId as string | undefined,
    documentTitle: (ext.documentTitle as string) ?? api.consent_types.join(', '),
    discussedTopics: (ext.discussedTopics as string[]) ?? [],
    signedBy: api.signatory,
    witnessedBy: ext.witnessedBy as ActorRef | undefined,
    signedAt: api.valid_from ?? undefined,
    declinedReason: ext.declinedReason as string | undefined,
    createdAt: api.valid_from ?? nowIso(),
  }
}

// ─────────────────────────────────────────────────────────────────────────

type OncologyContextValue = {
  state: OncologyState
  ready: boolean
  backendError: string | null

  getRegimen: (id: string) => Regimen | undefined
  createRegimen: (input: Omit<Regimen, 'id' | 'status'>) => Promise<Regimen>

  createMdtCase: (input: Omit<MDTCase, 'id' | 'createdAt' | 'status' | 'linkedPlanIds'>) => Promise<MDTCase>
  approveMdtRecommendation: (mdtCaseId: string, actor: ActorRef) => Promise<void>
  createPlanFromMdt: (
    mdtCaseId: string,
    specialty: 'medical_oncology' | 'radiation_oncology' | 'surgical' | 'combined',
    actor: ActorRef
  ) => Promise<TreatmentPlan>

  getCarePlan: (patientId: string) => CarePlan | undefined
  getTreatmentPlan: (patientId: string) => TreatmentPlan | undefined
  amendTreatmentPlan: (planId: string, changes: Partial<TreatmentPlan>, reason: string, actor: ActorRef) => Promise<TreatmentPlan>

  getOrdersForPatient: (patientId: string) => TreatmentOrder[]
  createTreatmentOrder: (input: Omit<TreatmentOrder, 'id' | 'createdAt' | 'status'>) => Promise<TreatmentOrder>
  authorizeOrder: (orderId: string, actor: ActorRef) => Promise<TreatmentOrder>
  transitionOrder: (orderId: string, to: TreatmentStatus, actor: ActorRef, reason?: string) => Promise<{ ok: true; order: TreatmentOrder } | { ok: false; error: string }>
  recordVerification: (orderId: string, checklist: Omit<VerificationCheckpoint, 'id' | 'orderId' | 'verifiedAt'>) => Promise<VerificationCheckpoint>
  recordDoseModification: (orderId: string, drugLineId: string, modification: Omit<DoseModification, 'id' | 'timestamp'>) => Promise<DoseModification>

  createDispenseRecord: (input: Omit<DispenseRecord, 'id'>) => Promise<DispenseRecord>
  updateDispenseRecord: (id: string, changes: Partial<DispenseRecord>, actor: ActorRef, reason?: string) => Promise<DispenseRecord | undefined>

  recordPreAdministrationChecklist: (checklist: Omit<PreAdministrationChecklist, 'confirmedAt'>) => Promise<PreAdministrationChecklist>
  recordAdministration: (entry: Omit<MARDrugAdministration, 'id'>) => Promise<MARDrugAdministration>
  updateAdministration: (id: string, changes: Partial<MARDrugAdministration>) => Promise<MARDrugAdministration | undefined>
  recordPostAdministration: (record: Omit<PostAdministrationRecord, 'recordedAt'>) => PostAdministrationRecord

  recordToxicityEvent: (event: Omit<ToxicityEvent, 'id' | 'recordedAt'>) => Promise<ToxicityEvent>
  recordResponseAssessment: (assessment: Omit<ResponseAssessment, 'id'>) => Promise<ResponseAssessment>

  createRadiationPrescription: (input: Omit<RadiationPrescription, 'id' | 'createdAt' | 'status' | 'rtSubStatus'>) => Promise<RadiationPrescription>
  transitionRadiationSubStatus: (prescriptionId: string, to: RtSubStatus, actor: ActorRef) => Promise<{ ok: true } | { ok: false; error: string }>
  scheduleFractions: (prescriptionId: string, count: number, startDate: string) => Promise<RadiationFraction[]>
  recordFractionOutcome: (fractionId: string, changes: Partial<RadiationFraction>) => Promise<RadiationFraction | undefined>

  createSurgicalPlan: (input: Omit<SurgicalPlan, 'id' | 'createdAt' | 'status' | 'surgicalSubStatus' | 'histopathologyAvailable'>) => Promise<SurgicalPlan>
  transitionSurgicalSubStatus: (planId: string, to: SurgicalSubStatus, actor: ActorRef) => Promise<{ ok: true } | { ok: false; error: string }>
  recordOperativeOutcome: (planId: string, changes: Partial<SurgicalPlan>, actor: ActorRef) => Promise<SurgicalPlan | undefined>

  getJourney: (patientId: string) => JourneyMilestone[]

  getReadinessForPatient: (patientId: string) => TreatmentReadinessAssessment[]
  recordTreatmentReadiness: (input: Omit<TreatmentReadinessAssessment, 'id' | 'createdAt'>) => TreatmentReadinessAssessment

  getConsentsForPatient: (patientId: string) => ConsentRecord[]
  recordConsent: (input: Omit<ConsentRecord, 'id' | 'createdAt'>) => Promise<ConsentRecord>
  updateConsentStatus: (id: string, status: ConsentStatus, actor: ActorRef, changes?: Partial<ConsentRecord>) => Promise<ConsentRecord | undefined>

  getAuditTrail: (entityType?: string, entityId?: string) => AuditEntry[]
}

const OncologyContext = React.createContext<OncologyContextValue | null>(null)

export function OncologyProvider({ children }: { children: React.ReactNode }) {
  const { role } = useDemoAccess()
  const roleId = role.roleId as RoleId

  const [backendState, setBackendState] = React.useState<Omit<OncologyState, keyof LocalState>>(emptyBackendState)
  const [localState, setLocalState] = React.useState<LocalState>(seedLocalState)
  const [ready, setReady] = React.useState(false)
  const [backendError, setBackendError] = React.useState<string | null>(null)
  const backendPatientIdRef = React.useRef<number | null>(null)
  const mdtDecisionIdRef = React.useRef<Map<string, number>>(new Map())
  const rawOrdersRef = React.useRef<Map<string, ApiTreatmentOrder>>(new Map())

  const persistLocal = React.useCallback((next: LocalState) => {
    setLocalState(next)
    window.localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(next))
  }, [])

  /** Appends to the client-side audit log (this app's UI-facing history view — the
   * backend keeps its own independent audit trail via log_audit/DomainEvent for its own
   * purposes). Called after a backend write is already confirmed, never before. */
  const appendAudit = React.useCallback((entry: Omit<AuditEntry, 'id' | 'timestamp'>) => {
    setLocalState((current) => {
      const full: AuditEntry = { ...entry, id: nextId('audit'), timestamp: nowIso() }
      const next = { ...current, auditLog: [full, ...current.auditLog] }
      window.localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  React.useEffect(() => {
    const saved = window.localStorage.getItem(LOCAL_STORAGE_KEY)
    if (saved) {
      try {
        setLocalState({ ...seedLocalState(), ...(JSON.parse(saved) as Partial<LocalState>) })
      } catch {
        // Corrupt local state — fall back to the seed rather than crash the app.
      }
    }
  }, [])

  const loadAll = React.useCallback(async () => {
    if (!ROLE_TO_BACKEND[roleId]) {
      // This role has no oncology backend identity (e.g. Lab, Radiologist, Finance) —
      // nothing to fetch; leave the cache empty rather than error.
      setReady(true)
      return
    }
    try {
      setBackendError(null)
      const { patient } = await fetchDemoPatient(roleId)
      const patientId = patient.id
      backendPatientIdRef.current = patientId

      const [mdtRes, plansRes, ordersRes, toxRes, respRes, consentsRes, rtRes, surgRes, regimensRes, summaryRes, stagingRes, resultsRes, domainEventsRes] = await Promise.all([
        apiListMdtCases(roleId, patientId),
        apiGetPatientTreatmentPlans(roleId, patientId),
        apiGetPatientTreatmentOrders(roleId, patientId),
        apiListToxicityEvents(roleId, patientId),
        apiListResponseAssessments(roleId, patientId),
        apiListConsents(roleId, patientId),
        apiListRadiationPrescriptions(roleId, patientId),
        apiListSurgicalPlans(roleId, patientId),
        apiListRegimens(roleId),
        // Cross-domain, supplementary to Patient Summary only — a failure here (e.g. the
        // demo patient has no general-CCA intake yet) must not break the oncology load.
        apiGetGeneralPatientSummary(roleId, patientId).catch(() => ({ blocks: [] })),
        apiGetStagingHistory(roleId, patientId).catch(() => ({ history: [] })),
        apiListPatientResults(roleId, patientId).catch(() => ({ results: [] })),
        apiListDomainEvents(roleId, patientId).catch(() => ({ domain_events: [] })),
      ])

      const comorbidityBlock = summaryRes.blocks.find((b) => b.key === 'comorbidities_meds')
      const latestStaging = [...stagingRes.history].filter((h) => h.stage_value).pop()
      const stagingDetail = latestStaging ? {
        stageValue: latestStaging.stage_value ?? '',
        systemLabel: [latestStaging.staging_system, latestStaging.system_version].filter(Boolean).join(' '),
        confirmedAt: latestStaging.confirmed_at ?? '',
      } : null
      const recentImaging = resultsRes.results
        .filter((r) => r.result_type === 'IMAGING')
        .slice(0, 5)
        .map((r) => ({ id: String(r.id), title: r.title, status: r.status, resultedAt: r.resulted_at ?? '' }))

      const mdtCases: MDTCase[] = await Promise.all(mdtRes.mdt_cases.map(async (c) => {
        const ext = await apiGetExtension(roleId, 'cca_mdt_cases', c.id)
        const payload = (ext.extension.payload ?? {}) as Record<string, unknown>
        if (typeof payload.decisionId === 'number') mdtDecisionIdRef.current.set(String(c.id), payload.decisionId)
        return mdtCaseFromApi(c, payload)
      }))

      const treatmentPlans: TreatmentPlan[] = await Promise.all(plansRes.treatment_plans.map(async (p) => {
        const phasesRes = await apiListTreatmentPlanPhases(roleId, p.id)
        return treatmentPlanFromApi(p, phasesRes.phases)
      }))

      const treatmentOrders: TreatmentOrder[] = []
      const dispenseRecords: DispenseRecord[] = []
      for (const o of ordersRes.treatment_orders) {
        rawOrdersRef.current.set(String(o.id), o)
        const readinessRes = await apiGetPharmacyReadiness(roleId, o.id, patientId).catch(() => ({ pharmacy_readiness: null, valid_statuses: [] }))
        const readiness = readinessRes.pharmacy_readiness
        const order = treatmentOrderFromApi(o, readiness ? (readiness.status as string) : null)
        treatmentOrders.push(order)
        const dispense = dispenseRecordFromReadiness(order.id, readiness, order.drugLines[0]?.id ?? '')
        if (dispense) dispenseRecords.push(dispense)
      }

      const carePlans: CarePlan[] = treatmentPlans.map((tp) => synthesizeCarePlan(tp, mdtCases.find((m) => m.id === tp.mdtCaseId)))

      setBackendState({
        regimens: regimensRes.regimens.map(regimenFromApi),
        mdtCases, carePlans, treatmentPlans, treatmentOrders,
        verifications: [],
        dispenseRecords,
        marEntries: [],
        postAdministrationRecords: [],
        preAdministrationChecklists: [],
        toxicityEvents: toxRes.toxicity_events.map((t) => toxicityFromApi(t)),
        responseAssessments: respRes.response_assessments.map((r) => responseFromApi(r)),
        radiationPrescriptions: rtRes.radiation_prescriptions.map((r) => radiationPrescriptionFromApi(r)),
        radiationFractions: [],
        surgicalPlans: surgRes.surgical_plans.map((p) => surgicalPlanFromApi(p)),
        consentRecords: consentsRes.consents.map((c) => consentFromApi(c)),
        comorbiditiesSummary: comorbidityBlock?.value ?? '',
        stagingDetail, recentImaging,
        domainEvents: domainEventsRes.domain_events,
      })
      setReady(true)
    } catch (error) {
      setBackendError(error instanceof BackendError ? error.message : 'Could not load treatment data from the backend.')
      setReady(true)
    }
  }, [roleId])

  React.useEffect(() => { void loadAll() }, [loadAll])

  function requirePatientId() {
    if (backendPatientIdRef.current === null) throw new Error('Backend patient has not resolved yet')
    return backendPatientIdRef.current
  }

  // ── Regimen library ──
  const getRegimen = React.useCallback((id: string) => backendState.regimens.find((r) => r.id === id), [backendState.regimens])
  const createRegimen = React.useCallback(async (input: Omit<Regimen, 'id' | 'status'>) => {
    const res = await apiCreateRegimen(roleId, {
      name: input.name, cancer_indication: input.cancerIndication, intent_setting: input.intentSetting,
      schedule: input.scheduleDescription, number_of_cycles: input.plannedCycles,
      premedications: input.premedications.join('; '), hydration: input.hydration.join('; '),
      supportive_therapy: input.supportiveTherapy.join('; '), hold_parameters: input.treatmentHoldParameterReferences.join('; '),
      reference_notes: input.references.join('; '), version: String(input.version), effective_date: input.effectiveDate,
      approved_by: input.approvedBy.name,
      drug_lines: input.drugSequence.map((line) => ({
        sequence_number: line.sequence, generic_name: line.genericDrugName, dose_basis: line.doseBasisDescription, route: line.route,
      })),
    })
    const regimen = regimenFromApi(res.regimen)
    setBackendState((s0) => ({ ...s0, regimens: [regimen, ...s0.regimens] }))
    appendAudit({ entityType: 'Regimen', entityId: regimen.id, action: 'created', actor: input.approvedBy })
    return regimen
  }, [roleId, appendAudit])

  // ── MDT ──
  const createMdtCase = React.useCallback(async (input: Omit<MDTCase, 'id' | 'createdAt' | 'status' | 'linkedPlanIds'>) => {
    const patientId = requirePatientId()
    const caseRes = await apiCreateMdtCase(roleId, { patient_id: patientId, question: input.recommendation || input.cancerDiagnosis })
    const caseId = caseRes.mdt_case.id
    const decisionRes = await apiRecordMdtRecommendation(roleId, caseId, {
      recommendation: input.recommendation, modality_direction: input.specialtyResponsible, rationale: input.rationale,
      attendees: input.participants.map((p) => ({ name: p.name, role: p.roleLabel })),
    })
    mdtDecisionIdRef.current.set(String(caseId), decisionRes.decision.id)
    const extPayload = {
      cancerDiagnosis: input.cancerDiagnosis, stage: input.stage, performanceStatus: input.performanceStatus,
      pathologyBiomarkers: input.pathologyBiomarkers, alternativeOptionsDiscussed: input.alternativeOptionsDiscussed,
      finalConsensus: input.finalConsensus, outstandingInvestigations: input.outstandingInvestigations,
      mdtDate: input.mdtDate, treatmentIntent: input.treatmentIntent, participants: input.participants,
      recommendation: input.recommendation, rationale: input.rationale, specialtyResponsible: input.specialtyResponsible,
      proposedBy: input.proposedBy, decisionId: decisionRes.decision.id, createdAt: nowIso(),
    }
    await apiPutExtension(roleId, 'cca_mdt_cases', caseId, extPayload, patientId)
    const record = mdtCaseFromApi({ id: caseId, question: input.recommendation, status: 'DISCUSSED' }, extPayload, input.proposedBy)
    setBackendState((s0) => ({ ...s0, mdtCases: [record, ...s0.mdtCases] }))
    appendAudit({ entityType: 'MDTCase', entityId: record.id, action: 'created', actor: input.proposedBy })
    return record
  }, [roleId, appendAudit])

  const approveMdtRecommendation = React.useCallback(async (mdtCaseId: string, actor: ActorRef) => {
    const patientId = requirePatientId()
    await apiApproveMdtRecommendation(roleId, Number(mdtCaseId), { disposition: 'ACCEPT' })
    const existing = await apiGetExtension(roleId, 'cca_mdt_cases', Number(mdtCaseId))
    await apiPutExtension(roleId, 'cca_mdt_cases', Number(mdtCaseId), { ...(existing.extension.payload ?? {}), approvedBy: actor, approvedAt: nowIso() }, patientId)
    setBackendState((s0) => ({ ...s0, mdtCases: s0.mdtCases.map((c) => (c.id === mdtCaseId ? { ...c, status: 'recommendation_recorded' as const, approvedBy: actor, approvedAt: nowIso() } : c)) }))
    appendAudit({ entityType: 'MDTCase', entityId: mdtCaseId, action: 'recommendation_approved', actor })
  }, [roleId, appendAudit])

  const createPlanFromMdt = React.useCallback(async (
    mdtCaseId: string, specialty: 'medical_oncology' | 'radiation_oncology' | 'surgical' | 'combined', actor: ActorRef
  ) => {
    const mdtCase = backendState.mdtCases.find((c) => c.id === mdtCaseId)
    if (!mdtCase) throw new Error(`MDT case ${mdtCaseId} not found`)
    const patientId = requirePatientId()
    const modalityLabel = specialty === 'medical_oncology' ? 'Systemic Chemotherapy' : specialty === 'radiation_oncology' ? 'Radiation Therapy' : specialty === 'surgical' ? 'Surgical' : 'Combined Modality'
    const createRes = await apiCreateTreatmentPlan(roleId, {
      patient_id: patientId, mdt_decision_id: mdtDecisionIdRef.current.get(mdtCaseId), intent: mdtCase.treatmentIntent, modality: modalityLabel,
    })
    // Dashboard's own model treats an MDT-originated plan as immediately active (no
    // separate "sign the plan" step exists in this UI) — the backend requires DRAFT ->
    // ACTIVE via an explicit sign before any order can be created against it, so that
    // happens here, immediately, rather than leaving a plan permanently unsignable.
    const planRes = await apiSignTreatmentPlan(roleId, createRes.treatment_plan.id)
    const responsibleSpecialty = specialty === 'surgical' ? 'surgical_oncology' : specialty
    const plan = treatmentPlanFromApi(planRes.treatment_plan, [], {
      diagnosis: mdtCase.cancerDiagnosis, stage: mdtCase.stage, biomarkers: mdtCase.pathologyBiomarkers,
      currentDiseaseStatus: mdtCase.finalConsensus, responsibleSpecialty, mdtCaseId: mdtCase.id,
    })
    const linkKey = specialty === 'medical_oncology' ? 'medicalOncology' : specialty === 'radiation_oncology' ? 'radiationOncology' : specialty === 'surgical' ? 'surgical' : 'combined'
    setBackendState((s0) => ({
      ...s0,
      treatmentPlans: [plan, ...s0.treatmentPlans],
      carePlans: [synthesizeCarePlan(plan, mdtCase), ...s0.carePlans],
      mdtCases: s0.mdtCases.map((c) => (c.id === mdtCaseId ? { ...c, status: 'plan_created' as const, linkedPlanIds: { ...c.linkedPlanIds, [linkKey]: plan.id } } : c)),
    }))
    appendAudit({ entityType: 'TreatmentPlan', entityId: plan.id, action: `created_from_mdt:${specialty}`, actor, reason: `MDT case ${mdtCaseId}` })
    return plan
  }, [roleId, backendState.mdtCases, appendAudit])

  // ── Care Plan / Treatment Plan ──
  const getCarePlan = React.useCallback((patientId: string) => backendState.carePlans.find((c) => c.patientId === patientId && c.status === 'active'), [backendState.carePlans])
  const getTreatmentPlan = React.useCallback((patientId: string) => backendState.treatmentPlans.find((p) => p.patientId === patientId && p.status === 'active'), [backendState.treatmentPlans])

  const amendTreatmentPlan = React.useCallback(async (planId: string, changes: Partial<TreatmentPlan>, reason: string, actor: ActorRef) => {
    const current = backendState.treatmentPlans.find((p) => p.id === planId)
    if (!current) throw new Error(`Treatment plan ${planId} not found`)
    // Backend versions a Treatment Plan in place (same id, version_no bumped) rather than
    // dashboard's original supersede-with-a-new-id pattern — see status-map.ts's header
    // for why the two lifecycles differ. The id intentionally stays the same here.
    const res = await apiUpdateTreatmentPlan(roleId, Number(planId), {
      change_reason: reason, intent: changes.intent, protocol_name: changes.diagnosis ?? undefined,
    })
    if (changes.phases) {
      await apiReplaceTreatmentPlanPhases(roleId, Number(planId), changes.phases.map((phase) => ({
        sequence: phase.sequence, modality: phase.modality, label: phase.label,
        regimen_or_procedure_ref: phase.regimenOrProcedureRef, planned_start: phase.plannedStart,
        duration_description: phase.durationDescription, status: phase.status,
        responsible_clinician_name: phase.responsibleClinician?.name, responsible_clinician_role: phase.responsibleClinician?.roleLabel,
      })))
    }
    const phasesRes = await apiListTreatmentPlanPhases(roleId, Number(planId))
    const amended = treatmentPlanFromApi(res.treatment_plan, phasesRes.phases, { ...current, ...changes })
    setBackendState((s0) => ({ ...s0, treatmentPlans: s0.treatmentPlans.map((p) => (p.id === planId ? amended : p)) }))
    appendAudit({ entityType: 'TreatmentPlan', entityId: amended.id, action: 'amended', actor, reason, previousValue: String(current.version), newValue: String(amended.version) })
    return amended
  }, [roleId, backendState.treatmentPlans, appendAudit])

  // ── Treatment Order ──
  const getOrdersForPatient = React.useCallback((patientId: string) => backendState.treatmentOrders.filter((o) => o.patientId === patientId), [backendState.treatmentOrders])

  const createTreatmentOrder = React.useCallback(async (input: Omit<TreatmentOrder, 'id' | 'createdAt' | 'status'>) => {
    const patientId = requirePatientId()
    const instructions = {
      regimenId: input.regimenId, regimenName: input.regimenName, diagnosis: input.diagnosis,
      treatmentIntent: input.treatmentIntent, lineOfTherapy: input.lineOfTherapy, cycleNumber: input.cycleNumber,
      day: input.day, plannedNumberOfCycles: input.plannedNumberOfCycles, protocolReferenceVersion: input.protocolReferenceVersion,
      drugLines: input.drugLines, eligibilityParametersChecked: input.eligibilityParametersChecked,
      heightCm: input.heightCm, weightKg: input.weightKg, bsaM2: input.bsaM2, allergiesAcknowledged: input.allergiesAcknowledged,
      orderingClinician: input.orderingClinician,
    }
    const res = await apiCreateTreatmentOrder(roleId, { patient_id: patientId, treatment_plan_id: Number(input.treatmentPlanId), instructions })
    rawOrdersRef.current.set(String(res.treatment_order.id), res.treatment_order)
    const order = treatmentOrderFromApi(res.treatment_order, null)
    setBackendState((s0) => ({ ...s0, treatmentOrders: [order, ...s0.treatmentOrders] }))
    appendAudit({ entityType: 'TreatmentOrder', entityId: order.id, action: 'created', actor: input.orderingClinician })
    return order
  }, [roleId, appendAudit])

  const authorizeOrder = React.useCallback(async (orderId: string, actor: ActorRef) => {
    const order = backendState.treatmentOrders.find((o) => o.id === orderId)
    if (!order) throw new Error(`Order ${orderId} not found`)
    const res = await apiSignTreatmentOrder(roleId, Number(orderId))
    rawOrdersRef.current.set(orderId, res.treatment_order)
    const authorized = treatmentOrderFromApi(res.treatment_order, null, [], order)
    setBackendState((s0) => ({ ...s0, treatmentOrders: s0.treatmentOrders.map((o) => (o.id === orderId ? authorized : o)) }))
    appendAudit({ entityType: 'TreatmentOrder', entityId: orderId, action: 'authorized', actor, previousValue: order.status, newValue: authorized.status })
    return authorized
  }, [roleId, backendState.treatmentOrders, appendAudit])

  const transitionOrder = React.useCallback(async (orderId: string, to: TreatmentStatus, actor: ActorRef, reason?: string) => {
    const order = backendState.treatmentOrders.find((o) => o.id === orderId)
    if (!order) return { ok: false as const, error: `Order ${orderId} not found` }
    if (isCancelledOrderAdministrationAttempt(order.status) && to === 'in_progress') return { ok: false as const, error: 'This order is cancelled and cannot be administered.' }
    if (isUnverifiedDispenseAttempt(order.status) && to === 'preparation_pending') return { ok: false as const, error: 'This order has not completed pharmacy verification and cannot move to preparation.' }
    try {
      assertTreatmentStatusTransition(order.status, to)
    } catch (error) {
      return { ok: false as const, error: error instanceof Error ? error.message : 'Illegal transition' }
    }

    try {
      const patientId = requirePatientId()
      const rawOrder = rawOrdersRef.current.get(orderId)
      let updated: TreatmentOrder
      if (to === 'cancelled') {
        const res = await apiCancelTreatmentOrder(roleId, Number(orderId), reason ?? 'Cancelled')
        rawOrdersRef.current.set(orderId, res.treatment_order)
        updated = treatmentOrderFromApi(res.treatment_order, null, [], order)
      } else {
        const pharmacyStatus = pharmacyStatusForTarget(to)
        if (pharmacyStatus && rawOrder) {
          const res = await apiPostPharmacyReadiness(roleId, { patient_id: patientId, order_id: Number(orderId), status: pharmacyStatus })
          updated = treatmentOrderFromApi(rawOrder, (res.pharmacy_readiness as { status: string }).status, [], order)
        } else {
          // 'ordered' / 'in_progress' / 'administered' / 'completed' / 'held' / 'delayed' —
          // no dedicated backend order-status call for these; they're either the natural
          // resting composed status (status-map.ts) or driven by recordAdministration /
          // recordPostAdministration elsewhere. Applied as a local display override so the
          // clicked action is reflected immediately.
          updated = { ...order, status: to }
        }
      }
      setBackendState((s0) => ({ ...s0, treatmentOrders: s0.treatmentOrders.map((o) => (o.id === orderId ? updated : o)) }))
      appendAudit({ entityType: 'TreatmentOrder', entityId: orderId, action: 'status_change', actor, reason, previousValue: order.status, newValue: updated.status })
      return { ok: true as const, order: updated }
    } catch (error) {
      return { ok: false as const, error: error instanceof BackendError ? error.message : 'Could not update the order' }
    }
  }, [roleId, backendState.treatmentOrders, appendAudit])

  const recordVerification = React.useCallback(async (orderId: string, checklist: Omit<VerificationCheckpoint, 'id' | 'orderId' | 'verifiedAt'>) => {
    const patientId = requirePatientId()
    const record: VerificationCheckpoint = { ...checklist, id: nextId('verification'), orderId, verifiedAt: nowIso() }
    if (checklist.outcome === 'verified') {
      const res = await apiPostPharmacyReadiness(roleId, {
        patient_id: patientId, order_id: Number(orderId), status: 'Verified',
        product_verified: checklist.drugConfirmed, expiry_checked: checklist.expiryChecked,
      })
      const readinessId = (res.pharmacy_readiness as { id: number }).id
      await apiPutExtension(roleId, 'cca_pharmacy_readiness', readinessId, { checklist }, patientId)
    }
    setBackendState((s0) => ({ ...s0, verifications: [record, ...s0.verifications] }))
    appendAudit({ entityType: 'TreatmentOrder', entityId: orderId, action: `verification:${checklist.outcome}`, actor: checklist.verifiedBy, reason: checklist.queryReason })
    return record
  }, [roleId, appendAudit])

  const recordDoseModification = React.useCallback(async (orderId: string, drugLineId: string, modification: Omit<DoseModification, 'id' | 'timestamp'>) => {
    const patientId = requirePatientId()
    const record: DoseModification = { ...modification, id: nextId('dose-mod'), timestamp: nowIso() }
    setBackendState((s0) => ({
      ...s0,
      treatmentOrders: s0.treatmentOrders.map((o) =>
        o.id === orderId
          ? { ...o, drugLines: o.drugLines.map((line) => (line.id === drugLineId ? { ...line, doseModifications: [record, ...line.doseModifications] } : line)) }
          : o
      ),
    }))
    const existing = await apiGetExtension(roleId, 'cca_treatment_orders', Number(orderId))
    const priorMods = ((existing.extension.payload ?? {}) as Record<string, unknown>).doseModifications as DoseModification[] | undefined
    await apiPutExtension(roleId, 'cca_treatment_orders', Number(orderId), { doseModifications: [record, ...(priorMods ?? [])] }, patientId)
    appendAudit({ entityType: 'OrderedDrugLine', entityId: drugLineId, action: `dose_modification:${modification.type}`, actor: modification.approvedBy, reason: modification.reason, previousValue: modification.originalDose, newValue: modification.modifiedDose })
    return record
  }, [roleId, appendAudit])

  // ── Pharmacy ── (one DispenseRecord per order — see header note on backend cardinality)
  const createDispenseRecord = React.useCallback(async (input: Omit<DispenseRecord, 'id'>) => {
    const record: DispenseRecord = { ...input, id: nextId('dispense') }
    setBackendState((s0) => ({ ...s0, dispenseRecords: [record, ...s0.dispenseRecords] }))
    return record
  }, [])

  const updateDispenseRecord = React.useCallback(async (id: string, changes: Partial<DispenseRecord>, actor: ActorRef, reason?: string) => {
    const current = backendState.dispenseRecords.find((d) => d.id === id)
    if (!current) return undefined
    const updated = { ...current, ...changes }
    const patientId = requirePatientId()
    const backendStatusMap: Partial<Record<DispenseRecord['status'], string>> = {
      verified: 'Verified', preparation: 'Preparing', prepared: 'Ready', dispensed: 'Dispensed',
    }
    const backendStatus = updated.status ? backendStatusMap[updated.status] : undefined
    if (backendStatus && updated.orderId) {
      const res = await apiPostPharmacyReadiness(roleId, {
        patient_id: patientId, order_id: Number(updated.orderId), status: backendStatus, notes: updated.holdOrQueryReason,
        second_checker_name: updated.wastageRecorded ? updated.wastageRecorded.recordedBy.name : undefined,
      })
      const readinessId = (res.pharmacy_readiness as { id: number }).id
      await apiPutExtension(roleId, 'cca_pharmacy_readiness', readinessId, { dispenseRecord: updated }, patientId)
    }
    setBackendState((s0) => ({ ...s0, dispenseRecords: s0.dispenseRecords.map((d) => (d.id === id ? updated : d)) }))
    appendAudit({ entityType: 'DispenseRecord', entityId: id, action: `status:${updated.status}`, actor, reason, previousValue: current.status, newValue: updated.status })
    return updated
  }, [roleId, backendState.dispenseRecords, appendAudit])

  // ── Day Care / MAR ──
  // Persisted as an OncologyRecordExtension on the order itself (same mechanism and same
  // per-order extension row as recordDoseModification below), not localStorage — a
  // pre-administration checklist is a safety record and must survive a reload / a
  // different device signing in as the same nurse, not live only in one browser tab.
  const recordPreAdministrationChecklist = React.useCallback(async (checklist: Omit<PreAdministrationChecklist, 'confirmedAt'>) => {
    const patientId = requirePatientId()
    const record: PreAdministrationChecklist = { ...checklist, confirmedAt: nowIso() }
    const existing = await apiGetExtension(roleId, 'cca_treatment_orders', Number(checklist.orderId))
    const priorPayload = (existing.extension.payload ?? {}) as Record<string, unknown>
    await apiPutExtension(roleId, 'cca_treatment_orders', Number(checklist.orderId), { ...priorPayload, preAdministrationChecklist: record }, patientId)
    setBackendState((s0) => ({ ...s0, preAdministrationChecklists: [record, ...s0.preAdministrationChecklists.filter((c) => c.orderId !== record.orderId)] }))
    appendAudit({ entityType: 'TreatmentOrder', entityId: checklist.orderId, action: 'pre_administration_checklist_confirmed', actor: checklist.confirmedBy })
    return record
  }, [roleId, appendAudit])

  const recordAdministration = React.useCallback(async (entry: Omit<MARDrugAdministration, 'id'>) => {
    const patientId = requirePatientId()
    const medRes = await apiPostMedication(roleId, {
      patient_id: patientId, order_id: Number(entry.orderId), medication_name: entry.drug, dose: entry.doseGiven,
      route: entry.route, sequence_no: entry.sequence,
    })
    const record: MARDrugAdministration = { ...entry, id: String(medRes.medication.id) }
    setBackendState((s0) => ({ ...s0, marEntries: [record, ...s0.marEntries] }))
    appendAudit({ entityType: 'TreatmentOrder', entityId: entry.orderId, action: `administration:${entry.drug}:${entry.infusionStatus}`, actor: entry.administeredBy })
    return record
  }, [roleId, appendAudit])

  const updateAdministration = React.useCallback(async (id: string, changes: Partial<MARDrugAdministration>) => {
    const current = backendState.marEntries.find((m) => m.id === id)
    if (!current) return undefined
    const updated = { ...current, ...changes }
    if (changes.infusionStatus) {
      const eventTypeMap: Partial<Record<MARDrugAdministration['infusionStatus'], 'START' | 'PAUSE' | 'COMPLETE' | 'STOP'>> = {
        in_progress: 'START', paused: 'PAUSE', completed: 'COMPLETE', discontinued: 'STOP', held: 'STOP',
      }
      const eventType = eventTypeMap[changes.infusionStatus]
      if (eventType) {
        await apiRecordMedicationEvent(roleId, Number(id), {
          event_type: eventType, notes: changes.reactionOrToxicity,
          omission_reason: eventType === 'STOP' ? (changes.varianceFromOrder ?? 'Discontinued') : undefined,
        })
      }
    }
    setBackendState((s0) => ({ ...s0, marEntries: s0.marEntries.map((m) => (m.id === id ? updated : m)) }))
    appendAudit({ entityType: 'TreatmentOrder', entityId: current.orderId, action: `administration_update:${updated.drug}:${updated.infusionStatus}`, actor: current.administeredBy })
    return updated
  }, [roleId, backendState.marEntries, appendAudit])

  const recordPostAdministration = React.useCallback((record: Omit<PostAdministrationRecord, 'recordedAt'>) => {
    const full: PostAdministrationRecord = { ...record, recordedAt: nowIso() }
    setBackendState((s0) => ({ ...s0, postAdministrationRecords: [full, ...s0.postAdministrationRecords] }))
    appendAudit({ entityType: 'TreatmentOrder', entityId: record.orderId, action: `completion:${record.completionStatus}`, actor: record.recordedBy })
    return full
  }, [appendAudit])

  // ── Toxicity / Response ──
  const recordToxicityEvent = React.useCallback(async (event: Omit<ToxicityEvent, 'id' | 'recordedAt'>) => {
    const patientId = requirePatientId()
    const res = await apiRecordToxicity(roleId, { patient_id: patientId, term: event.term.display, grade: event.grade, baseline_value: 'Grade 0 (Baseline)' })
    await apiPutExtension(roleId, 'cca_toxicity_events', res.toxicity.id, {
      relationshipToTherapy: event.relationshipToTherapy, outcome: event.outcome, intervention: event.intervention,
      treatmentModificationId: event.treatmentModificationId, orderId: event.orderId,
    }, patientId)
    const record: ToxicityEvent = { ...event, id: String(res.toxicity.id), recordedAt: nowIso() }
    setBackendState((s0) => ({ ...s0, toxicityEvents: [record, ...s0.toxicityEvents] }))
    appendAudit({ entityType: 'ToxicityEvent', entityId: record.id, action: `recorded:grade_${event.grade}`, actor: event.recordedBy })
    return record
  }, [roleId, appendAudit])

  const recordResponseAssessment = React.useCallback(async (assessment: Omit<ResponseAssessment, 'id'>) => {
    const patientId = requirePatientId()
    const res = await apiRecordResponseAssessment(roleId, {
      patient_id: patientId, response_category: assessment.responseCategory === 'not_evaluable' ? 'NE' : assessment.responseCategory,
      lesions: assessment.lesions, imaging_reference: assessment.imagingDate,
    })
    await apiPutExtension(roleId, 'cca_response_assessments', res.response.id, { diseaseStatus: assessment.diseaseStatus, relevantBiomarkers: assessment.relevantBiomarkers }, patientId)
    const record: ResponseAssessment = { ...assessment, id: String(res.response.id) }
    setBackendState((s0) => ({ ...s0, responseAssessments: [record, ...s0.responseAssessments] }))
    appendAudit({ entityType: 'ResponseAssessment', entityId: record.id, action: `recorded:${assessment.responseCategory}`, actor: assessment.assessedBy })
    return record
  }, [roleId, appendAudit])

  // ── Radiation ──
  const createRadiationPrescription = React.useCallback(async (input: Omit<RadiationPrescription, 'id' | 'createdAt' | 'status' | 'rtSubStatus'>) => {
    const patientId = requirePatientId()
    const res = await apiCreateRadiationPrescription(roleId, {
      patient_id: patientId, treatment_site: input.treatmentSite, laterality: input.laterality, diagnosis: input.diagnosis,
      intent: input.intent, modality: input.modality, technique: input.technique, treatment_phase: input.treatmentPhase,
      total_prescribed_dose_gy: Number(input.totalPrescribedDoseGy), dose_per_fraction_gy: Number(input.dosePerFractionGy),
      number_of_fractions: input.numberOfFractions, frequency: input.frequency, start_date: input.startDate,
      target_volumes: input.targetVolumes, organs_at_risk: input.organsAtRisk, simulation_required: input.simulationRequired,
      immobilization: input.immobilization, image_guidance_required: input.imageGuidanceRequired, bolus: input.bolus,
      special_instructions: input.specialInstructions, dicom_rt_plan_ref: input.dicomRtPlanRef,
    })
    const record = radiationPrescriptionFromApi(res.radiation_prescription, input.treatmentPlanId, input.createdBy)
    setBackendState((s0) => ({ ...s0, radiationPrescriptions: [record, ...s0.radiationPrescriptions] }))
    appendAudit({ entityType: 'RadiationPrescription', entityId: record.id, action: 'created', actor: input.createdBy })
    return record
  }, [roleId, appendAudit])

  const transitionRadiationSubStatus = React.useCallback(async (prescriptionId: string, to: RtSubStatus, actor: ActorRef) => {
    const rx = backendState.radiationPrescriptions.find((r) => r.id === prescriptionId)
    if (!rx) return { ok: false as const, error: `Radiation prescription ${prescriptionId} not found` }
    try {
      assertRtSubStatusTransition(rx.rtSubStatus, to)
    } catch (error) {
      return { ok: false as const, error: error instanceof Error ? error.message : 'Illegal transition' }
    }
    try {
      const res = await apiTransitionRadiationPrescription(roleId, Number(prescriptionId), to)
      const updated = radiationPrescriptionFromApi(res.radiation_prescription, rx.treatmentPlanId, rx.createdBy, to === 'physician_approved' ? actor : rx.physicianApprovedBy)
      setBackendState((s0) => ({ ...s0, radiationPrescriptions: s0.radiationPrescriptions.map((r) => (r.id === prescriptionId ? updated : r)) }))
      appendAudit({ entityType: 'RadiationPrescription', entityId: prescriptionId, action: 'status_change', actor, previousValue: rx.rtSubStatus, newValue: to })
      return { ok: true as const }
    } catch (error) {
      return { ok: false as const, error: error instanceof BackendError ? error.message : 'Could not update the prescription' }
    }
  }, [roleId, backendState.radiationPrescriptions, appendAudit])

  const scheduleFractions = React.useCallback(async (prescriptionId: string) => {
    // Fraction rows are created server-side automatically on the transition into
    // treatment_ready (cca_oncology_ext.py) — this refetches rather than keeping a
    // second, divergent local copy.
    const res = await apiListRadiationFractions(roleId, Number(prescriptionId))
    const fractions = res.fractions.map((f) => radiationFractionFromApi(f, prescriptionId))
    setBackendState((s0) => ({ ...s0, radiationFractions: [...s0.radiationFractions.filter((f) => f.prescriptionId !== prescriptionId), ...fractions] }))
    return fractions
  }, [roleId])

  const recordFractionOutcome = React.useCallback(async (fractionId: string, changes: Partial<RadiationFraction>) => {
    const current = backendState.radiationFractions.find((f) => f.id === fractionId)
    if (!current || !changes.status) return current
    const res = await apiRecordFractionEvent(roleId, Number(fractionId), {
      status: changes.status as 'delivered' | 'missed' | 'rescheduled', delivered_dose_gy: changes.deliveredDoseGy ? Number(changes.deliveredDoseGy) : undefined,
      interruption_reason: changes.interruptionReason, on_treatment_review_note: changes.onTreatmentReviewNote,
    })
    const updated = radiationFractionFromApi(res.fraction as Record<string, unknown> & { id: number; fraction_number: number; status: string }, current.prescriptionId)
    setBackendState((s0) => ({ ...s0, radiationFractions: s0.radiationFractions.map((f) => (f.id === fractionId ? updated : f)) }))
    return updated
  }, [roleId, backendState.radiationFractions])

  // ── Surgical ──
  const createSurgicalPlan = React.useCallback(async (input: Omit<SurgicalPlan, 'id' | 'createdAt' | 'status' | 'surgicalSubStatus' | 'histopathologyAvailable'>) => {
    const patientId = requirePatientId()
    const res = await apiCreateSurgicalPlan(roleId, {
      patient_id: patientId, procedure: input.procedure, indication: input.indication, intent: input.intent,
      anatomical_site: input.anatomicalSite, laterality: input.laterality, proposed_extent: input.proposedExtent,
      approach: input.approach, nodal_procedure: input.nodalProcedure, reconstruction: input.reconstruction,
      planned_date: input.plannedDate, priority: input.priority, pre_op_requirements: input.preOpRequirements.join('; '),
      required_imaging_pathology: input.requiredImagingPathology.join('; '), anaesthesia_clearance: input.anaesthesiaClearance,
      blood_requirement: input.bloodRequirement, special_instructions: input.specialInstructions,
    })
    const record = surgicalPlanFromApi(res.surgical_plan, input.treatmentPlanId, input.recommendedBy)
    setBackendState((s0) => ({ ...s0, surgicalPlans: [record, ...s0.surgicalPlans] }))
    appendAudit({ entityType: 'SurgicalPlan', entityId: record.id, action: 'created', actor: input.recommendedBy })
    return record
  }, [roleId, appendAudit])

  const transitionSurgicalSubStatus = React.useCallback(async (planId: string, to: SurgicalSubStatus, actor: ActorRef) => {
    const plan = backendState.surgicalPlans.find((p) => p.id === planId)
    if (!plan) return { ok: false as const, error: `Surgical plan ${planId} not found` }
    try {
      assertSurgicalSubStatusTransition(plan.surgicalSubStatus, to)
    } catch (error) {
      return { ok: false as const, error: error instanceof Error ? error.message : 'Illegal transition' }
    }
    try {
      const res = await apiTransitionSurgicalPlan(roleId, Number(planId), to)
      const updated = surgicalPlanFromApi(res.surgical_plan, plan.treatmentPlanId, plan.recommendedBy, plan.operativeFindings)
      setBackendState((s0) => ({ ...s0, surgicalPlans: s0.surgicalPlans.map((p) => (p.id === planId ? updated : p)) }))
      appendAudit({ entityType: 'SurgicalPlan', entityId: planId, action: 'status_change', actor, previousValue: plan.surgicalSubStatus, newValue: to })
      return { ok: true as const }
    } catch (error) {
      return { ok: false as const, error: error instanceof BackendError ? error.message : 'Could not update the plan' }
    }
  }, [roleId, backendState.surgicalPlans, appendAudit])

  const recordOperativeOutcome = React.useCallback(async (planId: string, changes: Partial<SurgicalPlan>, actor: ActorRef) => {
    const current = backendState.surgicalPlans.find((p) => p.id === planId)
    if (!current || !changes.performedProcedure) return current
    const res = await apiRecordSurgicalOutcome(roleId, Number(planId), {
      performed_procedure: changes.performedProcedure, performed_date: changes.performedAt,
      histopathology_summary: changes.histopathologySummary, fed_back_to_mdt_case_id: changes.fedBackToMdtCaseId ? Number(changes.fedBackToMdtCaseId) : undefined,
    })
    const operativeFindings = changes.operativeFindings ?? current.operativeFindings
    const updated = surgicalPlanFromApi(res.surgical_plan, current.treatmentPlanId, current.recommendedBy, operativeFindings)
    setBackendState((s0) => ({ ...s0, surgicalPlans: s0.surgicalPlans.map((p) => (p.id === planId ? updated : p)) }))
    appendAudit({ entityType: 'SurgicalPlan', entityId: planId, action: 'operative_outcome_recorded', actor })
    return updated
  }, [roleId, backendState.surgicalPlans, appendAudit])

  // ── Journey (local — no backend entity for department-level milestones yet) ──
  const getJourney = React.useCallback((patientId: string) => localState.journeyMilestones.filter((m) => m.patientId === patientId), [localState.journeyMilestones])

  // ── Treatment Readiness (local — no backend entity yet) ──
  const getReadinessForPatient = React.useCallback((patientId: string) => localState.treatmentReadinessAssessments.filter((r) => r.patientId === patientId), [localState.treatmentReadinessAssessments])
  const recordTreatmentReadiness = React.useCallback((input: Omit<TreatmentReadinessAssessment, 'id' | 'createdAt'>) => {
    const record: TreatmentReadinessAssessment = { ...input, id: nextId('readiness'), createdAt: nowIso() }
    persistLocal({ ...localState, treatmentReadinessAssessments: [record, ...localState.treatmentReadinessAssessments] })
    appendAudit({ entityType: 'TreatmentReadinessAssessment', entityId: record.id, action: `decision:${input.decision}`, actor: input.assessedBy, reason: input.decisionReason })
    return record
  }, [localState, persistLocal, appendAudit])

  // ── Consent ──
  const getConsentsForPatient = React.useCallback((patientId: string) => backendState.consentRecords.filter((c) => c.patientId === patientId), [backendState.consentRecords])
  const recordConsent = React.useCallback(async (input: Omit<ConsentRecord, 'id' | 'createdAt'>) => {
    const patientId = requirePatientId()
    const res = await apiRecordConsent(roleId, patientId, { consent_types: [input.type], signatory: input.signedBy ?? input.witnessedBy?.name ?? 'Patient' })
    await apiPutExtension(roleId, 'cca_consents', res.consent.id, {
      documentTitle: input.documentTitle, discussedTopics: input.discussedTopics, relatedOrderId: input.relatedOrderId,
      relatedRadiationPrescriptionId: input.relatedRadiationPrescriptionId, relatedSurgicalPlanId: input.relatedSurgicalPlanId,
      status: input.status, witnessedBy: input.witnessedBy, declinedReason: input.declinedReason,
    }, patientId)
    const record: ConsentRecord = { ...input, id: String(res.consent.id), createdAt: nowIso() }
    setBackendState((s0) => ({ ...s0, consentRecords: [record, ...s0.consentRecords] }))
    appendAudit({ entityType: 'ConsentRecord', entityId: record.id, action: `status:${input.status}`, actor: input.witnessedBy ?? { userId: 'patient', name: input.signedBy ?? 'Patient', roleLabel: 'Patient' } })
    return record
  }, [roleId, appendAudit])

  const updateConsentStatus = React.useCallback(async (id: string, status: ConsentStatus, actor: ActorRef, changes?: Partial<ConsentRecord>) => {
    const current = backendState.consentRecords.find((c) => c.id === id)
    if (!current) return undefined
    const updated: ConsentRecord = { ...current, ...changes, status }
    const existing = await apiGetExtension(roleId, 'cca_consents', Number(id))
    await apiPutExtension(roleId, 'cca_consents', Number(id), { ...(existing.extension.payload ?? {}), status, signedAt: updated.signedAt, declinedReason: updated.declinedReason }, Number(current.patientId))
    setBackendState((s0) => ({ ...s0, consentRecords: s0.consentRecords.map((c) => (c.id === id ? updated : c)) }))
    appendAudit({ entityType: 'ConsentRecord', entityId: id, action: `status:${status}`, actor, previousValue: current.status, newValue: status })
    return updated
  }, [roleId, backendState.consentRecords, appendAudit])

  // Merges the real, durably-persisted event stream (backendState.domainEvents, fetched
  // once at load) with this session's own actions (localState.auditLog, appended as they
  // happen). The two are time-disjoint — domainEvents is everything up to page load,
  // auditLog is everything since — so this is a plain chronological merge, not a dedupe.
  const getAuditTrail = React.useCallback(
    (entityType?: string, entityId?: string) => {
      const local = localState.auditLog.filter((entry) => (!entityType || entry.entityType === entityType) && (!entityId || entry.entityId === entityId))
      const idField = entityType ? DOMAIN_EVENT_ID_FIELDS[entityType] : undefined
      const extensionTable = entityType ? EXTENSION_ENTITY_TABLES[entityType] : undefined
      const resolvedEntityType = entityType
      const backend: AuditEntry[] = entityId && resolvedEntityType
        ? backendState.domainEvents
            .filter((e) => {
              const payload = (e.payload ?? {}) as Record<string, unknown>
              if (idField && String(payload[idField]) === entityId) return true
              if (extensionTable && payload.entity_table === extensionTable && String(payload.entity_id) === entityId) return true
              return false
            })
            .map((e) => {
              const payload = (e.payload ?? {}) as Record<string, unknown>
              return {
                id: `domain-${e.id}`,
                entityType: resolvedEntityType, entityId,
                action: (typeof payload.title === 'string' && payload.title) || e.event_type.replace(/_/g, ' '),
                actor: actorFromName(typeof payload.actor === 'string' ? payload.actor : undefined, typeof payload.role === 'string' ? payload.role : 'Clinician'),
                timestamp: e.created_at ?? nowIso(),
                reason: typeof payload.description === 'string' ? payload.description : undefined,
              }
            })
        : []
      return [...backend, ...local].sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1))
    },
    [localState.auditLog, backendState.domainEvents]
  )

  const state: OncologyState = { ...backendState, ...localState }

  const value: OncologyContextValue = {
    state, ready, backendError,
    getRegimen, createRegimen,
    createMdtCase, approveMdtRecommendation, createPlanFromMdt,
    getCarePlan, getTreatmentPlan, amendTreatmentPlan,
    getOrdersForPatient, createTreatmentOrder, authorizeOrder, transitionOrder, recordVerification, recordDoseModification,
    createDispenseRecord, updateDispenseRecord,
    recordPreAdministrationChecklist, recordAdministration, updateAdministration, recordPostAdministration,
    recordToxicityEvent, recordResponseAssessment,
    createRadiationPrescription, transitionRadiationSubStatus, scheduleFractions, recordFractionOutcome,
    createSurgicalPlan, transitionSurgicalSubStatus, recordOperativeOutcome,
    getJourney,
    getReadinessForPatient, recordTreatmentReadiness,
    getConsentsForPatient, recordConsent, updateConsentStatus,
    getAuditTrail,
  }

  return <OncologyContext.Provider value={value}>{children}</OncologyContext.Provider>
}

export function useOncology() {
  const value = React.useContext(OncologyContext)
  if (!value) throw new Error('OncologyProvider is required')
  return value
}
