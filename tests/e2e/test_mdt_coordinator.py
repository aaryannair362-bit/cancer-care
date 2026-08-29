"""
Real-browser regression test for the MDT Coordinator screen (frontend/mdt_coordinator.html):
schedule an MDT case, add a participant, and grant external specialist access.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def mdt_case(make_user, db_session):
    from app.models_cca import CCAPatient, MDTCase

    coordinator = make_user(email="mdtcoord@e2e-cca.com", role="CCAMDTCoordinator")
    # Grant-access now requires the email to belong to a registered External MDT Specialist
    # account in the same org -- see grant_external_access's validation in cca_coordination.py.
    external = make_user(
        email="externalmdt@aivana.com", role="CCAExternalMDTSpecialist",
        organization_id=coordinator.organization_id,
    )
    patient = CCAPatient(
        mrn="E2E-MDT-0001", name="E2E MDT Coordinator Test Patient", age=56, sex="Female",
        organization_id=coordinator.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.flush()
    case = MDTCase(patient_id=patient.id, question="Adjuvant therapy sequencing?", status="PROPOSED")
    db_session.add(case)
    db_session.commit()
    db_session.refresh(case)
    return coordinator, patient, case, external


def test_mdt_coordinator_schedules_adds_participant_and_grants_external_access(js_page, live_server_url, mdt_case):
    coordinator, patient, case, external = mdt_case
    login_as(js_page, live_server_url, coordinator, landing_path="/mdt_coordinator.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate("showTab('mdt')")
    js_page.wait_for_timeout(400)
    js_page.evaluate(f"openMdtCase({case.id}, {patient.id})")
    js_page.wait_for_timeout(400)

    js_page.fill(f"#mdtc-date-{case.id}", "2026-09-20")
    js_page.fill(f"#mdtc-time-{case.id}", "15:00")
    js_page.select_option(f"#mdtc-type-{case.id}", "Virtual")
    js_page.click(f'button[onclick="submitMdtSchedule({case.id})"]')
    js_page.wait_for_timeout(600)

    # submitMdtSchedule() success calls loadMdtReferralQueue(), which re-renders the whole
    # queue -- re-open the case to continue with participants/external access.
    js_page.evaluate(f"openMdtCase({case.id}, {patient.id})")
    js_page.wait_for_timeout(400)

    js_page.fill(f"#mdtc-pname-{case.id}", "Dr. Meera Iyer")
    js_page.fill(f"#mdtc-prole-{case.id}", "Radiation Oncologist")
    js_page.click(f'button[onclick="submitAddParticipant({case.id})"]')
    js_page.wait_for_timeout(600)

    js_page.fill(f"#mdtc-extname-{case.id}", "Dr. External Reviewer")
    js_page.select_option(f"#mdtc-extemail-{case.id}", "externalmdt@aivana.com")
    js_page.click(f'button[onclick="submitGrantExternalAccess({case.id})"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import MDTCase as MDTCaseModel, MDTParticipant, CCAExternalAccess

    db = SessionLocal()
    try:
        saved_case = db.query(MDTCaseModel).filter(MDTCaseModel.id == case.id).first()
        assert saved_case.status == "SCHEDULED"
        assert saved_case.meeting_type == "Virtual"
        participant = db.query(MDTParticipant).filter(MDTParticipant.case_id == case.id).first()
        assert participant is not None
        assert participant.specialist_name == "Dr. Meera Iyer"
        access = db.query(CCAExternalAccess).filter(CCAExternalAccess.case_id == case.id).first()
        assert access is not None
        assert access.specialist_email == "externalmdt@aivana.com"
        assert access.access_status == "Active"
    finally:
        db.close()

    # Closes the loop the picker exists for: the grant must actually reach the specialist's
    # own assigned-cases view, not just look successful on the Coordinator's side.
    import requests
    from tests._voice_helpers import mint_tokens
    token = mint_tokens(external)["access_token"]
    assigned = requests.get(f"{live_server_url}/api/cca/mdt/assigned-cases",
                             headers={"Authorization": f"Bearer {token}"}).json()
    assert any(c["id"] == case.id for c in assigned["assigned_cases"]), (
        "granted case did not reach the External MDT Specialist's assigned-cases view"
    )


def test_grant_external_access_rejects_unregistered_email(live_server_url, mdt_case):
    """The Coordinator UI only offers registered specialists via a dropdown now, but the API
    must independently reject an email with no matching CCAExternalMDTSpecialist account --
    this is the exact bug this fix closes: granting always used to return success regardless
    of whether the email belonged to anyone, so a Coordinator believed a case had been pushed
    while the specialist's page silently showed nothing."""
    import requests
    from tests._voice_helpers import mint_tokens

    coordinator, patient, case, external = mdt_case
    token = mint_tokens(coordinator)["access_token"]
    resp = requests.post(
        f"{live_server_url}/api/cca/mdt/cases/{case.id}/external-access",
        json={"specialist_name": "Dr. Nobody", "specialist_email": "not.registered@nowhere.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, f"expected a clear rejection, got {resp.status_code}: {resp.text}"

    from app.main import SessionLocal
    from app.models_cca import CCAExternalAccess

    db = SessionLocal()
    try:
        leaked = db.query(CCAExternalAccess).filter(
            CCAExternalAccess.case_id == case.id, CCAExternalAccess.specialist_email == "not.registered@nowhere.com"
        ).first()
        assert leaked is None, "a grant to an unregistered email must not create an access row"
    finally:
        db.close()
