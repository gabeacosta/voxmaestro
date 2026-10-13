"""WT-VOICE-AUDIO-001: authenticated model-grounded WAV playback contract."""
from __future__ import annotations

import io
import wave

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.test_tailnet_stage import (
    ORIGIN, SESSION, GOOD, ReadOnlyAdapter, headers, site
)
from voxmaestro.integrations.private_speech import (
    PrivateSpeechClips, speaker_probe, mac_speech_bytes,
)
from voxmaestro.integrations.tailnet_stage import make_tailnet_stage

# Re-export the pre-existing fixture for pytest; also mark it as deliberately used.
__all__ = ["site"]


async def fixed_voice(_text: str) -> bytes:
    return speaker_probe()


async def build_audio_app(site, *, store=None):
    adapter = ReadOnlyAdapter()
    app = make_tailnet_stage(
        adapter=adapter, site_dir=site, origin=ORIGIN,
        allowed_logins={"alice@example.com"},
        speech_clips=store or PrivateSpeechClips(renderer=fixed_voice),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, adapter


@pytest.mark.asyncio
async def test_tone_requires_tailnet_identity_and_is_real_decodable_wav(site):
    client, _ = await build_audio_app(site)
    try:
        assert (await client.get("/api/voice/audio-test.wav")).status == 401
        assert (await client.get(
            "/api/voice/audio-test.wav",
            headers={"Tailscale-User-Login": "mallory@example.com"},
        )).status == 401
        result = await client.get("/api/voice/audio-test.wav", headers=GOOD)
        assert result.status == 200
        assert result.headers["Content-Type"] == "audio/wav"
        data = await result.read()
        assert data.startswith(b"RIFF") and data[8:12] == b"WAVE"
        with wave.open(io.BytesIO(data), "rb") as audio:
            assert audio.getnchannels() == 1
            assert audio.getframerate() == 22050
            assert audio.getnframes() > 15000
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_ws_final_response_mints_user_bound_audio_with_range(site):
    store = PrivateSpeechClips(renderer=fixed_voice)
    client, _ = await build_audio_app(site, store=store)
    try:
        bootstrap = await client.post(
            "/api/voice/bootstrap", json={}, headers=headers(origin=ORIGIN)
        )
        token = (await bootstrap.json())["ticket"]
        ws = await client.ws_connect(
            "/session", headers=headers(origin=ORIGIN),
            protocols=("voxmaestro.v1", "vsai.ticket." + token),
        )
        await ws.send_json({"type": "start", "sessionId": SESSION, "locale": "en"})
        greeting = await ws.receive_json(timeout=3)
        assert "speechId" not in greeting

        await ws.send_json({
            "type": "message", "sessionId": SESSION, "text": "What is Maya?"
        })
        answer = await ws.receive_json(timeout=3)
        assert answer["type"] == "response"
        assert answer["metadata"]["final"] is True
        assert len(answer["speechId"]) >= 24
        audio_path = "/api/voice/audio/" + answer["speechId"]

        assert (await client.get(audio_path)).status == 401
        assert (await client.get(audio_path, headers=headers(identity="mallory@example.com"))).status == 401

        full = await client.get(audio_path, headers=GOOD)
        assert full.status == 200
        audio_bytes = await full.read()
        assert audio_bytes.startswith(b"RIFF")
        partial = await client.get(
            audio_path, headers={**GOOD, "Range": "bytes=0-1023"}
        )
        assert partial.status == 206
        assert partial.headers["Content-Range"] == f"bytes 0-1023/{len(audio_bytes)}"
        assert await partial.read() == audio_bytes[:1024]

        suffix = await client.get(
            audio_path, headers={**GOOD, "Range": "bytes=-100"}
        )
        assert suffix.status == 206
        assert await suffix.read() == audio_bytes[-100:]

        bad = await client.get(
            audio_path, headers={**GOOD, "Range": "bytes=99999999-"}
        )
        assert bad.status == 416

        await ws.close()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_speech_ids_expire_and_cannot_cross_principals():
    clock = [100.]
    store = PrivateSpeechClips(renderer=fixed_voice, ttl=15, clock=lambda: clock[0])
    key = store.issue("alice@example.com", "A qualified reply")
    assert key
    assert await store.audio("mallory@example.com", key) is None
    assert await store.audio("alice@example.com", key) == speaker_probe()
    clock[0] += 16
    assert await store.audio("alice@example.com", key) is None
    assert store.issue("alice@example.com", "x" * 421) is None


@pytest.mark.asyncio
async def test_invalid_or_untrusted_audio_is_never_served():
    async def fake_text(_):
        return b"TEXT INSTEAD OF AUDIO"
    store = PrivateSpeechClips(renderer=fake_text)
    key = store.issue("alice@example.com", "hello")
    assert await store.audio("alice@example.com", key) is None
    assert await store.audio("alice@example.com", "../secrets") is None


def test_mac_renderer_refuses_empty_or_oversized_input_without_running_subprocess():
    for text in ("", " ", "x" * 421):
        with pytest.raises(ValueError):
            mac_speech_bytes(text)
