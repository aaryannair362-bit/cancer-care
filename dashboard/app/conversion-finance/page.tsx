'use client'

import * as React from 'react'
import { AlertTriangle, ArrowRight, BadgeIndianRupee, CheckCircle2, ChevronRight, CircleDollarSign, Clock3, UserRound, WalletCards } from 'lucide-react'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { StakeholderWorkflowPanel } from '@/components/stakeholder-workflow-panel'
import { useDemoAccess } from '@/components/demo-access-provider'
import { FinancialCounsellingHandoff } from '@/components/care-coordination-workspace'
import { FinancialCounsellingWorkspace } from '@/components/financial-services-workspace'

type Status = 'Open' | 'In progress' | 'Awaiting response' | 'Ready' | 'Completed'
type Priority = 'Urgent' | 'High' | 'Routine'
type Workflow = { id: string; task: string; owner: string; status: Status; priority: Priority; due: string; next: string; context: string; delay: string; escalation: string }

const workflows: Workflow[] = [
  { id: 'payer', task: 'Insurance / payer verification', owner: 'Financial Navigation', status: 'Awaiting response', priority: 'High', due: '24 Aug 2026', next: 'Follow up on fictional eligibility query', context: 'Sunita Patil - adjuvant AC chemotherapy episode', delay: 'Demo payer response remains outstanding', escalation: 'Review at the next access huddle if unresolved' },
  { id: 'authorization', task: 'Treatment authorization', owner: 'Authorization Desk', status: 'In progress', priority: 'Urgent', due: 'Today, 15:00', next: 'Confirm required fictional supporting documents', context: 'Cycle 3 systemic treatment planning', delay: 'Clinical summary marked incomplete in this prototype', escalation: 'Escalated to oncology access lead' },
  { id: 'counselling', task: 'Financial counselling', owner: 'Patient Financial Counsellor', status: 'Open', priority: 'Routine', due: '26 Aug 2026', next: 'Schedule a fictional counselling conversation', context: 'Estimated pathway costs and support discussion', delay: 'Patient contact window not yet confirmed', escalation: 'No escalation' },
  { id: 'clearance', task: 'Treatment-cycle clearance', owner: 'Day Care Coordination', status: 'Awaiting response', priority: 'Urgent', due: 'Today, 17:00', next: 'Await clinician-led laboratory review', context: 'Cycle 3 treatment readiness checkpoint', delay: 'Fictional laboratory results require clinical review', escalation: 'Visible to medical oncology; no automated clearance' },
  { id: 'deposit', task: 'Pending payment / deposit', owner: 'Patient Access', status: 'Open', priority: 'High', due: '27 Aug 2026', next: 'Explain fictional deposit workflow and assistance options', context: 'Day-care scheduling prerequisite demonstration', delay: 'No payment is requested or processed in this prototype', escalation: 'Financial navigator review if support is requested' },
  { id: 'referral', task: 'Conversion / referral follow-up', owner: 'Referral Coordination', status: 'Completed', priority: 'Routine', due: 'Completed 18 Jun', next: 'No immediate action', context: 'External oncology referral to registered episode', delay: 'No active delay', escalation: 'Closed' },
]

const funnel = [
  ['Referral', '128', '100%'], ['Registered', '112', '88%'], ['Clinically assessed', '96', '75%'],
  ['Treatment planned', '78', '61%'], ['Financially cleared', '63', '49%'], ['Treatment started', '55', '43%'],
]
const statusVariant = { Open: 'neutral', 'In progress': 'information', 'Awaiting response': 'warning', Ready: 'success', Completed: 'success' } as const
const priorityVariant = { Urgent: 'critical', High: 'warning', Routine: 'neutral' } as const

