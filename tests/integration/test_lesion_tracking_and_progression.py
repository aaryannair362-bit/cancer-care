"""
Final gap-closing round, item 6: structured, longitudinal per-lesion tracking (shared by
Response Assessment and Radiology, both of which previously relied only on an unstructured
JSON blob) + a distinct Progression/Recurrence event.

Never tests a computed RECIST percent-change/threshold judgment -- baseline/nadir/current
are just the earliest/smallest/latest recorded measurement (plain min/first/last), and
event_type/response_category are always the clinician's own classification.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@lesion-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="LESION-TEST-0001", name="Lesion Tracking Test Patient", age=61, sex="Male", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def test_lesion_measurement_baseline_nadir_current(client, onc_headers, patient):
    missing_label = client.post(f"/api/cca/patients/{patient.id}/target-lesions", headers=onc_headers, json={})
    assert missing_label.status_code == 422

    lesion = client.post(f"/api/cca/patients/{patient.id}/target-lesions", headers=onc_headers, json={
        "lesion_label": "Lesion 1 -- Liver segment VI", "organ_site": "Liver", "identified_on": "2026-01-01",
    })
    assert lesion.status_code == 201, lesion.text
    lesion_id = lesion.json()["lesion"]["id"]

    missing_diameter = client.post(f"/api/cca/target-lesions/{lesion_id}/measurements", headers=onc_headers, json={"measured_on": "2026-01-01"})
    assert missing_diameter.status_code == 422

    client.post(f"/api/cca/target-lesions/{lesion_id}/measurements", headers=onc_headers, json={
        "measured_on": "2026-01-01", "longest_diameter_mm": 40.0,
    })
    client.post(f"/api/cca/target-lesions/{lesion_id}/measurements", headers=onc_headers, json={
        "measured_on": "2026-03-01", "longest_diameter_mm": 22.0,
    })
    client.post(f"/api/cca/target-lesions/{lesion_id}/measurements", headers=onc_headers, json={
        "measured_on": "2026-05-01", "longest_diameter_mm": 28.0,
    })

    listed = client.get(f"/api/cca/patients/{patient.id}/target-lesions", headers=onc_headers)
    assert listed.status_code == 200, listed.text
    lesion_out = listed.json()["lesions"][0]
    assert lesion_out["baseline_diameter_mm"] == 40.0
    assert lesion_out["nadir_diameter_mm"] == 22.0
    assert lesion_out["current_diameter_mm"] == 28.0
    assert lesion_out["measurement_count"] == 3

    measurements = client.get(f"/api/cca/target-lesions/{lesion_id}/measurements", headers=onc_headers)
    assert len(measurements.json()["measurements"]) == 3


def test_lesion_resolution(client, onc_headers, patient):
    lesion_id = client.post(f"/api/cca/patients/{patient.id}/target-lesions", headers=onc_headers, json={
        "lesion_label": "Lesion 2 -- Lung RUL",
    }).json()["lesion"]["id"]

    resolved = client.post(f"/api/cca/target-lesions/{lesion_id}/measurements", headers=onc_headers, json={
        "measured_on": "2026-06-01", "present": False, "notes": "No longer visible on CT.",
    })
    assert resolved.status_code == 201, resolved.text

    listed = client.get(f"/api/cca/patients/{patient.id}/target-lesions", headers=onc_headers)
    assert listed.json()["lesions"][0]["status"] == "RESOLVED"


def test_progression_recurrence_event(client, onc_headers, patient):
    bad_type = client.post(f"/api/cca/patients/{patient.id}/progression-recurrence-events", headers=onc_headers, json={
        "event_type": "Worsening", "detected_on": "2026-06-01",
    })
    assert bad_type.status_code == 422

    created = client.post(f"/api/cca/patients/{patient.id}/progression-recurrence-events", headers=onc_headers, json={
        "event_type": "Progression", "detected_on": "2026-06-01", "site": "Liver",
        "evidence": "New 15mm liver lesion on surveillance CT.", "clinical_impact": "Change to second-line therapy.",
    })
    assert created.status_code == 201, created.text

    listed = client.get(f"/api/cca/patients/{patient.id}/progression-recurrence-events", headers=onc_headers)
    assert listed.status_code == 200
    assert listed.json()["events"][0]["event_type"] == "Progression"
