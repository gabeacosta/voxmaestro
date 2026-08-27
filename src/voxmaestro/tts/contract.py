"""Frozen TTS backend contract.

Invariants:
- Capabilities are runtime-probed, never copied from PyPI/README text.
- Every audio chunk carries (turn_id, seq); consumers drop stale turns.
- Exported voice states are KV caches bound 1:1 to a session at open.
- French is the 24-layer slow lane until a 6-layer distill exists.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class LanguageLane(str, Enum):
    """Pocket TTS distill depth. FAST is 6-layer; QUALITY is 24-layer."""

    FAST = "6l"
    QUALITY = "24l"


@dataclass(frozen=True)
class LanguageSupport:
    """Advertised support for one language code."""

    code: str
    default_lane: LanguageLane
    has_6l: bool
    has_24l: bool
    notes: str = ""


# Inventory as of the 2026-05-04 six-language Pocket TTS release.
# Advertise via runtime probe; this tuple is the expected baseline, not a substitute.
POCKET_TTS_LANGUAGES: tuple[LanguageSupport, ...] = (
    LanguageSupport("en", LanguageLane.FAST, True, True),
    LanguageSupport("de", LanguageLane.FAST, True, True),
    LanguageSupport("it", LanguageLane.FAST, True, True),
    LanguageSupport("pt", LanguageLane.FAST, True, True),
    LanguageSupport("es", LanguageLane.FAST, True, True),
    LanguageSupport(
        "fr",
        LanguageLane.QUALITY,
        False,
        True,
        "french_24l only until a 6-layer distill ships",
    ),
)


@dataclass(frozen=True)
class ConsentRecord:
    """Provenance for a voice used in synthesis."""

    voice_id: str
    source: str
    granted_at: str
    license: str
    notes: str = ""


@dataclass(frozen=True)
class VoiceManifest:
    """Session-bound voice handle. KV-cache states must not be shared."""

    voice_id: str
    language: str
    lane: LanguageLane
    sample_rate: int
    backend_id: str
    backend_version: str
    quantization: str
    consent_record: ConsentRecord
    session_id: str

    def __post_init__(self) -> None:
        """Reject French 6l, missing bind, or invalid sample rate."""
        if self.language == "fr" and self.lane is not LanguageLane.QUALITY:
            raise ValueError("French must use the 24l quality lane")
        if not self.session_id:
            raise ValueError("VoiceManifest.session_id is required (WT-TTS-002)")
        if not self.backend_version:
            raise ValueError("backend_version is required on VoiceManifest")
        if not self.quantization:
            raise ValueError("quantization is required on VoiceManifest")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")


@dataclass(frozen=True)
class TTSCapabilities:
    """Runtime-probed backend surface. Do not copy PyPI or README claims."""

    backend_id: str
    backend_version: str
    quantizations: tuple[str, ...]
    languages: tuple[LanguageSupport, ...]
    sample_rates: tuple[int, ...]
    streaming: bool
    voice_state_export: bool
    runtime_memory_bytes: dict[str, int]
    advertised_from: str

    def __post_init__(self) -> None:
        """Require advertised_from == runtime-probe."""
        if self.advertised_from != "runtime-probe":
            raise ValueError(
                "TTSCapabilities.advertised_from must be 'runtime-probe' "
                "(do not copy PyPI or README claims)"
            )

    def language(self, code: str) -> LanguageSupport:
        """Return support for ``code`` or raise KeyError."""
        for item in self.languages:
            if item.code == code:
                return item
        raise KeyError(code)


@dataclass(frozen=True)
class AudioChunk:
    """One tagged PCM fragment. Writers drop chunks whose turn_id is stale."""

    pcm: bytes
    sample_rate: int
    turn_id: str
    seq: int
    is_last: bool = False
    flush_reason: str = "clause"

    def __post_init__(self) -> None:
        """Require turn_id, non-negative seq, and a known flush_reason."""
        if not self.turn_id:
            raise ValueError("AudioChunk.turn_id is required (WT-TTS-001)")
        if self.seq < 0:
            raise ValueError("AudioChunk.seq must be >= 0")
        if self.flush_reason not in {"clause", "punctuation", "barge-in", "end"}:
            raise ValueError(f"unknown flush_reason: {self.flush_reason}")


@dataclass(frozen=True)
class SynthesizeRequest:
    """One synthesis job bound to a session and turn."""

    text: str
    turn_id: str
    session_id: str
    voice: VoiceManifest
    language: str
    seq_start: int = 0

    def __post_init__(self) -> None:
        """Reject cross-session voice handles and French 6l requests."""
        if self.session_id != self.voice.session_id:
            raise ValueError(
                "SynthesizeRequest.session_id must match VoiceManifest.session_id "
                "(WT-TTS-002: never share a voice-state handle across sessions)"
            )
        if self.language == "fr" and self.voice.lane is not LanguageLane.QUALITY:
            raise ValueError("French synthesis requires the 24l lane")


@runtime_checkable
class TTSBackend(Protocol):
    """Blocking TTS backend. Iterate synthesize() only from TTSWorker."""

    def capabilities(self) -> TTSCapabilities:
        """Return runtime-probed capabilities."""

    def open_session(self, session_id: str, voice: VoiceManifest) -> None:
        """Bind one exported voice state to one session."""

    def close_session(self, session_id: str) -> None:
        """Release the bound voice state. Handles must not be reused."""

    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        """Blocking generator of tagged PCM chunks. Run via TTSWorker."""

    def cancel(self, turn_id: str) -> None:
        """Cooperative cancel: close generator, stop producing that turn."""
