'use client'

import * as React from 'react'
import { AlertTriangle, ArrowRight, Camera, CheckCircle2, Clock3, CreditCard, FileText, IdCard, Search, Upload, UserPlus, UserRound } from 'lucide-react'
import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { DocumentUploadField } from '@/components/documents/document-upload-field'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { DEMO_PATIENT_ID } from '@/lib/oncology/seed-data'
import { cn } from '@/lib/utils'
import type { ActorRef } from '@/lib/oncology/types'

type SearchState = 'empty' | 'searching' | 'match' | 'multiple' | 'none'
const consentLabels = ['Registration consent','Privacy / data consent','Communication consent'] as const
const documentLabels = ['Referral letter','Previous reports','Previous treatment records'] as const

function IdentityDocumentCard({
  title,
  type,
  icon: Icon,
  actor,
}: {
  title: string
  type: 'aadhaar' | 'ration'
  icon: typeof IdCard
  actor: ActorRef
}) {
  const isAadhaar = type === 'aadhaar'
  const [documentNumber, setDocumentNumber] = React.useState('')
  const [name, setName] = React.useState('')
  return <div className="aivana-gradient-soft rounded-xl border border-white/70 p-4 shadow-soft-sm">
    <div className="flex items-center gap-3">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-white/70 bg-surface/80 text-brand-deep"><Icon className="size-4" aria-hidden="true" /></span>
      <p className="text-sm font-semibold text-supporting">{title}</p>
    </div>
    <div className="mt-4 grid gap-4 sm:grid-cols-2">
      <label className="text-xs font-medium text-metadata">{isAadhaar ? 'Aadhaar number' : 'Ration card number'}<Input className="mt-1" inputMode={isAadhaar ? 'numeric' : 'text'} placeholder={isAadhaar ? 'Enter Aadhaar number' : 'Enter ration card number'} value={documentNumber} onChange={(e) => setDocumentNumber(e.target.value)} /></label>
      <label className="text-xs font-medium text-metadata">{isAadhaar ? 'Name as per Aadhaar' : 'Name as per ration card'}<Input className="mt-1" placeholder="Enter name on card" value={name} onChange={(e) => setName(e.target.value)} /></label>
      {isAadhaar ? <label className="text-xs font-medium text-metadata">Date of birth<Input className="mt-1" type="date" /></label> : <label className="text-xs font-medium text-metadata">Card type<Input className="mt-1" placeholder="Enter card type" /></label>}
      <label className="text-xs font-medium text-metadata sm:col-span-2">Address<textarea rows={3} placeholder="Enter address shown on card" className="mt-1 w-full resize-y rounded-md border border-input bg-input-background px-3 py-2 text-sm text-foreground shadow-sm placeholder:text-metadata focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" /></label>
      <div className="sm:col-span-2">
        <DocumentUploadField
          patientId={DEMO_PATIENT_ID} actor={actor} documentType={title} buttonLabel={`Upload ${title}`}
          onFieldAccepted={(key, value) => { if (key === 'documentNumber') setDocumentNumber(value); if (key === 'nameOnDocument') setName(value) }}
        />
      </div>
    </div>
  </div>
}

