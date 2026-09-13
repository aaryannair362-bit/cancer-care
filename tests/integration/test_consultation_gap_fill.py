"""
Core Oncology 4 Sections gap-fill, Consultation items 1.2/1.3/1.5/1.6:
  - GET /consultation-worklist -- org-wide New/Follow-up/Results Pending/Urgent flags.
  - POST /results/{id}/action-complete -- closes the ACKNOWLEDGED -> ACTIONED lifecycle.
  - POST/PATCH /patients/{id}/cancer-diagnosis -- structured diagnosis capture.
  - Finalizing an already-FINAL encounter's note now requires amendment_reason and snapshots
    the pre-amendment content, instead of silently overwriting it.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@consult-gap-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="CONSULT-GAP-0001", name="Consult Gap Test Patient", age=48, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def test_consultation_worklist_flags_new_encounter_and_urgent_result(client, onc_headers, patient, db_session):
    from app.models_cca import CCAResult

    encounter = client.post(f"/api/cca/patients/{patient.id}/encounters", headers=onc_headers, json={
        "specialty": "Medical Oncology", "encounter_type": "OPD_CONSULTATION",
    })
    assert encounter.status_code == 201, encounter.text

    critical_result = CCAResult(patient_id=patient.id, result_type="LAB", title="Critical Potassium", status="NEW", is_critical=True)
    db_session.add(critical_result)
    db_session.commit()

    worklist = client.get("/api/cca/consultation-worklist", headers=onc_headers)
    assert worklist.status_code == 200, worklist.text
    row = next(r for r in worklist.json()["results"] if r["id"] == patient.id)
    assert row["flags"]["new"] is True
    assert row["flags"]["urgent"] is True
    assert row["flags"]["results_pending"] is True
    assert row["flags"]["follow_up"] is False


def test_consultation_worklist_flags_follow_up_separately_from_new(client, onc_headers, patient):
    encounter = client.post(f"/api/cca/patients/{patient.id}/encounters", headers=onc_headers, json={
        "specialty": "Medical Oncology", "encounter_type": "FOLLOW_UP_VISIT",
    })
    assert encounter.status_code == 201, encounter.text

    worklist = client.get("/api/cca/consultation-worklist", headers=onc_headers)
    row = next(r for r in worklist.json()["results"] if r["id"] == patient.id)
    assert row["flags"]["follow_up"] is True
    assert row["flags"]["new"] is False


def test_result_action_complete_requires_prior_acknowledgement(client, onc_headers, patient, db_session):
    from app.models_cca import CCAResult

    result = CCAResult(patient_id=patient.id, result_type="LAB", title="CBC", status="NEW")
    db_session.add(result)
    db_session.commit()
    db_session.refresh(result)

    too_soon = client.post(f"/api/cca/results/{result.id}/action-complete", headers=onc_headers)
    assert too_soon.status_code == 400

    client.post(f"/api/cca/results/{result.id}/acknowledge", headers=onc_headers)
    done = client.post(f"/api/cca/results/{result.id}/action-complete", headers=onc_headers)
    assert done.status_code == 200, done.text
    assert done.json()["result"]["status"] == "ACTIONED"
    assert done.json()["result"]["actioned_by"] is not None

    listed = client.get("/api/cca/results", headers=onc_headers)
    row = next(r for r in listed.json()["results"] if r["id"] == result.id)
    assert row["status"] == "ACTIONED"


def test_cancer_diagnosis_create_list_and_update(client, onc_headers, patient):
    missing_site = client.post(f"/api/cca/patients/{patient.id}/cancer-diagnosis", headers=onc_headers, json={})
    assert missing_site.status_code == 422

    created = client.post(f"/api/cca/patients/{patient.id}/cancer-diagnosis", headers=onc_headers, json={
        "primary_site": "Left breast", "histology": "Invasive ductal carcinoma", "grade": "Grade 2",
    })
    assert created.status_code == 201, created.text
    diagnosis_id = created.json()["diagnosis"]["id"]
    assert created.json()["diagnosis"]["status"] == "SUSPECTED"

    listed = client.get(f"/api/cca/patients/{patient.id}/cancer-diagnosis", headers=onc_headers)
    assert listed.status_code == 200
    assert len(listed.json()["diagnoses"]) == 1

    confirmed = client.patch(f"/api/cca/cancer-diagnosis/{diagnosis_id}", headers=onc_headers, json={"status": "CONFIRMED"})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["diagnosis"]["status"] == "CONFIRMED"
    assert confirmed.json()["diagnosis"]["confirmed_by"] is not None


def test_encounter_second_finalise_requires_amendment_reason_and_snapshots(client, onc_headers, patient):
    encounter = client.post(f"/api/cca/patients/{patient.id}/encounters", headers=onc_headers, json={
        "specialty": "Medical Oncology", "encounter_type": "OPD_CONSULTATION",
    })
    encounter_id = encounter.json()["encounter"]["id"]

    first = client.post(f"/api/cca/encounters/{encounter_id}/note/finalise", headers=onc_headers, json={
        "chief_complaint": "Follow-up for breast lump.", "visit_type": "NEW_CONSULTATION",
    })
    assert first.status_code == 200, first.text
    assert first.json()["encounter"]["note_status"] == "FINAL"

    missing_reason = client.post(f"/api/cca/encounters/{encounter_id}/note/finalise", headers=onc_headers, json={
        "chief_complaint": "Corrected chief complaint.",
    })
    assert missing_reason.status_code == 400

    amended = client.post(f"/api/cca/encounters/{encounter_id}/note/finalise", headers=onc_headers, json={
        "chief_complaint": "Corrected chief complaint.", "amendment_reason": "Transcription error in original note.",
    })
    assert amended.status_code == 200, amended.text
    assert amended.json()["encounter"]["note_status"] == "AMENDED"

    case_summary = client.get(f"/api/cca/patients/{patient.id}/case-summary", headers=onc_headers)
    assert case_summary.status_code == 200
    enc_row = next(e for e in case_summary.json()["encounters"] if e["id"] == encounter_id)
    assert enc_row["visit_type"] == "NEW_CONSULTATION"
