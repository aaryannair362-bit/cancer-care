/**
 * Example handlers for common voice-agent operations with Supabase.
 * This file shows patterns for querying and updating data.
 * Remove this file once you've built your actual handlers.
 */

import { supabase } from './supabase'
import type { AvailabilitySlot, Appointment, CallLog, Patient } from '../../../types'

/**
 * Get a patient by phone number, or create if doesn't exist
 */
export async function getOrCreatePatient(
  phone: string,
  name: string,
  preferredLanguage: string = 'en'
): Promise<Patient | null> {
  // Try to find existing patient
  const { data: existing } = await supabase
    .from('patients')
    .select('*')
    .eq('phone', phone)
    .single()

  if (existing) {
    return existing
  }

  // Create new patient
  const { data: newPatient, error } = await supabase
    .from('patients')
    .insert({
      name,
      phone,
      preferred_language: preferredLanguage,
    })
    .select()
    .single()

  if (error) {
    console.error('Failed to create patient:', error)
    return null
  }

  return newPatient
}

/**
 * Get available slots for a doctor on a specific date
 */
export async function getAvailableSlots(
  doctorId: string,
  date: string // YYYY-MM-DD
): Promise<AvailabilitySlot[]> {
  const { data, error } = await supabase
    .from('availability_slots')
    .select('*')
    .eq('doctor_id', doctorId)
    .eq('slot_date', date)
    .eq('is_blocked', false)
    .order('start_time', { ascending: true })

  if (error) {
    console.error('Failed to get availability slots:', error)
    return []
  }

  return data || []
}

/**
 * Create a new appointment
 */
export async function createAppointment(
  doctorId: string,
  patientId: string,
  scheduledAt: string, // ISO 8601 timestamp
  durationMinutes: number = 30,
  reason: string | null = null
): Promise<Appointment | null> {
  const { data, error } = await supabase
    .from('appointments')
    .insert({
      doctor_id: doctorId,
      patient_id: patientId,
      scheduled_at: scheduledAt,
      duration_minutes: durationMinutes,
      reason,
      mode: 'call-booked',
      status: 'upcoming',
      created_via: 'ai-voice-agent',
    })
    .select()
    .single()

  if (error) {
    console.error('Failed to create appointment:', error)
    return null
  }

  return data
}

/**
 * Log a voice call
 */
export async function logCall(
  patientId: string,
  appointmentId: string | null,
  callStartedAt: string, // ISO 8601 timestamp
  callEndedAt: string, // ISO 8601 timestamp
  transcript: string | null = null,
  outcome: string | null = null,
  escalatedToHuman: boolean = false
): Promise<CallLog | null> {
  const { data, error } = await supabase
    .from('call_logs')
    .insert({
      patient_id: patientId,
      appointment_id: appointmentId,
      call_started_at: callStartedAt,
      call_ended_at: callEndedAt,
      transcript,
      outcome,
      escalated_to_human: escalatedToHuman,
    })
    .select()
    .single()

  if (error) {
    console.error('Failed to log call:', error)
    return null
  }

  return data
}

/**
 * Get all doctors (for selection/routing)
 */
export async function getAllDoctors() {
  const { data, error } = await supabase
    .from('doctors')
    .select('*')
    .order('name', { ascending: true })

  if (error) {
    console.error('Failed to get doctors:', error)
    return []
  }

  return data || []
}

/**
 * Update appointment status
 */
export async function updateAppointmentStatus(
  appointmentId: string,
  status: 'upcoming' | 'in-progress' | 'completed' | 'missed' | 'cancelled'
): Promise<boolean> {
  const { error } = await supabase
    .from('appointments')
    .update({ status, updated_at: new Date().toISOString() })
    .eq('id', appointmentId)

  if (error) {
    console.error('Failed to update appointment:', error)
    return false
  }

  return true
}

/**
 * Subscribe to real-time appointment updates
 * Useful for dashboards monitoring appointment status
 */
export function subscribeToAppointmentUpdates(
  doctorId: string,
  callback: (appointment: Appointment) => void
) {
  const subscription = supabase
    .from(`appointments:doctor_id=eq.${doctorId}`)
    .on('*', (payload) => {
      callback(payload.new as Appointment)
    })
    .subscribe()

  return subscription
}
