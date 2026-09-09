"""SQLite-backed event store for durable task history and replay."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ai_ecosystem.core.events.bus import Event, EventHandler, EventStore
from ai_ecosystem.core.persistence.sqlite import Database


class SqliteEventStore(EventStore):
    """Durable ordered event store backed by the runtime SQLite database."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, event: Event) -> int:
        cursor = self._db.execute(
            "INSERT INTO events (snapshot) VALUES (?)",
            (event.model_dump_json(),),
        )
        return int(cursor.lastrowid)

    def list(self) -> list[Event]:
        rows = self._db.query("SELECT snapshot FROM events ORDER BY seq")
        return [Event.model_validate_json(row[0]) for row in rows]

    def replay(self, handler: EventHandler) -> int:
        count = 0
        for event in self.list():
            handler(event)
            count += 1
        return count

    def events_since(self, seq: int) -> list[tuple[int, Event]]:
        rows = self._db.query(
            "SELECT seq, snapshot FROM events WHERE seq > ? ORDER BY seq",
            (seq,),
        )
        return [(int(row[0]), Event.model_validate_json(row[1])) for row in rows]

    def append_and_ignore(self, event: Event) -> None:
        self.append(event)

    def attach(self, bus: Any) -> None:
        bus.subscribe_all(self.append_and_ignore)
