"""Credential handling: providers read secrets, never keep or leak them.

Rules enforced by construction and tests:

* secrets come from a SecretsProvider (environment in production,
  in-memory dicts in tests) -- never source code, never the frontend;
* secret values never enter logs, events, prompts, or error messages
  (use :func:`redact` before emitting anything user-visible);
* secret-looking payload keys are masked wherever they appear.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Optional


class SecretsProvider(ABC):
    """Source of named credentials (read-only interface)."""

    @abstractmethod
    def get(self, name: str) -> Optional[str]:
        """Return the credential, or None when absent."""
        raise NotImplementedError

    def require(self, name: str) -> str:
        """Return the credential or raise a redacted error."""
        value = self.get(name)
        if not value:
            raise CredentialError(f"credential {name!r} is not configured")
        return value


class CredentialError(Exception):
    """A credential is missing (message names the key, never the value)."""


class EnvSecretsProvider(SecretsProvider):
    """Reads ``PREFIX_NAME`` variables from the process environment."""

    def __init__(self, prefix: str = "AI_ECO_") -> None:
        self._prefix = prefix

    def get(self, name: str) -> Optional[str]:
        """Look up PREFIX + name in the environment."""
        return os.environ.get(f"{self._prefix}{name}")


class DictSecretsProvider(SecretsProvider):
    """In-memory credentials for tests (never production secrets)."""

    def __init__(self, values: Optional[dict[str, str]] = None) -> None:
        self._values = dict(values or {})

    def get(self, name: str) -> Optional[str]:
        """Look up a test credential."""
        return self._values.get(name)


_SECRET_HINTS = ("key", "token", "secret", "password", "credential", "private")

# High-confidence secret *values* (independent of their keys).
_VALUE_PATTERNS = (
    r"sk-[A-Za-z0-9]{8,}",
    r"ghp_[A-Za-z0-9]{8,}",
    r"gho_[A-Za-z0-9]{8,}",
    r"AKIA[0-9A-Z]{16}",
    r"xox[bap]-[A-Za-z0-9-]+",
    r"-----BEGIN [A-Z ]*PRIVATE KEY",
)


def looks_secret(key: str) -> bool:
    """True when a mapping key smells like a credential.

    Token matching (not substring) avoids false positives like "monkey" or
    "keyboard" while still catching api_key, auth-token, etc.
    """
    import re

    tokens = set(re.split(r"[^a-z0-9]+", key.lower()))
    return any(hint in tokens for hint in _SECRET_HINTS)


def looks_like_secret_value(value: object) -> bool:
    """True when a string value matches known credential formats."""
    import re

    if not isinstance(value, str):
        return False
    return any(re.search(pattern, value) for pattern in _VALUE_PATTERNS)


def redact(mapping: dict[str, Any]) -> dict[str, Any]:
    """Copy with secret-looking entries masked, at any depth.

    Masks values under secret-smelling keys AND values matching known
    credential formats wherever they appear in nested mappings/lists.
    """
    return {key: _redact_value(key, value) for key, value in mapping.items()}


def sanitize(value: Any) -> Any:
    """Recursively sanitize ANY value (review: one canonical sanitizer).

    Handles dicts, lists, tuples, sets, Pydantic models, dataclasses,
    and exceptions -- anything that can flow into logs, persistence,
    model context, or UI events. Secret-looking content becomes "***";
    containers keep their shape with sanitized contents.
    """
    import dataclasses

    if isinstance(value, dict):
        return {key: _redact_value(key, item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_item(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return {_sanitize_item(item) for item in value}
    if isinstance(value, BaseException):
        return sanitize_exception(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return sanitize(model_dump(mode="json"))
        except Exception:  # noqa: BLE001 -- fall through to repr
            pass
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return sanitize(dataclasses.asdict(value))
        except Exception:  # noqa: BLE001 -- fall through to repr
            pass
    return "***" if looks_like_secret_value(value) else value


def _sanitize_item(item: Any) -> Any:
    if isinstance(item, dict):
        return redact(item)
    if isinstance(item, (list, tuple, set, frozenset)):
        return sanitize(item)
    return "***" if looks_like_secret_value(item) else item


def sanitize_exception(exc: BaseException) -> str:
    """Exception text safe for logs/events: masks credential formats."""
    text = f"{type(exc).__name__}: {exc}"
    if looks_like_secret_value(text):
        return f"{type(exc).__name__}: ***"
    return text


def _redact_value(key: str, value: Any) -> Any:
    if looks_secret(key) or looks_like_secret_value(value):
        return "***"
    if isinstance(value, dict):
        return redact(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_item(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return {_sanitize_item(item) for item in value}
    return value
