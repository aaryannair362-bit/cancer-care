"""
Safety/dataflow-critical follow-up round: Batches 4-5 (Radiation Physics QA / RT Delivery,
C.16/C.17) were missing a Discrepancy Record with a QA-blocking gate (SCR-PHY-007), a waiver
mechanism on QA sign-off (SCR-PHY-008), a Pre-Treatment Verification gate before fraction
delivery (SCR-RTT-002), and RTT-observed toxicity feeding the shared ToxicityEvent record
(RTT-050) instead of a standalone free-text field.

Never tests a computed dose/MU/tolerance -- every gate here is a structural/count check or
a recorded clinician attestation, matching the docstrings already in models_cca_oncology_ext.py.
"""
import pytest

from app.models_cca import CCAPatient

_FULL_PHYSICS_QA_CHECKLIST = {
    "prescription_plan_concordance": True, "dose_volume_constraint_review": True,
    "target_oar_coverage_review": True, "machine_deliverability_review": True,
}


@pytest.fixture
def oncologist(make_user):
    return make_user(email="ro@rt-gaps-test.com", role="CCARadiationOncologist")


@pytest.fixture
def physicist(make_user, oncologist):
    return make_user(email="physicist@rt-gaps-test.com", role="CCARadiationPhysicist", organization_id=oncologist.organization_id)


@pytest.fixture
def radiologist(make_user, oncologist):
    return make_user(email="rtt@rt-gaps-test.com", role="CCARadiologist", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def physicist_headers(auth_headers, physicist):
    return auth_headers(physicist)


@pytest.fixture
def rtt_headers(auth_headers, radiologist):
    return auth_headers(radiologist)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(
        mrn="RT-GAPS-0001", name="RT Gaps Test Patient", age=59, sex="Female",
        organization_id=oncologist.organization_id, journey_state="On Treatment",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _phase_at_physics_qa(client, onc_headers, physicist_headers, patient_id, number_of_fractions=2):
    # RO Consultation gate (gap review item 9) -- required before a course can be prescribed.
    client.post(f"/api/cca/patients/{patient_id}/radiation-consultations", headers=onc_headers, json={"cied_present": False})
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "diagnosis": "Breast Cancer", "intent": "Curative", "technique": "IMRT",
    }).json()["radiation_prescription"]["id"]
    phase_id = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/phases", headers=onc_headers, json={
        "label": "Breast", "treatment_site": "Left breast", "total_prescribed_dose_gy": 40,
        "dose_per_fraction_gy": 2.67, "number_of_fractions": number_of_fractions,
    }).json()["phase"]["id"]
    for status in ("simulation_pending", "simulation_complete", "contouring", "planning", "physics_qa"):
        client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": status})
    return phase_id


def _phase_treatment_ready(client, onc_headers, physicist_headers, patient_id, number_of_fractions=2):
    phase_id = _phase_at_physics_qa(client, onc_headers, physicist_headers, patient_id, number_of_fractions)
    client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_PHYSICS_QA_CHECKLIST, "note": "All clear.",
    })
    for status in ("physician_approved", "treatment_ready"):
        client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": status})
    return phase_id


def _first_fraction(client, onc_headers, phase_id):
    return client.get(f"/api/cca/radiation-phases/{phase_id}/fractions", headers=onc_headers).json()["fractions"][0]


# ---------------------------------------------------------------------------
# Discrepancy Record + QA gate (SCR-PHY-007)
# ---------------------------------------------------------------------------

def test_open_discrepancy_blocks_qa_approval(client, onc_headers, physicist_headers, patient):
    phase_id = _phase_at_physics_qa(client, onc_headers, physicist_headers, patient.id)

    discrepancy = client.post(f"/api/cca/radiation-phases/{phase_id}/discrepancies", headers=physicist_headers, json={
        "category": "Plan-prescription mismatch", "severity": "Major", "description": "Laterality on plan does not match prescription.",
    })
    assert discrepancy.status_code == 201, discrepancy.text
    discrepancy_id = discrepancy.json()["discrepancy"]["id"]
    assert discrepancy.json()["discrepancy"]["status"] == "OPEN"

    blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_PHYSICS_QA_CHECKLIST, "note": "Attempting approval.",
    })
    assert blocked.status_code == 409

    close = client.post(f"/api/cca/radiation-discrepancies/{discrepancy_id}/close", headers=physicist_headers, json={
        "resolution": "Plan corrected and re-verified against prescription.",
    })
    assert close.status_code == 200, close.text
    assert close.json()["discrepancy"]["status"] == "CLOSED"

    approved = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_PHYSICS_QA_CHECKLIST, "note": "Discrepancy resolved.",
    })
    assert approved.status_code == 200, approved.text


def test_only_physicist_can_raise_or_close_discrepancy(client, onc_headers, physicist_headers, patient):
    phase_id = _phase_at_physics_qa(client, onc_headers, physicist_headers, patient.id)
    forbidden = client.post(f"/api/cca/radiation-phases/{phase_id}/discrepancies", headers=onc_headers, json={
        "category": "x", "severity": "Minor", "description": "y",
    })
    assert forbidden.status_code == 403


