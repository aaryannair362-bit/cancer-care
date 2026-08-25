// Database table types generated from Supabase schema

export type Doctor = {
  id: string
  name: string
  specialization: string
  phone: string
  email: string
  created_at: string
  updated_at: string
}

export type Patient = {
  id: string
  name: string
  phone: string
  age: number | null
  preferred_language: string
  created_at: string
  updated_at: string
}

export type AvailabilitySlot = {
  id: string
  doctor_id: string
  slot_date: string // YYYY-MM-DD
  start_time: string // HH:MM:SS
  end_time: string // HH:MM:SS
  is_blocked: boolean
  reason: string | null
  created_at: string
  updated_at: string
}

export type AppointmentMode = 'in-person' | 'video' | 'call-booked'
export type AppointmentStatus =
  | 'upcoming'
  | 'in-progress'
  | 'completed'
  | 'missed'
  | 'cancelled'
export type CreatedVia = 'dashboard' | 'ai-voice-agent'

export type Appointment = {
  id: string
  doctor_id: string
  patient_id: string
  scheduled_at: string // ISO 8601 timestamp
  duration_minutes: number
  reason: string | null
  mode: AppointmentMode
  status: AppointmentStatus
  created_via: CreatedVia
  created_at: string
  updated_at: string
}

export type CallLog = {
  id: string
  patient_id: string
  appointment_id: string | null
  call_started_at: string // ISO 8601 timestamp
  call_ended_at: string | null // ISO 8601 timestamp
  transcript: string | null
  outcome: string | null
  escalated_to_human: boolean
  created_at: string
}

// Type for API responses
export type ApiResponse<T> = {
  data: T | null
  error: string | null
}

// Type for paginated responses
export type PaginatedResponse<T> = {
  data: T[]
  count: number
  total: number
  error: string | null
}
