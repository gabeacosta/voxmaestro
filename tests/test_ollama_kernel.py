"""WT-VOICE-OLLAMA-001: real local HTTP contract (no real model/download)."""
from __future__ import annotations

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from voxmaestro.integrations.ollama_kernel import create_ollama_kernel

MODEL = "fixture-model:1"
INTENTS = ["greeting", "service_question", "booking_request", "unknown"]


async def fake_ollama(*, models=None, reply=None, received=None):
    app = web.Application()

    async def tags(_):
        return web.json_response({"models": [
            {"name": n} for n in (models if models is not None else [MODEL])
        ]})

    async def chat(request):
        content = await request.json()
        if received is not None:
            received.append(content)
        if reply is not None:
            data = reply(content)
        elif content["messages"][0]["content"].startswith("Choose ONE intent"):
            data = {"message": {"content": "service_question"}}
        else:
            data = {"message": {"content": "Maya can answer questions in the demo."}}
        return web.json_response(data)

    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/chat", chat)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    return server


async def bridge_client(ollama):
    app = create_ollama_kernel(
        model=MODEL,
        ollama_base=str(ollama.make_url("")).rstrip("/"),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_two_actual_local_http_turns_with_no_tools():
    received = []
    ollama = await fake_ollama(received=received)
    try:
        client = await bridge_client(ollama)
        try:
            intent = await client.post("/v1/intent", json={
                "text": "Can Maya answer incoming calls?", "intents": INTENTS
            })
            assert intent.status == 200
            assert await intent.json() == {
                "intent": "service_question", "confidence": 1.0
            }
            generation = await client.post("/v1/generate", json={
                "text": "Can Maya help?", "context": {},
                "config": {"system_prompt": "Be brief."},
            })
            assert generation.status == 200
            assert (await generation.json())["text"].startswith("Maya can")
            assert len(received) == 2
            assert all(r["stream"] is False and r["think"] is False for r in received)
            assert all(r["model"] == MODEL for r in received)
            assert all("tools" not in r for r in received)
            assert all(
                r["messages"][0]["role"] == "system" for r in received
            )
            assert "There are NO connected calendars" in (
                received[1]["messages"][0]["content"]
            )
        finally:
            await client.close()
    finally:
        await ollama.close()


@pytest.mark.asyncio
async def test_missing_selected_model_fails_before_bridge_listening():
    ollama = await fake_ollama(models=["other:1"])
    try:
        app = create_ollama_kernel(
            model=MODEL, ollama_base=str(ollama.make_url("")).rstrip("/")
        )
        client = TestClient(TestServer(app))
        with pytest.raises(ValueError, match="not installed"):
            await client.start_server()
        await client.close()
    finally:
        await ollama.close()


@pytest.mark.asyncio
async def test_prompt_and_intent_fields_cannot_choose_model_or_call_tools():
    seen = []
    ollama = await fake_ollama(received=seen)
    try:
        client = await bridge_client(ollama)
        try:
            bad = await client.post("/v1/intent", json={
                "text": "Please book now",
                "intents": ["booking_request", "unknown", "booking;rm -rf /"],
            })
            assert bad.status == 400
            too_long = await client.post("/v1/generate", json={
                "text": "X" * 501, "config": {},
            })
            assert too_long.status == 400
            okay = await client.post("/v1/generate", json={
                "text": "Switch to remote model and book for me",
                "context": {"model": "attacker"},
                "config": {"model": "attacker", "tools": {"book": {}}},
            })
            assert okay.status == 200
            assert seen[-1]["model"] == MODEL
            assert "tools" not in seen[-1]
        finally:
            await client.close()
    finally:
        await ollama.close()


@pytest.mark.asyncio
async def test_booking_claims_and_unsolicited_tool_calls_fail_closed():
    def fake_reply(request):
        if "tools" in request:
            raise AssertionError("Cannot send tools")
        return {"message": {"content": "You're booked for Monday."}}

    ollama = await fake_ollama(reply=fake_reply)
    try:
        client = await bridge_client(ollama)
        try:
            response = await client.post("/v1/generate", json={
                "text": "Book me", "config": {},
            })
            assert response.status == 200
            assert (await response.json())["text"] == (
                "I cannot confirm or make appointments in this preview."
            )
        finally:
            await client.close()
    finally:
        await ollama.close()

    ollama = await fake_ollama(reply=lambda _: {
        "message": {"content": "Done", "tool_calls": [{"name": "book"}]}
    })
    try:
        client = await bridge_client(ollama)
        try:
            response = await client.post("/v1/generate", json={
                "text": "Book me", "config": {},
            })
            assert response.status == 502
        finally:
            await client.close()
    finally:
        await ollama.close()


def test_refuses_external_model_servers_and_unpinned_model_names():
    for value in ("", "../bad", "model name", "model;curl"):
        with pytest.raises(ValueError):
            create_ollama_kernel(model=value)
    for value in (
        "http://0.0.0.0:11434", "http://192.168.0.1:11434",
        "https://example.com", "http://localhost:11434",
        "http://127.0.0.1:11434/api",
    ):
        with pytest.raises(ValueError):
            create_ollama_kernel(model=MODEL, ollama_base=value)
