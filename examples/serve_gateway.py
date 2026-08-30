"""Serve the microscroll landing session over a real WebSocket.

Text-only works with no optional deps. Add voice with:

    pip install websockets pocket-tts faster-whisper
    python examples/serve_gateway.py --voice --mic

Then point the browser client at ws://127.0.0.1:7780.
Intent/generation come from the kernel endpoints declared in
examples/microscroll_landing.yaml; tool calls POST to the tool's own
endpoint. Nothing is simulated: if a dependency is down, the client sees the
real failure event.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.request
from pathlib import Path

from voxmaestro import VoxMaestroRuntime
from voxmaestro.conductor import SchemaLoader
from voxmaestro.fleet import fleet_from_config
from voxmaestro.integrations.gateway import WebSocketGateway
from voxmaestro.integrations.web_session import WebSessionAdapter

CONFIG_PATH = Path(__file__).with_name("microscroll_landing.yaml")
VOICE_ID = "alba"  # Kyutai demo voice; use a licensed voice in production


async def http_tool_executor(tool_name, tool, params, context):
    request = urllib.request.Request(
        tool["endpoint"],
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def _call():
        with urllib.request.urlopen(
            request, timeout=tool.get("timeout_ms", 3000) / 1000
        ) as response:
            return json.loads(response.read())

    return await asyncio.to_thread(_call)


def make_voice_for(backend):
    from voxmaestro.tts.contract import ConsentRecord, LanguageLane, VoiceManifest

    caps = backend.capabilities()

    def voice_for(session_id: str, message: dict):
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
                notes="Demo server only; replace with a consented voice",
            ),
            session_id=session_id,
        )

    return voice_for


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7780)
    parser.add_argument("--voice", action="store_true", help="enable Pocket TTS")
    parser.add_argument("--mic", action="store_true", help="enable Whisper ASR")
    args = parser.parse_args()

    config = SchemaLoader.load(CONFIG_PATH)
    classifier, generator = fleet_from_config(config)
    runtime = VoxMaestroRuntime(
        config,
        intent_classifier=classifier,
        tool_executor=http_tool_executor,
    )

    tts_backend = None
    voice_for = None
    if args.voice:
        from voxmaestro.tts.pocket import PocketTTSBackend

        tts_backend = PocketTTSBackend(language="en", quantize=True)
        voice_for = make_voice_for(tts_backend)

    asr = None
    if args.mic:
        from voxmaestro.asr import AsrIngress
        from voxmaestro.asr_whisper import WhisperASRBackend

        asr = AsrIngress(WhisperASRBackend())

    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generator,
        tts_backend=tts_backend,
        voice_for=voice_for,
        observe=lambda name, value: print(f"[metric] {name}={value:.1f}"),
    )
    gateway = WebSocketGateway(adapter, asr=asr)

    import websockets

    async with websockets.serve(gateway.handle, args.host, args.port):
        print(f"listening on ws://{args.host}:{args.port}")
        await asyncio.Future()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
