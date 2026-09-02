'use client'

import * as React from 'react'
import Link from 'next/link'
import { ArrowLeft, CheckCircle2, CircleStop, FileAudio, Mic, Pause, Pencil, Play, Save, ShieldCheck, Sparkles, UserRound, XCircle } from 'lucide-react'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { ScribeStatus, useDemoAccess } from '@/components/demo-access-provider'
import { AiBadge } from '@/components/ui/ai-badge'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'

type NoteKey = 'subjective' | 'objective' | 'assessment' | 'plan'
const initialNote: Record<NoteKey, string> = {
  subjective: 'Reports increased fatigue and mild nausea after cycle 2. Appetite reduced for two days but tolerating oral fluids. Denies fever, chills, dyspnoea, chest pain, bleeding, or persistent vomiting.',
  objective: 'ECOG 1. Temperature 37.2 °C, pulse 88 bpm, BP 118/76 mmHg, SpO₂ 98% on room air. Alert and oriented. Mild pallor; no clinical dehydration.',
  assessment: 'Treatment-related grade 1 fatigue and nausea during adjuvant AC chemotherapy. No current features of febrile neutropenia or acute toxicity requiring admission.',
  plan: 'Continue current treatment subject to laboratory review. Optimise prescribed antiemetics, encourage oral hydration and small frequent meals, reinforce neutropenic precautions, and review before cycle 3.',
}
const transcript = [
  ['10:31:04', 'Clinician', 'How have you felt since the second chemotherapy cycle?'],
  ['10:31:12', 'Patient', 'Mostly tired, and a little nauseated. I have been drinking, but eating less for two days.'],
  ['10:31:29', 'Clinician', 'Any fever, chills, breathing difficulty, chest pain, bleeding, or vomiting that will not settle?'],
  ['10:31:38', 'Patient', 'No fever or chills. No breathing problem or bleeding. I have not vomited.'],
  ['10:32:02', 'Clinician', "Your observations are stable. We will review today's blood results, adjust the nausea medicines, and see you before cycle three."],
]
const noteSections: Array<[NoteKey, string, string]> = [
  ['subjective', 'Subjective', 'Chief concern, history and patient-reported symptoms'],
  ['objective', 'Objective', 'Examination, observations and relevant clinical findings'],
  ['assessment', 'Assessment', 'Clinician-reviewed assessment'],
  ['plan', 'Plan', 'Treatment, investigations and follow-up'],
]

