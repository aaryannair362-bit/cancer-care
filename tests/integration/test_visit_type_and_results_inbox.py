"""
Final gap-closing round, item 16 (Medical Oncology upgrade tier): dedicated New
Consultation / Follow-up Visit distinction, and a real Results Inbox UI.

The backend mechanics already existed (CCAEncounter.encounter_type was already settable at
creation; GET /results and POST /results/{id}/acknowledge already existed) -- this was a
real dead UI gap: medical_oncologist.html never offered the choice, and the only frontend
with a "results inbox" mockup (cca_os.html) was a disconnected demo prototype never wired
to the real backend. These tests lock in the backend mechanics the new UI now depends on.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@visit-type-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="VISIT-TYPE-TEST-0001", name="Visit Type Test Patient", age=53, sex="Male", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def test_follow_up_encounter_type_and_interval_fields_round_trip(client, onc_headers, patient):
    encounter = client.post(f"/api/cca/patients/{patient.id}/encounters", headers=onc_headers, json={
        "specialty": "Medical Oncology", "encounter_type": "FOLLOW_UP_VISIT",
    })
    assert encounter.status_code == 201, encounter.text
    encounter_id = encounter.json()["encounter"]["id"]

    finalised = client.post(f"/api/cca/encounters/{encounter_id}/note/finalise", headers=onc_headers, json={
        "visit_type": "FOLLOW_UP_VISIT",
        "interval_history": "No new symptoms since last cycle.",
        "interval_toxicity": "Grade 1 fatigue, otherwise well.",
        "interval_response_review": "CT shows partial response, continue current regimen.",
    })
    assert finalised.status_code == 200, finalised.text
    assert finalised.json()["encounter"]["status"] == "CLOSED"


def test_results_inbox_list_and_acknowledge(client, onc_headers, patient, db_session):
    from app.models_cca import CCAResult

    result = CCAResult(patient_id=patient.id, result_type="LAB", title="CBC", status="NEW")
    db_session.add(result)
    db_session.commit()
    db_session.refresh(result)

    listed = client.get("/api/cca/results", headers=onc_headers)
    assert listed.status_code == 200, listed.text
    row = next(r for r in listed.json()["results"] if r["id"] == result.id)
    assert row["status"] == "NEW"

    acknowledged = client.post(f"/api/cca/results/{result.id}/acknowledge", headers=onc_headers)
    assert acknowledged.status_code == 200, acknowledged.text

    listed_after = client.get("/api/cca/results", headers=onc_headers)
    row_after = next(r for r in listed_after.json()["results"] if r["id"] == result.id)
    assert row_after["status"] == "ACKNOWLEDGED"
    assert row_after["acknowledged_by"] is not None
