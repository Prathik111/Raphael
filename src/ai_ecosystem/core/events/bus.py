"""Event-driven backbone (BUILD_PLAN Gate 2).

Synchronous, in-order, in-memory delivery. Later gates may add a durable
transport, but they must preserve these semantics:

* publisher -> subscriber(s) in subscription order
* a failing subscriber never blocks the others (isolated, reported)
* every published event can be persisted and replayed in order
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Optional

from pydantic import ConfigDict, Field

from ai_ecosystem.core.errors.exceptions import EventBusError, SubscriberError
from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.core.models.enums import EventType

EventHandler = Callable[["Event"], None]
ErrorHandler = Callable[[SubscriberError], None]


class Event(Entity):
    """An immutable fact emitted by the runtime."""

    model_config = ConfigDict(frozen=True)

    event_type: EventType = EventType.TASK_CREATED
    task_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    """Synchronous in-memory publisher/subscriber bus (thread-safe)."""

    def __init__(self, on_error: Optional[ErrorHandler] = None) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._global_handlers: list[EventHandler] = []
        self._on_error = on_error
        self._published = 0

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Register ``handler`` for one event type (idempotent per pair)."""
        if not callable(handler):
            raise EventBusError(f"handler for {event_type} is not callable")
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Register ``handler`` for every event type (loggers, stores)."""
        if not callable(handler):
            raise EventBusError("global handler is not callable")
        with self._lock:
            if handler not in self._global_handlers:
                self._global_handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> bool:
        """Remove a subscription; True if one was removed."""
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)
                return True
            return False

    def publish(self, event: Event) -> int:
        """Deliver ``event`` to matching + global handlers in order.

        Returns the number of handlers invoked (including ones that
        fail). A raising handler is isolated: delivery continues and
        the failure is reported via the ``on_error`` callback, which
        itself can never break delivery.
        """
        with self._lock:
            targets = list(self._handlers.get(event.event_type, []))
            targets.extend(self._global_handlers)
            self._published += 1
        delivered = 0
        for handler in targets:
            delivered += 1
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001 - isolation is the point
                name = getattr(handler, "__name__", type(handler).__name__)
                report = SubscriberError(name, exc)
                if self._on_error is not None:
                    try:
                        self._on_error(report)
                    except Exception:  # noqa: BLE001 - isolation includes callback
                        pass
        return delivered

    @property
    def published_count(self) -> int:
        """Total events published through this bus."""
        with self._lock:
            return self._published


class EventStore(ABC):
    """Durable ordered event log (Gate 3 provides the database backend)."""

    @abstractmethod
    def append(self, event: Event) -> int:
        """Persist ``event``; return its sequence number."""
        raise NotImplementedError

    @abstractmethod
    def list(self) -> list[Event]:
        """All stored events in insertion order."""
        raise NotImplementedError

    @abstractmethod
    def replay(self, handler: EventHandler) -> int:
        """Re-deliver every stored event to ``handler``; return count."""
        raise NotImplementedError


class InMemoryEventStore(EventStore):
    """In-process store used by tests and the pre-database runtime."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[Event] = []

    def append(self, event: Event) -> int:
        with self._lock:
            self._events.append(event)
            return len(self._events) - 1

    def list(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def replay(self, handler: EventHandler) -> int:
        with self._lock:
            snapshot = list(self._events)
        count = 0
        for event in snapshot:
            handler(event)
            count += 1
        return count

    def attach(self, bus: EventBus, event_type: Optional[EventType] = None) -> None:
        """Persist everything (or one type) published on ``bus``."""
        if event_type is None:
            bus.subscribe_all(self.append_and_ignore)
        else:
            bus.subscribe(event_type, self.append_and_ignore)

    def append_and_ignore(self, event: Event) -> None:
        """``subscribe``-compatible wrapper discarding the sequence number."""
        self.append(event)

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)
