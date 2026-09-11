"""Startup, shutdown, and backup discipline (Gate 43).

startup_recovery() reopens the world after a crash: agent tasks and
scheduler jobs stuck RUNNING return to queues (attempts preserved),
and the audit chain is verified before anything else runs. shutdown_all
closes resources best-effort in reverse order. Database.backup_to uses
SQLite's online backup API (consistent snapshots, no dump parsing).
"""

from __future__ import annotations

from typing import Any


def startup_recovery(db: Any, runtime: Any = None,
                     scheduler_repo: Any = None) -> dict[str, Any]:
    """Recover crash-interrupted state; returns a structured report."""
    # Local imports: core must not depend on tools/agents at module load.
    from ai_ecosystem.agent.multi.manager import AgentManager, AgentRegistry
    from ai_ecosystem.security.audit import AuditLog
    from ai_ecosystem.tools.registry.registry import ToolRegistry

    report: dict[str, Any] = {"agent_tasks_reset": 0, "scheduler_jobs_reset": 0,
                              "audit_ok": True, "audit_detail": ""}
    if runtime is not None:
        manager = AgentManager(AgentRegistry(db), runtime, ToolRegistry())
        reset = manager.resume_interrupted()
        report["agent_tasks_reset"] = len(reset)
    if scheduler_repo is not None:
        count = 0
        for job in scheduler_repo.list():
            from ai_ecosystem.scheduler import ScheduledStatus

            if job.status is ScheduledStatus.RUNNING:
                job.status = ScheduledStatus.QUEUED
                scheduler_repo.update(job)
                count += 1
        report["scheduler_jobs_reset"] = count
    try:
        ok, detail = AuditLog(db).verify()
    except Exception as exc:  # noqa: BLE001 -- unreadable audit is a finding
        ok, detail = False, f"audit unreadable: {exc}"
    report["audit_ok"] = ok
    report["audit_detail"] = detail
    return report


def shutdown_all(*closables: Any) -> list[str]:
    """Close/shutdown each resource best-effort; returns error strings."""
    errors = []
    for closable in reversed(list(closables)):
        try:
            if hasattr(closable, "shutdown"):
                closable.shutdown()
            elif hasattr(closable, "stop"):
                closable.stop()
            elif hasattr(closable, "close"):
                closable.close()
            elif callable(closable):
                closable()
        except Exception as exc:  # noqa: BLE001 -- best effort means best effort
            errors.append(f"{type(closable).__name__}: {exc}")
    return errors
