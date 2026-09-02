import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BackendError, fetchDemoPatient, n, s } from './backend-client'

describe('id boundary helpers', () => {
  it('s() converts a backend integer id to a string, passing through null/undefined', () => {
    expect(s(42)).toBe('42')
    expect(s(null)).toBeUndefined()
    expect(s(undefined)).toBeUndefined()
  })
  it('n() converts a dashboard string id back to a number, passing through empty/null/undefined', () => {
    expect(n('42')).toBe(42)
    expect(n('')).toBeUndefined()
    expect(n(null)).toBeUndefined()
    expect(n(undefined)).toBeUndefined()
  })
})

describe('backend-client auth flow', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('mints a token via demo-login on first use and reuses it on the next call', async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'tok-1', refresh_token: 'ref-1' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'success', patient: { id: 22, mrn: 'CCA-ONC-DEMO-001', name: 'Sunita Patil', age: 52, sex: 'Female', journey_state: 'Medical Oncology' } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'success', patient: { id: 22, mrn: 'CCA-ONC-DEMO-001', name: 'Sunita Patil', age: 52, sex: 'Female', journey_state: 'Medical Oncology' } }), { status: 200 }))

    const first = await fetchDemoPatient('doctor')
    expect(first.patient.id).toBe(22)
    // Second call must not re-mint a token — only 3 fetches total (1 login + 2 data calls).
    await fetchDemoPatient('doctor')
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[0][0]).toContain('/api/auth/demo-login')
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({ Authorization: 'Bearer tok-1' })
  })

  it('discards the cached token and retries once on a 401, then throws if it fails again', async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'tok-1', refresh_token: 'ref-1' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // first data call rejected
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'tok-2', refresh_token: 'ref-2' }), { status: 200 })) // re-login
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // retry also rejected

    await expect(fetchDemoPatient('doctor')).rejects.toThrow(BackendError)
    expect(fetchMock).toHaveBeenCalledTimes(4) // exactly one retry, never loops
  })

  it('raises a clear BackendError when the network is unreachable', async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>
    fetchMock.mockRejectedValue(new TypeError('fetch failed'))
    await expect(fetchDemoPatient('doctor')).rejects.toThrow(/could not reach/i)
  })

  it('raises a BackendError for a role with no oncology backend identity', async () => {
    await expect(fetchDemoPatient('lab')).rejects.toThrow(/no oncology backend identity/i)
  })
})
