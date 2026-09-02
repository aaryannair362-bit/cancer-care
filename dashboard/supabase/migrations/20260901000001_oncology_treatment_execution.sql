-- Oncology treatment-execution schema (PDF Master To-Do List item 26).
--
-- Mirrors dashboard/lib/oncology/types.ts table-for-table so the client model and the
-- database model never drift. Not yet wired into the running app — dashboard/lib/oncology
-- currently persists to the browser via localStorage, matching this app's existing
-- demo-first pattern (see dashboard/MIGRATION_TO_SUPABASE.md for how the doctor-appointment
-- module made the same transition). Applying this migration and pointing
-- dashboard/lib/oncology/store.tsx at Supabase instead of localStorage is a data-access
-- swap; nothing about the domain model or the screens built on top of it needs to change.
--
-- Deliberately absent from every table below: a calculated-dose column, a dose-threshold
-- or cumulative-dose-limit column, or any constraint that validates a dose value. Doses
-- are stored exactly as the authorizing clinician entered them (see ordered_drug_lines.
-- ordered_dose) and carried forward by foreign key, never recomputed by the database.

create extension if not exists "uuid-ossp";

-- ───────────────────────── Care Plan → Treatment Plan hierarchy ─────────────────────────

create table onco_care_plans (
  id uuid primary key default uuid_generate_v4(),
  patient_id uuid not null,
  status text not null default 'active' check (status in ('active','superseded','closed')),
  intent text not null,
  diagnosis_summary text not null,
  originating_mdt_case_id uuid,
  version integer not null default 1,
  supersedes uuid references onco_care_plans(id),
  change_reason text,
  created_by_user_id text not null,
  created_by_name text not null,
  created_by_role text not null,
  created_at timestamptz not null default now()
);

create table onco_treatment_plans (
  id uuid primary key default uuid_generate_v4(),
  care_plan_id uuid not null references onco_care_plans(id) on delete cascade,
  patient_id uuid not null,
  status text not null default 'active' check (status in ('active','superseded','closed')),
  diagnosis text not null,
  stage text,
  histology text,
  biomarkers text[] not null default '{}',
  intent text not null,
  line_of_therapy text,
  current_disease_status text,
  responsible_specialty text not null check (responsible_specialty in ('medical_oncology','radiation_oncology','surgical_oncology','combined')),
  mdt_case_id uuid,
  version integer not null default 1,
  supersedes uuid references onco_treatment_plans(id),
  change_reason text,
  created_by_user_id text not null,
  created_by_name text not null,
  created_by_role text not null,
  created_at timestamptz not null default now()
);

create table onco_treatment_phases (
  id uuid primary key default uuid_generate_v4(),
  treatment_plan_id uuid not null references onco_treatment_plans(id) on delete cascade,
  sequence integer not null,
  modality text not null check (modality in ('systemic','radiation','surgical','combined_modality','supportive')),
  label text not null,
  regimen_or_procedure_ref text,
  planned_start date,
  duration_description text,
  status text not null,
  responsible_clinician_user_id text not null,
  responsible_clinician_name text not null,
  responsible_clinician_role text not null
);

-- ───────────────────────── MDT ─────────────────────────

create table onco_mdt_cases (
  id uuid primary key default uuid_generate_v4(),
  patient_id uuid not null,
  status text not null default 'scheduled' check (status in ('scheduled','discussed','recommendation_recorded','plan_created')),
  mdt_date date not null,
  cancer_diagnosis text not null,
  stage text,
  performance_status text,
  pathology_biomarkers text[] not null default '{}',
  treatment_intent text not null,
  recommendation text,
  specialty_responsible text check (specialty_responsible in ('medical_oncology','radiation_oncology','surgical_oncology','combined')),
  alternative_options_discussed text,
  rationale text,
  final_consensus text,
  outstanding_investigations text[] not null default '{}',
  proposed_by_user_id text not null,
  proposed_by_name text not null,
  approved_by_user_id text,
  approved_by_name text,
  approved_at timestamptz,
  linked_medical_oncology_plan_id uuid references onco_treatment_plans(id),
  linked_radiation_oncology_plan_id uuid references onco_treatment_plans(id),
  linked_surgical_plan_id uuid references onco_treatment_plans(id),
  linked_combined_plan_id uuid references onco_treatment_plans(id),
  created_at timestamptz not null default now()
);

