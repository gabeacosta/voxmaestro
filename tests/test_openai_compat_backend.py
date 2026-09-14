from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from voxmaestro.workers.openai_compat_backend import (
    LocalInferenceError,
    OpenAICompatInferenceBackend,
)


class _FakeChatServer:
    def __init__(self, *, response: dict | None = None) -> None:
        self.requests: list[dict] = []
        self.response = response or {
            "choices": [{"message": {"role": "assistant", "content": "native reply"}}]
        }
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                owner.requests.append({"path": self.path, "body": body})
                raw = json.dumps(owner.response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def test_calls_one_pinned_chat_completion_endpoint() -> None:
    with _FakeChatServer() as fake:
        backend = OpenAICompatInferenceBackend(endpoint=fake.endpoint, model="ternary-bonsai-27b")
        result = backend.generate(
            "What are your hours?",
            {"system_prompt": "Answer only from supplied business facts."},
            {"max_tokens": 48, "deadline_ms": 2000},
        )

    assert result == "native reply"
    assert len(fake.requests) == 1
    assert fake.requests[0]["path"] == "/v1/chat/completions"
    assert fake.requests[0]["body"]["model"] == "ternary-bonsai-27b"
    assert fake.requests[0]["body"]["max_tokens"] == 48
    assert fake.requests[0]["body"]["stream"] is False
    assert fake.requests[0]["body"]["messages"] == [
        {"role": "system", "content": "Answer only from supplied business facts."},
        {"role": "user", "content": "What are your hours?"},
    ]


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://192.168.1.10:8080/v1",
        "https://models.example.com/v1",
    ],
)
def test_native_inference_endpoint_must_stay_loopback(endpoint: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        OpenAICompatInferenceBackend(endpoint=endpoint, model="bonsai")


def test_no_model_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="model is required"):
        OpenAICompatInferenceBackend(endpoint="http://127.0.0.1:8080/v1", model="")


def test_malformed_response_fails_closed() -> None:
    with _FakeChatServer(response={"choices": []}) as fake:
        backend = OpenAICompatInferenceBackend(endpoint=fake.endpoint, model="bonsai")
        with pytest.raises(LocalInferenceError, match="violated"):
            backend.generate("hello", {}, {})


def test_full_chat_completion_url_is_not_duplicated() -> None:
    with _FakeChatServer() as fake:
        backend = OpenAICompatInferenceBackend(
            endpoint=f"{fake.endpoint}/chat/completions",
            model="binary-bonsai-27b",
        )
        assert backend.generate("hello", {}, {}) == "native reply"

    assert fake.requests[0]["path"] == "/v1/chat/completions"
