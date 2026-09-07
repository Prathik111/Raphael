"""Skill registry, discovery, plan building, and candidates (Gate 17).

A skill is a versioned workflow template, never an executable. Using
one means: select -> build a Plan -> validate -> risk/policy/authorize
-> execute via ToolRunner -> verify. Every arrow is the normal
machinery; skills add reuse, not privilege.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import Field

from ai_ecosystem.agent.planner.validator import PlanValidator
from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.core.models.domain import Plan, PlanStep, Skill, ToolCall
from ai_ecosystem.core.models.enums import (
    EventType,
    MemoryScope,
    RiskLevel,
    SkillStatus,
    SkillType,
)
from ai_ecosystem.core.persistence.sqlite import SqliteSkillRepository
from ai_ecosystem.security.policy.engines import PolicyEngine, RiskContext, RiskEngine
from ai_ecosystem.tools.registry.registry import ToolRegistry

_PLACEHOLDER = re.compile(r"\$inputs\.([A-Za-z_][A-Za-z0-9_]*)")


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


class SkillProposal(Entity):
    """A proposed skill awaiting explicit approval (never auto-active)."""

    name: str = ""
    description: str = ""
    skill_type: SkillType = SkillType.LEARNED_PROPOSAL
    allowed_tools: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    risk_class: RiskLevel = RiskLevel.MEDIUM
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    workflow: list[dict[str, Any]] = Field(default_factory=list)
    verification: list[dict[str, Any]] = Field(default_factory=list)
    author: str = ""
    scope: MemoryScope = MemoryScope.GLOBAL
    scope_id: str = ""
    reason: str = ""


def _version_key(version: str) -> tuple:
    """Order key tolerant of non-numeric segments (never raises).

    Each dot-separated chunk sorts numerics before text, so versions
    like 1.0.1, 1.0.a, and 1.0-rc1 order deterministically instead of
    crashing mixed-type comparison.
    """
    key = []
    for chunk in version.split("."):
        key.append((0, int(chunk), "") if chunk.isdigit() else (1, 0, chunk))
    return tuple(key)


def _valid_version(version: str) -> bool:
    return bool(version) and all(
        chunk.isdigit() or chunk.replace("-", "").replace("_", "").isalnum()
        for chunk in version.split("."))


class SkillRegistry:
    """Versioned, scoped skill storage over the existing skills table."""

    def __init__(self, repository: SqliteSkillRepository,
                 bus: Optional[EventBus] = None) -> None:
        self._repo = repository
        self._bus = bus

    def register(self, skill: Skill) -> Skill:
        """Store a new skill version (duplicate ids rejected)."""
        if not skill.name:
            raise DomainValidationError("skill name must not be empty")
        if not _valid_version(skill.version):
            raise DomainValidationError(
                f"invalid skill version {skill.version!r}")
        if self._repo.get(skill.id):
            raise DomainValidationError(f"duplicate skill id {skill.id!r}")
        created = self._repo.create(skill)
        self._emit(EventType.SKILL_CREATED, "",
                   {"skill_id": created.id, "name": created.name,
                    "version": created.version})
        return created

    def lookup(self, name: str, version: Optional[str] = None) -> Optional[Skill]:
        """Highest ACTIVE version, or one exact version (any status)."""
        matches = [s for s in self._repo.list() if s.name == name]
        if not matches:
            return None
        if version is not None:
            return next((s for s in matches if s.version == version), None)
        active = [s for s in matches if s.status is SkillStatus.ACTIVE]
        if not active:
            return None
        return sorted(active, key=lambda s: _version_key(s.version))[-1]

    def versions(self, name: str) -> list[Skill]:
        """All stored versions, oldest first (history preserved)."""
        return sorted(
            [s for s in self._repo.list() if s.name == name],
            key=lambda s: _version_key(s.version),
        )

    def new_version(self, name: str, **changes: Any) -> Skill:
        """Create the next version (old records are never mutated)."""
        current = self.lookup(name)
        if current is None:
            raise DomainValidationError(f"unknown skill {name!r}")
        data = current.model_dump()
        data.pop("id", None)
        data.pop("created_at", None)
        data.update(changes)
        data["version"] = _bump(current.version)
        created = self._repo.create(Skill.model_validate(data))
        self._emit(EventType.SKILL_UPDATED, "",
                   {"skill_id": created.id, "name": name,
                    "version": created.version})
        return created

    def set_status(self, skill_id: str, status: SkillStatus) -> Skill:
        """Enable/disable/archive one version (emits lifecycle events)."""
        from ai_ecosystem.core.errors.exceptions import ResourceNotFoundError

        skill = self._repo.get(skill_id)
        if skill is None:
            raise ResourceNotFoundError("Skill", skill_id)
        skill.status = status
        skill.touch()
        updated = self._repo.update(skill)
        if status is SkillStatus.DISABLED:
            self._emit(EventType.SKILL_DISABLED, "", {"skill_id": skill_id})
        return updated

    def delete(self, skill_id: str) -> bool:
        """Remove one version record (history of other versions stays)."""
        return self._repo.delete(skill_id)

    def select(self, query: str = "", capabilities: Optional[list[str]] = None,
               scope_id: str = "", limit: int = 5) -> list[Skill]:
        """Deterministic discovery: keywords + capability + scope match.

        Visibility rule: GLOBAL skills are visible everywhere; any other
        scope is visible only on exact scope_id match (empty never
        matches, so scoped skills can't leak to other tasks/agents).
        """
        wants = _keywords(query)
        required = set(capabilities or [])
        scored: list[tuple[float, str, Skill]] = []
        for skill in self._repo.list():
            if skill.status is not SkillStatus.ACTIVE:
                continue
            if skill.scope is not MemoryScope.GLOBAL and (
                    not scope_id or skill.scope_id != scope_id):
                continue
            if required and not required.issubset(set(skill.required_capabilities)):
                continue
            haystack = _keywords(f"{skill.name} {skill.description}")
            overlap = len(wants & haystack) / max(1, len(wants)) if wants else 0.5
            if wants and overlap <= 0.0:
                continue  # no keyword match: not discoverable by this query
            scored.append((round(overlap, 3), skill.name, skill))
        scored.sort(key=lambda item: (-item[0], item[1]))
        found = [skill for _, _, skill in scored[: max(0, limit)]]
        self._emit(EventType.SKILL_SELECTED, "",
                   {"query": query, "selected": [s.name for s in found]})
        return found

    def announce_outcome(self, skill: Skill, task_id: str, success: bool,
                         detail: str = "") -> None:
        """Emit SKILL_COMPLETED/FAILED after a built plan runs elsewhere."""
        self._emit(
            EventType.SKILL_COMPLETED if success else EventType.SKILL_FAILED,
            task_id, {"skill_id": skill.id, "name": skill.name,
                      "version": skill.version, "detail": detail})

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload))


def _bump(version: str) -> str:
    parts = version.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        parts.append("1")
    return ".".join(parts)


class SkillPlanBuilder:
    """Turns a skill + inputs into a validated, policy-checked Plan.

    The builder never executes. Risk is re-derived from the actual
    tools (a LOW label on a dangerous workflow cannot hide), and the
    plan still passes PlanValidator before anyone runs it.
    """

    def __init__(self, tools: ToolRegistry,
                 risk_engine: Optional[RiskEngine] = None,
                 policy_engine: Optional[PolicyEngine] = None,
                 bus: Optional[EventBus] = None) -> None:
        self._tools = tools
        self._risk = risk_engine or RiskEngine()
        self._policy = policy_engine or PolicyEngine()
        self._bus = bus

    def build(self, skill: Skill, inputs: dict[str, Any],
              goal: str = "", collect_arguments: bool = False,
              ) -> Plan | tuple[Plan, dict[str, dict[str, Any]]]:
        """Instantiate the workflow template into a runnable Plan.

        ``$inputs.x`` placeholders fill step descriptions AND per-step
        tool arguments (from each template's ``arguments`` mapping).
        With ``collect_arguments=True`` also returns
        ``{step_id: arguments}`` for the executor, so inputs actually
        reach the tools instead of silently dropping.
        """
        if skill.status is not SkillStatus.ACTIVE:
            raise DomainValidationError(
                f"skill {skill.name!r} v{skill.version} is {skill.status.value}")
        if not skill.workflow:
            raise DomainValidationError(f"skill {skill.name!r} has no workflow")
        steps = []
        collected: dict[str, dict[str, Any]] = {}
        for index, template in enumerate(skill.workflow):
            tool_name = template.get("tool", "")
            if tool_name not in skill.allowed_tools:
                raise DomainValidationError(
                    f"skill step uses tool {tool_name!r} outside allowed_tools")
            known = self._tools.get(tool_name)
            if known is None:
                raise DomainValidationError(f"unknown tool {tool_name!r}")
            step_id = template.get("id", f"step-{index + 1}")
            raw_arguments = template.get("arguments", {})
            if not isinstance(raw_arguments, dict):
                raise DomainValidationError(
                    f"step {step_id!r} arguments must be a mapping")
            step_arguments = {
                key: (self._fill(str(value), inputs)
                      if isinstance(value, str) else value)
                for key, value in raw_arguments.items()
            }
            collected[step_id] = step_arguments
            steps.append(PlanStep(
                id=step_id,
                description=self._fill(str(template.get("description", tool_name)), inputs),
                dependencies=list(template.get("dependencies", [])),
                tools=[tool_name],
                risk=known.risk_level,
                verification=str(template.get("verification", "")),
                completion_criteria=str(template.get("completion_criteria", "")),
            ))
        plan = Plan(
            goal=goal or f"skill:{skill.name} v{skill.version}",
            steps=steps,
            final_verification=str(skill.verification[0].get("check", "")
                                   if skill.verification else ""),
        )
        PlanValidator({t.name for t in self._tools.list_tools()}).validate(plan)
        if self._bus is not None:
            self._bus.publish(Event(
                event_type=EventType.SKILL_INVOKED, task_id="",
                payload={"skill_id": skill.id, "name": skill.name,
                         "version": skill.version}))
        if collect_arguments:
            return plan, collected
        return plan

    def check_policy(self, skill: Skill, agent_id: str = "") -> tuple[bool, str]:
        """Would the active policy allow this skill's tools? (advisory)."""
        for tool_name in skill.allowed_tools:
            tool = self._tools.get(tool_name)
            if tool is None:
                return False, f"unknown tool {tool_name!r}"
            assessment = self._risk.assess(
                "", tool, ToolCall(task_id="", tool=tool_name),
                RiskContext(agent_id=agent_id))
            granted, reason = self._policy.evaluate(assessment, tool_name, agent_id)
            if not granted:
                return False, reason
        return True, "all skill tools permitted"

    @staticmethod
    def _fill(template: str, inputs: dict[str, Any]) -> str:
        def replace(match: re.Match) -> str:
            return str(inputs.get(match.group(1), match.group(0)))

        return _PLACEHOLDER.sub(replace, template)


class SkillCandidateStore:
    """Proposal inbox: candidates wait for explicit human approval."""

    def __init__(self, bus: Optional[EventBus] = None) -> None:
        self._bus = bus
        self._candidates: dict[str, SkillProposal] = {}

    def propose(self, proposal: SkillProposal) -> SkillProposal:
        """File a candidate (DISABLED-by-birth: LEARNED_PROPOSAL type)."""
        if not proposal.name:
            raise DomainValidationError("skill proposal needs a name")
        if proposal.skill_type is not SkillType.LEARNED_PROPOSAL:
            raise DomainValidationError("candidates must be LEARNED_PROPOSAL")
        self._candidates[proposal.id] = proposal
        if self._bus is not None:
            self._bus.publish(Event(
                event_type=EventType.SKILL_PROPOSAL_CREATED, task_id="",
                payload={"proposal_id": proposal.id, "name": proposal.name}))
        return proposal

    def get(self, proposal_id: str) -> Optional[SkillProposal]:
        """Fetch a pending candidate (None when unknown/decided)."""
        return self._candidates.get(proposal_id)

    def approve(self, proposal_id: str, registry: SkillRegistry,
                version: str = "1.0.0") -> Skill:
        """Adopt a candidate as an ACTIVE skill version.

        All proposal fields carry over, including capability
        requirements and risk class: approval must never silently
        downgrade what was reviewed.
        """
        proposal = self._candidates.pop(proposal_id, None)
        if proposal is None:
            raise DomainValidationError(f"unknown proposal {proposal_id!r}")
        return registry.register(Skill(
            name=proposal.name, version=version, description=proposal.description,
            input_schema=dict(proposal.input_schema),
            output_schema=dict(proposal.output_schema),
            skill_type=proposal.skill_type, status=SkillStatus.ACTIVE,
            allowed_tools=list(proposal.allowed_tools),
            required_capabilities=list(proposal.required_capabilities),
            risk_class=proposal.risk_class,
            workflow=[dict(step) for step in proposal.workflow],
            verification=[dict(check) for check in proposal.verification],
            author=proposal.author or "candidate-approval",
            scope=proposal.scope, scope_id=proposal.scope_id,
        ))

    def reject(self, proposal_id: str) -> bool:
        """Discard a candidate (recorded by the caller, not executed)."""
        return self._candidates.pop(proposal_id, None) is not None


def propose_from_workflow(plan: Plan, name: str, author: str = "",
                          scope_id: str = "") -> SkillProposal:
    """Draft a candidate from a successful plan (explicit adoption later)."""
    workflow = [{
        "id": step.id, "description": step.description,
        "tool": step.tools[0] if step.tools else "",
        "dependencies": list(step.dependencies),
        "verification": step.verification,
        "completion_criteria": step.completion_criteria,
    } for step in plan.steps]
    return SkillProposal(
        name=name, description=f"Learned from plan: {plan.goal}",
        allowed_tools=sorted({tool for step in plan.steps for tool in step.tools}),
        workflow=workflow,
        verification=[{"check": plan.final_verification}],
        author=author,
        scope=MemoryScope.PROJECT if scope_id else MemoryScope.GLOBAL,
        scope_id=scope_id,
        reason="successful workflow observed",
    )
