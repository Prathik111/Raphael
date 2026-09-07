"""Gate 2: EventBus semantics (ordering, fan-out, isolation)."""

import pytest

from ai_ecosystem.core.errors import EventBusError, SubscriberError
from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models.enums import EventType


def _event(kind=EventType.TASK_CREATED, task_id="t1"):
    return Event(event_type=kind, task_id=task_id, payload={"k": "v"})


def test_publisher_to_subscriber():
    bus, received = EventBus(), []
    bus.subscribe(EventType.TASK_CREATED, received.append)
    assert bus.publish(_event()) == 1
    assert len(received) == 1
    assert received[0].task_id == "t1"


def test_publisher_to_multiple_subscribers():
    bus = EventBus()
    a, b = [], []
    bus.subscribe(EventType.TASK_CREATED, a.append)
    bus.subscribe(EventType.TASK_CREATED, b.append)
    bus.publish(_event())
    assert len(a) == len(b) == 1


def test_type_isolation():
    bus, received = EventBus(), []
    bus.subscribe(EventType.PLAN_CREATED, received.append)
    bus.publish(_event(EventType.TASK_CREATED))
    assert received == []


def test_event_ordering_preserved():
    bus, order = EventBus(), []
    bus.subscribe(EventType.TOOL_STARTED, lambda e: order.append(e.payload["n"]))
    for n in range(10):
        bus.publish(Event(event_type=EventType.TOOL_STARTED, payload={"n": n}))
    assert order == list(range(10))


def test_failed_subscriber_isolated_and_reported():
    errors = []
    bus = EventBus(on_error=errors.append)
    calls = []

    def bad(event):
        raise RuntimeError("boom")

    bus.subscribe(EventType.TASK_CREATED, bad)
    bus.subscribe(EventType.TASK_CREATED, calls.append)
    bus.publish(_event())
    assert len(calls) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], SubscriberError)


def test_unsubscribe():
    bus, received = EventBus(), []
    bus.subscribe(EventType.TASK_CREATED, received.append)
    assert bus.unsubscribe(EventType.TASK_CREATED, received.append) is True
    bus.publish(_event())
    assert received == []
    assert bus.unsubscribe(EventType.TASK_CREATED, received.append) is False


def test_non_callable_rejected():
    bus = EventBus()
    with pytest.raises(EventBusError):
        bus.subscribe(EventType.TASK_CREATED, "not-a-function")


def test_published_count():
    bus = EventBus()
    bus.publish(_event())
    bus.publish(_event())
    assert bus.published_count == 2
