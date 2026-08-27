"""
Unit tests for backend/app/events.py -- the in-process domain event bus itself, independent
of any specific subscriber. Uses dedicated, namespaced event-type strings so a registered
test subscriber can't collide with (or get invoked by) any real production event.
"""
from app import events
from app.models_cca import DomainEvent, CCAJourneyEvent


def test_publish_persists_a_durable_domain_event(db_session):
    events.publish(db_session, "TEST_EVENT_PERSISTED", patient_id=None, actor="tester@x.com", role="Doctor", foo="bar")
    db_session.flush()  # this app's sessionmaker uses autoflush=False (database.py)
    row = db_session.query(DomainEvent).filter(DomainEvent.event_type == "TEST_EVENT_PERSISTED").first()
    assert row is not None
    assert row.payload["foo"] == "bar"
    assert row.payload["actor"] == "tester@x.com"


def test_publish_writes_a_journey_event_only_when_patient_id_given(db_session):
    events.publish(db_session, "TEST_EVENT_WITH_PATIENT", patient_id=999999, actor="tester@x.com", role="Doctor",
                    title="Test Title", description="Test description", category="TEST")
    db_session.flush()  # this app's sessionmaker uses autoflush=False (database.py)
    je = db_session.query(CCAJourneyEvent).filter(CCAJourneyEvent.event_type == "TEST_EVENT_WITH_PATIENT").first()
    assert je is not None
    assert je.event_title == "Test Title"
    assert je.event_category == "TEST"
    assert je.description == "Test description"

    before = db_session.query(CCAJourneyEvent).count()
    events.publish(db_session, "TEST_EVENT_NO_PATIENT", patient_id=None, actor="tester@x.com", role="Doctor")
    db_session.flush()
    after = db_session.query(CCAJourneyEvent).count()
    assert after == before  # no patient_id -> no journey event
    assert db_session.query(DomainEvent).filter(DomainEvent.event_type == "TEST_EVENT_NO_PATIENT").count() == 1  # but still a durable DomainEvent


def test_subscribers_are_invoked_with_the_full_payload(db_session):
    calls = []

    @events.subscribe("TEST_EVENT_SUBSCRIBED")
    def _handler(db, patient_id=None, actor=None, role=None, **payload):
        calls.append((db, patient_id, actor, role, payload))

    events.publish(db_session, "TEST_EVENT_SUBSCRIBED", patient_id=42, actor="a@x.com", role="Doctor", widget="gadget")

    assert len(calls) == 1
    call_db, call_patient_id, call_actor, call_role, call_payload = calls[0]
    assert call_db is db_session
    assert call_patient_id == 42
    assert call_actor == "a@x.com"
    assert call_role == "Doctor"
    assert call_payload == {"widget": "gadget"}


def test_multiple_subscribers_for_the_same_event_all_run_in_order(db_session):
    calls = []

    @events.subscribe("TEST_EVENT_MULTI")
    def _first(db, **_):
        calls.append("first")

    @events.subscribe("TEST_EVENT_MULTI")
    def _second(db, **_):
        calls.append("second")

    events.publish(db_session, "TEST_EVENT_MULTI", patient_id=None)
    assert calls == ["first", "second"]


def test_unsubscribed_event_type_has_no_handlers_and_does_not_error(db_session):
    events.publish(db_session, "TEST_EVENT_WITH_NO_SUBSCRIBERS", patient_id=None)  # must not raise
