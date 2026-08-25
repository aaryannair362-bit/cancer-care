'use client'

import * as React from 'react'
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  FlaskConical,
  TrendingDown,
  TrendingUp,
  UserRound,
} from 'lucide-react'

import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { StakeholderWorkflowPanel } from '@/components/stakeholder-workflow-panel'
import { useDemoAccess } from '@/components/demo-access-provider'

type ResultStatus = 'normal' | 'abnormal' | 'critical'
type LabResult = {
  id: string
  group: 'CBC' | 'Chemistry'
  test: string
  value: string
  unit: string
  range: string
  prior: string
  change: 'up' | 'down' | 'stable'
  status: ResultStatus
  interpretation: string
}

const results: LabResult[] = [
  { id: 'hb', group: 'CBC', test: 'Haemoglobin', value: '9.4', unit: 'g/dL', range: '12.0–15.5', prior: '10.1', change: 'down', status: 'abnormal', interpretation: 'Below reference range with a downward trend.' },
  { id: 'wbc', group: 'CBC', test: 'WBC', value: '2.1', unit: '×10⁹/L', range: '4.0–11.0', prior: '3.6', change: 'down', status: 'abnormal', interpretation: 'Leucopenia following systemic therapy.' },
  { id: 'anc', group: 'CBC', test: 'ANC', value: '0.7', unit: '×10⁹/L', range: '1.5–7.5', prior: '1.8', change: 'down', status: 'critical', interpretation: 'Critical neutropenia threshold in this fictional result set; requires prompt clinician review.' },
  { id: 'platelets', group: 'CBC', test: 'Platelets', value: '138', unit: '×10⁹/L', range: '150–400', prior: '172', change: 'down', status: 'abnormal', interpretation: 'Mild thrombocytopenia compared with prior result.' },
  { id: 'creatinine', group: 'Chemistry', test: 'Creatinine', value: '0.82', unit: 'mg/dL', range: '0.50–1.10', prior: '0.79', change: 'stable', status: 'normal', interpretation: 'Within reference range; renal marker stable.' },
  { id: 'egfr', group: 'Chemistry', test: 'eGFR', value: '94', unit: 'mL/min/1.73m²', range: '≥60', prior: '96', change: 'stable', status: 'normal', interpretation: 'Estimated renal filtration remains within expected range.' },
  { id: 'ast', group: 'Chemistry', test: 'AST', value: '46', unit: 'U/L', range: '10–35', prior: '31', change: 'up', status: 'abnormal', interpretation: 'Mild transaminase elevation; correlate clinically.' },
  { id: 'alt', group: 'Chemistry', test: 'ALT', value: '51', unit: 'U/L', range: '7–35', prior: '29', change: 'up', status: 'abnormal', interpretation: 'Mild transaminase elevation; requires clinical review in context.' },
  { id: 'bilirubin', group: 'Chemistry', test: 'Total bilirubin', value: '0.6', unit: 'mg/dL', range: '0.2–1.2', prior: '0.5', change: 'stable', status: 'normal', interpretation: 'Within reference range.' },
  { id: 'albumin', group: 'Chemistry', test: 'Albumin', value: '3.3', unit: 'g/dL', range: '3.5–5.0', prior: '3.6', change: 'down', status: 'abnormal', interpretation: 'Mildly below reference range with a small decrease.' },
]

const statusBadge = { normal: 'success', abnormal: 'warning', critical: 'critical' } as const
const statusSurface = {
  normal: 'border-success/30 bg-success-subtle text-success-strong',
  abnormal: 'border-warning/30 bg-warning-subtle text-warning-strong',
  critical: 'border-critical/30 bg-critical-subtle text-critical-strong',
}

function Trend({ result }: { result: LabResult }) {
  if (result.change === 'stable') return <span className="text-xs text-metadata">Prior {result.prior}</span>
  const Icon = result.change === 'up' ? TrendingUp : TrendingDown
  return <span className={cn('flex items-center gap-1 text-xs', result.status === 'critical' ? 'text-critical-strong' : 'text-warning-strong')}><Icon className="size-3.5" />Prior {result.prior}</span>
}

