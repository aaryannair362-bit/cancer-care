-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- Doctors table
create table doctors (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  specialization text not null,
  phone text not null,
  email text not null unique,
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null
);

-- Patients table
create table patients (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  phone text not null unique,
  age integer,
  preferred_language text default 'en',
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null
);

-- Availability slots table
create table availability_slots (
  id uuid primary key default uuid_generate_v4(),
  doctor_id uuid not null references doctors(id) on delete cascade,
  slot_date date not null,
  start_time time not null,
  end_time time not null,
  is_blocked boolean default false,
  reason text,
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null,
  constraint valid_time_range check (start_time < end_time)
);

-- Appointments table
create table appointments (
  id uuid primary key default uuid_generate_v4(),
  doctor_id uuid not null references doctors(id) on delete cascade,
  patient_id uuid not null references patients(id) on delete cascade,
  scheduled_at timestamp with time zone not null,
  duration_minutes integer not null default 30,
  reason text,
  mode text not null default 'call-booked' check (mode in ('in-person', 'video', 'call-booked')),
  status text not null default 'upcoming' check (status in ('upcoming', 'in-progress', 'completed', 'missed', 'cancelled')),
  created_via text not null default 'ai-voice-agent' check (created_via in ('dashboard', 'ai-voice-agent')),
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null
);

-- Call logs table
create table call_logs (
  id uuid primary key default uuid_generate_v4(),
  patient_id uuid not null references patients(id) on delete cascade,
  appointment_id uuid references appointments(id) on delete set null,
  call_started_at timestamp with time zone not null,
  call_ended_at timestamp with time zone,
  transcript text,
  outcome text,
  escalated_to_human boolean default false,
  created_at timestamp with time zone default now() not null
);

-- Create indexes for common queries
create index idx_doctors_email on doctors(email);
create index idx_patients_phone on patients(phone);
create index idx_availability_slots_doctor_id_date on availability_slots(doctor_id, slot_date);
create index idx_appointments_doctor_id on appointments(doctor_id);
create index idx_appointments_patient_id on appointments(patient_id);
create index idx_appointments_scheduled_at on appointments(scheduled_at);
create index idx_appointments_status on appointments(status);
create index idx_call_logs_patient_id on call_logs(patient_id);
create index idx_call_logs_appointment_id on call_logs(appointment_id);
create index idx_call_logs_started_at on call_logs(call_started_at);

-- Enable RLS
alter table doctors enable row level security;
alter table patients enable row level security;
alter table availability_slots enable row level security;
alter table appointments enable row level security;
alter table call_logs enable row level security;

-- RLS Policies for doctors table
-- Doctors can only see their own profile
create policy "Doctors can read own profile"
  on doctors for select
  using (auth.uid()::text = id::text);

-- Allow service role (voice agent) to read all doctors
create policy "Service role can read all doctors"
  on doctors for select
  using (auth.role() = 'service_role');

-- Allow service role to insert and update doctors
create policy "Service role can insert doctors"
  on doctors for insert
  with check (auth.role() = 'service_role');

create policy "Service role can update doctors"
  on doctors for update
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');

-- RLS Policies for patients table
-- Service role can read all patients
create policy "Service role can read all patients"
  on patients for select
  using (auth.role() = 'service_role');

-- Service role can insert and update patients
create policy "Service role can insert patients"
  on patients for insert
  with check (auth.role() = 'service_role');

create policy "Service role can update patients"
  on patients for update
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');

-- RLS Policies for availability_slots table
-- Doctors can only see their own availability slots
create policy "Doctors can read own availability slots"
  on availability_slots for select
  using (doctor_id = auth.uid());

-- Allow service role to read all availability slots
create policy "Service role can read all availability slots"
  on availability_slots for select
  using (auth.role() = 'service_role');

-- Doctors can insert availability slots for themselves
create policy "Doctors can insert own availability slots"
  on availability_slots for insert
  with check (doctor_id = auth.uid());

-- Allow service role to insert availability slots
create policy "Service role can insert availability slots"
  on availability_slots for insert
  with check (auth.role() = 'service_role');

-- Doctors can update only their own availability slots
create policy "Doctors can update own availability slots"
  on availability_slots for update
  using (doctor_id = auth.uid())
  with check (doctor_id = auth.uid());

-- Allow service role to update availability slots
create policy "Service role can update availability slots"
  on availability_slots for update
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');

-- RLS Policies for appointments table
-- Doctors can only see their own appointments
create policy "Doctors can read own appointments"
  on appointments for select
  using (doctor_id = auth.uid());

-- Allow service role to read all appointments
create policy "Service role can read all appointments"
  on appointments for select
  using (auth.role() = 'service_role');

-- Doctors can insert appointments for themselves
create policy "Doctors can insert own appointments"
  on appointments for insert
  with check (doctor_id = auth.uid());

-- Allow service role to insert appointments
create policy "Service role can insert appointments"
  on appointments for insert
  with check (auth.role() = 'service_role');

-- Doctors can update only their own appointments
create policy "Doctors can update own appointments"
  on appointments for update
  using (doctor_id = auth.uid())
  with check (doctor_id = auth.uid());

-- Allow service role to update appointments
create policy "Service role can update appointments"
  on appointments for update
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');

-- RLS Policies for call_logs table
-- Service role can read all call logs
create policy "Service role can read all call logs"
  on call_logs for select
  using (auth.role() = 'service_role');

-- Service role can insert call logs
create policy "Service role can insert call logs"
  on call_logs for insert
  with check (auth.role() = 'service_role');

-- Service role can update call logs
create policy "Service role can update call logs"
  on call_logs for update
  using (auth.role() = 'service_role')
  with check (auth.role() = 'service_role');
