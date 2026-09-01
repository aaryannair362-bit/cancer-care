/**
 * Integration adapter layer — the seam where NEXUS, MOSAIQ, ABDM and a real FHIR
 * endpoint plug in later (PDF items 22, 23, 25, 29, 30).
 *
 * Every adapter here is honest about being unconnected today: it returns a typed
 * `not_connected` result rather than fabricating data, and every UI consumer is
 * required to render that state (never a silent blank, never invented content — see
 * `RecommendationPanel`). Wiring a live integration later means implementing the
 * `fetch`/`send` body of the matching adapter; no consuming screen needs to change,
 * because they were built against this contract from the start.
 *
 * AI/NEXUS guardrail (item 29): nothing in this file writes a TreatmentOrder,
 * DoseModification, VerificationCheckpoint, or MARDrugAdministration. A recommendation
 * is data for a human to read and accept/modify/dismiss — never a call that mutates
 * the clinical record on its own.
 */

import type { ActorRef, RecommendationAudience, RecommendationSlot } from './types'

export type IntegrationStatus = 'not_connected' | 'connected' | 'error'

export type IntegrationDescriptor = {
  name: string
  status: IntegrationStatus
  standardsUsed: string[]
  note: string
}

// ───────────────────────── NEXUS (guideline / recommendation engine) ─────────────────────────

export const nexusIntegration: IntegrationDescriptor = {
  name: 'NEXUS Guideline & Recommendation Engine',
  status: 'not_connected',
  standardsUsed: ['NCCN (licensed pathway content, once acquired)', 'HL7 FHIR R4'],
  note: 'Not yet wired. This adapter defines the exact contract NEXUS will fulfil — every screen that shows a RecommendationSlot already renders the not_connected state correctly, so connecting NEXUS is a matter of implementing fetchRecommendation() below, not redesigning any screen.',
}

let recommendationSlotCounter = 0

/**
 * Returns the recommendation a screen should render for this patient/context/audience.
 * Today this always resolves to `not_connected`. When NEXUS is connected, replace the
 * body with a real call and populate `recommendationText`, `rationale`, `provenance`,
 * `guidelinePathwayName` and `guidelineVersion` — the RecommendationSlot shape already
 * has a field for each. Per item 29/30, this function must never be made to write a
 * clinical record directly; it only returns data for `RecommendationPanel` to display.
 */
export async function fetchNexusRecommendation(
  patientId: string,
  context: string,
  audience: RecommendationAudience
): Promise<RecommendationSlot> {
  recommendationSlotCounter += 1
  return {
    id: `nexus-slot-${recommendationSlotCounter}`,
    patientId,
    context,
    audience,
    connectionState: 'not_connected',
    source: 'NEXUS',
  }
}

/** Records what a clinician decided to do with a recommendation — never auto-applied. */
export function recordRecommendationResponse(
  slot: RecommendationSlot,
  decision: 'accepted' | 'modified' | 'dismissed',
  actor: ActorRef,
  note?: string
): RecommendationSlot {
  return {
    ...slot,
    clinicianResponse: { decision, actor, at: new Date().toISOString(), note },
  }
}

// ───────────────────────── MOSAIQ integration mapping (item 25) ─────────────────────────
// Structural mapping only — deliberately not claimed as tested interoperability until
// interface-verified against CCA's actual MOSAIQ installation and licensed resources.

export type MosaiqMappingRow = {
  entity: string
  ourModel: string
  mosaiqConcept: string
  direction: 'not_yet_determined' | 'read_only_from_mosaiq' | 'write_to_mosaiq' | 'bidirectional'
  status: 'not_connected'
}

