/**
 * Mock data shaped exactly like the real database schema.
 * This file is a placeholder — replace with real supabase.from(...) calls later.
 * Delete this file once live Supabase is integrated.
 */

import type { Doctor, Patient, Appointment, AvailabilitySlot } from '../../types'

// Mock doctor (logged-in user)
export const mockLoggedInDoctor: Doctor = {
  id: '550e8400-e29b-41d4-a716-446655440001',
  name: 'Cardiologist',
  specialization: 'Cardiology',
  phone: '+1-415-555-0123',
  email: 'sarah.chen@healthcenter.com',
  created_at: '2024-01-15T10:00:00Z',
  updated_at: '2024-08-02T14:30:00Z',
}

// Mock patients
export const mockPatients: Patient[] = [
  {
    id: '550e8400-e29b-41d4-a716-446655440101',
    name: 'James Wilson',
    phone: '+1-415-555-0201',
    age: 52,
    preferred_language: 'en',
    created_at: '2024-03-10T08:00:00Z',
    updated_at: '2024-08-01T10:15:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440102',
    name: 'Maria Garcia',
    phone: '+1-415-555-0202',
    age: 38,
    preferred_language: 'es',
    created_at: '2024-04-22T09:30:00Z',
    updated_at: '2024-08-02T12:00:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440103',
    name: 'Robert Kim',
    phone: '+1-415-555-0203',
    age: 67,
    preferred_language: 'en',
    created_at: '2024-02-05T11:00:00Z',
    updated_at: '2024-07-28T14:45:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440104',
    name: 'Patricia Lewis',
    phone: '+1-415-555-0204',
    age: 45,
    preferred_language: 'en',
    created_at: '2024-05-18T13:20:00Z',
    updated_at: '2024-08-02T09:10:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440105',
    name: 'David Martinez',
    phone: '+1-415-555-0205',
    age: 55,
    preferred_language: 'es',
    created_at: '2024-01-28T10:45:00Z',
    updated_at: '2024-07-30T16:20:00Z',
  },
]

// Get today's date for appointments
const today = new Date()
today.setHours(0, 0, 0, 0)

// Mock appointments for today
export const mockAppointmentsToday: Appointment[] = [
  {
    id: '550e8400-e29b-41d4-a716-446655440201',
    doctor_id: mockLoggedInDoctor.id,
    patient_id: mockPatients[0].id,
    scheduled_at: new Date(today.getTime() + 9 * 60 * 60 * 1000).toISOString(), // 9 AM
    duration_minutes: 30,
    reason: 'Follow-up: High blood pressure check',
    mode: 'in-person',
    status: 'completed',
    created_via: 'dashboard',
    created_at: '2024-08-01T14:00:00Z',
    updated_at: '2024-08-02T09:15:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440202',
    doctor_id: mockLoggedInDoctor.id,
    patient_id: mockPatients[1].id,
    scheduled_at: new Date(today.getTime() + 9.5 * 60 * 60 * 1000).toISOString(), // 9:30 AM
    duration_minutes: 30,
    reason: 'Initial consultation for chest pain',
    mode: 'call-booked',
    status: 'completed',
    created_via: 'ai-voice-agent',
    created_at: '2024-08-02T07:45:00Z',
    updated_at: '2024-08-02T10:00:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440203',
    doctor_id: mockLoggedInDoctor.id,
    patient_id: mockPatients[2].id,
    scheduled_at: new Date(today.getTime() + 10.5 * 60 * 60 * 1000).toISOString(), // 10:30 AM
    duration_minutes: 45,
    reason: 'Medication review and ECG',
    mode: 'in-person',
    status: 'upcoming',
    created_via: 'dashboard',
    created_at: '2024-08-01T11:00:00Z',
    updated_at: '2024-08-02T14:30:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440204',
    doctor_id: mockLoggedInDoctor.id,
    patient_id: mockPatients[3].id,
    scheduled_at: new Date(today.getTime() + 12 * 60 * 60 * 1000).toISOString(), // 12:00 PM
    duration_minutes: 30,
    reason: 'Annual heart health screening',
    mode: 'video',
    status: 'missed',
    created_via: 'ai-voice-agent',
    created_at: '2024-07-31T16:00:00Z',
    updated_at: '2024-08-02T12:15:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440205',
    doctor_id: mockLoggedInDoctor.id,
    patient_id: mockPatients[4].id,
    scheduled_at: new Date(today.getTime() + 14.5 * 60 * 60 * 1000).toISOString(), // 2:30 PM
    duration_minutes: 30,
    reason: 'Stent follow-up consultation',
    mode: 'in-person',
    status: 'upcoming',
    created_via: 'dashboard',
    created_at: '2024-08-02T08:30:00Z',
    updated_at: '2024-08-02T14:30:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440206',
    doctor_id: mockLoggedInDoctor.id,
    patient_id: mockPatients[0].id,
    scheduled_at: new Date(today.getTime() + 16 * 60 * 60 * 1000).toISOString(), // 4:00 PM
    duration_minutes: 30,
    reason: 'Blood work review',
    mode: 'video',
    status: 'cancelled',
    created_via: 'ai-voice-agent',
    created_at: '2024-08-01T10:00:00Z',
    updated_at: '2024-08-02T11:00:00Z',
  },
]

