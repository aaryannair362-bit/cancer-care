"""
Final gap-closing round, item 17: MDT minute-capture depth -- per-participant quorum
timing (check-in/check-out) and structured dissent recording, plus a read-time quorum view.

Never a computed voting/majority judgment -- quorum sufficiency is left for the chair to
decide; this system only reports who actually checked in.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def coordinator(make_user):
    return make_user(email="coord@mdt-quorum-test.com", role="CCAMDTCoordinator")


@pytest.fixture
def oncologist(make_user, coordinator):
    return make_user(email="onc@mdt-quorum-test.com", role="CCAMedicalOncologist", organization_id=coordinator.organization_id)


@pytest.fixture
def coord_headers(auth_headers, coordinator):
    return auth_headers(coordinator)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def patient(db_session, coordinator):
    p = CCAPatient(mrn="MDT-QUORUM-0001", name="MDT Quorum Test Patient", age=61, sex="Male", organization_id=coordinator.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def case_id(client, onc_headers, patient):
    return client.post("/api/cca/mdt/cases", headers=onc_headers, json={
        "patient_id": patient.id, "question": "Adjuvant therapy plan?",
    }).json()["mdt_case"]["id"]


def test_checkin_checkout_and_quorum(client, coord_headers, case_id):
    p1 = client.post(f"/api/cca/mdt/cases/{case_id}/participants", headers=coord_headers, json={
        "specialist_name": "Dr. Radiologist", "specialist_role": "Radiologist",
    }).json()["participant"]["id"]
    p2 = client.post(f"/api/cca/mdt/cases/{case_id}/participants", headers=coord_headers, json={
        "specialist_name": "Dr. Pathologist", "specialist_role": "Pathologist",
    }).json()["participant"]["id"]

    early_quorum = client.get(f"/api/cca/mdt/cases/{case_id}/quorum", headers=coord_headers)
    assert early_quorum.status_code == 200
    assert early_quorum.json()["checked_in_count"] == 0

    checkout_before_checkin = client.post(f"/api/cca/mdt/participants/{p1}/check-out", headers=coord_headers)
    assert checkout_before_checkin.status_code == 409

    checkin = client.post(f"/api/cca/mdt/participants/{p1}/check-in", headers=coord_headers)
    assert checkin.status_code == 200, checkin.text
    assert checkin.json()["participant"]["attendance_status"] == "Present"
    assert checkin.json()["participant"]["arrived_at"] is not None

    quorum = client.get(f"/api/cca/mdt/cases/{case_id}/quorum", headers=coord_headers)
    assert quorum.json()["invited_count"] == 2
    assert quorum.json()["checked_in_count"] == 1

    checkout = client.post(f"/api/cca/mdt/participants/{p1}/check-out", headers=coord_headers)
    assert checkout.status_code == 200, checkout.text
    assert checkout.json()["participant"]["departed_at"] is not None


def test_dissent_recording(client, coord_headers, onc_headers, case_id):
    participant_id = client.post(f"/api/cca/mdt/cases/{case_id}/participants", headers=coord_headers, json={
        "specialist_name": "Dr. Surgeon", "specialist_role": "Surgical Oncologist",
    }).json()["participant"]["id"]

    missing_opinion = client.post(f"/api/cca/mdt/participants/{participant_id}/dissent", headers=onc_headers, json={})
    assert missing_opinion.status_code == 422

    dissent = client.post(f"/api/cca/mdt/participants/{participant_id}/dissent", headers=onc_headers, json={
        "dissenting_opinion": "Disagrees with upfront surgery; favours neoadjuvant approach.",
    })
    assert dissent.status_code == 200, dissent.text
    assert dissent.json()["participant"]["dissenting_opinion"]

    quorum = client.get(f"/api/cca/mdt/cases/{case_id}/quorum", headers=coord_headers)
    assert quorum.json()["dissenting_count"] == 1
