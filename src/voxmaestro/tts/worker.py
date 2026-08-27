"""Async bridge for blocking TTS generators.

Pocket TTS streaming is a blocking Python generator over torch tensors.
Running it on the asyncio loop stalls VAD/endpointing and explodes
cancellation latency. TTSWorker runs synthesize() in a worker thread
and feeds an asyncio.Queue.

Cancellation is cooperative: cancel() is called, the queue is drained,
and WriterGate drops any straggler whose turn_id != current_turn.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import AsyncIterator

from voxmaestro.tts.contract import AudioChunk, SynthesizeRequest, TTSBackend

_SENTINEL = object()


class WriterGate:
    """Server-side invalidation for WT-TTS-001.

    Browser FLUSH handles playback. This gate kills the in-flight race:
    drop anything where turn_id != current_turn.
    """

    def __init__(self, current_turn: Callable[[], str | None]) -> None:
        self._current_turn = current_turn

    def accept(self, chunk: AudioChunk) -> bool:
        current = self._current_turn()
        return current is not None and chunk.turn_id == current


class TTSWorker:
    def __init__(self, backend: TTSBackend, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._backend = backend
        self._loop = loop
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def cancel(self, turn_id: str) -> None:
        self._cancel.set()
        self._backend.cancel(turn_id)

    async def stream(
        self,
        req: SynthesizeRequest,
        gate: WriterGate | None = None,
    ) -> AsyncIterator[AudioChunk]:
        loop = self._loop or asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        self._cancel.clear()

        def _produce() -> None:
            try:
                for chunk in self._backend.synthesize(req):
                    if self._cancel.is_set():
                        break
                    if chunk.turn_id != req.turn_id:
                        continue
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
                    if chunk.is_last:
                        break
            except Exception as exc:  # noqa: BLE001 — surface backend failures to the loop
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        self._thread = threading.Thread(
            target=_produce,
            name=f"tts-worker-{req.turn_id}",
            daemon=True,
        )
        self._thread.start()

        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                if isinstance(item, Exception):
                    raise item
                chunk: AudioChunk = item
                if gate is not None and not gate.accept(chunk):
                    continue
                yield chunk
        finally:
            self._cancel.set()
            if self._thread is not None:
                self._thread.join(timeout=1.0)
