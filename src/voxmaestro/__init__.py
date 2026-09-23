"""VoxMaestro — The open source voice agent conductor."""

from .conductor import (
    CallPhase,
    ConversationContext,
    HandoffProtocol,
    SchemaLoader,
    StateMachine,
    ToolBridge,
    ToolCallResult,
    TransitionResult,
    VoxMaestro,
)
from .runtime import (
    CallSession,
    RuntimeConfigurationError,
    RuntimeToolResult,
    VoxMaestroRuntime,
)
from .outcome import (
    OutcomeAttestation,
    OutcomeGate,
    OutcomeStatus,
    OutcomeVerificationError,
    OutcomeVerificationRequest,
    RequirementFinding,
    TaskContract,
    TaskRequirement,
)

__version__ = "0.1.0"
__all__ = [
    "VoxMaestro",
    "VoxMaestroRuntime",
    "CallSession",
    "ConversationContext",
    "SchemaLoader",
    "StateMachine",
    "ToolBridge",
    "HandoffProtocol",
    "RuntimeConfigurationError",
    "RuntimeToolResult",
    "CallPhase",
    "TransitionResult",
    "ToolCallResult",
    "OutcomeAttestation",
    "OutcomeGate",
    "OutcomeStatus",
    "OutcomeVerificationError",
    "OutcomeVerificationRequest",
    "RequirementFinding",
    "TaskContract",
    "TaskRequirement",
]
