# Supabase Configuration

This directory contains Supabase configuration and migrations for the healthcare appointment platform.

## Setup Instructions

### 1. Create a Supabase Project

1. Go to https://supabase.com and sign up/log in
2. Click "New Project"
3. Enter a project name (e.g., "healthcare-voice-agent")
4. Create a strong database password and save it
5. Select your region (closest to your users)
6. Wait for the project to initialize (5-10 minutes)

### 2. Get Your Credentials

Once the project is ready:

1. Go to **Settings** → **API** in the Supabase dashboard
2. Copy the following and add them to your `.env.local` and `voice-agent/.env`:

   - **Project URL** → `SUPABASE_URL`
   - **anon public key** → `SUPABASE_ANON_KEY` (dashboard)
   - **service_role key** → `SUPABASE_SERVICE_ROLE_KEY` (voice-agent)

### 3. Apply the Migration

You have two options:

#### Option A: Using Supabase CLI (Recommended)

1. Install Supabase CLI: https://supabase.com/docs/guides/cli/getting-started

2. Link your project:
   ```bash
   supabase link --project-ref <project-ref>
   ```
   Get `<project-ref>` from your Supabase project settings URL:
   `https://app.supabase.com/projects/<project-ref>/...`

3. Run the migration:
   ```bash
   supabase db push
   ```

#### Option B: Using Supabase Dashboard SQL Editor

1. Go to **SQL Editor** in the Supabase dashboard
2. Click "New Query"
3. Copy the entire contents of `migrations/20240802000001_init_healthcare_schema.sql`
4. Paste it into the SQL editor
5. Click "Run"
6. Verify all tables were created successfully

### 4. Verify the Schema

After running the migration, verify everything worked:

1. Go to **Table Editor** in the Supabase dashboard
2. You should see these tables:
   - `doctors`
   - `patients`
   - `availability_slots`
   - `appointments`
   - `call_logs`

3. Click each table to verify columns and data types

## Migration Details

The migration creates:

### Tables

- **doctors** — Doctor profiles with specialization
- **patients** — Patient profiles with preferred language
- **availability_slots** — Recurring/blocked time slots per doctor
- **appointments** — Scheduled appointments between doctors and patients
- **call_logs** — Logs of voice calls with transcripts and outcomes

### Indexes

Indexes are created on common query patterns:
- Doctor lookups by email
- Patient lookups by phone
- Appointment queries by doctor, patient, status, and date
- Call log queries by patient and appointment

### Row-Level Security (RLS)

RLS is enabled on all tables with these policies:

**Doctors can:**
- View only their own profile
- View only their own availability slots
- View only their own appointments
- Create and update their own availability and appointments

**Service Role (Voice Agent) can:**
- Read and write all tables without restrictions
- This allows the voice-agent service to query availability, create appointments, and log calls

**Patients:**
- No direct database access (managed by voice agent on their behalf)

## Using the Types

Both `dashboard/` and `voice-agent/` can import types from the root `types/` directory:

```typescript
// In dashboard or voice-agent
import { Doctor, Patient, Appointment } from '../../types'
```

To make imports easier, you can add path aliases to `tsconfig.json`:

```json
{
  "compilerOptions": {
    "paths": {
      "@types/*": ["../../types/*"]
    }
  }
}
```

Then import as:
```typescript
import { Doctor, Appointment } from '@types'
```

## Next Steps

After applying the migration:

1. Test Supabase connection from the dashboard (`dashboard/lib/supabase.ts`)
2. Implement API routes in the dashboard to manage doctors and appointments
3. Implement voice-agent endpoints to query availability and create appointments
4. Set up Supabase real-time subscriptions for live calendar updates

## Troubleshooting

**Migration fails with "Extension not found":**
- This shouldn't happen on Supabase, but if it does, UUID is already available

**RLS policies blocking queries:**
- Ensure you're using the correct API key:
  - Dashboard uses `anon key` (for authenticated doctors)
  - Voice-agent uses `service_role key` (for unrestricted access)

**Connection errors:**
- Double-check `SUPABASE_URL` and keys are correct
- Ensure `.env.local` is in `.gitignore` (don't commit secrets!)

## Additional Resources

- Supabase Docs: https://supabase.com/docs
- RLS Guide: https://supabase.com/docs/guides/auth/row-level-security
- CLI Guide: https://supabase.com/docs/guides/cli