create table onco_mdt_participants (
  id uuid primary key default uuid_generate_v4(),
  mdt_case_id uuid not null references onco_mdt_cases(id) on delete cascade,
  user_id text not null,
  name text not null,
  specialty text not null,
  attendance text not null check (attendance in ('present','remote','apologies'))
);

-- ───────────────────────── Regimen library (reference content — no dose calculation) ─────────────────────────

create table onco_regimens (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  cancer_indication text not null,
  intent_setting text not null,
  schedule_description text,
  planned_cycles integer,
  premedications text[] not null default '{}',
  hydration text[] not null default '{}',
  supportive_therapy text[] not null default '{}',
  treatment_hold_parameter_references text[] not null default '{}',
  "references" text[] not null default '{}',
  version integer not null default 1,
  effective_date date,
  approved_by_user_id text not null,
  approved_by_name text not null,
  status text not null default 'active' check (status in ('active','retired'))
);

create table onco_regimen_drug_lines (
  id uuid primary key default uuid_generate_v4(),
  regimen_id uuid not null references onco_regimens(id) on delete cascade,
  sequence integer not null,
  generic_drug_name text not null,
  dose_basis_description text not null, -- reference text only, e.g. "60 mg/m^2" — not a formula
  route text not null,
  diluent text,
  infusion_rate text,
  infusion_duration text,
  is_premedication boolean not null default false,
  is_supportive boolean not null default false
);

-- ───────────────────────── Treatment Order → OrderedItem ─────────────────────────

create table onco_treatment_orders (
  id uuid primary key default uuid_generate_v4(),
  patient_id uuid not null,
  treatment_plan_id uuid not null references onco_treatment_plans(id),
  status text not null default 'draft',
  regimen_id uuid references onco_regimens(id),
  regimen_name text,
  diagnosis text not null,
  treatment_intent text not null,
  line_of_therapy text,
  cycle_number integer not null,
  day integer not null,
  planned_number_of_cycles integer,
  protocol_reference_version text,
  eligibility_parameters_checked jsonb not null default '[]', -- [{parameter, valuePresent, clinicianReviewed, note}]
  height_cm numeric,
  weight_kg numeric,
  bsa_m2 text,
  allergies_acknowledged boolean not null default false,
  ordering_clinician_user_id text not null,
  ordering_clinician_name text not null,
  authorized_at timestamptz,
  created_at timestamptz not null default now()
);

create table onco_ordered_drug_lines (
  id uuid primary key default uuid_generate_v4(),
  order_id uuid not null references onco_treatment_orders(id) on delete cascade,
  sequence integer not null,
  generic_drug_name text not null,
  dose_basis_description text,
  ordered_dose text not null, -- the single carried-forward dose value; never recomputed
  route text not null,
  diluent text,
  diluent_volume text,
  infusion_rate text,
  infusion_duration text,
  administration_date_time timestamptz,
  is_premedication boolean not null default false,
  is_supportive boolean not null default false
);

create table onco_dose_modifications (
  id uuid primary key default uuid_generate_v4(),
  order_id uuid not null references onco_treatment_orders(id) on delete cascade,
  drug_line_id uuid not null references onco_ordered_drug_lines(id) on delete cascade,
  type text not null check (type in ('dose_reduction','dose_escalation','treatment_delay','drug_omission','drug_substitution','cycle_postponement','treatment_discontinuation')),
  reason text not null,
  toxicity_ref uuid,
  relevant_lab_ref text,
  clinical_justification text not null,
  original_dose text not null,
  modified_dose text,
  percent_change text,
  approved_by_user_id text not null,
  approved_by_name text not null,
  "timestamp" timestamptz not null default now()
);

