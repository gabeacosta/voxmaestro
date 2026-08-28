from __future__ import annotations

import pytest

from voxmaestro.tts.contract import ConsentRecord, LanguageLane, VoiceManifest
from voxmaestro.tts.languages import ENABLED_LANGUAGES, enabled_language_support, require_enabled
from voxmaestro.tts.pocket import PocketTTSBackend, pocket_language


def _consent() -> ConsentRecord:
    return ConsentRecord(
        voice_id="alba",
        source="kyutai-demo",
        granted_at="2026-05-04T00:00:00Z",
        license="demo",
    )


def _manifest(**overrides: object) -> VoiceManifest:
    base: dict[str, object] = {
        "voice_id": "alba",
        "language": "en",
        "lane": LanguageLane.FAST,
        "sample_rate": 24000,
        "backend_id": "pocket-python",
        "backend_version": "test",
        "quantization": "int8",
        "consent_record": _consent(),
        "session_id": "sess_1",
    }
    base.update(overrides)
    return VoiceManifest(**base)  # type: ignore[arg-type]


class FakeTTSModel:
    sample_rate = 24000

    @classmethod
    def load_model(cls, language: str | None = None, quantize: bool = False) -> FakeTTSModel:
        inst = cls()
        inst.language = language
        inst.quantize = quantize
        return inst

    def get_state_for_audio_prompt(self, voice: str) -> dict[str, str]:
        return {"voice": voice}

    def generate_audio_stream(self, model_state: dict[str, str], text_to_generate: str):
        del model_state, text_to_generate
        yield b"ok"


def _backend() -> PocketTTSBackend:
    return PocketTTSBackend(
        model=FakeTTSModel.load_model(quantize=True),
        model_cls=FakeTTSModel,
        rss_fn=lambda: 234_000_000,
        backend_version="test",
        quantize=True,
    )


def test_enabled_set_is_en_es() -> None:
    assert ENABLED_LANGUAGES == frozenset({"en", "es"})
    assert {item.code for item in enabled_language_support()} == {"en", "es"}


def test_spanish_maps_to_pocket_name() -> None:
    assert pocket_language("en", LanguageLane.FAST) == "english"
    assert pocket_language("es", LanguageLane.FAST) == "spanish"
    assert pocket_language("es", LanguageLane.QUALITY) == "spanish_24l"


def test_other_languages_rejected() -> None:
    with pytest.raises(ValueError, match="en/es only"):
        require_enabled("fr")
    with pytest.raises(ValueError, match="en/es only"):
        pocket_language("fr", LanguageLane.QUALITY)
    with pytest.raises(ValueError, match="en/es only"):
        pocket_language("de", LanguageLane.FAST)


def test_capabilities_advertise_only_en_es() -> None:
    codes = {item.code for item in _backend().capabilities().languages}
    assert codes == {"en", "es"}


def test_open_session_rejects_french() -> None:
    backend = _backend()
    voice = _manifest(language="fr", lane=LanguageLane.QUALITY)
    with pytest.raises(ValueError, match="en/es only"):
        backend.open_session("sess_1", voice)
