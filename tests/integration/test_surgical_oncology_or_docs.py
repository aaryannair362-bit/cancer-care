"""
Oncology Review Results PDF items 24-27: Intra-operative Monitoring, Intra-operative Notes,
Specimen Labelling and Lab Handoff, and Surgical Blood Transfusion Record. All keyed off an
existing SurgicalPlan (backend/app/routers/cca_oncology_ext.py), gated to the Surgical
Oncologist or the new Surgical Nurse role.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def surgeon(make_user):
    return make_user(email="surgeon@surgical-or-docs-test.com", role="CCASurgicalOncologist")


@pytest.fixture
def surgical_nurse(make_user, surgeon):
    return make_user(email="ornurse@surgical-or-docs-test.com", role="CCASurgicalNurse", organization_id=surgeon.organization_id)


@pytest.fixture
def infusion_nurse(make_user, surgeon):
    return make_user(email="infusionnurse@surgical-or-docs-test.com", role="CCAInfusionNurse", organization_id=surgeon.organization_id)


@pytest.fixture
def surgeon_headers(auth_headers, surgeon):
    return auth_headers(surgeon)


@pytest.fixture
def or_nurse_headers(auth_headers, surgical_nurse):
    return auth_headers(surgical_nurse)


@pytest.fixture
def infusion_nurse_headers(auth_headers, infusion_nurse):
    return auth_headers(infusion_nurse)


@pytest.fixture
def patient(db_session, surgeon):
    p = CCAPatient(
        mrn="SURG-OR-0001", name="Surgical OR Docs Test Patient", age=57, sex="Male",
        organization_id=surgeon.organization_id, journey_state="Surgical Oncology",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_and_schedule_plan(client, surgeon_headers, patient_id):
    """Drives the surgeon's own unmodified /api/cca/surgical-plans endpoints (already existed,
    but previously had zero frontend/test coverage) from recommended through scheduled."""
    created = client.post("/api/cca/surgical-plans", headers=surgeon_headers, json={
        "patient_id": patient_id, "procedure": "Modified radical mastectomy", "intent": "Curative",
        "anatomical_site": "Left breast",
    })
    assert created.status_code == 201, created.text
    plan_id = created.json()["surgical_plan"]["id"]

    for target in ("surgeon_reviewed", "planned", "pre_op_ready", "scheduled"):
        moved = client.patch(f"/api/cca/surgical-plans/{plan_id}", headers=surgeon_headers, json={"status": target})
        assert moved.status_code == 200, moved.text
    return plan_id


def test_surgical_plan_lifecycle_smoke(client, surgeon_headers, patient):
    """The SurgicalPlan backend existed with zero test coverage before this phase -- a basic
    smoke test that the pre-existing lifecycle still works end-to-end."""
    plan_id = _create_and_schedule_plan(client, surgeon_headers, patient.id)
    listed = client.get(f"/api/cca/patients/{patient.id}/surgical-plans", headers=surgeon_headers)
    assert listed.status_code == 200
    assert listed.json()["surgical_plans"][0]["id"] == plan_id
    assert listed.json()["surgical_plans"][0]["status"] == "scheduled"


# ---------------------------------------------------------------------------
# Item 24: Intra-operative Monitoring
# ---------------------------------------------------------------------------

def test_intraop_monitoring_recorded_by_surgeon_and_or_nurse(client, surgeon_headers, or_nurse_headers, patient):
    plan_id = _create_and_schedule_plan(client, surgeon_headers, patient.id)

    by_surgeon = client.post(f"/api/cca/surgical-plans/{plan_id}/intraop-monitoring", headers=surgeon_headers, json={
        "vitals": {"bp": "118/76", "pulse": "78", "spo2": "98%"}, "anaesthesia_status": "General, stable",
    })
    assert by_surgeon.status_code == 201, by_surgeon.text

    by_nurse = client.post(f"/api/cca/surgical-plans/{plan_id}/intraop-monitoring", headers=or_nurse_headers, json={
        "vitals": {"bp": "116/74"}, "blood_loss_estimate": "150mL", "events_complications": "None.",
    })
    assert by_nurse.status_code == 201, by_nurse.text

    listed = client.get(f"/api/cca/surgical-plans/{plan_id}/intraop-monitoring", headers=surgeon_headers)
    assert len(listed.json()["results"]) == 2


def test_infusion_nurse_cannot_write_or_documentation(client, surgeon_headers, infusion_nurse_headers, patient):
    """The Day Care Infusion Nurse is a different care setting -- must not be able to write OR
    documentation (see _require_surgical_team's docstring)."""
    plan_id = _create_and_schedule_plan(client, surgeon_headers, patient.id)
    denied = client.post(f"/api/cca/surgical-plans/{plan_id}/intraop-monitoring", headers=infusion_nurse_headers, json={
        "vitals": {"bp": "118/76"},
    })
    assert denied.status_code == 403


# ---------------------------------------------------------------------------
# Item 25: Intra-operative Notes
# ---------------------------------------------------------------------------

def test_operative_note_requires_procedure_performed_and_never_overwrites_plan_field(client, surgeon_headers, patient):
    plan_id = _create_and_schedule_plan(client, surgeon_headers, patient.id)

    missing = client.post(f"/api/cca/surgical-plans/{plan_id}/operative-notes", headers=surgeon_headers, json={
        "findings": "Tumour excised with clear margins.",
    })
    assert missing.status_code == 422

    note = client.post(f"/api/cca/surgical-plans/{plan_id}/operative-notes", headers=surgeon_headers, json={
        "procedure_performed": "Modified radical mastectomy with axillary clearance.",
        "findings": "Tumour excised with clear margins.", "surgeon": "Dr. A. Verma",
        "estimated_blood_loss": "200mL",
    })
    assert note.status_code == 201, note.text
    assert note.json()["operative_note"]["procedure_performed"] == "Modified radical mastectomy with axillary clearance."

    plan = client.get(f"/api/cca/patients/{patient.id}/surgical-plans", headers=surgeon_headers).json()["surgical_plans"][0]
    assert plan["performed_procedure"] is None  # untouched by the operative note


# ---------------------------------------------------------------------------
# Item 26: Specimen Labelling and Lab Handoff
# ---------------------------------------------------------------------------

def test_specimen_chain_of_custody_progression(client, surgeon_headers, or_nurse_headers, patient):
    plan_id = _create_and_schedule_plan(client, surgeon_headers, patient.id)

    added = client.post(f"/api/cca/surgical-plans/{plan_id}/specimens", headers=or_nurse_headers, json={
        "specimen_label": "Left breast mass", "specimen_type": "Excisional biopsy", "container_type": "Formalin jar",
    })
    assert added.status_code == 201, added.text
    specimen_id = added.json()["specimen"]["id"]
    assert added.json()["specimen"]["status"] == "Collected"
    assert added.json()["specimen"]["collected_by"] == "ornurse@surgical-or-docs-test.com"

    skip_ahead = client.post(f"/api/cca/specimens/{specimen_id}/event", headers=or_nurse_headers, json={
        "status": "ReceivedByLab", "received_by_lab": "Lab Tech R. Nair",
    })
    assert skip_ahead.status_code == 409  # cannot skip HandedOff

    missing_recipient = client.post(f"/api/cca/specimens/{specimen_id}/event", headers=or_nurse_headers, json={"status": "HandedOff"})
    assert missing_recipient.status_code == 422

    handed_off = client.post(f"/api/cca/specimens/{specimen_id}/event", headers=or_nurse_headers, json={
        "status": "HandedOff", "handed_off_to": "Porter K. Singh",
    })
    assert handed_off.status_code == 200, handed_off.text
    assert handed_off.json()["specimen"]["status"] == "HandedOff"

    received = client.post(f"/api/cca/specimens/{specimen_id}/event", headers=or_nurse_headers, json={
        "status": "ReceivedByLab", "received_by_lab": "Lab Tech R. Nair", "lab_accession_number": "PATH-2026-0042",
    })
    assert received.status_code == 200, received.text
    assert received.json()["specimen"]["status"] == "ReceivedByLab"
    assert received.json()["specimen"]["lab_accession_number"] == "PATH-2026-0042"


def test_specimen_requires_label(client, surgeon_headers, patient):
    plan_id = _create_and_schedule_plan(client, surgeon_headers, patient.id)
    missing = client.post(f"/api/cca/surgical-plans/{plan_id}/specimens", headers=surgeon_headers, json={})
    assert missing.status_code == 422


# ---------------------------------------------------------------------------
# Item 27: Surgical Blood Transfusion Record
# ---------------------------------------------------------------------------

def test_surgical_blood_transfusion_requires_product_type_and_unit_id(client, surgeon_headers, patient):
    plan_id = _create_and_schedule_plan(client, surgeon_headers, patient.id)
    missing = client.post(f"/api/cca/surgical-plans/{plan_id}/blood-transfusions", headers=surgeon_headers, json={})
    assert missing.status_code == 422


def test_surgical_blood_transfusion_records_and_lists(client, or_nurse_headers, surgeon_headers, patient):
    plan_id = _create_and_schedule_plan(client, surgeon_headers, patient.id)

    recorded = client.post(f"/api/cca/surgical-plans/{plan_id}/blood-transfusions", headers=or_nurse_headers, json={
        "product_type": "PRBC", "unit_id": "UNIT-OR-0001", "blood_group": "A+",
        "crossmatch_confirmed": True, "crossmatch_reference": "XM-OR-12", "volume": "350mL",
        "indication": "Intra-operative blood loss >500mL",
    })
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["blood_transfusion"]["administered_by"] == "ornurse@surgical-or-docs-test.com"

    listed = client.get(f"/api/cca/surgical-plans/{plan_id}/blood-transfusions", headers=surgeon_headers)
    assert len(listed.json()["results"]) == 1
    assert listed.json()["results"][0]["reaction_occurred"] is False


def test_cross_org_surgical_plan_is_not_found(client, surgeon_headers, patient, make_user, auth_headers):
    plan_id = _create_and_schedule_plan(client, surgeon_headers, patient.id)
    other_org_nurse = make_user(email="other-org-ornurse@surgical-or-docs-test.com", role="CCASurgicalNurse")
    other_headers = auth_headers(other_org_nurse)

    res = client.get(f"/api/cca/surgical-plans/{plan_id}/intraop-monitoring", headers=other_headers)
    assert res.status_code == 404
