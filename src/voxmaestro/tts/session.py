"""Session glue: TTSWorker + TurnWriter for one live connection.

web_session (or any transport) should hold one SessionAudio:

    audio = SessionAudio(backend, send=ws.send)
    await audio.speak(req)
    audio.barge_in(new_turn_id)  # user interrupt
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from voxmaestro.tts.contract import AudioChunk, SynthesizeRequest, TTSBackend
from voxmaestro.tts.worker import TTSWorker
from voxmaestro.tts.writer import TurnWriter


class SessionAudio:
    """Own one backend worker and one turn-gated writer."""

    def __init__(self, backend: TTSBackend, send: Callable[[AudioChunk], Any]) -> None:
        """Bind a TTS backend and a sync or async audio sink."""
        self.backend = backend
        self.worker = TTSWorker(backend)
        self.writer = TurnWriter(send)

    async def speak(self, req: SynthesizeRequest) -> int:
        """Begin ``req.turn_id`` and write gated chunks. Return emitted count."""
        self.writer.begin_turn(req.turn_id)
        emitted = 0
        async for chunk in self.worker.stream(req, gate=self.writer.gate):
            if await self.writer.write(chunk):
                emitted += 1
        return emitted

    def barge_in(self, turn_id: str) -> None:
        """Cancel the previous turn and make ``turn_id`` the only live turn."""
        old = self.writer.current_turn
        self.writer.barge_in(turn_id)
        if old is not None and old != turn_id:
            self.worker.cancel(old)

    def flush(self) -> None:
        """Drop all in-flight audio until the next speak()."""
        old = self.writer.current_turn
        self.writer.flush()
        if old is not None:
            self.worker.cancel(old)
