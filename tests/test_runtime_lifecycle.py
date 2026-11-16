"""Gateway / voice bootstrap lifecycle — STARTING → READY / DEGRADED / FAILED."""

from __future__ import annotations

import threading
import time

import pytest

from voxmaestro.runtime_lifecycle import (
    LifecycleState,
    VoiceRuntimeLifecycle,
    bounded_call,
)


def test_lifecycle_starts_ready_and_shuts_down() -> None:
    life = VoiceRuntimeLifecycle(startup_timeout_s=2.0)
    assert life.state is LifecycleState.STARTING
    life.mark_component("kokoro", ok=True, detail="health ok")
    life.mark_component("ollama", ok=True, detail="tags ok")
    life.mark_component("whisper", ok=True, detail="model loaded")
    assert life.evaluate() is LifecycleState.READY
    snap = life.snapshot()
    assert snap["state"] == "READY"
    assert snap["ready"] is True
    life.shutdown()
    assert life.state is LifecycleState.FAILED
    assert life.snapshot()["ready"] is False


def test_lifecycle_degraded_when_optional_asr_missing() -> None:
    life = VoiceRuntimeLifecycle(startup_timeout_s=2.0, require_whisper=False)
    life.mark_component("kokoro", ok=True)
    life.mark_component("ollama", ok=True)
    life.mark_component("whisper", ok=False, detail="not requested")
    assert life.evaluate() is LifecycleState.DEGRADED
    assert life.snapshot()["ready"] is True  # conversation TTS path still usable


def test_lifecycle_failed_on_required_component() -> None:
    life = VoiceRuntimeLifecycle(startup_timeout_s=2.0)
    life.mark_component("kokoro", ok=False, detail="down")
    life.mark_component("ollama", ok=True)
    life.mark_component("whisper", ok=True)
    assert life.evaluate() is LifecycleState.FAILED
    assert life.snapshot()["ready"] is False
    assert any("kokoro" in d for d in life.snapshot()["diagnostics"])


def test_startup_timeout_fails_closed() -> None:
    life = VoiceRuntimeLifecycle(startup_timeout_s=0.05)
    time.sleep(0.08)
    assert life.evaluate() is LifecycleState.FAILED
    assert any("startup_timeout" in d for d in life.snapshot()["diagnostics"])


def test_bounded_call_times_out() -> None:
    gate = threading.Event()

    def slow() -> str:
        gate.wait(timeout=2.0)
        return "done"

    with pytest.raises(TimeoutError, match="bounded_call"):
        bounded_call(slow, timeout_s=0.05, label="slow")
    gate.set()


def test_whisper_singleton_no_duplicate_init() -> None:
    from voxmaestro.voice_bootstrap import WhisperPool

    calls: list[str] = []

    class FakeASR:
        def __init__(self, **kwargs):
            calls.append("init")
            self.kwargs = kwargs

        def capabilities(self):
            class C:
                sample_rates = (16000,)
                backend_id = "fake"
                backend_version = "t"

            return C()

    pool = WhisperPool(factory=FakeASR, timeout_s=2.0)
    a = pool.get(model_size="base")
    b = pool.get(model_size="base")
    assert a is b
    assert calls == ["init"]
    pool.release()
    c = pool.get(model_size="base")
    assert calls == ["init", "init"]
    assert c is not a
