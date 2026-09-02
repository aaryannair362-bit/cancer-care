'use client'

import * as React from 'react'
import { CalendarClock, CheckCircle2, ClipboardCheck, History, PauseCircle, XCircle } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { RecommendationPanel } from '@/components/oncology/recommendation-panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useOncology } from '@/lib/oncology/store'
import type { ActorRef, TreatmentReadinessDecision } from '@/lib/oncology/types'

const fieldClassName = 'text-xs font-medium text-metadata'

const DECISION_META: Record<TreatmentReadinessDecision, { label: string; variant: 'success' | 'warning' | 'critical' | 'information'; icon: typeof CheckCircle2 }> = {
  proceed: { label: 'Proceed', variant: 'success', icon: CheckCircle2 },
  proceed_modified: { label: 'Proceed Modified', variant: 'information', icon: ClipboardCheck },
  hold: { label: 'Hold', variant: 'warning', icon: PauseCircle },
  delay: { label: 'Delay', variant: 'warning', icon: CalendarClock },
  stop: { label: 'Stop', variant: 'critical', icon: XCircle },
}

export default function TreatmentReadinessPage() {
  const { role, selectedPatient } = useDemoAccess()
  const { getOrdersForPatient, getTreatmentPlan, getReadinessForPatient, recordTreatmentReadiness, getAuditTrail } = useOncology()

  const actor: ActorRef = { userId: role.roleId, name: role.label, roleLabel: role.label }
  const order = getOrdersForPatient(selectedPatient.id)[0]
  const treatmentPlan = getTreatmentPlan(selectedPatient.id)
  const history = getReadinessForPatient(selectedPatient.id)
  const latest = history[0]

  const [form, setForm] = React.useState({
    labsReviewed: false, labsSummary: '', performanceStatus: 'ECOG 1', weightKg: '', bsaM2: '',
    toxicitiesReviewed: false, toxicitySummary: '', previousTreatmentTolerance: '', doseModificationPlanned: '',
    currentMedicationsReviewed: false, allergiesReviewed: false, infectionOrClinicalConcerns: '',
    decision: 'proceed' as TreatmentReadinessDecision, decisionReason: '',
  })

  const allReviewed = form.labsReviewed && form.toxicitiesReviewed && form.currentMedicationsReviewed && form.allergiesReviewed

  const submit = () => {
    recordTreatmentReadiness({
      patientId: selectedPatient.id, treatmentPlanId: treatmentPlan?.id, cycleNumber: order?.cycleNumber ?? 1,
      assessmentDate: new Date().toISOString().slice(0, 10), labsReviewed: form.labsReviewed, labsSummary: form.labsSummary || undefined,
      performanceStatus: form.performanceStatus, weightKg: form.weightKg ? Number(form.weightKg) : undefined,
      bsaM2: form.bsaM2 || undefined, toxicitiesReviewed: form.toxicitiesReviewed, toxicitySummary: form.toxicitySummary || undefined,
      previousTreatmentTolerance: form.previousTreatmentTolerance || undefined, doseModificationPlanned: form.doseModificationPlanned || undefined,
      currentMedicationsReviewed: form.currentMedicationsReviewed, allergiesReviewed: form.allergiesReviewed,
      infectionOrClinicalConcerns: form.infectionOrClinicalConcerns || undefined, decision: form.decision,
      decisionReason: form.decisionReason || undefined, assessedBy: actor,
    })
    setForm((f) => ({ ...f, labsSummary: '', toxicitySummary: '', decisionReason: '' }))
  }

  return (
    <PageContainer>
      <PageHeader title="Treatment Readiness" description="Reviewed before every cycle/session — the clinical decision that gates whether this cycle proceeds at all" />

      <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-5">
        <div className="sm:col-span-2"><p className={fieldClassName}>Patient</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn}</p></div>
        <div><p className={fieldClassName}>Regimen</p><p className="mt-1 text-sm font-semibold text-supporting">{order?.regimenName ?? '—'}</p></div>
        <div><p className={fieldClassName}>Assessing cycle</p><p className="mt-1 text-sm font-semibold text-supporting">Cycle {order?.cycleNumber ?? 1}</p></div>
        <div><p className={fieldClassName}>Last decision</p><p className="mt-1">{latest ? <Badge variant={DECISION_META[latest.decision].variant}>{DECISION_META[latest.decision].label}</Badge> : <Badge variant="neutral">None recorded</Badge>}</p></div>
      </CardContent></Card>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Card>
            <CardHeader className="border-b border-divider"><CardTitle>Readiness review</CardTitle><CardDescription>Labs, performance status, toxicities, medications and allergies — reviewed before deciding whether to proceed</CardDescription></CardHeader>
            <CardContent className="space-y-5 pt-6">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex items-start gap-3 rounded-lg border border-border bg-input-background p-3 text-sm text-supporting"><input type="checkbox" className="mt-0.5 size-4 accent-primary" checked={form.labsReviewed} onChange={(e) => setForm((f) => ({ ...f, labsReviewed: e.target.checked }))} />Labs reviewed</label>
                <label className="flex items-start gap-3 rounded-lg border border-border bg-input-background p-3 text-sm text-supporting"><input type="checkbox" className="mt-0.5 size-4 accent-primary" checked={form.toxicitiesReviewed} onChange={(e) => setForm((f) => ({ ...f, toxicitiesReviewed: e.target.checked }))} />Toxicities reviewed</label>
                <label className="flex items-start gap-3 rounded-lg border border-border bg-input-background p-3 text-sm text-supporting"><input type="checkbox" className="mt-0.5 size-4 accent-primary" checked={form.currentMedicationsReviewed} onChange={(e) => setForm((f) => ({ ...f, currentMedicationsReviewed: e.target.checked }))} />Current medications reviewed</label>
                <label className="flex items-start gap-3 rounded-lg border border-border bg-input-background p-3 text-sm text-supporting"><input type="checkbox" className="mt-0.5 size-4 accent-primary" checked={form.allergiesReviewed} onChange={(e) => setForm((f) => ({ ...f, allergiesReviewed: e.target.checked }))} />Allergies reviewed</label>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <label className={fieldClassName}>Labs summary<Input className="mt-1" value={form.labsSummary} onChange={(e) => setForm((f) => ({ ...f, labsSummary: e.target.value }))} placeholder="CBC/ANC, renal, hepatic function" /></label>
                <label className={fieldClassName}>Performance status<Input className="mt-1" value={form.performanceStatus} onChange={(e) => setForm((f) => ({ ...f, performanceStatus: e.target.value }))} /></label>
                <label className={fieldClassName}>Weight (kg)<Input className="mt-1" type="number" value={form.weightKg} onChange={(e) => setForm((f) => ({ ...f, weightKg: e.target.value }))} /></label>
                <label className={fieldClassName}>BSA (m²)<Input className="mt-1" value={form.bsaM2} onChange={(e) => setForm((f) => ({ ...f, bsaM2: e.target.value }))} /></label>
                <label className={fieldClassName}>Toxicity summary<Input className="mt-1" value={form.toxicitySummary} onChange={(e) => setForm((f) => ({ ...f, toxicitySummary: e.target.value }))} placeholder="Grade, resolution status" /></label>
                <label className={fieldClassName}>Previous treatment tolerance<Input className="mt-1" value={form.previousTreatmentTolerance} onChange={(e) => setForm((f) => ({ ...f, previousTreatmentTolerance: e.target.value }))} /></label>
                <label className={fieldClassName}>Dose modification planned<Input className="mt-1" value={form.doseModificationPlanned} onChange={(e) => setForm((f) => ({ ...f, doseModificationPlanned: e.target.value }))} placeholder="None, or refer to Treatment Order" /></label>
                <label className={fieldClassName}>Infection / clinical concerns<Input className="mt-1" value={form.infectionOrClinicalConcerns} onChange={(e) => setForm((f) => ({ ...f, infectionOrClinicalConcerns: e.target.value }))} placeholder="None reported" /></label>
              </div>

              <div className="border-t border-divider pt-5">
                <p className="text-sm font-semibold text-supporting">Treatment decision</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {(Object.keys(DECISION_META) as TreatmentReadinessDecision[]).map((d) => {
                    const meta = DECISION_META[d]
                    const Icon = meta.icon
                    return <Button key={d} type="button" size="sm" variant={form.decision === d ? 'primary' : 'outline'} onClick={() => setForm((f) => ({ ...f, decision: d }))}><Icon />{meta.label}</Button>
                  })}
                </div>
                {form.decision !== 'proceed' ? <label className={`${fieldClassName} mt-3 block`}>Reason<Input className="mt-1" value={form.decisionReason} onChange={(e) => setForm((f) => ({ ...f, decisionReason: e.target.value }))} placeholder="Why this decision was made" /></label> : null}
              </div>

              {!allReviewed ? <p className="rounded-lg border border-warning/25 bg-warning-subtle p-3 text-xs font-semibold text-warning-strong">Labs, toxicities, medications and allergies should all be reviewed before recording a decision.</p> : null}
              <Button type="button" onClick={submit} disabled={form.decision !== 'proceed' && !form.decisionReason.trim()}><ClipboardCheck />Record Readiness Decision</Button>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <RecommendationPanel patientId={selectedPatient.id} context="treatment-readiness" audience="clinician" actor={actor} title="Guideline context for this decision" />

          <Card><CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><History className="size-4 text-brand-deep" /><CardTitle>Readiness history</CardTitle></div></CardHeader>
            <CardContent className="max-h-[420px] space-y-3 overflow-y-auto pt-6">
              {history.length === 0 ? <p className="text-sm text-metadata">No readiness assessments recorded yet.</p> : history.map((h) => {
                const meta = DECISION_META[h.decision]
                const Icon = meta.icon
                return (
                  <div key={h.id} className="rounded-lg border border-divider bg-surface-elevated/70 p-3 text-xs">
                    <div className="flex items-center justify-between gap-2"><span className="font-semibold text-supporting">Cycle {h.cycleNumber} · {h.assessmentDate}</span><Badge variant={meta.variant}><Icon />{meta.label}</Badge></div>
                    <p className="mt-1 text-metadata">{h.assessedBy.name}{h.decisionReason ? ` · ${h.decisionReason}` : ''}</p>
                  </div>
                )
              })}
            </CardContent>
          </Card>

          <Card><CardHeader className="border-b border-divider"><CardTitle>Audit trail</CardTitle></CardHeader>
            <CardContent className="max-h-[420px] space-y-3 overflow-y-auto pt-6">
              {getAuditTrail('TreatmentReadinessAssessment').length === 0 ? <p className="text-sm text-metadata">No recorded changes yet.</p> : getAuditTrail('TreatmentReadinessAssessment').map((entry) => (
                <div key={entry.id} className="border-b border-divider pb-2 text-xs last:border-0 last:pb-0">
                  <p className="font-semibold text-supporting">{entry.action}</p>
                  <p className="text-metadata">{entry.actor.name} · {new Date(entry.timestamp).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </PageContainer>
  )
}
