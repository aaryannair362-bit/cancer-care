"""
Real-browser regression test for the Lab / Phlebotomy screen (frontend/laboratory.html):
collect specimen -> enter result, and specimen rejection.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def lab_orders(make_user, db_session):
    from app.models_cca import CCAPatient, CCAOrder

    lab_tech = make_user(email="lab@e2e-cca.com", role="CCALabPhlebotomy")
    patient = CCAPatient(
        mrn="E2E-LAB-0001", name="E2E Lab Test Patient", age=44, sex="Male",
        organization_id=lab_tech.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.flush()
    order1 = CCAOrder(patient_id=patient.id, order_type="LAB", item_name="CBC",
                       clinical_indication="Baseline pre-treatment labs.", status="RAISED")
    order2 = CCAOrder(patient_id=patient.id, order_type="LAB", item_name="LFT",
                       clinical_indication="Baseline liver function.", status="RAISED")
    db_session.add_all([order1, order2])
    db_session.commit()
    db_session.refresh(order1)
    db_session.refresh(order2)
    return lab_tech, patient, order1, order2


def test_lab_collect_and_enter_result(js_page, live_server_url, lab_orders):
    lab_tech, patient, order1, order2 = lab_orders
    login_as(js_page, live_server_url, lab_tech, landing_path="/laboratory.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate("showTab('lab')")
    js_page.wait_for_timeout(300)

    js_page.click(f'button[onclick="submitLabCollect({order1.id})"]')
    js_page.wait_for_timeout(600)

    js_page.click(f'button[onclick="openLabResultInline({order1.id})"]')
    js_page.wait_for_timeout(200)
    js_page.fill(f"#lab-findings-{order1.id}", "Hb 11.5, WBC 6.8, Platelets 260 - within normal limits.")
    js_page.click(f'button[onclick="submitLabResult({order1.id})"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import CCAOrder, CCAResult

    db = SessionLocal()
    try:
        saved_order = db.query(CCAOrder).filter(CCAOrder.id == order1.id).first()
        assert saved_order.collected_by is not None
        assert saved_order.status == "RESULTED"
        result = db.query(CCAResult).filter(CCAResult.order_id == order1.id).first()
        assert result is not None
        assert "Hb 11.5" in result.findings_text
    finally:
        db.close()


def test_lab_reject_specimen(js_page, live_server_url, lab_orders):
    lab_tech, patient, order1, order2 = lab_orders
    login_as(js_page, live_server_url, lab_tech, landing_path="/laboratory.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate("showTab('lab')")
    js_page.wait_for_timeout(300)

    js_page.click(f'button[onclick="openLabRejectInline({order2.id})"]')
    js_page.wait_for_timeout(200)
    js_page.fill(f"#lab-reject-reason-{order2.id}", "Hemolysed sample, recollection needed.")
    js_page.click(f'button[onclick="submitLabReject({order2.id})"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import CCAOrder

    db = SessionLocal()
    try:
        saved_order = db.query(CCAOrder).filter(CCAOrder.id == order2.id).first()
        assert saved_order.rejection_reason == "Hemolysed sample, recollection needed."
        assert saved_order.workflow_state == "RecollectionRequired"
    finally:
        db.close()
