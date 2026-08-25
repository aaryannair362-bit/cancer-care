'use client'

import * as React from 'react'
import Link from 'next/link'
import {
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  FlaskConical,
  HeartHandshake,
  ListChecks,
  Mic,
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

const treatmentSteps = [
  { title: 'Breast-conserving surgery', detail: 'Lumpectomy with sentinel-node assessment', date: '12 Jun 2026', status: 'Completed' },
  { title: 'Adjuvant AC chemotherapy', detail: 'Doxorubicin and cyclophosphamide · four-cycle course', date: 'Current · Cycle 2, Day 8', status: 'In progress' },
  { title: 'Cycle 3 readiness review', detail: 'Clinical toxicity assessment and laboratory clearance', date: 'Next review · 30 Aug 2026', status: 'Upcoming' },
  { title: 'Radiation oncology review', detail: 'Review after systemic therapy completion and clinician confirmation', date: 'Future milestone', status: 'Planned' },
] as const

const clinicalPlan = [
  { title: 'Investigations', icon: FlaskConical, items: ['CBC with ANC before the next cycle', 'Renal and liver function panel', 'Review available staging CT report'] },
  { title: 'Supportive care', icon: HeartHandshake, items: ['Optimise antiemetic schedule', 'Hydration and nutrition support', 'Reinforce neutropenic precautions'] },
  { title: 'Follow-up', icon: CalendarClock, items: ['Medical oncology review before cycle 3', 'Earlier review for fever or clinical deterioration'] },
  { title: 'Monitoring requirements', icon: ShieldCheck, items: ['Treatment toxicity and performance status', 'Allergy acknowledgement', 'Laboratory and symptom review'] },
] as const

const statusVariant = { Completed: 'success', 'In progress': 'information', Upcoming: 'warning', Planned: 'neutral' } as const

function SurgicalPlanWorkspace() {
  const { selectedPatient, selectPatient } = useDemoAccess()
  const [query,setQuery] = React.useState('')
  const [searched,setSearched] = React.useState(false)
  const [operability,setOperability] = React.useState('Operable')
  const [intent,setIntent] = React.useState('Curative')
  const [requirements,setRequirements] = React.useState<Record<string,boolean>>({})
  const matches = [demoPatient.name,demoPatient.mrn,demoPatient.mobile].some((value)=>value.toLowerCase().includes(query.trim().toLowerCase()))
  const preoperativeRequirements = ['Laboratory review','Imaging review','Anaesthetic fitness','Specialty clearance if required','Blood preparation','Consent','Other pending investigations']

  return <PageContainer>
    <PageHeader title="Surgical Plan" description="Operability, proposed surgery, and peri-operative next steps" />
    <Card className="mb-6"><CardContent className="p-5"><p className="text-sm font-semibold text-supporting">Search patient</p><div className="mt-3 flex flex-col gap-3 sm:flex-row"><Input aria-label="Surgical plan patient search" placeholder="Name, MRN or mobile number" value={query} onChange={(event)=>{setQuery(event.target.value);setSearched(false)}}/><Button type="button" onClick={()=>setSearched(true)}><Search/>Search</Button></div>{searched?<div className="mt-3 rounded-lg border border-border bg-surface-app p-3">{matches?<div className="flex flex-wrap items-center gap-3"><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-supporting">{demoPatient.name}</p><p className="text-xs text-metadata">MRN {demoPatient.mrn} · {demoPatient.mobile}</p></div><Button type="button" size="sm" onClick={()=>selectPatient(demoPatient)}>Open Surgical Plan</Button></div>:<p className="text-sm text-metadata">No matching patient.</p>}</div>:null}</CardContent></Card>
    <Card variant="elevated" className="aivana-accent-line mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-4">{[['Patient',`${selectedPatient.name} · ${selectedPatient.mrn}`],['Diagnosis / site',selectedPatient.diagnosis],['Stage / TNM',`${selectedPatient.stage} · pT2N0M0`],['Histology','Grade 2 invasive ductal carcinoma'],['Biomarkers',selectedPatient.biology],['ECOG','1'],['Previous major treatment','Lumpectomy · June 2026'],['Current treatment',`${selectedPatient.treatment} · ${selectedPatient.treatmentPoint}`]].map(([label,value])=><div key={label} className="min-w-0"><p className="text-xs text-metadata">{label}</p><p className="mt-1 break-words text-sm font-semibold text-supporting">{value}</p></div>)}</CardContent></Card>
    <div className="grid gap-6 xl:grid-cols-2">
      <Card><CardHeader className="border-b border-divider"><CardTitle>Operability and intent</CardTitle></CardHeader><CardContent className="space-y-5 pt-6"><div><p className="text-sm font-semibold text-supporting">Operability</p><div className="mt-3 flex flex-wrap gap-2">{['Operable','Borderline resectable','Currently unresectable','Reassess after treatment'].map((value)=><Button key={value} type="button" size="sm" variant={operability===value?'primary':'outline'} onClick={()=>setOperability(value)}>{value}</Button>)}</div></div><div><p className="text-sm font-semibold text-supporting">Surgical intent</p><div className="mt-3 flex flex-wrap gap-2">{['Curative','Diagnostic','Cytoreductive','Palliative','Salvage'].map((value)=><Button key={value} type="button" size="sm" variant={intent===value?'primary':'outline'} onClick={()=>setIntent(value)}>{value}</Button>)}</div></div></CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Proposed surgery</CardTitle><CardDescription>Clinician-entered planning fields</CardDescription></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[['Planned procedure','Finalize after imaging review'],['Anatomical site','Left breast'],['Laterality','Left'],['Lymph-node procedure','To be confirmed'],['Organ resection details','Not applicable / confirm'],['Reconstruction requirement','To be assessed']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Pre-operative requirements</CardTitle></CardHeader><CardContent className="grid gap-3 pt-6 sm:grid-cols-2">{preoperativeRequirements.map((item)=><label key={item} className="flex items-center gap-3 rounded-lg border border-border bg-input-background p-3 text-sm text-supporting"><input type="checkbox" checked={Boolean(requirements[item])} onChange={()=>setRequirements((current)=>({...current,[item]:!current[item]}))} className="size-4 accent-primary"/>{item}</label>)}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Planned surgery and context</CardTitle></CardHeader><CardContent className="space-y-4 pt-6"><div className="grid gap-4 sm:grid-cols-3">{[['Planned date','To be scheduled'],['Status','Planning'],['Pre-operative readiness','Pending review']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</div><div className="flex flex-wrap gap-2"><Badge variant="success">MDT recommendation available</Badge><Badge variant="information">Guideline Pathway available</Badge><Badge variant="information">Staging available</Badge></div></CardContent></Card>
    </div>
    <Card className="mt-6"><CardHeader className="border-b border-divider"><CardTitle>Post-operative plan</CardTitle></CardHeader><CardContent className="grid gap-3 pt-6 sm:grid-cols-2 lg:grid-cols-4">{['Pathology review','Wound / post-operative review','Adjuvant referral','Repeat MDT if needed','Follow-up'].map((item)=><div key={item} className="rounded-lg border border-border bg-input-background p-3 text-sm font-medium text-supporting">{item}</div>)}</CardContent></Card>
    <Card variant="elevated" className="mt-6"><CardHeader className="border-b border-divider"><CardTitle>Next surgical action</CardTitle></CardHeader><CardContent className="flex flex-col gap-4 pt-6 sm:flex-row sm:items-center sm:justify-between"><p className="text-sm font-semibold text-supporting">Review imaging and finalize surgical approach</p><div className="flex flex-wrap gap-2"><Link href="/doctor-opd" className={buttonVariants({size:'sm'})}>Open Consultation<ArrowRight/></Link><Link href="/mdt-tumour-board" className={buttonVariants({variant:'outline',size:'sm'})}>View MDT</Link></div></CardContent></Card>
  </PageContainer>
}

function RadiationPlanWorkspace() {
  const { selectedPatient, selectPatient } = useDemoAccess()
  const [query,setQuery] = React.useState('')
  const [searched,setSearched] = React.useState(false)
  const [intent,setIntent] = React.useState('Adjuvant')
  const [planningState,setPlanningState] = React.useState('Simulation pending')
  const [requirements,setRequirements] = React.useState<Record<string,boolean>>({})
  const matches = [demoPatient.name,demoPatient.mrn,demoPatient.mobile].some((value)=>value.toLowerCase().includes(query.trim().toLowerCase()))
  const preparation = ['Planning imaging','Simulation','Immobilisation requirement','Pathology review','Laboratory review where required','Dental clearance where relevant','Anaesthetic / specialty clearance','Consent','Concurrent treatment coordination','Other clinician-entered requirement']

  return <PageContainer>
    <PageHeader title="Radiation Plan" description="Radiation treatment planning, readiness, and course review" />
    <Card className="mb-6"><CardContent className="p-5"><p className="text-sm font-semibold text-supporting">Search patient</p><div className="mt-3 flex flex-col gap-3 sm:flex-row"><Input aria-label="Radiation plan patient search" placeholder="Name, MRN or mobile number" value={query} onChange={(event)=>{setQuery(event.target.value);setSearched(false)}}/><Button type="button" onClick={()=>setSearched(true)}><Search/>Search</Button></div>{searched?<div className="mt-3 rounded-lg border border-border bg-surface-app p-3">{matches?<div className="flex flex-wrap items-center gap-3"><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-supporting">{demoPatient.name}</p><p className="text-xs text-metadata">MRN {demoPatient.mrn} · {demoPatient.mobile}</p></div><Button type="button" size="sm" onClick={()=>selectPatient(demoPatient)}>Open Radiation Plan</Button></div>:<p className="text-sm text-metadata">No matching patient.</p>}</div>:null}</CardContent></Card>
    <Card variant="elevated" className="aivana-accent-line mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-4">{[['Patient',`${selectedPatient.name} · ${selectedPatient.mrn}`],['Diagnosis / primary site',selectedPatient.diagnosis],['Histology','Grade 2 invasive ductal carcinoma'],['Stage / TNM',`${selectedPatient.stage} · pT2N0M0`],['Biomarkers',selectedPatient.biology],['ECOG','1'],['Previous surgery','Lumpectomy · June 2026'],['Systemic therapy',`${selectedPatient.treatment} · ${selectedPatient.treatmentPoint}`],['Previous radiotherapy','None documented']].map(([label,value])=><div key={label} className="min-w-0"><p className="text-xs text-metadata">{label}</p><p className="mt-1 break-words text-sm font-semibold text-supporting">{value}</p></div>)}</CardContent></Card>
    <Card className="mb-6"><CardHeader className="border-b border-divider"><CardTitle>Radiation intent</CardTitle></CardHeader><CardContent className="flex flex-wrap gap-2 pt-6">{['Definitive / Curative','Adjuvant','Neoadjuvant','Palliative','Salvage','Consolidative','Other'].map((value)=><Button key={value} type="button" size="sm" variant={intent===value?'primary':'outline'} onClick={()=>setIntent(value)}>{value}</Button>)}</CardContent></Card>
    <div className="grid gap-6 xl:grid-cols-2">
      <Card><CardHeader className="border-b border-divider"><CardTitle>Radiation treatment details</CardTitle><CardDescription>Documentation and planning UI only</CardDescription></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[['Treatment site','Left breast'],['Target region','Clinician definition pending'],['Laterality','Left'],['Planned technique','To be selected by treating clinician'],['Total planned dose','Enter planned dose'],['Dose per fraction','Enter dose per fraction'],['Number of fractions','Enter planned fractions'],['Treatment schedule','To be scheduled'],['Concurrent systemic therapy','To be discussed / pending']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Planning workflow status</CardTitle></CardHeader><CardContent className="pt-6"><div className="flex flex-wrap gap-2">{['Consultation completed','Simulation pending','Simulation completed','Contouring pending','Contouring completed','Plan preparation','Plan review','Ready for treatment','On treatment','Treatment completed'].map((value)=><Button key={value} type="button" size="sm" variant={planningState===value?'primary':'outline'} onClick={()=>setPlanningState(value)}>{value}</Button>)}</div><Badge className="mt-4" variant="warning">{planningState}</Badge></CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Pre-radiation requirements</CardTitle></CardHeader><CardContent className="grid gap-3 pt-6 sm:grid-cols-2">{preparation.map((item)=><label key={item} className="flex items-center gap-3 rounded-lg border border-border bg-input-background p-3 text-sm text-supporting"><input type="checkbox" checked={Boolean(requirements[item])} onChange={()=>setRequirements((current)=>({...current,[item]:!current[item]}))} className="size-4 accent-primary"/>{item}</label>)}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Treatment course</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[['Treatment start date','Not started'],['Planned fractions','Pending prescription'],['Completed fractions','0'],['Remaining fractions','Pending prescription'],['Expected completion date','To be scheduled'],['Treatment interruptions','None documented'],['Interruption reason','Not applicable']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>On-treatment review / toxicity</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[['Treatment tolerance','Not yet on treatment'],['Acute toxicity','None documented'],['Symptom change','Clinician review'],['Skin / mucosal effects','Not applicable before treatment'],['Pain','Review during encounter'],['Nutrition / hydration','Review as clinically indicated'],['Treatment interruption','No'],['Supportive-care requirement','To be assessed']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Post-radiation plan</CardTitle></CardHeader><CardContent className="grid gap-3 pt-6 sm:grid-cols-2">{['Treatment completion summary','Follow-up timing','Imaging follow-up','Medical Oncology follow-up','Surgical Oncology follow-up','MDT reassessment where needed','Supportive care','Surveillance plan'].map((item)=><label key={item} className="text-xs font-medium text-metadata">{item}<Input className="mt-1" placeholder="Clinician-entered plan"/></label>)}</CardContent></Card>
    </div>
    <Card variant="elevated" className="mt-6"><CardHeader className="border-b border-divider"><CardTitle>Next radiation action</CardTitle></CardHeader><CardContent className="flex flex-col gap-4 pt-6 sm:flex-row sm:items-center sm:justify-between"><p className="text-sm font-semibold text-supporting">Review imaging and finalize simulation / radiation planning</p><div className="flex flex-wrap gap-2"><Link href="/doctor-opd" className={buttonVariants({size:'sm'})}>Open Consultation<ArrowRight/></Link><Link href="/mdt-tumour-board" className={buttonVariants({variant:'outline',size:'sm'})}>View MDT</Link></div></CardContent></Card>
  </PageContainer>
}

export default function CarePlanPage() {
  const { role, selectedPatient, selectPatient, workflow } = useDemoAccess()
  const [query,setQuery] = React.useState('')
  const [searched,setSearched] = React.useState(false)
  const matches = [demoPatient.name,demoPatient.mrn,demoPatient.mobile].some((value)=>value.toLowerCase().includes(query.trim().toLowerCase()))

  if (role.roleId === 'surgical-oncology') return <SurgicalPlanWorkspace />
  if (role.roleId === 'radiation-oncology') return <RadiationPlanWorkspace />

  return <PageContainer>
    <PageHeader title="Treatment Plan" description="Active treatment plan and next clinical steps" />

    <Card className="mb-6"><CardContent className="p-5"><p className="text-sm font-semibold text-supporting">Search patient</p><div className="mt-3 flex flex-col gap-3 sm:flex-row"><Input aria-label="Treatment plan patient search" placeholder="Name, MRN or mobile number" value={query} onChange={(event)=>{setQuery(event.target.value);setSearched(false)}}/><Button type="button" onClick={()=>setSearched(true)}><Search/>Search</Button></div>{searched?<div className="mt-3 rounded-lg border border-border bg-surface-app p-3">{matches?<div className="flex flex-wrap items-center gap-3"><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-supporting">{demoPatient.name}</p><p className="text-xs text-metadata">MRN {demoPatient.mrn} · {demoPatient.mobile}</p></div><Button type="button" size="sm" onClick={()=>selectPatient(demoPatient)}>Open Treatment Plan</Button></div>:<p className="text-sm text-metadata">No matching patient.</p>}</div>:null}</CardContent></Card>

    <Card variant="elevated" className="mb-6 aivana-accent-line">
      <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center">
        <span className="flex size-11 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand-deep"><UserRound className="size-5" /></span>
        <div className="min-w-0 flex-1"><p className="break-words font-display text-lg font-semibold text-foreground">{selectedPatient.name}</p><p className="mt-1 break-words text-sm text-metadata">MRN {selectedPatient.mrn} · {selectedPatient.age} years · {selectedPatient.sex}</p></div>
        <Badge className="w-fit shrink-0 whitespace-nowrap" variant="information">{workflow.phase}</Badge>
      </CardContent>
    </Card>

    <Card className="mb-6">
      <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><ClipboardCheck className="size-4" /></span><div><CardTitle>Active care plan</CardTitle><CardDescription className="mt-1">Current clinician-owned treatment context</CardDescription></div></div></CardHeader>
      <CardContent className="grid gap-4 pt-6 sm:grid-cols-2 lg:grid-cols-3">
        {[
          ['Diagnosis', selectedPatient.diagnosis],
          ['Cancer stage', selectedPatient.stage],
          ['TNM', 'pT2N0M0'],
          ['Key biomarkers', selectedPatient.biology],
          ['ECOG', '1'],
          ['Treatment intent', 'Adjuvant · curative intent'],
          ['Previous major treatment', 'Lumpectomy · June 2026'],
          ['Current treatment / regimen', 'Doxorubicin + cyclophosphamide'],
          ['Current cycle / day', selectedPatient.treatmentPoint],
          ['Treating clinician', 'Medical Oncologist'],
        ].map(([label,value])=><div key={label} className="min-w-0 rounded-xl border border-white/70 bg-surface/80 p-4 shadow-soft-sm"><p className="text-xs font-medium text-metadata">{label}</p><p className="mt-2 break-words text-sm font-semibold leading-5 text-supporting">{value}</p></div>)}
      </CardContent>
    </Card>

    <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><ListChecks className="size-4" /></span><div><CardTitle>Treatment plan</CardTitle><CardDescription className="mt-1">Completed, current, and upcoming treatment milestones</CardDescription></div></div></CardHeader>
        <CardContent className="pt-6"><ol className="space-y-4">{treatmentSteps.map((step,index)=><li key={step.title} className="relative flex min-w-0 gap-4"><div className="flex flex-col items-center"><span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-white/70 bg-brand-soft text-xs font-semibold text-brand-deep">{index + 1}</span>{index < treatmentSteps.length - 1 ? <span className="mt-2 h-full min-h-10 w-px bg-divider" /> : null}</div><div className="min-w-0 flex-1 pb-2"><div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><p className="break-words text-sm font-semibold text-supporting">{step.title}</p><p className="mt-1 break-words text-xs leading-5 text-metadata">{step.detail}</p><p className="mt-2 text-xs font-medium text-supporting">{step.date}</p></div><Badge className="w-fit shrink-0 whitespace-nowrap" variant={statusVariant[step.status]}>{step.status}</Badge></div></div></li>)}</ol></CardContent>
      </Card>

      <div className="min-w-0 space-y-6">
        <Card variant="elevated"><CardHeader className="border-b border-divider"><CardTitle>Next clinical action</CardTitle></CardHeader><CardContent className="pt-6"><p className="text-sm font-semibold leading-6 text-supporting">Review treatment toxicity and CBC/ANC before confirming cycle 3 readiness.</p><div className="mt-4 flex flex-wrap gap-2"><Link href="/doctor-opd" className={buttonVariants({ size: 'sm' })}>Open Doctor OPD<ArrowRight /></Link><Link href="/lab" className={buttonVariants({ variant: 'outline', size: 'sm' })}>Review laboratory results</Link></div></CardContent></Card>
        <Card><CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><Users className="size-4 text-brand-deep"/><CardTitle>MDT / care team decisions</CardTitle></div></CardHeader><CardContent className="space-y-4 pt-6"><div><p className="text-xs text-metadata">Agreed direction</p><p className="mt-1 text-sm font-medium leading-6 text-supporting">Continue the documented adjuvant pathway subject to treating-clinician review and treatment readiness.</p></div><div className="grid gap-3 sm:grid-cols-2"><div><p className="text-xs text-metadata">Responsible clinician</p><p className="mt-1 text-sm font-semibold text-supporting">Medical Oncologist</p></div><div><p className="text-xs text-metadata">Coordination owner</p><p className="mt-1 text-sm font-semibold text-supporting">Patient Liaison / Care Coordinator</p></div></div><Badge variant="warning">Clinician review required</Badge></CardContent></Card>
      </div>
    </div>

    <Card className="mt-6">
      <CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><Stethoscope className="size-4 text-brand-deep"/><CardTitle>Clinical plan</CardTitle></div></CardHeader>
      <CardContent className="grid gap-4 pt-6 md:grid-cols-2 xl:grid-cols-4">{clinicalPlan.map((section)=>{const Icon=section.icon;return <section key={section.title} className="min-w-0 rounded-xl border border-white/70 bg-surface/76 p-5 shadow-soft-sm"><div className="flex items-center gap-3"><span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><Icon className="size-4" /></span><h3 className="break-words text-sm font-semibold text-supporting">{section.title}</h3></div><ul className="mt-4 space-y-3">{section.items.map((item)=><li key={item} className="flex min-w-0 gap-2 text-xs leading-5 text-metadata"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-brand-deep"/><span className="min-w-0 break-words">{item}</span></li>)}</ul></section>})}</CardContent>
    </Card>

    <Card variant="ai" className="mt-6"><CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center"><div className="flex min-w-0 flex-1 gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-ai-highlight text-brand-deep"><Mic className="size-4" /></span><div className="min-w-0"><p className="text-sm font-semibold text-supporting">Clinical documentation</p><p className="mt-1 break-words text-xs leading-5 text-metadata">Continue encounter documentation or review supporting clinical records.</p></div></div><div className="flex min-w-0 flex-wrap gap-2"><Link href="/opd-scribe" className={buttonVariants({ variant: 'secondary', size: 'sm' })}><Mic />Open Scribe</Link><Link href="/documents" className={buttonVariants({ variant: 'outline', size: 'sm' })}><FileText />View documentation</Link></div></CardContent></Card>
  </PageContainer>
}
