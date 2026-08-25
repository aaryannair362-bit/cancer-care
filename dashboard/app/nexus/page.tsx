import {
  ArrowRight,
  CheckCircle2,
  CircleDot,
  Clock3,
  FlaskConical,
  Network,
  ShieldAlert,
  Stethoscope,
  UserRound,
  Users,
} from 'lucide-react'

import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { StakeholderWorkflowPanel } from '@/components/stakeholder-workflow-panel'

type Status = 'Open' | 'In progress' | 'Awaiting review' | 'Completed'
type Priority = 'Urgent' | 'High' | 'Routine'

const tasks: Array<{ task: string; owner: string; due: string; priority: Priority; status: Status; workflow: string; action: string }> = [
  { task: 'Review latest laboratory results', owner: 'Medical Oncologist', due: 'Today · 11:30', priority: 'Urgent', status: 'Awaiting review', workflow: 'Lab → Medical Oncology', action: 'Review critical ANC and CBC trend' },
  { task: 'Review staging CT report', owner: 'Medical Oncology', due: 'Today · 14:00', priority: 'High', status: 'Open', workflow: 'Radiology → OPD', action: 'Correlate report with pathology and plan' },
  { task: 'Confirm treatment-cycle readiness', owner: 'Day Care Team', due: '24 Aug · 09:00', priority: 'High', status: 'In progress', workflow: 'Pre-treatment readiness', action: 'Complete clinician and laboratory clearance' },
  { task: 'Oncologist follow-up', owner: 'Medical Oncologist', due: '30 Aug · 10:30', priority: 'Routine', status: 'Open', workflow: 'Medical Oncology OPD', action: 'Review toxicity and cycle 3 plan' },
  { task: 'Prepare MDT case summary', owner: 'Tumour Board Coordinator', due: '27 Aug · 16:00', priority: 'Routine', status: 'In progress', workflow: 'MDT / Tumour Board', action: 'Compile pathology, imaging, and treatment summary' },
  { task: 'Patient symptom follow-up', owner: 'Oncology Nurse Navigator', due: '25 Aug · 12:00', priority: 'Routine', status: 'Open', workflow: 'Patient follow-up', action: 'Call regarding nausea, intake, and warning symptoms' },
  { task: 'Complete OPD documentation', owner: 'Medical Oncologist', due: 'Completed · 23 Aug', priority: 'Routine', status: 'Completed', workflow: 'OPD Scribe', action: 'Clinician-approved note recorded in demo' },
]

const timeline = [
  { label: 'Registration', detail: 'Identity and referral captured', status: 'Completed', icon: UserRound },
  { label: 'Nurse Intake', detail: 'Symptoms, vitals, safety checks', status: 'Completed', icon: CheckCircle2 },
  { label: 'Doctor OPD', detail: 'Assessment and clinical plan', status: 'Completed', icon: Stethoscope },
  { label: 'Diagnostics', detail: 'Lab and radiology review pending', status: 'In progress', icon: FlaskConical },
  { label: 'Treatment readiness', detail: 'Awaiting clinician clearance', status: 'Awaiting review', icon: CircleDot },
  { label: 'Follow-up / MDT', detail: 'Coordinated next review', status: 'Open', icon: Users },
]

const statusVariant = { Open: 'neutral', 'In progress': 'information', 'Awaiting review': 'warning', Completed: 'success' } as const
const priorityVariant = { Urgent: 'critical', High: 'warning', Routine: 'neutral' } as const

