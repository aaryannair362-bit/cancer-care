'use client'

import { Activity, BadgeIndianRupee, Beaker, BookOpen, ChevronRight, ClipboardCheck, FileSearch, HandHeart, History, LayoutDashboard, Microscope, Pill, Radiation, ScanSearch, Scissors, Settings, ShieldCheck, Syringe } from 'lucide-react'
import Link from 'next/link'

import { cn } from '@/lib/utils'
import { navGroups, settingsNav, type NavItem } from '@/lib/navigation'

interface SidebarProps {
  activeId: string
  onNavigate?: () => void
  allowedModules: string[]
  roleId?: string
}

function NavList({
  items,
  activeId,
  onNavigate,
}: {
  items: NavItem[]
  activeId: string
  onNavigate?: () => void
}) {
  return (
    <ul className="space-y-1.5">
      {items.map((item) => {
        const isActive = item.id === activeId
        const Icon = item.icon
        return (
          <li key={item.id}>
            <Link
              href={item.href}
              onClick={onNavigate}
              aria-current={isActive ? 'page' : undefined}
              className={cn(
                'group flex w-full items-center gap-3 rounded-2xl px-3.5 py-3 text-sm font-medium transition-all duration-200',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
                isActive
                  ? 'aivana-selected-surface border border-brand/20 text-foreground shadow-neu [&_svg]:text-brand-deep'
                  : 'border border-transparent bg-surface-elevated/52 text-supporting hover:-translate-y-px hover:border-white/65 hover:bg-brand-soft/62 hover:text-foreground hover:shadow-soft-sm'
              )}
            >
              <Icon className="size-[18px] shrink-0" aria-hidden="true" />
              <span className="truncate">{item.label}</span>
              <ChevronRight className={cn('ml-auto size-3.5 transition-transform', isActive ? 'rotate-0 opacity-90' : 'opacity-40 group-hover:translate-x-0.5 group-hover:opacity-70')} aria-hidden="true" />
            </Link>
          </li>
        )
      })}
    </ul>
  )
}

/**
 * Left navigation — grouped Oncology OS modules with Settings pinned at the
 * bottom. Presentational: active state is controlled by the parent shell so it
 * can be swapped to route-based (usePathname) once routes exist.
 */
