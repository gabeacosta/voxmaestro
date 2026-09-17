from __future__ import annotations

import http.client
import json
from copy import deepcopy
from pathlib import Path

import pytest

from examples.serve_gateway import http_tool_executor
from voxmaestro import VoxMaestroRuntime
from voxmaestro.conductor import SchemaLoader
from voxmaestro.integrations.web_session import WebSessionAdapter
from voxmaestro.workers.retrieval_worker import (
    MAX_BODY_BYTES,
    RetrievalWorker,
    RetrievalWorkerError,
    RetrievalWorkerServer,
)


class FakeEmbedClient:
    model = "nomic-test"
    expected_digest = "digest-test"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def inventory(self) -> dict[str, str]:
        return {"digest": self.expected_digest, "ollama_version": "test"}

    def embed(self, texts):
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            if "calendar" in text.lower() or "calendario" in text.lower():
                vectors.append([1.0, 0.0])
            elif "pizza" in text.lower():
                vectors.append([0.7, 0.7])
            else:
                vectors.append([0.0, 1.0])
        return vectors, {"total_duration_ns": 1}


class FailingEmbedClient(FakeEmbedClient):
    def embed(self, texts):
        self.calls.append(list(texts))
        raise OSError("backend unavailable")


@pytest.fixture
def corpus_path(tmp_path: Path) -> Path:
    corpus = {
        "schema_version": "vsai-retrieval-admission.v0",
        "purpose": "test",
        "provenance": {"sources": [{"id": "source", "sha256": "abc"}]},
        "documents": [
            {
                "id": "en_calendar",
                "language": "en",
                "category": "faq",
                "text": "The calendar connects to Outlook.",
                "source_ids": ["source"],
            },
            {
                "id": "es_calendar",
                "language": "es",
                "category": "faq",
                "text": "El calendario se conecta con Outlook.",
                "source_ids": ["source"],
            },
            {
                "id": "en_policy",
                "language": "en",
                "category": "policy",
                "text": "Insurance must be verified.",
                "source_ids": ["source"],
            },
        ],
        "queries": [
            {
                "id": "q1",
                "language": "en",
                "expected_id": "en_calendar",
                "text": "Which calendar?",
            }
        ],
        "thresholds": {},
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))
    return path


def test_persistent_index_avoids_document_reembedding(
    corpus_path: Path, tmp_path: Path
) -> None:
    index_path = tmp_path / "index.json"
    builder = FakeEmbedClient()
    RetrievalWorker.from_files(
        corpus_path,
        index_path,
        client=builder,
        rebuild_index=True,
    )
    assert len(builder.calls) == 1
    assert all(text.startswith("search_document: ") for text in builder.calls[0])

    runtime_client = FakeEmbedClient()
    worker = RetrievalWorker.from_files(corpus_path, index_path, client=runtime_client)
    result = worker.retrieve({"query": "Does it use a calendar?", "language": "en"})

    assert len(runtime_client.calls) == 1
    assert runtime_client.calls[0] == ["search_query: Does it use a calendar?"]
    assert result["status"] == "ok"
    assert result["matches"][0]["id"] == "en_calendar"
    assert all(match["language"] == "en" for match in result["matches"])
    assert result["model_digest"] == "digest-test"


def test_ambiguous_out_of_domain_query_returns_no_passages(
    corpus_path: Path, tmp_path: Path
) -> None:
    client = FakeEmbedClient()
    worker = RetrievalWorker.from_files(
        corpus_path,
        tmp_path / "index.json",
        client=client,
        rebuild_index=True,
        top_k=1,
        min_score=0.6,
        min_margin=0.03,
        subject_terms=("VoiceScheduleAI",),
    )
    client.calls.clear()

    result = worker.retrieve(
        {"query": "Does VoiceScheduleAI sell pizza?", "language": "en"}
    )

    assert client.calls == [["search_query: Does sell pizza?"]]
    assert result["status"] == "no_match"
    assert result["success"] is False
    assert result["matches"] == []


