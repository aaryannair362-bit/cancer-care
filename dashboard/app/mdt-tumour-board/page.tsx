'use client'

import { AlertTriangle, CheckCircle2, ClipboardCheck, FileText, FlaskConical, Scan, ShieldAlert, Stethoscope, UserRound, Users } from 'lucide-react'
import { useDemoAccess } from '@/components/demo-access-provider'
import { MdtCoordinatorWorkspace } from '@/components/mdt-coordinator-workspace'
import { ExternalMdtSpecialistWorkspace } from '@/components/external-mdt-specialist-workspace'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { StakeholderWorkflowPanel } from '@/components/stakeholder-workflow-panel'
import { useOncology } from '@/lib/oncology/store'
import { hasPermission } from '@/lib/demo-access'
import type { ActorRef } from '@/lib/oncology/types'

const team = [
  ['Medical Oncology', 'Medical Oncologist', 'Confirmed'], ['Surgical Oncology', 'Surgical Oncologist', 'Confirmed'],
  ['Radiation Oncology', 'Radiation Oncologist', 'Awaiting review'], ['Radiology', 'Radiologist', 'Report reviewed'],
  ['Pathology', 'Pathologist / Molecular Diagnostics', 'Confirmed'], ['Nursing / Care Coordination', 'Nurse Navigator', 'Confirmed'],
]
const reviewSections = [
  { title: 'Pathology', icon: FileText, text: 'Invasive ductal carcinoma, 24 mm, grade 2. ER 90% positive, PR 70% positive, HER2 IHC 1+ (negative), Ki-67 22%. Margins clear; sentinel nodes 0/3.', status: 'Reviewed' },
  { title: 'Imaging', icon: Scan, text: 'Fictional CT chest/abdomen/pelvis shows post-operative change without reported distant metastatic disease. Targeted ultrasound shows a small uncomplicated axillary seroma.', status: 'Awaiting MDT' },
  { title: 'Treatment history', icon: Stethoscope, text: 'Left breast lumpectomy and sentinel node biopsy in June 2026. Adjuvant doxorubicin/cyclophosphamide underway; currently cycle 2, day 8.', status: 'Current' },
  { title: 'Clinical status', icon: FlaskConical, text: 'ECOG 1 with grade 1 fatigue and nausea. Fictional CBC shows ANC 0.7 ×10⁹/L and haemoglobin 9.4 g/dL, requiring clinician-led interpretation.', status: 'Needs review' },
]
const workflow = [
  ['Case prepared', 'Completed'], ['Pathology reviewed', 'Completed'], ['Imaging review', 'Awaiting review'],
  ['MDT scheduled', '29 Aug · 08:00'], ['Discussion completed', 'Pending'], ['Follow-up actions', 'Pending'],
]
const actions = [
  ['Communicate MDT outcome', 'Oncology Nurse Navigator', 'After MDT', 'Open'],
  ['Update clinician-authored treatment plan', 'Medical Oncology', 'Within 1 working day', 'Open'],
  ['Schedule oncology follow-up', 'NEXUS Coordination', '30 Aug 2026', 'In progress'],
  ['Obtain additional pathology review if requested', 'Pathology Coordinator', 'MDT dependent', 'Awaiting review'],
]

