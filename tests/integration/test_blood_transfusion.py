"""
Oncology Review Results PDF items 28 (Blood Product) and 29 (Post-transfusion Feedback):
BloodProductAdministration lifecycle and TransfusionFeedback recording, on the Infusion
Nurse's own workspace/role (reused, since blood products are administered during the same
oncology day-care visit as chemo). Mirrors test_infusion_nurse_workspace.py's fixture shape.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@blood-transfusion-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def nurse(make_user, oncologist):
    return make_user(email="nurse@blood-transfusion-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def front_desk(make_user, oncologist):
    return make_user(email="frontdesk@blood-transfusion-test.com", role="CCAFrontDesk", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


@pytest.fixture
def front_desk_headers(auth_headers, front_desk):
    return auth_headers(front_desk)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(
        mrn="BLOOD-TX-0001", name="Blood Transfusion Test Patient", age=64, sex="Female",
        organization_id=oncologist.organization_id, journey_state="On Treatment",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_signed_order(client, onc_headers, patient_id):
    draft = client.post("/api/cca/treatment-plans", headers=onc_headers, json={"patient_id": patient_id})
    assert draft.status_code == 200, draft.text
    plan_id = draft.json()["treatment_plan"]["id"]

    signed_plan = client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})
    assert signed_plan.status_code == 200, signed_plan.text

    order_draft = client.post("/api/cca/treatment-orders", headers=onc_headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id,
        "instructions": {"text": "Doxorubicin 60mg/m2 IV, day 1"},
    })
    assert order_draft.status_code == 200, order_draft.text
    order_id = order_draft.json()["treatment_order"]["id"]

    order_signed = client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=onc_headers, json={})
    assert order_signed.status_code == 200, order_signed.text
    return order_id


def _add_blood_product(client, nurse_headers, patient_id, order_id, unit_id="UNIT-0001"):
    added = client.post("/api/cca/treatment/blood-products", headers=nurse_headers, json={
        "patient_id": patient_id, "order_id": order_id, "product_type": "PRBC", "unit_id": unit_id,
        "blood_group": "O+", "crossmatch_confirmed": True, "crossmatch_reference": "XM-778",
        "consent_confirmed": True, "second_verifier_name": "Nurse B. Rao",
    })
    assert added.status_code == 200, added.text
    return added.json()["blood_product"]["id"]


# ---------------------------------------------------------------------------
# Item 28: Blood Product
# ---------------------------------------------------------------------------

def test_add_blood_product_requires_product_type_and_unit_id(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    missing = client.post("/api/cca/treatment/blood-products", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id,
    })
    assert missing.status_code == 422


def test_blood_product_lifecycle_and_illegal_transitions(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _add_blood_product(client, nurse_headers, patient.id, order_id)

    listed = client.get(f"/api/cca/treatment/{order_id}/blood-products?patient_id={patient.id}", headers=nurse_headers)
    assert listed.status_code == 200
    assert listed.json()["results"][0]["status"] == "Pending"
    assert listed.json()["results"][0]["crossmatch_confirmed"] is True

    illegal = client.post(f"/api/cca/treatment/blood-products/{admin_id}/event", headers=nurse_headers, json={"event_type": "COMPLETE"})
    assert illegal.status_code == 409

    start = client.post(f"/api/cca/treatment/blood-products/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert start.status_code == 200, start.text
    assert start.json()["blood_product"]["status"] == "InProgress"
    assert start.json()["blood_product"]["start_time"] is not None

    complete = client.post(f"/api/cca/treatment/blood-products/{admin_id}/event", headers=nurse_headers, json={"event_type": "COMPLETE"})
    assert complete.status_code == 200, complete.text
    assert complete.json()["blood_product"]["status"] == "Completed"
    assert complete.json()["blood_product"]["administered_by"] == "nurse@blood-transfusion-test.com"


def test_blood_product_stop_requires_documented_reason(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _add_blood_product(client, nurse_headers, patient.id, order_id)
    client.post(f"/api/cca/treatment/blood-products/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})

    no_reason = client.post(f"/api/cca/treatment/blood-products/{admin_id}/event", headers=nurse_headers, json={"event_type": "STOP"})
    assert no_reason.status_code == 422

    with_reason = client.post(f"/api/cca/treatment/blood-products/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "STOP", "notes": "Patient reported chills, unit stopped for review.",
    })
    assert with_reason.status_code == 200, with_reason.text
    assert with_reason.json()["blood_product"]["status"] == "Stopped"


def test_front_desk_cannot_write_blood_products(client, front_desk_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    denied = client.post("/api/cca/treatment/blood-products", headers=front_desk_headers, json={
        "patient_id": patient.id, "order_id": order_id, "product_type": "PRBC", "unit_id": "UNIT-9999",
    })
    assert denied.status_code == 403


# ---------------------------------------------------------------------------
# Item 29: Post-transfusion Feedback
# ---------------------------------------------------------------------------

def test_transfusion_feedback_with_no_reaction(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _add_blood_product(client, nurse_headers, patient.id, order_id)

    feedback = client.post("/api/cca/treatment/transfusion-feedback", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "blood_product_id": admin_id,
        "outcome": "Transfusion completed, patient stable.",
    })
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["feedback"]["reaction_occurred"] is False
    assert feedback.json()["feedback"]["blood_product_id"] == admin_id

    listed = client.get(f"/api/cca/treatment/{order_id}/transfusion-feedback?patient_id={patient.id}", headers=nurse_headers)
    assert len(listed.json()["results"]) == 1


def test_transfusion_feedback_with_reaction(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _add_blood_product(client, nurse_headers, patient.id, order_id)

    feedback = client.post("/api/cca/treatment/transfusion-feedback", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "blood_product_id": admin_id,
        "reaction_occurred": True, "reaction_type": "Febrile Non-Hemolytic",
        "symptoms": "Fever 38.6C, chills.", "action_taken": "Transfusion paused, antipyretic given.",
    })
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["feedback"]["reaction_occurred"] is True
    assert feedback.json()["feedback"]["reaction_type"] == "Febrile Non-Hemolytic"


def test_transfusion_feedback_rejects_blood_product_from_different_order(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    other_order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id_on_other_order = _add_blood_product(client, nurse_headers, patient.id, other_order_id, unit_id="UNIT-0002")

    feedback = client.post("/api/cca/treatment/transfusion-feedback", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "blood_product_id": admin_id_on_other_order,
    })
    assert feedback.status_code == 422


def test_transfusion_feedback_without_linked_blood_product(client, nurse_headers, onc_headers, patient):
    """blood_product_id stays optional -- feedback can still be recorded standalone."""
    order_id = _create_signed_order(client, onc_headers, patient.id)

    feedback = client.post("/api/cca/treatment/transfusion-feedback", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "outcome": "Well tolerated.",
    })
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["feedback"]["blood_product_id"] is None
