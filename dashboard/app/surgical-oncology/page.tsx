'use client'

import * as React from 'react'
import { CheckCircle2, ClipboardCheck, History, Scissors, Users } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { RecommendationPanel } from '@/components/oncology/recommendation-panel'
import { SurgicalStatusPill } from '@/components/oncology/status-pill'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { canAuthorizeSurgery } from '@/lib/demo-access'
import { useOncology } from '@/lib/oncology/store'
import type { ActorRef, SurgicalSubStatus } from '@/lib/oncology/types'

const fieldClassName = 'text-xs font-medium text-metadata'

const SURG_ADVANCE: Partial<Record<SurgicalSubStatus, { label: string; to: SurgicalSubStatus; requiresAuthority?: boolean }>> = {
  recommended: { label: 'Surgeon Review Complete', to: 'surgeon_reviewed', requiresAuthority: true },
  surgeon_reviewed: { label: 'Confirm Surgical Plan', to: 'planned', requiresAuthority: true },
  planned: { label: 'Mark Pre-op Ready', to: 'pre_op_ready' },
  pre_op_ready: { label: 'Schedule Surgery', to: 'scheduled' },
  performed: { label: 'Move to Post-op', to: 'post_op' },
}

const emptyForm = {
  procedure: '', indication: '', intent: 'Curative', anatomicalSite: '', laterality: '', proposedExtent: '',
  approach: '', nodalProcedure: '', reconstruction: '', plannedDate: '', priority: 'routine' as 'routine' | 'urgent' | 'emergency',
  preOpRequirements: 'Laboratory review, Imaging review, Anaesthetic fitness, Consent', requiredImagingPathology: '',
  anaesthesiaClearance: '', bloodRequirement: '', specialInstructions: '',
}

function splitList(value: string) {
  return value.split(',').map((v) => v.trim()).filter(Boolean)
}

