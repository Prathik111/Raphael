"""Production composition root for the local authenticated runtime API."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

from ai_ecosystem.agent.executor import ParallelExecutor
from ai_ecosystem.agent.executor.cancellation import CancellationToken
from ai_ecosystem.agent.orchestrator import AgentConfig, SingleAgent
from ai_ecosystem.agent.planner.backend import ModelReasoningBackend
from ai_ecosystem.agent.verifier import Verifier
from ai_ecosystem.core.config import AppConfig
from ai_ecosystem.core.events.bus import Event
from ai_ecosystem.core.events.sqlite import SqliteEventStore
from ai_ecosystem.core.models.enums import EventType, RiskLevel, TaskState
from ai_ecosystem.core.persistence import SqliteApprovalRepository, SqliteMemoryRepository, SqliteSkillRepository, SqliteVerificationRepository
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.core.secrets import EnvSecretsProvider
from ai_ecosystem.core.models import Tool
from ai_ecosystem.intelligence import HttpChatModelProvider, MockModelProvider, ModelProvider, ModelRouter, ProviderProfile, RoutingRequirements
from ai_ecosystem.intelligence.models.providers import ModelResponse
from ai_ecosystem.interface.api import ApiError, RuntimeAPI
from ai_ecosystem.interface.server import LocalHttpServer
from ai_ecosystem.personalization.memory import MemoryStore
from ai_ecosystem.personalization.personality import PersonalizationEngine, PersonalityStore, PreferenceStore
from ai_ecosystem.security import ApprovalStore, AuthorizationManager, Policy, PolicyEngine, RiskContext
from ai_ecosystem.security.data_policy import DataClass
from ai_ecosystem.system.monitor.manager import SystemAwarenessManager
from ai_ecosystem.system.monitor.probe import LocalSystemProbe
from ai_ecosystem.tools import ToolRegistry, ToolRunner, filesystem_tools, git_tools, respond_tools, terminal_tools
from ai_ecosystem.tools.registry import ToolHandler
from ai_ecosystem.tools.registry.execution_ledger import ExecutionLedger

log = logging.getLogger("ai_ecosystem.serve")

@dataclass
class Stack:
    config: AppConfig
    runtime: AgentRuntime
    api: RuntimeAPI
    server: LocalHttpServer
    agent: SingleAgent | None = None
    providers: list[ModelProvider] = field(default_factory=list)
    model_configured: bool = False
    workspace: str = ""
    auth_token: str | None = None
    dispatcher: BoundedDispatcher | None = None

class BoundedDispatcher:
    """Fixed worker pool with bounded intake and cooperative shutdown."""
    def __init__(self, max_workers: int = 2, max_queued: int = 16) -> None:
        import queue
        self._queue: queue.Queue = queue.Queue(maxsize=max(0, max_queued)); self._stopped = threading.Event()
        self._workers = [threading.Thread(target=self._loop, daemon=True, name=f"agent-worker-{i}") for i in range(max(1, max_workers))]
        for worker in self._workers: worker.start()
    @property
    def depth(self) -> int: return self._queue.qsize()
    def submit(self, task_id: str, fn: Any) -> None:
        import queue
        try: self._queue.put_nowait((task_id, fn))
        except queue.Full: raise ApiError("queue_full", "agent queue is full; try again later") from None
    def _loop(self) -> None:
        import queue
        while not self._stopped.is_set():
            try: _, fn = self._queue.get(timeout=0.2)
            except queue.Empty: continue
            try: fn()
            except Exception as exc: log.error("agent worker failed: %s", exc)
            finally: self._queue.task_done()
    def shutdown(self, timeout_s: float = 10.0) -> None:
        import time
        self._stopped.set(); deadline = time.monotonic() + min(max(timeout_s, 0.0), 60.0)
        for worker in self._workers: worker.join(max(0.0, deadline - time.monotonic()))

def load_or_create_token(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle: token = handle.read().strip()
        if token: return token
    except OSError: pass
    directory = os.path.dirname(os.path.abspath(path)); os.makedirs(directory, exist_ok=True); token = secrets.token_urlsafe(32)
    try: fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path, encoding="utf-8") as handle: existing = handle.read().strip()
        if existing: return existing
        raise RuntimeError("credential file exists but contains no token") from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle: handle.write(token)
    try: os.chmod(path, 0o600)
    except OSError: pass
    return token

def build_router() -> tuple[ModelRouter, list[ModelProvider], bool]:
    from ai_ecosystem.core.errors.exceptions import ModelUnavailableError
    router = ModelRouter(); providers: list[ModelProvider] = []
    http_provider = HttpChatModelProvider.from_secrets(EnvSecretsProvider())
    if http_provider is not None:
        router.register(http_provider, ProviderProfile(provider_id=http_provider.provider_id, local=False, trusted=True, data_classes={DataClass.PUBLIC, DataClass.INTERNAL}, latency_class="standard")); providers.append(http_provider); return router, providers, True
    def missing(_: Any) -> ModelResponse:
        raise ModelUnavailableError("no model configured: set AI_ECO_MODEL_ENDPOINT (plus AI_ECO_MODEL_API_KEY for cloud providers, AI_ECO_MODEL_NAME for the model)")
    stub = MockModelProvider("unconfigured", handler=missing); router.register(stub, ProviderProfile(provider_id="unconfigured", local=True, data_classes=set(DataClass))); return router, [], False

def build_stack(config: AppConfig | None = None, workspace: str = "workspace", auth_token: str | None = None, no_auth: bool = False) -> Stack:
    config = config or AppConfig.from_env(); workspace = os.path.abspath(workspace); os.makedirs(workspace, exist_ok=True)
    runtime = AgentRuntime(config.db_path); bus = runtime.bus
    bus._on_error = lambda report: log.error("event subscriber failed: %s", report)  # noqa: SLF001
    events = SqliteEventStore(runtime.db); events.attach(bus); registry = ToolRegistry()
    raw_allowlist = os.environ.get("AI_ECO_ENV_ALLOWLIST", ""); env_allowlist = frozenset(name.strip() for name in raw_allowlist.split(",") if name.strip())
    bundles: list[tuple[Tool, ToolHandler]] = list(filesystem_tools(workspace)) + list(git_tools(workspace)) + list(respond_tools()) + list(terminal_tools(workspace, env_allowlist or None))
    for tool, handler in bundles: registry.register(tool, handler)
    floor = {"LOW": RiskLevel.LOW, "MEDIUM": RiskLevel.MEDIUM, "HIGH": RiskLevel.HIGH, "CRITICAL": RiskLevel.CRITICAL}.get(config.require_approval.strip().upper())
    approval_store = ApprovalStore(SqliteApprovalRepository(runtime.db))
    authorizer = AuthorizationManager(registry, policy_engine=PolicyEngine(Policy(name="local-operator", auto_grant_up_to=RiskLevel.MEDIUM, deny_critical=False, approval_required_from=floor)), context=RiskContext(agent_id="single-agent", root=workspace), allow_shells=config.allow_shells, approval_store=approval_store)
    execution_ledger = ExecutionLedger(runtime.db)
    execution_ledger.recover_unknowns()
    runner = ToolRunner(registry, authorizer, bus, execution_ledger=execution_ledger); router, providers, model_configured = build_router()
    verifier = Verifier(repository=SqliteVerificationRepository(runtime.db), bus=bus).with_test_command(runner, registry)
    memories = MemoryStore(SqliteMemoryRepository(runtime.db), bus=bus); personalities = PersonalityStore(runtime.db, bus); preferences = PreferenceStore(runtime.db, bus)
    personalization = PersonalizationEngine(personalities, preferences, memories, bus); skills = SqliteSkillRepository(runtime.db).list()
    awareness: SystemAwarenessManager | None = None
    try: awareness = SystemAwarenessManager(LocalSystemProbe(), bus=bus)
    except Exception: log.warning("system awareness unavailable")
    tokens: dict[str, CancellationToken] = {}; tokens_lock = threading.Lock(); dispatcher = BoundedDispatcher(max_workers=config.max_workers, max_queued=config.max_queued_tasks)
    def agent_factory() -> SingleAgent:
        import platform
        provider = router.select(RoutingRequirements()); tools = list(registry.list_tools())
        required = {tool.name: set(tool.input_schema.get("required", [])) for tool in tools}; schemas = {tool.name: {"required": tool.input_schema.get("required", []), "properties": tool.input_schema.get("properties", {})} for tool in tools}; docs = {tool.name: {"description": tool.description, "required": tool.input_schema.get("required", []), "properties": tool.input_schema.get("properties", {})} for tool in tools}
        shell_hint = ("shell interpreters explicitly allowed; prefer direct executables" if config.allow_shells else f"{platform.system()}; shell interpreters blocked by policy; use direct executables and file tools")
        planner = ModelReasoningBackend(provider, tool_arguments=required, tool_schemas=schemas, tool_docs=docs, platform_hint=shell_hint, structured_requester=lambda request, model_cls: router.request_structured(RoutingRequirements(), request, model_cls))
        return SingleAgent(runtime=runtime, router=router, requirements=RoutingRequirements(), registry=registry, runner=runner, planner=planner, verifier=verifier, memories=memories, personalization=personalization, executor_factory=lambda: ParallelExecutor(runner, registry, bus), bus=bus)
    def dispatch(task_id: str) -> None:
        token = CancellationToken()
        with tokens_lock: tokens[task_id] = token
        def work() -> None:
            try: agent_factory().run_task(task_id, AgentConfig(workspace=workspace, project_id="default"), cancel_token=token)
            except Exception as exc: log.error("dispatch failed for %s: %s", task_id, exc)
            finally:
                with tokens_lock: tokens.pop(task_id, None)
        dispatcher.submit(task_id, work)
    def on_cancel(task_id: str) -> None:
        with tokens_lock: token = tokens.get(task_id)
        if token is not None: token.cancel()
    api = RuntimeAPI(runtime, dispatch=dispatch, awareness=awareness, models=providers, skills=skills, event_store=events, on_cancel=on_cancel, approvals=approval_store)
    token = None if no_auth else auth_token; server = LocalHttpServer(api, host=config.api_host, port=config.api_port, auth_token=token); agent = agent_factory()
    for task in runtime.manager._tasks.list():  # noqa: SLF001
        if task.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED): continue
        ctx = runtime.manager.get_context(task.id)
        if ctx is not None and ctx.plan is not None:
            def resume(task_id: str = task.id) -> None:
                try: agent.resume(task_id, AgentConfig(workspace=workspace, project_id="default"))
                except Exception as exc: log.error("recovery failed for %s: %s", task_id, exc)
            try: dispatcher.submit(task.id, resume)
            except ApiError: log.error("could not enqueue recovery for %s", task.id)
        else:
            try:
                runtime.manager.transition(task.id, TaskState.FAILED); bus.publish(Event(event_type=EventType.TASK_FAILED, task_id=task.id, payload={"error": "interrupted before a resumable plan was persisted"}))
            except Exception: log.exception("could not settle interrupted task %s", task.id)
    return Stack(config=config, runtime=runtime, api=api, server=server, agent=agent, providers=providers, model_configured=model_configured, workspace=workspace, auth_token=token, dispatcher=dispatcher)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the AI Ecosystem local runtime API")
    parser.add_argument("--db", default=None); parser.add_argument("--workspace", default="workspace"); parser.add_argument("--host", default=None); parser.add_argument("--port", type=int, default=None); parser.add_argument("--api-token-file", default=None); parser.add_argument("--api-token", default=None); parser.add_argument("--no-auth", action="store_true")
    args = parser.parse_args(argv); logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"); config = AppConfig.from_env()
    if args.db: config.db_path = args.db
    if args.host: config.api_host = args.host
    if args.port: config.api_port = args.port
    if args.no_auth:
        if config.environment != "development": parser.error("--no-auth requires AI_ECO_ENVIRONMENT=development")
        if config.api_host not in ("127.0.0.1", "localhost", "::1"): parser.error("--no-auth refuses non-loopback binds")
    token_file = args.api_token_file or os.path.join(os.path.dirname(os.path.abspath(config.db_path)), "api_credential")
    token = None if args.no_auth else (args.api_token or load_or_create_token(token_file)); stack = build_stack(config, workspace=args.workspace, auth_token=token, no_auth=args.no_auth); stack.server.start()
    log.info("serving %s (db=%s workspace=%s model=%s auth=%s)", stack.server.url, config.db_path, stack.workspace, "configured" if stack.model_configured else "NOT CONFIGURED", "off" if args.no_auth else "on")
    try: threading.Event().wait()
    except KeyboardInterrupt: pass
    finally:
        stack.server.stop()
        if stack.dispatcher is not None: stack.dispatcher.shutdown()
        stack.runtime.shutdown()
    return 0

if __name__ == "__main__": raise SystemExit(main())