export const mosaiqIntegrationMapping: MosaiqMappingRow[] = [
  { entity: 'Patient identity', ourModel: 'Patient / PatientIdentifier', mosaiqConcept: 'MOSAIQ Patient', direction: 'not_yet_determined', status: 'not_connected' },
  { entity: 'Diagnosis', ourModel: 'TreatmentPlan.diagnosis', mosaiqConcept: 'MOSAIQ Diagnosis', direction: 'not_yet_determined', status: 'not_connected' },
  { entity: 'Encounters', ourModel: 'JourneyMilestone', mosaiqConcept: 'MOSAIQ Encounter / Appointment', direction: 'not_yet_determined', status: 'not_connected' },
  { entity: 'Treatment order', ourModel: 'TreatmentOrder', mosaiqConcept: 'MOSAIQ Rx / Medical Oncology order', direction: 'not_yet_determined', status: 'not_connected' },
  { entity: 'Medication request', ourModel: 'TreatmentOrder.drugLines', mosaiqConcept: 'MOSAIQ Medication Request', direction: 'not_yet_determined', status: 'not_connected' },
  { entity: 'Medication administration', ourModel: 'MARDrugAdministration', mosaiqConcept: 'MOSAIQ Administration Record', direction: 'not_yet_determined', status: 'not_connected' },
  { entity: 'RT prescription / plan', ourModel: 'RadiationPrescription', mosaiqConcept: 'MOSAIQ RT Prescription + DICOM RT Plan reference', direction: 'not_yet_determined', status: 'not_connected' },
  { entity: 'RT treatment record', ourModel: 'RadiationFraction', mosaiqConcept: 'MOSAIQ Treatment History / delivered fraction record', direction: 'not_yet_determined', status: 'not_connected' },
  { entity: 'Appointments', ourModel: 'JourneyMilestone', mosaiqConcept: 'MOSAIQ Scheduling', direction: 'not_yet_determined', status: 'not_connected' },
  { entity: 'Documents', ourModel: '(Documents module — outside this domain model)', mosaiqConcept: 'MOSAIQ Document Management', direction: 'not_yet_determined', status: 'not_connected' },
  { entity: 'Results', ourModel: '(Lab / Radiology modules — outside this domain model)', mosaiqConcept: 'MOSAIQ Results', direction: 'not_yet_determined', status: 'not_connected' },
]

// ───────────────────────── ABDM (item 23) ─────────────────────────

export type AbdmLinkStatus = {
  abhaId?: string
  hipHiuStatus: 'not_configured'
  consentArtefactStatus: 'not_configured'
  badge: 'PRODUCTION INTEGRATION REQUIRED'
}

export function getAbdmLinkStatus(abhaId?: string): AbdmLinkStatus {
  return { abhaId, hipHiuStatus: 'not_configured', consentArtefactStatus: 'not_configured', badge: 'PRODUCTION INTEGRATION REQUIRED' }
}

// ───────────────────────── FHIR / mCODE mapping-ready shapes (item 22) ─────────────────────────
// Plain-object FHIR-shaped projections. No FHIR server is called — these functions exist so
// the internal model is mechanically exportable once a live FHIR endpoint exists.

import type { CarePlan, ConsentRecord, MARDrugAdministration, RadiationPrescription, ResponseAssessment, SurgicalPlan, TreatmentOrder } from './types'

export function toFhirMedicationRequest(order: TreatmentOrder) {
  return {
    resourceType: 'MedicationRequest',
    id: order.id,
    status: order.status === 'cancelled' ? 'cancelled' : order.status === 'completed' ? 'completed' : 'active',
    intent: 'order',
    subject: { reference: `Patient/${order.patientId}` },
    authoredOn: order.createdAt,
    requester: { display: order.orderingClinician.name },
    // mCODE Cancer-Related Medication Request extension slots:
    extension: [
      { url: 'mcode:treatmentIntent', valueString: order.treatmentIntent },
      { url: 'mcode:lineOfTherapy', valueString: order.lineOfTherapy },
      { url: 'mcode:cycleNumber', valueInteger: order.cycleNumber },
    ],
    dosageInstruction: order.drugLines.map((line) => ({
      text: `${line.genericDrugName} ${line.orderedDose} via ${line.route}`,
      route: { text: line.route },
    })),
  }
}

