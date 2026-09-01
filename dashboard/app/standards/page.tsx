import { CheckCircle2, CircleDashed, Layers, Lock } from 'lucide-react'

import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { standardsMatrix, type StandardsMatrixRow } from '@/lib/oncology/standards-matrix'

const STATUS_META: Record<StandardsMatrixRow['status'], { label: string; variant: 'neutral' | 'warning' | 'information' | 'critical'; icon: typeof CheckCircle2 }> = {
  not_started: { label: 'Not started', variant: 'critical', icon: CircleDashed },
  structural: { label: 'Structural — model ready, not connected', variant: 'information', icon: Layers },
  partial: { label: 'Partial — real content, unvalidated', variant: 'warning', icon: CheckCircle2 },
  blocked: { label: 'Blocked — licence or external dependency', variant: 'neutral', icon: Lock },
}

export default function StandardsPage() {
  const counts = standardsMatrix.reduce<Record<string, number>>((acc, row) => ({ ...acc, [row.status]: (acc[row.status] ?? 0) + 1 }), {})

  return (
    <PageContainer>
      <PageHeader title="Standards & Interoperability" description="Where CCA OS stands against every standard named in the pre-demo checklist — kept as data, not a slide" />

      <Card className="mb-6"><CardContent className="grid gap-4 p-5 sm:grid-cols-4">
        {(Object.keys(STATUS_META) as StandardsMatrixRow['status'][]).map((status) => (
          <div key={status}><p className="text-xs text-metadata">{STATUS_META[status].label}</p><p className="mt-1 font-display text-2xl font-semibold text-supporting">{counts[status] ?? 0}</p></div>
        ))}
      </CardContent></Card>

      <div className="space-y-4">
        {standardsMatrix.map((row) => {
          const meta = STATUS_META[row.status]
          const Icon = meta.icon
          return (
            <Card key={row.area}>
              <CardHeader className="border-b border-divider">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div><CardTitle>{row.area}</CardTitle><p className="mt-1 text-xs text-metadata">{row.standardReference}</p></div>
                  <Badge variant={meta.variant}><Icon />{meta.label}</Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-3 pt-6">
                <div><p className="text-xs font-semibold uppercase tracking-wide text-metadata">How CCA OS implements it</p><p className="mt-1 text-sm leading-6 text-supporting">{row.howImplemented}</p></div>
                <div><p className="text-xs font-semibold uppercase tracking-wide text-metadata">Evidence</p><p className="mt-1 text-xs leading-5 text-metadata">{row.evidence}</p></div>
              </CardContent>
            </Card>
          )
        })}
      </div>
    </PageContainer>
  )
}
