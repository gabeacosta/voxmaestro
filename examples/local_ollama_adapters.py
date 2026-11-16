"""Local Ollama intent + generation adapters for offline Voice Maestro.

These adapters talk only to loopback Ollama (``127.0.0.1:11434``). They never
mint tool authority. Failures surface as ``unknown`` / raised errors rather
than invented success.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:latest"

_INTENT_IDS = (
    "greeting",
    "service_question",
    "pricing_question",
    "availability_question",
    "booking_request",
    "human_request",
    "not_interested",
    "unknown",
)


def _post_json(url: str, payload: Mapping[str, Any], timeout_s: float = 60.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise RuntimeError("ollama response is not a JSON object")
    return body


def _chat(model: str, system: str, user: str, *, base_url: str, timeout_s: float) -> str:
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        data = _post_json(f"{base_url.rstrip('/')}/api/chat", payload, timeout_s=timeout_s)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ollama unreachable: {exc}") from exc
    message = data.get("message") or {}
    text = str(message.get("content") or "").strip()
    if not text:
        raise RuntimeError("ollama returned empty content")
    return text


def make_ollama_classifier(
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA,
    timeout_s: float = 30.0,
):
    """Return an async intent classifier compatible with VoxMaestroRuntime."""

    async def classify(text: str, context) -> str:
        del context
        system = (
            "Classify the caller utterance into exactly one intent id. "
            f"Allowed ids: {', '.join(_INTENT_IDS)}. "
            "Reply with only the intent id, no punctuation."
        )
        try:
            raw = _chat(model, system, text, base_url=base_url, timeout_s=timeout_s)
        except Exception:
            return "unknown"
        token = raw.split()[0].strip().lower().strip("`\"'")
        return token if token in _INTENT_IDS else "unknown"

    return classify


def make_ollama_generator(
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA,
    timeout_s: float = 60.0,
):
    """Return an async generation adapter for WebSessionAdapter."""

    async def generate(text: str, context: Mapping[str, Any], generation_config: Mapping[str, Any]) -> str:
        system = str(
            generation_config.get("system_prompt")
            or (
                "You are a concise local voice assistant. "
                "Keep replies under two short sentences. "
                "Do not invent bookings, prices, or external effects."
            )
        )
        tool_results = context.get("tool_results") if isinstance(context, Mapping) else None
        user = text
        if tool_results:
            user = f"{text}\n\nTool results: {json.dumps(tool_results, ensure_ascii=False)}"
        return _chat(model, system, user, base_url=base_url, timeout_s=timeout_s)

    return generate
