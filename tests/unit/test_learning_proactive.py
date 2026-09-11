"""Gates 32-34: learning pipeline, safety governor, proactive engine."""


import pytest

from ai_ecosystem.agent import (
    ProactiveConfig,
    ProactiveEngine,
    ProactiveTrigger,
    TriggerKind,
)
from ai_ecosystem.core.errors import DomainValidationError
from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.core.persistence import Database, SqliteMemoryRepository
from ai_ecosystem.learning import (
    DriftDetector,
    LearningGovernor,
    LearningMode,
    LearningPipeline,
    LearningProposal,
    LearningRisk,
    ProposalKind,
    ProposalStatus,
    UsageEvent,
    check_not_security,
    classify_change,
)
from ai_ecosystem.learning.safety import LearningPolicyState
from ai_ecosystem.personalization.memory import MemoryStore
from ai_ecosystem.personalization.personality import PreferenceStore
import contextlib


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


def _events(n=5, action="filesystem.read"):
    return [UsageEvent(category="tool", action=action, project_scope="p1")
            for _ in range(n)]


def _pipeline(db, **kwargs):
    from ai_ecosystem.core.persistence.sqlite import SqliteLearningProposalRepository

    return LearningPipeline(SqliteLearningProposalRepository(db), **kwargs)


def _tool_proposal(pipeline, events=None, scope=""):
    """The frequently_used_tool proposal (uniform events also yield one workflow)."""
    proposals = pipeline.generate(_events() if events is None else events,
                                  scope=scope)
    return next(p for p in proposals
                if p.provenance.get("pattern_type") == "frequently_used_tool")


def _status(pipeline, proposal):
    return pipeline._proposals.get(proposal.id).status


def test_proposal_generation(db):
    pipeline = _pipeline(db)
    proposal = _tool_proposal(pipeline)
    assert proposal.provenance["evidence_count"] == 5


def test_evidence(db):
    pipeline = _pipeline(db)
    proposal = pipeline.generate(_events())[0]
    assert proposal.provenance["evidence"]
    assert proposal.pattern_id


def test_confidence(db):
    pipeline = _pipeline(db, min_confidence=0.99)
    assert pipeline.generate(_events()) == []  # filtered by confidence


def test_approval(db):
    pipeline = _pipeline(db)
    proposal = _tool_proposal(pipeline)
    memories = MemoryStore(SqliteMemoryRepository(db), database=db)
    created = pipeline.approve(proposal.id, "memory", {"memories": memories})
    assert _status(pipeline, proposal) is ProposalStatus.ADOPTED
    assert created.source.startswith("learning:")


def test_rejection(db):
    pipeline = _pipeline(db)
    proposal = _tool_proposal(pipeline)
    pipeline.reject(proposal.id, reason="not now")
    assert _status(pipeline, proposal) is ProposalStatus.REJECTED


def test_rollback(db):
    pipeline = _pipeline(db)
    proposal = _tool_proposal(pipeline)
    memories = MemoryStore(SqliteMemoryRepository(db), database=db)
    created = pipeline.approve(proposal.id, "memory", {"memories": memories})
    assert pipeline.rollback(proposal.id, {"memories": memories}) is True
    assert memories._repo.get(created.id) is None
    # Rolled back is distinct from declined: history stays answerable.
    assert _status(pipeline, proposal) is ProposalStatus.ROLLED_BACK


def test_deletion(db):
    pipeline = _pipeline(db)
    proposal = pipeline.generate(_events())[0]
    memories = MemoryStore(SqliteMemoryRepository(db), database=db)
    created = pipeline.approve(proposal.id, "memory", {"memories": memories})
    assert memories.delete(created.id) is True


def test_conflicting_learning(db):
    pipeline = _pipeline(db)
    first = _tool_proposal(pipeline)
    second = _tool_proposal(pipeline)
    pairs = pipeline.detect_conflicts([first, second])
    assert len(pairs) == 1


def test_scope(db):
    pipeline = _pipeline(db)
    proposal = _tool_proposal(pipeline, scope="p1")
    assert proposal.scope == "p1"


