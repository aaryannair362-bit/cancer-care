'use client'

import * as React from 'react'
import { CheckCircle2, ClipboardCheck, FileText, ShieldAlert, XCircle } from 'lucide-react'

import { useDemoAccess } from '@/components/demo-access-provider'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useOncology } from '@/lib/oncology/store'
import type { ActorRef, ConsentStatus, ConsentType } from '@/lib/oncology/types'

const fieldClassName = 'text-xs font-medium text-metadata'

const CONSENT_META: { type: ConsentType; label: string; topics: string[] }[] = [
  { type: 'general_treatment', label: 'General treatment consent', topics: ['Nature of proposed treatment', 'Risks and benefits', 'Alternatives discussed', 'Right to withdraw'] },
  { type: 'chemotherapy', label: 'Chemotherapy consent & education', topics: ['Chemotherapy education', 'Expected adverse effects', 'Emergency / contact instructions', 'Premedication and supportive care'] },
  { type: 'radiation', label: 'Radiation consent', topics: ['Radiation treatment course explained', 'Expected skin/local effects', 'Simulation and planning process'] },
  { type: 'surgical', label: 'Surgical consent', topics: ['Procedure and extent explained', 'Anaesthesia risks', 'Post-operative expectations'] },
  { type: 'data_processing', label: 'Data processing consent', topics: ['Clinical record storage', 'Use for care coordination'] },
  { type: 'recording', label: 'Conversation recording consent', topics: ['Voice/AI documentation use', 'Patient information documents provided'] },
]

const STATUS_META: Record<ConsentStatus, { label: string; variant: 'neutral' | 'warning' | 'success' | 'critical' | 'information' }> = {
  not_started: { label: 'Not started', variant: 'neutral' },
  discussed: { label: 'Discussed', variant: 'information' },
  signed: { label: 'Signed', variant: 'success' },
  declined: { label: 'Declined', variant: 'critical' },
  withdrawn: { label: 'Withdrawn', variant: 'critical' },
}

export default function ConsentPage() {
  const { role, selectedPatient } = useDemoAccess()
  const { getConsentsForPatient, recordConsent } = useOncology()

  const actor: ActorRef = { userId: role.roleId, name: role.label, roleLabel: role.label }
  const consents = getConsentsForPatient(selectedPatient.id)
  const latestByType = (type: ConsentType) => consents.find((c) => c.type === type)

  const [openType, setOpenType] = React.useState<ConsentType | null>(null)
  const [signedBy, setSignedBy] = React.useState(selectedPatient.name)
  const [topics, setTopics] = React.useState<Record<string, boolean>>({})

  const openForm = (type: ConsentType) => {
    setOpenType(type)
    setSignedBy(selectedPatient.name)
    setTopics({})
  }

  const submit = (type: ConsentType, status: ConsentStatus, declinedReason?: string) => {
    const meta = CONSENT_META.find((m) => m.type === type)!
    const discussedTopics = meta.topics.filter((t) => topics[t])
    recordConsent({
      patientId: selectedPatient.id, type, status, documentTitle: meta.label, discussedTopics,
      signedBy: status === 'signed' ? signedBy : undefined, witnessedBy: actor,
      signedAt: status === 'signed' ? new Date().toISOString() : undefined, declinedReason,
    })
    setOpenType(null)
  }

  return (
    <PageContainer>
      <PageHeader title="Consent & Patient Education" description="Treatment consent, chemotherapy/radiation/surgical-specific consent, and education artefacts — visible before treatment" />

      <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-2"><div><p className={fieldClassName}>Patient</p><p className="mt-1 font-display text-lg font-semibold">{selectedPatient.name}</p><p className="text-xs text-metadata">MRN {selectedPatient.mrn}</p></div><div><p className={fieldClassName}>Consents on record</p><p className="mt-1 text-sm font-semibold text-supporting">{consents.filter((c) => c.status === 'signed').length} signed of {CONSENT_META.length} categories</p></div></CardContent></Card>

      <div className="space-y-4">
        {CONSENT_META.map((meta) => {
          const latest = latestByType(meta.type)
          const status = latest?.status ?? 'not_started'
          const statusMeta = STATUS_META[status]
          return (
            <Card key={meta.type}>
              <CardHeader className="border-b border-divider"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle>{meta.label}</CardTitle>{latest ? <CardDescription className="mt-1">{latest.signedBy ? `Signed by ${latest.signedBy}` : `Witnessed by ${latest.witnessedBy?.name}`} · {latest.signedAt ? new Date(latest.signedAt).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : new Date(latest.createdAt).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}</CardDescription> : <CardDescription className="mt-1">Not yet discussed</CardDescription>}</div><Badge variant={statusMeta.variant}>{statusMeta.label}</Badge></div></CardHeader>
              <CardContent className="pt-6">
                {latest && latest.discussedTopics.length > 0 ? (
                  <div className="mb-4 flex flex-wrap gap-1.5">{latest.discussedTopics.map((t) => <Badge key={t} variant="neutral"><CheckCircle2 />{t}</Badge>)}</div>
                ) : null}

                {openType === meta.type ? (
                  <div className="space-y-4 rounded-lg border border-divider bg-surface-elevated/70 p-4">
                    <div><p className="text-xs font-semibold uppercase tracking-wide text-metadata">Topics discussed</p>
                      <div className="mt-2 grid gap-2 sm:grid-cols-2">{meta.topics.map((topic) => <label key={topic} className="flex items-center gap-2 rounded-lg border border-border bg-input-background p-2.5 text-sm text-supporting"><input type="checkbox" className="size-4 accent-primary" checked={Boolean(topics[topic])} onChange={() => setTopics((t) => ({ ...t, [topic]: !t[topic] }))} />{topic}</label>)}</div>
                    </div>
                    <label className={fieldClassName}>Signed by (patient / guardian name)<Input className="mt-1" value={signedBy} onChange={(e) => setSignedBy(e.target.value)} /></label>
                    <div className="flex flex-wrap gap-2">
                      <Button type="button" size="sm" onClick={() => submit(meta.type, 'signed')}><ClipboardCheck />Record Signed</Button>
                      <Button type="button" size="sm" variant="outline" onClick={() => submit(meta.type, 'discussed')}><FileText />Record Discussed Only</Button>
                      <Button type="button" size="sm" variant="destructive" onClick={() => submit(meta.type, 'declined', 'Patient declined')}><XCircle />Record Declined</Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => setOpenType(null)}>Cancel</Button>
                    </div>
                  </div>
                ) : (
                  <Button type="button" size="sm" variant="outline" onClick={() => openForm(meta.type)}><FileText />{latest ? 'Record update' : 'Record consent'}</Button>
                )}
              </CardContent>
            </Card>
          )
        })}
      </div>

      <div className="mt-6 flex items-start gap-3 rounded-xl border border-information/25 bg-information-subtle p-4 text-sm text-information-strong">
        <ShieldAlert className="mt-0.5 size-4 shrink-0" />
        <p>Workflow demonstration only — not a legally binding electronic signature. Status is visible here before treatment where institutional policy requires it, and feeds the Day Care pre-administration checklist's consent check.</p>
      </div>
    </PageContainer>
  )
}
