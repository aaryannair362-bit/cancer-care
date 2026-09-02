'use client'

/**
 * Isolated backend API client for the oncology module (PDF item 26 — the real thing,
 * not the localStorage placeholder it replaces).
 *
 * Deliberately separate from everything else in this app: it does not touch
 * `app/login/page.tsx`, `demo-access-provider.tsx`, or any non-oncology screen. Every
 * other role/module in this app keeps working exactly as it did before this file
 * existed. Auth here is its own thing — a role-scoped bearer token minted via the real
 * backend's passwordless `/api/auth/demo-login`, stored under its own localStorage key,
 * never shared with (or read by) the rest of the app.
 *
 * Backend integer ids are converted to strings the moment a response crosses into this
 * module's callers (see `s()` below) and converted back to numbers the moment a request
 * leaves it (see `n()`) — every dashboard type keeps declaring `id: string` and no
 * consuming page component needs to change.
 */

import type { RoleId } from '../demo-access'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'
const AUTH_STORAGE_KEY = 'aivana-onco-backend-auth'

/** Only roles with at least one oncology screen in their module list (demo-access.ts). */
export const ROLE_TO_BACKEND: Partial<Record<RoleId, string>> = {
  doctor: 'CCAMedicalOncologist',
  'surgical-oncology': 'CCASurgicalOncologist',
  'radiation-oncology': 'CCARadiationOncologist',
  pharmacy: 'CCAPharmacist',
  'infusion-nurse': 'CCAInfusionNurse',
  'mdt-coordinator': 'CCAMDTCoordinator',
  nurse: 'CCANurseNavigator',
  navigator: 'CCAPatientLiaison',
  admin: 'Admin',
}

type TokenSet = { accessToken: string; refreshToken: string }
type AuthStore = Partial<Record<RoleId, TokenSet>>

function readAuthStore(): AuthStore {
  try {
    const raw = window.localStorage.getItem(AUTH_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as AuthStore) : {}
  } catch {
    return {}
  }
}

function writeTokenSet(roleId: RoleId, tokens: TokenSet) {
  const store = readAuthStore()
  store[roleId] = tokens
  window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(store))
}

function clearTokenSet(roleId: RoleId) {
  const store = readAuthStore()
  delete store[roleId]
  window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(store))
}

async function demoLogin(backendRole: string): Promise<TokenSet> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/api/auth/demo-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: backendRole }),
    })
  } catch {
    throw new BackendError('Could not reach the treatment backend. Check that it is running.', 0)
  }
  if (!res.ok) throw new BackendError(`Could not sign in to the treatment backend as ${backendRole}`, res.status)
  const body = await res.json()
  return { accessToken: body.access_token as string, refreshToken: body.refresh_token as string }
}

async function getTokenSet(roleId: RoleId): Promise<TokenSet> {
  const backendRole = ROLE_TO_BACKEND[roleId]
  if (!backendRole) throw new BackendError(`Role "${roleId}" has no oncology backend identity`, 0)
  const existing = readAuthStore()[roleId]
  if (existing) return existing
  const minted = await demoLogin(backendRole)
  writeTokenSet(roleId, minted)
  return minted
}

export class BackendError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

/**
 * Every backend call in this module goes through here. On a 401 it discards the cached
 * token for that role and retries once with a freshly minted one (mirroring the
 * one-shot-refresh-then-redirect pattern frontend/js/api.js already uses for the other
 * frontend) — never more than once, so a genuinely broken backend surfaces as an error
 * instead of looping.
 */
