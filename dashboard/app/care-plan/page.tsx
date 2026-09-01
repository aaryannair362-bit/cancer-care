'use client'

import * as React from 'react'
import Link from 'next/link'
import {
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  FlaskConical,
  HeartHandshake,
  ListChecks,
  Mic,
  ShieldCheck,
  Stethoscope,
  UserRound,
  Users,
  Search,
} from 'lucide-react'

import { demoPatient, useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { TreatmentStatusPill } from '@/components/oncology/status-pill'
import { useOncology } from '@/lib/oncology/store'

const clinicalPlan = [
  { title: 'Investigations', icon: FlaskConical, items: ['CBC with ANC before the next cycle', 'Renal and liver function panel', 'Review available staging CT report'] },
  { title: 'Supportive care', icon: HeartHandshake, items: ['Optimise antiemetic schedule', 'Hydration and nutrition support', 'Reinforce neutropenic precautions'] },
  { title: 'Follow-up', icon: CalendarClock, items: ['Medical oncology review before cycle 3', 'Earlier review for fever or clinical deterioration'] },
  { title: 'Monitoring requirements', icon: ShieldCheck, items: ['Treatment toxicity and performance status', 'Allergy acknowledgement', 'Laboratory and symptom review'] },
] as const

export default function CarePlanPage() {
  const { selectedPatient, selectPatient, workflow } = useDemoAccess()
  const { getCarePlan, getTreatmentPlan } = useOncology()
  const [query,setQuery] = React.useState('')
  const [searched,setSearched] = React.useState(false)
  const matches = [demoPatient.name,demoPatient.mrn,demoPatient.mobile].some((value)=>value.toLowerCase().includes(query.trim().toLowerCase()))

  const carePlan = getCarePlan(selectedPatient.id)
  const treatmentPlan = getTreatmentPlan(selectedPatient.id)
  const phases = treatmentPlan?.phases ?? []

  return <PageContainer>
    <PageHeader title="Care Plan & Treatment Plan" description="Care Plan = overall strategy. Treatment Plan = the intended course derived from it. Neither is an executable order — see Treatment Order for that." />

    <Card className="mb-6"><CardContent className="p-5"><p className="text-sm font-semibold text-supporting">Search patient</p><div className="mt-3 flex flex-col gap-3 sm:flex-row"><Input aria-label="Treatment plan patient search" placeholder="Name, MRN or mobile number" value={query} onChange={(event)=>{setQuery(event.target.value);setSearched(false)}}/><Button type="button" onClick={()=>setSearched(true)}><Search/>Search</Button></div>{searched?<div className="mt-3 rounded-lg border border-border bg-surface-app p-3">{matches?<div className="flex flex-wrap items-center gap-3"><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-supporting">{demoPatient.name}</p><p className="text-xs text-metadata">MRN {demoPatient.mrn} · {demoPatient.mobile}</p></div><Button type="button" size="sm" onClick={()=>selectPatient(demoPatient)}>Open Care Plan</Button></div>:<p className="text-sm text-metadata">No matching patient.</p>}</div>:null}</CardContent></Card>

    <Card variant="elevated" className="mb-6 aivana-accent-line">
      <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center">
        <span className="flex size-11 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand-deep"><UserRound className="size-5" /></span>
        <div className="min-w-0 flex-1"><p className="break-words font-display text-lg font-semibold text-foreground">{selectedPatient.name}</p><p className="mt-1 break-words text-sm text-metadata">MRN {selectedPatient.mrn} · {selectedPatient.age} years · {selectedPatient.sex}</p></div>
        <Badge className="w-fit shrink-0 whitespace-nowrap" variant="information">{workflow.phase}</Badge>
      </CardContent>
    </Card>

    <Card className="mb-6">
      <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><ClipboardCheck className="size-4" /></span><div><CardTitle>Care Plan <span className="font-normal text-metadata">— overall multidisciplinary strategy</span></CardTitle>{carePlan ? <CardDescription className="mt-1">v{carePlan.version} · originated from MDT case {carePlan.originatingMdtCaseId} · {carePlan.intent} intent</CardDescription> : <CardDescription className="mt-1">No Care Plan yet — created once an MDT recommendation is turned into a plan</CardDescription>}</div></div></CardHeader>
      <CardContent className="grid gap-4 pt-6 sm:grid-cols-2 lg:grid-cols-3">
        {[
          ['Diagnosis', carePlan?.diagnosisSummary ?? selectedPatient.diagnosis],
          ['TNM', treatmentPlan?.stage ?? selectedPatient.stage],
          ['Key biomarkers', treatmentPlan?.biomarkers.join(', ') ?? selectedPatient.biology],
          ['Treatment intent', treatmentPlan?.intent ?? '—'],
          ['Line of therapy', treatmentPlan?.lineOfTherapy ?? '—'],
          ['Responsible specialty', treatmentPlan?.responsibleSpecialty.replace(/_/g,' ') ?? '—'],
          ['Current disease status', treatmentPlan?.currentDiseaseStatus ?? '—'],
          ['Current cycle / day', selectedPatient.treatmentPoint],
        ].map(([label,value])=><div key={label} className="min-w-0 rounded-xl border border-white/70 bg-surface/80 p-4 shadow-soft-sm"><p className="text-xs font-medium text-metadata">{label}</p><p className="mt-2 break-words text-sm font-semibold leading-5 text-supporting">{value}</p></div>)}
      </CardContent>
    </Card>

    <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><ListChecks className="size-4" /></span><div><CardTitle>Treatment Plan <span className="font-normal text-metadata">— intended phases</span></CardTitle><CardDescription className="mt-1">Completed, current, and upcoming treatment milestones · each phase links to its own order once authorized</CardDescription></div></div></CardHeader>
        <CardContent className="pt-6">{phases.length === 0 ? <p className="text-sm text-metadata">No treatment phases recorded yet.</p> : <ol className="space-y-4">{phases.map((phase,index)=><li key={phase.id} className="relative flex min-w-0 gap-4"><div className="flex flex-col items-center"><span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-white/70 bg-brand-soft text-xs font-semibold text-brand-deep">{index + 1}</span>{index < phases.length - 1 ? <span className="mt-2 h-full min-h-10 w-px bg-divider" /> : null}</div><div className="min-w-0 flex-1 pb-2"><div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><p className="break-words text-sm font-semibold text-supporting">{phase.label}</p><p className="mt-1 break-words text-xs leading-5 text-metadata">{phase.durationDescription} · {phase.responsibleClinician.name}</p>{phase.plannedStart ? <p className="mt-2 text-xs font-medium text-supporting">{phase.plannedStart}</p> : null}</div><TreatmentStatusPill status={phase.status} className="w-fit shrink-0 whitespace-nowrap" /></div>{phase.modality === 'systemic' ? <Link href="/treatment-order" className="mt-2 inline-block text-xs font-semibold text-brand-deep underline-offset-2 hover:underline">Open Treatment Order →</Link> : phase.modality === 'radiation' ? <Link href="/radiation-oncology" className="mt-2 inline-block text-xs font-semibold text-brand-deep underline-offset-2 hover:underline">Open Radiation Prescription →</Link> : phase.modality === 'surgical' ? <Link href="/surgical-oncology" className="mt-2 inline-block text-xs font-semibold text-brand-deep underline-offset-2 hover:underline">Open Surgical Plan →</Link> : null}</div></li>)}</ol>}</CardContent>
      </Card>

      <div className="min-w-0 space-y-6">
        <Card variant="elevated"><CardHeader className="border-b border-divider"><CardTitle>Next clinical action</CardTitle></CardHeader><CardContent className="pt-6"><p className="text-sm font-semibold leading-6 text-supporting">Review treatment toxicity and CBC/ANC before confirming cycle 3 readiness.</p><div className="mt-4 flex flex-wrap gap-2"><Link href="/doctor-opd" className={buttonVariants({ size: 'sm' })}>Open Doctor OPD<ArrowRight /></Link><Link href="/lab" className={buttonVariants({ variant: 'outline', size: 'sm' })}>Review laboratory results</Link></div></CardContent></Card>
        <Card><CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><Users className="size-4 text-brand-deep"/><CardTitle>MDT / care team decisions</CardTitle></div></CardHeader><CardContent className="space-y-4 pt-6"><div><p className="text-xs text-metadata">Agreed direction</p><p className="mt-1 text-sm font-medium leading-6 text-supporting">Continue the documented adjuvant pathway subject to treating-clinician review and treatment readiness.</p></div><div className="grid gap-3 sm:grid-cols-2"><div><p className="text-xs text-metadata">Responsible clinician</p><p className="mt-1 text-sm font-semibold text-supporting">Medical Oncologist</p></div><div><p className="text-xs text-metadata">Coordination owner</p><p className="mt-1 text-sm font-semibold text-supporting">Patient Liaison / Care Coordinator</p></div></div><Badge variant="warning">Clinician review required</Badge></CardContent></Card>
      </div>
    </div>

    <Card className="mt-6">
      <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><Stethoscope className="size-4 text-brand-deep"/><CardTitle>Clinical plan</CardTitle></div></CardHeader>
      <CardContent className="grid gap-4 pt-6 md:grid-cols-2 xl:grid-cols-4">{clinicalPlan.map((section)=>{const Icon=section.icon;return <section key={section.title} className="min-w-0 rounded-xl border border-white/70 bg-surface/76 p-5 shadow-soft-sm"><div className="flex items-center gap-3"><span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><Icon className="size-4" /></span><h3 className="break-words text-sm font-semibold text-supporting">{section.title}</h3></div><ul className="mt-4 space-y-3">{section.items.map((item)=><li key={item} className="flex min-w-0 gap-2 text-xs leading-5 text-metadata"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-brand-deep"/><span className="min-w-0 break-words">{item}</span></li>)}</ul></section>})}</CardContent>
    </Card>

    <Card variant="ai" className="mt-6"><CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center"><div className="flex min-w-0 flex-1 gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-ai-highlight text-brand-deep"><Mic className="size-4" /></span><div className="min-w-0"><p className="text-sm font-semibold text-supporting">Clinical documentation</p><p className="mt-1 break-words text-xs leading-5 text-metadata">Continue encounter documentation or review supporting clinical records.</p></div></div><div className="flex min-w-0 flex-wrap gap-2"><Link href="/opd-scribe" className={buttonVariants({ variant: 'secondary', size: 'sm' })}><Mic />Open Scribe</Link><Link href="/documents" className={buttonVariants({ variant: 'outline', size: 'sm' })}><FileText />View documentation</Link></div></CardContent></Card>
  </PageContainer>
}
