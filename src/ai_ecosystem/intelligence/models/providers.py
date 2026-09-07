"""Model abstraction (BUILD_PLAN Gate 4, Part 3).

Providers are interchangeable behind :class:`ModelProvider`; the rest of
the agent only sees requests, responses, and capabilities. No network
calls here -- real backends arrive later as new provider classes.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from typing import Any, Optional

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import (
    ModelMalformedError,
    ModelUnavailableError,
)


class ModelCapabilities(BaseModel):
    """What a model can do (Gate 4 capability flags)."""

    tool_calling: bool = False
    structured_output: bool = False
    reasoning: bool = False
    vision: bool = False
    streaming: bool = False
    parallel_tool_calls: bool = False
    context_length: int = 0


class ModelRequest(BaseModel):
    """One inference request, provider-agnostic."""

    prompt: str = ""
    system: str = ""
    timeout_s: float = 30.0
    max_tokens: int = 1024


class ModelResponse(BaseModel):
    """One inference response, provider-agnostic."""

    text: str = ""
    structured: dict[str, Any] = Field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    latency_s: float = 0.0


class ModelProvider(ABC):
    """Interchangeable inference backend."""

    def __init__(self, provider_id: str, capabilities: ModelCapabilities) -> None:
        self.provider_id = provider_id
        self.capabilities = capabilities

    @abstractmethod
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Run one request; raise a ModelError subclass on failure."""
        raise NotImplementedError

    def stream(self, request: ModelRequest) -> Iterator[str]:
        """Yield response chunks; default requires streaming capability."""
        if not self.capabilities.streaming:
            raise ModelUnavailableError(
                f"provider {self.provider_id!r} does not support streaming"
            )
        yield self.complete(request).text


class MockModelProvider(ModelProvider):
    """Scriptable provider for tests and offline development.

    ``handler`` maps a request to a response (or raises to simulate
    outages). Every call is recorded in :attr:`calls`.
    """

    def __init__(
        self,
        provider_id: str = "mock",
        capabilities: Optional[ModelCapabilities] = None,
        handler: Optional[Callable[[ModelRequest], ModelResponse]] = None,
    ) -> None:
        super().__init__(
            provider_id, capabilities or ModelCapabilities(structured_output=True)
        )
        self._handler = handler or (lambda req: ModelResponse(text="mock", model=provider_id))
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        started = time.monotonic()
        response = self._handler(request)
        response.latency_s = time.monotonic() - started
        if not response.model:
            response.model = self.provider_id
        return response

    def stream(self, request: ModelRequest) -> Iterator[str]:
        if not self.capabilities.streaming:
            raise ModelUnavailableError(
                f"provider {self.provider_id!r} does not support streaming"
            )
        text = self.complete(request).text
        chunk, size = 0, 8
        while chunk < len(text):
            yield text[chunk : chunk + size]
            chunk += size


def extract_json_block(text: str) -> str:
    """Return the parseable JSON object inside model output.

    Real models wrap answers in prose or fences; validators need the
    object. Prefers the full text, then a fenced block, then the first
    balanced ``{...}`` span (string-aware, so braces inside quotes
    don't end the scan early).
    """
    stripped = text.strip()
    try:
        json.loads(stripped)
        return stripped
    except ValueError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S)
    if fence:
        return fence.group(1)
    start = stripped.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(stripped)):
            char = stripped[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return stripped[start:index + 1]
    return stripped


def request_structured(
    provider: ModelProvider, request: ModelRequest, model_cls: type[BaseModel]
) -> BaseModel:
    """Complete and validate a structured response into ``model_cls``.

    Raises :class:`ModelMalformedError` when the provider output cannot
    satisfy the schema -- the planner (Gate 7) treats this as retryable.
    """
    response = provider.complete(request)
    try:
        if response.structured:
            return model_cls.model_validate(response.structured)
        return model_cls.model_validate_json(extract_json_block(response.text))
    except Exception as exc:
        raise ModelMalformedError(
            f"provider {provider.provider_id!r} returned invalid output: {exc}"
        ) from exc
