from __future__ import annotations

import pytest

from voxmaestro.fleet import (
    KernelGenerationAdapter,
    KernelIntentClassifier,
    RemoteWorkerGenerationAdapter,
    fleet_from_config,
)


class _Ctx:
    call_id = "c1"
    current_state = "engage"
    intent_history = ["greeting"]


def _post_ok(url, payload, timeout_ms):
    return {"intent": "availability_question", "confidence": 0.9}


@pytest.mark.asyncio
async def test_intent_passthrough():
    classify = KernelIntentClassifier("http://k:7788/", post_fn=_post_ok)
    assert await classify("Thursday?", _Ctx()) == "availability_question"


@pytest.mark.asyncio
async def test_intent_failure_is_unknown():
    def boom(url, payload, timeout_ms):
        raise OSError("connection refused")

    classify = KernelIntentClassifier("http://k:7788", post_fn=boom)
    assert await classify("hi", _Ctx()) == "unknown"


@pytest.mark.asyncio
async def test_low_confidence_is_unknown():
    def low(url, payload, timeout_ms):
        return {"intent": "booking_request", "confidence": 0.2}

    classify = KernelIntentClassifier("http://k", min_confidence=0.5, post_fn=low)
    assert await classify("book me", _Ctx()) == "unknown"


@pytest.mark.asyncio
async def test_intent_payload_shape():
    seen = {}

    def capture(url, payload, timeout_ms):
        seen.update(payload)
        seen["url"] = url
        return {"intent": "greeting"}

    classify = KernelIntentClassifier(
        "http://k:7788", intents=("greeting", "unknown"), post_fn=capture
    )
    assert await classify("hello", _Ctx()) == "greeting"
    assert seen["url"] == "http://k:7788/v1/intent"
    assert seen["text"] == "hello"
    assert seen["call_id"] == "c1"
    assert seen["state"] == "engage"
    assert seen["intent_history"] == ["greeting"]
    assert seen["intents"] == ["greeting", "unknown"]


@pytest.mark.asyncio
async def test_generation_round_trip():
    def post(url, payload, timeout_ms):
        assert url == "http://k:7788/v1/generate"
        assert payload["text"] == "hi"
        assert payload["config"]["model"] == "l1"
        return {"text": "Hello there."}

    generate = KernelGenerationAdapter("http://k:7788", post_fn=post)
    assert await generate("hi", {"call_id": "c1"}, {"model": "l1"}) == "Hello there."


@pytest.mark.asyncio
async def test_generation_empty_raises():
    def post(url, payload, timeout_ms):
        return {"text": "  "}

    generate = KernelGenerationAdapter("http://k", post_fn=post)
    with pytest.raises(RuntimeError, match="no text"):
        await generate("hi", {}, {})


@pytest.mark.asyncio
async def test_generation_error_propagates():
    def boom(url, payload, timeout_ms):
        raise OSError("down")

    generate = KernelGenerationAdapter("http://k", post_fn=boom)
    with pytest.raises(OSError, match="down"):
        await generate("hi", {}, {})


@pytest.mark.asyncio
async def test_remote_worker_envelope_is_bounded_and_strips_secrets():
    seen = {}

    def post(url, payload, timeout_ms):
        seen["url"] = url
        seen["payload"] = payload
        seen["timeout_ms"] = timeout_ms
        return {
            "status": "ok",
            "worker_id": "contabo-vps8-qwen3-4b",
            "slot": "l1_worker",
            "request_id": "c1",
            "result": {"text": "Remote answer."},
        }

    generate = RemoteWorkerGenerationAdapter(
        "http://100.64.0.10:7788",
        worker_id="contabo-vps8-qwen3-4b",
        post_fn=post,
    )
    result = await generate(
        "hi",
        {
            "call_id": "c1",
            "state": "engage",
            "api_key": "do-not-send",
            "nested": {"access_token": "also-secret", "safe": "ok"},
        },
        {"model": "qwen3:4b", "max_tokens": 120, "secret": "not-forwarded"},
    )

    assert result == "Remote answer."
    assert seen["url"] == "http://100.64.0.10:7788/v1/work"
    payload = seen["payload"]
    assert payload["contract_version"] == "remote_worker.v0"
    assert payload["worker_id"] == "contabo-vps8-qwen3-4b"
    assert payload["slot"] == "l1_worker"
    assert payload["input"] == {"text": "hi"}
    assert payload["limits"]["max_tokens"] == 120
    assert "api_key" not in payload["context"]
    assert "access_token" not in payload["context"]["nested"]
    assert payload["context"]["nested"]["safe"] == "ok"
    assert "model" not in payload
    assert "config" not in payload


