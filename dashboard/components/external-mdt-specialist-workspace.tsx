'use client'

import * as React from 'react'
import { ArrowRight, CheckCircle2, Clock3, LockKeyhole, ShieldCheck } from 'lucide-react'
import { useRouter } from 'next/navigation'

import { demoPatient, useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

const selectClassName = 'h-10 w-full min-w-0 rounded-xl border border-input bg-input-background px-3 text-sm text-supporting shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
const labelClassName = 'text-xs font-medium text-metadata'
const accessExpiry = new Date('2026-08-31T18:00:00+05:30')

function accessState() {
  const now = new Date()
  if (now >= accessExpiry) return { label:'Expired', active:false, variant:'critical' as const }
  const hours = (accessExpiry.getTime()-now.getTime())/36e5
  return { label:hours<=72?'Expiring':'Active', active:true, variant:hours<=72?'warning' as const:'success' as const }
}

export function ExternalAssignedCases() {
  const router = useRouter()
  const { selectPatient } = useDemoAccess()
  const access = accessState()
  const openCase = () => { if (!access.active) return; selectPatient(demoPatient); router.push('/mdt-tumour-board') }

  return <PageContainer>
    <PageHeader title="Assigned MDT cases" description="Cases shared with you for multidisciplinary review" />
    <div className="mb-6 flex items-start gap-3 rounded-lg border border-information/25 bg-information-subtle p-4 text-information-strong"><LockKeyhole className="mt-0.5 size-4 shrink-0"/><p className="text-sm">Case-scoped access only. Hospital-wide patient search and unrelated records are unavailable.</p></div>
    <Card><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Assigned cases</CardTitle><CardDescription className="mt-1">Explicit MDT invitations available to this specialist</CardDescription></div><Badge variant="neutral">1 assigned case</Badge></div></CardHeader><CardContent className="pt-6"><article className="min-w-0 rounded-xl border border-white/75 bg-[linear-gradient(145deg,hsl(var(--surface)/0.98),hsl(var(--surface-elevated)/0.72))] p-5 shadow-soft-sm"><div className="grid gap-5 lg:grid-cols-3"><div className="min-w-0"><p className="font-display text-lg font-semibold">Case MDT-DEMO-118</p><p className="mt-1 text-xs text-metadata">Limited identity · {demoPatient.age} years · {demoPatient.sex}</p><div className="mt-3 flex flex-wrap gap-2"><Badge variant="information">Shared for MDT review</Badge><Badge variant={access.variant}>{access.label}</Badge></div><p className="mt-4 text-sm font-semibold text-supporting">{demoPatient.diagnosis} · {demoPatient.stage}</p></div><div className="min-w-0"><p className={labelClassName}>Referral</p><p className="mt-1 text-sm font-semibold text-supporting">Dr. Kavya Menon · Medical Oncology</p><p className="mt-1 text-xs text-metadata">Meeting: 29 Aug 2026 · 08:00 · Invitation accepted</p><p className="mt-4 text-xs font-medium text-metadata">Readiness</p><p className="mt-1 text-sm font-semibold text-supporting">Shared evidence available · imaging review pending</p></div><div className="min-w-0 rounded-lg border border-brand/25 bg-brand-soft p-4"><p className={labelClassName}>MDT question</p><p className="mt-1 text-sm font-semibold leading-6 text-supporting">Confirm sequencing of adjuvant systemic therapy and radiotherapy planning.</p><p className="mt-3 text-xs text-metadata">Access expires 31 Aug 2026 · 18:00 IST</p></div></div><div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-divider pt-5"><p className="text-xs text-metadata">Next action: review shared evidence and prepare specialist opinion</p><Button type="button" size="sm" disabled={!access.active} onClick={openCase}>Open Assigned Case<ArrowRight/></Button></div></article></CardContent></Card>
  </PageContainer>
}

const briefSections = [
  ['Clinical summary','Post-lumpectomy Stage IIA hormone-receptor-positive, HER2-negative breast cancer. ECOG 1; adjuvant AC chemotherapy underway with treatment-associated cytopenia requiring internal clinician review.'],
  ['Shared pathology summary','Invasive ductal carcinoma, grade 2; ER 90%, PR 70%, HER2 IHC 1+; clear margins and sentinel nodes 0/3.'],
  ['Shared imaging summary','Staging CT reports no distant metastatic disease. Post-operative left breast and axillary changes are described.'],
  ['Relevant treatment history','Left breast lumpectomy and sentinel-node biopsy in June 2026; adjuvant AC cycle 2 in progress; no radiotherapy delivered.'],
]

export function ExternalMdtSpecialistWorkspace() {
  const { selectedPatient, performAction } = useDemoAccess()
  const access = accessState()
  const [opinion,setOpinion] = React.useState('')
  const [rationale,setRationale] = React.useState('')
  const [evidence,setEvidence] = React.useState('')
  const [concerns,setConcerns] = React.useState('')
  const [information,setInformation] = React.useState('')
  const [certainty,setCertainty] = React.useState('Moderate')
  const [opinionStatus,setOpinionStatus] = React.useState<'Draft'|'Submitted'|'Signed'|'Superseded'>('Draft')
  const [requestCategory,setRequestCategory] = React.useState('Additional imaging')
  const [requestText,setRequestText] = React.useState('')
  const [requestStatus,setRequestStatus] = React.useState('Not requested')
  const readOnly = !access.active || opinionStatus === 'Signed' || opinionStatus === 'Submitted'

  const submitOpinion = () => {
    if (!opinion.trim() || !rationale.trim()) return
    setOpinionStatus('Submitted')
    performAction('comment-mdt','External specialist opinion submitted','MDT / Tumour Board',{destination:'MDT / Tumour Board',status:'Submitted',owner:'External MDT Specialist',nextAction:'Internal MDT clinician review'})
  }
  const signOpinion = () => {
    if (opinionStatus !== 'Submitted') return
    setOpinionStatus('Signed')
    performAction('comment-mdt','External specialist opinion signed','MDT / Tumour Board',{destination:'MDT / Tumour Board',status:'Signed',owner:'External MDT Specialist',nextAction:'Await internal MDT decision'})
  }

  if (!access.active) return <PageContainer><PageHeader title="Assigned MDT case" description="Case access is unavailable"/><Card><CardContent className="flex min-h-64 flex-col items-center justify-center p-6 text-center"><LockKeyhole className="size-8 text-critical-strong"/><p className="mt-4 font-semibold">Assigned-case access has expired</p><p className="mt-2 text-sm text-metadata">The MDT case and its NEXUS context are unavailable until access is renewed by an authorized internal user.</p></CardContent></Card></PageContainer>

  return <PageContainer>
    <PageHeader title="Assigned MDT case" description="Review shared evidence and contribute an attributable specialist opinion" actions={<Badge variant={access.variant}>{access.label} until 31 Aug 2026</Badge>}/>
    <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-4"><div><p className={labelClassName}>Case identity</p><p className="mt-1 font-semibold">MDT-DEMO-118 · Limited identity</p><p className="text-xs text-metadata">{selectedPatient.age} years · {selectedPatient.sex}</p></div><div><p className={labelClassName}>Diagnosis / stage</p><p className="mt-1 text-sm font-semibold text-supporting">{selectedPatient.diagnosis} · {selectedPatient.stage}</p></div><div><p className={labelClassName}>Referring clinician</p><p className="mt-1 text-sm font-semibold text-supporting">Dr. Kavya Menon · Medical Oncology</p></div><div><Badge variant="information"><ShieldCheck/>Shared for MDT review</Badge><p className="mt-2 text-xs text-metadata">Only evidence included in this assigned case is visible.</p></div></CardContent></Card>

    <Card className="mb-6" variant="gradient"><CardHeader className="border-b border-divider"><CardTitle>Question for MDT</CardTitle></CardHeader><CardContent className="pt-6"><p className="text-lg font-semibold leading-8 text-supporting">Confirm multidisciplinary consensus on sequencing of adjuvant systemic therapy and radiotherapy planning, considering toxicity, pathology risk features, and staging imaging.</p></CardContent></Card>

    <div className="mb-6 grid gap-4 sm:grid-cols-2">{briefSections.map(([title,text])=><Card key={title}><CardHeader className="border-b border-divider"><CardTitle className="text-base">{title}</CardTitle></CardHeader><CardContent className="pt-5"><p className="text-sm leading-6 text-supporting">{text}</p></CardContent></Card>)}</div>

    <Card className="mb-6"><CardHeader className="border-b border-divider"><CardTitle>Key evidence and open issues</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 lg:grid-cols-2"><div><h3 className="text-sm font-semibold text-supporting">Supporting evidence</h3><ul className="mt-3 space-y-2 text-sm text-supporting"><li>• Clear surgical margins and node-negative pathology</li><li>• Hormone-receptor-positive, HER2-negative biology</li><li>• Staging CT without reported distant disease</li></ul></div><div><h3 className="text-sm font-semibold text-supporting">Open issues</h3><ul className="mt-3 space-y-2 text-sm text-warning-strong"><li>• Current ANC requires internal clinician interpretation</li><li>• Final imaging comparison requires confirmation</li><li>• Timing depends on treatment tolerance and recovery</li></ul></div></CardContent></Card>

    <Card className="mb-6"><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>External Specialist Opinion</CardTitle><CardDescription className="mt-1">Clinician-entered and separately attributable contribution</CardDescription></div><Badge variant={opinionStatus==='Signed'?'success':opinionStatus==='Submitted'?'information':'neutral'}>{opinionStatus}</Badge></div></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2"><label className={`${labelClassName} sm:col-span-2`}>Recommendation / opinion<Input className="mt-1" value={opinion} disabled={readOnly} onChange={(event)=>setOpinion(event.target.value)} placeholder="Enter your specialist opinion"/></label><label className={`${labelClassName} sm:col-span-2`}>Rationale<Input className="mt-1" value={rationale} disabled={readOnly} onChange={(event)=>setRationale(event.target.value)} placeholder="Document clinical rationale"/></label><label className={labelClassName}>Supporting evidence<Input className="mt-1" value={evidence} disabled={readOnly} onChange={(event)=>setEvidence(event.target.value)} placeholder="Evidence supporting this opinion"/></label><label className={labelClassName}>Concerns / contradictions<Input className="mt-1" value={concerns} disabled={readOnly} onChange={(event)=>setConcerns(event.target.value)} placeholder="Document concerns or contradictions"/></label><label className={labelClassName}>Information still required<Input className="mt-1" value={information} disabled={readOnly} onChange={(event)=>setInformation(event.target.value)} placeholder="Outstanding information"/></label><label className={labelClassName}>Level of certainty<select className={`${selectClassName} mt-1`} value={certainty} disabled={readOnly} onChange={(event)=>setCertainty(event.target.value)}><option>High</option><option>Moderate</option><option>Low</option></select></label><div className="flex flex-wrap justify-end gap-2 sm:col-span-2"><Button type="button" variant="secondary" disabled={readOnly}>Save Draft</Button><Button type="button" disabled={readOnly||!opinion.trim()||!rationale.trim()} onClick={submitOpinion}>Submit Opinion</Button><Button type="button" variant="outline" disabled={opinionStatus!=='Submitted'} onClick={signOpinion}><CheckCircle2/>Acknowledge / Sign Own Opinion</Button></div><p className="rounded-lg border border-warning/25 bg-warning-subtle p-3 text-xs leading-5 text-warning-strong sm:col-span-2">This opinion does not replace the final MDT recommendation. Authorized internal clinicians retain decision and sign-off ownership.</p></CardContent></Card>

    <Card className="mb-6"><CardHeader className="border-b border-divider"><CardTitle>Request more information</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2"><label className={labelClassName}>Category<select className={`${selectClassName} mt-1`} value={requestCategory} onChange={(event)=>setRequestCategory(event.target.value)}><option>Additional imaging</option><option>Pathology clarification</option><option>Molecular result</option><option>Staging clarification</option><option>Treatment history</option><option>Clinical update</option><option>Other</option></select></label><label className={labelClassName}>Request<Input className="mt-1" value={requestText} onChange={(event)=>setRequestText(event.target.value)} placeholder="Describe the information required"/></label><div className="flex flex-wrap items-center justify-between gap-3 sm:col-span-2"><Badge variant={requestStatus==='Response available'?'success':requestStatus==='Requested'?'warning':'neutral'}>{requestStatus}</Badge><div className="flex gap-2"><Button type="button" variant="outline" size="sm" disabled={!requestText.trim()} onClick={()=>setRequestStatus('Requested')}>Request more information</Button><Button type="button" variant="ghost" size="sm" disabled={requestStatus!=='Requested'} onClick={()=>setRequestStatus('Response available')}>Mark response available</Button></div></div></CardContent></Card>

    <Card><CardHeader className="border-b border-divider"><CardTitle>Final MDT decision</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-3"><div><p className={labelClassName}>Decision</p><p className="mt-1 text-sm font-semibold text-supporting">Pending internal clinician-led MDT</p></div><div><p className={labelClassName}>Decision owner</p><p className="mt-1 text-sm font-semibold text-supporting">Authorized internal MDT clinicians</p></div><div><p className={labelClassName}>Specialist contribution</p><p className="mt-1 flex items-center gap-2 text-sm font-semibold text-supporting"><Clock3 className="size-4"/>{opinionStatus}</p></div></CardContent></Card>
  </PageContainer>
}