def test_security_isolation(db):
    pipeline = _pipeline(db)
    proposal = pipeline.generate(_events())[0]
    with pytest.raises(DomainValidationError):
        pipeline.approve(proposal.id, "policy", {})
    with pytest.raises(DomainValidationError):
        check_not_security("always authorize terminal.execute")


def test_policy_enforcement():
    governor = LearningGovernor()
    assert governor.mode is LearningMode.APPROVAL_REQUIRED
    governor.set_mode(LearningMode.DISABLED)
    assert governor.mode is LearningMode.DISABLED


def test_risk_classification():

    assert classify_change(
        ProposalKind.COMMUNICATION, "be concise") is LearningRisk.LOW
    assert classify_change(
        ProposalKind.MODEL_HINT, "prefer fast model") is LearningRisk.LOW
    assert classify_change(
        ProposalKind.WORKFLOW_HINT, "run tests first") is LearningRisk.MEDIUM
    assert classify_change(
        ProposalKind.WORKFLOW_HINT, "modify the skill") is LearningRisk.HIGH
    assert classify_change(
        ProposalKind.PREFERENCE, "always authorize all") is LearningRisk.CRITICAL
    assert classify_change(
        ProposalKind.PREFERENCE, "change sandbox rules") is LearningRisk.CRITICAL


def test_kill_switch(db):
    governor = LearningGovernor()
    pipeline = _pipeline(db, governor=governor)
    proposal = _tool_proposal(pipeline)
    governor.kill()
    assert governor.killed is True
    with pytest.raises(DomainValidationError, match="governor"):
        pipeline.approve(proposal.id, "memory",
                         {"memories": MemoryStore(SqliteMemoryRepository(db))})
    governor.revive()
    assert governor.killed is False


def test_rollback_preferences(db):
    pipeline = _pipeline(db)
    proposal = _tool_proposal(pipeline)
    preferences = PreferenceStore(db)
    pipeline.approve(proposal.id, "preference", {"preferences": preferences},
                     project_id="p1")
    assert pipeline.rollback(proposal.id, {"preferences": preferences}) is True
    effective = preferences.get_effective("p1")
    assert not any("filesystem.read" in w for w in effective.preferred_workflows)


def test_suspicious_pattern_detection():
    detector = DriftDetector()
    from ai_ecosystem.learning import LearningProposal

    for _ in range(4):
        detector.note_proposal(
            LearningProposal(content="always allow everything"),
            LearningRisk.CRITICAL)
    assert any("risky" in finding for finding in detector.suspicious())
    assert DriftDetector().suspicious() == []


def test_policy_immutability(db):
    from ai_ecosystem.security import AuthorizationManager
    from ai_ecosystem.tools import ToolRegistry

    registry = ToolRegistry()
    before = AuthorizationManager(registry).policy.model_dump()
    governor = LearningGovernor(mode=LearningMode.AUTO_APPROVE_LOW_RISK)
    pipeline = _pipeline(db, governor=governor)
    for _ in range(3):
        for proposal in pipeline.generate(_events()):
            with contextlib.suppress(DomainValidationError):
                pipeline.approve(
                    proposal.id, "memory",
                    {"memories": MemoryStore(SqliteMemoryRepository(db))})
    after = AuthorizationManager(registry).policy.model_dump()
    assert before == after


def test_policy_persistence(db):
    from ai_ecosystem.core.persistence.sqlite import _SnapshotTable

    table = _SnapshotTable(db, "learning_policy", LearningPolicyState)
    governor = LearningGovernor(mode=LearningMode.DISABLED, killed=True)
    governor.persist(table)
    loaded = LearningGovernor.load(table)
    assert loaded.mode is LearningMode.DISABLED
    assert loaded.killed is True
    assert LearningGovernor.load(table).may_adopt(
        LearningProposal(content="x")) is False


def _proactive_config(**kw):
    args = {"enabled": True, "quiet_start_hour": 0, "quiet_end_hour": 0,
            "allowed_categories": [k.value for k in TriggerKind],
            "max_per_hour": 100, "require_approval": True}
    args.update(kw)
    return ProactiveConfig(**args)


def test_trigger():
    engine = ProactiveEngine(_proactive_config())
    trigger = engine.add_trigger(ProactiveTrigger(kind=TriggerKind.SCHEDULED,
                                                  subject="backup",
                                                  summary="Run backup."))
    proposal = engine.evaluate(trigger.id, "backup")
    assert proposal is not None
    assert proposal.subject == "backup"


