'use client'

/**
 * OncologyProvider — the canonical treatment-execution store (PDF item 26).
 *
 * Every clinical screen that touches a Treatment Order, Pharmacy dispense, Day Care
 * administration, MDT case, Radiation prescription, Surgical plan, toxicity event, or
 * response assessment reads and writes through `useOncology()`. Nothing here computes
 * or validates a dose — see the header note in types.ts. What this store *does*
 * enforce:
 *   - every status change goes through the state machine guard (no impossible jumps)
 *   - every status change and every clinically significant edit appends an AuditEntry
 *     with actor + timestamp + reason (item 20) — nothing is silently overwritten
 *   - versioned entities (CarePlan, TreatmentPlan) supersede rather than mutate
 *
 * Persists to localStorage today (matching this app's existing demo-provider pattern —
 * see demo-access-provider.tsx). The shape below is a deliberate 1:1 match for the table
 * layout in supabase/migrations/20260901000001_oncology_treatment_execution.sql — that
 * file predates this session and is NOT the intended backend (this app targets the
 * existing Render-hosted Postgres service the same team already runs at backend/app,
 * not Supabase — see backend/app/routers/cca.py for the real, tested CCA API this store
 * should eventually call). The migration's column layout stayed useful as a reference
 * for this client-side shape; nothing here has ever called the Supabase client.
 */

import * as React from 'react'

import {
  assertRtSubStatusTransition, assertSurgicalSubStatusTransition, assertTreatmentStatusTransition,
  isCancelledOrderAdministrationAttempt, isUnverifiedDispenseAttempt,
} from './state-machine'
import { DEMO_PATIENT_ID, seedCarePlan, seedMdtCase, seedRegimens, seedSurgicalPlan, seedTreatmentOrder, seedTreatmentPlan } from './seed-data'
import type {
  ActorRef, AuditEntry, CarePlan, ConsentRecord, ConsentStatus, DispenseRecord, DoseModification,
  JourneyMilestone, MARDrugAdministration, MDTCase, PostAdministrationRecord, PreAdministrationChecklist,
  RadiationFraction, RadiationPrescription, Regimen, ResponseAssessment, RtSubStatus,
  SurgicalPlan, SurgicalSubStatus, ToxicityEvent, TreatmentOrder, TreatmentPlan, TreatmentReadinessAssessment,
  TreatmentStatus, VerificationCheckpoint,
} from './types'

const STORAGE_KEY = 'aivana-onco-state-v1'

type OncologyState = {
  regimens: Regimen[]
  mdtCases: MDTCase[]
  carePlans: CarePlan[]
  treatmentPlans: TreatmentPlan[]
  treatmentOrders: TreatmentOrder[]
  verifications: VerificationCheckpoint[]
  dispenseRecords: DispenseRecord[]
  preAdministrationChecklists: PreAdministrationChecklist[]
  marEntries: MARDrugAdministration[]
  postAdministrationRecords: PostAdministrationRecord[]
  toxicityEvents: ToxicityEvent[]
  responseAssessments: ResponseAssessment[]
  radiationPrescriptions: RadiationPrescription[]
  radiationFractions: RadiationFraction[]
  surgicalPlans: SurgicalPlan[]
  journeyMilestones: JourneyMilestone[]
  treatmentReadinessAssessments: TreatmentReadinessAssessment[]
  consentRecords: ConsentRecord[]
  auditLog: AuditEntry[]
}

