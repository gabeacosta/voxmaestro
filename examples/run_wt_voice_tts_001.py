"""Run WT-VOICE-TTS-001 Pocket lanes on a real voice host.

This is intentionally a manual/self-hosted benchmark. It imports pocket-tts and
executes the frozen VoxMaestro TTS contract; normal CI never loads model weights.

Example:
    python examples/run_wt_voice_tts_001.py --language en --quantize --sessions 1 --runs 3

The executor does NOT claim acoustic session-crosstalk evidence. Until a real
cross-talk detector is supplied, the emitted lane is marked evidence-incomplete
and therefore adjudicates TEST_INVALID. Contract-level session binding remains a
separate invariant and is already tested elsewhere.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

import yaml

from voxmaestro.tts.contract import ConsentRecord, LanguageLane, SynthesizeRequest, VoiceManifest
from voxmaestro.tts.measurement import TTSRunObservation, aggregate_lane, measure_request
from voxmaestro.tts.pocket import PocketTTSBackend
from voxmaestro.tts.qualification import qualify_tts_lane

CORPUS_PATH = Path(__file__).parent.parent / "docs" / "wt" / "tts_003_corpus.yaml"
DEFAULT_OUT = Path("evidence/wt-voice-tts-001")


def _duration_from_float32_pcm(pcm: bytes, sample_rate: int) -> float:
    """Pocket-specific duration decoder for raw float32 mono PCM.

    This assumption belongs here, not in the provider-neutral measurement layer.
    Pocket's current adapter converts model stream tensors directly to float32
    bytes. Reject malformed buffers rather than guessing.
    """

    if sample_rate <= 0:
        raise ValueError("sample_rate must be > 0")
    if not pcm or len(pcm) % 4:
        raise ValueError("Pocket PCM must be non-empty float32-aligned bytes")
    return (len(pcm) // 4) / float(sample_rate)


def _voice(
    backend: PocketTTSBackend,
    *,
    session_id: str,
    language: str,
    voice_id: str,
    quantization: str,
) -> VoiceManifest:
    caps = backend.capabilities()
    return VoiceManifest(
        voice_id=voice_id,
        language=language,
        lane=LanguageLane.FAST,
        sample_rate=caps.sample_rates[0],
        backend_id=caps.backend_id,
        backend_version=caps.backend_version,
        quantization=quantization,
        consent_record=ConsentRecord(
            voice_id=voice_id,
            source="kyutai-demo",
            granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            license="demo",
            notes="WT-VOICE-TTS-001 benchmark only",
        ),
        session_id=session_id,
    )


def _reference_duration(
    backend: PocketTTSBackend,
    req: SynthesizeRequest,
) -> float:
    """Untimed Pocket-aware duration pass used only to decode rendered length."""

    pcm = b"".join(chunk.pcm for chunk in backend.synthesize(req))
    return _duration_from_float32_pcm(pcm, req.voice.sample_rate)


async def _measure_composite(
    backend: PocketTTSBackend,
    req: SynthesizeRequest,
    *,
    audio_duration_s: float,
) -> TTSRunObservation:
    """Combine normal latency/RTF with a separate interruption probe."""

    normal = await measure_request(
        backend,
        req,
        audio_duration_s=audio_duration_s,
        cancel_after_first_chunk=False,
    )
    cancel_req = replace(req, turn_id=f"{req.turn_id}-cancel")
    interrupted = await measure_request(
        backend,
        cancel_req,
        audio_duration_s=audio_duration_s,
        cancel_after_first_chunk=True,
    )
    return TTSRunObservation(
        success=normal.success and interrupted.success,
        first_chunk_ms=normal.first_chunk_ms,
        synthesize_ms=normal.synthesize_ms,
        audio_duration_s=normal.audio_duration_s,
        cancel_to_silence_ms=interrupted.cancel_to_silence_ms,
        audio_underruns=normal.audio_underruns + interrupted.audio_underruns,
        stale_chunks_after_cancel=interrupted.stale_chunks_after_cancel,
        # Acoustic cross-talk is deliberately not inferred from handle binding.
        session_crosstalk_events=0,
    )


async def run_lane(args: argparse.Namespace) -> dict:
    corpus = yaml.safe_load(CORPUS_PATH.read_text())
    utterances = [u for u in corpus["utterances"] if u["language"] == args.language]
    if not utterances:
        raise ValueError(f"no corpus utterances for language {args.language!r}")

    backend = PocketTTSBackend(language=args.language, quantize=args.quantize)
    caps = backend.capabilities()
    quantization = "int8" if args.quantize else "fp32"
    sessions: list[tuple[str, VoiceManifest]] = []
    try:
        for index in range(args.sessions):
            session_id = f"wt-tts-{args.language}-{quantization}-s{index + 1}"
            voice = _voice(
                backend,
                session_id=session_id,
                language=args.language,
                voice_id=args.voice,
                quantization=quantization,
            )
            backend.open_session(session_id, voice)
            sessions.append((session_id, voice))

        observations: list[TTSRunObservation] = []
        raw_runs: list[dict] = []
        for run_index in range(args.runs):
            utterance = utterances[run_index % len(utterances)]
            requests = []
            for session_index, (session_id, voice) in enumerate(sessions):
                turn_id = f"r{run_index + 1}-s{session_index + 1}"
                requests.append(
                    SynthesizeRequest(
                        text=utterance["text"],
                        turn_id=turn_id,
                        session_id=session_id,
                        voice=voice,
                        language=args.language,
                    )
                )

            # One untimed duration decode per request. This uses the Pocket-specific
            # float32 representation and never contaminates provider-neutral code.
            durations = [_reference_duration(backend, req) for req in requests]
            measured = await asyncio.gather(
                *(
                    _measure_composite(backend, req, audio_duration_s=duration)
                    for req, duration in zip(requests, durations)
                )
            )
            observations.extend(measured)
            raw_runs.extend(asdict(item) for item in measured)

        lane = aggregate_lane(
            backend=caps.backend_id,
            quantization=quantization,
            sessions=args.sessions,
            language=args.language,
            observations=observations,
        )
        # Admission/session binding is verified elsewhere, but acoustic cross-talk
        # has not been measured in this executor. Fail closed as TEST_INVALID.
        lane = replace(lane, evidence_complete=False)
        qualification = qualify_tts_lane(lane)
        return {
            "schema": "wt-voice-tts-001.evidence.v1",
            "backend_version": caps.backend_version,
            "host_lane": "self-hosted-voice",
            "acoustic_crosstalk_measured": False,
            "lane": asdict(lane),
            "qualification": {
                "verdict": qualification.verdict.value,
                "findings": [asdict(item) for item in qualification.findings],
            },
            "runs": raw_runs,
        }
    finally:
        for session_id, _ in sessions:
            backend.close_session(session_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", choices=("en", "es"), required=True)
    parser.add_argument("--sessions", type=int, choices=(1, 2, 4, 8), required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--voice", default="alba")
    parser.add_argument("--quantize", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.runs < 3:
        parser.error("--runs must be >= 3 for the frozen promotion policy")
    return args


def main() -> int:
    args = parse_args()
    evidence = asyncio.run(run_lane(args))
    args.out.mkdir(parents=True, exist_ok=True)
    quantization = "int8" if args.quantize else "fp32"
    path = args.out / f"pocket-{quantization}-{args.language}-s{args.sessions}.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"evidence": str(path), "verdict": evidence["qualification"]["verdict"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