async function fetchOnly(roleId: RoleId, method: string, path: string, body?: unknown): Promise<Response> {
  const { accessToken } = await getTokenSet(roleId)
  try {
    return await fetch(`${API_BASE}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    // Only a genuine transport failure (server unreachable, DNS, CORS) lands here — a bad
    // role or a failed demo-login throws its own specific BackendError from getTokenSet
    // above, before this call is even attempted, and must not be masked by this message.
    throw new BackendError('Could not reach the treatment backend. Check that it is running.', 0)
  }
}

async function request<T>(roleId: RoleId, method: string, path: string, body?: unknown): Promise<T> {
  let res = await fetchOnly(roleId, method, path, body)
  if (res.status === 401) {
    clearTokenSet(roleId)
    res = await fetchOnly(roleId, method, path, body)
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new BackendError((detail as { detail?: string }).detail ?? `Request failed (${res.status})`, res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

// ── id boundary: backend integer <-> dashboard string ──
export const s = (id: number | null | undefined): string | undefined => (id === null || id === undefined ? undefined : String(id))
export const n = (id: string | null | undefined): number | undefined => (id === null || id === undefined || id === '' ? undefined : Number(id))

// ── Demo patient resolution ──
export async function fetchDemoPatient(roleId: RoleId) {
  return request<{ status: string; patient: { id: number; mrn: string; name: string; age: number; sex: string; journey_state: string } }>(
    roleId, 'GET', '/api/cca/oncology-ext/demo-patient'
  )
}

// ── MDT ──
export async function apiCreateMdtCase(roleId: RoleId, body: { patient_id: number; question: string; priority?: string; tumor_board?: string }) {
  return request<{ mdt_case: { id: number; status: string } }>(roleId, 'POST', '/api/cca/mdt/cases', body)
}
export async function apiListMdtCases(roleId: RoleId, patientId: number) {
  return request<{ mdt_cases: Array<{ id: number; patient_id: number; question: string; priority: string; status: string }> }>(
    roleId, 'GET', `/api/cca/mdt/cases?patient_id=${patientId}`
  )
}
export async function apiRecordMdtRecommendation(roleId: RoleId, caseId: number, body: { recommendation: string; modality_direction?: string; rationale?: string; attendees?: unknown }) {
  return request<{ decision: { id: number; status: string } }>(roleId, 'POST', `/api/cca/mdt/cases/${caseId}/recommendation`, body)
}
export async function apiApproveMdtRecommendation(roleId: RoleId, caseId: number, body: { disposition: 'ACCEPT' | 'PARTIAL' | 'REJECT'; reason?: string }) {
  return request<{ status: string; case_status: string }>(roleId, 'POST', `/api/cca/mdt/cases/${caseId}/approve`, body)
}

// ── Treatment Plan ──
export type ApiTreatmentPlan = {
  id: number; patient_id: number; care_plan_id: number | null; mdt_decision_id: number | null
  intent: string; modality: string; protocol_name: string; planned_sessions: number; completed_sessions: number
  status: string; version_no: number; supersedes_id: number | null
  signer_email: string | null; signer_role: string | null; signed_at: string | null; created_by: string
}
export async function apiCreateTreatmentPlan(roleId: RoleId, body: { patient_id: number; mdt_decision_id?: number; intent?: string; modality?: string; protocol_name?: string; planned_sessions?: number; supersedes_id?: number; change_reason?: string }) {
  return request<{ treatment_plan: ApiTreatmentPlan }>(roleId, 'POST', '/api/cca/treatment-plans', body)
}
export async function apiGetPatientTreatmentPlans(roleId: RoleId, patientId: number) {
  return request<{ treatment_plans: ApiTreatmentPlan[] }>(roleId, 'GET', `/api/cca/patients/${patientId}/treatment-plans`)
}
export async function apiSignTreatmentPlan(roleId: RoleId, planId: number, reason?: string) {
  return request<{ treatment_plan: ApiTreatmentPlan }>(roleId, 'POST', `/api/cca/treatment-plans/${planId}/sign`, { reason })
}
export async function apiUpdateTreatmentPlan(roleId: RoleId, planId: number, body: { change_reason: string; protocol_name?: string; planned_sessions?: number; intent?: string }) {
  return request<{ treatment_plan: ApiTreatmentPlan }>(roleId, 'PUT', `/api/cca/treatment-plans/${planId}`, body)
}
export type ApiTreatmentPlanPhase = {
  id: number; treatment_plan_id: number; sequence: number; modality: string; label: string
  regimen_or_procedure_ref: string | null; planned_start: string | null; duration_description: string | null
  status: string; responsible_clinician_name: string | null; responsible_clinician_role: string | null
}
export async function apiListTreatmentPlanPhases(roleId: RoleId, planId: number) {
  return request<{ phases: ApiTreatmentPlanPhase[] }>(roleId, 'GET', `/api/cca/treatment-plans/${planId}/phases`)
}
export async function apiReplaceTreatmentPlanPhases(roleId: RoleId, planId: number, phases: Array<Partial<ApiTreatmentPlanPhase>>) {
  return request<{ phases: ApiTreatmentPlanPhase[] }>(roleId, 'PUT', `/api/cca/treatment-plans/${planId}/phases`, { phases })
}

// ── Treatment Order ──
export type ApiTreatmentOrder = {
  id: number; treatment_plan_id: number; treatment_session_id: number; patient_id: number
  instructions: Record<string, unknown> | null; version_no: number; status: string
  signer_email: string | null; signer_role: string | null; signed_at: string | null; created_by: string
}
export async function apiCreateTreatmentOrder(roleId: RoleId, body: { patient_id: number; treatment_plan_id: number; instructions?: Record<string, unknown> }) {
  return request<{ treatment_order: ApiTreatmentOrder }>(roleId, 'POST', '/api/cca/treatment-orders', body)
}
export async function apiGetPatientTreatmentOrders(roleId: RoleId, patientId: number) {
  return request<{ treatment_orders: ApiTreatmentOrder[] }>(roleId, 'GET', `/api/cca/patients/${patientId}/treatment-orders`)
}
export async function apiSignTreatmentOrder(roleId: RoleId, orderId: number) {
  return request<{ treatment_order: ApiTreatmentOrder }>(roleId, 'POST', `/api/cca/treatment-orders/${orderId}/sign`)
}
export async function apiCancelTreatmentOrder(roleId: RoleId, orderId: number, reason: string) {
  return request<{ treatment_order: ApiTreatmentOrder }>(roleId, 'POST', `/api/cca/treatment-orders/${orderId}/cancel`, { reason })
}

// ── Pharmacy ──
export async function apiGetPharmacyReadiness(roleId: RoleId, orderId: number, patientId: number) {
  return request<{ pharmacy_readiness: Record<string, unknown> | null; valid_statuses: string[] }>(
    roleId, 'GET', `/api/cca/treatment/${orderId}/pharmacy-readiness?patient_id=${patientId}`
  )
}
export async function apiPostPharmacyReadiness(roleId: RoleId, body: { patient_id: number; order_id: number; status?: string; notes?: string; product_verified?: boolean; expiry_checked?: boolean; second_checker_name?: string }) {
  return request<{ pharmacy_readiness: Record<string, unknown> }>(roleId, 'POST', '/api/cca/treatment/pharmacy-readiness', body)
}

// ── Medications / MAR ──
export type ApiMedicationAdministration = {
  id: number; patient_id: number; treatment_order_id: number; medication_name: string; category: string
  dose: string | null; route: string | null; sequence_no: number; status: string
  start_time: string | null; end_time: string | null; administered_by: string | null
}
export async function apiGetMedications(roleId: RoleId, orderId: number, patientId: number) {
  return request<{ results: ApiMedicationAdministration[] }>(roleId, 'GET', `/api/cca/treatment/${orderId}/medications?patient_id=${patientId}`)
}
export async function apiPostMedication(roleId: RoleId, body: { patient_id: number; order_id: number; medication_name: string; category?: string; dose?: string; route?: string; sequence_no?: number }) {
  return request<{ medication: ApiMedicationAdministration }>(roleId, 'POST', '/api/cca/treatment/medications', body)
}
export async function apiRecordMedicationEvent(roleId: RoleId, adminId: number, body: { event_type: 'START' | 'PAUSE' | 'RESUME' | 'STOP' | 'COMPLETE' | 'OMIT'; notes?: string; omission_reason?: string; actual_rate?: string; actual_volume?: string }) {
  return request<{ medication: ApiMedicationAdministration }>(roleId, 'POST', `/api/cca/treatment/medications/${adminId}/event`, body)
}

// ── Toxicity ──
export type ApiToxicityEvent = { id: number; patient_id: number; term: string; grade: number; baseline_value: string; grading_standard: string; onset_date: string | null; ongoing: boolean }
export async function apiRecordToxicity(roleId: RoleId, body: { patient_id: number; term: string; grade: number; baseline_value: string }) {
  return request<{ toxicity: ApiToxicityEvent }>(roleId, 'POST', '/api/cca/treatment/toxicity', body)
}
export async function apiListToxicityEvents(roleId: RoleId, patientId: number) {
  return request<{ toxicity_events: ApiToxicityEvent[] }>(roleId, 'GET', `/api/cca/patients/${patientId}/toxicity-events`)
}

// ── Response assessment ──
export type ApiResponseAssessment = { id: number; patient_id: number; framework: string; framework_version: string; response_category: string; confirmed: boolean; lesions: unknown; imaging_reference: string | null; recorded_by: string; recorded_at: string | null }
export async function apiRecordResponseAssessment(roleId: RoleId, body: { patient_id: number; response_category: string; confirmed?: boolean; lesions?: unknown; imaging_reference?: string }) {
  return request<{ response: ApiResponseAssessment }>(roleId, 'POST', '/api/cca/response-assessments', body)
}
export async function apiListResponseAssessments(roleId: RoleId, patientId: number) {
  return request<{ response_assessments: ApiResponseAssessment[] }>(roleId, 'GET', `/api/cca/patients/${patientId}/response-assessments`)
}

// ── Consent ──
export async function apiRecordConsent(roleId: RoleId, patientId: number, body: { consent_types: string[]; signatory: string; signatory_reason?: string }) {
  return request<{ consent: { id: number; consent_types: string[]; signatory: string; status: string; valid_from: string | null } }>(
    roleId, 'POST', `/api/cca/patients/${patientId}/consents`, body
  )
}
export async function apiListConsents(roleId: RoleId, patientId: number) {
  return request<{ consents: Array<{ id: number; consent_types: string[]; signatory: string; status: string; valid_from: string | null }> }>(
    roleId, 'GET', `/api/cca/patients/${patientId}/consents`
  )
}

// ── Radiation Oncology ──
export type ApiRadiationPrescription = Record<string, unknown> & { id: number; patient_id: number; rt_sub_status: string; number_of_fractions: number }
export async function apiCreateRadiationPrescription(roleId: RoleId, body: Record<string, unknown>) {
  return request<{ radiation_prescription: ApiRadiationPrescription }>(roleId, 'POST', '/api/cca/radiation-prescriptions', body)
}
export async function apiListRadiationPrescriptions(roleId: RoleId, patientId: number) {
  return request<{ radiation_prescriptions: ApiRadiationPrescription[] }>(roleId, 'GET', `/api/cca/patients/${patientId}/radiation-prescriptions`)
}
export async function apiTransitionRadiationPrescription(roleId: RoleId, prescriptionId: number, status: string) {
  return request<{ radiation_prescription: ApiRadiationPrescription }>(roleId, 'POST', `/api/cca/radiation-prescriptions/${prescriptionId}/transition`, { status })
}
export async function apiListRadiationFractions(roleId: RoleId, prescriptionId: number) {
  return request<{ fractions: Array<Record<string, unknown> & { id: number; fraction_number: number; status: string }> }>(
    roleId, 'GET', `/api/cca/radiation-prescriptions/${prescriptionId}/fractions`
  )
}
export async function apiRecordFractionEvent(roleId: RoleId, fractionId: number, body: { status: 'delivered' | 'missed' | 'rescheduled'; delivered_dose_gy?: number; interruption_reason?: string; on_treatment_review_note?: string }) {
  return request<{ fraction: Record<string, unknown> }>(roleId, 'POST', `/api/cca/radiation-fractions/${fractionId}/event`, body)
}
export async function apiCompleteRadiationCourse(roleId: RoleId, prescriptionId: number) {
  return request<{ radiation_prescription: ApiRadiationPrescription }>(roleId, 'POST', `/api/cca/radiation-prescriptions/${prescriptionId}/complete`)
}

// ── Surgical Oncology ──
export type ApiSurgicalPlan = Record<string, unknown> & { id: number; patient_id: number; status: string; procedure: string }
export async function apiCreateSurgicalPlan(roleId: RoleId, body: Record<string, unknown>) {
  return request<{ surgical_plan: ApiSurgicalPlan }>(roleId, 'POST', '/api/cca/surgical-plans', body)
}
export async function apiListSurgicalPlans(roleId: RoleId, patientId: number) {
  return request<{ surgical_plans: ApiSurgicalPlan[] }>(roleId, 'GET', `/api/cca/patients/${patientId}/surgical-plans`)
}
export async function apiTransitionSurgicalPlan(roleId: RoleId, planId: number, status: string) {
  return request<{ surgical_plan: ApiSurgicalPlan }>(roleId, 'PATCH', `/api/cca/surgical-plans/${planId}`, { status })
}
export async function apiRecordSurgicalOutcome(roleId: RoleId, planId: number, body: { performed_procedure: string; performed_date?: string; histopathology_summary?: string; fed_back_to_mdt_case_id?: number }) {
  return request<{ surgical_plan: ApiSurgicalPlan }>(roleId, 'POST', `/api/cca/surgical-plans/${planId}/performed`, body)
}

// ── Regimen library ──
export type ApiRegimen = { id: number; name: string; cancer_indication: string | null; drug_lines: Array<Record<string, unknown>> } & Record<string, unknown>
export async function apiCreateRegimen(roleId: RoleId, body: Record<string, unknown>) {
  return request<{ regimen: ApiRegimen }>(roleId, 'POST', '/api/cca/regimens', body)
}
export async function apiListRegimens(roleId: RoleId) {
  return request<{ regimens: ApiRegimen[] }>(roleId, 'GET', '/api/cca/regimens')
}

// ── Domain events (real, persisted audit trail — see cca_oncology_ext.py's
// list_domain_events for why this replaced the localStorage-only audit log) ──
export type ApiDomainEvent = { id: number; event_type: string; payload: Record<string, unknown> | null; created_at: string | null }
export async function apiListDomainEvents(roleId: RoleId, patientId: number) {
  return request<{ domain_events: ApiDomainEvent[] }>(roleId, 'GET', `/api/cca/patients/${patientId}/domain-events`)
}

// ── Cross-domain reads for Patient Summary (PDF item 18) ──
// These three read from the general CCA patient record (diagnosis intake, staging,
// results), not the oncology-execution tables above — the same underlying CCAPatient the
// oncology-ext demo-patient resolver attaches to, just a different slice of its record.
// Read-only: nothing here writes, and nothing here is dose- or safety-threshold data.
export async function apiGetGeneralPatientSummary(roleId: RoleId, patientId: number) {
  return request<{ blocks: Array<{ key: string; title: string; value: string; absenceState: string }> }>(
    roleId, 'GET', `/api/cca/patients/${patientId}/summary`
  )
}
export async function apiGetStagingHistory(roleId: RoleId, patientId: number) {
  return request<{ history: Array<{ id: number; stage_value: string | null; status: string; staging_system: string | null; system_version: string | null; confirmed_at: string | null; version_no: number }> }>(
    roleId, 'GET', `/api/cca/patients/${patientId}/staging`
  )
}
export async function apiListPatientResults(roleId: RoleId, patientId: number) {
  return request<{ results: Array<{ id: number; title: string; result_type: string; status: string; resulted_at: string | null }> }>(
    roleId, 'GET', `/api/cca/results?patient_id=${patientId}`
  )
}

// ── Generic record extension (supplementary fields with no backend column) ──
export async function apiGetExtension(roleId: RoleId, entityTable: string, entityId: number) {
  return request<{ extension: { payload: Record<string, unknown> | null } }>(
    roleId, 'GET', `/api/cca/oncology-ext/extension?entity_table=${entityTable}&entity_id=${entityId}`
  )
}
/** `patientId` powers the extension write's own domain event (see cca_oncology_ext.py's
 * put_record_extension) — without it, the write still persists but leaves no audit-trail
 * trace once the browser session that made it ends. Every current call site has one. */
export async function apiPutExtension(roleId: RoleId, entityTable: string, entityId: number, payload: Record<string, unknown>, patientId?: number) {
  return request<{ extension: { payload: Record<string, unknown> } }>(
    roleId, 'PUT', '/api/cca/oncology-ext/extension', { entity_table: entityTable, entity_id: entityId, payload, patient_id: patientId }
  )
}
