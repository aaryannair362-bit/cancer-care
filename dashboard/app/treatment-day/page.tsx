'use client'

import * as React from 'react'
import { AlertTriangle, CheckCircle2, ShieldAlert, Syringe } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { TreatmentStatusPill } from '@/components/oncology/status-pill'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { canAdministerTreatment } from '@/lib/demo-access'
import { useOncology } from '@/lib/oncology/store'
import { CTCAE_GENERIC_GRADES, CTCAE_RELATIONSHIP_TO_THERAPY, CTCAE_TERMS } from '@/lib/oncology/terminology'
import type { ActorRef, MARDrugAdministration } from '@/lib/oncology/types'

const selectClassName = 'h-10 w-full min-w-0 rounded-xl border border-input bg-input-background px-3 text-sm text-supporting shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
const fieldClassName = 'text-xs font-medium text-metadata'

export default function TreatmentDayPage() {
  const { role, selectedPatient, performAction } = useDemoAccess()
  const { getOrdersForPatient, transitionOrder, recordPreAdministrationChecklist, recordAdministration, updateAdministration, recordPostAdministration, recordToxicityEvent, state } = useOncology()
  const canAct = canAdministerTreatment(role)
  const actor: ActorRef = { userId: role.roleId, name: role.label, roleLabel: role.label }

  const order = getOrdersForPatient(selectedPatient.id)[0]

  const [checklist, setChecklist] = React.useState({
    twoPatientIdentifiersConfirmed: false, orderVerified: false, consentConfirmed: false, allergyVerified: false,
    preTreatmentVitalsRecorded: false, requiredLabsAvailable: false, venousAccessConfirmed: false, pharmacyPreparedMedicationConfirmed: false,
  })
  const [showToxicityForm, setShowToxicityForm] = React.useState(false)
  const [toxTerm, setToxTerm] = React.useState(CTCAE_TERMS[0].display)
  const [toxGrade, setToxGrade] = React.useState<1 | 2 | 3 | 4 | 5>(1)
  const [toxRelationship, setToxRelationship] = React.useState('possible')
  const [toxIntervention, setToxIntervention] = React.useState('')
  const [postVitals, setPostVitals] = React.useState('BP 118/76 · Pulse 80')
  const [dischargeInstructions, setDischargeInstructions] = React.useState('Report fever, breathing difficulty, rash, or worsening symptoms')
  const [nextCycleDate, setNextCycleDate] = React.useState('')

  if (!order) return <PageContainer><PageHeader title="Day Care / Infusion" description="Administer and document clinician-approved oncology day-care treatment" /><Card><CardContent className="p-6 text-sm text-metadata">No treatment order is on record for this patient.</CardContent></Card></PageContainer>

  const marEntries = state.marEntries.filter((m) => m.orderId === order.id)
  const dispensableLines = order.drugLines.filter((line) => !line.isPremedication && !line.isSupportive)
  const premedLines = order.drugLines.filter((line) => line.isPremedication)

  const allChecklistDone = Object.values(checklist).every(Boolean)

  const confirmChecklist = () => {
    recordPreAdministrationChecklist({ orderId: order.id, ...checklist, confirmedBy: actor })
    transitionOrder(order.id, 'ready_for_administration', actor)
  }

  const beginAdministration = () => {
    transitionOrder(order.id, 'in_progress', actor)
    dispensableLines.forEach((line, index) => {
      if (!marEntries.find((m) => m.drugLineId === line.id)) {
        recordAdministration({ orderId: order.id, drugLineId: line.id, sequence: index + 1, drug: line.genericDrugName, doseGiven: line.orderedDose, route: line.route, administeredBy: actor, infusionStatus: 'not_started' })
      }
    })
  }

  const getEntry = (drugLineId: string) => marEntries.find((m) => m.drugLineId === drugLineId)
  const allCompleted = dispensableLines.length > 0 && dispensableLines.every((line) => getEntry(line.id)?.infusionStatus === 'completed')

  const holdForReaction = () => {
    if (!showToxicityForm) { setShowToxicityForm(true); return }
    const term = CTCAE_TERMS.find((t) => t.display === toxTerm)
    recordToxicityEvent({
      patientId: order.patientId, orderId: order.id,
      term: term ? { code: term.code, system: term.system, display: term.display } : { code: 'local', system: 'local', display: toxTerm },
      grade: toxGrade, onset: new Date().toISOString(), relationshipToTherapy: toxRelationship as 'unrelated' | 'unlikely' | 'possible' | 'probable' | 'definite',
      intervention: toxIntervention, outcome: 'ongoing', recordedBy: actor,
    })
    transitionOrder(order.id, 'held', actor, `${toxTerm} (Grade ${toxGrade})`)
    setShowToxicityForm(false)
  }

  const completeTreatment = () => {
    recordPostAdministration({ orderId: order.id, completionStatus: 'completed', postTreatmentVitals: postVitals, dischargeInstructions, nextCycleDate: nextCycleDate || undefined, recordedBy: actor })
    transitionOrder(order.id, 'administered', actor)
    transitionOrder(order.id, 'completed', actor)
    performAction('create-follow-up', 'Treatment-day administration completed', 'Day Care / Infusion', { destination: 'Oncology follow-up', status: 'Completed', owner: 'Medical Oncology', nextAction: 'Review tolerance and prepare next cycle' })
  }

  return <PageContainer>
    <PageHeader title="Day Care / Infusion" description="Administer and document clinician-approved oncology day-care treatment" />

    <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-5">
      <div className="sm:col-span-2"><p className={fieldClassName}>Patient</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn} · {selectedPatient.age} years · {selectedPatient.sex}</p></div>
      <div><p className={fieldClassName}>Regimen / cycle</p><p className="mt-1 text-sm font-semibold text-supporting">{order.regimenName}</p><p className="text-xs text-metadata">Cycle {order.cycleNumber} · Day {order.day}</p></div>
      <div><p className={fieldClassName}>Order status</p><p className="mt-1"><TreatmentStatusPill status={order.status} /></p></div>
      <div><p className={fieldClassName}>Major alert</p><p className="mt-1 flex items-start gap-2 text-sm font-semibold text-critical-strong"><ShieldAlert className="mt-0.5 size-4 shrink-0" />{selectedPatient.allergy}</p></div>
    </CardContent></Card>

    {!canAct ? <div className="mb-6 rounded-xl border border-warning/25 bg-warning-subtle p-3 text-xs font-semibold text-warning-strong">Viewing in read-only mode. Recording administration requires the Infusion Nurse role.</div> : null}

    {order.status !== 'dispensed' && order.status !== 'ready_for_administration' && order.status !== 'in_progress' && order.status !== 'administered' && order.status !== 'completed' ? (
      <Card><CardContent className="p-6 text-sm text-metadata">{order.status === 'held' ? 'This order is held — resolve the query in Pharmacy or with the ordering clinician before continuing.' : 'Awaiting pharmacy dispensing. This screen receives verified, dispensed medication — it does not create or alter the order.'}</CardContent></Card>
    ) : null}

    <div className="grid gap-6 xl:grid-cols-2">
      {order.status === 'dispensed' ? (
        <Card className="xl:col-span-2"><CardHeader className="border-b border-divider"><CardTitle>Before administration</CardTitle></CardHeader>
          <CardContent className="pt-6">
            <div className="grid gap-3 sm:grid-cols-2">
              {([
                ['twoPatientIdentifiersConfirmed', 'Two patient identifiers confirmed'],
                ['orderVerified', 'Order verification confirmed'],
                ['consentConfirmed', 'Consent status confirmed'],
                ['allergyVerified', 'Allergy verified'],
                ['preTreatmentVitalsRecorded', 'Pre-treatment vitals recorded'],
                ['requiredLabsAvailable', 'Required labs available'],
                ['venousAccessConfirmed', 'Venous-access status confirmed'],
                ['pharmacyPreparedMedicationConfirmed', 'Pharmacy-prepared medication confirmed'],
              ] as const).map(([key, label]) => (
                <label key={key} className="flex items-center gap-3 rounded-lg border border-border bg-input-background p-3 text-sm text-supporting">
                  <input type="checkbox" className="size-4 accent-primary" checked={checklist[key]} disabled={!canAct} onChange={() => setChecklist((c) => ({ ...c, [key]: !c[key] }))} />{label}
                </label>
              ))}
            </div>
            <Button type="button" className="mt-4" disabled={!canAct || !allChecklistDone} onClick={confirmChecklist}><CheckCircle2 />Confirm & Move to Administration</Button>
          </CardContent>
        </Card>
      ) : null}

      {order.status === 'ready_for_administration' ? (
        <Card className="xl:col-span-2"><CardContent className="flex flex-wrap items-center justify-between gap-4 p-5"><p className="text-sm font-semibold text-supporting">Pre-administration checklist confirmed. Ready to begin.</p><Button type="button" disabled={!canAct} onClick={beginAdministration}><Syringe />Begin Administration</Button></CardContent></Card>
      ) : null}

      {(order.status === 'in_progress' || order.status === 'administered' || order.status === 'completed') ? (
        <>
          {premedLines.length > 0 ? (
            <Card><CardHeader className="border-b border-divider"><CardTitle>Pre-medication</CardTitle></CardHeader><CardContent className="space-y-3 pt-6">
              {premedLines.map((line) => <div key={line.id} className="flex items-center justify-between rounded-lg border border-divider bg-surface-elevated/70 p-3 text-sm"><span className="font-semibold text-supporting">{line.genericDrugName} · {line.orderedDose}</span><span className="text-xs text-metadata">{line.route}</span></div>)}
            </CardContent></Card>
          ) : null}

          <Card className={premedLines.length > 0 ? '' : 'xl:col-span-2'}><CardHeader className="border-b border-divider"><CardTitle>Medication Administration Record</CardTitle></CardHeader>
            <CardContent className="space-y-3 pt-6">
              {dispensableLines.map((line, index) => {
                const entry = getEntry(line.id)
                if (!entry) return null
                return <MarRow key={line.id} entry={entry} sequence={index + 1} canAct={canAct} onUpdate={(changes) => updateAdministration(entry.id, changes)} />
              })}
            </CardContent>
          </Card>
        </>
      ) : null}

      {order.status === 'in_progress' ? (
        <Card className="xl:col-span-2"><CardHeader className="border-b border-divider"><CardTitle>Reaction / escalation</CardTitle></CardHeader>
          <CardContent className="space-y-4 pt-6">
            {showToxicityForm ? (
              <div className="grid gap-3 sm:grid-cols-2">
                <label className={fieldClassName}>Toxicity term (CTCAE)<select className={`${selectClassName} mt-1`} value={toxTerm} onChange={(e) => setToxTerm(e.target.value)}>{CTCAE_TERMS.map((t) => <option key={t.code} value={t.display}>{t.display}</option>)}</select></label>
                <label className={fieldClassName}>Grade<select className={`${selectClassName} mt-1`} value={toxGrade} onChange={(e) => setToxGrade(Number(e.target.value) as 1 | 2 | 3 | 4 | 5)}>{CTCAE_GENERIC_GRADES.map((g) => <option key={g.grade} value={g.grade}>{g.label}</option>)}</select></label>
                <label className={fieldClassName}>Relationship to therapy<select className={`${selectClassName} mt-1`} value={toxRelationship} onChange={(e) => setToxRelationship(e.target.value)}>{CTCAE_RELATIONSHIP_TO_THERAPY.map((r) => <option key={r.value} value={r.value}>{r.display}</option>)}</select></label>
                <label className={fieldClassName}>Intervention<Input className="mt-1" value={toxIntervention} onChange={(e) => setToxIntervention(e.target.value)} placeholder="Immediate intervention taken" /></label>
              </div>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="destructive" size="sm" disabled={!canAct} onClick={holdForReaction}><AlertTriangle />{showToxicityForm ? 'Record Toxicity & Hold Infusion' : 'Hold Infusion'}</Button>
            </div>
            <p className="text-xs leading-5 text-metadata">Resume, modify, discontinue, or defer remains a clinician-led decision — recorded as a dose modification on the Treatment Order screen, not here.</p>
          </CardContent>
        </Card>
      ) : null}

      {order.status === 'in_progress' && allCompleted ? (
        <Card variant="elevated" className="xl:col-span-2"><CardHeader className="border-b border-divider"><CardTitle>Treatment completion</CardTitle></CardHeader>
          <CardContent className="grid gap-4 pt-6 sm:grid-cols-2 lg:grid-cols-4">
            <label className={fieldClassName}>Post-treatment vitals<Input className="mt-1" value={postVitals} onChange={(e) => setPostVitals(e.target.value)} /></label>
            <label className={`${fieldClassName} sm:col-span-2`}>Discharge instructions<Input className="mt-1" value={dischargeInstructions} onChange={(e) => setDischargeInstructions(e.target.value)} /></label>
            <label className={fieldClassName}>Next cycle date<Input className="mt-1" type="date" value={nextCycleDate} onChange={(e) => setNextCycleDate(e.target.value)} /></label>
            <div className="flex items-end sm:col-span-2 lg:col-span-4"><Button type="button" disabled={!canAct} onClick={completeTreatment}><Syringe />Complete Treatment</Button></div>
          </CardContent>
        </Card>
      ) : null}

      {order.status === 'completed' ? <Card variant="elevated" className="xl:col-span-2"><CardContent className="p-5"><Badge variant="success"><CheckCircle2 />Treatment completed</Badge></CardContent></Card> : null}
    </div>
  </PageContainer>
}

function MarRow({ entry, sequence, canAct, onUpdate }: { entry: MARDrugAdministration; sequence: number; canAct: boolean; onUpdate: (changes: Partial<MARDrugAdministration>) => void }) {
  return (
    <div className="grid min-w-0 items-center gap-3 rounded-lg border border-divider bg-surface-elevated/70 p-4 md:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,0.8fr))]">
      <div className="min-w-0"><p className="font-semibold text-supporting">{sequence}. {entry.drug}</p><p className="text-xs text-metadata">{entry.doseGiven} (carried forward from order) · {entry.route}</p></div>
      <Input aria-label={`${entry.drug} start time`} type="time" disabled={!canAct} value={entry.startTime ?? ''} onChange={(e) => onUpdate({ startTime: e.target.value })} />
      <Input aria-label={`${entry.drug} end time`} type="time" disabled={!canAct} value={entry.endTime ?? ''} onChange={(e) => onUpdate({ endTime: e.target.value })} />
      <select aria-label={`${entry.drug} administration status`} className={selectClassName} disabled={!canAct} value={entry.infusionStatus} onChange={(e) => {
        const status = e.target.value as MARDrugAdministration['infusionStatus']
        onUpdate({ infusionStatus: status, startTime: status === 'in_progress' && !entry.startTime ? new Date().toTimeString().slice(0, 5) : entry.startTime, endTime: status === 'completed' ? new Date().toTimeString().slice(0, 5) : entry.endTime })
      }}>
        <option value="not_started">Pending</option><option value="in_progress">In progress</option><option value="paused">Paused</option><option value="completed">Completed</option><option value="held">Held</option><option value="discontinued">Discontinued</option>
      </select>
      <p className="text-xs text-metadata md:col-span-4"><CheckCircle2 className="mr-1 inline size-3.5" />Administered by {entry.administeredBy.name}</p>
    </div>
  )
}
