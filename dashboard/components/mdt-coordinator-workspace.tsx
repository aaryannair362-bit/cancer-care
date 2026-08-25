'use client'

import * as React from 'react'
import { AlertTriangle, CalendarClock, ClipboardList, Users } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

const selectClassName = 'h-10 w-full min-w-0 rounded-xl border border-input bg-input-background px-3 text-sm text-supporting shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
const labelClassName = 'text-xs font-medium text-metadata'

const readinessItems = [
  ['Clinical summary','Available'],['Pathology','Available'],['Histology','Available'],['Biomarkers / molecular results','Needs review'],
  ['Imaging','Needs review'],['Staging','Available'],['Treatment history','Available'],['Previous reports','Available'],
  ['NEXUS summary','Available'],['Operative notes','Not required'],
]

const participantRoles = ['Medical Oncologist','Surgical Oncologist','Radiation Oncologist','Radiologist','Pathologist / Molecular Diagnostics','External MDT Specialist']

const followUpActions = [
  ['Additional pathology review','Pathology','Pending','28 Aug 2026'],
  ['Confirm imaging comparison','Radiology','In progress','28 Aug 2026'],
  ['Update clinician-authored treatment plan','Medical Oncology','Pending','30 Aug 2026'],
  ['Arrange oncology follow-up','Nurse Navigator','Pending','30 Aug 2026'],
]

