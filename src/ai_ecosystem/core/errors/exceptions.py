"""Domain error hierarchy.

All recoverable and fatal failures in the ecosystem derive from
:class:`AiEcosystemError` so callers can catch the whole family with one
clause while still distinguishing causes. No business logic here.
"""


class AiEcosystemError(Exception):
    """Base class for every error raised by the AI Ecosystem runtime."""


class DomainValidationError(AiEcosystemError):
    """A domain object or transition request failed contract validation."""


class InvalidStateTransitionError(DomainValidationError):
    """An illegal task state-machine transition was requested."""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"invalid transition: {from_state} -> {to_state}")


class ContextSerializationError(AiEcosystemError):
    """An ExecutionContext snapshot could not be serialized or restored."""


class EventBusError(AiEcosystemError):
    """The event bus itself failed (not a subscriber failure)."""


class SubscriberError(AiEcosystemError):
    """A single event subscriber raised; the bus isolates and reports it."""

    def __init__(self, handler_name: str, original: BaseException) -> None:
        self.handler_name = handler_name
        self.original = original
        super().__init__(f"subscriber {handler_name!r} failed: {original}")


class PersistenceError(AiEcosystemError):
    """Base class for repository / storage failures (Gate 3 implements)."""


class ResourceNotFoundError(PersistenceError):
    """A requested record does not exist in the repository."""

    def __init__(self, resource: str, resource_id: str) -> None:
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource} {resource_id!r} not found")


class ModelError(AiEcosystemError):
    """Base class for model-layer failures (Gate 4)."""


class ModelUnavailableError(ModelError):
    """No provider could serve the request (down, unknown, filtered out)."""


class ModelTimeoutError(ModelError):
    """The provider did not answer within the deadline."""


class ModelMalformedError(ModelError):
    """The provider returned output that failed structured validation."""


class NoSuitableModelError(ModelError):
    """The router found no provider satisfying the requirements."""


class ToolError(AiEcosystemError):
    """Base class for tool-layer failures (Gate 5)."""


class ToolExecutionError(ToolError):
    """A tool handler failed during execution."""

    def __init__(self, tool: str, message: str) -> None:
        self.tool = tool
        super().__init__(f"tool {tool!r} failed: {message}")


class ToolTimeoutError(ToolExecutionError):
    """A tool exceeded its deadline."""

    def __init__(self, tool: str, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        super().__init__(tool, f"timed out after {timeout_s}s")


class AuthorizationDeniedError(AiEcosystemError):
    """The policy layer refused a tool call (Gate 6)."""

    def __init__(self, task_id: str, tool: str, reason: str) -> None:
        self.task_id = task_id
        self.tool = tool
        self.reason = reason
        super().__init__(f"denied {tool!r} for task {task_id!r}: {reason}")


class PlanValidationError(DomainValidationError):
    """A plan failed structural validation (Gate 7)."""
