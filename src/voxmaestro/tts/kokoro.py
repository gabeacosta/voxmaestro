"""Kokoro TTS backend for the frozen VoxMaestro TTS contract.

Talks to the local Kokoro HTTP service (default ``127.0.0.1:8880``) already
running as ``com.clue.kokoro-tts``. Implements the same ``TTSBackend`` surface
as ``PocketTTSBackend`` so ``TTSWorker``, ``TurnWriter``, and ``SessionAudio``
cancellation / barge-in paths are reused unchanged.

Capabilities are runtime-probed (health + voices). Product languages for this
adapter are English-only until a separate local ES TTS is routed beside it —
do not claim Spanish from Kokoro without a quality qualification receipt.
"""

from __future__ import annotations

import json
import struct
import threading
import urllib.error
import urllib.request
import wave
from collections.abc import Callable, Iterator
from io import BytesIO
from typing import Any, Protocol

from voxmaestro.tts.contract import (
    AudioChunk,
    LanguageLane,
    LanguageSupport,
    SynthesizeRequest,
    TTSCapabilities,
    VoiceManifest,
)

DEFAULT_BASE_URL = "http://127.0.0.1:8880"
DEFAULT_VOICE = "bm_george"
DEFAULT_CHUNK_SAMPLES = 960  # 40 ms at 24 kHz


class KokoroTTSUnavailableError(RuntimeError):
    """Raised when the local Kokoro service cannot be probed or used."""


class KokoroClient(Protocol):
    """Minimal client surface used by KokoroTTSBackend (real HTTP or fake)."""

    def health(self) -> dict[str, Any]:
        """Return health payload."""

    def voices(self) -> list[str]:
        """Return available voice ids."""

    def synthesize_wav(self, text: str, voice: str, *, cancel_check: Callable[[], bool]) -> bytes:
        """Return a complete WAV body, or empty bytes if cancelled mid-flight."""


