"""PDF/image text extraction with evidence-preserving, deterministic clinical parsing.

OCR engine: RapidOCR (pure pip install -- onnxruntime, no system package, no torch). This app
previously used docTR (also pip-only, chosen over Tesseract specifically to avoid the
`apt-get install tesseract-ocr` / Docker requirement -- see git history for that reasoning). docTR
was moved off after a live production OOM on Render's `starter` plan (512MB RAM, confirmed
31 Aug 2026 -- "ran out of memory (used over 512MB)"): `import torch` alone costs ~175MB RSS
before any model loads, and both docTR architectures measured in OCR_BENCHMARK.md's "Architecture
comparison" peaked at 572-597MB -- already over 512MB before this app's own FastAPI/uvicorn
process needs any memory. No docTR architecture choice closes that gap; the floor is torch itself.
RapidOCR (onnxruntime, no torch) measured at just 154MB peak RSS on the same methodology and
corpus -- see OCR_BENCHMARK.md's "Engine reconsidered for memory ceiling" section.

Trade-off, stated plainly and chosen knowingly: RapidOCR is less accurate than docTR. On this
repo's own clean synthetic test PDFs, RapidOCR scored 94.09% mean vs. docTR's 99.7%, with two of
five documents scoring notably worse (84-87%). On the original real-document OCR_BENCHMARK.md
corpus, RapidOCR scored 76.39% vs. docTR's 81.72% and Tesseract's 82.71% -- it was the
lowest-accuracy engine in that comparison. This was accepted specifically to fit the deployed
plan's memory ceiling without an infra/cost change; every ClinicalFact drafted from OCR text still
lands PROPOSED for clinician verification, never auto-finalized, which is the safety net this
trade-off leans on. If the plan is ever upgraded, moving back to docTR (or Tesseract, which needs
Docker) for the accuracy gain is worth reconsidering.

Language: RapidOCR's bundled models target Latin-script text; documents in non-Latin scripts
(e.g. Devanagari) are out of scope, same limitation docTR had. No per-page or per-request timeout
is actually enforced in code -- see OCR_BENCHMARK.md's Production behavior section.

OCR_PROVIDER="sarvam" (config.py; the default whenever SARVAM_API_KEY is configured) offloads
OCR to Sarvam's Document AI (Sarvam Vision 1.5) instead of running RapidOCR in this process at
all -- see _extract_via_sarvam() below. This addresses the two production failure modes RapidOCR
can't: it moves the compute off Render's free-tier process entirely (no more competing with
FastAPI/uvicorn for the same 512MB), and it uses a model built for full-page layout/table
understanding rather than RapidOCR's plain per-line text detection, which is what mixed
image+text pages need. On ANY failure (network, quota, an unexpected response shape) this falls
back automatically to the local RapidOCR path below -- same "never hard-fail a document"
philosophy this file already followed before Sarvam existed as an option.
"""
from __future__ import annotations

import io
import logging
import re
import threading
from datetime import datetime
from typing import Any

from sarvamai import SarvamAI

from .config import settings

logger = logging.getLogger(__name__)

