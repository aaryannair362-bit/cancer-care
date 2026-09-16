"""
Background per-page enrichment for an already-uploaded CCADocument -- true per-page OCR text
(ocr_service.extract_document_pages), the hybrid page-type classifier plus page-scoped clinical
fact extraction (cca_engine.classify_and_extract_page), persisted as CCADocumentPage rows (and
extra, correctly page-attributed ClinicalFact rows).

Runs as a FastAPI BackgroundTask scheduled from routers/cca.py's upload_document, AFTER the
document itself is already saved and the upload response has been sent -- purely so a multi-page
document's per-page/per-chunk classification+fact-extraction LLM calls (one per page/chunk, see
cca_engine.classify_and_extract_page) don't add to Front Desk's upload wait. It costs no extra
OCR calls beyond what upload_document's own extract_document() already made (see
ocr_service.extract_document_pages's docstring). The document and its whole-document OCR text/
classification are already usable immediately; this only adds the Patient History "Scans"
section and page-attributed facts a short time later.

Opens its own DB session (SessionLocal, the same factory routers/cca.py's own get_cca_db uses) --
the request's own session is gone by the time this runs, same reason every other out-of-request
DB access in this codebase (tests, startup scripts) does the same.
"""
import logging

from . import ocr_service
from .cca_engine import classify_and_extract_page
from .database import SessionLocal
from .models_cca import CCADocument, CCADocumentPage, ClinicalFact

logger = logging.getLogger(__name__)


def process_document_pages(document_id: int, content: bytes, content_type: str, ocr_result: dict) -> None:
    """Never raises -- a background task exception is logged and swallowed here rather than
    propagating anywhere a caller could observe it; this is best-effort enrichment on top of a
    document that already exists and is already usable without it."""
    try:
        pages = ocr_service.extract_document_pages(content, content_type, ocr_result)
    except Exception:
        logger.exception("extract_document_pages failed for document %d", document_id)
        return
    if not pages:
        return

    db = SessionLocal()
    try:
        doc = db.query(CCADocument).filter(CCADocument.id == document_id).first()
        if not doc:
            return  # document was deleted/never committed -- nothing to attach pages to

        # upload_document already ran extract_clinical_facts() over the WHOLE document's text
        # (sliced, not truncated -- see that function's docstring) plus
        # extract_deterministic_lab_facts()'s full-text lab-value scan, before scheduling this
        # background task, and committed those as ClinicalFact rows. classify_and_extract_page()
        # now ALWAYS runs its own LLM fact extraction per page/chunk (not just when the page isn't
        # confidently keyword-classified -- see that function's docstring for why), so a page's
        # text substantially overlaps what the whole-document pass already saw and (re-)drafts the
        # same facts a second time, with no de-dup between the two passes. Tracking what already
        # exists for this document up front keeps the genuinely useful case (a page/chunk boundary
        # the whole-document pass sliced differently, contributing a fact phrased/rounded slightly
        # differently) while dropping true repeats.
        # Maps (fact_type, value) -> the existing ClinicalFact ROW OBJECT (not just a marker),
        # so a fact whose page_number is still None (the whole-document pass no longer claims a
        # false "page 1" -- see upload_document's own comment) can be updated in place with the
        # real page number once this per-page pass independently rediscovers it, instead of the
        # dedup below silently discarding that strictly-better information forever.
        existing_facts: dict[tuple[str, str], ClinicalFact] = {
            (f.fact_type, f.value): f
            for f in db.query(ClinicalFact).filter(ClinicalFact.document_id == document_id)
        }

        for page in pages:
            try:
                classification = classify_and_extract_page(page["text"], page["is_image_heavy"])
            except Exception:
                logger.exception(
                    "classify_and_extract_page failed for document %d page %d", document_id, page["page"]
                )
                classification = {"page_type": "UNCLASSIFIED", "confidence": 0.0, "facts": []}

            db.add(CCADocumentPage(
                document_id=document_id,
                page_number=page["page"],
                text=page["text"],
                page_type=classification["page_type"],
                classification_confidence=classification["confidence"],
                is_image_heavy=page["is_image_heavy"],
                image_content=page.get("image_bytes"),
                image_mime_type=page.get("image_mime_type"),
            ))

            for fact in classification["facts"]:
                key = (fact["fact_type"], fact["value"])
                existing = existing_facts.get(key)
                if existing is not None:
                    # Same fact already on record from the whole-document pass. That pass can
                    # only ever leave page_number unset (None) since it never knew the true
                    # page -- if that's still the case here, this per-page pass DOES know it, so
                    # fill it in rather than silently keeping an unknown page forever. Never
                    # overwrites a page_number some earlier pass already set to a real value.
                    if existing.page_number is None:
                        existing.page_number = page["page"]
                    continue
                new_fact = ClinicalFact(
                    patient_id=doc.patient_id, document_id=document_id, fact_type=fact["fact_type"],
                    value=fact["value"], verbatim_span=fact["verbatim"], page_number=page["page"],
                    confidence=fact["confidence"], status="PROPOSED",
                )
                db.add(new_fact)
                existing_facts[key] = new_fact

        db.commit()
    except Exception:
        logger.exception("process_document_pages failed for document %d", document_id)
        db.rollback()
    finally:
        db.close()
