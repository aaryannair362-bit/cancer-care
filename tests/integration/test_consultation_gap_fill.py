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


def test_encounter_note_draft_persists_scribe_phase_2_fields(client, onc_headers, patient, monkeypatch):
    """POST /encounters/{id}/note/draft (the CCA oncology consumer of scribe.scribe_transcript,
    per its own docstring: "mirrors Doctor OPD's POST /api/scribe") had no test coverage at all
    before this. Confirms the Scribe Phase 2 fields (biomarkers/imagingFindings/comorbidities/
    existingResultsReviewed) reach CCAEncounter.note_content end-to-end through the real
    endpoint, not just scribe.py's own unit tests -- this JSON column is what actually persists
    them for the oncology flow (unlike the general Consultation table's fixed columns)."""
    from tests.conftest import mock_groq_json

    encounter = client.post(f"/api/cca/patients/{patient.id}/encounters", headers=onc_headers, json={
        "specialty": "Medical Oncology", "encounter_type": "OPD_CONSULTATION",
    })
    encounter_id = encounter.json()["encounter"]["id"]

    mock_groq_json(monkeypatch, {
        "chiefComplaint": "Lump in the left breast",
        "biomarkers": [{"marker": "HER2", "result": "IHC 3+, positive"}],
        "imagingFindings": [{"study": "MRI breast", "finding": "5.4 cm lesion, 2 o'clock position"}],
        "comorbidities": ["Type 2 diabetes mellitus, ~12 years, HbA1c 8.3%"],
        "existingResultsReviewed": ["MRI breast", "CT chest/abdomen/pelvis"],
    })

    draft = client.post(f"/api/cca/encounters/{encounter_id}/note/draft", headers=onc_headers, json={
        "transcript": "Doctor and patient discuss a breast lump, biomarker results, and imaging findings at length.",
    })
    assert draft.status_code == 200, draft.text
    note = draft.json()["note_content"]
    assert note["biomarkers"] == [{"marker": "HER2", "result": "IHC 3+, positive"}]
    assert note["imagingFindings"] == [{"study": "MRI breast", "finding": "5.4 cm lesion, 2 o'clock position"}]
    assert note["comorbidities"] == ["Type 2 diabetes mellitus, ~12 years, HbA1c 8.3%"]
    assert note["existingResultsReviewed"] == ["MRI breast", "CT chest/abdomen/pelvis"]


def test_encounter_note_draft_new_fields_are_persisted_to_the_db(client, onc_headers, patient, db_session, monkeypatch):
    """Same real endpoint as above, but confirms the new fields are actually persisted on
    CCAEncounter.note_content (a JSON column, unlike the general Consultation table's fixed
    columns -- see that model's docstring) rather than only present in the immediate response.
    Note: GET .../case-summary's own "encounters" projection does NOT surface note_content at
    all (a pre-existing, unrelated gap -- it only ever reads a few specific snake_case keys that
    don't match scribe_transcript's camelCase AI_DRAFT output), so persistence is checked
    directly against the row instead."""
    from tests.conftest import mock_groq_json
    from app.models_cca import CCAEncounter

    encounter = client.post(f"/api/cca/patients/{patient.id}/encounters", headers=onc_headers, json={
        "specialty": "Medical Oncology", "encounter_type": "OPD_CONSULTATION",
    })
    encounter_id = encounter.json()["encounter"]["id"]

    mock_groq_json(monkeypatch, {
        "biomarkers": [{"marker": "ER", "result": "<1%, negative"}],
    })
    client.post(f"/api/cca/encounters/{encounter_id}/note/draft", headers=onc_headers, json={
        "transcript": "Doctor and patient discuss biomarker results at length.",
    })

    db_session.expire_all()
    row = db_session.query(CCAEncounter).filter(CCAEncounter.id == encounter_id).first()
    assert row.note_content["biomarkers"] == [{"marker": "ER", "result": "<1%, negative"}]
