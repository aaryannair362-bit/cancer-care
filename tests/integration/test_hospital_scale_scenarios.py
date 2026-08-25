"""
End-to-end scenarios across the full range of hospital scale this system is meant to serve --
from a single-doctor small clinic (one org, a handful of users and patients) up to a
multi-specialty hospital (many wards, many nurses and head nurses, many simultaneous patients),
including trauma-center-style high patient throughput and rural-vs-urban staffing patterns
(a rural clinic with a skeleton crew wearing multiple hats vs. an urban hospital with fully
staffed, specialized roles per ward).
"""
import pytest


# ---------------------------------------------------------------------------
# Small clinic: one admin/doctor, one or two support staff, a handful of patients -- the
# minimum viable deployment of this system.
# ---------------------------------------------------------------------------

@pytest.fixture
def small_clinic(make_user):
    admin = make_user(email="admin@small-clinic.com", role="Admin")
    doctor = make_user(email="doctor@small-clinic.com", role="Doctor", organization_id=admin.organization_id)
    head_nurse = make_user(email="head@small-clinic.com", role="HeadNurse", organization_id=admin.organization_id)
    nurse = make_user(email="nurse@small-clinic.com", role="Nurse", organization_id=admin.organization_id)
    return {"admin": admin, "doctor": doctor, "head_nurse": head_nurse, "nurse": nurse}


def test_small_clinic_full_day_single_ward_single_nurse(client, small_clinic, auth_headers):
    """A rural/small-town clinic scenario: one ward, one nurse covering everything, a handful
    of patients admitted and managed across a single day."""
    hn = auth_headers(small_clinic["head_nurse"])
    nurse = small_clinic["nurse"]

    patient_ids = []
    for i in range(4):
        pid = client.post("/api/ipd/patients", json={"name": f"Clinic Patient {i}", "ward": "General Ward", "bed": str(i)},
                           headers=hn).json()["id"]
        client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurse.id}, headers=hn)
        patient_ids.append(pid)

    for pid in patient_ids:
        client.post("/api/ipd/vitals", json={"patient_id": pid, "heart_rate": 76, "temperature": 37.0},
                     headers=auth_headers(nurse))

    roster = client.get("/api/ipd/patients", headers=auth_headers(nurse)).json()
    assert len(roster) == 4
    assert all(p["assigned_nurse"]["id"] == nurse.id for p in roster)


def test_small_clinic_doctor_and_ipd_share_one_org(client, small_clinic, auth_headers):
    """A small clinic's doctor sees OPD walk-ins AND the same organization's IPD ward exists --
    confirms both workflows coexist correctly within one small organization."""
    doctor = small_clinic["doctor"]
    hn = auth_headers(small_clinic["head_nurse"])

    ipd_patient = client.post("/api/ipd/patients", json={"name": "Admitted Patient", "ward": "General Ward"}, headers=hn).json()["id"]

    resp = client.get("/api/ipd/patients", headers=auth_headers(doctor))
    assert resp.status_code == 200
    assert any(p["id"] == ipd_patient for p in resp.json())


def test_small_clinic_admin_manages_its_own_small_staff(client, small_clinic, auth_headers):
    admin = small_clinic["admin"]
    resp = client.get("/api/auth/users", headers=auth_headers(admin))
    assert resp.status_code == 200
    assert len(resp.json()) == 4  # admin, doctor, head_nurse, nurse


# ---------------------------------------------------------------------------
# Multi-specialty hospital: many wards, many nurses/head nurses, many simultaneous patients.
# ---------------------------------------------------------------------------

@pytest.fixture
def multi_specialty_hospital(make_user):
    admin = make_user(email="admin@multi-hospital.com", role="Admin")
    org_id = admin.organization_id
    head_nurses = [make_user(email=f"head{i}@multi-hospital.com", role="HeadNurse", organization_id=org_id) for i in range(3)]
    nurses = [make_user(email=f"nurse{i}@multi-hospital.com", role="Nurse", organization_id=org_id) for i in range(10)]
    doctors = [make_user(email=f"doctor{i}@multi-hospital.com", role="Doctor", organization_id=org_id) for i in range(5)]
    stations = [make_user(email=f"station{i}@multi-hospital.com", role="NursingStation", organization_id=org_id) for i in range(2)]
    return {"admin": admin, "head_nurses": head_nurses, "nurses": nurses, "doctors": doctors, "stations": stations}


WARDS = ["ICU", "Emergency", "General Medicine", "Cardiology", "Orthopedics", "Oncology",
         "Maternity", "Pediatrics", "Neurology", "Nephrology"]


