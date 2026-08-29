"""
Real-browser regression test for the Pathologist screen (frontend/pathologist.html):
pathology report draft -> finalize, and molecular test ordering.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def pathology_order(make_user, db_session):
    from app.models_cca import CCAPatient, CCAOrder

    pathologist = make_user(email="pathologist@e2e-cca.com", role="CCAPathologist")
    patient = CCAPatient(
        mrn="E2E-PATH-0001", name="E2E Pathologist Test Patient", age=49, sex="Female",
        organization_id=pathologist.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.flush()
    order = CCAOrder(
        patient_id=patient.id, order_type="PATHOLOGY", item_name="Core Needle Biopsy",
        clinical_indication="Histologic confirmation.", status="SCHEDULED",
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return pathologist, patient, order


def test_pathologist_drafts_and_finalizes_report(js_page, live_server_url, pathology_order):
    pathologist, patient, order = pathology_order
    login_as(js_page, live_server_url, pathologist, landing_path="/pathologist.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate("showTab('pathology')")
    js_page.wait_for_timeout(300)
    js_page.evaluate(f"openPathologyOrder({order.id})")
    js_page.wait_for_timeout(300)

    js_page.fill("#path-findings", "Invasive ductal carcinoma, Grade 2, margins clear.")
    js_page.click(f'button[onclick="submitPathologyReport({order.id})"]')
    js_page.wait_for_timeout(600)

    js_page.click('button:has-text("Finalize Report")')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import CCAResult

    db = SessionLocal()
    try:
        result = db.query(CCAResult).filter(CCAResult.order_id == order.id).order_by(CCAResult.id.desc()).first()
        assert result is not None
        assert "Invasive ductal carcinoma" in result.findings_text
        assert result.report_status == "Finalized"
    finally:
        db.close()


def test_pathologist_orders_molecular_test(js_page, live_server_url, pathology_order):
    pathologist, patient, order = pathology_order
    login_as(js_page, live_server_url, pathologist, landing_path="/pathologist.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate("showTab('molecular')")
    js_page.wait_for_timeout(300)

    js_page.fill("#mol-patient-id", str(patient.id))
    js_page.fill("#mol-marker", "HER2")
    js_page.click('button[onclick="submitOrderMolecularTest()"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import CCABiomarkerResult

    db = SessionLocal()
    try:
        test = db.query(CCABiomarkerResult).filter(CCABiomarkerResult.patient_id == patient.id).first()
        assert test is not None
        assert test.marker_name == "HER2"
    finally:
        db.close()
