"""
Ward Oversight (headnurse.html) real-browser coverage: dashboard KPIs, nurse assignment,
shift scheduling, reports, and ward configuration.
"""
import pytest

from tests.e2e.conftest import REQUIRES_BROWSER, login_as

pytestmark = REQUIRES_BROWSER


@pytest.fixture
def head_nurse(make_user):
    return make_user(email="head@e2e-hn.com", role="HeadNurse")


@pytest.fixture
def nurse(make_user, head_nurse):
    return make_user(email="nurse@e2e-hn.com", role="Nurse", organization_id=head_nurse.organization_id)


def test_dashboard_kpis_render(js_page, live_server_url, head_nurse):
    login_as(js_page, live_server_url, head_nurse)
    js_page.wait_for_selector("#dash-kpis .kpi")
    assert js_page.locator("#dash-kpis .kpi").count() == 4
    assert js_page.js_errors == []


def test_add_ward_and_see_it_listed(js_page, live_server_url, head_nurse):
    login_as(js_page, live_server_url, head_nurse)
    js_page.click(".tab[data-tab='wards']")
    js_page.fill("#w-name", "General Ward E2E")
    js_page.fill("#w-capacity", "10")
    js_page.click("#ward-form button[type=submit]")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Ward added')")
    js_page.wait_for_selector("#ward-rows :text('General Ward E2E')")
    assert js_page.js_errors == []


def test_assign_nurse_to_patient(js_page, live_server_url, head_nurse, nurse, db_session):
    from app.models import Patient

    patient = Patient(
        name="Assignment Test Patient", age=50, gender="M", ward="ICU", bed="2",
        organization_id=head_nurse.organization_id, created_by=head_nurse.id, status="Active",
    )
    db_session.add(patient)
    db_session.commit()

    login_as(js_page, live_server_url, head_nurse)
    js_page.click(".tab[data-tab='assignments']")
    js_page.wait_for_selector("#assign-rows :text('Assignment Test Patient')")
    js_page.select_option(f'.reassign-select[data-patient="{patient.id}"]', label=f"{nurse.email} (0)")
    js_page.click(f'button[data-assign="{patient.id}"]')
    js_page.wait_for_selector(".hms-toast--visible:has-text('assigned')")
    js_page.wait_for_selector(f'tr:has(button[data-unassign="{patient.id}"])')
    assert js_page.js_errors == []


def test_shift_grid_updates(js_page, live_server_url, head_nurse, nurse):
    login_as(js_page, live_server_url, head_nurse)
    js_page.click(".tab[data-tab='shifts']")
    js_page.wait_for_selector(f'select[data-nurse="{nurse.id}"]')
    first_select = js_page.locator(f'select[data-nurse="{nurse.id}"]').first
    first_select.select_option("Morning")
    js_page.wait_for_selector(".hms-toast--visible:has-text('Shift updated')")
    assert js_page.js_errors == []


def test_reports_tab_loads(js_page, live_server_url, head_nurse):
    login_as(js_page, live_server_url, head_nurse)
    js_page.click(".tab[data-tab='reports']")
    js_page.click("#report-refresh")
    js_page.wait_for_selector("#report-tasks tr")
    assert js_page.js_errors == []
