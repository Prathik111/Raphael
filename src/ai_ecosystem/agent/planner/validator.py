"""Plan validation + dependency resolution (Gate 7, pure logic).

The runtime refuses any plan that fails here, so the planner (model or
otherwise) can never emit something unrunnable. Dependencies reference
step ``id`` values, which model output must therefore state explicitly.
"""

from __future__ import annotations

from types import SimpleNamespace

from ai_ecosystem.core.errors.exceptions import PlanValidationError
from ai_ecosystem.core.models.domain import Plan, PlanStep
from ai_ecosystem.core.models.enums import RiskLevel
from ai_ecosystem.tools.registry.validation import check_arguments


class DependencyResolver:
    """Topological ordering over plan steps (Kahn's algorithm)."""

    @staticmethod
    def order(steps: list[PlanStep]) -> list[PlanStep]:
        """Steps in dependency order; raises on missing deps or cycles."""
        by_id = {step.id: step for step in steps}
        for step in steps:
            for dep in step.dependencies:
                if dep == step.id:
                    raise PlanValidationError(
                        f"step {step.id!r} depends on itself"
                    )
                if dep not in by_id:
                    raise PlanValidationError(
                        f"step {step.id!r} depends on unknown step {dep!r}"
                    )
        incoming = {step.id: 0 for step in steps}
        children: dict[str, list[str]] = {step.id: [] for step in steps}
        for step in steps:
            for dep in step.dependencies:
                children[dep].append(step.id)
                incoming[step.id] += 1
        ready = [step.id for step in steps if incoming[step.id] == 0]
        ordered: list[PlanStep] = []
        while ready:
            current = ready.pop(0)
            ordered.append(by_id[current])
            for child in children[current]:
                incoming[child] -= 1
                if incoming[child] == 0:
                    ready.append(child)
        if len(ordered) != len(steps):
            raise PlanValidationError("plan contains a dependency cycle")
        return ordered


class PlanValidator:
    """Strict structural gate every plan must pass before execution."""

    def __init__(self, known_tools: set[str] | None = None,
                 required_args: dict[str, set[str]] | None = None,
                 tool_schemas: dict[str, dict] | None = None) -> None:
        self._known_tools = set(known_tools or [])
        # Tool name -> required parameter names. When provided, steps
        # must supply every required argument up front, so a model that
        # forgets 'command' or 'path' gets a repair round instead of a
        # doomed execution with three wasted retries.
        self._required_args = {name: set(params)
                               for name, params in (required_args or {}).items()}
        # Tool name -> {"required": [...], "properties": {param: type}}.
        # Superset of required_args: also checks JSON types, so a model
        # that passes 'command' as a string instead of an argv list is
        # corrected at plan time.
        self._schemas = dict(tool_schemas or {})
        for name, schema in self._schemas.items():
            if isinstance(schema, dict):
                self._required_args.setdefault(
                    name, set(schema.get("required", [])))

    def validate(self, plan: Plan) -> Plan:
        """Return the plan when runnable; raise PlanValidationError."""
        if not plan.goal.strip():
            raise PlanValidationError("plan has no goal")
        if not plan.steps:
            raise PlanValidationError("plan has no steps")
        ids = [step.id for step in plan.steps]
        if any(not sid for sid in ids):
            raise PlanValidationError("every step needs an id")
        if len(set(ids)) != len(ids):
            raise PlanValidationError("step ids must be unique")
        for step in plan.steps:
            self._validate_step(step)
        DependencyResolver.order(plan.steps)  # missing deps + cycles
        if not plan.final_verification.strip():
            raise PlanValidationError("plan has no final verification")
        return plan

    def _validate_step(self, step: PlanStep) -> None:
        if not step.description.strip():
            raise PlanValidationError(f"step {step.id!r} has no description")
        if not step.tools:
            raise PlanValidationError(f"step {step.id!r} names no tools")
        unknown = [name for name in step.tools if name not in self._known_tools]
        if unknown:
            raise PlanValidationError(
                f"step {step.id!r} uses unknown tools: {', '.join(unknown)}"
            )
        provided = set(step.arguments or {})
        for name in step.tools:
            required = self._required_args.get(name, set())
            missing = [param for param in sorted(required)
                       if param not in provided]
            if missing:
                raise PlanValidationError(
                    f"step {step.id!r} tool {name!r} is missing required "
                    f"arguments: {', '.join(missing)}"
                )
            # Same canonical contract check the runner enforces at the
            # final boundary (presence + JSON types), adapted from the
            # registry contract into the validator's dict-based inputs.
            properties = {}
            schema = self._schemas.get(name)
            if isinstance(schema, dict):
                props = schema.get("properties")
                if isinstance(props, dict):
                    properties = props
            contract = SimpleNamespace(input_schema={
                "required": sorted(required), "properties": properties})
            problems = check_arguments(contract, dict(step.arguments or {}))
            # Presence errors already raised above with step context.
            type_problems = [p for p in problems
                             if not p.startswith("missing required")]
            if type_problems:
                raise PlanValidationError(
                    f"step {step.id!r} tool {name!r}: "
                    f"{'; '.join(type_problems)}"
                )
        if step.risk is RiskLevel.CRITICAL:
            raise PlanValidationError(
                f"step {step.id!r} is CRITICAL risk and cannot be auto-planned"
            )
        if not step.completion_criteria.strip():
            raise PlanValidationError(
                f"step {step.id!r} has no completion criteria"
            )
        if not step.verification.strip():
            raise PlanValidationError(f"step {step.id!r} has no verification")
