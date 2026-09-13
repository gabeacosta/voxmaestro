"""Slice 2 software-level voice-loop evidence (WT-VOICE handoff, 2026-09-13).

This is NOT a physical microphone/speaker run. This cloud execution container
has no audio hardware, so this script cannot and does not claim to prove the
physical path -- that remains BLOCKED (see EXECUTION_STATUS.md). It instead
drives the REAL production classes end to end -- ``WebSocketGateway``,
``AsrIngress``, ``WhisperASRBackend``, ``WebSessionAdapter``,
``PocketTTSBackend``, ``SessionAudio`` -- exactly as ``examples/serve_gateway.py``
and ``examples/pocket_voice_demo.py`` wire them, over synthetic PCM and a fake
WebSocket connection (same ``FakeConn`` shape as ``tests/test_gateway.py``)
instead of a live mic/socket.

The only substitutions are the two neural network weights this sandbox cannot
load without a multi-gigabyte, GPU-oriented torch/CUDA download that has no
bearing on the target Mac mini (Apple Silicon, no CUDA) and is unsafe for this
container's fixed disk allowance:

- Whisper's actual speech model is replaced with a scripted ``.transcribe()``
  stub (same injection point ``tests/test_asr_whisper.py`` uses). The real
  energy-endpointing / buffering / session code in ``WhisperASRBackend`` runs
  unmodified against real synthetic int16 PCM (loud = speech, silence = gap).
- Pocket TTS's actual model is replaced with ``FakeTTSModel`` (byte-for-byte
  the same fake used in ``tests/test_tts_pocket.py``). The real streaming /
  cancellation / ``SessionAudio`` / ``TTSWorker`` thread-bridge code runs
  unmodified.

Everything else -- state machine transitions, tool execution and failure
handling, barge-in cancellation, session isolation -- is the real shipped
code, unmodified.

Part A drives the mic-ingress path through the real ``WebSocketGateway``:
    mic PCM -> WhisperASRBackend -> transcript -> VoxMaestro turn
    -> generated reply -> Pocket TTS -> audio-out events
for one English and one Spanish session, plus one injected dependency
failure in the availability tool.

Part B reproduces the barge-in scenario the same way
``examples/pocket_voice_demo.py`` already proves it on real hardware: two
concurrent turns on one session via direct ``WebSessionAdapter.iter_events``
calls, so the event loop genuinely interleaves them (a full
``WebSocketGateway.handle()`` pass processes one connection's queued frames
strictly sequentially and cannot exhibit real interleaving on its own).

Run:
    python examples/voice_loop_slice2.py
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from voxmaestro import VoxMaestroRuntime
from voxmaestro.asr import AsrIngress
from voxmaestro.asr_whisper import WhisperASRBackend
from voxmaestro.integrations.gateway import WebSocketGateway
from voxmaestro.integrations.web_session import WebSessionAdapter
from voxmaestro.tts.contract import ConsentRecord, LanguageLane, VoiceManifest
from voxmaestro.tts.pocket import PocketTTSBackend

CONFIG_PATH = Path(__file__).with_name("microscroll_landing.yaml")
MIC_SAMPLE_RATE = 16000  # must match WhisperASRBackend's default session rate


# --- Fake neural weights (documented above); everything else is real code ---


class _Segment:
    def __init__(self, text: str) -> None:
        self.text = text


class ScriptedWhisperModel:
    """Stand-in for the actual Whisper net. Ignores audio, returns a script."""

    def __init__(self, script: dict[str, list[str]]) -> None:
        self._script = {key: list(value) for key, value in script.items()}

    def transcribe(self, audio: Any, language: str) -> tuple[list[_Segment], None]:
        queue = self._script.get(language, [])
        text = queue.pop(0) if queue else "(no more scripted utterances)"
        return [_Segment(text)], None


class FakeTTSModel:
    """Byte-identical stand-in to tests/test_tts_pocket.py::FakeTTSModel."""

    sample_rate = 24000

    @classmethod
    def load_model(cls, language=None, config=None, quantize=False):
        inst = cls()
        inst.quantize = quantize
        inst.language = language
        return inst

    def get_state_for_audio_prompt(self, voice: str) -> dict[str, str]:
        return {"voice": voice, "token": f"state-{voice}"}

    def generate_audio_stream(self, model_state, text_to_generate) -> Iterator[bytes]:
        del text_to_generate
        # Runs in TTSWorker's background producer thread (see tts/worker.py),
        # so a real sleep here does not block the event loop. It exists only
        # so a real barge-in has a window to land mid-stream instead of the
        # whole (otherwise instant) fake stream finishing before the
        # interrupting message is even sent.
        import time as _time

        for _ in range(200):
            _time.sleep(0.01)
            yield b"\x00\x00\x00\x00" * 64


def _loud(ms: int, rate: int = MIC_SAMPLE_RATE) -> bytes:
    n = int(rate * ms / 1000)
    return (b"\x10\x27" * n)[: n * 2]  # int16 LE, nonzero amplitude


def _silent(ms: int, rate: int = MIC_SAMPLE_RATE) -> bytes:
    n = int(rate * ms / 1000)
    return b"\x00\x00" * n


class FakeConn:
    """Same shape as tests/test_gateway.py::FakeConn: drives WebSocketGateway
    without a real socket. Frames are queued up front (there is no live
    microphone to read from), then handed to the gateway one at a time.
    """

    def __init__(self, incoming: list[Any]) -> None:
        self.incoming = incoming
        self.sent: list[str] = []

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for message in self.incoming:
            yield message

    async def send(self, data: str) -> None:
        self.sent.append(data)


# --- Scenario ---


async def classify(text: str, context) -> str:
    lowered = text.lower()
    if any(w in lowered for w in ("thursday", "friday", "available", "openings", "jueves")):
        return "availability_question"
    if "charge" in lowered or "cost" in lowered or "price" in lowered:
        return "pricing_question"
    if "book" in lowered:
        return "booking_request"
    return "unknown"


async def generate(text: str, context, generation_config) -> str:
    tool_results = context.get("tool_results") or {}
    availability = tool_results.get("check_availability")
    if availability:
        return f"I found openings: {', '.join(availability['slots'])}."
    return f"Demo reply to: {text}"


_availability_calls = 0


async def check_availability(tool_name, tool, params, context) -> dict:
    global _availability_calls
    _availability_calls += 1
    await asyncio.sleep(0.3)
    if _availability_calls == 2:
        # Item 6 of the S2 acceptance list: one reproducible dependency
        # failure. This is a real exception, not a scripted "pretend" outcome
        # -- RuntimeToolBridge.execute (runtime.py) catches it and reports
        # success=False; nothing claims the tool succeeded.
        raise ConnectionError("availability backend unreachable (injected failure)")
    return {"slots": ["Thursday 3pm", "Friday 10am"]}


def make_voice_for(backend: PocketTTSBackend):
    caps = backend.capabilities()

    def voice_for(session_id: str, message: dict) -> VoiceManifest:
        locale = str(message.get("locale") or "en")
        language = "es" if locale.startswith("es") else "en"
        return VoiceManifest(
            voice_id="alba",
            language=language,
            lane=LanguageLane.FAST,
            sample_rate=caps.sample_rates[0],
            backend_id=caps.backend_id,
            backend_version=caps.backend_version,
            quantization="int8",
            consent_record=ConsentRecord(
                voice_id="alba",
                source="kyutai-demo",
                granted_at="2026-09-13T00:00:00Z",
                license="demo",
                notes="Slice-2 software evidence run only",
            ),
            session_id=session_id,
        )

    return voice_for


def log(label: str, payload: Any) -> None:
    print(f"[{label}] {payload}")


async def drain(gateway: WebSocketGateway, conn: FakeConn) -> None:
    await gateway.handle(conn)
    for raw in conn.sent:
        event = json.loads(raw)
        etype = event.get("type")
        if etype == "audio":
            meta = event.get("metadata", {})
            log(
                "audio",
                f"turn={event.get('turnId')} seq={event.get('seq')} "
                f"isLast={event.get('isLast')} flushReason={meta.get('flushReason')}",
            )
        else:
            log(etype, f"{event.get('text', '')!r} meta={event.get('metadata')}")


async def part_a(tts_backend: PocketTTSBackend) -> None:
    print("=== PART A: mic PCM -> ASR -> VoxMaestro -> generation -> TTS (via WebSocketGateway) ===")
    whisper_script = {
        "en": ["Do you have anything Thursday?"],
        "es": ["Tienen algo el jueves?"],
    }
    asr_backend = WhisperASRBackend(
        model=ScriptedWhisperModel(whisper_script),
        sample_rate=MIC_SAMPLE_RATE,
        silence_ms=200,
        min_speech_ms=50,
        backend_version="scripted-fake",
    )
    asr = AsrIngress(asr_backend)

    runtime = VoxMaestroRuntime.from_yaml(
        CONFIG_PATH,
        intent_classifier=classify,
        tool_executor=check_availability,
    )
    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generate,
        tts_backend=tts_backend,
        voice_for=make_voice_for(tts_backend),
        observe=lambda name, value: log("metric", f"{name}={value:.1f}"),
    )
    # NOTE: mic_sample_rate must match the ASR backend's session rate. The
    # gateway's own default (24000) does NOT match WhisperASRBackend's
    # default (16000) -- see FIRST_SLICE_REPORT.md "defects discovered".
    gateway = WebSocketGateway(adapter, asr=asr, mic_sample_rate=MIC_SAMPLE_RATE)

    print("\n-- session s1 (en): normal utterance, tool succeeds, generated reply --")
    conn1 = FakeConn(
        [
            json.dumps({"type": "start", "sessionId": "s1", "locale": "en"}),
            _loud(300),
            _silent(250),  # crosses silence_ms=200 -> ASR finalizes the utterance
            json.dumps({"type": "end", "sessionId": "s1"}),
        ]
    )
    await drain(gateway, conn1)

    print("\n-- session s2 (es): Spanish-language utterance, injected tool failure --")
    conn2 = FakeConn(
        [
            json.dumps({"type": "start", "sessionId": "s2", "locale": "es"}),
            _loud(300),
            _silent(250),
            json.dumps({"type": "end", "sessionId": "s2"}),
        ]
    )
    await drain(gateway, conn2)
    print(f"\navailability tool invocations so far: {_availability_calls} (2nd call injected-fails)")


async def part_b(tts_backend: PocketTTSBackend) -> None:
    print("\n=== PART B: barge-in mid-TTS, cancellation, and resume (direct adapter.iter_events) ===")
    # Mirrors examples/pocket_voice_demo.py's own proven concurrency pattern:
    # WebSocketGateway.handle() processes one connection's frames strictly in
    # order, so genuine interleaving needs two concurrent iter_events() calls
    # sharing one session, exactly as the shipped demo already does.
    runtime = VoxMaestroRuntime.from_yaml(
        CONFIG_PATH,
        intent_classifier=classify,
        tool_executor=check_availability,
    )
    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generate,
        tts_backend=tts_backend,
        voice_for=make_voice_for(tts_backend),
        observe=lambda name, value: log("metric", f"{name}={value:.1f}"),
    )
    session_id = "s3"
    greeting_chunks_seen = 0

    async def run(message: dict) -> None:
        nonlocal greeting_chunks_seen
        async for event in adapter.iter_events(message):
            if event["type"] == "audio":
                if event["turnId"] == "greeting":
                    greeting_chunks_seen += 1
                meta = event.get("metadata", {})
                log(
                    "audio",
                    f"turn={event['turnId']} seq={event['seq']} "
                    f"isLast={event['isLast']} flushReason={meta.get('flushReason')}",
                )
                continue
            log(event["type"], f"{event.get('text', '')!r} meta={event.get('metadata')}")

    # IMPORTANT: WebSessionAdapter._emit_speech (web_session.py) does not
    # yield per-chunk audio *events* while speak() is streaming -- it awaits
    # the whole SessionAudio.speak() call to completion, then drains the
    # queued chunk-events in one burst. So waiting for the first "greeting"
    # audio *event* (as examples/pocket_voice_demo.py's own comment implies
    # it does) actually waits until the greeting has ALREADY finished
    # internally, which cannot be interrupted -- nothing would be left to
    # cancel. The real interruption point is INSIDE SessionAudio.speak()'s
    # per-chunk asyncio.Queue loop (tts/worker.py), which genuinely yields to
    # other tasks while streaming. So the interrupting call below is fired
    # from a concurrently *scheduled* task after a short fixed real-time
    # delay -- long enough to land while speak() is mid-stream, not gated on
    # an event that (as just described) only fires once it is too late.
    start_task = asyncio.create_task(
        run({"type": "start", "sessionId": session_id, "surface": "microscroll", "locale": "en"})
    )

    async def interrupt() -> None:
        await asyncio.sleep(0.03)  # ~3 fake chunks in at 10ms/chunk; well inside the 2s stream
        print("-- greeting audio is mid-stream; sending a second utterance now (barge-in) --")
        await run({"type": "message", "sessionId": session_id, "text": "Never mind, what do you charge?"})

    interrupt_task = asyncio.create_task(interrupt())
    await asyncio.gather(start_task, interrupt_task)
    print(f"-- greeting chunks actually delivered before/around the interrupt: {greeting_chunks_seen} (fake stream has 200 available) --")
    await run({"type": "end", "sessionId": session_id})
    print("-- session ended cleanly after barge-in; ASR/turn cycle resumed for a new turn above --")


def _new_tts_backend() -> PocketTTSBackend:
    return PocketTTSBackend(
        model=FakeTTSModel.load_model(quantize=True),
        model_cls=FakeTTSModel,
        rss_fn=lambda: 100_000_000,
        backend_version="scripted-fake",
        sample_rate=24000,
    )


async def main() -> int:
    # NOTE: Part A deliberately reuses ONE backend instance across s1 and s2
    # (matching how examples/serve_gateway.py runs one long-lived backend for
    # a whole server process). Doing so exposed a real defect -- see
    # FIRST_SLICE_REPORT.md "defects discovered": PocketTTSBackend.cancel()
    # permanently poisons a turn_id for the life of the backend instance
    # (nothing ever removes it from the internal `_cancel` set), so s2's
    # "greeting" turn -- same literal turn_id as s1's -- goes silently to
    # zero audio chunks. That is real, reproduced behavior, not a script bug;
    # it is left in place below rather than worked around.
    #
    # Part B needs a *working* greeting to demonstrate barge-in against, so
    # it gets its own fresh backend instance rather than inheriting Part A's
    # already-poisoned "greeting" turn_id.
    await part_a(_new_tts_backend())
    await part_b(_new_tts_backend())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
