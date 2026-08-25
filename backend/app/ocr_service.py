"""PDF/image text extraction with evidence-preserving, deterministic clinical parsing.

OCR engine: EasyOCR (pure pip install -- torch + torchvision, no system package). Switched
from Tesseract specifically to drop the system-binary dependency that required either a Docker
build step (`apt-get install tesseract-ocr`) or an unverifiable PATH lookup on Render's native
Python runtime -- EasyOCR's model weights are pulled from PyPI/its own CDN at first use and
cached under the process's home directory, no OS package manager involved. Trade-off, stated
plainly: Tesseract has broader Indic-script language-pack coverage than EasyOCR's supported
list (https://www.jaided.ai/easyocr/) -- if a document language this hospital needs isn't in
that list, this module will only extract the languages actually configured in OCR_LANGUAGES.
"""
from __future__ import annotations

import io
import re
import threading
from datetime import datetime
from typing import Any

_reader = None
_reader_lock = threading.Lock()


def _get_reader():
    """Lazy singleton: EasyOCR's Reader() loads model weights (~4s the first time, cached to
    disk after) -- built once per process on first real OCR call, not at import time, so a
    deployment that never actually uses OCR doesn't pay that cost on every startup."""
    global _reader
    if _reader is None:
        with _reader_lock:
            if _reader is None:
                import easyocr
                from .config import settings
                languages = [lang.strip() for lang in settings.OCR_LANGUAGES.split(",") if lang.strip()] or ["en"]
                _reader = easyocr.Reader(languages, gpu=False)
    return _reader


def _run_ocr(image) -> str:
    """image: a PIL.Image. Returns extracted text, one line per detected text region.
    Deliberately paragraph=False (EasyOCR's default): paragraph=True merges vertically-close
    lines into one run-on string, which breaks _clinical_signals()'s regex parser below --
    that parser identifies a field (e.g. "Medications: ...") by it starting its own line, the
    same "Label: value" per-line structure Tesseract's line-oriented output always preserved.
    Reading order follows EasyOCR's own detection order, not strictly guaranteed top-to-bottom
    for complex layouts, but adequate for the mostly-linear clinical report layouts this module
    targets."""
    import numpy as np
    reader = _get_reader()
    lines = reader.readtext(np.array(image.convert("RGB")), detail=0, paragraph=False)
    return "\n".join(lines).strip()


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
            try:
                import pymupdf
                from PIL import Image
                doc = pymupdf.open(stream=content, filetype="pdf")
                for index in needs_ocr:
                    pix = doc[index].get_pixmap(matrix=pymupdf.Matrix(2.2, 2.2), alpha=False)
                    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    ocr_by_page[index] = _run_ocr(image)
                engines.append("easyocr")
            except Exception as exc:
                # Native pages remain useful; fail only when the complete document has no text.
                if not any(native):
                    raise RuntimeError(f"Scanned PDF needs OCR: {exc}") from exc
        engines.insert(0, "pypdf")
        for i, native_text in enumerate(native):
            chosen = native_text if len(native_text) >= 40 else ocr_by_page.get(i, native_text)
            page_text.append({"page": i + 1, "text": chosen, "method": "ocr" if i in ocr_by_page else "embedded_text"})
    else:
        try:
            from PIL import Image
            image = Image.open(io.BytesIO(content))
            text = _run_ocr(image)
            page_text = [{"page": 1, "text": text, "method": "ocr"}]
            engines.append("easyocr")
        except Exception as exc:
            raise RuntimeError(f"Image OCR failed: {exc}") from exc

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
    }