-- ───────────────────────── Verification → Dispense ─────────────────────────

create table onco_verification_checkpoints (
  id uuid primary key default uuid_generate_v4(),
  order_id uuid not null references onco_treatment_orders(id) on delete cascade,
  patient_identity_confirmed boolean not null default false,
  drug_confirmed boolean not null default false,
  dose_confirmed boolean not null default false,
  route_confirmed boolean not null default false,
  sequence_confirmed boolean not null default false,
  cycle_day_confirmed boolean not null default false,
  allergies_reviewed boolean not null default false,
  required_labs_present boolean not null default false,
  expiry_checked boolean not null default false,
  verified_by_user_id text not null,
  verified_by_name text not null,
  verified_at timestamptz not null default now(),
  outcome text not null check (outcome in ('verified','query_raised','rejected')),
  query_reason text
);

create table onco_dispense_records (
  id uuid primary key default uuid_generate_v4(),
  order_id uuid not null references onco_treatment_orders(id) on delete cascade,
  drug_line_id uuid not null references onco_ordered_drug_lines(id) on delete cascade,
  patient_id uuid not null,
  status text not null default 'pending_review' check (status in ('pending_review','verified','preparation','prepared','dispensed','held','rejected','cancelled')),
  available_formulation_strength text,
  quantity_required text,
  diluent text,
  volume text,
  preparation_instructions text,
  batch_lot text,
  expiry date,
  prepared_by_user_id text,
  prepared_by_name text,
  prepared_at timestamptz,
  verified_by_user_id text,
  verified_by_name text,
  verified_at timestamptz,
  dispensed_at timestamptz,
  destination text,
  wastage_quantity text,
  wastage_reason text,
  wastage_recorded_by_name text,
  wastage_recorded_at timestamptz,
  hold_or_query_reason text
);

-- ───────────────────────── Administration / MAR ─────────────────────────

create table onco_pre_administration_checklists (
  id uuid primary key default uuid_generate_v4(),
  order_id uuid not null references onco_treatment_orders(id) on delete cascade,
  two_patient_identifiers_confirmed boolean not null default false,
  order_verified boolean not null default false,
  consent_confirmed boolean not null default false,
  allergy_verified boolean not null default false,
  pre_treatment_vitals_recorded boolean not null default false,
  required_labs_available boolean not null default false,
  venous_access_confirmed boolean not null default false,
  pharmacy_prepared_medication_confirmed boolean not null default false,
  confirmed_by_user_id text not null,
  confirmed_by_name text not null,
  confirmed_at timestamptz not null default now()
);

create table onco_mar_entries (
  id uuid primary key default uuid_generate_v4(),
  order_id uuid not null references onco_treatment_orders(id) on delete cascade,
  drug_line_id uuid not null references onco_ordered_drug_lines(id),
  sequence integer not null,
  drug text not null,
  dose_given text not null, -- should equal the order's ordered_dose unless a dose_modification exists
  start_time time,
  end_time time,
  rate text,
  route text not null,
  line_access text,
  administered_by_user_id text not null,
  administered_by_name text not null,
  infusion_status text not null default 'not_started' check (infusion_status in ('not_started','in_progress','paused','completed','held','discontinued')),
  reaction_or_toxicity text,
  intervention text,
  variance_from_order text
);

create table onco_post_administration_records (
  id uuid primary key default uuid_generate_v4(),
  order_id uuid not null references onco_treatment_orders(id) on delete cascade,
  completion_status text not null check (completion_status in ('completed','partially_completed','held','deferred','discontinued')),
  post_treatment_vitals text,
  discharge_instructions text,
  next_cycle_date date,
  recorded_by_user_id text not null,
  recorded_by_name text not null,
  recorded_at timestamptz not null default now()
);

