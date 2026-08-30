"""HTTP fleet adapters for the kernel L0/L1 slots.

The microscroll config declares ``provider: "kernel"`` with HTTP endpoints for
intent and generation. These adapters are the client side of that contract.

Kernel contract (v0):

- ``POST {endpoint}/v1/intent``
    body: ``{"text", "call_id", "state", "intent_history", "intents"}``
    200 -> ``{"intent": "availability_question", "confidence": 0.0-1.0}``
- ``POST {endpoint}/v1/generate``
    body: ``{"text", "context", "config"}``
    200 -> ``{"text": "..."}``

Failures never invent output: intent errors classify as ``"unknown"`` (the
``*`` transition decides what happens next), generation errors raise so the
gateway surfaces ``generation_failed``. stdlib urllib only; inject
``post_fn`` in tests.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Optional

PostFn = Callable[[str, Mapping[str, Any], float], Mapping[str, Any]]


def urllib_post(url: str, payload: Mapping[str, Any], timeout_ms: float) -> Mapping[str, Any]:
    """POST JSON with stdlib urllib and parse the JSON response."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_ms / 1000) as response:
        return json.loads(response.read())


class KernelIntentClassifier:
    """L0 intent slot over HTTP. Any failure classifies as ``unknown``."""

    def __init__(
        self,
        endpoint: str,
        *,
        intents: tuple[str, ...] = (),
        timeout_ms: float = 1500,
        min_confidence: float = 0.0,
        post_fn: Optional[PostFn] = None,
    ) -> None:
        """Bind the kernel intent endpoint and the legal intent ids."""
        self.endpoint = endpoint.rstrip("/")
        self.intents = intents
        self.timeout_ms = timeout_ms
        self.min_confidence = min_confidence
        self._post = post_fn or urllib_post

    async def __call__(self, text: str, context: Any) -> str:
        payload = {
            "text": text,
            "call_id": getattr(context, "call_id", ""),
            "state": getattr(context, "current_state", ""),
            "intent_history": list(getattr(context, "intent_history", []) or []),
            "intents": list(self.intents),
        }
        try:
            data = await asyncio.to_thread(
                self._post,
                f"{self.endpoint}/v1/intent",
                payload,
                self.timeout_ms,
            )
        except Exception:
            return "unknown"
        intent = data.get("intent")
        confidence = data.get("confidence")
        if not isinstance(intent, str) or not intent:
            return "unknown"
        if isinstance(confidence, (int, float)) and confidence < self.min_confidence:
            return "unknown"
        return intent


class KernelGenerationAdapter:
    """L1 generation slot over HTTP. Errors raise to the caller."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_ms: float = 4000,
        post_fn: Optional[PostFn] = None,
    ) -> None:
        """Bind the kernel generation endpoint."""
        self.endpoint = endpoint.rstrip("/")
        self.timeout_ms = timeout_ms
        self._post = post_fn or urllib_post

    async def __call__(
        self,
        text: str,
        context: Mapping[str, Any],
        generation_config: Mapping[str, Any],
    ) -> str:
        payload = {
            "text": text,
            "context": dict(context),
            "config": dict(generation_config),
        }
        data = await asyncio.to_thread(
            self._post,
            f"{self.endpoint}/v1/generate",
            payload,
            self.timeout_ms,
        )
        result = data.get("text")
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("kernel generation returned no text")
        return result


def fleet_from_config(
    config: Mapping[str, Any],
    *,
    post_fn: Optional[PostFn] = None,
) -> tuple[Optional[KernelIntentClassifier], Optional[KernelGenerationAdapter]]:
    """Build kernel adapters from a config's ``intent``/``generation`` blocks.

    Returns ``(None, None)`` for non-kernel providers so callers keep their
    own injected adapters.
    """
    intent_cfg = config.get("intent", {})
    generation_cfg = config.get("generation", {})
    classifier: Optional[KernelIntentClassifier] = None
    generator: Optional[KernelGenerationAdapter] = None
    if intent_cfg.get("provider") == "kernel" and intent_cfg.get("endpoint"):
        intents = tuple(
            item["id"]
            for item in intent_cfg.get("intents", [])
            if isinstance(item, Mapping) and item.get("id")
        )
        classifier = KernelIntentClassifier(
            str(intent_cfg["endpoint"]), intents=intents, post_fn=post_fn
        )
    if generation_cfg.get("provider") == "kernel" and generation_cfg.get("endpoint"):
        generator = KernelGenerationAdapter(
            str(generation_cfg["endpoint"]), post_fn=post_fn
        )
    return classifier, generator
