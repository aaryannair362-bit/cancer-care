'use client'

import * as React from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Columns2,
  FileImage,
  Scan,
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

type Study = {
  id: string
  title: string
  modality: string
  date: string
  status: 'Report available' | 'Final' | 'Scheduled'
  review: boolean
  indication: string
  technique: string
  findings: string
  impression: string
  comparison: string
  radiologist: string
}

const studies: Study[] = [
  {
    id: 'ct-cap', title: 'CT chest, abdomen and pelvis', modality: 'CT with contrast', date: '22 Aug 2026', status: 'Report available', review: true,
    indication: 'Baseline staging following left breast lumpectomy for invasive ductal carcinoma before continuation of adjuvant systemic therapy.',
    technique: 'Contrast-enhanced CT from thoracic inlet through pelvis with multiplanar reformats. Fictional protocol and acquisition details.',
    findings: 'Post-operative change in the left breast and axilla. No enlarged thoracic, abdominal, or pelvic lymph nodes. No suspicious pulmonary nodule. Liver, adrenal glands, and visualised osseous structures show no focal lesion in this fictional study.',
    impression: 'Post-operative changes without fictional CT evidence of distant metastatic disease. Correlate with clinical, pathological, and multidisciplinary review.',
    comparison: 'Compared with CT chest dated 14 May 2026: post-operative changes are new; otherwise no material interval change.',
    radiologist: 'Radiologist · Final report · 22 Aug 2026, 16:42',
  },
  {
    id: 'breast-us', title: 'Targeted left breast and axillary ultrasound', modality: 'Ultrasound', date: '18 Aug 2026', status: 'Final', review: false,
    indication: 'Assessment of post-operative left axillary discomfort.',
    technique: 'Targeted grayscale and Doppler ultrasound of the left surgical bed and axilla.',
    findings: 'Small simple post-operative fluid collection at the axillary surgical bed. No suspicious solid mass or morphologically abnormal lymph node identified in this fictional examination.',
    impression: 'Small uncomplicated post-operative seroma. No suspicious sonographic finding.',
    comparison: 'No prior post-operative ultrasound available for comparison.',
    radiologist: 'Radiologist · Final report · 18 Aug 2026, 12:18',
  },
  {
    id: 'echo', title: 'Echocardiogram', modality: 'Ultrasound', date: '04 Sep 2026', status: 'Scheduled', review: false,
    indication: 'Protocol cardiac function assessment during anthracycline-containing therapy.',
    technique: 'Study not yet performed.', findings: 'No findings available.', impression: 'No report available.', comparison: 'Will compare with baseline echocardiogram dated 27 Jun 2026.', radiologist: 'Scheduled · 04 Sep 2026, 09:00',
  },
]

const studyStatus = { 'Report available': 'information', Final: 'success', Scheduled: 'neutral' } as const

