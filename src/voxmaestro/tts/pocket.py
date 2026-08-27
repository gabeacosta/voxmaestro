"""Pocket TTS backend for the frozen VoxMaestro TTS contract.

pocket-tts is an optional runtime dependency. Tests inject a fake model so CI
never loads torch. Capabilities are always advertised_from='runtime-probe'.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable, Iterator
from typing import Any

from voxmaestro.tts.contract import (
    POCKET_TTS_LANGUAGES,
    AudioChunk,
    LanguageLane,
    SynthesizeRequest,
    TTSCapabilities,
    VoiceManifest,
)

_ISO_TO_POCKET = {
    "en": "english",
    "de": "german",
    "it": "italian",
    "pt": "portuguese",
    "es": "spanish",
    "fr": "french_24l",
}


class PocketTTSNotInstalledError(ImportError):
    """Raised when pocket-tts is required but not installed."""


def pocket_language(code: str, lane: LanguageLane) -> str:
    """Map ISO 639-1 plus lane to a Pocket TTS voice-config name."""
    if code == "fr":
        if lane is not LanguageLane.QUALITY:
            raise ValueError("French must use the 24l quality lane")
        return "french_24l"
    base = _ISO_TO_POCKET.get(code)
    if base is None:
        raise KeyError(code)
    if lane is LanguageLane.QUALITY and code != "en":
        return f"{base}_24l"
    return base


def _import_tts_model() -> Any:
    """Import Kyutai TTSModel or raise PocketTTSNotInstalledError."""
    try:
        from pocket_tts import TTSModel
    except ImportError as exc:
        raise PocketTTSNotInstalledError(
            "pocket-tts is not installed; pip install pocket-tts"
        ) from exc
    return TTSModel


def _to_pcm(chunk: Any) -> bytes:
    """Convert a stream fragment to raw PCM bytes."""
    if isinstance(chunk, (bytes, bytearray)):
        return bytes(chunk)
    numpy_fn = getattr(chunk, "numpy", None)
    if numpy_fn is not None:
        arr = numpy_fn()
        tobytes = getattr(arr, "tobytes", None)
        if tobytes is not None:
            return tobytes()
    raise TypeError(f"unsupported audio chunk type: {type(chunk)!r}")


def _call_stream(model: Any, state: Any, text: str) -> Iterator[Any]:
    """Invoke generate_audio_stream with whichever parameter names exist."""
    fn = model.generate_audio_stream
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    if "model_state" in sig.parameters:
        kwargs["model_state"] = state
    if "text_to_generate" in sig.parameters:
        kwargs["text_to_generate"] = text
    if kwargs:
        return fn(**kwargs)
    return fn(state, text)


class PocketTTSBackend:
    """TTSBackend adapter around pocket_tts.TTSModel."""

    backend_id = "pocket-python"

    def __init__(
        self,
        *,
        language: str = "en",
        quantize: bool = True,
        model: Any | None = None,
        model_cls: Any | None = None,
        rss_fn: Callable[[], int] | None = None,
        backend_version: str | None = None,
        sample_rate: int = 24000,
    ) -> None:
        """Load or inject a model and freeze the probed capability surface."""
        self._quantize = quantize
        self._quantization = "int8" if quantize else "fp32"
        self._sample_rate = sample_rate
        self._sessions: dict[str, Any] = {}
        self._session_voices: dict[str, VoiceManifest] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()

        cls = model_cls
        if model is None:
            cls = cls or _import_tts_model()
            sig = inspect.signature(cls.load_model)
            if quantize and "quantize" not in sig.parameters:
                raise RuntimeError("this pocket-tts build has no load_model(quantize=...)")
            load_kwargs: dict[str, Any] = {
                "language": pocket_language(
                    language,
                    LanguageLane.FAST if language != "fr" else LanguageLane.QUALITY,
                ),
            }
            if "quantize" in sig.parameters:
                load_kwargs["quantize"] = quantize
            model = cls.load_model(**load_kwargs)
        self._model = model
        self._model_cls = cls or type(model)
        self._sample_rate = int(getattr(model, "sample_rate", sample_rate))
        self._backend_version = backend_version or _package_version()
        rss = rss_fn() if rss_fn is not None else _rss_bytes()
        self._capabilities = self._probe(rss)

    def _probe(self, rss: int) -> TTSCapabilities:
        """Build capabilities from inspect() plus measured RSS."""
        load = getattr(self._model_cls, "load_model", None)
        has_quantize = False
        if load is not None:
            has_quantize = "quantize" in inspect.signature(load).parameters
        quants = ("fp32", "int8") if has_quantize else ("fp32",)
        streaming = hasattr(self._model, "generate_audio_stream")
        voice_export = hasattr(self._model, "get_state_for_audio_prompt")
        return TTSCapabilities(
            backend_id=self.backend_id,
            backend_version=self._backend_version,
            quantizations=quants,
            languages=POCKET_TTS_LANGUAGES,
            sample_rates=(self._sample_rate,),
            streaming=streaming,
            voice_state_export=voice_export,
            runtime_memory_bytes={self._quantization: rss},
            advertised_from="runtime-probe",
        )

    def capabilities(self) -> TTSCapabilities:
        """Return the frozen runtime-probed capability record."""
        return self._capabilities

    def open_session(self, session_id: str, voice: VoiceManifest) -> None:
        """Bind one KV voice state to session_id."""
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
        pocket_language(voice.language, voice.lane)
        getter = getattr(self._model, "get_state_for_audio_prompt", None)
        if getter is None:
            raise RuntimeError("model cannot export voice state")
        with self._lock:
            if session_id in self._sessions:
                raise RuntimeError(f"session {session_id!r} already open")
            self._sessions[session_id] = getter(voice.voice_id)
            self._session_voices[session_id] = voice

    def close_session(self, session_id: str) -> None:
        """Drop the session handle. It must not be reused."""
        with self._lock:
            self._sessions.pop(session_id, None)
            self._session_voices.pop(session_id, None)

    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        """Blocking generator of tagged PCM. Run only from TTSWorker."""
        with self._lock:
            state = self._sessions.get(req.session_id)
        if state is None:
            raise RuntimeError(f"session {req.session_id!r} is not open")
        seq = req.seq_start
        pending: AudioChunk | None = None
        for raw in _call_stream(self._model, state, req.text):
            if req.turn_id in self._cancel:
                break
            chunk = AudioChunk(
                pcm=_to_pcm(raw),
                sample_rate=self._sample_rate,
                turn_id=req.turn_id,
                seq=seq,
            )
            seq += 1
            if pending is not None:
                yield pending
            pending = chunk
        if pending is not None and req.turn_id not in self._cancel:
            yield AudioChunk(
                pcm=pending.pcm,
                sample_rate=pending.sample_rate,
                turn_id=pending.turn_id,
                seq=pending.seq,
                is_last=True,
                flush_reason="end",
            )

    def cancel(self, turn_id: str) -> None:
        """Stop producing chunks for turn_id at the next yield."""
        self._cancel.add(turn_id)


def _package_version() -> str:
    """Return installed pocket-tts version, or 'unknown'."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover
        return "unknown"
    try:
        return version("pocket-tts")
    except PackageNotFoundError:
        return "unknown"


def _rss_bytes() -> int:
    """Best-effort current process max RSS in bytes."""
    try:
        import resource
    except ImportError:  # pragma: no cover
        return 0
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage if usage > 10_000_000 else usage * 1024
