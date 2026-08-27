"""Async bridge for blocking TTS generators.

Pocket TTS streaming is a blocking Python generator over torch tensors.
Running it on the asyncio loop stalls VAD/endpointing and explodes
cancellation latency. TTSWorker runs synthesize() in a worker thread
and feeds an asyncio.Queue.

Cancellation is cooperative and per-stream: cancel(turn_id) sets that
stream's event and calls TTSBackend.cancel(turn_id). Producer threads
are joined in an executor so cleanup cannot stall the event loop.
WriterGate drops any straggler whose turn_id != current_turn.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable

from voxmaestro.tts.contract import AudioChunk, SynthesizeRequest, TTSBackend

_SENTINEL = object()


class WriterGate:
    """Server-side invalidation for WT-TTS-001.

    Browser FLUSH handles playback. This gate kills the in-flight race:
    drop anything where turn_id != current_turn.
    """

    def __init__(self, current_turn: Callable[[], str | None]) -> None:
        """Bind a callable that returns the writer's current turn id."""
        self._current_turn = current_turn

    def accept(self, chunk: AudioChunk) -> bool:
        """Return True if chunk.turn_id matches the current turn."""
        current = self._current_turn()
        return current is not None and chunk.turn_id == current


class TTSWorker:
    """Run one blocking synthesize() generator per stream() call."""

    def __init__(self, backend: TTSBackend) -> None:
        """Attach a backend. Cancel state is created per stream, not here."""
        self._backend = backend
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def cancel(self, turn_id: str) -> None:
        """Signal one stream and ask the backend to stop that turn."""
        with self._lock:
            event = self._cancels.get(turn_id)
        if event is not None:
            event.set()
        self._backend.cancel(turn_id)

    async def stream(
        self,
        req: SynthesizeRequest,
        gate: WriterGate | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Yield tagged chunks for ``req`` until last, cancel, or error."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue()
        cancel = threading.Event()
        with self._lock:
            if req.turn_id in self._cancels:
                raise RuntimeError(f"turn {req.turn_id!r} already streaming")
            self._cancels[req.turn_id] = cancel

        def _produce() -> None:
            try:
                for chunk in self._backend.synthesize(req):
                    if cancel.is_set():
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

        thread = threading.Thread(
            target=_produce,
            name=f"tts-worker-{req.turn_id}",
            daemon=True,
        )
        thread.start()

        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                if isinstance(item, Exception):
                    raise item
                chunk = item
                assert isinstance(chunk, AudioChunk)
                if gate is not None and not gate.accept(chunk):
                    continue
                yield chunk
        finally:
            cancel.set()
            self._backend.cancel(req.turn_id)
            with self._lock:
                self._cancels.pop(req.turn_id, None)
            await loop.run_in_executor(None, thread.join, 1.0)
