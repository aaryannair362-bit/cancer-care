'use client'

import * as React from 'react'
import { AlertTriangle, CheckCircle2, FileText, Loader2, Trash2, Upload } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useDocuments } from '@/lib/documents/store'
import type { ActorRef, PatientDocumentRecord } from '@/lib/oncology/types'

const FIELD_LABELS: Record<string, string> = {
  documentNumber: 'Document number', nameOnDocument: 'Name on document', diagnoses: 'Diagnoses',
  medications: 'Medications', allergies: 'Allergies', investigations: 'Investigations', procedures: 'Procedures',
}

const STATUS_META: Record<PatientDocumentRecord['ocrStatus'], { label: string; variant: 'neutral' | 'warning' | 'success' | 'critical' | 'information' }> = {
  pending: { label: 'Pending', variant: 'neutral' },
  processing: { label: 'Reading document…', variant: 'information' },
  completed: { label: 'Text extracted', variant: 'success' },
  needs_review: { label: 'Needs review', variant: 'warning' },
  failed: { label: 'Extraction failed', variant: 'critical' },
}

/**
 * Real file capture + real client-side OCR (see lib/documents/ocr.ts), replacing what
 * were previously two decorative buttons — one with a file input that had no onChange
 * handler, one that only ever set a fake "Demo capture ready" status string. Every
 * extracted field is shown for a human to accept, never auto-applied — the same
 * discipline this app already applies to AI output everywhere else (see
 * PreviousDocumentsSection's Accept/Edit pattern, and the "AI never the source of
 * truth" rule this codebase carries throughout the oncology store).
 */
export function DocumentUploadField({
  patientId, actor, documentType, buttonLabel, accept = 'image/*,.pdf', onFieldAccepted, onUploaded,
}: {
  patientId: string
  actor: ActorRef
  documentType: string
  buttonLabel: string
  accept?: string
  /** Called only when the user explicitly clicks "Use this value" for a given extracted field. */
  onFieldAccepted?: (fieldKey: string, value: string) => void
  /** Called once a file has genuinely been captured (independent of OCR outcome). */
  onUploaded?: (record: PatientDocumentRecord) => void
}) {
  const { uploadDocument } = useDocuments()
  const inputRef = React.useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = React.useState(false)
  const [record, setRecord] = React.useState<PatientDocumentRecord | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [accepted, setAccepted] = React.useState<Record<string, boolean>>({})

  const handleFile = async (file: File | undefined) => {
    if (!file) return
    setError(null)
    setUploading(true)
    setAccepted({})
    try {
      const result = await uploadDocument({ file, patientId, documentType, uploadedBy: actor })
      setRecord(result)
      onUploaded?.(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const clear = () => {
    setRecord(null)
    setError(null)
    setAccepted({})
    if (inputRef.current) inputRef.current.value = ''
  }

  const statusMeta = record ? STATUS_META[record.ocrStatus] : null

  return (
    <div className="space-y-3">
      {!record ? (
        <label className="flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-md border border-dashed border-border bg-input-background px-3 py-2 text-sm font-medium text-supporting transition-colors hover:bg-surface">
          {uploading ? <Loader2 className="size-4 animate-spin text-metadata" aria-hidden="true" /> : <Upload className="size-4 text-metadata" aria-hidden="true" />}
          {uploading ? 'Reading document…' : buttonLabel}
          <input ref={inputRef} type="file" accept={accept} className="sr-only" disabled={uploading} onChange={(e) => handleFile(e.target.files?.[0])} />
        </label>
      ) : (
        <div className="rounded-md border border-divider bg-surface-elevated/70 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2"><FileText className="size-4 shrink-0 text-metadata" /><span className="truncate text-sm font-medium text-supporting">{record.filename}</span></div>
            <div className="flex items-center gap-2">{statusMeta ? <Badge variant={statusMeta.variant}>{record.ocrStatus === 'processing' ? <Loader2 className="size-3 animate-spin" /> : null}{statusMeta.label}</Badge> : null}<Button type="button" size="sm" variant="ghost" onClick={clear}><Trash2 /></Button></div>
          </div>

          {record.ocrStatus === 'failed' && record.ocrError ? (
            <p className="mt-2 flex items-center gap-2 text-xs text-critical-strong"><AlertTriangle className="size-3.5 shrink-0" />{record.ocrError}</p>
          ) : null}

          {record.extractedFields && Object.keys(record.extractedFields).length > 0 ? (
            <div className="mt-3 space-y-2 border-t border-divider pt-3">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-metadata">Extracted — review before use</p>
              {Object.entries(record.extractedFields).map(([key, value]) => (
                <div key={key} className="flex flex-wrap items-center justify-between gap-2 rounded bg-input-background px-2.5 py-2 text-xs">
                  <div className="min-w-0"><span className="font-semibold text-supporting">{FIELD_LABELS[key] ?? key}: </span><span className="text-metadata">{value}</span></div>
                  {onFieldAccepted ? (
                    accepted[key] ? <Badge variant="success"><CheckCircle2 />Used</Badge> : <Button type="button" size="sm" variant="outline" onClick={() => { onFieldAccepted(key, value); setAccepted((a) => ({ ...a, [key]: true })) }}>Use this value</Button>
                  ) : null}
                </div>
              ))}
            </div>
          ) : record.ocrStatus === 'completed' || record.ocrStatus === 'needs_review' ? (
            <p className="mt-2 text-xs text-metadata">Text extracted, but no recognizable fields — review the document directly.</p>
          ) : null}
        </div>
      )}
      {error ? <p className="flex items-center gap-2 text-xs text-critical-strong"><AlertTriangle className="size-3.5 shrink-0" />{error}</p> : null}
    </div>
  )
}
