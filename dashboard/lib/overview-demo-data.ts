import {
  ClipboardCheck,
  FileText,
  Scan,
  UserPlus,
  Users,
  FlaskConical,
  type LucideIcon,
} from 'lucide-react'

/**
 * OS Overview demo data.
 * ALL data below is fictional and for demonstration only — names, MRNs and
 * values do not represent real patients.
 */

export type Tone = 'default' | 'critical' | 'warning' | 'success'

export interface Metric {
  id: string
  label: string
  value: string
  hint: string
  tone?: Tone
}

export const metrics: Metric[] = [
  { id: 'opd', label: 'Patients in OPD today', value: '148', hint: '+12 vs 7-day avg' },
  { id: 'intake', label: 'Awaiting nurse intake', value: '9', hint: 'avg wait 18 min', tone: 'warning' },
  { id: 'diagnostics', label: 'Diagnostics pending', value: '31', hint: 'Lab 19 · Radiology 12' },
  { id: 'mdt', label: 'MDT cases this week', value: '7', hint: '3 for Thursday board' },
  { id: 'chairs', label: 'Chemo chairs in use', value: '22 / 28', hint: '79% utilisation' },
  { id: 'alerts', label: 'Critical alerts', value: '3', hint: 'needs review', tone: 'critical' },
]

export interface StatusDatum {
  key: string
  label: string
  count: number
  bar: string
  text: string
}

export const todayActivity: { total: number; statuses: StatusDatum[] } = {
  total: 148,
  statuses: [
    { key: 'completed', label: 'Completed', count: 88, bar: 'bg-success', text: 'text-success-strong' },
    { key: 'in-progress', label: 'In progress', count: 14, bar: 'bg-information', text: 'text-information-strong' },
    { key: 'upcoming', label: 'Upcoming', count: 39, bar: 'bg-brand-strong', text: 'text-supporting' },
    { key: 'missed', label: 'Missed', count: 5, bar: 'bg-critical', text: 'text-critical-strong' },
    { key: 'cancelled', label: 'Cancelled', count: 2, bar: 'bg-emphasized', text: 'text-metadata' },
  ],
}

export type Priority = 'urgent' | 'high' | 'routine' | 'ai'

export interface ActionItem {
  id: string
  title: string
  module: string
  meta: string
  priority: Priority
}

export const pendingActions: ActionItem[] = [
  { id: 'a1', title: 'Review 5 abnormal lab flags', module: 'Lab', meta: '2 critical', priority: 'urgent' },
  { id: 'a2', title: 'Verify staging for 6 new OPD cases', module: 'Doctor OPD', meta: 'due today', priority: 'high' },
  { id: 'a3', title: 'Sign off 4 OPD Scribe notes', module: 'OPD Scribe', meta: 'AI-drafted', priority: 'ai' },
  { id: 'a4', title: 'Confirm 3 MDT case packs', module: 'MDT / Tumour Board', meta: 'board Thu 08:00', priority: 'high' },
  { id: 'a5', title: 'Complete financial counselling', module: 'Conversion / Finance', meta: '2 patients', priority: 'routine' },
  { id: 'a6', title: 'Follow up insurance pre-authorisation', module: 'Conversion / Finance', meta: '4 pending', priority: 'routine' },
]

export type Severity = 'critical' | 'warning' | 'information'

export interface AlertItem {
  id: string
  severity: Severity
  title: string
  detail: string
  time: string
}

export const alerts: AlertItem[] = [
  { id: 'al1', severity: 'critical', title: 'Critical K⁺ 6.2 mmol/L', detail: 'R. Mehta · MRN AIV-24-0912 · Lab', time: '8m ago' },
  { id: 'al2', severity: 'critical', title: 'Neutropenic fever flagged', detail: 'S. Banerjee · MRN AIV-24-0731 · Doctor OPD', time: '21m ago' },
  { id: 'al3', severity: 'warning', title: 'Biopsy result overdue 48h', detail: 'A. Nair · MRN AIV-24-0688 · Radiology', time: '1h ago' },
  { id: 'al4', severity: 'warning', title: 'Chemo chair capacity 90%', detail: 'Day care · 14:00–16:00 slot', time: 'now' },
  { id: 'al5', severity: 'information', title: '3 insurance pre-auths pending', detail: 'Conversion / Finance', time: '2h ago' },
]

export interface ActivityItem {
  id: string
  icon: LucideIcon
  action: string
  subject: string
  module: string
  time: string
}

export const recentActivity: ActivityItem[] = [
  { id: 'r1', icon: ClipboardCheck, action: 'Nurse intake completed', subject: 'Priya Sharma', module: 'Nurse Intake', time: '2m' },
  { id: 'r2', icon: FileText, action: 'OPD note finalised', subject: 'Medical Oncologist · NSCLC review', module: 'Doctor OPD', time: '6m' },
  { id: 'r3', icon: Scan, action: 'CT chest reported', subject: 'Vikram Rao', module: 'Radiology', time: '12m' },
  { id: 'r4', icon: UserPlus, action: 'Patient registered', subject: 'Meera Iyer', module: 'Registration', time: '15m' },
  { id: 'r5', icon: Users, action: 'MDT decision recorded', subject: 'Case AIV-MDT-114 · Ca breast', module: 'MDT / Tumour Board', time: '23m' },
  { id: 'r6', icon: FlaskConical, action: 'Lab panel resulted', subject: 'CBC · Rahul Mehta', module: 'Lab', time: '31m' },
]

export interface AiItem {
  id: string
  title: string
  detail: string
  confidence: string
}

export const aiActivity: AiItem[] = [
  { id: 'ai1', title: 'OPD Scribe drafted 7 encounter notes', detail: '4 awaiting clinician review', confidence: 'High confidence' },
  { id: 'ai2', title: 'NEXUS matched 2 patients to open trials', detail: 'Needs oncologist review', confidence: 'Medium confidence' },
  { id: 'ai3', title: '12 pathology reports auto-summarised', detail: 'Provenance linked to source', confidence: 'High confidence' },
]
