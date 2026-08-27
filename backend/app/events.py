"""
Minimal in-process domain event bus.

The architecture doc calls for named domain events (CARE_PLAN_ACTIVATED,
TREATMENT_PLAN_SIGNED, TREATMENT_ADMINISTERED, ...) with a "consumers/effect" column --
other parts of the system reacting to a state change, not just performing it. No message
queue, Celery, or background worker exists anywhere in this codebase (see
ARCHITECTURE_NOTES.md), and none is needed for a P0 slice: every publish() call and every
subscriber it invokes run synchronously, in the same request and the same db session as the
triggering write, so a subscriber's side effects commit or roll back atomically with
whatever caused them -- no eventual consistency, no retry/dead-letter handling to build.

publish() does two things every caller used to hand-roll separately at each call site:
  1. Persists a durable DomainEvent row -- the actual event stream, replayable later.
  2. Writes the human-readable CCAJourneyEvent row from the same payload (if patient_id is
     given), eliminating the ad hoc CCAJourneyEvent(...) construction previously duplicated
     at every write endpoint.
Then it invokes every subscriber registered for that event_type, in registration order, each
receiving (db, patient_id=..., actor=..., role=..., **the rest of the payload).

If this ever needs to become genuinely asynchronous (a real queue, a webhook fan-out),
publish() is the only function that would need to change -- callers never construct
CCAJourneyEvent or DomainEvent directly, and subscribers never know whether they were
invoked synchronously or not.
"""
from collections import defaultdict
from typing import Callable, Dict, List

_SUBSCRIBERS: Dict[str, List[Callable]] = defaultdict(list)


def subscribe(event_type: str):
    """Decorator: registers fn(db, patient_id=..., actor=..., role=..., **payload) to run
    whenever event_type is published. Importing the module a subscriber is defined in is
    what registers it -- see routers/cca.py's import of event_subscribers."""
    def decorator(fn: Callable) -> Callable:
        _SUBSCRIBERS[event_type].append(fn)
        return fn
    return decorator


def publish(db, event_type: str, *, patient_id=None, actor=None, role=None,
            title=None, description=None, category=None, **payload):
    from .models_cca import DomainEvent, CCAJourneyEvent

    db.add(DomainEvent(
        event_type=event_type, patient_id=patient_id,
        payload={"actor": actor, "role": role, "title": title, "description": description, **payload},
    ))

    if patient_id is not None:
        db.add(CCAJourneyEvent(
            patient_id=patient_id,
            event_type=event_type,
            event_title=title or event_type.replace("_", " ").title(),
            event_category=category or event_type.split("_")[0],
            description=description or "",
            actor_name=actor,
            actor_role=role,
        ))

    for handler in list(_SUBSCRIBERS.get(event_type, [])):
        handler(db, patient_id=patient_id, actor=actor, role=role, **payload)
