"""Frozen ASR backend contract and browser-mic ingress glue (voice-in).

Mirrors the TTS adapter rules:
- Capabilities are runtime-probed, never copied from README/PyPI claims.
- Every transcript event carries (session_id, utterance_id, seq).
- Partials never reach the conversation runtime; only finals become turns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

SHIPPING_ASR_LANGUAGES: tuple[str, ...] = ("en", "es")


@dataclass(frozen=True)
class ASRCapabilities:
    """Runtime-probed ASR surface. Do not copy README or PyPI claims."""

    backend_id: str
    backend_version: str
    languages: tuple[str, ...]
    sample_rates: tuple[int, ...]
    streaming: bool
    advertised_from: str

    def __post_init__(self) -> None:
        """Require runtime probing and a pinned backend version."""
        if self.advertised_from != "runtime-probe":
            raise ValueError(
                "ASRCapabilities.advertised_from must be 'runtime-probe'"
            )
        if not self.backend_version:
            raise ValueError("backend_version is required on ASRCapabilities")


@dataclass(frozen=True)
class TranscriptEvent:
    """One partial or final transcript fragment for an utterance."""

    session_id: str
    utterance_id: str
    seq: int
    text: str
    is_final: bool = False
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        """Require session/utterance ids, non-negative seq, sane confidence."""
        if not self.session_id:
            raise ValueError("TranscriptEvent.session_id is required")
        if not self.utterance_id:
            raise ValueError("TranscriptEvent.utterance_id is required")
        if self.seq < 0:
            raise ValueError("TranscriptEvent.seq must be >= 0")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


@runtime_checkable
class ASRBackend(Protocol):
    """Blocking frame-in ASR backend. One session per live connection."""

    def capabilities(self) -> ASRCapabilities:
        """Return runtime-probed capabilities."""

    def open_session(self, session_id: str, language: str) -> None:
        """Start recognizing one session in ``language``."""

    def close_session(self, session_id: str) -> None:
        """Release session state. Handles must not be reused."""

    def accept(
        self, session_id: str, pcm: bytes, sample_rate: int
    ) -> list[TranscriptEvent]:
        """Consume one mic frame; return events produced (possibly none)."""

    def flush(self, session_id: str) -> Optional[TranscriptEvent]:
        """Force-finalize the open utterance, or None when nothing is open."""


class AsrIngress:
    """Feed mic frames to a backend; hand adapter-ready events upward."""

    def __init__(self, backend: ASRBackend, *, language: str = "en") -> None:
        """Bind a backend and the default language for new sessions."""
        self.backend = backend
        self.language = language

    def open(self, session_id: str, language: Optional[str] = None) -> None:
        """Open a backend session, defaulting to the ingress language."""
        self.backend.open_session(session_id, language or self.language)

    def close(self, session_id: str) -> None:
        """Close the backend session."""
        self.backend.close_session(session_id)

    def accept(
        self, session_id: str, pcm: bytes, sample_rate: int
    ) -> list[dict]:
        """Map backend events to gateway events for one mic frame."""
        return self._map_all(self.backend.accept(session_id, pcm, sample_rate))

    def flush(self, session_id: str) -> list[dict]:
        """Map the force-finalized utterance, if any."""
        event = self.backend.flush(session_id)
        if event is None:
            return []
        return self._map_all([event])

    @classmethod
    def _map_all(cls, events: list[TranscriptEvent]) -> list[dict]:
        out = []
        for event in events:
            mapped = cls._map(event)
            if mapped is not None:
                out.append(mapped)
        return out

    @staticmethod
    def _map(event: TranscriptEvent) -> Optional[dict]:
        if event.is_final:
            text = event.text.strip()
            if not text:
                return None
            return {
                "type": "message",
                "sessionId": event.session_id,
                "text": text,
                "metadata": {"source": "asr", "utteranceId": event.utterance_id},
            }
        return {
            "type": "transcript_partial",
            "sessionId": event.session_id,
            "utteranceId": event.utterance_id,
            "text": event.text,
        }
