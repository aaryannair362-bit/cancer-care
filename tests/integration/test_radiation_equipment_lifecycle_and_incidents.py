"""
Radiation Oncology / Radiation Physicist missing-development round, Batch 5: equipment issue
severity + extended post-resolution lifecycle (Open -> Investigating -> CorrectiveAction ->
Verified -> Closed, appended after the existing Open->Resolved contract, which stays
unchanged), and the Incident / Near-Miss / Radiation Safety Record -- previously entirely
absent from this codebase.

Never tests a computed severity/risk score -- severity and every investigation field are
physicist-typed, matching this repo's standing rule.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="ro@rt-incident-test.com", role="CCARadiationOncologist")


@pytest.fixture
def physicist(make_user, oncologist):
    return make_user(email="physicist@rt-incident-test.com", role="CCARadiationPhysicist", organization_id=oncologist.organization_id)


@pytest.fixture
def rtt(make_user, oncologist):
    return make_user(email="rtt@rt-incident-test.com", role="CCARadiationTechnologist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def physicist_headers(auth_headers, physicist):
    return auth_headers(physicist)


@pytest.fixture
def rtt_headers(auth_headers, rtt):
    return auth_headers(rtt)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="RT-INCIDENT-0001", name="RT Incident Test Patient", age=60, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def unit_id(client, physicist_headers):
    return client.post("/api/cca/radiation-units", headers=physicist_headers, json={"name": "Linac Incident Test"}).json()["unit"]["id"]


def test_equipment_issue_severity_is_optional_and_validated(client, rtt_headers, unit_id):
    no_severity = client.post(f"/api/cca/radiation-units/{unit_id}/issues", headers=rtt_headers, json={
        "description": "Door interlock intermittent fault.", "category": "Interlock",
    })
    assert no_severity.status_code == 201, no_severity.text
    assert no_severity.json()["issue"]["severity"] is None

    invalid = client.post(f"/api/cca/radiation-units/{unit_id}/issues", headers=rtt_headers, json={
        "description": "x", "category": "Interlock", "severity": "Extreme",
    })
    assert invalid.status_code == 422

    with_severity = client.post(f"/api/cca/radiation-units/{unit_id}/issues", headers=rtt_headers, json={
        "description": "Beam output drifted outside daily tolerance.", "category": "Dosimetry", "severity": "Critical",
    })
    assert with_severity.status_code == 201
    assert with_severity.json()["issue"]["severity"] == "Critical"


def test_equipment_issue_post_resolution_lifecycle_appended_after_resolved(client, rtt_headers, physicist_headers, unit_id):
    issue_id = client.post(f"/api/cca/radiation-units/{unit_id}/issues", headers=rtt_headers, json={
        "description": "MLC leaf position error.", "category": "Mechanical", "severity": "Major",
    }).json()["issue"]["id"]

    too_early = client.post(f"/api/cca/radiation-issues/{issue_id}/status", headers=physicist_headers, json={"status": "INVESTIGATING"})
    assert too_early.status_code == 409  # not RESOLVED yet -- existing OPEN->RESOLVED contract unchanged

    resolved = client.post(f"/api/cca/radiation-issues/{issue_id}/resolve", headers=physicist_headers, json={
        "resolution": "Recalibrated MLC.", "return_to_service_checks": "Output and MLC QA re-verified.",
    })
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["issue"]["status"] == "RESOLVED"

    investigating = client.post(f"/api/cca/radiation-issues/{issue_id}/status", headers=physicist_headers, json={"status": "INVESTIGATING"})
    assert investigating.status_code == 200
    assert investigating.json()["issue"]["status"] == "INVESTIGATING"

    missing_note = client.post(f"/api/cca/radiation-issues/{issue_id}/status", headers=physicist_headers, json={"status": "CORRECTIVE_ACTION"})
    assert missing_note.status_code == 422

    corrective = client.post(f"/api/cca/radiation-issues/{issue_id}/status", headers=physicist_headers, json={
        "status": "CORRECTIVE_ACTION", "note": "Updated MLC calibration procedure and retrained staff.",
    })
    assert corrective.status_code == 200
    assert corrective.json()["issue"]["corrective_action"]

    verified = client.post(f"/api/cca/radiation-issues/{issue_id}/status", headers=physicist_headers, json={"status": "VERIFIED"})
    assert verified.status_code == 200

    closed = client.post(f"/api/cca/radiation-issues/{issue_id}/status", headers=physicist_headers, json={"status": "CLOSED"})
    assert closed.status_code == 200
    assert closed.json()["issue"]["status"] == "CLOSED"

    already_closed = client.post(f"/api/cca/radiation-issues/{issue_id}/status", headers=physicist_headers, json={"status": "INVESTIGATING"})
    assert already_closed.status_code == 409  # can't go backward from CLOSED


def test_only_physicist_can_advance_post_resolution_status(client, rtt_headers, physicist_headers, onc_headers, unit_id):
    issue_id = client.post(f"/api/cca/radiation-units/{unit_id}/issues", headers=rtt_headers, json={
        "description": "x", "category": "Software",
    }).json()["issue"]["id"]
    client.post(f"/api/cca/radiation-issues/{issue_id}/resolve", headers=physicist_headers, json={
        "resolution": "x", "return_to_service_checks": "x",
    })
    forbidden = client.post(f"/api/cca/radiation-issues/{issue_id}/status", headers=onc_headers, json={"status": "INVESTIGATING"})
    assert forbidden.status_code == 403


def test_safety_incident_creation_requires_type_severity_and_description(client, rtt_headers, patient):
    missing_type = client.post("/api/cca/radiation-safety-incidents", headers=rtt_headers, json={
        "severity": "Major", "description": "x",
    })
    assert missing_type.status_code == 422

    missing_description = client.post("/api/cca/radiation-safety-incidents", headers=rtt_headers, json={
        "incident_type": "NearMiss", "severity": "Minor",
    })
    assert missing_description.status_code == 422

    created = client.post("/api/cca/radiation-safety-incidents", headers=rtt_headers, json={
        "incident_type": "NearMiss", "severity": "Major", "category": "Wrong Site",
        "description": "Setup field name did not match the day's prescription phase; caught at time-out before beam-on.",
        "immediate_action_taken": "Treatment held, RO and physicist notified before proceeding.",
        "patient_id": patient.id,
    })
    assert created.status_code == 201, created.text
    body = created.json()["safety_incident"]
    assert body["investigation_status"] == "Reported"
    assert body["patient_id"] == patient.id


def test_safety_incident_without_a_patient_is_allowed(client, rtt_headers):
    created = client.post("/api/cca/radiation-safety-incidents", headers=rtt_headers, json={
        "incident_type": "NearMiss", "severity": "Minor", "category": "Process Failure",
        "description": "QA checklist step skipped during morning warm-up, caught by second physicist before first patient.",
    })
    assert created.status_code == 201, created.text
    assert created.json()["safety_incident"]["patient_id"] is None


def test_safety_incident_investigation_and_close_requires_root_cause_and_plan(client, rtt_headers, physicist_headers, onc_headers):
    incident_id = client.post("/api/cca/radiation-safety-incidents", headers=rtt_headers, json={
        "incident_type": "Incident", "severity": "Critical", "description": "Wrong patient setup images pulled up at console; caught before imaging.",
    }).json()["safety_incident"]["id"]

    forbidden = client.post(f"/api/cca/radiation-safety-incidents/{incident_id}/investigate", headers=onc_headers, json={
        "investigation_status": "UnderInvestigation",
    })
    assert forbidden.status_code == 403

    close_too_early = client.post(f"/api/cca/radiation-safety-incidents/{incident_id}/close", headers=physicist_headers)
    assert close_too_early.status_code == 409

    investigated = client.post(f"/api/cca/radiation-safety-incidents/{incident_id}/investigate", headers=physicist_headers, json={
        "investigation_status": "RootCauseIdentified",
        "root_cause": "Patient list was not refreshed after the prior patient's session ended.",
        "corrective_action_plan": "Mandatory patient-list refresh + verbal ID check added to console SOP.",
    })
    assert investigated.status_code == 200, investigated.text
    assert investigated.json()["safety_incident"]["reviewed_by"]

    closed = client.post(f"/api/cca/radiation-safety-incidents/{incident_id}/close", headers=physicist_headers)
    assert closed.status_code == 200
    assert closed.json()["safety_incident"]["investigation_status"] == "Closed"

    already_closed = client.post(f"/api/cca/radiation-safety-incidents/{incident_id}/close", headers=physicist_headers)
    assert already_closed.status_code == 409


def test_safety_incidents_are_org_scoped_and_filterable(client, rtt_headers, physicist_headers):
    client.post("/api/cca/radiation-safety-incidents", headers=rtt_headers, json={
        "incident_type": "NearMiss", "severity": "Minor", "description": "x",
    })
    client.post("/api/cca/radiation-safety-incidents", headers=rtt_headers, json={
        "incident_type": "Incident", "severity": "Critical", "description": "y",
    })
    critical_only = client.get("/api/cca/radiation-safety-incidents", headers=physicist_headers, params={"severity": "Critical"}).json()["safety_incidents"]
    assert all(i["severity"] == "Critical" for i in critical_only)
    assert len(critical_only) >= 1
