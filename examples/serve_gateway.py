"""Serve the microscroll landing session over a real WebSocket.

Text-only works with no optional deps. Local-complete voice path:

    pip install websockets faster-whisper
    # Kokoro must already be healthy on :8880 (com.clue.kokoro-tts)
    python examples/serve_gateway.py --voice --mic --tts kokoro

Optional Spanish leaf (does not replace Kokoro):

    pip install pocket-tts
    python examples/serve_gateway.py --voice --mic --tts auto

Then open examples/local_voice_ui.html (or --serve-ui) at the printed URL.
Intent/generation come from the kernel endpoints declared in the YAML config;
consequential tools require an external Veynit grant (see governance.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.request
from collections.abc import Mapping
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from voxmaestro import VoxMaestroRuntime
from voxmaestro.conductor import SchemaLoader
from voxmaestro.fleet import fleet_from_config
from voxmaestro.governance import GovernedToolExecutor
from voxmaestro.integrations.gateway import WebSocketGateway
from voxmaestro.integrations.web_session import WebSessionAdapter

CONFIG_PATH = Path(__file__).with_name("microscroll_landing.yaml")
POCKET_VOICE_ID = "alba"  # Kyutai demo voice; use a licensed voice in production
KOKORO_VOICE_ID = "bm_george"
CONSEQUENTIAL_TOOLS = frozenset({"check_availability", "request_booking"})


def build_tool_payload(tool, params, context):
    """Build an HTTP tool payload from explicit, trusted config mappings."""
    payload = dict(params)
    request_context = tool.get("request_context")
    if not isinstance(request_context, Mapping):
        return payload

    query_key = request_context.get("latest_caller_text_as")
    if query_key is not None:
        if not isinstance(query_key, str) or not query_key:
            raise ValueError("latest_caller_text_as must be a non-empty string")
        latest = next(
            (
                turn.get("content")
                for turn in reversed(context.conversation_history)
                if turn.get("role") == "caller"
            ),
            None,
        )
        if not isinstance(latest, str) or not latest.strip():
            raise ValueError("latest caller text is unavailable")
        payload[query_key] = latest

    language_key = request_context.get("session_language_as")
    if language_key is not None:
        if not isinstance(language_key, str) or not language_key:
            raise ValueError("session_language_as must be a non-empty string")
        locale = context.metadata.get("locale") or request_context.get("default_language")
        normalized = str(locale or "").lower().replace("_", "-")
        if normalized == "es" or normalized.startswith("es-"):
            language = "es"
        elif normalized == "en" or normalized.startswith("en-"):
            language = "en"
        else:
            raise ValueError("session language must resolve to en or es")
        payload[language_key] = language
    return payload


async def http_tool_executor(tool_name, tool, params, context):
    payload = build_tool_payload(tool, params, context)
    request = urllib.request.Request(
        tool["endpoint"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def _call():
        with urllib.request.urlopen(
            request, timeout=tool.get("timeout_ms", 3000) / 1000
        ) as response:
            return json.loads(response.read())

    return await asyncio.to_thread(_call)


def make_voice_for_leaf(backend, *, voice_id: str, source: str, license_name: str):
    """Bind VoiceManifest fields to one leaf TTSBackend (not the router)."""
    from voxmaestro.tts.contract import ConsentRecord, LanguageLane, VoiceManifest

    caps = backend.capabilities()
    language = caps.languages[0].code

    def voice_for(session_id: str, message: dict):
        locale = str(message.get("locale") or language)
        requested = "es" if locale.lower().startswith("es") else "en"
        if requested != language:
            raise ValueError(
                f"leaf backend {caps.backend_id} only serves {language!r}; "
                f"got locale={locale!r}"
            )
        return VoiceManifest(
            voice_id=voice_id,
            language=language,
            lane=LanguageLane.FAST,
            sample_rate=caps.sample_rates[0],
            backend_id=caps.backend_id,
            backend_version=caps.backend_version,
            quantization=caps.quantizations[0],
            consent_record=ConsentRecord(
                voice_id=voice_id,
                source=source,
                granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                license=license_name,
                notes="Local Voice Maestro demo; replace with consented production voice",
            ),
            session_id=session_id,
        )

    return voice_for


def make_voice_for_router(backends: dict):
    """Pick the leaf VoiceManifest by session locale."""
    from voxmaestro.tts.contract import ConsentRecord, LanguageLane, VoiceManifest

    leaf_voice = {
        "en": (KOKORO_VOICE_ID, "kokoro-onnx-local", "Apache-2.0"),
        "es": (POCKET_VOICE_ID, "kyutai-demo", "demo"),
    }

    def voice_for(session_id: str, message: dict):
        locale = str(message.get("locale") or "en")
        language = "es" if locale.lower().startswith("es") else "en"
        backend = backends.get(language)
        if backend is None:
            raise ValueError(f"no TTS backend for language {language!r}")
        caps = backend.capabilities()
        voice_id, source, license_name = leaf_voice[language]
        return VoiceManifest(
            voice_id=voice_id,
            language=language,
            lane=LanguageLane.FAST,
            sample_rate=caps.sample_rates[0],
            backend_id=caps.backend_id,
            backend_version=caps.backend_version,
            quantization=caps.quantizations[0],
            consent_record=ConsentRecord(
                voice_id=voice_id,
                source=source,
                granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                license=license_name,
                notes="Routed local TTS; Kokoro preferred for EN",
            ),
            session_id=session_id,
        )

    return voice_for


def build_tts(tts_mode: str):
    """Construct TTS backend + voice_for. Pocket is never removed when unused."""
    if tts_mode == "pocket":
        from voxmaestro.tts.pocket import PocketTTSBackend

        backend = PocketTTSBackend(language="en", quantize=True)
        return backend, make_voice_for_leaf(
            backend,
            voice_id=POCKET_VOICE_ID,
            source="kyutai-demo",
            license_name="demo",
        )

    if tts_mode == "kokoro":
        from voxmaestro.tts.kokoro import KokoroTTSBackend

        backend = KokoroTTSBackend(default_voice=KOKORO_VOICE_ID)
        return backend, make_voice_for_leaf(
            backend,
            voice_id=KOKORO_VOICE_ID,
            source="kokoro-onnx-local",
            license_name="Apache-2.0",
        )

    if tts_mode == "auto":
        from voxmaestro.tts.kokoro import KokoroTTSBackend
        from voxmaestro.tts.router import LanguageRoutedTTSBackend

        leaves: dict = {"en": KokoroTTSBackend(default_voice=KOKORO_VOICE_ID)}
        try:
            from voxmaestro.tts.pocket import PocketTTSBackend

            leaves["es"] = PocketTTSBackend(language="es", quantize=True)
        except Exception as exc:  # noqa: BLE001 — ES leaf optional
            print(f"[tts] ES Pocket leaf unavailable ({exc}); EN Kokoro only")
        if len(leaves) == 1:
            backend = leaves["en"]
            return backend, make_voice_for_leaf(
                backend,
                voice_id=KOKORO_VOICE_ID,
                source="kokoro-onnx-local",
                license_name="Apache-2.0",
            )
        router = LanguageRoutedTTSBackend(leaves)
        return router, make_voice_for_router(leaves)

    raise ValueError(f"unknown --tts mode: {tts_mode!r}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7780)
    parser.add_argument("--voice", action="store_true", help="enable TTS")
    parser.add_argument(
        "--tts",
        choices=("kokoro", "pocket", "auto"),
        default="kokoro",
        help="TTS backend (default: kokoro). auto = Kokoro EN + Pocket ES when present",
    )
    parser.add_argument("--mic", action="store_true", help="enable Whisper ASR")
    parser.add_argument(
        "--whisper-model",
        default="base",
        help="faster-whisper model size (use multilingual for EN+ES; default: base)",
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="runtime YAML config")
    parser.add_argument(
        "--fleet",
        choices=("yaml", "local-ollama"),
        default="local-ollama",
        help="intent/generation fleet (default: local-ollama on :11434)",
    )
    parser.add_argument("--ollama-model", default="qwen3:latest")
    parser.add_argument(
        "--dry-run-tools",
        action="store_true",
        help="simulate tool results without external dispatch (speech path still live)",
    )
    parser.add_argument(
        "--serve-ui",
        action="store_true",
        help="also serve examples/local_voice_ui.html on --ui-port",
    )
    parser.add_argument("--ui-port", type=int, default=7781)
    parser.add_argument(
        "--allow-ungoverned-tools",
        action="store_true",
        help="DANGEROUS: skip Veynit grant gate (local debug only)",
    )
    return parser.parse_args(argv)


def _start_ui_server(host: str, port: int) -> None:
    examples_dir = Path(__file__).resolve().parent

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(examples_dir), **kwargs)

        def log_message(self, format, *args):  # noqa: A003
            return

    server = ThreadingHTTPServer((host, port), Handler)
    thread = Thread(target=server.serve_forever, name="voice-ui-http", daemon=True)
    thread.start()
    print(f"UI http://{host}:{port}/local_voice_ui.html")


async def main() -> int:
    args = parse_args()
    config = SchemaLoader.load(args.config)

    if args.fleet == "local-ollama":
        import sys

        examples_dir = str(Path(__file__).resolve().parent)
        if examples_dir not in sys.path:
            sys.path.insert(0, examples_dir)
        from local_ollama_adapters import make_ollama_classifier, make_ollama_generator

        classifier = make_ollama_classifier(model=args.ollama_model)
        generator = make_ollama_generator(model=args.ollama_model)
        print(f"fleet local-ollama model={args.ollama_model}")
    else:
        classifier, generator = fleet_from_config(config)

    tool_executor = http_tool_executor
    if not args.allow_ungoverned_tools and not args.dry_run_tools:
        tool_executor = GovernedToolExecutor(
            http_tool_executor,
            consequential_tools=CONSEQUENTIAL_TOOLS,
        )

    runtime = VoxMaestroRuntime(
        config,
        intent_classifier=classifier,
        tool_executor=tool_executor,
        dry_run=args.dry_run_tools,
    )

    tts_backend = None
    voice_for = None
    if args.voice:
        tts_backend, voice_for = build_tts(args.tts)
        caps = tts_backend.capabilities()
        print(
            f"tts {caps.backend_id} {caps.backend_version} "
            f"langs={[lang.code for lang in caps.languages]}"
        )

    asr = None
    mic_sample_rate = 24000
    if args.mic:
        from voxmaestro.asr import AsrIngress
        from voxmaestro.asr_whisper import WhisperASRBackend

        whisper_backend = WhisperASRBackend(model_size=args.whisper_model)
        # Gateway mic rate must match ASR session rate (not TTS rate).
        mic_sample_rate = whisper_backend.capabilities().sample_rates[0]
        asr = AsrIngress(whisper_backend)
        print(f"asr {whisper_backend.backend_id} rate={mic_sample_rate}")

    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generator,
        tts_backend=tts_backend,
        voice_for=voice_for,
        observe=lambda name, value: print(f"[metric] {name}={value:.1f}"),
    )
    gateway = WebSocketGateway(adapter, asr=asr, mic_sample_rate=mic_sample_rate)

    if args.serve_ui:
        _start_ui_server(args.host, args.ui_port)

    import websockets

    async with websockets.serve(gateway.handle, args.host, args.port):
        print(f"listening on ws://{args.host}:{args.port}")
        await asyncio.Future()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
