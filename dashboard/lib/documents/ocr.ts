/**
 * Client-side document text extraction — real OCR, no backend dependency.
 *
 * Mirrors the per-page strategy of the deployed backend's OCR pipeline
 * (backend/app/ocr_service.py): for a PDF, try each page's embedded text first
 * (pdfjs-dist's text layer, equivalent to the backend's pypdf pass); a page with fewer
 * than 40 characters of embedded text is rendered to a canvas and OCR'd instead
 * (tesseract.js, equivalent to the backend's RapidOCR fallback). Plain images
 * (JPEG/PNG/etc.) always go straight to OCR. This covers text-only, image-only and
 * mixed PDFs the same way the backend does, entirely in the browser.
 *
 * Why client-side rather than calling the real backend: the deployed FastAPI service's
 * document endpoint (backend/app/routers/patient_documents.py) requires an authenticated
 * backend user and a real numeric Patient row scoped to an organization — neither of
 * which this app's localStorage-only demo role/patient model has today. Building that
 * auth bridge is a separate, larger integration than "make the upload button work" and
 * is intentionally not improvised here. See PatientDocumentRecord in lib/oncology/types.ts
 * for how this stays a drop-in swap once that bridge exists.
 *
 * pdf.js requires its worker script served as a static asset — copied at
 * public/pdf.worker.min.mjs from node_modules/pdfjs-dist/build/pdf.worker.min.mjs. Keep
 * that file in sync if the pdfjs-dist dependency version ever changes (a mismatched
 * worker/API version throws at first use, not silently).
 */
'use client'

const MIN_EMBEDDED_TEXT_LENGTH = 40

export type OcrPage = { page: number; text: string; method: 'embedded_text' | 'ocr' | 'ocr_failed' }
export type OcrResult = { text: string; engine: string; pages: OcrPage[]; failedPages: number[] }

let tesseractWorkerPromise: Promise<import('tesseract.js').Worker> | null = null
async function getTesseractWorker() {
  if (!tesseractWorkerPromise) {
    const { createWorker } = await import('tesseract.js')
    tesseractWorkerPromise = createWorker('eng')
  }
  return tesseractWorkerPromise
}

async function ocrCanvas(canvas: HTMLCanvasElement): Promise<string> {
  const worker = await getTesseractWorker()
  const { data } = await worker.recognize(canvas)
  return (data.text || '').trim()
}

async function ocrImageFile(file: File): Promise<OcrResult> {
  const bitmap = await createImageBitmap(file)
  const canvas = document.createElement('canvas')
  canvas.width = bitmap.width
  canvas.height = bitmap.height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas 2D context unavailable')
  ctx.drawImage(bitmap, 0, 0)
  try {
    const text = await ocrCanvas(canvas)
    if (!text) throw new Error('No readable text was found in this image')
    return { text, engine: 'tesseract.js', pages: [{ page: 1, text, method: 'ocr' }], failedPages: [] }
  } catch (error) {
    return { text: '', engine: 'tesseract.js', pages: [{ page: 1, text: '', method: 'ocr_failed' }], failedPages: [1] }
  }
}

async function extractPdfPageToCanvas(page: import('pdfjs-dist').PDFPageProxy): Promise<HTMLCanvasElement> {
  const viewport = page.getViewport({ scale: 2.2 })
  const canvas = document.createElement('canvas')
  canvas.width = viewport.width
  canvas.height = viewport.height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas 2D context unavailable')
  await page.render({ canvasContext: ctx, viewport }).promise
  return canvas
}

