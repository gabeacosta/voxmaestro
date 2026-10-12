"""WT-VOICE-WSS-ADMISSION-001: real HTTP+WebSocket adversarial qualification."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from voxmaestro.integrations.secure_ingress import (
    AdmissionRejected,
    MemoryVoiceAdmission,
    create_voice_app,
)

ORIGIN = "https://voicescheduleai.com"
OTHER = "https://evil.example"
WSS_URL = "wss://voice.voicescheduleai.com/session"
SESSION = "session_12345678"


class PreviewAdapter:
    def __init__(self, config=None):
        self.runtime = SimpleNamespace(config=config or {"tools": {}, "handoff": {"delivery": []}})
        self.active = set()
        self.calls = []

    async def iter_events(self, frame):
        self.calls.append(dict(frame))
        sid = frame["sessionId"]
        if frame["type"] == "start":
            self.active.add(sid)
            yield {"type": "greeting", "sessionId": sid, "text": "Hi, I'm Maya."}
        elif frame["type"] == "message":
            yield {
                "type": "response",
                "sessionId": sid,
                "text": "No calendar is connected.",
                "metadata": {"final": True},
            }
        elif frame["type"] == "end":
            self.active.discard(sid)


@pytest.fixture
def admission():
    return MemoryVoiceAdmission(allowed_origins={ORIGIN})


async def build_client(admission, adapter=None):
    adapter = adapter or PreviewAdapter()

    # Deliberate TEST FIXTURE ONLY: this header is not a real authenticator.
    # A deployed app must never accept user-controlled identity headers.
    def fixture_auth(request):
        return request.headers.get("X-Test-Identity")

    app = create_voice_app(
        adapter=adapter,
        authenticator=fixture_auth,
        admission=admission,
        wss_url=WSS_URL,
    )
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    return client, adapter


def auth_headers(*, origin=ORIGIN, identity="alice"):
    return {"Origin": origin, "X-Test-Identity": identity}


async def issue(client, *, origin=ORIGIN, identity="alice"):
    response = await client.post(
        "/api/voice/bootstrap",
        json={},
        headers=auth_headers(origin=origin, identity=identity),
    )
    return response


def protocols(token):
    return ("voxmaestro.v1", "vsai.ticket." + token)


@pytest.mark.asyncio
async def test_denies_unauthenticated_bootstrap_bad_origin_and_non_json(admission):
    client, _ = await build_client(admission)
    try:
        response = await issue(client, identity="")
        assert response.status == 401
        response = await issue(client, origin=OTHER)
        assert response.status == 403
        response = await client.post(
            "/api/voice/bootstrap",
            data="{}", headers={**auth_headers(), "Content-Type": "text/plain"},
        )
        assert response.status == 415
        response = await client.post(
            "/api/voice/bootstrap", json={"scope": "booking:write"},
            headers=auth_headers(),
        )
        assert response.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bootstrap_returns_bounded_private_ticket(admission):
    client, _ = await build_client(admission)
    try:
        response = await issue(client)
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        data = await response.json()
        assert data["scope"] == "conversation:readonly"
        assert len(data["ticket"]) >= 24
        assert data["wssUrl"] == WSS_URL
        assert data["expiresAt"].endswith("Z")
        assert data["ticket"] not in repr(client.server.app)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_redeem_single_use_atomic_race(admission):
    token, _ = await admission.issue("alice", ORIGIN)

    async def attempt():
        try:
            await admission.redeem(token, principal="alice", origin=ORIGIN)
            return "accepted"
        except AdmissionRejected:
            return "denied"

    results = await asyncio.gather(*[attempt() for _ in range(25)])
    assert results.count("accepted") == 1
    assert results.count("denied") == 24
    await admission.release("alice")


@pytest.mark.asyncio
async def test_wrong_principal_origin_and_expiry_fail_closed():
    ticks = [100.0]
    registry = MemoryVoiceAdmission(
        allowed_origins={ORIGIN, "https://review.example"},
        ticket_ttl_seconds=5,
        monotonic=lambda: ticks[0],
    )
    token, _ = await registry.issue("alice", ORIGIN)
    with pytest.raises(AdmissionRejected):
        await registry.redeem(token, principal="bob", origin=ORIGIN)
    # The first failed attempt consumes the ticket; don't permit later replay.
    with pytest.raises(AdmissionRejected):
        await registry.redeem(token, principal="alice", origin=ORIGIN)
    token2, _ = await registry.issue("alice", ORIGIN)
    ticks[0] += 6
    with pytest.raises(AdmissionRejected):
        await registry.redeem(token2, principal="alice", origin=ORIGIN)
    with pytest.raises(AdmissionRejected):
        await registry.redeem("bad", principal="alice", origin=ORIGIN)


@pytest.mark.asyncio
async def test_real_websocket_start_turn_close_and_ticket_replay(admission):
    client, adapter = await build_client(admission)
    try:
        token = (await (await issue(client)).json())["ticket"]
        ws = await client.ws_connect(
            "/session",
            headers=auth_headers(),
            protocols=protocols(token),
        )
        assert ws.protocol == "voxmaestro.v1"  # Never reflect bearer ticket.
        await ws.send_json({"type": "start", "sessionId": SESSION, "locale": "en"})
        greeting = await ws.receive_json(timeout=3)
        assert greeting["type"] == "greeting"
        await ws.send_json({"type": "message", "sessionId": SESSION, "text": "Hi"})
        response = await ws.receive_json(timeout=3)
        assert response["type"] == "response"
        assert "No calendar" in response["text"]
        assert SESSION in adapter.active
        await ws.close()
        await asyncio.sleep(0.03)
        assert SESSION not in adapter.active
        assert any(x["type"] == "end" for x in adapter.calls)
        with pytest.raises(WSServerHandshakeError) as replay:
            await client.ws_connect(
                "/session", headers=auth_headers(), protocols=protocols(token)
            )
        assert replay.value.status == 403
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_websocket_rejects_wrong_origin_identity_and_no_ticket(admission):
    client, adapter = await build_client(admission)
    try:
        token = (await (await issue(client)).json())["ticket"]
        for headers, proto in [
            (auth_headers(origin=OTHER), protocols(token)),
            (auth_headers(identity="mallory"), protocols(token)),
            (auth_headers(), ("voxmaestro.v1",)),
        ]:
            with pytest.raises(WSServerHandshakeError) as error:
                await client.ws_connect("/session", headers=headers, protocols=proto)
            assert error.value.status == 403
        assert not adapter.active
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_websocket_mismatched_session_is_closed_and_cleaned(admission):
    client, adapter = await build_client(admission)
    try:
        token = (await (await issue(client)).json())["ticket"]
        ws = await client.ws_connect("/session", headers=auth_headers(), protocols=protocols(token))
        await ws.send_json({"type": "start", "sessionId": SESSION, "locale": "en"})
        await ws.receive_json(timeout=3)
        await ws.send_json({"type": "message", "sessionId": "foreign_session123", "text": "secret"})
        message = await ws.receive(timeout=3)
        assert message.type.name in {"CLOSE", "CLOSING", "CLOSED"}
        await asyncio.sleep(0.03)
        assert SESSION not in adapter.active
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_connection_budget_is_enforced_and_released(admission):
    client, adapter = await build_client(admission)
    try:
        token1 = (await (await issue(client)).json())["ticket"]
        ws = await client.ws_connect("/session", headers=auth_headers(), protocols=protocols(token1))
        await ws.send_json({"type": "start", "sessionId": SESSION})
        await ws.receive_json(timeout=3)
        token2 = (await (await issue(client)).json())["ticket"]
        with pytest.raises(WSServerHandshakeError) as error:
            await client.ws_connect("/session", headers=auth_headers(), protocols=protocols(token2))
        assert error.value.status == 403
        await ws.close()
        await asyncio.sleep(0.03)
        token3 = (await (await issue(client)).json())["ticket"]
        ws2 = await client.ws_connect("/session", headers=auth_headers(), protocols=protocols(token3))
        await ws2.send_json({"type": "start", "sessionId": "session_0000"})
        assert (await ws2.receive_json(timeout=3))["type"] == "greeting"
        await ws2.close()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_consequential_configs_rejected_before_server_runs(admission):
    for config in [
        {"tools": {"book": {"endpoint": "https://example.com"}}},
        {"tools": {}, "handoff": {"delivery": [{"target": "crm"}]}},
    ]:
        with pytest.raises(ValueError):
            create_voice_app(
                adapter=PreviewAdapter(config=config),
                admission=admission,
                authenticator=lambda r: "alice",
                wss_url=WSS_URL,
            )


@pytest.mark.asyncio
async def test_http_message_rejects_nonstart_and_binary(admission):
    client, adapter = await build_client(admission)
    try:
        token = (await (await issue(client)).json())["ticket"]
        ws = await client.ws_connect("/session", headers=auth_headers(), protocols=protocols(token))
        await ws.send_json({"type": "message", "sessionId": SESSION, "text": "skip start"})
        msg = await ws.receive(timeout=3)
        assert msg.type.name in {"CLOSE", "CLOSING", "CLOSED"}
        assert not adapter.active
        token2 = (await (await issue(client)).json())["ticket"]
        ws2 = await client.ws_connect("/session", headers=auth_headers(), protocols=protocols(token2))
        await ws2.send_json({"type": "start", "sessionId": "session_2222"})
        await ws2.receive_json(timeout=3)
        await ws2.send_bytes(b"\x00\x01")
        msg = await ws2.receive(timeout=3)
        assert msg.type.name in {"CLOSE", "CLOSING", "CLOSED"}
        await asyncio.sleep(0.03)
        assert not adapter.active
    finally:
        await client.close()
