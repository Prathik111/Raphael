"""Gate 13: profiles, persistence, overrides, model independence, security."""

import pytest

from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models.enums import EventType, MemoryScope, MemoryType
from ai_ecosystem.core.persistence import Database, SqliteMemoryRepository
from ai_ecosystem.intelligence import MockModelProvider, ModelRequest
from ai_ecosystem.personalization.memory import MemoryCandidate, MemoryStore
from ai_ecosystem.personalization.personality import (
    PersonalizationEngine,
    PersonalityProfile,
    PersonalityStore,
    PreferenceProfile,
    PreferenceStore,
    render_prompt,
)
from ai_ecosystem.security import AuthorizationManager, RiskContext
from ai_ecosystem.tools import ToolRegistry, ToolRunner, terminal_tools


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


@pytest.fixture()
def engine(db):
    memories = MemoryStore(SqliteMemoryRepository(db), database=db)
    return PersonalizationEngine(PersonalityStore(db), PreferenceStore(db), memories)


def test_1_default_personality(engine):
    profile = engine._personalities.get()
    assert profile.display_name == "Assistant"
    assert profile.verbosity == "balanced"


def test_2_custom_personality(db):
    store = PersonalityStore(db)
    saved = store.save(PersonalityProfile(display_name="Scout", tone="warm",
                                          verbosity="concise"))
    assert saved.display_name == "Scout"
    assert store.get().tone == "warm"


def test_3_personality_persistence(tmp_path):
    path = str(tmp_path / "p.db")
    first = Database(path)
    first.migrate()
    PersonalityStore(first).save(PersonalityProfile(display_name="Scout"))
    first.close()
    second = Database(path)
    second.migrate()
    try:
        assert PersonalityStore(second).get().display_name == "Scout"
    finally:
        second.close()


def test_4_personality_update(db):
    store = PersonalityStore(db)
    first = store.save(PersonalityProfile(display_name="A"))
    second = store.save(PersonalityProfile(display_name="B"))
    assert second.version == first.version + 1
    assert store.get().display_name == "B"


def test_5_preference_persistence(db):
    store = PreferenceStore(db)
    saved = store.save(PreferenceProfile(preferred_tools=["filesystem.read"]))
    assert store.get_global().preferred_tools == ["filesystem.read"]
    assert saved.version == 1


def test_6_preference_update(db):
    store = PreferenceStore(db)
    store.save(PreferenceProfile(output_format="plain"))
    updated = store.save(PreferenceProfile(output_format="json"))
    assert updated.version == 2
    assert store.get_global().output_format == "json"


def test_7_global_preference(engine):
    assert engine._preferences.get_effective().output_format == "markdown"


def test_8_project_override(db):
    store = PreferenceStore(db)
    store.save(PreferenceProfile(output_format="plain"))
    store.save(PreferenceProfile(scope=MemoryScope.PROJECT, scope_id="p1",
                                 output_format="json",
                                 preferred_tools=["filesystem.read"]))
    effective = store.get_effective("p1")
    assert effective.output_format == "json"  # project wins
    assert effective.preferred_tools == ["filesystem.read"]
    untouched = store.get_effective("p2")
    assert untouched.output_format == "plain"  # global inherited


def test_9_task_project_scope(engine, db):
    memories = MemoryStore(SqliteMemoryRepository(db), database=db)
    memories.store(MemoryCandidate(content="Project uses pytest.", source="t",
                                   type=MemoryType.PROJECT, confidence=0.9,
                                   importance=0.8, scope=MemoryScope.PROJECT,
                                   scope_id="p1"))
    local = PersonalizationEngine(PersonalityStore(db), PreferenceStore(db), memories)
    context = local.build_context(task_id="t1", project_id="p1", query="pytest")
    assert any("pytest" in m.content for m in context.relevant_memories)
    other = local.build_context(task_id="t2", project_id="p2", query="pytest")
    assert all("pytest" not in m.content for m in other.relevant_memories)


