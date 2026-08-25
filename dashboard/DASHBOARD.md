# Doctor Dashboard

A professional, modern dashboard for doctors to manage their daily schedule, appointments, and availability.

## Features

### 📅 Day-View Schedule

- See all appointments for today organized by status
- Status categories: Completed, Upcoming, In Progress, Missed, Cancelled
- Each appointment shows:
  - Time and duration
  - Patient name
  - Visit reason
  - Current status (color-coded)
  - How it was booked (AI voice agent or dashboard)

### 👤 Appointment Details

- Click any appointment to view full details
- See complete patient information:
  - Name, phone number, age, preferred language
  - Appointment date/time and duration
  - Visit reason and appointment mode
- Update appointment status in real-time
- Status transitions: upcoming → in-progress → completed/missed/cancelled

### 🕐 Availability Management

- View all time slots for today
- Block/unblock slots for breaks, meetings, or emergencies
- Add optional reason when blocking (e.g., "Lunch", "Staff Meeting")
- Visual indicators:
  - Green: Available for booking
  - Red: Blocked/unavailable
- One-click toggle to unblock

### 🔔 Notifications

Smart alerts for:
- **Missed Appointments** — Patient didn't show up
- **AI Voice Bookings** — New appointments from voice agent
- **Schedule Changes** — Cancelled or rescheduled appointments

Click the ✕ to dismiss notifications.

### 📊 Quick Stats

At-a-glance numbers in the sidebar:
- Total appointments today
- Completed appointments
- Upcoming appointments
- Missed appointments

## Layout

```
┌─────────────────────────────────────────────────────┐
│  Header: Doctor Name, Specialization, Today's Date  │
├─────────────────────────────────────────┬───────────┤
│                                         │           │
│  Left: Schedule & Notifications         │  Right:   │
│  ├─ Notifications (if any)             │  Stats &  │
│  └─ Appointments grouped by status     │  Availab. │
│                                         │           │
└─────────────────────────────────────────┴───────────┘
```

## Design Philosophy

**Healthcare-Focused:**
- Clean, professional aesthetic with medical blue accents
- Status colors follow healthcare conventions (green=good, red=urgent)
- Clear visual hierarchy for quick scanning

**Doctor-Centric:**
- Shows only today's data (not overwhelming)
- Action-oriented design (block slot, update status with one click)
- Notifications highlight what needs attention
- Sidebar provides quick stats without cluttering main schedule

**Accessibility:**
- High contrast text
- Large, clear buttons
- Responsive on tablets (common in clinical settings)
- No hover-dependent info

## Using the Dashboard

### View Appointments

1. Appointments are organized by status
2. Green completed appointments are slightly faded
3. Click any appointment to open the detail modal

### Update Appointment Status

1. Click an appointment
2. Click the status button you want (Upcoming → In Progress → Completed)
3. Close the modal

### Block Availability

1. Look at "Today's Availability" on the right
2. Click "Block" on any available slot
3. Enter a reason (optional)
4. Click "Confirm Block"

### Unblock Availability

1. Click "Unblock" on a blocked slot (red background)
2. The slot is immediately unblocked

## Mock Data

Currently using mock data from `lib/mock-data.ts`:

- **Cardiologist** — 5 appointments today
- **5 Patients** — With varied visit reasons and booking sources
- **7 Time Slots** — Includes lunch break

### Replacing Mock Data

When you have a live Supabase project:

1. Remove `lib/mock-data.ts`
2. In `app/page.tsx`, replace calls like:
   ```typescript
   const doc = await fetchLoggedInDoctor()
   ```
   with:
   ```typescript
   const { data: doc } = await supabase
     .from('doctors')
     .select('*')
     .single()
   ```
3. Use `supabase.from('appointments').select(...)` for real queries
4. Each function is clearly marked as a placeholder — just swap them out

## Component Structure

```
app/
  page.tsx              # Main dashboard (state management & data loading)

components/
  StatusBadge.tsx       # Status indicator (Upcoming, Completed, etc.)
  ModeBadge.tsx         # Appointment type indicator (In-Person, Video, Call)
  AppointmentCard.tsx   # Single appointment in the list
  AppointmentModal.tsx  # Full appointment details modal
  AvailabilitySlotsPanel.tsx  # Time slot manager
  NotificationsPanel.tsx      # Alert notifications

lib/
  mock-data.ts          # Sample data (delete when live)
  supabase.ts           # Supabase client config
```

## Performance Notes

- All components use client-side rendering (`'use client'`)
- State updates are instant (no loading states for mock data)
- When using real Supabase, add loading states and error handling
- Real-time subscriptions can be added via `supabase.on('*')`

## Future Enhancements

- [ ] Week/month view
- [ ] Search patients
- [ ] Notes on appointments
- [ ] Call recording links
- [ ] Patient history
- [ ] Export schedule
- [ ] Integration with calendar apps (Google Calendar, Outlook)
- [ ] SMS reminders to patients
- [ ] Prescription writing
- [ ] Live status updates via Supabase real-time
