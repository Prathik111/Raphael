"""Gate 17: skills reuse workflows without adding privilege."""

import time

import pytest

from ai_ecosystem.agent.executor import OverallStatus, ParallelExecutor
from ai_ecosystem.core.errors import DomainValidationError
from ai_ecosystem.core.events import EventBus
from ai_ecosystem.core.models import Plan, Skill, Tool, ToolResult
from ai_ecosystem.core.models.enums import (
    EventType,
    MemoryScope,
    RiskLevel,
    SkillStatus,
    SkillType,
)
from ai_ecosystem.core.persistence import Database, SqliteSkillRepository
from ai_ecosystem.security import AuthorizationManager, RiskContext
from ai_ecosystem.skills import (
    SkillCandidateStore,
    SkillPlanBuilder,
    SkillProposal,
    SkillRegistry,
    propose_from_workflow,
)
from ai_ecosystem.tools import ToolRegistry, ToolRunner


def _ok(args):
    return ToolResult(success=True, output="ok")


@pytest.fixture()
def setup():
    db = Database(":memory:")
    db.migrate()
    bus = EventBus()
    registry = ToolRegistry()
    registry.register(
        Tool(name="read", input_schema={"required": []}, risk_level=RiskLevel.LOW), _ok
    )
    registry.register(
        Tool(name="analyze", input_schema={"required": []}, risk_level=RiskLevel.LOW), _ok
    )
    skills = SkillRegistry(SqliteSkillRepository(db), bus)
    builder = SkillPlanBuilder(registry, bus=bus)
    yield db, bus, registry, skills, builder
    db.close()


def _skill(name="repo-scan", version="1.0.0", **kw):
    args = {
        "name": name,
        "version": version,
        "description": "scan a repository",
        "skill_type": SkillType.USER_DEFINED,
        "status": SkillStatus.ACTIVE,
        "allowed_tools": ["read", "analyze"],
        "workflow": [
            {
                "id": "s1",
                "description": "read $inputs.target",
                "tool": "read",
                "dependencies": [],
                "verification": "v",
                "completion_criteria": "c",
            },
            {
                "id": "s2",
                "description": "analyze findings",
                "tool": "analyze",
                "dependencies": ["s1"],
                "verification": "v",
                "completion_criteria": "c",
            },
        ],
        "verification": [{"check": "report ready"}],
    }
    args.update(kw)
    return Skill(**args)


def test_1_skill_registration(setup):
    _, _, _, skills, _ = setup
    created = skills.register(_skill())
    assert created.id
    assert skills.lookup("repo-scan").id == created.id


def test_2_duplicate_rejection(setup):
    _, _, _, skills, _ = setup
    created = skills.register(_skill())
    twin = _skill()
    twin.id = created.id  # force identical identity
    with pytest.raises(DomainValidationError, match="duplicate"):
        skills.register(twin)


def test_3_lookup(setup):
    _, _, _, skills, _ = setup
    skills.register(_skill())
    assert skills.lookup("repo-scan").version == "1.0.0"
    assert skills.lookup("repo-scan", version="9.9.9") is None
    assert skills.lookup("unknown") is None


def test_4_versioning(setup):
    _, _, _, skills, _ = setup
    v1 = skills.register(_skill())
    v2 = skills.new_version("repo-scan", description="better scan")
    assert v2.version == "1.0.1"
    assert v2.id != v1.id
    assert skills.lookup("repo-scan").id == v2.id  # latest active wins


def test_16_historical_version_preservation(setup):
    _, _, _, skills, _ = setup
    v1 = skills.register(_skill())
    skills.new_version("repo-scan", description="v2")
    assert [s.version for s in skills.versions("repo-scan")] == ["1.0.0", "1.0.1"]
    assert skills.lookup("repo-scan", version="1.0.0").id == v1.id


def test_5_scoped_skills(setup):
    _, _, _, skills, _ = setup
    skills.register(_skill(name="proj-scan", scope=MemoryScope.PROJECT, scope_id="p1"))
    assert skills.select("scan", scope_id="p1")
    assert not [s for s in skills.select("scan", scope_id="p2") if s.name == "proj-scan"]


def test_6_skill_selection(setup):
    _, _, _, skills, _ = setup
    skills.register(_skill())
    found = skills.select("scan a repository")
    assert found and found[0].name == "repo-scan"
    assert skills.select("unrelated query about cooking") == []


def test_7_skill_invocation_builds_plan(setup):
    _, _, registry, skills, builder = setup
    skill = skills.register(_skill())
    plan = builder.build(skill, {"target": "src/"})
    assert isinstance(plan, Plan)
    assert plan.steps[0].description == "read src/"
    assert [s.id for s in plan.steps] == ["s1", "s2"]


def test_8_plan_generation_validated(setup):
    _, _, _, skills, builder = setup
    bad = _skill(
        workflow=[
            {
                "id": "s1",
                "description": "bad",
                "tool": "read",
                "dependencies": ["ghost"],
                "verification": "v",
                "completion_criteria": "c",
            }
        ]
    )
    skill = skills.register(bad)
    with pytest.raises(Exception):
        builder.build(skill, {})


def test_9_skill_execution_through_toolrunner(setup):
    _, bus, registry, skills, builder = setup
    skill = skills.register(_skill())
    plan = builder.build(skill, {"target": "src/"})
    authorizer = AuthorizationManager(registry)
    runner = ToolRunner(registry, authorizer, bus)
    executor = ParallelExecutor(runner, registry, bus)
    result = executor.execute("t", plan)
    assert result.status is OverallStatus.COMPLETED
    skills.announce_outcome(skill, "t", True)


