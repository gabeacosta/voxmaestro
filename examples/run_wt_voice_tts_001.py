"""Run WT-VOICE-TTS-001 Pocket lanes on a real voice host.

This is intentionally a manual/self-hosted benchmark. It imports pocket-tts and
executes the frozen VoxMaestro TTS contract; normal CI never loads model weights.

Example:
    python examples/run_wt_voice_tts_001.py --language en --quantize --sessions 1 --runs 3
    # with real acoustic cross-talk evidence for sessions > 1 (needs faster-whisper):
    python examples/run_wt_voice_tts_001.py --language en --sessions 4 --runs 3 --use-whisper-crosstalk-check

Each concurrent session in a run is assigned a distinct corpus utterance.
With ``--use-whisper-crosstalk-check``, each session's captured audio is
transcribed and compared against every session's assignment
(``voxmaestro.tts.crosstalk.detect_crosstalk``): a session is flagged only
when its audio actually matches a *different* session's line meaningfully
better than its own, not merely from turn_id/handle bookkeeping. Without
that flag (the default), or for any run where a transcript comes back
inconclusive, or for the ``sessions=1`` lane (nothing to cross into), the
crosstalk aspect of that run's evidence is respectively left unmeasured
(sessions > 1) or is trivially satisfied (sessions == 1) -- either way,
never a fabricated "no crosstalk" claim.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Optional

import yaml

from voxmaestro.tts.contract import ConsentRecord, LanguageLane, SynthesizeRequest, VoiceManifest
from voxmaestro.tts.crosstalk import AcousticTranscriber, CrosstalkFinding, detect_crosstalk
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


def _synthesize_reference(
    backend: PocketTTSBackend,
    req: SynthesizeRequest,
) -> tuple[bytes, float]:
    """Untimed Pocket-aware pass used to decode rendered length and, when a
    crosstalk transcriber is configured, to supply the same audio for ASR
    verification -- one synthesis pass serves both, never two."""

    pcm = b"".join(chunk.pcm for chunk in backend.synthesize(req))
    return pcm, _duration_from_float32_pcm(pcm, req.voice.sample_rate)


async def _measure_composite(
    backend: PocketTTSBackend,
    req: SynthesizeRequest,
    *,
    audio_duration_s: float,
    crosstalk_events: int = 0,
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
        session_crosstalk_events=crosstalk_events,
    )


def _crosstalk_events_for_run(
    findings: tuple[CrosstalkFinding, ...],
) -> dict[str, int]:
    return {finding.session_id: (1 if finding.crosstalk else 0) for finding in findings}


async def run_lane(
    args: argparse.Namespace,
    *,
    transcriber: Optional[AcousticTranscriber] = None,
) -> dict:
    corpus = yaml.safe_load(CORPUS_PATH.read_text())
    utterances = [u for u in corpus["utterances"] if u["language"] == args.language]
    if not utterances:
        raise ValueError(f"no corpus utterances for language {args.language!r}")
    # Not fatal when sessions > len(utterances): those extra sessions
    # necessarily share an assigned line with an earlier session in the same
    # run, weakening (not eliminating) crosstalk detectability for that pair.

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

        if args.ready_file is not None:
            args.ready_file.parent.mkdir(parents=True, exist_ok=True)
            args.ready_file.write_text(
                json.dumps(
                    {
                        "schema": "wt-voice-load-ready.v1",
                        "backend": caps.backend_id,
                        "backend_version": caps.backend_version,
                        "language": args.language,
                        "sessions": args.sessions,
                        "quantization": quantization,
                        "acoustic_transcriber_loaded": transcriber is not None,
                        "ready_monotonic_s": time.monotonic(),
                    },
                    sort_keys=True,
                )
                + "\n"
            )

        observations: list[TTSRunObservation] = []
        raw_runs: list[dict] = []
        crosstalk_findings: list[dict] = []
        crosstalk_evidence_complete = True
        for run_index in range(args.runs):
            requests = []
            assignments: dict[str, str] = {}
            for session_index, (session_id, voice) in enumerate(sessions):
                turn_id = f"r{run_index + 1}-s{session_index + 1}"
                # Distinct utterance per concurrent session (not per run): if
                # every session spoke the same line, no transcript could ever
                # reveal which session's audio it actually came from.
                utterance = utterances[(run_index + session_index) % len(utterances)]
                assignments[session_id] = utterance["text"]
                requests.append(
                    SynthesizeRequest(
                        text=utterance["text"],
                        turn_id=turn_id,
                        session_id=session_id,
                        voice=voice,
                        language=args.language,
                    )
                )

            # One untimed synthesis decode per request, reused for both the
            # duration measurement and (when configured) the crosstalk
            # transcript -- this is Pocket-specific float32 handling and
            # never contaminates provider-neutral code.
            rendered = [_synthesize_reference(backend, req) for req in requests]

            crosstalk_events: dict[str, int] = dict.fromkeys(assignments, 0)
            if args.sessions > 1:
                if transcriber is None:
                    crosstalk_evidence_complete = False
                else:
                    transcripts = {
                        req.session_id: transcriber.transcribe(pcm, req.voice.sample_rate, args.language)
                        for req, (pcm, _duration) in zip(requests, rendered)
                    }
                    findings = detect_crosstalk(assignments, transcripts)
                    crosstalk_findings.extend(
                        {"run": run_index + 1, **asdict(finding)} for finding in findings
                    )
                    if any(finding.inconclusive for finding in findings):
                        crosstalk_evidence_complete = False
                    else:
                        crosstalk_events = _crosstalk_events_for_run(findings)

            measured = await asyncio.gather(
                *(
                    _measure_composite(
                        backend,
                        req,
                        audio_duration_s=duration,
                        crosstalk_events=crosstalk_events[req.session_id],
                    )
                    for req, (_pcm, duration) in zip(requests, rendered)
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
        # sessions == 1 has nothing to cross into, so crosstalk evidence is
        # trivially complete regardless of whether a transcriber was given.
        # For sessions > 1, only real, conclusive ASR evidence for every run
        # counts as complete -- never inferred from turn_id/handle bookkeeping.
        if args.sessions > 1 and not crosstalk_evidence_complete:
            lane = replace(lane, evidence_complete=False)
        qualification = qualify_tts_lane(lane)
        return {
            "schema": "wt-voice-tts-001.evidence.v1",
            "backend_version": caps.backend_version,
            "host_lane": "self-hosted-voice",
            "acoustic_crosstalk_measured": args.sessions > 1 and transcriber is not None,
            "acoustic_crosstalk_findings": crosstalk_findings,
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
    parser.add_argument(
        "--ready-file",
        type=Path,
        default=None,
        help=(
            "Optional readiness witness written only after the real TTS backend "
            "and requested acoustic transcriber are loaded and sessions are open."
        ),
    )
    parser.add_argument(
        "--use-whisper-crosstalk-check",
        action="store_true",
        help=(
            "For sessions > 1, transcribe each session's captured audio with "
            "faster-whisper and require it to be traceable to its own "
            "assigned utterance (see voxmaestro.tts.crosstalk). Without this "
            "flag, sessions > 1 lanes stay evidence-incomplete (TEST_INVALID) "
            "on the crosstalk aspect; sessions == 1 is unaffected either way."
        ),
    )
    args = parser.parse_args()
    if args.runs < 3:
        parser.error("--runs must be >= 3 for the frozen promotion policy")
    return args


def main() -> int:
    args = parse_args()
    transcriber = None
    if args.use_whisper_crosstalk_check:
        from voxmaestro.tts.crosstalk import WhisperAcousticTranscriber

        transcriber = WhisperAcousticTranscriber()
    evidence = asyncio.run(run_lane(args, transcriber=transcriber))
    args.out.mkdir(parents=True, exist_ok=True)
    quantization = "int8" if args.quantize else "fp32"
    path = args.out / f"pocket-{quantization}-{args.language}-s{args.sessions}.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"evidence": str(path), "verdict": evidence["qualification"]["verdict"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
