'use client'

import * as React from 'react'
import { ClipboardCheck, Plus, ScanSearch, Trash2 } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { RecommendationPanel } from '@/components/oncology/recommendation-panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useOncology } from '@/lib/oncology/store'
import { RECIST_CATEGORIES } from '@/lib/oncology/terminology'
import type { ActorRef, Lesion } from '@/lib/oncology/types'

const fieldClassName = 'text-xs font-medium text-metadata'
const selectClassName = 'h-10 w-full min-w-0 rounded-xl border border-input bg-input-background px-3 text-sm text-supporting shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

const RESPONSE_VARIANT: Record<string, 'success' | 'information' | 'warning' | 'critical' | 'neutral'> = {
  CR: 'success', PR: 'success', SD: 'information', PD: 'critical', not_evaluable: 'neutral',
}

let lesionCounter = 0
function newLesion(): Lesion {
  lesionCounter += 1
  return { id: `lesion-draft-${lesionCounter}`, site: '', type: 'target', baselineMeasurementMm: '', followUpMeasurementMm: '' }
}

export default function ResponseAssessmentPage() {
  const { role, selectedPatient } = useDemoAccess()
  const { state, getTreatmentPlan, recordResponseAssessment } = useOncology()

  const actor: ActorRef = { userId: role.roleId, name: role.label, roleLabel: role.label }
  const treatmentPlan = getTreatmentPlan(selectedPatient.id)
  const history = state.responseAssessments.filter((a) => a.patientId === selectedPatient.id)

  const [lesions, setLesions] = React.useState<Lesion[]>([newLesion()])
  const [form, setForm] = React.useState({
    frameworkName: 'RECIST 1.1' as 'RECIST 1.1' | 'clinical_assessment', assessmentDate: new Date().toISOString().slice(0, 10),
    imagingDate: '', responseCategory: 'SD' as (typeof RECIST_CATEGORIES)[number]['value'], diseaseStatus: '', relevantBiomarkers: '',
  })

  const updateLesion = (id: string, changes: Partial<Lesion>) => setLesions((ls) => ls.map((l) => (l.id === id ? { ...l, ...changes } : l)))
  const removeLesion = (id: string) => setLesions((ls) => ls.filter((l) => l.id !== id))

  const submit = () => {
    recordResponseAssessment({
      patientId: selectedPatient.id, frameworkName: form.frameworkName, assessmentDate: form.assessmentDate,
      imagingDate: form.imagingDate || undefined, lesions: lesions.filter((l) => l.site.trim()),
      responseCategory: form.responseCategory, diseaseStatus: form.diseaseStatus || 'Not specified',
      relevantBiomarkers: form.relevantBiomarkers.split(',').map((v) => v.trim()).filter(Boolean), assessedBy: actor,
    })
    setLesions([newLesion()])
    setForm((f) => ({ ...f, diseaseStatus: '', imagingDate: '' }))
  }

  return (
    <PageContainer>
      <PageHeader title="Treatment Response Assessment" description="Baseline and follow-up lesions classified on standard RECIST 1.1 categories — never an arbitrary better/worse label" />

      <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-4">
        <div className="sm:col-span-2"><p className={fieldClassName}>Patient</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn}</p></div>
        <div><p className={fieldClassName}>Diagnosis</p><p className="mt-1 text-sm font-semibold text-supporting">{treatmentPlan?.diagnosis ?? selectedPatient.diagnosis}</p></div>
        <div><p className={fieldClassName}>Latest response</p><p className="mt-1">{history[0] ? <Badge variant={RESPONSE_VARIANT[history[0].responseCategory]}>{RECIST_CATEGORIES.find((c) => c.value === history[0].responseCategory)?.label}</Badge> : <Badge variant="neutral">None recorded</Badge>}</p></div>
      </CardContent></Card>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Card>
            <CardHeader className="border-b border-divider"><CardTitle>Lesion measurements</CardTitle><CardDescription>Baseline vs. follow-up, per lesion</CardDescription></CardHeader>
            <CardContent className="space-y-3 pt-6">
              {lesions.map((lesion) => (
                <div key={lesion.id} className="grid gap-3 rounded-lg border border-divider bg-surface-elevated/70 p-3 sm:grid-cols-[1fr_auto_auto_auto_auto]">
                  <label className={fieldClassName}>Site<Input className="mt-1" value={lesion.site} onChange={(e) => updateLesion(lesion.id, { site: e.target.value })} placeholder="e.g. Left breast, hepatic segment VI" /></label>
                  <label className={fieldClassName}>Type<select className={`${selectClassName} mt-1`} value={lesion.type} onChange={(e) => updateLesion(lesion.id, { type: e.target.value as Lesion['type'] })}><option value="target">Target</option><option value="non_target">Non-target</option><option value="new">New</option></select></label>
                  <label className={fieldClassName}>Baseline (mm)<Input className="mt-1 w-24" value={lesion.baselineMeasurementMm ?? ''} onChange={(e) => updateLesion(lesion.id, { baselineMeasurementMm: e.target.value })} /></label>
                  <label className={fieldClassName}>Follow-up (mm)<Input className="mt-1 w-24" value={lesion.followUpMeasurementMm ?? ''} onChange={(e) => updateLesion(lesion.id, { followUpMeasurementMm: e.target.value })} /></label>
                  <div className="flex items-end"><Button type="button" size="sm" variant="ghost" onClick={() => removeLesion(lesion.id)}><Trash2 /></Button></div>
                </div>
              ))}
              <Button type="button" size="sm" variant="outline" onClick={() => setLesions((ls) => [...ls, newLesion()])}><Plus />Add lesion</Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="border-b border-divider"><CardTitle>Response classification</CardTitle></CardHeader>
            <CardContent className="space-y-5 pt-6">
              <div className="grid gap-4 sm:grid-cols-2">
                <label className={fieldClassName}>Framework<select className={`${selectClassName} mt-1`} value={form.frameworkName} onChange={(e) => setForm((f) => ({ ...f, frameworkName: e.target.value as typeof f.frameworkName }))}><option value="RECIST 1.1">RECIST 1.1</option><option value="clinical_assessment">Clinical assessment</option></select></label>
                <label className={fieldClassName}>Assessment date<Input className="mt-1" type="date" value={form.assessmentDate} onChange={(e) => setForm((f) => ({ ...f, assessmentDate: e.target.value }))} /></label>
                <label className={fieldClassName}>Imaging date<Input className="mt-1" type="date" value={form.imagingDate} onChange={(e) => setForm((f) => ({ ...f, imagingDate: e.target.value }))} /></label>
                <label className={fieldClassName}>Relevant biomarkers (comma-separated)<Input className="mt-1" value={form.relevantBiomarkers} onChange={(e) => setForm((f) => ({ ...f, relevantBiomarkers: e.target.value }))} placeholder="CA 15-3, CEA" /></label>
                <label className={`${fieldClassName} sm:col-span-2`}>Disease status<Input className="mt-1" value={form.diseaseStatus} onChange={(e) => setForm((f) => ({ ...f, diseaseStatus: e.target.value }))} placeholder="e.g. No evidence of progression" /></label>
              </div>

              <div>
                <p className="text-sm font-semibold text-supporting">Response category</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {RECIST_CATEGORIES.map((c) => (
                    <Button key={c.value} type="button" size="sm" variant={form.responseCategory === c.value ? 'primary' : 'outline'} onClick={() => setForm((f) => ({ ...f, responseCategory: c.value }))}>{c.label}</Button>
                  ))}
                </div>
                <p className="mt-2 text-xs leading-5 text-metadata">{RECIST_CATEGORIES.find((c) => c.value === form.responseCategory)?.definition}</p>
              </div>

              <Button type="button" onClick={submit}><ClipboardCheck />Record Response Assessment</Button>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <RecommendationPanel patientId={selectedPatient.id} context="response-assessment" audience="clinician" actor={actor} title="Guideline context for this response" />

          <Card><CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><ScanSearch className="size-4 text-brand-deep" /><CardTitle>Assessment history</CardTitle></div></CardHeader>
            <CardContent className="max-h-[420px] space-y-3 overflow-y-auto pt-6">
              {history.length === 0 ? <p className="text-sm text-metadata">No response assessments recorded yet.</p> : history.map((a) => (
                <div key={a.id} className="rounded-lg border border-divider bg-surface-elevated/70 p-3 text-xs">
                  <div className="flex items-center justify-between gap-2"><span className="font-semibold text-supporting">{a.assessmentDate}</span><Badge variant={RESPONSE_VARIANT[a.responseCategory]}>{a.responseCategory}</Badge></div>
                  <p className="mt-1 text-metadata">{a.diseaseStatus} · {a.lesions.length} lesion(s) tracked</p>
                  <p className="mt-1 text-metadata">{a.assessedBy.name} · {a.frameworkName}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </PageContainer>
  )
}
