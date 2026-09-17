"""Deterministic L0 boundary tests; fake inference is never model admission."""
import asyncio
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from voxmaestro.fleet import KernelIntentClassifier
from voxmaestro.workers.intent_worker import (
    MAX_BODY_BYTES,
    UNKNOWN,
    IntentWorker,
    IntentWorkerServer,
)

REQUEST = {"text": "Do you have appointments?", "intents": ["availability_question", "unknown"]}
GOOD = {"intent": "availability_question", "confidence": 0.91}


@pytest.fixture
def upstream():
    state = SimpleNamespace(calls=[], status=200, content=json.dumps(GOOD), delay=0,
                            extra={}, raw=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            state.calls.append((self.path, dict(self.headers),
                                json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            time.sleep(state.delay)
            body = state.raw if state.raw is not None else json.dumps({"choices": [{"message": {
                "content": state.content, **state.extra}}]}).encode()
            self.send_response(state.status)
            self.send_header("Location", "http://127.0.0.1:1/stolen")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.endpoint = f"http://127.0.0.1:{server.server_port}/v1"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(2)


def test_valid_one_fixed_request_and_discards_context(upstream, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    worker = IntentWorker(endpoint=upstream.endpoint, model="fixed-model")
    result = worker.classify({**REQUEST, "model": "evil", "endpoint": "http://evil",
                             "api_key": "private-material", "state": "private-state",
                             "context": {"authorization": "Bearer do-not-send"}})
    assert result == GOOD
    assert len(upstream.calls) == 1
    path, headers, payload = upstream.calls[0]
    assert path == "/v1/chat/completions"
    assert payload["model"] == "fixed-model"
    assert payload["max_tokens"] == 64
    assert payload["temperature"] == 0
    assert payload["stream"] is False
    assert "Authorization" not in headers
    assert "private" not in json.dumps(payload)
    assert "evil" not in json.dumps(payload)
    assert not {"tools", "tool_choice", "provider", "fallback"} & payload.keys()
    assert json.loads(payload["messages"][1]["content"])["intents"] == {
        "availability_question": "", "unknown": ""}


@pytest.mark.parametrize("content", [
    '{"intent":"illegal","confidence":0.99}',
    'Here is JSON: {"intent":"availability_question","confidence":0.99}',
    '```json\n{"intent":"availability_question","confidence":0.99}\n```',
    '', '{', '[]', 'null',
    '{"intent":"availability_question"}',
    '{"intent":"availability_question","confidence":-0.1}',
    '{"intent":"availability_question","confidence":1.1}',
    '{"intent":"availability_question","confidence":0.49}',
    '{"intent":"availability_question","confidence":true}',
    '{"intent":"availability_question","confidence":"0.9"}',
    '{"intent":"availability_question","confidence":NaN}',
    '{"intent":"availability_question","confidence":Infinity}',
    '{"intent":"availability_question","confidence":1e999}',
    '{"intent":"availability_question","confidence":' + '9' * 400 + '}',
    '{"intent":[],"confidence":0.9}',
    '{"intent":"availability_question","confidence":0.9,"tool":"book"}',
    '{"intent":"unknown","confidence":0.9}',
    '{"intent":"illegal","intent":"availability_question","confidence":0.9}',
])
def test_invalid_output_is_unknown_without_retry(upstream, content):
    upstream.content = content
    assert IntentWorker(endpoint=upstream.endpoint).classify(REQUEST) == UNKNOWN
    assert len(upstream.calls) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 500])
def test_failure_and_redirect_never_retry(upstream, status):
    upstream.status = status
    assert IntentWorker(endpoint=upstream.endpoint).classify(REQUEST) == UNKNOWN
    assert len(upstream.calls) == 1


def test_dead_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_port
    server.server_close()
    start = time.monotonic()
    assert IntentWorker(endpoint=f"http://127.0.0.1:{port}/v1", timeout_s=0.1).classify(REQUEST) == UNKNOWN
    assert time.monotonic() - start < 0.5


def test_timeout_holds_concurrency_permit(upstream):
    upstream.delay = 0.25
    worker = IntentWorker(endpoint=upstream.endpoint, timeout_s=0.1)
    start = time.monotonic()
    result = []
    thread = threading.Thread(target=lambda: result.append(worker.classify(REQUEST)))
    thread.start()
    deadline = time.monotonic() + 1
    while not upstream.calls and time.monotonic() < deadline:
        time.sleep(0.001)
    assert worker.classify(REQUEST) == UNKNOWN
    thread.join(0.5)
    assert result == [UNKNOWN]
    assert len(upstream.calls) == 1
    assert time.monotonic() - start < 0.5


@pytest.mark.parametrize("value", [
    "Bearer private-token", "api_key=private", "password: private", "token=private",
    "sk-" + "a" * 30, "ghp_" + "a" * 30, "hf_" + "a" * 30,
    "eyJabc.abc.abc", "postgres://user:password@localhost/db",
    "-----BEGIN RSA PRIVATE KEY-----",
])
def test_credentials_never_leave_text_or_descriptions(upstream, value):
    worker = IntentWorker(endpoint=upstream.endpoint)
    assert worker.classify({**REQUEST, "text": value}) == UNKNOWN
    assert worker.classify({**REQUEST, "intents": [{"id": "availability_question",
                                                  "description": value}]}) == UNKNOWN
    assert upstream.calls == []


@pytest.mark.parametrize("payload", [None, [], {}, {**REQUEST, "text": ""},
    {**REQUEST, "text": "x" * 4000}, {**REQUEST, "intents": []},
    {**REQUEST, "intents": ["x", "x"]}, {**REQUEST, "intents": ["not legal"]},
    {**REQUEST, "intents": [None]}, {**REQUEST, "intents": "availability_question"},
])
def test_bad_input_never_invokes_model(upstream, payload):
    assert IntentWorker(endpoint=upstream.endpoint).classify(payload) == UNKNOWN
    assert not upstream.calls


def test_legal_descriptions_forwarded(upstream):
    assert IntentWorker(endpoint=upstream.endpoint).classify({**REQUEST, "intents": [
        {"id": "availability_question", "description": "Ask about open appointments",
         "api_key": "do-not-forward"}]}) == GOOD
    sent = json.loads(upstream.calls[0][2]["messages"][1]["content"])
    assert sent["intents"] == {"availability_question": "Ask about open appointments"}


@pytest.mark.parametrize("endpoint", ["https://example.com/v1", "http://0.0.0.0/v1",
    "http://192.168.1.1/v1", "http://localhost/v1", "http://user:pass@127.0.0.1/v1",
    "http://127.0.0.1/v1?token=x", "http://127.0.0.1/v1#x", "ftp://127.0.0.1/v1",
    "http://127.0.0.1/other"])
def test_loopback_credential_free_endpoint_only(endpoint):
    with pytest.raises(ValueError):
        IntentWorker(endpoint=endpoint)


@pytest.mark.parametrize("kwargs", [{"timeout_s": 0}, {"timeout_s": float("nan")},
    {"timeout_s": float("inf")}, {"min_confidence": -1}, {"min_confidence": True},
    {"min_confidence": float("nan")}, {"model": ""}])
def test_invalid_startup_limits(kwargs):
    with pytest.raises(ValueError):
        IntentWorker(**kwargs)


def test_server_rejects_public_bind():
    with pytest.raises(ValueError):
        IntentWorkerServer(worker=IntentWorker(), host="0.0.0.0", port=0)


@pytest.mark.parametrize("raw", [b"invalid", b"{}", b"[]", b"x" * 17000,
    b'{"choices":[{"message":{"content":null}}]}',
    b'{"choices":[]}', b'{"choices":[{},{}]}'])
def test_invalid_upstream_envelope(upstream, raw):
    upstream.raw = raw
    assert IntentWorker(endpoint=upstream.endpoint).classify(REQUEST) == UNKNOWN
    assert len(upstream.calls) == 1


def test_model_tool_call_is_rejected(upstream):
    upstream.extra = {"tool_calls": [{"function": {"name": "booking"}}]}
    assert IntentWorker(endpoint=upstream.endpoint).classify(REQUEST) == UNKNOWN
    assert len(upstream.calls) == 1


def test_http_paths_body_bounds_and_kernel_integration(upstream):
    service = IntentWorkerServer(worker=IntentWorker(endpoint=upstream.endpoint), port=0)
    service.start()
    try:
        port = int(service.url.rsplit(":", 1)[1])
        for method, path in [("POST", "/unknown"), ("GET", "/unknown")]:
            connection = http.client.HTTPConnection("127.0.0.1", port)
            connection.request(method, path, "{}")
            assert connection.getresponse().status == 404
            connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", port)
        connection.request("POST", "/v1/intent", "x" * (MAX_BODY_BYTES + 1))
        assert json.loads(connection.getresponse().read()) == UNKNOWN
        connection.close()
        assert not upstream.calls
        classifier = KernelIntentClassifier(service.url, intents=tuple(REQUEST["intents"]))
        context = SimpleNamespace(call_id="test", current_state="greeting", intent_history=[])
        assert asyncio.run(classifier(REQUEST["text"], context)) == GOOD["intent"]
        upstream.content = "garbage"
        assert asyncio.run(classifier(REQUEST["text"], context)) == "unknown"
        assert len(upstream.calls) == 2
    finally:
        service.stop()


def test_expired_request_keeps_inference_slot_until_inference_exits(monkeypatch):
    worker = IntentWorker(timeout_s=0.02)
    finish = threading.Event()
    calls = []

    def blocked_inference(prompt):
        calls.append(prompt)
        finish.wait(1)
        return GOOD

    monkeypatch.setattr(worker, "_infer", blocked_inference)
    try:
        assert worker.classify(REQUEST) == UNKNOWN
        assert worker.classify(REQUEST) == UNKNOWN
        assert len(calls) == 1
    finally:
        finish.set()
