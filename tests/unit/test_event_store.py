"""Gate 2: EventStore persistence + replay."""

from ai_ecosystem.core.events import Event, EventBus, InMemoryEventStore
from ai_ecosystem.core.models.enums import EventType


def _event(n, kind=EventType.TASK_CREATED):
    return Event(event_type=kind, task_id=f"t{n}", payload={"n": n})


def test_append_returns_sequence_numbers():
    store = InMemoryEventStore()
    assert store.append(_event(0)) == 0
    assert store.append(_event(1)) == 1


def test_list_preserves_insertion_order():
    store = InMemoryEventStore()
    for n in range(5):
        store.append(_event(n))
    assert [e.payload["n"] for e in store.list()] == [0, 1, 2, 3, 4]


def test_replay_redelivers_in_order():
    store = InMemoryEventStore()
    for n in range(3):
        store.append(_event(n))
    seen = []
    assert store.replay(seen.append) == 3
    assert [e.payload["n"] for e in seen] == [0, 1, 2]


def test_attach_persists_bus_traffic():
    bus, store = EventBus(), InMemoryEventStore()
    store.attach(bus)
    bus.publish(_event(0))
    bus.publish(_event(1, EventType.PLAN_CREATED))
    assert len(store) == 2


def test_attach_single_type_filters():
    bus, store = EventBus(), InMemoryEventStore()
    store.attach(bus, EventType.PLAN_CREATED)
    bus.publish(_event(0, EventType.TASK_CREATED))
    bus.publish(_event(1, EventType.PLAN_CREATED))
    assert len(store) == 1
    assert store.list()[0].event_type == EventType.PLAN_CREATED
