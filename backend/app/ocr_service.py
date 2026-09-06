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
"""
from __future__ import annotations

import io
import re
import threading
from datetime import datetime
from typing import Any

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


def extract_document(content: bytes, content_type: str) -> dict[str, Any]:
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
