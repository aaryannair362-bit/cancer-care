'use client'

import * as React from 'react'
import { Ban, CheckCircle2, MessageSquareWarning, PackageCheck, PauseCircle, ShieldCheck, Trash2 } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { TreatmentStatusPill } from '@/components/oncology/status-pill'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { canVerifyDispense } from '@/lib/demo-access'
import { useOncology } from '@/lib/oncology/store'
import type { ActorRef, PharmacyDispenseStatus, VerificationCheckpoint } from '@/lib/oncology/types'

const fieldClassName = 'text-xs font-medium text-metadata'

type CheckKey = Exclude<keyof VerificationCheckpoint, 'id' | 'orderId' | 'verifiedBy' | 'verifiedAt' | 'outcome' | 'queryReason'>
const CHECK_LABELS: Record<CheckKey, string> = {
  patientIdentityConfirmed: 'Patient identity',
  drugConfirmed: 'Drug',
  doseConfirmed: 'Dose matches order',
  routeConfirmed: 'Route',
  sequenceConfirmed: 'Sequence',
  cycleDayConfirmed: 'Cycle / day',
  allergiesReviewed: 'Allergies reviewed',
  requiredLabsPresent: 'Required labs present',
  expiryChecked: 'Expiry',
}

export default function PharmacyPage() {
  const { role, selectedPatient } = useDemoAccess()
  const { getOrdersForPatient, transitionOrder, recordVerification, createDispenseRecord, updateDispenseRecord, state } = useOncology()
  const actor: ActorRef = { userId: role.roleId, name: role.label, roleLabel: role.label }
  const canAct = canVerifyDispense(role)

  const order = getOrdersForPatient(selectedPatient.id)[0]
  const [checks, setChecks] = React.useState<Record<CheckKey, boolean>>(
    Object.fromEntries(Object.keys(CHECK_LABELS).map((key) => [key, false])) as Record<CheckKey, boolean>
  )
  const [queryReason, setQueryReason] = React.useState('')
  const [showQuery, setShowQuery] = React.useState(false)
  const [wastageLine, setWastageLine] = React.useState<string | null>(null)
  const [wastageReason, setWastageReason] = React.useState('')

  if (!order) {
    return <PageContainer><PageHeader title="Pharmacy" description="Verification, preparation and dispensing" /><Card><CardContent className="p-6 text-sm text-metadata">No treatment order is awaiting pharmacy action.</CardContent></Card></PageContainer>
  }

  const dispensableLines = order.drugLines.filter((line) => !line.isPremedication && !line.isSupportive)
  const dispenseRecords = state.dispenseRecords.filter((d) => d.orderId === order.id)
  const getRecord = (drugLineId: string) => dispenseRecords.find((d) => d.drugLineId === drugLineId)

  const allChecked = Object.values(checks).every(Boolean)

  const submitVerification = (outcome: VerificationCheckpoint['outcome']) => {
    recordVerification(order.id, { ...checks, verifiedBy: actor, outcome, queryReason: outcome !== 'verified' ? queryReason : undefined })
    if (outcome !== 'verified') {
      transitionOrder(order.id, 'held', actor, queryReason || `Pharmacy ${outcome === 'rejected' ? 'rejected' : 'raised a query on'} the order`)
    }
    setShowQuery(false); setQueryReason('')
  }

  const beginPreparation = () => {
    transitionOrder(order.id, 'preparation_pending', actor)
    dispensableLines.forEach((line) => {
      if (!getRecord(line.id)) {
        createDispenseRecord({ orderId: order.id, drugLineId: line.id, patientId: order.patientId, status: 'preparation' })
      }
    })
  }

  const markPrepared = (drugLineId: string, fields: { availableFormulationStrength: string; quantityRequired: string; diluent: string; volume: string; batchLot: string; expiry: string }) => {
    const record = getRecord(drugLineId)
    if (!record) return
    updateDispenseRecord(record.id, { ...fields, status: 'prepared', preparedBy: actor, preparedAt: new Date().toISOString(), verifiedBy: actor, verifiedAt: new Date().toISOString() }, actor)
  }

  const allPrepared = dispensableLines.length > 0 && dispensableLines.every((line) => getRecord(line.id)?.status === 'prepared')

  const dispenseToDayCare = () => {
    dispensableLines.forEach((line) => {
      const record = getRecord(line.id)
      if (record) updateDispenseRecord(record.id, { status: 'dispensed', dispensedAt: new Date().toISOString(), destination: 'Day Care / Infusion' }, actor)
    })
    transitionOrder(order.id, 'prepared', actor)
    transitionOrder(order.id, 'dispensed', actor)
  }

  const recordWastage = (drugLineId: string) => {
    const record = getRecord(drugLineId)
    if (!record || !wastageReason.trim()) return
    updateDispenseRecord(record.id, { wastageRecorded: { quantity: 'As prepared', reason: wastageReason, recordedBy: actor, at: new Date().toISOString() } }, actor, wastageReason)
    setWastageLine(null); setWastageReason('')
  }

  return (
    <PageContainer>
      <PageHeader title="Pharmacy" description="Verification, preparation and dispensing against the authorized treatment order" />

      <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-5">
        <div className="sm:col-span-2"><p className={fieldClassName}>Patient</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn}</p></div>
        <div><p className={fieldClassName}>Regimen / cycle</p><p className="mt-1 text-sm font-semibold text-supporting">{order.regimenName}</p><p className="text-xs text-metadata">Cycle {order.cycleNumber} · Day {order.day}</p></div>
        <div><p className={fieldClassName}>Ordering clinician</p><p className="mt-1 text-sm font-semibold text-supporting">{order.orderingClinician.name}</p></div>
        <div><p className={fieldClassName}>Order status</p><p className="mt-1"><TreatmentStatusPill status={order.status} /></p></div>
      </CardContent></Card>

      {!canAct ? <div className="mb-6 rounded-xl border border-warning/25 bg-warning-subtle p-3 text-xs font-semibold text-warning-strong">Viewing in read-only mode. Verification, preparation and dispensing actions require the Oncology Pharmacist role.</div> : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          {order.status === 'ordered' ? (
            <Card variant="elevated"><CardHeader className="border-b border-divider"><CardTitle>Order received</CardTitle><CardDescription>Ordered dose is carried forward unchanged from the Medical Oncology order — pharmacy verifies, it does not re-enter or recalculate.</CardDescription></CardHeader>
              <CardContent className="space-y-3 pt-6">
                {order.drugLines.map((line) => <div key={line.id} className="flex items-center justify-between rounded-lg border border-divider bg-surface-elevated/70 p-3 text-sm"><span className="font-semibold text-supporting">{line.genericDrugName}</span><span className="text-metadata">{line.orderedDose}</span></div>)}
                <Button type="button" disabled={!canAct} onClick={beginPreparation}><ShieldCheck />Begin Pharmacy Review</Button>
              </CardContent>
            </Card>
          ) : null}

          {order.status === 'verification_pending' ? (
            <Card variant="elevated"><CardHeader className="border-b border-divider"><CardTitle>Independent verification checkpoints</CardTitle><CardDescription>Every item confirmed before this order can move to preparation</CardDescription></CardHeader>
              <CardContent className="space-y-4 pt-6">
                <div className="grid gap-3 sm:grid-cols-2">
                  {(Object.keys(CHECK_LABELS) as CheckKey[]).map((key) => (
                    <label key={key} className="flex items-center gap-3 rounded-lg border border-border bg-input-background p-3 text-sm text-supporting">
                      <input type="checkbox" className="size-4 accent-primary" checked={checks[key]} disabled={!canAct} onChange={() => setChecks((c) => ({ ...c, [key]: !c[key] }))} />{CHECK_LABELS[key]}
                    </label>
                  ))}
                </div>
                {showQuery ? <label className={fieldClassName}>Reason for query / rejection<Input className="mt-1" value={queryReason} onChange={(e) => setQueryReason(e.target.value)} placeholder="What needs the oncologist's attention?" /></label> : null}
                <div className="flex flex-wrap gap-2">
                  <Button type="button" disabled={!canAct || !allChecked} onClick={() => submitVerification('verified')}><CheckCircle2 />Verify</Button>
                  <Button type="button" variant="outline" disabled={!canAct} onClick={() => (showQuery ? submitVerification('query_raised') : setShowQuery(true))}><MessageSquareWarning />{showQuery ? 'Submit Query' : 'Raise Query'}</Button>
                  <Button type="button" variant="destructive" disabled={!canAct || !queryReason.trim()} onClick={() => submitVerification('rejected')}><Ban />Reject Order</Button>
                </div>
              </CardContent>
            </Card>
          ) : null}

          {order.status === 'verified' ? (
            <Card variant="elevated"><CardContent className="flex flex-wrap items-center justify-between gap-4 p-5"><p className="text-sm font-semibold text-supporting">Verified. Ready to begin preparation.</p><Button type="button" disabled={!canAct} onClick={beginPreparation}><PackageCheck />Move to Preparation</Button></CardContent></Card>
          ) : null}

          {(order.status === 'preparation_pending' || order.status === 'prepared' || order.status === 'dispensed') ? (
            <Card><CardHeader className="border-b border-divider"><CardTitle>Preparation & dispensing</CardTitle></CardHeader>
              <CardContent className="space-y-4 pt-6">
                {dispensableLines.map((line) => {
                  const record = getRecord(line.id)
                  return <DispenseLineCard key={line.id} drug={line.genericDrugName} dose={line.orderedDose} status={record?.status ?? 'preparation'} record={record} canAct={canAct}
                    onMarkPrepared={(fields) => markPrepared(line.id, fields)}
                    onWastage={() => setWastageLine(line.id)}
                  />
                })}
                {wastageLine ? <div className="rounded-lg border border-warning/25 bg-warning-subtle p-3"><label className={fieldClassName}>Wastage reason<Input className="mt-1" value={wastageReason} onChange={(e) => setWastageReason(e.target.value)} /></label><div className="mt-2 flex gap-2"><Button type="button" size="sm" onClick={() => recordWastage(wastageLine)}>Record Wastage</Button><Button type="button" size="sm" variant="outline" onClick={() => setWastageLine(null)}>Cancel</Button></div></div> : null}
                {order.status === 'preparation_pending' ? <Button type="button" disabled={!canAct || !allPrepared} onClick={dispenseToDayCare}><PackageCheck />Dispense to Day Care</Button> : null}
                {order.status === 'dispensed' ? <Badge variant="success">Dispensed to Day Care / Infusion</Badge> : null}
              </CardContent>
            </Card>
          ) : null}

          {order.status === 'held' ? (
            <Card variant="alert"><CardContent className="flex flex-wrap items-center justify-between gap-4 p-5"><div><p className="flex items-center gap-2 text-sm font-semibold"><PauseCircle className="size-4" />Order held</p><p className="mt-1 text-xs">Sent back to the ordering clinician for review.</p></div>{canAct ? <Button type="button" size="sm" variant="outline" onClick={() => transitionOrder(order.id, 'verification_pending', actor, 'Resumed after clinician review')}>Resume Pharmacy Review</Button> : null}</CardContent></Card>
          ) : null}
        </div>

        <div className="space-y-6">
          <Card><CardHeader className="border-b border-divider"><CardTitle>Actions</CardTitle></CardHeader><CardContent className="space-y-3 pt-6">
            {canAct && order.status !== 'held' && order.status !== 'cancelled' && order.status !== 'completed' ? (
              <Button type="button" variant="destructive" size="sm" onClick={() => transitionOrder(order.id, 'cancelled', actor, 'Cancelled by pharmacy')}><Trash2 />Cancel Order</Button>
            ) : null}
            <p className="text-xs leading-5 text-metadata">Substituting a formulation is only available through the approved-workflow query above — pharmacy proposes, the ordering clinician confirms.</p>
          </CardContent></Card>
        </div>
      </div>
    </PageContainer>
  )
}