export function MdtCoordinatorWorkspace() {
  const { selectedPatient, performAction } = useDemoAccess()
  const [readiness, setReadiness] = React.useState('Awaiting information')
  const [boardDate, setBoardDate] = React.useState('2026-08-29')
  const [meetingType, setMeetingType] = React.useState('Hybrid')
  const [invites, setInvites] = React.useState<Record<string,string>>({})
  const [attendance, setAttendance] = React.useState<Record<string,string>>({})
  const [discussion, setDiscussion] = React.useState('Awaiting MDT')
  const [decisionStatus, setDecisionStatus] = React.useState('Decision pending')
  const [scheduleNotice, setScheduleNotice] = React.useState('')

  const schedule = () => {
    setReadiness('Scheduled')
    setScheduleNotice('Board schedule recorded')
    performAction('schedule-board','MDT board scheduled','MDT / Tumour Board',{destination:'MDT / Tumour Board',status:'Scheduled',owner:'MDT Coordinator',nextAction:'Confirm participants and agenda'})
  }

  return <PageContainer>
    <PageHeader title="MDT / Tumour Board" description="Coordinate case readiness, scheduling, participation, and follow-up" />

    <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-5">
      <div className="sm:col-span-2"><p className={labelClassName}>Patient / case</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn} · MDT-DEMO-118 · {selectedPatient.age} years · {selectedPatient.sex}</p></div>
      <div><p className={labelClassName}>Diagnosis / stage</p><p className="mt-1 text-sm font-semibold text-supporting">{selectedPatient.diagnosis} · {selectedPatient.stage}</p></div>
      <div><p className={labelClassName}>Referral</p><p className="mt-1 text-sm font-semibold text-supporting">Medical Oncology · Dr. Kavya Menon</p><p className="text-xs text-metadata">24 Aug 2026 · Urgent</p></div>
      <div><p className={labelClassName}>MDT status</p><Badge className="mt-1" variant="warning">{readiness}</Badge></div>
    </CardContent></Card>

    <Card className="mb-6" variant="gradient"><CardHeader className="border-b border-divider"><CardTitle>What decision does the MDT need to make for this patient?</CardTitle></CardHeader><CardContent className="pt-6"><p className="text-base font-semibold leading-7 text-supporting">Confirm multidisciplinary consensus on sequencing of adjuvant systemic therapy and radiotherapy planning, considering treatment toxicity, pathology risk features, and staging imaging.</p><div className="mt-4 grid gap-3 text-xs sm:grid-cols-3"><p><span className="text-metadata">Referring department</span><br/><strong>Medical Oncology</strong></p><p><span className="text-metadata">Proposed board</span><br/><strong>29 Aug 2026 · 08:00</strong></p><p><span className="text-metadata">Urgency</span><br/><strong className="text-warning-strong">Urgent</strong></p></div></CardContent></Card>

    <div className="mb-6 grid gap-6 xl:grid-cols-3">
      <Card className="xl:col-span-2"><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>Case readiness</CardTitle><CardDescription className="mt-1">Required material for multidisciplinary review</CardDescription></div><Badge variant="warning">2 need review</Badge></div></CardHeader><CardContent className="grid gap-3 pt-6 sm:grid-cols-2">{readinessItems.map(([item,status])=><div key={item} className="flex min-w-0 items-center justify-between gap-3 rounded-lg border border-divider bg-surface-elevated/70 p-3"><p className="min-w-0 text-sm font-semibold text-supporting">{item}</p><Badge variant={status==='Available'?'success':status==='Needs review'?'warning':'neutral'}>{status}</Badge></div>)}<p className="flex items-center gap-2 rounded-lg border border-warning/25 bg-warning-subtle p-3 text-sm font-semibold text-warning-strong sm:col-span-2"><AlertTriangle className="size-4 shrink-0"/>Missing for MDT: final molecular result confirmation and imaging review.</p></CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Operational readiness</CardTitle></CardHeader><CardContent className="space-y-4 pt-6"><label className={labelClassName}>Readiness status<select className={`${selectClassName} mt-1`} value={readiness} onChange={(event)=>setReadiness(event.target.value)}><option>Incomplete</option><option>Awaiting information</option><option>Ready for scheduling</option><option>Scheduled</option><option>Ready for MDT</option></select></label><Button type="button" variant="outline" className="w-full" onClick={()=>performAction('assemble-packet','MDT packet assembled','MDT / Tumour Board',{destination:'MDT / Tumour Board',status:'Ready for review'})}><ClipboardList/>Record packet readiness</Button><p className="text-xs leading-5 text-metadata">Operational readiness only. Diagnosis and clinical treatment decisions cannot be modified here.</p></CardContent></Card>
    </div>

    <Card className="mb-6"><CardHeader className="border-b border-divider"><CardTitle>Board scheduling</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2 lg:grid-cols-4"><label className={labelClassName}>Board date<Input className="mt-1" type="date" value={boardDate} onChange={(event)=>setBoardDate(event.target.value)}/></label><label className={labelClassName}>Start time<Input className="mt-1" type="time" defaultValue="08:00"/></label><label className={labelClassName}>Meeting type<select className={`${selectClassName} mt-1`} value={meetingType} onChange={(event)=>setMeetingType(event.target.value)}><option>In-person</option><option>Virtual</option><option>Hybrid</option></select></label><label className={labelClassName}>Room / location<Input className="mt-1" defaultValue="MDT Room 2"/></label><label className={`${labelClassName} sm:col-span-2`}>Meeting link<Input className="mt-1" placeholder="Optional virtual meeting link"/></label><label className={labelClassName}>Agenda position<Input className="mt-1" defaultValue="Case 3"/></label><label className={labelClassName}>Expected duration<Input className="mt-1" defaultValue="15 minutes"/></label><div className="flex flex-wrap items-center justify-end gap-2 sm:col-span-2 lg:col-span-4"><Button type="button" variant="outline" size="sm" onClick={()=>setScheduleNotice('Case removed from board')}>Remove from board</Button><Button type="button" variant="secondary" size="sm" onClick={schedule}>Reschedule</Button><Button type="button" size="sm" onClick={schedule}><CalendarClock/>Schedule</Button>{scheduleNotice?<Badge variant="success">{scheduleNotice}</Badge>:null}</div></CardContent></Card>

    <Card className="mb-6"><CardHeader className="border-b border-divider"><div className="flex items-center justify-between gap-3"><div><CardTitle>Participants</CardTitle><CardDescription className="mt-1">Invitation and attendance tracking</CardDescription></div><Badge variant="brand"><Users/>6 roles</Badge></div></CardHeader><CardContent className="grid gap-3 pt-6 md:grid-cols-2">{participantRoles.map((participant)=><div key={participant} className="grid min-w-0 gap-3 rounded-lg border border-divider bg-surface-elevated/70 p-3 sm:grid-cols-[minmax(0,1fr)_140px_140px] sm:items-center"><p className="min-w-0 text-sm font-semibold text-supporting">{participant}</p><select aria-label={`${participant} invitation`} className={selectClassName} value={invites[participant]??'Not invited'} onChange={(event)=>setInvites((current)=>({...current,[participant]:event.target.value}))}><option>Not invited</option><option>Invited</option><option>Accepted</option><option>Declined</option><option>Pending</option></select><select aria-label={`${participant} attendance`} className={selectClassName} value={attendance[participant]??'Absent'} onChange={(event)=>setAttendance((current)=>({...current,[participant]:event.target.value}))}><option>Present</option><option>Absent</option><option>Joined remotely</option></select></div>)}</CardContent></Card>

    <div className="mb-6 grid gap-6 xl:grid-cols-2">
      <Card><CardHeader className="border-b border-divider"><CardTitle>MDT agenda</CardTitle></CardHeader><CardContent className="pt-6"><div className="grid gap-3 rounded-lg border border-brand/20 bg-brand-soft p-4 sm:grid-cols-[auto_minmax(0,1fr)_auto]"><span className="flex size-8 items-center justify-center rounded-lg bg-surface font-semibold">3</span><div className="min-w-0"><p className="font-semibold text-supporting">{selectedPatient.name} · {selectedPatient.diagnosis}</p><p className="mt-1 text-xs leading-5 text-metadata">Sequencing of adjuvant systemic therapy and radiotherapy · Urgent · 15 minutes</p></div><Badge variant="warning">Upcoming</Badge></div><div className="mt-4 flex flex-wrap gap-2"><Button type="button" size="sm" variant="outline">Move up</Button><Button type="button" size="sm" variant="outline">Move down</Button><Button type="button" size="sm" variant="secondary" onClick={()=>setDiscussion('In discussion')}>Open case</Button></div></CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><CardTitle>During MDT</CardTitle><Badge variant={discussion==='Discussed'?'success':'information'}>{discussion}</Badge></div></CardHeader><CardContent className="pt-6"><label className={labelClassName}>Operational discussion state<select className={`${selectClassName} mt-1`} value={discussion} onChange={(event)=>setDiscussion(event.target.value)}><option>Awaiting MDT</option><option>Case opened</option><option>In discussion</option><option>Discussed</option><option>Decision awaiting sign-off</option><option>Case deferred</option><option>Additional information required</option><option>Repeat MDT required</option></select></label><p className="mt-4 rounded-lg border border-information/25 bg-information-subtle p-3 text-xs leading-5 text-information-strong">The coordinator records meeting progress only and cannot author the clinical recommendation.</p></CardContent></Card>
    </div>

    <Card className="mb-6"><CardHeader className="border-b border-divider"><CardTitle>MDT decision tracking</CardTitle><CardDescription>Track ownership and sign-off without editing clinical content</CardDescription></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2 lg:grid-cols-4"><label className={labelClassName}>Decision status<select className={`${selectClassName} mt-1`} value={decisionStatus} onChange={(event)=>setDecisionStatus(event.target.value)}><option>Decision pending</option><option>Awaiting clinician sign-off</option><option>Signed</option><option>Action pending</option><option>Completed</option></select></label><label className={labelClassName}>Responsible clinician<Input className="mt-1" value="Dr. Kavya Menon" readOnly/></label><label className={labelClassName}>Pending approvers<Input className="mt-1" value="Medical Oncology · Radiation Oncology" readOnly/></label><label className={labelClassName}>Repeat MDT<Input className="mt-1" value="Not currently required" readOnly/></label><p className="rounded-lg border border-warning/25 bg-warning-subtle p-3 text-xs leading-5 text-warning-strong sm:col-span-2 lg:col-span-4">Signed clinical decision content is read-only. Final recommendation ownership remains with authorized MDT clinicians.</p></CardContent></Card>

    <Card><CardHeader className="border-b border-divider"><CardTitle>Follow-up actions</CardTitle></CardHeader><CardContent className="grid gap-3 pt-6 sm:grid-cols-2">{followUpActions.map(([action,owner,status,due])=><div key={action} className="rounded-lg border border-divider p-4"><div className="flex items-start justify-between gap-3"><p className="text-sm font-semibold text-supporting">{action}</p><Badge variant={status==='In progress'?'information':'neutral'}>{status}</Badge></div><div className="mt-3 grid grid-cols-2 gap-3 text-xs"><p><span className="text-metadata">Owner</span><br/><strong>{owner}</strong></p><p><span className="text-metadata">Due</span><br/><strong>{due}</strong></p></div></div>)}</CardContent></Card>
  </PageContainer>
}