function LabOperationsWorkspace() {
  const { selectedPatient, advanceDiagnostic } = useDemoAccess()
  const [collectionStatus,setCollectionStatus] = React.useState('Not collected')
  const [sampleStatus,setSampleStatus] = React.useState('Ordered')
  const [rejectionReason,setRejectionReason] = React.useState('')
  const [resultStatus,setResultStatus] = React.useState('Result pending')
  const [criticalState,setCriticalState] = React.useState('Not flagged')
  const sampleSteps = ['Collected','Labelled','Sent to lab','Received','Processing','Result available','Completed']
  const reject = () => { if(rejectionReason){setCollectionStatus('Recollection required');setSampleStatus('Recollect')} }
  return <PageContainer>
    <PageHeader title="Lab Worklist" description="Coordinate laboratory orders, collection, sample processing, and result-status visibility." />
    <Card className="mb-6"><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Orders requiring action</CardTitle><CardDescription className="mt-1">Browser-local laboratory workflow · no LIS connection</CardDescription></div><Badge variant="warning">1 order</Badge></div></CardHeader><CardContent className="pt-6"><article className="grid gap-4 rounded-xl border border-border bg-surface/80 p-5 sm:grid-cols-2 lg:grid-cols-4">{[['Patient',`${selectedPatient.name} · ${selectedPatient.mrn}`],['Tests','CBC with ANC · renal and liver function panel'],['Specimen','Venous blood · EDTA and serum tubes'],['Ordering service','Medical Oncology · Medical Oncologist'],['Order date / time','23 Aug 2026 · 07:45'],['Priority','High'],['Collection / processing',`${collectionStatus} · ${sampleStatus}`],['Result / critical status',`${resultStatus} · ${criticalState}`]].map(([label,value])=><div key={label}><p className="text-xs text-metadata">{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}</article></CardContent></Card>
    <div className="grid gap-6 xl:grid-cols-2">
      <Card><CardHeader className="border-b border-divider"><CardTitle>Order details</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[['Ordered tests','CBC with differential and ANC; renal/liver panel'],['Ordering clinician','Medical Oncologist'],['Ordering department','Medical Oncology'],['Order date / time','23 Aug 2026 · 07:45'],['Priority','High'],['Indication','Pre-treatment laboratory readiness review']].map(([label,value])=><div key={label}><p className="text-xs text-metadata">{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Collection details</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[['Specimen type','Venous blood'],['Tube / container','EDTA lavender top · serum separator'],['Collection date / time','23 Aug 2026 · 08:14'],['Collected by','Lab / Phlebotomy'],['Fasting status','Not required'],['Collection notes','Patient identity and labels verified in demo workflow']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}<label className="text-xs font-medium text-metadata sm:col-span-2">Collection status<select value={collectionStatus} onChange={(event)=>setCollectionStatus(event.target.value)} className="mt-1 h-10 w-full rounded-md border border-input bg-input-background px-3 text-sm">{['Not collected','In progress','Collected','Failed collection','Recollection required'].map((value)=><option key={value}>{value}</option>)}</select></label></CardContent></Card>
    </div>
    <Card className="mt-6"><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Sample tracking</CardTitle><CardDescription className="mt-1">Operational progression without a laboratory integration</CardDescription></div><Badge variant="information">{sampleStatus}</Badge></div></CardHeader><CardContent className="pt-6"><div className="flex flex-wrap gap-2">{sampleSteps.map((step)=><Button key={step} type="button" size="sm" variant={sampleStatus===step?'primary':'outline'} onClick={()=>{setSampleStatus(step);advanceDiagnostic('lab',step);if(step==='Result available')setResultStatus('Result available')}}>{step}</Button>)}</div></CardContent></Card>
    <div className="mt-6 grid gap-6 xl:grid-cols-2">
      <Card><CardHeader className="border-b border-divider"><CardTitle>Sample rejection / recollection</CardTitle></CardHeader><CardContent className="space-y-4 pt-6"><label className="text-xs font-medium text-metadata">Rejection reason<select value={rejectionReason} onChange={(event)=>setRejectionReason(event.target.value)} className="mt-1 h-10 w-full rounded-md border border-input bg-input-background px-3 text-sm"><option value="">Select reason</option>{['Insufficient sample','Hemolysed sample','Clotted sample','Incorrect container','Mislabeled specimen','Transport delay','Sample integrity issue','Other'].map((value)=><option key={value}>{value}</option>)}</select></label><Button type="button" variant="outline" disabled={!rejectionReason} onClick={reject}>Mark Recollection Required</Button>{collectionStatus==='Recollection required'?<div className="rounded-lg border border-warning/30 bg-warning-subtle p-3 text-sm font-medium text-warning-strong">Recollection required · {rejectionReason}</div>:null}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Results status</CardTitle><CardDescription>Values are visible without autonomous interpretation.</CardDescription></CardHeader><CardContent className="space-y-4 pt-6"><div className="flex flex-wrap gap-2">{['Result pending','Result available','Critical result'].map((value)=><Button key={value} type="button" size="sm" variant={resultStatus===value?'primary':'outline'} onClick={()=>{setResultStatus(value);if(value==='Critical result')setCriticalState('Flagged')}}>{value}</Button>)}</div><div className="grid gap-3 sm:grid-cols-3">{[['ANC','0.7 ×10⁹/L'],['Haemoglobin','9.4 g/dL'],['Platelets','138 ×10⁹/L']].map(([label,value])=><div key={label} className="rounded-lg border border-border bg-input-background p-3"><p className="text-xs text-metadata">{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}</div><p className="text-xs text-metadata">Operational result visibility only. Clinical interpretation and treatment decisions remain with the treating team.</p></CardContent></Card>
    </div>
    <Card className={cn('mt-6',resultStatus==='Critical result'&&'border-critical/30')}><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Critical-result workflow</CardTitle><CardDescription className="mt-1">Existing browser-local escalation and acknowledgement state</CardDescription></div><Badge variant={criticalState==='Resolved'?'success':criticalState==='Not flagged'?'neutral':'critical'}>{criticalState}</Badge></div></CardHeader><CardContent className="space-y-4 pt-6"><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[['Critical test / result','ANC · 0.7 ×10⁹/L'],['Urgency','Prompt clinical review'],['Responsible team','Medical Oncology'],['Acknowledgement',criticalState==='Acknowledged'||criticalState==='Resolved'?'Acknowledged':'Pending']].map(([label,value])=><div key={label}><p className="text-xs text-metadata">{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}</div><div className="flex flex-wrap gap-2"><Button type="button" variant="outline" onClick={()=>setCriticalState('Escalated')}>Notify / Escalate</Button><Button type="button" variant="outline" onClick={()=>setCriticalState('Acknowledged')}>Mark acknowledged</Button><Button type="button" onClick={()=>setCriticalState('Resolved')}>Mark resolved</Button></div></CardContent></Card>
  </PageContainer>
}

export default function LabPage() {
  const { role } = useDemoAccess()
  const [selectedId, setSelectedId] = React.useState('anc')
  const [reviewed, setReviewed] = React.useState(false)
  const [acknowledged, setAcknowledged] = React.useState(false)
  const [note, setNote] = React.useState('CBC demonstrates treatment-associated cytopenias, including critical ANC in this fictional result set. Results require clinician correlation and review before any care decision.')
  const selected = results.find((result) => result.id === selectedId) ?? results[0]
  const criticalCount = results.filter((result) => result.status === 'critical').length
  const abnormalCount = results.filter((result) => result.status === 'abnormal').length

  const handleReview = () => { setReviewed(true); setAcknowledged(false) }
  const handleAcknowledge = () => { if (reviewed) setAcknowledged(true) }

  if (role.roleId === 'lab') return <LabOperationsWorkspace />

  return (
    <PageContainer>
      <PageHeader title="Lab" description="Review oncology laboratory results, trends, and items requiring clinician attention." actions={<Badge variant="information">Fictional demo data</Badge>} />
      <StakeholderWorkflowPanel module="lab" />

      <Card className="mb-6 bg-surface-clinical">
        <CardContent className="grid gap-5 p-5 sm:grid-cols-2 xl:grid-cols-5">
          <div className="flex items-center gap-3 sm:col-span-2"><span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-surface text-supporting"><UserRound className="size-5" /></span><div><p className="font-display font-semibold">Sunita Patil <span className="text-xs font-normal text-metadata">(Fictional)</span></p><p className="text-xs text-metadata">MRN DEMO-ONC-02481 · 39 years · Female</p></div></div>
          <div><p className="text-xs uppercase tracking-wider text-metadata">Diagnosis</p><p className="mt-1 text-sm font-medium text-supporting">Stage IIA breast cancer</p></div>
          <div><p className="text-xs uppercase tracking-wider text-metadata">Treatment</p><p className="mt-1 text-sm font-medium text-supporting">AC chemotherapy · C2D8</p></div>
          <div><p className="text-xs uppercase tracking-wider text-metadata">Encounter</p><p className="mt-1 text-sm font-medium text-supporting">23 Aug 2026 · OPD review</p></div>
        </CardContent>
      </Card>

      {acknowledged ? <div role="status" className="mb-6 flex gap-3 rounded-lg border border-success/30 bg-success-subtle px-4 py-3 text-success-strong"><CheckCircle2 className="mt-0.5 size-5 shrink-0" /><div><p className="text-sm font-semibold">Results acknowledged in this demonstration</p><p className="mt-0.5 text-xs">No result, note, or acknowledgement was sent to a clinical system.</p></div></div> : null}

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Card><CardContent className="p-5"><p className="text-sm text-metadata">Result set</p><p className="mt-2 font-display text-2xl font-semibold">Final</p><p className="mt-1 text-xs text-metadata">Collected 23 Aug 2026 · 08:14</p></CardContent></Card>
        <Card><CardContent className="p-5"><p className="text-sm text-metadata">Critical results</p><p className="mt-2 font-display text-2xl font-semibold text-critical-strong">{criticalCount}</p><p className="mt-1 text-xs text-critical-strong">Requires prompt clinical review</p></CardContent></Card>
        <Card><CardContent className="p-5"><p className="text-sm text-metadata">Abnormal results</p><p className="mt-2 font-display text-2xl font-semibold text-warning-strong">{abnormalCount}</p><p className="mt-1 text-xs text-warning-strong">Review with trends and clinical context</p></CardContent></Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader className="border-b border-divider"><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>Results overview</CardTitle><CardDescription className="mt-1">CBC and chemotherapy-relevant chemistry markers</CardDescription></div><div className="flex gap-2"><Badge variant="critical">Critical</Badge><Badge variant="warning">Abnormal</Badge><Badge variant="success">Normal</Badge></div></div></CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto"><table className="w-full min-w-[680px] text-left text-sm"><thead className="border-b border-divider bg-surface-elevated text-xs uppercase tracking-wider text-metadata"><tr><th className="px-5 py-3 font-medium">Test</th><th className="px-3 py-3 font-medium">Current</th><th className="px-3 py-3 font-medium">Reference</th><th className="px-3 py-3 font-medium">Trend</th><th className="px-3 py-3 font-medium">Status</th><th className="px-5 py-3"><span className="sr-only">Detail</span></th></tr></thead><tbody className="divide-y divide-divider">{results.map((result) => <tr key={result.id} className={cn('transition-colors', selectedId === result.id ? 'bg-brand-soft' : 'hover:bg-surface-app')}><td className="px-5 py-3"><p className="font-medium text-supporting">{result.test}</p><p className="text-xs text-metadata">{result.group}</p></td><td className={cn('px-3 py-3 font-semibold', result.status === 'critical' ? 'text-critical-strong' : result.status === 'abnormal' ? 'text-warning-strong' : 'text-supporting')}>{result.value} <span className="text-xs font-normal text-metadata">{result.unit}</span></td><td className="px-3 py-3 text-metadata">{result.range}</td><td className="px-3 py-3"><Trend result={result} /></td><td className="px-3 py-3"><Badge variant={statusBadge[result.status]}>{result.status === 'critical' && <AlertCircle />}{result.status}</Badge></td><td className="px-5 py-3 text-right"><button type="button" onClick={() => setSelectedId(result.id)} className="rounded-md p-2 text-metadata hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`View ${result.test} detail`}><ChevronRight className="size-4" /></button></td></tr>)}</tbody></table></div>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card variant="elevated">
            <CardHeader className="border-b border-divider"><div className="flex items-start justify-between gap-3"><div><CardTitle>{selected.test}</CardTitle><CardDescription className="mt-1">Selected result detail</CardDescription></div><Badge variant={statusBadge[selected.status]}>{selected.status}</Badge></div></CardHeader>
            <CardContent className="space-y-5 pt-6"><div className={cn('rounded-md border p-4', statusSurface[selected.status])}><p className="text-xs font-medium uppercase tracking-wider">Current value</p><p className="mt-2 font-display text-3xl font-semibold">{selected.value} <span className="text-sm font-medium">{selected.unit}</span></p><p className="mt-2 text-sm">{selected.interpretation}</p></div><dl className="space-y-3 text-sm"><div className="flex justify-between gap-3"><dt className="text-metadata">Reference range</dt><dd className="font-medium text-supporting">{selected.range} {selected.unit}</dd></div><div className="flex justify-between gap-3"><dt className="text-metadata">Prior value</dt><dd className="font-medium text-supporting">{selected.prior} {selected.unit}</dd></div><div className="flex justify-between gap-3"><dt className="text-metadata">Specimen</dt><dd className="font-medium text-supporting">Venous blood</dd></div><div className="flex justify-between gap-3"><dt className="text-metadata">Collected</dt><dd className="font-medium text-supporting">23 Aug · 08:14</dd></div><div className="flex justify-between gap-3"><dt className="text-metadata">Result status</dt><dd className="font-medium text-supporting">Final · Demo</dd></div></dl>{selected.status !== 'normal' ? <div className="flex gap-2 rounded-md border border-warning/30 bg-warning-subtle p-3 text-xs text-warning-strong"><AlertTriangle className="size-4 shrink-0" /><span>Requires clinical review. This interpretation does not recommend or determine treatment.</span></div> : null}</CardContent>
          </Card>
        </div>
      </div>

      <Card className="mt-6">
        <CardHeader className="border-b border-divider"><div className="flex items-start gap-3"><span className="flex size-9 items-center justify-center rounded-md bg-brand-soft"><ClipboardCheck className="size-4" /></span><div><CardTitle>Clinical review workflow</CardTitle><CardDescription className="mt-1">Review the full result set, add context, and acknowledge the demonstration result.</CardDescription></div></div></CardHeader>
        <CardContent className="space-y-5 pt-6"><div className="grid gap-3 sm:grid-cols-3"><div className={cn('rounded-md border p-3', reviewed ? 'border-success/30 bg-success-subtle' : 'border-border bg-input-background')}><p className="text-sm font-medium">1. Review results</p><p className="mt-1 text-xs text-metadata">Inspect CBC, chemistry, trends, and flags.</p></div><div className="rounded-md border border-border bg-input-background p-3"><p className="text-sm font-medium">2. Add clinical note</p><p className="mt-1 text-xs text-metadata">Document relevant clinical context.</p></div><div className={cn('rounded-md border p-3', acknowledged ? 'border-success/30 bg-success-subtle' : 'border-border bg-input-background')}><p className="text-sm font-medium">3. Acknowledge</p><p className="mt-1 text-xs text-metadata">Confirm human review of the result set.</p></div></div><div className="space-y-2"><label htmlFor="clinicalNote" className="text-sm font-medium text-supporting">Clinical review note</label><textarea id="clinicalNote" rows={4} value={note} onChange={(event) => { setNote(event.target.value); setAcknowledged(false) }} className="flex h-auto w-full resize-y rounded-md border border-input bg-input-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1" /><p className="text-xs text-metadata">Fictional note · not saved or submitted</p></div><div className="flex flex-col-reverse gap-3 border-t border-divider pt-5 sm:flex-row sm:items-center sm:justify-between"><div>{reviewed ? <Badge variant="success"><CheckCircle2 />Results reviewed</Badge> : <Badge variant="warning"><FlaskConical />Review pending</Badge>}</div><div className="flex gap-2"><Button type="button" variant="secondary" onClick={handleReview}>Review results</Button><Button type="button" onClick={handleAcknowledge} disabled={!reviewed || !note.trim()}>Acknowledge</Button></div></div></CardContent>
      </Card>
    </PageContainer>
  )
}