function DispenseLineCard({
  drug, dose, status, record, canAct, onMarkPrepared, onWastage,
}: {
  drug: string; dose: string; status: PharmacyDispenseStatus; record?: { availableFormulationStrength?: string; quantityRequired?: string; diluent?: string; volume?: string; batchLot?: string; expiry?: string }
  canAct: boolean
  onMarkPrepared: (fields: { availableFormulationStrength: string; quantityRequired: string; diluent: string; volume: string; batchLot: string; expiry: string }) => void
  onWastage: () => void
}) {
  const [formulation, setFormulation] = React.useState(record?.availableFormulationStrength ?? '')
  const [quantity, setQuantity] = React.useState(record?.quantityRequired ?? '')
  const [diluent, setDiluent] = React.useState(record?.diluent ?? '')
  const [volume, setVolume] = React.useState(record?.volume ?? '')
  const [batchLot, setBatchLot] = React.useState(record?.batchLot ?? '')
  const [expiry, setExpiry] = React.useState(record?.expiry ?? '')
  const prepared = status === 'prepared' || status === 'dispensed'

  return (
    <div className="rounded-xl border border-divider bg-surface-elevated/70 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><p className="text-sm font-semibold text-supporting">{drug}</p><p className="text-xs text-metadata">Ordered dose (carried forward): {dose}</p></div>
        <Badge variant={status === 'dispensed' ? 'success' : status === 'prepared' ? 'information' : 'warning'}>{status}</Badge>
      </div>
      {!prepared ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <label className={fieldClassName}>Available formulation / strength<Input className="mt-1" value={formulation} onChange={(e) => setFormulation(e.target.value)} /></label>
          <label className={fieldClassName}>Quantity required<Input className="mt-1" value={quantity} onChange={(e) => setQuantity(e.target.value)} /></label>
          <label className={fieldClassName}>Diluent / volume<div className="mt-1 flex gap-2"><Input value={diluent} onChange={(e) => setDiluent(e.target.value)} placeholder="Diluent" /><Input value={volume} onChange={(e) => setVolume(e.target.value)} placeholder="Volume" /></div></label>
          <label className={fieldClassName}>Batch / lot<Input className="mt-1" value={batchLot} onChange={(e) => setBatchLot(e.target.value)} /></label>
          <label className={fieldClassName}>Expiry<Input className="mt-1" type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} /></label>
          <div className="flex items-end"><Button type="button" size="sm" disabled={!canAct} onClick={() => onMarkPrepared({ availableFormulationStrength: formulation, quantityRequired: quantity, diluent, volume, batchLot, expiry })}>Mark Prepared</Button></div>
        </div>
      ) : (
        <div className="mt-3 grid gap-2 text-xs text-metadata sm:grid-cols-3">
          <span>{record?.availableFormulationStrength}</span><span>Qty {record?.quantityRequired}</span><span>{record?.diluent} · {record?.volume}</span>
          <span>Batch {record?.batchLot}</span><span>Exp {record?.expiry}</span>
          {status === 'prepared' ? <button type="button" onClick={onWastage} className="text-left font-semibold text-brand-deep underline-offset-2 hover:underline">Record wastage</button> : null}
        </div>
      )}
    </div>
  )
}
