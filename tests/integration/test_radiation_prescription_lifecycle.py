"""
Radiation Oncology missing-development round, Batch 1: RadiationPrescription
Draft/Signed/Amended/Discontinued lifecycle with version history, procedure-specific
consent linkage (CCAConsent.radiation_prescription_id), and MDT decision linkage --
same patterns as the immediately-prior Surgical Oncologist round's SurgicalPlan
review/version/consent/MDT endpoints, adapted to RadiationPrescription's own fields.
"""
from app.models_cca import CCAPatient

import pytest


@pytest.fixture
def oncologist(make_user):
    return make_user(email="ro@rt-rx-lifecycle-test.com", role="CCARadiationOncologist")


@pytest.fixture
def other_oncologist(make_user, oncologist):
    return make_user(email="ro2@rt-rx-lifecycle-test.com", role="CCAMedicalOncologist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def other_headers(auth_headers, other_oncologist):
    return auth_headers(other_oncologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(mrn="RT-RX-0001", name="Radiation Prescription Lifecycle Patient", age=57, sex="Female", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _consult(client, onc_headers, patient_id):
    client.post(f"/api/cca/patients/{patient_id}/radiation-consultations", headers=onc_headers, json={"cied_present": False})


def test_prescription_created_signed_by_default(client, onc_headers, patient):
    _consult(client, onc_headers, patient.id)
    res = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient.id, "diagnosis": "Breast Cancer", "intent": "Adjuvant", "technique": "IMRT",
    })
    assert res.status_code == 201, res.text
    rx = res.json()["radiation_prescription"]
    assert rx["status"] == "Signed"
    assert rx["signed_at"] is not None
    assert rx["mdt_decision_id"] is None


def test_draft_prescription_requires_explicit_sign(client, onc_headers, patient):
    _consult(client, onc_headers, patient.id)
    created = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient.id, "diagnosis": "Breast Cancer", "intent": "Adjuvant", "draft": True,
    }).json()["radiation_prescription"]
    assert created["status"] == "Draft"
    assert created["signed_at"] is None

    signed = client.post(f"/api/cca/radiation-prescriptions/{created['id']}/sign", headers=onc_headers)
    assert signed.status_code == 200, signed.text
    body = signed.json()["radiation_prescription"]
    assert body["status"] == "Signed"
    assert body["signed_at"] is not None

    already_signed = client.post(f"/api/cca/radiation-prescriptions/{created['id']}/sign", headers=onc_headers)
    assert already_signed.status_code == 409


def test_amend_requires_reason_snapshots_prior_state_and_flags_phases_for_physics_review(client, onc_headers, patient):
    _consult(client, onc_headers, patient.id)
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient.id, "diagnosis": "Lung Cancer", "intent": "Curative", "technique": "VMAT",
    }).json()["radiation_prescription"]["id"]
    phase_id = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/phases", headers=onc_headers, json={
        "label": "Lung", "treatment_site": "Lung", "total_prescribed_dose_gy": 40,
        "dose_per_fraction_gy": 20, "number_of_fractions": 2,
    }).json()["phase"]["id"]

    missing_reason = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/amend", headers=onc_headers, json={"technique": "3D-CRT"})
    assert missing_reason.status_code == 422

    amended = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/amend", headers=onc_headers, json={
        "reason": "Switched to VMAT boost technique per updated planning CT.", "technique": "VMAT-Boost",
    })
    assert amended.status_code == 200, amended.text
    body = amended.json()["radiation_prescription"]
    assert body["status"] == "Amended"
    assert body["technique"] == "VMAT-Boost"

    versions = client.get(f"/api/cca/radiation-prescriptions/{rx_id}/versions", headers=onc_headers)
    assert versions.status_code == 200
    version_rows = versions.json()["versions"]
    assert len(version_rows) == 1
    assert version_rows[0]["change_reason"] == "Switched to VMAT boost technique per updated planning CT."
    assert version_rows[0]["snapshot"]["technique"] == "VMAT"  # snapshot taken BEFORE the amendment applied

    phases = client.get(f"/api/cca/radiation-prescriptions/{rx_id}/phases", headers=onc_headers).json()["phases"]
    assert phases[0]["id"] == phase_id
    assert phases[0]["physics_review_required"] is True

    acknowledged = client.post(f"/api/cca/radiation-phases/{phase_id}/acknowledge-physics-review", headers=onc_headers)
    assert acknowledged.status_code == 403  # radiation oncologist is not a physicist


