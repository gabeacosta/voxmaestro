"""WT-MAYA-FULLSTACK-001: real HTTP model bridge, runtime, HTTPS-origin WSS.

All sockets bind loopback in CI. Tailscale identity is a TEST fixture; this
does not claim real Tailscale/SSO or physical iPhone evidence.
"""
from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from voxmaestro.fleet import KernelGenerationAdapter, KernelIntentClassifier
from voxmaestro.integrations.ollama_kernel import create_ollama_kernel
from voxmaestro.integrations.tailnet_stage import make_tailnet_stage
from voxmaestro.integrations.web_session import WebSessionAdapter
from voxmaestro.runtime import VoxMaestroRuntime

ORIGIN = "https://stage.example.ts.net:10000"
HEADERS = {"Origin": ORIGIN, "Tailscale-User-Login": "reviewer@example.com"}
INTENT = "vsai-intent-v7:latest"
GENERATOR = "qwen3:4b-instruct"
SID = "maya_session_00123"


@pytest.mark.asyncio
async def test_real_model_http_bridge_to_full_voxmaestro_websocket_runtime(tmp_path):
    emitted = []
    model = web.Application()

    async def tags(_):
        return web.json_response({"models": [
            {"name": INTENT}, {"name": GENERATOR}
        ]})

    async def chat(request):
        payload = await request.json()
        emitted.append(payload)
        first = payload["messages"][0]["content"]
        if first.startswith("Choose ONE intent"):
            answer = "booking_request" if "book" in (
                payload["messages"][1]["content"].lower()
            ) else "service_question"
        else:
            answer = "Maya can answer questions about the receptionist preview."
        return web.json_response({"message": {"content": answer}})

    model.router.add_get("/api/tags", tags)
    model.router.add_post("/api/chat", chat)
    model_server = TestServer(model, host="127.0.0.1")
    await model_server.start_server()

    bridge = TestClient(TestServer(create_ollama_kernel(
        intent_model=INTENT,
        generation_model=GENERATOR,
        ollama_base=str(model_server.make_url("")).rstrip("/"),
    )))
    try:
        await bridge.start_server()
        kernel_endpoint = str(bridge.make_url("")).rstrip("/")
        config = {
            "schema_version": "0.1.0",
            "agent": {"name": "maya-staging-test", "language": "en"},
            "intent": {
                "provider": "kernel", "endpoint": kernel_endpoint + "/v1/intent",
                "intents": [
                    {"id": "service_question"},
                    {"id": "booking_request"},
                    {"id": "unknown"},
                ],
            },
            "generation": {
                "provider": "kernel",
                "endpoint": kernel_endpoint + "/v1/generate",
                "system_prompt": "Never invent bookings.",
            },
            "states": {
                "initial": {"transitions": {"*": "engage"}},
                "engage": {"transitions": {"*": "engage"}},
            },
            "tools": {},
            "handoff": {"delivery": []},
        }
        classifier = KernelIntentClassifier(
            kernel_endpoint, intents=("service_question", "booking_request", "unknown"),
            timeout_ms=9000,
        )
        generator = KernelGenerationAdapter(kernel_endpoint, timeout_ms=9000)
        runtime = VoxMaestroRuntime(config, intent_classifier=classifier)
        adapter = WebSessionAdapter(runtime, generation_adapter=generator)
        site = tmp_path / "maya"
        for path in [
            "index.html", "styles.css", "demo.js",
            "voxmaestro-voice.mjs", "voxmaestro-voice-ui.mjs",
            "assets/maya-hero.webp",
        ]:
            f = site / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("Maya voice noindex id=\"microphone\" voxmaestro-voice-ui.mjs")

        gateway = TestClient(TestServer(make_tailnet_stage(
            adapter=adapter, site_dir=site, origin=ORIGIN,
            allowed_logins={"reviewer@example.com"},
        )))
        try:
            await gateway.start_server()
            unauthorized = await gateway.post(
                "/api/voice/bootstrap", json={},
                headers={"Origin": ORIGIN},
            )
            assert unauthorized.status == 401
            admission = await gateway.post(
                "/api/voice/bootstrap", json={}, headers=HEADERS
            )
            assert admission.status == 200
            grant = await admission.json()
            assert grant["scope"] == "conversation:readonly"
            ws = await gateway.ws_connect(
                "/session", headers=HEADERS,
                protocols=("voxmaestro.v1", "vsai.ticket." + grant["ticket"]),
            )
            await ws.send_json({
                "type": "start", "sessionId": SID, "locale": "en",
            })
            greeting = await ws.receive_json(timeout=8)
            assert greeting["type"] == "greeting"

            await ws.send_json({
                "type": "message", "sessionId": SID,
                "text": "What can Maya help me with?",
            })
            normal = await ws.receive_json(timeout=15)
            assert normal["type"] == "response"
            assert "Maya can answer questions" in normal["text"]
            assert normal["metadata"]["final"]
            assert normal["metadata"]["intent"] == "service_question"

            await ws.send_json({
                "type": "message", "sessionId": SID,
                "text": "Please book tomorrow",
            })
            blocked = await ws.receive_json(timeout=15)
            assert blocked["type"] == "response"
            assert blocked["text"] == (
                "I cannot check availability or make appointments in this preview."
            )
            assert blocked["metadata"]["intent"] == "booking_request"
            assert [x["model"] for x in emitted] == [
                INTENT, GENERATOR, INTENT,
            ]
            assert all("tools" not in x for x in emitted)
            await ws.close()
            await asyncio.sleep(0.04)
            assert adapter.active_session_ids == ()
        finally:
            await gateway.close()
    finally:
        await bridge.close()
        await model_server.close()
