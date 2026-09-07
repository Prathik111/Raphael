"""Gate 1: state-machine transitions (valid, invalid, terminal)."""

import pytest

from ai_ecosystem.core.errors import InvalidStateTransitionError
from ai_ecosystem.core.models.enums import TaskState
from ai_ecosystem.core.runtime import state_machine as sm


def test_happy_path_is_valid():
    chain = [
        TaskState.CREATED,
        TaskState.UNDERSTANDING,
        TaskState.AWARENESS,
        TaskState.RESEARCHING,
        TaskState.PLANNING,
        TaskState.WAITING_PERMISSION,
        TaskState.EXECUTING,
        TaskState.VERIFYING,
        TaskState.COMPLETED,
    ]
    for frm, to in zip(chain, chain[1:]):
        assert sm.is_valid_transition(frm, to), f"{frm} -> {to}"


def test_recovery_loop_is_valid():
    assert sm.is_valid_transition(TaskState.VERIFYING, TaskState.RECOVERING)
    assert sm.is_valid_transition(TaskState.RECOVERING, TaskState.EXECUTING)
    assert sm.is_valid_transition(TaskState.RECOVERING, TaskState.PLANNING)


def test_terminal_states_have_no_outgoing():
    for state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
        assert sm.next_states(state) == frozenset()
        assert sm.is_terminal(state)


def test_non_terminal_states_can_fail_or_cancel():
    for state in (TaskState.CREATED, TaskState.EXECUTING, TaskState.VERIFYING):
        assert sm.is_valid_transition(state, TaskState.FAILED)
        assert sm.is_valid_transition(state, TaskState.CANCELLED)


def test_invalid_transitions_rejected():
    assert not sm.is_valid_transition(TaskState.CREATED, TaskState.EXECUTING)
    assert not sm.is_valid_transition(TaskState.PLANNING, TaskState.COMPLETED)
    assert not sm.is_valid_transition(TaskState.COMPLETED, TaskState.CREATED)
    assert not sm.is_valid_transition(TaskState.FAILED, TaskState.RECOVERING)


def test_assert_transition_raises_with_details():
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        sm.assert_transition(TaskState.CREATED, TaskState.EXECUTING)
    assert exc_info.value.from_state == "CREATED"
    assert exc_info.value.to_state == "EXECUTING"


def test_assert_transition_passes_on_valid_edge():
    sm.assert_transition(TaskState.CREATED, TaskState.UNDERSTANDING)
