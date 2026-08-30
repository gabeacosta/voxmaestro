from __future__ import annotations

import pytest

from voxmaestro.asr import (
    SHIPPING_ASR_LANGUAGES,
    ASRCapabilities,
    AsrIngress,
    TranscriptEvent,
)


def _caps() -> ASRCapabilities:
    return ASRCapabilities(
        backend_id="fake-asr",
        backend_version="test",
        languages=SHIPPING_ASR_LANGUAGES,
        sample_rates=(24000,),
        streaming=True,
        advertised_from="runtime-probe",
    )


def test_capabilities_must_be_runtime_probed():
    with pytest.raises(ValueError, match="runtime-probe"):
        ASRCapabilities(
            backend_id="fake-asr",
            backend_version="test",
            languages=SHIPPING_ASR_LANGUAGES,
            sample_rates=(24000,),
            streaming=True,
            advertised_from="readme",
        )


def test_capabilities_require_version():
    with pytest.raises(ValueError, match="backend_version"):
        ASRCapabilities(
            backend_id="fake-asr",
            backend_version="",
            languages=SHIPPING_ASR_LANGUAGES,
            sample_rates=(24000,),
            streaming=True,
            advertised_from="runtime-probe",
        )


def test_transcript_event_requires_ids():
    with pytest.raises(ValueError, match="session_id"):
        TranscriptEvent(session_id="", utterance_id="u1", seq=0, text="hi")
    with pytest.raises(ValueError, match="utterance_id"):
        TranscriptEvent(session_id="s1", utterance_id="", seq=0, text="hi")


def test_transcript_event_seq_and_confidence():
    with pytest.raises(ValueError, match="seq"):
        TranscriptEvent(session_id="s1", utterance_id="u1", seq=-1, text="hi")
    with pytest.raises(ValueError, match="confidence"):
        TranscriptEvent(
            session_id="s1",
            utterance_id="u1",
            seq=0,
            text="hi",
            confidence=1.5,
        )


class FakeASRBackend:
    def __init__(self) -> None:
        self.opened: list[tuple[str, str]] = []
        self.closed: list[str] = []
        self.events: list[TranscriptEvent] = []
        self.flush_event = None

    def capabilities(self) -> ASRCapabilities:
        return _caps()

    def open_session(self, session_id: str, language: str) -> None:
        self.opened.append((session_id, language))

    def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)

    def accept(self, session_id: str, pcm: bytes, sample_rate: int):
        return self.events

    def flush(self, session_id: str):
        return self.flush_event


def _event(**overrides) -> TranscriptEvent:
    base = dict(session_id="s1", utterance_id="u1", seq=0, text="")
    base.update(overrides)
    return TranscriptEvent(**base)


def test_ingress_maps_final_to_message_and_partial_passthrough():
    backend = FakeASRBackend()
    backend.events = [
        _event(seq=0, text="hel"),
        _event(seq=1, text="hello", is_final=True, confidence=0.9),
    ]
    ingress = AsrIngress(backend)

    out = ingress.accept("s1", b"\x00\x01", 24000)

    assert out[0]["type"] == "transcript_partial"
    assert out[0]["text"] == "hel"
    assert out[0]["utteranceId"] == "u1"
    assert out[1] == {
        "type": "message",
        "sessionId": "s1",
        "text": "hello",
        "metadata": {"source": "asr", "utteranceId": "u1"},
    }


def test_ingress_drops_empty_final():
    backend = FakeASRBackend()
    backend.events = [_event(text="   ", is_final=True)]
    assert AsrIngress(backend).accept("s1", b"\x00", 24000) == []


def test_ingress_open_flush_close():
    backend = FakeASRBackend()
    ingress = AsrIngress(backend, language="es")

    ingress.open("s1")
    assert backend.opened == [("s1", "es")]
    ingress.open("s2", language="en")
    assert backend.opened[-1] == ("s2", "en")

    assert ingress.flush("s1") == []
    backend.flush_event = _event(text="adios", is_final=True)
    out = ingress.flush("s1")
    assert out[0]["type"] == "message"
    assert out[0]["text"] == "adios"

    ingress.close("s1")
    assert backend.closed == ["s1"]
