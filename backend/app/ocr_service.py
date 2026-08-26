"""PDF/image text extraction with evidence-preserving, deterministic clinical parsing.

OCR engine: docTR (pure pip install -- torch + torchvision, no system package). Switched from
Tesseract specifically to drop the system-binary dependency that required either a Docker build
step (`apt-get install tesseract-ocr`) or an unverifiable PATH lookup on Render's native Python
runtime -- docTR's model weights download from Hugging Face at first use and cache under the
process's home directory, no OS package manager involved. Chosen over EasyOCR/RapidOCR/PaddleOCR
per this repo's own measured comparison (see OCR_BENCHMARK.md): docTR is the closest match to
Tesseract's accuracy (81.72% vs. 82.71%) of any pip-only engine tested, at a latency (~11s/page
on the benchmark machine) that's still well inside the 30s/page production timeout -- EasyOCR
was slower and less accurate, PaddleOCR matched accuracy but took 190s/page and had Windows
install issues, RapidOCR was both slower and less accurate. Trade-off, stated plainly: docTR's
pretrained recognition model targets Latin-script text; unlike Tesseract or EasyOCR, it does not
offer a per-language model or language-code setting, so it is not a fit for documents in
non-Latin scripts (e.g. Devanagari). All the benchmark's source documents were English.
"""
from __future__ import annotations

import io
import re
import threading
from datetime import datetime
from typing import Any

_predictor = None
_predictor_lock = threading.Lock()


def _get_predictor():
    """Lazy singleton: docTR's ocr_predictor() loads model weights (a few seconds the first
    time, cached to disk after) -- built once per process on first real OCR call, not at import
    time, so a deployment that never actually uses OCR doesn't pay that cost on every startup."""
    global _predictor
    if _predictor is None:
        with _predictor_lock:
            if _predictor is None:
                from doctr.models import ocr_predictor
                _predictor = ocr_predictor(pretrained=True)
    return _predictor


def _run_ocr(image) -> str:
    """image: a PIL.Image. Returns extracted text, one line per detected text line, words
    space-joined within a line -- the same "Label: value" per-line structure
    _clinical_signals()'s regex parser below depends on (it identifies a field, e.g.
    "Medications: ...", by it starting its own line). docTR's `.export()` groups words into
    lines/blocks/pages already in reading order, so no extra sorting is needed."""
    import numpy as np
    predictor = _get_predictor()
    exported = predictor([np.array(image.convert("RGB"))]).export()
    lines = [
        " ".join(word["value"] for word in line["words"])
        for page in exported["pages"]
        for block in page["blocks"]
        for line in block["lines"]
    ]
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
                engines.append("doctr")
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
            engines.append("doctr")
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