export default function SurgicalOncologyPage() {
  const { role, selectedPatient } = useDemoAccess()
  const { state, getTreatmentPlan, createSurgicalPlan, transitionSurgicalSubStatus, recordOperativeOutcome, getAuditTrail } = useOncology()

  const actor: ActorRef = { userId: role.roleId, name: role.label, roleLabel: role.label }
  const canAuthorize = canAuthorizeSurgery(role)
  const treatmentPlan = getTreatmentPlan(selectedPatient.id)
  const mdtCase = state.mdtCases.find((c) => c.patientId === selectedPatient.id)
  const plans = state.surgicalPlans.filter((p) => p.patientId === selectedPatient.id).sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
  const plan = plans.find((p) => p.surgicalSubStatus !== 'histopathology_available') ?? plans[0]
  const history = plans.filter((p) => p.id !== plan?.id)

  const [showForm, setShowForm] = React.useState(!plan)
  const [form, setForm] = React.useState(emptyForm)
  const [outcome, setOutcome] = React.useState({ performedProcedure: '', operativeFindings: '' })
  const [histopath, setHistopath] = React.useState({ summary: '', feedBack: true })

  const submitPlan = () => {
    if (!treatmentPlan || !form.procedure.trim() || !form.anatomicalSite.trim()) return
    createSurgicalPlan({
      patientId: selectedPatient.id, treatmentPlanId: treatmentPlan.id, procedure: form.procedure, indication: form.indication,
      intent: form.intent, anatomicalSite: form.anatomicalSite, laterality: form.laterality || undefined, proposedExtent: form.proposedExtent,
      approach: form.approach, nodalProcedure: form.nodalProcedure || undefined, reconstruction: form.reconstruction || undefined,
      plannedDate: form.plannedDate || undefined, priority: form.priority, preOpRequirements: splitList(form.preOpRequirements),
      requiredImagingPathology: splitList(form.requiredImagingPathology), anaesthesiaClearance: form.anaesthesiaClearance || undefined,
      bloodRequirement: form.bloodRequirement || undefined, specialInstructions: form.specialInstructions || undefined, recommendedBy: actor,
    })
    setForm(emptyForm)
    setShowForm(false)
  }

  const advance = plan ? SURG_ADVANCE[plan.surgicalSubStatus] : undefined
  const audit = plan ? getAuditTrail('SurgicalPlan', plan.id) : []

  const doAdvance = () => { if (plan && advance) transitionSurgicalSubStatus(plan.id, advance.to, actor) }
  const submitPerformed = () => {
    if (!plan || !outcome.performedProcedure.trim()) return
    recordOperativeOutcome(plan.id, { performedProcedure: outcome.performedProcedure, performedAt: new Date().toISOString(), operativeFindings: outcome.operativeFindings }, actor)
    transitionSurgicalSubStatus(plan.id, 'performed', actor)
  }
  const submitHistopathology = () => {
    if (!plan || !histopath.summary.trim()) return
    recordOperativeOutcome(plan.id, { histopathologySummary: histopath.summary, histopathologyAvailable: true, fedBackToMdtCaseId: histopath.feedBack ? mdtCase?.id : undefined }, actor)
    transitionSurgicalSubStatus(plan.id, 'histopathology_available', actor)
  }

  return (
    <PageContainer>
      <PageHeader
        title="Surgical Oncology Treatment Order"
        description="Surgical plan, pre-op readiness, and the planned-vs-performed distinction — feeds histopathology back into MDT"
        actions={plan && (plan.surgicalSubStatus === 'histopathology_available' || !plan) ? (
          <Button type="button" variant="outline" onClick={() => setShowForm((s) => !s)}><Scissors />New Surgical Plan</Button>
        ) : undefined}
      />

      <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-4">
        <div className="sm:col-span-2"><p className={fieldClassName}>Patient</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn}</p></div>
        <div><p className={fieldClassName}>Diagnosis</p><p className="mt-1 text-sm font-semibold text-supporting">{treatmentPlan?.diagnosis ?? selectedPatient.diagnosis}</p></div>
        <div><p className={fieldClassName}>Active plan status</p><p className="mt-1">{plan ? <SurgicalStatusPill status={plan.surgicalSubStatus} /> : <Badge variant="neutral">No plan yet</Badge>}</p></div>
      </CardContent></Card>

      {showForm ? (
        <Card className="mb-6">
          <CardHeader className="border-b border-divider"><CardTitle>{plan ? 'Record new surgical plan' : 'Surgical plan'}</CardTitle><CardDescription>Recorded after MDT recommendation — the surgeon reviews and authorizes before this becomes an active plan</CardDescription></CardHeader>
          <CardContent className="space-y-6 pt-6">
            {!treatmentPlan ? <p className="rounded-lg border border-warning/25 bg-warning-subtle p-3 text-xs font-semibold text-warning-strong">No Treatment Plan exists yet for this patient — one is required before a surgical plan can be recorded.</p> : null}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <label className={fieldClassName}>Procedure<Input className="mt-1" value={form.procedure} onChange={(e) => setForm((f) => ({ ...f, procedure: e.target.value }))} placeholder="e.g. Lumpectomy with sentinel-node biopsy" /></label>
              <label className={fieldClassName}>Indication<Input className="mt-1" value={form.indication} onChange={(e) => setForm((f) => ({ ...f, indication: e.target.value }))} /></label>
              <label className={fieldClassName}>Intent<Input className="mt-1" value={form.intent} onChange={(e) => setForm((f) => ({ ...f, intent: e.target.value }))} placeholder="Curative / Diagnostic / Palliative" /></label>
              <label className={fieldClassName}>Anatomical site<Input className="mt-1" value={form.anatomicalSite} onChange={(e) => setForm((f) => ({ ...f, anatomicalSite: e.target.value }))} /></label>
              <label className={fieldClassName}>Laterality<Input className="mt-1" value={form.laterality} onChange={(e) => setForm((f) => ({ ...f, laterality: e.target.value }))} placeholder="Left / Right / N/A" /></label>
              <label className={fieldClassName}>Proposed extent<Input className="mt-1" value={form.proposedExtent} onChange={(e) => setForm((f) => ({ ...f, proposedExtent: e.target.value }))} /></label>
              <label className={fieldClassName}>Approach<Input className="mt-1" value={form.approach} onChange={(e) => setForm((f) => ({ ...f, approach: e.target.value }))} placeholder="Open / Laparoscopic / Robotic" /></label>
              <label className={fieldClassName}>Nodal procedure<Input className="mt-1" value={form.nodalProcedure} onChange={(e) => setForm((f) => ({ ...f, nodalProcedure: e.target.value }))} /></label>
              <label className={fieldClassName}>Reconstruction<Input className="mt-1" value={form.reconstruction} onChange={(e) => setForm((f) => ({ ...f, reconstruction: e.target.value }))} /></label>
              <label className={fieldClassName}>Planned date<Input className="mt-1" type="date" value={form.plannedDate} onChange={(e) => setForm((f) => ({ ...f, plannedDate: e.target.value }))} /></label>
              <label className={fieldClassName}>Priority<select className="mt-1 h-10 w-full rounded-xl border border-input bg-input-background px-3 text-sm text-supporting" value={form.priority} onChange={(e) => setForm((f) => ({ ...f, priority: e.target.value as typeof form.priority }))}><option value="routine">Routine</option><option value="urgent">Urgent</option><option value="emergency">Emergency</option></select></label>
              <label className={fieldClassName}>Anaesthesia clearance<Input className="mt-1" value={form.anaesthesiaClearance} onChange={(e) => setForm((f) => ({ ...f, anaesthesiaClearance: e.target.value }))} placeholder="Pending / Cleared" /></label>
              <label className={fieldClassName}>Blood requirement<Input className="mt-1" value={form.bloodRequirement} onChange={(e) => setForm((f) => ({ ...f, bloodRequirement: e.target.value }))} placeholder="None / Cross-match X units" /></label>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className={fieldClassName}>Pre-op requirements (comma-separated)<Input className="mt-1" value={form.preOpRequirements} onChange={(e) => setForm((f) => ({ ...f, preOpRequirements: e.target.value }))} /></label>
              <label className={fieldClassName}>Required imaging / pathology (comma-separated)<Input className="mt-1" value={form.requiredImagingPathology} onChange={(e) => setForm((f) => ({ ...f, requiredImagingPathology: e.target.value }))} placeholder="Diagnostic mammogram, Core biopsy report" /></label>
              <label className={`${fieldClassName} sm:col-span-2`}>Special instructions<Input className="mt-1" value={form.specialInstructions} onChange={(e) => setForm((f) => ({ ...f, specialInstructions: e.target.value }))} /></label>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="button" onClick={submitPlan} disabled={!treatmentPlan || !form.procedure.trim() || !form.anatomicalSite.trim()}><Scissors />Record Surgical Plan</Button>
              {plan ? <Button type="button" variant="outline" onClick={() => setShowForm(false)}>Cancel</Button> : null}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {plan ? (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <div className="space-y-6">
            <Card>
              <CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><CardTitle>Surgical plan</CardTitle>{advance ? <Button type="button" size="sm" disabled={advance.requiresAuthority && !canAuthorize} onClick={doAdvance}><ClipboardCheck />{advance.requiresAuthority && !canAuthorize ? 'Requires surgical oncologist' : advance.label}</Button> : null}</div></CardHeader>
              <CardContent className="grid gap-4 pt-6 sm:grid-cols-3">
                {[['Procedure (planned)', plan.procedure], ['Indication', plan.indication], ['Intent', plan.intent], ['Anatomical site', plan.anatomicalSite], ['Laterality', plan.laterality ?? '—'],
                  ['Proposed extent', plan.proposedExtent], ['Approach', plan.approach], ['Nodal procedure', plan.nodalProcedure ?? '—'], ['Reconstruction', plan.reconstruction ?? '—'],
                  ['Planned date', plan.plannedDate ?? '—'], ['Priority', plan.priority], ['Anaesthesia clearance', plan.anaesthesiaClearance ?? '—']]
                  .map(([label, value]) => <div key={label}><p className={fieldClassName}>{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}
                {plan.preOpRequirements.length > 0 ? <div className="sm:col-span-3"><p className={fieldClassName}>Pre-op requirements</p><div className="mt-1 flex flex-wrap gap-1.5">{plan.preOpRequirements.map((r) => <Badge key={r} variant="neutral">{r}</Badge>)}</div></div> : null}
              </CardContent>
            </Card>

            {plan.surgicalSubStatus === 'scheduled' ? (
              <Card variant="elevated"><CardHeader className="border-b border-divider"><CardTitle>Record surgery performed</CardTitle><CardDescription>Distinguishes what was actually done from what was planned</CardDescription></CardHeader>
                <CardContent className="space-y-4 pt-6">
                  <label className={fieldClassName}>Procedure actually performed<Input className="mt-1" value={outcome.performedProcedure || plan.procedure} onChange={(e) => setOutcome((o) => ({ ...o, performedProcedure: e.target.value }))} /></label>
                  <label className={fieldClassName}>Operative findings<Input className="mt-1" value={outcome.operativeFindings} onChange={(e) => setOutcome((o) => ({ ...o, operativeFindings: e.target.value }))} placeholder="Margins, complications, intraoperative findings" /></label>
                  <Button type="button" disabled={!canAuthorize} onClick={submitPerformed}><CheckCircle2 />Record Performed</Button>
                </CardContent>
              </Card>
            ) : null}

            {plan.surgicalSubStatus === 'post_op' ? (
              <Card variant="elevated"><CardHeader className="border-b border-divider"><CardTitle>Histopathology & MDT feedback</CardTitle></CardHeader>
                <CardContent className="space-y-4 pt-6">
                  <label className={fieldClassName}>Histopathology summary<Input className="mt-1" value={histopath.summary} onChange={(e) => setHistopath((h) => ({ ...h, summary: e.target.value }))} placeholder="Grade, stage, margins, receptor status" /></label>
                  <label className="flex items-center gap-2 text-sm text-supporting"><input type="checkbox" className="size-4 accent-primary" checked={histopath.feedBack} disabled={!mdtCase} onChange={(e) => setHistopath((h) => ({ ...h, feedBack: e.target.checked }))} />Feed back to MDT case {mdtCase ? `(${mdtCase.id})` : '(none linked)'} for adjuvant planning</label>
                  <Button type="button" disabled={!canAuthorize || !histopath.summary.trim()} onClick={submitHistopathology}><Users />Record Histopathology</Button>
                </CardContent>
              </Card>
            ) : null}

            {plan.surgicalSubStatus === 'histopathology_available' ? (
              <Card variant="elevated"><CardHeader className="border-b border-divider"><CardTitle>Post-operative outcome</CardTitle></CardHeader>
                <CardContent className="grid gap-4 pt-6 sm:grid-cols-2">
                  <div><p className={fieldClassName}>Procedure performed</p><p className="mt-1 text-sm font-semibold text-supporting">{plan.performedProcedure ?? plan.procedure}</p></div>
                  <div><p className={fieldClassName}>Performed at</p><p className="mt-1 text-sm font-semibold text-supporting">{plan.performedAt ? new Date(plan.performedAt).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—'}</p></div>
                  <div className="sm:col-span-2"><p className={fieldClassName}>Operative findings</p><p className="mt-1 text-sm text-supporting">{plan.operativeFindings ?? '—'}</p></div>
                  <div className="sm:col-span-2"><p className={fieldClassName}>Histopathology</p><p className="mt-1 text-sm text-supporting">{plan.histopathologySummary ?? '—'}</p></div>
                  {plan.fedBackToMdtCaseId ? <Badge variant="success" className="sm:col-span-2 w-fit"><Users />Fed back to MDT case for adjuvant planning</Badge> : null}
                </CardContent>
              </Card>
            ) : null}
          </div>

          <div className="space-y-6">
            <RecommendationPanel patientId={selectedPatient.id} context="surgical-plan" audience="clinician" actor={actor} title="Guideline context for this plan" />

            <Card><CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><History className="size-4 text-brand-deep" /><CardTitle>Audit trail</CardTitle></div></CardHeader>
              <CardContent className="max-h-[420px] space-y-3 overflow-y-auto pt-6">
                {audit.length === 0 ? <p className="text-sm text-metadata">No recorded changes yet.</p> : audit.map((entry) => (
                  <div key={entry.id} className="border-b border-divider pb-2 text-xs last:border-0 last:pb-0">
                    <p className="font-semibold text-supporting">{entry.action}</p>
                    <p className="text-metadata">{entry.actor.name} · {new Date(entry.timestamp).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}</p>
                  </div>
                ))}
              </CardContent>
            </Card>

            {history.length > 0 ? (
              <Card><CardHeader className="border-b border-divider"><CardTitle>Other surgical plans</CardTitle></CardHeader>
                <CardContent className="max-h-[420px] space-y-3 overflow-y-auto pt-6">
                  {history.map((h) => (
                    <div key={h.id} className="rounded-lg border border-divider bg-surface-elevated/70 p-3 text-sm"><p className="font-semibold text-supporting">{h.performedProcedure ?? h.procedure}</p><p className="mt-1 text-xs text-metadata"><SurgicalStatusPill status={h.surgicalSubStatus} /></p></div>
                  ))}
                </CardContent>
              </Card>
            ) : null}
          </div>
        </div>
      ) : null}
    </PageContainer>
  )
}
