"""PACE task state machine (BUILD_PLAN Gate 1).

Single source of truth for legal transitions. The runtime, planner, and
future executor must consult this module instead of hard-coding edges.

Flow::

    CREATED -> UNDERSTANDING -> AWARENESS -> RESEARCHING -> PLANNING
      -> WAITING_PERMISSION -> EXECUTING -> VERIFYING -> COMPLETED

    AWARENESS -> PLANNING is allowed when research is skipped (Gate 14);
    the RESEARCHING state is then simply not entered.

    VERIFYING -> RECOVERING -> EXECUTING   (retry loop, bounded by policy)
    RECOVERING -> PLANNING                 (replan path)
    any non-terminal -> FAILED / CANCELLED
"""

from ai_ecosystem.core.errors.exceptions import InvalidStateTransitionError
from ai_ecosystem.core.models.enums import TaskState

TERMINAL_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
)

ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.CREATED: frozenset({TaskState.UNDERSTANDING}),
    TaskState.UNDERSTANDING: frozenset({TaskState.AWARENESS}),
    TaskState.AWARENESS: frozenset({TaskState.RESEARCHING, TaskState.PLANNING}),
    TaskState.RESEARCHING: frozenset({TaskState.PLANNING}),
    TaskState.PLANNING: frozenset({TaskState.WAITING_PERMISSION}),
    TaskState.WAITING_PERMISSION: frozenset({TaskState.EXECUTING}),
    TaskState.EXECUTING: frozenset({TaskState.VERIFYING}),
    TaskState.VERIFYING: frozenset({TaskState.COMPLETED, TaskState.RECOVERING}),
    TaskState.RECOVERING: frozenset({TaskState.EXECUTING, TaskState.PLANNING}),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}

# Any non-terminal state may escalate to FAILED or CANCELLED.
for _state, _targets in list(ALLOWED_TRANSITIONS.items()):
    if _state not in TERMINAL_STATES:
        ALLOWED_TRANSITIONS[_state] = _targets | {TaskState.FAILED, TaskState.CANCELLED}


def is_valid_transition(from_state: TaskState, to_state: TaskState) -> bool:
    """Return True iff ``from_state -> to_state`` is a legal edge."""
    return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())


def assert_transition(from_state: TaskState, to_state: TaskState) -> None:
    """Raise :class:`InvalidStateTransitionError` if the edge is illegal."""
    if not is_valid_transition(from_state, to_state):
        raise InvalidStateTransitionError(from_state.value, to_state.value)


def next_states(state: TaskState) -> frozenset[TaskState]:
    """Legal successor states of ``state`` (empty for terminal states)."""
    return ALLOWED_TRANSITIONS.get(state, frozenset())


def is_terminal(state: TaskState) -> bool:
    """True for COMPLETED / FAILED / CANCELLED (no outgoing edges)."""
    return state in TERMINAL_STATES
