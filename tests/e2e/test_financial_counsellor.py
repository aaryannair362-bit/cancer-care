"""
Real-browser regression test for the Financial Counsellor screen
(frontend/patient_financial_services.html): create a case from the UI, then update
counselling, estimate status, payer route, and financial clearance.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def financial_patient(make_user, db_session):
    from app.models_cca import CCAPatient

    counsellor = make_user(email="fincounsel@e2e-cca.com", role="CCAFinancialCounsellor")
    patient = CCAPatient(
        mrn="E2E-FIN-0001", name="E2E Financial Counsellor Test Patient", age=59, sex="Male",
        organization_id=counsellor.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return counsellor, patient


def test_financial_counsellor_creates_case_and_manages_full_lifecycle(js_page, live_server_url, financial_patient):
    counsellor, patient = financial_patient
    login_as(js_page, live_server_url, counsellor, landing_path="/patient_financial_services.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(300)
    js_page.click('button[onclick="submitCreateCase()"]')
    js_page.wait_for_timeout(600)

    from app.main import SessionLocal
    from app.models_cca import CCAFinancialCase

    db = SessionLocal()
    try:
        case = db.query(CCAFinancialCase).filter(CCAFinancialCase.patient_id == patient.id).first()
        assert case is not None, "Open Coordination Case button did not create a financial case"
        case_id = case.id
    finally:
        db.close()

    js_page.evaluate(f"openFinancialCase({case_id})")
    js_page.wait_for_timeout(300)
    js_page.fill(f"#fin-notes-{case_id}", "Discussed treatment cost estimate with patient's family.")
    js_page.select_option(f"#fin-status-{case_id}", "Completed")
    js_page.click(f'button[onclick="submitCounselling({case_id})"]')
    js_page.wait_for_timeout(600)

    js_page.evaluate(f"openFinancialCase({case_id})")
    js_page.wait_for_timeout(300)
    js_page.select_option(f"#fin-estimate-status-{case_id}", "Shared")
    js_page.click(f'button[onclick="submitEstimate({case_id})"]')
    js_page.wait_for_timeout(600)

    js_page.evaluate(f"openFinancialCase({case_id})")
    js_page.wait_for_timeout(300)
    js_page.select_option(f"#fin-payer-{case_id}", "PrivateInsurance")
    js_page.click(f'button[onclick="submitInsurance({case_id})"]')
    js_page.wait_for_timeout(600)

    js_page.evaluate(f"openFinancialCase({case_id})")
    js_page.wait_for_timeout(300)
    js_page.select_option(f"#fin-clearance-{case_id}", "Cleared")
    js_page.click(f'button[onclick="submitClearance({case_id})"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    db = SessionLocal()
    try:
        saved = db.query(CCAFinancialCase).filter(CCAFinancialCase.id == case_id).first()
        assert saved.counselling_status == "Completed"
        assert "cost estimate" in saved.counselling_notes
        assert saved.estimate_status == "Shared"
        assert saved.payer_route == "PrivateInsurance"
        assert saved.financial_clearance_status == "Cleared"
    finally:
        db.close()
