"""Repository interfaces (BUILD_PLAN Gate 3 contract, Part 1 declares only).

Part 1 rule: interfaces frozen here so Gates 2+ can be written against
them; Gate 3 (Part 2) provides the relational implementation plus
transactions, migrations, and crash recovery. No storage code here.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Generic, TypeVar

from ai_ecosystem.core.models.domain import (
    Agent,
    Device,
    ExecutionContext,
    Memory,
    Plan,
    Skill,
    Task,
    VerificationResult,
)

if TYPE_CHECKING:
    from ai_ecosystem.agent.multi.messages import AgentMessage  # noqa: F401
    from ai_ecosystem.learning.models import (  # noqa: F401
        LearningProposal,
        UsageEvent,
        UsagePattern,
    )
    from ai_ecosystem.system.monitor.models import SystemSnapshot  # noqa: F401

T = TypeVar("T")


class Repository(ABC, Generic[T]):
    """Minimal CRUD contract every repository follows."""

    @abstractmethod
    def create(self, item: T) -> T:
        """Persist a new item and return it."""
        raise NotImplementedError

    @abstractmethod
    def get(self, item_id: str) -> T | None:
        """Return the item, or None when it does not exist."""
        raise NotImplementedError

    @abstractmethod
    def update(self, item: T) -> T:
        """Replace the stored item with the same id and return it."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, item_id: str) -> bool:
        """Remove the item; True when something was removed."""
        raise NotImplementedError

    @abstractmethod
    def list(self) -> list[T]:
        """All stored items (insertion order where meaningful)."""
        raise NotImplementedError


class TaskRepository(Repository[Task], ABC):
    """Durable task records."""


class PlanRepository(Repository[Plan], ABC):
    """Durable plans."""


class ExecutionContextRepository(ABC):
    """Durable execution contexts, keyed by task.

    Gate 3's critical test (kill mid-execution, restart, resume) is
    implemented against this interface.
    """

    @abstractmethod
    def save(self, context: ExecutionContext) -> ExecutionContext:
        """Insert or replace the context for ``context.task_id``."""
        raise NotImplementedError

    @abstractmethod
    def load(self, task_id: str) -> ExecutionContext | None:
        """Latest context for ``task_id``, or None when unknown."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, task_id: str) -> bool:
        """Drop the context; True when something was removed."""
        raise NotImplementedError


class EventRepository(ABC):
    """Durable event log backing :class:`EventStore` semantics."""

    @abstractmethod
    def append_snapshot(self, snapshot: str) -> int:
        """Persist a serialized event; return its sequence number."""
        raise NotImplementedError

    @abstractmethod
    def list_snapshots(self) -> list[str]:
        """All serialized events in insertion order."""
        raise NotImplementedError


class MemoryRepository(Repository[Memory], ABC):
    """Durable memory records (Gate 12 pipeline stores through this)."""


class SkillRepository(Repository[Skill], ABC):
    """Durable versioned skills (Gate 19 registry builds on this)."""


class AgentRepository(Repository[Agent], ABC):
    """Durable agent identities (Gate 15 lifecycle manages these)."""


class DeviceRepository(Repository[Device], ABC):
    """Durable device records (Gates 23-24 sync these)."""


class VerificationRepository(Repository[VerificationResult], ABC):
    """Durable verification outcomes (Gate 9 verifier stores through this)."""


class SnapshotRepository(Repository["SystemSnapshot"], ABC):
    """Durable system snapshots (Gate 15 awareness stores through this)."""


class UsageEventRepository(Repository["UsageEvent"], ABC):
    """Durable usage events (Gate 16 observation stores through this)."""


class UsagePatternRepository(Repository["UsagePattern"], ABC):
    """Durable usage patterns (Gate 16 detection stores through this)."""


class LearningProposalRepository(Repository["LearningProposal"], ABC):
    """Durable learning proposals (Gate 16 review queue)."""


class MessageRepository(Repository["AgentMessage"], ABC):
    """Durable agent messages (Gate 19 conversation audit)."""