export function toFhirMedicationAdministration(record: MARDrugAdministration, patientId: string) {
  return {
    resourceType: 'MedicationAdministration',
    id: record.id,
    status: record.infusionStatus === 'completed' ? 'completed' : record.infusionStatus === 'discontinued' ? 'stopped' : 'in-progress',
    subject: { reference: `Patient/${patientId}` },
    medicationCodeableConcept: { text: record.drug },
    effectivePeriod: { start: record.startTime, end: record.endTime },
    performer: [{ actor: { display: record.administeredBy.name } }],
    dosage: { dose: { text: record.doseGiven }, route: { text: record.route } },
  }
}

export function toFhirCarePlan(plan: CarePlan) {
  return {
    resourceType: 'CarePlan',
    id: plan.id,
    status: plan.status === 'active' ? 'active' : plan.status === 'superseded' ? 'revoked' : 'completed',
    intent: 'plan',
    subject: { reference: `Patient/${plan.patientId}` },
    description: plan.diagnosisSummary,
    // mCODE Cancer Disease Status / Care Plan profile slot.
    category: [{ text: 'oncology' }],
  }
}

export function toFhirRadiotherapyCourseSummary(prescription: RadiationPrescription) {
  // Structural placeholder for HL7 mCODE's Radiotherapy Course Summary profile.
  // Real DICOM RT Plan / RT Dose / RT Structure Set objects are referenced, not stored,
  // via `dicomRtPlanRef` — this app is a workflow/order layer, not a planning system.
  return {
    resourceType: 'Procedure',
    id: prescription.id,
    status: prescription.status === 'completed' ? 'completed' : 'in-progress',
    subject: { reference: `Patient/${prescription.patientId}` },
    code: { text: `Radiotherapy — ${prescription.modality} / ${prescription.technique}` },
    extension: [
      { url: 'mcode:totalPrescribedDose', valueString: prescription.totalPrescribedDoseGy },
      { url: 'mcode:fractions', valueInteger: prescription.numberOfFractions },
      { url: 'dicom:rtPlanReference', valueString: prescription.dicomRtPlanRef ?? null },
    ],
  }
}

export function toFhirSurgicalProcedure(plan: SurgicalPlan) {
  return {
    resourceType: 'Procedure',
    id: plan.id,
    status: plan.performedAt ? 'completed' : 'preparation',
    subject: { reference: `Patient/${plan.patientId}` },
    code: { text: plan.performedProcedure ?? plan.procedure },
    bodySite: [{ text: `${plan.anatomicalSite}${plan.laterality ? ` (${plan.laterality})` : ''}` }],
  }
}

// mCODE's Tumor Marker Test / Cancer Disease Status profiles are the closest fit for a
// RECIST response assessment — represented here as an Observation, category "imaging",
// with the RECIST category as the coded value. Lesion-level detail stays in `component`.
export function toFhirResponseObservation(assessment: ResponseAssessment) {
  return {
    resourceType: 'Observation',
    id: assessment.id,
    status: 'final',
    category: [{ text: 'imaging' }],
    code: { text: `Treatment response — ${assessment.frameworkName}` },
    subject: { reference: `Patient/${assessment.patientId}` },
    effectiveDateTime: assessment.assessmentDate,
    valueCodeableConcept: { text: assessment.responseCategory },
    component: assessment.lesions.map((lesion) => ({
      code: { text: `${lesion.site} (${lesion.type})` },
      valueString: `${lesion.baselineMeasurementMm ?? '—'} mm -> ${lesion.followUpMeasurementMm ?? '—'} mm`,
    })),
  }
}

export function toFhirConsent(record: ConsentRecord) {
  return {
    resourceType: 'Consent',
    id: record.id,
    status: record.status === 'signed' ? 'active' : record.status === 'declined' || record.status === 'withdrawn' ? 'rejected' : 'draft',
    scope: { text: 'treatment' },
    category: [{ text: record.type }],
    patient: { reference: `Patient/${record.patientId}` },
    dateTime: record.signedAt ?? record.createdAt,
    provision: { text: record.discussedTopics.join('; ') },
  }
}
