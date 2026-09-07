"""Gate 2 completion: TaskCreated -> EventBus -> Logger -> EventStore.

No UI, no LLM. Proves the backbone later gates (UI, cloud, audit,
telemetry) will consume.
"""

import logging

from ai_ecosystem.core.events import Event, EventBus, InMemoryEventStore
from ai_ecosystem.core.logging import subscribe_logger
from ai_ecosystem.core.models.enums import EventType


def test_task_created_flows_to_logger_and_store(caplog):
    bus, store = EventBus(), InMemoryEventStore()
    subscribe_logger(bus)
    store.attach(bus)

    with caplog.at_level(logging.INFO, logger="ai_ecosystem.events"):
        bus.publish(Event(event_type=EventType.TASK_CREATED, task_id="task-1"))

    stored = store.list()
    assert len(stored) == 1
    assert stored[0].event_type == EventType.TASK_CREATED
    assert stored[0].task_id == "task-1"
    assert any("TaskCreated" in r.message for r in caplog.records)


def test_lifecycle_sequence_stays_ordered_in_store():
    bus, store = EventBus(), InMemoryEventStore()
    store.attach(bus)
    kinds = [
        EventType.TASK_CREATED,
        EventType.PLAN_CREATED,
        EventType.AGENT_STARTED,
        EventType.TOOL_REQUESTED,
        EventType.TOOL_COMPLETED,
        EventType.AGENT_COMPLETED,
    ]
    for kind in kinds:
        bus.publish(Event(event_type=kind, task_id="task-7"))
    assert [e.event_type for e in store.list()] == kinds
