"""
Real-browser regression test for the HeadNurse "Nursing Notes" action button
(frontend/headnurse.html).

The mic/Process voice flow this file used to cover (POST /api/ipd/nurse-consult) was removed
per the 2026-08-25 CHANGELOG decision -- see tests/e2e/test_ipd_nursing_note_manual_entry.py
for the current, manual-entry regression coverage of the same modal (that file's fixture logs
in as a plain Nurse; this modal and its "Save Nursing Notes" path are identical for HeadNurse,
just reached via headnurse.html's own copy of the same modal markup/handlers rather than
ipd.html's -- HeadNurse never reaches ipd.html at all, see that file's own redirect).
"""
import pytest

from tests.e2e.conftest import mint_tokens, set_tokens_in_browser

pytestmark = pytest.mark.e2e


@pytest.fixture
def unassigned_patient(make_user, db_session):
    from app.models import Patient

    head_nurse = make_user(email="head@e2e-hn-voice.com", role="HeadNurse")
    patient = Patient(name="E2E HN Voice Patient", age=48, gender="F", ward="General",
                       organization_id=head_nurse.organization_id, created_by=head_nurse.id)
    db_session.add(patient)
    db_session.commit()
    db_session.refresh(patient)
    return head_nurse, patient


def test_headnurse_sees_nursing_notes_action_button(js_page, live_server_url, unassigned_patient):
    head_nurse, patient = unassigned_patient
    tokens = mint_tokens(head_nurse)
    set_tokens_in_browser(js_page, live_server_url, tokens["access_token"], tokens["refresh_token"])
    js_page.goto(f"{live_server_url}/headnurse.html")
    js_page.wait_for_timeout(300)
    js_page.evaluate(f"showPatientDetail({patient.id})")
    js_page.wait_for_timeout(300)
    assert js_page.locator("button:has-text('📝 Nursing Notes')").count() == 1
