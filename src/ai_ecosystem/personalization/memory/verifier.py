"""Authoritative memory verification boundary.

A memory candidate is never allowed to make its own ``verified`` claim.
Only this verifier can mint a receipt, and the receipt is bound to the exact
content hash and evidence used for verification.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, Field

from ai_ecosystem.core.models.base import utcnow
from ai_ecosystem.personalization.memory.models import MemoryCandidate


class VerificationReceipt(BaseModel):
    memory_hash: str
    evidence_ids: list[str] = Field(default_factory=list)
    verifier: str
    method: str
    verified_at: datetime = Field(default_factory=utcnow)
    verifier_version: str = "1"


def candidate_hash(candidate: MemoryCandidate) -> str:
    payload = {
        "content": candidate.content,
        "type": candidate.type.value,
        "source": candidate.source,
        "confidence": candidate.confidence,
        "importance": candidate.importance,
        "scope": candidate.scope.value,
        "scope_id": candidate.scope_id,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MemoryVerifier:
    """Mint receipts only from independently supplied evidence."""

    def verify(self, candidate: MemoryCandidate, *, evidence_ids: list[str],
               verifier: str, method: str) -> VerificationReceipt:
        if not verifier.strip():
            raise ValueError("verifier identity is required")
        if not method.strip():
            raise ValueError("verification method is required")
        if not evidence_ids:
            raise ValueError("at least one evidence id is required")
        return VerificationReceipt(
            memory_hash=candidate_hash(candidate),
            evidence_ids=list(evidence_ids),
            verifier=verifier,
            method=method,
        )

    @staticmethod
    def valid(candidate: MemoryCandidate, receipt: VerificationReceipt) -> bool:
        return receipt.memory_hash == candidate_hash(candidate)
