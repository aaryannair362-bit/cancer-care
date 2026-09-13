"""
Unit tests for app.ocr_service's OCR_PROVIDER="sarvam" path (Sarvam Document AI) -- see
ocr_service.py's module docstring for why this exists (offloads OCR compute off Render's
free-tier process, and handles mixed image+text pages better than local RapidOCR). The real
`sarvamai` SDK is never invoked here -- `ocr_service.SarvamAI` is replaced with a fake factory
mimicking the real SDK's document_intelligence job lifecycle (create_job -> upload_file -> start
-> wait_until_complete -> download_output, which saves a ZIP -- verified by reading the
installed SDK's source during development) without any network calls.

tests/integration/test_patient_document_ocr.py already covers the local RapidOCR path directly
(OCR_PROVIDER is pinned to "local" for the whole test suite in tests/conftest.py) -- these tests
are additive, exercising only the Sarvam path and the fallback-to-local behavior.
"""
import base64
import io
import os
import zipfile

import pytest
from pypdf import PdfWriter

from app import ocr_service


def _make_pdf(num_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class _FakeStatus:
    def __init__(self, job_state):
        self.job_state = job_state


class _FakeDocJob:
    def __init__(self, zip_bytes, final_state="Completed"):
        self._zip_bytes = zip_bytes
        self._final_state = final_state
        self.uploaded_path = None
        self.create_kwargs = {}

    def upload_file(self, file_path):
        self.uploaded_path = file_path

    def start(self):
        return _FakeStatus("Running")

    def wait_until_complete(self, poll_interval=2.0, timeout=300.0):
        return _FakeStatus(self._final_state)

    def download_output(self, output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(self._zip_bytes)


def _install_fake_sdk(monkeypatch, jobs):
    """jobs: a list of _FakeDocJob, consumed one per create_job() call in order -- mirrors one
    real job per <=10-page chunk for a multi-chunk PDF."""
    remaining = list(jobs)

    def _fake_sarvam_ai(*, api_subscription_key):
        assert api_subscription_key == "test-key"

        def _create_job(**kwargs):
            job = remaining.pop(0)
            job.create_kwargs = kwargs
            return job

        di_client = type("DI", (), {"create_job": staticmethod(_create_job)})()
        client = type("Client", (), {"document_intelligence": di_client})()
        return client

    monkeypatch.setattr(ocr_service, "SarvamAI", _fake_sarvam_ai)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(ocr_service.settings, "SARVAM_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _sarvam_provider(monkeypatch):
    monkeypatch.setattr(ocr_service.settings, "OCR_PROVIDER", "sarvam")


def test_strip_embedded_base64_images_removes_image_data_keeps_captions():
    """Regression test for a real bug found by running an actual 10-page scanned document
    through the live Sarvam API: its markdown output embedded 20 full-resolution page/figure
    images inline as base64 data URIs, consuming 95% of the "text" (623,713 of 625,492
    characters) -- which meant extract_clinical_facts()'s 8000-character cap to Groq was
    almost entirely base64 noise, silently burying real clinical content on later pages past
    where the model could ever see it. AI-generated figure captions (real, short, occasionally
    useful text) must survive; only the base64 payload itself is stripped."""
    text = (
        "Diagnosis: Breast carcinoma\n\n"
        "![Image](data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUG)\n\n"
        "*The image displays a circular blue ink stamp.*\n\n"
        "Medications: Tamoxifen 20mg OD"
    )
    cleaned = ocr_service._strip_embedded_base64_images(text)
    assert "base64" not in cleaned
    assert "Breast carcinoma" in cleaned
    assert "Medications: Tamoxifen 20mg OD" in cleaned
    assert "circular blue ink stamp" in cleaned


def test_extract_document_strips_embedded_base64_images_from_sarvam_output(monkeypatch):
    """End-to-end version of the same regression at the extract_document() level: a page
    dominated by an embedded base64 image must not drown out the real text on the same page."""
    huge_fake_image = "A" * 50000
    zip_bytes = _make_zip({"output.md": (
        f"Diagnosis: Breast carcinoma\n\n"
        f"![Image](data:image/jpeg;base64,{huge_fake_image})\n\n"
        f"Medications: Tamoxifen 20mg OD"
    )})
    _install_fake_sdk(monkeypatch, [_FakeDocJob(zip_bytes)])

    result = ocr_service.extract_document(_make_pdf(1), "application/pdf")

    assert len(result["text"]) < 1000  # real content only, not the ~50KB fake image
    assert "Breast carcinoma" in result["text"]
    assert "Tamoxifen" in result["text"]


def test_extract_document_uses_sarvam_and_parses_markdown_output(monkeypatch):
    zip_bytes = _make_zip({"output.md": "Diagnosis: Breast carcinoma\nMedications: Tamoxifen"})
    _install_fake_sdk(monkeypatch, [_FakeDocJob(zip_bytes)])

    result = ocr_service.extract_document(_make_pdf(1), "application/pdf")

    assert result["engine"] == "sarvam_doc_ai"
    assert "Breast carcinoma" in result["text"]
    assert result["signals"]["diagnoses"] == ["Breast carcinoma"]
    assert result["page_count"] == 1


def test_extract_document_splits_pdfs_over_ten_pages_into_multiple_jobs(monkeypatch):
    job1 = _FakeDocJob(_make_zip({"a.md": "Diagnosis: Lung cancer stage II"}))
    job2 = _FakeDocJob(_make_zip({"a.md": "Medications: Cisplatin"}))
    _install_fake_sdk(monkeypatch, [job1, job2])

    result = ocr_service.extract_document(_make_pdf(15), "application/pdf")

    assert result["page_count"] == 15
    assert len(result["pages"]) == 2
    assert result["pages"][0]["page"] == 1
    assert result["pages"][1]["page"] == 11
    assert "Lung cancer" in result["text"]
    assert "Cisplatin" in result["text"]


def test_extract_document_skips_a_failed_chunk_but_keeps_the_others(monkeypatch):
    """Regression test: a single chunk job failing (transient network blip, one oversized chunk)
    must not lose every other chunk's already-successful text -- previously this whole function
    was a list comprehension where any one job's exception aborted the entire document, throwing
    away chunks that had already succeeded."""
    job1 = _FakeDocJob(_make_zip({"a.md": "Diagnosis: Lung cancer stage II"}))
    job2 = _FakeDocJob(b"", final_state="Failed")
    job3 = _FakeDocJob(_make_zip({"a.md": "Medications: Cisplatin"}))
    _install_fake_sdk(monkeypatch, [job1, job2, job3])

    result = ocr_service.extract_document(_make_pdf(25), "application/pdf")  # 3 chunks: 1-10, 11-20, 21-25

    assert result["engine"] == "sarvam_doc_ai"
    assert "Lung cancer" in result["text"]
    assert "Cisplatin" in result["text"]
    assert len(result["pages"]) == 2  # the failed middle chunk is simply absent, not fatal


def test_extract_document_pages_reuses_sarvam_chunks_without_new_calls(monkeypatch):
    """Regression test for the real bug found against files in data_insurance/ (50- and 25-page
    fully-scanned real hospital records, 0 pages with any native text): extract_document_pages()
    used to fire one brand-new Sarvam job PER PAGE to get true page granularity, which reliably
    rate-limited itself into silence against Sarvam's real, documented Document Intelligence
    limit (10 requests/minute, uniform across every plan tier) -- a single job's own lifecycle
    already spends most of that budget, so a real multi-page document meant dozens of jobs
    back-to-back with no pacing, each failure silently dropping that page. It must now reuse the
    SAME chunk jobs extract_document() already ran, making zero additional Sarvam calls."""
    job1 = _FakeDocJob(_make_zip({"a.md": "Diagnosis: Lung cancer stage II"}))
    job2 = _FakeDocJob(_make_zip({"a.md": "Medications: Cisplatin"}))
    _install_fake_sdk(monkeypatch, [job1, job2])

    content = _make_pdf(15)
    ocr_result = ocr_service.extract_document(content, "application/pdf")
    assert ocr_result["engine"] == "sarvam_doc_ai"

    def _blow_up(*, api_subscription_key):
        raise AssertionError("extract_document_pages must not create any new Sarvam jobs")
    monkeypatch.setattr(ocr_service, "SarvamAI", _blow_up)

    pages = ocr_service.extract_document_pages(content, "application/pdf", ocr_result)

    assert len(pages) == 2
    assert pages[0]["page"] == 1
    assert "Lung cancer" in pages[0]["text"]
    assert pages[1]["page"] == 11
    assert "Cisplatin" in pages[1]["text"]


def test_extract_document_pages_carries_embedded_chunk_image_through(monkeypatch):
    """A chunk's representative embedded image (an actual scan photo, not just a stamp/logo)
    must survive from extract_document() into extract_document_pages() -- previously the
    whole-document path (_run_one_sarvam_doc_job) discarded it entirely, so a reused chunk could
    never show a scan thumbnail even after the redundant per-page re-OCR was removed."""
    fake_image_b64 = base64.b64encode(b"fake-scan-bytes").decode()
    zip_bytes = _make_zip({"a.md": f"*The image shows a chest X-ray.*\n\n![Image](data:image/jpeg;base64,{fake_image_b64})"})
    _install_fake_sdk(monkeypatch, [_FakeDocJob(zip_bytes)])

    content = _make_pdf(1)
    ocr_result = ocr_service.extract_document(content, "application/pdf")

    pages = ocr_service.extract_document_pages(content, "application/pdf", ocr_result)

    assert len(pages) == 1
    assert pages[0]["image_bytes"] == b"fake-scan-bytes"
    assert pages[0]["image_mime_type"] == "image/jpeg"
    assert pages[0]["is_image_heavy"] is True  # negligible remaining text once the image data is stripped, image present


def test_extract_document_falls_back_to_local_ocr_when_sarvam_job_fails(monkeypatch):
    from PIL import Image, ImageDraw, ImageFont

    _install_fake_sdk(monkeypatch, [_FakeDocJob(b"", final_state="Failed")])

    image = Image.new("RGB", (1800, 420), "white")
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 58)
    except OSError:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(image)
    draw.text((60, 70), "Diagnosis: Breast carcinoma", font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    result = ocr_service.extract_document(buffer.getvalue(), "image/png")

    # Fell back to local RapidOCR rather than raising -- same "never hard-fail" contract this
    # module already had before Sarvam existed.
    assert result["engine"] == "rapidocr"
    assert "Breast carcinoma" in result["text"]


def test_extract_document_tiff_never_attempts_sarvam(monkeypatch):
    """TIFF isn't in Sarvam Document AI's accepted format list (PDF/PNG/JPG/ZIP) -- must go
    straight to local OCR without ever constructing a Sarvam client."""
    from PIL import Image, ImageDraw, ImageFont

    def _blow_up(*, api_subscription_key):
        raise AssertionError("Sarvam must never be called for a TIFF upload")

    monkeypatch.setattr(ocr_service, "SarvamAI", _blow_up)

    image = Image.new("RGB", (1800, 420), "white")
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 58)
    except OSError:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(image)
    draw.text((60, 70), "Diagnosis: Breast carcinoma", font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="TIFF")

    result = ocr_service.extract_document(buffer.getvalue(), "image/tiff")

    assert result["engine"] == "rapidocr"
    assert "Breast carcinoma" in result["text"]


def test_extract_document_uses_local_ocr_when_provider_is_local(monkeypatch):
    monkeypatch.setattr(ocr_service.settings, "OCR_PROVIDER", "local")

    def _blow_up(*, api_subscription_key):
        raise AssertionError("Sarvam must never be called when OCR_PROVIDER=local")

    monkeypatch.setattr(ocr_service, "SarvamAI", _blow_up)

    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1800, 420), "white")
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 58)
    except OSError:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(image)
    draw.text((60, 70), "Diagnosis: Breast carcinoma", font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    result = ocr_service.extract_document(buffer.getvalue(), "image/png")
    assert result["engine"] == "rapidocr"
