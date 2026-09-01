'use client'

import * as React from 'react'
import Link from 'next/link'
import { AlertTriangle, ArrowRight, CheckCircle2, CircleDot, Clock3, FileText, FlaskConical, History, Network, Pill, Scissors, Stethoscope, UserRound, Users } from 'lucide-react'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { useDemoAccess } from '@/components/demo-access-provider'
import { useOncology } from '@/lib/oncology/store'
import type { JourneyDepartment } from '@/lib/oncology/types'

/**
 * Department/care-stage transitions only (PDF item 1) — every appointment, lab upload,
 * prescription, note edit, or internal status change stays in Audit & Activity, which
 * already exists precisely for that granular detail. This page answers one question:
 * where is the patient right now, and what department-level stages has the case
 * actually moved through.
 */
const DEPARTMENT_ICON: Record<JourneyDepartment, typeof UserRound> = {
  registration: UserRound,
  nurse_intake: CheckCircle2,
  medical_oncology: Stethoscope,
  radiology: FlaskConical,
  pathology: FileText,
  surgical_oncology: Scissors,
  radiation_oncology: CircleDot,
  mdt_tumour_board: Users,
  day_care_infusion: Network,
  pharmacy: Pill,
  surgery: Scissors,
  radiation_treatment: CircleDot,
  follow_up: Clock3,
}

const upcoming = [
  ['Laboratory result review', 'Today · 11:30', 'Medical Oncology', 'High'], ['Treatment-cycle readiness', 'Before 31 Aug', 'Day Care Team', 'High'],
  ['MDT review and outcome', '29 Aug · 08:00', 'Tumour Board', 'Routine'], ['Oncology follow-up', '30 Aug · 10:30', 'Medical Oncologist', 'Routine'],
  ['Patient symptom follow-up', '25 Aug · 12:00', 'Nurse Navigator', 'Routine'],
]

