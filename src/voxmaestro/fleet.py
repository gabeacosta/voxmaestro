"""HTTP fleet adapters for local kernel and remote worker slots.

The runtime owns routing. Adapters only invoke an already-selected semantic
slot and return a proposal. They do not own provider choice, retries, tool
authority, credentials, or fallback policy.

Local kernel contract (v0):

- ``POST {endpoint}/v1/intent``
    body: ``{"text", "call_id", "state", "intent_history", "intents"}``
    200 -> ``{"intent": "availability_question", "confidence": 0.0-1.0}``
- ``POST {endpoint}/v1/generate``
    body: ``{"text", "context", "config"}``
    200 -> ``{"text": "..."}``

Remote worker contract (v0):

- ``POST {endpoint}/v1/work``
    body contains a fixed ``worker_id`` and semantic ``slot`` plus bounded
    input/context/limits. Model/provider selection stays behind the worker
    binding and is never chosen by the model.
    200 -> ``{"status":"ok","worker_id","slot","result":{"text":"..."}}``

Failures never invent output: intent errors classify as ``"unknown"`` (the
``*`` transition decides what happens next); generation errors raise so the
caller can surface a bounded failure. stdlib urllib only; inject ``post_fn``
in tests.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Optional

PostFn = Callable[[str, Mapping[str, Any], float], Mapping[str, Any]]

_SECRET_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


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


def _without_secret_material(value: Any) -> Any:
    """Return a JSON-shaped copy with credential-like mapping keys removed."""
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS):
                continue
            cleaned[key] = _without_secret_material(raw_value)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_without_secret_material(item) for item in value]
    return value


def _validate_remote_endpoint(endpoint: str) -> str:
    """Require TLS or a private/loopback/CGNAT address for plain HTTP."""
    normalized = endpoint.rstrip("/")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("remote worker endpoint must be an http(s) URL")
    if parsed.scheme == "https":
        return normalized

    host = parsed.hostname
    if host in {"localhost", "127.0.0.1", "::1"}:
        return normalized
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            "plain HTTP remote worker endpoint must use a private IP; use HTTPS for hostnames"
        ) from exc
    if address.is_private or address.is_loopback or address in _CGNAT:
        return normalized
    raise ValueError("plain HTTP remote worker endpoint cannot use a public IP")


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
    """Local L1 generation slot over HTTP. Errors raise to the caller."""

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


class RemoteWorkerGenerationAdapter:
    """Bounded generation adapter for a preselected remote worker slot.

    The caller fixes ``worker_id`` and ``slot`` at construction time. The
    outbound payload strips credential-like keys and does not forward model or
    provider selection. The response must echo the expected identity. There is
    exactly one remote invocation per call and no automatic fallback.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        worker_id: str,
        slot: str = "l1_worker",
        timeout_ms: float = 5000,
        post_fn: Optional[PostFn] = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("remote worker_id is required")
        if not slot.strip():
            raise ValueError("remote worker slot is required")
        self.endpoint = _validate_remote_endpoint(endpoint)
        self.worker_id = worker_id
        self.slot = slot
        self.timeout_ms = timeout_ms
        self._post = post_fn or urllib_post

    async def __call__(
        self,
        text: str,
        context: Mapping[str, Any],
        generation_config: Mapping[str, Any],
    ) -> str:
        request_id = str(context.get("request_id") or context.get("call_id") or "")
        limits: dict[str, Any] = {"deadline_ms": int(self.timeout_ms)}
        max_tokens = generation_config.get("max_tokens")
        if isinstance(max_tokens, int) and max_tokens > 0:
            limits["max_tokens"] = max_tokens

        payload = {
            "contract_version": "remote_worker.v0",
            "request_id": request_id,
            "worker_id": self.worker_id,
            "slot": self.slot,
            "task": "generate",
            "input": {"text": text},
            "context": _without_secret_material(context),
            "limits": limits,
        }
        data = await asyncio.to_thread(
            self._post,
            f"{self.endpoint}/v1/work",
            payload,
            self.timeout_ms,
        )
        if data.get("status") != "ok":
            raise RuntimeError("remote worker returned non-ok status")
        if data.get("worker_id") != self.worker_id:
            raise RuntimeError("remote worker identity mismatch")
        if data.get("slot") != self.slot:
            raise RuntimeError("remote worker slot mismatch")
        response_request_id = data.get("request_id")
        if response_request_id is not None and str(response_request_id) != request_id:
            raise RuntimeError("remote worker request_id mismatch")
        result = data.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("remote worker returned no result")
        output = result.get("text")
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("remote worker returned no text")
        return output


GenerationAdapter = KernelGenerationAdapter | RemoteWorkerGenerationAdapter


def fleet_from_config(
    config: Mapping[str, Any],
    *,
    post_fn: Optional[PostFn] = None,
) -> tuple[Optional[KernelIntentClassifier], Optional[GenerationAdapter]]:
    """Build fleet adapters from a config's ``intent``/``generation`` blocks.

    ``kernel`` keeps the local adapter contract. ``remote_worker`` creates a
    bounded remote generation adapter with fixed worker/slot identity. Other
    providers return ``None`` so callers may supply their own adapters.
    """
    intent_cfg = config.get("intent", {})
    generation_cfg = config.get("generation", {})
    classifier: Optional[KernelIntentClassifier] = None
    generator: Optional[GenerationAdapter] = None
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
    elif generation_cfg.get("provider") == "remote_worker" and generation_cfg.get("endpoint"):
        worker_id = generation_cfg.get("worker_id")
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("remote_worker generation requires worker_id")
        generator = RemoteWorkerGenerationAdapter(
            str(generation_cfg["endpoint"]),
            worker_id=worker_id,
            slot=str(generation_cfg.get("slot") or "l1_worker"),
            timeout_ms=float(generation_cfg.get("timeout_ms") or 5000),
            post_fn=post_fn,
        )
    return classifier, generator