export default function OpdScribePage() {
  const { selectedPatient, workflow, setScribeStatus } = useDemoAccess()
  const [recording, setRecording] = React.useState(false)
  const [note, setNote] = React.useState(initialNote)
  const [editing, setEditing] = React.useState(false)
  const status = workflow.scribeStatus
  const setStatus = (next: ScribeStatus) => setScribeStatus(next)
  const update = (key: NoteKey, value: string) => { setNote((current) => ({ ...current, [key]: value })); setStatus('Edited by Medical Oncologist — Not Signed') }
  const edit = () => { setEditing(true); if (status !== 'Signed') setStatus('Edited by Medical Oncologist — Not Signed') }
  const saveDraft = () => { setEditing(false); setStatus('Edited by Medical Oncologist — Not Signed') }
  const accept = () => { setEditing(false); setStatus('Clinician Accepted') }
  const reject = () => { setEditing(false); setRecording(false); setStatus('Rejected') }
  const sign = () => { if (status === 'Clinician Accepted') setStatus('Signed') }
  const resetDraft = () => { setNote(initialNote); setEditing(false); setStatus('AI Draft — Not Signed') }
  const statusVariant = status === 'Signed' ? 'success' : status === 'Rejected' ? 'critical' : status === 'Clinician Accepted' ? 'information' : 'warning'
  const progressIndex = status === 'Signed' ? 3 : status === 'Clinician Accepted' ? 2 : status.startsWith('Edited') ? 1 : 0

  return <PageContainer className="max-w-[1440px]">
    <PageHeader title="Consultation Documentation" description="Review and complete the clinician-owned documentation for this fictional encounter." actions={<Badge variant={statusVariant}>{status === 'Signed' ? <CheckCircle2 /> : <Sparkles />}{status}</Badge>} />

    <div className="mb-5 flex flex-wrap items-center gap-2"><Link href="/doctor-opd#patient-summary" className={buttonVariants({ variant: 'ghost', size: 'sm' })}><ArrowLeft />Back to Patient Summary</Link><Link href="/doctor-opd" className={buttonVariants({ variant: 'ghost', size: 'sm' })}><ArrowLeft />Back to Doctor OPD</Link><span className="ml-auto hidden text-xs text-metadata sm:block">Selected patient remains active</span></div>

    <Card className="aivana-accent-line mb-5 shadow-soft-sm"><CardContent className="flex flex-col gap-4 p-4 pl-5 sm:flex-row sm:items-center sm:px-5 sm:pl-6"><span className="aivana-gradient-soft flex size-10 shrink-0 items-center justify-center rounded-full text-supporting"><UserRound className="size-5" /></span><div className="min-w-0 flex-1"><p className="font-display font-semibold text-supporting">{selectedPatient.name}</p><p className="mt-0.5 text-xs text-metadata">MRN {selectedPatient.mrn} · {selectedPatient.age} years · {selectedPatient.sex}</p></div><div className="grid gap-1 text-sm sm:text-right"><p className="font-medium text-supporting">{selectedPatient.stage} breast cancer</p><p className="text-xs text-metadata">{selectedPatient.treatment} · {selectedPatient.treatmentPoint}</p></div><div className="hidden h-9 w-px bg-divider lg:block"/><div className="sm:text-right"><p className="text-[10px] font-semibold uppercase tracking-wider text-metadata">Responsible clinician</p><p className="mt-1 text-sm font-medium text-supporting">Medical Oncologist</p></div></CardContent></Card>

    <div className="mb-5 rounded-lg border border-divider bg-surface px-4 py-3"><div className="flex flex-col gap-3 lg:flex-row lg:items-center"><div className="flex items-start gap-2 lg:w-64"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-information-strong"/><div><p className="text-xs font-semibold text-supporting">AI-generated draft</p><p className="mt-0.5 text-xs text-metadata">Requires clinician review and approval</p></div></div><ol className="flex min-w-0 flex-1 items-center">{['AI Draft','Edited','Accepted','Signed'].map((label,index)=><li key={label} className="flex min-w-0 flex-1 items-center"><span className={cn('flex size-6 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold',index<=progressIndex?'border-primary bg-primary text-primary-foreground':'border-border bg-surface text-metadata')}>{index<progressIndex?<CheckCircle2 className="size-3.5"/>:index+1}</span><span className={cn('ml-2 truncate text-xs font-medium',index===progressIndex?'text-supporting':'text-metadata')}>{label}</span>{index<3?<span className={cn('mx-2 h-px min-w-3 flex-1',index<progressIndex?'bg-primary':'bg-border')}/>:null}</li>)}</ol></div></div>

    {status === 'Signed' ? <div role="status" className="mb-5 flex gap-3 rounded-lg bg-success-subtle px-4 py-3 text-success-strong"><CheckCircle2 className="mt-0.5 size-5 shrink-0"/><div><p className="text-sm font-semibold">Scribe note signed and complete</p><p className="mt-0.5 text-xs">Demo state only — nothing was legally signed, saved, published, or transmitted.</p></div></div> : null}
    {status === 'Rejected' ? <div role="status" className="mb-5 flex flex-wrap items-center gap-3 rounded-lg bg-critical-subtle px-4 py-3 text-critical-strong"><XCircle className="size-5 shrink-0"/><div className="min-w-0 flex-1"><p className="text-sm font-semibold">AI draft rejected</p><p className="mt-0.5 text-xs">Return to the supporting source or regenerate the fictional draft.</p></div><Button type="button" size="sm" variant="outline" onClick={resetDraft}>Regenerate Draft</Button></div> : null}

    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(300px,1fr)]">
      <div className="space-y-5">
        <Card variant="ai" className="aivana-accent-line overflow-hidden"><CardHeader className="border-b border-ai-highlight bg-surface pb-4 pl-6"><div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div><CardTitle>Clinical note</CardTitle><CardDescription className="mt-1">Primary documentation workspace · clinician review required</CardDescription></div><div className="flex flex-wrap items-center gap-2"><AiBadge><Sparkles />AI-generated</AiBadge>{editing ? <Badge variant="information"><Pencil/>Editing</Badge> : <Badge variant={statusVariant}>{status}</Badge>}</div></div></CardHeader><CardContent className="space-y-6 p-5 pl-6 sm:p-6 sm:pl-7">{noteSections.map(([key,label,description])=><section key={key} className="border-b border-divider pb-6 last:border-0 last:pb-0"><div className="mb-2 flex flex-wrap items-end justify-between gap-2"><div><label htmlFor={key} className="text-sm font-semibold text-supporting">{label}</label><p className="mt-0.5 text-xs text-metadata">{description}</p></div><span className="text-[10px] font-semibold uppercase tracking-wider text-metadata">{editing?'Clinician editing':status==='Signed'?'Clinician approved':status==='Clinician Accepted'?'Clinician accepted':status.startsWith('Edited')?'Clinician edited':'AI draft'}</span></div><textarea id={key} rows={key==='plan'?6:5} value={note[key]} readOnly={!editing||status==='Signed'} onChange={(event)=>update(key,event.target.value)} className={cn('flex h-auto w-full resize-y rounded-md border px-3 py-3 text-sm leading-6 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',editing?'border-emphasized bg-input-background shadow-neu-inset':'border-transparent bg-surface-app')} /></section>)}</CardContent></Card>

        <Card className="shadow-soft"><CardContent className="p-4"><div className="flex flex-col gap-4 lg:flex-row lg:items-center"><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-supporting">Clinician review</p><p className="mt-0.5 text-xs text-metadata">Review, edit if needed, accept the final draft, then sign the demonstration note.</p></div><div className="flex flex-wrap items-center gap-2"><Button type="button" variant="outline" onClick={edit} disabled={status==='Signed'}><Pencil/>Edit</Button>{editing?<Button type="button" onClick={saveDraft}><Save/>Save Draft</Button>:null}<Button type="button" onClick={accept} disabled={editing||status==='Signed'||status==='Rejected'}><CheckCircle2/>Accept</Button><Button type="button" className="bg-success text-white hover:bg-success/90" onClick={sign} disabled={status!=='Clinician Accepted'}><ShieldCheck/>Sign &amp; Complete</Button><Button type="button" variant="ghost" className="text-critical-strong hover:bg-critical-subtle hover:text-critical-strong" onClick={reject} disabled={status==='Signed'}><XCircle/>Reject</Button></div></div>{status==='Clinician Accepted'?<p className="mt-3 border-t border-divider pt-3 text-xs text-warning-strong">Signing confirms the Medical Oncologist's approval of this fictional clinical documentation. This is not a legal electronic signature.</p>:null}</CardContent></Card>
      </div>

      <aside className="space-y-5 xl:sticky xl:top-6">
        <Card><CardHeader className="border-b border-divider pb-4"><div className="flex items-start justify-between gap-3"><div><CardTitle>Source recording</CardTitle><CardDescription className="mt-1">Supporting demonstration source</CardDescription></div><Badge variant={recording?'critical':'neutral'}>{recording?'Demo recording':'Stopped'}</Badge></div></CardHeader><CardContent className="p-5"><div className="flex items-center gap-4"><button type="button" onClick={()=>setRecording(!recording)} className={cn('flex size-12 shrink-0 items-center justify-center rounded-full text-primary-foreground shadow-neu focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',recording?'bg-critical':'bg-primary')} aria-label={recording?'Stop demo recording':'Start demo recording'}>{recording?<CircleStop className="size-5"/>:<Mic className="size-5"/>}</button><div className="min-w-0"><p className="text-sm font-semibold text-supporting">{recording?'Demo recording active':'No recording active'}</p><p className="mt-0.5 text-xs text-metadata">{recording?'00:02:41 · no audio captured':'No microphone access or audio storage'}</p></div></div><div className="mt-4 flex gap-2"><Button type="button" variant="outline" size="sm" disabled={!recording}><Pause/>Pause</Button><Button type="button" variant="ghost" size="sm"><Play/>Play demo</Button></div></CardContent></Card>

        <Card><CardHeader className="border-b border-divider pb-4"><div className="flex items-start justify-between gap-3"><div><CardTitle>Transcript</CardTitle><CardDescription className="mt-1">Supporting source · not the clinical record</CardDescription></div><FileAudio className="size-4 text-metadata"/></div></CardHeader><CardContent className="divide-y divide-divider p-0">{transcript.map(([time,speaker,text])=><div key={time} className="px-4 py-3"><div className="flex items-center justify-between gap-3"><span className={cn('text-xs font-semibold',speaker==='Clinician'?'text-information-strong':'text-supporting')}>{speaker}</span><span className="text-[10px] text-metadata">{time}</span></div><p className="mt-1.5 text-xs leading-5 text-supporting">{text}</p></div>)}</CardContent></Card>

        <div className="flex items-start gap-2 px-1 text-xs leading-5 text-metadata"><ShieldCheck className="mt-0.5 size-4 shrink-0"/><p>AI is assistive. The Medical Oncologist remains responsible for review, editing, acceptance, and sign-off. No autonomous diagnosis, staging, or treatment selection occurs.</p></div>
      </aside>
    </div>
  </PageContainer>
}
