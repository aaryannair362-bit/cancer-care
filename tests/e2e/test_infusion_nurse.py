"""
Real-browser regression test for the Infusion Nurse "Treatment Day / Infusion" screen
(frontend/infusion_nurse.html): toxicity recording (allowed for this role), the read-only
Treatment Order/Clearance view, and the treatment-day nursing workspace added to close the
Day Care / Infusion Nurse Gap Analysis (30 Aug 2026) -- queue, pre-treatment safety check,
vascular access, medication administration lifecycle, monitoring, hold/reaction/extravasation,
and completion. These are lightweight DOM-wiring smoke tests; the full business-logic surface
(role gates, state-machine transitions, completion gating) is covered by
tests/integration/test_infusion_nurse_workspace.py against the real API -- no need to
duplicate that here.

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


@pytest.fixture
def infusion_patient_with_signed_order(infusion_patient, db_session):
    """Builds a signed Treatment Order directly via the ORM, the same way infusion_patient
    already builds its CCAPatient directly -- not by driving the oncologist's own UI/role,
    which is out of scope for this module's tests."""
    from datetime import date

    from app.models_cca import TreatmentPlan, TreatmentSession, TreatmentOrder

    nurse, patient = infusion_patient
    plan = TreatmentPlan(
        patient_id=patient.id, modality="Systemic Chemotherapy", protocol_name="AC-T",
        status="ACTIVE", signer_email="onc@e2e-cca.com", signer_role="CCAMedicalOncologist",
    )
    db_session.add(plan)
    db_session.flush()
    session = TreatmentSession(
        treatment_plan_id=plan.id, patient_id=patient.id, session_no=1, cycle_no=1, day_no=1,
        planned_on=date.today(), status="PLANNED",
    )
    db_session.add(session)
    db_session.flush()
    order = TreatmentOrder(
        treatment_plan_id=plan.id, treatment_session_id=session.id, patient_id=patient.id,
        instructions={"text": "Doxorubicin 60mg/m2 IV, day 1"}, status="SIGNED",
        signer_email="onc@e2e-cca.com", signer_role="CCAMedicalOncologist",
    )
    db_session.add(order)
    db_session.commit()
    return nurse, patient, order.id


def test_infusion_nurse_records_toxicity(js_page, live_server_url, infusion_patient):
    nurse, patient = infusion_patient
    login_as(js_page, live_server_url, nurse, landing_path="/infusion_nurse.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(400)
    js_page.evaluate("switchSubtab('pretreatment')")
    js_page.wait_for_timeout(200)

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


def test_infusion_nurse_has_no_clearance_decision_action(js_page, live_server_url, infusion_patient_with_signed_order):
    """This role can no longer submit a clearance decision at all -- the form (and the
    backend-rejected submitClearance() it used to post to) is gone from this page entirely,
    replaced by a read-only order-status view. Root-cause fix for the prior "clean 403 instead
    of a crash" workaround: the action shouldn't have been offered to this role in the first
    place."""
    nurse, patient, order_id = infusion_patient_with_signed_order
    login_as(js_page, live_server_url, nurse, landing_path="/infusion_nurse.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(400)
    js_page.evaluate("switchSubtab('orders')")
    js_page.wait_for_timeout(400)

    html = js_page.inner_html("#treatment-content")
    assert "clearance-reason" not in html
    assert "submitClearance" not in html
    assert "Treatment Order &amp; Clearance" in html or "Treatment Order & Clearance" in html
    assert "SIGNED" in html

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"


def test_infusion_queue_shows_todays_patient_and_marks_arrived(js_page, live_server_url, infusion_patient_with_signed_order):
    nurse, patient, order_id = infusion_patient_with_signed_order
    login_as(js_page, live_server_url, nurse, landing_path="/infusion_nurse.html")
    js_page.wait_for_timeout(500)

    assert patient.name in js_page.inner_html("#patient-list")

    js_page.click(f'button[onclick="markArrived({patient_session_id(js_page, patient.id)})"]')
    js_page.wait_for_timeout(500)
    assert "Arrived" in js_page.inner_html("#patient-list")
    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"


def patient_session_id(js_page, patient_id):
    """The queue row's session_id isn't otherwise exposed to the test -- read it back off the
    already-rendered onclick attribute rather than re-deriving it independently."""
    import re

    html = js_page.inner_html("#patient-list")
    match = re.search(r"markArrived\((\d+)\)", html)
    assert match, f"no 'Mark Arrived' button found for patient {patient_id} in queue HTML"
    return int(match.group(1))


def test_administration_tab_medication_start_to_complete_lifecycle(js_page, live_server_url, infusion_patient_with_signed_order):
    nurse, patient, order_id = infusion_patient_with_signed_order
    login_as(js_page, live_server_url, nurse, landing_path="/infusion_nurse.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(400)
    js_page.evaluate("switchSubtab('administration')")
    js_page.wait_for_timeout(400)

    js_page.fill("#med-name", "Doxorubicin")
    js_page.fill("#med-dose", "60mg/m2")
    js_page.fill("#med-route", "IV")
    js_page.click('button[onclick="submitAddMedication()"]')
    js_page.wait_for_timeout(500)
    assert "Doxorubicin" in js_page.inner_html("#medications-card")
    assert "Pending" in js_page.inner_html("#medications-card")

    js_page.click('button:has-text("Start")')
    js_page.wait_for_timeout(500)
    assert "InProgress" in js_page.inner_html("#medications-card")

    js_page.click('button:has-text("Complete")')
    js_page.wait_for_timeout(500)
    assert "Completed" in js_page.inner_html("#medications-card")

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"


def test_hold_panel_opens_and_records(js_page, live_server_url, infusion_patient):
    nurse, patient = infusion_patient
    login_as(js_page, live_server_url, nurse, landing_path="/infusion_nurse.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(400)

    js_page.click('button[onclick="toggleInlinePanel(\'hold\')"]')
    js_page.wait_for_timeout(200)
    js_page.fill("#hold-reason", "Patient reports dizziness, holding infusion for review.")
    js_page.click('button[onclick="submitHold()"]')
    js_page.wait_for_timeout(500)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import TreatmentHoldEvent

    db = SessionLocal()
    try:
        hold = db.query(TreatmentHoldEvent).filter(TreatmentHoldEvent.patient_id == patient.id).first()
        assert hold is not None
        assert "dizziness" in hold.reason
    finally:
        db.close()
