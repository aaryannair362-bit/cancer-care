"""
Product 1 vs Product 2 Functional Gap Report, Batch 10: Clinical Masters / Administration
(C.26). One generic ClinicalMaster/ClinicalMasterItem pair covering Facility, Department,
Clinician/Roster, Formulary, Lab Catalogue, Radiology Protocol, Surgery Template, Pathology
Synoptic Template, Consent Template, Value Set, and Unit Normalisation masters -- see
ClinicalMaster's docstring in models_cca.py for why this is one generic pair rather than 18
near-duplicate tables, and for what's deliberately excluded (Dose Modification & Rounding
Rule Master, the dose/readiness rule sub-tables, OAR constraints, alert/escalation rules --
all rule-engine logic over thresholds, out of scope per standing repo policy).

Unlike patient-scoped CCA data, masters are organization configuration -- not part of
cca_seed.py's force_reset, matching how the pre-existing Regimen library already persists
across demo resets.
"""
import pytest


@pytest.fixture
def admin(make_user):
    return make_user(email="admin@masters-test.com", role="Admin")


@pytest.fixture
def front_desk(make_user, admin):
    return make_user(email="fd@masters-test.com", role="CCAFrontDesk", organization_id=admin.organization_id)


@pytest.fixture
def admin_headers(auth_headers, admin):
    return auth_headers(admin)


@pytest.fixture
def fd_headers(auth_headers, front_desk):
    return auth_headers(front_desk)


def test_create_requires_admin_and_valid_type(client, admin_headers, fd_headers):
    non_admin = client.post("/api/cca/clinical-masters", headers=fd_headers, json={"master_type": "FACILITY", "name": "Main Campus"})
    assert non_admin.status_code == 403

    bad_type = client.post("/api/cca/clinical-masters", headers=admin_headers, json={"master_type": "NOT_A_TYPE", "name": "X"})
    assert bad_type.status_code == 422

    ok = client.post("/api/cca/clinical-masters", headers=admin_headers, json={"master_type": "FACILITY", "name": "Main Campus", "owner": "admin@masters-test.com"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["master"]["status"] == "DRAFT"
    assert ok.json()["master"]["version"] == 1


def test_full_lifecycle_and_versioning(client, admin_headers, fd_headers):
    v1 = client.post("/api/cca/clinical-masters", headers=admin_headers, json={
        "master_type": "FORMULARY", "name": "Oncology Formulary", "owner": "Pharmacy Lead",
    }).json()["master"]

    item = client.post(f"/api/cca/clinical-masters/{v1['id']}/items", headers=admin_headers, json={
        "fields": {"drug": "Doxorubicin", "hazardous": True, "routes_allowed": ["IV"]},
    })
    assert item.status_code == 200, item.text

    # Non-admin cannot add items.
    forbidden = client.post(f"/api/cca/clinical-masters/{v1['id']}/items", headers=fd_headers, json={"fields": {"x": 1}})
    assert forbidden.status_code == 403

    publish_v1 = client.post(f"/api/cca/clinical-masters/{v1['id']}/publish", headers=admin_headers)
    assert publish_v1.status_code == 200, publish_v1.text
    assert publish_v1.json()["master"]["status"] == "PUBLISHED"

    # Cannot edit items (or the master itself) once published.
    blocked_item = client.post(f"/api/cca/clinical-masters/{v1['id']}/items", headers=admin_headers, json={"fields": {"drug": "Late addition"}})
    assert blocked_item.status_code == 409
    blocked_edit = client.put(f"/api/cca/clinical-masters/{v1['id']}", headers=admin_headers, json={"name": "Renamed"})
    assert blocked_edit.status_code == 409

    # A new version supersedes and, on publish, retires the prior one.
    v2 = client.post("/api/cca/clinical-masters", headers=admin_headers, json={
        "master_type": "FORMULARY", "name": "Oncology Formulary", "supersedes_id": v1["id"],
    }).json()["master"]
    assert v2["version"] == 2

    publish_v2 = client.post(f"/api/cca/clinical-masters/{v2['id']}/publish", headers=admin_headers)
    assert publish_v2.status_code == 200, publish_v2.text
    v1_after = client.get(f"/api/cca/clinical-masters/{v1['id']}", headers=admin_headers).json()["master"]
    assert v1_after["status"] == "RETIRED"

    retire_v2 = client.post(f"/api/cca/clinical-masters/{v2['id']}/retire", headers=admin_headers)
    assert retire_v2.status_code == 200
    assert retire_v2.json()["master"]["status"] == "RETIRED"

    # Cannot publish an already-retired master.
    republish = client.post(f"/api/cca/clinical-masters/{v2['id']}/publish", headers=admin_headers)
    assert republish.status_code == 409


def test_list_filter_by_type(client, admin_headers):
    client.post("/api/cca/clinical-masters", headers=admin_headers, json={"master_type": "DEPARTMENT", "name": "Radiology Dept"})
    client.post("/api/cca/clinical-masters", headers=admin_headers, json={"master_type": "VALUE_SET", "name": "ECOG Value Set"})

    all_masters = client.get("/api/cca/clinical-masters", headers=admin_headers)
    assert all_masters.status_code == 200
    assert len(all_masters.json()["masters"]) >= 2

    filtered = client.get("/api/cca/clinical-masters?master_type=DEPARTMENT", headers=admin_headers)
    assert filtered.status_code == 200
    assert all(m["master_type"] == "DEPARTMENT" for m in filtered.json()["masters"])


def test_cross_org_isolation(client, admin_headers, make_user, auth_headers):
    other_admin = make_user(email="admin2@masters-test.com", role="Admin")
    other_headers = auth_headers(other_admin)

    master = client.post("/api/cca/clinical-masters", headers=admin_headers, json={"master_type": "FACILITY", "name": "Org A Campus"}).json()["master"]

    cross_org = client.get(f"/api/cca/clinical-masters/{master['id']}", headers=other_headers)
    assert cross_org.status_code == 404
