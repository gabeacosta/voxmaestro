from __future__ import annotations

import pytest

from voxmaestro.asr_whisper import WhisperASRBackend


class _Segment:
    def __init__(self, text):
        self.text = text


class FakeWhisperModel:
    def __init__(self, text="hello there"):
        self.text = text
        self.calls = []

    def transcribe(self, audio, language=None):
        self.calls.append((len(audio), language))
        return [_Segment(f" {self.text} ")], None


def _loud(ms: int, rate: int = 16000) -> bytes:
    return b"\xff\x7f" * int(rate * ms / 1000)


def _silent(ms: int, rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(rate * ms / 1000)


def _backend(**overrides) -> WhisperASRBackend:
    return WhisperASRBackend(
        model=FakeWhisperModel(), backend_version="test", **overrides
    )


def test_capabilities_runtime_probed():
    caps = _backend().capabilities()
    assert caps.advertised_from == "runtime-probe"
    assert caps.backend_version == "test"
    assert caps.streaming is False
    assert caps.languages == ("en", "es")


def test_open_rejects_non_shipping_language():
    backend = _backend()
    with pytest.raises(ValueError, match="en/es"):
        backend.open_session("s1", "fr")


def test_duplicate_open_rejected():
    backend = _backend()
    backend.open_session("s1", "en")
    with pytest.raises(RuntimeError, match="already open"):
        backend.open_session("s1", "en")


def test_accept_requires_open_session():
    backend = _backend()
    with pytest.raises(RuntimeError, match="not open"):
        backend.accept("nope", _loud(100), 16000)


def test_sample_rate_mismatch_rejected():
    backend = _backend()
    backend.open_session("s1", "en")
    with pytest.raises(ValueError, match="sample_rate"):
        backend.accept("s1", _loud(100), 24000)


def test_silence_finalizes_utterance():
    backend = _backend(silence_ms=200, min_speech_ms=100)
    backend.open_session("s1", "en")

    assert backend.accept("s1", _loud(150), 16000) == []
    events = backend.accept("s1", _silent(250), 16000)

    assert len(events) == 1
    event = events[0]
    assert event.is_final is True
    assert event.text == "hello there"
    assert event.utterance_id == "s1-u1"
    assert event.session_id == "s1"
    assert event.seq == 1


def test_noise_below_min_speech_is_dropped():
    backend = _backend(silence_ms=200, min_speech_ms=500)
    backend.open_session("s1", "en")

    assert backend.accept("s1", _loud(100), 16000) == []
    assert backend.accept("s1", _silent(250), 16000) == []
    assert backend._model.calls == []


def test_flush_finalizes_open_buffer():
    backend = _backend()
    backend.open_session("s1", "es")
    backend.accept("s1", _loud(100), 16000)

    event = backend.flush("s1")

    assert event is not None
    assert event.is_final is True
    assert backend._model.calls[-1][1] == "es"
    assert backend.flush("s1") is None


def test_close_releases_session():
    backend = _backend()
    backend.open_session("s1", "en")
    backend.close_session("s1")
    assert backend.flush("s1") is None
    with pytest.raises(RuntimeError, match="not open"):
        backend.accept("s1", _loud(50), 16000)
