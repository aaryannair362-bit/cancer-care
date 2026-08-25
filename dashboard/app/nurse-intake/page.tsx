'use client'

import * as React from 'react'
import Link from 'next/link'
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  ClipboardCheck,
  FileHeart,
  HeartPulse,
  NotebookPen,
  Pill,
  Plus,
  ShieldAlert,
  Stethoscope,
  UserRound,
} from 'lucide-react'

import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { useDemoAccess } from '@/components/demo-access-provider'
import { NurseHandoffSummary, PreviousDocumentsSection, StructuredOncologyHistory } from '@/components/nurse-intake-enhancements'

type IntakeValues = {
  chiefConcern: string
  symptomOnset: string
  painScore: string
  oncologyHistory: string
  currentTreatment: string
  treatmentCycle: string
  temperature: string
  pulse: string
  respiratoryRate: string
  systolic: string
  diastolic: string
  oxygenSaturation: string
  weight: string
  height: string
  ecog: string
  fallRisk: string
  nutritionConcern: string
  observations: string
  handoffPriority: string
  identityVerified: boolean
  medicationReconciled: boolean
  allergiesReviewed: boolean
}

type Errors = Partial<Record<keyof IntakeValues, string>>

const initialValues: IntakeValues = {
  chiefConcern: 'Increasing fatigue and mild nausea after the second chemotherapy cycle.',
  symptomOnset: '2026-08-20',
  painScore: '2',
  oncologyHistory: 'Stage IIA left breast invasive ductal carcinoma, ER/PR positive, HER2 negative. Lumpectomy completed in June 2026. No known metastatic disease.',
  currentTreatment: 'Adjuvant doxorubicin and cyclophosphamide',
  treatmentCycle: 'Cycle 2, day 8',
  temperature: '37.2',
  pulse: '88',
  respiratoryRate: '16',
  systolic: '118',
  diastolic: '76',
  oxygenSaturation: '98',
  weight: '61.4',
  height: '162',
  ecog: '1',
  fallRisk: 'low',
  nutritionConcern: 'mild',
  observations: 'Alert and oriented. Mild pallor noted. Oral intake reduced for two days; tolerating fluids. No fever, dyspnoea, chest pain, or active vomiting reported.',
  handoffPriority: 'routine',
  identityVerified: true,
  medicationReconciled: true,
  allergiesReviewed: false,
}

const symptoms = [
  'Fatigue',
  'Nausea',
  'Vomiting',
  'Pain',
  'Fever or chills',
  'Breathlessness',
  'Neuropathy',
  'Mouth sores',
  'Constipation',
  'Diarrhoea',
  'Reduced appetite',
  'Bleeding or bruising',
]

const initialSymptoms = ['Fatigue', 'Nausea', 'Reduced appetite']

const initialMedications = [
  { name: 'Ondansetron', dose: '8 mg', schedule: 'As needed for nausea', status: 'Active' },
  { name: 'Dexamethasone', dose: '4 mg', schedule: 'Twice daily, days 2–4 post-treatment', status: 'Active' },
  { name: 'Pantoprazole', dose: '40 mg', schedule: 'Once daily', status: 'Active' },
]

function FieldLabel({ htmlFor, children, required }: { htmlFor: string; children: React.ReactNode; required?: boolean }) {
  return <label htmlFor={htmlFor} className="text-sm font-medium text-supporting">{children}{required ? <span className="ml-1 text-critical" aria-hidden="true">*</span> : null}</label>
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null
  return <p id={id} className="flex items-center gap-1.5 text-xs text-critical-strong"><AlertCircle className="size-3.5 shrink-0" aria-hidden="true" />{message}</p>
}

function SectionHeading({ icon: Icon, title, description, action }: { icon: typeof UserRound; title: string; description: string; action?: React.ReactNode }) {
  return (
    <CardHeader className="border-b border-divider pb-4">
      <div className="flex items-start gap-3">
        <span className="aivana-gradient-soft flex size-9 shrink-0 items-center justify-center rounded-md text-supporting shadow-soft-sm"><Icon className="size-4" aria-hidden="true" /></span>
        <div className="min-w-0 flex-1"><CardTitle>{title}</CardTitle><CardDescription className="mt-1">{description}</CardDescription></div>
        {action}
      </div>
    </CardHeader>
  )
}

