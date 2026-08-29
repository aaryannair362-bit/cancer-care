"""
Real-browser regression test for the Radiologist "Generate AI Draft" fix
(frontend/radiologist.html's generateAiDraftForRadiology()).

Before the fix it called POST /api/scribe -- the general OPD endpoint gated is_doctor(...)
only -- so a real CCARadiologist got a 403 every time and Findings/Impression could never be
AI-populated. Now calls POST /api/cca/scribe-extract, a properly radiologist-scoped, non-
persisting extraction endpoint (backend/app/routers/cca.py).
"""
import json

import pytest

from tests.e2e.conftest import login_as

pytestmark = pytest.mark.e2e


@pytest.fixture
def radiology_setup(make_user, db_session):
    from app.models_cca import CCAPatient, CCAOrder

    radiologist = make_user(email="radiologist@e2e-cca.com", role="CCARadiologist")
    patient = CCAPatient(
        mrn="E2E-RAD-0001", name="E2E Radiologist Test Patient", age=60, sex="Male",
        organization_id=radiologist.organization_id, journey_state="Registered",
    )
    db_session.add(patient)
    db_session.flush()
    order = CCAOrder(
        patient_id=patient.id, order_type="RADIOLOGY", item_name="CT Chest/Abdomen/Pelvis",
        clinical_indication="Staging workup.", status="SCHEDULED",
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return radiologist, patient, order


def test_radiologist_generate_ai_draft_populates_findings_and_impression(
    js_page, live_server_url, radiology_setup, monkeypatch
):
    import app.main as app_main

    radiologist, patient, order = radiology_setup

    def _fake_call_groq_api(prompt, system=None, temperature=0.3):
        return json.dumps({
            "chiefComplaint": "",
            "hpi": "No suspicious pulmonary nodules. No mediastinal or hilar lymphadenopathy.",
            "primaryDiagnosis": "No evidence of distant metastatic disease.",
            "differentialDiagnosis": "",
            "medications": [],
            "advice": "Correlate clinically. Follow-up imaging in 3 months.",
            "labTests": [],
        })

    monkeypatch.setattr(app_main.scribe, "_call_groq_api", _fake_call_groq_api)

    login_as(js_page, live_server_url, radiologist, landing_path="/radiologist.html")
    js_page.wait_for_timeout(500)

    js_page.evaluate(f"openPatient({patient.id})")
    js_page.wait_for_timeout(300)
    js_page.evaluate(f"openImagingOrder({order.id})")
    js_page.wait_for_timeout(500)

    js_page.fill("#scribe-transcript",
                 "No suspicious pulmonary nodules. No mediastinal or hilar lymphadenopathy. "
                 "No evidence of distant metastatic disease. Follow-up imaging in 3 months.")
    js_page.click("button:has-text('Generate AI Draft')")
    js_page.wait_for_function(
        "document.getElementById('img-findings').value.length > 0", timeout=10000
    )

    assert js_page.js_errors == [], f"unexpected JS errors: {js_page.js_errors}"
    findings = js_page.eval_on_selector("#img-findings", "el => el.value")
    impression = js_page.eval_on_selector("#img-impression", "el => el.value")
    assert "pulmonary nodules" in findings
    assert "distant metastatic disease" in impression
