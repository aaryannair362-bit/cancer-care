import {
  LayoutDashboard,
  UserRound,
  UserPlus,
  ClipboardCheck,
  Stethoscope,
  ClipboardList,
  Mic,
  FlaskConical,
  Scan,
  Network,
  Users,
  Wallet,
  Route,
  FileText,
  Settings,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  id: string
  label: string
  icon: LucideIcon
  /**
   * Route target. Kept for when real routes exist; the shell currently drives
   * active state from local selection (no module screens built yet).
   */
  href: string
}

export interface NavGroup {
  id: string
  /** Optional section label; omit for the top-level overview group. */
  label?: string
  items: NavItem[]
}

/**
 * Oncology OS modules, grouped for hierarchy. Grouping is organizational only —
 * no module screens or workflows are implemented yet.
 */
export const navGroups: NavGroup[] = [
  {
    id: 'overview',
    items: [
      { id: 'os-overview', label: 'OS Overview', icon: LayoutDashboard, href: '/' },
    ],
  },
  {
    id: 'clinical',
    label: 'Clinical',
    items: [
      { id: 'registration', label: 'Registration', icon: UserPlus, href: '/registration' },
      { id: 'patients', label: 'Patients', icon: UserRound, href: '/patients' },
      { id: 'nurse-intake', label: 'Nurse Intake', icon: ClipboardCheck, href: '/nurse-intake' },
      { id: 'doctor-opd', label: 'Doctor OPD', icon: Stethoscope, href: '/doctor-opd' },
      { id: 'care-plan', label: 'Care Plan', icon: ClipboardList, href: '/care-plan' },
      { id: 'opd-scribe', label: 'OPD Scribe', icon: Mic, href: '/opd-scribe' },
    ],
  },
  {
    id: 'diagnostics',
    label: 'Diagnostics',
    items: [
      { id: 'lab', label: 'Lab', icon: FlaskConical, href: '/lab' },
      { id: 'radiology', label: 'Radiology', icon: Scan, href: '/radiology' },
    ],
  },
  {
    id: 'coordination',
    label: 'Coordination',
    items: [
      { id: 'nexus', label: 'NEXUS', icon: Network, href: '/nexus' },
      { id: 'mdt', label: 'MDT / Tumour Board', icon: Users, href: '/mdt-tumour-board' },
    ],
  },
  {
    id: 'operations',
    label: 'Operations',
    items: [
      { id: 'finance', label: 'Conversion / Finance', icon: Wallet, href: '/conversion-finance' },
      { id: 'patient-journey', label: 'Patient Journey', icon: Route, href: '/patient-journey' },
      { id: 'documents', label: 'Documents', icon: FileText, href: '/documents' },
    ],
  },
]

/** Pinned to the bottom of the sidebar, separated from the modules. */
export const settingsNav: NavItem[] = [
  { id: 'settings', label: 'Settings', icon: Settings, href: '/settings' },
]

/** Flat list of every navigable item (for active-item lookup). */
export const allNav: NavItem[] = [
  ...navGroups.flatMap((group) => group.items),
  ...settingsNav,
]
