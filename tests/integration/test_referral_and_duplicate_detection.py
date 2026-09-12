"""
Final gap-closing round, item 7: Front Desk referral state machine (New -> Accepted /
Rejected / MoreInfo -> Assigned), priority, duplicate-patient detection/merge, and the
live Front Desk Attention panel (previously hardcoded demo text).

Never a fuzzy/scored duplicate match -- exact phone or exact name+dob only, and merging
is a flag-and-link action a human confirms, never an automatic decision.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def front_desk(make_user):
    return make_user(email="fd@referral-test.com", role="CCAFrontDesk")


@pytest.fixture
def fd_headers(auth_headers, front_desk):
    return auth_headers(front_desk)


def test_referral_state_machine(client, fd_headers, front_desk):
    missing_reason = client.post("/api/cca/referrals", headers=fd_headers, json={"patient_name": "John Doe"})
    assert missing_reason.status_code == 422

    created = client.post("/api/cca/referrals", headers=fd_headers, json={
        "patient_name": "John Doe", "referral_reason": "Suspected colon cancer.", "priority": "Urgent",
        "referring_source": "Dr. Smith, City Clinic",
    })
    assert created.status_code == 201, created.text
    referral_id = created.json()["referral"]["id"]
    assert created.json()["referral"]["status"] == "New"

    invalid_transition = client.post(f"/api/cca/referrals/{referral_id}/transition", headers=fd_headers, json={"status": "Assigned"})
    assert invalid_transition.status_code == 409

    missing_reason_for_reject = client.post(f"/api/cca/referrals/{referral_id}/transition", headers=fd_headers, json={"status": "MoreInfo"})
    assert missing_reason_for_reject.status_code == 422

    to_more_info = client.post(f"/api/cca/referrals/{referral_id}/transition", headers=fd_headers, json={
        "status": "MoreInfo", "status_reason": "Missing referral letter.",
    })
    assert to_more_info.status_code == 200, to_more_info.text

    accepted = client.post(f"/api/cca/referrals/{referral_id}/transition", headers=fd_headers, json={"status": "Accepted"})
    assert accepted.status_code == 200, accepted.text

    patient = client.post("/api/cca/patients", headers=fd_headers, json={"name": "John Doe", "age": 55, "phone": "9999999999"}).json()["patient"]

    missing_patient = client.post(f"/api/cca/referrals/{referral_id}/transition", headers=fd_headers, json={"status": "Assigned"})
    assert missing_patient.status_code == 422

    assigned = client.post(f"/api/cca/referrals/{referral_id}/transition", headers=fd_headers, json={
        "status": "Assigned", "patient_id": patient["id"], "assigned_to": "Medical Oncology",
    })
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["referral"]["patient_id"] == patient["id"]

    listed = client.get("/api/cca/referrals?status=Assigned", headers=fd_headers)
    assert listed.status_code == 200
    assert len(listed.json()["referrals"]) == 1


def test_duplicate_detection_and_merge(client, fd_headers, db_session, front_desk):
    p1 = CCAPatient(mrn="DUP-0001", name="Jane Roe", phone="8888888888", organization_id=front_desk.organization_id)
    p2 = CCAPatient(mrn="DUP-0002", name="Jane Roe", phone="8888888888", organization_id=front_desk.organization_id)
    db_session.add_all([p1, p2])
    db_session.commit()
    db_session.refresh(p1)
    db_session.refresh(p2)

    candidates = client.get("/api/cca/patients/duplicate-candidates", headers=fd_headers)
    assert candidates.status_code == 200, candidates.text
    groups = candidates.json()["duplicate_groups"]
    assert any(len(g) == 2 and {p["id"] for p in g} == {p1.id, p2.id} for g in groups)

    self_merge = client.post(f"/api/cca/patients/{p1.id}/merge-duplicate", headers=fd_headers, json={"merge_into_patient_id": p1.id})
    assert self_merge.status_code == 422

    merged = client.post(f"/api/cca/patients/{p2.id}/merge-duplicate", headers=fd_headers, json={"merge_into_patient_id": p1.id})
    assert merged.status_code == 200, merged.text

    candidates_after = client.get("/api/cca/patients/duplicate-candidates", headers=fd_headers)
    assert not any(p2.id in [x["id"] for x in g] for g in candidates_after.json()["duplicate_groups"])


def test_front_desk_attention_reflects_live_data(client, fd_headers, db_session, front_desk):
    client.post("/api/cca/referrals", headers=fd_headers, json={"patient_name": "New Referral", "referral_reason": "x"})
    p1 = CCAPatient(mrn="ATT-0001", name="Attn Test", phone="7777777777", organization_id=front_desk.organization_id)
    p2 = CCAPatient(mrn="ATT-0002", name="Attn Test", phone="7777777777", organization_id=front_desk.organization_id)
    db_session.add_all([p1, p2])
    db_session.commit()

    attention = client.get("/api/cca/front-desk/attention", headers=fd_headers)
    assert attention.status_code == 200, attention.text
    body = attention.json()
    assert body["referrals_awaiting_triage"] >= 1
    assert body["possible_duplicate_patients"] >= 1
    assert body["registrations_missing_mandatory_info"] >= 1