_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    """Lazy singleton: RapidOCR() loads its bundled ONNX models on first construction (under a
    second, per OCR_BENCHMARK.md) -- built once per process on first real OCR call, not at import
    time, so a deployment that never actually uses OCR doesn't pay that cost on every startup."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR
                _engine = RapidOCR()
    return _engine


def _run_ocr(image) -> str:
    """image: a PIL.Image. Returns extracted text, one line per detected text region -- the same
    "Label: value" per-line structure _clinical_signals()'s regex parser below depends on (it
    identifies a field, e.g. "Medications: ...", by it starting its own line). Verified against
    this repo's own scenario PDFs: RapidOCR's detector already groups words into line-level
    regions in reading order, not word-by-word, so no extra grouping/sorting is needed here."""
    import numpy as np
    engine = _get_engine()
    result, _ = engine(np.asarray(image.convert("RGB")))
    return "\n".join(item[1] for item in (result or [])).strip()


def _clinical_signals(text: str) -> dict[str, Any]:
    """Extract conservative, reviewable signals; never manufacture missing clinical facts."""
    compact = re.sub(r"[ \t]+", " ", text or "").strip()
    lines = [line.strip(" :-\t") for line in compact.splitlines() if line.strip()]
    patterns = {
        "diagnoses": r"(?:diagnosis|impression|assessment)\s*[:\-]\s*(.+?)(?=\s{2,}(?:medications?|allerg|investig|findings?|procedures?)\s*[:\-]|$)",
        "medications": r"(?:medications?|drugs?|prescription)\s*[:\-]\s*(.+?)(?=\s{2,}(?:diagnosis|allerg|investig|findings?|procedures?)\s*[:\-]|$)",
        "allergies": r"(?:allerg(?:y|ies)|drug allergies?)\s*[:\-]\s*(.+?)(?=\s{2,}(?:diagnosis|medications?|investig|findings?|procedures?)\s*[:\-]|$)",
        "investigations": r"(?:investigations?|laboratory|lab results?|findings?)\s*[:\-]\s*(.+?)(?=\s{2,}(?:diagnosis|medications?|allerg|procedures?)\s*[:\-]|$)",
        "procedures": r"(?:procedures?|surgery|operation)\s*[:\-]\s*(.+?)(?=\s{2,}(?:diagnosis|medications?|allerg|investig|findings?)\s*[:\-]|$)",
    }
    result: dict[str, Any] = {key: [] for key in patterns}
    for line in lines:
        for key, pattern in patterns.items():
            match = re.search(pattern, line, flags=re.I)
            if match:
                value = match.group(1).strip()
                if value and value.lower() not in {"nil", "none", "n/a"} and value not in result[key]:
                    result[key].append(value[:1000])
    date_matches = re.findall(r"\b(?:0?[1-9]|[12]\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:19|20)\d{2}\b", compact)
    result["dates_mentioned"] = list(dict.fromkeys(date_matches))[:20]
    result["text_preview"] = compact[:1200]
    return result


def _extract_local(content: bytes, content_type: str) -> dict[str, Any]:
    page_text: list[dict[str, Any]] = []
    engines: list[str] = []

    if content_type == "application/pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise ValueError("Password-protected PDFs are not supported") from exc
        native = [(page.extract_text() or "").strip() for page in reader.pages]
        needs_ocr = [i for i, text in enumerate(native) if len(text) < 40]
        ocr_by_page: dict[int, str] = {}
        if needs_ocr:
            doc = None
            try:
                import pymupdf
                from PIL import Image
                doc = pymupdf.open(stream=content, filetype="pdf")
            except Exception:
                doc = None
            if doc is not None:
                for index in needs_ocr:
                    # Each page is attempted independently -- a corrupted page or a transient
                    # failure (e.g. memory pressure) must not abort OCR for every page after it
                    # in the same document. Previously one bad page silently dropped the rest of
                    # a multi-page report with no error surfaced anywhere.
                    try:
                        pix = doc[index].get_pixmap(matrix=pymupdf.Matrix(2.2, 2.2), alpha=False)
                        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                        ocr_by_page[index] = _run_ocr(image)
                    except Exception:
                        continue
            if ocr_by_page:
                engines.append("rapidocr")
        engines.insert(0, "pypdf")
        for i, native_text in enumerate(native):
            if i in ocr_by_page:
                page_text.append({"page": i + 1, "text": ocr_by_page[i], "method": "ocr"})
            elif len(native_text) >= 40:
                page_text.append({"page": i + 1, "text": native_text, "method": "embedded_text"})
            elif i in needs_ocr:
                # Needed OCR and didn't get usable text -- tagged explicitly so callers can
                # surface this instead of it looking like a normal (if short) embedded-text page.
                page_text.append({"page": i + 1, "text": native_text, "method": "ocr_failed"})
            else:
                page_text.append({"page": i + 1, "text": native_text, "method": "embedded_text"})
    else:
        try:
            from PIL import Image
            image = Image.open(io.BytesIO(content))
        except Exception as exc:
            raise RuntimeError(f"Image OCR failed: {exc}") from exc
        # A multi-page TIFF (a common scanner output for multi-page paper records) holds several
        # frames; Image.open() alone only ever sees frame 0, so every page after the first was
        # being silently dropped with no error. JPEG/PNG report n_frames=1, so single-page
        # images are unaffected.
        for i in range(getattr(image, "n_frames", 1)):
            try:
                image.seek(i)
                page_text.append({"page": i + 1, "text": _run_ocr(image), "method": "ocr"})
            except Exception:
                page_text.append({"page": i + 1, "text": "", "method": "ocr_failed"})
        if any(p["method"] == "ocr" for p in page_text):
            engines.append("rapidocr")

    full_text = "\n\n".join(p["text"] for p in page_text if p["text"]).strip()
    if not full_text:
        raise ValueError("No readable text was found in this document")
    return {
        "text": full_text,
        "pages": page_text,
        "page_count": len(page_text),
        "engine": "+".join(dict.fromkeys(engines)),
        "signals": _clinical_signals(full_text),
        "processed_at": datetime.utcnow(),
        "ocr_failed_pages": [p["page"] for p in page_text if p["method"] == "ocr_failed"],
    }


# Sarvam Document AI's digitise endpoint accepts PDF/PNG/JPG/ZIP -- TIFF (also accepted by this
# app's own upload forms, for multi-page scanner output) is NOT in that list, so a TIFF is never
# even attempted here; it goes straight to the local RapidOCR path instead of wasting a call
# Sarvam would just reject.
_SARVAM_DOC_AI_CONTENT_TYPES = {"application/pdf", "image/png", "image/jpeg"}
# Verified against docs.sarvam.ai: PDF/ZIP uploads are capped at 10 pages per job. A longer PDF
# is split into consecutive <=10-page sub-documents, each digitised as its own job, and the
# results stitched back together in page order.
_SARVAM_DOC_AI_MAX_PAGES_PER_JOB = 10
# "md" (Markdown), not "html" or "json": the SDK's own docstring says output is delivered as a
# ZIP whose internal layout isn't otherwise documented -- Markdown is the one format that's
# still meaningfully readable as near-plain-text without needing to know that layout in advance
# (an HTML file would need tag-stripping; an undocumented JSON block schema would need guessing
# field names). This keeps _run_one_sarvam_doc_job's parsing honest about what it actually knows.
_SARVAM_DOC_AI_OUTPUT_FORMAT = "md"
_SARVAM_DOC_AI_POLL_INTERVAL_SEC = 2.0
_SARVAM_DOC_AI_TIMEOUT_SEC = 300.0


def _split_pdf_into_chunks(content: bytes, max_pages: int) -> list[tuple[int, bytes]]:
    """Returns [(first_page_number (1-indexed), chunk_pdf_bytes), ...], each chunk holding at
    most `max_pages` consecutive pages in original order. A PDF with <= max_pages pages returns
    a single chunk starting at page 1, unchanged."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(content))
    chunks: list[tuple[int, bytes]] = []
    for start in range(0, len(reader.pages), max_pages):
        writer = PdfWriter()
        for page in reader.pages[start:start + max_pages]:
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append((start + 1, buf.getvalue()))
    return chunks


