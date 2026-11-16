"""Language-routed TTS facade."""

from __future__ import annotations

import threading
from collections.abc import Iterator

from voxmaestro.tts.contract import (
    AudioChunk,
    ConsentRecord,
    LanguageLane,
    LanguageSupport,
    SynthesizeRequest,
    TTSCapabilities,
    VoiceManifest,
)
from voxmaestro.tts.router import LanguageRoutedTTSBackend


class Leaf:
    def __init__(self, code: str, *, hold: threading.Event | None = None) -> None:
        self.code = code
        self.hold = hold
        self.cancelled: list[str] = []
        self._sessions: set[str] = set()
        self._caps = TTSCapabilities(
            backend_id=f"leaf-{code}",
            backend_version="t",
            quantizations=("fp32",),
            languages=(LanguageSupport(code, LanguageLane.FAST, True, False),),
            sample_rates=(24000,),
            streaming=True,
            voice_state_export=False,
            runtime_memory_bytes={"fp32": 1},
            advertised_from="runtime-probe",
        )

    def capabilities(self) -> TTSCapabilities:
        return self._caps

    def open_session(self, session_id: str, voice: VoiceManifest) -> None:
        assert voice.language == self.code
        self._sessions.add(session_id)

    def close_session(self, session_id: str) -> None:
        self._sessions.discard(session_id)

    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        if self.hold is not None:
            self.hold.wait(timeout=2.0)
        yield AudioChunk(pcm=b"x", sample_rate=24000, turn_id=req.turn_id, seq=0, is_last=True)

    def cancel(self, turn_id: str) -> None:
        self.cancelled.append(turn_id)


def _voice(language: str, backend_id: str, session_id: str = "s1") -> VoiceManifest:
    return VoiceManifest(
        voice_id="v",
        language=language,
        lane=LanguageLane.FAST,
        sample_rate=24000,
        backend_id=backend_id,
        backend_version="t",
        quantization="fp32",
        consent_record=ConsentRecord("v", "t", "2026-01-01T00:00:00Z", "demo"),
        session_id=session_id,
    )


def test_router_dispatches_by_language_and_cancels_owner() -> None:
    hold = threading.Event()
    en = Leaf("en", hold=hold)
    es = Leaf("es")
    router = LanguageRoutedTTSBackend({"en": en, "es": es})
    router.open_session("s1", _voice("en", "leaf-en"))

    gen = router.synthesize(
        SynthesizeRequest(
            text="hi",
            turn_id="t1",
            session_id="s1",
            voice=_voice("en", "leaf-en"),
            language="en",
        )
    )
    # Ownership is recorded before the first yield.
    router.cancel("t1")
    hold.set()
    chunks = list(gen)
    assert len(chunks) == 1
    assert en.cancelled == ["t1"]
    assert es.cancelled == []
