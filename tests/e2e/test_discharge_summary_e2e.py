"""
Real-browser coverage of the new Discharge Summary tab/button in frontend/ipd.html.
"""
import json

import pytest

from tests.e2e.conftest import mint_tokens, set_tokens_in_browser

pytestmark = pytest.mark.e2e


@pytest.fixture
def ward_setup(make_user, db_session):
    from app.models import Patient, NurseAssignment, Vital, NursingNote

    head_nurse = make_user(email="head@e2e-discharge-summary.com", role="HeadNurse")
    nurse = make_user(email="nurse@e2e-discharge-summary.com", role="Nurse", organization_id=head_nurse.organization_id)
    patient = Patient(name="E2E Discharge Summary Patient", age=62, gender="F", ward="General", bed="D1",
                       diagnosis="Post-operative recovery", organization_id=head_nurse.organization_id, created_by=head_nurse.id)
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    db_session.add(NurseAssignment(patient_id=patient.id, nurse_id=nurse.id, assigned_by=head_nurse.id))
    db_session.add(Vital(patient_id=patient.id, nurse_id=nurse.id, heart_rate=76, temperature=37.0))
    db_session.add(NursingNote(patient_id=patient.id, nurse_id=nurse.id, notes="Subjective: Recovering well"))
    db_session.commit()
    return head_nurse, nurse, patient


def _login(js_page, live_server_url, user):
    tokens = mint_tokens(user)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])
    js_page.goto(f"{live_server_url}/ipd.html")
    js_page.wait_for_timeout(300)


def test_discharge_summary_tab_shows_empty_state_before_generation(js_page, live_server_url, ward_setup):
    head_nurse, nurse, patient = ward_setup
    _login(js_page, live_server_url, head_nurse)
    js_page.evaluate(f"showPatientDetail({patient.id})")
    js_page.wait_for_timeout(300)
    js_page.click(".tab-btn[data-tab='discharge-summary-tab']")
    js_page.wait_for_timeout(300)
    text = js_page.locator("#discharge-summary-tab").inner_text()
    assert "No discharge summary" in text
    assert js_page.js_errors == []


def test_headnurse_can_generate_discharge_summary_through_ui(js_page, live_server_url, ward_setup, monkeypatch):
    import app.main as app_main

    head_nurse, nurse, patient = ward_setup

    def _fake_call(prompt, system=None, temperature=0.3, max_tokens=3000):
        return json.dumps({
            "admissionSummary": "Admitted post-operatively for recovery",
            "hospitalCourse": "Uneventful recovery, vitals stable throughout",
            "dischargeDiagnosis": "Post-operative recovery, uncomplicated",
            "medicationsAtDischarge": [{"drugName": "Paracetamol", "dose": "500mg", "frequency": "TID", "duration": "3 days"}],
            "followUpInstructions": "Follow up with surgeon in 2 weeks",
            "conditionAtDischarge": "Stable and ambulatory",
        })

    monkeypatch.setattr(app_main.scribe, "_call_groq_api", _fake_call)

    _login(js_page, live_server_url, head_nurse)
    js_page.evaluate(f"showPatientDetail({patient.id})")
    js_page.wait_for_timeout(300)
    js_page.click(".tab-btn[data-tab='discharge-summary-tab']")
    js_page.wait_for_timeout(200)
    js_page.click("#generate-discharge-summary-btn")
    js_page.wait_for_timeout(500)

    text = js_page.locator("#discharge-summary-tab").inner_text()
    assert "Post-operative recovery, uncomplicated" in text
    assert "Paracetamol" in text
    assert "Follow up with surgeon" in text
    assert js_page.js_errors == []


def test_print_button_appears_after_generation_and_opens_preview(js_page, live_server_url, ward_setup, monkeypatch):
    import app.main as app_main

    head_nurse, nurse, patient = ward_setup

    def _fake_call(prompt, system=None, temperature=0.3, max_tokens=3000):
        return json.dumps({
            "admissionSummary": "Admitted post-op", "hospitalCourse": "Uneventful",
            "dischargeDiagnosis": "Recovered well", "medicationsAtDischarge": [{"drugName": "Paracetamol", "dose": "500mg", "frequency": "TID", "duration": "3 days"}],
            "followUpInstructions": "Follow up in 2 weeks", "conditionAtDischarge": "Stable",
        })

    monkeypatch.setattr(app_main.scribe, "_call_groq_api", _fake_call)

    _login(js_page, live_server_url, head_nurse)
    js_page.evaluate(f"showPatientDetail({patient.id})")
    js_page.wait_for_timeout(300)
    js_page.click(".tab-btn[data-tab='discharge-summary-tab']")
    js_page.wait_for_timeout(200)
    js_page.click("#generate-discharge-summary-btn")
    js_page.wait_for_timeout(500)

    print_btn = js_page.locator("button:has-text('Print / Save PDF')").first
    assert print_btn.count() >= 1
    print_btn.click()
    js_page.wait_for_timeout(200)

    preview_text = js_page.locator("#discharge-print-preview").inner_text()
    assert "Recovered well" in preview_text
    assert "Paracetamol" in preview_text
    assert js_page.js_errors == []


def test_nurse_does_not_see_generate_button_but_sees_existing_summary(js_page, live_server_url, ward_setup, monkeypatch):
    import app.main as app_main

    head_nurse, nurse, patient = ward_setup

    def _fake_call(prompt, system=None, temperature=0.3, max_tokens=3000):
        return json.dumps({
            "admissionSummary": "x", "hospitalCourse": "x", "dischargeDiagnosis": "Recovered",
            "medicationsAtDischarge": [], "followUpInstructions": "x", "conditionAtDischarge": "Stable",
        })

    monkeypatch.setattr(app_main.scribe, "_call_groq_api", _fake_call)

    # Generate first as HeadNurse (real flow: someone with permission generates it).
    _login(js_page, live_server_url, head_nurse)
    js_page.evaluate(f"showPatientDetail({patient.id})")
    js_page.wait_for_timeout(300)
    js_page.click(".tab-btn[data-tab='discharge-summary-tab']")
    js_page.wait_for_timeout(200)
    js_page.click("#generate-discharge-summary-btn")
    js_page.wait_for_timeout(500)

    # Now the assigned Nurse views it -- read-only.
    _login(js_page, live_server_url, nurse)
    js_page.evaluate(f"showPatientDetail({patient.id})")
    js_page.wait_for_timeout(300)
    js_page.click(".tab-btn[data-tab='discharge-summary-tab']")
    js_page.wait_for_timeout(300)

    assert js_page.locator("#generate-discharge-summary-btn").count() == 0
    assert "Recovered" in js_page.locator("#discharge-summary-tab").inner_text()
    assert js_page.js_errors == []
