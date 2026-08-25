"""
TPA / Insurance Pre-Authorization module tests: patient search access, full-record read access,
pre-authorization submission (snapshot correctness), status resolution (Submitted/Approved/
Rejected), role permission boundaries, multi-tenant isolation, and the Billing interlink
(routers/billing.py.create_claim's pre_authorization_id attach/validate logic). Mirrors the
structure of test_billing_module.py / test_patient_registration_and_search.py.
"""
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tpa(make_user):
    return make_user(email="tpa@prehosp.com", role="TPA")


@pytest.fixture
def admin(make_user, tpa):
    return make_user(email="admin@prehosp.com", role="Admin", organization_id=tpa.organization_id)


@pytest.fixture
def doctor(make_user, tpa):
    return make_user(email="doctor@prehosp.com", role="Doctor", organization_id=tpa.organization_id)


@pytest.fixture
def billing(make_user, tpa):
    return make_user(email="billing@prehosp.com", role="Billing", organization_id=tpa.organization_id)


@pytest.fixture
def nurse(make_user, tpa):
    return make_user(email="nurse@prehosp.com", role="Nurse", organization_id=tpa.organization_id)


@pytest.fixture
def nursing_station(make_user, tpa):
    return make_user(email="frontdesk@prehosp.com", role="NursingStation", organization_id=tpa.organization_id)


@pytest.fixture
def pharmacist(make_user, tpa):
    return make_user(email="pharma@prehosp.com", role="Pharmacist", organization_id=tpa.organization_id)


@pytest.fixture
def other_tpa(make_user, tpa):
    """A second TPA user in the same org -- for "own submissions" visibility tests."""
    return make_user(email="tpa2@prehosp.com", role="TPA", organization_id=tpa.organization_id)


@pytest.fixture
def patient(tpa, db_session):
    from app.models import Patient

    p = Patient(name="Pre-Auth Patient", age=41, gender="F", diagnosis="Hypertension",
                organization_id=tpa.organization_id, created_by=tpa.id)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def other_org():
    """A second, fully separate organization: HeadNurse + a patient, for cross-tenant checks."""
    def _make(make_user, db_session):
        from app.models import Patient
        head = make_user(email="head@otherhosp.com", role="HeadNurse")
        p = Patient(name="Other Org Patient", age=30, gender="M",
                    organization_id=head.organization_id, created_by=head.id)
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        return head, p
    return _make


