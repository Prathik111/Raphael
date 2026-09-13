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
from typing import Any

from pydantic import BaseModel, Field

from ai_ecosystem.cloud.jobs import ComputeJob
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
        raise NotImplementedError

    @abstractmethod
    def complete(self, model: str, prompt: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class MockOCITransport(OCITransport):
    """Scripted transport: failures, latency, and canned outputs."""

    def __init__(
        self,
        outputs: dict[str, str] | None = None,
        failures: int = 0,
        latency_s: float = 0.0,
        capabilities: CloudCapabilities | None = None,
    ) -> None:
        self._outputs = dict(outputs or {})
        self._failures_left = failures
        self.latency_s = latency_s
        self.capabilities = capabilities or CloudCapabilities(
            cpu="4 OCPU", memory_gb=24.0, available_models=["oci-mock"]
        )
        self.calls = {"handshake": 0, "complete": 0, "close": 0}
        self.prompts: list[str] = []

    def _maybe_fail(self, operation: str) -> None:
        if self._failures_left > 0:
            self._failures_left -= 1
            raise CloudUnavailableError(f"mock OCI {operation} unavailable")

    def handshake(self, tenancy: str) -> dict[str, Any]:
        self.calls["handshake"] += 1
        self._maybe_fail("handshake")
        if self.latency_s:
            time.sleep(self.latency_s)
        return {"region": "mock-region", "authenticated": True}

    def complete(self, model: str, prompt: str) -> str:
        self.calls["complete"] += 1
        self._maybe_fail("complete")
        self.prompts.append(prompt)
        if self.latency_s:
            time.sleep(self.latency_s)
        return self._outputs.get(model, f"mock result for {model}")

    def close(self) -> None:
        self.calls["close"] += 1


class CloudProvider(ABC):
    """Provider seam: connect/disconnect/status/jobs/health."""

    name: str = "cloud"

    @abstractmethod
    def connect(self) -> ProviderInfo:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> ProviderInfo:
        raise NotImplementedError

    @abstractmethod
    def capabilities(self) -> CloudCapabilities:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError

    def job_logs(self, job: ComputeJob) -> str:
        raise CloudError(f"{self.name} does not support remote jobs")

    def job_result(self, job: ComputeJob) -> str:
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
        except Exception as exc:
            self._status = CloudStatus.ERROR
            raise CloudAuthError("OCI authentication failed") from exc
        self._region = str(facts.get("region", self._region))
        self._status = CloudStatus.CONNECTED
        return self.status()

    def disconnect(self) -> None:
        try:
            self._transport.close()
        finally:
            self._status = CloudStatus.DISCONNECTED

    def status(self) -> ProviderInfo:
        return ProviderInfo(
            provider=self.name,
            status=self._status,
            region=self._region,
            capabilities=self.capabilities()
            if self._status is CloudStatus.CONNECTED
            else CloudCapabilities(),
            latency_ms=self._latency_ms,
        )

    def capabilities(self) -> CloudCapabilities:
        transport = self._transport
        if isinstance(transport, MockOCITransport):
            return transport.capabilities
        return CloudCapabilities()

    def health(self) -> dict[str, Any]:
        started = time.monotonic()
        try:
            self._transport.handshake("health-check")
            reachable, authenticated = True, True
        except CloudUnavailableError:
            reachable, authenticated = False, False
        except Exception:
            reachable, authenticated = True, False
        elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
        available = reachable and authenticated and self._status is CloudStatus.CONNECTED
        return {
            "provider": self.name,
            "reachable": reachable,
            "authenticated": authenticated,
            "available": available,
            "status": (
                self._status.value
                if available
                else CloudStatus.DEGRADED.value
                if reachable
                else CloudStatus.ERROR.value
            ),
            "latency_ms": elapsed_ms,
        }

    def complete_remote(self, model: str, prompt: str) -> str:
        if self._status is not CloudStatus.CONNECTED:
            raise CloudUnavailableError("OCI is not connected")
        if len(prompt) > 500_000:
            raise CloudError("prompt exceeds 500KB remote limit")
        return self._transport.complete(model, prompt)

    def describe_redacted(self) -> dict[str, Any]:
        info = self.status()
        return {
            "provider": info.provider,
            "status": info.status.value,
            "region": info.region,
            "capabilities": info.capabilities.model_dump(),
        }


def redact_event(payload: dict[str, Any]) -> dict[str, Any]:
    return redact(payload)
