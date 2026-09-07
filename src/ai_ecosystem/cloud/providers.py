"""Cloud provider abstraction + OCI implementation (Gate 23).

CloudProvider is the seam: every cloud looks the same to the runtime,
and OCIProvider is one implementation behind scripted transports in
tests (CI never touches a real account). The provider has NO access
to ToolRunner, the registry, or local policy -- it cannot execute
locally, read local files, or change authorization. It moves prompts
and job specs outward and results inward, nothing else.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import AiEcosystemError
from ai_ecosystem.core.secrets import CredentialError, SecretsProvider, redact


class CloudError(AiEcosystemError):
    """Base class for cloud failures (never carries credentials)."""


class CloudUnavailableError(CloudError):
    """The provider cannot be reached or is unhealthy."""


class CloudAuthError(CloudError):
    """Credentials are missing or rejected (no values in the message)."""


class CloudStatus(str, Enum):
    """Connection lifecycle of a provider."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


class CloudCapabilities(BaseModel):
    """What a cloud endpoint offers (structural, planner-safe)."""

    cpu: str = ""
    memory_gb: float = 0.0
    gpu: str = ""
    storage_gb: float = 0.0
    network: bool = False
    available_models: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)


class ProviderInfo(BaseModel):
    """Describe() output: status + redacted config + capabilities."""

    provider: str = ""
    status: CloudStatus = CloudStatus.DISCONNECTED
    region: str = ""
    capabilities: CloudCapabilities = Field(default_factory=CloudCapabilities)
    latency_ms: float | None = None


class OCITransport(ABC):
    """Low-level OCI wire calls (mocked in tests, SDK-backed later)."""

    @abstractmethod
    def handshake(self, tenancy: str) -> dict[str, Any]:
        """Authenticate + return endpoint facts (no secrets echoed)."""
        raise NotImplementedError

    @abstractmethod
    def complete(self, model: str, prompt: str) -> str:
        """Run one remote completion."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Release the connection."""
        raise NotImplementedError


class MockOCITransport(OCITransport):
    """Scripted transport: failures, latency, and canned outputs."""

    def __init__(
        self,
        outputs: Optional[dict[str, str]] = None,
        failures: int = 0,
        latency_s: float = 0.0,
        capabilities: Optional[CloudCapabilities] = None,
    ) -> None:
        self._outputs = dict(outputs or {})
        self._failures_left = failures
        self.latency_s = latency_s
        self.capabilities = capabilities or CloudCapabilities(
            cpu="4 OCPU", memory_gb=24.0, available_models=["oci-mock"])
        self.calls = {"handshake": 0, "complete": 0, "close": 0}
        self.prompts: list[str] = []

    def _maybe_fail(self, operation: str) -> None:
        if self._failures_left > 0:
            self._failures_left -= 1
            raise CloudUnavailableError(f"mock OCI {operation} unavailable")

    def handshake(self, tenancy: str) -> dict[str, Any]:
        """Pretend to authenticate (tenancy acknowledged, never echoed)."""
        self.calls["handshake"] += 1
        self._maybe_fail("handshake")
        if self.latency_s:
            time.sleep(self.latency_s)
        return {"region": "mock-region", "authenticated": True}

    def complete(self, model: str, prompt: str) -> str:
        """Return the canned output for a model (default echo)."""
        self.calls["complete"] += 1
        self._maybe_fail("complete")
        self.prompts.append(prompt)
        if self.latency_s:
            time.sleep(self.latency_s)
        return self._outputs.get(model, f"mock result for {model}")

    def close(self) -> None:
        """Record the close."""
        self.calls["close"] += 1