async function ocrPdfFile(file: File): Promise<OcrResult> {
  const pdfjsLib = await import('pdfjs-dist')
  pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'

  const buffer = await file.arrayBuffer()
  const doc = await pdfjsLib.getDocument({ data: buffer }).promise
  const pages: OcrPage[] = []
  const engines = new Set<string>()

  for (let i = 1; i <= doc.numPages; i += 1) {
    const page = await doc.getPage(i)
    const content = await page.getTextContent()
    const embeddedText = content.items.map((item) => ('str' in item ? item.str : '')).join(' ').replace(/\s+/g, ' ').trim()

    if (embeddedText.length >= MIN_EMBEDDED_TEXT_LENGTH) {
      pages.push({ page: i, text: embeddedText, method: 'embedded_text' })
      engines.add('pdfjs')
      continue
    }

    try {
      const canvas = await extractPdfPageToCanvas(page)
      const ocrText = await ocrCanvas(canvas)
      if (ocrText) {
        pages.push({ page: i, text: ocrText, method: 'ocr' })
        engines.add('tesseract.js')
      } else {
        pages.push({ page: i, text: embeddedText, method: 'ocr_failed' })
      }
    } catch {
      pages.push({ page: i, text: embeddedText, method: 'ocr_failed' })
    }
  }

  const text = pages.map((p) => p.text).filter(Boolean).join('\n\n').trim()
  const failedPages = pages.filter((p) => p.method === 'ocr_failed').map((p) => p.page)
  if (!text) throw new Error('No readable text was found in this document')
  return { text, engine: Array.from(engines).join('+') || 'pdfjs', pages, failedPages }
}

export async function extractDocumentText(file: File): Promise<OcrResult> {
  if (file.type === 'application/pdf') return ocrPdfFile(file)
  if (file.type.startsWith('image/')) return ocrImageFile(file)
  throw new Error('Upload a PDF, JPEG, PNG, or TIFF file')
}

// ───────────────────────── Conservative signal extraction ─────────────────────────
// Deliberately mirrors backend/app/ocr_service.py's _clinical_signals(): never
// manufacture a missing fact, only surface what a "Label: value" line actually says,
// and every value stays PROPOSED for a human to confirm — see the upload component.

const CLINICAL_PATTERNS: Record<string, RegExp> = {
  diagnoses: /(?:diagnosis|impression|assessment)\s*[:\-]\s*(.+)/i,
  medications: /(?:medications?|drugs?|prescription)\s*[:\-]\s*(.+)/i,
  allergies: /(?:allerg(?:y|ies)|drug allergies?)\s*[:\-]\s*(.+)/i,
  investigations: /(?:investigations?|laboratory|lab results?|findings?)\s*[:\-]\s*(.+)/i,
  procedures: /(?:procedures?|surgery|operation)\s*[:\-]\s*(.+)/i,
}

export function extractClinicalSignals(text: string): Record<string, string[]> {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  const result: Record<string, string[]> = { diagnoses: [], medications: [], allergies: [], investigations: [], procedures: [] }
  for (const line of lines) {
    for (const [key, pattern] of Object.entries(CLINICAL_PATTERNS)) {
      const match = line.match(pattern)
      if (match) {
        const value = match[1].trim()
        if (value && !['nil', 'none', 'n/a'].includes(value.toLowerCase()) && !result[key].includes(value)) {
          result[key].push(value.slice(0, 1000))
        }
      }
    }
  }
  return result
}

// ID-document extraction (Aadhaar / ration card / any government ID photo): a document
// number pattern (4-4-4 digit groups, or any 6+ digit run) and a name. Always presented
// for the front-desk user to confirm or correct — never auto-saved.
const DOCUMENT_HEADER_WORDS = /^(government|republic|union|state|department|ministry|card|certificate|identity|proof)\b/i

export function extractIdentityFields(text: string): { documentNumber?: string; nameOnDocument?: string } {
  const numberMatch = text.match(/\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b/) ?? text.match(/\b[A-Z]{0,3}\d{6,12}\b/)
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)

  // Prefer an explicitly labeled "Name: ..." line over any positional guess.
  const labeledName = lines.map((line) => line.match(/(?:^|\s)name\s*[:\-]\s*(.+)/i)).find((m) => m)?.[1]?.trim()
  const nameLine = labeledName || lines.find((line) => /^[A-Za-z][A-Za-z .'-]{2,60}$/.test(line) && !/\d/.test(line) && !DOCUMENT_HEADER_WORDS.test(line))

  return {
    documentNumber: numberMatch?.[0]?.replace(/\s+/g, ' ').trim(),
    nameOnDocument: nameLine,
  }
}
