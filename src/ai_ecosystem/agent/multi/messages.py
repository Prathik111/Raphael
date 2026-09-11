"""Structured, bounded agent messaging (Gate 19).

Messages are DATA, never instructions. A payload saying "run X" is
ordinary text; the recipient still plans, validates, and authorizes
everything itself. The bus enforces shape, size, scope, TTL, hop
limits, and duplicate rejection so A->B->A loops cannot run forever.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import Field

from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import Entity, utcnow
from ai_ecosystem.core.models.enums import EventType

MAX_PAYLOAD_BYTES = 64_000


class MessageType(str, Enum):
    """Explicit message categories (no free-form commands)."""

    TASK_REQUEST = "TASK_REQUEST"
    TASK_ACCEPTED = "TASK_ACCEPTED"
    TASK_PROGRESS = "TASK_PROGRESS"
    TASK_RESULT = "TASK_RESULT"
    TASK_FAILURE = "TASK_FAILURE"
    INFORMATION_REQUEST = "INFORMATION_REQUEST"
    INFORMATION_RESPONSE = "INFORMATION_RESPONSE"
    HANDOFF = "HANDOFF"
    VERIFICATION_REQUEST = "VERIFICATION_REQUEST"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    CANCEL_REQUEST = "CANCEL_REQUEST"


class AgentMessage(Entity):
    """One typed, scoped, expiring message between agents."""

    sender: str = ""
    recipient: str = ""
    task_id: str = ""
    message_type: MessageType = MessageType.INFORMATION_RESPONSE
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    correlation_id: str = ""
    ttl_hops: int = 8
    hops: int = 0
    expires_at: datetime | None = None


class MessageBus:
    """Bounded in-process router with validation and loop prevention."""

    def __init__(
        self,
        bus: EventBus | None = None,
        repository: Any | None = None,
        max_payload_bytes: int = MAX_PAYLOAD_BYTES,
        max_inbox: int = 100,
        max_hops: int = 8,
    ) -> None:
        self._bus = bus
        self._repository = repository
        self._max_payload = max_payload_bytes
        self._max_inbox = max_inbox
        self._max_hops = max_hops
        self._inboxes: dict[str, deque[AgentMessage]] = defaultdict(deque)
        self._registered: set[str] = set()
        self._seen_pairs: set[tuple[str, str]] = set()
        self._forwarded: dict[tuple[str, str, str], int] = {}

    def register(self, agent_id: str) -> None:
        """Join an agent to the bus (idempotent)."""
        self._registered.add(agent_id)

    def send(self, message: AgentMessage) -> AgentMessage:
        """Validate, bound, and route one message (raises when invalid)."""
        self._validate(message)
        pair = (message.id, message.recipient)
        if pair in self._seen_pairs:
            raise DomainValidationError(
                f"duplicate message {message.id!r} to {message.recipient!r}"
            )
        inbox = self._inboxes[message.recipient]
        if len(inbox) >= self._max_inbox:
            raise DomainValidationError(
                f"inbox for {message.recipient!r} is full ({self._max_inbox})"
            )
        self._seen_pairs.add(pair)
        inbox.append(message)
        if self._repository is not None:
            self._repository.create(message)
        self._emit(
            EventType.AGENT_MESSAGE_SENT,
            message.task_id,
            {
                "message_id": message.id,
                "sender": message.sender,
                "recipient": message.recipient,
                "type": message.message_type.value,
            },
        )
        return message

    def receive(self, agent_id: str) -> AgentMessage | None:
        """Pop the oldest message for an agent (None when empty)."""
        self._require_registered(agent_id)
        inbox = self._inboxes[agent_id]
        if not inbox:
            return None
        message = inbox.popleft()
        if self._expired(message):
            self._emit(
                EventType.AGENT_MESSAGE_REJECTED,
                message.task_id,
                {"message_id": message.id, "reason": "expired on receive"},
            )
            return self.receive(agent_id)
        self._emit(
            EventType.AGENT_MESSAGE_RECEIVED,
            message.task_id,
            {"message_id": message.id, "recipient": agent_id},
        )
        return message

    def forward(self, message: AgentMessage, recipient: str) -> AgentMessage:
        """Relay with hop accounting (loop prevention enforced).

        Relays keep the message id but record the (id, recipient) pair,
        so fan-out to several recipients works while true duplicates
        (same id to the same recipient) are still rejected. Each relay
        consumes one ttl_hops.
        """
        if message.hops + 1 > self._max_hops:
            raise DomainValidationError(
                f"message {message.id!r} exceeded {self._max_hops} hops"
            )
        if message.ttl_hops <= 1:
            raise DomainValidationError(f"message {message.id!r} ttl exhausted")
        key = (message.correlation_id or message.id, message.sender, recipient)
        self._forwarded[key] = self._forwarded.get(key, 0) + 1
        if self._forwarded[key] > self._max_hops:
            raise DomainValidationError(
                f"forwarding loop detected for {message.correlation_id!r}"
            )
        relayed = message.model_copy(
            update={
                "recipient": recipient,
                "hops": message.hops + 1,
                "ttl_hops": message.ttl_hops - 1,
            }
        )
        relayed.id = message.id
        return self.send(relayed)

    def pending(self, agent_id: str) -> int:
        """Queued message count for an agent."""
        self._require_registered(agent_id)
        return len(self._inboxes[agent_id])

    def handoff(
        self,
        sender: str,
        recipient: str,
        task_id: str,
        result_summary: str,
        correlation_id: str = "",
    ) -> AgentMessage:
        """Transfer a task result as validated data (never as orders)."""
        message = self.send(
            AgentMessage(
                sender=sender,
                recipient=recipient,
                task_id=task_id,
                message_type=MessageType.HANDOFF,
                payload={"result": result_summary},
                correlation_id=correlation_id or task_id,
            )
        )
        self._emit(
            EventType.AGENT_HANDOFF,
            task_id,
            {"message_id": message.id, "sender": sender, "recipient": recipient},
        )
        return message

    def history(self, correlation_id: str) -> list[AgentMessage]:
        """Reconstruct one conversation (live inboxes + persisted sends).

        Received (consumed) messages leave the inbox, so history merges
        the durable repository when one is configured; entries dedupe
        by message id and order by creation time.
        """
        found: dict[str, AgentMessage] = {}
        if self._repository is not None:
            try:
                stored = self._repository.list()
            except Exception:  # noqa: BLE001 -- history degrades, never fails
                stored = []
            for message in stored:
                if message.correlation_id == correlation_id:
                    found[message.id] = message
        for inbox in self._inboxes.values():
            for message in inbox:
                if message.correlation_id == correlation_id:
                    found[message.id] = message
        return sorted(found.values(), key=lambda m: (m.created_at, m.id))

    # -- validation ------------------------------------------------------

    def _validate(self, message: AgentMessage) -> None:
        if not message.sender or message.sender not in self._registered:
            self._reject(message, f"unknown sender {message.sender!r}")
            raise DomainValidationError(f"unknown sender {message.sender!r}")
        if not message.recipient or message.recipient not in self._registered:
            self._reject(message, f"unknown recipient {message.recipient!r}")
            raise DomainValidationError(f"unknown recipient {message.recipient!r}")
        if not message.task_id:
            self._reject(message, "missing task scope")
            raise DomainValidationError("message has no task scope")
        size = len(message.model_dump_json().encode("utf-8"))
        if size > self._max_payload:
            self._reject(message, f"payload {size} bytes exceeds limit")
            raise DomainValidationError("message exceeds size limit")
        if message.ttl_hops < 1:
            self._reject(message, "ttl exhausted")
            raise DomainValidationError("message ttl exhausted")
        if self._expired(message):
            self._reject(message, "message expired")
            raise DomainValidationError("message expired")

    @staticmethod
    def _expired(message: AgentMessage) -> bool:
        return message.expires_at is not None and utcnow() > message.expires_at

    def _require_registered(self, agent_id: str) -> None:
        if agent_id not in self._registered:
            raise DomainValidationError(f"unknown agent {agent_id!r}")

    def _reject(self, message: AgentMessage, reason: str) -> None:
        self._emit(
            EventType.AGENT_MESSAGE_REJECTED,
            message.task_id,
            {"message_id": message.id, "reason": reason},
        )

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload)
            )


def expire_in(seconds: float) -> datetime:
    """Convenience: an expiry timestamp relative to now."""
    return utcnow() + timedelta(seconds=seconds)
