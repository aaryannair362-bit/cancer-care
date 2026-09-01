'use client'

import * as React from 'react'
import { AlertTriangle, CheckCircle2, ClipboardCheck, FileWarning, History, Route, ShieldAlert } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { RecommendationPanel } from '@/components/oncology/recommendation-panel'
import { TreatmentStatusPill } from '@/components/oncology/status-pill'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { canPrescribeTreatment } from '@/lib/demo-access'
import { useOncology } from '@/lib/oncology/store'
import { ADMINISTRATION_ROUTES, DOSE_MODIFICATION_TYPES, TREATMENT_INTENTS } from '@/lib/oncology/terminology'
import type { ActorRef, DoseModificationType } from '@/lib/oncology/types'

const fieldClassName = 'text-xs font-medium text-metadata'
const selectClassName = 'h-10 w-full min-w-0 rounded-xl border border-input bg-input-background px-3 text-sm text-supporting shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

export default function TreatmentOrderPage() {
  const { role, selectedPatient } = useDemoAccess()
  const {
    state, getOrdersForPatient, getRegimen, getTreatmentPlan, authorizeOrder,
    recordDoseModification, getAuditTrail,
  } = useOncology()

  const actor: ActorRef = { userId: role.roleId, name: role.label, roleLabel: role.label }
  const canAuthorize = canPrescribeTreatment(role)
  const treatmentPlan = getTreatmentPlan(selectedPatient.id)
  const orders = getOrdersForPatient(selectedPatient.id)
  const order = orders[0]

  const [modifyingLine, setModifyingLine] = React.useState<string | null>(null)
  const [modType, setModType] = React.useState<DoseModificationType>('dose_reduction')
  const [modReason, setModReason] = React.useState('')
  const [modJustification, setModJustification] = React.useState('')
  const [modNewDose, setModNewDose] = React.useState('')

  if (!order) {
    return (
      <PageContainer>
        <PageHeader title="Treatment Order" description="Create a standardized, regimen-driven treatment order" />
        <Card><CardContent className="p-6 text-sm text-metadata">No treatment order exists yet for this patient. Order creation from the regimen library opens once a Treatment Plan has been authorized.</CardContent></Card>
      </PageContainer>
    )
  }

  const regimen = order.regimenId ? getRegimen(order.regimenId) : undefined
  const orderAudit = getAuditTrail('TreatmentOrder', order.id)
  const canEditModifications = canPrescribeTreatment(role)

  // ── Order-to-delivery traceability (item 32): MDT -> Treatment Plan -> Doctor order ->
  // Pharmacy verification -> Preparation -> Dispensing -> Nurse verification -> Administration,
  // each stage with its actor and timestamp, reading the same records every other screen writes.
  const mdtCase = treatmentPlan?.mdtCaseId ? state.mdtCases.find((c) => c.id === treatmentPlan.mdtCaseId) : undefined
  const verification = state.verifications.find((v) => v.orderId === order.id && v.outcome === 'verified')
  const dispenseRecords = state.dispenseRecords.filter((d) => d.orderId === order.id)
  const preparedRecord = dispenseRecords.find((d) => d.preparedAt)
  const dispensedRecord = dispenseRecords.find((d) => d.dispensedAt)
  const firstAdministration = state.marEntries.filter((m) => m.orderId === order.id).sort((a, b) => (a.startTime ?? '').localeCompare(b.startTime ?? ''))[0]
  const traceability: { label: string; actor?: string; at?: string; reached: boolean }[] = [
    { label: 'MDT recommendation', actor: mdtCase?.approvedBy?.name, at: mdtCase?.approvedAt, reached: Boolean(mdtCase?.approvedAt) },
    { label: 'Treatment plan created', actor: treatmentPlan?.createdBy.name, at: treatmentPlan?.createdAt, reached: Boolean(treatmentPlan) },
    { label: 'Doctor order authorized', actor: order.orderingClinician.name, at: order.authorizedAt, reached: Boolean(order.authorizedAt) },
    { label: 'Pharmacy verification', actor: verification?.verifiedBy.name, at: verification?.verifiedAt, reached: Boolean(verification) },
    { label: 'Preparation', actor: preparedRecord?.preparedBy?.name, at: preparedRecord?.preparedAt, reached: Boolean(preparedRecord?.preparedAt) },
    { label: 'Dispensing', actor: preparedRecord?.preparedBy?.name, at: dispensedRecord?.dispensedAt, reached: Boolean(dispensedRecord?.dispensedAt) },
    { label: 'Nurse / administration', actor: firstAdministration?.administeredBy.name, at: firstAdministration?.startTime, reached: Boolean(firstAdministration && firstAdministration.infusionStatus !== 'not_started') },
  ]

  const submitModification = (lineId: string) => {
    if (!modReason.trim() || !modJustification.trim()) return
    const line = order.drugLines.find((l) => l.id === lineId)
    if (!line) return
    recordDoseModification(order.id, lineId, {
      type: modType, reason: modReason, clinicalJustification: modJustification,
      originalDose: line.orderedDose, modifiedDose: modNewDose || undefined, approvedBy: actor,
    })
    setModifyingLine(null); setModReason(''); setModJustification(''); setModNewDose('')
  }

  return (
    <PageContainer>
      <PageHeader
        title="Medical Oncology Treatment Order"
        description="Standardized, regimen-driven ordering — the single order every downstream screen reads"
        actions={order.status === 'draft' || order.status === 'proposed' || order.status === 'clinician_approved' ? (
          <Button type="button" disabled={!canAuthorize} onClick={() => authorizeOrder(order.id, actor)}>
            <ClipboardCheck />{canAuthorize ? 'Authorize Order' : 'Requires prescribing clinician'}
          </Button>
        ) : undefined}
      />

      <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-5">
        <div className="sm:col-span-2"><p className={fieldClassName}>Patient</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn} · {selectedPatient.age} years · {selectedPatient.sex}</p></div>
        <div><p className={fieldClassName}>Diagnosis</p><p className="mt-1 text-sm font-semibold text-supporting">{order.diagnosis}</p></div>
        <div><p className={fieldClassName}>Treatment intent</p><p className="mt-1 text-sm font-semibold text-supporting">{TREATMENT_INTENTS.find((i) => i.value === order.treatmentIntent)?.display} · {order.lineOfTherapy}</p></div>
        <div><p className={fieldClassName}>Order status</p><p className="mt-1"><TreatmentStatusPill status={order.status} /></p></div>
      </CardContent></Card>

      {selectedPatient.allergy ? (
        <div className="mb-6 flex items-start gap-3 rounded-xl border border-critical/25 bg-critical-subtle p-4 text-sm font-semibold text-critical-strong">
          <ShieldAlert className="mt-0.5 size-5 shrink-0" />
          <div><p>Allergy on record</p><p className="mt-1 text-sm font-normal">{selectedPatient.allergy}</p><p className="mt-2 text-xs font-normal">{order.allergiesAcknowledged ? 'Acknowledged by ordering clinician at authorization.' : 'Requires clinician acknowledgement before authorization.'}</p></div>
        </div>
      ) : null}

      <Card className="mb-6"><CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><Route className="size-4 text-brand-deep" /><CardTitle>Order-to-delivery traceability</CardTitle></div></CardHeader>
        <CardContent className="pt-6"><div className="overflow-x-auto"><ol className="flex min-w-[900px] items-start">{traceability.map((stage, index) => (
          <li key={stage.label} className="relative flex-1">
            <div className="flex items-center">
              <span className={`relative z-10 flex size-8 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold ${stage.reached ? 'border-success bg-success-subtle text-success-strong' : 'border-border bg-surface text-metadata'}`}>{index + 1}</span>
              {index < traceability.length - 1 ? <span className={`h-0.5 flex-1 ${stage.reached ? 'bg-success' : 'bg-border-emphasized'}`} /> : null}
            </div>
            <div className="mt-2 mr-3"><p className="text-xs font-semibold text-supporting">{stage.label}</p>{stage.reached ? <p className="mt-0.5 text-[11px] text-metadata">{stage.actor ?? '—'}{stage.at ? ` · ${new Date(stage.at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}` : ''}</p> : <p className="mt-0.5 text-[11px] text-metadata">Not yet reached</p>}</div>
          </li>
        ))}</ol></div></CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Card>
            <CardHeader className="border-b border-divider"><CardTitle>Regimen / protocol</CardTitle><CardDescription>{regimen ? `${regimen.name} · v${regimen.version} · approved by ${regimen.approvedBy.name}` : 'No regimen reference linked'}</CardDescription></CardHeader>
            <CardContent className="grid gap-4 pt-6 sm:grid-cols-3">
              {[['Cycle number', String(order.cycleNumber)], ['Day', String(order.day)], ['Planned cycles', String(order.plannedNumberOfCycles)], ['Protocol / version', order.protocolReferenceVersion ?? '—'], ['Height', order.heightCm ? `${order.heightCm} cm` : '—'], ['Weight', order.weightKg ? `${order.weightKg} kg` : '—'], ['BSA', order.bsaM2 ? `${order.bsaM2} m²` : '—']]
                .map(([label, value]) => <div key={label}><p className={fieldClassName}>{label}</p><p className="mt-1 text-sm font-semibold text-supporting">{value}</p></div>)}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="border-b border-divider"><CardTitle>Drug sequence</CardTitle><CardDescription>Premedication → systemic drugs → supportive medication, in the order they are given</CardDescription></CardHeader>
            <CardContent className="space-y-3 pt-6">
              {order.drugLines.map((line, index) => {
                const activeModifications = line.doseModifications
                return (
                  <div key={line.id} className="rounded-xl border border-divider bg-surface-elevated/70 p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="flex items-start gap-3">
                        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-brand-soft text-xs font-semibold text-brand-deep">{index + 1}</span>
                        <div>
                          <p className="text-sm font-semibold text-supporting">{line.genericDrugName}{line.isPremedication ? <Badge variant="neutral" className="ml-2">Premedication</Badge> : null}{line.isSupportive ? <Badge variant="neutral" className="ml-2">Supportive</Badge> : null}</p>
                          <p className="mt-1 text-xs text-metadata">Dose basis (reference): {line.doseBasisDescription} · Route: {line.route}{line.diluent ? ` · Diluent: ${line.diluent}` : ''}{line.infusionDuration ? ` · Duration: ${line.infusionDuration}` : ''}</p>
                        </div>
                      </div>
                      <div className="text-right">
                        <p className={fieldClassName}>Ordered dose</p>
                        <p className="text-sm font-semibold text-supporting">{line.orderedDose}</p>
                      </div>
                    </div>
                    {activeModifications.length > 0 ? (
                      <div className="mt-3 space-y-2 border-t border-divider pt-3">
                        {activeModifications.map((mod) => (
                          <div key={mod.id} className="flex items-start gap-2 rounded-lg bg-warning-subtle p-2.5 text-xs text-warning-strong">
                            <FileWarning className="mt-0.5 size-3.5 shrink-0" />
                            <div><p className="font-semibold">{DOSE_MODIFICATION_TYPES.find((t) => t.value === mod.type)?.display}: {mod.originalDose} → {mod.modifiedDose ?? '—'}</p><p className="mt-0.5">{mod.reason} · approved by {mod.approvedBy.name}</p></div>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {!line.isPremedication && !line.isSupportive && canEditModifications ? (
                      modifyingLine === line.id ? (
                        <div className="mt-3 space-y-3 border-t border-divider pt-3">
                          <div className="grid gap-3 sm:grid-cols-2">
                            <label className={fieldClassName}>Modification type<select className={`${selectClassName} mt-1`} value={modType} onChange={(e) => setModType(e.target.value as DoseModificationType)}>{DOSE_MODIFICATION_TYPES.map((t) => <option key={t.value} value={t.value}>{t.display}</option>)}</select></label>
                            <label className={fieldClassName}>New dose (leave blank if not applicable)<Input className="mt-1" value={modNewDose} onChange={(e) => setModNewDose(e.target.value)} placeholder="e.g. 82 mg (20% reduction)" /></label>
                          </div>
                          <label className={fieldClassName}>Reason / toxicity<Input className="mt-1" value={modReason} onChange={(e) => setModReason(e.target.value)} placeholder="e.g. Grade 3 neutropenia, prior cycle" /></label>
                          <label className={fieldClassName}>Clinical justification<Input className="mt-1" value={modJustification} onChange={(e) => setModJustification(e.target.value)} placeholder="Clinician-entered rationale" /></label>
                          <div className="flex gap-2"><Button type="button" size="sm" onClick={() => submitModification(line.id)}>Record Modification</Button><Button type="button" size="sm" variant="outline" onClick={() => setModifyingLine(null)}>Cancel</Button></div>
                        </div>
                      ) : (
                        <button type="button" onClick={() => setModifyingLine(line.id)} className="mt-3 text-xs font-semibold text-brand-deep underline-offset-2 hover:underline">Record dose modification / delay / hold</button>
                      )
                    ) : null}
                  </div>
                )
              })}
              <p className="rounded-lg border border-information/25 bg-information-subtle p-3 text-xs leading-5 text-information-strong">Ordered dose is entered and authorized by the treating clinician and carries forward unchanged through pharmacy verification, preparation, dispensing and administration. This system does not calculate, round, or threshold-check doses — dose decisions, including any modification above, remain clinician-decided and are recorded with the original value preserved.</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="border-b border-divider"><CardTitle>Treatment eligibility / hold parameters</CardTitle><CardDescription>Presence and clinician review — not an automated pass/fail</CardDescription></CardHeader>
            <CardContent className="grid gap-3 pt-6 sm:grid-cols-2">
              {order.eligibilityParametersChecked.map((check) => (
                <div key={check.parameter} className="flex items-start gap-3 rounded-lg border border-divider bg-input-background p-3 text-sm text-supporting">
                  {check.clinicianReviewed ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-brand-deep" /> : <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning-strong" />}
                  <div><p className="font-semibold">{check.parameter}</p>{check.note ? <p className="mt-0.5 text-xs text-metadata">{check.note}</p> : null}</div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <RecommendationPanel patientId={order.patientId} context="treatment-order" audience="clinician" actor={actor} title="Guideline context for this order" />

          <Card><CardHeader className="border-b border-divider"><CardTitle>Order provenance</CardTitle></CardHeader><CardContent className="space-y-3 pt-6 text-sm">
            <div><p className={fieldClassName}>Treatment plan</p><p className="mt-1 font-semibold text-supporting">{treatmentPlan?.diagnosis ?? order.diagnosis} · {treatmentPlan?.stage}</p></div>
            <div><p className={fieldClassName}>Ordering clinician</p><p className="mt-1 font-semibold text-supporting">{order.orderingClinician.name}</p></div>
            <div><p className={fieldClassName}>Authorized</p><p className="mt-1 font-semibold text-supporting">{order.authorizedAt ? new Date(order.authorizedAt).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'Not yet authorized'}</p></div>
          </CardContent></Card>

          <Card><CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><History className="size-4 text-brand-deep" /><CardTitle>Audit trail</CardTitle></div></CardHeader>
            <CardContent className="space-y-3 pt-6">
              {orderAudit.length === 0 ? <p className="text-sm text-metadata">No recorded changes yet.</p> : orderAudit.map((entry) => (
                <div key={entry.id} className="border-b border-divider pb-2 text-xs last:border-0 last:pb-0">
                  <p className="font-semibold text-supporting">{entry.action}</p>
                  <p className="text-metadata">{entry.actor.name} · {new Date(entry.timestamp).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}</p>
                  {entry.reason ? <p className="mt-0.5 text-metadata">Reason: {entry.reason}</p> : null}
                </div>
              ))}
            </CardContent>
          </Card>

          <Card><CardHeader className="border-b border-divider"><CardTitle>Standard routes reference</CardTitle></CardHeader><CardContent className="flex flex-wrap gap-2 pt-6">{ADMINISTRATION_ROUTES.map((r) => <Badge key={r.code} variant="neutral">{r.display}</Badge>)}</CardContent></Card>
        </div>
      </div>
    </PageContainer>
  )
}
