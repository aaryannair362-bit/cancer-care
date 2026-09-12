"""
Final gap-closing round, item 8: Nurse Navigation per-attempt Contact Log +
active-treatment Education Delivery record. Previously CCACoordinationCase only carried a
rolling single-state snapshot (communication_status/last_contact_at), with no per-attempt
history, and education delivery only existed as a single post-treatment survivorship flag.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def liaison(make_user):
    return make_user(email="liaison@coord-test.com", role="CCAPatientLiaison")


@pytest.fixture
def liaison_headers(auth_headers, liaison):
    return auth_headers(liaison)


@pytest.fixture
def patient(db_session, liaison):
    p = CCAPatient(mrn="COORD-TEST-0001", name="Coordination Test Patient", age=48, sex="Female", organization_id=liaison.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def case_id(client, liaison_headers, patient):
    return client.post("/api/cca/coordination/cases", headers=liaison_headers, json={"patient_id": patient.id}).json()["case"]["id"]


def test_contact_log_and_rolling_status_sync(client, liaison_headers, case_id):
    bad_outcome = client.post(f"/api/cca/coordination/cases/{case_id}/contact-log", headers=liaison_headers, json={"outcome": "Nope"})
    assert bad_outcome.status_code == 422

    attempt1 = client.post(f"/api/cca/coordination/cases/{case_id}/contact-log", headers=liaison_headers, json={
        "contact_method": "Phone", "outcome": "VoicemailLeft", "notes": "Left voicemail, no callback yet.",
    })
    assert attempt1.status_code == 201, attempt1.text

    attempt2 = client.post(f"/api/cca/coordination/cases/{case_id}/contact-log", headers=liaison_headers, json={
        "contact_method": "Phone", "outcome": "Reached", "notes": "Spoke with patient directly.",
    })
    assert attempt2.status_code == 201, attempt2.text

    listed = client.get(f"/api/cca/coordination/cases/{case_id}/contact-log", headers=liaison_headers)
    assert listed.status_code == 200
    assert len(listed.json()["contact_log"]) == 2

    # The rolling snapshot reflects the latest attempt's outcome.
    queue = client.get("/api/cca/coordination/queue", headers=liaison_headers)
    case_row = next(c for c in queue.json()["queue"] if c["id"] == case_id)
    assert case_row["communication_status"] == "Reached"


def test_education_delivery_record(client, liaison_headers, case_id):
    missing_topic = client.post(f"/api/cca/coordination/cases/{case_id}/education-records", headers=liaison_headers, json={})
    assert missing_topic.status_code == 422

    record = client.post(f"/api/cca/coordination/cases/{case_id}/education-records", headers=liaison_headers, json={
        "topic": "Chemotherapy side effects and when to call", "material_used": "Chemo Education Booklet v3",
        "language": "English", "comprehension_teach_back": "Confirmed",
    })
    assert record.status_code == 201, record.text

    listed = client.get(f"/api/cca/coordination/cases/{case_id}/education-records", headers=liaison_headers)
    assert listed.status_code == 200
    assert len(listed.json()["education_records"]) == 1
    assert listed.json()["education_records"][0]["comprehension_teach_back"] == "Confirmed"
