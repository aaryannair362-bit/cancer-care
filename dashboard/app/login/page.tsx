'use client'

import * as React from 'react'
import { ShieldAlert } from 'lucide-react'
import { useDemoAccess } from '@/components/demo-access-provider'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'

export default function LoginPage() {
  const { roles, signIn } = useDemoAccess()
  const [id,setId] = React.useState(roles[0].id)
  return <main className="flex min-h-screen items-center justify-center bg-background p-4"><Card variant="elevated" className="w-full max-w-md"><CardHeader className="border-b border-divider"><p className="font-display text-[17px] font-semibold tracking-[-0.01em] text-foreground">Aivana</p><CardDescription className="mt-2">Sign in to your workspace</CardDescription></CardHeader><CardContent className="space-y-5 pt-6"><div><label htmlFor="role" className="text-sm font-medium text-supporting">Demo Role</label><select id="role" value={id} onChange={(event)=>setId(event.target.value as typeof id)} className="mt-2 flex h-10 w-full rounded-md border border-input bg-input-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{roles.map((role)=><option key={role.id} value={role.id}>{role.label}</option>)}</select></div><Button type="button" className="w-full" onClick={()=>signIn(id)}>Sign in</Button><div className="flex gap-2 rounded-md border border-information/30 bg-information-subtle p-3 text-xs text-information-strong"><ShieldAlert className="size-4 shrink-0"/><span>Demonstration authentication only. No SSO, OAuth, MFA, identity provider, or production authorization is configured.</span></div></CardContent></Card></main>
}
