"""
Real-browser regression test for the Infusion Nurse "Treatment Day / Infusion" screen
(frontend/infusion_nurse.html): toxicity recording (allowed for this role), and the
Treatment Order/Clearance status view.

A prior fix made the backend's clean 403 ("Only a treating oncologist may perform this
action") show as an error toast instead of a raw 500 when this role submitted a clearance
decision -- masking, not fixing, the actual problem: this role's own page presented an action
(the "Decide Treatment Clearance" form) it could never successfully use, since
routers/cca.py's record_clearance_decision is deliberately clinician-only (auth._require_
clinician's docstring lists "treatment clearance" among clinical decisions withheld from
Day-Care). The real fix removed that form from this page entirely -- the clearance decision
now lives on medical_oncologist.html's Treatment Plan tab, where the backend actually allows
it -- and replaced it here with a read-only order-status view, matching what this role is
meant to do per the architecture doc's Infusion Nurse packet: verify the order/clearance
already given, not decide it.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def infusion_patient(make_user, db_session):
    from app.models_cca import CCAPatient

    nurse = make_user(email="infusion@e2e-cca.com", role="CCAInfusionNurse")
    patient = CCAPatient(
        mrn="E2E-INFUSION-0001", name="E2E Infusion Nurse Test Patient", age=52, sex="Female",
        organization_id=nurse.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return nurse, patient


def test_infusion_nurse_records_toxicity(js_page, live_server_url, infusion_patient):
    nurse, patient = infusion_patient
    login_as(js_page, live_server_url, nurse, landing_path="/infusion_nurse.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(400)

    js_page.fill("#tox-term", "Fatigue")
    js_page.select_option("#tox-grade", "1")
    js_page.fill("#tox-baseline", "Grade 1 fatigue, mild and intermittent.")
    js_page.click('button[onclick="submitToxicity()"]')
    js_page.wait_for_timeout(600)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import ToxicityEvent

    db = SessionLocal()
    try:
        event = db.query(ToxicityEvent).filter(ToxicityEvent.patient_id == patient.id).first()
        assert event is not None
        assert event.term == "Fatigue"
        assert event.grade == 1
    finally:
        db.close()


def test_infusion_nurse_has_no_clearance_decision_action(js_page, live_server_url, infusion_patient):
    """This role can no longer submit a clearance decision at all -- the form (and the
    backend-rejected submitClearance() it used to post to) is gone from this page entirely,
    replaced by a read-only order-status view. Root-cause fix for the prior "clean 403 instead
    of a crash" workaround: the action shouldn't have been offered to this role in the first
    place."""
    nurse, patient = infusion_patient
    login_as(js_page, live_server_url, nurse, landing_path="/infusion_nurse.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(400)

    html = js_page.inner_html("#treatment-content")
    assert "clearance-reason" not in html
    assert "submitClearance" not in html
    assert "Treatment Order" in html  # read-only status card still present

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"
