"""
Adversarial/edge-case document generator for OCR/document-ingestion break-testing.

Each function returns raw `bytes` for a document the ingestion pipeline
(backend/app/ocr_service.py, backend/app/cca_engine.py, backend/app/document_pages.py,
backend/app/routers/cca.py's upload_document / routers/patient_documents.py's upload_document)
has never seen before -- generated at call time rather than committed as binary fixtures, so
these stay reviewable as plain Python and never bit-rot as opaque blobs in git.

Reuses tools/generate_scenario_docs.py's reportlab pattern (SimpleDocTemplate + flowables) for
realistic PDF content, pypdf for encryption/corruption, and PIL for image/TIFF fixtures. Lab
report lines are rendered as one bare "<Label>: <value> <unit>" Paragraph per line (NOT a
two-column Table) deliberately -- both pypdf's embedded-text extraction and a real OCR engine's
line-level detection (see ocr_service._run_ocr's docstring) turn one Paragraph into one line,
which is exactly the shape ocr_service._LAB_VALUE_LINE_PATTERNS requires to fire. A two-column
table's cell text has no such guarantee.
"""
from __future__ import annotations

import io
import os

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image as RLImage

_LINE_STYLE = ParagraphStyle("Line", fontName="Helvetica", fontSize=10, leading=14)
_HEADING_STYLE = ParagraphStyle("Heading", fontName="Helvetica-Bold", fontSize=13, leading=17, spaceAfter=8)

# One real example line per ocr_service._LAB_TEST_ALIASES entry (kept in sync manually -- this
# is test-fixture content, not the app's own alias list) so a generated report exercises every
# alias at least once, not just a handful.
_LAB_LINES = [
    "Hemoglobin: 11.2 g/dL", "Total Leukocyte Count: 9800 /cumm", "Platelet Count: 210 x10^9/L",
    "Neutrophils: 62 %", "Lymphocytes: 30 %", "ESR: 18 mm/hr", "CRP: 4.2 mg/L",
    "Creatinine: 0.9 mg/dL", "Blood Urea Nitrogen: 14 mg/dL", "Sodium: 138 mEq/L",
    "Potassium: 4.1 mEq/L", "Calcium: 9.2 mg/dL", "Albumin: 4.0 g/dL",
    "Total Bilirubin: 0.8 mg/dL", "SGOT: 28 U/L", "SGPT: 24 U/L", "Alkaline Phosphatase: 76 U/L",
    "LDH: 180 U/L", "Blood Glucose: 96 mg/dL", "HbA1c: 5.6 %", "TSH: 2.1 mIU/L",
    "PSA: 0.8 ng/mL", "CEA: 1.9 ng/mL", "CA-125: 12 U/mL", "CA 19-9: 15 U/mL",
    "CA 15-3: 11 U/mL", "INR: 1.0",
]


def _render_pdf(story: list) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    doc.build(story)
    return buf.getvalue()


def huge_lab_report_pdf(pages: int = 90, lines_per_page: int = 30) -> bytes:
    """A synthetic multi-page (default 90) lab/pathology report -- far past a single LLM call's
    worth of text and past Sarvam's 10-page-per-job chunk boundary (9 chunks at the default page
    count), so this exercises classify_and_extract_page's per-chunk fact extraction (Tier 2)
    across the FULL document, and ocr_service._clinical_signals's lab-value scanner (Tier 3) on
    lines far beyond what any single LLM call would ever see in one prompt."""
    story = [Paragraph(f"Synthetic Lab & Pathology Report -- Page batch 1 of {pages}", _HEADING_STYLE)]
    for page_num in range(pages):
        if page_num > 0:
            story.append(PageBreak())
            story.append(Paragraph(f"Clinical Pathology Laboratory -- Report page {page_num + 1} of {pages}", _HEADING_STYLE))
        for i in range(lines_per_page):
            line = _LAB_LINES[(page_num * lines_per_page + i) % len(_LAB_LINES)]
            story.append(Paragraph(line, _LINE_STYLE))
            story.append(Spacer(1, 2))
    return _render_pdf(story)


def dense_single_page_lab_report_pdf() -> bytes:
    """One page, every known lab alias represented at least once -- stresses the lab-value
    regex's alias coverage/false-positive rate on a realistically dense single report page
    rather than the huge multi-page document's per-chunk behavior."""
    story = [Paragraph("Comprehensive Metabolic & Tumor Marker Panel", _HEADING_STYLE)]
    for line in _LAB_LINES:
        story.append(Paragraph(line, _LINE_STYLE))
    return _render_pdf(story)


def mixed_image_and_text_pdf(text_pages: int = 2, image_pages: int = 2) -> bytes:
    """Alternates real-text pages (>=40 chars native text) with image-only pages (no extractable
    native text at all) in one document -- exercises ocr_service._extract_local's per-page
    is_image_heavy/needs_ocr branching within a single upload rather than across separate files."""
    from PIL import Image as PILImage

    story = []
    for i in range(max(text_pages, image_pages)):
        if i > 0:
            story.append(PageBreak())
        if i < text_pages:
            story.append(Paragraph(f"Consult Note -- Page {i + 1}", _HEADING_STYLE))
            story.append(Paragraph(
                "Chief Complaint: Persistent cough for three weeks. History: No fever, mild "
                "weight loss. Impression: Findings ordered as below.", _LINE_STYLE,
            ))
        if i < image_pages:
            if i >= text_pages:
                story.append(Spacer(1, 20))
            img_buf = io.BytesIO()
            PILImage.new("RGB", (900, 1200), color=(40 + i * 30, 40, 40)).save(img_buf, format="PNG")
            img_buf.seek(0)
            story.append(RLImage(img_buf, width=4 * inch, height=5.3 * inch))
    return _render_pdf(story)


