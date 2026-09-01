'use client'

import { BookOpen, CheckCircle2 } from 'lucide-react'

import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { useOncology } from '@/lib/oncology/store'

const fieldClassName = 'text-xs font-medium text-metadata'

export default function RegimensPage() {
  const { state } = useOncology()

  return (
    <PageContainer>
      <PageHeader title="Regimen Library" description="Controlled regimen templates — first-class clinical objects, not per-screen UI shortcuts" />

      <div className="space-y-6">
        {state.regimens.map((regimen) => (
          <Card key={regimen.id}>
            <CardHeader className="border-b border-divider">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><CardTitle>{regimen.name}</CardTitle><CardDescription className="mt-1">{regimen.cancerIndication} · {regimen.intentSetting}</CardDescription></div>
                <Badge variant={regimen.status === 'active' ? 'success' : 'neutral'}>{regimen.status}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-6 pt-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <div><p className={fieldClassName}>Schedule</p><p className="mt-1 text-sm font-semibold text-supporting">{regimen.scheduleDescription}</p></div>
                <div><p className={fieldClassName}>Planned cycles</p><p className="mt-1 text-sm font-semibold text-supporting">{regimen.plannedCycles}</p></div>
                <div><p className={fieldClassName}>Version</p><p className="mt-1 text-sm font-semibold text-supporting">v{regimen.version} · effective {regimen.effectiveDate}</p></div>
                <div><p className={fieldClassName}>Approved by</p><p className="mt-1 text-sm font-semibold text-supporting">{regimen.approvedBy.name}</p></div>
              </div>

              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-metadata">Drug sequence</p>
                <div className="mt-2 space-y-2">
                  {regimen.drugSequence.map((line) => (
                    <div key={line.sequence} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-divider bg-surface-elevated/70 p-3 text-sm">
                      <div className="flex items-center gap-3"><span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-brand-soft text-xs font-semibold text-brand-deep">{line.sequence}</span><div><p className="font-semibold text-supporting">{line.genericDrugName}{line.isPremedication ? <Badge variant="neutral" className="ml-2">Premedication</Badge> : null}{line.isSupportive ? <Badge variant="neutral" className="ml-2">Supportive</Badge> : null}</p><p className="text-xs text-metadata">Dose basis (reference): {line.doseBasisDescription} · {line.route}{line.diluent ? ` · ${line.diluent}` : ''}{line.infusionDuration ? ` · ${line.infusionDuration}` : ''}</p></div></div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-3">
                <div><p className="text-xs font-semibold uppercase tracking-wide text-metadata">Premedications</p><ul className="mt-2 space-y-1">{regimen.premedications.map((p) => <li key={p} className="flex items-start gap-2 text-xs text-supporting"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-brand-deep" />{p}</li>)}</ul></div>
                <div><p className="text-xs font-semibold uppercase tracking-wide text-metadata">Hydration</p><ul className="mt-2 space-y-1">{regimen.hydration.map((p) => <li key={p} className="flex items-start gap-2 text-xs text-supporting"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-brand-deep" />{p}</li>)}</ul></div>
                <div><p className="text-xs font-semibold uppercase tracking-wide text-metadata">Supportive therapy</p><ul className="mt-2 space-y-1">{regimen.supportiveTherapy.map((p) => <li key={p} className="flex items-start gap-2 text-xs text-supporting"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-brand-deep" />{p}</li>)}</ul></div>
              </div>

              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-metadata">Treatment / hold parameters (reference — no thresholds encoded)</p>
                <div className="mt-2 flex flex-wrap gap-1.5">{regimen.treatmentHoldParameterReferences.map((p) => <Badge key={p} variant="neutral">{p}</Badge>)}</div>
              </div>

              <div className="flex items-center gap-2 rounded-lg border border-information/25 bg-information-subtle p-3 text-xs text-information-strong"><BookOpen className="size-3.5 shrink-0" />References: {regimen.references.join('; ')}</div>
            </CardContent>
          </Card>
        ))}

        {state.regimens.length === 0 ? <Card><CardContent className="p-6 text-sm text-metadata">No regimens in the library yet.</CardContent></Card> : null}
      </div>
    </PageContainer>
  )
}
