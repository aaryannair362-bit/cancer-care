/**
 * The one controlled state machine for the treatment lifecycle (PDF item 15) and the
 * hard transition guard behind PDF item 28's "prevent impossible state transitions" /
 * "prevent unsigned orders from dispensing" / "prevent cancelled orders from
 * administration" / "no unsafe status jump such as Ordered -> Administered without
 * required verification".
 *
 * This module enforces SEQUENCING and AUTHORIZATION GATES only — it has no opinion on
 * whether a dose, drug, or clinical decision is *correct*. That distinction is
 * deliberate: sequencing is a workflow-integrity property the system is allowed to
 * own; clinical correctness is not.
 */

import type { RtSubStatus, SurgicalSubStatus, TreatmentStatus } from './types'

export class IllegalTransitionError extends Error {
  constructor(kind: string, from: string, to: string) {
    super(`Illegal ${kind} transition: "${from}" -> "${to}" is not an allowed next state.`)
    this.name = 'IllegalTransitionError'
  }
}

// ───────────────────────── Treatment order chain ─────────────────────────

const TERMINAL_TREATMENT_STATUSES: TreatmentStatus[] = ['cancelled', 'completed']

/**
 * Allowed forward transitions. `held` and `delayed` are reachable from any non-terminal
 * state and can return to the state they were held from (the caller supplies `to`
 * explicitly on resume — this table only says a hold/resume is *structurally* legal,
 * not which state to resume into).
 */
const TREATMENT_TRANSITIONS: Record<TreatmentStatus, TreatmentStatus[]> = {
  draft: ['proposed', 'cancelled'],
  proposed: ['mdt_recommended', 'clinician_approved', 'cancelled'],
  mdt_recommended: ['clinician_approved', 'cancelled'],
  clinician_approved: ['ordered', 'cancelled'],
  ordered: ['verification_pending', 'held', 'cancelled'],
  verification_pending: ['verified', 'held', 'cancelled'],
  // NB: verified is required before preparation/dispense — an order can never jump
  // straight from `ordered` to a pharmacy or administration state.
  verified: ['preparation_pending', 'held', 'cancelled'],
  preparation_pending: ['prepared', 'held', 'cancelled'],
  prepared: ['dispensed', 'held', 'cancelled'],
  dispensed: ['ready_for_administration', 'held', 'cancelled'],
  ready_for_administration: ['in_progress', 'held', 'delayed', 'cancelled'],
  in_progress: ['administered', 'held', 'cancelled'],
  administered: ['completed'],
  held: ['ordered', 'verification_pending', 'verified', 'preparation_pending', 'prepared', 'dispensed', 'ready_for_administration', 'in_progress', 'cancelled'],
  delayed: ['ready_for_administration', 'cancelled'],
  cancelled: [],
  completed: [],
}

export function canTransitionTreatmentStatus(from: TreatmentStatus, to: TreatmentStatus): boolean {
  if (from === to) return false
  if (TERMINAL_TREATMENT_STATUSES.includes(from)) return false
  return TREATMENT_TRANSITIONS[from]?.includes(to) ?? false
}

export function assertTreatmentStatusTransition(from: TreatmentStatus, to: TreatmentStatus): void {
  if (!canTransitionTreatmentStatus(from, to)) throw new IllegalTransitionError('treatment order', from, to)
}

/**
 * The two guardrails item 28 names explicitly, expressed as direct checks (both are
 * also implied by TREATMENT_TRANSITIONS above — kept as named predicates because the
 * UI wants a specific denial message for each, not a generic "illegal transition").
 */
export function isUnverifiedDispenseAttempt(status: TreatmentStatus): boolean {
  return status === 'ordered' || status === 'verification_pending'
}
export function isCancelledOrderAdministrationAttempt(status: TreatmentStatus): boolean {
  return status === 'cancelled'
}

// ───────────────────────── Radiation sub-status chain ─────────────────────────

const RT_TRANSITIONS: Record<RtSubStatus, RtSubStatus[]> = {
  prescribed: ['simulation_pending'],
  simulation_pending: ['simulation_complete'],
  simulation_complete: ['contouring'],
  contouring: ['planning'],
  planning: ['physics_qa'],
  physics_qa: ['physician_approved', 'planning'],
  physician_approved: ['treatment_ready'],
  treatment_ready: ['on_treatment'],
  on_treatment: ['interrupted', 'completed'],
  interrupted: ['on_treatment', 'completed'],
  completed: [],
}

export function canTransitionRtSubStatus(from: RtSubStatus, to: RtSubStatus): boolean {
  if (from === to) return false
  return RT_TRANSITIONS[from]?.includes(to) ?? false
}
export function assertRtSubStatusTransition(from: RtSubStatus, to: RtSubStatus): void {
  if (!canTransitionRtSubStatus(from, to)) throw new IllegalTransitionError('radiation course', from, to)
}

// ───────────────────────── Surgical sub-status chain ─────────────────────────

const SURGICAL_TRANSITIONS: Record<SurgicalSubStatus, SurgicalSubStatus[]> = {
  recommended: ['surgeon_reviewed'],
  surgeon_reviewed: ['planned'],
  planned: ['pre_op_ready'],
  pre_op_ready: ['scheduled'],
  scheduled: ['performed'],
  performed: ['post_op'],
  post_op: ['histopathology_available'],
  histopathology_available: [],
}

export function canTransitionSurgicalSubStatus(from: SurgicalSubStatus, to: SurgicalSubStatus): boolean {
  if (from === to) return false
  return SURGICAL_TRANSITIONS[from]?.includes(to) ?? false
}
export function assertSurgicalSubStatusTransition(from: SurgicalSubStatus, to: SurgicalSubStatus): void {
  if (!canTransitionSurgicalSubStatus(from, to)) throw new IllegalTransitionError('surgical plan', from, to)
}

// ───────────────────────── Generic helpers ─────────────────────────

export function isTerminalTreatmentStatus(status: TreatmentStatus): boolean {
  return TERMINAL_TREATMENT_STATUSES.includes(status)
}
