"""
Radiation Oncology / Radiation Physicist missing-development round, Batch 2: simulation &
dataset readiness check, and target/OAR structure-set approval gate. Both PDFs describe these
as entirely missing -- previously CCARadiationPhase only carried a bare simulation_required
bool and a free-text immobilization string, and a phase could reach "planning" with no
contour ever reviewed.

Never tests a computed dose/variance value -- every field here is a physicist/technologist
attestation or a structural readiness check, matching this repo's standing rule.
"""
import pytest

from app.models_cca import CCAPatient


@pytest.fixture
def oncologist(make_user):
    return make_user(email="ro@rt-sim-contour-test.com", role="CCARadiationOncologist")


@pytest.fixture
def physicist(make_user, oncologist):
    return make_user(email="physicist@rt-sim-contour-test.com", role="CCARadiationPhysicist", organization_id=oncologist.organization_id)


@pytest.fixture
def radiologist(make_user, oncologist):
    return make_user(email="rtt@rt-sim-contour-test.com", role="CCARadiationTechnologist", organization_id=oncologist.organization_id)


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
    p = CCAPatient(mrn="RT-SIM-0001", name="RT Simulation Contouring Test Patient", age=58, sex="Male", organization_id=oncologist.organization_id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _create_phase(client, onc_headers, patient_id):
    client.post(f"/api/cca/patients/{patient_id}/radiation-consultations", headers=onc_headers, json={"cied_present": False})
    rx_id = client.post("/api/cca/radiation-prescriptions", headers=onc_headers, json={
        "patient_id": patient_id, "diagnosis": "Lung Cancer", "intent": "Curative", "technique": "VMAT",
    }).json()["radiation_prescription"]["id"]
    phase = client.post(f"/api/cca/radiation-prescriptions/{rx_id}/phases", headers=onc_headers, json={
        "label": "Lung", "treatment_site": "Lung", "total_prescribed_dose_gy": 40,
        "dose_per_fraction_gy": 20, "number_of_fractions": 2, "technique": "VMAT-SBRT",
    }).json()["phase"]
    return phase["id"]


def test_phase_technique_overrides_course_technique(client, onc_headers, patient):
    phase_id = _create_phase(client, onc_headers, patient.id)
    phase = client.get(f"/api/cca/radiation-phases/{phase_id}", headers=onc_headers).json()["phase"]
    assert phase["technique"] == "VMAT-SBRT"


def test_simulation_complete_blocked_without_ready_simulation_record(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase(client, onc_headers, patient.id)
    client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "simulation_pending"})

    blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "simulation_complete"})
    assert blocked.status_code == 409

    sim = client.post(f"/api/cca/radiation-phases/{phase_id}/simulation", headers=rtt_headers, json={
        "modality": "CT Sim", "immobilization_device": "Vac-Lok", "contrast_used": False,
        "ct_dataset_status": "Transferred", "dataset_transferred_to_tps": True,
    })
    assert sim.status_code == 201, sim.text
    sim_id = sim.json()["simulation"]["id"]

    still_blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "simulation_complete"})
    assert still_blocked.status_code == 409  # recorded, but not yet reviewed/Ready

    missing_reason = client.post(f"/api/cca/radiation-simulation-records/{sim_id}/review", headers=physicist_headers, json={"readiness_status": "NotReady"})
    assert missing_reason.status_code == 422

    not_ready = client.post(f"/api/cca/radiation-simulation-records/{sim_id}/review", headers=physicist_headers, json={
        "readiness_status": "NotReady", "rejection_reason": "Scan range did not cover full treatment field.",
    })
    assert not_ready.status_code == 200
    still_blocked_2 = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "simulation_complete"})
    assert still_blocked_2.status_code == 409

    ready = client.post(f"/api/cca/radiation-simulation-records/{sim_id}/review", headers=physicist_headers, json={"readiness_status": "Ready"})
    assert ready.status_code == 200
    assert ready.json()["simulation"]["readiness_status"] == "Ready"

    advanced = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "simulation_complete"})
    assert advanced.status_code == 200, advanced.text


