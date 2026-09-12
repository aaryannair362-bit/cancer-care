"""
Product 1 vs Product 2 Functional Gap Report, Batch 3: Day Care / Full MAR --
2-of-3 identity-match gate before identity can be verified, administration
sequence enforcement, independent chairside double-check (by a different
person) required before starting an Antineoplastic medication, full
completion detail (completion_status/reaction_occurred/variance) on
COMPLETE/STOP, treatment-day tolerance, and the read-only Full MAR
aggregation report.

Never tests dose computation -- every checklist/attestation item here is a
nurse-typed boolean or category, never a system-computed comparison.
"""
import pytest

from app.models_cca import CCAPatient

_IV_CHECKLIST_ALL_TRUE = {k: True for k in [
    "drug", "dose", "volume_diluent", "route", "rate", "expiry", "physical_integrity", "sequence", "pump_settings",
]}


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@day-care-mar-test.com", role="CCAMedicalOncologist")


@pytest.fixture
def nurse(make_user, oncologist):
    return make_user(email="nurse@day-care-mar-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def nurse_b(make_user, oncologist):
    return make_user(email="nurseb@day-care-mar-test.com", role="CCAInfusionNurse", organization_id=oncologist.organization_id)


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


@pytest.fixture
def nurse_headers(auth_headers, nurse):
    return auth_headers(nurse)


@pytest.fixture
def nurse_b_headers(auth_headers, nurse_b):
    return auth_headers(nurse_b)


@pytest.fixture
def patient(db_session, oncologist):
    p = CCAPatient(
        mrn="DAYCARE-MAR-0001", name="Day Care MAR Test Patient", age=64, sex="Female",
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


def _add_medication(client, nurse_headers, patient_id, order_id, name="Doxorubicin", sequence_no=1, category="Antineoplastic"):
    added = client.post("/api/cca/treatment/medications", headers=nurse_headers, json={
        "patient_id": patient_id, "order_id": order_id, "medication_name": name,
        "category": category, "sequence_no": sequence_no,
    })
    assert added.status_code == 200, added.text
    return added.json()["medication"]["id"]


# ---------------------------------------------------------------------------
# Pre-Treatment Safety Check: 2-of-3 identity match gate
# ---------------------------------------------------------------------------

def test_identity_verified_requires_at_least_two_of_three_matches(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    zero_matches = client.post("/api/cca/treatment/safety-check", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "identity_verified": True,
    })
    assert zero_matches.status_code == 422

    one_match = client.post("/api/cca/treatment/safety-check", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "identity_verified": True, "name_matched": True,
    })
    assert one_match.status_code == 422

    two_matches = client.post("/api/cca/treatment/safety-check", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "identity_verified": True,
        "name_matched": True, "mrn_matched": True,
    })
    assert two_matches.status_code == 200, two_matches.text
    assert two_matches.json()["safety_check"]["identity_verified"] is True

    three_matches = client.post("/api/cca/treatment/safety-check", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "identity_verified": True,
        "name_matched": True, "mrn_matched": True, "dob_matched": True,
    })
    assert three_matches.status_code == 200, three_matches.text


def test_identity_match_checkboxes_can_be_saved_without_verifying_identity_yet(client, nurse_headers, onc_headers, patient):
    """The gate only fires when identity_verified is being set true -- a nurse can save
    partial progress (e.g. one identifier checked so far) without being blocked."""
    order_id = _create_signed_order(client, onc_headers, patient.id)

    partial = client.post("/api/cca/treatment/safety-check", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "name_matched": True,
    })
    assert partial.status_code == 200, partial.text
    assert partial.json()["safety_check"]["identity_verified"] is False


# ---------------------------------------------------------------------------
# Administration sequence enforcement
# ---------------------------------------------------------------------------

