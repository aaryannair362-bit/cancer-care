"""
Safety/dataflow-critical follow-up round (functional/dataflow-parity verification against
the reference spec, not a new gap-report batch): two Batch 1 (Systemic Treatment Orders,
C.9) gaps found were the missing toxicity->dose-change audit link on TreatmentOrder
(SCR-ORD-005) and the missing standalone Hold/Delay/Discontinue-Regimen decision with
structured resumption criteria (SCR-ORD-006), distinct from TreatmentClearance (C.10's
treatment-day decision) and TreatmentHoldEvent (the Day-Care nurse's mid-infusion stop).

Never tests a computed dose/threshold -- resumption_criteria is a clinician-authored
checklist, never evaluated by the server.
"""
import pytest

from app.cca_seed import seed_cca_database
from app.models_cca import CCAPatient, TreatmentPlan, ToxicityEvent


@pytest.fixture
def oncologist(make_user):
    return make_user(email="onc@hold-decision-test.com", role="CCAMedicalOncologist")


@pytest.fixture(autouse=True)
def seed_demo_data(db_session, oncologist):
    seed_cca_database(db_session, force_reset=False, organization_id=oncologist.organization_id)
    db_session.commit()


@pytest.fixture
def onc_headers(auth_headers, oncologist):
    return auth_headers(oncologist)


def _patient_id(db_session, org_id):
    return db_session.query(CCAPatient).filter(CCAPatient.mrn == "CCA-2026-004417", CCAPatient.organization_id == org_id).first().id


def _active_plan(client, onc_headers, db_session, patient_id):
    plan_id = client.post("/api/cca/treatment-plans", headers=onc_headers, json={
        "patient_id": patient_id, "modality": "Systemic Chemotherapy", "protocol_name": "AC-T",
    }).json()["treatment_plan"]["id"]
    client.post(f"/api/cca/treatment-plans/{plan_id}/sign", headers=onc_headers)
    return plan_id


def _make_toxicity(db_session, patient_id):
    tox = ToxicityEvent(patient_id=patient_id, term="Peripheral sensory neuropathy", grade=3, baseline_value="Grade 0 (Baseline)")
    db_session.add(tox)
    db_session.commit()
    db_session.refresh(tox)
    return tox.id


def test_treatment_order_toxicity_link(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _active_plan(client, onc_headers, db_session, patient_id)
    tox_id = _make_toxicity(db_session, patient_id)

    bad = client.post("/api/cca/treatment-orders", headers=onc_headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id, "toxicity_event_id": 999999,
    })
    assert bad.status_code == 422

    good = client.post("/api/cca/treatment-orders", headers=onc_headers, json={
        "patient_id": patient_id, "treatment_plan_id": plan_id, "toxicity_event_id": tox_id,
        "dose_modification_percent": "75%", "revision_reason": "Grade 3 neuropathy on prior cycle.",
    })
    assert good.status_code == 200, good.text
    assert good.json()["treatment_order"]["toxicity_event_id"] == tox_id


def test_hold_decision_lifecycle(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _active_plan(client, onc_headers, db_session, patient_id)
    tox_id = _make_toxicity(db_session, patient_id)

    bad_type = client.post(f"/api/cca/treatment-plans/{plan_id}/hold-decisions", headers=onc_headers, json={
        "decision_type": "Not A Type", "reason_detail": "x",
    })
    assert bad_type.status_code == 422

    decision = client.post(f"/api/cca/treatment-plans/{plan_id}/hold-decisions", headers=onc_headers, json={
        "decision_type": "Hold", "reason_category": "Toxicity", "toxicity_event_id": tox_id,
        "reason_detail": "Grade 3 peripheral neuropathy -- hold pending recovery.",
        "resumption_criteria": [{"criterion": "Neuropathy improves to Grade <=1", "met": False}],
        "next_plan": "Reassess at 2-week review; consider dose reduction on resume.",
        "patient_informed": True,
    })
    assert decision.status_code == 200, decision.text
    body = decision.json()["hold_decision"]
    assert body["status"] == "OPEN"
    assert body["patient_informed"] is True
    assert body["patient_informed_at"] is not None
    decision_id = body["id"]

    listed = client.get(f"/api/cca/patients/{patient_id}/hold-decisions", headers=onc_headers)
    assert listed.status_code == 200
    assert len(listed.json()["hold_decisions"]) == 1

    resume = client.post(f"/api/cca/hold-decisions/{decision_id}/resume", headers=onc_headers, json={
        "resumption_criteria": [{"criterion": "Neuropathy improves to Grade <=1", "met": True}],
    })
    assert resume.status_code == 200, resume.text
    assert resume.json()["hold_decision"]["status"] == "RESUMED"
    assert resume.json()["hold_decision"]["resumption_criteria"][0]["met"] is True

    double_resume = client.post(f"/api/cca/hold-decisions/{decision_id}/resume", headers=onc_headers, json={})
    assert double_resume.status_code == 409


def test_discontinue_regimen_decision_cascades_plan(client, onc_headers, db_session, oncologist):
    patient_id = _patient_id(db_session, oncologist.organization_id)
    plan_id = _active_plan(client, onc_headers, db_session, patient_id)

    decision = client.post(f"/api/cca/treatment-plans/{plan_id}/hold-decisions", headers=onc_headers, json={
        "decision_type": "Discontinue Regimen", "reason_category": "Progression",
        "reason_detail": "Disease progression on interim imaging.",
    })
    assert decision.status_code == 200, decision.text

    plan = client.get(f"/api/cca/treatment-plans/{plan_id}", headers=onc_headers).json()["treatment_plan"]
    assert plan["status"] == "CANCELLED"
