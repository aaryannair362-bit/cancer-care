'use client'

import * as React from 'react'
import { AlertTriangle, CalendarClock, CheckCircle2, ClipboardCheck, History, PauseCircle, Radiation, RotateCcw } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { RecommendationPanel } from '@/components/oncology/recommendation-panel'
import { RtStatusPill } from '@/components/oncology/status-pill'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { canAuthorizeRt } from '@/lib/demo-access'
import { useOncology } from '@/lib/oncology/store'
import { TREATMENT_INTENTS } from '@/lib/oncology/terminology'
import type { ActorRef, RtSubStatus, TreatmentIntent } from '@/lib/oncology/types'

const fieldClassName = 'text-xs font-medium text-metadata'

const RT_ADVANCE: Partial<Record<RtSubStatus, { label: string; to: RtSubStatus; requiresAuthority?: boolean }>> = {
  prescribed: { label: 'Send for Simulation', to: 'simulation_pending' },
  simulation_pending: { label: 'Mark Simulation Complete', to: 'simulation_complete' },
  simulation_complete: { label: 'Begin Contouring', to: 'contouring' },
  contouring: { label: 'Move to Planning', to: 'planning' },
  planning: { label: 'Send for Physics QA', to: 'physics_qa' },
  physics_qa: { label: 'Physician Approve', to: 'physician_approved', requiresAuthority: true },
  physician_approved: { label: 'Mark Treatment Ready', to: 'treatment_ready' },
}

function splitList(value: string) {
  return value.split(',').map((v) => v.trim()).filter(Boolean)
}

