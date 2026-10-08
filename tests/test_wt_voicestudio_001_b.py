"""No hardware or model dependency: test only the specimen's validation logic."""
from __future__ import annotations

import importlib.util
import io
import json
import wave
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

BASE = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BASE / "examples" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wav(frames: int = 24000) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(b"\x00\x00" * frames)
    return stream.getvalue()


def test_duration_requires_real_wav():
    b = _load("voice_probe", "run_wt_voicestudio_001_b.py")
    assert b.wav_duration(_wav()) == 1.0
    for raw in (b"", b"not a wav", _wav(0), _wav()[:-1024]):
        with pytest.raises(ValueError):
            b.wav_duration(raw)


@pytest.mark.parametrize("url", [
    "https://localhost:3900", "http://somewhere.com:3900", "http://127.0.0.1",
    "http://192.168.1.1:3900", "http://127.0.0.1:3900/speech",
    "http://127.0.0.1:3900?access_token=secret",
])
def test_only_explicit_loopback(url):
    with pytest.raises(ValueError):
        _load("voice_probe", "run_wt_voicestudio_001_b.py").local_endpoint(url)


def test_corpus_locked_per_language():
    b = _load("voice_probe", "run_wt_voicestudio_001_b.py")
    en, sha = b.corpus_samples("en", 3)
    es, other_sha = b.corpus_samples("es", 3)
    assert [x["id"] for x in en] == ["en-fill-01", "en-clause-01", "en-clause-02"]
    assert [x["id"] for x in es] == ["es-fill-01", "es-clause-01", "es-clause-02"]
    assert sha == other_sha and len(sha) == 64


def test_no_simulated_hardware_pass(tmp_path):
    b = _load("voice_probe", "run_wt_voicestudio_001_b.py")
    receipt = tmp_path / "invalid.json"
    with patch.object(b, "m4_attestation", side_effect=RuntimeError("test has no real Mac")):
        code = b.main([
            "--backend", "voicestudio", "--language", "en", "--voice", "alloy",
            "--out", str(receipt),
        ])
    assert code == 2
    assert json.loads(receipt.read_text())["qualification"] == "TEST_INVALID"


def _fixture(backend: str):
    return {
        "schema": "wt-voicestudio-001-b.hardware.v1",
        "backend": backend, "qualification": "RECORDED_NOT_PROMOTABLE",
        "language": "en",
        "hardware": {"system": "Darwin", "chip": "Apple M4", "memory_bytes": 17179869184},
        "corpus_sha256": "a" * 64,
        "rows": [{"utterance_id": i} for i in ("a", "b", "c")],
        "complete_p95_ms": 300.0, "rtf_p95": 0.5,
    }


def test_comparison_remains_observation_even_when_faster():
    m = _load("compare_probe", "summarize_wt_voicestudio_001_b.py")
    vs, pocket = _fixture("voicestudio"), _fixture("pocket")
    pocket["complete_p95_ms"] = 600.0
    result = m.compare(vs, pocket)
    assert result["complete_p95_ratio"] == 0.5
    assert result["qualification"] == "OBSERVED_NOT_PROMOTABLE"


def test_incomparable_results_not_valid():
    m = _load("compare_probe", "summarize_wt_voicestudio_001_b.py")
    vs, pocket = _fixture("voicestudio"), _fixture("pocket")
    mutations = [
        ("corpus_sha256", "b" * 64),
        ("language", "es"),
        ("rows", []),
        ("qualification", "PASS"),
        ("complete_p95_ms", -1.0),
    ]
    for key, value in mutations:
        other = deepcopy(pocket)
        other[key] = value
        assert m.compare(vs, other)["qualification"] == "TEST_INVALID"
