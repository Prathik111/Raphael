"""Reasoning backends: turn a goal into a structured Plan via a model.

The backend only *drafts*; :class:`PlanValidator` still approves. Tests
inject :class:`MockModelProvider` with canned outputs (Gate 7 rule: use
deterministic mock LLM outputs).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from ai_ecosystem.agent.planner.validator import PlanValidator
from ai_ecosystem.core.models.domain import Plan
from ai_ecosystem.intelligence.models.providers import (
    ModelProvider,
    ModelRequest,
    request_structured,
)


class ReasoningBackend(ABC):
    """Anything that can draft a plan for a goal."""

    @abstractmethod
    def draft(self, goal: str, available_tools: list[str]) -> Plan:
        """Return a candidate plan (unvalidated)."""
        raise NotImplementedError


class ModelReasoningBackend(ReasoningBackend):
    """Drafts plans through an interchangeable model provider."""

    SYSTEM = (
        "You are a planner. Reply with exactly one JSON object, no prose "
        "before or after, matching this schema: "
        '{"goal": str, "steps": [{"id": str, "description": str, '
        '"dependencies": [step ids], "tools": [tool names], '
        '"arguments": {named tool parameters}, '
        '"risk": one of LOW, MEDIUM, HIGH, '
        '"verification": str, "completion_criteria": str}], '
        '"final_verification": str}. '
        "Rules: dependencies reference step ids; tools use only the "
        "available_tools names; arguments holds every required parameter "
        "with the right JSON type -- tool_reference lists each tool's "
        "required parameters, their types, and what it does; copy "
        "required names exactly "
        "(e.g. {\"path\": \"notes.txt\"}; "
        "terminal commands are argv lists like "
        "{\"command\": [\"echo\", \"hi\"]}, never one string); risk is "
        "exactly one of the three words LOW, MEDIUM, HIGH (never a "
        "sentence); use at most one tool per step. "
        "When the goal needs no tools at all (greetings, explanations, "
        "questions you can answer directly), plan exactly one step using "
        "only agent.respond with the full answer in its text argument -- "
        "never invent files to read or commands to run for such goals."
    )

    def __init__(
        self, provider: ModelProvider, known_tools: set[str] | None = None,
        max_repair_attempts: int = 2,
        tool_arguments: dict[str, set[str]] | None = None,
        tool_schemas: dict[str, dict] | None = None,
        tool_docs: dict[str, dict] | None = None,
        platform_hint: str = "",
    ) -> None:
        self._provider = provider
        self._validator = PlanValidator(known_tools)
        self._max_repairs = max(0, max_repair_attempts)
        self._tool_arguments = tool_arguments
        self._tool_schemas = tool_schemas
        # Tool name -> {"description": str, "required": [...],
        # "properties": {param: type}}. Feeds the prompt reference AND
        # the validator, so both agree on what each tool needs.
        self._tool_docs = dict(tool_docs or {})
        self._platform_hint = platform_hint.strip()

    def _contracts(self) -> tuple[dict[str, set[str]], dict[str, dict]]:
        """Merged (required-args, schemas) from all config sources."""
        required: dict[str, set[str]] = {
            name: set(params)
            for name, params in (self._tool_arguments or {}).items()}
        schemas: dict[str, dict] = dict(self._tool_schemas or {})
        for name, doc in self._tool_docs.items():
            if isinstance(doc, dict):
                required.setdefault(name, set(doc.get("required", [])))
                schemas.setdefault(name, {
                    "required": doc.get("required", []),
                    "properties": doc.get("properties", {})})
        return required, schemas

    def _prompt_body(self, goal: str, available_tools: list[str],
                     error: str = "") -> dict:
        """Plan request payload: goal, tools, and their contracts."""
        body: dict = {"goal": goal, "available_tools": available_tools}
        if self._tool_docs:
            body["tool_reference"] = {
                name: self._tool_docs[name] for name in available_tools
                if name in self._tool_docs}
        if self._platform_hint:
            body["platform"] = self._platform_hint
        if error:
            body["previous_draft_rejected"] = error[:800]
            body["instruction"] = (
                "Return ONLY the corrected JSON object. Fix exactly what "
                "was rejected; keep every other field. risk must be one "
                "of LOW, MEDIUM, HIGH.")
        return body

    def draft(self, goal: str, available_tools: list[str]) -> Plan:
        """Request a plan from the model (may raise ModelMalformedError)."""
        request = ModelRequest(
            prompt=json.dumps(self._prompt_body(goal, available_tools)),
            system=self.SYSTEM,
        )
        return request_structured(self._provider, request, Plan)

    def plan(self, goal: str, available_tools: list[str]) -> Plan:
        """Draft *and* validate; only runnable plans are returned.

        A rejected draft gets up to ``max_repair_attempts`` repair
        rounds: the validation error is shown to the model with an
        order to return only corrected JSON. Persistent failure raises
        the last error (the agent then fails the task honestly).
        """
        from ai_ecosystem.core.errors.exceptions import (
            AiEcosystemError,
            ModelError,
        )

        validator = PlanValidator(
            set(available_tools), *self._contracts())
        try:
            return validator.validate(self.draft(goal, available_tools))
        except (ModelError, AiEcosystemError, ValueError) as first_error:
            last_error = first_error
        for _ in range(self._max_repairs):
            try:
                repaired = self._repair(goal, available_tools, last_error)
                return validator.validate(repaired)
            except (ModelError, AiEcosystemError, ValueError) as error:
                last_error = error
        raise last_error

    def _repair(self, goal: str, available_tools: list[str],
                error: Exception) -> Plan:
        """One guided correction round after a rejected draft."""
        from ai_ecosystem.intelligence.models.providers import ModelRequest

        prompt = json.dumps(
            self._prompt_body(goal, available_tools, error=str(error)))
        return request_structured(
            self._provider, ModelRequest(prompt=prompt, system=self.SYSTEM),
            Plan)
