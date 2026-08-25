"""
GET /api/consultations (search/pagination/finalized extension) and GET /api/consultations/analytics
-- both scoped per-doctor (Consultation.user_id), not per-organization, matching this endpoint's
existing convention that a doctor's consultation history is their own. Analytics reports
consultation volume only -- the latency/token-usage fields that used to reflect the AI scribe
pipeline's own telemetry were removed along with that pipeline (see CHANGELOG.md); the
Consultation.gemini_latency/total_tokens columns themselves still exist (unused, no backfill),
so tests can still set them without affecting the analytics assertions below.
"""
import pytest
from datetime import datetime, timedelta

from app.models import Consultation


@pytest.fixture
def doctor(make_user):
    return make_user(email="doctor@consult-search.com", role="Doctor")


@pytest.fixture
def other_doctor(make_user, doctor):
    return make_user(email="doctor2@consult-search.com", role="Doctor", organization_id=doctor.organization_id)


@pytest.fixture
def head_nurse(make_user, doctor):
    return make_user(email="head@consult-search.com", role="HeadNurse", organization_id=doctor.organization_id)


def _make_consultation(db_session, doctor, case_id, patient_name, **overrides):
    fields = dict(
        case_id=case_id, patient_name=patient_name, organization_id=doctor.organization_id,
        user_id=doctor.id, chief_complaint="Cough", primary_diagnosis="Bronchitis",
        gemini_latency=1.5, total_tokens=200,
    )
    fields.update(overrides)
    c = Consultation(**fields)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


def test_search_matches_patient_name_case_insensitively(client, doctor, db_session, auth_headers):
    _make_consultation(db_session, doctor, "case-1", "John Doe")
    _make_consultation(db_session, doctor, "case-2", "Jane Smith")
    resp = client.get("/api/consultations?search=john", headers=auth_headers(doctor))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["consultations"][0]["patient_name"] == "John Doe"


def test_search_substring_match(client, doctor, db_session, auth_headers):
    _make_consultation(db_session, doctor, "case-1", "Alexander Hamilton")
    resp = client.get("/api/consultations?search=hamil", headers=auth_headers(doctor))
    assert resp.json()["total"] == 1


def test_pagination_total_and_limit(client, doctor, db_session, auth_headers):
    for i in range(5):
        _make_consultation(db_session, doctor, f"case-{i}", f"Patient {i}")
    resp = client.get("/api/consultations?limit=2&offset=0", headers=auth_headers(doctor))
    body = resp.json()
    assert body["total"] == 5
    assert len(body["consultations"]) == 2
    resp2 = client.get("/api/consultations?limit=2&offset=4", headers=auth_headers(doctor))
    assert len(resp2.json()["consultations"]) == 1


def test_limit_is_capped_at_200(client, doctor, auth_headers):
    resp = client.get("/api/consultations?limit=99999", headers=auth_headers(doctor))
    assert resp.status_code == 200


def test_finalized_field_reflects_finalize_call(client, doctor, db_session, auth_headers):
    c = _make_consultation(db_session, doctor, "case-1", "Not Yet Finalized")
    resp = client.get("/api/consultations", headers=auth_headers(doctor))
    assert resp.json()["consultations"][0]["finalized"] is False

    client.patch(f"/api/consultations/{c.id}/finalize", json={}, headers=auth_headers(doctor))
    resp2 = client.get("/api/consultations", headers=auth_headers(doctor))
    assert resp2.json()["consultations"][0]["finalized"] is True


def test_consultations_scoped_to_own_doctor_not_whole_org(client, doctor, other_doctor, db_session, auth_headers):
    _make_consultation(db_session, doctor, "case-1", "Doctor A Patient")
    _make_consultation(db_session, other_doctor, "case-2", "Doctor B Patient")
    resp = client.get("/api/consultations", headers=auth_headers(doctor))
    body = resp.json()
    assert body["total"] == 1
    assert body["consultations"][0]["patient_name"] == "Doctor A Patient"


def test_analytics_requires_doctor_role(client, head_nurse, auth_headers):
    resp = client.get("/api/consultations/analytics", headers=auth_headers(head_nurse))
    assert resp.status_code == 403


def test_analytics_route_ordering_does_not_422_for_non_doctor(client, head_nurse, auth_headers):
    """Regression guard: /api/consultations/analytics must resolve to the analytics handler (403
    for a non-doctor) rather than being swallowed by /api/consultations/{consultation_id}'s int
    coercion (which would 422 trying to parse "analytics" as an int)."""
    resp = client.get("/api/consultations/analytics", headers=auth_headers(head_nurse))
    assert resp.status_code == 403


def test_analytics_never_exposes_model_or_provider_name(client, doctor, db_session, auth_headers):
    _make_consultation(db_session, doctor, "case-1", "Patient A")
    resp = client.get("/api/consultations/analytics", headers=auth_headers(doctor))
    body = resp.json()
    serialized = str(body).lower()
    assert "groq" not in serialized
    assert "llama" not in serialized
    assert "model" not in serialized


def test_analytics_aggregates_consultation_volume(client, doctor, db_session, auth_headers):
    _make_consultation(db_session, doctor, "case-1", "P1")
    _make_consultation(db_session, doctor, "case-2", "P2")
    resp = client.get("/api/consultations/analytics", headers=auth_headers(doctor))
    body = resp.json()
    assert body["total_consultations"] == 2
    assert sum(d["count"] for d in body["consultations_per_day"]) == 2


def test_analytics_excludes_consultations_outside_window(client, doctor, db_session, auth_headers):
    old = _make_consultation(db_session, doctor, "case-old", "Old Patient")
    old.created_at = datetime.utcnow() - timedelta(days=60)
    db_session.commit()
    _make_consultation(db_session, doctor, "case-new", "New Patient")
    resp = client.get("/api/consultations/analytics?days=30", headers=auth_headers(doctor))
    assert resp.json()["total_consultations"] == 1


def test_analytics_days_capped_at_90(client, doctor, auth_headers):
    resp = client.get("/api/consultations/analytics?days=99999", headers=auth_headers(doctor))
    assert resp.json()["period_days"] == 90


def test_analytics_scoped_to_own_doctor(client, doctor, other_doctor, db_session, auth_headers):
    _make_consultation(db_session, doctor, "case-1", "Doctor A Patient")
    _make_consultation(db_session, other_doctor, "case-2", "Doctor B Patient")
    _make_consultation(db_session, other_doctor, "case-3", "Doctor B Patient 2")
    resp = client.get("/api/consultations/analytics", headers=auth_headers(doctor))
    assert resp.json()["total_consultations"] == 1
