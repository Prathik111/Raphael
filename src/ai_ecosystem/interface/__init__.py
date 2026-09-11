"""Public interface API (desktop boundary; runtime stays authoritative)."""

from ai_ecosystem.interface.api import ApiError, RuntimeAPI
from ai_ecosystem.interface.gateway import (
    EcosystemGateway,
    GatewayError,
    hmac_check,
    hmac_sign,
)
from ai_ecosystem.interface.hardware import (
    HardwareGateway,
    SimulatedDevice,
)
from ai_ecosystem.interface.phone import PhoneClient
from ai_ecosystem.interface.protocol import (
    AuthRef,
    EcosystemEnvelope,
    ProtocolError,
    ProtocolValidator,
    envelope_for,
)
from ai_ecosystem.interface.server import (
    ApiClient,
    ApiUnavailableError,
    LocalHttpServer,
)
from ai_ecosystem.interface.views import (
    AgentView,
    EventAdapter,
    PermissionView,
    StepView,
    TaskView,
    ToolActivity,
)
from ai_ecosystem.interface.voice import (
    MockSTT,
    MockTTS,
    STTProvider,
    TTSProvider,
    VoiceError,
    VoiceSession,
)
from ai_ecosystem.interface.workspace import (
    DataRef,
    NodeLayout,
    NodeState,
    NodeType,
    SqliteWorkspaceRepository,
    Workspace,
    WorkspaceManager,
    WorkspaceNode,
)

__all__ = [
    "AgentView",
    "ApiClient",
    "ApiError",
    "ApiUnavailableError",
    "AuthRef",
    "DataRef",
    "EcosystemEnvelope",
    "EcosystemGateway",
    "EventAdapter",
    "GatewayError",
    "HardwareGateway",
    "LocalHttpServer",
    "MockSTT",
    "MockTTS",
    "NodeLayout",
    "NodeState",
    "NodeType",
    "PermissionView",
    "PhoneClient",
    "ProtocolError",
    "ProtocolValidator",
    "RuntimeAPI",
    "STTProvider",
    "SimulatedDevice",
    "SqliteWorkspaceRepository",
    "StepView",
    "TTSProvider",
    "TaskView",
    "ToolActivity",
    "VoiceError",
    "VoiceSession",
    "Workspace",
    "WorkspaceManager",
    "WorkspaceNode",
    "envelope_for",
    "hmac_check",
    "hmac_sign",
]
