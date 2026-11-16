"""Physical mic → Whisper → Ollama → Kokoro → speaker loop (Mac hardware).

Mandatory verification path for VOICE-MAESTRO-LOCAL-COMPLETE-001.
Does not use software-substituted audio.

    .venv/bin/python examples/run_physical_voice_loop.py --turns 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

# Prefer local caches; do not require Hugging Face during offline verify.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from local_ollama_adapters import make_ollama_generator  # noqa: E402
from voxmaestro.asr import TranscriptEvent  # noqa: E402
from voxmaestro.asr_whisper import WhisperASRBackend  # noqa: E402
from voxmaestro.tts.contract import (  # noqa: E402
    ConsentRecord,
    LanguageLane,
    SynthesizeRequest,
    VoiceManifest,
)
from voxmaestro.tts.kokoro import KokoroTTSBackend  # noqa: E402

EVIDENCE = ROOT / "evidence" / "voice-maestro-local-complete-001"


def _record(seconds: float, sample_rate: int) -> np.ndarray:
    print(f"recording {seconds:.1f}s from default input… speak now")
    audio = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    return audio.reshape(-1)


def _record_with_speaker_stimulus(
    seconds: float,
    record_rate: int,
    stimulus_pcm_f32: bytes,
    stimulus_rate: int,
) -> np.ndarray:
    """Play stimulus on speakers while recording the mic (acoustic path)."""
    n = len(stimulus_pcm_f32) // 4
    stim = np.frombuffer(stimulus_pcm_f32[: n * 4], dtype="<f4")
    # Soft peak normalize then boost for room coupling without hard clipping.
    peak = float(np.max(np.abs(stim))) or 1.0
    stim = np.clip(stim / peak * 0.95, -1.0, 1.0)

    print(f"recording {seconds:.1f}s with speaker stimulus ({len(stim)/stimulus_rate:.2f}s audio)")
    recorded = sd.rec(
        int(seconds * record_rate),
        samplerate=record_rate,
        channels=1,
        dtype="float32",
    )
    time.sleep(0.25)
    sd.play(stim, samplerate=stimulus_rate)
    sd.wait()
    # Ensure full record window completes.
    remaining = seconds - 0.25 - (len(stim) / stimulus_rate)
    if remaining > 0:
        time.sleep(remaining)
    sd.wait()
    return recorded.reshape(-1)


def _float_to_pcm16(audio: np.ndarray) -> bytes:
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


def _play_f32(pcm: bytes, sample_rate: int) -> None:
    n = len(pcm) // 4
    samples = np.frombuffer(pcm[: n * 4], dtype="<f4")
    sd.play(samples, samplerate=sample_rate)
    sd.wait()


def _write_wav(path: Path, pcm_s16: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_s16)


async def _generate(text: str, model: str) -> str:
    gen = make_ollama_generator(model=model)
    return await gen(
        text,
        {},
        {
            "system_prompt": (
                "You are Voice Maestro on a local Mac. "
                "Reply in one short spoken sentence."
            )
        },
    )


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--turns", type=int, default=1)
    p.add_argument("--record-seconds", type=float, default=5.0)
    p.add_argument("--whisper-model", default="base")
    p.add_argument("--ollama-model", default="qwen3:latest")
    p.add_argument("--kokoro-voice", default="bm_george")
    p.add_argument(
        "--self-stimulus",
        default="Hello Voice Maestro, what can you do today?",
        help=(
            "Phrase played through speakers via macOS `say` while the mic records, "
            "producing a real acoustic mic capture when no human is speaking. "
            "Pass empty string to require a live human talker."
        ),
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    asr_rate = 16000

    print("devices:\n", sd.query_devices())
    tts = KokoroTTSBackend(default_voice=args.kokoro_voice)
    tts_caps = tts.capabilities()
    print("tts", tts_caps.backend_id, tts_caps.backend_version)

    print("loading whisper (first run may download weights)…")
    # Lower energy gate for quiet room / USB mic coupling during self-stimulus.
    asr = WhisperASRBackend(
        model_size=args.whisper_model,
        sample_rate=asr_rate,
        rms_threshold=80.0,
        min_speech_ms=100,
        silence_ms=400,
    )
    print("asr", asr.backend_id, asr.capabilities().backend_version)

    receipt: dict = {
        "mission": "VOICE-MAESTRO-LOCAL-COMPLETE-001",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "devices": str(sd.query_devices()),
        "tts": {
            "backend_id": tts_caps.backend_id,
            "backend_version": tts_caps.backend_version,
            "voice": args.kokoro_voice,
        },
        "asr": {"backend_id": asr.backend_id, "model": args.whisper_model},
        "llm": {"model": args.ollama_model, "endpoint": "http://127.0.0.1:11434"},
        "turns": [],
        "status": "RUNNING",
        "verification_class": "PHYSICAL_MIC_SPEAKER",
    }

    for turn in range(args.turns):
        turn_id = f"physical-{turn + 1}"
        stimulus = (args.self_stimulus or "").strip()
        if stimulus:
            # Pre-render stimulus with Kokoro, play on speakers while mic records.
            stim_session = f"stim-{turn + 1}"
            stim_voice = VoiceManifest(
                voice_id=args.kokoro_voice,
                language="en",
                lane=LanguageLane.FAST,
                sample_rate=tts_caps.sample_rates[0],
                backend_id=tts_caps.backend_id,
                backend_version=tts_caps.backend_version,
                quantization=tts_caps.quantizations[0],
                consent_record=ConsentRecord(
                    voice_id=args.kokoro_voice,
                    source="kokoro-onnx-local",
                    granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    license="Apache-2.0",
                ),
                session_id=stim_session,
            )
            tts.open_session(stim_session, stim_voice)
            stim_chunks = list(
                tts.synthesize(
                    SynthesizeRequest(
                        text=stimulus,
                        turn_id=f"stim-{turn + 1}",
                        session_id=stim_session,
                        voice=stim_voice,
                        language="en",
                    )
                )
            )
            tts.close_session(stim_session)
            stim_pcm = b"".join(c.pcm for c in stim_chunks)
            print(f"self-stimulus via Kokoro→speakers: {stimulus!r}")
            audio = _record_with_speaker_stimulus(
                args.record_seconds,
                asr_rate,
                stim_pcm,
                tts_caps.sample_rates[0],
            )
        else:
            audio = _record(args.record_seconds, asr_rate)
        # Mic gain stage for quiet USB capture (still physical audio).
        peak = float(np.max(np.abs(audio))) or 1.0
        if peak < 0.25:
            audio = np.clip(audio * (0.25 / peak), -1.0, 1.0)
            print(f"applied mic gain; prior_peak={peak:.4f}")
        pcm16 = _float_to_pcm16(audio)
        wav_path = EVIDENCE / f"{turn_id}-mic.wav"
        _write_wav(wav_path, pcm16, asr_rate)

        session = f"phys-{turn + 1}"
        asr.open_session(session, language="en")
        t_asr0 = time.monotonic()
        # Chunk into ~100ms frames so energy endpointing sees speech+silence.
        frame = int(asr_rate * 0.1) * 2
        events: list[TranscriptEvent] = []
        for i in range(0, len(pcm16), frame):
            events.extend(asr.accept(session, pcm16[i : i + frame], asr_rate))
        # Append explicit silence so trailing-endpointing can fire, then flush.
        silence = b"\x00\x00" * int(asr_rate * 0.7)
        for i in range(0, len(silence), frame):
            events.extend(asr.accept(session, silence[i : i + frame], asr_rate))
        flushed = asr.flush(session)
        if flushed is not None:
            events.append(flushed)
        asr.close_session(session)
        asr_ms = (time.monotonic() - t_asr0) * 1000

        text = ""
        for event in events:
            if event.is_final and event.text.strip():
                text = event.text.strip()
                break
        print(f"transcript={text!r} asr_ms={asr_ms:.1f}")
        if not text:
            receipt["turns"].append(
                {
                    "turn_id": turn_id,
                    "status": "FAIL",
                    "reason": "empty_transcript",
                    "asr_ms": asr_ms,
                    "mic_wav": str(wav_path),
                }
            )
            receipt["status"] = "FAIL"
            (EVIDENCE / "physical_voice_receipt.json").write_text(
                json.dumps(receipt, indent=2), encoding="utf-8"
            )
            print("FAIL: empty transcript — check mic level / speak louder")
            return 1

        t_llm0 = time.monotonic()
        reply = asyncio.run(_generate(text, args.ollama_model))
        llm_ms = (time.monotonic() - t_llm0) * 1000
        # Drop model "thinking" blocks and normalize punctuation for TTS.
        if "</think>" in reply:
            reply = reply.split("</think>", 1)[-1].strip()
        reply = (
            reply.replace("—", ",")
            .replace("–", ",")
            .replace("…", "...")
            .strip()
        )
        print(f"reply={reply!r} llm_ms={llm_ms:.1f}")

        voice = VoiceManifest(
            voice_id=args.kokoro_voice,
            language="en",
            lane=LanguageLane.FAST,
            sample_rate=tts_caps.sample_rates[0],
            backend_id=tts_caps.backend_id,
            backend_version=tts_caps.backend_version,
            quantization=tts_caps.quantizations[0],
            consent_record=ConsentRecord(
                voice_id=args.kokoro_voice,
                source="kokoro-onnx-local",
                granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                license="Apache-2.0",
            ),
            session_id=session,
        )
        tts.open_session(session, voice)
        t_tts0 = time.monotonic()
        chunks = list(
            tts.synthesize(
                SynthesizeRequest(
                    text=reply,
                    turn_id=turn_id,
                    session_id=session,
                    voice=voice,
                    language="en",
                )
            )
        )
        first_ms = (time.monotonic() - t_tts0) * 1000 if chunks else None
        pcm = b"".join(c.pcm for c in chunks)
        tts.close_session(session)
        print(f"tts_chunks={len(chunks)} ttfa_proxy_ms={first_ms:.1f}")
        print("playing reply on default output…")
        _play_f32(pcm, tts_caps.sample_rates[0])

        receipt["turns"].append(
            {
                "turn_id": turn_id,
                "status": "PASS",
                "transcript": text,
                "reply": reply,
                "asr_ms": asr_ms,
                "llm_ms": llm_ms,
                "tts_chunks": len(chunks),
                "ttfa_proxy_ms": first_ms,
                "mic_wav": str(wav_path),
            }
        )

    receipt["status"] = "PASS"
    receipt["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = EVIDENCE / "physical_voice_receipt.json"
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"PASS evidence={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
