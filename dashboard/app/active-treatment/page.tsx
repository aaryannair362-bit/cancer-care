'use client'

import Link from 'next/link'
import { AlertTriangle, ArrowRight, HeartHandshake, Pill, Radiation, Scissors, ShieldAlert, Syringe, Users } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { TreatmentStatusPill, RtStatusPill, SurgicalStatusPill } from '@/components/oncology/status-pill'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { useOncology } from '@/lib/oncology/store'

const fieldClassName = 'text-xs font-medium text-metadata'

export default function ActiveTreatmentPage() {
  const { selectedPatient } = useDemoAccess()
  const { state, getTreatmentPlan, getOrdersForPatient, getConsentsForPatient } = useOncology()

  const treatmentPlan = getTreatmentPlan(selectedPatient.id)
  const mdtCase = state.mdtCases.find((c) => c.patientId === selectedPatient.id)
  const order = getOrdersForPatient(selectedPatient.id)[0]
  const dispenseRecords = order ? state.dispenseRecords.filter((d) => d.orderId === order.id) : []
  const marEntries = order ? state.marEntries.filter((m) => m.orderId === order.id) : []
  const administeredCount = marEntries.filter((m) => m.infusionStatus === 'completed').length
  const dispensableLineCount = order ? order.drugLines.filter((l) => !l.isPremedication && !l.isSupportive).length : 0

  const radiation = state.radiationPrescriptions.filter((r) => r.patientId === selectedPatient.id)[0]
  const radiationFractions = radiation ? state.radiationFractions.filter((f) => f.prescriptionId === radiation.id) : []
  const deliveredFractions = radiationFractions.filter((f) => f.status === 'delivered').length

  const surgicalPlans = state.surgicalPlans.filter((p) => p.patientId === selectedPatient.id).sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
  const surgicalPlan = surgicalPlans.find((p) => p.surgicalSubStatus !== 'histopathology_available') ?? surgicalPlans[0]

  const activePhase = treatmentPlan?.phases.find((p) => p.status === 'in_progress')
  const nextPlannedPhase = treatmentPlan?.phases.find((p) => p.status === 'proposed' || p.status === 'draft')
  const plannedTreatmentDisplay = activePhase
    ? `${activePhase.label} (in progress)`
    : nextPlannedPhase
      ? `${nextPlannedPhase.label} (planned)`
      : treatmentPlan
        ? `${treatmentPlan.intent} · ${treatmentPlan.lineOfTherapy}`
        : 'No treatment plan yet'

  const ongoingToxicities = state.toxicityEvents.filter((t) => t.patientId === selectedPatient.id && t.outcome === 'ongoing')
  const consents = getConsentsForPatient(selectedPatient.id)
  const signedConsents = consents.filter((c) => c.status === 'signed').length

  const nextStep = !treatmentPlan
    ? 'Awaiting MDT recommendation and Treatment Plan creation.'
    : order && order.status !== 'completed' && order.status !== 'cancelled'
      ? `Systemic therapy: ${order.status.replace(/_/g, ' ')} — continue in ${order.status === 'ordered' || order.status === 'verification_pending' ? 'Pharmacy' : order.status === 'dispensed' || order.status === 'ready_for_administration' || order.status === 'in_progress' ? 'Day Care / Infusion' : 'Treatment Order'}.`
      : radiation && radiation.rtSubStatus !== 'completed'
        ? `Radiation: ${radiation.rtSubStatus.replace(/_/g, ' ')} — continue in Radiation Oncology.`
        : surgicalPlan && surgicalPlan.surgicalSubStatus !== 'histopathology_available'
          ? `Surgical plan: ${surgicalPlan.surgicalSubStatus.replace(/_/g, ' ')} — continue in Surgical Oncology.`
          : 'Current active phases are complete — review Response Assessment and Treatment Readiness for the next cycle.'

  return (
    <PageContainer>
      <PageHeader title="Active Treatment" description="One view: what was decided, what's ordered, what's been given, and what comes next — across every modality" />

      <Card variant="elevated" className="mb-6 aivana-accent-line">
        <CardContent className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-5">
          <div className="sm:col-span-2 lg:col-span-1"><p className={fieldClassName}>Patient</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn}</p></div>
          <div><p className={fieldClassName}>Cancer diagnosis</p><p className="mt-1 text-sm font-semibold text-supporting">{treatmentPlan?.diagnosis ?? selectedPatient.diagnosis}</p><p className="text-xs text-metadata">{treatmentPlan?.stage ?? selectedPatient.stage}</p></div>
          <div><p className={fieldClassName}>MDT decision</p><p className="mt-1 text-sm font-semibold text-supporting">{mdtCase?.finalConsensus ?? 'No MDT case yet'}</p></div>
          <div><p className={fieldClassName}>What's planned</p><p className="mt-1 text-sm font-semibold text-supporting">{plannedTreatmentDisplay}</p></div>
          <div><p className={fieldClassName}>Current disease status</p><p className="mt-1 text-sm font-semibold text-supporting">{treatmentPlan?.currentDiseaseStatus ?? '—'}</p></div>
        </CardContent>
      </Card>

      {selectedPatient.allergy ? (
        <div className="mb-6 flex items-start gap-3 rounded-xl border border-critical/25 bg-critical-subtle p-4 text-sm font-semibold text-critical-strong">
          <ShieldAlert className="mt-0.5 size-5 shrink-0" /><div>Allergy on record — {selectedPatient.allergy}</div>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><Pill className="size-4" /></span><div><CardTitle>Systemic Therapy</CardTitle><CardDescription className="mt-1">{order ? order.regimenName : 'No treatment order yet'}</CardDescription></div></div></CardHeader>
          <CardContent className="space-y-3 pt-6">
            {order ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm text-supporting">Cycle {order.cycleNumber} of {order.plannedNumberOfCycles}</span><TreatmentStatusPill status={order.status} /></div>
                <div className="grid grid-cols-2 gap-2 text-xs text-metadata">
                  <span>Ordered by {order.orderingClinician.name}</span>
                  <span>{dispenseRecords.filter((d) => d.status === 'prepared' || d.status === 'dispensed').length}/{dispensableLineCount} prepared by pharmacy</span>
                  <span>{dispenseRecords.filter((d) => d.status === 'dispensed').length}/{dispensableLineCount} dispensed</span>
                  <span>{administeredCount}/{dispensableLineCount} administered</span>
                  <span>{order.drugLines.some((l) => l.doseModifications.length > 0) ? 'Dose modified' : 'No dose modifications'}</span>
                </div>
                <Link href="/treatment-order" className={buttonVariants({ size: 'sm', variant: 'outline' })}>Open Treatment Order<ArrowRight /></Link>
              </>
            ) : <p className="text-sm text-metadata">No systemic treatment order exists yet for this patient.</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><Radiation className="size-4" /></span><div><CardTitle>Radiation</CardTitle><CardDescription className="mt-1">{radiation ? `${radiation.treatmentSite}${radiation.laterality ? ` · ${radiation.laterality}` : ''}` : 'No RT prescription yet'}</CardDescription></div></div></CardHeader>
          <CardContent className="space-y-3 pt-6">
            {radiation ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm text-supporting">{radiation.totalPrescribedDoseGy} Gy / {radiation.numberOfFractions} fractions</span><RtStatusPill status={radiation.rtSubStatus} /></div>
                <p className="text-xs text-metadata">{deliveredFractions} / {radiation.numberOfFractions} fractions delivered</p>
                <Link href="/radiation-oncology" className={buttonVariants({ size: 'sm', variant: 'outline' })}>Open Radiation Prescription<ArrowRight /></Link>
              </>
            ) : <p className="text-sm text-metadata">No radiation prescription exists yet for this patient.</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><Scissors className="size-4" /></span><div><CardTitle>Surgery</CardTitle><CardDescription className="mt-1">{surgicalPlan ? (surgicalPlan.performedProcedure ?? surgicalPlan.procedure) : 'No surgical plan yet'}</CardDescription></div></div></CardHeader>
          <CardContent className="space-y-3 pt-6">
            {surgicalPlan ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm text-supporting">{surgicalPlan.anatomicalSite}{surgicalPlan.laterality ? ` · ${surgicalPlan.laterality}` : ''}</span><SurgicalStatusPill status={surgicalPlan.surgicalSubStatus} /></div>
                <p className="text-xs text-metadata">{surgicalPlan.histopathologyAvailable ? 'Histopathology available' : 'Awaiting histopathology'}</p>
                <Link href="/surgical-oncology" className={buttonVariants({ size: 'sm', variant: 'outline' })}>Open Surgical Plan<ArrowRight /></Link>
              </>
            ) : <p className="text-sm text-metadata">No surgical plan exists yet for this patient.</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><HeartHandshake className="size-4" /></span><div><CardTitle>Supportive Care</CardTitle><CardDescription className="mt-1">Toxicity, consent and premedication status</CardDescription></div></div></CardHeader>
          <CardContent className="space-y-3 pt-6">
            <div className="flex items-center justify-between text-sm"><span className="text-supporting">Ongoing toxicities</span>{ongoingToxicities.length > 0 ? <Badge variant="warning"><AlertTriangle />{ongoingToxicities.length} active</Badge> : <Badge variant="success">None active</Badge>}</div>
            <div className="flex items-center justify-between text-sm"><span className="text-supporting">Consents signed</span><Badge variant={signedConsents > 0 ? 'success' : 'neutral'}>{signedConsents} of {consents.length}</Badge></div>
            {order ? <p className="text-xs text-metadata">{order.drugLines.filter((l) => l.isPremedication).length} premedication line(s), {order.drugLines.filter((l) => l.isSupportive).length} supportive line(s) on the current order.</p> : null}
            <div className="flex flex-wrap gap-2">
              <Link href="/consent" className={buttonVariants({ size: 'sm', variant: 'outline' })}>Consent<ArrowRight /></Link>
              <Link href="/treatment-day" className={buttonVariants({ size: 'sm', variant: 'outline' })}>Day Care<ArrowRight /></Link>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card variant="elevated" className="mt-6"><CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><Users className="size-4 text-brand-deep" /><CardTitle>What comes next</CardTitle></div></CardHeader>
        <CardContent className="pt-6"><p className="text-sm font-semibold leading-6 text-supporting">{nextStep}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link href="/treatment-readiness" className={buttonVariants({ size: 'sm' })}><Syringe />Treatment Readiness</Link>
            <Link href="/response-assessment" className={buttonVariants({ size: 'sm', variant: 'outline' })}>Response Assessment</Link>
            <Link href="/mdt-tumour-board" className={buttonVariants({ size: 'sm', variant: 'outline' })}>MDT / Tumour Board</Link>
          </div>
        </CardContent>
      </Card>
    </PageContainer>
  )
}
