from __future__ import annotations

import pytest

from voxmaestro.tts.contract import AudioChunk
from voxmaestro.tts.writer import TurnWriter


def _chunk(turn_id: str, seq: int = 0, pcm: bytes = b"x") -> AudioChunk:
    return AudioChunk(pcm=pcm, sample_rate=24000, turn_id=turn_id, seq=seq)


@pytest.mark.asyncio
async def test_write_emits_current_turn() -> None:
    sent: list[bytes] = []
    writer = TurnWriter(lambda chunk: sent.append(chunk.pcm))
    writer.begin_turn("t1")
    assert await writer.write(_chunk("t1", pcm=b"a")) is True
    assert sent == [b"a"]
    assert writer.chunks_emitted == 1
    assert writer.chunks_dropped_stale_turn == 0


@pytest.mark.asyncio
async def test_barge_in_drops_old_turn() -> None:
    sent: list[str] = []
    writer = TurnWriter(lambda chunk: sent.append(chunk.turn_id))
    writer.begin_turn("t1")
    await writer.write(_chunk("t1"))
    writer.barge_in("t2")
    assert await writer.write(_chunk("t1")) is False
    assert await writer.write(_chunk("t2")) is True
    assert sent == ["t1", "t2"]
    assert writer.chunks_dropped_stale_turn == 1
    assert writer.current_turn == "t2"


@pytest.mark.asyncio
async def test_flush_drops_all_until_begin() -> None:
    sent: list[bytes] = []
    writer = TurnWriter(lambda chunk: sent.append(chunk.pcm))
    writer.begin_turn("t1")
    writer.flush()
    assert await writer.write(_chunk("t1")) is False
    writer.begin_turn("t1")
    assert await writer.write(_chunk("t1", pcm=b"z")) is True
    assert sent == [b"z"]


@pytest.mark.asyncio
async def test_async_sink() -> None:
    sent: list[bytes] = []

    async def _send(chunk: AudioChunk) -> None:
        sent.append(chunk.pcm)

    writer = TurnWriter(_send)
    writer.begin_turn("t1")
    await writer.write(_chunk("t1", pcm=b"ok"))
    assert sent == [b"ok"]


def test_begin_turn_requires_id() -> None:
    writer = TurnWriter(lambda chunk: None)
    with pytest.raises(ValueError, match="turn_id"):
        writer.begin_turn("")
