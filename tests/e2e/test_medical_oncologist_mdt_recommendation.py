"""
Real-browser regression test for the Medical Oncologist's own MDT recommendation/disposition
UI (frontend/medical_oncologist.html) -- record a tumour-board recommendation, then exercise
the treating-clinician disposition flow, including the native prompt() dialog path for
Partially Accept/Reject that nothing in this suite had driven through a real browser before
(only verified via direct API calls earlier this session).
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def mdt_cases_for_oncologist(make_user, db_session):
    from app.models_cca import CCAPatient, MDTCase

    oncologist = make_user(email="medonc2@e2e-cca.com", role="CCAMedicalOncologist")
    patient = CCAPatient(
        mrn="E2E-MDTREC-0001", name="E2E MDT Recommendation Patient", age=54, sex="Female",
        organization_id=oncologist.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.flush()
    case_accept = MDTCase(patient_id=patient.id, question="Adjuvant chemo sequencing?", status="SCHEDULED")
    case_partial = MDTCase(patient_id=patient.id, question="Radiation boost volume?", status="SCHEDULED")
    db_session.add_all([case_accept, case_partial])
    db_session.commit()
    db_session.refresh(case_accept)
    db_session.refresh(case_partial)
    return oncologist, patient, case_accept, case_partial


def test_record_recommendation_then_accept_disposition(js_page, live_server_url, mdt_cases_for_oncologist):
    oncologist, patient, case_accept, _ = mdt_cases_for_oncologist
    login_as(js_page, live_server_url, oncologist, landing_path="/medical_oncologist.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatientFromQueue({patient.id})")
    js_page.wait_for_timeout(300)
    js_page.evaluate("showTab('mdt')")
    js_page.wait_for_timeout(400)

    js_page.click(f'button[onclick="openRecommendationForm({case_accept.id})"]')
    js_page.wait_for_timeout(200)
    js_page.fill(f"#mdt-rec-text-{case_accept.id}", "Proceed with dose-dense AC-T, reassess at midpoint imaging.")
    js_page.click(f'button[onclick="submitMdtRecommendation({case_accept.id})"]')
    js_page.wait_for_timeout(600)

    js_page.click(f'button[onclick="openMdtDispositionForm({case_accept.id})"]')
    js_page.wait_for_timeout(200)
    js_page.click(f"button[onclick=\"submitMdtDisposition({case_accept.id}, 'ACCEPT')\"]")
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import MDTCase as MDTCaseModel, MDTDecision

    db = SessionLocal()
    try:
        saved = db.query(MDTCaseModel).filter(MDTCaseModel.id == case_accept.id).first()
        assert saved.status == "APPROVED", f"unexpected status after Accept: {saved.status}"
        decision = db.query(MDTDecision).filter(MDTDecision.case_id == case_accept.id).first()
        assert decision is not None
        assert "dose-dense AC-T" in decision.recommendation
    finally:
        db.close()


def test_partial_accept_disposition_via_prompt_dialog(js_page, live_server_url, mdt_cases_for_oncologist):
    oncologist, patient, _, case_partial = mdt_cases_for_oncologist
    login_as(js_page, live_server_url, oncologist, landing_path="/medical_oncologist.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatientFromQueue({patient.id})")
    js_page.wait_for_timeout(300)
    js_page.evaluate("showTab('mdt')")
    js_page.wait_for_timeout(400)

    js_page.click(f'button[onclick="openRecommendationForm({case_partial.id})"]')
    js_page.wait_for_timeout(200)
    js_page.fill(f"#mdt-rec-text-{case_partial.id}", "Boost to tumour bed, 16 Gy in 8 fractions.")
    js_page.click(f'button[onclick="submitMdtRecommendation({case_partial.id})"]')
    js_page.wait_for_timeout(600)

    js_page.click(f'button[onclick="openMdtDispositionForm({case_partial.id})"]')
    js_page.wait_for_timeout(200)

    dialog_messages = []
    def _handle_dialog(dialog):
        dialog_messages.append(dialog.message)
        dialog.accept("Reducing boost volume given proximity to cardiac silhouette.")
    js_page.on("dialog", _handle_dialog)

    js_page.click(f"button[onclick=\"promptMdtDisposition({case_partial.id}, 'PARTIAL', 'What is being modified, and why?')\"]")
    js_page.wait_for_timeout(600)

    assert dialog_messages, "promptMdtDisposition never opened the reason prompt"
    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import MDTCase as MDTCaseModel, MDTDecision

    db = SessionLocal()
    try:
        saved = db.query(MDTCaseModel).filter(MDTCaseModel.id == case_partial.id).first()
        assert saved.status == "PARTIALLY_APPROVED", f"unexpected status after Partial Accept: {saved.status}"
        decision = db.query(MDTDecision).filter(MDTDecision.case_id == case_partial.id).first()
        assert decision is not None
        assert "cardiac silhouette" in (decision.disposition_reason or "")
    finally:
        db.close()
