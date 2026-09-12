"""
Product 1 vs Product 2 Functional Gap Report, Batch 5: RT Delivery.

Covers what Product 1's server.py actually implements for daily fraction delivery
(rt_deliver_fraction), interruption handling (rt_record_interruption), and periodic
on-treatment review (rt_record_otv) -- none of which existed with any real detail in
Product 2 before this batch (a bare status flip for "interrupted", no interruption/OTV
records at all, no duplicate-delivery guard, no image-guidance/setup-variation/verified-by
fields on a delivered fraction).

Never tests dose/MU computation -- Product 1's rt_fraction_safety() computes and blocks on
a delivered-vs-prescribed dose tolerance; this repo's standing rule forbids porting that.
dose_match_confirmed is a plain RTT attestation (boolean + note if mismatched), never a
system-computed comparison.
"""
import pytest

from app.models_cca import CCAPatient

_FULL_PHYSICS_QA_CHECKLIST = {
    "prescription_plan_concordance": True, "dose_volume_constraint_review": True,
    "target_oar_coverage_review": True, "machine_deliverability_review": True,
}


@pytest.fixture
def oncologist(make_user):
    return make_user(email="ro@rt-delivery-test.com", role="CCARadiationOncologist")


@pytest.fixture
def physicist(make_user, oncologist):
    return make_user(email="physicist@rt-delivery-test.com", role="CCARadiationPhysicist", organization_id=oncologist.organization_id)


@pytest.fixture
def radiologist(make_user, oncologist):
    """Radiation Technologist and Radiologist are the same login in this hospital's role
    structure -- no separate CCARadiationTechnologist role exists."""
    return make_user(email="rtt@rt-delivery-test.com", role="CCARadiologist", organization_id=oncologist.organization_id)


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
        mrn="RT-DELIVERY-0001", name="RT Delivery Test Patient", age=62, sex="Male",
        organization_id=oncologist.organization_id, journey_state="On Treatment",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient_id, number_of_fractions=3):
    # RO Consultation gate (gap review item 9) -- required before a course can be prescribed.
    client.post(f"/api/cca/patients/{patient_id}/radiation-consultations", headers=onc_headers, json={"cied_present": False})
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "diagnosis": "Prostate Cancer", "intent": "Curative", "technique": "VMAT",
    }).json()["radiation_prescription"]["id"]
    phase_id = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/phases", headers=onc_headers, json={
        "label": "Prostate", "treatment_site": "Prostate", "total_prescribed_dose_gy": 60,
        "dose_per_fraction_gy": 3, "number_of_fractions": number_of_fractions,
    }).json()["phase"]["id"]
    for status in ("simulation_pending", "simulation_complete", "contouring", "planning", "physics_qa"):
        client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": status})
    client.post(f"/api/cca/radiation-phases/{phase_id}/physics-qa", headers=physicist_headers, json={
        "decision": "Approved", "checklist": _FULL_PHYSICS_QA_CHECKLIST, "note": "All clear.",
    })
    for status in ("physician_approved", "treatment_ready"):
        r = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": status})
        assert r.status_code == 200, r.text
    return phase_id


def _first_fraction_id(client, onc_headers, phase_id):
    fractions = client.get(f"/api/cca/radiation-phases/{phase_id}/fractions", headers=onc_headers).json()["fractions"]
    return fractions[0]["id"], fractions[1]["id"]


def _verify_pretreatment(client, rtt_headers, fraction_id, fraction_number):
    """Pre-Treatment Verification (safety/dataflow-critical follow-up round, reference
    SCR-RTT-002) is now a hard precondition for recording a fraction as delivered."""
    r = client.post(f"/api/cca/radiation-fractions/{fraction_id}/pretreatment-verification", headers=rtt_headers, json={
        "identity_reverified": True, "site_laterality_confirmed": True, "confirmed_fraction_number": fraction_number,
    })
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Fraction delivery
# ---------------------------------------------------------------------------

def test_duplicate_delivery_is_rejected(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    admin_id, _ = _first_fraction_id(client, onc_headers, phase_id)
    _verify_pretreatment(client, rtt_headers, admin_id, 1)

    first = client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={"status": "delivered"})
    assert first.status_code == 200, first.text

    duplicate = client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={"status": "delivered"})
    assert duplicate.status_code == 409


def test_delivery_captures_image_guidance_setup_and_verifier(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    admin_id, _ = _first_fraction_id(client, onc_headers, phase_id)
    _verify_pretreatment(client, rtt_headers, admin_id, 1)

    delivered = client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={
        "status": "delivered", "image_guidance_performed": True, "setup_variation": "Minor shift, adjusted per protocol.",
        "verified_by": "RTT B. Rao (second check)", "dose_match_confirmed": True,
    })
    assert delivered.status_code == 200, delivered.text
    fraction = delivered.json()["fraction"]
    assert fraction["image_guidance_performed"] is True
    assert fraction["setup_variation"] == "Minor shift, adjusted per protocol."
    assert fraction["verified_by"] == "RTT B. Rao (second check)"
    assert fraction["dose_match_confirmed"] is True


def test_dose_mismatch_requires_a_note(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    admin_id, _ = _first_fraction_id(client, onc_headers, phase_id)
    _verify_pretreatment(client, rtt_headers, admin_id, 1)

    missing_note = client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={
        "status": "delivered", "dose_match_confirmed": False,
    })
    assert missing_note.status_code == 422

    with_note = client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={
        "status": "delivered", "dose_match_confirmed": False, "dose_mismatch_note": "Machine output slightly low, physics notified.",
    })
    assert with_note.status_code == 200, with_note.text
    assert with_note.json()["fraction"]["dose_mismatch_note"] == "Machine output slightly low, physics notified."