export default function RadiationOncologyPage() {
  const { role, selectedPatient } = useDemoAccess()
  const {
    state, getTreatmentPlan, createRadiationPrescription, transitionRadiationSubStatus,
    scheduleFractions, recordFractionOutcome, getAuditTrail,
  } = useOncology()

  const actor: ActorRef = { userId: role.roleId, name: role.label, roleLabel: role.label }
  const canAuthorize = canAuthorizeRt(role)
  const treatmentPlan = getTreatmentPlan(selectedPatient.id)
  const prescriptions = state.radiationPrescriptions.filter((p) => p.patientId === selectedPatient.id)
  const prescription = prescriptions.find((p) => p.rtSubStatus !== 'completed') ?? prescriptions[0]

  const [form, setForm] = React.useState({
    diagnosis: treatmentPlan?.diagnosis ?? selectedPatient.diagnosis,
    treatmentSite: '', laterality: '', intent: 'adjuvant' as TreatmentIntent, modality: 'External Beam Radiotherapy (EBRT)',
    technique: '', treatmentPhase: '', totalPrescribedDoseGy: '', dosePerFractionGy: '', numberOfFractions: '',
    frequency: 'Once daily, Monday–Friday', startDate: '', concurrentSystemicTreatment: '', targetVolumes: '',
    organsAtRisk: '', simulationRequired: true, immobilization: '', imageGuidanceRequired: true, bolus: '',
    specialInstructions: '', dicomRtPlanRef: '',
  })

  const [scheduleCount, setScheduleCount] = React.useState('')
  const [scheduleStart, setScheduleStart] = React.useState('')

  const submitPrescription = () => {
    if (!treatmentPlan || !form.treatmentSite.trim() || !form.totalPrescribedDoseGy.trim() || !form.numberOfFractions.trim()) return
    createRadiationPrescription({
      patientId: selectedPatient.id, treatmentPlanId: treatmentPlan.id, diagnosis: form.diagnosis,
      treatmentSite: form.treatmentSite, laterality: form.laterality || undefined, intent: form.intent,
      modality: form.modality, technique: form.technique, treatmentPhase: form.treatmentPhase,
      totalPrescribedDoseGy: form.totalPrescribedDoseGy, dosePerFractionGy: form.dosePerFractionGy,
      numberOfFractions: Number(form.numberOfFractions), frequency: form.frequency, startDate: form.startDate || undefined,
      concurrentSystemicTreatment: form.concurrentSystemicTreatment || undefined,
      targetVolumes: splitList(form.targetVolumes), organsAtRisk: splitList(form.organsAtRisk),
      simulationRequired: form.simulationRequired, immobilization: form.immobilization || undefined,
      imageGuidanceRequired: form.imageGuidanceRequired, bolus: form.bolus || undefined,
      specialInstructions: form.specialInstructions || undefined, dicomRtPlanRef: form.dicomRtPlanRef || undefined,
      createdBy: actor,
    })
  }

  if (!prescription) {
    return (
      <PageContainer>
        <PageHeader title="Radiation Oncology Treatment Prescription" description="Converts an MDT recommendation into a proper RT prescription — separate from the Medical Oncology order" />
        {!treatmentPlan ? (
          <Card><CardContent className="p-6 text-sm text-metadata">No Treatment Plan exists yet for this patient. A prescription can be written once MDT has recommended radiation oncology and a Treatment Plan has been created.</CardContent></Card>
        ) : (
          <Card>
            <CardHeader className="border-b border-divider"><CardTitle>New RT prescription</CardTitle><CardDescription>Diagnosis, site, dose and fractionation — the fields the 2024 safety standards and DICOM RT expect this record to carry</CardDescription></CardHeader>
            <CardContent className="space-y-6 pt-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <label className={fieldClassName}>Diagnosis<Input className="mt-1" value={form.diagnosis} onChange={(e) => setForm((f) => ({ ...f, diagnosis: e.target.value }))} /></label>
                <label className={fieldClassName}>Treatment site<Input className="mt-1" value={form.treatmentSite} onChange={(e) => setForm((f) => ({ ...f, treatmentSite: e.target.value }))} placeholder="e.g. Left chest wall + regional nodes" /></label>
                <label className={fieldClassName}>Laterality<Input className="mt-1" value={form.laterality} onChange={(e) => setForm((f) => ({ ...f, laterality: e.target.value }))} placeholder="Left / Right / Bilateral / N/A" /></label>
                <label className={fieldClassName}>Intent<select className="mt-1 h-10 w-full rounded-xl border border-input bg-input-background px-3 text-sm text-supporting" value={form.intent} onChange={(e) => setForm((f) => ({ ...f, intent: e.target.value as TreatmentIntent }))}>{TREATMENT_INTENTS.map((i) => <option key={i.value} value={i.value}>{i.display}</option>)}</select></label>
                <label className={fieldClassName}>Modality<Input className="mt-1" value={form.modality} onChange={(e) => setForm((f) => ({ ...f, modality: e.target.value }))} /></label>
                <label className={fieldClassName}>Technique<Input className="mt-1" value={form.technique} onChange={(e) => setForm((f) => ({ ...f, technique: e.target.value }))} placeholder="e.g. IMRT, VMAT, 3D-CRT" /></label>
                <label className={fieldClassName}>Treatment phase<Input className="mt-1" value={form.treatmentPhase} onChange={(e) => setForm((f) => ({ ...f, treatmentPhase: e.target.value }))} placeholder="e.g. Whole-breast, boost" /></label>
              </div>

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <label className={fieldClassName}>Total prescribed dose (Gy)<Input className="mt-1" value={form.totalPrescribedDoseGy} onChange={(e) => setForm((f) => ({ ...f, totalPrescribedDoseGy: e.target.value }))} placeholder="e.g. 50" /></label>
                <label className={fieldClassName}>Dose per fraction (Gy)<Input className="mt-1" value={form.dosePerFractionGy} onChange={(e) => setForm((f) => ({ ...f, dosePerFractionGy: e.target.value }))} placeholder="e.g. 2" /></label>
                <label className={fieldClassName}>Number of fractions<Input className="mt-1" type="number" value={form.numberOfFractions} onChange={(e) => setForm((f) => ({ ...f, numberOfFractions: e.target.value }))} placeholder="e.g. 25" /></label>
                <label className={fieldClassName}>Frequency<Input className="mt-1" value={form.frequency} onChange={(e) => setForm((f) => ({ ...f, frequency: e.target.value }))} /></label>
                <label className={fieldClassName}>Start date<Input className="mt-1" type="date" value={form.startDate} onChange={(e) => setForm((f) => ({ ...f, startDate: e.target.value }))} /></label>
                <label className={fieldClassName}>Concurrent systemic treatment<Input className="mt-1" value={form.concurrentSystemicTreatment} onChange={(e) => setForm((f) => ({ ...f, concurrentSystemicTreatment: e.target.value }))} placeholder="None, or drug name" /></label>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <label className={fieldClassName}>Target volumes (comma-separated)<Input className="mt-1" value={form.targetVolumes} onChange={(e) => setForm((f) => ({ ...f, targetVolumes: e.target.value }))} placeholder="PTV_Breast, PTV_Boost" /></label>
                <label className={fieldClassName}>Organs at risk (comma-separated)<Input className="mt-1" value={form.organsAtRisk} onChange={(e) => setForm((f) => ({ ...f, organsAtRisk: e.target.value }))} placeholder="Heart, ipsilateral lung" /></label>
                <label className={fieldClassName}>Immobilization<Input className="mt-1" value={form.immobilization} onChange={(e) => setForm((f) => ({ ...f, immobilization: e.target.value }))} placeholder="e.g. Breast board" /></label>
                <label className={fieldClassName}>Bolus<Input className="mt-1" value={form.bolus} onChange={(e) => setForm((f) => ({ ...f, bolus: e.target.value }))} placeholder="Not applicable, or details" /></label>
                <label className={`${fieldClassName} sm:col-span-2`}>Special instructions<Input className="mt-1" value={form.specialInstructions} onChange={(e) => setForm((f) => ({ ...f, specialInstructions: e.target.value }))} /></label>
                <label className={`${fieldClassName} sm:col-span-2`}>DICOM RT Plan reference (external planning/OIS system)<Input className="mt-1" value={form.dicomRtPlanRef} onChange={(e) => setForm((f) => ({ ...f, dicomRtPlanRef: e.target.value }))} placeholder="e.g. plan ID from your TPS/OIS — referenced, not stored here" /></label>
              </div>

              <div className="flex flex-wrap gap-4">
                <label className="flex items-center gap-2 text-sm text-supporting"><input type="checkbox" className="size-4 accent-primary" checked={form.simulationRequired} onChange={(e) => setForm((f) => ({ ...f, simulationRequired: e.target.checked }))} />Simulation required</label>
                <label className="flex items-center gap-2 text-sm text-supporting"><input type="checkbox" className="size-4 accent-primary" checked={form.imageGuidanceRequired} onChange={(e) => setForm((f) => ({ ...f, imageGuidanceRequired: e.target.checked }))} />Image guidance required</label>
              </div>

              <p className="rounded-lg border border-information/25 bg-information-subtle p-3 text-xs leading-5 text-information-strong">This is a workflow/order layer, not a treatment-planning system. Real dose calculation, contouring and plan optimization happen in your planning/OIS system — this record carries the prescription and references that system's plan, it does not replace it.</p>

              <Button type="button" onClick={submitPrescription} disabled={!form.treatmentSite.trim() || !form.totalPrescribedDoseGy.trim() || !form.numberOfFractions.trim()}><Radiation />Create RT Prescription</Button>
            </CardContent>
          </Card>
        )}
      </PageContainer>
    )
  }

  const fractions = state.radiationFractions.filter((f) => f.prescriptionId === prescription.id).sort((a, b) => a.fractionNumber - b.fractionNumber)
  const delivered = fractions.filter((f) => f.status === 'delivered')
  const cumulativeDose = delivered.reduce((sum, f) => sum + (Number(f.deliveredDoseGy) || Number(prescription.dosePerFractionGy) || 0), 0)
  const advance = RT_ADVANCE[prescription.rtSubStatus]
  const audit = getAuditTrail('RadiationPrescription', prescription.id)

  const doAdvance = () => {
    if (!advance) return
    transitionRadiationSubStatus(prescription.id, advance.to, actor)
  }
  const returnToPlanning = () => transitionRadiationSubStatus(prescription.id, 'planning', actor)
  const beginTreatment = () => {
    const count = Number(scheduleCount || prescription.numberOfFractions)
    const start = scheduleStart || new Date().toISOString().slice(0, 10)
    scheduleFractions(prescription.id, count, start)
    transitionRadiationSubStatus(prescription.id, 'on_treatment', actor)
  }
  const markInterrupted = () => transitionRadiationSubStatus(prescription.id, 'interrupted', actor)
  const resumeTreatment = () => transitionRadiationSubStatus(prescription.id, 'on_treatment', actor)
  const completeCourse = () => transitionRadiationSubStatus(prescription.id, 'completed', actor)

  const markFraction = (id: string, status: 'delivered' | 'missed' | 'rescheduled') => {
    recordFractionOutcome(id, {
      status,
      deliveredDoseGy: status === 'delivered' ? prescription.dosePerFractionGy : undefined,
      deliveredAt: status === 'delivered' ? new Date().toISOString() : undefined,
      deliveredBy: status === 'delivered' ? actor : undefined,
    })
  }

  return (
    <PageContainer>
      <PageHeader
        title="Radiation Oncology Treatment Prescription"
        description="Prescription, planning workflow and fraction-by-fraction course — separate from the Medical Oncology order"
        actions={advance ? (
          <Button type="button" disabled={advance.requiresAuthority && !canAuthorize} onClick={doAdvance}>
            <ClipboardCheck />{advance.requiresAuthority && !canAuthorize ? 'Requires radiation oncologist' : advance.label}
          </Button>
        ) : undefined}
      />

      <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-5">
        <div className="sm:col-span-2"><p className={fieldClassName}>Patient</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn}</p></div>
        <div><p className={fieldClassName}>Site / laterality</p><p className="mt-1 text-sm font-semibold text-supporting">{prescription.treatmentSite}{prescription.laterality ? ` · ${prescription.laterality}` : ''}</p></div>
        <div><p className={fieldClassName}>Prescription</p><p className="mt-1 text-sm font-semibold text-supporting">{prescription.totalPrescribedDoseGy} Gy / {prescription.numberOfFractions} fractions</p></div>
        <div><p className={fieldClassName}>Status</p><p className="mt-1"><RtStatusPill status={prescription.rtSubStatus} /></p></div>
      </CardContent></Card>

      {prescription.rtSubStatus === 'physics_qa' ? (
        <div className="mb-6"><Button type="button" size="sm" variant="outline" onClick={returnToPlanning}><RotateCcw />Return to Planning</Button></div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Card>
            <CardHeader className="border-b border-divider"><CardTitle>Prescription details</CardTitle></CardHeader>
            <CardContent className="grid gap-4 pt-6 sm:grid-cols-3">
              {[['Diagnosis', prescription.diagnosis], ['Intent', TREATMENT_INTENTS.find((i) => i.value === prescription.intent)?.display ?? prescription.intent],
                ['Modality', prescription.modality], ['Technique', prescription.technique || '—'], ['Treatment phase', prescription.treatmentPhase || '—'],
                ['Frequency', prescription.frequency], ['Start date', prescription.startDate ?? '—'], ['Concurrent systemic Rx', prescription.concurrentSystemicTreatment ?? 'None'],
                ['Simulation required', prescription.simulationRequired ? 'Yes' : 'No'], ['Immobilization', prescription.immobilization ?? '—'],
                ['Image guidance required', prescription.imageGuidanceRequired ? 'Yes' : 'No'], ['Bolus', prescription.bolus ?? '—']]
                .map(([label, value]) => <div key={label}><p className={fieldClassName}>{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}
              {prescription.targetVolumes.length > 0 ? <div className="sm:col-span-3"><p className={fieldClassName}>Target volumes</p><div className="mt-1 flex flex-wrap gap-1.5">{prescription.targetVolumes.map((v) => <Badge key={v} variant="neutral">{v}</Badge>)}</div></div> : null}
              {prescription.organsAtRisk.length > 0 ? <div className="sm:col-span-3"><p className={fieldClassName}>Organs at risk</p><div className="mt-1 flex flex-wrap gap-1.5">{prescription.organsAtRisk.map((v) => <Badge key={v} variant="warning">{v}</Badge>)}</div></div> : null}
              {prescription.specialInstructions ? <div className="sm:col-span-3"><p className={fieldClassName}>Special instructions</p><p className="mt-1 text-sm text-supporting">{prescription.specialInstructions}</p></div> : null}
              {prescription.dicomRtPlanRef ? <div className="sm:col-span-3"><p className={fieldClassName}>DICOM RT Plan reference</p><p className="mt-1 font-mono text-xs text-metadata">{prescription.dicomRtPlanRef} (external planning/OIS system — not stored here)</p></div> : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><CardTitle>Treatment course</CardTitle><Badge variant="information">{delivered.length} / {prescription.numberOfFractions} fractions delivered</Badge></div><CardDescription>Delivered cumulative dose ≈ {cumulativeDose.toFixed(1)} Gy of {prescription.totalPrescribedDoseGy} Gy prescribed</CardDescription></CardHeader>
            <CardContent className="pt-6">
              {fractions.length === 0 ? (
                (prescription.rtSubStatus === 'treatment_ready') ? (
                  <div className="grid gap-3 sm:grid-cols-3">
                    <label className={fieldClassName}>Fraction count<Input className="mt-1" type="number" value={scheduleCount} onChange={(e) => setScheduleCount(e.target.value)} placeholder={String(prescription.numberOfFractions)} /></label>
                    <label className={fieldClassName}>Start date<Input className="mt-1" type="date" value={scheduleStart} onChange={(e) => setScheduleStart(e.target.value)} /></label>
                    <div className="flex items-end"><Button type="button" onClick={beginTreatment}><Radiation />Schedule Fractions & Begin</Button></div>
                  </div>
                ) : <p className="text-sm text-metadata">Fractions are scheduled once the prescription reaches Treatment Ready.</p>
              ) : (
                <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
                  {fractions.map((f) => (
                    <div key={f.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-divider bg-surface-elevated/70 p-3 text-sm">
                      <div><span className="font-semibold text-supporting">Fraction {f.fractionNumber}</span><span className="ml-2 text-xs text-metadata">{f.scheduledDate}{f.deliveredDoseGy ? ` · ${f.deliveredDoseGy} Gy delivered` : ''}</span></div>
                      <div className="flex items-center gap-2">
                        <Badge variant={f.status === 'delivered' ? 'success' : f.status === 'missed' ? 'critical' : f.status === 'rescheduled' ? 'warning' : 'neutral'}>{f.status}</Badge>
                        {prescription.rtSubStatus === 'on_treatment' && f.status === 'scheduled' ? (
                          <>
                            <Button type="button" size="sm" variant="outline" onClick={() => markFraction(f.id, 'delivered')}><CheckCircle2 />Delivered</Button>
                            <Button type="button" size="sm" variant="outline" onClick={() => markFraction(f.id, 'missed')}><AlertTriangle />Missed</Button>
                          </>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {prescription.rtSubStatus === 'on_treatment' ? (
                <div className="mt-4 flex flex-wrap gap-2 border-t border-divider pt-4">
                  <Button type="button" variant="destructive" size="sm" onClick={markInterrupted}><PauseCircle />Record Interruption</Button>
                  {delivered.length >= prescription.numberOfFractions ? <Button type="button" size="sm" onClick={completeCourse}><CheckCircle2 />Mark Course Complete</Button> : null}
                </div>
              ) : null}
              {prescription.rtSubStatus === 'interrupted' ? (
                <div className="mt-4 flex flex-wrap gap-2 border-t border-divider pt-4"><Button type="button" size="sm" onClick={resumeTreatment}><CalendarClock />Resume Treatment</Button></div>
              ) : null}
              {prescription.rtSubStatus === 'completed' ? <Badge className="mt-4" variant="success"><CheckCircle2 />Course completed</Badge> : null}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <RecommendationPanel patientId={selectedPatient.id} context="radiation-prescription" audience="clinician" actor={actor} title="Guideline context for this prescription" />

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
        </div>
      </div>
    </PageContainer>
  )
}
