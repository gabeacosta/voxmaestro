"""Deadline-bounded reflex gate.

Any backend failure produces an explicit fallback observation. The caller keeps
using its pre-existing deterministic/runtime path.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time

from .backends import ReflexBackend
from .shapes import GateDecision


def _consume_task(task: asyncio.Task) -> None:
    """Consume a detached task result without hiding caller cancellation."""
    if task.cancelled():
        return
    try:
        task.exception()
    except BaseException:
        pass


class ReflexGate:
    """Observe a turn with a local classifier without gaining routing authority."""

    def __init__(self, backend: ReflexBackend, *, timeout_ms: float = 150.0) -> None:
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, (int, float))
            or not math.isfinite(timeout_ms)
            or not 0 < timeout_ms <= 150
        ):
            raise ValueError("timeout_ms must be finite and in (0, 150]")
        self.backend = backend
        self.timeout_ms = float(timeout_ms)

    async def classify(self, text: str) -> GateDecision:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        started = time.monotonic()
        task = asyncio.create_task(self.backend.classify(text))
        try:
            done, _ = await asyncio.wait(
                {task},
                timeout=self.timeout_ms / 1000,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except Exception:
            task.cancel()
            task.add_done_callback(_consume_task)
            return self._fallback(started, digest, "backend_error")

        if task not in done:
            task.cancel()
            task.add_done_callback(_consume_task)
            return self._fallback(started, digest, "timeout")

        try:
            proposal = task.result()
        except asyncio.CancelledError:
            return self._fallback(started, digest, "backend_error")
        except Exception:
            return self._fallback(started, digest, "backend_error")

        latency_ms = (time.monotonic() - started) * 1000
        return GateDecision(
            status="ok",
            latency_ms=latency_ms,
            backend_id=self.backend.backend_id,
            input_digest=digest,
            intent=proposal.intent,
            tool_needed_probability=proposal.tool_needed_probability,
            language=proposal.language,
            model_id=proposal.model_id,
            model_hash=proposal.model_hash,
        )

    def _fallback(self, started: float, digest: str, reason: str) -> GateDecision:
        return GateDecision(
            status="fallback",
            latency_ms=(time.monotonic() - started) * 1000,
            backend_id=self.backend.backend_id,
            input_digest=digest,
            model_id=getattr(self.backend, "model_id", None),
            model_hash=getattr(self.backend, "model_hash", None),
            fallback_reason=reason,
        )
