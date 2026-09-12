"""
Final gap-closing round, item 2 (CRITICAL): MDT Action Tracker + Minutes/Chair Sign-off +
Case Pack. Previously MDTDecision.outstanding_items was an unstructured JSON blob with no
owner/due-date/status/escalation, and there was no minutes/chair sign-off or case-pack view.

Never tests a computed clinical judgment -- status/owner/escalation are always the
clinician's own typed choice; the case pack is pure read-time aggregation.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@mdt-action-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="MDT-ACTION-0001", name="MDT Action Tracker Test Patient", age=49, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def mdt_case_id(client, onc_headers, patient):
    return client.post("/api/cca/mdt/cases", headers=onc_headers, json={
        "patient_id": patient.id, "question": "Neoadjuvant vs upfront surgery?",
    }).json()["mdt_case"]["id"]


def test_action_item_lifecycle(client, onc_headers, mdt_case_id):
    missing_desc = client.post(f"/api/cca/mdt/cases/{mdt_case_id}/action-items", headers=onc_headers, json={})
    assert missing_desc.status_code == 422

    created = client.post(f"/api/cca/mdt/cases/{mdt_case_id}/action-items", headers=onc_headers, json={
        "description": "Obtain additional MRI before final decision.", "owner": "Dr. Radiologist", "priority": "Urgent",
    })
    assert created.status_code == 201, created.text
    item_id = created.json()["action_item"]["id"]
    assert created.json()["action_item"]["status"] == "OPEN"

    listed = client.get(f"/api/cca/mdt/cases/{mdt_case_id}/action-items", headers=onc_headers)
    assert listed.status_code == 200
    assert len(listed.json()["action_items"]) == 1

    completed = client.post(f"/api/cca/mdt/action-items/{item_id}/complete", headers=onc_headers, json={
        "completion_note": "MRI obtained and reviewed.",
    })
    assert completed.status_code == 200, completed.text
    assert completed.json()["action_item"]["status"] == "COMPLETED"

    already_done = client.post(f"/api/cca/mdt/action-items/{item_id}/complete", headers=onc_headers, json={})
    assert already_done.status_code == 409


def test_action_item_escalation(client, onc_headers, mdt_case_id):
    item_id = client.post(f"/api/cca/mdt/cases/{mdt_case_id}/action-items", headers=onc_headers, json={
        "description": "Confirm HER2 status before regimen selection.",
    }).json()["action_item"]["id"]

    missing_target = client.post(f"/api/cca/mdt/action-items/{item_id}/escalate", headers=onc_headers, json={})
    assert missing_target.status_code == 422

    escalated = client.post(f"/api/cca/mdt/action-items/{item_id}/escalate", headers=onc_headers, json={
        "escalated_to": "Department Head", "escalated_reason": "Lab delay beyond SLA.",
    })
    assert escalated.status_code == 200, escalated.text
    assert escalated.json()["action_item"]["status"] == "ESCALATED"


def test_minutes_draft_and_sign(client, onc_headers, mdt_case_id):
    missing_text = client.post(f"/api/cca/mdt/cases/{mdt_case_id}/minutes", headers=onc_headers, json={})
    assert missing_text.status_code == 422

    drafted = client.post(f"/api/cca/mdt/cases/{mdt_case_id}/minutes", headers=onc_headers, json={
        "minutes_text": "Discussed staging and options.", "options_considered": "Upfront surgery vs neoadjuvant -- neoadjuvant chosen for downstaging.",
        "chair_name": "Dr. Chair",
    })
    assert drafted.status_code == 200, drafted.text
    minutes_id = drafted.json()["minutes"]["id"]
    assert drafted.json()["minutes"]["status"] == "DRAFT"

    # Editing in place while still DRAFT updates the same row, not a new one.
    updated = client.post(f"/api/cca/mdt/cases/{mdt_case_id}/minutes", headers=onc_headers, json={
        "minutes_text": "Discussed staging and options (revised).",
    })
    assert updated.json()["minutes"]["id"] == minutes_id

    signed = client.post(f"/api/cca/mdt/minutes/{minutes_id}/sign", headers=onc_headers)
    assert signed.status_code == 200, signed.text
    assert signed.json()["minutes"]["status"] == "SIGNED"

    locked = client.post(f"/api/cca/mdt/cases/{mdt_case_id}/minutes", headers=onc_headers, json={"minutes_text": "Trying to edit after sign-off."})
    assert locked.status_code == 409

    already_signed = client.post(f"/api/cca/mdt/minutes/{minutes_id}/sign", headers=onc_headers)
    assert already_signed.status_code == 409


def test_agenda_position_and_case_pack(client, onc_headers, mdt_case_id):
    set_position = client.post(f"/api/cca/mdt/cases/{mdt_case_id}/agenda-position", headers=onc_headers, json={"agenda_position": 3})
    assert set_position.status_code == 200
    assert set_position.json()["agenda_position"] == 3

    client.post(f"/api/cca/mdt/cases/{mdt_case_id}/action-items", headers=onc_headers, json={"description": "Pending item."})
    client.post(f"/api/cca/mdt/cases/{mdt_case_id}/recommendation", headers=onc_headers, json={"recommendation": "Proceed with neoadjuvant chemotherapy."})

    pack = client.get(f"/api/cca/mdt/cases/{mdt_case_id}/case-pack", headers=onc_headers)
    assert pack.status_code == 200, pack.text
    body = pack.json()
    assert body["case"]["agenda_position"] == 3
    assert body["patient"]["mrn"] == "MDT-ACTION-0001"
    assert body["open_action_item_count"] == 1
    assert body["latest_decision"]["recommendation"] == "Proceed with neoadjuvant chemotherapy."
