"""WT-VOICESTUDIO-001-B: real hardware audio timing, not an inferred live-voice verdict.

Runs an existing, pre-warmed VoiceStudio service OR the native PocketTTS adapter.
No model installation, proxy, remote endpoint, auto-fallback, or fake audio.
Neither complete-WAV latency nor a valid WAV proves first-audio, barge-in,
memory peak, voice quality, or cross-session isolation.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from voxmaestro.tts.contract import ConsentRecord, LanguageLane, SynthesizeRequest, VoiceManifest
from voxmaestro.tts.pocket import PocketTTSBackend

CORPUS = Path(__file__).resolve().parents[1] / "docs/wt/tts_003_corpus.yaml"
REFERENCE_SHA = "06c6e077f0fc35149efefc3561e9be5ae835d916"
MAX_WAV_BYTES = 30_000_000


def local_endpoint(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise ValueError("VoiceStudio endpoint must be bare loopback HTTP with explicit port")
    return url.rstrip("/")


def wav_duration(raw: bytes) -> float:
    if not raw or len(raw) > MAX_WAV_BYTES:
        raise ValueError("empty or excessive WAV payload")
    try:
        with wave.open(io.BytesIO(raw), "rb") as stream:
            if stream.getcomptype() != "NONE":
                raise ValueError("compressed WAV is unsupported")
            frames, rate = stream.getnframes(), stream.getframerate()
            if frames <= 0 or rate <= 0 or stream.getnchannels() <= 0:
                raise ValueError("WAV has no audio")
            expected = frames * stream.getnchannels() * stream.getsampwidth()
            if len(stream.readframes(frames)) != expected:
                raise ValueError("truncated WAV")
            return frames / rate
    except (EOFError, wave.Error) as exc:
        raise ValueError("malformed WAV") from exc


def p95(values: list[float]) -> float:
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("percentile requires finite nonnegative observations")
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def corpus_samples(language: str, runs: int) -> tuple[list[dict[str, str]], str]:
    data = CORPUS.read_bytes()
    samples = [x for x in yaml.safe_load(data)["utterances"] if x["language"] == language]
    if len(samples) < 3 or runs < 3:
        raise ValueError("at least three language samples and three runs required")
    return [samples[i % len(samples)] for i in range(runs)], hashlib.sha256(data).hexdigest()


def m4_attestation() -> dict[str, Any]:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("requires the physical Apple Silicon macOS runner")
    report = subprocess.run(
        ["system_profiler", "SPHardwareDataType", "-json"],
        capture_output=True, text=True, check=True, timeout=25,
    )
    devices = json.loads(report.stdout).get("SPHardwareDataType", [])
    if len(devices) != 1 or "M4" not in str(devices[0].get("chip_type", "")):
        raise RuntimeError("physical Apple M4 chip not attested")
    memory = subprocess.run(
        ["sysctl", "-n", "hw.memsize"], check=True, capture_output=True, text=True, timeout=10,
    )
    size = int(memory.stdout.strip())
    if not 15_000_000_000 <= size <= 18_000_000_000:
        raise RuntimeError("16 GB unified memory not attested")
    return {"system": "Darwin", "arch": "arm64",
            "chip": devices[0]["chip_type"], "memory_bytes": size}


def voice_studio_wav(url: str, model: str, voice: str, language: str,
                     sentence: str, timeout: float) -> bytes:
    payload = json.dumps({
        "model": model, "voice": voice, "input": sentence, "language": language,
        "response_format": "wav",
    }).encode()
    headers = {"Content-Type": "application/json", "Accept": "audio/wav"}
    if os.environ.get("OMNIVOICE_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["OMNIVOICE_API_KEY"]
    request = urllib.request.Request(
        url + "/v1/audio/speech", payload, headers, method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("unexpected speech API response")
        audio = response.read(MAX_WAV_BYTES + 1)
    wav_duration(audio)
    return audio


def pocket_open(language: str, name: str) -> tuple[PocketTTSBackend, VoiceManifest]:
    backend = PocketTTSBackend(language=language, quantize=True)
    caps = backend.capabilities()
    voice = VoiceManifest(
        voice_id=name, language=language, lane=LanguageLane.FAST,
        sample_rate=caps.sample_rates[0], backend_id=caps.backend_id,
        backend_version=caps.backend_version, quantization="int8",
        consent_record=ConsentRecord(
            voice_id=name, source="kyutai-demo", granted_at="2026-10-08T00:00:00Z",
            license="benchmark fixture; commercial rights not established",
        ),
        session_id="wt-voicestudio-001-b-" + language,
    )
    backend.open_session(voice.session_id, voice)
    return backend, voice


def pocket_pcm(backend: PocketTTSBackend, voice: VoiceManifest,
               sentence: str, turn: str) -> tuple[bytes, float]:
    request = SynthesizeRequest(
        text=sentence, turn_id=turn, session_id=voice.session_id,
        voice=voice, language=voice.language,
    )
    audio = b"".join(chunk.pcm for chunk in backend.synthesize(request))
    if not audio or len(audio) % 4:
        raise ValueError("invalid Pocket float32 mono PCM")
    return audio, len(audio) / (4 * voice.sample_rate)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.runs < 3 or args.warmups < 1 or args.timeout <= 0:
        raise ValueError("require runs>=3, warmups>=1 and positive timeout")
    hardware = m4_attestation()
    samples, corpus_hash = corpus_samples(args.language, args.runs)
    url = local_endpoint(args.endpoint) if args.backend == "voicestudio" else None
    result: dict[str, Any] = {
        "schema": "wt-voicestudio-001-b.hardware.v1",
        "qualification": "RECORDED_NOT_PROMOTABLE",
        "reason": "no matched voice, live first audio, cancellation or acoustic validation",
        "backend": args.backend, "language": args.language, "voice": args.voice,
        "model": args.model if args.backend == "voicestudio" else "pocket-python int8",
        "hardware": hardware, "corpus_sha256": corpus_hash,
        "source_reference_sha": REFERENCE_SHA,
        "running_voicestudio_build_attested": False,
        "memory_peak_measured": False,
        "measure": "warm wall-clock complete-audio; NOT first audio",
        "warmups": args.warmups, "rows": [],
    }
    backend, voice = (pocket_open(args.language, args.voice) if args.backend == "pocket"
                      else (None, None))
    try:
        for index in range(args.warmups + args.runs):
            sample = samples[0] if index < args.warmups else samples[index - args.warmups]
            start = time.perf_counter()
            if backend is not None and voice is not None:
                audio, duration = pocket_pcm(backend, voice, sample["text"], f"job-{index}")
            else:
                assert url is not None
                audio = voice_studio_wav(
                    url, args.model, args.voice, args.language, sample["text"], args.timeout,
                )
                duration = wav_duration(audio)
            elapsed_ms = (time.perf_counter() - start) * 1000
            if index >= args.warmups:
                result["rows"].append({
                    "utterance_id": sample["id"],
                    "audio_sha256": hashlib.sha256(audio).hexdigest(),
                    "duration_s": round(duration, 6),
                    "complete_ms": round(elapsed_ms, 3),
                    "rtf": round(elapsed_ms / (1000 * duration), 5),
                })
    finally:
        if backend is not None and voice is not None:
            backend.close_session(voice.session_id)
    result["complete_p95_ms"] = p95([x["complete_ms"] for x in result["rows"]])
    result["rtf_p95"] = p95([x["rtf"] for x in result["rows"]])
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", required=True, choices=("voicestudio", "pocket"))
    p.add_argument("--language", required=True, choices=("en", "es"))
    p.add_argument("--voice", required=True)
    p.add_argument("--model", default="omnivoice")
    p.add_argument("--endpoint", default="http://127.0.0.1:3900")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--warmups", type=int, default=1)
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    receipt: dict[str, Any] = {
        "schema": "wt-voicestudio-001-b.hardware.v1",
        "qualification": "TEST_INVALID", "backend": args.backend,
        "language": args.language, "reason": "hardware run incomplete",
    }
    try:
        receipt = run(args)
    except Exception as exc:  # noqa: BLE001 -- never fabricate measured audio
        receipt["reason"] = type(exc).__name__ + ": " + str(exc)[:300]
    receipt["recorded_at"] = datetime.now(timezone.utc).isoformat()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"qualification": receipt["qualification"], "receipt": str(args.out)}))
    return 0 if receipt["qualification"] == "RECORDED_NOT_PROMOTABLE" else 2


if __name__ == "__main__":
    sys.exit(main())
