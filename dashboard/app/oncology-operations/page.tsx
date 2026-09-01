'use client'

import { Activity, AlertTriangle, Building2, ClipboardList, Pill, Radiation, Scissors, Syringe, Users } from 'lucide-react'

import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { useOncology } from '@/lib/oncology/store'

function Tile({ icon: Icon, label, value, detail }: { icon: typeof Users; label: string; value: number | string; detail?: string }) {
  return (
    <div className="rounded-xl border border-white/70 bg-surface/80 p-4 shadow-soft-sm">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-metadata"><Icon className="size-3.5" />{label}</div>
      <p className="mt-2 font-display text-2xl font-semibold text-supporting">{value}</p>
      {detail ? <p className="mt-1 text-xs text-metadata">{detail}</p> : null}
    </div>
  )
}

export default function OncologyOperationsPage() {
  const { state } = useOncology()

  const activeOrderPatientIds = new Set(state.treatmentOrders.filter((o) => o.status !== 'completed' && o.status !== 'cancelled').map((o) => o.patientId))
  const inProgress = state.treatmentOrders.filter((o) => o.status === 'in_progress').length
  const awaitingPharmacy = state.treatmentOrders.filter((o) => o.status === 'ordered' || o.status === 'verification_pending').length
  const pendingInfusion = state.treatmentOrders.filter((o) => o.status === 'dispensed' || o.status === 'ready_for_administration').length
  const heldOrDelayed = state.treatmentOrders.filter((o) => o.status === 'held' || o.status === 'delayed').length

  const dispensedTimings = state.treatmentOrders
    .map((o) => {
      const orderedEntry = state.auditLog.find((a) => a.entityType === 'TreatmentOrder' && a.entityId === o.id && a.newValue === 'ordered')
      const dispensedEntry = state.auditLog.find((a) => a.entityType === 'TreatmentOrder' && a.entityId === o.id && a.newValue === 'dispensed')
      if (!orderedEntry || !dispensedEntry) return null
      return new Date(dispensedEntry.timestamp).getTime() - new Date(orderedEntry.timestamp).getTime()
    })
    .filter((v): v is number => v !== null && v >= 0)
  const avgTurnaroundMinutes = dispensedTimings.length > 0 ? Math.round(dispensedTimings.reduce((a, b) => a + b, 0) / dispensedTimings.length / 60000) : null

  const fractionsScheduled = state.radiationFractions.length
  const fractionsDelivered = state.radiationFractions.filter((f) => f.status === 'delivered').length
  const fractionsMissed = state.radiationFractions.filter((f) => f.status === 'missed').length

  const surgeryPending = state.surgicalPlans.filter((p) => ['recommended', 'surgeon_reviewed', 'planned'].includes(p.surgicalSubStatus)).length
  const surgeryScheduled = state.surgicalPlans.filter((p) => ['pre_op_ready', 'scheduled'].includes(p.surgicalSubStatus)).length
  const surgeryCompleted = state.surgicalPlans.filter((p) => p.surgicalSubStatus === 'histopathology_available').length

  const mdtPending = state.mdtCases.filter((c) => c.status !== 'plan_created').length

  const delayReasons = [
    ...state.treatmentOrders.flatMap((o) => o.drugLines.flatMap((l) => l.doseModifications.filter((m) => m.type === 'treatment_delay' || m.type === 'cycle_postponement').map((m) => ({ reason: m.reason, at: m.timestamp })))),
    ...state.auditLog.filter((a) => a.entityType === 'TreatmentOrder' && a.action === 'status_change' && a.newValue === 'delayed' && a.reason).map((a) => ({ reason: a.reason!, at: a.timestamp })),
  ]

  const departmentWorkload = [
    { label: 'Medical Oncology', icon: Pill, count: activeOrderPatientIds.size },
    { label: 'Radiation Oncology', icon: Radiation, count: state.radiationPrescriptions.filter((r) => r.rtSubStatus !== 'completed').length },
    { label: 'Surgical Oncology', icon: Scissors, count: surgeryPending + surgeryScheduled },
    { label: 'MDT / Tumour Board', icon: Users, count: mdtPending },
  ]

  return (
    <PageContainer>
      <PageHeader title="Oncology Operations" description="Operational counts across the treatment-execution workflow — not a clinical drill-down" />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={Users} label="Patients on active treatment" value={activeOrderPatientIds.size} />
        <Tile icon={Syringe} label="Infusions in progress" value={inProgress} />
        <Tile icon={ClipboardList} label="Orders awaiting pharmacy verification" value={awaitingPharmacy} />
        <Tile icon={Pill} label="Pharmacy turnaround (ordered → dispensed)" value={avgTurnaroundMinutes !== null ? `${avgTurnaroundMinutes} min avg` : '—'} detail={dispensedTimings.length === 0 ? 'No completed dispenses yet' : `${dispensedTimings.length} order(s) measured`} />
        <Tile icon={Syringe} label="Infusions pending (dispensed, not yet started)" value={pendingInfusion} />
        <Tile icon={AlertTriangle} label="Missed / delayed treatments" value={heldOrDelayed + fractionsMissed} detail={`${heldOrDelayed} order(s) held/delayed · ${fractionsMissed} fraction(s) missed`} />
        <Tile icon={Radiation} label="Radiation fractions scheduled / delivered" value={`${fractionsDelivered} / ${fractionsScheduled}`} />
        <Tile icon={Scissors} label="Surgery pending / scheduled / completed" value={`${surgeryPending} / ${surgeryScheduled} / ${surgeryCompleted}`} />
        <Tile icon={Users} label="MDT cases pending plan creation" value={mdtPending} />
        <Tile icon={Building2} label="Day-care chairs occupied" value={inProgress} detail="Chair-capacity modeling not yet configured — shown as active infusions" />
      </div>

      <Card className="mt-6"><CardHeader className="border-b border-divider"><div className="flex items-center gap-3"><Activity className="size-4 text-brand-deep" /><CardTitle>Department workload</CardTitle></div></CardHeader>
        <CardContent className="grid gap-3 pt-6 sm:grid-cols-2 lg:grid-cols-4">
          {departmentWorkload.map((d) => { const Icon = d.icon; return <div key={d.label} className="flex items-center justify-between rounded-lg border border-divider bg-surface-elevated/70 p-3 text-sm"><span className="flex items-center gap-2 text-supporting"><Icon className="size-4 text-metadata" />{d.label}</span><Badge variant="information">{d.count}</Badge></div> })}
        </CardContent>
      </Card>

      <Card className="mt-6"><CardHeader className="border-b border-divider"><CardTitle>Treatment delays and reasons</CardTitle><CardDescription>Every recorded delay, postponement or hold with its reason</CardDescription></CardHeader>
        <CardContent className="space-y-2 pt-6">
          {delayReasons.length === 0 ? <p className="text-sm text-metadata">No delays recorded.</p> : delayReasons.map((d, i) => (
            <div key={i} className="flex items-center justify-between rounded-lg border border-divider bg-surface-elevated/70 p-3 text-sm"><span className="text-supporting">{d.reason}</span><span className="text-xs text-metadata">{new Date(d.at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}</span></div>
          ))}
        </CardContent>
      </Card>
    </PageContainer>
  )
}