const controlClassName = 'flex h-10 w-full rounded-md border border-input bg-input-background px-3 py-2 text-sm text-foreground shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1'

export default function NurseIntakePage() {
  const { role, tasks, performAction } = useDemoAccess()
  const nurseTasks = tasks.filter((task) => task.destination === 'Nurse Intake')
  const [values, setValues] = React.useState<IntakeValues>(initialValues)
  const [selectedSymptoms, setSelectedSymptoms] = React.useState<string[]>(initialSymptoms)
  const [errors, setErrors] = React.useState<Errors>({})
  const [completed, setCompleted] = React.useState(false)
  const [draftSaved, setDraftSaved] = React.useState(false)
  const [medications, setMedications] = React.useState(initialMedications)
  const heightCm = Number(values.height)
  const weightKg = Number(values.weight)
  const bmi = heightCm > 0 && weightKg > 0 ? weightKg / ((heightCm / 100) ** 2) : 0
  const bsa = heightCm > 0 && weightKg > 0 ? 0.007184 * (heightCm ** 0.725) * (weightKg ** 0.425) : 0

  const update = <K extends keyof IntakeValues>(field: K, value: IntakeValues[K]) => {
    setValues((current) => ({ ...current, [field]: value }))
    if (errors[field]) setErrors((current) => ({ ...current, [field]: undefined }))
    if (completed) setCompleted(false)
  }

  const fieldState = (field: keyof IntakeValues) => ({
    'aria-invalid': Boolean(errors[field]),
    'aria-describedby': errors[field] ? `${field}-error` : undefined,
    className: cn(errors[field] && 'border-critical focus-visible:ring-critical'),
  })

  const validate = () => {
    const next: Errors = {}
    const required: Array<keyof IntakeValues> = ['chiefConcern', 'temperature', 'pulse', 'respiratoryRate', 'systolic', 'diastolic', 'oxygenSaturation', 'weight', 'height', 'ecog', 'observations']
    required.forEach((field) => { if (!values[field]) next[field] = 'This field is required.' })
    if (!values.identityVerified) next.identityVerified = 'Verify patient identity before completing intake.'
    if (!values.medicationReconciled) next.medicationReconciled = 'Complete medication reconciliation.'
    if (!values.allergiesReviewed) next.allergiesReviewed = 'Review and confirm allergy status.'
    if (values.temperature && (+values.temperature < 34 || +values.temperature > 43)) next.temperature = 'Enter a temperature between 34 and 43 °C.'
    if (values.pulse && (+values.pulse < 30 || +values.pulse > 220)) next.pulse = 'Enter a pulse between 30 and 220 bpm.'
    if (values.oxygenSaturation && (+values.oxygenSaturation < 50 || +values.oxygenSaturation > 100)) next.oxygenSaturation = 'Enter a value between 50 and 100%.'
    if (values.painScore && (+values.painScore < 0 || +values.painScore > 10)) next.painScore = 'Pain score must be from 0 to 10.'
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!validate()) {
      setCompleted(false)
      requestAnimationFrame(() => document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus())
      return
    }
    performAction('complete-intake', 'Nurse Intake completed', 'Nurse Intake', { destination: 'Doctor OPD', status: 'Completed' })
    performAction('handoff-doctor', 'Handoff to Medical Oncology', 'Nurse Intake', { destination: 'Doctor OPD', status: 'Awaiting review' })
    setCompleted(true)
  }

  return (
    <PageContainer>
      <PageHeader title="Nurse Intake" description="Document the pre-consultation oncology assessment and prepare a structured clinical handoff." actions={<>{role.roleId === 'nurse' ? <Link href="/patients" className={buttonVariants({ variant: 'outline', size: 'sm' })}><ArrowLeft />Back to Patients</Link> : null}<Badge variant={completed ? 'success' : 'warning'}>{completed ? <CheckCircle2 /> : <ClipboardCheck />}{completed ? 'Ready for handoff' : 'Intake in progress'}</Badge></>} />
      {nurseTasks.slice(0,1).map((task)=><div key={task.id} className="mb-6 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-information/25 bg-information-subtle px-4 py-3"><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-information-strong">New task: {task.task}</p><p className="mt-1 text-xs text-information-strong">{task.patient} · Created by {task.createdBy}</p></div>{nurseTasks.length>1?<span className="text-xs font-medium text-information-strong">+{nurseTasks.length-1} more in queue</span>:null}<Badge variant="warning">{task.status}</Badge></div>)}

      <Card className="aivana-accent-line mb-6 bg-surface-clinical">
        <CardContent className="grid gap-5 p-5 sm:grid-cols-2 xl:grid-cols-5">
          <div className="flex items-center gap-3 sm:col-span-2"><span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-surface text-supporting"><UserRound className="size-5" /></span><div><p className="font-display text-base font-semibold">Sunita Patil <span className="text-xs font-normal text-metadata">(Fictional)</span></p><p className="text-xs text-metadata">MRN DEMO-ONC-02481 · 39 years · Female</p></div></div>
          <div><p className="text-xs font-medium uppercase tracking-wider text-metadata">Primary diagnosis</p><p className="mt-1 text-sm font-medium text-supporting">Stage IIA breast cancer</p></div>
          <div><p className="text-xs font-medium uppercase tracking-wider text-metadata">Current treatment</p><p className="mt-1 text-sm font-medium text-supporting">AC chemotherapy · C2D8</p></div>
          <div><p className="text-xs font-medium uppercase tracking-wider text-metadata">Encounter</p><p className="mt-1 text-sm font-medium text-supporting">23 Aug 2026 · 10:30</p></div>
        </CardContent>
      </Card>

      {completed ? <div role="status" className="mb-6 flex flex-col gap-3 rounded-lg border border-success/30 bg-success-subtle px-4 py-3 text-success-strong sm:flex-row sm:items-center sm:justify-between"><div className="flex items-start gap-3"><CheckCircle2 className="mt-0.5 size-5 shrink-0" /><div><p className="text-sm font-semibold">Nurse intake completed</p><p className="mt-0.5 text-xs">Patient ready for Doctor OPD. Demo journey events were created locally; no clinical record was transmitted.</p></div></div><Link href="/doctor-opd" className={buttonVariants({ size: 'sm' })}>Open Doctor OPD →</Link></div> : null}
      {draftSaved && !completed ? <div role="status" className="mb-6 rounded-lg border border-information/30 bg-information-subtle px-4 py-3 text-sm text-information-strong">Draft saved for this demo session only.</div> : null}

      <form noValidate onSubmit={handleSubmit} className="space-y-6">
        <Card>
          <SectionHeading icon={Stethoscope} title="Reason for visit and presenting symptoms" description="Capture the patient’s concern in their own words, then record symptoms requiring review." />
          <CardContent className="space-y-6 pt-6">
            <div className="grid gap-5 sm:grid-cols-3">
              <div className="space-y-2 sm:col-span-2"><FieldLabel htmlFor="chiefConcern" required>Chief concern</FieldLabel><textarea id="chiefConcern" rows={3} value={values.chiefConcern} onChange={(e) => update('chiefConcern', e.target.value)} className={cn(controlClassName, 'h-auto resize-y', errors.chiefConcern && 'border-critical')} aria-invalid={Boolean(errors.chiefConcern)} /><FieldError id="chiefConcern-error" message={errors.chiefConcern} /></div>
              <div className="grid gap-5"><div className="space-y-2"><FieldLabel htmlFor="symptomOnset">Symptom onset</FieldLabel><Input id="symptomOnset" type="date" value={values.symptomOnset} onChange={(e) => update('symptomOnset', e.target.value)} /></div><div className="space-y-2"><FieldLabel htmlFor="painScore">Pain score (0–10)</FieldLabel><Input id="painScore" type="number" min="0" max="10" value={values.painScore} onChange={(e) => update('painScore', e.target.value)} {...fieldState('painScore')} /><FieldError id="painScore-error" message={errors.painScore} /></div></div>
            </div>
            <fieldset><legend className="mb-3 text-sm font-medium text-supporting">Presenting symptoms</legend><div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{symptoms.map((symptom) => <label key={symptom} className={cn('flex min-h-10 items-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors', selectedSymptoms.includes(symptom) ? 'border-brand bg-brand-soft text-supporting' : 'border-border bg-input-background text-supporting')}><input type="checkbox" checked={selectedSymptoms.includes(symptom)} onChange={() => setSelectedSymptoms((current) => current.includes(symptom) ? current.filter((item) => item !== symptom) : [...current, symptom])} className="size-4 accent-primary" /><span>{symptom}</span></label>)}</div></fieldset>
          </CardContent>
        </Card>

        <PreviousDocumentsSection />

        <Card>
          <SectionHeading icon={FileHeart} title="Relevant oncology history" description="Summarise diagnosis and active treatment for rapid clinical orientation." />
          <CardContent className="grid gap-5 pt-6 sm:grid-cols-2">
            <div className="space-y-2 sm:col-span-2"><FieldLabel htmlFor="oncologyHistory">Oncology history</FieldLabel><textarea id="oncologyHistory" rows={4} value={values.oncologyHistory} onChange={(e) => update('oncologyHistory', e.target.value)} className={cn(controlClassName, 'h-auto resize-y')} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="currentTreatment">Current treatment</FieldLabel><Input id="currentTreatment" value={values.currentTreatment} onChange={(e) => update('currentTreatment', e.target.value)} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="treatmentCycle">Cycle / treatment point</FieldLabel><Input id="treatmentCycle" value={values.treatmentCycle} onChange={(e) => update('treatmentCycle', e.target.value)} /></div>
            <StructuredOncologyHistory />
          </CardContent>
        </Card>

        <div className="grid gap-6 xl:grid-cols-2">
          <Card>
            <SectionHeading icon={Pill} title="Current medications" description="Review name, dose, and schedule with the patient." action={<Badge variant="neutral">{medications.filter((item) => item.status === 'Active').length} active</Badge>} />
            <CardContent className="pt-6">
              <div className="overflow-x-auto"><table className="w-full min-w-[680px] text-left text-sm"><thead className="border-b border-divider text-xs uppercase tracking-wider text-metadata"><tr><th className="pb-3 font-medium">Medication</th><th className="pb-3 font-medium">Dose</th><th className="pb-3 font-medium">Schedule</th><th className="pb-3 font-medium">Status</th><th className="pb-3 text-right font-medium">Actions</th></tr></thead><tbody className="divide-y divide-divider">{medications.map((medication) => <tr key={medication.name}><td className="py-3 font-medium text-supporting">{medication.name}</td><td className="py-3 text-metadata">{medication.dose}</td><td className="py-3 text-metadata">{medication.schedule}</td><td className="py-3"><Badge variant={medication.status === 'Active' ? 'success' : 'neutral'}>{medication.status}</Badge></td><td className="py-3"><div className="flex justify-end gap-1"><Button type="button" size="sm" variant="ghost" aria-label={`Edit ${medication.name}`} onClick={() => setMedications((current) => current.map((item) => item.name === medication.name ? { ...item, dose: item.dose.includes('edited') ? item.dose : `${item.dose} (edited)` } : item))}>Edit</Button><Button type="button" size="sm" variant="ghost" aria-label={`Continue ${medication.name}`} onClick={() => setMedications((current) => current.map((item) => item.name === medication.name ? { ...item, status: 'Active' } : item))}>Continue</Button><Button type="button" size="sm" variant="ghost" aria-label={`Stop ${medication.name}`} onClick={() => setMedications((current) => current.map((item) => item.name === medication.name ? { ...item, status: 'Stopped' } : item))}>Stop</Button></div></td></tr>)}</tbody></table></div>
              <Button type="button" size="sm" variant="outline" className="mt-4" onClick={() => setMedications((current) => current.some((item) => item.name === 'Metoclopramide') ? current : [...current, { name: 'Metoclopramide', dose: '10 mg', schedule: 'As needed for nausea', status: 'Active' }])}><Plus />Add medication</Button>
              <label className={cn('mt-5 flex items-start gap-3 border-t border-divider pt-4 text-sm text-supporting', errors.medicationReconciled && 'text-critical-strong')}><input type="checkbox" checked={values.medicationReconciled} onChange={(e) => update('medicationReconciled', e.target.checked)} aria-invalid={Boolean(errors.medicationReconciled)} className="mt-0.5 size-4 accent-primary" /><span>Medication list reconciled with patient <span className="text-critical">*</span></span></label><FieldError id="medicationReconciled-error" message={errors.medicationReconciled} />
            </CardContent>
          </Card>

          <Card>
            <SectionHeading icon={ShieldAlert} title="Allergies" description="Record substance and reaction; never rely on status colour alone." action={<Badge variant="critical">1 documented</Badge>} />
            <CardContent className="pt-6"><div className="rounded-md border border-critical/30 bg-critical-subtle p-4"><div className="flex items-start justify-between gap-3"><div><p className="text-sm font-semibold text-critical-strong">Paclitaxel</p><p className="mt-1 text-sm text-critical-strong">Immediate hypersensitivity reaction: flushing, chest tightness, and wheeze.</p></div><Badge variant="critical">Severe</Badge></div><p className="mt-3 text-xs text-critical-strong">Recorded during first infusion · 02 Aug 2026</p></div><label className={cn('mt-5 flex items-start gap-3 text-sm text-supporting', errors.allergiesReviewed && 'text-critical-strong')}><input type="checkbox" checked={values.allergiesReviewed} onChange={(e) => update('allergiesReviewed', e.target.checked)} aria-invalid={Boolean(errors.allergiesReviewed)} className="mt-0.5 size-4 accent-primary" /><span>Allergy status reviewed with patient <span className="text-critical">*</span></span></label><FieldError id="allergiesReviewed-error" message={errors.allergiesReviewed} />{values.allergiesReviewed ? <div className="mt-4 rounded-md border border-success/30 bg-success-subtle p-4 text-success-strong"><div className="flex items-center justify-between gap-3"><p className="text-xs font-semibold uppercase tracking-wider">Allergy confirmed</p><Badge variant="success">Verified</Badge></div><p className="mt-2 text-sm font-semibold">Paclitaxel · Severe hypersensitivity reaction</p><p className="mt-1 text-xs">Verified by Nurse Navigator · 23 Aug 2026</p></div> : null}</CardContent>
          </Card>
        </div>

        <Card>
          <SectionHeading icon={HeartPulse} title="Vitals" description="Record current observations and verify any clinically unexpected value." />
          <CardContent className="grid gap-5 pt-6 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-2"><FieldLabel htmlFor="temperature" required>Temperature (°C)</FieldLabel><Input id="temperature" type="number" step="0.1" value={values.temperature} onChange={(e) => update('temperature', e.target.value)} {...fieldState('temperature')} /><FieldError id="temperature-error" message={errors.temperature} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="pulse" required>Pulse (bpm)</FieldLabel><Input id="pulse" type="number" value={values.pulse} onChange={(e) => update('pulse', e.target.value)} {...fieldState('pulse')} /><FieldError id="pulse-error" message={errors.pulse} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="respiratoryRate" required>Respiratory rate</FieldLabel><Input id="respiratoryRate" type="number" value={values.respiratoryRate} onChange={(e) => update('respiratoryRate', e.target.value)} {...fieldState('respiratoryRate')} /><FieldError id="respiratoryRate-error" message={errors.respiratoryRate} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="oxygenSaturation" required>SpO₂ (%)</FieldLabel><Input id="oxygenSaturation" type="number" value={values.oxygenSaturation} onChange={(e) => update('oxygenSaturation', e.target.value)} {...fieldState('oxygenSaturation')} /><FieldError id="oxygenSaturation-error" message={errors.oxygenSaturation} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="systolic" required>Systolic BP</FieldLabel><Input id="systolic" type="number" value={values.systolic} onChange={(e) => update('systolic', e.target.value)} {...fieldState('systolic')} /><FieldError id="systolic-error" message={errors.systolic} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="diastolic" required>Diastolic BP</FieldLabel><Input id="diastolic" type="number" value={values.diastolic} onChange={(e) => update('diastolic', e.target.value)} {...fieldState('diastolic')} /><FieldError id="diastolic-error" message={errors.diastolic} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="weight" required>Weight (kg)</FieldLabel><Input id="weight" type="number" step="0.1" value={values.weight} onChange={(e) => update('weight', e.target.value)} {...fieldState('weight')} /><FieldError id="weight-error" message={errors.weight} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="height" required>Height (cm)</FieldLabel><Input id="height" type="number" step="0.1" value={values.height} onChange={(e) => update('height', e.target.value)} {...fieldState('height')} /><FieldError id="height-error" message={errors.height} /></div>
            <div className="rounded-md border border-border bg-input-background p-3"><p className="text-xs font-medium text-metadata">BMI (calculated)</p><p className="mt-1 text-lg font-semibold text-supporting">{bmi ? bmi.toFixed(1) : '—'} <span className="text-xs font-normal text-metadata">kg/m²</span></p></div>
            <div className="rounded-md border border-border bg-input-background p-3"><p className="text-xs font-medium text-metadata">BSA (calculated)</p><p className="mt-1 text-lg font-semibold text-supporting">BSA: {bsa ? bsa.toFixed(2) : '—'} <span className="text-xs font-normal text-metadata">m²</span></p><p className="mt-1 text-xs text-metadata">Formula: DuBois</p></div>
          </CardContent>
        </Card>

        <Card>
          <SectionHeading icon={Activity} title="Functional status and general assessment" description="Capture performance, safety, and supportive-care concerns." />
          <CardContent className="grid gap-6 pt-6 lg:grid-cols-3">
            <fieldset><legend className="mb-3 text-sm font-medium text-supporting">ECOG performance status <span className="text-critical">*</span></legend><div className="space-y-2">{[['0','Fully active'],['1','Restricted in strenuous activity'],['2','Ambulatory, unable to work'],['3','Limited self-care'],['4','Completely disabled']].map(([value,label]) => <label key={value} className={cn('flex items-center gap-3 rounded-md border px-3 py-2 text-sm', values.ecog === value ? 'border-brand bg-brand-soft' : 'border-border bg-input-background')}><input type="radio" name="ecog" value={value} checked={values.ecog === value} onChange={(e) => update('ecog', e.target.value)} className="size-4 accent-primary" /><span><strong className="font-medium">{value}</strong> · {label}</span></label>)}</div><FieldError id="ecog-error" message={errors.ecog} /></fieldset>
            <div className="space-y-5"><div className="space-y-2"><FieldLabel htmlFor="fallRisk">Fall risk</FieldLabel><select id="fallRisk" value={values.fallRisk} onChange={(e) => update('fallRisk', e.target.value)} className={controlClassName}><option value="low">Low</option><option value="moderate">Moderate</option><option value="high">High</option></select></div><div className="space-y-2"><FieldLabel htmlFor="nutritionConcern">Nutrition concern</FieldLabel><select id="nutritionConcern" value={values.nutritionConcern} onChange={(e) => update('nutritionConcern', e.target.value)} className={controlClassName}><option value="none">None identified</option><option value="mild">Mild — monitor intake</option><option value="moderate">Moderate — dietitian review</option><option value="high">High — urgent review</option></select></div></div>
            <div className="rounded-lg border border-border bg-surface-elevated p-4"><p className="text-sm font-semibold text-supporting">General assessment</p><dl className="mt-4 space-y-3 text-sm"><div className="flex justify-between gap-3"><dt className="text-metadata">Consciousness</dt><dd className="font-medium text-supporting">Alert, oriented</dd></div><div className="flex justify-between gap-3"><dt className="text-metadata">Mobility</dt><dd className="font-medium text-supporting">Independent</dd></div><div className="flex justify-between gap-3"><dt className="text-metadata">Distress</dt><dd className="font-medium text-supporting">None observed</dd></div><div className="flex justify-between gap-3"><dt className="text-metadata">Support person</dt><dd className="font-medium text-supporting">Present</dd></div></dl></div>
          </CardContent>
        </Card>

        <Card>
          <SectionHeading icon={NotebookPen} title="Nurse observations and handoff" description="Document objective observations and set the appropriate clinician-review priority." />
          <CardContent className="grid gap-5 pt-6 lg:grid-cols-3">
            <div className="space-y-2 lg:col-span-2"><FieldLabel htmlFor="observations" required>Nurse observations</FieldLabel><textarea id="observations" rows={6} value={values.observations} onChange={(e) => update('observations', e.target.value)} className={cn(controlClassName, 'h-auto resize-y', errors.observations && 'border-critical')} aria-invalid={Boolean(errors.observations)} /><FieldError id="observations-error" message={errors.observations} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="handoffPriority">Handoff priority</FieldLabel><select id="handoffPriority" value={values.handoffPriority} onChange={(e) => update('handoffPriority', e.target.value)} className={controlClassName}><option value="routine">Routine review</option><option value="priority">Priority review</option><option value="urgent">Urgent clinician review</option></select><div className="rounded-md border border-information/30 bg-information-subtle p-3 text-xs text-information-strong">Escalate immediately outside this prototype if the patient has an acute or critical clinical concern.</div></div>
          </CardContent>
        </Card>

        <NurseHandoffSummary allergyConfirmed={values.allergiesReviewed} priority={values.handoffPriority} />

        <Card>
          <SectionHeading icon={ClipboardCheck} title="Intake completion" description="Complete required safety checks before handing off to the oncology clinician." />
          <CardContent className="space-y-3 pt-6">
            <label className={cn('flex items-start gap-3 text-sm text-supporting', errors.identityVerified && 'text-critical-strong')}><input type="checkbox" checked={values.identityVerified} onChange={(e) => update('identityVerified', e.target.checked)} aria-invalid={Boolean(errors.identityVerified)} className="mt-0.5 size-4 accent-primary" /><span>Patient identity verified using two identifiers <span className="text-critical">*</span></span></label><FieldError id="identityVerified-error" message={errors.identityVerified} />
            <label className="flex items-start gap-3 text-sm text-supporting"><input type="checkbox" checked={values.medicationReconciled} onChange={(e) => update('medicationReconciled', e.target.checked)} className="mt-0.5 size-4 accent-primary" /><span>Current medications reconciled</span></label>
            <label className="flex items-start gap-3 text-sm text-supporting"><input type="checkbox" checked={values.allergiesReviewed} onChange={(e) => update('allergiesReviewed', e.target.checked)} className="mt-0.5 size-4 accent-primary" /><span>Allergy status reviewed</span></label>
          </CardContent>
        </Card>

        <div className="-mx-4 flex flex-col-reverse gap-3 border-t border-border bg-surface px-4 py-4 shadow-soft sm:mx-0 sm:flex-row sm:items-center sm:justify-between sm:rounded-xl sm:border">
          <p className="text-xs text-metadata"><span className="text-critical">*</span> Required fields · Demo data is not persisted</p>
          <div className="flex flex-wrap gap-2"><Button type="button" variant="secondary" onClick={() => setDraftSaved(true)}>Save Draft</Button><Button type="button" variant="outline" onClick={() => { setValues(initialValues); setSelectedSymptoms(initialSymptoms); setErrors({}); setCompleted(false); setDraftSaved(false); setMedications(initialMedications) }}>Reset demo</Button><Button type="submit">Complete &amp; Handoff → Doctor OPD</Button></div>
        </div>
      </form>
    </PageContainer>
  )
}
