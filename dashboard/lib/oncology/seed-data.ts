/**
 * Seed data for the oncology domain store — consistent with the existing demo patient
 * (Sunita Patil, DEMO-ONC-02481) already established in demo-access-provider.tsx and
 * care-plan/page.tsx, so every screen tells the same story instead of contradicting
 * itself (PDF item 39's data-consistency requirement, applied from day one).
 */

import type {
  ActorRef, CarePlan, MDTCase, Regimen, SurgicalPlan, TreatmentOrder, TreatmentPlan,
} from './types'

export const DEMO_PATIENT_ID = 'sunita-patil'

const drKavyaMenon: ActorRef = { userId: 'dr-kavya-menon', name: 'Dr. Kavya Menon', roleLabel: 'Medical Oncologist' }
const drSameerKulkarni: ActorRef = { userId: 'dr-sameer-kulkarni', name: 'Dr. Sameer Kulkarni', roleLabel: 'Surgical Oncologist' }
const drNishaThomas: ActorRef = { userId: 'dr-nisha-thomas', name: 'Dr. Nisha Thomas', roleLabel: 'Radiation Oncologist' }
const rahulSen: ActorRef = { userId: 'rahul-sen', name: 'Rahul Sen', roleLabel: 'MDT Coordinator' }
const pharmacistDemo: ActorRef = { userId: 'pharmacist-demo', name: 'Oncology Pharmacy', roleLabel: 'Oncology Pharmacist' }

export const seedRegimens: Regimen[] = [
  {
    id: 'regimen-ac-adjuvant-breast',
    name: 'AC — Doxorubicin + Cyclophosphamide (Adjuvant, Breast)',
    cancerIndication: 'Breast cancer — adjuvant',
    intentSetting: 'adjuvant',
    drugSequence: [
      { sequence: 1, genericDrugName: 'Ondansetron', doseBasisDescription: 'fixed', route: 'Intravenous', isPremedication: true },
      { sequence: 2, genericDrugName: 'Dexamethasone', doseBasisDescription: 'fixed', route: 'Intravenous', isPremedication: true },
      { sequence: 3, genericDrugName: 'Doxorubicin', doseBasisDescription: '60 mg/m² (reference)', route: 'Intravenous', diluent: 'Normal saline', infusionRate: 'Slow IV push / short infusion per protocol' },
      { sequence: 4, genericDrugName: 'Cyclophosphamide', doseBasisDescription: '600 mg/m² (reference)', route: 'Intravenous', diluent: 'Normal saline', infusionDuration: '30–60 minutes' },
    ],
    scheduleDescription: 'Every 21 days',
    plannedCycles: 4,
    premedications: ['Ondansetron 8 mg IV', 'Dexamethasone 12 mg IV'],
    hydration: ['Standard IV hydration per unit protocol'],
    supportiveTherapy: ['Antiemetic prophylaxis', 'Growth factor support per unit protocol where indicated'],
    treatmentHoldParameterReferences: ['CBC with ANC', 'Platelet count', 'Renal function', 'Hepatic function', 'Performance status'],
    references: ['Institutional adjuvant breast protocol'],
    version: 1,
    effectiveDate: '2026-01-01',
    approvedBy: pharmacistDemo,
    status: 'active',
  },
]

