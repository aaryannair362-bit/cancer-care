'use client'

/**
 * DocumentsProvider — real file capture + real client-side OCR, persisted the same way
 * every other store in this app persists today (localStorage; see lib/oncology/store.tsx
 * for the identical pattern and the same "data-access swap, not a remodel" rationale).
 *
 * This exists because two attachment buttons shipped fully decorative: the Aadhaar/Ration
 * upload in registration-workspace.tsx had a file input with no onChange handler at all,
 * and nurse-intake-enhancements.tsx's "Upload / Capture document" button only ever set a
 * fake status string ("Demo capture ready — no file was stored."). Both now go through
 * this store and lib/documents/ocr.ts's real OCR — a selected file is genuinely read,
 * genuinely OCR'd, and genuinely persisted for this browser session.
 */

import * as React from 'react'

import { extractClinicalSignals, extractDocumentText, extractIdentityFields } from './ocr'
import type { ActorRef, DocumentOcrStatus, PatientDocumentRecord } from '@/lib/oncology/types'

const STORAGE_KEY = 'aivana-documents-v1'
// localStorage's typical per-origin quota is 5-10MB total, shared with every other store
// this app keeps there (see lib/oncology/store.tsx) — and a data: URL inflates a file by
// ~33%. 4MB keeps a single upload (plus its OCR text) comfortably inside that budget.
const MAX_FILE_SIZE_BYTES = 4 * 1024 * 1024

function safePersist(next: PatientDocumentRecord[]): boolean {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    return true
  } catch {
    // Quota exceeded or storage unavailable — keep working in memory for this session
    // rather than crash; the upload still succeeds, it just won't survive a reload.
    return false
  }
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read file'))
    reader.readAsDataURL(file)
  })
}

let idCounter = 0
function nextId() {
  idCounter += 1
  return `doc-${Date.now()}-${idCounter}`
}

type DocumentsContextValue = {
  ready: boolean
  documents: PatientDocumentRecord[]
  getDocumentsForPatient: (patientId: string) => PatientDocumentRecord[]
  uploadDocument: (input: { file: File; patientId: string; documentType: string; uploadedBy: ActorRef }) => Promise<PatientDocumentRecord>
  removeDocument: (id: string) => void
}

const DocumentsContext = React.createContext<DocumentsContextValue | null>(null)

export function DocumentsProvider({ children }: { children: React.ReactNode }) {
  const [documents, setDocuments] = React.useState<PatientDocumentRecord[]>([])
  const [ready, setReady] = React.useState(false)

  React.useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY)
    if (saved) {
      try {
        setDocuments(JSON.parse(saved) as PatientDocumentRecord[])
      } catch {
        // Corrupt local state — start empty rather than crash the app.
      }
    }
    setReady(true)
  }, [])

  const getDocumentsForPatient = React.useCallback((patientId: string) => documents.filter((d) => d.patientId === patientId), [documents])

  const uploadDocument = React.useCallback(
    async ({ file, patientId, documentType, uploadedBy }: { file: File; patientId: string; documentType: string; uploadedBy: ActorRef }) => {
      if (file.size === 0) throw new Error('This file is empty (0 bytes) — choose a different file.')
      if (file.size > MAX_FILE_SIZE_BYTES) throw new Error(`File is ${(file.size / (1024 * 1024)).toFixed(1)} MB — this browser session can hold files up to ${MAX_FILE_SIZE_BYTES / (1024 * 1024)} MB. Try a smaller scan or a lower-resolution photo.`)

      const dataUrl = await readFileAsDataUrl(file)
      let record: PatientDocumentRecord = {
        id: nextId(), patientId, filename: file.name, contentType: file.type, fileSize: file.size,
        documentType, dataUrl, ocrStatus: 'processing' as DocumentOcrStatus, uploadedBy, uploadedAt: new Date().toISOString(),
      }
      setDocuments((current) => { const next = [record, ...current]; safePersist(next); return next })

      try {
        const result = await extractDocumentText(file)
        const signals = extractClinicalSignals(result.text)
        const identity = extractIdentityFields(result.text)
        const extractedFields: Record<string, string> = {}
        for (const [key, values] of Object.entries(signals)) {
          if (values.length > 0) extractedFields[key] = values.join('; ')
        }
        if (identity.documentNumber) extractedFields.documentNumber = identity.documentNumber
        if (identity.nameOnDocument) extractedFields.nameOnDocument = identity.nameOnDocument
        record = {
          ...record, ocrStatus: result.failedPages.length > 0 ? 'needs_review' : 'completed', ocrEngine: result.engine,
          extractedText: result.text, extractedFields, processedAt: new Date().toISOString(),
        }
      } catch (error) {
        record = { ...record, ocrStatus: 'failed', ocrError: error instanceof Error ? error.message : 'OCR failed', processedAt: new Date().toISOString() }
      }

      setDocuments((current) => {
        const next = current.map((d) => (d.id === record.id ? record : d))
        safePersist(next)
        return next
      })
      return record
    },
    []
  )

  const removeDocument = React.useCallback(
    (id: string) => {
      setDocuments((current) => {
        const next = current.filter((d) => d.id !== id)
        safePersist(next)
        return next
      })
    },
    []
  )

  const value: DocumentsContextValue = { ready, documents, getDocumentsForPatient, uploadDocument, removeDocument }
  return <DocumentsContext.Provider value={value}>{children}</DocumentsContext.Provider>
}

export function useDocuments() {
  const value = React.useContext(DocumentsContext)
  if (!value) throw new Error('DocumentsProvider is required')
  return value
}
