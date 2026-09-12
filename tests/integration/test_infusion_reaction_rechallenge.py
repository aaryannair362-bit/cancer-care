"""
Safety/dataflow-critical follow-up round: Batch 3 (Day Care/MAR, C.12) was missing the
reaction-rechallenge decision the reference spec requires (MAR-023) -- "written back to the
allergy/reaction record so it appears on every future order and administration." Rather
than a shared CCAPatient allergy field (this codebase's standing convention explicitly
avoids that, see PreTreatmentSafetyCheck's docstring in models_cca.py), the write-back is a
read-time view (GET /patients/{id}/reaction-precautions) over InfusionReactionEvent rows.

Mirrors test_infusion_reaction_and_completion.py's fixture/helper shape.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@rechallenge-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def nurse(make_user, oncologist):
    return make_user(email="nurse@rechallenge-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(
        mrn="RECHALLENGE-0001", name="Rechallenge Test Patient", age=58, sex="Female",
        organization_id=oncologist.organization_id, journey_state="On Treatment",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_signed_order(client, onc_headers, patient_id):
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={"patient_id": patient_id}).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers, json={})
    order_id = client.post("/api/cca/treatment-orders", headers=onc_headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id,
    }).json()["treatment_order"]["id"]
    client.post(f"/api/cca/treatment-orders/{order_id}/sign", headers=onc_headers, json={})
    return order_id


def _add_medication(client, nurse_headers, patient_id, order_id, name="Paclitaxel"):
    added = client.post("/api/cca/treatment/medications", headers=nurse_headers, json={
        "patient_id": patient_id, "order_id": order_id, "medication_name": name, "category": "Antineoplastic", "sequence_no": 1,
    })
    assert added.status_code == 200, added.text
    return added.json()["medication"]["id"]


def test_rechallenge_decision_and_precaution_view(client, onc_headers, nurse_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _add_medication(client, nurse_headers, patient.id, order_id)

    reaction = client.post("/api/cca/treatment/reaction", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "administration_id": admin_id,
        "symptoms": "Flushing, mild dyspnoea during infusion.", "infusion_action": "Paused",
    })
    assert reaction.status_code == 200, reaction.text
    reaction_id = reaction.json()["reaction"]["id"]

    # Not yet visible in the precaution view -- no rechallenge decision recorded yet.
    empty = client.get(f"/api/cca/patients/{patient.id}/reaction-precautions", headers=onc_headers)
    assert empty.status_code == 200
    assert empty.json()["precautions"] == []

    bad_decision = client.post(f"/api/cca/treatment/reaction/{reaction_id}/rechallenge-decision", headers=onc_headers, json={
        "future_rechallenge_decision": "Not A Real Option",
    })
    assert bad_decision.status_code == 422

    decided = client.post(f"/api/cca/treatment/reaction/{reaction_id}/rechallenge-decision", headers=onc_headers, json={
        "rechallenge_attempted": False,
        "future_rechallenge_decision": "Permitted with premedication",
        "future_precautions": "Premedicate with antihistamine and corticosteroid; halve initial infusion rate.",
    })
    assert decided.status_code == 200, decided.text
    assert decided.json()["reaction"]["future_rechallenge_decision"] == "Permitted with premedication"
    assert decided.json()["reaction"]["rechallenge_decided_by"] == "onc@rechallenge-test.com"

    # Non-clinician (nurse) cannot make the rechallenge decision.
    forbidden = client.post(f"/api/cca/treatment/reaction/{reaction_id}/rechallenge-decision", headers=nurse_headers, json={
        "future_rechallenge_decision": "Not permitted",
    })
    assert forbidden.status_code == 403

    # Now visible in the precaution view, resolving the agent name via administration_id.
    precautions = client.get(f"/api/cca/patients/{patient.id}/reaction-precautions", headers=nurse_headers)
    assert precautions.status_code == 200
    rows = precautions.json()["precautions"]
    assert len(rows) == 1
    assert rows[0]["agent"] == "Paclitaxel"
    assert rows[0]["future_rechallenge_decision"] == "Permitted with premedication"
