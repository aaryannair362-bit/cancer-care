"""
Oncology Review Results PDF items 11 (Reaction Per Drug) and 12 (Overall Chemotherapy
Completion status): InfusionReactionEvent.administration_id precise per-drug attribution,
and TreatmentDayCompletion.disposition constrained to a fixed nurse-chosen set.

Mirrors test_infusion_nurse_workspace.py's fixture/helper shape.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@infusion-reaction-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def nurse(make_user, oncologist):
    return make_user(email="nurse@infusion-reaction-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(
        mrn="INFUSION-RX-0001", name="Infusion Reaction Test Patient", age=61, sex="Male",
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
        "instructions": {"text": "Doxorubicin 60mg/m2 IV + Cyclophosphamide 600mg/m2 IV, day 1"},
    })
    assert order_draft.status_code == 200, order_draft.text
    order_id = order_draft.json()["treatment_order"]["id"]

    order_signed = client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=onc_headers, json={})
    assert order_signed.status_code == 200, order_signed.text
    return order_id


def _add_medication(client, nurse_headers, patient_id, order_id, name="Doxorubicin", sequence_no=1):
    added = client.post("/api/cca/treatment/medications", headers=nurse_headers, json={
        "patient_id": patient_id, "order_id": order_id, "medication_name": name,
        "category": "Antineoplastic", "sequence_no": sequence_no,
    })
    assert added.status_code == 200, added.text
    return added.json()["medication"]["id"]


# ---------------------------------------------------------------------------
# Item 11: Reaction Per Drug
# ---------------------------------------------------------------------------

def test_reaction_with_valid_administration_id_persists_and_is_returned(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _add_medication(client, nurse_headers, patient.id, order_id)

    reaction = client.post("/api/cca/treatment/reaction", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "administration_id": admin_id,
        "symptoms": "Facial flushing and mild dyspnea.", "infusion_action": "Paused",
    })
    assert reaction.status_code == 200, reaction.text
    assert reaction.json()["reaction"]["administration_id"] == admin_id

    listed = client.get(f"/api/cca/treatment/{order_id}/reactions?patient_id={patient.id}", headers=nurse_headers)
    assert listed.json()["results"][0]["administration_id"] == admin_id


def test_reaction_with_administration_id_from_different_order_is_rejected(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    other_order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id_on_other_order = _add_medication(client, nurse_headers, patient.id, other_order_id)

    reaction = client.post("/api/cca/treatment/reaction", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "administration_id": admin_id_on_other_order,
        "symptoms": "Facial flushing.",
    })
    assert reaction.status_code == 422


def test_reaction_still_works_with_only_free_text_medication_running(client, nurse_headers, onc_headers, patient):
    """Backward compatible: a drug not on the structured medication list can still be
    recorded as free text, with no administration_id."""
    order_id = _create_signed_order(client, onc_headers, patient.id)

    reaction = client.post("/api/cca/treatment/reaction", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "medication_running": "Pre-medication (unlisted)",
        "symptoms": "Mild itching.",
    })
    assert reaction.status_code == 200, reaction.text
    assert reaction.json()["reaction"]["administration_id"] is None
    assert reaction.json()["reaction"]["medication_running"] == "Pre-medication (unlisted)"


# ---------------------------------------------------------------------------
# Item 12: Overall Chemotherapy Completion status
# ---------------------------------------------------------------------------

def _complete_the_only_medication(client, nurse_headers, patient_id, order_id):
    admin_id = _add_medication(client, nurse_headers, patient_id, order_id)
    client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "COMPLETE"})


@pytest.mark.parametrize("disposition", ["Completed", "Partially Completed", "Not Completed", "Discontinued"])
def test_completion_accepts_each_valid_disposition(client, nurse_headers, onc_headers, patient, disposition):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    _complete_the_only_medication(client, nurse_headers, patient.id, order_id)

    done = client.post("/api/cca/treatment/completion", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "disposition": disposition,
    })
    assert done.status_code == 200, done.text
    assert done.json()["completion"]["disposition"] == disposition


def test_completion_rejects_invalid_disposition_value(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    _complete_the_only_medication(client, nurse_headers, patient.id, order_id)

    rejected = client.post("/api/cca/treatment/completion", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "disposition": "Stable, tolerated infusion well.",
    })
    assert rejected.status_code == 422


def test_completion_with_no_disposition_still_succeeds(client, nurse_headers, onc_headers, patient):
    """disposition stays optional -- the nurse may complete without one."""
    order_id = _create_signed_order(client, onc_headers, patient.id)
    _complete_the_only_medication(client, nurse_headers, patient.id, order_id)

    done = client.post("/api/cca/treatment/completion", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id,
    })
    assert done.status_code == 200, done.text
    assert done.json()["completion"]["disposition"] is None
