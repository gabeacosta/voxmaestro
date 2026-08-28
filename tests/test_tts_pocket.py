from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from voxmaestro.tts.contract import (
    ConsentRecord,
    LanguageLane,
    SynthesizeRequest,
    VoiceManifest,
)
from voxmaestro.tts.pocket import PocketTTSBackend, PocketTTSNotInstalledError
from voxmaestro.tts.worker import TTSWorker


def _consent() -> ConsentRecord:
    return ConsentRecord(
        voice_id="alba",
        source="kyutai-demo",
        granted_at="2026-05-04T00:00:00Z",
        license="demo",
    )


def _manifest(**overrides: object) -> VoiceManifest:
    base: dict[str, object] = {
        "voice_id": "alba",
        "language": "en",
        "lane": LanguageLane.FAST,
        "sample_rate": 24000,
        "backend_id": "pocket-python",
        "backend_version": "test",
        "quantization": "int8",
        "consent_record": _consent(),
        "session_id": "sess_1",
    }
    base.update(overrides)
    return VoiceManifest(**base)  # type: ignore[arg-type]


class FakeTTSModel:
    def __init__(self) -> None:
        self.quantize = False
        self.sample_rate = 24000
        self.prompts: list[str] = []

    @classmethod
    def load_model(
        cls,
        language: str | None = None,
        config: str | None = None,
        quantize: bool = False,
    ) -> FakeTTSModel:
        inst = cls()
        inst.quantize = quantize
        inst.language = language
        return inst

    def get_state_for_audio_prompt(self, voice: str) -> dict[str, str]:
        self.prompts.append(voice)
        return {"voice": voice, "token": f"state-{voice}"}

    def generate_audio_stream(
        self,
        model_state: dict[str, str],
        text_to_generate: str,
    ) -> Iterator[bytes]:
        del text_to_generate
        yield b"aaa"
        yield b"bbb"


def _backend(model: FakeTTSModel | None = None) -> PocketTTSBackend:
    fake = model or FakeTTSModel.load_model(quantize=True)
    return PocketTTSBackend(
        model=fake,
        model_cls=FakeTTSModel,
        rss_fn=lambda: 234_000_000,
        backend_version="test",
        sample_rate=24000,
        quantize=True,
    )


def test_capabilities_are_runtime_probed() -> None:
    caps = _backend().capabilities()
    assert caps.advertised_from == "runtime-probe"
    assert "int8" in caps.quantizations
    assert caps.runtime_memory_bytes["int8"] == 234_000_000
    assert caps.streaming is True


def test_open_rejects_quantization_mismatch() -> None:
    backend = _backend()
    voice = _manifest(quantization="fp32")
    with pytest.raises(ValueError, match="quantization"):
        backend.open_session("sess_1", voice)


def test_open_rejects_version_mismatch() -> None:
    backend = _backend()
    voice = _manifest(backend_version="other")
    with pytest.raises(ValueError, match="backend_version"):
        backend.open_session("sess_1", voice)


def test_session_state_is_one_to_one() -> None:
    backend = _backend()
    backend.open_session("sess_1", _manifest())
    with pytest.raises(RuntimeError, match="already open"):
        backend.open_session("sess_1", _manifest())
    backend.close_session("sess_1")
    with pytest.raises(RuntimeError, match="not open"):
        list(
            backend.synthesize(
                SynthesizeRequest(
                    text="hi",
                    turn_id="t1",
                    session_id="sess_1",
                    voice=_manifest(),
                    language="en",
                )
            )
        )


def test_synthesize_tags_turn_and_seq() -> None:
    backend = _backend()
    voice = _manifest()
    backend.open_session("sess_1", voice)
    chunks = list(
        backend.synthesize(
            SynthesizeRequest(
                text="hi",
                turn_id="t9",
                session_id="sess_1",
                voice=voice,
                language="en",
            )
        )
    )
    assert [chunk.seq for chunk in chunks] == [0, 1]
    assert chunks[0].pcm == b"aaa"
    assert chunks[-1].is_last is True
    assert chunks[-1].turn_id == "t9"


@pytest.mark.asyncio
async def test_worker_cancel_stops_held_stream() -> None:
    class Held(FakeTTSModel):
        def __init__(self) -> None:
            super().__init__()
            self.gate = threading.Event()

        def generate_audio_stream(
            self,
            model_state: dict[str, str],
            text_to_generate: str,
        ) -> Iterator[bytes]:
            del model_state, text_to_generate
            self.gate.wait(timeout=1.0)
            yield b"late"

    model = Held()
    backend = _backend(model)
    voice = _manifest()
    backend.open_session("sess_1", voice)
    worker = TTSWorker(backend)
    req = SynthesizeRequest(
        text="hi",
        turn_id="t1",
        session_id="sess_1",
        voice=voice,
        language="en",
    )

    async def _consume() -> list[Any]:
        return [chunk async for chunk in worker.stream(req)]

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)
    worker.cancel("t1")
    model.gate.set()
    chunks = await task
    assert chunks == []


def test_missing_package_message() -> None:
    with pytest.raises(PocketTTSNotInstalledError, match="pocket-tts"):
        raise PocketTTSNotInstalledError(
            "pocket-tts is not installed; pip install pocket-tts"
        )