def password_protected_pdf(password: str = "Str0ng!TestPassw0rd") -> bytes:
    """A REAL encrypted PDF requiring a real (non-empty) password -- distinct from an
    unencrypted PDF, exercises ocr_service._extract_local's `reader.decrypt("")` path against a
    password it cannot actually satisfy (as opposed to the empty-password-works case an
    unprotected PDF trivially passes)."""
    from pypdf import PdfReader, PdfWriter

    plain = _render_pdf([
        Paragraph("Confidential Referral Letter", _HEADING_STYLE),
        Paragraph("Please evaluate and advise oncology management for this patient.", _LINE_STYLE),
    ])
    reader = PdfReader(io.BytesIO(plain))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password=password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def corrupted_pdf() -> bytes:
    """A valid %PDF- header followed by garbage -- no valid xref/trailer, so pypdf.PdfReader
    construction itself fails. Distinct from password_protected_pdf (that one parses fine and
    fails only at decrypt/extract time)."""
    return b"%PDF-1.7\n" + os.urandom(512) + b"\n%%NOT-A-REAL-TRAILER"


def zero_byte_file() -> bytes:
    return b""


def oversized_pdf(target_mb: int = 205) -> bytes:
    """Exceeds MAX_PATIENT_DOCUMENT_MB (200, matched to Sarvam Document AI's own accepted
    upload size) purely by size -- both upload_document endpoints reject on
    `len(content) > max_bytes` before ever attempting to parse the file, so the PDF content
    itself doesn't need to be valid for this case."""
    return b"%PDF-1.4\n" + os.urandom(target_mb * 1024 * 1024)


def multiframe_tiff(frames: int = 4) -> bytes:
    """A multi-page TIFF (common scanner output) -- exercises ocr_service._extract_local's
    image branch's per-frame Image.seek(i) loop rather than the single-frame JPEG/PNG case."""
    from PIL import Image as PILImage, ImageDraw

    images = []
    for i in range(frames):
        img = PILImage.new("RGB", (800, 1000), color="white")
        draw = ImageDraw.Draw(img)
        draw.text((40, 40), f"Scanned Page {i + 1} of {frames}", fill="black")
        draw.rectangle([40, 100, 760, 900], outline="black", width=2)
        images.append(img)
    buf = io.BytesIO()
    images[0].save(buf, format="TIFF", save_all=True, append_images=images[1:])
    return buf.getvalue()


def devanagari_scan_image() -> bytes:
    """A raster image containing Devanagari text -- RapidOCR/Sarvam both document Latin-script
    as their effective scope (see ocr_service.py's module docstring); this exercises the
    documented out-of-scope-language failure mode rather than a genuine bug. Falls back to a
    blank (still text-free) image if no Devanagari-capable font is found on this machine, which
    still exercises the same "OCR finds nothing readable" path."""
    from PIL import Image as PILImage, ImageDraw, ImageFont

    img = PILImage.new("RGB", (900, 300), color="white")
    draw = ImageDraw.Draw(img)
    text = "\u0930\u094b\u0917\u0940 \u0915\u093e \u0928\u093f\u0926\u093e\u0928: \u0938\u094d\u0924\u0928 \u0915\u0948\u0902\u0938\u0930"  # "Patient diagnosis: breast cancer"
    for font_path in (r"C:\Windows\Fonts\mangal.ttf", r"C:\Windows\Fonts\Nirmala.ttf"):
        try:
            font = ImageFont.truetype(font_path, 36)
            draw.text((30, 120), text, fill="black", font=font)
            break
        except Exception:
            continue
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


ADVERSARIAL_DOCUMENTS: dict[str, tuple[str, str]] = {
    # name -> (filename, content_type); bytes come from calling the same-named function above.
    "huge_lab_report": ("huge_lab_report.pdf", "application/pdf"),
    "dense_single_page_lab_report": ("dense_single_page_lab_report.pdf", "application/pdf"),
    "mixed_image_and_text": ("mixed_image_and_text.pdf", "application/pdf"),
    "password_protected": ("password_protected.pdf", "application/pdf"),
    "corrupted": ("corrupted.pdf", "application/pdf"),
    "zero_byte": ("zero_byte.pdf", "application/pdf"),
    "oversized": ("oversized.pdf", "application/pdf"),
    "multiframe_tiff": ("multiframe.tiff", "image/tiff"),
    "devanagari_scan": ("devanagari_scan.png", "image/png"),
}

_BUILDERS = {
    "huge_lab_report": huge_lab_report_pdf,
    "dense_single_page_lab_report": dense_single_page_lab_report_pdf,
    "mixed_image_and_text": mixed_image_and_text_pdf,
    "password_protected": password_protected_pdf,
    "corrupted": corrupted_pdf,
    "zero_byte": zero_byte_file,
    "oversized": oversized_pdf,
    "multiframe_tiff": multiframe_tiff,
    "devanagari_scan": devanagari_scan_image,
}


def build(name: str) -> bytes:
    """Look up + build one adversarial document by its ADVERSARIAL_DOCUMENTS key."""
    return _BUILDERS[name]()