class HttpKokoroClient:
    """urllib client for the local OpenAI-compatible Kokoro server."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout_s: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _get_json(self, path: str) -> Any:
        req = urllib.request.Request(f"{self.base_url}{path}", method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def health(self) -> dict[str, Any]:
        payload = self._get_json("/health")
        if not isinstance(payload, dict):
            raise KokoroTTSUnavailableError("health payload is not an object")
        return payload

    def voices(self) -> list[str]:
        payload = self._get_json("/voices")
        voices = payload.get("voices") if isinstance(payload, dict) else None
        if not isinstance(voices, list) or not voices:
            raise KokoroTTSUnavailableError("voices list missing from /voices")
        return [str(v) for v in voices]

    def synthesize_wav(self, text: str, voice: str, *, cancel_check: Callable[[], bool]) -> bytes:
        if cancel_check():
            return b""
        body = json.dumps(
            {
                "model": "kokoro",
                "input": text,
                "voice": voice,
                "response_format": "wav",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/audio/speech",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Run the blocking HTTP call in a helper thread so cancel_check can
        # abandon the result without waiting for the full WAV body.
        box: dict[str, Any] = {}
        done = threading.Event()

        def _fetch() -> None:
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    box["data"] = resp.read()
            except Exception as exc:  # noqa: BLE001 — surfaced after join/cancel
                box["error"] = exc
            finally:
                done.set()

        thread = threading.Thread(target=_fetch, name="kokoro-http-synth", daemon=True)
        thread.start()
        while not done.wait(timeout=0.05):
            if cancel_check():
                return b""
        if cancel_check():
            return b""
        if "error" in box:
            exc = box["error"]
            if isinstance(exc, urllib.error.URLError):
                raise KokoroTTSUnavailableError(f"kokoro synthesize failed: {exc}") from exc
            raise KokoroTTSUnavailableError(f"kokoro synthesize failed: {exc}") from exc
        return bytes(box.get("data") or b"")


def _wav_to_s16_mono(wav_bytes: bytes) -> tuple[bytes, int]:
    """Decode WAV to mono PCM s16le and sample rate."""
    with wave.open(BytesIO(wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got sample width {width}")
    if channels == 1:
        return frames, rate
    if channels != 2:
        raise ValueError(f"unsupported channel count: {channels}")
    # Downmix stereo s16le to mono.
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    mono = []
    for i in range(0, len(samples), 2):
        mono.append(int((samples[i] + samples[i + 1]) / 2))
    return struct.pack(f"<{len(mono)}h", *mono), rate


def _s16_to_f32_le(pcm_s16: bytes) -> bytes:
    """Convert s16le PCM to float32 little-endian (Pocket-compatible sink format)."""
    n = len(pcm_s16) // 2
    if n == 0:
        return b""
    samples = struct.unpack(f"<{n}h", pcm_s16[: n * 2])
    return struct.pack(f"<{n}f", *(s / 32768.0 for s in samples))


class KokoroTTSBackend:
    """TTSBackend adapter around a local Kokoro HTTP service."""

    backend_id = "kokoro-http"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        default_voice: str = DEFAULT_VOICE,
        client: KokoroClient | None = None,
        backend_version: str | None = None,
        chunk_samples: int = DEFAULT_CHUNK_SAMPLES,
        rss_fn: Callable[[], int] | None = None,
        sample_rate: int = 24000,
        http_timeout_s: float = 180.0,
    ) -> None:
        """Probe the service and freeze the capability surface."""
        self._client: KokoroClient = client or HttpKokoroClient(
            base_url, timeout_s=http_timeout_s
        )
        self._default_voice = default_voice
        self._chunk_samples = chunk_samples
        self._sample_rate = sample_rate
        self._quantization = "onnx-fp32"
        self._sessions: dict[str, VoiceManifest] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()

        try:
            health = self._client.health()
            voices = self._client.voices()
        except Exception as exc:  # noqa: BLE001 — probe failures become unavailable
            raise KokoroTTSUnavailableError(f"kokoro probe failed: {exc}") from exc
        if health.get("status") != "ok":
            raise KokoroTTSUnavailableError(f"kokoro health not ok: {health!r}")
        if self._default_voice not in voices:
            raise KokoroTTSUnavailableError(
                f"default voice {self._default_voice!r} not in probed voices {voices}"
            )
        self._voices = tuple(voices)
        self._backend_version = backend_version or str(health.get("model") or "kokoro-onnx")
        rss = rss_fn() if rss_fn is not None else 0
        # EN only: local server hardcodes lang=en-us; ES needs a separate adapter.
        self._capabilities = TTSCapabilities(
            backend_id=self.backend_id,
            backend_version=self._backend_version,
            quantizations=(self._quantization,),
            languages=(
                LanguageSupport("en", LanguageLane.FAST, True, False, "kokoro-onnx preset voices"),
            ),
            sample_rates=(self._sample_rate,),
            streaming=True,
            voice_state_export=False,
            runtime_memory_bytes={self._quantization: rss},
            advertised_from="runtime-probe",
        )

    def capabilities(self) -> TTSCapabilities:
        """Return the frozen runtime-probed capability record."""
        return self._capabilities

    def open_session(self, session_id: str, voice: VoiceManifest) -> None:
        """Bind one voice handle to session_id (no shared KV cache)."""
        if voice.session_id != session_id:
            raise ValueError("voice.session_id must equal session_id (WT-TTS-002)")
        if voice.backend_id != self.backend_id:
            raise ValueError(f"backend_id {voice.backend_id!r} != {self.backend_id!r}")
        if voice.backend_version != self._backend_version:
            raise ValueError("backend_version mismatch (WT-TTS-002)")
        if voice.quantization != self._quantization:
            raise ValueError("quantization mismatch (WT-TTS-002)")
        if voice.sample_rate != self._sample_rate:
            raise ValueError("sample_rate mismatch (WT-TTS-002)")
        if voice.language != "en":
            raise ValueError(
                f"language {voice.language!r} is not supported by Kokoro adapter "
                "(EN only; route ES to a separate local TTS)"
            )
        if voice.voice_id not in self._voices:
            raise ValueError(f"voice_id {voice.voice_id!r} not in probed voices")
        with self._lock:
            if session_id in self._sessions:
                raise RuntimeError(f"session {session_id!r} already open")
            self._sessions[session_id] = voice

    def close_session(self, session_id: str) -> None:
        """Drop the session handle. It must not be reused."""
        with self._lock:
            self._sessions.pop(session_id, None)

    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        """Blocking generator of tagged float32 PCM. Run only from TTSWorker."""
        with self._lock:
            voice = self._sessions.get(req.session_id)
        if voice is None:
            raise RuntimeError(f"session {req.session_id!r} is not open")
        if req.language != "en":
            raise ValueError(f"language {req.language!r} is not supported by Kokoro adapter")

        def cancel_check() -> bool:
            return req.turn_id in self._cancel

        try:
            if cancel_check():
                return
            wav = self._client.synthesize_wav(
                req.text,
                voice.voice_id,
                cancel_check=cancel_check,
            )
            if not wav or cancel_check():
                return
            pcm_s16, rate = _wav_to_s16_mono(wav)
            if rate != self._sample_rate:
                # Keep contract honest: capability advertised a single rate.
                raise ValueError(
                    f"kokoro returned sample_rate {rate}, expected {self._sample_rate}"
                )
            bytes_per_chunk = self._chunk_samples * 2
            seq = req.seq_start
            pending: AudioChunk | None = None
            offset = 0
            while offset < len(pcm_s16):
                if cancel_check():
                    break
                piece = pcm_s16[offset : offset + bytes_per_chunk]
                offset += bytes_per_chunk
                if len(piece) < 2:
                    break
                # Pad short final frame so sinks see consistent frame sizes optional —
                # leave unpadded; float32 length still encodes sample count.
                chunk = AudioChunk(
                    pcm=_s16_to_f32_le(piece),
                    sample_rate=self._sample_rate,
                    turn_id=req.turn_id,
                    seq=seq,
                )
                seq += 1
                if pending is not None:
                    yield pending
                pending = chunk
            if pending is not None and not cancel_check():
                yield AudioChunk(
                    pcm=pending.pcm,
                    sample_rate=pending.sample_rate,
                    turn_id=pending.turn_id,
                    seq=pending.seq,
                    is_last=True,
                    flush_reason="end",
                )
        finally:
            # Cancel flags must not outlive the synthesize() they targeted
            # (same invariant as PocketTTSBackend).
            self._cancel.discard(req.turn_id)

    def cancel(self, turn_id: str) -> None:
        """Stop producing chunks for turn_id at the next yield / HTTP check."""
        self._cancel.add(turn_id)
