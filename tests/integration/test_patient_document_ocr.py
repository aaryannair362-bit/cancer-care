from datetime import datetime
import io

import pytest


def test_real_image_ocr_extracts_clinical_text():
    from PIL import Image, ImageDraw, ImageFont
    from app.ocr_service import extract_document
    image = Image.new("RGB", (1800, 420), "white")
    font_path = r"C:\Windows\Fonts\arial.ttf"
    try:
        font = ImageFont.truetype(font_path, 58)
    except OSError:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(image)
    draw.text((60, 70), "Diagnosis: Breast carcinoma", font=font, fill="black")
    draw.text((60, 180), "Medications: Tamoxifen", font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = extract_document(buffer.getvalue(), "image/png")
    assert "Breast carcinoma" in result["text"]
    assert result["signals"]["diagnoses"] == ["Breast carcinoma"]
    assert result["signals"]["medications"] == ["Tamoxifen"]


def test_scanned_pdf_uses_ocr_fallback_without_crashing():
    """Regression: the PDF path previously referenced an undefined `fitz` name."""
    from PIL import Image, ImageDraw, ImageFont
    from app.ocr_service import extract_document

    image = Image.new("RGB", (1800, 520), "white")
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 58)
    except OSError:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(image)
    draw.text((60, 80), "Diagnosis: Breast carcinoma", font=font, fill="black")
    draw.text((60, 200), "Allergies: Penicillin", font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=220)

    result = extract_document(buffer.getvalue(), "application/pdf")

    assert "Breast carcinoma" in result["text"]
    assert result["pages"][0]["method"] == "ocr"
    assert result["engine"] == "pypdf+doctr"


@pytest.fixture
def users(make_user):
    station = make_user(email="records@ocr.test", role="NursingStation")
    doctor = make_user(email="doctor@ocr.test", role="Doctor", organization_id=station.organization_id)
    nurse = make_user(email="nurse@ocr.test", role="Nurse", organization_id=station.organization_id)
    outsider = make_user(email="outside@ocr.test", role="Doctor")
    return station, doctor, nurse, outsider


def _patient(client, user, auth_headers):
    response = client.post("/api/patients/register", json={"name": "Transfer Patient", "age": 51}, headers=auth_headers(user))
    assert response.status_code == 201
    return response.json()


def _fake_result(*_args, **_kwargs):
    return {
        "text": "Diagnosis: Breast carcinoma\nMedications: Tamoxifen\nFindings: No distant metastasis",
        "pages": [{"page": 1, "text": "Diagnosis: Breast carcinoma", "method": "embedded_text"}],
        "page_count": 1, "engine": "pypdf",
        "signals": {"diagnoses": ["Breast carcinoma"], "medications": ["Tamoxifen"], "allergies": [], "investigations": ["No distant metastasis"], "procedures": [], "dates_mentioned": [], "text_preview": "Diagnosis: Breast carcinoma"},
        "processed_at": datetime.utcnow(),
    }


def test_registration_staff_uploads_and_doctor_reads_original_and_summary(client, users, auth_headers, monkeypatch):
    from app.routers import patient_documents
    station, doctor, _, _ = users
    patient = _patient(client, station, auth_headers)
    monkeypatch.setattr(patient_documents, "extract_document", _fake_result)
    source = b"%PDF-1.4 external clinical report"
    response = client.post(
        f"/api/patients/{patient['id']}/documents",
        files={"file": ("outside-report.pdf", source, "application/pdf")},
        data={"document_type": "Radiology report", "source_hospital": "Other Hospital"},
        headers=auth_headers(station),
    )
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["ocr_status"] == "Completed"

    listing = client.get(f"/api/patients/{patient['id']}/documents", headers=auth_headers(doctor))
    assert listing.status_code == 200
    original = client.get(f"/api/patients/{patient['id']}/documents/{doc['id']}/file", headers=auth_headers(doctor))
    assert original.content == source
    assert original.headers["cache-control"] == "private, no-store"

    summary = client.get(f"/api/patients/{patient['id']}/case-summary", headers=auth_headers(doctor)).json()
    assert summary["patient"]["name"] == "Transfer Patient"
    assert summary["imported_record_findings"]["diagnoses"] == ["Breast carcinoma"]
    assert summary["documents"][0]["file_url"].endswith(f"/{doc['id']}/file")


def test_duplicate_upload_is_rejected(client, users, auth_headers, monkeypatch):
    from app.routers import patient_documents
    station, _, _, _ = users
    patient = _patient(client, station, auth_headers)
    monkeypatch.setattr(patient_documents, "extract_document", _fake_result)
    kwargs = {"files": {"file": ("same.pdf", b"%PDF duplicate", "application/pdf")}, "headers": auth_headers(station)}
    assert client.post(f"/api/patients/{patient['id']}/documents", **kwargs).status_code == 201
    assert client.post(f"/api/patients/{patient['id']}/documents", **kwargs).status_code == 409


def test_role_and_tenant_boundaries(client, users, auth_headers, monkeypatch):
    from app.routers import patient_documents
    station, doctor, nurse, outsider = users
    patient = _patient(client, station, auth_headers)
    monkeypatch.setattr(patient_documents, "extract_document", _fake_result)
    path = f"/api/patients/{patient['id']}/documents"
    assert client.post(path, files={"file": ("x.pdf", b"%PDF x", "application/pdf")}, headers=auth_headers(doctor)).status_code == 403
    assert client.get(path, headers=auth_headers(nurse)).status_code == 403
    assert client.get(path, headers=auth_headers(outsider)).status_code == 404


@pytest.mark.parametrize("content_type", ["text/plain", "application/zip", "text/html"])
def test_unsafe_file_types_are_rejected(client, users, auth_headers, content_type):
    station, _, _, _ = users
    patient = _patient(client, station, auth_headers)
    response = client.post(f"/api/patients/{patient['id']}/documents", files={"file": ("bad.bin", b"bad", content_type)}, headers=auth_headers(station))
    assert response.status_code == 415


def test_ocr_failure_keeps_original_for_manual_review(client, users, auth_headers, monkeypatch):
    from app.routers import patient_documents
    station, doctor, _, _ = users
    patient = _patient(client, station, auth_headers)
    monkeypatch.setattr(patient_documents, "extract_document", lambda *_: (_ for _ in ()).throw(RuntimeError("OCR engine unavailable")))
    response = client.post(f"/api/patients/{patient['id']}/documents", files={"file": ("scan.png", b"PNG bytes", "image/png")}, headers=auth_headers(station))
    assert response.status_code == 201
    assert response.json()["ocr_status"] == "NeedsReview"
    original = client.get(response.json()["file_url"], headers=auth_headers(doctor))
    assert original.content == b"PNG bytes"
