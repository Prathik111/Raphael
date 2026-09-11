"""Human approval requests bound to exact actions."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import timedelta
from typing import Any

from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.core.models.domain import ApprovalRequest
from ai_ecosystem.core.models.enums import ApprovalStatus


def approval_hash(tool_name: str, arguments: dict[str, Any], policy_name: str) -> str:
    canonical = json.dumps(
        {"tool": tool_name, "arguments": arguments, "policy": policy_name},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ApprovalStore:
    """Approval lifecycle with in-process atomic decision semantics."""

    def __init__(self, repository: Any = None, default_ttl_s: float = 900.0) -> None:
        if default_ttl_s <= 0:
            from ai_ecosystem.core.errors.exceptions import DomainValidationError

            raise DomainValidationError("approval TTL must be positive")
        self._repo = repository
        self._memory: dict[str, ApprovalRequest] = {}
        self._default_ttl = default_ttl_s
        self._lock = threading.RLock()

    def _save(self, request: ApprovalRequest) -> ApprovalRequest:
        if self._repo is not None:
            return self._repo.update(request)
        request.touch()
        self._memory[request.id] = request
        return request

    def _load(self, approval_id: str) -> ApprovalRequest | None:
        if self._repo is not None:
            return self._repo.get(approval_id)
        return self._memory.get(approval_id)

    def request(
        self,
        task_id: str,
        tool_name: str,
        arguments: dict,
        risk: str,
        reason: str,
        policy_name: str,
        ttl_s: float | None = None,
    ) -> ApprovalRequest:
        wanted = approval_hash(tool_name, dict(arguments), policy_name)
        with self._lock:
            for existing in self.pending(task_id=task_id):
                if existing.tool == tool_name and existing.arguments_hash == wanted:
                    return existing
            now = utcnow()
            ttl = self._default_ttl if ttl_s is None else ttl_s
            if ttl <= 0:
                from ai_ecosystem.core.errors.exceptions import DomainValidationError

                raise DomainValidationError("approval TTL must be positive")
            request = ApprovalRequest(
                task_id=task_id,
                tool=tool_name,
                arguments=dict(arguments),
                arguments_hash=wanted,
                risk=risk,
                reason=reason,
                policy=policy_name,
                status=ApprovalStatus.PENDING,
                expires_at=now + timedelta(seconds=ttl),
            )
            if self._repo is not None:
                return self._repo.create(request)
            request.touch()
            self._memory[request.id] = request
            return request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            return self._load(approval_id)

    def find_valid(
        self, task_id: str, tool_name: str, arguments: dict, policy_name: str
    ) -> ApprovalRequest | None:
        with self._lock:
            for request in self.all():
                if request.task_id != task_id or request.tool != tool_name:
                    continue
                if self.verify(request.id, tool_name, arguments, policy_name):
                    return request
        return None

    def all(self) -> list[ApprovalRequest]:
        return self._repo.list() if self._repo is not None else list(self._memory.values())

    def pending(self, task_id: str = "") -> list[ApprovalRequest]:
        with self._lock:
            self.sweep_expired()
            all_requests = (
                self._repo.list() if self._repo is not None else list(self._memory.values())
            )
            return [
                r
                for r in all_requests
                if r.status is ApprovalStatus.PENDING and (not task_id or r.task_id == task_id)
            ]

    def decide(
        self, approval_id: str, approved: bool, decided_by: str = "operator"
    ) -> ApprovalRequest:
        from ai_ecosystem.core.errors.exceptions import DomainValidationError

        with self._lock:
            request = self._load(approval_id)
            if request is None:
                raise DomainValidationError(f"unknown approval {approval_id!r}")
            self._expire_one(request)
            if request.status is not ApprovalStatus.PENDING:
                raise DomainValidationError(
                    f"approval {approval_id!r} is already {request.status.value}"
                )
            request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
            request.decided_at = utcnow()
            request.decided_by = decided_by
            return self._save(request)

    def verify(self, approval_id: str, tool_name: str, arguments: dict, policy_name: str) -> bool:
        with self._lock:
            request = self._load(approval_id)
            if request is None:
                return False
            self._expire_one(request)
            if request.status is not ApprovalStatus.APPROVED or request.tool != tool_name:
                return False
            return request.arguments_hash == approval_hash(tool_name, dict(arguments), policy_name)

    def _expire_one(self, request: ApprovalRequest) -> None:
        if (
            request.status is ApprovalStatus.PENDING
            and request.expires_at is not None
            and request.expires_at <= utcnow()
        ):
            request.status = ApprovalStatus.EXPIRED
            self._save(request)

    def sweep_expired(self) -> int:
        count = 0
        candidates = self._repo.list() if self._repo is not None else list(self._memory.values())
        for request in candidates:
            before = request.status
            self._expire_one(request)
            if before is ApprovalStatus.PENDING and request.status is ApprovalStatus.EXPIRED:
                count += 1
        return count
