"""TTS adapter surface for VoxMaestro.

Backends implement TTSBackend. The conductor talks only to this package,
never to torch/MLX generators directly.
"""

from voxmaestro.tts.contract import (
    POCKET_TTS_LANGUAGES,
    AudioChunk,
    ConsentRecord,
    LanguageLane,
    LanguageSupport,
    SynthesizeRequest,
    TTSBackend,
    TTSCapabilities,
    VoiceManifest,
)
from voxmaestro.tts.pocket import PocketTTSBackend, PocketTTSNotInstalledError, pocket_language
from voxmaestro.tts.worker import TTSWorker, WriterGate

__all__ = [
    "POCKET_TTS_LANGUAGES",
    "AudioChunk",
    "ConsentRecord",
    "LanguageLane",
    "LanguageSupport",
    "PocketTTSBackend",
    "PocketTTSNotInstalledError",
    "SynthesizeRequest",
    "TTSBackend",
    "TTSCapabilities",
    "TTSWorker",
    "VoiceManifest",
    "WriterGate",
    "pocket_language",
]
