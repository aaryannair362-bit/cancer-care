"""
Real-browser regression test for the Radiology Coordinator screen
(frontend/radiology_coordinator.html): schedule an imaging order and update its preparation
status, and confirm both actually persist.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def imaging_order(make_user, db_session):
    from app.models_cca import CCAPatient, CCAOrder

    coordinator = make_user(email="radcoord@e2e-cca.com", role="CCARadiologyCoordinator")
    patient = CCAPatient(
        mrn="E2E-RADCOORD-0001", name="E2E Radiology Coordinator Patient", age=58, sex="Male",
        organization_id=coordinator.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.flush()
    order = CCAOrder(
        patient_id=patient.id, order_type="RADIOLOGY", item_name="MRI Brain",
        clinical_indication="Rule out CNS metastasis.", status="RAISED",
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return coordinator, patient, order


def test_radiology_coordinator_schedules_and_updates_preparation(js_page, live_server_url, imaging_order):
    coordinator, patient, order = imaging_order
    login_as(js_page, live_server_url, coordinator, landing_path="/radiology_coordinator.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate("showTab('imaging')")
    js_page.wait_for_timeout(300)
    js_page.evaluate(f"openImagingOrder({order.id})")
    js_page.wait_for_timeout(300)

    js_page.fill("#img-sched-at", "2026-09-15T09:30")
    js_page.fill("#img-location", "MRI Suite 1")
    js_page.click('button[onclick="submitImagingSchedule(' + str(order.id) + ')"]')
    js_page.wait_for_timeout(600)

    # submitImagingSchedule() success calls loadImagingWorklist(), which replaces the whole
    # list + detail panel -- re-open the order's detail to continue, matching the real flow.
    js_page.evaluate(f"openImagingOrder({order.id})")
    js_page.wait_for_timeout(300)

    js_page.select_option("#img-prep-status", "Completed")
    js_page.click(f'button[onclick="submitImagingPreparation({order.id})"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import CCAOrder

    db = SessionLocal()
    try:
        db.expire_all()
        saved = db.query(CCAOrder).filter(CCAOrder.id == order.id).first()
        assert saved.location == "MRI Suite 1"
        assert saved.scheduled_at is not None
        assert saved.preparation_status == "Completed"
    finally:
        db.close()
