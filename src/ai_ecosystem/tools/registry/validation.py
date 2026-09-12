"""Single canonical argument validation for tool contracts.

Review fix 02/06/58: the planner validator, the runner's final
boundary check, and the authorizer must agree on what "valid
arguments" means. Every one of them calls :func:`check_arguments`
against the registry contract, so a mismatch cannot pass one gate
and fail another.
"""

from __future__ import annotations

from typing import Any

_TYPES: dict[str, type | tuple] = {
    "string": str,
    "array": list,
    "object": dict,
    "boolean": bool,
    "number": (int, float),
}


def check_arguments(tool: Any, arguments: dict) -> list[str]:
    """Return human-readable problems (empty when the call is valid).

    Checks required-parameter presence AND JSON types from the
    contract's ``input_schema`` (``required`` + ``properties``).
    Unknown type names are ignored (forward compatible).
    """
    problems: list[str] = []
    schema = getattr(tool, "input_schema", {}) or {}
    required = schema.get("required", [])
    missing = [name for name in required if name not in arguments]
    if missing:
        problems.append(f"missing required arguments: {', '.join(missing)}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return problems
    for param, want in properties.items():
        if param not in arguments:
            continue
        want_type = _TYPES.get(want)
        if want_type is not None and not isinstance(arguments[param], want_type):
            problems.append(
                f"argument {param!r} must be {want}, got {type(arguments[param]).__name__}"
            )
    return problems
