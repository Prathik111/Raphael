"""Gate 2: EventBus -> Logger bridge."""

import logging

from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.logging import format_event, subscribe_logger
from ai_ecosystem.core.models.enums import EventType


def test_format_event_contains_type_and_task():
    line = format_event(Event(event_type=EventType.TASK_CREATED, task_id="t9"))
    assert "TaskCreated" in line and "t9" in line


def test_subscribed_logger_receives_events(caplog):
    bus = EventBus()
    subscribe_logger(bus)
    with caplog.at_level(logging.INFO, logger="ai_ecosystem.events"):
        bus.publish(Event(event_type=EventType.TASK_CREATED, task_id="t1"))
    assert any("TaskCreated" in r.message for r in caplog.records)
