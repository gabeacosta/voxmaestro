from __future__ import annotations

import pytest

from voxmaestro.fleet import (
    KernelGenerationAdapter,
    KernelIntentClassifier,
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


def test_fleet_from_config_non_kernel():
    classifier, generator = fleet_from_config({"intent": {"provider": "other"}})
    assert classifier is None
    assert generator is None