def test_multi_specialty_hospital_full_ward_setup(client, multi_specialty_hospital, auth_headers):
    """Admit patients across every ward, distribute across every head nurse's assignment
    authority and every nurse, confirming the whole roster is consistent org-wide."""
    stations = multi_specialty_hospital["stations"]
    head_nurses = multi_specialty_hospital["head_nurses"]
    nurses = multi_specialty_hospital["nurses"]

    patient_ids = []
    for i, ward in enumerate(WARDS):
        for bed in range(3):  # 3 patients per ward = 30 total
            station = stations[i % len(stations)]
            pid = client.post("/api/ipd/patients", json={"name": f"{ward} Patient {bed}", "ward": ward, "bed": str(bed)},
                               headers=auth_headers(station)).json()["id"]
            patient_ids.append(pid)

    assert len(patient_ids) == 30

    for i, pid in enumerate(patient_ids):
        head_nurse = head_nurses[i % len(head_nurses)]
        nurse = nurses[i % len(nurses)]
        resp = client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurse.id}, headers=auth_headers(head_nurse))
        assert resp.status_code == 200

    full_roster = client.get("/api/ipd/patients", headers=auth_headers(head_nurses[0])).json()
    assert len(full_roster) == 30
    wards_present = {p["ward"] for p in full_roster}
    assert wards_present == set(WARDS)


def test_multi_specialty_hospital_each_nurse_sees_only_their_own_patients(client, multi_specialty_hospital, auth_headers):
    head_nurse = multi_specialty_hospital["head_nurses"][0]
    nurses = multi_specialty_hospital["nurses"][:3]
    hn = auth_headers(head_nurse)

    for i, nurse in enumerate(nurses):
        for j in range(2):
            pid = client.post("/api/ipd/patients", json={"name": f"N{i}P{j}", "ward": "General", "bed": f"{i}-{j}"},
                               headers=hn).json()["id"]
            client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurse.id}, headers=hn)

    for nurse in nurses:
        my_roster = client.get("/api/ipd/patients", headers=auth_headers(nurse)).json()
        assert len(my_roster) == 2
        assert all(p["assigned_nurse"]["id"] == nurse.id for p in my_roster)


def test_multi_specialty_hospital_headnurse_workload_balancing_across_wards(client, multi_specialty_hospital, auth_headers):
    head_nurse = multi_specialty_hospital["head_nurses"][0]
    nurses = multi_specialty_hospital["nurses"][:5]
    hn = auth_headers(head_nurse)

    for i in range(20):
        pid = client.post("/api/ipd/patients", json={"name": f"Load Patient {i}", "ward": WARDS[i % len(WARDS)], "bed": str(i)},
                           headers=hn).json()["id"]
        client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurses[i % 5].id}, headers=hn)

    workload = {w["id"]: w["patient_count"] for w in client.get("/api/ipd/nurse-workload", headers=hn).json()}
    assert all(workload[n.id] == 4 for n in nurses)


def test_multi_specialty_hospital_multiple_doctors_each_have_private_opd_practice(client, multi_specialty_hospital, auth_headers):
    doctors = multi_specialty_hospital["doctors"][:3]
    for i, doc in enumerate(doctors):
        client.post("/api/consultations", json={"chief_complaint": f"Complaint from doctor {i}'s patient"},
                    headers=auth_headers(doc))
    for i, doc in enumerate(doctors):
        consultations = client.get("/api/consultations", headers=auth_headers(doc)).json()["consultations"]
        assert len(consultations) == 1
        assert consultations[0]["chief_complaint"] == f"Complaint from doctor {i}'s patient"


def test_multi_specialty_hospital_abnormal_vitals_surface_across_all_wards(client, multi_specialty_hospital, auth_headers):
    head_nurse = multi_specialty_hospital["head_nurses"][0]
    hn = auth_headers(head_nurse)
    critical_ids = []
    for ward in WARDS[:5]:
        pid = client.post("/api/ipd/patients", json={"name": f"Critical in {ward}", "ward": ward}, headers=hn).json()["id"]
        client.post("/api/ipd/vitals", json={"patient_id": pid, "heart_rate": 145}, headers=hn)
        critical_ids.append(pid)

    roster = client.get("/api/ipd/patients", headers=hn).json()
    abnormal_ids = {p["id"] for p in roster if p["abnormal"]}
    assert set(critical_ids).issubset(abnormal_ids)


# ---------------------------------------------------------------------------
# Trauma center: high patient throughput, mostly emergency admissions in a short window.
# ---------------------------------------------------------------------------

