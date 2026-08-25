'use client'

import { AlertTriangle, ArrowRight, CheckCircle2, Clock3, Sparkles } from 'lucide-react'
import { useDemoAccess } from '@/components/demo-access-provider'
import { AdminOperationsDashboard } from '@/components/admin-operations-workspace'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { AiBadge } from '@/components/ui/ai-badge'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { aiActivity, alerts, metrics, pendingActions, recentActivity, todayActivity } from '@/lib/overview-demo-data'
import { cn } from '@/lib/utils'

const toneClasses = { default: 'text-foreground', critical: 'text-critical-strong', warning: 'text-warning-strong', success: 'text-success-strong' }
const priorityBadge = { urgent: 'critical', high: 'warning', routine: 'neutral', ai: 'information' } as const
const severityBadge = { critical: 'critical', warning: 'warning', information: 'information' } as const

export default function OverviewPage() {
  const {role}=useDemoAccess()
  if(role.roleId==='admin') return <AdminOperationsDashboard/>
  return (
    <PageContainer>
      <PageHeader title="OS Overview" description="Operational picture across today’s oncology care pathways." actions={<Badge variant="information">Fictional demo data</Badge>} />
      <Card className="mb-7 overflow-hidden">
        <CardContent className="grid divide-y divide-divider p-0 sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-6">
          {metrics.map((metric, index) => <div key={metric.id} className={cn('min-w-0 px-5 py-6', index > 1 && 'sm:border-t sm:border-divider xl:border-t-0')}><p className="text-sm font-medium text-metadata">{metric.label}</p><p className={cn('mt-3 font-display text-3xl font-semibold tracking-[-0.03em]', toneClasses[metric.tone ?? 'default'])}>{metric.value}</p><p className="mt-1 text-xs leading-relaxed text-metadata">{metric.hint}</p></div>)}
        </CardContent>
      </Card>
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-6 xl:col-span-2">
          <Card><CardHeader className="border-b border-divider"><CardTitle>Today’s patient activity</CardTitle><CardDescription>{todayActivity.total} fictional encounters across active workflows</CardDescription></CardHeader><CardContent className="space-y-4 pt-6">{todayActivity.statuses.map((status) => <div key={status.key} className="grid grid-cols-[110px_1fr_36px] items-center gap-3 text-sm"><span className="text-supporting">{status.label}</span><div className="h-2 overflow-hidden rounded-pill bg-surface-elevated"><div className={cn('h-full rounded-pill', status.bar)} style={{ width: `${Math.max(4, status.count / todayActivity.total * 100)}%` }} /></div><span className={cn('text-right font-semibold', status.text)}>{status.count}</span></div>)}</CardContent></Card>
          <Card><CardHeader className="border-b border-divider"><CardTitle>Pending actions</CardTitle><CardDescription>Items awaiting clinical or operational review</CardDescription></CardHeader><CardContent className="divide-y divide-divider p-0">{pendingActions.map((item) => <div key={item.id} className="flex items-center gap-3 px-6 py-4"><Clock3 className="size-4 shrink-0 text-metadata" /><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-supporting">{item.title}</p><p className="mt-0.5 text-xs text-metadata">{item.module} · {item.meta}</p></div><Badge variant={priorityBadge[item.priority]}>{item.priority === 'ai' ? 'AI review' : item.priority}</Badge><ArrowRight className="size-4 text-disabled" /></div>)}</CardContent></Card>
          <Card><CardHeader className="border-b border-divider"><CardTitle>Recent activity</CardTitle><CardDescription>Latest fictional updates across the oncology service</CardDescription></CardHeader><CardContent className="divide-y divide-divider p-0">{recentActivity.map((item) => { const Icon = item.icon; return <div key={item.id} className="flex items-center gap-3 px-6 py-4"><span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-surface-elevated text-metadata"><Icon className="size-4" /></span><div className="min-w-0 flex-1"><p className="text-sm font-medium text-supporting">{item.action}</p><p className="truncate text-xs text-metadata">{item.subject} · {item.module}</p></div><span className="text-xs text-metadata">{item.time}</span></div> })}</CardContent></Card>
        </div>
        <div className="space-y-6">
          <Card><CardHeader className="border-b border-divider"><CardTitle>Clinical alerts</CardTitle><CardDescription>Prioritised for acknowledgement</CardDescription></CardHeader><CardContent className="space-y-3 pt-6">{alerts.map((alert) => <div key={alert.id} className={cn('rounded-md border p-3', alert.severity === 'critical' ? 'border-critical/30 bg-critical-subtle' : alert.severity === 'warning' ? 'border-warning/30 bg-warning-subtle' : 'border-information/30 bg-information-subtle')}><div className="flex items-start gap-2"><AlertTriangle className="mt-0.5 size-4 shrink-0" /><div className="min-w-0 flex-1"><p className="text-sm font-semibold">{alert.title}</p><p className="mt-1 text-xs">{alert.detail}</p></div><Badge variant={severityBadge[alert.severity]}>{alert.time}</Badge></div></div>)}</CardContent></Card>
          <Card variant="ai"><CardHeader className="border-b border-ai-highlight"><div className="flex items-center justify-between gap-3"><CardTitle>AI activity</CardTitle><AiBadge>Assistant output</AiBadge></div><CardDescription>Requires human review before clinical use</CardDescription></CardHeader><CardContent className="space-y-4 pt-6">{aiActivity.map((item) => <div key={item.id} className="flex gap-3"><Sparkles className="mt-0.5 size-4 shrink-0 text-ai" /><div><p className="text-sm font-medium text-supporting">{item.title}</p><p className="mt-1 text-xs text-metadata">{item.detail}</p><p className="mt-1 flex items-center gap-1 text-xs text-information-strong"><CheckCircle2 className="size-3" />{item.confidence}</p></div></div>)}</CardContent></Card>
        </div>
      </div>
    </PageContainer>
  )
}
