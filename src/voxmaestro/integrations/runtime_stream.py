"""Streaming adapter for ``VoxMaestroRuntime``.

The adapter emits filler frames while a tool is still running. It is deliberately
framework-neutral for now; a native Pipecat processor can consume the same
streaming contract in the next integration slice.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, Optional

from voxmaestro.runtime import CallSession, VoxMaestroRuntime


class RuntimeFrame:
    """Base event emitted by the runtime stream."""


class FillerFrame(RuntimeFrame):
    def __init__(self, text: str, audio_path: Optional[str] = None):
        self.text = text
        self.audio_path = audio_path


class ToolResultFrame(RuntimeFrame):
    def __init__(
        self,
        tool_name: str,
        result: Any,
        success: bool,
        *,
        simulated: bool = False,
        error: Optional[str] = None,
    ):
        self.tool_name = tool_name
        self.result = result
        self.success = success
        self.simulated = simulated
        self.error = error


class StateChangeFrame(RuntimeFrame):
    def __init__(self, from_state: str, to_state: str, trigger: Optional[str] = None):
        self.from_state = from_state
        self.to_state = to_state
        self.trigger = trigger


class HandoffFrame(RuntimeFrame):
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload


class RuntimeStreamProcessor:
    """Turn transcripts into frames without buffering the hot path."""

    def __init__(
        self,
        runtime: VoxMaestroRuntime,
        intent_classifier: Optional[Callable] = None,
    ):
        self.runtime = runtime
        self.intent_classifier = intent_classifier
        self._session: Optional[CallSession] = None
        self._queue: asyncio.Queue[RuntimeFrame] = asyncio.Queue()

    @property
    def session(self) -> CallSession:
        if self._session is None:
            raise RuntimeError("No active call. Call start_call() first.")
        return self._session

    @property
    def context(self):
        return self.session.context

    def start_call(self, call_id: str, caller_phone: str = "", **metadata: Any):
        self._session = self.runtime.start_call(
            call_id,
            caller_phone,
            on_filler=self._emit_filler,
            on_transfer=self._emit_handoff,
            **metadata,
        )
        return self.context

    async def iter_frames(self, transcript: Any) -> AsyncIterator[RuntimeFrame]:
        text = getattr(transcript, "text", None)
        if not text:
            return

        intent = None
        if self.intent_classifier:
            intent = await self.intent_classifier(text, self.context)

        state_before = self.context.current_state
        turn_task = asyncio.create_task(self.session.process_turn(text, intent=intent))
        queue_task: Optional[asyncio.Task[RuntimeFrame]] = asyncio.create_task(
            self._queue.get()
        )

        while True:
            wait_set: set[asyncio.Task[Any]] = {turn_task}
            if queue_task is not None:
                wait_set.add(queue_task)
            done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

            if queue_task is not None and queue_task in done:
                yield queue_task.result()
                queue_task = (
                    None
                    if turn_task.done()
                    else asyncio.create_task(self._queue.get())
                )

            if turn_task in done:
                result = turn_task.result()
                if queue_task is not None and not queue_task.done():
                    queue_task.cancel()
                    await asyncio.gather(queue_task, return_exceptions=True)
                break

        while not self._queue.empty():
            yield self._queue.get_nowait()

        if self.context.current_state != state_before:
            yield StateChangeFrame(
                state_before,
                self.context.current_state,
                result.get("action"),
            )

        tool_result = result.get("tool_result")
        if tool_result is not None:
            yield ToolResultFrame(
                tool_result.tool_name,
                tool_result.data,
                tool_result.success,
                simulated=tool_result.simulated,
                error=tool_result.error,
            )

    async def collect(self, transcript: Any) -> list[RuntimeFrame]:
        return [frame async for frame in self.iter_frames(transcript)]

    async def _emit_filler(self, filler: dict[str, Any]) -> None:
        await self._queue.put(
            FillerFrame(filler.get("text", ""), filler.get("audio"))
        )

    async def _emit_handoff(self, teardown: dict[str, Any]) -> None:
        await self._queue.put(HandoffFrame(teardown))
