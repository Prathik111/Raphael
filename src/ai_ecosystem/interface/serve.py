"""Production composition root: serve the local runtime API (Gate 20+).

This is the process the desktop app talks to. It wires the real
subsystems together on loopback and blocks until stopped::

    python -m ai_ecosystem.interface.serve
    ai-ecosystem-serve --db data/runtime.db --workspace workspace

Behavior contract:

* Binds 127.0.0.1:8765 by default (AppConfig; ``AI_ECO_*`` overrides).
  Production refuses non-loopback binds (the API has no auth layer).
* Every submitted task is dispatched to a background worker thread
  running :class:`SingleAgent` against the *same* task id the UI polls.
* Model: OpenAI-compatible HTTP provider when ``AI_ECO_MODEL_ENDPOINT``
  + ``AI_ECO_MODEL_API_KEY`` are set; otherwise tasks are accepted and
  stay CREATED with an explicit "model not configured" status so the
  UI can say so instead of failing silently.
* Policy: local-operator default (auto-grant up to HIGH, CRITICAL
  always denied). The operator runs this against their own workspace;
  the model-output containment boundary stays enforced.
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from ai_ecosystem.agent.executor import ParallelExecutor
from ai_ecosystem.agent.orchestrator import AgentConfig, SingleAgent
from ai_ecosystem.agent.planner.backend import ModelReasoningBackend
from ai_ecosystem.agent.verifier import Verifier
from ai_ecosystem.core.config import AppConfig
from ai_ecosystem.core.events import InMemoryEventStore
from ai_ecosystem.core.events.bus import Event
from ai_ecosystem.core.models.enums import EventType, RiskLevel, TaskState
from ai_ecosystem.core.persistence import (
    Database,
    SqliteMemoryRepository,
    SqliteSkillRepository,
    SqliteVerificationRepository,
)
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.core.secrets import EnvSecretsProvider
from ai_ecosystem.intelligence import (
    HttpChatModelProvider,
    MockModelProvider,
    ModelProvider,
    ModelRouter,
    ProviderProfile,
    RoutingRequirements,
)
from ai_ecosystem.intelligence.models.providers import ModelResponse
from ai_ecosystem.interface.api import ApiError, RuntimeAPI
from ai_ecosystem.interface.server import LocalHttpServer
from ai_ecosystem.personalization.memory import MemoryStore
from ai_ecosystem.personalization.personality import (
    PersonalizationEngine,
    PersonalityStore,
    PreferenceStore,
)
from ai_ecosystem.security import (
    AuthorizationManager,
    Policy,
    PolicyEngine,
    RiskContext,
)
from ai_ecosystem.system.monitor.manager import SystemAwarenessManager
from ai_ecosystem.system.monitor.probe import LocalSystemProbe
from ai_ecosystem.tools import (
    ToolRegistry,
    ToolRunner,
    filesystem_tools,
    git_tools,
    respond_tools,
    terminal_tools,
)

log = logging.getLogger("ai_ecosystem.serve")


@dataclass
class Stack:
    """Everything one server process owns."""

    config: AppConfig
    runtime: AgentRuntime
    api: RuntimeAPI
    server: LocalHttpServer
    agent: Optional[SingleAgent] = None
    providers: list[ModelProvider] = field(default_factory=list)
    model_configured: bool = False
    workspace: str = ""
    auth_token: Optional[str] = None
    dispatcher: Optional["BoundedDispatcher"] = None


class BoundedDispatcher:
    """Fixed worker pool over a bounded queue (review fix 05/07).

    Unbounded per-task threads let one client exhaust the machine;
    here at most ``max_workers`` tasks run and ``max_queued`` wait.
    Overflow raises ``ApiError("queue_full")`` (HTTP 429) instead of
    silently degrading. Workers are daemon threads; shutdown() stops
    intake and joins with a timeout (in-flight tasks keep their
    cooperative cancel semantics).
    """

    def __init__(self, max_workers: int = 2, max_queued: int = 16) -> None:
        import queue as _queue

        self._max_workers = max(1, max_workers)
        self._max_queued = max(0, max_queued)
        self._queue: _queue.Queue = _queue.Queue(maxsize=self._max_queued)
        self._stopped = threading.Event()
        self._workers = [
            threading.Thread(target=self._loop, daemon=True,
                             name=f"agent-worker-{index}")
            for index in range(self._max_workers)]
        for worker in self._workers:
            worker.start()

    @property
    def depth(self) -> int:
        """Queued (not yet running) items."""
        return self._queue.qsize()

    def submit(self, task_id: str, fn: Any) -> None:
        """Enqueue one task; raise ApiError("queue_full") when full."""
        import queue as _queue

        try:
            self._queue.put_nowait((task_id, fn))
        except _queue.Full:
            raise ApiError(
                "queue_full",
                f"task queue full ({self._max_queued} waiting, "
                f"{self._max_workers} running); try again later") from None

    def _loop(self) -> None:
        import queue as _queue

        while not self._stopped.is_set():
            try:
                _, fn = self._queue.get(timeout=0.2)
            except _queue.Empty:
                continue
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 -- worker never dies
                log.error("worker failed: %s", exc)
            finally:
                self._queue.task_done()

    def shutdown(self, timeout_s: float = 10.0) -> None:
        """Stop intake; running tasks finish cooperatively (bounded wait)."""
        import time as _time

        self._stopped.set()
        deadline = _time.monotonic() + min(max(timeout_s, 0.0), 60.0)
        for worker in self._workers:
            worker.join(timeout=max(0.0, deadline - _time.monotonic()))


def load_or_create_token(path: str) -> str:
    """Per-install API token: read it, or generate + store it.

    The file holds one line (no newline needed). Permissions are
    restricted best-effort (POSIX 0o600; Windows ACLs cannot express
    that from here, and the threat model notes it). The token value
    is never logged.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
        if token:
            return token
    except OSError:
        pass
    import secrets as _secrets

    token = _secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(token)
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
    try:
        os.chmod(path, 0o600)
    except OSError:  # noqa: BLE001 -- best effort on Windows
        pass
    return token


