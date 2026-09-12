"""
Product 1 vs Product 2 Functional Gap Report, Batch 6: Pathology.

Product 2 already had a working CCAOrder/CCAResult-based pathology pipeline
(backend/app/routers/cca_diagnostics.py) before this batch -- the real gap found by
research was narrower than a full rebuild: `structured_report` accepted a completely
unvalidated arbitrary JSON blob, finalize had no completeness gate at all, and there was no
amendment/immutability workflow (a second draft call after finalization silently created an
orphaned second Draft row with no link back to the finalized one).

Never tests a computed clinical judgment -- stage_group is pathologist-typed free text in
every real Product 1 sample, never derived from path_t/path_n/path_m; the only "logic"
ported is a plain arithmetic sanity check (nodes_positive cannot exceed nodes_examined), not
a clinical decision.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, CCAOrder


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@pathology-report-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def pathologist(make_user, oncologist):
    return make_user(email="pathologist@pathology-report-test.com", role="CCAPathologist", organization_id=oncologist.organization_id)


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def path_headers(auth_headers, pathologist):
    return auth_headers(pathologist)


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id).first().id


def _make_pathology_order(db_session, patient_id):
    order = CCAOrder(
        patient_id=patient_id, order_type="PATHOLOGY", item_name="Core Needle Biopsy",
        clinical_indication="Suspicious mass on imaging.", requested_by="onc@pathology-report-test.com",
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


_FULL_STRUCTURED_REPORT = {
    "site": "Left breast", "specimen": "Core needle biopsy", "accession": "S26-00417",
    "histology": "Invasive ductal carcinoma", "grade": "2", "tumour_size_mm": "18",
    "margin_status": "Clear", "closest_margin_mm": "5", "nodes_examined": "3", "nodes_positive": "1",
    "lymphovascular_invasion": True, "perineural_invasion": False,
    "path_t": "pT1c", "path_n": "pN1mi", "path_m": "pMX", "stage_group": "IIA",
}


def test_finalize_requires_site_specimen_and_histology(client, path_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_pathology_order(db_session, patient_id)

    incomplete = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "Pending full workup.", "structured_report": {"grade": "2"},
    })
    assert incomplete.status_code == 200, incomplete.text
    result_id = incomplete.json()["result"]["id"]

    blocked = client.post(f"/api/cca/pathology/results/{result_id}/finalize", headers=path_headers)
    assert blocked.status_code == 409
    assert "site" in blocked.text.lower()

    complete = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "Invasive ductal carcinoma, grade 2.", "structured_report": _FULL_STRUCTURED_REPORT,
    })
    assert complete.status_code == 200, complete.text

    finalized = client.post(f"/api/cca/pathology/results/{result_id}/finalize", headers=path_headers)
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["result"]["report_status"] == "Finalized"


def test_node_coherence_check(client, path_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_pathology_order(db_session, patient_id)

    bad = dict(_FULL_STRUCTURED_REPORT, nodes_examined="2", nodes_positive="5")
    rejected = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "x", "structured_report": bad,
    })
    assert rejected.status_code == 422

    ok = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "x", "structured_report": _FULL_STRUCTURED_REPORT,
    })
    assert ok.status_code == 200, ok.text


def test_finalized_report_is_immutable_without_amendment_reason(client, path_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_pathology_order(db_session, patient_id)

    draft = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "Invasive ductal carcinoma.", "structured_report": _FULL_STRUCTURED_REPORT,
    })
    result_id = draft.json()["result"]["id"]
    client.post(f"/api/cca/pathology/results/{result_id}/finalize", headers=path_headers)

    blocked_edit = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "Corrected diagnosis.", "structured_report": _FULL_STRUCTURED_REPORT,
    })
    assert blocked_edit.status_code == 409
    assert "immutable" in blocked_edit.text.lower()

    already_finalized = client.post(f"/api/cca/pathology/results/{result_id}/finalize", headers=path_headers)
    assert already_finalized.status_code == 409


def test_amendment_creates_a_linked_record_and_supersedes_the_original(client, path_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_pathology_order(db_session, patient_id)

    draft = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "findings_text": "Invasive ductal carcinoma, grade 2.", "structured_report": _FULL_STRUCTURED_REPORT,
    })
    original_id = draft.json()["result"]["id"]
    client.post(f"/api/cca/pathology/results/{original_id}/finalize", headers=path_headers)

    amended_report = dict(_FULL_STRUCTURED_REPORT, grade="3")
    amendment = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=path_headers, json={
        "amendment_reason": "Grade revised after additional immunostains.",
        "findings_text": "Invasive ductal carcinoma, grade 3 (amended).",
        "structured_report": amended_report,
    })
    assert amendment.status_code == 200, amendment.text
    amendment_body = amendment.json()["result"]
    assert amendment_body["id"] != original_id
    assert amendment_body["supersedes_id"] == original_id
    assert amendment_body["report_status"] == "Draft"
    assert amendment_body["amendment_reason"] == "Grade revised after additional immunostains."

    order_view = client.get(f"/api/cca/pathology/orders/{order.id}", headers=path_headers).json()
    by_id = {r["id"]: r for r in order_view["results"]}
    assert by_id[original_id]["report_status"] == "Superseded"
    assert by_id[original_id]["superseded_by_id"] == amendment_body["id"]

    finalized_amendment = client.post(f"/api/cca/pathology/results/{amendment_body['id']}/finalize", headers=path_headers)
    assert finalized_amendment.status_code == 200, finalized_amendment.text
    assert finalized_amendment.json()["result"]["report_status"] == "Finalized"


def test_non_pathologist_cannot_draft_or_finalize(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    order = _make_pathology_order(db_session, patient_id)

    rejected = client.post(f"/api/cca/pathology/orders/{order.id}/report", headers=onc_headers, json={
        "findings_text": "x", "structured_report": _FULL_STRUCTURED_REPORT,
    })
    assert rejected.status_code == 403
