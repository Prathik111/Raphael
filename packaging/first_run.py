"""First-run wizard state machine (Gate 44).

Guides Welcome -> system detection -> models -> permissions ->
privacy -> optional OCI/phone/hardware -> ready. Cloud, phone, and
hardware steps are skippable: the product never forces them.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import DomainValidationError


class WizardStep(str, Enum):
    """Ordered first-run stages."""

    WELCOME = "WELCOME"
    SYSTEM_DETECTION = "SYSTEM_DETECTION"
    MODEL_CONFIGURATION = "MODEL_CONFIGURATION"
    PERMISSION_CONFIGURATION = "PERMISSION_CONFIGURATION"
    PRIVACY_SETTINGS = "PRIVACY_SETTINGS"
    OCI_SETUP = "OCI_SETUP"
    PHONE_SETUP = "PHONE_SETUP"
    HARDWARE_SETUP = "HARDWARE_SETUP"
    READY = "READY"


_ORDER = [step for step in WizardStep]
_OPTIONAL = {WizardStep.OCI_SETUP, WizardStep.PHONE_SETUP, WizardStep.HARDWARE_SETUP}


class FirstRunState(BaseModel):
    """Durable wizard progress (kept outside the agent database)."""

    step: WizardStep = WizardStep.WELCOME
    completed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


class FirstRunWizard:
    """Step machine with skippable optional stages."""

    def __init__(self, state: Optional[FirstRunState] = None) -> None:
        self._state = state or FirstRunState()

    @property
    def state(self) -> FirstRunState:
        """Current progress (serializable)."""
        return self._state

    @property
    def current(self) -> WizardStep:
        """Stage awaiting input."""
        return self._state.step

    @property
    def done(self) -> bool:
        """True once READY is reached."""
        return self._state.step is WizardStep.READY

    def complete(self, settings: Optional[dict] = None) -> WizardStep:
        """Finish the current stage and advance (settings recorded).

        Settings keys that smell like credentials are refused: wizard
        state is stored unencrypted, so secrets must go to the
        SecretsProvider, never into setup answers.
        """
        from ai_ecosystem.core.secrets import looks_secret

        if self.done:
            raise DomainValidationError("wizard already complete")
        current = self._state.step
        if settings:
            for key in settings:
                if looks_secret(str(key)):
                    raise DomainValidationError(
                        f"setup answer {key!r} looks like a credential; "
                        "configure secrets via the environment instead")
            self._state.settings.update(settings)
        self._state.completed.append(current.value)
        self._state.step = _ORDER[_ORDER.index(current) + 1]
        return self._state.step

    def skip(self) -> WizardStep:
        """Skip an optional stage (required stages refuse)."""
        if self.done:
            raise DomainValidationError("wizard already complete")
        current = self._state.step
        if current not in _OPTIONAL:
            raise DomainValidationError(f"stage {current.value} cannot be skipped")
        self._state.skipped.append(current.value)
        self._state.step = _ORDER[_ORDER.index(current) + 1]
        return self._state.step
