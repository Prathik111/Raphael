"""Base model with stable identity and timestamps.

Every domain object carries a unique ``id`` (uuid4 hex) and UTC
``created_at`` / ``updated_at`` so events, audit, and persistence can
correlate records across restarts.
"""

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def new_id() -> str:
    """Generate a stable unique identifier."""
    return uuid4().hex


def utcnow() -> datetime:
    """Current UTC timestamp (timezone-aware)."""
    return datetime.now(timezone.utc)


class Entity(BaseModel):
    """Base class for all domain entities."""

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(default_factory=new_id)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self) -> None:
        """Refresh ``updated_at`` to now."""
        self.updated_at = utcnow()
