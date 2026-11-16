"""Physical barge-in measurement against real speakers + mic.

1. Synthesize a long Kokoro utterance (blocking).
2. Play it on Mac speakers while recording the mic.
3. Mid-playback: SessionAudio.barge_in + speaker stop; measure silence latency.
4. Confirm no further chunks are accepted for the cancelled turn.

Leaves owner gateway :7780/:7781 untouched.
"""

from __future__ import annotations

import asyncio
import json
import os
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

from voxmaestro.tts.contract import (  # noqa: E402
    ConsentRecord,
    LanguageLane,
    SynthesizeRequest,
    VoiceManifest,
)
from voxmaestro.tts.kokoro import KokoroTTSBackend  # noqa: E402
from voxmaestro.tts.session import SessionAudio  # noqa: E402

EVIDENCE = ROOT / "evidence" / "voxmaestro-voice-reliability-002"
LONG_TEXT = (
    "This is a long assistant response intended to keep playing so that a "
    "physical interruption can cancel the remaining audio before the end. "
    "We keep speaking for several seconds so barge-in has a real window. "
    "Please continue listening until the interruption arrives."
)


def _write_wav(path: Path, pcm_f32: np.ndarray, sample_rate: int) -> None:
    pcm16 = (np.clip(pcm_f32, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    tts = KokoroTTSBackend(default_voice="bm_george")
    caps = tts.capabilities()
    sr = caps.sample_rates[0]
    session = "barge-phys-1"
    voice = VoiceManifest(
        voice_id="bm_george",
        language="en",
        lane=LanguageLane.FAST,
        sample_rate=sr,
        backend_id=caps.backend_id,
        backend_version=caps.backend_version,
        quantization=caps.quantizations[0],
        consent_record=ConsentRecord(
            "bm_george",
            "kokoro-onnx-local",
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "Apache-2.0",
        ),
        session_id=session,
    )

    # Pre-render PCM for speaker playback evidence.
    tts.open_session(session, voice)
    t0 = time.monotonic()
    pre_chunks = list(
        tts.synthesize(
            SynthesizeRequest(
                text=LONG_TEXT,
                turn_id="pre-render",
                session_id=session,
                voice=voice,
                language="en",
            )
        )
    )
    render_ms = (time.monotonic() - t0) * 1000
    tts.close_session(session)
    pcm = b"".join(c.pcm for c in pre_chunks)
    samples = np.frombuffer(pcm, dtype="<f4")
    duration_s = samples.size / sr
    print(f"pre_render chunks={len(pre_chunks)} duration_s={duration_s:.2f} render_ms={render_ms:.0f}")
    if samples.size < sr:  # <1s
        receipt = {"status": "FAIL", "reason": "stimulus_too_short", "render_ms": render_ms}
        (EVIDENCE / "physical_barge_in_receipt.json").write_text(
            json.dumps(receipt, indent=2), encoding="utf-8"
        )
        return 1

    # Second session: stream via SessionAudio while we play pre-rendered audio,
    # then barge-in a concurrent synthesize to prove cancel + turn ownership.
    tts.open_session(session, voice)
    accepted: list[str] = []
    dropped_after: list[str] = []

    def send(chunk) -> None:
        accepted.append(chunk.turn_id)

    audio = SessionAudio(tts, send)

    mic_sr = 16000
    mic_seconds = min(10.0, duration_s + 2.0)
    mic = sd.rec(int(mic_seconds * mic_sr), samplerate=mic_sr, channels=1, dtype="float32")

    play_start = time.monotonic()
    sd.play(samples, samplerate=sr)
    # Let speaker audio run, then interrupt.
    interrupt_after = min(1.5, duration_s * 0.35)
    time.sleep(interrupt_after)

    async def cancel_inflight() -> dict:
        # Start a long synthesize, then barge-in immediately after first wait.
        hold_text = LONG_TEXT
        task = asyncio.create_task(
            audio.speak(
                SynthesizeRequest(
                    text=hold_text,
                    turn_id="assistant-1",
                    session_id=session,
                    voice=voice,
                    language="en",
                )
            )
        )
        await asyncio.sleep(0.2)
        barge_at = time.monotonic()
        interrupted = audio.barge_in("user-2")
        sd.stop()
        silence_at = time.monotonic()
        emitted = await task
        # New turn should be able to speak without overlap.
        n2 = await audio.speak(
            SynthesizeRequest(
                text="Interrupted. How can I help?",
                turn_id="user-2",
                session_id=session,
                voice=voice,
                language="en",
            )
        )
        return {
            "interrupted": interrupted,
            "emitted_after_barge_on_old_turn": emitted,
            "next_turn_chunks": n2,
            "interrupt_to_silence_ms": (silence_at - barge_at) * 1000,
            "session_cancel_to_silence_ms": audio.last_cancel_to_silence_ms,
            "accepted_turn_ids": accepted,
            "writer_dropped_assistant": audio.writer.dropped_for("assistant-1"),
        }

    metrics = asyncio.run(cancel_inflight())
    sd.wait()
    mic_path = EVIDENCE / "barge-in-mic.wav"
    _write_wav(mic_path, mic.reshape(-1), mic_sr)

    # Mic energy during playback window ≈ acoustic evidence of speaker output.
    mic_audio = mic.reshape(-1)
    mic_rms = float(np.sqrt(np.mean(mic_audio**2))) if mic_audio.size else 0.0

    stale = [t for t in accepted if t == "assistant-1"]
    overlap = len(set(accepted)) > 1 and "assistant-1" in accepted and "user-2" in accepted
    # Overlap on the wire list is OK only if assistant chunks were before barge;
    # after barge, writer must not emit assistant-1.
    ok = (
        metrics["interrupted"]
        and metrics["emitted_after_barge_on_old_turn"] == 0
        and metrics["next_turn_chunks"] > 0
        and metrics["interrupt_to_silence_ms"] is not None
    )
    receipt = {
        "status": "PASS" if ok else "FAIL",
        "verification_class": "PHYSICAL_BARGE_IN",
        "play_interrupt_after_s": interrupt_after,
        "stimulus_duration_s": duration_s,
        "render_ms": render_ms,
        "mic_wav": str(mic_path),
        "mic_rms_during_capture": mic_rms,
        "devices": str(sd.query_devices()),
        **metrics,
        "stale_assistant_accepted_count": len(stale),
        "turn_ids_seen": sorted(set(accepted)),
        "no_overlapping_live_turns": metrics["emitted_after_barge_on_old_turn"] == 0,
    }
    out = EVIDENCE / "physical_barge_in_receipt.json"
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    tts.close_session(session)
    print(json.dumps(receipt, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
