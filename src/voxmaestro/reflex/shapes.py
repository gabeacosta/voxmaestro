"""Typed reflex contracts.

The reflex is a sensor, not a router. A GateDecision may inform deterministic
runtime policy, but it never owns state transitions, tool authority, or effects.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Optional


SCHEMA_VERSION = "reflex.v1"


class ReflexIntent(str, Enum):
    SCHEDULE = "schedule"
    FAQ = "faq"
    PRICING = "pricing"
    COMPLAINT = "complaint"
    DISCLOSURE_TRIGGER = "disclosure-trigger"
    OFF_SCRIPT = "off-script"


class Language(str, Enum):
    EN = "en"
    ES = "es"


@dataclass(frozen=True)
class BackendDecision:
    """Shape-constrained proposal returned by one local reflex backend."""

    intent: ReflexIntent
    tool_needed_probability: float
    language: Language
    model_id: str
    model_hash: str

    def __post_init__(self) -> None:
        probability = self.tool_needed_probability
        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            raise ValueError("tool_needed_probability must be numeric")
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError("tool_needed_probability must be finite and in [0, 1]")
        if not self.model_id.strip():
            raise ValueError("model_id is required")
        if not self.model_hash.strip():
            raise ValueError("model_hash is required")


@dataclass(frozen=True)
class GateDecision:
    """Observed reflex result. Missing classifications mean fail-neutral fallback."""

    status: str
    latency_ms: float
    backend_id: str
    input_digest: str
    intent: Optional[ReflexIntent] = None
    tool_needed_probability: Optional[float] = None
    language: Optional[Language] = None
    model_id: Optional[str] = None
    model_hash: Optional[str] = None
    fallback_reason: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in {"ok", "fallback"}:
            raise ValueError("status must be 'ok' or 'fallback'")
        if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError("latency_ms must be finite and >= 0")
        if not self.backend_id.strip():
            raise ValueError("backend_id is required")
        if not self.input_digest.strip():
            raise ValueError("input_digest is required")
        if self.status == "ok":
            if self.intent is None or self.language is None:
                raise ValueError("ok decisions require intent and language")
            probability = self.tool_needed_probability
            if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                raise ValueError("ok decisions require numeric tool probability")
            if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
                raise ValueError("tool probability must be finite and in [0, 1]")
            if not self.model_id or not self.model_hash:
                raise ValueError("ok decisions require model identity")
            if self.fallback_reason is not None:
                raise ValueError("ok decisions cannot carry fallback_reason")
        else:
            if not self.fallback_reason:
                raise ValueError("fallback decisions require fallback_reason")
            if (
                self.intent is not None
                or self.tool_needed_probability is not None
                or self.language is not None
            ):
                raise ValueError("fallback decisions cannot carry a classification")

    @property
    def usable(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.intent is not None:
            data["intent"] = self.intent.value
        if self.language is not None:
            data["language"] = self.language.value
        return data

    def telemetry(self) -> dict[str, Any]:
        """Return transcript-free observability tags."""

        return {
            "reflex.schema_version": self.schema_version,
            "reflex.status": self.status,
            "reflex.backend_id": self.backend_id,
            "reflex.input_digest": self.input_digest,
            "reflex.intent": self.intent.value if self.intent else None,
            "reflex.tool_needed_probability": self.tool_needed_probability,
            "reflex.language": self.language.value if self.language else None,
            "reflex.model_id": self.model_id,
            "reflex.model_hash": self.model_hash,
            "reflex.fallback_reason": self.fallback_reason,
        }