export const seedMdtCase: MDTCase = {
  id: 'mdt-case-sunita-1',
  patientId: DEMO_PATIENT_ID,
  status: 'plan_created',
  mdtDate: '2026-06-20',
  cancerDiagnosis: 'Left breast invasive ductal carcinoma',
  stage: 'Stage IIA (pT2N0M0)',
  performanceStatus: 'ECOG 1',
  pathologyBiomarkers: ['ER positive', 'PR positive', 'HER2 negative'],
  treatmentIntent: 'adjuvant',
  recommendation: 'Adjuvant AC chemotherapy (4 cycles) followed by radiation oncology review; endocrine therapy to be confirmed after systemic therapy.',
  specialtyResponsible: 'medical_oncology',
  alternativeOptionsDiscussed: 'Dose-dense AC-T regimen considered and deferred given performance status and patient preference discussion.',
  rationale: 'Node-negative, hormone-receptor-positive, HER2-negative disease following breast-conserving surgery; adjuvant systemic therapy per institutional protocol.',
  participants: [
    { ...drKavyaMenon, specialty: 'Medical Oncology', attendance: 'present' },
    { ...drSameerKulkarni, specialty: 'Surgical Oncology', attendance: 'present' },
    { ...drNishaThomas, specialty: 'Radiation Oncology', attendance: 'present' },
    { ...rahulSen, specialty: 'MDT Coordination', attendance: 'present' },
  ],
  finalConsensus: 'Proceed with adjuvant AC chemotherapy; radiation oncology to review after systemic therapy completion.',
  outstandingInvestigations: [],
  proposedBy: drKavyaMenon,
  approvedBy: drKavyaMenon,
  approvedAt: '2026-06-20T11:30:00+05:30',
  linkedPlanIds: { medicalOncology: 'treatment-plan-sunita-1' },
  createdAt: '2026-06-20T10:00:00+05:30',
}

export const seedCarePlan: CarePlan = {
  id: 'care-plan-sunita-1',
  patientId: DEMO_PATIENT_ID,
  status: 'active',
  intent: 'adjuvant',
  diagnosisSummary: 'Left breast invasive ductal carcinoma, Stage IIA (pT2N0M0), ER/PR positive, HER2 negative',
  originatingMdtCaseId: seedMdtCase.id,
  version: 1,
  createdBy: drKavyaMenon,
  createdAt: '2026-06-20T11:35:00+05:30',
}

export const seedTreatmentPlan: TreatmentPlan = {
  id: 'treatment-plan-sunita-1',
  carePlanId: seedCarePlan.id,
  patientId: DEMO_PATIENT_ID,
  status: 'active',
  diagnosis: 'Left breast invasive ductal carcinoma',
  stage: 'Stage IIA (pT2N0M0)',
  histology: 'Grade 2 invasive ductal carcinoma',
  biomarkers: ['ER positive', 'PR positive', 'HER2 negative'],
  intent: 'adjuvant',
  lineOfTherapy: 'First line — adjuvant',
  currentDiseaseStatus: 'No evidence of residual disease post-surgery',
  responsibleSpecialty: 'medical_oncology',
  mdtCaseId: seedMdtCase.id,
  phases: [
    { id: 'phase-1', sequence: 1, modality: 'surgical', label: 'Breast-conserving surgery', regimenOrProcedureRef: 'Lumpectomy with sentinel-node assessment', plannedStart: '2026-06-12', durationDescription: 'Single procedure', status: 'completed', responsibleClinician: drSameerKulkarni },
    { id: 'phase-2', sequence: 2, modality: 'systemic', label: 'Adjuvant AC chemotherapy', regimenOrProcedureRef: seedRegimens[0].id, plannedStart: '2026-07-18', durationDescription: '4 cycles, every 21 days', status: 'in_progress', responsibleClinician: drKavyaMenon },
    { id: 'phase-3', sequence: 3, modality: 'radiation', label: 'Radiation oncology review', durationDescription: 'After systemic therapy completion', status: 'proposed', responsibleClinician: drNishaThomas },
  ],
  version: 1,
  createdBy: drKavyaMenon,
  createdAt: '2026-06-20T11:40:00+05:30',
}

