"""Deployment configuration: dev / testing / production (Gate 43).

One model, environment overrides, no duplication. Secrets are never
part of configuration -- only non-sensitive wiring (paths, levels,
limits) lives here; credentials stay in SecretsProvider.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, model_validator

Environment = Literal["development", "testing", "production"]


class AppConfig(BaseModel):
    """Central runtime configuration (no secrets, ever)."""

    environment: Environment = "development"
    db_path: str = "ai_ecosystem.db"
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    log_level: str = "INFO"
    lease_timeout_s: float = 60.0
    max_retries: int = 3
    sync_max_retries: int = 3
    scheduler_workers: int = 4
    allow_shells: bool = False
    max_workers: int = 2
    max_queued_tasks: int = 16
    # Human-approval floor: "" disables approvals (auto-grant behavior);
    # "HIGH"/"CRITICAL" (or "MEDIUM") pauses matching calls until a
    # human approves the exact arguments (AI_ECO_REQUIRE_APPROVAL).
    require_approval: str = ""

    @model_validator(mode="after")
    def _production_bind_loopback(self) -> AppConfig:
        if self.environment == "production" and self.api_host not in (
                "127.0.0.1", "localhost", "::1"):
            raise ValueError(
                f"production api_host {self.api_host!r} is not loopback; "
                "the local API has no auth layer")
        return self

    @classmethod
    def from_env(cls, prefix: str = "AI_ECO_") -> AppConfig:
        """Build from environment variables (AI_ECO_* by default)."""
        from ai_ecosystem.core.errors.exceptions import DomainValidationError

        values: dict[str, str] = {}
        fields = ("environment", "db_path", "api_host", "api_port",
                  "log_level", "lease_timeout_s", "max_retries",
                  "sync_max_retries", "scheduler_workers", "allow_shells",
                  "max_workers", "max_queued_tasks", "require_approval")
        for field in fields:
            raw = os.environ.get(f"{prefix}{field.upper()}")
            if raw is not None:
                values[field] = raw
        try:
            return cls.model_validate(values)
        except ValueError as exc:
            raise DomainValidationError(f"invalid configuration: {exc}") from exc

    def is_production(self) -> bool:
        """True only for the production environment."""
        return self.environment == "production"
