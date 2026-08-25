# Migrating from Mock Data to Live Supabase

This guide shows how to replace the mock data layer with real Supabase queries.

## Current State

**Mock Data File:** `lib/mock-data.ts`

The dashboard currently uses hardcoded mock data for:
- Logged-in doctor
- Today's appointments
- Available time slots
- Patient information

All mock functions have clear comments: `// Placeholder for real Supabase call`

## Step-by-Step Migration

### 1. Update Environment Variables

First, ensure your `.env.local` has Supabase credentials:

```
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key-here
```

### 2. Replace `fetchLoggedInDoctor()`

**Before (mock):**
```typescript
export async function fetchLoggedInDoctor(): Promise<Doctor> {
  return mockLoggedInDoctor
}
```

**After (Supabase):**
```typescript
export async function fetchLoggedInDoctor(): Promise<Doctor> {
  // Get doctor from Supabase auth.user() 
  const { data: { user } } = await supabase.auth.getUser()
  
  if (!user) throw new Error('Not authenticated')
  
  const { data: doctor, error } = await supabase
    .from('doctors')
    .select('*')
    .eq('id', user.id)
    .single()
  
  if (error) throw error
  return doctor
}
```

### 3. Replace `fetchTodayAppointments()`

**Before (mock):**
```typescript
export async function fetchTodayAppointments(doctorId: string): Promise<Appointment[]> {
  return mockAppointmentsToday.filter(a => a.doctor_id === doctorId)
}
```

**After (Supabase):**
```typescript
export async function fetchTodayAppointments(doctorId: string): Promise<Appointment[]> {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  
  const tomorrow = new Date(today)
  tomorrow.setDate(tomorrow.getDate() + 1)
  
  const { data, error } = await supabase
    .from('appointments')
    .select('*')
    .eq('doctor_id', doctorId)
    .gte('scheduled_at', today.toISOString())
    .lt('scheduled_at', tomorrow.toISOString())
    .order('scheduled_at', { ascending: true })
  
  if (error) throw error
  return data || []
}
```

### 4. Replace `fetchTodayAvailabilitySlots()`

**Before (mock):**
```typescript
export async function fetchTodayAvailabilitySlots(doctorId: string): Promise<AvailabilitySlot[]> {
  return mockAvailabilitySlots.filter(s => s.doctor_id === doctorId)
}
```

**After (Supabase):**
```typescript
export async function fetchTodayAvailabilitySlots(doctorId: string): Promise<AvailabilitySlot[]> {
  const today = new Date().toISOString().split('T')[0] // YYYY-MM-DD
  
  const { data, error } = await supabase
    .from('availability_slots')
    .select('*')
    .eq('doctor_id', doctorId)
    .eq('slot_date', today)
    .order('start_time', { ascending: true })
  
  if (error) throw error
  return data || []
}
```

### 5. Replace `fetchPatient()`

**Before (mock):**
```typescript
export async function fetchPatient(patientId: string): Promise<Patient | null> {
  return mockPatients.find(p => p.id === patientId) || null
}
```

**After (Supabase):**
```typescript
export async function fetchPatient(patientId: string): Promise<Patient | null> {
  const { data, error } = await supabase
    .from('patients')
    .select('*')
    .eq('id', patientId)
    .single()
  
  if (error) return null // Patient not found
  return data
}
```

### 6. Replace `updateAvailabilitySlot()`

**Before (mock):**
```typescript
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
```

**After (Supabase):**
```typescript
export async function updateAvailabilitySlot(
  slotId: string,
  isBlocked: boolean,
  reason?: string
): Promise<AvailabilitySlot | null> {
  const { data, error } = await supabase
    .from('availability_slots')
    .update({
      is_blocked: isBlocked,
      reason: reason || null,
      updated_at: new Date().toISOString(),
    })
    .eq('id', slotId)
    .select()
    .single()
  
  if (error) {
    console.error('Failed to update slot:', error)
    return null
  }
  return data
}
```

### 7. Replace `updateAppointmentStatus()`

**Before (mock):**
```typescript
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
```

**After (Supabase):**
```typescript
export async function updateAppointmentStatus(
  appointmentId: string,
  status: string
): Promise<Appointment | null> {
  const { data, error } = await supabase
    .from('appointments')
    .update({
      status: status as Appointment['status'],
      updated_at: new Date().toISOString(),
    })
    .eq('id', appointmentId)
    .select()
    .single()
  
  if (error) {
    console.error('Failed to update appointment:', error)
    return null
  }
  return data
}
```

## 8. Clean Up Mock Data

Once all functions are migrated:

1. Delete `lib/mock-data.ts` completely
2. Remove import from `app/page.tsx`:
   ```typescript
   // Remove this line:
   import { fetchLoggedInDoctor, ... } from '@/lib/mock-data'
   ```
3. Import from the updated file instead:
   ```typescript
   import { fetchLoggedInDoctor, ... } from '@/lib/supabase-handlers'
   ```

## Error Handling & Loading States

With real Supabase, add proper error handling in `app/page.tsx`:

```typescript
useEffect(() => {
  const loadData = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const doc = await fetchLoggedInDoctor()
      setDoctor(doc)
      // ... rest of loading
    } catch (err) {
      console.error('Failed to load dashboard:', err)
      setError('Failed to load dashboard. Please refresh.')
    } finally {
      setIsLoading(false)
    }
  }
  
  loadData()
}, [])
```

## Real-Time Updates (Optional)

Once live, enhance the dashboard with real-time Supabase subscriptions:

```typescript
useEffect(() => {
  if (!doctor) return
  
  const subscription = supabase
    .from(`appointments:doctor_id=eq.${doctor.id}`)
    .on('*', (payload) => {
      // Refresh appointments when changed by another client
      console.log('Appointment updated:', payload)
      // Re-fetch or update state
    })
    .subscribe()
  
  return () => {
    subscription.unsubscribe()
  }
}, [doctor])
```

## Testing the Migration

1. Ensure your Supabase project has the schema set up (see `/dashboard/supabase/migrations/`)
2. Create a test doctor account with some appointments
3. Test each function:
   - Doctor loads correctly
   - Appointments display
   - Status updates work
   - Availability slots can be blocked/unblocked

## Troubleshooting

### "Not authenticated" error
- Doctor not signed in via Supabase auth
- Check that `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` are correct

### Appointments not showing
- Check that `scheduled_at` is in ISO 8601 format
- Verify doctor_id matches the logged-in user's id
- Check RLS policies allow the doctor to see their own appointments

### Updates not working
- Verify RLS policies allow updates
- Check that the doctor owns the appointment/slot being updated

### Real-time not working
- Supabase real-time requires `REALTIME_ENABLED` in your project
- Enable in Supabase dashboard: Settings → Replication
