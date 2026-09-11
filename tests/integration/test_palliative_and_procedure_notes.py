"""
Oncology Review Results PDF items 30-33: Palliative Treatment Orders, and Procedures & Notes
for the Palliative Care, Medical Oncology, and Radiation Oncology specialties
(backend/app/routers/cca_oncology_ext.py's ClinicalProcedureNote/PalliativeTreatmentOrder).
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def palliative(make_user):
    return make_user(email="palliative@palliative-test.com", role="CCAPalliativeCareSpecialist")


@pytest.fixture
def med_onc(make_user, palliative):
    return make_user(email="medonc@palliative-test.com", role="CCAMedicalOncologist", organization_id=palliative.organization_id)


@pytest.fixture
def rad_onc(make_user, palliative):
    return make_user(email="radonc@palliative-test.com", role="CCARadiationOncologist", organization_id=palliative.organization_id)


@pytest.fixture
def surgeon(make_user, palliative):
    return make_user(email="surgeon@palliative-test.com", role="CCASurgicalOncologist", organization_id=palliative.organization_id)


@pytest.fixture
def palliative_headers(auth_headers, palliative):
    return auth_headers(palliative)


@pytest.fixture
def med_onc_headers(auth_headers, med_onc):
    return auth_headers(med_onc)


@pytest.fixture
def rad_onc_headers(auth_headers, rad_onc):
    return auth_headers(rad_onc)


@pytest.fixture
def surgeon_headers(auth_headers, surgeon):
    return auth_headers(surgeon)


@pytest.fixture
def patient(db_session, palliative):
    p = CCAPatient(
        mrn="PALLIATIVE-0001", name="Palliative Test Patient", age=71, sex="Male",
        organization_id=palliative.organization_id, journey_state="Palliative Care",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


# ---------------------------------------------------------------------------
# Items 31-33: Procedures & Notes (Palliative, Medical Oncology, Radiation Oncology)
# ---------------------------------------------------------------------------

def test_each_of_the_three_specialties_can_record_its_own_procedure_note(client, palliative_headers, med_onc_headers, rad_onc_headers, patient):
    for headers, expected_role in ((palliative_headers, "CCAPalliativeCareSpecialist"), (med_onc_headers, "CCAMedicalOncologist"), (rad_onc_headers, "CCARadiationOncologist")):
        recorded = client.post(f"/api/cca/patients/{patient.id}/procedure-notes", headers=headers, json={
            "procedure_name": "Bone marrow biopsy", "indication": "Cytopenia workup",
        })
        assert recorded.status_code == 201, recorded.text
        assert recorded.json()["procedure_note"]["performed_by_role"] == expected_role

    listed = client.get(f"/api/cca/patients/{patient.id}/procedure-notes", headers=palliative_headers)
    assert len(listed.json()["results"]) == 3


def test_surgical_oncologist_cannot_record_a_procedure_note(client, surgeon_headers, patient):
    """Surgical procedures go through SurgicalOperativeNote/SurgicalPlan instead -- the
    Surgical Oncologist is deliberately not one of the three roles this item set covers."""
    denied = client.post(f"/api/cca/patients/{patient.id}/procedure-notes", headers=surgeon_headers, json={
        "procedure_name": "Central line placement",
    })
    assert denied.status_code == 403


def test_procedure_note_requires_procedure_name(client, palliative_headers, patient):
    missing = client.post(f"/api/cca/patients/{patient.id}/procedure-notes", headers=palliative_headers, json={})
    assert missing.status_code == 422


# ---------------------------------------------------------------------------
# Item 30: Palliative Treatment Orders
# ---------------------------------------------------------------------------

def _create_order(client, palliative_headers, patient_id, order_type="Pain Management"):
    created = client.post(f"/api/cca/patients/{patient_id}/palliative-orders", headers=palliative_headers, json={
        "order_type": order_type, "instructions": "Titrate per patient-reported pain scale, reassess in 24h.",
    })
    assert created.status_code == 201, created.text
    return created.json()["palliative_order"]["id"]


def test_only_palliative_specialist_can_create_order(client, med_onc_headers, patient):
    denied = client.post(f"/api/cca/patients/{patient.id}/palliative-orders", headers=med_onc_headers, json={
        "order_type": "Pain Management", "instructions": "Some instructions.",
    })
    assert denied.status_code == 403


def test_order_requires_type_and_instructions(client, palliative_headers, patient):
    missing = client.post(f"/api/cca/patients/{patient.id}/palliative-orders", headers=palliative_headers, json={
        "order_type": "Pain Management",
    })
    assert missing.status_code == 422


def test_order_lifecycle_must_be_sequential(client, palliative_headers, patient):
    order_id = _create_order(client, palliative_headers, patient.id)

    skip_ahead = client.patch(f"/api/cca/palliative-orders/{order_id}", headers=palliative_headers, json={"status": "Active"})
    assert skip_ahead.status_code == 409

    signed = client.patch(f"/api/cca/palliative-orders/{order_id}", headers=palliative_headers, json={"status": "Signed"})
    assert signed.status_code == 200, signed.text
    assert signed.json()["palliative_order"]["signer_email"] == "palliative@palliative-test.com"

    active = client.patch(f"/api/cca/palliative-orders/{order_id}", headers=palliative_headers, json={"status": "Active"})
    assert active.status_code == 200, active.text


def test_discontinue_requires_reason_and_only_from_signed_or_active(client, palliative_headers, patient):
    order_id = _create_order(client, palliative_headers, patient.id)

    too_early = client.patch(f"/api/cca/palliative-orders/{order_id}", headers=palliative_headers, json={
        "status": "Discontinued", "discontinued_reason": "No longer needed.",
    })
    assert too_early.status_code == 409  # still Draft

    client.patch(f"/api/cca/palliative-orders/{order_id}", headers=palliative_headers, json={"status": "Signed"})

    missing_reason = client.patch(f"/api/cca/palliative-orders/{order_id}", headers=palliative_headers, json={"status": "Discontinued"})
    assert missing_reason.status_code == 422

    discontinued = client.patch(f"/api/cca/palliative-orders/{order_id}", headers=palliative_headers, json={
        "status": "Discontinued", "discontinued_reason": "Patient transitioned to hospice care.",
    })
    assert discontinued.status_code == 200, discontinued.text
    assert discontinued.json()["palliative_order"]["status"] == "Discontinued"


def test_lists_multiple_orders_for_a_patient(client, palliative_headers, patient):
    _create_order(client, palliative_headers, patient.id, "Pain Management")
    _create_order(client, palliative_headers, patient.id, "Symptom Control")

    listed = client.get(f"/api/cca/patients/{patient.id}/palliative-orders", headers=palliative_headers)
    assert len(listed.json()["results"]) == 2
