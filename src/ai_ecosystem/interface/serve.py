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
from ai_ecosystem.core.models.enums import RiskLevel
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
from ai_ecosystem.interface.api import RuntimeAPI
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
                workspace: str = "workspace") -> Stack:
    """Compose runtime + tools + policy + agent + API (no sockets yet)."""
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
        context=RiskContext(agent_id="single-agent", root=workspace))
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
        shell_hint = ("Windows (no bash/sh; shell via "
                      "'cmd /c ...' only; prefer file tools)" if system == "Windows"
                      else f"{system} (POSIX shell available)")
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

        threading.Thread(target=work, daemon=True,
                         name=f"agent-{task_id[:8]}").start()

    def on_cancel(task_id: str) -> None:
        with tokens_lock:
            token = tokens.get(task_id)
        if token is not None:
            token.cancel()

    api = RuntimeAPI(runtime, dispatch=dispatch, awareness=awareness,
                     models=providers, skills=skills, event_store=events,
                     on_cancel=on_cancel)
    server = LocalHttpServer(api, host=config.api_host, port=config.api_port)
    return Stack(config=config, runtime=runtime, api=api, server=server,
                 agent=agent_factory(), providers=providers,
                 model_configured=model_configured, workspace=workspace)


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

    stack = build_stack(config, workspace=args.workspace)
    stack.server.start()
    log.info("serving %s (db=%s workspace=%s model=%s)",
             stack.server.url, config.db_path, stack.workspace,
             "configured" if stack.model_configured
             else "NOT CONFIGURED (set AI_ECO_MODEL_ENDPOINT/AI_ECO_MODEL_API_KEY)")
    try:
        threading.Event().wait()  # sleep until Ctrl-C
    except KeyboardInterrupt:
        pass
    finally:
        stack.server.stop()
        stack.runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
