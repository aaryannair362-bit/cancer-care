"""
Final gap-closing round, item 10: Surgical Oncology EXPAND-tier additions -- WHO Surgical
Safety Checklist (Sign-In/Time-Out/Sign-Out), Wound Assessment, Drain Register, Stoma
Register, and structured post-op Complication tracking (previously only a free-text field
on the operative note).

Never a computed pass/fail or severity score -- checklist items are the team's own
attestation, and clavien_dindo_grade is always the surgeon's own classification.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def surgeon(make_user):
    return make_user(email="surgeon@surgical-test.com", role="CCASurgicalOncologist")


@pytest.fixture
def surgeon_headers(auth_headers, surgeon):
    return auth_headers(surgeon)


@pytest.fixture
def patient(db_session, surgeon):
    p = CCAPatient(mrn="SURG-TEST-0001", name="Surgical Oncology Test Patient", age=59, sex="Female", organization_id=surgeon.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def plan_id(client, surgeon_headers, patient):
    return client.post("/api/cca/surgical-plans", headers=surgeon_headers, json={
        "patient_id": patient.id, "procedure": "Modified Radical Mastectomy",
    }).json()["surgical_plan"]["id"]


def test_safety_checklist_phase_ordering(client, surgeon_headers, plan_id):
    bad_phase = client.post(f"/api/cca/surgical-plans/{plan_id}/safety-checklist/bogus", headers=surgeon_headers, json={})
    assert bad_phase.status_code == 422

    out_of_order = client.post(f"/api/cca/surgical-plans/{plan_id}/safety-checklist/time_out", headers=surgeon_headers, json={
        "items": {"site_marked": True},
    })
    assert out_of_order.status_code == 409

    sign_in = client.post(f"/api/cca/surgical-plans/{plan_id}/safety-checklist/sign_in", headers=surgeon_headers, json={
        "items": {"patient_identity_confirmed": True, "site_marked": True, "anaesthesia_safety_check_complete": True},
    })
    assert sign_in.status_code == 200, sign_in.text

    duplicate = client.post(f"/api/cca/surgical-plans/{plan_id}/safety-checklist/sign_in", headers=surgeon_headers, json={"items": {}})
    assert duplicate.status_code == 409

    time_out = client.post(f"/api/cca/surgical-plans/{plan_id}/safety-checklist/time_out", headers=surgeon_headers, json={
        "items": {"team_introductions_done": True, "procedure_confirmed": True},
    })
    assert time_out.status_code == 200, time_out.text

    sign_out = client.post(f"/api/cca/surgical-plans/{plan_id}/safety-checklist/sign_out", headers=surgeon_headers, json={
        "items": {"instrument_count_correct": True, "specimen_labelled": True},
    })
    assert sign_out.status_code == 200, sign_out.text

    fetched = client.get(f"/api/cca/surgical-plans/{plan_id}/safety-checklist", headers=surgeon_headers)
    assert fetched.status_code == 200
    body = fetched.json()["safety_checklist"]
    assert body["sign_in_at"] and body["time_out_at"] and body["sign_out_at"]


def test_wound_assessment(client, surgeon_headers, plan_id):
    entry = client.post(f"/api/cca/surgical-plans/{plan_id}/wound-assessments", headers=surgeon_headers, json={
        "wound_site": "Left chest wall incision", "appearance": "Clean, dry, intact", "dressing_changed": True,
    })
    assert entry.status_code == 201, entry.text

    listed = client.get(f"/api/cca/surgical-plans/{plan_id}/wound-assessments", headers=surgeon_headers)
    assert listed.status_code == 200
    assert len(listed.json()["wound_assessments"]) == 1


def test_drain_register_lifecycle(client, surgeon_headers, plan_id):
    missing_site = client.post(f"/api/cca/surgical-plans/{plan_id}/drains", headers=surgeon_headers, json={})
    assert missing_site.status_code == 422

    drain = client.post(f"/api/cca/surgical-plans/{plan_id}/drains", headers=surgeon_headers, json={
        "drain_site": "Axillary", "drain_type": "Jackson-Pratt",
    })
    assert drain.status_code == 201, drain.text
    drain_id = drain.json()["drain"]["id"]

    missing_volume = client.post(f"/api/cca/drains/{drain_id}/output", headers=surgeon_headers, json={})
    assert missing_volume.status_code == 422

    output1 = client.post(f"/api/cca/drains/{drain_id}/output", headers=surgeon_headers, json={"volume_ml": 45, "character": "Serosanguinous"})
    assert output1.status_code == 200, output1.text
    assert len(output1.json()["drain"]["output_log"]) == 1

    client.post(f"/api/cca/drains/{drain_id}/output", headers=surgeon_headers, json={"volume_ml": 20})
    removed = client.post(f"/api/cca/drains/{drain_id}/remove", headers=surgeon_headers, json={})
    assert removed.status_code == 200, removed.text
    assert removed.json()["drain"]["status"] == "Removed"
    assert len(removed.json()["drain"]["output_log"]) == 2

    already_removed = client.post(f"/api/cca/drains/{drain_id}/remove", headers=surgeon_headers, json={})
    assert already_removed.status_code == 409


def test_stoma_register(client, surgeon_headers, plan_id):
    missing_type = client.post(f"/api/cca/surgical-plans/{plan_id}/stomas", headers=surgeon_headers, json={})
    assert missing_type.status_code == 422

    stoma = client.post(f"/api/cca/surgical-plans/{plan_id}/stomas", headers=surgeon_headers, json={
        "stoma_type": "Colostomy", "site": "Left lower quadrant", "education_provided": True,
    })
    assert stoma.status_code == 201, stoma.text

    listed = client.get(f"/api/cca/surgical-plans/{plan_id}/stomas", headers=surgeon_headers)
    assert listed.status_code == 200
    assert listed.json()["stomas"][0]["education_provided"] is True


def test_post_op_complication_tracking(client, surgeon_headers, plan_id):
    complication = client.post(f"/api/cca/surgical-plans/{plan_id}/complications", headers=surgeon_headers, json={
        "complication": "Surgical site infection", "clavien_dindo_grade": "II", "management": "Oral antibiotics started.",
    })
    assert complication.status_code == 201, complication.text
    complication_id = complication.json()["complication"]["id"]

    listed = client.get(f"/api/cca/surgical-plans/{plan_id}/complications", headers=surgeon_headers)
    assert listed.status_code == 200
    assert listed.json()["complications"][0]["resolved"] is False

    resolved = client.post(f"/api/cca/complications/{complication_id}/resolve", headers=surgeon_headers, json={})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["complication"]["resolved"] is True
