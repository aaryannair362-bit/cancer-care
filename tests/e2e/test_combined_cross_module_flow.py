"""
Combined cross-module real-browser flow: every module built this session exercised together
in one coherent patient journey, through the actual UI, role-switching exactly as a real
hospital day would -- not each module's happy path tested in isolation.

Leg A (OPD/Pharmacy/Billing chain, one patient throughout):
  Front desk registers a patient -> schedules + checks in an appointment (issuing a queue
  token) -> Doctor records an OPD consultation against that patient -> Pharmacist dispenses a
  prescribed drug against that same patient/consultation -> Billing captures the pharmacy
  charge onto an invoice, finalizes it, and collects payment.

Leg B (IPD ward chain, a separately-admitted inpatient -- POST /api/ipd/patients creates its
own Patient row independent of front-desk registration, by this codebase's own design):
  HeadNurse admits and assigns a nurse -> Nurse charts vitals -> Doctor performs a ward round
  -> HeadNurse generates a discharge summary.
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture
def org_users(make_user):
    nursing_station = make_user(email="frontdesk@e2e-combined.com", role="NursingStation")
    org_id = nursing_station.organization_id
    return {
        "nursing_station": nursing_station,
        "doctor": make_user(email="doctor@e2e-combined.com", role="Doctor", organization_id=org_id),
        "pharmacist": make_user(email="pharm@e2e-combined.com", role="Pharmacist", organization_id=org_id),
        "billing": make_user(email="billing@e2e-combined.com", role="Billing", organization_id=org_id),
        "head_nurse": make_user(email="head@e2e-combined.com", role="HeadNurse", organization_id=org_id),
        "nurse": make_user(email="nurse@e2e-combined.com", role="Nurse", organization_id=org_id),
    }


def test_full_outpatient_billing_journey(js_page, live_server_url, org_users):
    # -- Front desk: register the patient --
    login_as(js_page, live_server_url, org_users["nursing_station"])
    js_page.wait_for_selector("#register-form")
    js_page.fill("#reg-name", "Combined Flow Patient")
    js_page.fill("#reg-age", "29")
    js_page.select_option("#reg-gender", "Male")
    js_page.fill("#reg-phone", "9123456780")
    js_page.click("#register-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Registered')")
    js_page.wait_for_function("document.querySelectorAll('#search-results tr').length > 0")

    patient_id = js_page.evaluate(
        """async () => {
            const res = await fetch('/api/patients/search?q=Combined%20Flow', {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const rows = await res.json();
            return rows[0].id;
        }"""
    )
    assert patient_id

    # -- Front desk: schedule and check in an appointment (issues a queue token) --
    js_page.click(".tab[data-tab='appointments']")
    js_page.wait_for_selector("#appt-doctor option", state="attached")
    js_page.fill("#appt-patient-id", str(patient_id))
    js_page.select_option("#appt-doctor", label=org_users["doctor"].email)
    next_hour = js_page.evaluate("() => { const d = new Date(Date.now() + 3600000); return d.toISOString().slice(0, 16); }")
    js_page.fill("#appt-time", next_hour)
    js_page.click("#schedule-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('scheduled')")
    js_page.wait_for_function("document.querySelectorAll('#appt-rows tr[data-checkin]').length > 0 || document.querySelector('#appt-rows button[data-checkin]')")
    js_page.click("#appt-rows button[data-checkin]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Checked in')")

    js_page.click(".tab[data-tab='queue']")
    js_page.wait_for_selector("#queue-rows :text('Waiting')")

    # -- Doctor: record an OPD consultation for this patient --
    login_as(js_page, live_server_url, org_users["doctor"])
    js_page.wait_for_selector("#consult-form")
    js_page.fill("#c-patient-id", str(patient_id))
    js_page.fill("#c-patient-name", "Combined Flow Patient")
    js_page.fill("#c-chief-complaint", "Sore throat and mild fever")
    js_page.fill("#c-primary-dx", "Pharyngitis")
    js_page.fill(".med-row .med-name", "Azithromycin")
    js_page.fill(".med-row .med-dose", "500mg")
    js_page.click("#consult-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Saved')")

    consultation_id = js_page.evaluate(
        """async () => {
            const res = await fetch('/api/consultations?search=Combined', {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const data = await res.json();
            return data.consultations[0].id;
        }"""
    )
    assert consultation_id

    # -- Pharmacist: dispense the prescribed drug against this patient/consultation --
    login_as(js_page, live_server_url, org_users["pharmacist"])
    js_page.wait_for_selector("#drug-form")
    js_page.fill("#d-name", "Azithromycin Combined")
    js_page.fill("#d-price", "8.00")
    js_page.click("#drug-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Add')")
    js_page.wait_for_selector("#drug-rows :text('Azithromycin Combined')")
    js_page.click("#drug-rows tr[data-id]")
    js_page.wait_for_selector("#drug-modal.modal--open")
    js_page.fill("#b-qty", "50")
    future_date = js_page.evaluate("() => { const d = new Date(); d.setFullYear(d.getFullYear() + 1); return d.toISOString().slice(0, 10); }")
    js_page.fill("#b-expiry", future_date)
    js_page.click("#batch-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Batch received')")
    js_page.click("#drug-modal .modal__close")

    js_page.click(".tab[data-tab='dispense']")
    js_page.wait_for_selector("#disp-drug option", state="attached")
    js_page.locator("#disp-drug").select_option(index=0)
    js_page.fill("#disp-qty", "10")
    js_page.fill("#disp-patient", str(patient_id))
    js_page.fill("#disp-consult", str(consultation_id))
    js_page.click("#dispense-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Dispensed')")

    dispensing_record_id = js_page.evaluate(
        f"""async () => {{
            const res = await fetch('/api/pharmacy/dispensing-records?patient_id={patient_id}', {{
                headers: {{ Authorization: `Bearer ${{localStorage.getItem('access_token')}}` }}
            }});
            const rows = await res.json();
            return rows[0].id;
        }}"""
    )
    assert dispensing_record_id

    # -- Billing: invoice the visit, capture the pharmacy charge, finalize, collect payment --
    login_as(js_page, live_server_url, org_users["billing"])
    js_page.wait_for_selector("#invoice-form")
    js_page.fill("#inv-patient", str(patient_id))
    js_page.fill("#inv-consult", str(consultation_id))
    js_page.click("#invoice-form button[type=submit]")
    js_page.wait_for_selector("#invoice-modal.modal--open")

    js_page.fill("#lm-desc", "Consultation fee")
    js_page.fill("#lm-price", "400")
    js_page.click("#line-manual-form button[type=submit]")
    js_page.wait_for_selector("#invoice-modal-body :text('Consultation fee')")

    js_page.fill("#cp-ids", str(dispensing_record_id))
    js_page.click("#capture-pharmacy-form button[type=submit]")
    js_page.wait_for_selector("#invoice-modal-body :text('Pharmacy dispense')")

    js_page.click("#finalize-invoice-btn")
    js_page.wait_for_selector(".hms-toast--visible:has-text('finalized')")

    js_page.wait_for_selector("#payment-form")
    total_due_text = js_page.locator(".kpi__value--warn, .kpi__value").all_inner_texts()
    js_page.fill("#pay-amount", "480")
    js_page.select_option("#pay-method", "Cash")
    js_page.click("#payment-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('collected')")

    assert js_page.js_errors == []


def test_full_inpatient_ward_journey(js_page, live_server_url, org_users):
    # -- HeadNurse: admit and assign --
    login_as(js_page, live_server_url, org_users["head_nurse"], landing_path="/ipd.html")
    js_page.wait_for_selector("#admit-btn")
    js_page.click("#admit-btn")
    js_page.wait_for_selector("#admit-modal.modal--open")
    js_page.fill("#a-name", "Inpatient Combined Flow")
    js_page.fill("#a-ward", "General Ward")
    js_page.fill("#a-age", "67")
    js_page.click("#admit-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('admitted')")
    js_page.wait_for_selector("#roster-rows :text('Inpatient Combined Flow')")

    patient_id = js_page.evaluate(
        """async () => {
            const res = await fetch('/api/ipd/patients', {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` }
            });
            const rows = await res.json();
            return rows.find(p => p.name === 'Inpatient Combined Flow').id;
        }"""
    )
    js_page.evaluate(
        f"""async () => {{
            await fetch('/api/ipd/assign', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json', Authorization: `Bearer ${{localStorage.getItem('access_token')}}` }},
                body: JSON.stringify({{ patient_id: {patient_id}, nurse_id: {org_users["nurse"].id} }}),
            }});
        }}"""
    )

    # -- Nurse: chart vitals --
    login_as(js_page, live_server_url, org_users["nurse"])
    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    js_page.click("#roster-rows tr[data-id]")
    js_page.wait_for_selector("#pv-name:has-text('Inpatient Combined Flow')")
    js_page.click(".tab[data-tab='vitals']")
    js_page.fill("#v-sys", "130")
    js_page.fill("#v-dia", "85")
    js_page.fill("#v-hr", "82")
    js_page.click("#vitals-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Vitals recorded')")

    # -- Doctor: ward round --
    login_as(js_page, live_server_url, org_users["doctor"], landing_path="/ipd.html")
    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    js_page.click("#roster-rows tr[data-id]")
    js_page.wait_for_selector("#pv-name:has-text('Inpatient Combined Flow')")
    js_page.click(".tab[data-tab='round']")
    js_page.fill("#r-chief", "Day 2 post-admission review")
    js_page.fill(".med-row .rm-name", "Ceftriaxone")
    js_page.fill(".med-row .rm-dose", "1g")
    js_page.click("#round-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Round saved')")

    # -- HeadNurse: discharge summary --
    login_as(js_page, live_server_url, org_users["head_nurse"], landing_path="/ipd.html")
    js_page.wait_for_function("document.querySelectorAll('#roster-rows tr[data-id]').length > 0")
    js_page.click("#roster-rows tr[data-id]")
    js_page.wait_for_selector("#pv-name:has-text('Inpatient Combined Flow')")
    js_page.click(".tab[data-tab='discharge']")
    js_page.click("#generate-discharge-btn")
    js_page.wait_for_selector(".hms-toast--visible:has-text('generated')")
    js_page.wait_for_selector("#discharge-body :text('post-admission')")

    assert js_page.js_errors == []
