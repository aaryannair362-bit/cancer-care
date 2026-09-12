"""
Feature completion round: the remaining Pathology (C.19) gaps found verifying functional/
dataflow parity against the reference spec -- Block/Slide registry (SCR-PAT-004),
Archive/Custody (SCR-PAT-017), Frozen Section (SCR-PAT-012), Second Opinion/External Review
(SCR-PAT-013), Pathology MDT Review Note (SCR-PAT-016), and the derived-context helper for
Pathological Treatment Response (SCR-PAT-008, which needed no new table).

Never tests a computed clinical judgment -- concordance/QC/custody status are all
pathologist-typed conclusions, and the neoadjuvant interval is plain date arithmetic.
"""
import pytest

from app.models_cca import CCAPatient, CCAOrder


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@path-feature-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def pathologist(make_user, oncologist):
    return make_user(email="pathologist@path-feature-test.com", role="CCAPathologist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def path_headers(auth_headers, pathologist):
    return auth_headers(pathologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="PATH-FEATURE-0001", name="Pathology Feature Test Patient", age=52, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _make_pathology_order(db_session, patient_id):
    order = CCAOrder(
        patient_id=patient_id, order_type="PATHOLOGY", item_name="Mastectomy Specimen",
        clinical_indication="Post-neoadjuvant resection.", requested_by="onc@path-feature-test.com",
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


# ---------------------------------------------------------------------------
# Block / Slide registry + Processing Queue
# ---------------------------------------------------------------------------

def test_block_slide_registry_and_processing_queue(client, path_headers, onc_headers, db_session, patient):
    order = _make_pathology_order(db_session, patient.id)

    forbidden = client.post(f"/api/cca/pathology/orders/{order.id}/block-slides", headers=onc_headers, json={
        "item_type": "Block", "block_or_slide_id": "B1",
    })
    assert forbidden.status_code == 403

    block = client.post(f"/api/cca/pathology/orders/{order.id}/block-slides", headers=path_headers, json={
        "item_type": "Block", "block_or_slide_id": "B1", "tissue": "Tumour block", "location": "Cassette rack 3",
    })
    assert block.status_code == 201, block.text
    block_id = block.json()["block_slide"]["id"]
    assert block.json()["block_slide"]["processing_status"] == "Pending"

    listed = client.get(f"/api/cca/pathology/orders/{order.id}/block-slides", headers=path_headers)
    assert listed.status_code == 200
    assert len(listed.json()["block_slides"]) == 1

    queue = client.get("/api/cca/pathology/processing-queue", headers=path_headers)
    assert queue.status_code == 200
    assert any(b["id"] == block_id for b in queue.json()["queue"])

    bad_status = client.post(f"/api/cca/pathology/block-slides/{block_id}/update", headers=path_headers, json={"processing_status": "Not A Status"})
    assert bad_status.status_code == 422

    updated = client.post(f"/api/cca/pathology/block-slides/{block_id}/update", headers=path_headers, json={
        "processing_status": "Archived", "qc_status": "Pass",
    })
    assert updated.status_code == 200, updated.text

    queue_after = client.get("/api/cca/pathology/processing-queue", headers=path_headers)
    assert not any(b["id"] == block_id for b in queue_after.json()["queue"])


# ---------------------------------------------------------------------------
# Archive & Custody
# ---------------------------------------------------------------------------

def test_custody_events_and_inventory(client, path_headers, db_session, patient):
    order = _make_pathology_order(db_session, patient.id)
    block_id = client.post(f"/api/cca/pathology/orders/{order.id}/block-slides", headers=path_headers, json={
        "item_type": "Slide", "block_or_slide_id": "S1", "location": "Slide cabinet 1",
    }).json()["block_slide"]["id"]

    bad = client.post(f"/api/cca/pathology/block-slides/{block_id}/custody-events", headers=path_headers, json={"custody_status": "Not A Status"})
    assert bad.status_code == 422

    archived = client.post(f"/api/cca/pathology/block-slides/{block_id}/custody-events", headers=path_headers, json={
        "custody_status": "Archived", "location": "Slide cabinet 1",
    })
    assert archived.status_code == 201, archived.text

    inventory = client.get("/api/cca/pathology/custody-inventory", headers=path_headers)
    assert inventory.status_code == 200
    row = next(r for r in inventory.json()["inventory"] if r["id"] == block_id)
    assert row["custody_status"] == "Archived"
    assert row["patient_name"] is not None

    loaned = client.post(f"/api/cca/pathology/block-slides/{block_id}/custody-events", headers=path_headers, json={
        "custody_status": "Loaned", "released_to": "External Institution X", "expected_return": "2027-01-01",
    })
    assert loaned.status_code == 201
    assert loaned.json()["custody_event"]["released_at"] is not None

    inventory_after = client.get("/api/cca/pathology/custody-inventory", headers=path_headers)
    row_after = next(r for r in inventory_after.json()["inventory"] if r["id"] == block_id)
    assert row_after["custody_status"] == "Loaned"


# ---------------------------------------------------------------------------
# Frozen Section
# ---------------------------------------------------------------------------

def test_frozen_section_lifecycle(client, path_headers, db_session, patient):
    missing_question = client.post(f"/api/cca/patients/{patient.id}/frozen-sections", headers=path_headers, json={})
    assert missing_question.status_code == 422

    fs = client.post(f"/api/cca/patients/{patient.id}/frozen-sections", headers=path_headers, json={
        "theatre": "OR 3", "question_from_surgeon": "Margin clear?", "frozen_impression": "Margin clear of tumour.",
    })
    assert fs.status_code == 201, fs.text
    fs_id = fs.json()["frozen_section"]["id"]

    ack_too_early = client.post(f"/api/cca/frozen-sections/{fs_id}/acknowledge", headers=path_headers, json={"acknowledged_by": "Dr. Surgeon"})
    assert ack_too_early.status_code == 409

    comm = client.post(f"/api/cca/frozen-sections/{fs_id}/communicate", headers=path_headers, json={
        "communicated_to": "Dr. Surgeon", "communication_method": "Phone",
    })
    assert comm.status_code == 200, comm.text

    ack = client.post(f"/api/cca/frozen-sections/{fs_id}/acknowledge", headers=path_headers, json={"acknowledged_by": "Dr. Surgeon"})
    assert ack.status_code == 200
    assert ack.json()["frozen_section"]["acknowledged_by"] == "Dr. Surgeon"

    bad_concordance = client.post(f"/api/cca/frozen-sections/{fs_id}/reconcile", headers=path_headers, json={"permanent_result_concordance": "Maybe"})
    assert bad_concordance.status_code == 422

    reconciled = client.post(f"/api/cca/frozen-sections/{fs_id}/reconcile", headers=path_headers, json={"permanent_result_concordance": "Concordant"})
    assert reconciled.status_code == 200
    assert reconciled.json()["frozen_section"]["permanent_result_concordance"] == "Concordant"

    listed = client.get(f"/api/cca/patients/{patient.id}/frozen-sections", headers=path_headers)
    assert listed.status_code == 200
    assert len(listed.json()["frozen_sections"]) == 1


# ---------------------------------------------------------------------------
# Second Opinion / External Review
# ---------------------------------------------------------------------------

def test_second_opinion_review(client, path_headers, patient):
    bad_concordance = client.post(f"/api/cca/patients/{patient.id}/second-opinions", headers=path_headers, json={
        "external_institution": "Outside Hospital", "prior_diagnosis": "IDC grade 2", "review_diagnosis": "IDC grade 2",
        "concordance": "Somewhat",
    })
    assert bad_concordance.status_code == 422

    ok = client.post(f"/api/cca/patients/{patient.id}/second-opinions", headers=path_headers, json={
        "external_institution": "Outside Hospital", "external_accession": "EXT-001",
        "material_received": ["Slides", "Blocks"], "prior_diagnosis": "IDC grade 2", "review_diagnosis": "IDC grade 3",
        "concordance": "Major Discrepancy", "clinical_impact": "Upstaging may change adjuvant therapy plan.",
    })
    assert ok.status_code == 201, ok.text

    listed = client.get(f"/api/cca/patients/{patient.id}/second-opinions", headers=path_headers)
    assert listed.status_code == 200
    assert listed.json()["second_opinions"][0]["concordance"] == "Major Discrepancy"


# ---------------------------------------------------------------------------
# Pathology MDT Review Note
# ---------------------------------------------------------------------------

def test_pathology_mdt_review_note(client, path_headers, patient):
    missing_fields = client.post(f"/api/cca/patients/{patient.id}/pathology-mdt-notes", headers=path_headers, json={
        "material_reviewed": "H&E slides.",
    })
    assert missing_fields.status_code == 422

    note = client.post(f"/api/cca/patients/{patient.id}/pathology-mdt-notes", headers=path_headers, json={
        "material_reviewed": "H&E slides, IHC panel.", "key_findings": "Invasive ductal carcinoma, ER+/HER2-.",
        "diagnostic_staging_statement": "pT2N1, grade 2 IDC.", "recommendation": "Adjuvant chemotherapy discussion.",
    })
    assert note.status_code == 201, note.text

    listed = client.get(f"/api/cca/patients/{patient.id}/pathology-mdt-notes", headers=path_headers)
    assert listed.status_code == 200
    assert len(listed.json()["pathology_mdt_notes"]) == 1


# ---------------------------------------------------------------------------
# Pathological Treatment Response -- derived neoadjuvant context
# ---------------------------------------------------------------------------

def test_neoadjuvant_context_derived(client, onc_headers, path_headers, db_session, patient):
    order = _make_pathology_order(db_session, patient.id)

    no_therapy = client.get(f"/api/cca/pathology/orders/{order.id}/neoadjuvant-context", headers=path_headers)
    assert no_therapy.status_code == 200
    assert no_therapy.json()["neoadjuvant_therapy_received"] is False

    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={
        "patient_id": patient.id, "intent": "Neoadjuvant", "protocol_name": "AC-T",
    }).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})
    order_tx = client.post("/api/cca/treatment-orders", headers=onc_headers, json={
        "patient_id": patient.id, "treatment_plan_id": plan_id,
    }).json()["treatment_order"]
    client.post(f"/api/cca/treatment-orders/{order_tx['id']}/sign", headers=onc_headers)

    with_therapy = client.get(f"/api/cca/pathology/orders/{order.id}/neoadjuvant-context", headers=path_headers)
    assert with_therapy.status_code == 200, with_therapy.text
    body = with_therapy.json()
    assert body["neoadjuvant_therapy_received"] is True
    assert "AC-T" in body["neoadjuvant_treatment_summary"]
    assert body["therapy_completion_date"] is not None
    assert body["interval_to_specimen_days"] is not None