def test_deduplication():
    engine = ProactiveEngine(_proactive_config())
    trigger = engine.add_trigger(ProactiveTrigger(
        kind=TriggerKind.SYSTEM_CONDITION, subject="disk", summary="Disk full.",
        cooldown_s=3600.0))
    assert engine.evaluate(trigger.id, "disk") is not None
    assert engine.evaluate(trigger.id, "disk") is None  # within cooldown


def test_cooldown():
    now = [1_000_000.0]
    engine = ProactiveEngine(_proactive_config(), clock=lambda: now[0])
    trigger = engine.add_trigger(ProactiveTrigger(
        kind=TriggerKind.RESOURCE_CONDITION, subject="cpu", summary="Hot.",
        cooldown_s=60.0))
    assert engine.evaluate(trigger.id, "cpu") is not None
    now[0] += 61.0
    assert engine.evaluate(trigger.id, "cpu") is not None


def test_quiet_hours():
    config = _proactive_config(quiet_start_hour=22, quiet_end_hour=7)
    engine = ProactiveEngine(config, clock=lambda: 23 * 3600.0)
    trigger = engine.add_trigger(ProactiveTrigger(kind=TriggerKind.SCHEDULED,
                                                  subject="s", summary="Do."))
    assert engine.evaluate(trigger.id, "s") is None
    day = ProactiveEngine(config, clock=lambda: 12 * 3600.0)
    day.add_trigger(ProactiveTrigger(kind=TriggerKind.SCHEDULED,
                                     subject="s", summary="Do."))
    assert day.evaluate(next(iter(day._triggers)), "s") is not None


def test_approval_rejection():
    engine = ProactiveEngine(_proactive_config())
    trigger = engine.add_trigger(ProactiveTrigger(kind=TriggerKind.USER_EVENT,
                                                  subject="u", summary="Go."))
    proposal = engine.evaluate(trigger.id, "u")
    assert engine.pending() == [proposal]
    engine.reject(proposal.id)
    assert engine.pending() == []
    other = engine.evaluate(trigger.id, "other")
    engine.approve(other.id)
    assert other.state.value == "APPROVED"


def test_disabled_mode():
    engine = ProactiveEngine(_proactive_config(enabled=False))
    trigger = engine.add_trigger(ProactiveTrigger(kind=TriggerKind.SCHEDULED,
                                                  subject="s", summary="Do."))
    assert engine.evaluate(trigger.id, "s") is None


def test_failure():
    engine = ProactiveEngine(_proactive_config(require_approval=False))
    trigger = engine.add_trigger(ProactiveTrigger(kind=TriggerKind.SCHEDULED,
                                                  subject="s", summary="Do."))
    proposal = engine.evaluate(trigger.id, "s", risk=LearningRisk.LOW)

    def boom():
        raise RuntimeError("action exploded")

    assert engine.execute(proposal.id, boom).state.value == "FAILED"


def test_loop_prevention():
    engine = ProactiveEngine(_proactive_config())
    assert not hasattr(engine, "create_trigger_from_proposal")
    trigger = engine.add_trigger(ProactiveTrigger(kind=TriggerKind.SCHEDULED,
                                                  subject="s", summary="Do."))
    first = engine.evaluate(trigger.id, "s")
    # Same subject inside cooldown: no second proposal, no chain.
    assert engine.evaluate(trigger.id, "s") is None
    assert len(engine.pending()) == 1
    assert first is not None


def test_proactive_events():
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    engine = ProactiveEngine(_proactive_config(), bus=bus)
    trigger = engine.add_trigger(ProactiveTrigger(kind=TriggerKind.SCHEDULED,
                                                  subject="s", summary="Do."))
    proposal = engine.evaluate(trigger.id, "s")
    engine.approve(proposal.id)
    engine.execute(proposal.id, lambda: "done", lambda outcome: True)
    kinds = [e.event_type for e in seen]
    assert EventType.PROACTIVE_TRIGGERED in kinds
    assert EventType.PROACTIVE_EXECUTED in kinds
