"""
Real-browser regression test for the External MDT Specialist screen
(frontend/external_mdt_specialist.html): the case-scoped access grant is only useful if a
specialist logged in under the exact granted email actually sees the case and can submit an
opinion. Also confirms case isolation: a specialist NOT granted access sees no cases at all.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e

GRANTED_EMAIL = "dr.external.reviewer@e2e-cca.com"


@pytest.fixture
def shared_case(make_user, db_session):
    from datetime import datetime, timedelta
    from app.models_cca import CCAPatient, MDTCase, CCAExternalAccess

    coordinator = make_user(email="mdtcoord2@e2e-cca.com", role="CCAMDTCoordinator")
    external_specialist = make_user(email=GRANTED_EMAIL, role="CCAExternalMDTSpecialist",
                                     organization_id=coordinator.organization_id)
    patient = CCAPatient(
        mrn="E2E-EXTMDT-0001", name="E2E External MDT Test Patient", age=61, sex="Male",
        organization_id=coordinator.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.flush()
    case = MDTCase(patient_id=patient.id, question="Second opinion on surgical resectability?", status="SCHEDULED")
    db_session.add(case)
    db_session.flush()
    access = CCAExternalAccess(
        case_id=case.id, specialist_name="Dr. External Reviewer", specialist_email=GRANTED_EMAIL,
        access_status="Active", granted_by="mdtcoord2@e2e-cca.com", expires_at=datetime.utcnow() + timedelta(days=14),
    )
    db_session.add(access)
    db_session.commit()
    db_session.refresh(case)
    return external_specialist, patient, case


def test_external_specialist_sees_assigned_case_and_submits_opinion(js_page, live_server_url, shared_case):
    external_specialist, patient, case = shared_case
    login_as(js_page, live_server_url, external_specialist, landing_path="/external_mdt_specialist.html")
    js_page.wait_for_timeout(600)

    assert js_page.eval_on_selector("#cases-content", "el => el.textContent").find("Second opinion") != -1, (
        "granted case did not appear in this specialist's assigned-cases view"
    )

    js_page.click(f'button[onclick="loadOpinionsAndForm({case.id})"]')
    js_page.wait_for_timeout(300)
    js_page.fill(f"#opinion-rec-{case.id}", "Resectable with neoadjuvant downsizing first.")
    js_page.fill(f"#opinion-rationale-{case.id}", "Tumor size and location favor a staged approach.")
    js_page.select_option(f"#opinion-certainty-{case.id}", "High")
    js_page.click(f'button[onclick="submitOpinion({case.id})"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import CCAExternalOpinion

    db = SessionLocal()
    try:
        opinion = db.query(CCAExternalOpinion).filter(CCAExternalOpinion.case_id == case.id).first()
        assert opinion is not None
        assert "Resectable" in opinion.recommendation
        assert opinion.certainty == "High"
    finally:
        db.close()


def test_external_specialist_without_a_grant_sees_no_cases(js_page, live_server_url, shared_case, make_user):
    external_specialist, patient, case = shared_case
    unrelated_specialist = make_user(email="not.granted@e2e-cca.com", role="CCAExternalMDTSpecialist",
                                      organization_id=external_specialist.organization_id)
    login_as(js_page, live_server_url, unrelated_specialist, landing_path="/external_mdt_specialist.html")
    js_page.wait_for_timeout(600)

    content = js_page.eval_on_selector("#cases-content", "el => el.textContent")
    assert "Second opinion" not in content, "a specialist with no access grant should not see this case"
    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"