def _submit_pre_auth(client, headers, patient_id):
    resp = client.post("/api/pre-authorizations", json={"patient_id": patient_id}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _set_status(client, headers, pre_auth_id, status):
    return client.patch(f"/api/pre-authorizations/{pre_auth_id}/status", json={"status": status}, headers=headers)


# ---------------------------------------------------------------------------
# Search / record-read access (patients.py, main.py get_patient_details)
# ---------------------------------------------------------------------------

def test_tpa_can_search_patients(client, tpa, patient, auth_headers):
    resp = client.get(f"/api/patients/search?q={patient.name}", headers=auth_headers(tpa))
    assert resp.status_code == 200
    assert any(p["id"] == patient.id for p in resp.json())


def test_tpa_search_is_org_scoped(client, tpa, patient, make_user, db_session, auth_headers):
    """TPA search must not leak another org's patients (all-patients-in-*this*-hospital, not global)."""
    from app.models import Patient
    other_head = make_user(email="otherhead@otherhosp2.com", role="HeadNurse")
    other_patient = Patient(name="Foreign Patient", age=22, gender="M",
                             organization_id=other_head.organization_id, created_by=other_head.id)
    db_session.add(other_patient)
    db_session.commit()

    resp = client.get("/api/patients/search?q=Patient", headers=auth_headers(tpa))
    assert resp.status_code == 200
    seen_ids = {p["id"] for p in resp.json()}
    assert patient.id in seen_ids
    assert other_patient.id not in seen_ids


def test_tpa_can_view_any_patient_detail_org_wide(client, tpa, patient, auth_headers):
    """No per-patient assignment check for TPA, unlike Nurse -- org-wide read, per design."""
    resp = client.get(f"/api/patients/{patient.id}/details", headers=auth_headers(tpa))
    assert resp.status_code == 200
    body = resp.json()
    assert body["patient"]["id"] == patient.id
    assert "procedures" in body  # extended for the TPA record view; must not regress for others


def test_tpa_can_review_ocr_case_summary_and_original_evidence(client, tpa, patient, db_session, auth_headers):
    from app.models import PatientDocument
    source = b"%PDF-1.4 insurer evidence"
    doc = PatientDocument(
        patient_id=patient.id, organization_id=tpa.organization_id, uploaded_by=tpa.id,
        filename="external-report.pdf", content_type="application/pdf", file_size=len(source),
        sha256="a" * 64, file_content=source, document_type="External report",
        ocr_status="Completed", page_count=1, extracted_text="Diagnosis: Angina",
        extracted_data={"diagnoses": ["Angina"], "medications": [], "allergies": [], "investigations": [], "procedures": []},
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    summary = client.get(f"/api/patients/{patient.id}/case-summary", headers=auth_headers(tpa))
    assert summary.status_code == 200
    assert summary.json()["imported_record_findings"]["diagnoses"] == ["Angina"]
    original = client.get(f"/api/patients/{patient.id}/documents/{doc.id}/file", headers=auth_headers(tpa))
    assert original.status_code == 200
    assert original.content == source


def test_tpa_cannot_view_cross_org_patient_detail(client, tpa, make_user, db_session, auth_headers):
    from app.models import Patient
    other_head = make_user(email="otherhead2@otherhosp3.com", role="HeadNurse")
    other_patient = Patient(name="Cross Org Patient", age=55, gender="F",
                             organization_id=other_head.organization_id, created_by=other_head.id)
    db_session.add(other_patient)
    db_session.commit()
    db_session.refresh(other_patient)

    resp = client.get(f"/api/patients/{other_patient.id}/details", headers=auth_headers(tpa))
    assert resp.status_code == 404


def test_nurse_still_cannot_search_after_tpa_added(client, nurse, auth_headers):
    """Regression: adding TPA to _require_search_access must not loosen Nurse's exclusion."""
    resp = client.get("/api/patients/search", headers=auth_headers(nurse))
    assert resp.status_code == 403


def test_existing_search_roles_unaffected_by_tpa_addition(client, doctor, nursing_station, admin, patient, auth_headers):
    for user in (doctor, nursing_station, admin):
        resp = client.get(f"/api/patients/search?q={patient.name}", headers=auth_headers(user))
        assert resp.status_code == 200, f"{user.role} search access regressed"


def test_tpa_cannot_access_other_modules(client, tpa, patient, auth_headers):
    """TPA is search+pre-auth only -- no billing, no pharmacy, no ward access."""
    headers = auth_headers(tpa)
    assert client.get("/api/billing/invoices", headers=headers).status_code == 403
    assert client.get("/api/ipd/patients", headers=headers).status_code == 403
    assert client.post("/api/patients/register", json={"name": "X", "age": 1}, headers=headers).status_code == 403


# ---------------------------------------------------------------------------
# Pre-authorization submission
# ---------------------------------------------------------------------------

def test_tpa_can_submit_pre_authorization(client, tpa, patient, auth_headers):
    result = _submit_pre_auth(client, auth_headers(tpa), patient.id)
    assert result["patient_id"] == patient.id
    assert result["status"] == "Submitted"
    assert result["submitted_at"] is not None


@pytest.mark.parametrize("role_fixture", ["admin", "doctor", "billing", "nurse", "nursing_station", "pharmacist"])
def test_non_tpa_roles_cannot_submit_pre_authorization(client, request, role_fixture, patient, auth_headers):
    user = request.getfixturevalue(role_fixture)
    resp = client.post("/api/pre-authorizations", json={"patient_id": patient.id}, headers=auth_headers(user))
    assert resp.status_code == 403


def test_submit_pre_authorization_requires_patient_id(client, tpa, auth_headers):
    resp = client.post("/api/pre-authorizations", json={}, headers=auth_headers(tpa))
    assert resp.status_code == 400


def test_submit_pre_authorization_rejects_unknown_patient(client, tpa, auth_headers):
    resp = client.post("/api/pre-authorizations", json={"patient_id": 999999}, headers=auth_headers(tpa))
    assert resp.status_code == 404


def test_submit_pre_authorization_rejects_cross_org_patient(client, tpa, make_user, db_session, auth_headers):
    from app.models import Patient
    other_head = make_user(email="otherhead3@otherhosp4.com", role="HeadNurse")
    other_patient = Patient(name="Not My Patient", age=60, gender="M",
                             organization_id=other_head.organization_id, created_by=other_head.id)
    db_session.add(other_patient)
    db_session.commit()
    db_session.refresh(other_patient)

    resp = client.post("/api/pre-authorizations", json={"patient_id": other_patient.id}, headers=auth_headers(tpa))
    assert resp.status_code == 404


def test_pre_authorization_snapshot_captures_full_clinical_picture(client, tpa, doctor, patient, auth_headers):
    """The snapshot must reflect the *complete* record (all consultations, all procedures), not
    just the most recent, since it's the "complete data" the pre-auth is meant to carry."""
    doc_headers = auth_headers(doctor)
    for complaint, dx in [("Chest pain", "Angina"), ("Follow-up", "Angina - stable")]:
        resp = client.post("/api/consultations", json={
            "patient_id": patient.id, "chief_complaint": complaint, "primary_diagnosis": dx,
            "medications": [{"drugName": "Aspirin", "dose": "75mg"}],
        }, headers=doc_headers)
        assert resp.status_code == 200, resp.text
    for name in ["ECG", "Coronary Angiography"]:
        resp = client.post("/api/procedures", json={"patient_id": patient.id, "procedure_name": name}, headers=doc_headers)
        assert resp.status_code == 201, resp.text

    result = _submit_pre_auth(client, auth_headers(tpa), patient.id)

    from app.models import PreAuthorizationRequest
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.query(PreAuthorizationRequest).filter(PreAuthorizationRequest.id == result["id"]).first()
        snapshot = row.clinical_snapshot
    finally:
        db.close()

    assert snapshot["patient"]["id"] == patient.id
    assert snapshot["patient"]["diagnosis"] == "Hypertension"
    assert len(snapshot["consultations"]) == 2
    assert {c["chief_complaint"] for c in snapshot["consultations"]} == {"Chest pain", "Follow-up"}
    assert len(snapshot["procedures"]) == 2
    assert {p["procedure_name"] for p in snapshot["procedures"]} == {"ECG", "Coronary Angiography"}


def test_pre_authorization_snapshot_includes_imported_ocr_evidence(client, tpa, patient, db_session, auth_headers):
    from app.models import PatientDocument, PreAuthorizationRequest
    source = b"%PDF-1.4 imported"
    doc = PatientDocument(
        patient_id=patient.id, organization_id=tpa.organization_id, uploaded_by=tpa.id,
        filename="oncology-transfer.pdf", content_type="application/pdf", file_size=len(source),
        sha256="b" * 64, file_content=source, document_type="Discharge summary",
        source_hospital="Outside Hospital", ocr_status="Completed", page_count=4,
        extracted_data={"diagnoses": ["Carcinoma"], "medications": ["Tamoxifen"]},
    )
    db_session.add(doc)
    db_session.commit()

    result = _submit_pre_auth(client, auth_headers(tpa), patient.id)
    row = db_session.query(PreAuthorizationRequest).filter(PreAuthorizationRequest.id == result["id"]).first()
    imported = row.clinical_snapshot["imported_records"]
    assert len(imported) == 1
    assert imported[0]["filename"] == "oncology-transfer.pdf"
    assert imported[0]["extracted_data"]["diagnoses"] == ["Carcinoma"]
    assert imported[0]["sha256"] == "b" * 64
    assert "file_content" not in imported[0]


def test_multiple_pre_authorizations_can_exist_for_same_patient(client, tpa, patient, auth_headers):
    """Re-submission creates a new independent record rather than overwriting the last one."""
    first = _submit_pre_auth(client, auth_headers(tpa), patient.id)
    second = _submit_pre_auth(client, auth_headers(tpa), patient.id)
    assert first["id"] != second["id"]

    resp = client.get(f"/api/pre-authorizations?patient_id={patient.id}", headers=auth_headers(tpa))
    assert resp.status_code == 200
    assert {r["id"] for r in resp.json()} == {first["id"], second["id"]}


# ---------------------------------------------------------------------------
# Listing (GET /api/pre-authorizations)
# ---------------------------------------------------------------------------

def test_tpa_sees_only_own_submissions(client, tpa, other_tpa, patient, auth_headers):
    mine = _submit_pre_auth(client, auth_headers(tpa), patient.id)
    _submit_pre_auth(client, auth_headers(other_tpa), patient.id)  # not mine

    resp = client.get("/api/pre-authorizations", headers=auth_headers(tpa))
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert ids == {mine["id"]}


def test_admin_sees_every_submission_in_org(client, tpa, other_tpa, admin, patient, auth_headers):
    a = _submit_pre_auth(client, auth_headers(tpa), patient.id)
    b = _submit_pre_auth(client, auth_headers(other_tpa), patient.id)

    resp = client.get("/api/pre-authorizations", headers=auth_headers(admin))
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert {a["id"], b["id"]} <= ids


def test_billing_sees_every_submission_in_org(client, tpa, other_tpa, billing, patient, auth_headers):
    """This is the read path the Billing interlink depends on."""
    a = _submit_pre_auth(client, auth_headers(tpa), patient.id)
    b = _submit_pre_auth(client, auth_headers(other_tpa), patient.id)

    resp = client.get(f"/api/pre-authorizations?patient_id={patient.id}", headers=auth_headers(billing))
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert ids == {a["id"], b["id"]}


@pytest.mark.parametrize("role_fixture", ["doctor", "nurse", "nursing_station", "pharmacist"])
def test_other_roles_cannot_list_pre_authorizations(client, request, role_fixture, auth_headers):
    user = request.getfixturevalue(role_fixture)
    resp = client.get("/api/pre-authorizations", headers=auth_headers(user))
    assert resp.status_code == 403


def test_list_pre_authorizations_patient_id_filter_is_org_scoped(client, tpa, patient, make_user, db_session, auth_headers):
    """A TPA can't use patient_id to fish for another org's data even indirectly."""
    from app.models import Patient
    other_head = make_user(email="otherhead4@otherhosp5.com", role="HeadNurse")
    other_patient = Patient(name="Foreign Patient 2", age=25, gender="F",
                             organization_id=other_head.organization_id, created_by=other_head.id)
    db_session.add(other_patient)
    db_session.commit()
    db_session.refresh(other_patient)

    resp = client.get(f"/api/pre-authorizations?patient_id={other_patient.id}", headers=auth_headers(tpa))
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Status resolution
# ---------------------------------------------------------------------------

def test_tpa_can_approve_and_reject(client, tpa, patient, auth_headers):
    headers = auth_headers(tpa)
    approved = _submit_pre_auth(client, headers, patient.id)
    resp = _set_status(client, headers, approved["id"], "Approved")
    assert resp.status_code == 200
    assert resp.json()["status"] == "Approved"

    rejected = _submit_pre_auth(client, headers, patient.id)
    resp = _set_status(client, headers, rejected["id"], "Rejected")
    assert resp.status_code == 200
    assert resp.json()["status"] == "Rejected"


def test_any_tpa_in_org_can_resolve_anothers_submission(client, tpa, other_tpa, patient, auth_headers):
    """Documented current behavior: TPA status-update is org-scoped, not requester-scoped --
    any TPA operator at the same insurer/org can move a pending case forward."""
    submitted = _submit_pre_auth(client, auth_headers(tpa), patient.id)
    resp = _set_status(client, auth_headers(other_tpa), submitted["id"], "Approved")
    assert resp.status_code == 200


@pytest.mark.parametrize("role_fixture", ["admin", "doctor", "billing"])
def test_non_tpa_cannot_update_status_even_admin(client, request, role_fixture, tpa, patient, auth_headers):
    """Unlike most of this codebase's admin-can-override pattern, status resolution is TPA-only
    -- not even Admin can approve/reject on the TPA's behalf."""
    user = request.getfixturevalue(role_fixture)
    submitted = _submit_pre_auth(client, auth_headers(tpa), patient.id)
    resp = _set_status(client, auth_headers(user), submitted["id"], "Approved")
    assert resp.status_code == 403


def test_status_update_rejects_invalid_value(client, tpa, patient, auth_headers):
    headers = auth_headers(tpa)
    submitted = _submit_pre_auth(client, headers, patient.id)
    resp = _set_status(client, headers, submitted["id"], "Whatever")
    assert resp.status_code == 400


def test_status_update_rejects_unknown_id(client, tpa, auth_headers):
    resp = _set_status(client, auth_headers(tpa), 999999, "Approved")
    assert resp.status_code == 404


def test_status_update_is_org_scoped(client, tpa, make_user, db_session, auth_headers):
    """A TPA in Org A cannot resolve Org B's pre-authorization by guessing its id."""
    from app.models import Patient, PreAuthorizationRequest
    other_tpa_other_org = make_user(email="tpa@otherhosp6.com", role="TPA")
    other_patient = Patient(name="Other Org Patient 2", age=44, gender="M",
                             organization_id=other_tpa_other_org.organization_id, created_by=other_tpa_other_org.id)
    db_session.add(other_patient)
    db_session.commit()
    db_session.refresh(other_patient)

    other_submission = _submit_pre_auth(client, auth_headers(other_tpa_other_org), other_patient.id)

    resp = _set_status(client, auth_headers(tpa), other_submission["id"], "Approved")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Billing interlink (routers/billing.py.create_claim's pre_authorization_id logic)
# ---------------------------------------------------------------------------

def _insurance_invoice(client, headers, patient_id):
    resp = client.post("/api/billing/invoices", json={"patient_id": patient_id, "payer_type": "Insurance"}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_billing_sees_no_pre_authorization_before_tpa_submits(client, billing, patient, auth_headers):
    resp = client.get(f"/api/pre-authorizations?patient_id={patient.id}", headers=auth_headers(billing))
    assert resp.status_code == 200
    assert resp.json() == []


def test_claim_can_be_created_without_a_pre_authorization(client, billing, patient, auth_headers):
    """Pre-authorization is not a hard prerequisite for filing a claim in this codebase."""
    headers = auth_headers(billing)
    invoice = _insurance_invoice(client, headers, patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/claim",
                        json={"insurer_or_corporate_name": "Acme Insurance", "claim_amount": 5000}, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["pre_authorization_id"] is None


def test_claim_rejects_non_approved_pre_authorization(client, tpa, billing, patient, auth_headers):
    """This is the core guard: "everything comes up fine" (== Approved) is required before a
    claim can reference a pre-authorization."""
    submitted = _submit_pre_auth(client, auth_headers(tpa), patient.id)  # still "Submitted"
    invoice = _insurance_invoice(client, auth_headers(billing), patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/claim", json={
        "insurer_or_corporate_name": "Acme Insurance", "claim_amount": 5000,
        "pre_authorization_id": submitted["id"],
    }, headers=auth_headers(billing))
    assert resp.status_code == 400
    assert "Approved" in resp.json()["detail"]


def test_claim_rejects_rejected_pre_authorization(client, tpa, billing, patient, auth_headers):
    tpa_headers = auth_headers(tpa)
    submitted = _submit_pre_auth(client, tpa_headers, patient.id)
    _set_status(client, tpa_headers, submitted["id"], "Rejected")
    invoice = _insurance_invoice(client, auth_headers(billing), patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/claim", json={
        "insurer_or_corporate_name": "Acme Insurance", "claim_amount": 5000,
        "pre_authorization_id": submitted["id"],
    }, headers=auth_headers(billing))
    assert resp.status_code == 400


def test_claim_links_approved_pre_authorization_end_to_end(client, tpa, billing, patient, auth_headers):
    """Full chain: TPA submits -> TPA approves -> Billing sees it -> Billing links it to a claim
    -> the claim reflects the link. This is exactly the "shown at the billing end" requirement."""
    tpa_headers = auth_headers(tpa)
    submitted = _submit_pre_auth(client, tpa_headers, patient.id)
    approve_resp = _set_status(client, tpa_headers, submitted["id"], "Approved")
    assert approve_resp.status_code == 200

    billing_headers = auth_headers(billing)
    visible = client.get(f"/api/pre-authorizations?patient_id={patient.id}", headers=billing_headers).json()
    assert any(r["id"] == submitted["id"] and r["status"] == "Approved" for r in visible)

    invoice = _insurance_invoice(client, billing_headers, patient.id)
    claim_resp = client.post(f"/api/billing/invoices/{invoice['id']}/claim", json={
        "insurer_or_corporate_name": "Acme Insurance", "claim_amount": 5000,
        "pre_authorization_id": submitted["id"],
    }, headers=billing_headers)
    assert claim_resp.status_code == 201
    assert claim_resp.json()["pre_authorization_id"] == submitted["id"]

    fetched_claim = client.get(f"/api/billing/invoices/{invoice['id']}/claim", headers=billing_headers)
    assert fetched_claim.json()["pre_authorization_id"] == submitted["id"]


def test_claim_rejects_pre_authorization_belonging_to_a_different_patient(client, tpa, billing, patient, db_session, auth_headers):
    from app.models import Patient
    other_patient = Patient(name="Different Patient", age=29, gender="M",
                             organization_id=patient.organization_id, created_by=patient.created_by)
    db_session.add(other_patient)
    db_session.commit()
    db_session.refresh(other_patient)

    tpa_headers = auth_headers(tpa)
    submitted = _submit_pre_auth(client, tpa_headers, other_patient.id)
    _set_status(client, tpa_headers, submitted["id"], "Approved")

    billing_headers = auth_headers(billing)
    invoice = _insurance_invoice(client, billing_headers, patient.id)  # different patient's invoice
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/claim", json={
        "insurer_or_corporate_name": "Acme Insurance", "claim_amount": 5000,
        "pre_authorization_id": submitted["id"],
    }, headers=billing_headers)
    assert resp.status_code == 404


def test_claim_rejects_cross_org_pre_authorization_id(client, billing, patient, make_user, db_session, auth_headers):
    from app.models import Patient, PreAuthorizationRequest
    other_tpa_other_org = make_user(email="tpa@otherhosp7.com", role="TPA")
    other_patient = Patient(name="Other Org Patient 3", age=38, gender="F",
                             organization_id=other_tpa_other_org.organization_id, created_by=other_tpa_other_org.id)
    db_session.add(other_patient)
    db_session.commit()
    db_session.refresh(other_patient)
    other_org_pre_auth = PreAuthorizationRequest(
        organization_id=other_tpa_other_org.organization_id, patient_id=other_patient.id,
        requested_by=other_tpa_other_org.id, status="Approved", clinical_snapshot={},
    )
    db_session.add(other_org_pre_auth)
    db_session.commit()
    db_session.refresh(other_org_pre_auth)

    billing_headers = auth_headers(billing)
    invoice = _insurance_invoice(client, billing_headers, patient.id)
    resp = client.post(f"/api/billing/invoices/{invoice['id']}/claim", json={
        "insurer_or_corporate_name": "Acme Insurance", "claim_amount": 5000,
        "pre_authorization_id": other_org_pre_auth.id,
    }, headers=billing_headers)
    assert resp.status_code == 404


def test_tpa_cannot_create_billing_claims(client, tpa, patient, auth_headers):
    resp = client.post("/api/billing/invoices", json={"patient_id": patient.id, "payer_type": "Insurance"}, headers=auth_headers(tpa))
    assert resp.status_code == 403


def test_claim_rejects_pre_authorization_on_a_corporate_invoice(client, tpa, billing, patient, auth_headers):
    """Pre-authorization is an Insurance-only concept (BillingClaim.pre_authorization_id
    docstring, models.py) -- a Corporate claim must not be able to attach one even if it
    references a validly-Approved pre-authorization for the same patient."""
    tpa_headers = auth_headers(tpa)
    submitted = _submit_pre_auth(client, tpa_headers, patient.id)
    _set_status(client, tpa_headers, submitted["id"], "Approved")

    billing_headers = auth_headers(billing)
    resp = client.post("/api/billing/invoices", json={"patient_id": patient.id, "payer_type": "Corporate"}, headers=billing_headers)
    assert resp.status_code == 201
    invoice = resp.json()

    resp = client.post(f"/api/billing/invoices/{invoice['id']}/claim", json={
        "insurer_or_corporate_name": "Acme Corp", "claim_amount": 5000,
        "pre_authorization_id": submitted["id"],
    }, headers=billing_headers)
    assert resp.status_code == 400, (
        "Corporate claim accepted a pre_authorization_id -- pre-authorization is Insurance-only"
    )