export default function MdtTumourBoardPage() {
  const { role, selectedPatient } = useDemoAccess()
  const { state, createPlanFromMdt } = useOncology()
  if (role.roleId === 'mdt-coordinator') return <MdtCoordinatorWorkspace />
  if (role.roleId === 'mdt-clinician') return <ExternalMdtSpecialistWorkspace />

  const mdtCase = state.mdtCases.find((c) => c.patientId === selectedPatient.id)
  const actor: ActorRef = { userId: role.roleId, name: role.label, roleLabel: role.label }
  const canCreatePlan = hasPermission(role, 'clinical:approve')

  const planActions: { key: 'medical_oncology' | 'radiation_oncology' | 'surgical' | 'combined'; label: string; linked?: string }[] = mdtCase ? [
    { key: 'medical_oncology', label: 'Create Medical Oncology Plan', linked: mdtCase.linkedPlanIds.medicalOncology },
    { key: 'radiation_oncology', label: 'Create Radiation Oncology Plan', linked: mdtCase.linkedPlanIds.radiationOncology },
    { key: 'surgical', label: 'Create Surgical Plan', linked: mdtCase.linkedPlanIds.surgical },
    { key: 'combined', label: 'Combined-Modality Plan', linked: mdtCase.linkedPlanIds.combined },
  ] : []
  return <PageContainer>
    <PageHeader title="MDT / Tumour Board" description="Prepare and coordinate structured multidisciplinary oncology review." actions={<Badge variant="information">Fictional demo data</Badge>} />
    <StakeholderWorkflowPanel module="mdt" />
    <div className="mb-6 flex items-start gap-3 rounded-lg border border-information/30 bg-information-subtle px-4 py-3 text-information-strong"><ShieldAlert className="mt-0.5 size-4 shrink-0" /><div><p className="text-sm font-medium">Clinician-led demonstration workspace</p><p className="mt-0.5 text-xs">All data is fictional. This prototype has no EHR, HIS, PACS, or LIS connection, makes no autonomous decision, and executes no clinical recommendation.</p></div></div>
    <Card className="mb-6 bg-surface-clinical"><CardContent className="grid gap-5 p-5 sm:grid-cols-2 xl:grid-cols-5"><div className="flex items-center gap-3 sm:col-span-2"><span className="flex size-10 items-center justify-center rounded-full bg-surface"><UserRound className="size-5" /></span><div><p className="font-display font-semibold">Sunita Patil <span className="text-xs font-normal text-metadata">(Fictional)</span></p><p className="text-xs text-metadata">MRN DEMO-ONC-02481 · Case MDT-DEMO-118</p></div></div><div><p className="text-xs uppercase tracking-wider text-metadata">Diagnosis</p><p className="mt-1 text-sm font-medium text-supporting">Stage IIA breast cancer</p></div><div><p className="text-xs uppercase tracking-wider text-metadata">Current treatment</p><p className="mt-1 text-sm font-medium text-supporting">AC chemotherapy · C2D8</p></div><div><p className="text-xs uppercase tracking-wider text-metadata">Case status</p><Badge className="mt-1" variant="warning">Awaiting MDT review</Badge></div></CardContent></Card>

    <div className="mb-6 grid gap-6 xl:grid-cols-3">
      <Card className="xl:col-span-2"><CardHeader className="border-b border-divider"><CardTitle>MDT case summary</CardTitle><CardDescription>Prepared context for multidisciplinary review</CardDescription></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{[
        ['Diagnosis','Left breast invasive ductal carcinoma, hormone-receptor positive, HER2 negative'],['Stage','pT2N0M0 · Stage IIA'],
        ['Treatment','Lumpectomy completed; adjuvant AC chemotherapy cycle 2'],['Pathology','Grade 2, ER/PR positive, HER2 negative, clear margins, nodes 0/3'],
        ['Imaging','No fictional CT evidence of distant metastatic disease'],['Laboratory context','Treatment-associated cytopenias including critical fictional ANC'],
      ].map(([label,value]) => <div key={label} className="rounded-md border border-border bg-input-background p-4"><p className="text-xs uppercase tracking-wider text-metadata">{label}</p><p className="mt-2 text-sm font-medium leading-6 text-supporting">{value}</p></div>)}<div className="rounded-md border border-brand-soft bg-surface-clinical p-4 sm:col-span-2"><p className="text-xs uppercase tracking-wider text-metadata">Clinical question for MDT</p><p className="mt-2 text-sm font-semibold leading-6 text-supporting">Confirm multidisciplinary consensus on sequencing of adjuvant systemic therapy and radiotherapy planning, considering current toxicity, pathology risk features, and fictional staging imaging.</p></div></CardContent></Card>
      <Card><CardHeader className="border-b border-divider"><CardTitle>Workflow status</CardTitle><CardDescription>Case preparation and review gates</CardDescription></CardHeader><CardContent className="space-y-3 pt-6">{workflow.map(([label,status],index) => <div key={label} className="flex items-center gap-3"><span className={cn('flex size-7 items-center justify-center rounded-full', status === 'Completed' ? 'bg-success-subtle text-success-strong' : 'bg-surface-elevated text-metadata')}>{status === 'Completed' ? <CheckCircle2 className="size-4" /> : <span className="text-xs font-semibold">{index + 1}</span>}</span><div className="min-w-0 flex-1"><p className="text-sm font-medium text-supporting">{label}</p><p className="text-xs text-metadata">{status}</p></div></div>)}</CardContent></Card>
    </div>

    <Card className="mb-6"><CardHeader className="border-b border-divider"><div className="flex items-start justify-between gap-3"><div><CardTitle>Multidisciplinary team</CardTitle><CardDescription className="mt-1">Fictional attendees and reviewer readiness</CardDescription></div><Badge variant="brand"><Users />6 disciplines</Badge></div></CardHeader><CardContent className="grid gap-3 pt-6 sm:grid-cols-2 lg:grid-cols-3">{team.map(([role,name,status]) => <div key={role} className="rounded-md border border-border bg-input-background p-4"><div className="flex items-start justify-between gap-2"><div><p className="text-sm font-semibold text-supporting">{role}</p><p className="mt-1 text-xs text-metadata">{name}</p></div><Badge variant={status.includes('Awaiting') ? 'warning' : 'success'}>{status}</Badge></div></div>)}</CardContent></Card>

    <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{reviewSections.map((section) => { const Icon = section.icon; return <Card key={section.title}><CardHeader className="border-b border-divider pb-4"><div className="flex items-center justify-between gap-3"><span className="flex items-center gap-2"><Icon className="size-4 text-metadata" /><CardTitle className="text-base">{section.title}</CardTitle></span><Badge variant={section.status.includes('Needs') || section.status.includes('Awaiting') ? 'warning' : 'success'}>{section.status}</Badge></div></CardHeader><CardContent className="pt-5"><p className="text-sm leading-6 text-supporting">{section.text}</p></CardContent></Card> })}</div>

    {mdtCase ? (
      <Card variant="ai" className="mb-6">
        <CardHeader className="border-b border-ai-highlight"><div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>MDT recommendation — the record, not a summary</CardTitle><CardDescription className="mt-1">Every field below is discrete and stored; this is the record the whole downstream treatment chain traces back to</CardDescription></div><Badge variant={mdtCase.status === 'plan_created' ? 'success' : mdtCase.status === 'recommendation_recorded' ? 'information' : 'warning'}>{mdtCase.status.replace(/_/g, ' ')}</Badge></div></CardHeader>
        <CardContent className="space-y-6 pt-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ['Cancer diagnosis', mdtCase.cancerDiagnosis], ['Stage', mdtCase.stage], ['Performance status', mdtCase.performanceStatus],
              ['Biomarkers', mdtCase.pathologyBiomarkers.join(', ')], ['Treatment intent', mdtCase.treatmentIntent], ['Specialty responsible', mdtCase.specialtyResponsible.replace(/_/g, ' ')],
              ['MDT date', mdtCase.mdtDate],
            ].map(([label, value]) => <div key={label}><p className="text-xs uppercase tracking-wider text-metadata">{label}</p><p className="mt-1 text-sm font-medium text-supporting">{value}</p></div>)}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <div><p className="text-xs uppercase tracking-wider text-metadata">MDT recommendation</p><p className="mt-1 text-sm leading-6 text-supporting">{mdtCase.recommendation}</p></div>
            <div><p className="text-xs uppercase tracking-wider text-metadata">Alternative options discussed</p><p className="mt-1 text-sm leading-6 text-supporting">{mdtCase.alternativeOptionsDiscussed}</p></div>
            <div><p className="text-xs uppercase tracking-wider text-metadata">Rationale</p><p className="mt-1 text-sm leading-6 text-supporting">{mdtCase.rationale}</p></div>
            <div><p className="text-xs uppercase tracking-wider text-metadata">Final consensus</p><p className="mt-1 text-sm leading-6 text-supporting">{mdtCase.finalConsensus}</p></div>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wider text-metadata">Participants</p>
            <div className="mt-2 flex flex-wrap gap-2">{mdtCase.participants.map((p) => <Badge key={p.userId} variant="neutral">{p.name} · {p.specialty} ({p.attendance})</Badge>)}</div>
          </div>
          <div className="flex flex-wrap gap-4 border-t border-divider pt-4 text-xs text-metadata">
            <span>Proposed by {mdtCase.proposedBy.name}</span>
            {mdtCase.approvedBy ? <span>Approved by {mdtCase.approvedBy.name} · {new Date(mdtCase.approvedAt ?? '').toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}</span> : <span>Awaiting approval</span>}
          </div>

          <div className="border-t border-divider pt-4">
            <p className="text-sm font-semibold text-supporting">Turn this recommendation into an executable plan</p>
            <p className="mt-1 text-xs leading-5 text-metadata">This never happens automatically — the appropriate oncologist reviews and authorizes each plan below.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {planActions.map((action) => action.linked ? (
                <Badge key={action.key} variant="success"><CheckCircle2 />{action.label.replace('Create ', '')} created</Badge>
              ) : (
                <Button key={action.key} type="button" size="sm" variant="outline" disabled={!canCreatePlan} onClick={() => createPlanFromMdt(mdtCase.id, action.key, actor)}><ClipboardCheck />{action.label}</Button>
              ))}
            </div>
          </div>

          <div className="flex gap-2 rounded-md border border-warning/30 bg-warning-subtle p-3 text-xs text-warning-strong"><AlertTriangle className="size-4 shrink-0" /><span>An MDT recommendation never becomes an active treatment order by itself. Creating a plan above still requires the Medical Oncology / Radiation Oncology / Surgical Oncology order screens to be authorized separately.</span></div>
        </CardContent>
      </Card>
    ) : null}

    <Card><CardHeader className="border-b border-divider"><div className="flex items-start justify-between gap-3"><div><CardTitle>Follow-up actions</CardTitle><CardDescription className="mt-1">Fictional actions coordinated back through NEXUS after MDT</CardDescription></div><Badge variant="information">NEXUS linked</Badge></div></CardHeader><CardContent className="grid gap-4 pt-6 sm:grid-cols-2">{actions.map(([task,owner,due,status]) => <div key={task} className="rounded-lg border border-border p-4"><div className="flex items-start justify-between gap-3"><p className="text-sm font-semibold text-supporting">{task}</p><Badge variant={status === 'In progress' ? 'information' : status === 'Awaiting review' ? 'warning' : 'neutral'}>{status}</Badge></div><div className="mt-4 grid grid-cols-2 gap-3 text-xs"><div><p className="text-metadata">Owner</p><p className="mt-1 font-medium text-supporting">{owner}</p></div><div><p className="text-metadata">Due</p><p className="mt-1 font-medium text-supporting">{due}</p></div></div><div className="mt-4 flex justify-end border-t border-divider pt-3"><Button type="button" variant="ghost" size="sm">View in NEXUS</Button></div></div>)}</CardContent></Card>
  </PageContainer>
}