# Sarvam Document AI's markdown output embeds full-resolution page/figure images inline as
# `![...](data:image/...;base64,<data>)` -- NOT documented in advance, found by running a real
# scanned multi-page document through the live API: on one real 10-page report this was 20
# embedded images consuming 95% of the returned "text" (623,713 of 625,492 characters). Left
# in place, this defeated clinical fact extraction two ways: extract_clinical_facts() only
# sends the first 8000 characters to Groq, so a document's actual content past the first
# embedded image (often within the first page) never reached the model at all; and even
# without that cap, feeding a fact-extraction prompt 95% base64 noise wastes tokens and risks
# hitting context limits. Strip these blocks -- they're never useful as "text" in any
# downstream consumer (_clinical_signals()'s regex, extract_clinical_facts(), the stored
# excerpt) -- while leaving Sarvam's own AI-generated alt-text captions for figures (e.g. "The
# image displays a circular blue ink stamp...") in place, since those already occasionally
# carry real information (a hospital name/seal) and cost only a sentence, not tens of
# thousands of characters.
_BASE64_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(data:image/[^;]+;base64,[^)]*\)")


def _strip_embedded_base64_images(text: str) -> str:
    return _BASE64_IMAGE_PATTERN.sub("", text).strip()


def _run_one_sarvam_doc_job(client, file_bytes: bytes, ext: str) -> str:
    """
    Runs one Sarvam Document AI digitise job on a single file (<= 10 pages, Sarvam's own cap)
    and returns its extracted text. Raises on any failure -- extract_document() below catches
    that and falls back to local RapidOCR for the whole document, so a wrong assumption in here
    (see the ZIP-parsing note) degrades to "OCR ran locally instead", never to bad clinical text.
    """
    import os
    import tempfile
    import zipfile

    with tempfile.TemporaryDirectory(prefix="sarvam_docai_") as tmp_dir:
        in_path = os.path.join(tmp_dir, f"document{ext}")
        with open(in_path, "wb") as f:
            f.write(file_bytes)

        job = client.document_intelligence.create_job(
            language=settings.SARVAM_OCR_LANGUAGE, output_format=_SARVAM_DOC_AI_OUTPUT_FORMAT,
        )
        job.upload_file(in_path)
        job.start()
        status = job.wait_until_complete(
            poll_interval=_SARVAM_DOC_AI_POLL_INTERVAL_SEC, timeout=_SARVAM_DOC_AI_TIMEOUT_SEC,
        )
        if status.job_state not in ("Completed", "PartiallyCompleted"):
            raise RuntimeError(f"Sarvam Document AI job did not complete (state={status.job_state})")

        zip_path = os.path.join(tmp_dir, "output.zip")
        job.download_output(zip_path)

        # Sarvam's digitise output ZIP was NOT documented in advance to hold more than one
        # file -- verified live against the real API during development: for output_format="md"
        # it actually contains BOTH the requested "document.md" (the clean text this function
        # wants) AND a "metadata/page_NNN.json" per page (block-level coordinates/confidence/
        # reading-order, meant for layout-aware consumers, not plain-text extraction). An
        # earlier version of this function concatenated every file in the ZIP indiscriminately,
        # which duplicated every page's text (once from the .md, once re-embedded inside the
        # metadata JSON's own "text" fields) and polluted _clinical_signals() with malformed,
        # JSON-escaped duplicate matches -- caught by testing against the real API, not assumed.
        # Only read the file(s) matching the requested output extension; ignore everything else
        # in the archive (metadata/*, or any future addition) by construction.
        texts = []
        with zipfile.ZipFile(zip_path) as zf:
            names = sorted(
                n for n in zf.namelist()
                if not n.endswith("/") and n.lower().endswith(f".{_SARVAM_DOC_AI_OUTPUT_FORMAT}")
            )
            for name in names:
                raw = zf.read(name).decode("utf-8", errors="replace")
                if raw.strip():
                    texts.append(raw)
        combined = "\n\n".join(texts).strip()
        combined = _strip_embedded_base64_images(combined)
        if not combined:
            raise RuntimeError("Sarvam Document AI returned an empty result")
        return combined