function RadiologyCoordinationWorkspace() {
  const { selectedPatient, workflow, advanceDiagnostic } = useDemoAccess()
  const [appointmentStatus,setAppointmentStatus] = React.useState('Scheduled')
  const [scanStatus,setScanStatus] = React.useState('Scheduled')
  const [preparation,setPreparation] = React.useState<Record<string,string>>({
    'Fasting instructions':'Completed','Hydration / preparation instructions':'Completed','Contrast required':'Needs review','Renal-function prerequisite':'Pending','Pregnancy screening':'Not required','Allergy / contrast-reaction history':'Needs review','Previous imaging requested':'Completed','Other preparation requirement':'Not required',
  })
  const [issue,setIssue] = React.useState('Contrast clearance pending')
  const [issueState,setIssueState] = React.useState('Open')
  const [communications,setCommunications] = React.useState<Record<string,boolean>>({})
  const scanSteps = ['Order received','Scheduled','Patient arrived','Preparation complete','Ready for scan','Scan started','Scan completed','Study uploaded','Ready for Radiologist Review','Report finalized','Workflow completed']

  const markReady = () => { setScanStatus('Ready for Radiologist Review'); advanceDiagnostic('radiology','Ready for Radiologist Review') }
  return <PageContainer>
    <PageHeader title="Imaging Coordination" description="Coordinate imaging orders, preparation, scheduling, and operational handoff." />
    <Card variant="elevated" className="aivana-accent-line mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-4">{[['Patient',`${selectedPatient.name} · ${selectedPatient.mrn}`],['Requested study','CT chest, abdomen and pelvis'],['Modality / region','CT with contrast · chest / abdomen / pelvis'],['Clinical indication','Oncology staging review'],['Ordering department','Medical Oncology'],['Ordering clinician','Medical Oncologist'],['Priority','Routine'],['Order date','23 Aug 2026']].map(([label,value])=><div key={label}><p className="text-xs text-metadata">{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}</CardContent></Card>
    <div className="grid gap-6 xl:grid-cols-2">
      <Card><CardHeader className="border-b border-divider"><CardTitle>Scheduling</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[['Modality','CT with contrast'],['Appointment date','2026-08-24'],['Appointment time','11:30'],['Imaging location / scanner','Imaging Centre · CT-2']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}<label className="text-xs font-medium text-metadata">Appointment status<select value={appointmentStatus} onChange={(event)=>setAppointmentStatus(event.target.value)} className="mt-1 h-10 w-full rounded-md border border-input bg-input-background px-3 text-sm">{['Pending scheduling','Scheduled','Rescheduled','Cancelled','No-show','Completed'].map((value)=><option key={value}>{value}</option>)}</select></label><div className="flex flex-wrap items-end gap-2"><Button type="button" size="sm" variant="outline" onClick={()=>setAppointmentStatus('Rescheduled')}>Reschedule</Button><Button type="button" size="sm" variant="outline" onClick={()=>setAppointmentStatus('Cancelled')}>Cancel</Button></div></CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Preparation / prerequisites</CardTitle><CardDescription>Operational tracking only; clinical review remains with the appropriate clinician.</CardDescription></CardHeader><CardContent className="space-y-3 pt-6">{Object.entries(preparation).map(([item,status])=><div key={item} className="flex flex-col gap-2 rounded-lg border border-border bg-input-background p-3 sm:flex-row sm:items-center"><p className="min-w-0 flex-1 text-sm font-medium text-supporting">{item}</p><select aria-label={`${item} status`} value={status} onChange={(event)=>setPreparation((current)=>({...current,[item]:event.target.value}))} className="h-9 rounded-md border border-input bg-surface px-3 text-xs">{['Not required','Pending','Completed','Needs review'].map((value)=><option key={value}>{value}</option>)}</select></div>)}</CardContent></Card>
    </div>
    <Card className="mt-6"><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Scan workflow status</CardTitle><CardDescription className="mt-1">Operational progression without PACS integration</CardDescription></div><Badge variant="information">{scanStatus}</Badge></div></CardHeader><CardContent className="pt-6"><div className="flex flex-wrap gap-2">{scanSteps.map((step)=><Button key={step} type="button" size="sm" variant={scanStatus===step?'primary':'outline'} onClick={()=>{setScanStatus(step);advanceDiagnostic('radiology',step)}}>{step}</Button>)}</div><Button type="button" className="mt-5" onClick={markReady}>Ready for Radiologist Review</Button></CardContent></Card>
    <div className="mt-6 grid gap-6 xl:grid-cols-2">
      <Card><CardHeader className="border-b border-divider"><CardTitle>Delays / issues</CardTitle></CardHeader><CardContent className="space-y-4 pt-6"><label className="text-xs font-medium text-metadata">Operational blocker<select value={issue} onChange={(event)=>{setIssue(event.target.value);setIssueState('Open')}} className="mt-1 h-10 w-full rounded-md border border-input bg-input-background px-3 text-sm">{['Patient no-show','Preparation incomplete','Contrast clearance pending','Prerequisite lab pending','Prior imaging unavailable','Authorization pending','Scanner unavailable','Scheduling conflict','Study upload pending','Report delayed','Other'].map((value)=><option key={value}>{value}</option>)}</select></label><div className="flex flex-wrap items-center gap-2"><Badge variant={issueState==='Resolved'?'success':'warning'}>{issueState}</Badge><Button type="button" size="sm" variant="outline" onClick={()=>setIssueState('Escalated')}>Escalate</Button><Button type="button" size="sm" variant="outline" onClick={()=>{setAppointmentStatus('Rescheduled');setIssueState('Rescheduled')}}>Reschedule</Button><Button type="button" size="sm" onClick={()=>setIssueState('Resolved')}>Mark resolved</Button></div><p className="text-xs text-metadata">Current issue: {issue}. Clinical questions are escalated rather than interpreted by the coordinator.</p></CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Patient communication / preparation</CardTitle></CardHeader><CardContent className="space-y-3 pt-6">{['Preparation instructions shared','Appointment confirmed','Reminder sent','Patient contacted','Reschedule communication completed'].map((item)=><label key={item} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-input-background p-3 text-sm text-supporting"><span>{item}</span><input type="checkbox" checked={Boolean(communications[item])} onChange={()=>setCommunications((current)=>({...current,[item]:!current[item]}))} className="size-4 accent-primary"/></label>)}</CardContent></Card>
    </div>
    <Card className="mt-6"><CardHeader className="border-b border-divider"><CardTitle>Report tracking</CardTitle><CardDescription>Visibility only; report content and sign-off remain with the Radiologist.</CardDescription></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2 lg:grid-cols-4">{[['Shared workflow state',workflow.radiology],['Report status',scanStatus==='Report finalized'?'Finalized':'Pending'],['Critical finding','No notification recorded'],['Acknowledgement','Not applicable']].map(([label,value])=><div key={label} className="rounded-lg border border-border bg-input-background p-3"><p className="text-xs text-metadata">{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}</CardContent></Card>
  </PageContainer>
}

export default function RadiologyPage() {
  const { role } = useDemoAccess()
  const isRadiologist = role.roleId === 'radiologist'
  const [selectedId, setSelectedId] = React.useState('ct-cap')
  const [compareOpen, setCompareOpen] = React.useState(false)
  const [reviewed, setReviewed] = React.useState(false)
  const [acknowledged, setAcknowledged] = React.useState(false)
  const [reportState,setReportState] = React.useState<'Draft'|'Finalized'>('Draft')
  const [criticalFinding,setCriticalFinding] = React.useState(false)
  const [reportTechnique,setReportTechnique] = React.useState(studies[0].technique)
  const [reportFindings,setReportFindings] = React.useState(studies[0].findings)
  const [reportImpression,setReportImpression] = React.useState(studies[0].impression)
  const [note, setNote] = React.useState('Reviewed fictional staging CT report in the context of post-operative Stage IIA breast cancer. Imaging information will be correlated with pathology and multidisciplinary clinical assessment; no autonomous diagnosis or treatment decision made.')
  const selected = studies.find((study) => study.id === selectedId) ?? studies[0]
  const selectStudy = (id: string) => { const study=studies.find((item)=>item.id===id)??studies[0]; setSelectedId(id); setCompareOpen(false); setReviewed(false); setAcknowledged(false); setReportState('Draft'); setCriticalFinding(false); setReportTechnique(study.technique); setReportFindings(study.findings); setReportImpression(study.impression) }

  if (role.roleId === 'radiology') return <RadiologyCoordinationWorkspace />

  return (
    <PageContainer>
      <PageHeader title={isRadiologist ? 'Imaging Worklist' : 'Radiology'} description={isRadiologist ? 'Review oncology imaging studies and complete clinician-owned reports.' : 'Review oncology imaging studies, comparisons, and reports requiring clinician interpretation.'} actions={<Badge variant="information">Fictional demo data</Badge>} />
      {!isRadiologist ? <StakeholderWorkflowPanel module="radiology" /> : null}


      <Card className="mb-6 bg-surface-clinical"><CardContent className="grid gap-5 p-5 sm:grid-cols-2 xl:grid-cols-5"><div className="flex items-center gap-3 sm:col-span-2"><span className="flex size-10 items-center justify-center rounded-full bg-surface"><UserRound className="size-5" /></span><div><p className="font-display font-semibold">Sunita Patil <span className="text-xs font-normal text-metadata">(Fictional)</span></p><p className="text-xs text-metadata">MRN DEMO-ONC-02481 · 39 years · Female</p></div></div><div><p className="text-xs uppercase tracking-wider text-metadata">Diagnosis</p><p className="mt-1 text-sm font-medium text-supporting">Stage IIA breast cancer</p></div><div><p className="text-xs uppercase tracking-wider text-metadata">Treatment</p><p className="mt-1 text-sm font-medium text-supporting">AC chemotherapy · C2D8</p></div><div><p className="text-xs uppercase tracking-wider text-metadata">Encounter</p><p className="mt-1 text-sm font-medium text-supporting">23 Aug 2026 · OPD review</p></div></CardContent></Card>

      {isRadiologist ? <div className="mb-6 grid gap-6 xl:grid-cols-2"><Card><CardHeader className="border-b border-divider"><CardTitle>Study context</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[['Modality','CT with contrast'],['Study date / time','22 Aug 2026 · 15:40'],['Body region','Chest, abdomen and pelvis'],['Clinical indication','Oncology staging review'],['Referring service','Medical Oncology'],['Urgency','Routine'],['Contrast status','Intravenous contrast administered'],['Prior study','CT chest · 14 May 2026'],['Workflow state','In review · comparison available']].map(([label,value])=><div key={label}><p className="text-xs text-metadata">{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}</CardContent></Card><Card><CardHeader className="border-b border-divider"><CardTitle>DICOM / image viewer</CardTitle><CardDescription>Existing demonstration placeholder · no PACS connection</CardDescription></CardHeader><CardContent className="pt-6"><div className="flex min-h-48 items-center justify-center rounded-lg border border-dashed border-border bg-input-background"><div className="text-center"><Scan className="mx-auto size-8 text-metadata"/><p className="mt-3 text-sm font-semibold text-supporting">Imaging viewer placeholder</p><p className="mt-1 text-xs text-metadata">CT chest / abdomen / pelvis series</p></div></div></CardContent></Card></div> : null}

      {isRadiologist ? <Card className="mb-6"><CardHeader className="border-b border-divider"><CardTitle>Structured imaging interpretation</CardTitle><CardDescription>Radiologist-entered findings; no autonomous image analysis</CardDescription></CardHeader><CardContent className="space-y-6 pt-6"><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[['Primary lesion','Post-operative left breast bed'],['Tumour size','No measurable residual lesion documented'],['Tumour location','Left breast surgical bed'],['Local extension','No CT evidence documented'],['Lymph-node findings','No enlarged thoracic, abdominal, or pelvic nodes'],['Distant metastatic sites','No suspicious distant lesion documented'],['Additional findings','Post-operative changes'],['Interval change','No material interval change']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</div><div><p className="text-sm font-semibold text-supporting">Lesion measurements</p><div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[['Lesion site','Left breast surgical bed'],['Longest dimension','Not measurable'],['Short axis','Not applicable'],['Prior comparison','No measurable residual lesion']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</div></div><div className="grid gap-6 lg:grid-cols-2"><div><p className="text-sm font-semibold text-supporting">Response assessment</p><div className="mt-3 grid gap-3 sm:grid-cols-2">{[['Target lesions','None documented'],['Non-target lesions','Post-operative changes'],['New lesions','None documented'],['Overall response','Clinician-entered assessment pending']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</div><p className="mt-3 text-xs text-metadata">RECIST-style structured documentation only; no autonomous assessment.</p></div><div><p className="text-sm font-semibold text-supporting">Staging-relevant imaging evidence</p><div className="mt-3 grid gap-3 sm:grid-cols-3">{[['T evidence','Post-operative primary site'],['N evidence','No enlarged nodes on CT'],['M evidence','No distant metastatic finding on CT']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</div><p className="mt-3 text-xs text-metadata">Imaging evidence contributes to staging; the complete oncology stage is finalized by the treating team.</p></div></div></CardContent></Card> : null}

      {acknowledged ? <div role="status" className="mb-6 flex gap-3 rounded-lg border border-success/30 bg-success-subtle px-4 py-3 text-success-strong"><CheckCircle2 className="mt-0.5 size-5" /><div><p className="text-sm font-semibold">Report acknowledged in this demonstration</p><p className="text-xs">No acknowledgement, note, or report was submitted to a clinical system.</p></div></div> : null}

      <div className="grid gap-6 xl:grid-cols-3">
        <Card>
          <CardHeader className="border-b border-divider"><CardTitle>Imaging studies</CardTitle><CardDescription>Fictional oncology imaging timeline</CardDescription></CardHeader>
          <CardContent className="divide-y divide-divider p-0">{studies.map((study) => <button key={study.id} type="button" onClick={() => selectStudy(study.id)} className={cn('flex w-full items-start gap-3 px-5 py-4 text-left transition-colors', selectedId === study.id ? 'bg-brand-soft' : 'hover:bg-surface-app')}><span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-surface-elevated text-metadata"><Scan className="size-4" /></span><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-supporting">{study.title}</p><p className="mt-1 text-xs text-metadata">{study.modality} · {study.date}</p><div className="mt-2 flex flex-wrap gap-2"><Badge variant={studyStatus[study.status]}>{study.status}</Badge>{study.review ? <Badge variant="warning">Needs clinician review</Badge> : null}</div></div><ChevronRight className="mt-2 size-4 text-disabled" /></button>)}</CardContent>
        </Card>

        <div id={isRadiologist ? 'reports' : undefined} className="space-y-6 scroll-mt-6 xl:col-span-2">
          <Card variant="elevated">
            <CardHeader className="border-b border-divider"><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>{selected.title}</CardTitle><CardDescription className="mt-1">{selected.modality} · {selected.date}</CardDescription></div><div className="flex gap-2"><Badge variant={studyStatus[selected.status]}>{selected.status}</Badge>{selected.review ? <Badge variant="warning">Needs clinician review</Badge> : null}</div></div></CardHeader>
            <CardContent className="space-y-6 pt-6"><section><h3 className="text-sm font-semibold text-supporting">Indication</h3><p className="mt-2 text-sm leading-6 text-supporting">{selected.indication}</p></section><section><h3 className="text-sm font-semibold text-supporting">Technique</h3><p className="mt-2 text-sm leading-6 text-supporting">{selected.technique}</p></section><section><h3 className="text-sm font-semibold text-supporting">Findings</h3><p className="mt-2 text-sm leading-6 text-supporting">{selected.findings}</p></section><section className="rounded-md border border-brand-soft bg-surface-clinical p-4"><h3 className="text-sm font-semibold text-supporting">Impression</h3><p className="mt-2 text-sm leading-6 text-supporting">{selected.impression}</p></section><section><div className="flex items-center justify-between gap-3"><h3 className="text-sm font-semibold text-supporting">Comparison</h3><Button type="button" variant="outline" size="sm" onClick={() => setCompareOpen(!compareOpen)}><Columns2 />Compare with prior</Button></div>{compareOpen ? <div className="mt-3 grid gap-3 sm:grid-cols-2"><div className="rounded-md border border-border bg-input-background p-3"><p className="text-xs text-metadata">Current · {selected.date}</p><p className="mt-1 text-sm text-supporting">{selected.impression}</p></div><div className="rounded-md border border-border bg-input-background p-3"><p className="text-xs text-metadata">Comparison</p><p className="mt-1 text-sm text-supporting">{selected.comparison}</p></div></div> : <p className="mt-2 text-sm text-supporting">{selected.comparison}</p>}</section><div className="flex items-center gap-3 border-t border-divider pt-5"><FileImage className="size-4 text-metadata" /><div><p className="text-sm font-medium text-supporting">{selected.radiologist}</p><p className="text-xs text-metadata">Fictional radiologist and report status</p></div></div><div className="rounded-md border border-warning/30 bg-warning-subtle p-3 text-xs text-warning-strong">This report is information requiring clinician interpretation. It does not provide an autonomous diagnosis or treatment recommendation.</div></CardContent>
          </Card>

          {isRadiologist ? <Card><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Report drafting and finalization</CardTitle><CardDescription className="mt-1">Clinician-owned structured report</CardDescription></div><Badge variant={reportState==='Finalized'?'success':'warning'}>{reportState}</Badge></div></CardHeader><CardContent className="space-y-5 pt-6"><label className="block text-sm font-medium text-supporting">Technique<textarea rows={3} value={reportTechnique} onChange={(event)=>{setReportTechnique(event.target.value);setReportState('Draft')}} className="mt-2 flex h-auto w-full resize-y rounded-md border border-input bg-input-background px-3 py-2 text-sm"/></label><label className="block text-sm font-medium text-supporting">Findings<textarea rows={5} value={reportFindings} onChange={(event)=>{setReportFindings(event.target.value);setReportState('Draft')}} className="mt-2 flex h-auto w-full resize-y rounded-md border border-input bg-input-background px-3 py-2 text-sm"/></label><div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm font-medium text-supporting">Measurements<textarea rows={3} defaultValue="No measurable residual lesion. Comparison measurement not applicable." className="mt-2 flex h-auto w-full resize-y rounded-md border border-input bg-input-background px-3 py-2 text-sm"/></label><label className="block text-sm font-medium text-supporting">Comparison<textarea rows={3} defaultValue={selected.comparison} className="mt-2 flex h-auto w-full resize-y rounded-md border border-input bg-input-background px-3 py-2 text-sm"/></label></div><label className="block text-sm font-medium text-supporting">Impression<textarea rows={4} value={reportImpression} onChange={(event)=>{setReportImpression(event.target.value);setReportState('Draft')}} className="mt-2 flex h-auto w-full resize-y rounded-md border border-input bg-input-background px-3 py-2 text-sm"/></label><div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm font-medium text-supporting">Staging-relevant summary<textarea rows={3} defaultValue="Imaging evidence for T/N/M documented above; complete oncology staging remains with the treating team." className="mt-2 flex h-auto w-full resize-y rounded-md border border-input bg-input-background px-3 py-2 text-sm"/></label><label className="block text-sm font-medium text-supporting">Oncology response summary<textarea rows={3} defaultValue="No material interval change in this fictional comparison. Clinician-entered response assessment." className="mt-2 flex h-auto w-full resize-y rounded-md border border-input bg-input-background px-3 py-2 text-sm"/></label></div><div className={cn('rounded-lg border p-4',criticalFinding?'border-critical/30 bg-critical-subtle':'border-border bg-input-background')}><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="flex items-center gap-2 text-sm font-semibold text-supporting"><AlertTriangle className="size-4"/>Critical finding</p><p className="mt-1 text-xs text-metadata">Urgency, acknowledgement, and referring-team context use the existing local workflow only.</p></div><Button type="button" size="sm" variant={criticalFinding?'destructive':'outline'} onClick={()=>setCriticalFinding(!criticalFinding)}>{criticalFinding?'Critical finding flagged':'Flag critical finding'}</Button></div>{criticalFinding?<div className="mt-4 grid gap-3 sm:grid-cols-3"><Input aria-label="Critical finding detail" placeholder="Finding"/><Input aria-label="Critical finding urgency" placeholder="Urgency"/><Input aria-label="Responsible clinical team" defaultValue="Medical Oncology"/></div>:null}</div><div className="flex flex-wrap justify-end gap-2 border-t border-divider pt-5"><Button type="button" variant="outline" onClick={()=>setReportState('Draft')}>Save Draft</Button><Button type="button" variant="secondary" onClick={()=>setReportState('Draft')}>Edit</Button><Button type="button" disabled={!reportTechnique.trim()||!reportFindings.trim()||!reportImpression.trim()} onClick={()=>setReportState('Finalized')}>Finalize / Sign</Button></div><p className="text-xs text-metadata">Finalization is a browser-local demonstration and requires radiologist review. No report is transmitted.</p></CardContent></Card> : null}

          <Card>
            <CardHeader className="border-b border-divider"><div className="flex gap-3"><span className="flex size-9 items-center justify-center rounded-md bg-brand-soft"><ClipboardCheck className="size-4" /></span><div><CardTitle>Clinician review</CardTitle><CardDescription className="mt-1">Add context and acknowledge the selected report.</CardDescription></div></div></CardHeader>
            <CardContent className="space-y-5 pt-6"><div className="grid gap-3 sm:grid-cols-3"><div className={cn('rounded-md border p-3', reviewed ? 'border-success/30 bg-success-subtle' : 'border-border bg-input-background')}><p className="text-sm font-medium">1. Review report</p><p className="mt-1 text-xs text-metadata">Read findings, impression, and comparison.</p></div><div className="rounded-md border border-border bg-input-background p-3"><p className="text-sm font-medium">2. Add clinical note</p><p className="mt-1 text-xs text-metadata">Document clinical interpretation.</p></div><div className={cn('rounded-md border p-3', acknowledged ? 'border-success/30 bg-success-subtle' : 'border-border bg-input-background')}><p className="text-sm font-medium">3. Acknowledge</p><p className="mt-1 text-xs text-metadata">Confirm human review.</p></div></div><div className="space-y-2"><label htmlFor="radiologyNote" className="text-sm font-medium text-supporting">Clinical note</label><textarea id="radiologyNote" rows={4} value={note} onChange={(event) => { setNote(event.target.value); setAcknowledged(false) }} className="flex h-auto w-full resize-y rounded-md border border-input bg-input-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1" /><p className="text-xs text-metadata">Fictional note · not saved or submitted</p></div><div className="flex flex-col-reverse gap-3 border-t border-divider pt-5 sm:flex-row sm:items-center sm:justify-between">{reviewed ? <Badge variant="success"><CheckCircle2 />Report reviewed</Badge> : <Badge variant="warning"><Scan />Review pending</Badge>}<div className="flex gap-2"><Button type="button" variant="secondary" onClick={() => { setReviewed(true); setAcknowledged(false) }}>Review report</Button><Button type="button" disabled={!reviewed || !note.trim() || selected.status === 'Scheduled'} onClick={() => setAcknowledged(true)}>Acknowledge</Button></div></div></CardContent>
          </Card>
        </div>
      </div>
    </PageContainer>
  )
}
