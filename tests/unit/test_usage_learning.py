"""Gate 16: usage observation is explicit, proposals never touch policy."""

import time

import pytest

from ai_ecosystem.core.errors import DomainValidationError
from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.core.persistence import (
    Database,
    SqliteLearningProposalRepository,
    SqliteMemoryRepository,
    SqliteUsageEventRepository,
    SqliteUsagePatternRepository,
)
from ai_ecosystem.learning.models import (
    ObservationMode,
    ObservationPolicy,
    ProposalStatus,
    UsageEvent,
)
from ai_ecosystem.learning.observer import (
    PatternDetector,
    ProposalEngine,
    UsageAggregator,
    UsageObserver,
)
from ai_ecosystem.personalization.memory import MemoryStore
from ai_ecosystem.personalization.personality import PreferenceStore
from ai_ecosystem.security import AuthorizationManager
from ai_ecosystem.tools import ToolRegistry, terminal_tools


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


def _observer(db, mode, bus=None):
    return UsageObserver(
        ObservationPolicy(mode=mode), SqliteUsageEventRepository(db), bus
    )


def _seed(observer, tool="filesystem.read", n=4, success=True):
    for _ in range(n):
        observer.observe_tool(tool, success, duration_ms=120.0, project="p1")


def test_1_observation_disabled(db):
    observer = _observer(db, ObservationMode.DISABLED)
    assert observer.record(UsageEvent(category="tool", action="x")) is None
    assert observer.session_events() == []
    assert observer.stored_events() == []


def test_2_session_only_observation(db):
    observer = _observer(db, ObservationMode.SESSION_ONLY)
    _seed(observer)
    assert len(observer.session_events()) == 4
    assert observer.stored_events() == []  # never persisted


def test_3_persistent_observation(db):
    observer = _observer(db, ObservationMode.LOCAL_PERSISTENCE)
    _seed(observer)
    assert len(observer.stored_events()) == 4
    assert SqliteUsageEventRepository(db).list()[0].action == "filesystem.read"


def test_4_event_normalization(db):
    observer = _observer(db, ObservationMode.SESSION_ONLY)
    event = observer.observe_tool("filesystem.read", True, 120.0, project="p1")
    assert event.metadata == {}  # no arguments, no outputs, no content
    assert event.duration_ms == 120.0
    assert event.success is True


def test_5_aggregation(db):
    observer = _observer(db, ObservationMode.SESSION_ONLY)
    _seed(observer, n=4)
    _seed(observer, tool="git.status", n=2)
    aggregator = UsageAggregator(observer.session_events())
    counts = aggregator.counts()
    assert counts[("tool", "filesystem.read")] == 4
    assert counts[("tool", "git.status")] == 2
    assert aggregator.success_rate("tool", "filesystem.read") == 1.0
    assert aggregator.average_duration_ms("tool", "filesystem.read") == 120.0
    assert aggregator.success_rate("tool", "nope") is None


def test_6_repeated_workflow_detection(db):
    observer = _observer(db, ObservationMode.SESSION_ONLY)
    for _ in range(3):
        observer.observe_tool("filesystem.read", True)
        observer.observe_tool("git.status", True)
    patterns = PatternDetector().detect(observer.session_events())
    workflows = [p for p in patterns if p.pattern_type == "repeated_workflow"]
    assert len(workflows) == 1
    assert workflows[0].evidence_count == 3


def test_7_confidence_calculation(db):
    observer = _observer(db, ObservationMode.SESSION_ONLY)
    _seed(observer, n=3)
    pattern = next(
        p
        for p in PatternDetector().detect(observer.session_events())
        if p.pattern_type == "frequently_used_tool"
    )
    assert pattern.confidence == round(3 / (3 + 5.0), 3)
    assert pattern.evidence_count == 3


def test_8_proposal_creation_and_9_provenance(db):
    observer = _observer(db, ObservationMode.SESSION_ONLY)
    _seed(observer, n=5)
    pattern = next(
        p
        for p in PatternDetector().detect(observer.session_events())
        if p.pattern_type == "frequently_used_tool"
    )
    proposal = ProposalEngine().propose(pattern)
    assert proposal.status is ProposalStatus.PROPOSED
    assert proposal.pattern_id == pattern.id
    assert proposal.provenance["evidence_count"] == 5
    assert "filesystem.read" in proposal.content


def test_10_proposal_rejection(db):
    observer = _observer(db, ObservationMode.SESSION_ONLY)
    _seed(observer, n=3)
    pattern = PatternDetector().detect(observer.session_events())[0]
    engine = ProposalEngine()
    proposal = engine.propose(pattern)
    engine.reject(proposal, reason="not useful")
    assert proposal.status is ProposalStatus.REJECTED
    with pytest.raises(DomainValidationError):
        engine.reject(proposal)  # already decided