def test_10_permission_denial(setup):
    _, _, registry, skills, builder = setup
    skill = skills.register(_skill())
    plan = builder.build(skill, {"target": "src/"})
    from ai_ecosystem.tools import DenyAllAuthorizer

    runner = ToolRunner(registry, DenyAllAuthorizer())
    executor = ParallelExecutor(runner, registry)
    result = executor.execute("t", plan)
    assert result.status is OverallStatus.FAILED


def test_11_dangerous_skill_cannot_bypass_policy(setup, tmp_path):
    db, bus, registry, skills, builder = setup
    calls = {"n": 0}

    def danger(args):
        calls["n"] += 1
        return ToolResult(success=True, output="pwned")

    registry.register(
        Tool(name="danger", input_schema={"required": []}, risk_level=RiskLevel.HIGH), danger
    )
    evil = _skill(
        name="evil",
        allowed_tools=["danger"],
        workflow=[
            {
                "id": "s1",
                "description": "do evil",
                "tool": "danger",
                "dependencies": [],
                "verification": "v",
                "completion_criteria": "c",
            }
        ],
        risk_class=RiskLevel.LOW,
    )  # lies about its risk
    skill = skills.register(evil)
    plan = builder.build(skill, {})
    # Builder re-derives risk from the real tool contract, not the label.
    assert plan.steps[0].risk is RiskLevel.HIGH
    authorizer = AuthorizationManager(registry, context=RiskContext(root=str(tmp_path)))
    runner = ToolRunner(registry, authorizer, bus)
    result = ParallelExecutor(runner, registry, bus).execute("t", plan)
    assert result.status is OverallStatus.FAILED
    assert calls["n"] == 0  # denied before the handler


def test_12_skill_candidate_creation():
    plan = Plan(goal="g", steps=[], final_verification="v")
    from ai_ecosystem.core.models import PlanStep

    plan.steps = [
        PlanStep(
            id="s1",
            description="read it",
            dependencies=[],
            tools=["read"],
            verification="v",
            completion_criteria="c",
        )
    ]
    candidate = propose_from_workflow(plan, "auto-scan", author="observer")
    assert isinstance(candidate, SkillProposal)
    assert candidate.skill_type is SkillType.LEARNED_PROPOSAL
    assert candidate.allowed_tools == ["read"]
    assert candidate.workflow[0]["tool"] == "read"


def test_13_candidate_approval(setup):
    _, _, _, skills, _ = setup
    inbox = SkillCandidateStore()
    proposal = inbox.propose(
        SkillProposal(
            name="approved-scan",
            description="vetted",
            allowed_tools=["read"],
            workflow=[
                {
                    "id": "s1",
                    "description": "read",
                    "tool": "read",
                    "dependencies": [],
                    "verification": "v",
                    "completion_criteria": "c",
                }
            ],
        )
    )
    created = inbox.approve(proposal.id, skills)
    assert created.status is SkillStatus.ACTIVE
    assert skills.lookup("approved-scan") is not None
    assert inbox.get(proposal.id) is None  # consumed


def test_14_candidate_rejection():
    inbox = SkillCandidateStore()
    proposal = inbox.propose(SkillProposal(name="risky", allowed_tools=["x"]))
    assert inbox.reject(proposal.id) is True
    assert inbox.get(proposal.id) is None
    with pytest.raises(DomainValidationError):
        inbox.approve(proposal.id, None)


def test_15_disabled_skill_cannot_execute(setup):
    _, _, _, skills, builder = setup
    skill = skills.register(_skill())
    skills.set_status(skill.id, SkillStatus.DISABLED)
    assert skills.lookup("repo-scan") is None  # invisible to discovery
    with pytest.raises(DomainValidationError, match="DISABLED"):
        builder.build(skills._repo.get(skill.id), {})


def test_17_restart_persistence(tmp_path):
    path = str(tmp_path / "skills.db")
    first = Database(path)
    first.migrate()
    SkillRegistry(SqliteSkillRepository(first)).register(_skill())
    first.close()
    second = Database(path)
    second.migrate()
    try:
        found = SkillRegistry(SqliteSkillRepository(second)).lookup("repo-scan")
    finally:
        second.close()
    assert found is not None and found.version == "1.0.0"


def test_18_event_emission(setup):
    _, bus, _, skills, builder = setup
    seen = []
    bus.subscribe_all(seen.append)
    skill = skills.register(_skill())
    skills.select("scan")
    builder.build(skill, {"target": "x"})
    skills.set_status(skill.id, SkillStatus.DISABLED)
    skills.announce_outcome(skill, "t", True)
    inbox = SkillCandidateStore(bus)
    inbox.propose(SkillProposal(name="c", allowed_tools=["read"]))
    kinds = [e.event_type for e in seen]
    for expected in (
        EventType.SKILL_CREATED,
        EventType.SKILL_SELECTED,
        EventType.SKILL_INVOKED,
        EventType.SKILL_DISABLED,
        EventType.SKILL_COMPLETED,
        EventType.SKILL_PROPOSAL_CREATED,
    ):
        assert expected in kinds, expected


def test_skill_lookup_perf_smoke(setup):
    _, _, _, skills, _ = setup
    for index in range(50):
        skills.register(_skill(name=f"skill-{index:02d}"))
    started = time.monotonic()
    for _ in range(200):
        skills.select("repository scan")
    elapsed = time.monotonic() - started
    print(f"\nskill smoke: 200 lookups over 51 skills in {elapsed:.2f}s")
    assert elapsed < 5
    started = time.monotonic()
    for _ in range(200):
        skills.lookup("skill-07")
    elapsed = time.monotonic() - started
    print(f"skill smoke: 200 lookups in {elapsed:.2f}s")
    assert elapsed < 5
