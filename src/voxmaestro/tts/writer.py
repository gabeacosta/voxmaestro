"""Server-side audio writer for WT-TTS-001.

Generation tags every AudioChunk with (turn_id, seq). This writer is the
WebSocket (or any sink) last line of defense: drop anything whose turn_id
is not the current turn. Browser FLUSH handles playback; TurnWriter.flush()
clears current_turn so in-flight chunks die here too.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from voxmaestro.tts.contract import AudioChunk
from voxmaestro.tts.worker import WriterGate

SendFn = Callable[[AudioChunk], Any]


class TurnWriter:
    """Gate audio to a sink by current turn_id."""

    def __init__(self, send: SendFn) -> None:
        """Bind a sync or async sink that receives accepted AudioChunks."""
        self._send = send
        self._current: str | None = None
        self.gate = WriterGate(lambda: self._current)
        self.chunks_emitted = 0
        self.chunks_dropped_stale_turn = 0

    @property
    def current_turn(self) -> str | None:
        """Return the turn currently allowed through the gate."""
        return self._current

    def begin_turn(self, turn_id: str) -> None:
        """Set the live turn. Later chunks with a different turn_id are dropped."""
        if not turn_id:
            raise ValueError("turn_id is required")
        self._current = turn_id

    def barge_in(self, turn_id: str) -> None:
        """Replace the live turn so old in-flight audio cannot reach the sink."""
        self.begin_turn(turn_id)

    def flush(self) -> None:
        """Clear the live turn. Every subsequent chunk is dropped until begin_turn."""
        self._current = None

    async def write(self, chunk: AudioChunk) -> bool:
        """Send chunk if it matches current_turn. Return True if emitted."""
        if not self.gate.accept(chunk):
            self.chunks_dropped_stale_turn += 1
            return False
        result = self._send(chunk)
        if inspect.isawaitable(result):
            await result
        self.chunks_emitted += 1
        return True