export function Sidebar({ activeId, onNavigate, allowedModules, roleId }: SidebarProps) {
  const navItems = navGroups.flatMap((group)=>group.items)
  const doctorItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {...navItems.find((item)=>item.id==='doctor-opd')!,label:'Consultation'},
    {...navItems.find((item)=>item.id==='care-plan')!,label:'Treatment Plan'},
    {id:'treatment-order',label:'Treatment Order',icon:ClipboardCheck,href:'/treatment-order'},
    {id:'pharmacy',label:'Pharmacy',icon:Pill,href:'/pharmacy'},
    {id:'treatment-day',label:'Day Care / Infusion',icon:Syringe,href:'/treatment-day'},
    navItems.find((item)=>item.id==='treatment-readiness')!,
    navItems.find((item)=>item.id==='active-treatment')!,
    navItems.find((item)=>item.id==='patient-summary')!,
    navItems.find((item)=>item.id==='response-assessment')!,
    navItems.find((item)=>item.id==='consent')!,
    navItems.find((item)=>item.id==='regimens')!,
    navItems.find((item)=>item.id==='nexus')!,
    {id:'guideline-pathway',label:'Guideline Pathway',icon:BookOpen,href:'/nexus'},
    {id:'staging',label:'Staging',icon:ScanSearch,href:'/doctor-opd#staging'},
    navItems.find((item)=>item.id==='mdt')!,
  ]
  const nurseOrder = ['patients','nexus']
  const nurseItems = navGroups.flatMap((group)=>group.items).filter((item)=>nurseOrder.includes(item.id)).sort((a,b)=>nurseOrder.indexOf(a.id)-nurseOrder.indexOf(b.id))
  const surgicalItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {...navItems.find((item)=>item.id==='doctor-opd')!,label:'Consultation'},
    {...navItems.find((item)=>item.id==='care-plan')!,label:'Treatment Plan'},
    {id:'surgical-oncology',label:'Surgical Plan',icon:Scissors,href:'/surgical-oncology'},
    navItems.find((item)=>item.id==='active-treatment')!,
    navItems.find((item)=>item.id==='patient-summary')!,
    navItems.find((item)=>item.id==='consent')!,
    navItems.find((item)=>item.id==='nexus')!,
    {id:'guideline-pathway',label:'Guideline Pathway',icon:BookOpen,href:'/nexus'},
    {id:'staging',label:'Staging',icon:ScanSearch,href:'/doctor-opd#staging'},
    navItems.find((item)=>item.id==='mdt')!,
  ]
  const radiationItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {...navItems.find((item)=>item.id==='doctor-opd')!,label:'Consultation'},
    {...navItems.find((item)=>item.id==='care-plan')!,label:'Treatment Plan'},
    {id:'radiation-oncology',label:'Radiation Prescription',icon:Radiation,href:'/radiation-oncology'},
    navItems.find((item)=>item.id==='active-treatment')!,
    navItems.find((item)=>item.id==='patient-summary')!,
    navItems.find((item)=>item.id==='consent')!,
    navItems.find((item)=>item.id==='nexus')!,
    {id:'guideline-pathway',label:'Guideline Pathway',icon:BookOpen,href:'/nexus'},
    {id:'staging',label:'Staging',icon:ScanSearch,href:'/doctor-opd#staging'},
    navItems.find((item)=>item.id==='mdt')!,
  ]
  const radiologistItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {...navItems.find((item)=>item.id==='radiology')!,label:'Imaging Worklist'},
    {id:'radiology-reports',label:'Reports',icon:BookOpen,href:'/radiology#reports'},
    navItems.find((item)=>item.id==='response-assessment')!,
    navItems.find((item)=>item.id==='nexus')!,
    navItems.find((item)=>item.id==='mdt')!,
  ]
  const radiologyCoordinatorItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {...navItems.find((item)=>item.id==='radiology')!,label:'Imaging Coordination'},
  ]
  const pathologistItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {id:'pathology',label:'Pathology Worklist',icon:Microscope,href:'/pathology'},
    {id:'pathology-reports',label:'Reports',icon:FileSearch,href:'/pathology#reports'},
    {id:'molecular-diagnostics',label:'Molecular Diagnostics',icon:Beaker,href:'/pathology#molecular-diagnostics'},
    navItems.find((item)=>item.id==='nexus')!,
    navItems.find((item)=>item.id==='mdt')!,
  ]
  const labItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {...navItems.find((item)=>item.id==='lab')!,label:'Lab Worklist'},
  ]
  const pharmacyItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {id:'pharmacy',label:'Pharmacy Verification & Dispensing',icon:Pill,href:'/pharmacy'},
    navItems.find((item)=>item.id==='regimens')!,
  ]
  const infusionItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {id:'treatment-day',label:'Treatment Day / Infusion',icon:Syringe,href:'/treatment-day'},
    navItems.find((item)=>item.id==='treatment-readiness')!,
    navItems.find((item)=>item.id==='active-treatment')!,
    navItems.find((item)=>item.id==='consent')!,
    navItems.find((item)=>item.id==='nexus')!,
  ]
  const mdtCoordinatorItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    navItems.find((item)=>item.id==='mdt')!,
    navItems.find((item)=>item.id==='active-treatment')!,
    navItems.find((item)=>item.id==='nexus')!,
  ]
  const externalMdtItems: NavItem[] = [
    {...navItems.find((item)=>item.id==='patients')!,label:'Assigned Cases'},
    navItems.find((item)=>item.id==='mdt')!,
    navItems.find((item)=>item.id==='nexus')!,
  ]
  const navigatorItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {id:'care-coordination',label:'Care Coordination',icon:HandHeart,href:'/care-coordination'},
    {...navItems.find((item)=>item.id==='finance')!,label:'Financial Counselling'},
    navItems.find((item)=>item.id==='active-treatment')!,
    navItems.find((item)=>item.id==='patient-summary')!,
    navItems.find((item)=>item.id==='nexus')!,
  ]
  const financeItems: NavItem[] = [
    navItems.find((item)=>item.id==='patients')!,
    {...navItems.find((item)=>item.id==='finance')!,label:'Financial Counselling'},
    {id:'estimates-clearance',label:'Estimates & Clearance',icon:BadgeIndianRupee,href:'/estimates-clearance'},
  ]
  const adminItems: NavItem[] = [
    {id:'os-overview',label:'Operations Dashboard',icon:LayoutDashboard,href:'/'},
    navItems.find((item)=>item.id==='patients')!,
    {id:'workflow-operations',label:'Workflow Operations',icon:Activity,href:'/workflow-operations'},
    {id:'users-roles',label:'Users & Roles',icon:ShieldCheck,href:'/users-roles'},
    {id:'audit-activity',label:'Audit & Activity',icon:History,href:'/audit-activity'},
    {id:'standards',label:'Standards & Interoperability',icon:ShieldCheck,href:'/standards'},
    {id:'oncology-operations',label:'Oncology Operations',icon:Activity,href:'/oncology-operations'},
    {id:'terminology',label:'Dropdown Source of Truth',icon:ShieldCheck,href:'/terminology'},
    {id:'settings',label:'Settings',icon:Settings,href:'/settings'},
  ]
  const roleGroups = roleId === 'doctor'
    ? [{id:'medical-oncology',label:'Clinical',items:doctorItems}]
    : roleId === 'nurse'
      ? [{id:'nurse-navigation',label:'Clinical',items:nurseItems}]
      : roleId === 'surgical-oncology'
        ? [{id:'surgical-oncology',label:'Clinical',items:surgicalItems}]
        : roleId === 'radiation-oncology'
          ? [{id:'radiation-oncology',label:'Clinical',items:radiationItems}]
          : roleId === 'radiologist'
            ? [{id:'radiologist',label:'Imaging',items:radiologistItems}]
            : roleId === 'radiology'
              ? [{id:'radiology-coordination',label:'Imaging',items:radiologyCoordinatorItems}]
              : roleId === 'pathologist'
                ? [{id:'pathology',label:'Diagnostics',items:pathologistItems}]
                : roleId === 'lab'
                  ? [{id:'lab-operations',label:'Laboratory',items:labItems}]
                  : roleId === 'pharmacy'
                    ? [{id:'pharmacy-operations',label:'Pharmacy',items:pharmacyItems}]
                  : roleId === 'infusion-nurse'
                    ? [{id:'infusion-operations',label:'Day Care',items:infusionItems}]
                    : roleId === 'mdt-coordinator'
                      ? [{id:'mdt-coordination',label:'MDT Coordination',items:mdtCoordinatorItems}]
                      : roleId === 'mdt-clinician'
                        ? [{id:'external-mdt',label:'Assigned Review',items:externalMdtItems}]
                        : roleId === 'navigator'
                          ? [{id:'care-navigation',label:'Care Navigation',items:navigatorItems}]
                          : roleId === 'finance'
                            ? [{id:'financial-services',label:'Patient Financial Services',items:financeItems}]
                            : roleId === 'admin'
                              ? [{id:'operations-admin',label:'Operations',items:adminItems}]
      : navGroups.map((group) => ({ ...group, items: group.items.filter((item) => item.id === 'os-overview' || item.id === 'patients' || allowedModules.includes(item.id)) })).filter((group) => group.items.length > 0)
  return (
    <div className="flex h-full flex-col border-r border-white/55 bg-[radial-gradient(circle_at_0%_0%,hsl(var(--brand-soft)/0.82),transparent_18rem),linear-gradient(180deg,hsl(var(--surface-elevated)/0.96),hsl(var(--surface-app)/0.94))] shadow-[14px_0_42px_-30px_hsl(var(--brand-deep)/0.46)] backdrop-blur-xl">
      <div className="flex h-16 shrink-0 items-center px-5">
        <span className="font-display text-[17px] font-semibold tracking-[-0.01em] text-foreground">Aivana</span>
      </div>

      <nav aria-label="Modules" className="min-h-0 flex-1 space-y-6 overflow-y-auto px-3 py-5">
        {roleGroups.map((group) => (
          <div key={group.id}>
            {group.label ? (
              <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-metadata">
                {group.label}
              </p>
            ) : null}
            <NavList items={group.items} activeId={activeId} onNavigate={onNavigate} />
          </div>
        ))}
      </nav>

      {/* Settings — separated and pinned at the bottom */}
      {roleId !== 'admin' && allowedModules.includes('settings') ? <div className="shrink-0 border-t border-divider p-3"><NavList items={settingsNav} activeId={activeId} onNavigate={onNavigate} /></div> : null}
    </div>
  )
}
