"""Bounded, singleton bootstrap for heavy local voice components."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any, Callable

from voxmaestro.runtime_lifecycle import bounded_call


class WhisperPool:
    """Process-wide Whisper ASR singleton — no duplicate model loads."""

    def __init__(
        self,
        *,
        factory: Callable[..., Any] | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self._factory = factory
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._instance: Any | None = None
        self._key: tuple[Any, ...] | None = None

    def get(self, **kwargs: Any) -> Any:
        key = tuple(sorted(kwargs.items()))
        with self._lock:
            if self._instance is not None and self._key == key:
                return self._instance

            def _load() -> Any:
                factory = self._factory
                if factory is None:
                    from voxmaestro.asr_whisper import WhisperASRBackend

                    factory = WhisperASRBackend
                return factory(**kwargs)

            instance = bounded_call(
                _load, timeout_s=self._timeout_s, label="whisper_init"
            )
            self._instance = instance
            self._key = key
            return instance

    def set_timeout(self, timeout_s: float) -> None:
        self._timeout_s = timeout_s

    def release(self) -> None:
        with self._lock:
            self._instance = None
            self._key = None


_WHISPER_POOL = WhisperPool()


def get_whisper_pool() -> WhisperPool:
    return _WHISPER_POOL


def probe_kokoro(
    base_url: str = "http://127.0.0.1:8880",
    timeout_s: float = 5.0,
    retries: int = 3,
) -> str:
    """Return health detail or raise after bounded retries."""

    def _probe() -> str:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("status") != "ok":
            raise RuntimeError(f"kokoro health not ok: {payload!r}")
        return f"model={payload.get('model')} voice={payload.get('voice')}"

    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            return bounded_call(
                _probe, timeout_s=timeout_s + 0.5, label="kokoro_health"
            )
        except Exception as exc:  # noqa: BLE001 — retry transient TTS hang
            last_err = exc
            if attempt + 1 < retries:
                time.sleep(1.0 + attempt)
    assert last_err is not None
    raise last_err


def probe_ollama(base_url: str = "http://127.0.0.1:11434", timeout_s: float = 5.0) -> str:
    """Return model-count detail or raise."""

    def _probe() -> str:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        models = payload.get("models") or []
        if not models:
            raise RuntimeError("ollama has zero models")
        return f"models={len(models)}"

    return bounded_call(_probe, timeout_s=timeout_s + 0.5, label="ollama_tags")
