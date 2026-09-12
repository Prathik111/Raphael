"""Scoped, persistent, auditable memory store (Gate 12).

Memory is DATA and never grants authority. Retrieval is deterministic,
local keyword scoring with relevance, confidence, importance, freshness,
and explicit trust/provenance metadata.
"""

from __future__ import annotations

import re
from datetime import datetime

from ai_ecosystem.core.errors.exceptions import DomainValidationError, ResourceNotFoundError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.models.domain import Memory
from ai_ecosystem.core.models.enums import EventType, MemoryScope, MemoryStatus, MemoryType
from ai_ecosystem.core.persistence.repositories import MemoryRepository
from ai_ecosystem.core.persistence.sqlite import Database
from ai_ecosystem.personalization.memory.models import ConsolidationProposal, MemoryCandidate

_STOPWORDS = frozenset(
    ["a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for", "with", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that", "these", "those", "as", "at", "by", "from", "into", "over", "after", "such", "no", "not", "only", "also", "than", "then", "there", "their", "what", "when", "where", "which", "who", "will", "can", "has", "have", "had", "do", "does", "did", "how", "why"]
)


def keywords(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if word not in _STOPWORDS and len(word) > 2
    }


def _freshness(created_at: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    return round(1.0 / (1.0 + age_days / 30.0), 3)


class MemoryStore:
    """Manages memory lifecycle over a MemoryRepository."""

    def __init__(
        self,
        repository: MemoryRepository,
        bus: EventBus | None = None,
        importance_threshold: float = 0.3,
        database: Database | None = None,
    ) -> None:
        self._repo = repository
        self._bus = bus
        self._threshold = importance_threshold
        self._db = database

    def propose(self, candidate: MemoryCandidate) -> MemoryCandidate:
        if not candidate.content.strip():
            raise DomainValidationError("memory candidate has no content")
        for field in ("confidence", "importance"):
            value = getattr(candidate, field)
            if not 0.0 <= value <= 1.0:
                raise DomainValidationError(f"memory candidate {field} {value} outside [0, 1]")
        self._emit(
            EventType.MEMORY_CANDIDATE_CREATED,
            "",
            {
                "candidate_id": candidate.id,
                "type": candidate.type.value,
                "scope": candidate.scope.value,
                "reason": candidate.reason,
            },
        )
        return candidate

    def evaluate(self, candidate: MemoryCandidate) -> tuple[bool, str]:
        if candidate.importance < self._threshold:
            return (
                False,
                f"importance {candidate.importance:.2f} below threshold {self._threshold:.2f}",
            )
        if candidate.confidence <= 0.0:
            return False, "confidence must be positive"
        return True, "meets importance and confidence bars"

    def store(self, candidate: MemoryCandidate) -> Memory:
        self.propose(candidate)
        accepted, reason = self.evaluate(candidate)
        if not accepted:
            raise DomainValidationError(f"candidate rejected: {reason}")
        related = [m.id for m in self.find_conflicts(candidate)]
        metadata = dict(candidate.metadata)
        metadata.update({"reason": candidate.reason, "related": related, "history": []})
        memory = Memory(
            type=candidate.type,
            content=candidate.content,
            source=candidate.source,
            confidence=candidate.confidence,
            importance=candidate.importance,
            scope=candidate.scope,
            scope_id=candidate.scope_id,
            status=MemoryStatus.ACTIVE,
            retention_days=candidate.metadata.get("retention_days"),
            cloud_eligible=bool(candidate.metadata.get("cloud_eligible", False)),
            provenance=str(candidate.metadata.get("provenance", candidate.source or "unknown")),
            created_by=str(candidate.metadata.get("created_by", "unknown")),
            verified=bool(candidate.metadata.get("verified", False)),
            expires_at=candidate.metadata.get("expires_at"),
            metadata=metadata,
        )
        if self._db is not None:
            with self._db.transaction():
                created = self._repo.create(memory)
        else:
            created = self._repo.create(memory)
        self._emit(
            EventType.MEMORY_CREATED,
            "",
            {
                "memory_id": created.id,
                "type": created.type.value,
                "scope": created.scope.value,
                "scope_id": created.scope_id,
                "related": related,
                "verified": created.verified,
            },
        )
        return created

    def retrieve(
        self,
        scope: MemoryScope,
        scope_id: str = "",
        memory_type: MemoryType | None = None,
        query: str = "",
        limit: int = 10,
        project_id: str = "",
        include_archived: bool = False,
    ) -> list[Memory]:
        """Retrieve relevant, non-expired memories. Results remain untrusted DATA."""
        query_words = keywords(query)
        now = utcnow()
        scored: list[tuple[float, str, str, Memory]] = []
        for memory in self._repo.list():
            if memory.status is MemoryStatus.DELETED or (
                memory.status is MemoryStatus.ARCHIVED and not include_archived
            ):
                continue
            if memory.expires_at is not None and memory.expires_at <= now:
                continue
            if not self._visible(memory, scope, scope_id, project_id):
                continue
            if memory_type is not None and memory.type is not memory_type:
                continue
            relevance = self._relevance(query_words, memory)
            if query_words and relevance <= 0.0:
                continue
            trust = 1.0 if memory.verified else 0.0
            score = (
                0.45 * relevance
                + 0.2 * memory.importance
                + 0.15 * memory.confidence
                + 0.1 * _freshness(memory.created_at, now)
                + 0.1 * trust
            )
            scored.append((round(score, 3), memory.created_at.isoformat(), memory.id, memory))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [memory for _, _, _, memory in scored[: max(0, limit)]]

    @staticmethod
    def _visible(memory: Memory, scope: MemoryScope, scope_id: str, project_id: str) -> bool:
        if memory.scope is MemoryScope.GLOBAL:
            return True
        if memory.scope is scope and memory.scope_id == scope_id:
            return True
        return bool(
            project_id and memory.scope is MemoryScope.PROJECT and memory.scope_id == project_id
        )

    @staticmethod
    def _relevance(query_words: set[str], memory: Memory) -> float:
        if not query_words:
            return 0.0
        return len(query_words & keywords(memory.content)) / len(query_words)

    def find_conflicts(self, candidate: MemoryCandidate, limit: int = 5) -> list[Memory]:
        candidate_words = keywords(candidate.content)
        hits: list[tuple[int, Memory]] = []
        for memory in self._repo.list():
            if (
                memory.status is not MemoryStatus.ACTIVE
                or memory.scope is not candidate.scope
                or memory.scope_id != candidate.scope_id
                or memory.type is not candidate.type
            ):
                continue
            overlap = len(candidate_words & keywords(memory.content))
            if overlap >= 2:
                hits.append((overlap, memory))
        hits.sort(key=lambda item: (-item[0], item[1].created_at.isoformat()))
        return [memory for _, memory in hits[: max(0, limit)]]

    def update(
        self,
        memory_id: str,
        content: str | None = None,
        confidence: float | None = None,
        importance: float | None = None,
    ) -> Memory:
        memory = self._repo.get(memory_id)
        if memory is None:
            raise ResourceNotFoundError("Memory", memory_id)
        changed = any(value is not None for value in (content, confidence, importance))
        history = list(memory.metadata.get("history", []))
        history.append(
            {
                "content": memory.content,
                "confidence": memory.confidence,
                "importance": memory.importance,
                "verified": memory.verified,
                "updated_at": memory.updated_at.isoformat(),
            }
        )
        if content is not None:
            if not content.strip():
                raise DomainValidationError("memory content must not be empty")
            memory.content = content
        if confidence is not None:
            if not 0.0 <= confidence <= 1.0:
                raise DomainValidationError(f"confidence {confidence} outside [0, 1]")
            memory.confidence = confidence
        if importance is not None:
            if not 0.0 <= importance <= 1.0:
                raise DomainValidationError(f"importance {importance} outside [0, 1]")
            memory.importance = importance
        if changed:
            # Verification is a statement about the current content. Any mutation
            # invalidates that statement and preserves an explicit provenance trail.
            memory.verified = False
            memory.provenance = f"modified:{memory.provenance or 'unknown'}"
            memory.metadata["verification_invalidated"] = True
            memory.metadata["verification_invalidated_at"] = utcnow().isoformat()
        memory.metadata["history"] = history
        memory.touch()
        updated = self._repo.update(memory)
        self._emit(
            EventType.MEMORY_UPDATED,
            "",
            {
                "memory_id": memory_id,
                "history": len(history),
                "verified": updated.verified,
            },
        )
        return updated

    def archive(self, memory_id: str) -> Memory:
        memory = self._repo.get(memory_id)
        if memory is None:
            raise ResourceNotFoundError("Memory", memory_id)
        memory.status = MemoryStatus.ARCHIVED
        memory.touch()
        archived = self._repo.update(memory)
        self._emit(EventType.MEMORY_ARCHIVED, "", {"memory_id": memory_id})
        return archived

    def delete(self, memory_id: str) -> bool:
        removed = self._repo.delete(memory_id)
        if removed:
            self._emit(EventType.MEMORY_DELETED, "", {"memory_id": memory_id})
        return removed

    def propose_consolidation(
        self, ids: list[str], new_candidate: MemoryCandidate, reason: str
    ) -> ConsolidationProposal:
        if len(ids) < 2:
            raise DomainValidationError("consolidation needs at least 2 memories")
        olds = []
        for memory_id in ids:
            memory = self._repo.get(memory_id)
            if memory is None:
                raise ResourceNotFoundError("Memory", memory_id)
            if memory.status is not MemoryStatus.ACTIVE:
                raise DomainValidationError(f"memory {memory_id} is not ACTIVE")
            olds.append(memory)
        scopes = {(m.scope, m.scope_id) for m in olds}
        if len(scopes) != 1:
            raise DomainValidationError("cannot consolidate across scopes")
        if (new_candidate.scope, new_candidate.scope_id) != next(iter(scopes)):
            raise DomainValidationError("consolidated memory must match source scope")
        return ConsolidationProposal(new_candidate=new_candidate, archive_ids=ids, reason=reason)

    def apply_consolidation(self, proposal: ConsolidationProposal) -> Memory:
        fresh = self.propose_consolidation(
            list(proposal.archive_ids), proposal.new_candidate, proposal.reason
        )
        created = self.store(fresh.new_candidate)
        created.metadata["consolidates"] = list(fresh.archive_ids)
        self._repo.update(created)
        for memory_id in fresh.archive_ids:
            self.archive(memory_id)
        return created

    def purge_expired(self, now: datetime | None = None) -> list[str]:
        moment = now or utcnow()
        purged = []
        for memory in self._repo.list():
            expired_by_date = memory.expires_at is not None and memory.expires_at <= moment
            expired_by_retention = (
                memory.retention_days is not None
                and (moment - memory.created_at).total_seconds() / 86400.0 > memory.retention_days
            )
            if (expired_by_date or expired_by_retention) and self.delete(memory.id):
                purged.append(memory.id)
        return purged

    def list_cloud_eligible(self) -> list[Memory]:
        now = utcnow()
        return [
            m
            for m in self._repo.list()
            if m.status is MemoryStatus.ACTIVE
            and m.cloud_eligible
            and not (m.expires_at and m.expires_at <= now)
        ]

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(Event(event_type=event_type, task_id=task_id, payload=payload))
