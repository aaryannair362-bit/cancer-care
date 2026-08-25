'use client'

import { UserRound } from 'lucide-react'
import { useDemoAccess } from '@/components/demo-access-provider'
import { canViewPatient } from '@/lib/demo-access'

/**
 * Persistent patient-context area. Placeholder only — reserved for patient
 * identity/context (name, MRN, diagnosis, stage) once clinical screens exist.
 * Rules: keep patient context persistent; identity must be immediately clear.
 */
export function PatientContext() {
  const { role, workflow, selectedPatient } = useDemoAccess()
  const visible = canViewPatient(role)
  return (
    <div className="aivana-accent-line flex min-h-[52px] shrink-0 items-center gap-3 border-b border-white/55 bg-[linear-gradient(90deg,hsl(var(--brand-soft)/0.72),hsl(var(--surface-elevated)/0.66),hsl(var(--brand-indigo)/0.08))] px-4 py-2 pl-5 shadow-[0_10px_28px_-24px_hsl(var(--brand-deep)/0.36)] backdrop-blur-md sm:px-7 sm:pl-8">
      <span
        className="flex size-8 shrink-0 items-center justify-center rounded-full bg-surface-elevated text-brand-deep shadow-soft-sm"
        aria-hidden="true"
      >
        <UserRound className="size-4" />
      </span>
      <div className="flex min-w-0 flex-1 flex-col leading-tight md:flex-row md:items-center md:gap-5">
        <span className="truncate text-sm font-semibold text-supporting">{visible ? selectedPatient.name : 'Patient context restricted for this role'}</span>
        {visible ? <><span className="hidden h-4 w-px bg-border md:block"/><span className="truncate text-xs font-medium text-metadata">MRN {selectedPatient.mrn}</span><span className="hidden h-4 w-px bg-border lg:block"/><span className="hidden truncate text-xs text-metadata lg:block">{selectedPatient.stage} · {selectedPatient.treatment}</span><span className="hidden h-4 w-px bg-border xl:block"/><span className="hidden truncate text-xs text-metadata xl:block">{selectedPatient.treatmentPoint} · {workflow.phase}</span></> : <span className="truncate text-xs text-metadata">System-wide oversight does not expose patient details.</span>}
      </div>
    </div>
  )
}
