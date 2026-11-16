"""Observable startup lifecycle for the local voice runtime.

States: STARTING → READY | DEGRADED | FAILED.

Readiness is independent of whether an HTTP UI port is bound: a process can be
READY for WebSocket voice while the optional UI server is absent, and FAILED
even if something else is listening on a port.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class LifecycleState(str, Enum):
    STARTING = "STARTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


@dataclass
class ComponentStatus:
    ok: bool
    detail: str = ""
    required: bool = True
    checked_at: float = field(default_factory=time.monotonic)


class VoiceRuntimeLifecycle:
    """Track component health and expose a readiness snapshot."""

    def __init__(
        self,
        *,
        startup_timeout_s: float = 120.0,
        require_whisper: bool = True,
        require_kokoro: bool = True,
        require_ollama: bool = True,
    ) -> None:
        self.startup_timeout_s = startup_timeout_s
        self.require_whisper = require_whisper
        self.require_kokoro = require_kokoro
        self.require_ollama = require_ollama
        self._started_at = time.monotonic()
        self._components: dict[str, ComponentStatus] = {}
        self._state = LifecycleState.STARTING
        self._diagnostics: list[str] = []
        self._lock = threading.Lock()
        self._shutdown = False

    @property
    def state(self) -> LifecycleState:
        return self._state

    def mark_component(self, name: str, *, ok: bool, detail: str = "") -> None:
        required = {
            "whisper": self.require_whisper,
            "kokoro": self.require_kokoro,
            "ollama": self.require_ollama,
        }.get(name, True)
        with self._lock:
            self._components[name] = ComponentStatus(
                ok=ok, detail=detail, required=required
            )

    def evaluate(self) -> LifecycleState:
        with self._lock:
            if self._shutdown:
                self._state = LifecycleState.FAILED
                return self._state
            elapsed = time.monotonic() - self._started_at
            missing_required = [
                name
                for name, req in (
                    ("kokoro", self.require_kokoro),
                    ("ollama", self.require_ollama),
                    ("whisper", self.require_whisper),
                )
                if req and name not in self._components
            ]
            if elapsed > self.startup_timeout_s and (
                missing_required or self._state is LifecycleState.STARTING
            ):
                msg = (
                    f"startup_timeout after {elapsed:.1f}s "
                    f"(limit={self.startup_timeout_s:.1f}s); "
                    f"missing={missing_required or 'evaluate stalled'}"
                )
                if msg not in self._diagnostics:
                    self._diagnostics.append(msg)
                self._state = LifecycleState.FAILED
                return self._state

            failed_required = [
                f"{name}: {status.detail or 'failed'}"
                for name, status in self._components.items()
                if status.required and not status.ok
            ]
            if failed_required:
                for item in failed_required:
                    if item not in self._diagnostics:
                        self._diagnostics.append(item)
                self._state = LifecycleState.FAILED
                return self._state

            if missing_required:
                self._state = LifecycleState.STARTING
                return self._state

            optional_failed = [
                name
                for name, status in self._components.items()
                if not status.required and not status.ok
            ]
            if optional_failed:
                self._state = LifecycleState.DEGRADED
                return self._state

            self._state = LifecycleState.READY
            return self._state

    def snapshot(self) -> dict[str, Any]:
        state = self.evaluate()
        with self._lock:
            ready = state in {LifecycleState.READY, LifecycleState.DEGRADED}
            return {
                "state": state.value,
                "ready": ready,
                "elapsed_s": round(time.monotonic() - self._started_at, 3),
                "startup_timeout_s": self.startup_timeout_s,
                "components": {
                    name: {
                        "ok": status.ok,
                        "detail": status.detail,
                        "required": status.required,
                    }
                    for name, status in self._components.items()
                },
                "diagnostics": list(self._diagnostics),
            }

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            self._state = LifecycleState.FAILED
            self._diagnostics.append("shutdown")


def bounded_call(fn: Callable[[], T], *, timeout_s: float, label: str) -> T:
    """Run ``fn`` in a worker thread; raise TimeoutError on deadline."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout_s)
        except FuturesTimeout as exc:
            future.cancel()
            raise TimeoutError(
                f"bounded_call {label!r} exceeded {timeout_s:.1f}s"
            ) from exc