-- ───────────────────────── Toxicity (CTCAE-shaped) / Response (RECIST-shaped) ─────────────────────────

create table onco_toxicity_events (
  id uuid primary key default uuid_generate_v4(),
  patient_id uuid not null,
  order_id uuid references onco_treatment_orders(id),
  term_code text not null,
  term_system text not null default 'CTCAE-5',
  term_display text not null,
  grade smallint not null check (grade between 1 and 5),
  onset timestamptz not null,
  relationship_to_therapy text not null check (relationship_to_therapy in ('unrelated','unlikely','possible','probable','definite')),
  intervention text,
  treatment_modification_id uuid references onco_dose_modifications(id),
  outcome text not null check (outcome in ('resolved','resolving','ongoing','resolved_with_sequelae','fatal')),
  recorded_by_user_id text not null,
  recorded_by_name text not null,
  recorded_at timestamptz not null default now()
);

create table onco_response_assessments (
  id uuid primary key default uuid_generate_v4(),
  patient_id uuid not null,
  framework_name text not null default 'RECIST 1.1',
  assessment_date date not null,
  imaging_date date,
  lesions jsonb not null default '[]', -- [{id, site, type, baselineMeasurementMm, followUpMeasurementMm}]
  response_category text not null check (response_category in ('CR','PR','SD','PD','not_evaluable')),
  disease_status text,
  relevant_biomarkers text[] not null default '{}',
  assessed_by_user_id text not null,
  assessed_by_name text not null
);

-- ───────────────────────── Radiation Oncology ─────────────────────────

create table onco_radiation_prescriptions (
  id uuid primary key default uuid_generate_v4(),
  patient_id uuid not null,
  treatment_plan_id uuid not null references onco_treatment_plans(id),
  status text not null default 'draft',
  rt_sub_status text not null default 'prescribed',
  diagnosis text not null,
  treatment_site text not null,
  laterality text,
  intent text not null,
  modality text,
  technique text,
  treatment_phase text,
  total_prescribed_dose_gy text,
  dose_per_fraction_gy text,
  number_of_fractions integer,
  frequency text,
  start_date date,
  concurrent_systemic_treatment text,
  target_volumes text[] not null default '{}',
  organs_at_risk text[] not null default '{}',
  simulation_required boolean not null default true,
  immobilization text,
  image_guidance_required boolean not null default false,
  bolus text,
  special_instructions text,
  dicom_rt_plan_ref text, -- reference into an external planning/OIS system; no DICOM content stored here
  physician_approved_by_user_id text,
  physician_approved_by_name text,
  physician_approved_at timestamptz,
  created_by_user_id text not null,
  created_by_name text not null,
  created_at timestamptz not null default now()
);

create table onco_radiation_fractions (
  id uuid primary key default uuid_generate_v4(),
  prescription_id uuid not null references onco_radiation_prescriptions(id) on delete cascade,
  fraction_number integer not null,
  scheduled_date date not null,
  status text not null default 'scheduled' check (status in ('scheduled','delivered','missed','rescheduled')),
  delivered_dose_gy text,
  delivered_at timestamptz,
  delivered_by_user_id text,
  delivered_by_name text,
  interruption_reason text,
  on_treatment_review_note text
);

-- ───────────────────────── Surgical Oncology ─────────────────────────

create table onco_surgical_plans (
  id uuid primary key default uuid_generate_v4(),
  patient_id uuid not null,
  treatment_plan_id uuid not null references onco_treatment_plans(id),
  status text not null default 'draft',
  surgical_sub_status text not null default 'recommended',
  procedure text not null,
  indication text,
  intent text,
  anatomical_site text not null,
  laterality text,
  proposed_extent text,
  approach text,
  nodal_procedure text,
  reconstruction text,
  planned_date date,
  priority text not null default 'routine' check (priority in ('routine','urgent','emergency')),
  pre_op_requirements text[] not null default '{}',
  required_imaging_pathology text[] not null default '{}',
  anaesthesia_clearance text,
  blood_requirement text,
  special_instructions text,
  performed_procedure text, -- distinct from `procedure` once surgery actually happens
  performed_at timestamptz,
  operative_findings text,
  histopathology_available boolean not null default false,
  histopathology_summary text,
  fed_back_to_mdt_case_id uuid references onco_mdt_cases(id),
  recommended_by_user_id text not null,
  recommended_by_name text not null,
  created_at timestamptz not null default now()
);

