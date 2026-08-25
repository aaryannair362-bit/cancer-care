"""
Prescription Receipt (Pharmacy Workflow): GET /api/pharmacy/pending-prescriptions surfaces
recent consultations' medication orders as a dispensing queue, cross-referenced against what's
already been dispensed for that consultation.
"""
import pytest


@pytest.fixture
def doctor(make_user):
    return make_user(email="doctor@pendingrx.com", role="Doctor")


@pytest.fixture
def pharmacist(make_user, doctor):
    return make_user(email="pharmacist@pendingrx.com", role="Pharmacist", organization_id=doctor.organization_id)


def _create_consultation(client, headers, **overrides):
    payload = {"chief_complaint": "Fever", "medications": [{"drugName": "Amoxicillin", "dose": "500mg"}]}
    payload.update(overrides)
    resp = client.post("/api/consultations", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_pending_prescription_shown_and_flagged_not_in_formulary(client, doctor, pharmacist, auth_headers):
    # drug_matcher.correct_medication_names may rewrite "Amoxicillin" to its canonical
    # reference-dataset form/strength -- read back whatever was actually persisted rather than
    # assuming the name survives unchanged, matching how a real pharmacist would see it.
    created = _create_consultation(client, auth_headers(doctor))
    persisted_name = created["medications"][0]["drugName"]

    resp = client.get("/api/pharmacy/pending-prescriptions", headers=auth_headers(pharmacist))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    line = rows[0]["medications"][0]
    assert line["drug_name"] == persisted_name
    assert line["in_formulary"] is False
    assert line["status"] == "Pending"


def test_pending_prescription_matches_formulary_by_name(client, doctor, pharmacist, auth_headers):
    created = _create_consultation(client, auth_headers(doctor))
    persisted_name = created["medications"][0]["drugName"]
    client.post("/api/pharmacy/drugs", json={"name": persisted_name, "unit_price": 3.0}, headers=auth_headers(pharmacist))

    rows = client.get("/api/pharmacy/pending-prescriptions", headers=auth_headers(pharmacist)).json()
    line = rows[0]["medications"][0]
    assert line["in_formulary"] is True
    assert line["drug_id"] is not None
    assert line["status"] == "Pending"


def test_fully_dispensed_consultation_dropped_from_pending_list(client, doctor, pharmacist, auth_headers):
    from datetime import date, timedelta

    consultation = _create_consultation(client, auth_headers(doctor))
    persisted_name = consultation["medications"][0]["drugName"]
    drug = client.post("/api/pharmacy/drugs", json={"name": persisted_name, "unit_price": 3.0}, headers=auth_headers(pharmacist)).json()
    client.post(f"/api/pharmacy/drugs/{drug['id']}/batches", json={
        "received_quantity": 100, "expiry_date": (date.today() + timedelta(days=90)).isoformat(),
    }, headers=auth_headers(pharmacist))

    resp = client.post("/api/pharmacy/dispense", json={
        "drug_id": drug["id"], "quantity": 10, "consultation_id": consultation["id"],
    }, headers=auth_headers(pharmacist))
    assert resp.status_code == 201, resp.text

    rows = client.get("/api/pharmacy/pending-prescriptions", headers=auth_headers(pharmacist)).json()
    assert consultation["id"] not in [r["consultation_id"] for r in rows]


def test_discontinued_medication_excluded(client, doctor, pharmacist, auth_headers):
    _create_consultation(client, auth_headers(doctor), medications=[{"drugName": "OldDrug", "discontinued": True}])
    rows = client.get("/api/pharmacy/pending-prescriptions", headers=auth_headers(pharmacist)).json()
    assert rows == []


def test_other_roles_cannot_access_pending_prescriptions(client, doctor, auth_headers):
    resp = client.get("/api/pharmacy/pending-prescriptions", headers=auth_headers(doctor))
    assert resp.status_code == 403


def test_pending_prescriptions_scoped_to_organization(client, doctor, pharmacist, auth_headers, make_user):
    _create_consultation(client, auth_headers(doctor))

    other_doctor = make_user(email="doctor@otherorg-pendingrx.com", role="Doctor")
    other_pharmacist = make_user(email="pharmacist@otherorg-pendingrx.com", role="Pharmacist", organization_id=other_doctor.organization_id)
    _create_consultation(client, auth_headers(other_doctor))

    rows = client.get("/api/pharmacy/pending-prescriptions", headers=auth_headers(other_pharmacist)).json()
    assert len(rows) == 1
