from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from test_runtime_truth import CONFIG
from voxmaestro import VoxMaestroRuntime
from voxmaestro.integrations.runtime_stream import (
    FillerFrame,
    RuntimeStreamProcessor,
    ToolResultFrame,
)


class Transcript:
    def __init__(self, text: str):
        self.text = text


@pytest.mark.asyncio
async def test_filler_is_emitted_before_tool_finishes():
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    async def execute_tool(tool_name, tool, params, context):
        tool_started.set()
        await release_tool.wait()
        return {"available": True}

    async def classify(text, context):
        return "schedule_appointment"

    runtime = VoxMaestroRuntime(deepcopy(CONFIG), tool_executor=execute_tool)
    processor = RuntimeStreamProcessor(runtime, intent_classifier=classify)
    processor.start_call("stream-call")
    processor.context.current_state = "qualification"

    frames = processor.iter_frames(Transcript("Thursday at three"))
    first = await asyncio.wait_for(anext(frames), timeout=0.1)

    assert isinstance(first, FillerFrame)
    assert tool_started.is_set()
    assert not release_tool.is_set()

    release_tool.set()
    remaining = [frame async for frame in frames]

    assert any(isinstance(frame, ToolResultFrame) for frame in remaining)
    assert processor.context.current_state == "qualification"


@pytest.mark.asyncio
async def test_collection_does_not_duplicate_filler():
    async def execute_tool(tool_name, tool, params, context):
        return {"available": True}

    async def classify(text, context):
        return "schedule_appointment"

    runtime = VoxMaestroRuntime(deepcopy(CONFIG), tool_executor=execute_tool)
    processor = RuntimeStreamProcessor(runtime, intent_classifier=classify)
    processor.start_call("collect-call")
    processor.context.current_state = "qualification"

    frames = await processor.collect(Transcript("Book me"))

    assert sum(isinstance(frame, FillerFrame) for frame in frames) == 1
    assert sum(isinstance(frame, ToolResultFrame) for frame in frames) == 1
