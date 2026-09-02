'use client'

import * as React from 'react'
import Link from 'next/link'
import {
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  FileText,
  FileWarning,
  FlaskConical,
  HeartHandshake,
  ListChecks,
  Mic,
  Radiation,
  Scissors,
  Send,
  ShieldAlert,
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
import { RtStatusPill, SurgicalStatusPill, TreatmentStatusPill } from '@/components/oncology/status-pill'
import { useOncology } from '@/lib/oncology/store'
import type { TreatmentStatus } from '@/lib/oncology/types'

const clinicalPlan = [
  { title: 'Investigations', icon: FlaskConical, items: ['CBC with ANC before the next cycle', 'Renal and liver function panel', 'Review available staging CT report'] },
  { title: 'Supportive care', icon: HeartHandshake, items: ['Optimise antiemetic schedule', 'Hydration and nutrition support', 'Reinforce neutropenic precautions'] },
  { title: 'Follow-up', icon: CalendarClock, items: ['Medical oncology review before cycle 3', 'Earlier review for fever or clinical deterioration'] },
  { title: 'Monitoring requirements', icon: ShieldCheck, items: ['Treatment toxicity and performance status', 'Allergy acknowledgement', 'Laboratory and symptom review'] },
] as const

// The order lifecycle (state-machine.ts) has 17 granular statuses — the on-screen stepper
// groups them into the 7 stages a clinician actually watches for. This is a display grouping
// only; the underlying order.status stays the single source of truth everywhere else.
const ORDER_STEPPER = ['Draft', 'Pending Approval', 'Approved', 'Pharmacy Verified', 'Dispensed', 'Ready for Administration', 'Administered'] as const
const ORDER_STEP_INDEX: Record<TreatmentStatus, number> = {
  draft: 0, proposed: 0,
  mdt_recommended: 1, clinician_approved: 1,
  ordered: 2,
  verification_pending: 3, verified: 3, preparation_pending: 3, prepared: 3,
  dispensed: 4,
  ready_for_administration: 5,
  in_progress: 6, administered: 6, completed: 6,
  held: -1, delayed: -1, cancelled: -1,
}

export default function CarePlanPage() {
  const { selectedPatient, selectPatient } = useDemoAccess()
  const { state, getCarePlan, getTreatmentPlan, getOrdersForPatient } = useOncology()
  const [query, setQuery] = React.useState('')
  const [searched, setSearched] = React.useState(false)
  const matches = [demoPatient.name, demoPatient.mrn, demoPatient.mobile].some((value) => value.toLowerCase().includes(query.trim().toLowerCase()))

  const carePlan = getCarePlan(selectedPatient.id)
  const treatmentPlan = getTreatmentPlan(selectedPatient.id)
  const phases = treatmentPlan?.phases ?? []
  const order = getOrdersForPatient(selectedPatient.id)[0]
  const mdtCase = state.mdtCases.find((c) => c.patientId === selectedPatient.id)

  const radiation = state.radiationPrescriptions.filter((r) => r.patientId === selectedPatient.id)[0]
  const surgicalPlan = state.surgicalPlans.filter((p) => p.patientId === selectedPatient.id)[0]

  // Dispensing & Administration Status — the same underlying records treatment-order's
  // order-to-delivery traceability reads, grouped down to the 5 stages this compact panel
  // shows (full actor/timestamp detail stays on Treatment Order and Pharmacy).
  const verification = order ? state.verifications.find((v) => v.orderId === order.id && v.outcome === 'verified') : undefined
  const dispenseRecords = order ? state.dispenseRecords.filter((d) => d.orderId === order.id) : []
  const dispensedRecord = dispenseRecords.find((d) => d.dispensedAt)
  const preparedRecord = dispenseRecords.find((d) => d.preparedAt)
  const firstAdministration = order ? state.marEntries.filter((m) => m.orderId === order.id)[0] : undefined
  const dispensingStages = [
    { label: 'Doctor Order', done: Boolean(order?.authorizedAt), detail: order?.authorizedAt ? `Approved · ${new Date(order.authorizedAt).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}` : 'Not yet authorized' },
    { label: 'Pharmacy Verification', done: Boolean(verification), detail: verification ? `Verified by ${verification.verifiedBy.name}` : order?.authorizedAt ? 'Pending' : 'Not started' },
    { label: 'Dispensing / Preparation', done: Boolean(dispensedRecord), detail: dispensedRecord ? 'Dispensed' : preparedRecord ? 'Prepared' : 'Not started' },
    { label: 'Day-Care Nurse Verification', done: Boolean(firstAdministration), detail: firstAdministration ? `Confirmed by ${firstAdministration.administeredBy.name}` : 'Not started' },
    { label: 'Administration', done: Boolean(firstAdministration && firstAdministration.infusionStatus !== 'not_started'), detail: firstAdministration?.infusionStatus === 'completed' ? 'Completed' : firstAdministration && firstAdministration.infusionStatus !== 'not_started' ? 'In progress' : 'Not started' },
  ]
  const activeStageIndex = dispensingStages.findIndex((s) => !s.done)

  // Order Checks — height/weight/BSA capture plus whatever eligibility parameters the
  // ordering clinician recorded (item 5's "treatment eligibility/hold parameters"),
  // rendered as the same verified/pending/attention tile pattern the mockup uses.
  const orderChecks: { label: string; state: 'verified' | 'pending' | 'attention' }[] = order ? [
    { label: 'Height verified', state: order.heightCm ? 'verified' : 'pending' },
    { label: 'Weight verified', state: order.weightKg ? 'verified' : 'pending' },
    { label: 'BSA calculated', state: order.bsaM2 ? 'verified' : 'pending' },
    ...order.eligibilityParametersChecked.map((c) => ({ label: c.parameter, state: (c.clinicianReviewed ? 'verified' : c.valuePresent ? 'pending' : 'attention') as 'verified' | 'pending' | 'attention' })),
    { label: 'Allergy acknowledgement', state: (order.allergiesAcknowledged ? 'verified' : selectedPatient.allergy ? 'attention' : 'verified') },
  ] : []

  return <PageContainer>
    <PageHeader title="Treatment Plan" description="Care Plan = overall strategy. Treatment Plan = the intended course derived from it. Neither is an executable order — see Treatment Order for that." />

    <Card className="mb-6"><CardContent className="p-5"><p className="text-sm font-semibold text-supporting">Search patient</p><div className="mt-3 flex flex-col gap-3 sm:flex-row"><Input aria-label="Treatment plan patient search" placeholder="Name, MRN or mobile number" value={query} onChange={(event)=>{setQuery(event.target.value);setSearched(false)}}/><Button type="button" onClick={()=>setSearched(true)}><Search/>Search</Button></div>{searched?<div className="mt-3 rounded-lg border border-border bg-surface-app p-3">{matches?<div className="flex flex-wrap items-center gap-3"><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-supporting">{demoPatient.name}</p><p className="text-xs text-metadata">MRN {demoPatient.mrn} · {demoPatient.mobile}</p></div><Button type="button" size="sm" onClick={()=>selectPatient(demoPatient)}>Open Treatment Plan</Button></div>:<p className="text-sm text-metadata">No matching patient.</p>}</div>:null}</CardContent></Card>

    {/* Patient header bar */}
    <Card variant="elevated" className="mb-6 aivana-accent-line">
      <CardContent className="flex flex-col gap-4 p-5 lg:flex-row lg:items-center">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand-deep"><UserRound className="size-5" /></span>
          <div className="min-w-0">
            <p className="break-words font-display text-lg font-semibold text-foreground">{selectedPatient.name}</p>
            <p className="mt-1 break-words text-sm text-metadata">MRN {selectedPatient.mrn} · {selectedPatient.age} years · {selectedPatient.sex}</p>
            <p className="mt-1 break-words text-xs text-metadata">{treatmentPlan?.diagnosis ?? selectedPatient.diagnosis} · {treatmentPlan?.stage ?? selectedPatient.stage}</p>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:flex lg:items-center lg:gap-8">
          <div><p className="text-xs font-medium uppercase tracking-wide text-metadata">Treating clinician</p><p className="mt-1 text-sm font-semibold text-brand-deep">{order?.orderingClinician.name ?? (treatmentPlan ? specialtyLabel(treatmentPlan.responsibleSpecialty) : '—')}</p></div>
          <div><p className="text-xs font-medium uppercase tracking-wide text-metadata">Current treatment</p><p className="mt-1 text-sm font-semibold text-supporting">{order ? `${order.regimenName} · Cycle ${order.cycleNumber} · Day ${order.day}` : 'No active order'}</p></div>
        </div>
        {selectedPatient.allergy ? <Badge variant="critical" className="w-fit shrink-0 whitespace-nowrap"><ShieldAlert />Important alert · {selectedPatient.allergy}</Badge> : null}
      </CardContent>
    </Card>

    {/* 1. MDT / care team decision */}
    <Card className="mb-6">
      <CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><Users className="size-4" /></span><div><CardTitle>1. MDT / Care Team Decision</CardTitle>{carePlan ? <CardDescription className="mt-1">Care Plan v{carePlan.version} · {carePlan.intent} intent — the overall strategy this Treatment Plan operationalizes</CardDescription> : null}</div></div>{!order ? <Badge variant="warning">Clinician review required</Badge> : null}</div></CardHeader>
      <CardContent className="pt-6">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="sm:col-span-1"><p className="text-xs text-metadata">Agreed direction</p><p className="mt-1 text-sm font-medium leading-6 text-supporting">{mdtCase?.finalConsensus || mdtCase?.recommendation || 'Continue the documented pathway subject to treating-clinician review and treatment readiness.'}</p></div>
          <div><p className="text-xs text-metadata">Responsible clinician</p><p className="mt-1 text-sm font-semibold text-supporting">{treatmentPlan ? specialtyLabel(treatmentPlan.responsibleSpecialty) : 'Medical Oncologist'}</p></div>
          <div><p className="text-xs text-metadata">Coordination owner</p><p className="mt-1 text-sm font-semibold text-supporting">Patient Liaison / Care Coordinator</p></div>
        </div>
        <div className="mt-4">
          <Link href="/treatment-order" className={buttonVariants({ size: 'sm' })}>{order ? 'Open Treatment Order' : 'Create Treatment Order'}<ArrowRight /></Link>
        </div>
      </CardContent>
    </Card>

    <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <div className="min-w-0 space-y-6">
        {/* 2. Active Treatment Order */}
        {order ? (
          <Card>
            <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><ClipboardCheck className="size-4" /></span><div><CardTitle>2. Active Treatment Order</CardTitle><CardDescription className="mt-1">{order.regimenName} — Cycle {order.cycleNumber} of {order.plannedNumberOfCycles}</CardDescription></div></div></CardHeader>
            <CardContent className="space-y-6 pt-6">
              {ORDER_STEP_INDEX[order.status] >= 0 ? (
                <div className="overflow-x-auto pb-1"><ol className="flex min-w-[640px] items-start">{ORDER_STEPPER.map((label, index) => {
                  const current = ORDER_STEP_INDEX[order.status]
                  const reached = index <= current
                  const isCurrent = index === current
                  return <li key={label} className="relative flex-1">
                    <div className="flex items-center">
                      <span className={`relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border-2 text-[11px] font-semibold ${isCurrent ? 'border-primary bg-primary text-primary-foreground' : reached ? 'border-success bg-success-subtle text-success-strong' : 'border-border bg-surface text-metadata'}`}>{index + 1}</span>
                      {index < ORDER_STEPPER.length - 1 ? <span className={`h-0.5 flex-1 ${index < current ? 'bg-success' : 'bg-border-emphasized'}`} /> : null}
                    </div>
                    <p className={`mt-2 mr-2 text-[11px] font-medium leading-4 ${isCurrent ? 'text-brand-deep' : 'text-metadata'}`}>{label}</p>
                  </li>
                })}</ol></div>
              ) : <Badge variant="critical">{TREATMENT_STATUS_LABEL(order.status)}</Badge>}

              <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
                {[['Intent', order.treatmentIntent], ['Modality', 'Systemic therapy'], ['Frequency', `Every cycle · Day ${order.day}`], ['BSA', order.bsaM2 ? `${order.bsaM2} m²` : '—'], ['Monitoring', order.eligibilityParametersChecked.map((c) => c.parameter).join(', ') || '—'], ['Ordered by', order.orderingClinician.name]]
                  .map(([label, value]) => <div key={label}><p className="text-xs font-medium text-metadata">{label}</p><p className="mt-1 break-words text-sm font-semibold text-supporting">{value}</p></div>)}
              </div>

              <div>
                <p className="text-sm font-semibold text-supporting">Drug Sequence</p>
                <div className="mt-2 overflow-x-auto rounded-lg border border-divider"><table className="w-full min-w-[760px] text-left text-sm"><thead className="border-b border-divider bg-surface-elevated/70 text-xs text-metadata"><tr><th className="px-3 py-2 font-medium">Seq</th><th className="px-3 py-2 font-medium">Medication</th><th className="px-3 py-2 font-medium">Dose basis</th><th className="px-3 py-2 font-medium">Prescribed dose</th><th className="px-3 py-2 font-medium">Route</th><th className="px-3 py-2 font-medium">Day / Cycle</th><th className="px-3 py-2 font-medium">Dose modification</th><th className="px-3 py-2 font-medium">Status</th></tr></thead>
                  <tbody>{order.drugLines.map((line, index) => <tr key={line.id} className="border-b border-divider last:border-0"><td className="px-3 py-2 text-metadata">{index + 1}</td><td className="px-3 py-2 font-medium text-supporting">{line.genericDrugName}{line.isPremedication ? <Badge variant="neutral" className="ml-2">Premed</Badge> : null}{line.isSupportive ? <Badge variant="neutral" className="ml-2">Supportive</Badge> : null}</td><td className="px-3 py-2 text-metadata">{line.doseBasisDescription || '—'}</td><td className="px-3 py-2 font-semibold text-supporting">{line.orderedDose}</td><td className="px-3 py-2 text-metadata">{line.route}</td><td className="px-3 py-2 text-metadata">Day {order.day} · {order.cycleNumber}/{order.plannedNumberOfCycles}</td><td className="px-3 py-2">{line.doseModifications.length > 0 ? <span className="inline-flex items-center gap-1 text-xs font-medium text-warning-strong"><FileWarning className="size-3.5" />{line.doseModifications.length} modification{line.doseModifications.length > 1 ? 's' : ''}</span> : <span className="text-xs text-metadata">None</span>}</td><td className="px-3 py-2"><TreatmentStatusPill status={order.status} /></td></tr>)}</tbody>
                </table></div>
              </div>

              {order.drugLines.some((l) => l.isPremedication || l.isSupportive) ? (
                <div>
                  <p className="text-sm font-semibold text-supporting">Supportive / Pre-medications</p>
                  <ul className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-metadata">{order.drugLines.filter((l) => l.isPremedication || l.isSupportive).map((l) => <li key={l.id} className="flex items-center gap-1.5"><CheckCircle2 className="size-3.5 text-success-strong" />{l.genericDrugName}</li>)}</ul>
                </div>
              ) : null}

              <div>
                <p className="text-sm font-semibold text-supporting">Order Checks</p>
                <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{orderChecks.map((check) => <div key={check.label} className={`flex items-center gap-2 rounded-lg border p-3 text-xs font-medium ${check.state === 'verified' ? 'border-success/30 bg-success-subtle text-success-strong' : check.state === 'attention' ? 'border-critical/30 bg-critical-subtle text-critical-strong' : 'border-warning/30 bg-warning-subtle text-warning-strong'}`}>{check.state === 'verified' ? <CheckCircle2 className="size-4 shrink-0" /> : check.state === 'attention' ? <ShieldAlert className="size-4 shrink-0" /> : <CalendarClock className="size-4 shrink-0" />}{check.label}</div>)}</div>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Card><CardContent className="p-6 text-sm text-metadata">No treatment order exists yet for this patient — create one from the MDT / Care Team Decision above once the plan is authorized.</CardContent></Card>
        )}

        {/* Treatment Plan phases (kept — the intended-course view Order execution above operationalizes) */}
        <Card>
          <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><ListChecks className="size-4" /></span><div><CardTitle>Treatment Plan phases</CardTitle><CardDescription className="mt-1">Completed, current, and upcoming treatment milestones · each phase links to its own order once authorized</CardDescription></div></div></CardHeader>
          <CardContent className="pt-6">{phases.length === 0 ? <p className="text-sm text-metadata">No treatment phases recorded yet.</p> : <ol className="space-y-4">{phases.map((phase,index)=><li key={phase.id} className="relative flex min-w-0 gap-4"><div className="flex flex-col items-center"><span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-white/70 bg-brand-soft text-xs font-semibold text-brand-deep">{index + 1}</span>{index < phases.length - 1 ? <span className="mt-2 h-full min-h-10 w-px bg-divider" /> : null}</div><div className="min-w-0 flex-1 pb-2"><div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><p className="break-words text-sm font-semibold text-supporting">{phase.label}</p><p className="mt-1 break-words text-xs leading-5 text-metadata">{phase.durationDescription} · {phase.responsibleClinician.name}</p>{phase.plannedStart ? <p className="mt-2 text-xs font-medium text-supporting">{phase.plannedStart}</p> : null}</div><TreatmentStatusPill status={phase.status} className="w-fit shrink-0 whitespace-nowrap" /></div>{phase.modality === 'systemic' ? <Link href="/treatment-order" className="mt-2 inline-block text-xs font-semibold text-brand-deep underline-offset-2 hover:underline">Open Treatment Order →</Link> : phase.modality === 'radiation' ? <Link href="/radiation-oncology" className="mt-2 inline-block text-xs font-semibold text-brand-deep underline-offset-2 hover:underline">Open Radiation Prescription →</Link> : phase.modality === 'surgical' ? <Link href="/surgical-oncology" className="mt-2 inline-block text-xs font-semibold text-brand-deep underline-offset-2 hover:underline">Open Surgical Plan →</Link> : null}</div></li>)}</ol>}</CardContent>
        </Card>
      </div>

      <div className="min-w-0 space-y-6">
        {/* 3. Dispensing & Administration Status */}
        {order ? (
          <Card>
            <CardHeader className="border-b border-divider"><CardTitle>3. Dispensing &amp; Administration Status</CardTitle></CardHeader>
            <CardContent className="pt-6">
              <ol className="space-y-0">{dispensingStages.map((stage, index) => {
                const isCurrent = index === activeStageIndex
                const isDone = stage.done
                return <li key={stage.label} className="relative flex gap-3 pb-5 last:pb-0">
                  {index < dispensingStages.length - 1 ? <span className={`absolute left-[11px] top-6 h-full w-px ${isDone ? 'bg-success' : 'bg-border-emphasized'}`} /> : null}
                  <span className={`relative z-10 flex size-6 shrink-0 items-center justify-center rounded-full border-2 ${isDone ? 'border-success bg-success-subtle text-success-strong' : isCurrent ? 'border-warning bg-warning-subtle text-warning-strong' : 'border-border bg-surface text-metadata'}`}>{isDone ? <CheckCircle2 className="size-3.5" /> : <span className="size-2 rounded-full bg-current" />}</span>
                  <div className="min-w-0 flex-1"><p className={`text-sm font-semibold ${isDone ? 'text-supporting' : isCurrent ? 'text-warning-strong' : 'text-metadata'}`}>{stage.label}</p><p className="mt-0.5 text-xs text-metadata">{stage.detail}</p></div>
                </li>
              })}</ol>
              <div className="mt-2 flex flex-wrap gap-2 border-t border-divider pt-4">
                <Link href="/pharmacy" className={buttonVariants({ size: 'sm' })}><Send />Send to Pharmacy</Link>
                <Link href="/treatment-day" className={buttonVariants({ size: 'sm', variant: 'outline' })}>View Administration Workflow</Link>
              </div>
            </CardContent>
          </Card>
        ) : null}

        {/* 4. Radiation Therapy Order */}
        <Link href="/radiation-oncology" className="block">
          <Card className="transition-colors hover:bg-surface-app"><CardContent className="flex items-center justify-between gap-3 p-5">
            <div className="flex min-w-0 items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><Radiation className="size-4" /></span><div className="min-w-0"><p className="text-sm font-semibold text-supporting">4. Radiation Therapy Order</p><p className="mt-1 truncate text-xs text-metadata">{radiation ? `${radiation.treatmentSite} · ${radiation.totalPrescribedDoseGy} Gy / ${radiation.numberOfFractions} fractions` : 'No RT prescription yet'}</p></div></div>
            <div className="flex shrink-0 items-center gap-2">{radiation ? <RtStatusPill status={radiation.rtSubStatus} /> : null}<ChevronRight className="size-4 text-disabled" /></div>
          </CardContent></Card>
        </Link>

        {/* 5. Surgical Procedure Order */}
        <Link href="/surgical-oncology" className="block">
          <Card className="transition-colors hover:bg-surface-app"><CardContent className="flex items-center justify-between gap-3 p-5">
            <div className="flex min-w-0 items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><Scissors className="size-4" /></span><div className="min-w-0"><p className="text-sm font-semibold text-supporting">5. Surgical Procedure Order</p><p className="mt-1 truncate text-xs text-metadata">{surgicalPlan ? (surgicalPlan.performedProcedure ?? surgicalPlan.procedure) : 'No surgical plan yet'}</p></div></div>
            <div className="flex shrink-0 items-center gap-2">{surgicalPlan ? <SurgicalStatusPill status={surgicalPlan.surgicalSubStatus} /> : null}<ChevronRight className="size-4 text-disabled" /></div>
          </CardContent></Card>
        </Link>

        {/* 6. Treatment Timeline */}
        <Card>
          <CardHeader className="border-b border-divider"><CardTitle>6. Treatment Timeline</CardTitle></CardHeader>
          <CardContent className="max-h-[420px] overflow-y-auto pt-6">
            {phases.length === 0 ? <p className="text-sm text-metadata">No treatment phases recorded yet.</p> : (
              <ol className="space-y-4">{phases.map((phase, index) => (
                <li key={phase.id} className="flex items-start gap-3">
                  <span className={`flex size-6 shrink-0 items-center justify-center rounded-full border-2 text-[11px] font-semibold ${phase.status === 'completed' ? 'border-success bg-success-subtle text-success-strong' : phase.status === 'in_progress' ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-surface text-metadata'}`}>{phase.status === 'completed' ? <CheckCircle2 className="size-3.5" /> : index + 1}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-semibold text-supporting">{phase.label}</p><TreatmentStatusPill status={phase.status} /></div>
                    {phase.plannedStart ? <p className="mt-0.5 text-xs text-metadata">{phase.plannedStart}</p> : null}
                  </div>
                </li>
              ))}</ol>
            )}
          </CardContent>
        </Card>
      </div>
    </div>

    <Card className="mt-6">
      <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><Stethoscope className="size-4 text-brand-deep"/><CardTitle>Clinical plan</CardTitle></div></CardHeader>
      <CardContent className="grid gap-4 pt-6 md:grid-cols-2 xl:grid-cols-4">{clinicalPlan.map((section)=>{const Icon=section.icon;return <section key={section.title} className="min-w-0 rounded-xl border border-white/70 bg-surface/76 p-5 shadow-soft-sm"><div className="flex items-center gap-3"><span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><Icon className="size-4" /></span><h3 className="break-words text-sm font-semibold text-supporting">{section.title}</h3></div><ul className="mt-4 space-y-3">{section.items.map((item)=><li key={item} className="flex min-w-0 gap-2 text-xs leading-5 text-metadata"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-brand-deep"/><span className="min-w-0 break-words">{item}</span></li>)}</ul></section>})}</CardContent>
    </Card>

    <Card variant="ai" className="mt-6"><CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center"><div className="flex min-w-0 flex-1 gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-ai-highlight text-brand-deep"><Mic className="size-4" /></span><div className="min-w-0"><p className="text-sm font-semibold text-supporting">Clinical documentation</p><p className="mt-1 break-words text-xs leading-5 text-metadata">Continue encounter documentation or review supporting clinical records.</p></div></div><div className="flex min-w-0 flex-wrap gap-2"><Link href="/opd-scribe" className={buttonVariants({ variant: 'secondary', size: 'sm' })}><Mic />Open Scribe</Link><Link href="/documents" className={buttonVariants({ variant: 'outline', size: 'sm' })}><FileText />View documentation</Link></div></CardContent></Card>
  </PageContainer>
}

function TREATMENT_STATUS_LABEL(status: TreatmentStatus) {
  return status.replace(/_/g, ' ')
}

function specialtyLabel(specialty: string) {
  return specialty.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