-- ───────────────────────── Journey (simplified, department-level) ─────────────────────────

create table onco_journey_milestones (
  id uuid primary key default uuid_generate_v4(),
  patient_id uuid not null,
  department text not null check (department in ('registration','nurse_intake','medical_oncology','radiology','pathology','surgical_oncology','radiation_oncology','mdt_tumour_board','day_care_infusion','pharmacy','surgery','radiation_treatment','follow_up')),
  label text not null,
  milestone_date date,
  clinician_user_id text,
  clinician_name text,
  status text not null,
  is_current boolean not null default false
);

-- ───────────────────────── Audit trail (append-only) ─────────────────────────

create table onco_audit_log (
  id uuid primary key default uuid_generate_v4(),
  entity_type text not null,
  entity_id uuid not null,
  action text not null,
  actor_user_id text not null,
  actor_name text not null,
  actor_role text not null,
  reason text,
  previous_value text,
  new_value text,
  "timestamp" timestamptz not null default now()
);
-- No update/delete policy is defined for this table anywhere in this migration —
-- it is append-only by omission, not just by convention (item 20).

-- ───────────────────────── Indexes ─────────────────────────

create index idx_onco_care_plans_patient on onco_care_plans(patient_id);
create index idx_onco_treatment_plans_patient on onco_treatment_plans(patient_id);
create index idx_onco_treatment_orders_patient on onco_treatment_orders(patient_id);
create index idx_onco_treatment_orders_status on onco_treatment_orders(status);
create index idx_onco_ordered_drug_lines_order on onco_ordered_drug_lines(order_id);
create index idx_onco_dispense_records_order on onco_dispense_records(order_id);
create index idx_onco_mar_entries_order on onco_mar_entries(order_id);
create index idx_onco_toxicity_events_patient on onco_toxicity_events(patient_id);
create index idx_onco_response_assessments_patient on onco_response_assessments(patient_id);
create index idx_onco_radiation_fractions_prescription on onco_radiation_fractions(prescription_id);
create index idx_onco_journey_milestones_patient on onco_journey_milestones(patient_id);
create index idx_onco_audit_log_entity on onco_audit_log(entity_type, entity_id);

-- ───────────────────────── Row-level security ─────────────────────────
-- Enabled on every table; policies match this repo's existing convention of a
-- service-role read/write path (see 20240802000001_init_healthcare_schema.sql) as the
-- placeholder until per-role clinical policies (item 27's action-level RBAC, expressed
-- as RLS) are defined with the hospital's actual identity/auth provider.

do $$
declare
  t text;
begin
  for t in
    select unnest(array[
      'onco_care_plans','onco_treatment_plans','onco_treatment_phases','onco_mdt_cases','onco_mdt_participants',
      'onco_regimens','onco_regimen_drug_lines','onco_treatment_orders','onco_ordered_drug_lines','onco_dose_modifications',
      'onco_verification_checkpoints','onco_dispense_records','onco_pre_administration_checklists','onco_mar_entries',
      'onco_post_administration_records','onco_toxicity_events','onco_response_assessments','onco_radiation_prescriptions',
      'onco_radiation_fractions','onco_surgical_plans','onco_journey_milestones','onco_audit_log'
    ])
  loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy "Service role full access" on %I for all using (auth.role() = ''service_role'') with check (auth.role() = ''service_role'')', t);
  end loop;
end $$;
