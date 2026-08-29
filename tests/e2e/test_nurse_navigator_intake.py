"""
Real-browser regression test for the Nurse Navigator intake wizard
(frontend/nurse_navigator.html). Before this fix, the wizard made exactly one real API call
in its entirety (GET /cca/patients) -- completeHandoff() only swapped a UI panel and showed a
fake "success" screen; nothing a nurse entered (vitals, ECOG, BSA/BMI, allergies review status,
handoff note) was ever persisted, despite the backend's POST /encounters/{id}/intake already
working correctly. Also pins down the per-patient stale-form-values fix: opening a second
patient's intake must not carry over the first patient's typed vitals.
"""
import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def nurse_nav_patients(make_user, db_session):
    from app.models_cca import CCAPatient

    nurse = make_user(email="nursenav@e2e-cca.com", role="CCANurseNavigator")
    patient_a = CCAPatient(
        mrn="E2E-NURSENAV-A", name="E2E Nurse Nav Patient A", age=63, sex="Male",
        organization_id=nurse.organization_id, journey_state="Registered",
    )
    patient_b = CCAPatient(
        mrn="E2E-NURSENAV-B", name="E2E Nurse Nav Patient B", age=45, sex="Female",
        organization_id=nurse.organization_id, journey_state="Registered",
    )
    db_session.add_all([patient_a, patient_b])
    db_session.commit()
    db_session.refresh(patient_a)
    db_session.refresh(patient_b)
    return nurse, patient_a, patient_b


def test_intake_wizard_persists_vitals_and_handoff(js_page, live_server_url, nurse_nav_patients):
    nurse, patient_a, patient_b = nurse_nav_patients
    login_as(js_page, live_server_url, nurse, landing_path="/nurse_navigator.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"""
        openNurseIntakeWizard({{id: {patient_a.id}, name: 'E2E Nurse Nav Patient A',
                                 mrn: 'E2E-NURSENAV-A', age: 63, sex: 'Male',
                                 journey_state: 'New \\u00b7 Medical Oncology'}})
    """)
    js_page.wait_for_timeout(300)

    # Step 1: Visit & Symptoms
    js_page.fill("#nurse-concern", "New lump noted on self-exam")
    js_page.fill("#nurse-pain", "3")

    # Step 2d: Allergies/medication checklist (part of the "History & Medication" step group)
    js_page.evaluate("goToNurseStep(2)")
    js_page.wait_for_timeout(150)
    js_page.check("#chk-medication-reconciled")
    js_page.check("#chk-allergy-reviewed")

    # Step 3: Vitals & Functional Assessment
    js_page.evaluate("goToNurseStep(3)")
    js_page.wait_for_timeout(150)
    js_page.fill("#vitals-temp", "38.1")
    js_page.fill("#vitals-pulse", "96")
    js_page.fill("#vitals-rr", "18")
    js_page.fill("#vitals-spo2", "97")
    js_page.fill("#vitals-bp-sys", "128")
    js_page.fill("#vitals-bp-dia", "84")
    js_page.fill("#vitals-weight", "70.2")
    js_page.fill("#vitals-height", "165")
    js_page.select_option("#vitals-ecog", "2")
    js_page.select_option("#vitals-fallrisk", "Medium")

    # Step 5: Nurse Assessment
    js_page.evaluate("goToNurseStep(5)")
    js_page.wait_for_timeout(150)
    js_page.fill("#nurse-observations", "Patient anxious but cooperative, vitals reviewed.")

    # Step 6: Review & Handoff
    js_page.evaluate("goToNurseStep(6)")
    js_page.wait_for_timeout(150)

    assert js_page.eval_on_selector("#complete-handoff-btn", "el => el.disabled") is False, (
        "Complete & Send should be enabled once medication + allergy checklist items are checked"
    )

    js_page.click('button[onclick="completeHandoff()"]')
    js_page.wait_for_function(
        "document.getElementById('view-nurse-complete').classList.contains('active')", timeout=10000
    )

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    from app.main import SessionLocal
    from app.models_cca import CCAIntakeAssessment, CCAEncounter

    db = SessionLocal()
    try:
        intake = db.query(CCAIntakeAssessment).filter(CCAIntakeAssessment.patient_id == patient_a.id).first()
        assert intake is not None, "no CCAIntakeAssessment row was persisted -- completeHandoff() is still a no-op"
        assert intake.temperature_c == 38.1
        assert intake.heart_rate == 96
        assert intake.respiratory_rate == 18
        assert intake.oxygen_sat == 97
        assert intake.bp_systolic == 128
        assert intake.bp_diastolic == 84
        assert intake.ecog == 2
        assert intake.fall_risk == "Medium"
        assert intake.pain_score == 3
        assert abs(intake.weight_kg - 70.2) < 0.01
        assert intake.bsa is not None and intake.bsa > 0
        assert "New lump noted on self-exam" in intake.handoff_note
        assert intake.vitals_json is not None
        assert intake.vitals_json["chief_concern"] == "New lump noted on self-exam"
        assert intake.vitals_json["medication_reconciled"] is True

        encounter = db.query(CCAEncounter).filter(CCAEncounter.id == intake.encounter_id).first()
        assert encounter is not None
        assert encounter.patient_id == patient_a.id
    finally:
        db.close()


def test_opening_a_second_patient_does_not_carry_over_stale_values(js_page, live_server_url, nurse_nav_patients):
    nurse, patient_a, patient_b = nurse_nav_patients
    login_as(js_page, live_server_url, nurse, landing_path="/nurse_navigator.html")
    js_page.wait_for_timeout(400)

    js_page.evaluate(f"""
        openNurseIntakeWizard({{id: {patient_a.id}, name: 'A', mrn: 'A', age: 63, sex: 'Male', journey_state: 'New'}})
    """)
    js_page.wait_for_timeout(200)
    js_page.evaluate("goToNurseStep(3)")
    js_page.wait_for_timeout(150)
    js_page.fill("#vitals-temp", "40.5")
    js_page.fill("#vitals-pulse", "150")
    js_page.select_option("#vitals-ecog", "4")

    js_page.evaluate(f"""
        openNurseIntakeWizard({{id: {patient_b.id}, name: 'B', mrn: 'B', age: 45, sex: 'Female', journey_state: 'New'}})
    """)
    js_page.wait_for_timeout(200)
    js_page.evaluate("goToNurseStep(3)")
    js_page.wait_for_timeout(150)

    temp = js_page.eval_on_selector("#vitals-temp", "el => el.value")
    pulse = js_page.eval_on_selector("#vitals-pulse", "el => el.value")
    ecog = js_page.eval_on_selector("#vitals-ecog", "el => el.value")

    assert temp != "40.5", f"patient B's form shows patient A's temperature: {temp}"
    assert pulse != "150", f"patient B's form shows patient A's pulse: {pulse}"
    assert ecog != "4", f"patient B's form shows patient A's ECOG: {ecog}"
    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"
