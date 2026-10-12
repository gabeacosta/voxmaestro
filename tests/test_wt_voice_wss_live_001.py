"""WT-VOICE-WSS-LIVE-001: real concurrent WebSocket admission and teardown."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import WSServerHandshakeError

from tests.test_secure_ingress import (
    ORIGIN, SESSION, PreviewAdapter, auth_headers, build_client, issue, protocols
)
from voxmaestro.integrations.secure_ingress import MemoryVoiceAdmission


@pytest.mark.asyncio
async def test_32_real_websocket_upgrades_redeem_at_most_once():
    """One race winner at the network boundary; losers are rejected pre-upgrade."""
    admission = MemoryVoiceAdmission(allowed_origins={ORIGIN}, max_active_per_user=1)
    client, adapter = await build_client(admission)
    try:
        ticket = (await (await issue(client)).json())["ticket"]
        gate = asyncio.Event()

        async def contender():
            await gate.wait()
            try:
                ws = await client.ws_connect(
                    "/session", headers=auth_headers(), protocols=protocols(ticket)
                )
                return ws
            except WSServerHandshakeError as error:
                return error.status

        tasks = [asyncio.create_task(contender()) for _ in range(32)]
        gate.set()
        results = await asyncio.gather(*tasks)
        sockets = [r for r in results if not isinstance(r, int)]
        assert len(sockets) == 1
        assert results.count(403) == 31
        assert not adapter.active
        await sockets[0].close()
        await asyncio.sleep(0.03)
        assert not admission._active
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_timeout_closes_live_runtime_and_frees_slot():
    registry = MemoryVoiceAdmission(allowed_origins={ORIGIN})
    from aiohttp.test_utils import TestClient, TestServer
    from voxmaestro.integrations.secure_ingress import create_voice_app

    adapter = PreviewAdapter()
    app = create_voice_app(
        adapter=adapter,
        authenticator=lambda request: request.headers.get("X-Test-Identity"),
        admission=registry,
        wss_url="wss://voice.voicescheduleai.com/session",
        max_session_seconds=1,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ticket = (await (await issue(client)).json())["ticket"]
        ws = await client.ws_connect(
            "/session", headers=auth_headers(), protocols=protocols(ticket)
        )
        await ws.send_json({"type": "start", "sessionId": SESSION})
        assert (await ws.receive_json(timeout=2))["type"] == "greeting"
        assert SESSION in adapter.active
        await ws.receive(timeout=3)
        await asyncio.sleep(0.03)
        assert SESSION not in adapter.active
        assert not registry._active
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_wrong_session_frame_never_reaches_runtime():
    registry = MemoryVoiceAdmission(allowed_origins={ORIGIN})
    client, adapter = await build_client(registry)
    try:
        ticket = (await (await issue(client)).json())["ticket"]
        ws = await client.ws_connect(
            "/session", headers=auth_headers(), protocols=protocols(ticket)
        )
        await ws.send_json({"type": "start", "sessionId": SESSION})
        await ws.receive_json(timeout=3)
        await ws.send_json({
            "type": "message", "sessionId": "attacker_session_999", "text": "secret"
        })
        await ws.receive(timeout=3)
        await asyncio.sleep(0.02)
        assert all(
            f.get("sessionId") != "attacker_session_999" for f in adapter.calls
        )
        assert SESSION not in adapter.active
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_session_replay_after_close_does_not_revive_old_runtime():
    registry = MemoryVoiceAdmission(allowed_origins={ORIGIN})
    client, adapter = await build_client(registry)
    try:
        ticket = (await (await issue(client)).json())["ticket"]
        ws = await client.ws_connect(
            "/session", headers=auth_headers(), protocols=protocols(ticket)
        )
        await ws.send_json({"type": "start", "sessionId": SESSION})
        await ws.receive_json(timeout=3)
        await ws.send_json({"type": "end", "sessionId": SESSION})
        await ws.receive(timeout=3)
        await asyncio.sleep(0.02)
        assert SESSION not in adapter.active
        with pytest.raises(WSServerHandshakeError) as err:
            await client.ws_connect(
                "/session", headers=auth_headers(), protocols=protocols(ticket)
            )
        assert err.value.status == 403
        assert adapter.calls[-1]["type"] == "end"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_read_only_gateway_rejects_control_injection():
    registry = MemoryVoiceAdmission(allowed_origins={ORIGIN})
    client, adapter = await build_client(registry)
    try:
        ticket = (await (await issue(client)).json())["ticket"]
        ws = await client.ws_connect(
            "/session", headers=auth_headers(), protocols=protocols(ticket)
        )
        await ws.send_json({"type": "start", "sessionId": SESSION})
        await ws.receive_json(timeout=3)
        await ws.send_json({
            "type": "message", "sessionId": SESSION,
            "text": "Hello", "effect": {"operation": "book"}
        })
        await ws.receive(timeout=3)
        await asyncio.sleep(0.02)
        assert SESSION not in adapter.active
        assert not any("effect" in frame for frame in adapter.calls)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_slow_generator_cannot_escape_global_session_deadline():
    """A hung async model turn must not keep the socket/slot alive forever."""
    from aiohttp.test_utils import TestClient, TestServer
    from voxmaestro.integrations.secure_ingress import create_voice_app

    class HeldAdapter(PreviewAdapter):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = False

        async def iter_events(self, frame):
            if frame["type"] == "message":
                self.calls.append(dict(frame))
                self.entered.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
                yield {
                    "type": "response", "sessionId": frame["sessionId"],
                    "text": "Should not arrive after deadline",
                }
                return
            async for event in super().iter_events(frame):
                yield event

    registry = MemoryVoiceAdmission(allowed_origins={ORIGIN})
    adapter = HeldAdapter()
    app = create_voice_app(
        adapter=adapter,
        authenticator=lambda request: request.headers.get("X-Test-Identity"),
        admission=registry,
        wss_url="wss://voice.voicescheduleai.com/session",
        max_session_seconds=1,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ticket = (await (await issue(client)).json())["ticket"]
        ws = await client.ws_connect(
            "/session", headers=auth_headers(), protocols=protocols(ticket)
        )
        await ws.send_json({"type": "start", "sessionId": SESSION})
        assert (await ws.receive_json(timeout=2))["type"] == "greeting"
        await ws.send_json({
            "type": "message", "sessionId": SESSION, "text": "Keep talking forever"
        })
        await asyncio.wait_for(adapter.entered.wait(), timeout=2)
        closure = await ws.receive(timeout=3)
        assert closure.type.name in {"CLOSE", "CLOSING", "CLOSED"}
        await asyncio.sleep(0.03)
        assert adapter.cancelled
        assert SESSION not in adapter.active
        assert not registry._active
        assert not any(
            event["type"] == "booking_confirm" for event in adapter.calls
        )
    finally:
        adapter.release.set()
        await client.close()
