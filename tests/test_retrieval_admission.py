from __future__ import annotations

import json
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from voxmaestro.retrieval import (
    OllamaEmbedClient,
    cosine_similarity,
    rank_documents,
    validate_corpus,
    validate_embedding_endpoint,
)


def test_rank_documents_filters_language_before_similarity() -> None:
    documents = [
        {"id": "en-calendar", "language": "en"},
        {"id": "es-calendar", "language": "es"},
        {"id": "en-insurance", "language": "en"},
    ]
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

    ranked = rank_documents([1.0, 0.0], documents, vectors, language="es")

    assert [item["id"] for item in ranked] == ["es-calendar"]


def test_cosine_similarity_rejects_invalid_vectors() -> None:
    with pytest.raises(ValueError, match="same non-zero dimension"):
        cosine_similarity([1.0], [1.0, 2.0])
    with pytest.raises(ValueError, match="zero-norm"):
        cosine_similarity([0.0, 0.0], [1.0, 0.0])


def test_cosine_similarity_cannot_overflow_relevance_gate() -> None:
    assert cosine_similarity([1e308, 1e308], [1e308, 1e308]) == pytest.approx(1.0)
    assert cosine_similarity([1e308, 1e308], [1e308, -1e308]) == pytest.approx(0.0)


def test_validate_corpus_requires_unique_ids_and_provenance() -> None:
    corpus = {
        "schema_version": "vsai-retrieval-admission.v0",
        "provenance": {"sources": [{"id": "site", "sha256": "abc"}]},
        "documents": [
            {
                "id": "duplicate",
                "language": "en",
                "text": "One",
                "source_ids": ["site"],
            },
            {
                "id": "duplicate",
                "language": "es",
                "text": "Dos",
                "source_ids": ["missing"],
            },
        ],
        "queries": [
            {
                "id": "query",
                "language": "en",
                "text": "Question",
                "expected_id": "duplicate",
            }
        ],
        "thresholds": {},
    }

    with pytest.raises(ValueError, match="document ids must be unique"):
        validate_corpus(corpus)


def test_validate_embedding_endpoint_is_loopback_only() -> None:
    assert validate_embedding_endpoint("http://127.0.0.1:11434") == (
        "http://127.0.0.1:11434/api/embed"
    )
    with pytest.raises(ValueError, match="loopback"):
        validate_embedding_endpoint("https://embeddings.example.com")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434/api/embed?token=value",
        "http://127.0.0.1:11434/api/embed#fragment",
        "http://127.0.0.1:11434/other",
        "https://127.0.0.1:11434",
    ],
)
def test_embedding_endpoint_rejects_dns_credentials_and_url_metadata(endpoint) -> None:
    with pytest.raises(ValueError):
        validate_embedding_endpoint(endpoint)


@pytest.mark.parametrize(
    "secret",
    [
        "Bearer private-token",
        "api_key=private",
        "password: private",
        "ghp_" + "a" * 30,
        "eyJabc.abc.abc",
        "postgres://user:password@localhost/db",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_corpus_and_live_inputs_reject_credentials_before_inference(secret) -> None:
    corpus = {
        "schema_version": "vsai-retrieval-admission.v0",
        "provenance": {"sources": [{"id": "site"}]},
        "documents": [
            {"id": "doc", "language": "en", "text": "safe", "source_ids": ["site"]}
        ],
        "queries": [
            {"id": "query", "language": "en", "text": "safe", "expected_id": "doc"}
        ],
        "thresholds": {},
    }
    for section in ("documents", "queries"):
        unsafe = deepcopy(corpus)
        unsafe[section][0]["text"] = secret
        with pytest.raises(ValueError, match="credential-like"):
            validate_corpus(unsafe)

    client = OllamaEmbedClient(
        "http://127.0.0.1:1",
        "nomic-test",
        expected_digest="expected",
    )
    with pytest.raises(ValueError, match="credential-like"):
        client.embed([secret])


class _OllamaBoundaryStub:
    def __init__(self, *, redirect: bool = False, drift: bool = False) -> None:
        self.redirect = redirect
        self.drift = drift
        self.calls: list[str] = []
        self.raw_bodies: list[bytes] = []
        self.tag_calls = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):  # noqa: N802
                owner.calls.append(self.path)
                if self.path == "/api/tags":
                    owner.tag_calls += 1
                    if owner.redirect:
                        self.send_response(302)
                        self.send_header("Location", f"{owner.url}/redirected")
                        self.end_headers()
                        return
                    digest = "changed" if owner.drift and owner.tag_calls > 1 else "expected"
                    self._send({"models": [{"name": "nomic-test", "digest": digest}]})
                    return
                if self.path == "/api/version":
                    self._send({"version": "test"})
                    return
                self._send({"unexpected": True})

            def do_POST(self):  # noqa: N802
                owner.calls.append(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                owner.raw_bodies.append(raw)
                json.loads(raw)
                self._send({"embeddings": [[1.0, 0.0]]})

            def _send(self, body):
                encoded = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def test_embedding_client_never_follows_redirects() -> None:
    stub = _OllamaBoundaryStub(redirect=True)
    try:
        client = OllamaEmbedClient(
            stub.url,
            "nomic-test",
            expected_digest="expected",
        )
        with pytest.raises(RuntimeError, match="HTTP 302"):
            client.inventory()
        assert stub.calls == ["/api/tags"]
    finally:
        stub.close()


def test_embedding_client_detects_digest_drift_around_every_request() -> None:
    stub = _OllamaBoundaryStub(drift=True)
    try:
        client = OllamaEmbedClient(
            stub.url,
            "nomic-test",
            expected_digest="expected",
        )
        with pytest.raises(RuntimeError, match="digest mismatch"):
            client.embed(["safe query"])
        assert stub.calls == ["/api/tags", "/api/embed", "/api/tags"]
    finally:
        stub.close()


def test_embedding_client_sends_utf8_json_on_the_wire() -> None:
    stub = _OllamaBoundaryStub()
    try:
        client = OllamaEmbedClient(
            stub.url,
            "nomic-test",
            expected_digest="expected",
        )
        vectors, _ = client.embed(["¿Qué día? 👋"])

        assert vectors == [[1.0, 0.0]]
        assert json.loads(stub.raw_bodies[0]) == {
            "model": "nomic-test",
            "input": ["¿Qué día? 👋"],
            "keep_alive": "5m",
        }
        assert "¿Qué día? 👋".encode("utf-8") in stub.raw_bodies[0]
    finally:
        stub.close()
