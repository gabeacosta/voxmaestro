"""WT-VOICE-TAILNET-001: same-origin site + voice API under proxy identity."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from voxmaestro.integrations.tailnet_stage import (
    check_tailnet_origin,
    make_tailnet_stage,
    stage_site_files,
)

ORIGIN = "https://gabes-mini.example-tailnet.ts.net:8443"
GOOD = {"Tailscale-User-Login": "alice@example.com"}
SESSION = "session_stage_000001"


class ReadOnlyAdapter:
    def __init__(self):
        self.runtime = SimpleNamespace(config={"tools": {}, "handoff": {"delivery": []}})
        self.active = set()

    async def iter_events(self, frame):
        sid = frame["sessionId"]
        if frame["type"] == "start":
            self.active.add(sid)
            yield {"type": "greeting", "sessionId": sid, "text": "Hi, I'm Maya."}
        elif frame["type"] == "message":
            yield {"type": "response", "sessionId": sid, "text": "Read-only answer",
                   "metadata": {"final": True}}
        elif frame["type"] == "end":
            self.active.discard(sid)


@pytest.fixture
def site(tmp_path):
    for filename in (
        "index.html", "styles.css", "demo.js", "voxmaestro-voice.mjs",
        "voxmaestro-voice-ui.mjs", "assets/maya-hero.webp",
    ):
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"placeholder")
    (tmp_path / "index.html").write_text(
        '<html><head><meta name="robots" content="noindex"></head>'
        '<body><button id="microphone"></button>'
        '<script type="module" src="voxmaestro-voice-ui.mjs"></script></body></html>'
    )
    (tmp_path / "styles.css").write_text("body{color:white}")
    (tmp_path / "voxmaestro-voice-ui.mjs").write_text("export const live = true;")
    return tmp_path


async def stage_client(site, adapter=None):
    adapter = adapter or ReadOnlyAdapter()
    app = make_tailnet_stage(
        adapter=adapter, site_dir=site, origin=ORIGIN,
        allowed_logins={"alice@example.com"},
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, adapter


def headers(*, identity="alice@example.com", origin=None):
    d = {"Tailscale-User-Login": identity}
    if origin:
        d["Origin"] = origin
    return d


@pytest.mark.asyncio
async def test_identity_required_for_every_static_asset(site):
    client, _ = await stage_client(site)
    try:
        for path in ("/", "/styles.css", "/demo.js",
                     "/voxmaestro-voice.mjs", "/voxmaestro-voice-ui.mjs",
                     "/assets/maya-hero.webp"):
            assert (await client.get(path)).status == 401
            assert (await client.get(path, headers=headers(identity="bob@example.com"))).status == 401
            r = await client.get(path, headers=GOOD)
            assert r.status == 200, path
            assert r.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
            assert r.headers["Cache-Control"] == "no-store"
            assert "wss://gabes-mini.example-tailnet.ts.net:8443" in r.headers["Content-Security-Policy"]
            assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert (await (await client.get("/styles.css", headers=GOOD)).text()) == "body{color:white}"
        assert (await client.get("/api/secrets", headers=GOOD)).status == 404
        assert (await client.get("/../../pyproject.toml", headers=GOOD)).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_authenticated_bootstrap_and_read_only_websocket(site):
    client, adapter = await stage_client(site)
    try:
        assert (await client.post(
            "/api/voice/bootstrap", json={},
            headers=headers(identity="bob@example.com", origin=ORIGIN),
        )).status == 401
        assert (await client.post(
            "/api/voice/bootstrap", json={},
            headers=headers(origin="https://evil.ts.net:8443"),
        )).status == 403
        response = await client.post(
            "/api/voice/bootstrap", json={}, headers=headers(origin=ORIGIN),
        )
        assert response.status == 200
        ticket = (await response.json())["ticket"]
        assert (await response.json())["scope"] == "conversation:readonly"
        ws = await client.ws_connect(
            "/session", headers=headers(origin=ORIGIN),
            protocols=("voxmaestro.v1", "vsai.ticket." + ticket),
        )
        assert ws.protocol == "voxmaestro.v1"
        await ws.send_json({"type": "start", "sessionId": SESSION, "locale": "en"})
        assert (await ws.receive_json(timeout=3))["type"] == "greeting"
        await ws.send_json({"type": "message", "sessionId": SESSION, "text": "Hello"})
        assert (await ws.receive_json(timeout=3))["text"] == "Read-only answer"
        await ws.close()
        await __import__("asyncio").sleep(0.03)
        assert SESSION not in adapter.active
        with pytest.raises(WSServerHandshakeError) as replay:
            await client.ws_connect(
                "/session", headers=headers(origin=ORIGIN),
                protocols=("voxmaestro.v1", "vsai.ticket." + ticket),
            )
        assert replay.value.status == 403
    finally:
        await client.close()


def test_tailnet_only_origin_policy_and_missing_files(site, tmp_path):
    assert check_tailnet_origin(ORIGIN) == ORIGIN
    for bad in (
        "https://example.com:8443",
        "http://gabes-mini.example-tailnet.ts.net:8443",
        "https://gabes-mini.example-tailnet.ts.net",
        "https://gabes-mini.example-tailnet.ts.net:443",
        "https://gabes-mini.example-tailnet.ts.net:8443/path",
        "https://evil.ts.net:8443@attacker.example",
    ):
        with pytest.raises(ValueError):
            check_tailnet_origin(bad)
    (site / "assets/maya-hero.webp").unlink()
    with pytest.raises(FileNotFoundError):
        stage_site_files(site)


def test_approved_login_mandatory_and_effectful_adapter_denied(site):
    with pytest.raises(ValueError):
        make_tailnet_stage(
            adapter=ReadOnlyAdapter(), site_dir=site, origin=ORIGIN, allowed_logins=set(),
        )
    adapter = ReadOnlyAdapter()
    adapter.runtime.config = {"tools": {"book": {}}, "handoff": {"delivery": []}}
    with pytest.raises(ValueError):
        make_tailnet_stage(
            adapter=adapter, site_dir=site, origin=ORIGIN,
            allowed_logins={"alice@example.com"},
        )