def _extract_via_sarvam(content: bytes, content_type: str) -> dict[str, Any]:
    if not settings.SARVAM_API_KEY:
        raise ValueError("Sarvam API key not configured.")
    if content_type not in _SARVAM_DOC_AI_CONTENT_TYPES:
        raise ValueError(f"Sarvam Document AI does not support {content_type!r}")

    client = SarvamAI(api_subscription_key=settings.SARVAM_API_KEY)

    if content_type == "application/pdf":
        from pypdf import PdfReader
        chunks = _split_pdf_into_chunks(content, _SARVAM_DOC_AI_MAX_PAGES_PER_JOB)
        page_count = len(PdfReader(io.BytesIO(content)).pages)
        ext = ".pdf"
    else:
        chunks = [(1, content)]
        page_count = 1
        ext = ".png" if content_type == "image/png" else ".jpg"

    # Each chunk is tagged with the 1-indexed page it starts at -- Sarvam's response doesn't
    # give per-page text within a job, so this is job-level (not true per-page) granularity;
    # for the common case (<=10 page document, one job) it's exactly one entry for the whole
    # document, same shape a single-image OCR result already has.
    page_text: list[dict[str, Any]] = [
        {"page": first_page, "text": _run_one_sarvam_doc_job(client, chunk_bytes, ext), "method": "sarvam_ocr"}
        for first_page, chunk_bytes in chunks
    ]

    full_text = "\n\n".join(p["text"] for p in page_text if p["text"]).strip()
    if not full_text:
        raise RuntimeError("Sarvam Document AI returned no readable text")

    return {
        "text": full_text,
        "pages": page_text,
        "page_count": page_count,
        "engine": "sarvam_doc_ai",
        "signals": _clinical_signals(full_text),
        "processed_at": datetime.utcnow(),
        "ocr_failed_pages": [],
    }


def extract_document(content: bytes, content_type: str) -> dict[str, Any]:
    """
    Extracts text (+ derived clinical signals) from a PDF or image. Tries Sarvam Document AI
    first when OCR_PROVIDER="sarvam" (config.py's default whenever SARVAM_API_KEY is set) --
    see this module's docstring for why. On ANY failure from that path (network error, quota,
    unsupported file type, an unexpected response shape), falls back to the local RapidOCR path
    (_extract_local) automatically, so Sarvam being briefly unavailable can never take document
    upload down entirely. With OCR_PROVIDER="local" (or no Sarvam key configured), goes straight
    to local RapidOCR, unchanged from this module's original behavior.
    """
    if settings.OCR_PROVIDER == "sarvam":
        try:
            return _extract_via_sarvam(content, content_type)
        except Exception as exc:
            logger.warning("Sarvam Document AI OCR failed, falling back to local OCR: %s", exc)
    return _extract_local(content, content_type)