@pytest.mark.asyncio
async def test_remote_worker_identity_mismatch_fails_closed():
    def post(url, payload, timeout_ms):
        return {
            "status": "ok",
            "worker_id": "wrong-worker",
            "slot": "l1_worker",
            "result": {"text": "should not be accepted"},
        }

    generate = RemoteWorkerGenerationAdapter(
        "https://worker.example.com",
        worker_id="contabo-vps8-qwen3-4b",
        post_fn=post,
    )
    with pytest.raises(RuntimeError, match="identity mismatch"):
        await generate("hi", {"call_id": "c1"}, {})


@pytest.mark.asyncio
async def test_remote_worker_failure_is_one_shot_no_fallback():
    calls = 0

    def boom(url, payload, timeout_ms):
        nonlocal calls
        calls += 1
        raise OSError("remote unavailable")

    generate = RemoteWorkerGenerationAdapter(
        "https://worker.example.com",
        worker_id="contabo-vps8-qwen3-4b",
        post_fn=boom,
    )
    with pytest.raises(OSError, match="remote unavailable"):
        await generate("hi", {"call_id": "c1"}, {})
    assert calls == 1


def test_remote_worker_rejects_plain_http_public_endpoint():
    with pytest.raises(ValueError, match="public IP"):
        RemoteWorkerGenerationAdapter(
            "http://8.8.8.8:7788",
            worker_id="contabo-vps8-qwen3-4b",
        )


def test_fleet_from_config_kernel():
    config = {
        "intent": {
            "provider": "kernel",
            "endpoint": "http://127.0.0.1:7788",
            "intents": [{"id": "greeting"}, {"id": "unknown"}],
        },
        "generation": {"provider": "kernel", "endpoint": "http://127.0.0.1:7788"},
    }
    classifier, generator = fleet_from_config(config, post_fn=_post_ok)
    assert isinstance(classifier, KernelIntentClassifier)
    assert classifier.intents == ("greeting", "unknown")
    assert isinstance(generator, KernelGenerationAdapter)


def test_fleet_from_config_remote_worker():
    def post(url, payload, timeout_ms):
        return {
            "status": "ok",
            "worker_id": "contabo-vps8-qwen3-4b",
            "slot": "l1_worker",
            "result": {"text": "ok"},
        }

    config = {
        "intent": {"provider": "kernel", "endpoint": "http://127.0.0.1:7788"},
        "generation": {
            "provider": "remote_worker",
            "endpoint": "http://100.64.0.10:7788",
            "worker_id": "contabo-vps8-qwen3-4b",
            "slot": "l1_worker",
            "timeout_ms": 4500,
        },
    }
    classifier, generator = fleet_from_config(config, post_fn=post)
    assert isinstance(classifier, KernelIntentClassifier)
    assert isinstance(generator, RemoteWorkerGenerationAdapter)
    assert generator.worker_id == "contabo-vps8-qwen3-4b"
    assert generator.slot == "l1_worker"
    assert generator.timeout_ms == 4500


def test_fleet_from_config_non_kernel():
    classifier, generator = fleet_from_config({"intent": {"provider": "other"}})
    assert classifier is None
    assert generator is None
