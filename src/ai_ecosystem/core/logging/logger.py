"""Structured stdlib logging plus the EventBus -> Logger bridge.

Gate 2 completion requires the chain ``TaskCreated -> EventBus -> Logger
-> EventStore`` to work with no UI or LLM. :func:`subscribe_logger`
attaches a handler to a bus so every event is also logged; the
:class:`EventStore` side is wired by the caller (see tests/integration).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_ecosystem.core.events.bus import Event, EventBus

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure a sane default stdlib logging setup (idempotent)."""
    if logging.getLogger().handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger for ``name``."""
    return logging.getLogger(f"ai_ecosystem.{name}")


def format_event(event: Any) -> str:
    """Render an event as a single structured log line."""
    event_type = getattr(event, "event_type", type(event).__name__)
    if isinstance(event_type, Enum):
        event_type = event_type.value
    event_id = getattr(event, "id", "?")
    task_id = getattr(event, "task_id", None)
    payload = getattr(event, "payload", {})
    base = f"event={event_type} id={event_id}"
    if task_id is not None:
        base += f" task={task_id}"
    return f"{base} payload={payload!r}"


def subscribe_logger(bus: EventBus, name: str = "events") -> logging.Logger:
    """Subscribe a logger to *all* events on ``bus``; returns the logger.

    The handler never raises: logging must not break event delivery.
    """
    logger = get_logger(name)

    def _handle(event: Event) -> None:
        try:
            logger.info(format_event(event))
        except Exception:  # noqa: BLE001 -- logging must not break delivery
            pass

    bus.subscribe_all(_handle)
    return logger