class CloudProvider(ABC):
    """Provider seam: connect/disconnect/status/jobs/health."""

    name: str = "cloud"

    @abstractmethod
    def connect(self) -> ProviderInfo:
        """Authenticate and open the session."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """Close the session (idempotent)."""
        raise NotImplementedError

    @abstractmethod
    def status(self) -> ProviderInfo:
        """Current status without side effects."""
        raise NotImplementedError

    @abstractmethod
    def capabilities(self) -> CloudCapabilities:
        """What this endpoint offers."""
        raise NotImplementedError

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Reachability/auth/availability determination (bounded)."""
        raise NotImplementedError

    def job_logs(self, job_id: str) -> str:
        """Fetch remote logs (providers without jobs refuse explicitly)."""
        raise CloudError(f"{self.name} does not support remote jobs")

    def job_result(self, job_id: str) -> str:
        """Fetch a remote result reference (providers without jobs refuse)."""
        raise CloudError(f"{self.name} does not support remote jobs")


class OCIProvider(CloudProvider):
    """OCI implementation: credentials in, results out, nothing else."""

    name = "oci"

    def __init__(
        self,
        transport: OCITransport,
        secrets: SecretsProvider,
        region: str = "",
    ) -> None:
        self._transport = transport
        self._secrets = secrets
        self._region = region
        self._status = CloudStatus.DISCONNECTED
        self._latency_ms: float | None = None

    def connect(self) -> ProviderInfo:
        """Authenticate via the secrets provider (values never logged)."""
        self._status = CloudStatus.CONNECTING
        try:
            tenancy = self._secrets.require("OCI_TENANCY")
            started = time.monotonic()
            facts = self._transport.handshake(tenancy)
            self._latency_ms = round((time.monotonic() - started) * 1000.0, 2)
        except CloudUnavailableError:
            self._status = CloudStatus.ERROR
            raise
        except CredentialError as exc:
            self._status = CloudStatus.ERROR
            raise CloudAuthError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 -- normalize transport errors
            self._status = CloudStatus.ERROR
            raise CloudAuthError("OCI authentication failed") from exc
        self._region = str(facts.get("region", self._region))
        self._status = CloudStatus.CONNECTED
        return self.status()

    def disconnect(self) -> None:
        """Close quietly (safe to call twice)."""
        try:
            self._transport.close()
        finally:
            self._status = CloudStatus.DISCONNECTED

    def status(self) -> ProviderInfo:
        """Describe without secrets (region + capabilities only)."""
        return ProviderInfo(
            provider=self.name, status=self._status, region=self._region,
            capabilities=self.capabilities() if self._status is CloudStatus.CONNECTED
            else CloudCapabilities(),
            latency_ms=self._latency_ms,
        )

    def capabilities(self) -> CloudCapabilities:
        """Endpoint capabilities (mock transport carries its own)."""
        transport = self._transport
        if isinstance(transport, MockOCITransport):
            return transport.capabilities
        return CloudCapabilities()

    def health(self) -> dict[str, Any]:
        """Bounded health determination (no background polling here)."""
        started = time.monotonic()
        try:
            self._transport.handshake("health-check")
            reachable, authenticated = True, True
        except CloudUnavailableError:
            reachable, authenticated = False, False
        except Exception:  # noqa: BLE001
            reachable, authenticated = True, False
        elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
        available = reachable and authenticated and self._status is CloudStatus.CONNECTED
        return {"provider": self.name, "reachable": reachable,
                "authenticated": authenticated, "available": available,
                "status": (self._status.value if available
                           else CloudStatus.DEGRADED.value
                           if reachable else CloudStatus.ERROR.value),
                "latency_ms": elapsed_ms}

    def complete_remote(self, model: str, prompt: str) -> str:
        """Run one remote completion (connected only)."""
        if self._status is not CloudStatus.CONNECTED:
            raise CloudUnavailableError("OCI is not connected")
        if len(prompt) > 500_000:
            raise CloudError("prompt exceeds 500KB remote limit")
        return self._transport.complete(model, prompt)

    def describe_redacted(self) -> dict[str, Any]:
        """Status safe for logs and events (no credential material)."""
        info = self.status()
        return {"provider": info.provider, "status": info.status.value,
                "region": info.region,
                "capabilities": info.capabilities.model_dump()}


def redact_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip secret-looking keys from event payloads."""
    return redact(payload)
