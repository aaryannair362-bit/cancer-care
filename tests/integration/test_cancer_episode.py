"""
Final gap-closing round, item 1 (CRITICAL): Cancer Episode + Line of Therapy (Product 1 vs
Product 2 gap report item 5). Previously cancer_episode_ref on TreatmentCompletion/
SurveillancePlan was a free-text label with nothing to point to -- this is the formal
entity, plus line-of-therapy tracking, linked from diagnosis -> stage -> biomarker -> plan.

Never tests a computed clinical judgment -- line_number is server-assigned bookkeeping,
not a clinical decision; outcome/status are always the clinician's own typed choice.
"""
import pytest

from app.models_cca import CCAPatient, CCACancerDiagnosis


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@episode-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="EPISODE-TEST-0001", name="Cancer Episode Test Patient", age=57, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def test_create_episode_and_link_diagnosis(client, onc_headers, db_session, patient):
    diagnosis = CCACancerDiagnosis(patient_id=patient.id, primary_site="Left Breast", histology="Invasive Ductal Carcinoma")
    db_session.add(diagnosis)
    db_session.commit()
    db_session.refresh(diagnosis)

    bad_diagnosis = client.post(f"/api/cca/patients/{patient.id}/cancer-episodes", headers=onc_headers, json={
        "primary_diagnosis_id": 999999,
    })
    assert bad_diagnosis.status_code == 422

    created = client.post(f"/api/cca/patients/{patient.id}/cancer-episodes", headers=onc_headers, json={
        "primary_diagnosis_id": diagnosis.id, "label": "Left Breast IDC 2026", "intent_at_diagnosis": "Curative",
    })
    assert created.status_code == 201, created.text
    episode = created.json()["episode"]
    assert episode["episode_number"] == 1
    assert episode["primary_site"] == "Left Breast"
    assert episode["status"] == "ACTIVE"

    # A second episode for the same patient auto-increments episode_number.
    second = client.post(f"/api/cca/patients/{patient.id}/cancer-episodes", headers=onc_headers, json={
        "primary_site": "Right Thyroid", "label": "Second Primary",
    })
    assert second.status_code == 201
    assert second.json()["episode"]["episode_number"] == 2

    listed = client.get(f"/api/cca/patients/{patient.id}/cancer-episodes", headers=onc_headers)
    assert listed.status_code == 200
    assert len(listed.json()["episodes"]) == 2


def test_episode_chain_and_close(client, onc_headers, db_session, patient):
    episode_id = client.post(f"/api/cca/patients/{patient.id}/cancer-episodes", headers=onc_headers, json={
        "primary_site": "Left Breast", "label": "Left Breast IDC 2026",
    }).json()["episode"]["id"]

    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={
        "patient_id": patient.id, "intent": "Curative", "protocol_name": "AC-T", "episode_id": episode_id,
    }).json()["treatment_plan"]["id"]

    staging = client.post(f"/api/cca/patients/{patient.id}/staging/confirm", headers=onc_headers, json={
        "stage_value": "IIA", "t_stage": "T2", "n_stage": "N0", "m_stage": "M0", "stage_group": "IIA",
        "episode_id": episode_id,
    })
    assert staging.status_code == 200, staging.text

    detail = client.get(f"/api/cca/cancer-episodes/{episode_id}", headers=onc_headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert any(p["id"] == plan_id for p in body["treatment_plans"])
    assert len(body["staging_records"]) == 1

    bad_status = client.post(f"/api/cca/cancer-episodes/{episode_id}/close", headers=onc_headers, json={"status": "Nope"})
    assert bad_status.status_code == 422

    closed = client.post(f"/api/cca/cancer-episodes/{episode_id}/close", headers=onc_headers, json={
        "status": "SURVEILLANCE", "closed_reason": "Active treatment complete.",
    })
    assert closed.status_code == 200
    assert closed.json()["episode"]["status"] == "SURVEILLANCE"


def test_lines_of_therapy_lifecycle(client, onc_headers, patient):
    episode_id = client.post(f"/api/cca/patients/{patient.id}/cancer-episodes", headers=onc_headers, json={
        "primary_site": "Left Breast",
    }).json()["episode"]["id"]

    line1 = client.post(f"/api/cca/cancer-episodes/{episode_id}/lines-of-therapy", headers=onc_headers, json={
        "setting": "Adjuvant", "regimen_summary": "AC-T",
    })
    assert line1.status_code == 201, line1.text
    line1_id = line1.json()["line_of_therapy"]["id"]
    assert line1.json()["line_of_therapy"]["line_number"] == 1

    missing_reason = client.post(f"/api/cca/cancer-episodes/{episode_id}/lines-of-therapy", headers=onc_headers, json={
        "setting": "First-line Metastatic", "regimen_summary": "Paclitaxel",
    })
    assert missing_reason.status_code == 422

    line2 = client.post(f"/api/cca/cancer-episodes/{episode_id}/lines-of-therapy", headers=onc_headers, json={
        "setting": "First-line Metastatic", "regimen_summary": "Paclitaxel", "reason_for_line_change": "Distant recurrence.",
    })
    assert line2.status_code == 201, line2.text
    assert line2.json()["line_of_therapy"]["line_number"] == 2

    # Starting line 2 auto-closed line 1 since it was still ACTIVE.
    listed = client.get(f"/api/cca/cancer-episodes/{episode_id}/lines-of-therapy", headers=onc_headers).json()["lines_of_therapy"]
    line1_after = next(l for l in listed if l["id"] == line1_id)
    assert line1_after["status"] == "COMPLETED"

    bad_outcome = client.post(f"/api/cca/lines-of-therapy/{line1_id}/complete", headers=onc_headers, json={"outcome": "Fine I guess"})
    assert bad_outcome.status_code == 422

    completed = client.post(f"/api/cca/lines-of-therapy/{line1_id}/complete", headers=onc_headers, json={"outcome": "Progressed"})
    assert completed.status_code == 200
    assert completed.json()["line_of_therapy"]["status"] == "DISCONTINUED"


def test_link_treatment_plan_to_episode_retroactively(client, onc_headers, patient):
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={
        "patient_id": patient.id, "intent": "Curative", "protocol_name": "AC-T",
    }).json()["treatment_plan"]["id"]
    assert client.get(f"/api/cca/treatment-plans/{plan_id}", headers=onc_headers).json()["treatment_plan"]["episode_id"] is None

    episode_id = client.post(f"/api/cca/patients/{patient.id}/cancer-episodes", headers=onc_headers, json={
        "primary_site": "Left Breast",
    }).json()["episode"]["id"]
    line_id = client.post(f"/api/cca/cancer-episodes/{episode_id}/lines-of-therapy", headers=onc_headers, json={
        "setting": "Adjuvant", "regimen_summary": "AC-T",
    }).json()["line_of_therapy"]["id"]

    wrong_episode = client.post(f"/api/cca/treatment-plans/{plan_id}/link-episode", headers=onc_headers, json={"episode_id": 999999})
    assert wrong_episode.status_code == 422

    linked = client.post(f"/api/cca/treatment-plans/{plan_id}/link-episode", headers=onc_headers, json={
        "episode_id": episode_id, "line_of_therapy_id": line_id,
    })
    assert linked.status_code == 200, linked.text
    assert linked.json()["treatment_plan"]["episode_id"] == episode_id
    assert linked.json()["treatment_plan"]["line_of_therapy_id"] == line_id
