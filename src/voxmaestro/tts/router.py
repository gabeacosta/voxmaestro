"""Language-routed TTS facade over multiple TTSBackend adapters.

Keeps Kokoro as the preferred English backend while allowing a separate local
Spanish backend (e.g. Pocket) without replacing either implementation.
Cancellation is delegated to the backend that owns the active turn.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from voxmaestro.tts.contract import (
    AudioChunk,
    LanguageSupport,
    SynthesizeRequest,
    TTSBackend,
    TTSCapabilities,
    VoiceManifest,
)


class LanguageRoutedTTSBackend:
    """Dispatch TTSBackend calls by ``voice.language`` / ``req.language``."""

    backend_id = "language-router"

    def __init__(self, backends: dict[str, TTSBackend]) -> None:
        if not backends:
            raise ValueError("backends must be non-empty")
        self._backends = dict(backends)
        self._turn_owner: dict[str, str] = {}
        self._lock = threading.Lock()
        self._capabilities = self._merge_capabilities()

    def _merge_capabilities(self) -> TTSCapabilities:
        languages: list[LanguageSupport] = []
        sample_rates: set[int] = set()
        quants: list[str] = []
        mem: dict[str, int] = {}
        versions: list[str] = []
        streaming = True
        for code, backend in self._backends.items():
            caps = backend.capabilities()
            versions.append(f"{caps.backend_id}@{caps.backend_version}")
            for lang in caps.languages:
                if lang.code == code or lang.code not in self._backends:
                    languages.append(lang)
            sample_rates.update(caps.sample_rates)
            for q in caps.quantizations:
                if q not in quants:
                    quants.append(q)
            mem.update({f"{code}:{k}": v for k, v in caps.runtime_memory_bytes.items()})
            streaming = streaming and caps.streaming
        # Prefer languages that match a routed backend key.
        by_code = {lang.code: lang for lang in languages}
        ordered = tuple(by_code[c] for c in self._backends if c in by_code)
        if not ordered:
            ordered = tuple(by_code.values())
        return TTSCapabilities(
            backend_id=self.backend_id,
            backend_version="+".join(versions),
            quantizations=tuple(quants) or ("unknown",),
            languages=ordered,
            sample_rates=tuple(sorted(sample_rates)),
            streaming=streaming,
            voice_state_export=False,
            runtime_memory_bytes=mem,
            advertised_from="runtime-probe",
        )

    def capabilities(self) -> TTSCapabilities:
        return self._capabilities

    def _backend_for_language(self, language: str) -> TTSBackend:
        backend = self._backends.get(language)
        if backend is None:
            raise ValueError(f"no TTS backend routed for language {language!r}")
        return backend

    def open_session(self, session_id: str, voice: VoiceManifest) -> None:
        backend = self._backend_for_language(voice.language)
        # VoiceManifest.backend_id must match the leaf backend, not the router.
        backend.open_session(session_id, voice)

    def close_session(self, session_id: str) -> None:
        for backend in self._backends.values():
            try:
                backend.close_session(session_id)
            except Exception:  # noqa: BLE001 — close is best-effort per leaf
                continue

    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        language = req.language
        backend = self._backend_for_language(language)
        # Record ownership eagerly (this method is not itself a generator) so
        # cancel(turn_id) works before the first chunk is pulled.
        with self._lock:
            self._turn_owner[req.turn_id] = language

        def _run() -> Iterator[AudioChunk]:
            try:
                yield from backend.synthesize(req)
            finally:
                with self._lock:
                    self._turn_owner.pop(req.turn_id, None)

        return _run()

    def cancel(self, turn_id: str) -> None:
        with self._lock:
            language = self._turn_owner.get(turn_id)
        if language is not None:
            self._backends[language].cancel(turn_id)
            return
        # Unknown owner: cancel on every leaf (safe for barge-in races).
        for backend in self._backends.values():
            backend.cancel(turn_id)
