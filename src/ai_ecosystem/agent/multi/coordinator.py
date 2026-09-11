"""Coordinator: fan-out/fan-in and chains over the message bus (Gate 19).

Coordination reuses AgentManager for execution and the Verifier for
child-result checks. The coordinator moves *information*; every agent
still plans, validates, and authorizes its own work.
"""

from __future__ import annotations

from typing import Any

from ai_ecosystem.agent.executor.executor import OverallStatus
from ai_ecosystem.agent.multi.definitions import AgentTask
from ai_ecosystem.agent.multi.manager import AgentManager, SubtaskSpec
from ai_ecosystem.agent.multi.messages import (
    AgentMessage,
    MessageBus,
    MessageType,
)
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.enums import EventType, VerificationStatus


class Coordination:
    """Outcome of one coordinated run (fan-out or chain)."""

    def __init__(self, correlation_id: str,
                 results: dict[str, Any],
                 verified: dict[str, bool]) -> None:
        self.correlation_id = correlation_id
        self.results = dict(results)
        self.verified = dict(verified)

    @property
    def succeeded(self) -> list[str]:
        """Agent ids whose results executed AND verified."""
        return [agent for agent, ok in self.verified.items() if ok]

    @property
    def failed(self) -> list[str]:
        """Agent ids missing either execution or verification."""
        return [agent for agent in self.results if agent not in self.succeeded]


class Coordinator:
    """Supervisor-level fan-out/fan-in and sequential chains."""

    def __init__(
        self,
        manager: AgentManager,
        messages: MessageBus,
        verifier: Any = None,
        bus: EventBus | None = None,
    ) -> None:
        self._manager = manager
        self._messages = messages
        self._verifier = verifier
        self._bus = bus

    def fan_out(self, supervisor_id: str, specs: list[SubtaskSpec],
                correlation_id: str, timeout_s: float = 30.0) -> Coordination:
        """Run subtasks concurrently, request results, aggregate verified.

        The deadline is enforced with a cancellation token: when it
        fires, unstarted work stops and in-flight work drains to its
        natural (honestly recorded) outcome.
        """
        from threading import Timer

        from ai_ecosystem.agent.executor.cancellation import CancellationToken

        self._messages.register(supervisor_id)
        records: list[AgentTask] = []
        for spec in specs:
            record = self._manager.submit(
                spec.agent_id, spec.goal, spec.plan, spec.arguments)
            records.append(record)
            self._messages.send(AgentMessage(
                sender=supervisor_id, recipient=spec.agent_id,
                task_id=record.task_id, message_type=MessageType.TASK_REQUEST,
                payload={"goal": spec.goal}, correlation_id=correlation_id))
            self._emit(EventType.AGENT_TASK_REQUESTED, record.task_id,
                       {"agent_id": spec.agent_id})
        token = CancellationToken()
        timer = Timer(timeout_s, token.cancel) if timeout_s > 0 else None
        try:
            if timer is not None:
                timer.start()
            executions = self._manager.run_all([r.id for r in records], token)
        finally:
            if timer is not None:
                timer.cancel()
        results: dict[str, Any] = {}
        verified: dict[str, bool] = {}
        timed_out = token.cancelled
        for record in records:
            execution = executions.get(record.id)
            ok = execution is not None and execution.status is OverallStatus.COMPLETED
            self._messages.send(AgentMessage(
                sender=record.owner_agent, recipient=supervisor_id,
                task_id=record.task_id, message_type=MessageType.TASK_RESULT,
                payload={"success": ok,
                         "succeeded": execution.succeeded if execution else []},
                correlation_id=correlation_id))
            self._emit(EventType.AGENT_TASK_RESULT, record.task_id,
                       {"agent_id": record.owner_agent, "success": ok})
            results[record.owner_agent] = execution
            verified[record.owner_agent] = ok and self._verify_child(
                record.task_id, execution)
        if timed_out:
            self._emit(EventType.AGENT_TASK_RESULT, "",
                       {"supervisor": supervisor_id,
                        "detail": "fan-out deadline fired; partial results"})
        return Coordination(correlation_id, results, verified)

    def chain(self, supervisor_id: str, steps: list[SubtaskSpec],
              correlation_id: str) -> Coordination:
        """Sequential handoffs: each agent's result feeds the next."""
        self._messages.register(supervisor_id)
        results: dict[str, Any] = {}
        verified: dict[str, bool] = {}
        previous_summary = ""
        for spec in steps:
            record = self._manager.submit(
                spec.agent_id, spec.goal, spec.plan, spec.arguments)
            if previous_summary:
                self._messages.handoff(
                    supervisor_id, spec.agent_id, record.task_id,
                    previous_summary, correlation_id)
            execution = self._manager.run_task(record.id)
            ok = execution.status is OverallStatus.COMPLETED
            results[spec.agent_id] = execution
            verified[spec.agent_id] = ok and self._verify_child(
                record.task_id, execution)
            previous_summary = (
                f"{spec.agent_id}: {len(execution.succeeded)} steps succeeded")
            if not ok:
                break
        return Coordination(correlation_id, results, verified)

    def _verify_child(self, task_id: str, execution: Any) -> bool:
        """Child results pass through the Verifier before aggregation."""
        if self._verifier is None:
            return True
        verdict = self._verifier.verify(
            task_id, "coordinator-review", execution.all_tool_results(),
            [{"strategy": "command_results"}])
        return verdict.status is VerificationStatus.PASSED

    def _emit(self, event_type: EventType, task_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(event_type=event_type, task_id=task_id, payload=payload))