def build_router() -> tuple[ModelRouter, list[ModelProvider], bool]:
    """Router with the HTTP provider when configured, else a stub.

    The stub provider refuses with a clear error naming the missing
    variables, so a misconfigured server fails loudly at dispatch
    time instead of hanging a task.
    """
    from ai_ecosystem.core.errors.exceptions import ModelUnavailableError

    router = ModelRouter()
    providers: list[ModelProvider] = []
    http_provider = HttpChatModelProvider.from_secrets(EnvSecretsProvider())
    if http_provider is not None:
        router.register(
            http_provider,
            ProviderProfile(provider_id=http_provider.provider_id,
                            local=False, latency_class="standard"))
        providers.append(http_provider)
        return router, providers, True

    def _missing(request: Any) -> ModelResponse:
        raise ModelUnavailableError(
            "no model configured: set AI_ECO_MODEL_ENDPOINT and "
            "AI_ECO_MODEL_API_KEY (plus AI_ECO_MODEL_NAME)")

    stub = MockModelProvider("unconfigured", handler=_missing)
    router.register(stub, ProviderProfile(provider_id="unconfigured"))
    return router, [], False


def build_stack(config: Optional[AppConfig] = None,
                workspace: str = "workspace",
                auth_token: Optional[str] = None,
                no_auth: bool = False) -> Stack:
    """Compose runtime + tools + policy + agent + API (no sockets yet).

    Authentication is on by default: pass ``auth_token`` explicitly or
    let the launcher manage the per-install token file. ``no_auth``
    exists for tests only.
    """
    from ai_ecosystem.agent.executor.cancellation import CancellationToken

    config = config or AppConfig.from_env()
    # Resolve once: a relative jail root would silently move with CWD.
    workspace = os.path.abspath(workspace)
    os.makedirs(workspace, exist_ok=True)

    runtime = AgentRuntime(config.db_path)
    bus = runtime.bus
    # Surface subscriber failures in server logs (default is silent drop).
    # Assigned post-construction: AgentRuntime owns the bus wiring.
    bus._on_error = lambda report: log.error(  # noqa: SLF001 -- composition root
        "event subscriber failed: %s", report)
    events = InMemoryEventStore()
    events.attach(bus)

    registry = ToolRegistry()
    for tool, handler in (
            list(filesystem_tools(workspace))
            + list(git_tools(workspace))
            + list(respond_tools())
            + list(terminal_tools(workspace))):
        registry.register(tool, handler)
    authorizer = AuthorizationManager(
        registry,
        policy_engine=PolicyEngine(Policy(
            name="local-operator", auto_grant_up_to=RiskLevel.HIGH,
            deny_critical=True)),
        context=RiskContext(agent_id="single-agent", root=workspace),
        allow_shells=config.allow_shells)
    runner = ToolRunner(registry, authorizer, bus)

    router, providers, model_configured = build_router()
    verifier = Verifier(
        repository=SqliteVerificationRepository(runtime.db),
        bus=bus).with_test_command(runner, registry)
    memories = MemoryStore(SqliteMemoryRepository(runtime.db), bus=bus)
    personalities = PersonalityStore(runtime.db, bus)
    preferences = PreferenceStore(runtime.db, bus)
    personalization = PersonalizationEngine(
        personalities, preferences, memories, bus)
    skills = SqliteSkillRepository(runtime.db).list()
    awareness: Optional[SystemAwarenessManager] = None
    try:
        awareness = SystemAwarenessManager(LocalSystemProbe(), bus=bus)
    except Exception:  # noqa: BLE001 -- awareness is optional, never fatal
        log.warning("system awareness unavailable; continuing without it")

    tokens: dict[str, CancellationToken] = {}
    tokens_lock = threading.Lock()
    dispatcher = BoundedDispatcher(max_workers=config.max_workers,
                                   max_queued=config.max_queued_tasks)

    # Crash recovery (review fix 09/39): tasks left non-terminal by a
    # previous process (killed mid-run, queued but never started) can
    # never resume -- workers are gone. Settle them FAILED with an
    # honest reason instead of leaving them stuck forever.
    try:
        for task in runtime.manager._tasks.list():
            if task.state not in (TaskState.COMPLETED, TaskState.FAILED,
                                  TaskState.CANCELLED):
                try:
                    runtime.manager.transition(task.id, TaskState.FAILED)
                except Exception:  # noqa: BLE001 -- best effort per task
                    continue
                bus.publish(Event(
                    event_type=EventType.TASK_FAILED, task_id=task.id,
                    payload={"error": "interrupted by shutdown/restart; "
                                      "resubmit the goal to retry"}))
    except Exception:  # noqa: BLE001 -- recovery never blocks startup
        pass

    def agent_factory() -> SingleAgent:
        """One agent per dispatched task (run config is per-call state)."""
        import platform as _platform

        provider = router.select(RoutingRequirements())
        tools = list(registry.list_tools())
        required = {tool.name: set(tool.input_schema.get("required", []))
                    for tool in tools}
        schemas = {tool.name: {"required": tool.input_schema.get("required", []),
                               "properties": tool.input_schema.get("properties", {})}
                   for tool in tools}
        docs = {tool.name: {"description": tool.description,
                            "required": tool.input_schema.get("required", []),
                            "properties": tool.input_schema.get("properties", {})}
                for tool in tools}
        system = _platform.system()
        if config.allow_shells:
            shell_hint = ("shell interpreters explicitly allowed; prefer "
                          "direct executables and file tools")
        elif system == "Windows":
            shell_hint = ("Windows, shell interpreters BLOCKED by operator "
                          "policy (no cmd/bash/powershell): use direct "
                          "executables and file tools only")
        else:
            shell_hint = (f"{system}, shell interpreters BLOCKED by "
                          "operator policy: use direct executables and "
                          "file tools only")
        return SingleAgent(
            runtime=runtime, router=router,
            requirements=RoutingRequirements(), registry=registry,
            runner=runner,
            planner=ModelReasoningBackend(
                provider, tool_arguments=required, tool_schemas=schemas,
                tool_docs=docs,
                platform_hint=f"{system}; {shell_hint}"),
            verifier=verifier, memories=memories,
            personalization=personalization,
            executor_factory=lambda: ParallelExecutor(runner, registry, bus),
            bus=bus)

    def dispatch(task_id: str) -> None:
        # NOTE: the task row already exists when this runs (created by
        # RuntimeAPI first). On queue_full the row stays CREATED with no
        # worker; the operator sees the 429 and can cancel/retry it.
        # Deleting the row instead would hide work the client was told
        # about, which is worse than a visible idle task.
        token = CancellationToken()
        with tokens_lock:
            tokens[task_id] = token

        def work() -> None:
            try:
                agent_factory().run_task(
                    task_id, AgentConfig(workspace=workspace),
                    cancel_token=token)
            except Exception as exc:  # noqa: BLE001 -- agent terminates tasks
                log.error("dispatch for task %s failed: %s", task_id, exc)
            finally:
                with tokens_lock:
                    tokens.pop(task_id, None)

        dispatcher.submit(task_id, work)

    def on_cancel(task_id: str) -> None:
        with tokens_lock:
            token = tokens.get(task_id)
        if token is not None:
            token.cancel()

    api = RuntimeAPI(runtime, dispatch=dispatch, awareness=awareness,
                     models=providers, skills=skills, event_store=events,
                     on_cancel=on_cancel)
    token = None if no_auth else auth_token
    server = LocalHttpServer(api, host=config.api_host, port=config.api_port,
                             auth_token=token)
    return Stack(config=config, runtime=runtime, api=api, server=server,
                 agent=agent_factory(), providers=providers,
                 model_configured=model_configured, workspace=workspace,
                 auth_token=token, dispatcher=dispatcher)


