'use client'

import * as React from 'react'
import { Lightbulb, PlugZap } from 'lucide-react'

import { fetchNexusRecommendation, nexusIntegration, recordRecommendationResponse } from '@/lib/oncology/adapters'
import type { ActorRef, RecommendationAudience, RecommendationSlot } from '@/lib/oncology/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

/**
 * The "ready for NEXUS/AI recommendations" slot (PDF items 29, 30, 42). Every screen
 * that will eventually show a system recommendation to a clinician, nurse, pharmacist
 * or patient renders this component rather than inventing its own recommendation UI —
 * so wiring NEXUS later means implementing `fetchNexusRecommendation()` in adapters.ts,
 * not redesigning every screen that wants to show a suggestion.
 *
 * Guardrail this component enforces structurally: there is no code path from here into
 * `useOncology()`'s order/verification/dispense/administration writers. A recommendation
 * is always something a named, authorized human decides to accept, modify, or dismiss —
 * never something that becomes a clinical fact on its own.
 */
export function RecommendationPanel({
  patientId,
  context,
  audience,
  actor,
  title = 'Guideline recommendation',
}: {
  patientId: string
  context: string
  audience: RecommendationAudience
  actor: ActorRef
  title?: string
}) {
  const [slot, setSlot] = React.useState<RecommendationSlot | null>(null)

  React.useEffect(() => {
    let cancelled = false
    fetchNexusRecommendation(patientId, context, audience).then((result) => {
      if (!cancelled) setSlot(result)
    })
    return () => {
      cancelled = true
    }
  }, [patientId, context, audience])

  const respond = (decision: 'accepted' | 'modified' | 'dismissed') => {
    if (!slot) return
    setSlot(recordRecommendationResponse(slot, decision, actor))
  }

  if (!slot) return null

  if (slot.connectionState === 'not_connected') {
    return (
      <Card variant="supporting">
        <CardHeader className="border-b border-divider">
          <div className="flex items-center gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-deep"><Lightbulb className="size-4" /></span>
            <CardTitle>{title}</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="space-y-3 pt-6">
          <p className="flex items-center gap-2 text-sm font-semibold text-metadata"><PlugZap className="size-4 shrink-0" />Not yet connected</p>
          <p className="text-xs leading-5 text-metadata">
            {nexusIntegration.name} is architecturally wired to this screen but not yet live. Once connected,
            a guideline-sourced suggestion will appear here for {audienceLabel(audience)} to accept, modify, or
            dismiss — it will never be applied automatically.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card variant="ai">
      <CardHeader className="border-b border-ai-highlight/60">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-ai-highlight text-brand-deep"><Lightbulb className="size-4" /></span><CardTitle>{title}</CardTitle></div>
          <Badge variant="brand">AI-suggested · not a clinical fact</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pt-6">
        <p className="text-sm font-semibold leading-6 text-supporting">{slot.recommendationText}</p>
        {slot.rationale ? <p className="text-xs leading-5 text-metadata">{slot.rationale}</p> : null}
        <div className="flex flex-wrap gap-3 text-xs text-metadata">
          {slot.guidelinePathwayName ? <span>Pathway: {slot.guidelinePathwayName}</span> : null}
          {slot.guidelineVersion ? <span>Version {slot.guidelineVersion}</span> : null}
          {slot.provenance ? <span>Source: {slot.provenance}</span> : null}
        </div>
        {slot.clinicianResponse ? (
          <Badge variant={slot.clinicianResponse.decision === 'accepted' ? 'success' : slot.clinicianResponse.decision === 'modified' ? 'warning' : 'neutral'}>
            {slot.clinicianResponse.decision === 'accepted' ? 'Accepted' : slot.clinicianResponse.decision === 'modified' ? 'Modified' : 'Dismissed'} by {slot.clinicianResponse.actor.name}
          </Badge>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" onClick={() => respond('accepted')}>Accept</Button>
            <Button type="button" size="sm" variant="outline" onClick={() => respond('modified')}>Modify</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => respond('dismissed')}>Dismiss</Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function audienceLabel(audience: RecommendationAudience) {
  switch (audience) {
    case 'clinician': return 'the treating clinician'
    case 'nurse': return 'the nurse'
    case 'pharmacist': return 'the pharmacist'
    case 'patient': return 'the patient'
  }
}
