"""Local Whisper ASR backend (voice-in) for the frozen ASR contract.

faster-whisper is an optional runtime dependency; tests inject a fake model.
Whisper is not a streaming model: ``accept()`` buffers mic frames and
finalizes an utterance after ``silence_ms`` of low energy (simple RMS
endpointing). Partials are not emitted in v0; a streaming ASR lane can add
them later.

Mic frames must be mono int16 PCM at the probed session sample rate.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from typing import Any, Optional

from voxmaestro.asr import SHIPPING_ASR_LANGUAGES, ASRCapabilities, TranscriptEvent


class WhisperNotInstalledError(ImportError):
    """Raised when faster-whisper is required but not installed."""


def _import_whisper() -> Any:
    """Import faster_whisper.WhisperModel or raise WhisperNotInstalledError."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise WhisperNotInstalledError(
            "faster-whisper is not installed; pip install faster-whisper"
        ) from exc
    return WhisperModel


def _rms_int16(pcm: bytes) -> float:
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0.0
    return (sum(s * s for s in samples) / len(samples)) ** 0.5


def _to_float32(pcm: bytes) -> Any:
    pcm = pcm[: len(pcm) - (len(pcm) % 2)]
    try:
        import numpy as np
    except ImportError:
        samples = array("h")
        samples.frombytes(pcm)
        return array("f", (s / 32768.0 for s in samples))
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


@dataclass
class _AsrSession:
    language: str
    buffer: bytearray = field(default_factory=bytearray)
    silence_ms: float = 0.0
    speech_ms: float = 0.0
    utterance_n: int = 0
    seq_n: int = 0


class WhisperASRBackend:
    """ASRBackend over faster-whisper with energy endpointing."""

    backend_id = "faster-whisper"

    def __init__(
        self,
        *,
        model: Any | None = None,
        model_cls: Any | None = None,
        model_size: str = "base.en",
        sample_rate: int = 16000,
        silence_ms: int = 600,
        min_speech_ms: int = 250,
        rms_threshold: float = 500.0,
        backend_version: Optional[str] = None,
    ) -> None:
        """Load or inject a model and freeze the probed capability surface."""
        cls = model_cls
        if model is None:
            cls = cls or _import_whisper()
            model = cls(model_size)
        self._model = model
        self._model_size = model_size
        self._sample_rate = sample_rate
        self._silence_ms = silence_ms
        self._min_speech_ms = min_speech_ms
        self._rms_threshold = rms_threshold
        self._version = backend_version or _package_version()
        self._sessions: dict[str, _AsrSession] = {}
        self._capabilities = ASRCapabilities(
            backend_id=self.backend_id,
            backend_version=self._version,
            languages=SHIPPING_ASR_LANGUAGES,
            sample_rates=(sample_rate,),
            streaming=False,
            advertised_from="runtime-probe",
        )

    def capabilities(self) -> ASRCapabilities:
        """Return the frozen runtime-probed capability record."""
        return self._capabilities

    def open_session(self, session_id: str, language: str) -> None:
        """Open one session; only shipping languages (en/es) are accepted."""
        if language not in self._capabilities.languages:
            raise ValueError(f"language {language!r} is not shipping (en/es only)")
        if session_id in self._sessions:
            raise RuntimeError(f"session {session_id!r} already open")
        self._sessions[session_id] = _AsrSession(language=language)

    def close_session(self, session_id: str) -> None:
        """Drop the session buffer. Handles must not be reused."""
        self._sessions.pop(session_id, None)

    def accept(
        self, session_id: str, pcm: bytes, sample_rate: int
    ) -> list[TranscriptEvent]:
        """Buffer one frame; finalize on trailing silence after real speech."""
        session = self._session(session_id)
        if sample_rate != self._sample_rate:
            raise ValueError(
                f"sample_rate {sample_rate} != session rate {self._sample_rate}"
            )
        session.buffer.extend(pcm)
        frame_ms = (len(pcm) / 2) / self._sample_rate * 1000
        if _rms_int16(pcm) >= self._rms_threshold:
            session.speech_ms += frame_ms
            session.silence_ms = 0.0
            return []
        session.silence_ms += frame_ms
        if session.silence_ms >= self._silence_ms:
            if session.speech_ms >= self._min_speech_ms:
                event = self._finalize(session_id, session)
                return [event] if event is not None else []
            session.buffer.clear()
            session.silence_ms = 0.0
            session.speech_ms = 0.0
        return []

    def flush(self, session_id: str) -> Optional[TranscriptEvent]:
        """Force-finalize the buffered utterance, or None when empty/closed."""
        session = self._sessions.get(session_id)
        if session is None or not session.buffer:
            return None
        return self._finalize(session_id, session)

    def _session(self, session_id: str) -> _AsrSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise RuntimeError(f"session {session_id!r} is not open")
        return session

    def _finalize(
        self, session_id: str, session: _AsrSession
    ) -> Optional[TranscriptEvent]:
        pcm = bytes(session.buffer)
        session.buffer.clear()
        session.silence_ms = 0.0
        session.speech_ms = 0.0
        session.utterance_n += 1
        session.seq_n += 1
        return TranscriptEvent(
            session_id=session_id,
            utterance_id=f"{session_id}-u{session.utterance_n}",
            seq=session.seq_n,
            text=self._transcribe(pcm, session.language),
            is_final=True,
        )

    def _transcribe(self, pcm: bytes, language: str) -> str:
        audio = _to_float32(pcm)
        segments, _info = self._model.transcribe(audio, language=language)
        return " ".join(str(segment.text).strip() for segment in segments).strip()


def _package_version() -> str:
    """Return installed faster-whisper version, or 'unknown'."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover
        return "unknown"
    try:
        return version("faster-whisper")
    except PackageNotFoundError:
        return "unknown"
