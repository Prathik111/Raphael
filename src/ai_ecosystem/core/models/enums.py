"""Shared enumerations for the domain models.

Single source of truth for every status / level / type string in the
system so later gates (planner, executor, policy) cannot drift into
incompatible vocabularies.
"""

from enum import Enum


class TaskState(str, Enum):
    """PACE lifecycle states. Terminal: COMPLETED, FAILED, CANCELLED."""

    CREATED = "CREATED"
    UNDERSTANDING = "UNDERSTANDING"
    AWARENESS = "AWARENESS"
    RESEARCHING = "RESEARCHING"
    PLANNING = "PLANNING"
    WAITING_PERMISSION = "WAITING_PERMISSION"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RiskLevel(str, Enum):
    """Risk classification used by the future RiskEngine (Gate 6)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ToolCallStatus(str, Enum):
    """Tool lifecycle states (Gate 5 lifecycle, declared here as contract)."""

    REQUESTED = "REQUESTED"
    VALIDATED = "VALIDATED"
    RISK_CHECK = "RISK_CHECK"
    PERMISSION_CHECK = "PERMISSION_CHECK"
    EXECUTE = "EXECUTE"
    OBSERVE = "OBSERVE"
    VERIFY = "VERIFY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DENIED = "DENIED"


class PermissionDecision(str, Enum):
    """Outcome of a permission evaluation."""

    PENDING = "PENDING"
    GRANTED = "GRANTED"
    DENIED = "DENIED"


class VerificationStatus(str, Enum):
    """Outcome of a verification (Gate 9 implements the engine)."""

    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"


class StepState(str, Enum):
    """Runtime execution states for one DAG node (Gate 8)."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"
    TIMED_OUT = "TIMED_OUT"


class MemoryType(str, Enum):
    """Memory categories (Gate 12 implements the pipeline)."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROJECT = "project"
    PREFERENCE = "preference"
    SKILL = "skill"
    EXPERIENCE = "experience"


class SkillType(str, Enum):
    """Where a skill came from (Gate 17)."""

    BUILTIN = "BUILTIN"
    USER_DEFINED = "USER_DEFINED"
    LEARNED_PROPOSAL = "LEARNED_PROPOSAL"
    IMPORTED = "IMPORTED"


class SkillStatus(str, Enum):
    """Lifecycle of a skill version (Gate 17)."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ARCHIVED = "ARCHIVED"


class MemoryScope(str, Enum):
    """Isolation boundaries for memory (Gate 12)."""

    GLOBAL = "global"
    PROJECT = "project"
    TASK = "task"
    AGENT = "agent"


class MemoryStatus(str, Enum):
    """Lifecycle states for a memory record (Gate 12)."""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


