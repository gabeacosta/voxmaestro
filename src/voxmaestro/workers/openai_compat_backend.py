"""Fixed local OpenAI-compatible inference backend for ``remote_worker.v0``.

This adapter deliberately does *not* choose models, retry providers, or fall
back to another runtime. The caller pins one endpoint and one model identity
at construction time. It converts VoxMaestro's worker-level ``generate`` call
into a single ``/v1/chat/completions`` request and returns plain assistant
text.

It is intended for native local runtimes such as llama-server or MLX servers
that expose an OpenAI-compatible API. Authority, routing, retries, and
escalation remain outside the model process.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any


class LocalInferenceError(RuntimeError):
    """The pinned local inference runtime failed or violated the response contract."""


def _normalize_endpoint(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("endpoint must use http or https")
    if not parsed.hostname:
        raise ValueError("endpoint hostname is required")

    host = parsed.hostname.lower()
    if parsed.scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("plain HTTP inference endpoints must be loopback-only")
    return url.rstrip("/")


class OpenAICompatInferenceBackend:
    """One fixed model behind one fixed OpenAI-compatible endpoint.

    ``endpoint`` may be a base API URL such as ``http://127.0.0.1:8080/v1``
    or the full ``.../chat/completions`` URL. No API key is accepted because
    this first native-model slice is deliberately local-only and must not
    receive upstream credentials.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        timeout_s: float = 30.0,
        temperature: float | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model is required")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")

        endpoint = _normalize_endpoint(endpoint)
        self.url = endpoint if endpoint.endswith("/chat/completions") else f"{endpoint}/chat/completions"
        self.model = model
        self.timeout_s = timeout_s
        self.temperature = temperature

    def generate(self, text: str, context: Mapping[str, Any], limits: Mapping[str, Any]) -> str:
        messages: list[dict[str, str]] = []
        system_prompt = context.get("system_prompt")
        if isinstance(system_prompt, str) and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": text})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        max_tokens = limits.get("max_tokens")
        if isinstance(max_tokens, int) and max_tokens > 0:
            payload["max_tokens"] = max_tokens
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        request_timeout = self.timeout_s
        deadline_ms = limits.get("deadline_ms")
        if isinstance(deadline_ms, (int, float)) and deadline_ms > 0:
            request_timeout = min(request_timeout, float(deadline_ms) / 1000)

        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LocalInferenceError(f"local inference request failed: {exc}") from exc

        try:
            body = json.loads(raw)
            choices = body["choices"]
            content = choices[0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LocalInferenceError("local inference response violated chat-completions contract") from exc

        if not isinstance(content, str) or not content.strip():
            raise LocalInferenceError("local inference response contained no assistant text")
        return content.strip()