export default function NexusPage() {
  const openCount = tasks.filter((task) => task.status === 'Open').length
  const pendingClinical = tasks.filter((task) => task.status === 'Awaiting review').length
  const inProgress = tasks.filter((task) => task.status === 'In progress').length

  return (
    <PageContainer>
      <PageHeader title="NEXUS" description="Coordinate the next clinical and operational actions across the oncology journey." actions={<Badge variant="information">Fictional demo data</Badge>} />
      <StakeholderWorkflowPanel module="nexus" />


      <Card className="mb-6 bg-surface-clinical"><CardContent className="grid gap-5 p-5 sm:grid-cols-2 xl:grid-cols-5"><div className="flex items-center gap-3 sm:col-span-2"><span className="flex size-10 items-center justify-center rounded-full bg-surface"><UserRound className="size-5" /></span><div><p className="font-display font-semibold">Sunita Patil <span className="text-xs font-normal text-metadata">(Fictional)</span></p><p className="text-xs text-metadata">MRN DEMO-ONC-02481 · 39 years · Female</p></div></div><div><p className="text-xs uppercase tracking-wider text-metadata">Diagnosis</p><p className="mt-1 text-sm font-medium text-supporting">Stage IIA breast cancer</p></div><div><p className="text-xs uppercase tracking-wider text-metadata">Treatment</p><p className="mt-1 text-sm font-medium text-supporting">AC chemotherapy · C2D8</p></div><div><p className="text-xs uppercase tracking-wider text-metadata">Encounter</p><p className="mt-1 text-sm font-medium text-supporting">23 Aug 2026 · Coordination review</p></div></CardContent></Card>

      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        {[
          ['Current care phase', 'Adjuvant systemic therapy', 'Cycle 2 toxicity and readiness review', 'brand'],
          ['Open actions', String(openCount), 'Across clinical and coordination teams', 'neutral'],
          ['Pending clinical items', String(pendingClinical), 'Requires clinician interpretation', 'warning'],
          ['Pending diagnostics', '2', 'Laboratory and radiology review', 'information'],
          ['Upcoming coordination', String(inProgress), 'Treatment readiness and MDT pack', 'success'],
        ].map(([label, value, hint, variant]) => <Card key={label}><CardContent className="p-5"><p className="text-sm text-metadata">{label}</p><p className="mt-2 font-display text-2xl font-semibold text-foreground">{value}</p><div className="mt-2"><Badge variant={variant as 'brand' | 'neutral' | 'warning' | 'information' | 'success'}>{hint}</Badge></div></CardContent></Card>)}
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader className="border-b border-divider"><div className="flex items-start justify-between gap-3"><div><CardTitle>Coordinated actions</CardTitle><CardDescription className="mt-1">Clinical and operational work requiring clear ownership</CardDescription></div><Badge variant="brand"><Network />7 items</Badge></div></CardHeader>
          <CardContent className="grid min-w-0 gap-4 pt-6 lg:grid-cols-2">{tasks.map((task) => <article key={task.task} className={cn('flex min-w-0 flex-col rounded-lg border p-5', task.status === 'Completed' ? 'border-success/30 bg-success-subtle' : task.priority === 'Urgent' ? 'border-critical/30 bg-critical-subtle' : 'border-border bg-surface')}><div className="flex min-w-0 items-start justify-between gap-3"><div className="min-w-0 flex-1"><p className="break-words text-sm font-semibold text-supporting">{task.task}</p><p className="mt-1 break-words text-xs text-metadata">{task.workflow}</p></div><Badge className="shrink-0 whitespace-nowrap" variant={priorityVariant[task.priority]}>{task.priority}</Badge></div><dl className="mt-4 grid min-w-0 grid-cols-2 gap-3 text-xs"><div className="min-w-0"><dt className="text-metadata">Owner</dt><dd className="mt-1 break-words font-medium text-supporting">{task.owner}</dd></div><div className="min-w-0"><dt className="text-metadata">Due</dt><dd className="mt-1 break-words font-medium text-supporting">{task.due}</dd></div></dl><div className="mt-4 flex min-w-0 flex-col items-stretch gap-3 border-t border-divider pt-4 sm:flex-row sm:items-center"><Badge className="w-fit shrink-0 whitespace-nowrap" variant={statusVariant[task.status]}>{task.status}</Badge><Button type="button" variant="ghost" size="sm" className="min-w-0 flex-1 justify-between px-2 text-left sm:justify-end"><span className="min-w-0 flex-1 break-words">{task.action}</span><ArrowRight /></Button></div></article>)}</CardContent>
        </Card>

        <div className="space-y-6">
          <Card variant="elevated"><CardHeader className="border-b border-divider"><CardTitle>Assigned care team</CardTitle><CardDescription>Fictional ownership for this pathway</CardDescription></CardHeader><CardContent className="space-y-4 pt-6">{['Medical Oncologist','Nurse Navigator','Oncology Day-Care / Infusion Nurse','MDT Coordinator','Patient Liaison / Care Coordinator'].map((role) => <div key={role} className="flex items-center gap-3"><span className="flex size-8 items-center justify-center rounded-full bg-surface-elevated text-xs font-semibold text-supporting">{role.split(' ').map((part) => part[0]).slice(0,2).join('')}</span><p className="text-sm font-medium text-supporting">{role}</p></div>)}</CardContent></Card>

          <Card><CardHeader className="border-b border-divider"><CardTitle>Priority focus</CardTitle><CardDescription>Items for the next coordination huddle</CardDescription></CardHeader><CardContent className="space-y-3 pt-6"><div className="rounded-md border border-critical/30 bg-critical-subtle p-3"><p className="text-sm font-semibold text-critical-strong">Critical ANC requires review</p><p className="mt-1 text-xs text-critical-strong">Clinical interpretation remains with the treating team.</p></div><div className="rounded-md border border-warning/30 bg-warning-subtle p-3"><p className="text-sm font-semibold text-warning-strong">Treatment readiness incomplete</p><p className="mt-1 text-xs text-warning-strong">Await laboratory and clinician clearance.</p></div><div className="rounded-md border border-information/30 bg-information-subtle p-3"><p className="text-sm font-semibold text-information-strong">MDT summary preparation</p><p className="mt-1 text-xs text-information-strong">Imaging and pathology context due Thursday.</p></div></CardContent></Card>
        </div>
      </div>

      <Card className="mt-6">
        <CardHeader className="border-b border-divider"><div className="flex gap-3"><span className="flex size-9 items-center justify-center rounded-md bg-brand-soft"><Clock3 className="size-4" /></span><div><CardTitle>Care coordination timeline</CardTitle><CardDescription className="mt-1">How completed and upcoming workflows connect across this fictional oncology journey</CardDescription></div></div></CardHeader>
        <CardContent className="pt-6"><ol className="grid gap-4 lg:grid-cols-6">{timeline.map((step, index) => { const Icon = step.icon; return <li key={step.label} className="relative"><div className={cn('rounded-lg border p-4', step.status === 'Completed' ? 'border-success/30 bg-success-subtle' : step.status === 'In progress' ? 'border-information/30 bg-information-subtle' : step.status === 'Awaiting review' ? 'border-warning/30 bg-warning-subtle' : 'border-border bg-input-background')}><div className="flex items-center justify-between"><span className="flex size-8 items-center justify-center rounded-md bg-surface"><Icon className="size-4" /></span><span className="text-xs font-semibold text-metadata">{index + 1}</span></div><p className="mt-3 text-sm font-semibold text-supporting">{step.label}</p><p className="mt-1 text-xs text-metadata">{step.detail}</p><Badge className="mt-3" variant={statusVariant[step.status as Status]}>{step.status}</Badge></div></li> })}</ol><div className="mt-5 flex items-start gap-2 rounded-md border border-warning/30 bg-warning-subtle p-3 text-xs text-warning-strong"><ShieldAlert className="size-4 shrink-0" /><span>NEXUS coordinates visibility and ownership only. It does not diagnose, make autonomous clinical decisions, or authorise treatment.</span></div></CardContent>
      </Card>
    </PageContainer>
  )
}