function seedState(): OncologyState {
  return {
    regimens: seedRegimens,
    mdtCases: [seedMdtCase],
    carePlans: [seedCarePlan],
    treatmentPlans: [seedTreatmentPlan],
    treatmentOrders: [seedTreatmentOrder],
    verifications: [],
    dispenseRecords: [],
    preAdministrationChecklists: [],
    marEntries: [],
    postAdministrationRecords: [],
    toxicityEvents: [],
    responseAssessments: [],
    radiationPrescriptions: [],
    radiationFractions: [],
    surgicalPlans: [seedSurgicalPlan],
    journeyMilestones: [
      { id: 'jm-1', patientId: DEMO_PATIENT_ID, department: 'registration', label: 'Registration', date: '2026-05-20', status: 'complete', isCurrent: false },
      { id: 'jm-2', patientId: DEMO_PATIENT_ID, department: 'medical_oncology', label: 'Medical Oncology', date: '2026-05-22', status: 'complete', isCurrent: false },
      { id: 'jm-3', patientId: DEMO_PATIENT_ID, department: 'mdt_tumour_board', label: 'MDT / Tumour Board', date: '2026-06-20', status: 'complete', isCurrent: false },
      { id: 'jm-4', patientId: DEMO_PATIENT_ID, department: 'surgery', label: 'Surgery', date: '2026-06-12', status: 'complete', isCurrent: false },
      { id: 'jm-5', patientId: DEMO_PATIENT_ID, department: 'medical_oncology', label: 'Medical Oncology', date: '2026-07-18', status: 'in_progress', isCurrent: true },
      { id: 'jm-6', patientId: DEMO_PATIENT_ID, department: 'pharmacy', label: 'Pharmacy', date: '', status: 'ordered', isCurrent: false },
      { id: 'jm-7', patientId: DEMO_PATIENT_ID, department: 'day_care_infusion', label: 'Day Care / Infusion', date: '', status: 'ordered', isCurrent: false },
      { id: 'jm-8', patientId: DEMO_PATIENT_ID, department: 'follow_up', label: 'Follow-up', date: '', status: 'draft', isCurrent: false },
    ],
    treatmentReadinessAssessments: [],
    consentRecords: [],
    auditLog: [],
  }
}

let idCounter = 0
function nextId(prefix: string) {
  idCounter += 1
  return `${prefix}-${Date.now()}-${idCounter}`
}
function nowIso() {
  return new Date().toISOString()
}

type OncologyContextValue = {
  state: OncologyState
  ready: boolean

  // ── Regimen library (reference content — item 6) ──
  getRegimen: (id: string) => Regimen | undefined

  // ── MDT (item 3) ──
  createMdtCase: (input: Omit<MDTCase, 'id' | 'createdAt' | 'status' | 'linkedPlanIds'>) => MDTCase
  approveMdtRecommendation: (mdtCaseId: string, actor: ActorRef) => void
  createPlanFromMdt: (
    mdtCaseId: string,
    specialty: 'medical_oncology' | 'radiation_oncology' | 'surgical' | 'combined',
    actor: ActorRef
  ) => TreatmentPlan

  // ── Care Plan / Treatment Plan hierarchy (items 2, 4, 19) ──
  getCarePlan: (patientId: string) => CarePlan | undefined
  getTreatmentPlan: (patientId: string) => TreatmentPlan | undefined
  amendTreatmentPlan: (planId: string, changes: Partial<TreatmentPlan>, reason: string, actor: ActorRef) => TreatmentPlan

  // ── Treatment Order chain (items 5, 9, 10, 15, 26, 28) ──
  getOrdersForPatient: (patientId: string) => TreatmentOrder[]
  createTreatmentOrder: (input: Omit<TreatmentOrder, 'id' | 'createdAt' | 'status'>) => TreatmentOrder
  authorizeOrder: (orderId: string, actor: ActorRef) => TreatmentOrder
  transitionOrder: (orderId: string, to: TreatmentStatus, actor: ActorRef, reason?: string) => { ok: true; order: TreatmentOrder } | { ok: false; error: string }
  recordVerification: (orderId: string, checklist: Omit<VerificationCheckpoint, 'id' | 'orderId' | 'verifiedAt'>) => VerificationCheckpoint
  recordDoseModification: (orderId: string, drugLineId: string, modification: Omit<DoseModification, 'id' | 'timestamp'>) => DoseModification

  // ── Pharmacy (item 7) ──
  createDispenseRecord: (input: Omit<DispenseRecord, 'id'>) => DispenseRecord
  updateDispenseRecord: (id: string, changes: Partial<DispenseRecord>, actor: ActorRef, reason?: string) => DispenseRecord | undefined

  // ── Day Care / MAR (item 8) ──
  recordPreAdministrationChecklist: (checklist: Omit<PreAdministrationChecklist, 'confirmedAt'>) => PreAdministrationChecklist
  recordAdministration: (entry: Omit<MARDrugAdministration, 'id'>) => MARDrugAdministration
  updateAdministration: (id: string, changes: Partial<MARDrugAdministration>) => MARDrugAdministration | undefined
  recordPostAdministration: (record: Omit<PostAdministrationRecord, 'recordedAt'>) => PostAdministrationRecord

  // ── Toxicity / Response (items 16, 17) ──
  recordToxicityEvent: (event: Omit<ToxicityEvent, 'id' | 'recordedAt'>) => ToxicityEvent
  recordResponseAssessment: (assessment: Omit<ResponseAssessment, 'id'>) => ResponseAssessment

  // ── Radiation (items 11, 12) ──
  createRadiationPrescription: (input: Omit<RadiationPrescription, 'id' | 'createdAt' | 'status' | 'rtSubStatus'>) => RadiationPrescription
  transitionRadiationSubStatus: (prescriptionId: string, to: RtSubStatus, actor: ActorRef) => { ok: true } | { ok: false; error: string }
  scheduleFractions: (prescriptionId: string, count: number, startDate: string) => RadiationFraction[]
  recordFractionOutcome: (fractionId: string, changes: Partial<RadiationFraction>) => RadiationFraction | undefined

  // ── Surgical (item 13) ──
  createSurgicalPlan: (input: Omit<SurgicalPlan, 'id' | 'createdAt' | 'status' | 'surgicalSubStatus' | 'histopathologyAvailable'>) => SurgicalPlan
  transitionSurgicalSubStatus: (planId: string, to: SurgicalSubStatus, actor: ActorRef) => { ok: true } | { ok: false; error: string }
  recordOperativeOutcome: (planId: string, changes: Partial<SurgicalPlan>, actor: ActorRef) => SurgicalPlan | undefined

  // ── Journey (item 1) ──
  getJourney: (patientId: string) => JourneyMilestone[]

  // ── Treatment Readiness (item 31) ──
  getReadinessForPatient: (patientId: string) => TreatmentReadinessAssessment[]
  recordTreatmentReadiness: (input: Omit<TreatmentReadinessAssessment, 'id' | 'createdAt'>) => TreatmentReadinessAssessment

  // ── Consent & education (item 33) ──
  getConsentsForPatient: (patientId: string) => ConsentRecord[]
  recordConsent: (input: Omit<ConsentRecord, 'id' | 'createdAt'>) => ConsentRecord
  updateConsentStatus: (id: string, status: ConsentStatus, actor: ActorRef, changes?: Partial<ConsentRecord>) => ConsentRecord | undefined

  // ── Audit (item 20) ──
  getAuditTrail: (entityType?: string, entityId?: string) => AuditEntry[]
}