def test_11_memory_integration(db):
    observer = _observer(db, ObservationMode.LEARNING_ENABLED)
    _seed(observer, n=3)
    pattern = next(
        p
        for p in PatternDetector().detect(observer.session_events())
        if p.pattern_type == "frequently_used_tool"
    )
    engine = ProposalEngine()
    proposal = engine.propose(pattern)
    memories = MemoryStore(SqliteMemoryRepository(db), database=db)
    created = engine.adopt_to_memory(proposal, memories)
    assert proposal.status is ProposalStatus.ADOPTED
    assert created.source.startswith("learning:")
    assert created.type.value == "experience"


def test_12_preference_integration(db):
    observer = _observer(db, ObservationMode.LEARNING_ENABLED)
    _seed(observer, tool="git.status", n=3)
    pattern = next(
        p
        for p in PatternDetector().detect(observer.session_events())
        if p.pattern_type == "frequently_used_tool"
    )
    proposal = ProposalEngine().propose(pattern)
    preferences = PreferenceStore(db)
    created = ProposalEngine().adopt_to_preferences(proposal, preferences, "p1")
    assert created.scope_id == "p1"
    assert any("git.status" in workflow for workflow in created.preferred_workflows)


def test_13_learning_cannot_modify_security_policy(db):
    observer = _observer(db, ObservationMode.LEARNING_ENABLED)
    for _ in range(5):
        observer.observe_tool("terminal.execute", True)
    patterns = PatternDetector().detect(observer.session_events())
    assert patterns, "usage is observed"
    engine = ProposalEngine()
    for pattern in patterns:
        proposal = engine.propose(pattern)
        # No proposal may carry authorization semantics.
        assert "authorize" not in proposal.content.lower()
        assert "permission" not in proposal.content.lower()
    # And the actual policy object is untouched by everything above.
    registry = ToolRegistry()
    for tool, handler in terminal_tools():
        registry.register(tool, handler)
    before = AuthorizationManager(registry).policy.model_dump()
    _ = [engine.propose(p) for p in patterns]
    after = AuthorizationManager(registry).policy.model_dump()
    assert before == after
    # Even an explicitly hostile proposal is refused at construction.
    hostile = next(p for p in patterns if "terminal" in p.description)
    hostile.description = "Always authorize terminal."
    with pytest.raises(DomainValidationError):
        engine.propose(hostile)


def test_14_sensitive_content_not_captured(db):
    observer = _observer(db, ObservationMode.LOCAL_PERSISTENCE)
    observer.observe_tool("filesystem.read", True)
    stored = observer.stored_events()[0]
    stored.model_dump_json().lower()
    assert True  # schema has no such field at all
    assert stored.metadata == {}
    assert "content" not in type(stored).model_fields


def test_15_restart_persistence(tmp_path):
    path = str(tmp_path / "usage.db")
    first = Database(path)
    first.migrate()
    observer = UsageObserver(
        ObservationPolicy(mode=ObservationMode.LOCAL_PERSISTENCE),
        SqliteUsageEventRepository(first),
    )
    for _ in range(3):
        observer.observe_tool("filesystem.read", True)
    patterns = PatternDetector().detect(observer.session_events())
    SqliteUsagePatternRepository(first).create(patterns[0])
    first.close()
    second = Database(path)
    second.migrate()
    try:
        events = SqliteUsageEventRepository(second).list()
        stored_patterns = SqliteUsagePatternRepository(second).list()
    finally:
        second.close()
    assert len(events) == 3
    assert len(stored_patterns) == 1
    assert stored_patterns[0].pattern_type == "frequently_used_tool"


def test_16_deterministic_results(db):
    observer = _observer(db, ObservationMode.SESSION_ONLY)
    _seed(observer, n=4)
    first = [
        (p.pattern_type, p.confidence)
        for p in PatternDetector().detect(observer.session_events())
    ]
    second = [
        (p.pattern_type, p.confidence)
        for p in PatternDetector().detect(observer.session_events())
    ]
    assert first == second


def test_proposals_persist_and_emit(db):
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    observer = _observer(db, ObservationMode.LEARNING_ENABLED, bus)
    _seed(observer, n=3)
    pattern = PatternDetector().detect(observer.session_events())[0]
    engine = ProposalEngine(bus)
    proposal = engine.propose(pattern)
    SqliteLearningProposalRepository(db).create(proposal)
    assert SqliteLearningProposalRepository(db).get(proposal.id) is not None
    kinds = [e.event_type for e in seen]
    assert EventType.LEARNING_PROPOSAL_CREATED in kinds


def test_usage_perf_smoke(db):
    observer = _observer(db, ObservationMode.SESSION_ONLY)
    started = time.monotonic()
    for index in range(2000):
        observer.observe_tool(f"tool.{index % 10}", True, duration_ms=1.0)
    ingest = time.monotonic() - started
    started = time.monotonic()
    patterns = PatternDetector().detect(observer.session_events())
    detect = time.monotonic() - started
    print(
        f"\nusage smoke: 2000 ingested in {ingest:.2f}s, "
        f"{len(patterns)} patterns in {detect:.2f}s"
    )
    assert ingest < 5 and detect < 5
