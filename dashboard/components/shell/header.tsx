'use client'

import * as React from 'react'
import { Bell, ChevronDown, LogOut, Menu, Search, UserRound } from 'lucide-react'
import { useRouter } from 'next/navigation'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useDemoAccess } from '@/components/demo-access-provider'

interface HeaderProps { onMenuClick: () => void }

export function Header({ onMenuClick }: HeaderProps) {
  const { role, roles, switchRole, signOut } = useDemoAccess()
  const router = useRouter()
  const [query,setQuery] = React.useState('')
  const [accountOpen,setAccountOpen] = React.useState(false)
  const accountRef = React.useRef<HTMLDivElement>(null)
  const initials = role.shortLabel.split(' ').map((part)=>part[0]).slice(0,2).join('').toUpperCase()

  React.useEffect(() => {
    function closeAccount(event: MouseEvent) { if (accountRef.current && !accountRef.current.contains(event.target as Node)) setAccountOpen(false) }
    function closeOnEscape(event: KeyboardEvent) { if (event.key === 'Escape') setAccountOpen(false) }
    document.addEventListener('mousedown',closeAccount)
    document.addEventListener('keydown',closeOnEscape)
    return () => { document.removeEventListener('mousedown',closeAccount); document.removeEventListener('keydown',closeOnEscape) }
  }, [])

  const searchItems = [
    { label:'Sunita Patil', detail:'Patient · DEMO-ONC-02481 · Breast cancer Stage IIA', href:'/patient-journey' },
    { label:'DEMO-ONC-02481', detail:'MRN · Sunita Patil', href:'/patient-journey' },
    { label:'Pathology report', detail:'Document · Reviewed', href:'/documents' },
    { label:'CBC result requires review', detail:'Task · Medical Oncology', href:'/lab' },
    { label:'Invasive ductal carcinoma', detail:'Diagnosis · Left breast', href:'/doctor-opd' },
  ]
  const matches = query.trim().length > 1 ? searchItems.filter((item)=>`${item.label} ${item.detail}`.toLowerCase().includes(query.toLowerCase())) : []

  return <header className="flex h-[76px] shrink-0 items-center gap-3 border-b border-white/58 bg-[linear-gradient(105deg,hsl(var(--surface-elevated)/0.9),hsl(var(--surface)/0.82),hsl(var(--brand-soft)/0.54))] px-4 shadow-[0_12px_34px_-28px_hsl(var(--brand-deep)/0.42)] backdrop-blur-xl sm:px-7">
    <Button variant="ghost" size="icon" className="lg:hidden" onClick={onMenuClick} aria-label="Open navigation"><Menu /></Button>
    <div className="ml-auto flex flex-1 items-center justify-end gap-2">
      <div className="relative hidden md:block">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-metadata" aria-hidden="true" />
        <Input data-testid="global-search" type="search" placeholder="Search patient or workflow" aria-label="Search patient or workflow" className="h-12 w-64 rounded-full border-white/70 bg-surface/72 pl-10 shadow-[inset_0_1px_2px_hsl(var(--charcoal)/0.04),0_8px_20px_-14px_hsl(var(--brand-deep)/0.3)] xl:w-80 2xl:w-[420px]" value={query} onChange={(event)=>setQuery(event.target.value)} />
        {query.trim().length > 1 ? <div className="absolute right-0 top-11 z-50 w-80 rounded-lg border border-border bg-surface p-2 shadow-soft-lg">{matches.length ? matches.map((item)=><button key={item.label} type="button" onClick={()=>{setQuery('');router.push(item.href)}} className="block w-full rounded-md px-3 py-2 text-left hover:bg-surface-app"><span className="block text-sm font-semibold text-supporting">{item.label}</span><span className="mt-0.5 block text-xs text-metadata">{item.detail}</span></button>) : <p className="px-3 py-4 text-sm text-metadata">No matching patient, document, task, or diagnosis.</p>}</div> : null}
      </div>
      <Button variant="ghost" size="icon" className="size-11 rounded-full border border-white/68 bg-surface/66 shadow-soft-sm" aria-label="Notifications"><Bell /></Button>
      <select data-testid="role-selector" aria-label="Switch demo role" value={role.id} onChange={(event)=>switchRole(event.target.value as typeof role.id)} className="h-11 max-w-40 rounded-full border border-white/70 bg-surface/68 px-4 text-xs font-medium text-supporting shadow-soft-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{roles.map((item)=><option key={item.id} value={item.id}>{item.label}</option>)}</select>
      <div ref={accountRef} className="relative">
        <button type="button" className="flex h-11 items-center gap-2 rounded-full border border-white/68 bg-surface/66 pl-1.5 pr-3 text-supporting shadow-soft-sm transition-colors hover:bg-surface" aria-label="Open account menu" aria-haspopup="menu" aria-expanded={accountOpen} onClick={()=>setAccountOpen((open)=>!open)}><span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-soft text-xs font-semibold text-brand-deep">{initials}</span><ChevronDown className="size-3.5 text-metadata" aria-hidden="true" /></button>
        {accountOpen ? <div role="menu" className="absolute right-0 top-[52px] z-50 w-64 overflow-hidden rounded-xl border border-border bg-surface shadow-soft-lg"><div className="flex gap-3 border-b border-divider p-4"><span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand-deep"><UserRound className="size-4" /></span><div className="min-w-0"><p className="truncate text-sm font-semibold text-supporting">Demo account</p><p className="mt-0.5 truncate text-xs text-metadata">{role.label}</p></div></div><div className="p-2"><button type="button" role="menuitem" onClick={()=>{setAccountOpen(false);signOut()}} className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-medium text-supporting transition-colors hover:bg-surface-app focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><LogOut className="size-4 text-metadata" />Log out</button></div></div> : null}
      </div>
    </div>
  </header>
}
