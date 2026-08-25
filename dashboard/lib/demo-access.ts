export type RoleId = 'registration' | 'nurse' | 'doctor' | 'surgical-oncology' | 'radiation-oncology' | 'radiologist' | 'radiology' | 'pathologist' | 'lab' | 'infusion-nurse' | 'mdt-coordinator' | 'mdt-clinician' | 'navigator' | 'finance' | 'admin'
export type Permission = 'patient:view' | 'patient:edit' | 'clinical:edit' | 'clinical:approve' | 'mdt:send' | 'mdt:comment' | 'financial:view-limited' | 'financial:view-full' | 'admin:view'
export type ActionId = 'complete-registration' | 'handoff-nurse' | 'complete-intake' | 'handoff-doctor' | 'complete-opd' | 'order-investigations' | 'review-results' | 'send-mdt' | 'assemble-packet' | 'schedule-board' | 'comment-mdt' | 'approve-mdt' | 'draft-care-plan' | 'complete-lab' | 'complete-radiology' | 'create-follow-up' | 'update-finance'

export type DemoRole = { id: RoleId; roleId: RoleId; label: string; shortLabel: string }
export type RoleConfig = { permissions: Permission[]; modules: string[]; actions: ActionId[]; patientAccess: 'all-demo' | 'assigned-case' | 'limited' | 'none'; queue: string[]; landing: string }

export const demoRoles: DemoRole[] = [
  { id: 'registration', roleId: 'registration', label: 'Front Desk / Registration', shortLabel: 'Registration' },
  { id: 'nurse', roleId: 'nurse', label: 'Nurse Navigator', shortLabel: 'Nurse Navigator' },
  { id: 'doctor', roleId: 'doctor', label: 'Medical Oncologist', shortLabel: 'Medical Oncology' },
  { id: 'surgical-oncology', roleId: 'surgical-oncology', label: 'Surgical Oncologist', shortLabel: 'Surgical Oncology' },
  { id: 'radiation-oncology', roleId: 'radiation-oncology', label: 'Radiation Oncologist', shortLabel: 'Radiation Oncology' },
  { id: 'radiologist', roleId: 'radiologist', label: 'Radiologist', shortLabel: 'Radiologist' },
  { id: 'radiology', roleId: 'radiology', label: 'Radiology Coordinator', shortLabel: 'Radiology Coordinator' },
  { id: 'pathologist', roleId: 'pathologist', label: 'Pathologist / Molecular Diagnostics', shortLabel: 'Pathology' },
  { id: 'lab', roleId: 'lab', label: 'Lab / Phlebotomy', shortLabel: 'Lab' },
  { id: 'infusion-nurse', roleId: 'infusion-nurse', label: 'Oncology Day-Care / Infusion Nurse', shortLabel: 'Infusion Nurse' },
  { id: 'mdt-coordinator', roleId: 'mdt-coordinator', label: 'MDT Coordinator', shortLabel: 'MDT Coordinator' },
  { id: 'mdt-clinician', roleId: 'mdt-clinician', label: 'External MDT Specialist', shortLabel: 'External MDT Specialist' },
  { id: 'navigator', roleId: 'navigator', label: 'Patient Liaison / Care Coordinator', shortLabel: 'Care Coordinator' },
  { id: 'finance', roleId: 'finance', label: 'Financial Counsellor / Patient Financial Services', shortLabel: 'Patient Financial Services' },
  { id: 'admin', roleId: 'admin', label: 'Admin / Operations', shortLabel: 'Admin' },
]

export const legacyPersonaRoleIds: Record<string, RoleId> = { anita:'registration', leena:'nurse', kavya:'doctor', sameer:'surgical-oncology', nisha:'radiation-oncology', rohan:'radiology', priya:'lab', rahul:'mdt-coordinator', arjun:'mdt-clinician', maya:'navigator', neha:'finance', amit:'admin' }
export function resolveDemoRole(id: string | null | undefined) { const roleId = id && (legacyPersonaRoleIds[id] ?? id as RoleId); return demoRoles.find((role) => role.id === roleId) }