export default function ConversionFinancePage() {
  const { role } = useDemoAccess()
  const [selectedId, setSelectedId] = React.useState('authorization')
  const selected = workflows.find((item) => item.id === selectedId) ?? workflows[0]

  if (role.roleId === 'navigator') return <FinancialCounsellingHandoff />
  if (role.roleId === 'finance') return <FinancialCounsellingWorkspace />

  return <PageContainer>
    <PageHeader title="Conversion / Finance" description="Track operational conversion, treatment readiness, and financial workflow visibility across the oncology pathway." actions={<Badge variant="information">Fictional demo data</Badge>} />
    <StakeholderWorkflowPanel module="finance" />


    <Card className="mb-6 bg-surface-clinical"><CardContent className="grid gap-5 p-5 sm:grid-cols-2 xl:grid-cols-5"><div className="flex items-center gap-3 sm:col-span-2"><span className="flex size-10 items-center justify-center rounded-full bg-surface"><UserRound className="size-5" /></span><div><p className="font-display font-semibold">Sunita Patil <span className="text-xs font-normal text-metadata">(Fictional)</span></p><p className="text-xs text-metadata">MRN DEMO-ONC-02481 · 39 years · Female</p></div></div><div><p className="text-xs uppercase tracking-wider text-metadata">Diagnosis</p><p className="mt-1 text-sm font-medium text-supporting">Stage IIA breast cancer</p></div><div><p className="text-xs uppercase tracking-wider text-metadata">Treatment context</p><p className="mt-1 text-sm font-medium text-supporting">AC chemotherapy · Cycle 3 planning</p></div><div><p className="text-xs uppercase tracking-wider text-metadata">Episode status</p><p className="mt-1 text-sm font-medium text-supporting">Readiness in progress</p></div></CardContent></Card>

    <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">{[
      ['Active oncology episodes','128','Fictional active pathways',WalletCards],['Treatment-ready cases','55','43% of demo referrals',CheckCircle2],['Pending financial clearance','15','Workflow review required',Clock3],['Conversion opportunities','23','Planned, not yet cleared',CircleDollarSign],['Outstanding actions','31','Across access and care teams',AlertTriangle],
    ].map(([label,value,hint,Icon]) => <Card key={label as string}><CardContent className="p-5"><div className="flex items-start justify-between gap-3"><div><p className="text-sm text-metadata">{label as string}</p><p className="mt-2 font-display text-2xl font-semibold">{value as string}</p></div><span className="flex size-9 items-center justify-center rounded-md bg-brand-soft text-brand"><Icon className="size-4" /></span></div><p className="mt-2 text-xs text-metadata">{hint as string}</p></CardContent></Card>)}</div>

    <div className="mb-6 grid gap-6 xl:grid-cols-3">
      <Card className="xl:col-span-2"><CardHeader className="border-b border-divider"><div className="flex items-start justify-between gap-3"><div><CardTitle>Oncology financial workflow</CardTitle><CardDescription className="mt-1">Operational ownership and readiness visibility only</CardDescription></div><Badge variant="brand"><BadgeIndianRupee />6 items</Badge></div></CardHeader><CardContent className="grid gap-4 pt-6 lg:grid-cols-2">{workflows.map((item) => <button key={item.id} type="button" onClick={() => setSelectedId(item.id)} className={cn('rounded-lg border p-4 text-left transition-colors', selectedId === item.id ? 'border-brand bg-brand-soft' : 'border-border bg-surface hover:bg-surface-app')}><div className="flex items-start justify-between gap-3"><div><p className="text-sm font-semibold text-supporting">{item.task}</p><p className="mt-1 text-xs text-metadata">{item.owner}</p></div><Badge variant={priorityVariant[item.priority]}>{item.priority}</Badge></div><dl className="mt-4 grid grid-cols-2 gap-3 text-xs"><div><dt className="text-metadata">Status</dt><dd className="mt-1"><Badge variant={statusVariant[item.status]}>{item.status}</Badge></dd></div><div><dt className="text-metadata">Due date</dt><dd className="mt-1 font-medium text-supporting">{item.due}</dd></div></dl><div className="mt-4 flex items-center justify-between gap-3 border-t border-divider pt-3"><span className="text-xs font-medium text-supporting">{item.next}</span><ChevronRight className="size-4 shrink-0 text-metadata" /></div></button>)}</CardContent></Card>

      <Card variant="elevated"><CardHeader className="border-b border-divider"><div className="flex items-start justify-between gap-3"><div><CardTitle>Workflow detail</CardTitle><CardDescription className="mt-1">Selected fictional case item</CardDescription></div><Badge variant={statusVariant[selected.status]}>{selected.status}</Badge></div></CardHeader><CardContent className="space-y-5 pt-6"><div><p className="text-sm font-semibold text-supporting">{selected.task}</p><p className="mt-1 text-xs text-metadata">{selected.context}</p></div><dl className="space-y-3 text-sm">{[['Owner',selected.owner],['Priority',selected.priority],['Due date',selected.due],['Status',selected.status]].map(([label,value]) => <div key={label} className="flex justify-between gap-4"><dt className="text-metadata">{label}</dt><dd className="text-right font-medium text-supporting">{value}</dd></div>)}</dl><div className="rounded-md border border-warning/30 bg-warning-subtle p-4"><p className="text-xs uppercase tracking-wider text-metadata">Reason for delay</p><p className="mt-2 text-sm font-medium text-supporting">{selected.delay}</p></div><div className="rounded-md border border-border bg-input-background p-4"><p className="text-xs uppercase tracking-wider text-metadata">Escalation state</p><p className="mt-2 text-sm text-supporting">{selected.escalation}</p></div><div className="rounded-md border border-brand-soft bg-surface-clinical p-4"><p className="text-xs uppercase tracking-wider text-metadata">Next workflow action</p><p className="mt-2 text-sm font-semibold text-supporting">{selected.next}</p></div><Button type="button" className="w-full">Review workflow item<ArrowRight /></Button></CardContent></Card>
    </div>

    <div className="grid gap-6 xl:grid-cols-3"><Card className="xl:col-span-2"><CardHeader className="border-b border-divider"><CardTitle>Conversion funnel</CardTitle><CardDescription>Fictional/demo episode counts from referral to treatment start</CardDescription></CardHeader><CardContent className="pt-6"><ol className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">{funnel.map(([label,count,rate],index) => <li key={label} className="relative rounded-lg border border-border bg-surface p-4"><p className="text-xs font-medium text-metadata">{index + 1}. {label}</p><p className="mt-3 font-display text-2xl font-semibold">{count}</p><Badge className="mt-2" variant={index < 3 ? 'information' : index < 5 ? 'warning' : 'success'}>{rate} of referrals</Badge></li>)}</ol></CardContent></Card><Card><CardHeader className="border-b border-divider"><CardTitle>Operational queue</CardTitle><CardDescription>Items needing coordinated follow-through</CardDescription></CardHeader><CardContent className="space-y-3 pt-6">{[['Authorization awaiting payer response','warning'],['Treatment clearance pending laboratory review','critical'],['Financial counselling required','information'],['Referral follow-up due','neutral'],['Treatment cycle ready for scheduling','success']].map(([item,variant]) => <div key={item} className="flex items-center justify-between gap-3 rounded-md border border-border p-3"><span className="text-sm font-medium text-supporting">{item}</span><Badge variant={variant as 'warning'|'critical'|'information'|'neutral'|'success'}>{variant === 'success' ? 'Ready' : 'Open'}</Badge></div>)}</CardContent></Card></div>

  </PageContainer>
}