export default function PatientJourneyPage() {
  const { selectedPatient } = useDemoAccess()
  const { getJourney } = useOncology()
  const milestones = getJourney(selectedPatient.id)
  const [selectedId, setSelectedId] = React.useState<string | null>(null)
  const selected = milestones.find((m) => m.id === selectedId) ?? milestones.find((m) => m.isCurrent) ?? milestones[0]

  return <PageContainer>
    <PageHeader title="Patient Journey" description="Department and care-stage transitions only — click a stage to open its detailed activity in Audit & Activity." actions={<Badge variant="information">Fictional demo data</Badge>} />

    <div className="mb-6 flex items-start gap-3 rounded-lg border border-information/30 bg-information-subtle px-4 py-3 text-information-strong"><Network className="mt-0.5 size-4 shrink-0" /><div><p className="text-sm font-medium">Workflow visualisation only</p><p className="mt-0.5 text-xs">All data is fictional. Decisions remain clinician-led; this prototype makes no autonomous treatment decision and has no EHR, HIS, PACS, or LIS integration.</p></div></div>

    <Card className="mb-6 bg-surface-clinical"><CardContent className="grid gap-5 p-5 sm:grid-cols-2 xl:grid-cols-5"><div className="flex items-center gap-3 sm:col-span-2"><span className="flex size-10 items-center justify-center rounded-full bg-surface"><UserRound className="size-5" /></span><div><p className="font-display font-semibold">{selectedPatient.name} <span className="text-xs font-normal text-metadata">(Fictional)</span></p><p className="text-xs text-metadata">MRN {selectedPatient.mrn} · {selectedPatient.age} years · {selectedPatient.sex}</p></div></div><div><p className="text-xs uppercase tracking-wider text-metadata">Diagnosis / stage</p><p className="mt-1 text-sm font-medium text-supporting">{selectedPatient.diagnosis} · {selectedPatient.stage}</p></div><div><p className="text-xs uppercase tracking-wider text-metadata">Current treatment</p><p className="mt-1 text-sm font-medium text-supporting">{selectedPatient.treatment}</p></div><div><p className="text-xs uppercase tracking-wider text-metadata">Current location / care stage</p><p className="mt-1 text-sm font-semibold text-brand-deep">{milestones.find((m) => m.isCurrent)?.label ?? '—'}</p></div></CardContent></Card>

    <Card className="mb-6"><CardHeader className="border-b border-divider"><div className="flex items-start justify-between gap-3"><div><CardTitle>Oncology journey timeline</CardTitle><CardDescription className="mt-1">Meaningful department transitions only. Select a stage to inspect date, department, clinician and status.</CardDescription></div>{milestones.find((m) => m.isCurrent) ? <Badge variant="warning"><CircleDot />Current: {milestones.find((m) => m.isCurrent)?.label}</Badge> : null}</div></CardHeader>
      <CardContent className="pt-6"><div className="overflow-x-auto pb-2"><ol className="flex min-w-[1000px] items-start">{milestones.map((milestone, index) => {
        const Icon = DEPARTMENT_ICON[milestone.department]
        const complete = milestone.status === 'complete' || milestone.status === 'completed'
        return <li key={milestone.id} className="relative flex-1">
          <button type="button" onClick={() => setSelectedId(milestone.id)} className="group w-full text-left">
            <div className="flex items-center">
              <span className={cn('relative z-10 flex size-10 items-center justify-center rounded-full border-2', milestone.isCurrent ? 'border-primary bg-primary text-primary-foreground shadow-neu' : complete ? 'border-success bg-success-subtle text-success-strong' : 'border-border bg-surface text-metadata')}><Icon className="size-4" /></span>
              {index < milestones.length - 1 ? <span className={cn('h-0.5 flex-1', complete ? 'bg-success' : 'bg-border-emphasized')} /> : null}
            </div>
            <div className={cn('mt-3 mr-3 rounded-md border p-3 transition-colors', selected?.id === milestone.id ? 'border-brand bg-brand-soft' : 'border-transparent group-hover:bg-surface-app')}>
              <p className="text-sm font-semibold text-supporting">{milestone.label}</p>
              <p className="mt-1 text-xs text-metadata">{milestone.date || 'Not yet reached'}</p>
              <Badge className="mt-2" variant={complete ? 'success' : milestone.isCurrent ? 'information' : 'neutral'}>{complete ? 'Completed' : milestone.isCurrent ? 'In progress' : 'Upcoming'}</Badge>
            </div>
          </button>
        </li>
      })}</ol></div></CardContent>
    </Card>

    {selected ? (
      <div className="mb-6 grid gap-6 xl:grid-cols-3">
        <Card variant="elevated" className="xl:col-span-2"><CardHeader className="border-b border-divider"><div className="flex items-start justify-between gap-3"><div><CardTitle>{selected.label}</CardTitle><CardDescription className="mt-1">Selected stage details</CardDescription></div><Badge variant={selected.status === 'complete' || selected.status === 'completed' ? 'success' : selected.isCurrent ? 'information' : 'neutral'}>{selected.status.replace(/_/g,' ')}</Badge></div></CardHeader>
          <CardContent className="grid gap-5 pt-6 sm:grid-cols-2">
            <div><p className="text-xs uppercase tracking-wider text-metadata">Date</p><p className="mt-1 text-sm font-medium text-supporting">{selected.date || 'Not yet reached'}</p></div>
            <div><p className="text-xs uppercase tracking-wider text-metadata">Clinician</p><p className="mt-1 text-sm font-medium text-supporting">{selected.clinician?.name ?? '—'}</p></div>
            <div className="sm:col-span-2 rounded-md border border-brand-soft bg-surface-clinical p-4"><p className="text-xs uppercase tracking-wider text-metadata">Detailed activity for this department</p><p className="mt-2 text-sm leading-6 text-supporting">Every appointment, lab upload, prescription and status change within this stage is recorded, but kept out of this timeline by design.</p><Link href="/audit-activity" className="mt-3 inline-flex items-center gap-1 text-sm font-semibold text-brand-deep underline-offset-2 hover:underline"><History className="size-3.5" />Open detailed activity in Audit & Activity</Link></div>
          </CardContent>
        </Card>
        <Card><CardHeader className="border-b border-divider"><CardTitle>Current position</CardTitle><CardDescription>Care stage and readiness coordination</CardDescription></CardHeader><CardContent className="space-y-4 pt-6"><div className="flex items-center gap-3"><span className="flex size-10 items-center justify-center rounded-full bg-warning-subtle text-warning-strong"><FlaskConical className="size-5" /></span><div><p className="text-sm font-semibold text-supporting">{milestones.find((m) => m.isCurrent)?.label ?? 'No current stage'}</p><p className="text-xs text-metadata">Reflects workflow state only</p></div></div><div className="rounded-md border border-warning/30 bg-warning-subtle p-3 text-xs text-warning-strong">The journey position reflects workflow state only, not a clinical risk prediction or treatment authorisation.</div></CardContent></Card>
      </div>
    ) : null}

    <div className="grid gap-6 xl:grid-cols-3"><Card className="xl:col-span-2"><CardHeader className="border-b border-divider"><CardTitle>Upcoming care</CardTitle><CardDescription>Fictional scheduled and coordination items</CardDescription></CardHeader><CardContent className="divide-y divide-divider p-0">{upcoming.map(([task,due,owner,priority]) => <div key={task} className="flex flex-wrap items-center gap-3 px-6 py-4"><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-supporting">{task}</p><p className="mt-1 text-xs text-metadata">{owner} · {due}</p></div><Badge variant={priority === 'High' ? 'warning' : 'neutral'}>{priority}</Badge><Button type="button" variant="ghost" size="sm">View workflow<ArrowRight /></Button></div>)}</CardContent></Card><Card><CardHeader className="border-b border-divider"><CardTitle>Barriers / workflow risks</CardTitle><CardDescription>Operational issues requiring human follow-up</CardDescription></CardHeader><CardContent className="space-y-3 pt-6">{[['Pending laboratory review','Critical fictional ANC requires clinician interpretation'],['Treatment readiness incomplete','Clearance not yet recorded'],['Patient follow-up required','Symptom check scheduled for 25 Aug']].map(([title,detail]) => <div key={title} className="rounded-md border border-warning/30 bg-warning-subtle p-3"><div className="flex gap-2"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning-strong" /><div><p className="text-sm font-semibold text-warning-strong">{title}</p><p className="mt-1 text-xs text-warning-strong">{detail}</p></div></div></div>)}</CardContent></Card></div>
  </PageContainer>
}