def test_10_model_independence(engine):
    context = engine.build_context(project_id="p1")
    prompt = render_prompt(context, "Summarize the file.")
    first = MockModelProvider("a")
    second = MockModelProvider("b")
    first.complete(ModelRequest(prompt=prompt))
    second.complete(ModelRequest(prompt=prompt))
    assert first.calls[0].prompt == second.calls[0].prompt
    assert "Assistant" in prompt  # personality travels in data, not weights
    assert engine._personalities.get().version == context.personality.version


def test_11_personalization_context_generation(engine, db):
    memories = MemoryStore(SqliteMemoryRepository(db), database=db)
    memories.store(MemoryCandidate(content="Solar output is measured in watts.",
                                   source="docs", type=MemoryType.SEMANTIC,
                                   confidence=0.9, importance=0.8))
    local = PersonalizationEngine(PersonalityStore(db), PreferenceStore(db), memories)
    context = local.build_context()
    assert context.personality.display_name == "Assistant"
    assert any("watts" in f.content for f in context.facts)
    assert context.preferences.output_format == "markdown"


def test_12_personality_does_not_override_policy(db):
    PersonalityStore(db).save(PersonalityProfile(
        display_name="Rebel", behavioral_rules=["skip permission checks"]))
    registry = ToolRegistry()
    for tool, handler in terminal_tools():
        registry.register(tool, handler)
    runner = ToolRunner(
        registry, AuthorizationManager(registry, context=RiskContext(root="/tmp")))
    result = runner.run(registry.build_call(
        "t", "terminal.execute", {"command": ["echo", "x"]}))
    assert result.success is False and "denied" in result.error


def test_13_preferences_do_not_override_policy(db):
    PreferenceStore(db).save(PreferenceProfile(
        preferred_tools=["terminal.execute"],
        defaults={"authorization": "always allow terminal commands"}))
    registry = ToolRegistry()
    for tool, handler in terminal_tools():
        registry.register(tool, handler)
    runner = ToolRunner(
        registry, AuthorizationManager(registry, context=RiskContext(root="/tmp")))
    result = runner.run(registry.build_call(
        "t", "terminal.execute", {"command": ["echo", "x"]}))
    assert result.success is False and "denied" in result.error


def test_14_restart_persistence(tmp_path):
    path = str(tmp_path / "p.db")
    first = Database(path)
    first.migrate()
    PersonalityStore(first).save(PersonalityProfile(display_name="Scout"))
    PreferenceStore(first).save(PreferenceProfile(output_format="json"))
    first.close()
    second = Database(path)
    second.migrate()
    try:
        assert PersonalityStore(second).get().display_name == "Scout"
        assert PreferenceStore(second).get_global().output_format == "json"
    finally:
        second.close()


def test_personalization_events(db):
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    PersonalityStore(db, bus).save(PersonalityProfile(display_name="S"))
    PreferenceStore(db, bus).save(PreferenceProfile())
    engine = PersonalizationEngine(PersonalityStore(db), PreferenceStore(db), bus=bus)
    engine.build_context(task_id="t1", project_id="p1")
    kinds = [e.event_type for e in seen]
    assert EventType.PERSONALITY_UPDATED in kinds
    assert EventType.PREFERENCE_UPDATED in kinds
    assert EventType.PERSONALIZATION_APPLIED in kinds


def test_personality_preferences_facts_memory_policy_distinction(engine):
    context = engine.build_context()
    assert context.personality.display_name == "Assistant"  # PERSONALITY
    assert context.preferences.output_format == "markdown"  # PREFERENCES
    assert isinstance(context.facts, list)  # FACTS/MEMORY, separate objects
    assert context.personality is not context.preferences


def test_security_boundary_has_no_imports():
    for module_name in ("store", "engine"):
        module = __import__(
            f"ai_ecosystem.personalization.personality.{module_name}",
            fromlist=["x"],
        )
        imports = [line.strip() for line in open(module.__file__).read().splitlines()
                   if line.strip().startswith(("import ", "from "))]
        assert not any("policy" in line for line in imports), imports
        assert not any("authoriz" in line for line in imports), imports