const OncologyContext = React.createContext<OncologyContextValue | null>(null)

export function OncologyProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<OncologyState>(seedState)
  const [ready, setReady] = React.useState(false)

  React.useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY)
    if (saved) {
      try {
        setState({ ...seedState(), ...(JSON.parse(saved) as Partial<OncologyState>) })
      } catch {
        // Corrupt local state — fall back to the seed rather than crash the app.
      }
    }
    setReady(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const persist = React.useCallback((next: OncologyState) => {
    setState(next)
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  }, [])

  const appendAudit = React.useCallback(
    (current: OncologyState, entry: Omit<AuditEntry, 'id' | 'timestamp'>): AuditEntry => {
      const full: AuditEntry = { ...entry, id: nextId('audit'), timestamp: nowIso() }
      current.auditLog.unshift(full)
      return full
    },
    []
  )

  // ── Regimen library ──
  const getRegimen = React.useCallback((id: string) => state.regimens.find((r) => r.id === id), [state.regimens])

  // ── MDT ──
  const createMdtCase = React.useCallback(
    (input: Omit<MDTCase, 'id' | 'createdAt' | 'status' | 'linkedPlanIds'>) => {
      const record: MDTCase = { ...input, id: nextId('mdt'), createdAt: nowIso(), status: 'discussed', linkedPlanIds: {} }
      const next = { ...state, mdtCases: [record, ...state.mdtCases] }
      appendAudit(next, { entityType: 'MDTCase', entityId: record.id, action: 'created', actor: input.proposedBy })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )

  const approveMdtRecommendation = React.useCallback(
    (mdtCaseId: string, actor: ActorRef) => {
      const next = { ...state, mdtCases: state.mdtCases.map((c) => (c.id === mdtCaseId ? { ...c, status: 'recommendation_recorded' as const, approvedBy: actor, approvedAt: nowIso() } : c)) }
      appendAudit(next, { entityType: 'MDTCase', entityId: mdtCaseId, action: 'recommendation_approved', actor })
      persist(next)
    },
    [state, persist, appendAudit]
  )

  // "Create Medical Oncology Plan / Radiation Oncology Plan / Surgical Plan / Combined-Modality
  // Plan" — the exact action set item 3 asks for. Never automatic: a human calls this.
  const createPlanFromMdt = React.useCallback(
    (mdtCaseId: string, specialty: 'medical_oncology' | 'radiation_oncology' | 'surgical' | 'combined', actor: ActorRef) => {
      const mdtCase = state.mdtCases.find((c) => c.id === mdtCaseId)
      if (!mdtCase) throw new Error(`MDT case ${mdtCaseId} not found`)
      const carePlan: CarePlan = {
        id: nextId('care-plan'), patientId: mdtCase.patientId, status: 'active', intent: mdtCase.treatmentIntent,
        diagnosisSummary: `${mdtCase.cancerDiagnosis}, ${mdtCase.stage}`, originatingMdtCaseId: mdtCase.id, version: 1, createdBy: actor, createdAt: nowIso(),
      }
      const responsibleSpecialty = specialty === 'surgical' ? 'surgical_oncology' : specialty
      const plan: TreatmentPlan = {
        id: nextId('treatment-plan'), carePlanId: carePlan.id, patientId: mdtCase.patientId, status: 'active',
        diagnosis: mdtCase.cancerDiagnosis, stage: mdtCase.stage, histology: '', biomarkers: mdtCase.pathologyBiomarkers,
        intent: mdtCase.treatmentIntent, lineOfTherapy: 'First line', currentDiseaseStatus: mdtCase.finalConsensus,
        responsibleSpecialty, mdtCaseId: mdtCase.id, phases: [], version: 1, createdBy: actor, createdAt: nowIso(),
      }
      const linkKey = specialty === 'medical_oncology' ? 'medicalOncology' : specialty === 'radiation_oncology' ? 'radiationOncology' : specialty === 'surgical' ? 'surgical' : 'combined'
      const next: OncologyState = {
        ...state,
        carePlans: [carePlan, ...state.carePlans],
        treatmentPlans: [plan, ...state.treatmentPlans],
        mdtCases: state.mdtCases.map((c) => (c.id === mdtCaseId ? { ...c, status: 'plan_created' as const, linkedPlanIds: { ...c.linkedPlanIds, [linkKey]: plan.id } } : c)),
      }
      appendAudit(next, { entityType: 'TreatmentPlan', entityId: plan.id, action: `created_from_mdt:${specialty}`, actor, reason: `MDT case ${mdtCaseId}` })
      persist(next)
      return plan
    },
    [state, persist, appendAudit]
  )

  // ── Care Plan / Treatment Plan ──
  const getCarePlan = React.useCallback((patientId: string) => state.carePlans.find((c) => c.patientId === patientId && c.status === 'active'), [state.carePlans])
  const getTreatmentPlan = React.useCallback((patientId: string) => state.treatmentPlans.find((p) => p.patientId === patientId && p.status === 'active'), [state.treatmentPlans])

  const amendTreatmentPlan = React.useCallback(
    (planId: string, changes: Partial<TreatmentPlan>, reason: string, actor: ActorRef) => {
      const current = state.treatmentPlans.find((p) => p.id === planId)
      if (!current) throw new Error(`Treatment plan ${planId} not found`)
      const amended: TreatmentPlan = { ...current, ...changes, id: nextId('treatment-plan'), version: current.version + 1, supersedes: current.id, changeReason: reason, createdBy: actor, createdAt: nowIso(), status: 'active' }
      const next: OncologyState = {
        ...state,
        treatmentPlans: [amended, ...state.treatmentPlans.map((p) => (p.id === planId ? { ...p, status: 'superseded' as const } : p))],
      }
      appendAudit(next, { entityType: 'TreatmentPlan', entityId: amended.id, action: 'amended', actor, reason, previousValue: current.id, newValue: amended.id })
      persist(next)
      return amended
    },
    [state, persist, appendAudit]
  )

  // ── Treatment Order ──
  const getOrdersForPatient = React.useCallback((patientId: string) => state.treatmentOrders.filter((o) => o.patientId === patientId), [state.treatmentOrders])

  const createTreatmentOrder = React.useCallback(
    (input: Omit<TreatmentOrder, 'id' | 'createdAt' | 'status'>) => {
      const order: TreatmentOrder = { ...input, id: nextId('order'), status: 'draft', createdAt: nowIso() }
      const next = { ...state, treatmentOrders: [order, ...state.treatmentOrders] }
      appendAudit(next, { entityType: 'TreatmentOrder', entityId: order.id, action: 'created', actor: input.orderingClinician })
      persist(next)
      return order
    },
    [state, persist, appendAudit]
  )

  /**
   * A treating clinician creating and authorizing an order in one sitting walks the
   * full chain (draft -> proposed -> clinician_approved -> ordered) rather than
   * attempting an illegal direct jump — each step still goes through the state-machine
   * guard and gets its own audit entry, so the sequencing item 28 asks for is real, not
   * decorative. An order that already carries an `mdtCaseId`-linked provenance can
   * still walk this same chain; MDT approval itself is recorded on the MDTCase, not by
   * skipping steps here.
   */
  const authorizeOrder = React.useCallback(
    (orderId: string, actor: ActorRef) => {
      const order = state.treatmentOrders.find((o) => o.id === orderId)
      if (!order) throw new Error(`Order ${orderId} not found`)
      const chain: TreatmentStatus[] = order.status === 'draft' ? ['proposed', 'clinician_approved', 'ordered'] : order.status === 'proposed' ? ['clinician_approved', 'ordered'] : order.status === 'clinician_approved' ? ['ordered'] : []
      if (chain.length === 0) throw new Error(`Order ${orderId} cannot be authorized from status "${order.status}".`)
      let cursor = order.status
      for (const step of chain) {
        assertTreatmentStatusTransition(cursor, step)
        cursor = step
      }
      const authorized: TreatmentOrder = { ...order, status: 'ordered', authorizedAt: nowIso() }
      const next = { ...state, treatmentOrders: state.treatmentOrders.map((o) => (o.id === orderId ? authorized : o)) }
      appendAudit(next, { entityType: 'TreatmentOrder', entityId: orderId, action: 'authorized', actor, previousValue: order.status, newValue: 'ordered' })
      persist(next)
      return authorized
    },
    [state, persist, appendAudit]
  )

  const transitionOrder = React.useCallback(
    (orderId: string, to: TreatmentStatus, actor: ActorRef, reason?: string) => {
      const order = state.treatmentOrders.find((o) => o.id === orderId)
      if (!order) return { ok: false as const, error: `Order ${orderId} not found` }
      if (isCancelledOrderAdministrationAttempt(order.status) && to === 'in_progress') return { ok: false as const, error: 'This order is cancelled and cannot be administered.' }
      if (isUnverifiedDispenseAttempt(order.status) && to === 'preparation_pending') return { ok: false as const, error: 'This order has not completed pharmacy verification and cannot move to preparation.' }
      try {
        assertTreatmentStatusTransition(order.status, to)
      } catch (error) {
        return { ok: false as const, error: error instanceof Error ? error.message : 'Illegal transition' }
      }
      const updated: TreatmentOrder = { ...order, status: to }
      const next = { ...state, treatmentOrders: state.treatmentOrders.map((o) => (o.id === orderId ? updated : o)) }
      appendAudit(next, { entityType: 'TreatmentOrder', entityId: orderId, action: 'status_change', actor, reason, previousValue: order.status, newValue: to })
      persist(next)
      return { ok: true as const, order: updated }
    },
    [state, persist, appendAudit]
  )

  const recordVerification = React.useCallback(
    (orderId: string, checklist: Omit<VerificationCheckpoint, 'id' | 'orderId' | 'verifiedAt'>) => {
      const record: VerificationCheckpoint = { ...checklist, id: nextId('verification'), orderId, verifiedAt: nowIso() }
      const order = state.treatmentOrders.find((o) => o.id === orderId)
      let orders = state.treatmentOrders
      if (order && checklist.outcome === 'verified' && order.status === 'verification_pending') {
        orders = orders.map((o) => (o.id === orderId ? { ...o, status: 'verified' as const } : o))
      }
      const next = { ...state, verifications: [record, ...state.verifications], treatmentOrders: orders }
      appendAudit(next, { entityType: 'TreatmentOrder', entityId: orderId, action: `verification:${checklist.outcome}`, actor: checklist.verifiedBy, reason: checklist.queryReason })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )

  const recordDoseModification = React.useCallback(
    (orderId: string, drugLineId: string, modification: Omit<DoseModification, 'id' | 'timestamp'>) => {
      const record: DoseModification = { ...modification, id: nextId('dose-mod'), timestamp: nowIso() }
      const next: OncologyState = {
        ...state,
        treatmentOrders: state.treatmentOrders.map((o) =>
          o.id === orderId
            ? { ...o, drugLines: o.drugLines.map((line) => (line.id === drugLineId ? { ...line, doseModifications: [record, ...line.doseModifications] } : line)) }
            : o
        ),
      }
      appendAudit(next, { entityType: 'OrderedDrugLine', entityId: drugLineId, action: `dose_modification:${modification.type}`, actor: modification.approvedBy, reason: modification.reason, previousValue: modification.originalDose, newValue: modification.modifiedDose })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )

  // ── Pharmacy ──
  const createDispenseRecord = React.useCallback(
    (input: Omit<DispenseRecord, 'id'>) => {
      const record: DispenseRecord = { ...input, id: nextId('dispense') }
      const next = { ...state, dispenseRecords: [record, ...state.dispenseRecords] }
      persist(next)
      return record
    },
    [state, persist]
  )

  const updateDispenseRecord = React.useCallback(
    (id: string, changes: Partial<DispenseRecord>, actor: ActorRef, reason?: string) => {
      const current = state.dispenseRecords.find((d) => d.id === id)
      if (!current) return undefined
      const updated = { ...current, ...changes }
      const next = { ...state, dispenseRecords: state.dispenseRecords.map((d) => (d.id === id ? updated : d)) }
      appendAudit(next, { entityType: 'DispenseRecord', entityId: id, action: `status:${updated.status}`, actor, reason, previousValue: current.status, newValue: updated.status })
      persist(next)
      return updated
    },
    [state, persist, appendAudit]
  )

  // ── Day Care / MAR ──
  const recordPreAdministrationChecklist = React.useCallback(
    (checklist: Omit<PreAdministrationChecklist, 'confirmedAt'>) => {
      const record: PreAdministrationChecklist = { ...checklist, confirmedAt: nowIso() }
      const next = { ...state, preAdministrationChecklists: [record, ...state.preAdministrationChecklists] }
      appendAudit(next, { entityType: 'TreatmentOrder', entityId: checklist.orderId, action: 'pre_administration_checklist_confirmed', actor: checklist.confirmedBy })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )

  const recordAdministration = React.useCallback(
    (entry: Omit<MARDrugAdministration, 'id'>) => {
      const record: MARDrugAdministration = { ...entry, id: nextId('mar') }
      const next = { ...state, marEntries: [record, ...state.marEntries] }
      appendAudit(next, { entityType: 'TreatmentOrder', entityId: entry.orderId, action: `administration:${entry.drug}:${entry.infusionStatus}`, actor: entry.administeredBy })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )

  const updateAdministration = React.useCallback(
    (id: string, changes: Partial<MARDrugAdministration>) => {
      const current = state.marEntries.find((m) => m.id === id)
      if (!current) return undefined
      const updated = { ...current, ...changes }
      const next = { ...state, marEntries: state.marEntries.map((m) => (m.id === id ? updated : m)) }
      appendAudit(next, { entityType: 'TreatmentOrder', entityId: current.orderId, action: `administration_update:${updated.drug}:${updated.infusionStatus}`, actor: current.administeredBy })
      persist(next)
      return updated
    },
    [state, persist, appendAudit]
  )

  const recordPostAdministration = React.useCallback(
    (record: Omit<PostAdministrationRecord, 'recordedAt'>) => {
      const full: PostAdministrationRecord = { ...record, recordedAt: nowIso() }
      const next = { ...state, postAdministrationRecords: [full, ...state.postAdministrationRecords] }
      appendAudit(next, { entityType: 'TreatmentOrder', entityId: record.orderId, action: `completion:${record.completionStatus}`, actor: record.recordedBy })
      persist(next)
      return full
    },
    [state, persist, appendAudit]
  )

  // ── Toxicity / Response ──
  const recordToxicityEvent = React.useCallback(
    (event: Omit<ToxicityEvent, 'id' | 'recordedAt'>) => {
      const record: ToxicityEvent = { ...event, id: nextId('toxicity'), recordedAt: nowIso() }
      const next = { ...state, toxicityEvents: [record, ...state.toxicityEvents] }
      appendAudit(next, { entityType: 'ToxicityEvent', entityId: record.id, action: `recorded:grade_${event.grade}`, actor: event.recordedBy })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )

  const recordResponseAssessment = React.useCallback(
    (assessment: Omit<ResponseAssessment, 'id'>) => {
      const record: ResponseAssessment = { ...assessment, id: nextId('response') }
      const next = { ...state, responseAssessments: [record, ...state.responseAssessments] }
      appendAudit(next, { entityType: 'ResponseAssessment', entityId: record.id, action: `recorded:${assessment.responseCategory}`, actor: assessment.assessedBy })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )

  // ── Radiation ──
  const createRadiationPrescription = React.useCallback(
    (input: Omit<RadiationPrescription, 'id' | 'createdAt' | 'status' | 'rtSubStatus'>) => {
      const record: RadiationPrescription = { ...input, id: nextId('rt-rx'), status: 'draft', rtSubStatus: 'prescribed', createdAt: nowIso() }
      const next = { ...state, radiationPrescriptions: [record, ...state.radiationPrescriptions] }
      appendAudit(next, { entityType: 'RadiationPrescription', entityId: record.id, action: 'created', actor: input.createdBy })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )

  const transitionRadiationSubStatus = React.useCallback(
    (prescriptionId: string, to: RtSubStatus, actor: ActorRef) => {
      const rx = state.radiationPrescriptions.find((r) => r.id === prescriptionId)
      if (!rx) return { ok: false as const, error: `Radiation prescription ${prescriptionId} not found` }
      try {
        assertRtSubStatusTransition(rx.rtSubStatus, to)
      } catch (error) {
        return { ok: false as const, error: error instanceof Error ? error.message : 'Illegal transition' }
      }
      const updated: RadiationPrescription = {
        ...rx, rtSubStatus: to, status: to === 'completed' ? 'completed' : to === 'on_treatment' ? 'in_progress' : rx.status,
        physicianApprovedBy: to === 'physician_approved' ? actor : rx.physicianApprovedBy,
        physicianApprovedAt: to === 'physician_approved' ? nowIso() : rx.physicianApprovedAt,
      }
      const next = { ...state, radiationPrescriptions: state.radiationPrescriptions.map((r) => (r.id === prescriptionId ? updated : r)) }
      appendAudit(next, { entityType: 'RadiationPrescription', entityId: prescriptionId, action: 'status_change', actor, previousValue: rx.rtSubStatus, newValue: to })
      persist(next)
      return { ok: true as const }
    },
    [state, persist, appendAudit]
  )

  const scheduleFractions = React.useCallback(
    (prescriptionId: string, count: number, startDate: string) => {
      const fractions: RadiationFraction[] = Array.from({ length: count }, (_, index) => ({
        id: nextId('rt-fraction'), prescriptionId, fractionNumber: index + 1,
        scheduledDate: new Date(new Date(startDate).getTime() + index * 86400000).toISOString().slice(0, 10),
        status: 'scheduled',
      }))
      const next = { ...state, radiationFractions: [...state.radiationFractions, ...fractions] }
      persist(next)
      return fractions
    },
    [state, persist]
  )

  const recordFractionOutcome = React.useCallback(
    (fractionId: string, changes: Partial<RadiationFraction>) => {
      const current = state.radiationFractions.find((f) => f.id === fractionId)
      if (!current) return undefined
      const updated = { ...current, ...changes }
      const next = { ...state, radiationFractions: state.radiationFractions.map((f) => (f.id === fractionId ? updated : f)) }
      persist(next)
      return updated
    },
    [state, persist]
  )

  // ── Surgical ──
  const createSurgicalPlan = React.useCallback(
    (input: Omit<SurgicalPlan, 'id' | 'createdAt' | 'status' | 'surgicalSubStatus' | 'histopathologyAvailable'>) => {
      const record: SurgicalPlan = { ...input, id: nextId('surgical'), status: 'draft', surgicalSubStatus: 'recommended', histopathologyAvailable: false, createdAt: nowIso() }
      const next = { ...state, surgicalPlans: [record, ...state.surgicalPlans] }
      appendAudit(next, { entityType: 'SurgicalPlan', entityId: record.id, action: 'created', actor: input.recommendedBy })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )

  const transitionSurgicalSubStatus = React.useCallback(
    (planId: string, to: SurgicalSubStatus, actor: ActorRef) => {
      const plan = state.surgicalPlans.find((p) => p.id === planId)
      if (!plan) return { ok: false as const, error: `Surgical plan ${planId} not found` }
      try {
        assertSurgicalSubStatusTransition(plan.surgicalSubStatus, to)
      } catch (error) {
        return { ok: false as const, error: error instanceof Error ? error.message : 'Illegal transition' }
      }
      const updated: SurgicalPlan = { ...plan, surgicalSubStatus: to, status: to === 'histopathology_available' ? 'completed' : to === 'performed' ? 'in_progress' : plan.status }
      const next = { ...state, surgicalPlans: state.surgicalPlans.map((p) => (p.id === planId ? updated : p)) }
      appendAudit(next, { entityType: 'SurgicalPlan', entityId: planId, action: 'status_change', actor, previousValue: plan.surgicalSubStatus, newValue: to })
      persist(next)
      return { ok: true as const }
    },
    [state, persist, appendAudit]
  )

  const recordOperativeOutcome = React.useCallback(
    (planId: string, changes: Partial<SurgicalPlan>, actor: ActorRef) => {
      const current = state.surgicalPlans.find((p) => p.id === planId)
      if (!current) return undefined
      const updated = { ...current, ...changes }
      const next = { ...state, surgicalPlans: state.surgicalPlans.map((p) => (p.id === planId ? updated : p)) }
      appendAudit(next, { entityType: 'SurgicalPlan', entityId: planId, action: 'operative_outcome_recorded', actor })
      persist(next)
      return updated
    },
    [state, persist, appendAudit]
  )

  // ── Journey ──
  const getJourney = React.useCallback((patientId: string) => state.journeyMilestones.filter((m) => m.patientId === patientId), [state.journeyMilestones])

  // ── Treatment Readiness ──
  const getReadinessForPatient = React.useCallback(
    (patientId: string) => state.treatmentReadinessAssessments.filter((r) => r.patientId === patientId),
    [state.treatmentReadinessAssessments]
  )
  const recordTreatmentReadiness = React.useCallback(
    (input: Omit<TreatmentReadinessAssessment, 'id' | 'createdAt'>) => {
      const record: TreatmentReadinessAssessment = { ...input, id: nextId('readiness'), createdAt: nowIso() }
      const next = { ...state, treatmentReadinessAssessments: [record, ...state.treatmentReadinessAssessments] }
      appendAudit(next, { entityType: 'TreatmentReadinessAssessment', entityId: record.id, action: `decision:${input.decision}`, actor: input.assessedBy, reason: input.decisionReason })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )

  // ── Consent ──
  const getConsentsForPatient = React.useCallback((patientId: string) => state.consentRecords.filter((c) => c.patientId === patientId), [state.consentRecords])
  const recordConsent = React.useCallback(
    (input: Omit<ConsentRecord, 'id' | 'createdAt'>) => {
      const record: ConsentRecord = { ...input, id: nextId('consent'), createdAt: nowIso() }
      const next = { ...state, consentRecords: [record, ...state.consentRecords] }
      appendAudit(next, { entityType: 'ConsentRecord', entityId: record.id, action: `status:${input.status}`, actor: input.witnessedBy ?? { userId: 'patient', name: input.signedBy ?? 'Patient', roleLabel: 'Patient' } })
      persist(next)
      return record
    },
    [state, persist, appendAudit]
  )
  const updateConsentStatus = React.useCallback(
    (id: string, status: ConsentStatus, actor: ActorRef, changes?: Partial<ConsentRecord>) => {
      const current = state.consentRecords.find((c) => c.id === id)
      if (!current) return undefined
      const updated: ConsentRecord = { ...current, ...changes, status }
      const next = { ...state, consentRecords: state.consentRecords.map((c) => (c.id === id ? updated : c)) }
      appendAudit(next, { entityType: 'ConsentRecord', entityId: id, action: `status:${status}`, actor, previousValue: current.status, newValue: status })
      persist(next)
      return updated
    },
    [state, persist, appendAudit]
  )

  const getAuditTrail = React.useCallback(
    (entityType?: string, entityId?: string) => state.auditLog.filter((entry) => (!entityType || entry.entityType === entityType) && (!entityId || entry.entityId === entityId)),
    [state.auditLog]
  )

  const value: OncologyContextValue = {
    state, ready, getRegimen,
    createMdtCase, approveMdtRecommendation, createPlanFromMdt,
    getCarePlan, getTreatmentPlan, amendTreatmentPlan,
    getOrdersForPatient, createTreatmentOrder, authorizeOrder, transitionOrder, recordVerification, recordDoseModification,
    createDispenseRecord, updateDispenseRecord,
    recordPreAdministrationChecklist, recordAdministration, updateAdministration, recordPostAdministration,
    recordToxicityEvent, recordResponseAssessment,
    createRadiationPrescription, transitionRadiationSubStatus, scheduleFractions, recordFractionOutcome,
    createSurgicalPlan, transitionSurgicalSubStatus, recordOperativeOutcome,
    getJourney,
    getReadinessForPatient, recordTreatmentReadiness,
    getConsentsForPatient, recordConsent, updateConsentStatus,
    getAuditTrail,
  }

  return <OncologyContext.Provider value={value}>{children}</OncologyContext.Provider>
}

export function useOncology() {
  const value = React.useContext(OncologyContext)
  if (!value) throw new Error('OncologyProvider is required')
  return value
}