def test_no_match_is_an_http_failure_boundary(
    corpus_path: Path, tmp_path: Path
) -> None:
    client = FakeEmbedClient()
    worker = RetrievalWorker.from_files(
        corpus_path,
        tmp_path / "index.json",
        client=client,
        rebuild_index=True,
    )
    server = RetrievalWorkerServer(worker=worker, port=0)
    server.start()
    try:
        port = int(server.url.rsplit(":", 1)[1])
        connection = http.client.HTTPConnection("127.0.0.1", port)
        connection.request(
            "POST",
            "/v1/retrieve",
            json.dumps(
                {"query": "Does VoiceScheduleAI sell pizza?", "language": "en"}
            ),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = json.loads(response.read())

        assert response.status == 404
        assert body["status"] == "no_match"
        assert body["success"] is False
        assert body["matches"] == []
    finally:
        server.stop()


def test_backend_failure_is_not_reported_as_a_bad_request(
    corpus_path: Path, tmp_path: Path
) -> None:
    index_path = tmp_path / "index.json"
    RetrievalWorker.from_files(
        corpus_path,
        index_path,
        client=FakeEmbedClient(),
        rebuild_index=True,
    )
    worker = RetrievalWorker.from_files(
        corpus_path,
        index_path,
        client=FailingEmbedClient(),
    )
    server = RetrievalWorkerServer(worker=worker, port=0)
    server.start()
    try:
        port = int(server.url.rsplit(":", 1)[1])
        connection = http.client.HTTPConnection("127.0.0.1", port)
        connection.request(
            "POST",
            "/v1/retrieve",
            json.dumps({"query": "Which calendar?", "language": "en"}),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = json.loads(response.read())

        assert response.status == 502
        assert body == {
            "status": "error",
            "error": "fixed embedding backend failed",
        }
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_no_match_bypasses_l1_and_uses_runtime_failure_text(
    corpus_path: Path, tmp_path: Path
) -> None:
    client = FakeEmbedClient()
    worker = RetrievalWorker.from_files(
        corpus_path,
        tmp_path / "index.json",
        client=client,
        rebuild_index=True,
    )
    server = RetrievalWorkerServer(worker=worker, port=0)
    server.start()
    generation_calls: list[str] = []

    async def classify(text, context):
        return "service_question"

    async def generate(text, context, generation_config):
        generation_calls.append(text)
        return "This must not be used."

    try:
        config = deepcopy(
            SchemaLoader.load(Path("examples/microscroll_landing_small_fleet.yaml"))
        )
        config["tools"]["retrieve_business_context"]["endpoint"] = (
            f"{server.url}/v1/retrieve"
        )
        runtime = VoxMaestroRuntime(
            config,
            intent_classifier=classify,
            tool_executor=http_tool_executor,
        )
        adapter = WebSessionAdapter(runtime, generation_adapter=generate)
        await _collect(
            adapter,
            {"type": "start", "sessionId": "no-match", "locale": "en-US"},
        )

        events = await _collect(
            adapter,
            {
                "type": "message",
                "sessionId": "no-match",
                "text": "Does VoiceScheduleAI sell pizza?",
            },
        )

        assert generation_calls == []
        assert events[-1]["type"] == "response"
        assert events[-1]["text"] == "I cannot verify that information right now."
        assert events[-1]["metadata"]["phase"] == "tool_failure"
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_dead_retrieval_endpoint_bypasses_l1_without_fallback() -> None:
    generation_calls: list[str] = []

    async def classify(text, context):
        return "service_question"

    async def generate(text, context, generation_config):
        generation_calls.append(text)
        return "This must not be used."

    config = deepcopy(
        SchemaLoader.load(Path("examples/microscroll_landing_small_fleet.yaml"))
    )
    config["tools"]["retrieve_business_context"]["endpoint"] = (
        "http://127.0.0.1:1/v1/retrieve"
    )
    runtime = VoxMaestroRuntime(
        config,
        intent_classifier=classify,
        tool_executor=http_tool_executor,
    )
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)
    await _collect(
        adapter,
        {"type": "start", "sessionId": "dead-retrieval", "locale": "en-US"},
    )

    events = await _collect(
        adapter,
        {
            "type": "message",
            "sessionId": "dead-retrieval",
            "text": "Does VoiceScheduleAI work with Outlook?",
        },
    )

    assert generation_calls == []
    assert events[-1]["type"] == "response"
    assert events[-1]["text"] == "I cannot verify that information right now."
    assert events[-1]["metadata"]["phase"] == "tool_failure"


async def _collect(adapter: WebSessionAdapter, message: dict) -> list[dict]:
    return [event async for event in adapter.iter_events(message)]


def test_index_is_bound_to_corpus_and_model(corpus_path: Path, tmp_path: Path) -> None:
    index_path = tmp_path / "index.json"
    RetrievalWorker.from_files(
        corpus_path,
        index_path,
        client=FakeEmbedClient(),
        rebuild_index=True,
    )
    index = json.loads(index_path.read_text())
    index["model_digest"] = "different"
    index_path.write_text(json.dumps(index))

    with pytest.raises(RetrievalWorkerError, match="model digest"):
        RetrievalWorker.from_files(corpus_path, index_path, client=FakeEmbedClient())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": "", "language": "en"},
        {"query": "hello", "language": "fr"},
        {"query": "hello", "language": "en", "model": "other"},
        {"query": "hello", "language": "en", "api_key": "do-not-accept"},
        {"query": "Bearer do-not-embed", "language": "en"},
        {"query": "x" * 3000, "language": "en"},
    ],
)
def test_invalid_request_never_embeds(
    corpus_path: Path, tmp_path: Path, payload: dict
) -> None:
    client = FakeEmbedClient()
    worker = RetrievalWorker.from_files(
        corpus_path,
        tmp_path / "index.json",
        client=client,
        rebuild_index=True,
    )
    client.calls.clear()

    with pytest.raises(RetrievalWorkerError):
        worker.retrieve(payload)

    assert client.calls == []


def test_server_is_loopback_only_and_unknown_paths_fail(
    corpus_path: Path, tmp_path: Path
) -> None:
    worker = RetrievalWorker.from_files(
        corpus_path,
        tmp_path / "index.json",
        client=FakeEmbedClient(),
        rebuild_index=True,
    )
    with pytest.raises(ValueError, match="loopback"):
        RetrievalWorkerServer(worker=worker, host="0.0.0.0")

    server = RetrievalWorkerServer(worker=worker, port=0)
    server.start()
    try:
        port = int(server.url.rsplit(":", 1)[1])
        connection = http.client.HTTPConnection("127.0.0.1", port)
        connection.request("GET", "/health")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["model_digest"] == "digest-test"
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", port)
        connection.request("POST", "/unknown", "{}")
        assert connection.getresponse().status == 404
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", port)
        connection.request("POST", "/v1/retrieve", "x" * (MAX_BODY_BYTES + 1))
        response = connection.getresponse()
        assert response.status == 400
        assert json.loads(response.read())["status"] == "error"
        connection.close()
    finally:
        server.stop()
