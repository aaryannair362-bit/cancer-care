"""
Final gap-closing round, item 12: Nurse Intake medication reconciliation and standing
adverse-reaction/allergy history. Previously neither existed anywhere in this codebase --
PreTreatmentSafetyCheck's own docstring explicitly notes a shared allergy list was "out of
scope" when that module was built.

Never computes or validates a dose -- dose/frequency are always the nurse's own typed
record of what the patient reports taking.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def nurse(make_user):
    return make_user(email="nurse@intake-test.com", role="CCANurseNavigator")


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


@pytest.fixture
def patient(db_session, nurse):
    p = CCAPatient(mrn="INTAKE-TEST-0001", name="Intake Test Patient", age=64, sex="Female", organization_id=nurse.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def intake_id(client, nurse_headers, patient):
    encounter_id = client.post(f"/api/cca/patients/{patient.id}/encounters", headers=nurse_headers, json={}).json()["encounter"]["id"]
    return client.post(f"/api/cca/encounters/{encounter_id}/intake", headers=nurse_headers, json={
        "patient_id": patient.id,
    }).json()["intake"]["id"]


def test_medication_reconciliation(client, nurse_headers, intake_id):
    missing_drug = client.post(f"/api/cca/intake-assessments/{intake_id}/medication-reconciliation", headers=nurse_headers, json={})
    assert missing_drug.status_code == 422

    entry = client.post(f"/api/cca/intake-assessments/{intake_id}/medication-reconciliation", headers=nurse_headers, json={
        "drug_name": "Metformin", "dose": "500mg", "frequency": "BID", "route": "PO",
        "source": "Patient-reported", "action": "Continue",
    })
    assert entry.status_code == 201, entry.text

    listed = client.get(f"/api/cca/intake-assessments/{intake_id}/medication-reconciliation", headers=nurse_headers)
    assert listed.status_code == 200
    assert len(listed.json()["reconciliation_entries"]) == 1
    assert listed.json()["reconciliation_entries"][0]["action"] == "Continue"


def test_adverse_reaction_history(client, nurse_headers, patient):
    missing_allergen = client.post(f"/api/cca/patients/{patient.id}/adverse-reaction-history", headers=nurse_headers, json={})
    assert missing_allergen.status_code == 422

    entry = client.post(f"/api/cca/patients/{patient.id}/adverse-reaction-history", headers=nurse_headers, json={
        "allergen": "Penicillin", "reaction_description": "Hives, facial swelling", "severity": "Severe", "onset": "2015",
    })
    assert entry.status_code == 201, entry.text
    entry_id = entry.json()["adverse_reaction"]["id"]

    listed = client.get(f"/api/cca/patients/{patient.id}/adverse-reaction-history", headers=nurse_headers)
    assert listed.status_code == 200
    assert listed.json()["adverse_reactions"][0]["status"] == "Active"

    resolved = client.post(f"/api/cca/adverse-reaction-history/{entry_id}/resolve", headers=nurse_headers)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["adverse_reaction"]["status"] == "Resolved"
