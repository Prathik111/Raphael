"""Personalization engine: structured context, model-agnostic (Gate 13).

Combines personality + effective preferences + scoped memories into one
PersonalizationContext any compatible model can consume. The context
shapes communication only -- it is never consulted by authorization,
risk, or policy code (separate modules, no imports between them).
"""

from __future__ import annotations


from pydantic import Field

from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.core.models.domain import Memory
from ai_ecosystem.core.models.enums import EventType, MemoryScope, MemoryType
from ai_ecosystem.personalization.memory.store import MemoryStore
from ai_ecosystem.personalization.personality.profiles import (
    PersonalityProfile,
    PreferenceProfile,
)
from ai_ecosystem.personalization.personality.store import (
    PersonalityStore,
    PreferenceStore,
)


class PersonalizationContext(Entity):
    """Everything a model needs to respond in character, as pure data."""

    personality: PersonalityProfile = Field(default_factory=PersonalityProfile)
    preferences: PreferenceProfile = Field(default_factory=PreferenceProfile)
    facts: list[Memory] = Field(default_factory=list)
    relevant_memories: list[Memory] = Field(default_factory=list)
    project_id: str = ""
    task_id: str = ""


def render_prompt(context: PersonalizationContext, goal: str) -> str:
    """Render provider-agnostic prompt text from a context (deterministic)."""
    personality = context.personality
    lines = [
        f"You are {personality.display_name}: {personality.tone} tone, "
        f"{personality.formality} formality, {personality.verbosity} verbosity.",
        f"Communicate {personality.communication_style}; "
        f"humor {personality.humor}; proactivity {personality.proactivity}.",
    ]
    for rule in personality.behavioral_rules:
        lines.append(f"Behavioral rule: {rule}")
    prefs = context.preferences
    if prefs.preferred_tools:
        lines.append(f"Preferred tools: {', '.join(prefs.preferred_tools)}.")
    if prefs.output_format != "markdown":
        lines.append(f"Output format: {prefs.output_format}.")
    for fact in context.facts:
        lines.append(f"Known fact: {fact.content}")
    for memory in context.relevant_memories:
        lines.append(f"Recalled ({memory.scope.value}): {memory.content}")
    lines.append(f"User goal: {goal}")
    return "\n".join(lines)


class PersonalizationEngine:
    """Builds PersonalizationContexts from profiles + scoped memory."""

    def __init__(
        self,
        personalities: PersonalityStore,
        preferences: PreferenceStore,
        memories: MemoryStore | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._personalities = personalities
        self._preferences = preferences
        self._memories = memories
        self._bus = bus

    def build_context(
        self,
        task_id: str = "",
        project_id: str = "",
        agent_id: str = "",
        query: str = "",
    ) -> PersonalizationContext:
        """Assemble personality + effective prefs + scoped memories."""
        personality = self._personalities.get()
        preferences = self._preferences.get_effective(project_id)
        facts: list[Memory] = []
        relevant: list[Memory] = []
        if self._memories is not None:
            facts = self._memories.retrieve(
                MemoryScope.GLOBAL, memory_type=MemoryType.SEMANTIC, limit=5
            )
            if project_id:
                relevant.extend(self._memories.retrieve(
                    MemoryScope.PROJECT, project_id, query=query,
                    project_id=project_id, limit=5,
                ))
            if task_id:
                relevant.extend(self._memories.retrieve(
                    MemoryScope.TASK, task_id, query=query,
                    project_id=project_id, limit=5,
                ))
            if agent_id:
                relevant.extend(self._memories.retrieve(
                    MemoryScope.AGENT, agent_id, query=query, limit=5
                ))
        context = PersonalizationContext(
            personality=personality, preferences=preferences,
            facts=facts, relevant_memories=relevant[:10],
            project_id=project_id, task_id=task_id,
        )
        if self._bus is not None:
            self._bus.publish(Event(
                event_type=EventType.PERSONALIZATION_APPLIED, task_id=task_id,
                payload={"project_id": project_id,
                         "personality_version": personality.version,
                         "preferences_version": preferences.version,
                         "memories": len(relevant)},
            ))
        return context

    def summarize(self, context: PersonalizationContext, content: str) -> str:
        """Shape a result summary honoring verbosity (communication only)."""
        verbosity = context.personality.verbosity
        if verbosity == "concise":
            first = content.strip().splitlines()[0] if content.strip() else ""
            return f"[{context.personality.display_name}] {first}"
        if verbosity == "detailed":
            header = (
                f"{context.personality.display_name} "
                f"({context.personality.tone}, {context.personality.formality}):"
            )
            return f"{header}\n{content}"
        return f"{context.personality.display_name}: {content}"