export function RegistrationWorkspace() {
  const { role, performAction } = useDemoAccess()
  const actor: ActorRef = { userId: role.roleId, name: role.label, roleLabel: role.label }
  const [query,setQuery] = React.useState('')
  const [searchState,setSearchState] = React.useState<SearchState>('empty')
  const [duplicate,setDuplicate] = React.useState(false)
  const [newPatient,setNewPatient] = React.useState(false)
  const [consents,setConsents] = React.useState<Record<string,boolean>>({})
  const [documents,setDocuments] = React.useState<Record<string,boolean>>({})
  const [referralLetter,setReferralLetter] = React.useState<File | null>(null)
  const referralLetterInput = React.useRef<HTMLInputElement>(null)
  const [registered,setRegistered] = React.useState(false)
  const [handoff,setHandoff] = React.useState(false)
  const allConsents = consentLabels.every((item)=>consents[item])
  const allDocuments = documentLabels.every((item)=>documents[item])
  const checklist = [
    ['Patient identity',newPatient || searchState==='match'],['Duplicate check',!duplicate && (newPatient || searchState==='match')],['Referral',true],['Consent',allConsents],['Required documents',allDocuments],['Appointment',true],['Queue assignment',true],
  ] as const
  const ready = checklist.every(([,done])=>done)

  const runSearch = () => { setSearchState('searching'); window.setTimeout(()=>{ const value=query.trim().toLowerCase(); setSearchState(!value?'empty':value.includes('sunita')||value.includes('02481')||value.includes('98765')||value.includes('abha')?'match':value.includes('patil')?'multiple':'none') },300) }
  const beginNew = () => { setNewPatient(true); setDuplicate(true); setRegistered(false); setHandoff(false) }
  const sendToNurse = () => { if(!registered) return; performAction('complete-registration','Registration completed','Registration',{status:'Completed'}); performAction('handoff-nurse','Handoff to Nurse Intake','Registration',{destination:'Nurse Intake',status:'Awaiting intake'}); setHandoff(true) }
  const clearReferralLetter = () => {
    setReferralLetter(null)
    if (referralLetterInput.current) referralLetterInput.current.value = ''
  }

  return <PageContainer>
    <PageHeader title="Registration" description="Search, register, schedule, and hand off an oncology patient." />

    <Card className="mb-6"><CardHeader className="border-b border-divider"><CardTitle>Patient search</CardTitle><CardDescription>Search the fictional demo register by name, MRN, mobile number, or ABHA/demo ID.</CardDescription></CardHeader><CardContent className="pt-6"><div className="flex flex-col gap-3 sm:flex-row"><Input aria-label="Patient search" value={query} onChange={(event)=>setQuery(event.target.value)} onKeyDown={(event)=>{if(event.key==='Enter')runSearch()}} placeholder="Name, MRN, mobile, or ABHA/demo ID"/><Button type="button" onClick={runSearch}><Search/>Search</Button><Button type="button" variant="outline" onClick={beginNew}><UserPlus/>New Patient</Button></div><div className="mt-4">{searchState==='empty'?<p className="text-sm text-metadata">Enter a search value to begin.</p>:searchState==='searching'?<p className="flex items-center gap-2 text-sm text-information-strong"><Clock3 className="size-4"/>Searching fictional patient registerâ€¦</p>:searchState==='none'?<div className="rounded-md border border-border p-4"><p className="text-sm font-semibold">No results</p><p className="mt-1 text-xs text-metadata">No fictional patient matches this search.</p></div>:searchState==='multiple'?<div className="rounded-md border border-warning/30 bg-warning-subtle p-4"><p className="text-sm font-semibold text-warning-strong">Multiple possible matches</p><p className="mt-1 text-xs text-warning-strong">Sunita Patil Â· DEMO-ONC-02481 and Sunita P. Â· DEMO-ONC-01944 require manual review.</p></div>:<div className="flex flex-wrap items-center gap-4 rounded-md border border-success/30 bg-success-subtle p-4"><span className="flex size-10 items-center justify-center rounded-full bg-surface"><UserRound className="size-5"/></span><div className="min-w-0 flex-1"><p className="font-semibold">Sunita Patil</p><p className="text-xs text-metadata">DEMO-ONC-02481 Â· Breast Cancer Â· Stage IIA Â· +91 98765 41028</p></div><Badge variant="success">Matching patient</Badge><Button type="button" variant="outline" onClick={()=>{setNewPatient(false);setDuplicate(false)}}>Open existing patient</Button></div>}</div></CardContent></Card>

    <Card className="mb-6"><CardHeader className="border-b border-divider"><CardTitle>CCA registration and queue context</CardTitle></CardHeader><CardContent className="grid gap-3 pt-6 sm:grid-cols-2 lg:grid-cols-4">{[['ABDM registration','Demo placeholder complete'],['Aadhaar / identity','Demo placeholder verified'],['Current location','Front Office'],['Next location','Nurse Intake'],['Queue','Queue #04 · Ready'],['Specialty','Medical Oncology'],['Appointment','24 Aug 2026 · 09:30'],['Consent document',allConsents?'DEMO consent available':'Pending completion']].map(([label,value])=><div key={label} className="rounded-md border border-border bg-input-background p-3"><p className="text-[10px] font-semibold uppercase tracking-wider text-metadata">{label}</p><p className="mt-1 text-sm font-medium text-supporting">{value}</p></div>)}</CardContent></Card>

    {duplicate?<Card className="mb-6 border-warning/40 bg-warning-subtle"><CardContent className="flex flex-wrap items-center gap-4 p-5"><AlertTriangle className="size-5 text-warning-strong"/><div className="min-w-0 flex-1"><p className="font-semibold text-warning-strong">Possible existing patient</p><p className="mt-1 text-sm text-warning-strong">Sunita Patil Â· DEMO-ONC-02481 Â· Breast Cancer Â· Stage IIA</p><p className="mt-1 text-xs text-warning-strong">Demo duplicate check only â€” not a real MPI or ABDM duplicate service.</p></div><Button type="button" variant="outline" onClick={()=>{setDuplicate(false);setNewPatient(false);setSearchState('match')}}>Open existing patient</Button><Button type="button" onClick={()=>setDuplicate(false)}>Continue as new patient</Button></CardContent></Card>:null}

    <div className="grid gap-6 xl:grid-cols-3"><div className="space-y-6 xl:col-span-2">
      <Card><CardHeader className="border-b border-divider"><CardTitle>Patient identity</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2"><div className="flex min-h-28 flex-col items-center justify-center rounded-lg border border-dashed border-border bg-input-background sm:row-span-2"><Camera className="size-5 text-metadata"/><p className="mt-2 text-xs text-metadata">Photo / avatar capture placeholder</p><Button type="button" size="sm" variant="ghost">Capture photo</Button></div>{[['Full name','Sunita Patil'],['Date of birth','18 Apr 1987'],['Gender','Female'],['Mobile','+91 98765 41028'],['Address','14 Demo Care Lane, Bengaluru'],['ABHA / demo ID','DEMO-ABHA-91-2481']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Identity documents</CardTitle><CardDescription>Enter patient-provided Aadhaar or ration card details.</CardDescription></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2"><IdentityDocumentCard title="Aadhaar Card" type="aadhaar" icon={IdCard} actor={actor}/><IdentityDocumentCard title="Ration Card" type="ration" icon={CreditCard} actor={actor}/></CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Referral</CardTitle><CardDescription>Organization â†’ Department â†’ Referring clinician</CardDescription></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[['Referring organization','Northstar Community Hospital (Demo)'],['Department','Breast Surgery'],['Referring clinician','Referring Clinician'],['Referral reason','Multidisciplinary oncology review']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}<div className="rounded-md border border-border p-4 sm:col-span-2"><p className="text-sm font-medium">Referral Letter</p><p className="mt-1 text-xs text-metadata">Upload referral letter if provided</p>{referralLetter?<div className="mt-3 flex flex-col gap-3 rounded-md border border-success/30 bg-success-subtle p-3 sm:flex-row sm:items-center"><FileText className="size-5 shrink-0 text-success-strong" aria-hidden="true"/><div className="min-w-0 flex-1"><p className="break-words text-sm font-medium text-supporting">{referralLetter.name}</p><p className="mt-0.5 text-xs text-metadata">{referralLetter.type || 'Document'}</p></div><Badge variant="success">Uploaded</Badge><div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="outline" onClick={clearReferralLetter}>Remove</Button><label className="inline-flex h-9 cursor-pointer items-center justify-center rounded-md border border-input bg-surface px-3 text-xs font-medium text-supporting shadow-sm transition-colors hover:bg-secondary"><Upload className="mr-2 size-4" aria-hidden="true"/>Replace<input ref={referralLetterInput} type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" className="sr-only" onChange={(event)=>setReferralLetter(event.target.files?.[0] ?? null)}/></label></div></div>:<label className="mt-3 flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-md border border-dashed border-border bg-input-background px-3 py-2 text-sm font-medium text-supporting transition-colors hover:bg-surface"><Upload className="size-4 text-metadata" aria-hidden="true"/>Upload referral letter<input ref={referralLetterInput} type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" className="sr-only" onChange={(event)=>setReferralLetter(event.target.files?.[0] ?? null)}/></label>}</div></CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Consent</CardTitle><CardDescription>Workflow demonstration only â€” not legally binding electronic consent</CardDescription></CardHeader><CardContent className="space-y-3 pt-6">{consentLabels.map((item)=><div key={item} className="flex items-center justify-between gap-3 rounded-md border border-border p-3"><span className="text-sm font-medium">{item}</span><div className="flex items-center gap-2"><Badge variant={consents[item]?'success':'warning'}>{consents[item]?'Completed':'Pending'}</Badge><Button type="button" size="sm" variant="outline" onClick={()=>setConsents((value)=>({...value,[item]:!value[item]}))}>{consents[item]?'Reset':'Complete demo consent'}</Button></div></div>)}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Documents</CardTitle><CardDescription>Upload a real file — text is extracted client-side via OCR for review</CardDescription></CardHeader><CardContent className="space-y-4 pt-6">{documentLabels.map((item)=><div key={item} className="rounded-md border border-border p-3"><div className="mb-3 flex items-center gap-3"><FileText className="size-4 text-metadata"/><p className="text-sm font-medium">{item}</p>{documents[item]?<Badge variant="success" className="ml-auto">Uploaded</Badge>:null}</div><DocumentUploadField patientId={DEMO_PATIENT_ID} actor={actor} documentType={item} buttonLabel={`Upload ${item}`} onUploaded={()=>setDocuments((value)=>({...value,[item]:true}))} /></div>)}</CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Scheduling</CardTitle><CardDescription>Fictional appointment and queue assignment</CardDescription></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[['Department','Medical Oncology'],['Location','OPD-2'],['Doctor','Medical Oncologist'],['Appointment date / time','24 Aug 2026 Â· 09:30'],['Queue number','Queue #04']].map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</CardContent></Card>
    </div><div className="space-y-6"><Card variant="elevated"><CardHeader className="border-b border-divider"><CardTitle>Registration checklist</CardTitle><CardDescription>{ready?'Ready to register':'Incomplete registration'}</CardDescription></CardHeader><CardContent className="space-y-3 pt-6">{checklist.map(([label,done])=><div key={label} className="flex items-center justify-between gap-3"><span className={cn('text-sm',done?'text-supporting':'text-warning-strong')}>{label}</span><Badge variant={done?'success':'warning'}>{done?'Complete':'Incomplete'}</Badge></div>)}<Button type="button" className="mt-3 w-full" disabled={!ready||registered} onClick={()=>setRegistered(true)}>{registered?'Registration complete':'Complete registration'}</Button>{registered?<Button type="button" className="w-full" onClick={sendToNurse} disabled={handoff}>Send to Nurse Intake <ArrowRight/></Button>:null}{handoff?<div className="rounded-md border border-success/30 bg-success-subtle p-3 text-sm font-medium text-success-strong"><CheckCircle2 className="mr-2 inline size-4"/>Patient handed off to Nurse Intake</div>:null}</CardContent></Card><Card><CardHeader className="border-b border-divider"><CardTitle>Workflow state</CardTitle></CardHeader><CardContent className="space-y-2 pt-6">{[['Search',searchState],['Duplicate',duplicate?'Possible duplicate':'Checked'],['Registration',registered?'Complete':ready?'Ready to register':'Incomplete'],['Handoff',handoff?'Complete':'Pending']].map(([label,value])=><div key={label} className="flex justify-between gap-3 text-sm"><span className="text-metadata">{label}</span><span className="font-medium capitalize text-supporting">{value}</span></div>)}<p className="pt-3 text-xs text-metadata">Actor role: {role.label}</p></CardContent></Card></div></div>
  </PageContainer>
}