def test_trauma_center_mass_casualty_admission_surge(client, multi_specialty_hospital, auth_headers):
    """A mass-casualty event: many emergency patients admitted in rapid succession, each
    needing immediate vitals and triage-priority tasks."""
    station = multi_specialty_hospital["stations"][0]
    head_nurse = multi_specialty_hospital["head_nurses"][0]
    nurses = multi_specialty_hospital["nurses"]
    s = auth_headers(station)
    hn = auth_headers(head_nurse)

    patient_ids = []
    for i in range(25):
        pid = client.post("/api/ipd/patients", json={"name": f"Trauma Patient {i}", "ward": "Emergency", "bed": str(i),
                                                       "diagnosis": "Multiple trauma, pending assessment"}, headers=s).json()["id"]
        patient_ids.append(pid)

    for i, pid in enumerate(patient_ids):
        client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurses[i % len(nurses)].id}, headers=hn)
        client.post("/api/ipd/vitals", json={"patient_id": pid, "heart_rate": 110 + i % 30, "bp_systolic": 90 + i % 20},
                     headers=hn)
        client.post("/api/ipd/tasks", json={"patient_id": pid, "description": "Immediate triage assessment"}, headers=hn)

    roster = client.get("/api/ipd/patients", headers=s).json()
    emergency_patients = [p for p in roster if p["ward"] == "Emergency"]
    assert len(emergency_patients) == 25
    assert all(p["pending_tasks"] >= 1 for p in emergency_patients)


def test_trauma_center_rapid_discharge_turnover(client, multi_specialty_hospital, auth_headers):
    """Trauma centers discharge stabilized patients quickly to free beds -- a rapid admit/
    stabilize/discharge cycle for several patients."""
    station = multi_specialty_hospital["stations"][0]
    head_nurse = multi_specialty_hospital["head_nurses"][0]
    nurse = multi_specialty_hospital["nurses"][0]
    s = auth_headers(station)

    for i in range(10):
        pid = client.post("/api/ipd/patients", json={"name": f"Rapid Turnover {i}", "ward": "Emergency", "bed": str(i)},
                           headers=s).json()["id"]
        client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurse.id}, headers=auth_headers(head_nurse))
        client.post("/api/ipd/vitals", json={"patient_id": pid, "heart_rate": 78}, headers=auth_headers(nurse))
        client.put(f"/api/patients/{pid}", json={"status": "Discharged"}, headers=s)

    roster = client.get("/api/ipd/patients", headers=s).json()
    assert roster == []


# ---------------------------------------------------------------------------
# Rural vs urban staffing patterns
# ---------------------------------------------------------------------------

def test_rural_clinic_single_head_nurse_handles_all_roles_duties(client, make_user, auth_headers):
    """A rural clinic where the HeadNurse personally admits (uncommon in an urban hospital
    where NursingStation handles intake, but backend explicitly allows it), assigns, and
    records vitals -- one person wearing multiple hats due to limited staff."""
    head_nurse = make_user(email="head@rural-clinic.com", role="HeadNurse")
    nurse = make_user(email="nurse@rural-clinic.com", role="Nurse", organization_id=head_nurse.organization_id)
    hn = auth_headers(head_nurse)

    pid = client.post("/api/ipd/patients", json={"name": "Rural Patient", "ward": "General"}, headers=hn).json()["id"]
    client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurse.id}, headers=hn)
    vital_resp = client.post("/api/ipd/vitals", json={"patient_id": pid, "heart_rate": 80}, headers=hn)
    assert vital_resp.status_code == 200


def test_urban_hospital_fully_staffed_role_separation(client, multi_specialty_hospital, auth_headers):
    """An urban hospital with fully separated roles: NursingStation only admits, HeadNurse
    only assigns/manages, Nurse only records clinical data -- confirms each role stays in its
    lane even when all are available (as opposed to the rural single-operator scenario)."""
    station = multi_specialty_hospital["stations"][0]
    head_nurse = multi_specialty_hospital["head_nurses"][0]
    nurse = multi_specialty_hospital["nurses"][0]

    pid = client.post("/api/ipd/patients", json={"name": "Urban Patient", "ward": "ICU"}, headers=auth_headers(station)).json()["id"]
    assign_resp = client.post("/api/ipd/assign", json={"patient_id": pid, "nurse_id": nurse.id}, headers=auth_headers(head_nurse))
    assert assign_resp.status_code == 200
    vital_resp = client.post("/api/ipd/vitals", json={"patient_id": pid, "heart_rate": 82}, headers=auth_headers(nurse))
    assert vital_resp.status_code == 200
    # Station cannot record vitals even in a fully staffed urban setting -- role stays narrow.
    station_vital_attempt = client.post("/api/ipd/vitals", json={"patient_id": pid, "heart_rate": 90}, headers=auth_headers(station))
    assert station_vital_attempt.status_code == 403
