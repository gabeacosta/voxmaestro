"""Live Pocket TTS demo over WebSessionAdapter (no WebSocket server).

Runs the microscroll landing config end to end: the greeting is spoken through
PocketTTSBackend -> SessionAudio, a slow availability tool plays spoken filler,
and the final reply is spoken. The user message is sent while the greeting is
still streaming, so greeting audio is cancelled (barge-in) and the reply wins.
PCM for each turn is appended to ./demo-audio/<session>-<turn>.pcm as float32
little-endian at the probed sample rate.

Requires: pip install pocket-tts (pulls torch). Not run in CI.

    python examples/pocket_voice_demo.py
    ffplay -f f32le -ar 24000 demo-audio/demo-t1.pcm
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from voxmaestro import VoxMaestroRuntime
from voxmaestro.integrations.web_session import WebSessionAdapter
from voxmaestro.tts.contract import ConsentRecord, LanguageLane, VoiceManifest
from voxmaestro.tts.pocket import PocketTTSBackend

CONFIG_PATH = Path(__file__).with_name("microscroll_landing.yaml")
OUT_DIR = Path("demo-audio")
VOICE_ID = "alba"  # Kyutai demo voice; use a licensed voice in production


def make_voice_for(backend: PocketTTSBackend):
    caps = backend.capabilities()

    def voice_for(session_id: str, message: dict) -> VoiceManifest:
        locale = str(message.get("locale") or "en")
        language = "es" if locale.startswith("es") else "en"
        return VoiceManifest(
            voice_id=VOICE_ID,
            language=language,
            lane=LanguageLane.FAST,
            sample_rate=caps.sample_rates[0],
            backend_id=caps.backend_id,
            backend_version=caps.backend_version,
            quantization=(
                "int8" if "int8" in caps.quantizations else caps.quantizations[0]
            ),
            consent_record=ConsentRecord(
                voice_id=VOICE_ID,
                source="kyutai-demo",
                granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                license="demo",
                notes="Demo only; replace with a consented production voice",
            ),
            session_id=session_id,
        )

    return voice_for


async def classify(text: str, context) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("thursday", "friday", "available", "open")):
        return "availability_question"
    if "book" in lowered or "schedule" in lowered:
        return "booking_request"
    if "price" in lowered or "cost" in lowered:
        return "pricing_question"
    return "unknown"


async def generate(text, context, generation_config) -> str:
    tool_results = context.get("tool_results") or {}
    availability = tool_results.get("check_availability")
    if availability:
        return f"I found openings: {', '.join(availability['slots'])}."
    return f"Demo reply to: {text}"


async def check_availability(tool_name, tool, params, context):
    await asyncio.sleep(1.2)  # slow enough to hear the spoken filler
    return {"slots": ["Thursday 3pm", "Friday 10am"]}


async def main() -> int:
    backend = PocketTTSBackend(language="en", quantize=True)
    caps = backend.capabilities()
    print(f"backend {caps.backend_id} {caps.backend_version} {caps.quantizations}\n")

    runtime = VoxMaestroRuntime.from_yaml(
        CONFIG_PATH,
        intent_classifier=classify,
        tool_executor=check_availability,
    )

    OUT_DIR.mkdir(exist_ok=True)
    metrics: list[tuple[str, float]] = []
    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=make_voice_for(backend),
        observe=lambda name, value: metrics.append((name, value)),
    )
    session_id = "demo"
    greeting_audio_started = asyncio.Event()

    async def run(message: dict) -> None:
        async for event in adapter.iter_events(message):
            if event["type"] == "audio":
                if event["turnId"] == "greeting":
                    greeting_audio_started.set()
                path = OUT_DIR / f"{session_id}-{event['turnId']}.pcm"
                with path.open("ab") as handle:
                    handle.write(event["pcm"])
                continue
            print(f"[{event['type']}] {event.get('text', '')}")

    start = asyncio.create_task(
        run(
            {
                "type": "start",
                "sessionId": session_id,
                "surface": "microscroll",
                "locale": "en",
            }
        )
    )
    await asyncio.wait_for(greeting_audio_started.wait(), timeout=30)
    await run(
        {
            "type": "message",
            "sessionId": session_id,
            "text": "Do you have anything Thursday?",
        }
    )
    await start
    await run({"type": "end", "sessionId": session_id})

    print("\nmetrics:")
    for name, value in metrics:
        print(f"  {name}: {value:.1f}")
    print(f"\nPCM under {OUT_DIR}/ (f32le @ {caps.sample_rates[0]} Hz)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
