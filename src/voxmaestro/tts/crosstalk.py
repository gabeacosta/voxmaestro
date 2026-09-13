"""Acoustic session-crosstalk detection for WT-VOICE-TTS-001.

Deliberately separate from ``voxmaestro.tts.qualification`` (the frozen
adjudicator, which stays deterministic and provider-neutral) and
``voxmaestro.tts.measurement`` (the provider-neutral runner): this module
answers exactly one question, from real transcribed audio, never from
turn_id/session_id/handle bookkeeping alone --

    does session A's captured audio actually contain session B's speech?

``examples/run_wt_voice_tts_001.py`` previously hardcoded
``session_crosstalk_events=0`` with the comment "Acoustic cross-talk is
deliberately not inferred from handle binding" -- a structural,
identity-based check was considered and rejected as insufficient acoustic
evidence. This module is the real detector that replaces that placeholder.

Two halves, kept apart the same way measurement/qualification are:

- ``AcousticTranscriber`` (Protocol) + ``WhisperAcousticTranscriber``: turn
  captured PCM into text. Optional, heavy dependency (``faster-whisper``);
  never imported by ``detect_crosstalk`` below.
- ``detect_crosstalk``: a pure, dependency-free function over
  already-produced transcripts. Independently unit-testable without any ASR
  model, exactly like ``qualify_tts_lane`` is testable without a TTS model.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class AcousticTranscriber(Protocol):
    """One-shot decoder for a complete, already-synthesized utterance."""

    def transcribe(self, pcm_f32: bytes, sample_rate: int, language: str) -> str:
        """Return the best-effort transcript for ``pcm_f32`` (little-endian
        float32 mono PCM). Never raise for merely poor audio; return an
        empty or low-confidence string instead so the caller can treat the
        result as inconclusive rather than crashing the benchmark."""


@dataclass(frozen=True)
class CrosstalkFinding:
    """One session's crosstalk verdict for one benchmark run."""

    session_id: str
    own_similarity: float
    best_other_session_id: Optional[str]
    best_other_similarity: float
    crosstalk: bool
    inconclusive: bool


def _normalize(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _similarity(a: str, b: str) -> float:
    a, b = _normalize(a), _normalize(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def detect_crosstalk(
    assignments: Mapping[str, str],
    transcripts: Mapping[str, str],
    *,
    own_match_floor: float = 0.6,
    crosstalk_margin: float = 0.15,
) -> tuple[CrosstalkFinding, ...]:
    """Compare each session's transcribed audio against every session's
    assigned utterance for one run.

    ``assignments`` maps session_id -> the text that session was asked to
    speak (must be distinct per session for this check to mean anything;
    see ``examples/run_wt_voice_tts_001.py``). ``transcripts`` maps
    session_id -> the ASR transcript of that session's captured audio.

    A session is flagged ``crosstalk=True`` only when its transcript
    matches a *different* session's assigned text meaningfully better than
    it matches its own (``best_other_similarity > own_similarity +
    crosstalk_margin``) -- never merely because its own match is weak. A
    transcript that matches nothing well (ASR failure, garbled audio, an
    unassigned language) is ``inconclusive=True``, not a crosstalk verdict
    either way: this function must never turn "we couldn't tell" into a
    false "crosstalk" or a false "no crosstalk" claim. Callers should treat
    any inconclusive finding as evidence-incomplete for that run (per the
    project's frozen invariant: missing evidence produces TEST_INVALID,
    never an inferred PASS or FAIL).
    """
    if len(assignments) < 2:
        raise ValueError("detect_crosstalk requires at least two concurrent sessions")

    findings: list[CrosstalkFinding] = []
    for session_id, own_expected in assignments.items():
        transcript = transcripts.get(session_id, "")
        own_similarity = _similarity(transcript, own_expected)

        best_other_id: Optional[str] = None
        best_other_similarity = 0.0
        for other_id, other_expected in assignments.items():
            if other_id == session_id:
                continue
            score = _similarity(transcript, other_expected)
            if score > best_other_similarity:
                best_other_similarity = score
                best_other_id = other_id

        inconclusive = own_similarity < own_match_floor and best_other_similarity < own_match_floor
        crosstalk = (
            not inconclusive
            and best_other_id is not None
            and best_other_similarity > own_similarity + crosstalk_margin
        )
        findings.append(
            CrosstalkFinding(
                session_id=session_id,
                own_similarity=own_similarity,
                best_other_session_id=best_other_id if best_other_similarity > 0 else None,
                best_other_similarity=best_other_similarity,
                crosstalk=crosstalk,
                inconclusive=inconclusive,
            )
        )
    return tuple(findings)


class WhisperNotInstalledForCrosstalkError(ImportError):
    """Raised when a real acoustic check is requested but faster-whisper is missing."""


class WhisperAcousticTranscriber:
    """Real ASR oracle for the crosstalk check, over faster-whisper.

    Optional runtime dependency, exactly like
    ``voxmaestro.asr_whisper.WhisperASRBackend``; tests inject a fake model
    so this module never needs torch. faster-whisper's bundled feature
    extractor expects 16kHz mono audio, so input at another rate (Pocket
    TTS ships 24kHz) is linearly resampled first -- adequate for matching
    utterance *content*, not a claim of production audio quality.
    """

    def __init__(self, *, model: Any = None, model_size: str = "base", model_cls: Any = None) -> None:
        cls = model_cls
        if model is None:
            try:
                from faster_whisper import WhisperModel as cls  # noqa: N814
            except ImportError as exc:
                raise WhisperNotInstalledForCrosstalkError(
                    "faster-whisper is not installed; pip install faster-whisper"
                ) from exc
            model = cls(model_size)
        self._model = model

    def transcribe(self, pcm_f32: bytes, sample_rate: int, language: str) -> str:
        import numpy as np

        audio = np.frombuffer(pcm_f32, dtype=np.float32)
        if sample_rate != 16000 and audio.size:
            target_len = max(1, round(audio.size * 16000 / sample_rate))
            src_x = np.linspace(0, 1, num=audio.size, endpoint=False)
            dst_x = np.linspace(0, 1, num=target_len, endpoint=False)
            audio = np.interp(dst_x, src_x, audio).astype(np.float32)
        segments, _info = self._model.transcribe(audio, language=language)
        return " ".join(str(segment.text).strip() for segment in segments).strip()