# ---------------------------------------------------------------------------
# Waiver mechanism (SCR-PHY-008)
# ---------------------------------------------------------------------------

def test_waiver_allows_approval_with_unmet_checklist_item(client, onc_headers, physicist_headers, patient):
    phase_id = _phase_at_physics_qa(client, onc_headers, physicist_headers, patient.id)
    incomplete = {**_FULL_PHYSICS_QA_CHECKLIST, "machine_deliverability_review": False}

    still_blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": incomplete, "note": "Attempting approval.",
    })
    assert still_blocked.status_code == 422

    waived = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": incomplete, "note": "Machine deliverability check waived -- see reason.",
        "waived_items": [{"item": "machine_deliverability_review", "waived_by": "Chief Physicist", "reason": "Manufacturer QA cert current, cross-checked separately today."}],
    })
    assert waived.status_code == 200, waived.text
    assert waived.json()["phase"]["physics_qa_waived_items"][0]["item"] == "machine_deliverability_review"

    fetched = client.get(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers)
    assert fetched.json()["physics_qa"]["waived_items"][0]["reason"].startswith("Manufacturer")


def test_waiver_requires_authority_and_reason(client, onc_headers, physicist_headers, patient):
    phase_id = _phase_at_physics_qa(client, onc_headers, physicist_headers, patient.id)
    incomplete = {**_FULL_PHYSICS_QA_CHECKLIST, "target_oar_coverage_review": False}
    bad_waiver = client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": incomplete, "note": "x",
        "waived_items": [{"item": "target_oar_coverage_review"}],
    })
    assert bad_waiver.status_code == 422


# ---------------------------------------------------------------------------
# Pre-Treatment Verification gate (SCR-RTT-002)
# ---------------------------------------------------------------------------

def test_delivery_blocked_without_pretreatment_verification(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    fraction = _first_fraction(client, onc_headers, phase_id)

    blocked = client.post(f"/api/cca/radiation-fractions/{fraction['id']}/event", headers=rtt_headers, json={"status": "delivered"})
    assert blocked.status_code == 409

    verified = client.post(f"/api/cca/radiation-fractions/{fraction['id']}/pretreatment-verification", headers=rtt_headers, json={
        "identity_reverified": True, "site_laterality_confirmed": True, "confirmed_fraction_number": fraction["fraction_number"],
    })
    assert verified.status_code == 201, verified.text
    assert verified.json()["verification"]["fraction_number_mismatch"] is False

    delivered = client.post(f"/api/cca/radiation-fractions/{fraction['id']}/event", headers=rtt_headers, json={"status": "delivered"})
    assert delivered.status_code == 200, delivered.text


def test_fraction_number_mismatch_requires_note(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    fraction = _first_fraction(client, onc_headers, phase_id)

    missing_note = client.post(f"/api/cca/radiation-fractions/{fraction['id']}/pretreatment-verification", headers=rtt_headers, json={
        "identity_reverified": True, "site_laterality_confirmed": True,
        "confirmed_fraction_number": fraction["fraction_number"] + 1,
    })
    assert missing_note.status_code == 422

    with_note = client.post(f"/api/cca/radiation-fractions/{fraction['id']}/pretreatment-verification", headers=rtt_headers, json={
        "identity_reverified": True, "site_laterality_confirmed": True,
        "confirmed_fraction_number": fraction["fraction_number"] + 1,
        "mismatch_note": "Schedule board showed next fraction number -- corrected before proceeding.",
    })
    assert with_note.status_code == 201, with_note.text
    assert with_note.json()["verification"]["fraction_number_mismatch"] is True


def test_identity_and_site_must_both_be_confirmed(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    fraction = _first_fraction(client, onc_headers, phase_id)
    incomplete = client.post(f"/api/cca/radiation-fractions/{fraction['id']}/pretreatment-verification", headers=rtt_headers, json={
        "identity_reverified": True, "site_laterality_confirmed": False, "confirmed_fraction_number": fraction["fraction_number"],
    })
    assert incomplete.status_code == 422


# ---------------------------------------------------------------------------
# RTT toxicity -> shared ToxicityEvent linkage (RTT-050)
# ---------------------------------------------------------------------------

def test_fraction_toxicity_feeds_shared_toxicity_record(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    fraction = _first_fraction(client, onc_headers, phase_id)
    client.post(f"/api/cca/radiation-fractions/{fraction['id']}/pretreatment-verification", headers=rtt_headers, json={
        "identity_reverified": True, "site_laterality_confirmed": True, "confirmed_fraction_number": fraction["fraction_number"],
    })

    delivered = client.post(f"/api/cca/radiation-fractions/{fraction['id']}/event", headers=rtt_headers, json={
        "status": "delivered", "toxicity_term": "Radiation dermatitis", "toxicity_grade": 2,
    })
    assert delivered.status_code == 200, delivered.text
    tox_id = delivered.json()["fraction"]["toxicity_event_id"]
    assert tox_id is not None

    events = client.get(f"/api/cca/patients/{patient.id}/toxicity-events", headers=onc_headers).json()["toxicity_events"]
    assert any(e["id"] == tox_id and e["term"] == "Radiation dermatitis" and e["grade"] == 2 for e in events)
