'use client'

import { Settings, ShieldAlert } from 'lucide-react'
import { useDemoAccess } from '@/components/demo-access-provider'
import { AdminSettingsWorkspace } from '@/components/admin-operations-workspace'

import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { demoRoles, roleConfig } from '@/lib/demo-access'

export default function SettingsPage() {
  const {role}=useDemoAccess()
  if(role.roleId==='admin') return <AdminSettingsWorkspace/>
  return <PageContainer>
    <PageHeader title="Settings" description="Application access, permissions, and connection status." actions={<Badge variant="information">Demo environment</Badge>} />
    <Card>
      <CardHeader className="border-b border-divider"><div className="flex items-start gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-brand-soft text-brand"><Settings className="size-4" /></span><div><CardTitle>Identity and access</CardTitle><p className="mt-1 text-sm text-metadata">Users, roles, permissions, and connection boundaries.</p></div></div></CardHeader>
      <CardContent className="pt-6"><div className="flex items-start gap-3 rounded-lg border border-information/30 bg-information-subtle px-4 py-3 text-information-strong"><ShieldAlert className="mt-0.5 size-4 shrink-0" /><p className="text-sm">Identity integration is not configured.</p></div></CardContent>
    </Card>
    <div className="mt-6 grid gap-6 xl:grid-cols-2">
      <Card><CardHeader className="border-b border-divider"><CardTitle>Demo roles</CardTitle></CardHeader><CardContent className="divide-y divide-divider p-0">{demoRoles.map((role)=><div key={role.id} className="flex items-center justify-between gap-3 px-6 py-3"><p className="text-sm font-medium text-supporting">{role.label}</p></div>)}</CardContent></Card>
      <div className="space-y-6">
        <Card><CardHeader className="border-b border-divider"><CardTitle>Permissions</CardTitle></CardHeader><CardContent className="space-y-3 pt-6">{Object.entries(roleConfig).map(([role,config])=><div key={role} className="rounded-md border border-border p-3"><p className="text-sm font-semibold capitalize text-supporting">{role.replace(/-/g,' ')}</p><p className="mt-1 text-xs text-metadata">{config.permissions.length} permissions · {config.modules.length} modules · {config.actions.length} actions</p></div>)}</CardContent></Card>
        <Card><CardHeader className="border-b border-divider"><CardTitle>Integration status</CardTitle></CardHeader><CardContent className="space-y-3 pt-6">{['Identity provider','EHR / HIS','PACS / RIS','LIS / Laboratory','Billing / payer'].map((item)=><div key={item} className="flex items-center justify-between gap-3"><span className="text-sm text-supporting">{item}</span><Badge variant="warning">Not configured</Badge></div>)}</CardContent></Card>
        <Card><CardHeader className="border-b border-divider"><CardTitle>Audit visibility</CardTitle></CardHeader><CardContent className="pt-6"><p className="text-sm text-metadata">Recorded actions include actor, role, timestamp, action, and source module.</p></CardContent></Card>
      </div>
    </div>
  </PageContainer>
}
