'use client'

import { Lock, Pencil } from 'lucide-react'

import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import {
  ADMINISTRATION_ROUTES, CANCER_SITES, CTCAE_TERMS, DOSE_BASIS_DESCRIPTORS, type TerminologyEntry,
} from '@/lib/oncology/terminology'

const CODED_LISTS: { title: string; field: string; entries: TerminologyEntry[] }[] = [
  { title: 'Administration routes', field: 'Treatment Order → Route', entries: ADMINISTRATION_ROUTES },
  { title: 'Dose basis (reference descriptors)', field: 'Treatment Order → Dose basis', entries: DOSE_BASIS_DESCRIPTORS },
  { title: 'CTCAE toxicity terms', field: 'Toxicity Event → Term', entries: CTCAE_TERMS },
  { title: 'Cancer sites', field: 'Treatment Plan / MDT → Diagnosis site', entries: CANCER_SITES },
]

export default function TerminologyPage() {
  return (
    <PageContainer>
      <PageHeader title="Dropdown Source of Truth" description="Field → Allowed values → Terminology/code system → Clinical owner → Editable? — for review with Medical Oncology, Radiation Oncology, Surgical Oncology, Pharmacy and Nursing" />

      <div className="space-y-6">
        {CODED_LISTS.map((list) => (
          <Card key={list.title}>
            <CardHeader className="border-b border-divider"><CardTitle>{list.title}</CardTitle><CardDescription className="mt-1">Used by: {list.field}</CardDescription></CardHeader>
            <CardContent className="pt-6">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] border-collapse text-sm">
                  <thead><tr className="border-b border-divider text-left text-xs font-semibold uppercase tracking-wide text-metadata"><th className="pb-2 pr-4">Allowed value</th><th className="pb-2 pr-4">Code</th><th className="pb-2 pr-4">Code system</th><th className="pb-2 pr-4">Clinical owner</th><th className="pb-2">Editable?</th></tr></thead>
                  <tbody>
                    {list.entries.map((entry) => (
                      <tr key={entry.code} className="border-b border-divider last:border-0">
                        <td className="py-2 pr-4 font-medium text-supporting">{entry.display}</td>
                        <td className="py-2 pr-4 font-mono text-xs text-metadata">{entry.code}</td>
                        <td className="py-2 pr-4 text-xs text-metadata">{entry.system}</td>
                        <td className="py-2 pr-4 text-xs text-metadata">{entry.clinicalOwner}</td>
                        <td className="py-2">{entry.editable ? <Badge variant="information"><Pencil />Editable</Badge> : <Badge variant="neutral"><Lock />Locked</Badge>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="mt-6 rounded-xl border border-warning/25 bg-warning-subtle p-4 text-sm text-warning-strong">
        These are reference starter sets, not a licensed production terminology feed (see each module's header comment). Per item 36, review this document with a Medical Oncologist, Radiation Oncologist, Surgical Oncologist, Oncology Pharmacist and Oncology Nurse before treating any list as clinically final.
      </div>
    </PageContainer>
  )
}