// Mock availability slots for today
export const mockAvailabilitySlots: AvailabilitySlot[] = [
  {
    id: '550e8400-e29b-41d4-a716-446655440301',
    doctor_id: mockLoggedInDoctor.id,
    slot_date: today.toISOString().split('T')[0],
    start_time: '08:00:00',
    end_time: '09:00:00',
    is_blocked: false,
    reason: null,
    created_at: '2024-08-01T15:00:00Z',
    updated_at: '2024-08-01T15:00:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440302',
    doctor_id: mockLoggedInDoctor.id,
    slot_date: today.toISOString().split('T')[0],
    start_time: '09:00:00',
    end_time: '10:30:00',
    is_blocked: false,
    reason: null,
    created_at: '2024-08-01T15:00:00Z',
    updated_at: '2024-08-01T15:00:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440303',
    doctor_id: mockLoggedInDoctor.id,
    slot_date: today.toISOString().split('T')[0],
    start_time: '10:30:00',
    end_time: '12:00:00',
    is_blocked: false,
    reason: null,
    created_at: '2024-08-01T15:00:00Z',
    updated_at: '2024-08-01T15:00:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440304',
    doctor_id: mockLoggedInDoctor.id,
    slot_date: today.toISOString().split('T')[0],
    start_time: '12:00:00',
    end_time: '13:00:00',
    is_blocked: true,
    reason: 'Lunch',
    created_at: '2024-08-01T15:00:00Z',
    updated_at: '2024-08-01T15:00:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440305',
    doctor_id: mockLoggedInDoctor.id,
    slot_date: today.toISOString().split('T')[0],
    start_time: '13:00:00',
    end_time: '15:00:00',
    is_blocked: false,
    reason: null,
    created_at: '2024-08-01T15:00:00Z',
    updated_at: '2024-08-01T15:00:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440306',
    doctor_id: mockLoggedInDoctor.id,
    slot_date: today.toISOString().split('T')[0],
    start_time: '15:00:00',
    end_time: '17:00:00',
    is_blocked: false,
    reason: null,
    created_at: '2024-08-01T15:00:00Z',
    updated_at: '2024-08-01T15:00:00Z',
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440307',
    doctor_id: mockLoggedInDoctor.id,
    slot_date: today.toISOString().split('T')[0],
    start_time: '17:00:00',
    end_time: '18:00:00',
    is_blocked: false,
    reason: null,
    created_at: '2024-08-01T15:00:00Z',
    updated_at: '2024-08-01T15:00:00Z',
  },
]

/**
 * These functions are placeholders for real Supabase calls.
 * Replace them with supabase.from(...).select(...) once live.
 */

export async function fetchLoggedInDoctor(): Promise<Doctor> {
  return mockLoggedInDoctor
}

export async function fetchTodayAppointments(doctorId: string): Promise<Appointment[]> {
  return mockAppointmentsToday.filter(a => a.doctor_id === doctorId)
}

export async function fetchTodayAvailabilitySlots(doctorId: string): Promise<AvailabilitySlot[]> {
  return mockAvailabilitySlots.filter(s => s.doctor_id === doctorId)
}

export async function fetchPatient(patientId: string): Promise<Patient | null> {
  return mockPatients.find(p => p.id === patientId) || null
}

export async function updateAvailabilitySlot(
  slotId: string,
  isBlocked: boolean,
  reason?: string
): Promise<AvailabilitySlot | null> {
  const slot = mockAvailabilitySlots.find(s => s.id === slotId)
  if (slot) {
    slot.is_blocked = isBlocked
    slot.reason = reason || null
    slot.updated_at = new Date().toISOString()
    return slot
  }
  return null
}

export async function updateAppointmentStatus(
  appointmentId: string,
  status: string
): Promise<Appointment | null> {
  const appointment = mockAppointmentsToday.find(a => a.id === appointmentId)
  if (appointment) {
    appointment.status = status as any
    appointment.updated_at = new Date().toISOString()
    return appointment
  }
  return null
}
