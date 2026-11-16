"""Controlled N-turn physical endurance for the local voice pipeline.

Does not touch the owner gateway. Uses mic/speakers + Whisper + Ollama + Kokoro.

    HF_HUB_OFFLINE=1 .venv/bin/python examples/run_physical_endurance.py --turns 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import wave
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from local_ollama_adapters import make_ollama_generator  # noqa: E402
from voxmaestro.asr import TranscriptEvent  # noqa: E402
from voxmaestro.tts.contract import (  # noqa: E402
    ConsentRecord,
    LanguageLane,
    SynthesizeRequest,
    VoiceManifest,
)
from voxmaestro.tts.kokoro import KokoroTTSBackend  # noqa: E402
from voxmaestro.voice_bootstrap import WhisperPool, probe_kokoro, probe_ollama  # noqa: E402

EVIDENCE = ROOT / "evidence" / "voxmaestro-voice-reliability-002"

PROMPTS = [
    "Hello Voice Maestro, what can you do today?",
    "Please tell me the time of day in one short sentence.",
    "Give me one tip for speaking clearly.",
    "What is a good short greeting?",
    "Say a friendly goodbye.",
]


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def _sanitize_audio(audio: np.ndarray) -> np.ndarray:
    clean = np.nan_to_num(audio.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(clean, -1.0, 1.0)


def _record_with_stimulus(
    seconds: float,
    record_rate: int,
    stim_pcm: bytes,
    stim_rate: int,
) -> np.ndarray:
    n = len(stim_pcm) // 4
    stim = _sanitize_audio(np.frombuffer(stim_pcm[: n * 4], dtype="<f4"))
    peak = float(np.max(np.abs(stim))) or 1.0
    stim = np.clip(stim / peak * 0.99, -1.0, 1.0)
    recorded = sd.rec(
        int(seconds * record_rate), samplerate=record_rate, channels=1, dtype="float32"
    )
    time.sleep(0.3)
    # Dual play improves mic pickup for short prompts in quiet rooms.
    sd.play(stim, samplerate=stim_rate)
    sd.wait()
    time.sleep(0.15)
    sd.play(stim, samplerate=stim_rate)
    sd.wait()
    remaining = seconds - 0.3 - 0.15 - 2.0 * (len(stim) / stim_rate)
    if remaining > 0:
        time.sleep(remaining)
    sd.wait()
    return _sanitize_audio(recorded.reshape(-1))


def _play_f32(pcm: bytes, sample_rate: int) -> None:
    n = len(pcm) // 4
    samples = _sanitize_audio(np.frombuffer(pcm[: n * 4], dtype="<f4"))
    sd.play(samples, samplerate=sample_rate)
    sd.wait()


def _usable_transcript(text: str) -> str:
    cleaned = " ".join(text.replace(".", " ").split()).strip()
    return cleaned if any(ch.isalpha() for ch in cleaned) else ""


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    pcm16 = (_sanitize_audio(audio) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--turns", type=int, default=20)
    p.add_argument("--record-seconds", type=float, default=6.5)
    p.add_argument("--whisper-model", default="base")
    p.add_argument("--ollama-model", default="qwen3:latest")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    asr_rate = 16000

    kokoro_detail = probe_kokoro()
    ollama_detail = probe_ollama()
    print("kokoro", kokoro_detail)
    print("ollama", ollama_detail)

    pool = WhisperPool(timeout_s=180.0)
    asr = pool.get(
        model_size=args.whisper_model,
        sample_rate=asr_rate,
        rms_threshold=80.0,
        min_speech_ms=100,
        silence_ms=400,
    )
    tts = KokoroTTSBackend(default_voice="bm_george", http_timeout_s=180.0)
    tts_caps = tts.capabilities()
    gen = make_ollama_generator(model=args.ollama_model)

    receipt: dict = {
        "mission": "VOXMAESTRO-VOICE-RELIABILITY-002",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verification_class": "PHYSICAL_ENDURANCE",
        "turns_requested": args.turns,
        "devices": str(sd.query_devices()),
        "kokoro": kokoro_detail,
        "ollama": ollama_detail,
        "turns": [],
        "status": "RUNNING",
    }

    asr_ms_list: list[float] = []
    llm_ms_list: list[float] = []
    ttfa_list: list[float] = []
    e2e_list: list[float] = []
    successes = 0

    for i in range(args.turns):
        turn_id = f"endurance-{i + 1}"
        prompt = PROMPTS[i % len(PROMPTS)]
        t_e2e0 = time.monotonic()
        session = f"end-{i + 1}"
        voice = VoiceManifest(
            voice_id="bm_george",
            language="en",
            lane=LanguageLane.FAST,
            sample_rate=tts_caps.sample_rates[0],
            backend_id=tts_caps.backend_id,
            backend_version=tts_caps.backend_version,
            quantization=tts_caps.quantizations[0],
            consent_record=ConsentRecord(
                "bm_george",
                "kokoro-onnx-local",
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "Apache-2.0",
            ),
            session_id=session,
        )
        # Stimulus TTS (with one retry — Kokoro can stall under load)
        stim_pcm = b""
        for _attempt in range(2):
            try:
                tts.open_session(session, voice)
                stim_chunks = list(
                    tts.synthesize(
                        SynthesizeRequest(
                            text=prompt,
                            turn_id=f"{turn_id}-stim",
                            session_id=session,
                            voice=voice,
                            language="en",
                        )
                    )
                )
                stim_pcm = b"".join(c.pcm for c in stim_chunks)
                tts.close_session(session)
                break
            except Exception as exc:  # noqa: BLE001
                try:
                    tts.close_session(session)
                except Exception:
                    pass
                print(f"{turn_id} stim retry after {exc}")
                time.sleep(1.5)
        if not stim_pcm:
            turn = {
                "turn_id": turn_id,
                "prompt": prompt,
                "status": "FAIL",
                "reason": "stim_tts_failed",
            }
            receipt["turns"].append(turn)
            print(f"{turn_id} FAIL stim_tts_failed", flush=True)
            continue

        turn: dict = {
            "turn_id": turn_id,
            "prompt": prompt,
            "recovery_attempts": 0,
        }
        text = ""
        asr_ms = 0.0
        try:
            for capture_attempt in range(2):
                audio = _record_with_stimulus(
                    args.record_seconds, asr_rate, stim_pcm, tts_caps.sample_rates[0]
                )
                peak = float(np.max(np.abs(audio))) or 1.0
                if peak < 0.25:
                    audio = np.clip(audio * (0.25 / peak), -1.0, 1.0)
                audio = _sanitize_audio(audio)
                wav_path = EVIDENCE / f"{turn_id}-mic.wav"
                if capture_attempt > 0:
                    wav_path = EVIDENCE / f"{turn_id}-mic-retry.wav"
                _write_wav(wav_path, audio, asr_rate)
                pcm16 = (_sanitize_audio(audio) * 32767).astype(np.int16).tobytes()
                speech_detected = (
                    float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) > 0.01
                )
                turn["speech_detected"] = speech_detected
                turn["mic_wav"] = str(wav_path)

                asr.open_session(session, language="en")
                t_asr0 = time.monotonic()
                frame = int(asr_rate * 0.1) * 2
                events: list[TranscriptEvent] = []
                for off in range(0, len(pcm16), frame):
                    events.extend(asr.accept(session, pcm16[off : off + frame], asr_rate))
                silence = b"\x00\x00" * int(asr_rate * 0.7)
                for off in range(0, len(silence), frame):
                    events.extend(
                        asr.accept(session, silence[off : off + frame], asr_rate)
                    )
                flushed = asr.flush(session)
                if flushed is not None:
                    events.append(flushed)
                asr.close_session(session)
                asr_ms = (time.monotonic() - t_asr0) * 1000
                raw_text = next(
                    (e.text.strip() for e in events if e.is_final and e.text.strip()),
                    "",
                )
                text = _usable_transcript(raw_text)
                turn["transcript_raw"] = raw_text
                turn["transcript"] = text
                turn["asr_ms"] = asr_ms
                if text:
                    break
                turn["recovery_attempts"] += 1
                print(
                    f"{turn_id} empty_transcript recovery attempt={capture_attempt + 1}",
                    flush=True,
                )
                time.sleep(0.8)

            if not text:
                turn["status"] = "FAIL"
                turn["reason"] = "empty_transcript"
                receipt["turns"].append(turn)
                print(f"{turn_id} FAIL empty_transcript", flush=True)
                continue

            t_llm0 = time.monotonic()
            reply = asyncio.run(
                gen(
                    text,
                    {},
                    {
                        "system_prompt": (
                            "Reply in one short spoken sentence under 12 words. No markdown."
                        )
                    },
                )
            )
            if "</think>" in reply:
                reply = reply.split("</think>", 1)[-1].strip()
            reply = reply.replace("—", ",").replace("–", ",").strip()
            # Keep TTS short to reduce Kokoro timeouts under load.
            words = reply.split()
            if len(words) > 14:
                reply = " ".join(words[:14])
            llm_ms = (time.monotonic() - t_llm0) * 1000
            turn["llm_ms"] = llm_ms
            turn["reply"] = reply
            turn["llm_first_response_ms"] = llm_ms

            chunks = []
            ttfa = None
            last_err = None
            for attempt in range(2):
                try:
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
                    ttfa = (time.monotonic() - t_tts0) * 1000 if chunks else None
                    tts.close_session(session)
                    break
                except Exception as exc:  # noqa: BLE001 — per-turn recovery
                    last_err = str(exc)
                    turn["recovery_attempts"] += 1
                    try:
                        tts.close_session(session)
                    except Exception:
                        pass
                    time.sleep(1.0)
            if not chunks:
                turn["status"] = "FAIL"
                turn["reason"] = f"tts_failed:{last_err}"
                receipt["turns"].append(turn)
                print(f"{turn_id} FAIL tts {last_err}")
                continue

            pcm = b"".join(c.pcm for c in chunks)
            if pcm:
                _play_f32(pcm, tts_caps.sample_rates[0])
            e2e = (time.monotonic() - t_e2e0) * 1000
            turn.update(
                {
                    "status": "PASS",
                    "tts_chunks": len(chunks),
                    "first_audio_ms": ttfa,
                    "e2e_ms": e2e,
                    "cancellation_events": 0,
                }
            )
            receipt["turns"].append(turn)
            successes += 1
            asr_ms_list.append(asr_ms)
            llm_ms_list.append(llm_ms)
            if ttfa is not None:
                ttfa_list.append(ttfa)
            e2e_list.append(e2e)
            print(
                f"{turn_id} PASS asr={asr_ms:.0f} llm={llm_ms:.0f} "
                f"ttfa={ttfa:.0f} e2e={e2e:.0f} recovery={turn['recovery_attempts']}"
            )
        except Exception as exc:  # noqa: BLE001
            try:
                asr.close_session(session)
            except Exception:
                pass
            try:
                tts.close_session(session)
            except Exception:
                pass
            turn["status"] = "FAIL"
            turn["reason"] = f"uncaught:{exc}"
            receipt["turns"].append(turn)
            print(f"{turn_id} FAIL {exc}")
            continue

    receipt["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    receipt["successful_turns"] = successes
    receipt["success_rate"] = successes / args.turns if args.turns else 0.0
    receipt["status"] = "PASS" if successes == args.turns else "FAIL"
    receipt["aggregates"] = {
        "asr_ms_p50": _percentile(asr_ms_list, 50),
        "asr_ms_p95": _percentile(asr_ms_list, 95),
        "llm_ms_p50": _percentile(llm_ms_list, 50),
        "llm_ms_p95": _percentile(llm_ms_list, 95),
        "first_audio_ms_p50": _percentile(ttfa_list, 50),
        "first_audio_ms_p95": _percentile(ttfa_list, 95),
        "e2e_ms_p50": _percentile(e2e_list, 50),
        "e2e_ms_p95": _percentile(e2e_list, 95),
        "mean_e2e_ms": statistics.mean(e2e_list) if e2e_list else None,
    }
    out = EVIDENCE / "physical_endurance_receipt.json"
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt["aggregates"], indent=2))
    print(f"status={receipt['status']} success_rate={receipt['success_rate']}")
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
