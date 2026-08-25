'use client'

import * as React from 'react'
import { AlertTriangle, CheckCircle2, ShieldAlert, Syringe } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

const selectClassName = 'h-10 w-full min-w-0 rounded-xl border border-input bg-input-background px-3 text-sm text-supporting shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
const fieldClassName = 'text-xs font-medium text-metadata'

const preMedications = [
  { medication: 'Ondansetron', dose: '8 mg', route: 'IV', time: '10:15' },
  { medication: 'Dexamethasone', dose: '12 mg', route: 'IV', time: '10:20' },
]

const treatmentDrugs = [
  { medication: 'Doxorubicin', dose: 'Approved plan dose', route: 'IV', verification: 'Two-nurse verification required' },
  { medication: 'Cyclophosphamide', dose: 'Approved plan dose', route: 'IV infusion', verification: 'Two-nurse verification required' },
]

export default function TreatmentDayPage() {
  const { selectedPatient, performAction } = useDemoAccess()
  const [clearance, setClearance] = React.useState<'Pending'|'Cleared'|'Needs review'|'Not cleared'|'Held'>('Needs review')
  const [treatmentStatus, setTreatmentStatus] = React.useState('Awaiting clearance')
  const [preMedStatus, setPreMedStatus] = React.useState<Record<string,string>>({})
  const [drugStatus, setDrugStatus] = React.useState<Record<string,string>>({})
  const [reaction, setReaction] = React.useState('No reaction documented')
  const [clinicianNotified, setClinicianNotified] = React.useState(false)
  const [completed, setCompleted] = React.useState(false)
  const progressionBlocked = clearance !== 'Cleared'

  const holdInfusion = () => { setTreatmentStatus('Held'); setReaction('Infusion held — reaction assessment required') }
  const completeTreatment = () => {
    if (progressionBlocked) return
    setTreatmentStatus('Treatment completed')
    setCompleted(true)
    performAction('create-follow-up','Treatment-day administration completed','Treatment Day / Infusion',{destination:'Oncology follow-up',status:'Completed',owner:'Medical Oncology',nextAction:'Review tolerance and prepare next cycle'})
  }

  return <PageContainer>
    <PageHeader title="Treatment Day / Infusion" description="Administer and document clinician-approved oncology day-care treatment" />

    <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-5">
      <div className="sm:col-span-2"><p className={fieldClassName}>Patient</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn} · {selectedPatient.age} years · {selectedPatient.sex}</p></div>
      <div><p className={fieldClassName}>Diagnosis / stage</p><p className="mt-1 text-sm font-semibold text-supporting">{selectedPatient.diagnosis} · {selectedPatient.stage}</p></div>
      <div><p className={fieldClassName}>Treatment</p><p className="mt-1 text-sm font-semibold text-supporting">{selectedPatient.treatment}</p><p className="text-xs text-metadata">{selectedPatient.treatmentPoint} · Curative intent</p></div>
      <div><p className={fieldClassName}>Major alert</p><p className="mt-1 flex items-start gap-2 text-sm font-semibold text-critical-strong"><ShieldAlert className="mt-0.5 size-4 shrink-0" />{selectedPatient.allergy}</p></div>
    </CardContent></Card>

    <div className="grid gap-6 xl:grid-cols-2">
      <Card><CardHeader className="border-b border-divider"><CardTitle>Treatment order</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">
        {[['Regimen','Adjuvant AC chemotherapy'],['Cycle / day',selectedPatient.treatmentPoint],['Planned drugs','Doxorubicin · Cyclophosphamide'],['Route','Intravenous'],['Schedule','Every 21 days'],['Treatment intent','Curative'],['Treating clinician','Dr. Kavya Menon · Medical Oncology'],['Order status','Clinician approved · view only']].map(([label,value])=><div key={label}><p className={fieldClassName}>{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}
        <p className="rounded-lg border border-information/25 bg-information-subtle p-3 text-xs leading-5 text-information-strong sm:col-span-2">Administration is documented against the approved order. Treatment-plan creation and dose alteration are not available in this workspace.</p>
      </CardContent></Card>

      <Card><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><CardTitle>Laboratory and clearance</CardTitle><Badge variant={clearance==='Cleared'?'success':'warning'}>{clearance}</Badge></div></CardHeader><CardContent className="pt-6">
        <div className="grid gap-3 sm:grid-cols-2">{[['ANC','0.7 ×10⁹/L','Low · clinician review'],['Platelets','138 ×10⁹/L','Acceptable range'],['Haemoglobin','9.4 g/dL','Low · review'],['Renal function','Creatinine 0.82 mg/dL','Available'],['Liver function','AST 28 · ALT 31 U/L','Available']].map(([name,result,status])=><div key={name} className="rounded-lg border border-divider bg-surface-elevated/70 p-3"><div className="flex flex-wrap justify-between gap-2"><p className="text-sm font-semibold text-supporting">{name}</p><Badge variant={status.includes('Low')?'warning':'success'}>{status}</Badge></div><p className="mt-2 text-sm text-supporting">{result}</p><p className="mt-1 text-xs text-metadata">24 Aug 2026 · 08:20</p></div>)}</div>
        <label className="mt-4 block text-xs font-medium text-metadata">Clinician-recorded clearance state<select className={`${selectClassName} mt-1`} value={clearance} onChange={(event)=>setClearance(event.target.value as typeof clearance)}><option>Pending</option><option>Cleared</option><option>Needs review</option><option>Not cleared</option><option>Held</option></select></label>
        {progressionBlocked?<p className="mt-3 flex items-center gap-2 rounded-lg border border-warning/25 bg-warning-subtle p-3 text-sm font-semibold text-warning-strong"><AlertTriangle className="size-4 shrink-0" />Clinician review required. Normal treatment progression is blocked.</p>:null}
      </CardContent></Card>

      <Card><CardHeader className="border-b border-divider"><CardTitle>Pre-treatment assessment</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2 lg:grid-cols-3">
        {[['Identity verification','Two identifiers confirmed'],['Temperature','36.8 °C'],['Blood pressure','118/76 mmHg'],['Pulse','82 bpm'],['Respiratory rate','16 /min'],['SpO₂','98%'],['Weight','61.4 kg'],['BSA','1.67 m²'],['Performance status','ECOG 1'],['Current symptoms','Mild fatigue'],['Toxicity symptoms','Grade 1 nausea'],['Hydration','Clinically adequate'],['Allergy / ADR review','Severe paclitaxel ADR acknowledged'],['Venous access','Peripheral IV · patent']].map(([label,value])=><label key={label} className={fieldClassName}>{label}<Input className="mt-1" defaultValue={value}/></label>)}
      </CardContent></Card>

      <Card><CardHeader className="border-b border-divider"><CardTitle>Pre-medication</CardTitle></CardHeader><CardContent className="space-y-3 pt-6">{preMedications.map((item)=><div key={item.medication} className="grid items-center gap-3 rounded-lg border border-divider bg-surface-elevated/70 p-3 sm:grid-cols-[minmax(0,1fr)_auto_auto]"><div className="min-w-0"><p className="font-semibold text-supporting">{item.medication} · {item.dose}</p><p className="text-xs text-metadata">{item.route} · planned {item.time}</p></div><select aria-label={`${item.medication} status`} className={selectClassName} value={preMedStatus[item.medication]??'Pending'} onChange={(event)=>setPreMedStatus((current)=>({...current,[item.medication]:event.target.value}))}><option>Pending</option><option>Given</option><option>Held</option><option>Not required</option></select><Badge variant={preMedStatus[item.medication]==='Given'?'success':'neutral'}>{preMedStatus[item.medication]??'Pending'}</Badge></div>)}</CardContent></Card>

      <Card className="xl:col-span-2"><CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><CardTitle>Treatment administration</CardTitle><Badge variant={treatmentStatus==='Infusion in progress'?'information':treatmentStatus==='Treatment completed'?'success':'warning'}>{treatmentStatus}</Badge></div></CardHeader><CardContent className="space-y-3 pt-6">{treatmentDrugs.map((drug)=><div key={drug.medication} className="grid min-w-0 items-center gap-3 rounded-lg border border-divider bg-surface-elevated/70 p-4 md:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,0.8fr))]"><div className="min-w-0"><p className="font-semibold text-supporting">{drug.medication}</p><p className="text-xs text-metadata">{drug.dose} · {drug.route}</p></div><Input aria-label={`${drug.medication} start time`} type="time"/><Input aria-label={`${drug.medication} end time`} type="time"/><select aria-label={`${drug.medication} administration status`} className={selectClassName} disabled={progressionBlocked} value={drugStatus[drug.medication]??'Pending'} onChange={(event)=>{setDrugStatus((current)=>({...current,[drug.medication]:event.target.value}));if(event.target.value==='Started')setTreatmentStatus('Infusion in progress')}}><option>Pending</option><option>Started</option><option>Paused</option><option>Completed</option><option>Held</option><option>Discontinued</option></select><p className="text-xs text-metadata md:col-span-4"><CheckCircle2 className="mr-1 inline size-3.5"/>{drug.verification}</p></div>)}</CardContent></Card>

      <Card><CardHeader className="border-b border-divider"><CardTitle>Infusion monitoring</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2"><label className={fieldClassName}>Observation time<Input className="mt-1" type="time" defaultValue="11:00"/></label><label className={fieldClassName}>Infusion status<select className={`${selectClassName} mt-1`} value={treatmentStatus} onChange={(event)=>setTreatmentStatus(event.target.value)}><option>Awaiting clearance</option><option>Ready for treatment</option><option>Infusion in progress</option><option>Paused</option><option>Held</option></select></label><label className={fieldClassName}>Vitals<Input className="mt-1" defaultValue="BP 120/78 · Pulse 84 · SpO₂ 98%"/></label><label className={fieldClassName}>Symptoms / tolerance<Input className="mt-1" defaultValue="No new symptoms · tolerating infusion"/></label><label className={`${fieldClassName} sm:col-span-2`}>Nursing notes<Input className="mt-1" placeholder="Document treatment-day observations"/></label></CardContent></Card>

      <Card><CardHeader className="border-b border-divider"><CardTitle>Adverse reaction / escalation</CardTitle></CardHeader><CardContent className="space-y-4 pt-6"><label className={fieldClassName}>Reaction documentation<Input className="mt-1" value={reaction} onChange={(event)=>setReaction(event.target.value)} placeholder="Reaction, symptoms, severity and onset"/></label><div className="grid gap-3 sm:grid-cols-2"><label className={fieldClassName}>Drug / treatment<Input className="mt-1" defaultValue="No active reaction"/></label><label className={fieldClassName}>Outcome / disposition<Input className="mt-1" placeholder="Document clinician-led outcome"/></label></div><div className="flex flex-wrap gap-2"><Button type="button" variant="destructive" size="sm" onClick={holdInfusion}>Hold Infusion</Button><Button type="button" variant="outline" size="sm" onClick={()=>setClinicianNotified(true)}>Notify Clinician</Button><Button type="button" variant="outline" size="sm" onClick={()=>{setClinicianNotified(true);setTreatmentStatus('Reaction / escalation')}}>Escalate</Button></div>{clinicianNotified?<Badge variant="warning">Clinician notified</Badge>:null}<p className="text-xs leading-5 text-metadata">Resume, modify, discontinue, or defer remains a clinician-led decision.</p></CardContent></Card>

      <Card className="xl:col-span-2"><CardHeader className="border-b border-divider"><CardTitle>Treatment completion</CardTitle></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2 lg:grid-cols-4"><label className={fieldClassName}>Treatment status<select className={`${selectClassName} mt-1`} value={treatmentStatus} onChange={(event)=>setTreatmentStatus(event.target.value)}><option>Completed</option><option>Partially completed</option><option>Held</option><option>Deferred</option><option>Discontinued</option></select></label><label className={fieldClassName}>Final vitals<Input className="mt-1" defaultValue="BP 118/76 · Pulse 80"/></label><label className={fieldClassName}>Treatment tolerance<Input className="mt-1" defaultValue="Tolerated without acute event"/></label><label className={fieldClassName}>Discharge condition<Input className="mt-1" defaultValue="Stable"/></label><label className={`${fieldClassName} sm:col-span-2`}>Advice / precautions<Input className="mt-1" defaultValue="Report fever, breathing difficulty, rash, or worsening symptoms"/></label><label className={fieldClassName}>Next appointment<Input className="mt-1" defaultValue="20 Sep 2026 · 10:30"/></label><label className={fieldClassName}>Labs / review before next cycle<Input className="mt-1" defaultValue="CBC, renal/liver function and clinician review"/></label><div className="flex flex-wrap items-center justify-end gap-3 sm:col-span-2 lg:col-span-4">{completed?<Badge variant="success"><CheckCircle2/>Treatment completed</Badge>:null}<Button type="button" onClick={completeTreatment} disabled={progressionBlocked}><Syringe/>Complete Treatment</Button></div></CardContent></Card>
    </div>
  </PageContainer>
}
