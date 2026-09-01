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
  Pill,
  Syringe,
  ShieldCheck,
  Radiation,
  Scissors,
  Activity,
  CalendarCheck,
  ScanSearch,
  FileCheck2,
  BookOpen,
  FileHeart,
  Building2,
  Table2,
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
      { id: 'treatment-order', label: 'Treatment Order', icon: ClipboardCheck, href: '/treatment-order' },
      { id: 'pharmacy', label: 'Pharmacy', icon: Pill, href: '/pharmacy' },
      { id: 'treatment-day', label: 'Day Care / Infusion', icon: Syringe, href: '/treatment-day' },
      { id: 'radiation-oncology', label: 'Radiation Oncology', icon: Radiation, href: '/radiation-oncology' },
      { id: 'surgical-oncology', label: 'Surgical Oncology', icon: Scissors, href: '/surgical-oncology' },
      { id: 'treatment-readiness', label: 'Treatment Readiness', icon: CalendarCheck, href: '/treatment-readiness' },
      { id: 'active-treatment', label: 'Active Treatment', icon: Activity, href: '/active-treatment' },
      { id: 'patient-summary', label: 'Patient Summary', icon: FileHeart, href: '/patient-summary' },
      { id: 'response-assessment', label: 'Response Assessment', icon: ScanSearch, href: '/response-assessment' },
      { id: 'consent', label: 'Consent & Education', icon: FileCheck2, href: '/consent' },
      { id: 'regimens', label: 'Regimen Library', icon: BookOpen, href: '/regimens' },
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
      { id: 'oncology-operations', label: 'Oncology Operations', icon: Building2, href: '/oncology-operations' },
    ],
  },
]

/** Pinned to the bottom of the sidebar, separated from the modules. */
export const settingsNav: NavItem[] = [
  { id: 'standards', label: 'Standards & Interoperability', icon: ShieldCheck, href: '/standards' },
  { id: 'terminology', label: 'Dropdown Source of Truth', icon: Table2, href: '/terminology' },
  { id: 'settings', label: 'Settings', icon: Settings, href: '/settings' },
]

/** Flat list of every navigable item (for active-item lookup). */
export const allNav: NavItem[] = [
  ...navGroups.flatMap((group) => group.items),
  ...settingsNav,
]
