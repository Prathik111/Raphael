"""Model abstraction and normalized provider contracts."""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from typing import Any, Optional

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import ModelMalformedError, ModelUnavailableError


class ModelCapabilities(BaseModel):
    tool_calling: bool = False
    structured_output: bool = False
    reasoning: bool = False
    vision: bool = False
    streaming: bool = False
    parallel_tool_calls: bool = False
    context_length: int = 0


class ModelToolDefinition(BaseModel):
    """Provider-neutral function tool definition."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelToolCall(BaseModel):
    """One normalized native tool call returned by a model."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelRequest(BaseModel):
    prompt: str = ""
    system: str = ""
    timeout_s: float = 30.0
    max_tokens: int = 1024
    tools: list[ModelToolDefinition] = Field(default_factory=list)
    tool_choice: str | dict[str, Any] = "auto"


class ModelResponse(BaseModel):
    text: str = ""
    structured: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    latency_s: float = 0.0
    finish_reason: str = ""


class ModelProvider(ABC):
    def __init__(self, provider_id: str, capabilities: ModelCapabilities) -> None:
        self.provider_id = provider_id
        self.capabilities = capabilities

    @abstractmethod
    def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    def stream(self, request: ModelRequest) -> Iterator[str]:
        if not self.capabilities.streaming:
            raise ModelUnavailableError(
                f"provider {self.provider_id!r} does not support streaming"
            )
        yield self.complete(request).text


class MockModelProvider(ModelProvider):
    """Scriptable provider for deterministic tests and offline development."""

    def __init__(
        self,
        provider_id: str = "mock",
        capabilities: Optional[ModelCapabilities] = None,
        handler: Optional[Callable[[ModelRequest], ModelResponse]] = None,
    ) -> None:
        super().__init__(provider_id, capabilities or ModelCapabilities(structured_output=True))
        self._handler = handler or (
            lambda req: ModelResponse(text="mock", model=provider_id)
        )
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
        for index in range(0, len(text), 8):
            yield text[index : index + 8]


def extract_json_block(text: str) -> str:
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
                    return stripped[start : index + 1]
    return stripped


def request_structured(
    provider: ModelProvider, request: ModelRequest, model_cls: type[BaseModel]
) -> BaseModel:
    response = provider.complete(request)
    try:
        if response.structured:
            return model_cls.model_validate(response.structured)
        return model_cls.model_validate_json(extract_json_block(response.text))
    except Exception as exc:
        raise ModelMalformedError(
            f"provider {provider.provider_id!r} returned invalid output: {exc}"
        ) from exc
