"""Session glue: TTSWorker + TurnWriter for one live connection.

web_session (or any transport) should hold one SessionAudio:

    audio = SessionAudio(backend, send=ws.send)
    await audio.speak(req)
    audio.barge_in(new_turn_id)  # user interrupt
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from voxmaestro.tts.contract import AudioChunk, SynthesizeRequest, TTSBackend
from voxmaestro.tts.worker import TTSWorker
from voxmaestro.tts.writer import TurnWriter

ObserveFn = Callable[[str, float], Any]


def safe_observe(observe: ObserveFn | None) -> ObserveFn:
    """Return a hot-path-safe metrics callback."""
    def emit(name: str, value: float) -> None:
        if observe is None:
            return
        try:
            observe(name, value)
        except Exception:
            return
    return emit


class SessionAudio:
    """Own one backend worker and one turn-gated writer."""

    def __init__(
        self,
        backend: TTSBackend,
        send: Callable[[AudioChunk], Any],
        observe: ObserveFn | None = None,
    ) -> None:
        """Bind a TTS backend, a sink, and an optional metrics observer."""
        self.backend = backend
        self.worker = TTSWorker(backend)
        self.writer = TurnWriter(send)
        self._observe = safe_observe(observe)
        self._active: set[str] = set()
        self._pending_cancel: dict[str, float] = {}
        self.last_cancel_to_silence_ms: float | None = None

    async def speak(self, req: SynthesizeRequest) -> int:
        """Begin ``req.turn_id`` and write gated chunks. Return emitted count."""
        self.writer.begin_turn(req.turn_id)
        self._active.add(req.turn_id)
        emitted = 0
        started = time.monotonic()
        try:
            async for chunk in self.worker.stream(req, gate=self.writer.gate):
                if await self.writer.write(chunk):
                    emitted += 1
                    if emitted == 1:
                        self._observe("tts.first_chunk_ms", (time.monotonic() - started) * 1000)
            return emitted
        finally:
            self._active.discard(req.turn_id)
            cancelled_at = self._pending_cancel.pop(req.turn_id, None)
            if cancelled_at is not None:
                ms = (time.monotonic() - cancelled_at) * 1000
                self.last_cancel_to_silence_ms = ms
                self._observe("tts.cancel_to_silence_ms", ms)

    def barge_in(self, turn_id: str) -> bool:
        """Cancel the previous turn and make ``turn_id`` the only live turn."""
        old = self.writer.current_turn
        self.writer.barge_in(turn_id)
        if old is not None and old != turn_id:
            interrupted = old in self._active
            self._cancel_turn(old)
            return interrupted
        return False

    def flush(self) -> None:
        """Drop all in-flight audio until the next speak()."""
        old = self.writer.current_turn
        self.writer.flush()
        if old is not None:
            self._cancel_turn(old)

    def _cancel_turn(self, turn_id: str) -> None:
        """Cancel ``turn_id``; start the cancel_to_silence clock if streaming."""
        if turn_id in self._active:
            self._pending_cancel[turn_id] = time.monotonic()
        self.worker.cancel(turn_id)