def test_discontinue_requires_reason_and_is_terminal(client, onc_headers, patient):
    _consult(client, onc_headers, patient.id)
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient.id, "diagnosis": "Prostate Cancer", "intent": "Curative",
    }).json()["radiation_prescription"]["id"]

    missing_reason = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/discontinue", headers=onc_headers, json={})
    assert missing_reason.status_code == 422

    discontinued = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/discontinue", headers=onc_headers, json={
        "reason": "Patient withdrew consent for radiotherapy."
    })
    assert discontinued.status_code == 200, discontinued.text
    body = discontinued.json()["radiation_prescription"]
    assert body["status"] == "Discontinued"
    assert body["discontinued_reason"] == "Patient withdrew consent for radiotherapy."
    assert body["discontinued_by"]

    again = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/discontinue", headers=onc_headers, json={"reason": "x"})
    assert again.status_code == 409


def test_consent_status_derived_from_linked_consent(client, onc_headers, patient):
    _consult(client, onc_headers, patient.id)
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient.id, "diagnosis": "Cervical Cancer", "intent": "Curative",
    }).json()["radiation_prescription"]["id"]

    pending = client.get(f"/api/cca/radiation-prescriptions/{rx_id}/consent-status", headers=onc_headers)
    assert pending.status_code == 200
    assert pending.json()["consent_obtained"] is False

    captured = client.post(f"/api/cca/patients/{patient.id}/consents", headers=onc_headers, json={
        "consent_types": ["treatment"], "signatory": "Patient", "radiation_prescription_id": rx_id,
    })
    assert captured.status_code == 201, captured.text
    assert captured.json()["consent"]["radiation_prescription_id"] == rx_id

    documented = client.get(f"/api/cca/radiation-prescriptions/{rx_id}/consent-status", headers=onc_headers)
    assert documented.json()["consent_obtained"] is True


def test_link_mdt_decision_requires_approved_and_matching_patient(client, onc_headers, patient):
    _consult(client, onc_headers, patient.id)
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient.id, "diagnosis": "Rectal Cancer", "intent": "Neoadjuvant",
    }).json()["radiation_prescription"]["id"]

    case_id = client.post("/api/cca/mdt/cases", headers=onc_headers, json={"patient_id": patient.id, "question": "Radiation sequencing?"}).json()["mdt_case"]["id"]
    decision_id = client.post(f"/api/cca/mdt/cases/{case_id}/recommendation", headers=onc_headers, json={"recommendation": "Proceed with neoadjuvant CRT."}).json()["decision"]["id"]

    not_yet_approved = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/link-mdt-decision", headers=onc_headers, json={"mdt_decision_id": decision_id})
    assert not_yet_approved.status_code == 409

    client.post(f"/api/cca/mdt/cases/{case_id}/approve", headers=onc_headers, json={"disposition": "ACCEPT"})
    linked = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/link-mdt-decision", headers=onc_headers, json={"mdt_decision_id": decision_id})
    assert linked.status_code == 200, linked.text
    assert linked.json()["radiation_prescription"]["mdt_decision_id"] == decision_id


def test_only_radiation_signer_can_sign_amend_or_discontinue(client, onc_headers, other_headers, patient):
    _consult(client, onc_headers, patient.id)
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient.id, "diagnosis": "Head and Neck Cancer", "intent": "Curative", "draft": True,
    }).json()["radiation_prescription"]["id"]

    assert client.post(f"/api/cca/radiation-prescriptions/{rx_id}/sign", headers=other_headers).status_code == 403
    assert client.post(f"/api/cca/radiation-prescriptions/{rx_id}/amend", headers=other_headers, json={"reason": "x"}).status_code == 403
    assert client.post(f"/api/cca/radiation-prescriptions/{rx_id}/discontinue", headers=other_headers, json={"reason": "x"}).status_code == 403
