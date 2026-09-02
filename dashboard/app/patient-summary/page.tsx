'use client'

import Link from 'next/link'
import { AlertTriangle, ArrowRight, ShieldAlert, Users } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useOncology } from '@/lib/oncology/store'

const fieldClassName = 'text-xs font-medium uppercase tracking-wide text-metadata'
const NOT_RECORDED = 'Not recorded in this session'

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="min-w-0 rounded-xl border border-white/70 bg-surface/80 p-4 shadow-soft-sm"><p className={fieldClassName}>{label}</p><p className="mt-2 break-words text-sm font-semibold leading-5 text-supporting">{value || NOT_RECORDED}</p></div>
}

export default function PatientSummaryPage() {
  const { selectedPatient } = useDemoAccess()
  const { state, getTreatmentPlan, getOrdersForPatient } = useOncology()

  const treatmentPlan = getTreatmentPlan(selectedPatient.id)
  const mdtCase = state.mdtCases.find((c) => c.patientId === selectedPatient.id)
  const order = getOrdersForPatient(selectedPatient.id)[0]
  const toxicities = state.toxicityEvents.filter((t) => t.patientId === selectedPatient.id)
  const surgicalPlan = state.surgicalPlans.filter((p) => p.patientId === selectedPatient.id)[0]
  const radiation = state.radiationPrescriptions.filter((r) => r.patientId === selectedPatient.id)[0]
  const activePhase = treatmentPlan?.phases.find((p) => p.status === 'in_progress')
  const lastCompletedPhase = [...(treatmentPlan?.phases ?? [])].reverse().find((p) => p.status === 'completed')
  const nextPhase = treatmentPlan?.phases.find((p) => p.status === 'proposed' || p.status === 'draft')
  const currentJourney = state.journeyMilestones.filter((m) => m.patientId === selectedPatient.id).find((m) => m.isCurrent)

  const stageDisplay = treatmentPlan?.stage ?? selectedPatient.stage
  const stagingVersionDisplay = state.stagingDetail
    ? `${stageDisplay} (${state.stagingDetail.systemLabel}${state.stagingDetail.confirmedAt ? ` · confirmed ${new Date(state.stagingDetail.confirmedAt).toLocaleDateString()}` : ''})`
    : stageDisplay
  const recentImagingDisplay = state.recentImaging.length > 0
    ? state.recentImaging.map((r) => `${r.title}${r.resultedAt ? ` (${new Date(r.resultedAt).toLocaleDateString()})` : ''}`).join('; ')
    : undefined

  const careTeam = [
    order ? { role: order.orderingClinician.roleLabel, name: order.orderingClinician.name } : null,
    surgicalPlan ? { role: surgicalPlan.recommendedBy.roleLabel, name: surgicalPlan.recommendedBy.name } : null,
    radiation ? { role: radiation.createdBy.roleLabel, name: radiation.createdBy.name } : null,
  ].filter((v): v is { role: string; name: string } => v !== null)
  const uniqueCareTeam = Array.from(new Map(careTeam.map((c) => [`${c.role}:${c.name}`, c])).values())

  return (
    <PageContainer>
      <PageHeader title="Patient Summary" description="Oncology-relevant clinical picture at a glance — not a generic EMR summary" />

      <Card variant="elevated" className="mb-6 aivana-accent-line"><CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1"><p className="font-display text-lg font-semibold text-foreground">{selectedPatient.name}</p><p className="mt-1 text-sm text-metadata">MRN {selectedPatient.mrn} · {selectedPatient.age} years · {selectedPatient.sex}</p></div>
        {currentJourney ? <Badge variant="information">Current stage: {currentJourney.label}</Badge> : null}
      </CardContent></Card>

      {selectedPatient.allergy ? (
        <div className="mb-6 flex items-start gap-3 rounded-xl border border-critical/25 bg-critical-subtle p-4 text-sm font-semibold text-critical-strong"><ShieldAlert className="mt-0.5 size-5 shrink-0" />{selectedPatient.allergy}</div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Cancer diagnosis" value={treatmentPlan?.diagnosis ?? selectedPatient.diagnosis} />
        <Field label="Primary site" value={mdtCase?.cancerDiagnosis ?? selectedPatient.diagnosis} />
        <Field label="Histology" value={treatmentPlan?.histology} />
        <Field label="Stage and staging version/date" value={stagingVersionDisplay} />
        <Field label="Biomarkers / molecular findings" value={treatmentPlan?.biomarkers.join(', ') ?? selectedPatient.biology} />
        <Field label="Performance status (ECOG)" value={mdtCase?.performanceStatus} />
        <Field label="Current disease status" value={treatmentPlan?.currentDiseaseStatus} />
        <Field label="Current treatment phase" value={activePhase ? `${activePhase.label} (${activePhase.status.replace(/_/g, ' ')})` : NOT_RECORDED} />
        <Field label="Previous cancer treatments" value={lastCompletedPhase ? lastCompletedPhase.label : NOT_RECORDED} />
        <Field label="MDT decision" value={mdtCase?.finalConsensus} />
        <Field label="Active treatment plan" value={treatmentPlan ? `${treatmentPlan.intent} · ${treatmentPlan.lineOfTherapy}` : NOT_RECORDED} />
        <Field label="Last treatment" value={lastCompletedPhase?.label} />
        <Field label="Next treatment" value={nextPhase?.label} />
        <Field label="Toxicities" value={toxicities.length > 0 ? `${toxicities.length} recorded (${toxicities.filter((t) => t.outcome === 'ongoing').length} ongoing)` : 'None recorded'} />
        <Field label="Allergies" value={selectedPatient.allergy || 'None recorded'} />
        <Field label="Important comorbidities" value={state.comorbiditiesSummary} />
        <Field label="Relevant labs" value={order?.eligibilityParametersChecked.map((c) => c.parameter).join(', ')} />
        <Field label="Recent imaging" value={recentImagingDisplay} />
        <Field label="Outstanding actions" value={!treatmentPlan ? 'MDT recommendation → Treatment Plan' : !order ? 'Create Treatment Order' : order.status !== 'completed' ? `Continue systemic therapy (${order.status.replace(/_/g, ' ')})` : 'None outstanding'} />
        <Field label="Current care team" value={uniqueCareTeam.length > 0 ? uniqueCareTeam.map((c) => `${c.name} (${c.role})`).join(', ') : NOT_RECORDED} />
      </div>

      {toxicities.filter((t) => t.outcome === 'ongoing').length > 0 ? (
        <div className="mt-6 flex items-start gap-3 rounded-xl border border-warning/25 bg-warning-subtle p-4 text-sm text-warning-strong"><AlertTriangle className="mt-0.5 size-4 shrink-0" /><span>{toxicities.filter((t) => t.outcome === 'ongoing').length} toxicity event(s) still ongoing — review before the next treatment cycle.</span></div>
      ) : null}

      <Card className="mt-6"><CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><Users className="size-4 text-brand-deep" /><CardTitle>Open the full clinical record</CardTitle></div></CardHeader>
        <CardContent className="flex flex-wrap gap-2 pt-6">
          <Link href="/active-treatment" className={buttonVariants({ size: 'sm' })}>Active Treatment<ArrowRight /></Link>
          <Link href="/care-plan" className={buttonVariants({ size: 'sm', variant: 'outline' })}>Care Plan & Treatment Plan</Link>
          <Link href="/patient-journey" className={buttonVariants({ size: 'sm', variant: 'outline' })}>Patient Journey</Link>
        </CardContent>
      </Card>
    </PageContainer>
  )
}
