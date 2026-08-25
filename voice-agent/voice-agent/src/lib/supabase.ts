import { createClient } from '@supabase/supabase-js'

const supabaseUrl = process.env.SUPABASE_URL
const supabaseServiceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY

if (!supabaseUrl || !supabaseServiceRoleKey) {
  throw new Error(
    'Missing Supabase environment variables. Please check your .env file.'
  )
}

// Service role client has unrestricted access to all tables
// Used by voice-agent to query availability, create appointments, and log calls
export const supabase = createClient(supabaseUrl, supabaseServiceRoleKey)