def test_cannot_start_a_later_sequence_before_an_earlier_one_finishes(client, nurse_headers, nurse_b_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    first_id = _add_medication(client, nurse_headers, patient.id, order_id, name="Doxorubicin", sequence_no=1)
    second_id = _add_medication(client, nurse_headers, patient.id, order_id, name="Cyclophosphamide", sequence_no=2)

    # Independent verification for both up front so the only thing under test is sequencing.
    for admin_id in (first_id, second_id):
        iv = client.post(f"/api/cca/treatment/medications/{admin_id}/independent-verification", headers=nurse_b_headers, json={"checklist": _IV_CHECKLIST_ALL_TRUE})
        assert iv.status_code == 201, iv.text

    out_of_order = client.post(f"/api/cca/treatment/medications/{second_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert out_of_order.status_code == 422
    assert "sequence" in out_of_order.text.lower()

    started_first = client.post(f"/api/cca/treatment/medications/{first_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert started_first.status_code == 200, started_first.text
    client.post(f"/api/cca/treatment/medications/{first_id}/event", headers=nurse_headers, json={
        "event_type": "COMPLETE", "completion_status": "Administered", "reaction_occurred": False,
    })

    now_allowed = client.post(f"/api/cca/treatment/medications/{second_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert now_allowed.status_code == 200, now_allowed.text


# ---------------------------------------------------------------------------
# Independent verification (chairside double-check) for Antineoplastic lines
# ---------------------------------------------------------------------------

def test_antineoplastic_start_requires_independent_verification_by_a_different_person(client, nurse_headers, nurse_b_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _add_medication(client, nurse_headers, patient.id, order_id)

    missing_iv = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert missing_iv.status_code == 422
    assert "independent verification" in missing_iv.text.lower()

    self_verified = client.post(f"/api/cca/treatment/medications/{admin_id}/independent-verification", headers=nurse_headers, json={"checklist": _IV_CHECKLIST_ALL_TRUE})
    assert self_verified.status_code == 201, self_verified.text

    same_person_start = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert same_person_start.status_code == 409

    different_person_start = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_b_headers, json={"event_type": "START"})
    assert different_person_start.status_code == 200, different_person_start.text


def test_premedication_does_not_require_independent_verification(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _add_medication(client, nurse_headers, patient.id, order_id, name="Ondansetron", category="Premedication")

    started = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert started.status_code == 200, started.text


def test_independent_verification_rejects_incomplete_checklist(client, nurse_b_headers, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _add_medication(client, nurse_headers, patient.id, order_id)

    incomplete = dict(_IV_CHECKLIST_ALL_TRUE)
    incomplete["pump_settings"] = False
    rejected = client.post(f"/api/cca/treatment/medications/{admin_id}/independent-verification", headers=nurse_b_headers, json={"checklist": incomplete})
    assert rejected.status_code == 422


def test_independent_verification_rejects_duplicate(client, nurse_b_headers, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _add_medication(client, nurse_headers, patient.id, order_id)

    first = client.post(f"/api/cca/treatment/medications/{admin_id}/independent-verification", headers=nurse_b_headers, json={"checklist": _IV_CHECKLIST_ALL_TRUE})
    assert first.status_code == 201, first.text

    duplicate = client.post(f"/api/cca/treatment/medications/{admin_id}/independent-verification", headers=nurse_b_headers, json={"checklist": _IV_CHECKLIST_ALL_TRUE})
    assert duplicate.status_code == 409


# ---------------------------------------------------------------------------
# Full completion detail on COMPLETE/STOP
# ---------------------------------------------------------------------------

def _started_premedication(client, nurse_headers, patient_id, order_id):
    """Premedication sidesteps the independent-verification gate so these tests focus
    purely on the completion-detail validation."""
    admin_id = _add_medication(client, nurse_headers, patient_id, order_id, name="Dexamethasone", category="Premedication")
    started = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    assert started.status_code == 200, started.text
    return admin_id


def test_complete_requires_completion_status_and_reaction_occurred(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _started_premedication(client, nurse_headers, patient.id, order_id)

    missing_status = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "COMPLETE", "reaction_occurred": False,
    })
    assert missing_status.status_code == 422

    missing_reaction = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "COMPLETE", "completion_status": "Administered",
    })
    assert missing_reaction.status_code == 422


def test_complete_clean_administered_needs_no_variance_detail(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _started_premedication(client, nurse_headers, patient.id, order_id)

    clean = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "COMPLETE", "completion_status": "Administered", "reaction_occurred": False,
    })
    assert clean.status_code == 200, clean.text
    assert clean.json()["medication"]["completion_status"] == "Administered"
    assert clean.json()["medication"]["variance_type"] == "None"


def test_complete_non_clean_outcome_requires_variance_reason_and_note(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _started_premedication(client, nurse_headers, patient.id, order_id)

    missing_variance_detail = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "COMPLETE", "completion_status": "Partially Administered", "reaction_occurred": False,
    })
    assert missing_variance_detail.status_code == 422

    with_detail = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "COMPLETE", "completion_status": "Partially Administered", "reaction_occurred": False,
        "variance_type": "Rate variance", "variance_reason": "Access issue", "variance_note": "Line occluded twice, rate reduced for remainder of infusion.",
    })
    assert with_detail.status_code == 200, with_detail.text
    assert with_detail.json()["medication"]["variance_reason"] == "Access issue"


