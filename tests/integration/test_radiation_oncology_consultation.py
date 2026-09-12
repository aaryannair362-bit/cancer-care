"""
Final gap-closing round, item 9: Radiation Oncology Consultation (SCR-RO-002) -- CIED/
pacemaker status + management plan, and prior-RT/re-irradiation history, now required
before a course can be prescribed.

Never computes a cumulative dose or a re-irradiation tolerance -- cumulative_prior_oar_dose_note
and prior_rt_summary are always the clinician's own typed reference values.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def rad_onc(make_user):
    return make_user(email="ro@rt-consult-test.com", role="CCARadiationOncologist")


@pytest.fixture
def onc_headers(auth_headers, rad_onc):
    return auth_headers(rad_onc)


@pytest.fixture
def patient(db_session, rad_onc):
    p = CCAPatient(mrn="RT-CONSULT-0001", name="RT Consultation Test Patient", age=66, sex="Male", organization_id=rad_onc.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def test_prescription_blocked_without_consultation(client, onc_headers, patient):
    blocked = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient.id, "diagnosis": "Prostate Cancer",
    })
    assert blocked.status_code == 409


def test_cied_management_plan_required_when_present(client, onc_headers, patient):
    missing_plan = client.post(f"/api/cca/patients/{patient.id}/radiation-consultations", headers=onc_headers, json={
        "cied_present": True,
    })
    assert missing_plan.status_code == 422

    with_plan = client.post(f"/api/cca/patients/{patient.id}/radiation-consultations", headers=onc_headers, json={
        "cied_present": True, "cied_type": "Pacemaker", "cied_management_plan": "Cardiology cleared; device interrogated pre/post course.",
    })
    assert with_plan.status_code == 201, with_plan.text

    blocked = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient.id, "diagnosis": "Prostate Cancer",
    })
    assert blocked.status_code != 409, blocked.text


def test_prior_rt_history_and_prescription_unblocked(client, onc_headers, patient):
    consult = client.post(f"/api/cca/patients/{patient.id}/radiation-consultations", headers=onc_headers, json={
        "cied_present": False, "prior_rt_received": True, "prior_rt_site": "Pelvis, 2019",
        "prior_rt_summary": "50Gy/25# pelvic RT in 2019 per outside records.",
        "cumulative_prior_oar_dose_note": "Rectum reportedly near tolerance per outside summary.",
        "contraindications_checklist": {"pregnancy_excluded": True},
    })
    assert consult.status_code == 201, consult.text

    listed = client.get(f"/api/cca/patients/{patient.id}/radiation-consultations", headers=onc_headers)
    assert listed.status_code == 200
    assert listed.json()["consultations"][0]["prior_rt_received"] is True

    prescription = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient.id, "diagnosis": "Recurrent rectal cancer",
    })
    assert prescription.status_code == 201, prescription.text
