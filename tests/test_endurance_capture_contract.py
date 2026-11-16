"""Regression for endurance empty_transcript from truncated duplex capture.

Evidence: run1 endurance-4 and run2 endurance-10 WAVs have energy only in the
first third (exact zeros afterward). Legacy RMS>0.01 speech_detected accepted
them; Whisper returned empty/punctuation. See
evidence/voxmaestro-voice-reliability-002/ENDURANCE_CLOSURE_003_FAILURE_ANALYSIS.md.
"""

from __future__ import annotations

import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
EVIDENCE = ROOT / "evidence" / "voxmaestro-voice-reliability-002"
sys.path.insert(0, str(EXAMPLES))

# This contract test only needs pure capture/transcript helpers. The example
# module imports sounddevice at top level, which requires PortAudio and is not
# available on CI runners — stub it so collection does not need hardware audio.
if "sounddevice" not in sys.modules:
    _sounddevice_stub = types.ModuleType("sounddevice")
    _sounddevice_stub.CallbackStop = type("CallbackStop", (Exception,), {})
    sys.modules["sounddevice"] = _sounddevice_stub

from run_physical_endurance import (  # noqa: E402
    _audio_coverage_ratio,
    _capture_usable,
    _usable_transcript,
)


def _load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


@pytest.fixture(scope="module")
def fail4() -> np.ndarray:
    path = EVIDENCE / "run1-mics" / "endurance-4-mic.wav"
    if not path.exists():
        pytest.skip("immutable fail4 fixture missing")
    return _load_wav(path)


@pytest.fixture(scope="module")
def pass9() -> np.ndarray:
    path = EVIDENCE / "run2-mics" / "endurance-9-mic.wav"
    if not path.exists():
        pytest.skip("pass9 fixture missing")
    return _load_wav(path)


def test_legacy_rms_gate_falsely_accepts_truncated_fail4(fail4: np.ndarray) -> None:
    """Documents the 19/20 failure mechanism: RMS gate alone is insufficient."""
    rms = float(np.sqrt(np.mean(fail4.astype(np.float64) ** 2)))
    assert rms > 0.01  # legacy speech_detected would be True
    thirds = np.array_split(fail4, 3)
    mid_rms = float(np.sqrt(np.mean(thirds[1].astype(np.float64) ** 2)))
    assert mid_rms == 0.0  # truncated capture artifact


def test_coverage_gate_rejects_truncated_fail4(fail4: np.ndarray) -> None:
    assert _audio_coverage_ratio(fail4) < 0.35
    assert _capture_usable(fail4) is False


def test_coverage_gate_accepts_pass9(pass9: np.ndarray) -> None:
    assert _audio_coverage_ratio(pass9) >= 0.35
    assert _capture_usable(pass9) is True


def test_usable_transcript_rejects_punctuation_only() -> None:
    assert _usable_transcript(". . . .") == ""
    assert _usable_transcript("") == ""
    assert _usable_transcript("Hello there") == "Hello there"