def test_planning_blocked_without_approved_structure_set(client, onc_headers, physicist_headers, patient):
    phase_id = _create_phase(client, onc_headers, patient.id)
    sim_id = client.post(f"/api/cca/radiation-phases/{phase_id}/simulation", headers=physicist_headers, json={}).json()["simulation"]["id"]
    client.post(f"/api/cca/radiation-simulation-records/{sim_id}/review", headers=physicist_headers, json={"readiness_status": "Ready"})
    for status in ("simulation_pending", "simulation_complete", "contouring"):
        r = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": status})
        assert r.status_code == 200, r.text

    blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    assert blocked.status_code == 409

    structure_set = client.post(f"/api/cca/radiation-phases/{phase_id}/structure-sets", headers=physicist_headers, json={
        "target_volumes": {"PTV_Lung": "62.4cc"}, "organs_at_risk": {"Lung_Contra": "1200cc", "Heart": "480cc"},
    })
    assert structure_set.status_code == 201, structure_set.text
    structure_set_id = structure_set.json()["structure_set"]["id"]
    assert structure_set.json()["structure_set"]["version_no"] == 1
    assert structure_set.json()["structure_set"]["status"] == "Draft"

    still_blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    assert still_blocked.status_code == 409  # submitted, not yet Approved

    reviewed_only = client.post(f"/api/cca/radiation-structure-sets/{structure_set_id}/review", headers=physicist_headers, json={"status": "Reviewed"})
    assert reviewed_only.status_code == 200
    still_blocked_2 = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    assert still_blocked_2.status_code == 409  # Reviewed is not Approved

    approved = client.post(f"/api/cca/radiation-structure-sets/{structure_set_id}/review", headers=physicist_headers, json={
        "status": "Approved", "review_note": "Contours acceptable.",
    })
    assert approved.status_code == 200
    assert approved.json()["structure_set"]["status"] == "Approved"

    still_blocked_3 = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    assert still_blocked_3.status_code == 409  # structure set approved, but prescription not yet verified (Batch 3 gate)

    client.post(f"/api/cca/radiation-phases/{phase_id}/prescription-verification", headers=physicist_headers, json={"outcome": "Verified"})

    advanced = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    assert advanced.status_code == 200, advanced.text


def test_planning_gate_checks_only_the_latest_structure_set_version(client, onc_headers, physicist_headers, patient):
    """An older Approved set doesn't count once a newer (unreviewed) version has been
    submitted -- the gate must always look at the LATEST version, not any-ever-Approved."""
    phase_id = _create_phase(client, onc_headers, patient.id)
    sim_id = client.post(f"/api/cca/radiation-phases/{phase_id}/simulation", headers=physicist_headers, json={}).json()["simulation"]["id"]
    client.post(f"/api/cca/radiation-simulation-records/{sim_id}/review", headers=physicist_headers, json={"readiness_status": "Ready"})
    for status in ("simulation_pending", "simulation_complete", "contouring"):
        client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": status})

    first_id = client.post(f"/api/cca/radiation-phases/{phase_id}/structure-sets", headers=physicist_headers, json={
        "target_volumes": {"PTV": "v1"},
    }).json()["structure_set"]["id"]
    client.post(f"/api/cca/radiation-structure-sets/{first_id}/review", headers=physicist_headers, json={"status": "Approved"})

    second = client.post(f"/api/cca/radiation-phases/{phase_id}/structure-sets", headers=physicist_headers, json={
        "target_volumes": {"PTV": "v2 -- revised after re-simulation"},
    })
    assert second.json()["structure_set"]["version_no"] == 2

    blocked = client.post(f"/api/cca/radiation-phases/{phase_id}/transition", headers=physicist_headers, json={"status": "planning"})
    assert blocked.status_code == 409

    sets = client.get(f"/api/cca/radiation-phases/{phase_id}/structure-sets", headers=physicist_headers).json()["structure_sets"]
    assert len(sets) == 2
    assert sets[0]["version_no"] == 2  # ordered newest-first


def test_only_physicist_can_review_simulation_or_structure_set(client, onc_headers, physicist_headers, rtt_headers, patient):
    phase_id = _create_phase(client, onc_headers, patient.id)
    sim_id = client.post(f"/api/cca/radiation-phases/{phase_id}/simulation", headers=rtt_headers, json={}).json()["simulation"]["id"]
    assert client.post(f"/api/cca/radiation-simulation-records/{sim_id}/review", headers=rtt_headers, json={"readiness_status": "Ready"}).status_code == 403
    assert client.post(f"/api/cca/radiation-simulation-records/{sim_id}/review", headers=onc_headers, json={"readiness_status": "Ready"}).status_code == 403

    structure_set_id = client.post(f"/api/cca/radiation-phases/{phase_id}/structure-sets", headers=onc_headers, json={}).json()["structure_set"]["id"]
    assert client.post(f"/api/cca/radiation-structure-sets/{structure_set_id}/review", headers=rtt_headers, json={"status": "Approved"}).status_code == 403