def test_cancelled_is_a_valid_fraction_status(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    admin_id, _ = _first_fraction_id(client, onc_headers, phase_id)

    cancelled = client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={"status": "cancelled"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["fraction"]["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Interruptions
# ---------------------------------------------------------------------------

def test_interruption_requires_reason_and_valid_category(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    admin_id, _ = _first_fraction_id(client, onc_headers, phase_id)
    _verify_pretreatment(client, rtt_headers, admin_id, 1)
    client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={"status": "delivered"})

    missing_reason = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={"status": "interrupted"})
    assert missing_reason.status_code == 422

    bad_category = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={
        "status": "interrupted", "reason": "Patient unwell.", "category": "Not A Real Category",
    })
    assert bad_category.status_code == 422

    ok = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=onc_headers, json={
        "status": "interrupted", "reason": "Patient developed grade 3 skin toxicity.", "category": "Toxicity/Condition",
        "compensation_plan": "Resume once toxicity settles to grade 1; extend course by missed fractions.",
    })
    assert ok.status_code == 200, ok.text


def test_rtt_may_also_record_and_resume_an_interruption(client, onc_headers, physicist_headers, rtt_headers, patient):
    """Product 1: rt_record_interruption is gated to either Radiation Oncology or Radiation
    Technologist -- not RO-only."""
    phase_id = _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    admin_id, _ = _first_fraction_id(client, onc_headers, phase_id)
    _verify_pretreatment(client, rtt_headers, admin_id, 1)
    client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={"status": "delivered"})

    interrupted = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=rtt_headers, json={
        "status": "interrupted", "reason": "Linac fault.", "category": "Machine Issue",
    })
    assert interrupted.status_code == 200, interrupted.text

    interruptions = client.get(f"/api/cca/radiation-phases/{phase_id}/interruptions", headers=onc_headers).json()["interruptions"]
    assert len(interruptions) == 1
    assert interruptions[0]["end_at"] is None

    resumed = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=rtt_headers, json={"status": "on_treatment"})
    assert resumed.status_code == 200, resumed.text

    interruptions_after = client.get(f"/api/cca/radiation-phases/{phase_id}/interruptions", headers=onc_headers).json()["interruptions"]
    assert interruptions_after[0]["end_at"] is not None


def test_nurse_cannot_record_an_interruption(client, onc_headers, physicist_headers, rtt_headers, patient, make_user, auth_headers, oncologist):
    phase_id = _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    admin_id, _ = _first_fraction_id(client, onc_headers, phase_id)
    _verify_pretreatment(client, rtt_headers, admin_id, 1)
    client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={"status": "delivered"})

    nurse = make_user(email="nurse@rt-delivery-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)
    rejected = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=auth_headers(nurse), json={
        "status": "interrupted", "reason": "Not my job.",
    })
    assert rejected.status_code == 403


# ---------------------------------------------------------------------------
# On-Treatment Visit (OTV)
# ---------------------------------------------------------------------------

def test_otv_requires_at_least_one_delivered_fraction(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)

    too_soon = client.post(f"/api/cca/radiation-phases/{phase_id}/otv", headers=onc_headers, json={
        "assessment": "Tolerating treatment well.", "toxicity_summary": "No acute toxicity.", "plan": "Continue as planned.",
    })
    assert too_soon.status_code == 409

    admin_id, _ = _first_fraction_id(client, onc_headers, phase_id)
    _verify_pretreatment(client, rtt_headers, admin_id, 1)
    client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={"status": "delivered"})

    ok = client.post(f"/api/cca/radiation-phases/{phase_id}/otv", headers=onc_headers, json={
        "assessment": "Tolerating treatment well.", "toxicity_summary": "No acute toxicity.", "plan": "Continue as planned.",
    })
    assert ok.status_code == 201, ok.text
    assert ok.json()["otv"]["after_fraction_number"] == 1


def test_otv_requires_all_three_fields(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    admin_id, _ = _first_fraction_id(client, onc_headers, phase_id)
    _verify_pretreatment(client, rtt_headers, admin_id, 1)
    client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={"status": "delivered"})

    missing_plan = client.post(f"/api/cca/radiation-phases/{phase_id}/otv", headers=onc_headers, json={
        "assessment": "Tolerating treatment well.", "toxicity_summary": "No acute toxicity.",
    })
    assert missing_plan.status_code == 422


def test_otv_list_returns_multiple_visits(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase_treatment_ready(client, onc_headers, physicist_headers, patient.id)
    admin_id, second_id = _first_fraction_id(client, onc_headers, phase_id)
    _verify_pretreatment(client, rtt_headers, admin_id, 1)
    client.post(f"/api/cca/radiation-fractions/{admin_id}/event", headers=rtt_headers, json={"status": "delivered"})
    client.post(f"/api/cca/radiation-phases/{phase_id}/otv", headers=onc_headers, json={
        "assessment": "Visit 1.", "toxicity_summary": "None.", "plan": "Continue.",
    })
    _verify_pretreatment(client, rtt_headers, second_id, 2)
    client.post(f"/api/cca/radiation-fractions/{second_id}/event", headers=rtt_headers, json={"status": "delivered"})
    client.post(f"/api/cca/radiation-phases/{phase_id}/otv", headers=onc_headers, json={
        "assessment": "Visit 2.", "toxicity_summary": "Mild erythema.", "plan": "Continue, review skin weekly.",
        "weight_kg": 78.5, "performance_status": "ECOG 1",
    })

    visits = client.get(f"/api/cca/radiation-phases/{phase_id}/otv", headers=onc_headers).json()["otv"]
    assert len(visits) == 2
    assert {v["assessment"] for v in visits} == {"Visit 1.", "Visit 2."}
