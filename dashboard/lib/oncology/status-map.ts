/**
 * Status/id translators between the dashboard's unified domain model (types.ts) and the
 * real backend's per-entity native shapes (backend/app/models_cca.py,
 * models_cca_oncology_ext.py) — the seam Phase 3 of the backend-wiring work reads and
 * writes through, so no consuming screen needs to know these two vocabularies differ.
 *
 * Radiation (RtSubStatus) and Surgical (SurgicalSubStatus) sub-statuses are a straight
 * pass-through — the backend's transition endpoints were written to use dashboard's own
 * enum values verbatim, so there is nothing to translate for those two.
 *
 * TreatmentPlan and TreatmentOrder are the two genuinely lossy translations: the backend
 * models a much coarser lifecycle for both than the dashboard's single unified
 * TreatmentStatus enum, because pharmacy verification/preparation/dispensing and MAR
 * administration are separate backend tables (PharmacyReadiness,
 * InfusionMedicationAdministration) with their own status fields, not TreatmentOrder
 * sub-statuses. `composeOrderStatus` below is a best-effort projection of those three
 * tables onto one dashboard-shaped status — not a perfect inverse, since the backend's
 * pharmacy/medication status vocabularies are coarser (5 and ~6 values respectively) than
 * the dashboard's 17-value enum they're being mapped onto.
 */

import type { TreatmentStatus } from './types'

// ───────────────────────── Treatment Plan ─────────────────────────
// Backend: DRAFT, PROPOSED, ACTIVE, ON_HOLD, COMPLETED, SUPERSEDED, CANCELLED
// Dashboard TreatmentPlan.status: 'active' | 'superseded' | 'closed'

export function mapPlanStatus(backendStatus: string): 'active' | 'superseded' | 'closed' {
  if (backendStatus === 'SUPERSEDED') return 'superseded'
  if (backendStatus === 'COMPLETED' || backendStatus === 'CANCELLED') return 'closed'
  return 'active' // DRAFT, PROPOSED, ACTIVE, ON_HOLD — still the current plan being tracked
}

// ───────────────────────── Treatment Order (composed) ─────────────────────────
// Backend TreatmentOrder.status: DRAFT, SIGNED, EXECUTED, HELD, CANCELLED
// Backend PharmacyReadiness.status: Verified, Preparing, Ready, Dispensed, Received
// Backend medication administration aggregate: Pending/InProgress/Paused/Stopped/Completed/Omitted

export type OrderComposition = {
  orderStatus: string
  pharmacyStatus?: string | null
  medicationStatuses?: string[]
  hasCompletionRecord?: boolean
}

export function composeOrderStatus({ orderStatus, pharmacyStatus, medicationStatuses = [], hasCompletionRecord }: OrderComposition): TreatmentStatus {
  if (orderStatus === 'CANCELLED') return 'cancelled'
  if (orderStatus === 'HELD') return 'held'
  if (orderStatus === 'DRAFT') return 'draft'

  if (orderStatus === 'SIGNED') {
    if (!pharmacyStatus) return 'ordered'
    switch (pharmacyStatus) {
      case 'Verified': return 'verified'
      case 'Preparing': return 'preparation_pending'
      case 'Ready': return 'prepared'
      case 'Dispensed': return 'dispensed'
      case 'Received': return 'ready_for_administration'
      default: return 'verification_pending'
    }
  }

  // EXECUTED
  if (hasCompletionRecord) return 'completed'
  if (medicationStatuses.some((s) => s === 'InProgress')) return 'in_progress'
  if (medicationStatuses.length > 0 && medicationStatuses.every((s) => s === 'Completed')) return 'administered'
  return 'ready_for_administration'
}

/** Inverse-ish: which dashboard status a clinician action is aiming for maps to which
 * backend call — used by the store to decide which endpoint a `transitionOrder` call
 * should actually invoke. Not every dashboard status has a distinct backend call (several
 * pharmacy sub-states are all reached via the same POST /treatment/pharmacy-readiness
 * with a different `status` body field). */
export function pharmacyStatusForTarget(target: TreatmentStatus): string | undefined {
  switch (target) {
    case 'verified': return 'Verified'
    case 'preparation_pending': return 'Preparing'
    case 'prepared': return 'Ready'
    case 'dispensed': return 'Dispensed'
    case 'ready_for_administration': return 'Received'
    default: return undefined
  }
}

// ───────────────────────── MDT Case ─────────────────────────
// Backend: PROPOSED, PREPARED, SCHEDULED, DISCUSSED, RECOMMENDED, RETURNED_TO_RECORD, ACTIONED_BY_CLINICIAN, WITHDRAWN
// Dashboard MDTCase.status: 'scheduled' | 'discussed' | 'recommendation_recorded' | 'plan_created'

export function mapMdtCaseStatus(backendStatus: string, hasLinkedPlan: boolean): 'scheduled' | 'discussed' | 'recommendation_recorded' | 'plan_created' {
  if (hasLinkedPlan) return 'plan_created'
  if (backendStatus === 'RECOMMENDED' || backendStatus === 'ACTIONED_BY_CLINICIAN') return 'recommendation_recorded'
  if (backendStatus === 'DISCUSSED') return 'discussed'
  return 'scheduled'
}

// ───────────────────────── Medication administration ─────────────────────────
// Backend InfusionMedicationAdministration.status: Pending, InProgress, Paused, Stopped, Completed, Omitted
// Dashboard MARDrugAdministration.infusionStatus: not_started | in_progress | paused | completed | held | discontinued

export function mapMarStatusToBackendEvent(status: string): 'START' | 'PAUSE' | 'RESUME' | 'STOP' | 'COMPLETE' | 'OMIT' | undefined {
  switch (status) {
    case 'in_progress': return 'START'
    case 'paused': return 'PAUSE'
    case 'completed': return 'COMPLETE'
    case 'discontinued': return 'STOP'
    case 'held': return 'STOP'
    default: return undefined
  }
}

export function mapBackendMedicationStatus(status: string): 'not_started' | 'in_progress' | 'paused' | 'completed' | 'held' | 'discontinued' {
  switch (status) {
    case 'InProgress': return 'in_progress'
    case 'Paused': return 'paused'
    case 'Completed': return 'completed'
    case 'Stopped': return 'discontinued'
    case 'Omitted': return 'held'
    default: return 'not_started'
  }
}