def test_stop_requires_notes_and_completion_detail(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    admin_id = _started_premedication(client, nurse_headers, patient.id, order_id)

    no_reason = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "STOP", "completion_status": "Stopped", "reaction_occurred": True,
        "variance_type": "Other", "variance_reason": "Infusion reaction", "variance_note": "",
    })
    assert no_reason.status_code == 422

    stopped = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "STOP", "completion_status": "Stopped", "reaction_occurred": True,
        "variance_type": "Other", "variance_reason": "Infusion reaction",
        "variance_note": "Patient developed facial flushing, infusion stopped per protocol.",
        "notes": "Patient developed facial flushing, infusion stopped per protocol.",
    })
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["medication"]["reaction_occurred"] is True


# ---------------------------------------------------------------------------
# Treatment-day tolerance
# ---------------------------------------------------------------------------

def test_completion_rejects_invalid_tolerance(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    _started_premedication_and_completed(client, nurse_headers, patient.id, order_id)

    rejected = client.post("/api/cca/treatment/completion", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "tolerance": "Very Bad",
    })
    assert rejected.status_code == 422


def test_completion_accepts_valid_tolerance(client, nurse_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)
    _started_premedication_and_completed(client, nurse_headers, patient.id, order_id)

    ok = client.post("/api/cca/treatment/completion", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "tolerance": "Mild symptoms",
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["completion"]["tolerance"] == "Mild symptoms"


def _started_premedication_and_completed(client, nurse_headers, patient_id, order_id):
    admin_id = _started_premedication(client, nurse_headers, patient_id, order_id)
    done = client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "COMPLETE", "completion_status": "Administered", "reaction_occurred": False,
    })
    assert done.status_code == 200, done.text
    return admin_id


# ---------------------------------------------------------------------------
# Full MAR report
# ---------------------------------------------------------------------------

def test_full_mar_report_aggregates_every_artifact(client, nurse_headers, nurse_b_headers, onc_headers, patient):
    order_id = _create_signed_order(client, onc_headers, patient.id)

    client.post("/api/cca/treatment/safety-check", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "identity_verified": True,
        "name_matched": True, "mrn_matched": True,
    })
    client.post("/api/cca/treatment/vascular-access", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "device_type": "Peripheral IV", "access_ready": True,
    })
    admin_id = _add_medication(client, nurse_headers, patient.id, order_id)
    client.post(f"/api/cca/treatment/medications/{admin_id}/independent-verification", headers=nurse_b_headers, json={"checklist": _IV_CHECKLIST_ALL_TRUE})
    client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={"event_type": "START"})
    client.post(f"/api/cca/treatment/medications/{admin_id}/event", headers=nurse_headers, json={
        "event_type": "COMPLETE", "completion_status": "Administered", "reaction_occurred": False,
    })
    client.post("/api/cca/treatment/completion", headers=nurse_headers, json={
        "patient_id": patient.id, "order_id": order_id, "tolerance": "Good",
    })

    report = client.get(f"/api/cca/treatment/{order_id}/full-mar?patient_id={patient.id}", headers=nurse_headers)
    assert report.status_code == 200, report.text
    data = report.json()
    assert data["treatment_order_id"] == order_id
    assert data["safety_check"]["identity_verified"] is True
    assert data["vascular_access"]["device_type"] == "Peripheral IV"
    assert len(data["medications"]) == 1
    med = data["medications"][0]
    assert med["completion_status"] == "Administered"
    assert med["independent_verification"]["verified_by"] == "nurseb@day-care-mar-test.com"
    assert len(med["events"]) == 2  # START, COMPLETE
    assert data["completion"]["tolerance"] == "Good"
