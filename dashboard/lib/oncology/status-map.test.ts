import { describe, expect, it } from 'vitest'
import { composeOrderStatus, mapMdtCaseStatus, mapPlanStatus, pharmacyStatusForTarget } from './status-map'

describe('mapPlanStatus', () => {
  it('treats DRAFT, PROPOSED, ACTIVE and ON_HOLD as the current plan', () => {
    for (const s of ['DRAFT', 'PROPOSED', 'ACTIVE', 'ON_HOLD']) expect(mapPlanStatus(s)).toBe('active')
  })
  it('treats COMPLETED and CANCELLED as closed', () => {
    expect(mapPlanStatus('COMPLETED')).toBe('closed')
    expect(mapPlanStatus('CANCELLED')).toBe('closed')
  })
  it('maps SUPERSEDED to superseded', () => {
    expect(mapPlanStatus('SUPERSEDED')).toBe('superseded')
  })
})

describe('composeOrderStatus', () => {
  it('cancelled and held take priority regardless of pharmacy/medication state', () => {
    expect(composeOrderStatus({ orderStatus: 'CANCELLED', pharmacyStatus: 'Dispensed', medicationStatuses: ['Completed'] })).toBe('cancelled')
    expect(composeOrderStatus({ orderStatus: 'HELD', pharmacyStatus: 'Verified' })).toBe('held')
  })
  it('a draft order with nothing else is draft', () => {
    expect(composeOrderStatus({ orderStatus: 'DRAFT' })).toBe('draft')
  })
  it('a signed order with no pharmacy row yet is ordered, not verification_pending', () => {
    expect(composeOrderStatus({ orderStatus: 'SIGNED', pharmacyStatus: null })).toBe('ordered')
  })
  it('walks the full pharmacy sequence for a signed order', () => {
    const cases: Array<[string, ReturnType<typeof composeOrderStatus>]> = [
      ['Verified', 'verified'], ['Preparing', 'preparation_pending'], ['Ready', 'prepared'],
      ['Dispensed', 'dispensed'], ['Received', 'ready_for_administration'],
    ]
    for (const [pharmacyStatus, expected] of cases) {
      expect(composeOrderStatus({ orderStatus: 'SIGNED', pharmacyStatus })).toBe(expected)
    }
  })
  it('an executed order with an in-progress medication is in_progress', () => {
    expect(composeOrderStatus({ orderStatus: 'EXECUTED', medicationStatuses: ['Pending', 'InProgress'] })).toBe('in_progress')
  })
  it('an executed order where every medication completed is administered', () => {
    expect(composeOrderStatus({ orderStatus: 'EXECUTED', medicationStatuses: ['Completed', 'Completed'] })).toBe('administered')
  })
  it('an executed order with a completion record is completed', () => {
    expect(composeOrderStatus({ orderStatus: 'EXECUTED', medicationStatuses: ['Completed'], hasCompletionRecord: true })).toBe('completed')
  })
  it('an executed order with no medications yet is ready_for_administration', () => {
    expect(composeOrderStatus({ orderStatus: 'EXECUTED' })).toBe('ready_for_administration')
  })
})

describe('pharmacyStatusForTarget', () => {
  it('maps each pharmacy-shaped dashboard target to its backend status string', () => {
    expect(pharmacyStatusForTarget('verified')).toBe('Verified')
    expect(pharmacyStatusForTarget('preparation_pending')).toBe('Preparing')
    expect(pharmacyStatusForTarget('prepared')).toBe('Ready')
    expect(pharmacyStatusForTarget('dispensed')).toBe('Dispensed')
    expect(pharmacyStatusForTarget('ready_for_administration')).toBe('Received')
  })
  it('returns undefined for a target with no pharmacy-readiness call', () => {
    expect(pharmacyStatusForTarget('in_progress')).toBeUndefined()
    expect(pharmacyStatusForTarget('cancelled')).toBeUndefined()
  })
})

describe('mapMdtCaseStatus', () => {
  it('a linked plan always wins, regardless of backend status', () => {
    expect(mapMdtCaseStatus('DISCUSSED', true)).toBe('plan_created')
  })
  it('RECOMMENDED or ACTIONED_BY_CLINICIAN without a plan yet is recommendation_recorded', () => {
    expect(mapMdtCaseStatus('RECOMMENDED', false)).toBe('recommendation_recorded')
    expect(mapMdtCaseStatus('ACTIONED_BY_CLINICIAN', false)).toBe('recommendation_recorded')
  })
  it('DISCUSSED without a plan is discussed', () => {
    expect(mapMdtCaseStatus('DISCUSSED', false)).toBe('discussed')
  })
  it('anything earlier defaults to scheduled', () => {
    expect(mapMdtCaseStatus('PROPOSED', false)).toBe('scheduled')
  })
})
