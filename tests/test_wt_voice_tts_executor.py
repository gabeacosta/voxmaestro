from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).parent.parent / "examples" / "run_wt_voice_tts_001.py"
CORPUS_PATH = Path(__file__).parent.parent / "docs" / "wt" / "tts_003_corpus.yaml"


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


# --- Crosstalk wiring: examples/run_wt_voice_tts_001.py <-> voxmaestro.tts.crosstalk ---
#
# These tests use a fake TTS model that bakes the *requested text* directly
# into the produced PCM, and a fake transcriber that reads it back (or, for
# the leak test, deliberately swaps it with a sibling session's) -- proving
# the assignment -> transcribe -> detect_crosstalk -> evidence-completeness
# -> qualification-verdict wiring without any real audio, ASR model, or
# pocket-tts/torch import. detect_crosstalk()'s own decision logic has its
# own exhaustive, dependency-free tests in tests/test_tts_crosstalk.py.


class _LiteralTextTTSModel:
    sample_rate = 24000

    @classmethod
    def load_model(cls, language=None, config=None, quantize=False):
        return cls()

    def get_state_for_audio_prompt(self, voice: str) -> dict:
        return {"voice": voice}

    def generate_audio_stream(self, model_state, text_to_generate: str):
        payload = text_to_generate.encode("utf-8")
        payload += b"\x00" * (-len(payload) % 4)  # pad to a float32 boundary
        yield payload


class _LiteralTextTranscriber:
    """Reads back exactly what _LiteralTextTTSModel embedded -- the
    audio's content always matches the text that was actually requested."""

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, pcm_f32: bytes, sample_rate: int, language: str) -> str:
        self.calls += 1
        return pcm_f32.rstrip(b"\x00").decode("utf-8")


class _SequenceTranscriber(_LiteralTextTranscriber):
    """Ignores the actual audio and returns a pre-scripted sequence of
    transcripts instead, one per call in call order. Used to simulate a
    genuine content leak: report each session's transcript as its sibling
    session's assigned line for that run, positionally -- text-content
    matching would be ambiguous here since adjacent runs' utterance
    assignments legitimately overlap (see _pairwise_swap_sequence)."""

    def __init__(self, sequence: list[str]) -> None:
        super().__init__()
        self._sequence = list(sequence)
        self._index = 0

    def transcribe(self, pcm_f32: bytes, sample_rate: int, language: str) -> str:
        super().transcribe(pcm_f32, sample_rate, language)  # still counts the call
        value = self._sequence[self._index]
        self._index += 1
        return value


def _en_utterances() -> list[str]:
    corpus = yaml.safe_load(CORPUS_PATH.read_text())
    return [u["text"] for u in corpus["utterances"] if u["language"] == "en"]


def _pairwise_swap_sequence(*, runs: int) -> list[str]:
    """For a 2-session lane, the transcript run_lane() should observe for
    each call (in call order) if the two sessions' audio were genuinely
    swapped with each other every run: session 0's call reports session 1's
    assigned line and vice versa, using the exact same
    ``utterances[(run_index + session_index) % len(utterances)]`` mapping
    run_lane() itself uses -- built positionally, not by text content, since
    the same text can legitimately play different roles across runs."""
    utterances = _en_utterances()
    sequence: list[str] = []
    for run_index in range(runs):
        text0 = utterances[(run_index + 0) % len(utterances)]
        text1 = utterances[(run_index + 1) % len(utterances)]
        sequence.append(text1)  # returned for session 0's call
        sequence.append(text0)  # returned for session 1's call
    return sequence


def _patch_backend(executor, monkeypatch) -> None:
    """Bind PocketTTSBackend to the deterministic fake model so no real
    pocket-tts/torch is ever imported by these tests."""
    from voxmaestro.tts.pocket import PocketTTSBackend

    def factory(*, language, quantize):
        return PocketTTSBackend(
            model=_LiteralTextTTSModel.load_model(),
            model_cls=_LiteralTextTTSModel,
            rss_fn=lambda: 1,
            backend_version="test",
            sample_rate=24000,
            quantize=quantize,
            language=language,
        )

    monkeypatch.setattr(executor, "PocketTTSBackend", factory)


def _run_lane(executor, *, sessions: int, transcriber) -> dict:
    import argparse

    args = argparse.Namespace(
        language="en", sessions=sessions, runs=3, voice="alba", quantize=True, out=None
    )
    return asyncio.run(executor.run_lane(args, transcriber=transcriber))


def test_sessions_one_lane_never_needs_a_transcriber(monkeypatch):
    executor = _load_executor()
    _patch_backend(executor, monkeypatch)

    result = _run_lane(executor, sessions=1, transcriber=None)

    assert result["lane"]["evidence_complete"] is True
    assert result["acoustic_crosstalk_measured"] is False
    assert all(run["session_crosstalk_events"] == 0 for run in result["runs"])


def test_multi_session_without_transcriber_stays_evidence_incomplete(monkeypatch):
    executor = _load_executor()
    _patch_backend(executor, monkeypatch)

    result = _run_lane(executor, sessions=2, transcriber=None)

    assert result["lane"]["evidence_complete"] is False
    assert result["qualification"]["verdict"] == "TEST_INVALID"
    assert result["acoustic_crosstalk_measured"] is False


def test_multi_session_with_clean_transcriber_is_evidence_complete_and_crosstalk_free(monkeypatch):
    executor = _load_executor()
    _patch_backend(executor, monkeypatch)
    transcriber = _LiteralTextTranscriber()

    result = _run_lane(executor, sessions=2, transcriber=transcriber)

    assert result["acoustic_crosstalk_measured"] is True
    assert result["lane"]["evidence_complete"] is True
    assert result["lane"]["session_crosstalk_events"] == 0
    assert all(run["session_crosstalk_events"] == 0 for run in result["runs"])
    assert transcriber.calls == 2 * 3  # sessions * runs
    assert result["qualification"]["verdict"] in ("PASS", "FAIL")  # a real verdict, not TEST_INVALID


def test_real_content_leak_between_sessions_is_caught(monkeypatch):
    executor = _load_executor()
    _patch_backend(executor, monkeypatch)
    transcriber = _SequenceTranscriber(_pairwise_swap_sequence(runs=3))

    result = _run_lane(executor, sessions=2, transcriber=transcriber)

    assert result["acoustic_crosstalk_measured"] is True
    findings = result["acoustic_crosstalk_findings"]
    assert len(findings) == 2 * 3  # sessions * runs
    assert all(f["crosstalk"] for f in findings)  # every session, every run: swapped with its sibling
    assert result["lane"]["session_crosstalk_events"] == len(findings)
    assert result["qualification"]["verdict"] == "FAIL"  # max_session_crosstalk_events policy is 0
