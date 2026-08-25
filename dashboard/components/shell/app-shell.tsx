'use client'

import * as React from 'react'
import { X } from 'lucide-react'
import { usePathname, useRouter } from 'next/navigation'

import { cn } from '@/lib/utils'
import { allNav } from '@/lib/navigation'
import { canAccessRoute, canViewFinancialData, roleConfig } from '@/lib/demo-access'
import { useDemoAccess } from '@/components/demo-access-provider'
import { AccessDenied } from '@/components/access-denied'
import { Button } from '@/components/ui/button'
import { Sidebar } from '@/components/shell/sidebar'
import { Header } from '@/components/shell/header'

/**
 * Application shell: persistent left navigation, top header, persistent
 * patient-context strip, and a scrollable main workspace. Consistent across
 * every module. Contains no clinical workflow or product content.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const { role, ready, signedIn } = useDemoAccess()
  const [mobileOpen, setMobileOpen] = React.useState(false)
  const activeItem = allNav.find((item) => item.href === pathname) ?? allNav[0]
  const activeId = activeItem.id
  const allowed = canAccessRoute(role, pathname)
  const limitedFinance = pathname === '/conversion-finance' && !canViewFinancialData(role)

  const handleNavigate = () => {
    setMobileOpen(false)
  }

  // Close the mobile drawer on Escape.
  React.useEffect(() => {
    if (!mobileOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMobileOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [mobileOpen])

  React.useEffect(() => { if (ready && !signedIn && pathname !== '/login') router.replace('/login') }, [pathname,ready,router,signedIn])

  if (pathname === '/login') return <>{children}</>

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      <aside data-testid="desktop-sidebar" className="hidden w-[272px] shrink-0 lg:block">
        <Sidebar activeId={activeId} onNavigate={handleNavigate} allowedModules={roleConfig[role.roleId].modules} roleId={role.roleId} />
      </aside>

      {/* Mobile drawer */}
      <div
        data-testid="mobile-navigation"
        className={cn(
          'fixed inset-0 z-50 lg:hidden',
          mobileOpen ? 'pointer-events-auto' : 'pointer-events-none'
        )}
        aria-hidden={!mobileOpen}
      >
        {/* Backdrop */}
        <div
          className={cn(
            'absolute inset-0 bg-charcoal/40 transition-opacity duration-200',
            mobileOpen ? 'opacity-100' : 'opacity-0'
          )}
          onClick={() => setMobileOpen(false)}
        />
        {/* Panel */}
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Navigation"
          className={cn(
            'absolute inset-y-0 left-0 w-64 shadow-soft-lg transition-transform duration-200',
            mobileOpen ? 'translate-x-0' : '-translate-x-full'
          )}
        >
          <Button
            variant="ghost"
            size="icon"
            className="absolute right-2 top-3 z-10"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
          >
            <X />
          </Button>
          <Sidebar activeId={activeId} onNavigate={handleNavigate} allowedModules={roleConfig[role.roleId].modules} roleId={role.roleId} />
        </div>
      </div>

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <Header onMenuClick={() => setMobileOpen(true)} />
        <main data-testid="app-main" className="workspace-atmosphere min-w-0 flex-1 overflow-x-hidden overflow-y-auto">{allowed ? limitedFinance ? <><div className="border-b border-warning/30 bg-warning-subtle px-6 py-2 text-xs text-warning-strong">Limited financial status view · Editing and workflow actions are blocked for {role.label}.</div><div className="pointer-events-none">{children}</div></> : children : <AccessDenied role={role.label} />}</main>
      </div>
    </div>
  )
}