def main(argv: Optional[list[str]] = None) -> int:
    """Serve forever; Ctrl-C shuts down cleanly."""
    parser = argparse.ArgumentParser(
        description="Serve the AI Ecosystem local runtime API")
    parser.add_argument("--db", default=None,
                        help="SQLite path (default: AI_ECO_DB_PATH)")
    parser.add_argument("--workspace", default="workspace",
                        help="Agent workspace root (tools are jailed here)")
    parser.add_argument("--host", default=None, help="Bind host (loopback)")
    parser.add_argument("--port", type=int, default=None, help="Bind port")
    parser.add_argument("--api-token-file", default=None,
                        help="Path to the per-install bearer token file "
                             "(default: <db dir>/api_token)")
    parser.add_argument("--api-token", default=None,
                        help="Bearer token value (overrides the file; "
                             "prefer the file)")
    parser.add_argument("--no-auth", action="store_true",
                        help="Disable API authentication (tests only; "
                             "never use with a real agent)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = AppConfig.from_env()
    if args.db:
        config.db_path = args.db
    if args.host:
        config.api_host = args.host
    if args.port:
        config.api_port = args.port

    token: Optional[str] = None
    token_file = args.api_token_file or os.path.join(
        os.path.dirname(os.path.abspath(config.db_path)), "api_credential")
    if not args.no_auth:
        # One-time rename from the pre-remediation filename: never leave
        # a stale credential file behind.
        legacy = os.path.join(os.path.dirname(token_file), "api_token")
        if not os.path.exists(token_file) and os.path.exists(legacy):
            try:
                os.rename(legacy, token_file)
            except OSError:
                pass
        token = args.api_token or load_or_create_token(token_file)

    stack = build_stack(config, workspace=args.workspace,
                        auth_token=token, no_auth=args.no_auth)
    stack.server.start()
    log.info("serving %s (db=%s workspace=%s model=%s auth=%s)",
             stack.server.url, config.db_path, stack.workspace,
             "configured" if stack.model_configured
             else "NOT CONFIGURED (set AI_ECO_MODEL_ENDPOINT/AI_ECO_MODEL_API_KEY)",
             "off (INSECURE)" if args.no_auth else f"on ({token_file})")
    try:
        threading.Event().wait()  # sleep until Ctrl-C
    except KeyboardInterrupt:
        pass
    finally:
        stack.server.stop()
        if stack.dispatcher is not None:
            stack.dispatcher.shutdown()
        stack.runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