export const roleConfig: Record<RoleId, RoleConfig> = {
  registration: { permissions: ['patient:view','patient:edit'], modules: ['registration'], actions: ['complete-registration','handoff-nurse'], patientAccess: 'all-demo', queue: ['Patients awaiting registration','Duplicate matches','Consent pending','Scheduling pending'], landing: '/' },
  nurse: { permissions: ['patient:view','patient:edit','clinical:edit'], modules: ['nurse-intake','nexus'], actions: ['complete-intake','handoff-doctor'], patientAccess: 'all-demo', queue: ['Patients awaiting intake','Incomplete intake','Documents awaiting review','Allergies requiring acknowledgement'], landing: '/patients' },
  doctor: { permissions: ['patient:view','patient:edit','clinical:edit','clinical:approve','mdt:send','financial:view-limited'], modules: ['doctor-opd','care-plan','opd-scribe','lab','radiology','documents','nexus','mdt','patient-journey','finance'], actions: ['complete-opd','order-investigations','review-results','send-mdt','draft-care-plan'], patientAccess: 'all-demo', queue: ["Today's patients",'Labs awaiting review','Imaging awaiting review','Patients awaiting staging','Care plans awaiting confirmation','MDT-ready patients'], landing: '/patients' },
  'surgical-oncology': { permissions: ['patient:view','clinical:edit','clinical:approve','mdt:send'], modules: ['doctor-opd','care-plan','nexus','mdt'], actions: ['complete-opd','review-results','send-mdt','draft-care-plan'], patientAccess: 'assigned-case', queue: ['Cases for surgical review','Operability review pending','Post-operative follow-up','MDT cases'], landing: '/patients' },
  'radiation-oncology': { permissions: ['patient:view','clinical:edit','clinical:approve','mdt:send'], modules: ['doctor-opd','care-plan','nexus','mdt'], actions: ['complete-opd','review-results','send-mdt','draft-care-plan'], patientAccess: 'assigned-case', queue: ['Radiotherapy referrals','Planning review pending','Consent review','MDT cases'], landing: '/patients' },
  radiologist: { permissions: ['patient:view','clinical:edit'], modules: ['radiology','nexus','mdt'], actions: ['review-results','complete-radiology'], patientAccess: 'assigned-case', queue: ['Studies awaiting interpretation','Comparison studies','Reports requiring review','MDT imaging review'], landing: '/patients' },
  radiology: { permissions: ['patient:view','clinical:edit'], modules: ['radiology'], actions: ['complete-radiology'], patientAccess: 'all-demo', queue: ['Orders awaiting scheduling','Patients awaiting imaging','Reports pending','Reports completed'], landing: '/patients' },
  pathologist: { permissions: ['patient:view','clinical:edit'], modules: ['pathology','nexus','mdt'], actions: ['review-results'], patientAccess: 'assigned-case', queue: ['Pathology cases awaiting review','Molecular results pending','Reports requiring correlation','MDT pathology review'], landing: '/patients' },
  lab: { permissions: ['patient:view','clinical:edit'], modules: ['lab'], actions: ['complete-lab'], patientAccess: 'all-demo', queue: ['Orders','Samples pending','Partner lab status','Reports received','Reports awaiting clinical review'], landing: '/patients' },
  'infusion-nurse': { permissions: ['patient:view','clinical:edit'], modules: ['treatment-day','nexus'], actions: ['review-results','create-follow-up'], patientAccess: 'assigned-case', queue: ['Patients due for infusion','Treatment readiness checks','Supportive care tasks','Post-infusion follow-up'], landing: '/patients' },
  'mdt-coordinator': { permissions: ['patient:view','clinical:edit'], modules: ['mdt','nexus'], actions: ['assemble-packet','schedule-board'], patientAccess: 'all-demo', queue: ['Upcoming boards','Cases awaiting packet completion','Cases awaiting review','Decisions awaiting follow-up'], landing: '/patients' },
  'mdt-clinician': { permissions: ['patient:view','mdt:comment'], modules: ['mdt','nexus'], actions: ['comment-mdt'], patientAccess: 'assigned-case', queue: ['Assigned case for review','Permitted evidence','Comments awaiting submission','Opinion awaiting signature'], landing: '/patients' },
  navigator: { permissions: ['patient:view','financial:view-limited'], modules: ['care-coordination','finance','nexus'], actions: ['create-follow-up'], patientAccess: 'limited', queue: ['Patients needing contact','Appointments pending confirmation','Overdue follow-up','Financial counselling referrals'], landing: '/patients' },
  finance: { permissions: ['patient:view','financial:view-full'], modules: ['finance','estimates-clearance'], actions: ['update-finance'], patientAccess: 'limited', queue: ['Counselling pending','Estimates pending','Insurance authorization','Financial clearance'], landing: '/patients' },
  admin: { permissions: ['admin:view','patient:view'], modules: ['workflow-operations','users-roles','audit-activity','settings'], actions: [], patientAccess: 'all-demo', queue: ['Operational queues','Workflow bottlenecks','Users and roles','Audit visibility'], landing: '/' },
}

export const routeModule: Record<string, string> = { '/patients':'patients','/registration':'registration','/nurse-intake':'nurse-intake','/doctor-opd':'doctor-opd','/care-plan':'care-plan','/opd-scribe':'opd-scribe','/treatment-day':'treatment-day','/care-coordination':'care-coordination','/estimates-clearance':'estimates-clearance','/workflow-operations':'workflow-operations','/users-roles':'users-roles','/audit-activity':'audit-activity','/lab':'lab','/radiology':'radiology','/pathology':'pathology','/nexus':'nexus','/mdt-tumour-board':'mdt','/conversion-finance':'finance','/patient-journey':'patient-journey','/documents':'documents','/settings':'settings' }

export function hasPermission(role: DemoRole, permission: Permission) { return roleConfig[role.roleId].permissions.includes(permission) }
export function canViewPatient(role: DemoRole) { return hasPermission(role, 'patient:view') }
export function canEditPatient(role: DemoRole) { return hasPermission(role, 'patient:edit') }
export function canEditClinicalData(role: DemoRole) { return hasPermission(role, 'clinical:edit') }
export function canApprove(role: DemoRole) { return hasPermission(role, 'clinical:approve') }
export function canSendToMDT(role: DemoRole) { return hasPermission(role, 'mdt:send') }
export function canViewFinancialData(role: DemoRole) { return hasPermission(role, 'financial:view-full') }
export function canAccessRoute(role: DemoRole, pathname: string) { const moduleId = routeModule[pathname]; if (moduleId === 'patients') return canViewPatient(role); return !moduleId || roleConfig[role.roleId].modules.includes(moduleId) }
export function canPerformAction(role: DemoRole, action: ActionId) { return roleConfig[role.roleId].actions.includes(action) }
