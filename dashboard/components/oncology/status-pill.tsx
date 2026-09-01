import { Badge } from '@/components/ui/badge'
import { RT_SUB_STATUS_LABELS, SURGICAL_SUB_STATUS_LABELS, TREATMENT_STATUS_LABELS } from '@/lib/oncology/terminology'
import type { RtSubStatus, SurgicalSubStatus, TreatmentStatus } from '@/lib/oncology/types'

/**
 * Renders the one controlled status vocabulary (PDF item 15) consistently everywhere —
 * Medical Oncology, Pharmacy, Day Care, Radiation and Surgical screens all import this
 * instead of inventing their own status badge per screen.
 */

const TREATMENT_VARIANT: Record<TreatmentStatus, 'neutral' | 'brand' | 'success' | 'warning' | 'critical' | 'information'> = {
  draft: 'neutral',
  proposed: 'neutral',
  mdt_recommended: 'information',
  clinician_approved: 'information',
  ordered: 'brand',
  verification_pending: 'warning',
  verified: 'information',
  preparation_pending: 'warning',
  prepared: 'information',
  dispensed: 'brand',
  ready_for_administration: 'information',
  in_progress: 'information',
  administered: 'success',
  held: 'critical',
  delayed: 'warning',
  cancelled: 'critical',
  completed: 'success',
}

export function TreatmentStatusPill({ status, className }: { status: TreatmentStatus; className?: string }) {
  return <Badge variant={TREATMENT_VARIANT[status]} className={className}>{TREATMENT_STATUS_LABELS[status]}</Badge>
}

const RT_VARIANT: Record<RtSubStatus, 'neutral' | 'brand' | 'success' | 'warning' | 'critical' | 'information'> = {
  prescribed: 'neutral', simulation_pending: 'warning', simulation_complete: 'information', contouring: 'information',
  planning: 'information', physics_qa: 'warning', physician_approved: 'brand', treatment_ready: 'information',
  on_treatment: 'information', interrupted: 'critical', completed: 'success',
}
export function RtStatusPill({ status, className }: { status: RtSubStatus; className?: string }) {
  return <Badge variant={RT_VARIANT[status]} className={className}>{RT_SUB_STATUS_LABELS[status]}</Badge>
}

const SURGICAL_VARIANT: Record<SurgicalSubStatus, 'neutral' | 'brand' | 'success' | 'warning' | 'critical' | 'information'> = {
  recommended: 'neutral', surgeon_reviewed: 'information', planned: 'information', pre_op_ready: 'warning',
  scheduled: 'brand', performed: 'information', post_op: 'information', histopathology_available: 'success',
}
export function SurgicalStatusPill({ status, className }: { status: SurgicalSubStatus; className?: string }) {
  return <Badge variant={SURGICAL_VARIANT[status]} className={className}>{SURGICAL_SUB_STATUS_LABELS[status]}</Badge>
}
