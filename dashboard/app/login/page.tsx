'use client'

import * as React from 'react'
import { useDemoAccess } from '@/components/demo-access-provider'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'

export default function LoginPage() {
  const { roles, signIn } = useDemoAccess()
  const [id, setId] = React.useState(roles[0].id)
  const [password, setPassword] = React.useState('')
  const [touched, setTouched] = React.useState(false)
  const passwordValid = password.trim().length > 0

  const submit = () => {
    setTouched(true)
    if (!passwordValid) return
    signIn(id)
  }

  return <main className="flex min-h-screen items-center justify-center bg-background p-4">
    <Card variant="elevated" className="w-full max-w-md">
      <CardHeader className="border-b border-divider"><p className="font-display text-[17px] font-semibold tracking-[-0.01em] text-foreground">Aivana</p><CardDescription className="mt-2">Sign in to your workspace</CardDescription></CardHeader>
      <CardContent className="space-y-5 pt-6">
        <form noValidate onSubmit={(e) => { e.preventDefault(); submit() }} className="space-y-5">
          <div><label htmlFor="role" className="text-sm font-medium text-supporting">Role</label><select id="role" value={id} onChange={(event) => setId(event.target.value as typeof id)} className="mt-2 flex h-10 w-full rounded-md border border-input bg-input-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{roles.map((role) => <option key={role.id} value={role.id}>{role.label}</option>)}</select></div>
          <div><label htmlFor="password" className="text-sm font-medium text-supporting">Password</label><Input id="password" type="password" autoComplete="current-password" className="mt-2" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter password" aria-invalid={touched && !passwordValid} />{touched && !passwordValid ? <p className="mt-1 text-xs text-critical-strong">Password is required.</p> : null}</div>
          <Button type="submit" className="w-full">Sign in</Button>
        </form>
      </CardContent>
    </Card>
  </main>
}