class EventType(str, Enum):
    """Canonical event names (Gate 2). Producers must use these."""

    TASK_CREATED = "TaskCreated"
    PLAN_CREATED = "PlanCreated"
    AGENT_STARTED = "AgentStarted"
    AGENT_COMPLETED = "AgentCompleted"
    TOOL_REQUESTED = "ToolRequested"
    TOOL_STARTED = "ToolStarted"
    TOOL_COMPLETED = "ToolCompleted"
    TOOL_FAILED = "ToolFailed"
    PERMISSION_REQUESTED = "PermissionRequested"
    PERMISSION_GRANTED = "PermissionGranted"
    PERMISSION_DENIED = "PermissionDenied"
    VERIFICATION_STARTED = "VerificationStarted"
    VERIFICATION_PASSED = "VerificationPassed"
    VERIFICATION_FAILED = "VerificationFailed"
    VERIFICATION_INCONCLUSIVE = "VerificationInconclusive"
    VERIFICATION_ERROR = "VerificationError"
    RECOVERY_DECIDED = "RecoveryDecided"
    RECOVERY_EXHAUSTED = "RecoveryExhausted"
    RESEARCH_STARTED = "ResearchStarted"
    SOURCE_COLLECTED = "SourceCollected"
    SOURCE_REJECTED = "SourceRejected"
    EVIDENCE_EXTRACTED = "EvidenceExtracted"
    RESEARCH_COMPLETED = "ResearchCompleted"
    RESEARCH_FAILED = "ResearchFailed"
    MEMORY_CREATED = "MemoryCreated"
    SYSTEM_AWARENESS_REQUESTED = "SystemAwarenessRequested"
    SYSTEM_SNAPSHOT_CREATED = "SystemSnapshotCreated"
    CAPABILITY_DETECTED = "CapabilityDetected"
    RESOURCE_PRESSURE_DETECTED = "ResourcePressureDetected"
    LEARNING_PROPOSAL_CREATED = "LearningProposalCreated"
    PROPOSAL_ADOPTED = "ProposalAdopted"
    PROPOSAL_REJECTED = "ProposalRejected"
    SKILL_SELECTED = "SkillSelected"
    SKILL_INVOKED = "SkillInvoked"
    SKILL_COMPLETED = "SkillCompleted"
    SKILL_FAILED = "SkillFailed"
    SKILL_DISABLED = "SkillDisabled"
    SKILL_PROPOSAL_CREATED = "SkillProposalCreated"
    AGENT_REGISTERED = "AgentRegistered"
    AGENT_MESSAGE_SENT = "AgentMessageSent"
    AGENT_MESSAGE_RECEIVED = "AgentMessageReceived"
    AGENT_MESSAGE_REJECTED = "AgentMessageRejected"
    AGENT_HANDOFF = "AgentHandoff"
    AGENT_TASK_REQUESTED = "AgentTaskRequested"
    AGENT_TASK_RESULT = "AgentTaskResult"
    SYNC_STARTED = "SyncStarted"
    SYNC_OBJECT_UPLOADED = "SyncObjectUploaded"
    SYNC_OBJECT_DOWNLOADED = "SyncObjectDownloaded"
    SYNC_CONFLICT = "SyncConflict"
    SYNC_SKIPPED = "SyncSkipped"
    SYNC_FAILED = "SyncFailed"
    SYNC_COMPLETED = "SyncCompleted"
    PC_ONLINE = "PCOnline"
    PC_OFFLINE = "PCOffline"
    PC_EXPIRED = "PCExpired"
    WORKSPACE_UPDATED = "WorkspaceUpdated"
    PROACTIVE_TRIGGERED = "ProactiveTriggered"
    PROACTIVE_EXECUTED = "ProactiveExecuted"
    DEVICE_PAIRED = "DevicePaired"
    DEVICE_REVOKED = "DeviceRevoked"
    PHONE_DECISION = "PhoneDecision"
    MEMORY_CANDIDATE_CREATED = "MemoryCandidateCreated"
    MEMORY_UPDATED = "MemoryUpdated"
    MEMORY_ARCHIVED = "MemoryArchived"
    MEMORY_DELETED = "MemoryDeleted"
    MEMORY_RECALLED = "MemoryRecalled"
    PERSONALITY_UPDATED = "PersonalityUpdated"
    PREFERENCE_UPDATED = "PreferenceUpdated"
    PERSONALIZATION_APPLIED = "PersonalizationApplied"
    UNDERSTANDING_COMPLETED = "UnderstandingCompleted"
    AWARENESS_COMPLETED = "AwarenessCompleted"
    RECOVERY_STARTED = "RecoveryStarted"
    TASK_COMPLETED = "TaskCompleted"
    TASK_FAILED = "TaskFailed"
    SKILL_CREATED = "SkillCreated"
    SKILL_UPDATED = "SkillUpdated"
    GRAPH_STARTED = "GraphStarted"
    GRAPH_COMPLETED = "GraphCompleted"
    STEP_READY = "StepReady"
    STEP_STARTED = "StepStarted"
    STEP_COMPLETED = "StepCompleted"
    STEP_FAILED = "StepFailed"
    STEP_TIMED_OUT = "StepTimedOut"
    STEP_CANCELLED = "StepCancelled"
    STEP_SKIPPED = "StepSkipped"


class NodeType(str, Enum):
    """Ecosystem node kinds (Gate 35 standardizes the protocol)."""

    PC = "PC"
    OCI = "OCI"
    PHONE = "PHONE"
    HARDWARE = "HARDWARE"
    COMPUTE = "COMPUTE"
