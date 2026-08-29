"""
Real-browser regression test for the Patient Liaison screen (frontend/patient_liaison.html):
open a coordination case from the UI (not just the API), then update contact status, add a
barrier, and set a next action.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def liaison_patient(make_user, db_session):
    from app.models_cca import CCAPatient

    liaison = make_user(email="liaison@e2e-cca.com", role="CCAPatientLiaison")
    patient = CCAPatient(
        mrn="E2E-LIAISON-0001", name="E2E Patient Liaison Test Patient", age=47, sex="Female",
        organization_id=liaison.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return liaison, patient


def test_patient_liaison_creates_case_and_manages_it(js_page, live_server_url, liaison_patient):
    liaison, patient = liaison_patient
    login_as(js_page, live_server_url, liaison, landing_path="/patient_liaison.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(300)
    js_page.click('button[onclick="submitCreateCase()"]')
    js_page.wait_for_timeout(600)

    from app.main import SessionLocal
    from app.models_cca import CCACoordinationCase

    db = SessionLocal()
    try:
        case = db.query(CCACoordinationCase).filter(CCACoordinationCase.patient_id == patient.id).first()
        assert case is not None, "Open Coordination Case button did not create a case"
        case_id = case.id
    finally:
        db.close()

    js_page.evaluate(f"openCoordinationCase({case_id})")
    js_page.wait_for_timeout(300)

    js_page.select_option(f"#coord-contact-{case_id}", "Reached")
    js_page.click(f'button[onclick="submitContactStatus({case_id})"]')
    js_page.wait_for_timeout(600)

    js_page.evaluate(f"openCoordinationCase({case_id})")
    js_page.wait_for_timeout(300)
    js_page.fill(f"#coord-barrier-{case_id}", "Transportation")
    js_page.click(f'button[onclick="submitAddBarrier({case_id})"]')
    js_page.wait_for_timeout(600)

    js_page.fill(f"#coord-next-{case_id}", "Arrange hospital transport service")
    js_page.click(f'button[onclick="submitNextAction({case_id})"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    db = SessionLocal()
    try:
        saved = db.query(CCACoordinationCase).filter(CCACoordinationCase.id == case_id).first()
        assert saved.communication_status == "Reached"
        assert any(b.get("type") == "Transportation" for b in (saved.barriers or []))
        assert saved.next_action == "Arrange hospital transport service"
    finally:
        db.close()