export const seedTreatmentOrder: TreatmentOrder = {
  id: 'order-sunita-cycle2',
  patientId: DEMO_PATIENT_ID,
  treatmentPlanId: seedTreatmentPlan.id,
  status: 'ordered',
  regimenId: seedRegimens[0].id,
  regimenName: seedRegimens[0].name,
  diagnosis: 'Left breast invasive ductal carcinoma',
  treatmentIntent: 'adjuvant',
  lineOfTherapy: 'First line — adjuvant',
  cycleNumber: 2,
  day: 1,
  plannedNumberOfCycles: 4,
  protocolReferenceVersion: `${seedRegimens[0].id} · v${seedRegimens[0].version}`,
  drugLines: [
    { id: 'line-premed-1', sequence: 1, genericDrugName: 'Ondansetron', doseBasisDescription: 'fixed', orderedDose: '8 mg', route: 'Intravenous', isPremedication: true, doseModifications: [] },
    { id: 'line-premed-2', sequence: 2, genericDrugName: 'Dexamethasone', doseBasisDescription: 'fixed', orderedDose: '12 mg', route: 'Intravenous', isPremedication: true, doseModifications: [] },
    { id: 'line-drug-1', sequence: 3, genericDrugName: 'Doxorubicin', doseBasisDescription: '60 mg/m² (reference)', orderedDose: '103 mg', route: 'Intravenous', diluent: 'Normal saline', doseModifications: [] },
    { id: 'line-drug-2', sequence: 4, genericDrugName: 'Cyclophosphamide', doseBasisDescription: '600 mg/m² (reference)', orderedDose: '1030 mg', route: 'Intravenous', diluent: 'Normal saline', infusionDuration: '30–60 minutes', doseModifications: [] },
  ],
  eligibilityParametersChecked: [
    { parameter: 'CBC with ANC', valuePresent: true, clinicianReviewed: true, note: 'ANC 0.7 ×10⁹/L — low, clinician review' },
    { parameter: 'Platelet count', valuePresent: true, clinicianReviewed: true, note: '138 ×10⁹/L — acceptable range' },
    { parameter: 'Renal function', valuePresent: true, clinicianReviewed: true, note: 'Creatinine 0.82 mg/dL' },
    { parameter: 'Hepatic function', valuePresent: true, clinicianReviewed: true, note: 'AST 28 · ALT 31 U/L' },
    { parameter: 'Pregnancy', valuePresent: false, clinicianReviewed: false, note: 'Not applicable per clinician assessment' },
  ],
  heightCm: 160,
  weightKg: 61.4,
  bsaM2: '1.67',
  allergiesAcknowledged: true,
  orderingClinician: drKavyaMenon,
  authorizedAt: '2026-08-24T09:15:00+05:30',
  createdAt: '2026-08-24T09:10:00+05:30',
}

export const seedSurgicalPlan: SurgicalPlan = {
  id: 'surgical-plan-sunita-1',
  patientId: DEMO_PATIENT_ID,
  treatmentPlanId: seedTreatmentPlan.id,
  status: 'completed',
  surgicalSubStatus: 'histopathology_available',
  procedure: 'Breast-conserving surgery with sentinel-node assessment',
  indication: 'Left breast invasive ductal carcinoma, cT2N0',
  intent: 'Curative',
  anatomicalSite: 'Left breast',
  laterality: 'Left',
  proposedExtent: 'Lumpectomy with sentinel lymph-node biopsy',
  approach: 'Open',
  nodalProcedure: 'Sentinel lymph-node biopsy',
  plannedDate: '2026-06-12',
  priority: 'routine',
  preOpRequirements: ['Laboratory review', 'Imaging review', 'Anaesthetic fitness', 'Consent'],
  requiredImagingPathology: ['Diagnostic mammogram', 'Core biopsy report'],
  performedProcedure: 'Left breast lumpectomy with sentinel lymph-node biopsy (2 nodes, both negative)',
  performedAt: '2026-06-12T09:00:00+05:30',
  operativeFindings: 'Clear margins achieved; no intraoperative complications.',
  histopathologyAvailable: true,
  histopathologySummary: 'Grade 2 invasive ductal carcinoma, pT2N0M0, ER/PR positive, HER2 negative, margins clear.',
  fedBackToMdtCaseId: seedMdtCase.id,
  recommendedBy: drSameerKulkarni,
  createdAt: '2026-05-28T10:00:00+05:30',
}
