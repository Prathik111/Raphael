"""Personality + preference profiles (Gate 13).

PERSONALITY (how the agent communicates), PREFERENCES (what the user
likes), FACTS/MEMORY (what is true/remembered), and POLICIES (what is
allowed) are four separate things. Profiles are versioned data owned by
the user -- never authorization, never model weights.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.core.models.enums import MemoryScope

Tone = Literal["warm", "neutral", "formal", "playful"]
Formality = Literal["casual", "balanced", "formal"]
Humor = Literal["none", "light", "playful"]
Verbosity = Literal["concise", "balanced", "detailed"]
Proactivity = Literal["reactive", "suggestive", "proactive"]
CommunicationStyle = Literal["direct", "explanatory", "collaborative"]
OutputFormat = Literal["markdown", "plain", "json"]


class PersonalityProfile(Entity):
    """How the agent communicates (explicit constrained fields)."""

    display_name: str = "Assistant"
    tone: Tone = "neutral"
    formality: Formality = "balanced"
    humor: Humor = "none"
    verbosity: Verbosity = "balanced"
    proactivity: Proactivity = "reactive"
    communication_style: CommunicationStyle = "direct"
    behavioral_rules: list[str] = Field(default_factory=list)
    version: int = 1


class PreferenceProfile(Entity):
    """What the user prefers (workflows, tools, formats, defaults)."""

    scope: MemoryScope = MemoryScope.GLOBAL
    scope_id: str = ""
    preferred_workflows: list[str] = Field(default_factory=list)
    preferred_tools: list[str] = Field(default_factory=list)
    output_format: OutputFormat = "markdown"
    defaults: dict[str, str] = Field(default_factory=dict)
    version: int = 1
