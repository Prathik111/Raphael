"""Gate 1: error hierarchy catches as one family, details preserved."""

import pytest

from ai_ecosystem.core.errors import (
    AiEcosystemError,
    ContextSerializationError,
    DomainValidationError,
    EventBusError,
    InvalidStateTransitionError,
    PersistenceError,
    ResourceNotFoundError,
    SubscriberError,
)


def test_all_errors_catchable_as_base():
    for exc in (
        DomainValidationError("x"),
        InvalidStateTransitionError("A", "B"),
        ContextSerializationError("x"),
        EventBusError("x"),
        SubscriberError("h", ValueError("boom")),
        PersistenceError("x"),
        ResourceNotFoundError("Task", "1"),
    ):
        with pytest.raises(AiEcosystemError):
            raise exc


def test_transition_error_carries_states():
    err = InvalidStateTransitionError("CREATED", "EXECUTING")
    assert "CREATED" in str(err) and "EXECUTING" in str(err)


def test_not_found_error_carries_identity():
    err = ResourceNotFoundError("Task", "abc")
    assert err.resource == "Task" and err.resource_id == "abc"


def test_subscriber_error_wraps_original():
    original = ValueError("boom")
    err = SubscriberError("my_handler", original)
    assert err.handler_name == "my_handler"
    assert err.original is original
