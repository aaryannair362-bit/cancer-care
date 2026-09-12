"""
Final gap-closing round, item 5: Toxicity Register/Timeline (SCR-TOX-001) + Serious/
Reportable Adverse Event workflow (SCR-TOX-004). Previously ToxicityEvent had no register/
timeline aggregation and no seriousness/regulatory-reporting fields at all.

Never tests a computed clinical score -- peak_grade/latest_grade are plain max/last over
already-recorded grades, days_since_last_grading is plain date arithmetic, and
causality_assessment/outcome are always the clinician's own typed judgment.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@sae-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="SAE-TEST-0001", name="SAE Test Patient", age=55, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _record_toxicity(client, headers, patient_id, term, grade):
    r = client.post("/api/cca/treatment/toxicity", headers=headers, json={
        "patient_id": patient_id, "term": term, "grade": grade, "baseline_value": "Grade 0 (Baseline)",
    })
    assert r.status_code == 200, r.text
    return r.json()["toxicity"]["id"]


def test_toxicity_register_groups_and_peaks(client, onc_headers, patient):
    _record_toxicity(client, onc_headers, patient.id, "Peripheral sensory neuropathy", 1)
    _record_toxicity(client, onc_headers, patient.id, "Peripheral sensory neuropathy", 2)
    _record_toxicity(client, onc_headers, patient.id, "Nausea", 3)

    register = client.get(f"/api/cca/patients/{patient.id}/toxicity-register", headers=onc_headers)
    assert register.status_code == 200, register.text
    body = register.json()
    assert body["register"][0]["term"] == "Nausea"
    assert body["register"][0]["peak_grade"] == 3

    neuropathy = next(r for r in body["register"] if r["term"] == "Peripheral sensory neuropathy")
    assert neuropathy["peak_grade"] == 2
    assert neuropathy["latest_grade"] == 2
    assert len(neuropathy["timeline"]) == 2
    assert body["sae_report_count"] == 0


def test_sae_report_lifecycle(client, onc_headers, patient):
    tox_id = _record_toxicity(client, onc_headers, patient.id, "Neutropenic sepsis", 4)

    bad_criteria = client.post(f"/api/cca/patients/{patient.id}/sae-reports", headers=onc_headers, json={
        "seriousness_criteria": ["Not A Real Criterion"], "event_description": "x",
    })
    assert bad_criteria.status_code == 422

    missing_desc = client.post(f"/api/cca/patients/{patient.id}/sae-reports", headers=onc_headers, json={
        "seriousness_criteria": ["Hospitalization"],
    })
    assert missing_desc.status_code == 422

    created = client.post(f"/api/cca/patients/{patient.id}/sae-reports", headers=onc_headers, json={
        "seriousness_criteria": ["Hospitalization", "Life-threatening"], "event_description": "Neutropenic sepsis requiring ICU admission.",
        "toxicity_event_id": tox_id, "causality_assessment": "Probable", "action_taken_with_treatment": "Treatment Interrupted",
        "outcome": "Recovering",
    })
    assert created.status_code == 201, created.text
    sae_id = created.json()["sae_report"]["id"]
    assert created.json()["sae_report"]["status"] == "DRAFT"

    listed = client.get(f"/api/cca/patients/{patient.id}/sae-reports", headers=onc_headers)
    assert len(listed.json()["sae_reports"]) == 1

    missing_reported_to = client.post(f"/api/cca/sae-reports/{sae_id}/submit", headers=onc_headers, json={})
    assert missing_reported_to.status_code == 422

    submitted = client.post(f"/api/cca/sae-reports/{sae_id}/submit", headers=onc_headers, json={
        "reported_to": "Institutional Ethics Committee",
    })
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["sae_report"]["status"] == "SUBMITTED"

    already_submitted = client.post(f"/api/cca/sae-reports/{sae_id}/submit", headers=onc_headers, json={"reported_to": "X"})
    assert already_submitted.status_code == 409

    register = client.get(f"/api/cca/patients/{patient.id}/toxicity-register", headers=onc_headers)
    assert register.json()["sae_report_count"] == 1
