from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parent.parent / "examples" / "run_wt_voice_tts_001.py"


def _load_executor():
    spec = importlib.util.spec_from_file_location("wt_voice_tts_executor", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pocket_float32_duration_is_explicit_and_exact():
    executor = _load_executor()
    pcm = b"\x00" * (24000 * 4 * 2)

    assert executor._duration_from_float32_pcm(pcm, 24000) == 2.0


def test_pocket_duration_rejects_misaligned_or_empty_pcm():
    executor = _load_executor()

    with pytest.raises(ValueError):
        executor._duration_from_float32_pcm(b"", 24000)
    with pytest.raises(ValueError):
        executor._duration_from_float32_pcm(b"\x00\x00\x00", 24000)
    with pytest.raises(ValueError):
        executor._duration_from_float32_pcm(b"\x00" * 4, 0)
