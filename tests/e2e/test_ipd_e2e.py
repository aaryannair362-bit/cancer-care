"""
Ward (ipd.html) real-browser coverage: admission, roster drill-down, vitals, nursing notes,
ward round, tasks, IV fluids/intake-output, and discharge summary -- the full bedside
documentation flow, driven through the actual page rather than the API directly.
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture
def head_nurse(make_user):
    return make_user(email="head@e2e-ipd.com", role="HeadNurse")


@pytest.fixture
def nurse(make_user, head_nurse):
    return make_user(email="nurse@e2e-ipd.com", role="Nurse", organization_id=head_nurse.organization_id)


@pytest.fixture
def doctor(make_user, head_nurse):
    return make_user(email="doctor@e2e-ipd.com", role="Doctor", organization_id=head_nurse.organization_id)


def _admit_patient(page, name="Ramesh Kumar", ward="ICU"):
    page.click("#admit-btn")
    page.wait_for_selector("#admit-modal.modal--open")
    page.fill("#a-name", name)
    page.fill("#a-ward", ward)
    page.fill("#a-bed", "1")
    page.fill("#a-age", "60")
    page.click("#admit-form button[type=submit]")
    page.wait_for_selector(".hms-toast--visible:has-text('admitted')")


def test_admit_and_open_patient_roster_row(js_page, live_server_url, head_nurse):
    login_as(js_page, live_server_url, head_nurse, landing_path="/ipd.html")
    js_page.wait_for_selector("#admit-btn")
    _admit_patient(js_page)

    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    assert "Ramesh Kumar" in js_page.locator("#roster-rows").inner_text()

    js_page.click("#roster-rows tr[data-id]")
    js_page.wait_for_selector("#pv-name:has-text('Ramesh Kumar')")
    assert js_page.js_errors == []


def test_bedside_documentation_by_assigned_nurse(js_page, live_server_url, head_nurse, nurse, doctor):
    # HeadNurse admits and assigns
    login_as(js_page, live_server_url, head_nurse, landing_path="/ipd.html")
    js_page.wait_for_selector("#admit-btn")
    _admit_patient(js_page, name="Sunita Devi")
    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    patient_id = js_page.evaluate(
        """async () => {
            const res = await fetch('/api/ipd/patients', {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const rows = await res.json();
            return rows.find(p => p.name === 'Sunita Devi').id;
        }"""
    )
    js_page.evaluate(
        """async (id) => {
            await fetch('/api/ipd/assign', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('access_token')}` },
                body: JSON.stringify({ patient_id: id, nurse_id: %d }),
            });
        }""" % nurse.id,
        patient_id,
    )

    # Nurse records vitals and a SOAP note
    login_as(js_page, live_server_url, nurse)
    js_page.wait_for_selector("#roster-rows")
    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    js_page.click("#roster-rows tr[data-id]")
    js_page.wait_for_selector("#view-patient", state="visible")

    js_page.click(".tab[data-tab='vitals']")
    js_page.fill("#v-sys", "120")
    js_page.fill("#v-dia", "80")
    js_page.fill("#v-hr", "78")
    js_page.click("#vitals-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Vitals recorded')")
    js_page.wait_for_function("document.querySelectorAll('#vitals-rows tr').length > 0")

    js_page.click(".tab[data-tab='notes']")
    js_page.fill("#n-s", "Patient reports feeling better")
    js_page.fill("#n-o", "Alert, oriented, stable vitals")
    js_page.click("#notes-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Note saved')")

    js_page.click(".tab[data-tab='ivio']")
    js_page.fill("#iv-type", "Normal Saline 0.9%")
    js_page.fill("#iv-volume", "1000")
    js_page.click("#iv-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('IV fluid started')")

    js_page.select_option("#io-type", "Intake")
    js_page.fill("#io-category", "Oral")
    js_page.fill("#io-volume", "200")
    js_page.click("#io-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Recorded')")

    assert js_page.js_errors == []

    # Doctor performs a ward round on the same patient
    login_as(js_page, live_server_url, doctor, landing_path="/ipd.html")
    js_page.wait_for_selector("#roster-rows")
    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    js_page.click("#roster-rows tr[data-id]")
    js_page.wait_for_selector("#view-patient", state="visible")
    js_page.click(".tab[data-tab='round']")
    js_page.fill("#r-chief", "Post-op review")
    js_page.fill(".med-row .rm-name", "Paracetamol")
    js_page.fill(".med-row .rm-dose", "500mg")
    js_page.click("#round-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Round saved')")

    assert js_page.js_errors == []


def test_task_assignment_and_completion(js_page, live_server_url, head_nurse, nurse):
    login_as(js_page, live_server_url, head_nurse, landing_path="/ipd.html")
    js_page.wait_for_selector("#admit-btn")
    _admit_patient(js_page, name="Farhan Ali")
    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    js_page.click("#roster-rows tr[data-id]")
    js_page.wait_for_selector("#view-patient", state="visible")

    js_page.click(".tab[data-tab='tasks']")
    js_page.fill("#t-desc", "Administer evening dose")
    js_page.click("#task-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Task created')")
    js_page.wait_for_selector("#tasks-list :text('Administer evening dose')")

    assert js_page.js_errors == []


def test_admit_day_care_with_allergies_shows_on_overview(js_page, live_server_url, head_nurse):
    login_as(js_page, live_server_url, head_nurse, landing_path="/ipd.html")
    js_page.wait_for_selector("#admit-btn")
    js_page.click("#admit-btn")
    js_page.wait_for_selector("#admit-modal.modal--open")
    js_page.fill("#a-name", "Kavita Rao")
    js_page.fill("#a-ward", "Day Ward")
    js_page.select_option("#a-admission-type", "DayCare")
    js_page.fill("#a-allergies", "Penicillin, Latex")
    js_page.click("#admit-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('admitted')")

    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    assert "Day Care" in js_page.locator("#roster-rows").inner_text()

    js_page.click("#roster-rows tr[data-id]")
    js_page.wait_for_selector("#pv-name:has-text('Kavita Rao')")
    js_page.wait_for_selector("#overview-info :text('DayCare')")
    assert "Penicillin" in js_page.locator("#overview-info").inner_text()
    assert js_page.js_errors == []


def test_nursing_assessments_and_mar(js_page, live_server_url, head_nurse, nurse):
    login_as(js_page, live_server_url, head_nurse, landing_path="/ipd.html")
    js_page.wait_for_selector("#admit-btn")
    _admit_patient(js_page, name="Vikram Singh")
    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    patient_id = js_page.evaluate(
        """async () => {
            const res = await fetch('/api/ipd/patients', {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const rows = await res.json();
            return rows.find(p => p.name === 'Vikram Singh').id;
        }"""
    )
    js_page.evaluate(
        """async (id) => {
            await fetch('/api/ipd/assign', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('access_token')}` },
                body: JSON.stringify({ patient_id: id, nurse_id: %d }),
            });
        }""" % nurse.id,
        patient_id,
    )

    login_as(js_page, live_server_url, nurse)
    js_page.wait_for_selector("#roster-rows")
    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    js_page.click("#roster-rows tr[data-id]")
    js_page.wait_for_selector("#view-patient", state="visible")

    js_page.click(".tab[data-tab='assess']")
    js_page.wait_for_selector("#admission-assess-form")
    js_page.fill("#aa-complaint", "Fall at home")
    js_page.click("#admission-assess-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Admission assessment saved')")

    js_page.fill("#pa-score", "6")
    js_page.fill("#pa-location", "Knee")
    js_page.click("#pain-assess-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Pain assessment recorded')")
    js_page.wait_for_selector("#pain-assess-list :text('6/10')")

    js_page.check("#fr-history")
    js_page.click("#fall-risk-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Fall risk recorded')")
    js_page.wait_for_selector("#fall-risk-list .badge")

    js_page.fill("#pu-sensory", "3")
    js_page.fill("#pu-moisture", "3")
    js_page.fill("#pu-activity", "3")
    js_page.fill("#pu-mobility", "3")
    js_page.fill("#pu-nutrition", "3")
    js_page.fill("#pu-friction", "2")
    js_page.click("#pressure-ulcer-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Pressure ulcer risk recorded')")
    js_page.wait_for_selector("#pressure-ulcer-list .badge")

    js_page.click(".tab[data-tab='mar']")
    js_page.wait_for_selector("#mar-form")
    js_page.fill("#mar-drug", "Paracetamol")
    js_page.fill("#mar-dose", "500mg")
    js_page.click("#mar-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Medication administration recorded')")
    js_page.wait_for_selector("#mar-list :text('Paracetamol')")

    assert js_page.js_errors == []


def test_generate_discharge_summary(js_page, live_server_url, head_nurse):
    login_as(js_page, live_server_url, head_nurse, landing_path="/ipd.html")
    js_page.wait_for_selector("#admit-btn")
    _admit_patient(js_page, name="Deepa Nair")
    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    js_page.click("#roster-rows tr[data-id]")
    js_page.wait_for_selector("#view-patient", state="visible")

    js_page.click(".tab[data-tab='discharge']")
    js_page.click("#generate-discharge-btn")
    js_page.wait_for_selector(".hms-toast--visible:has-text('generated')")
    js_page.wait_for_selector("#discharge-body :text('Generated')")

    assert js_page.js_errors == []
