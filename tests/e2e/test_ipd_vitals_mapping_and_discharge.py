"""
Real-browser regression tests for two frontend/ipd.html fixes (see CHANGELOG.md):

1. The nursing-consult "Save" flow used to send every entered {parameter, value, unit} vital
   as a separate POST with ALL structured columns (bp_systolic, heart_rate, etc.) hardcoded to
   null and everything crammed into a free-text `notes` field -- so recorded vitals never
   populated the columns the abnormal-vitals dashboard alert and the "BP x/y | HR z" summary
   line actually read, and the patient chart literally showed "BP null/null | HR null". Fixed
   by mapVitalsToStructured() parsing common parameter names into the real numeric columns
   before Save, in one consolidated POST. (The full mic/Process -> Save round trip this used
   to be driven through no longer exists -- IPD's voice input was removed per the 2026-08-25
   CHANGELOG decision; see tests/e2e/test_ipd_nursing_note_manual_entry.py for the current,
   manual-entry version of this same structured-columns regression coverage.)
2. There was no discharge workflow anywhere in the UI at all -- PUT /api/patients/{id} could
   set `status`, but no frontend code ever sent it. Fixed by adding a Discharge action to the
   patient detail modal.
"""
import pytest

from tests.e2e.conftest import mint_tokens, set_tokens_in_browser

pytestmark = pytest.mark.e2e


@pytest.fixture
def ipd_setup(make_user, db_session):
    from app.models import Patient

    head_nurse = make_user(email="head@e2e-vitals-map.com", role="HeadNurse")
    patient = Patient(name="E2E Vitals Mapping Patient", age=52, gender="M", ward="General",
                       organization_id=head_nurse.organization_id, created_by=head_nurse.id)
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return head_nurse, patient


def test_map_vitals_to_structured_parses_common_parameter_names(js_page, live_server_url, ipd_setup):
    head_nurse, patient = ipd_setup
    tokens = mint_tokens(head_nurse)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])
    js_page.goto(f"{live_server_url}/ipd.html")
    js_page.wait_for_timeout(300)

    result = js_page.evaluate(
        """() => mapVitalsToStructured([
            { parameter: 'BP', value: '128/82', unit: 'mmHg' },
            { parameter: 'Heart Rate', value: '76', unit: 'bpm' },
            { parameter: 'Temperature', value: '37.4', unit: 'C' },
            { parameter: 'SpO2', value: '97', unit: '%' },
            { parameter: 'Respiratory Rate', value: '18', unit: '/min' },
            { parameter: 'Pain Score', value: '3', unit: '/10' }
        ])"""
    )
    assert result["bp_systolic"] == 128
    assert result["bp_diastolic"] == 82
    assert result["heart_rate"] == 76
    assert result["temperature"] == 37.4
    assert result["oxygen_sat"] == 97
    assert result["respiratory_rate"] == 18
    assert "Pain Score" in result["notes"]
    assert "3" in result["notes"]


def test_map_vitals_to_structured_handles_alternate_names_and_units(js_page, live_server_url, ipd_setup):
    head_nurse, patient = ipd_setup
    tokens = mint_tokens(head_nurse)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])
    js_page.goto(f"{live_server_url}/ipd.html")
    js_page.wait_for_timeout(300)

    result = js_page.evaluate(
        """() => mapVitalsToStructured([
            { parameter: 'Pulse', value: '88 bpm', unit: '' },
            { parameter: 'Blood Pressure', value: '110/70', unit: '' },
            { parameter: 'Oxygen Saturation', value: '99%', unit: '' }
        ])"""
    )
    assert result["heart_rate"] == 88
    assert result["bp_systolic"] == 110
    assert result["bp_diastolic"] == 70
    assert result["oxygen_sat"] == 99


def test_discharge_button_visible_for_head_nurse_and_discharges_patient(js_page, live_server_url, ipd_setup):
    head_nurse, patient = ipd_setup
    tokens = mint_tokens(head_nurse)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])
    js_page.goto(f"{live_server_url}/ipd.html")
    js_page.wait_for_timeout(300)
    js_page.evaluate(f"showPatientDetail({patient.id})")
    js_page.wait_for_timeout(300)

    discharge_btn = js_page.locator("button:has-text('🚪 Discharge')")
    assert discharge_btn.count() == 1, "Discharge action is missing from the patient detail modal"

    js_page.on("dialog", lambda d: d.accept())
    discharge_btn.click()
    js_page.wait_for_timeout(400)

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"

    import app.main as app_main
    from app.models import Patient
    refreshed = app_main.SessionLocal().query(Patient).filter(Patient.id == patient.id).first()
    assert refreshed.status == "Discharged"


def test_discharge_button_not_shown_for_nurse_role(js_page, live_server_url, make_user, db_session, ipd_setup):
    head_nurse, patient = ipd_setup
    nurse = make_user(email="nurse@e2e-vitals-map.com", role="Nurse", organization_id=head_nurse.organization_id)
    from app.models import NurseAssignment
    db_session.add(NurseAssignment(patient_id=patient.id, nurse_id=nurse.id, assigned_by=head_nurse.id))
    db_session.commit()

    tokens = mint_tokens(nurse)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])
    js_page.goto(f"{live_server_url}/ipd.html")
    js_page.wait_for_timeout(300)
    js_page.evaluate(f"showPatientDetail({patient.id})")
    js_page.wait_for_timeout(300)

    discharge_btn = js_page.locator("button:has-text('🚪 Discharge')")
    assert discharge_btn.count() == 0, "Nurse role must not see the Discharge action (HeadNurse/NursingStation only)"
