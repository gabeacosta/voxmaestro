"""Qualify the resident VSAI Nomic embedding lane with a pinned EN/ES corpus.

This is an admission runner, not a retrieval service. It talks only to one
operator-selected loopback Ollama endpoint and performs deterministic cosine
ranking in-process. The corpus is synthetic and provenance-bearing; passing
this gate does not qualify a production tenant index.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_CORPUS = Path(__file__).parent.parent / "docs" / "wt" / "vsai_retrieval_v0.json"
DEFAULT_OUTPUT = (
    Path(__file__).parent.parent
    / "evidence"
    / "small-model-fleet"
    / "nomic_retrieval_admission.json"
)
SCHEMA_VERSION = "vsai-retrieval-admission.v0"
NOMIC_DIGEST = "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"


def validate_embedding_endpoint(endpoint: str) -> str:
    """Return the fixed Ollama embed URL after enforcing loopback-only access."""
    parsed = urllib.parse.urlparse(endpoint.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("embedding endpoint must be an HTTP URL")
    if parsed.hostname.lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("embedding endpoint must be loopback-only")
    normalized = endpoint.rstrip("/")
    return normalized if normalized.endswith("/api/embed") else f"{normalized}/api/embed"


def validate_corpus(corpus: Mapping[str, Any]) -> None:
    """Reject ambiguous or untraceable admission fixtures before inference."""
    if corpus.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"corpus schema_version must be {SCHEMA_VERSION!r}")
    documents = corpus.get("documents")
    queries = corpus.get("queries")
    provenance = corpus.get("provenance")
    thresholds = corpus.get("thresholds")
    if not isinstance(documents, list) or not documents:
        raise ValueError("corpus documents must be a non-empty list")
    if not isinstance(queries, list) or not queries:
        raise ValueError("corpus queries must be a non-empty list")
    if not isinstance(provenance, Mapping) or not isinstance(provenance.get("sources"), list):
        raise ValueError("corpus provenance sources are required")
    if not isinstance(thresholds, Mapping):
        raise ValueError("corpus thresholds are required")

    document_ids = [document.get("id") for document in documents]
    if len(document_ids) != len(set(document_ids)):
        raise ValueError("document ids must be unique")
    source_ids = {source.get("id") for source in provenance["sources"] if isinstance(source, Mapping)}
    for document in documents:
        if document.get("language") not in {"en", "es"}:
            raise ValueError("document language must be en or es")
        if not isinstance(document.get("text"), str) or not document["text"].strip():
            raise ValueError("document text must be non-empty")
        references = document.get("source_ids")
        if not isinstance(references, list) or not references or not set(references) <= source_ids:
            raise ValueError("every document must reference known provenance sources")

    known_documents = set(document_ids)
    query_ids = [query.get("id") for query in queries]
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("query ids must be unique")
    for query in queries:
        if query.get("language") not in {"en", "es"}:
            raise ValueError("query language must be en or es")
        if query.get("expected_id") not in known_documents:
            raise ValueError("query expected_id must reference a document")
        if not isinstance(query.get("text"), str) or not query["text"].strip():
            raise ValueError("query text must be non-empty")


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return cosine similarity after strict vector validation."""
    if not left or len(left) != len(right):
        raise ValueError("vectors must have the same non-zero dimension")
    if not all(math.isfinite(value) for value in (*left, *right)):
        raise ValueError("vectors must contain only finite values")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("cosine similarity rejects a zero-norm vector")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def rank_documents(
    query_vector: Sequence[float],
    documents: Sequence[Mapping[str, Any]],
    document_vectors: Sequence[Sequence[float]],
    *,
    language: str,
) -> list[dict[str, Any]]:
    """Rank documents after the runtime-owned language filter is applied."""
    if len(documents) != len(document_vectors):
        raise ValueError("document/vector count mismatch")
    ranked = [
        {
            "id": document["id"],
            "score": cosine_similarity(query_vector, vector),
        }
        for document, vector in zip(documents, document_vectors, strict=True)
        if document.get("language") == language
    ]
    ranked.sort(key=lambda item: (-item["score"], item["id"]))
    return ranked


class OllamaEmbedClient:
    """One fixed Nomic model behind one fixed loopback Ollama endpoint."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        expected_digest: str,
        timeout_s: float = 5.0,
    ) -> None:
        if not model.strip():
            raise ValueError("embedding model is required")
        if timeout_s <= 0:
            raise ValueError("embedding timeout must be positive")
        self.url = validate_embedding_endpoint(endpoint)
        self.base_url = self.url.removesuffix("/api/embed")
        self.model = model
        self.expected_digest = expected_digest
        self.timeout_s = timeout_s

    def inventory(self) -> dict[str, str]:
        """Verify the mutable model tag resolves to the admitted Ollama digest."""
        with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=self.timeout_s) as response:
            tags = json.loads(response.read())
        models = tags.get("models")
        if not isinstance(models, list):
            raise RuntimeError("Ollama model inventory is malformed")
        match = next(
            (
                item
                for item in models
                if isinstance(item, Mapping) and item.get("name") == self.model
            ),
            None,
        )
        if match is None or not isinstance(match.get("digest"), str):
            raise RuntimeError(f"embedding model {self.model!r} is absent from Ollama inventory")
        digest = match["digest"]
        if digest != self.expected_digest:
            raise RuntimeError(
                f"embedding model digest mismatch: expected {self.expected_digest}, got {digest}"
            )
        with urllib.request.urlopen(
            f"{self.base_url}/api/version", timeout=self.timeout_s
        ) as response:
            version_body = json.loads(response.read())
        version = version_body.get("version")
        if not isinstance(version, str) or not version:
            raise RuntimeError("Ollama version response is malformed")
        return {"digest": digest, "ollama_version": version}

    def embed(self, texts: Sequence[str]) -> tuple[list[list[float]], dict[str, int | None]]:
        if not texts or len(texts) > 64:
            raise ValueError("embedding batch must contain 1-64 texts")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("embedding inputs must be non-empty strings")
        payload = {"model": self.model, "input": list(texts), "keep_alive": "5m"}
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            body = json.loads(response.read())
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError("embedding response count mismatch")
        dimensions = {len(vector) for vector in embeddings if isinstance(vector, list)}
        if len(dimensions) != 1 or not dimensions or 0 in dimensions:
            raise RuntimeError("embedding response dimensions are invalid or inconsistent")
        if any(
            not isinstance(value, (int, float)) or not math.isfinite(value)
            for vector in embeddings
            for value in vector
        ):
            raise RuntimeError("embedding response contains a non-finite value")
        metrics = {
            "total_duration_ns": body.get("total_duration"),
            "load_duration_ns": body.get("load_duration"),
            "prompt_eval_count": body.get("prompt_eval_count"),
        }
        return embeddings, metrics


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def run_admission(
    corpus: Mapping[str, Any],
    client: OllamaEmbedClient,
    *,
    corpus_bytes: int,
    latency_runs: int,
) -> dict[str, Any]:
    validate_corpus(corpus)
    inventory = client.inventory()
    documents = corpus["documents"]
    queries = corpus["queries"]
    thresholds = corpus["thresholds"]

    startup_begin = time.perf_counter()
    document_vectors, document_metrics = client.embed(
        [f"search_document: {document['text']}" for document in documents]
    )
    startup_ms = (time.perf_counter() - startup_begin) * 1000
    query_vectors, query_metrics = client.embed(
        [f"search_query: {query['text']}" for query in queries]
    )
    dimensions = len(document_vectors[0])

    results = []
    reciprocal_ranks = []
    top1_hits = 0
    top3_hits = 0
    per_language: dict[str, list[bool]] = defaultdict(list)
    for query, query_vector in zip(queries, query_vectors, strict=True):
        ranking = rank_documents(
            query_vector,
            documents,
            document_vectors,
            language=query["language"],
        )
        ids = [item["id"] for item in ranking]
        rank = ids.index(query["expected_id"]) + 1
        top1_hits += rank == 1
        top3_hits += rank <= 3
        per_language[query["language"]].append(rank <= 3)
        reciprocal_ranks.append(1 / rank)
        results.append(
            {
                "query_id": query["id"],
                "language": query["language"],
                "expected_id": query["expected_id"],
                "rank": rank,
                "top3": [
                    {"id": item["id"], "score": round(item["score"], 8)}
                    for item in ranking[:3]
                ],
            }
        )

    stability_input = f"search_query: {queries[0]['text']}"
    stability_a, _ = client.embed([stability_input])
    stability_b, _ = client.embed([stability_input])
    vectors_exactly_equal = stability_a == stability_b

    latency_ms = []
    server_duration_ms = []
    for index in range(latency_runs):
        text = f"search_query: {queries[index % len(queries)]['text']}"
        before = time.perf_counter()
        _, metrics = client.embed([text])
        latency_ms.append((time.perf_counter() - before) * 1000)
        duration_ns = metrics.get("total_duration_ns")
        if isinstance(duration_ns, int):
            server_duration_ms.append(duration_ns / 1_000_000)

    count = len(queries)
    top1_accuracy = top1_hits / count
    recall_at_3 = top3_hits / count
    mrr = statistics.fmean(reciprocal_ranks)
    language_recall = {
        language: sum(hits) / len(hits) for language, hits in sorted(per_language.items())
    }
    p95_ms = _nearest_rank(latency_ms, 0.95)
    checks = {
        "dimensions": dimensions == thresholds["dimensions"],
        "top1_accuracy": top1_accuracy >= thresholds["top1_accuracy_min"],
        "recall_at_3": recall_at_3 >= thresholds["recall_at_3_min"],
        "mrr": mrr >= thresholds["mrr_min"],
        "per_language_recall_at_3": all(
            score >= thresholds["per_language_recall_at_3_min"]
            for score in language_recall.values()
        ),
        "identical_vector_stability": vectors_exactly_equal
        is thresholds["identical_vector_stability"],
        "steady_p95_ms": p95_ms <= thresholds["steady_p95_ms_max"],
    }

    return {
        "program": "VM-VSAI-RETRIEVAL-001",
        "observed_date": time.strftime("%Y-%m-%d"),
        "model": client.model,
        "model_digest": inventory["digest"],
        "serving_library": f"Ollama {inventory['ollama_version']}",
        "endpoint": client.url,
        "endpoint_scope": "loopback-only",
        "corpus": {
            "schema_version": corpus["schema_version"],
            "purpose": corpus["purpose"],
            "provenance": corpus["provenance"],
            "document_count": len(documents),
            "query_count": len(queries),
            "corpus_bytes": corpus_bytes,
            "estimated_float32_vector_bytes": len(documents) * dimensions * 4,
        },
        "embedding": {
            "dimensions": dimensions,
            "document_batch_metrics": document_metrics,
            "query_batch_metrics": query_metrics,
            "startup_batch_wall_ms": round(startup_ms, 3),
            "identical_vector_stability": vectors_exactly_equal,
        },
        "relevance": {
            "language_filter": "runtime-owned exact en/es filter applied before ranking",
            "top1_accuracy": round(top1_accuracy, 6),
            "recall_at_3": round(recall_at_3, 6),
            "mrr": round(mrr, 6),
            "per_language_recall_at_3": language_recall,
            "queries": results,
        },
        "latency": {
            "requests": latency_runs,
            "wall_p50_ms": round(statistics.median(latency_ms), 3),
            "wall_p95_ms": round(p95_ms, 3),
            "wall_range_ms": [round(min(latency_ms), 3), round(max(latency_ms), 3)],
            "server_p50_ms": round(statistics.median(server_duration_ms), 3)
            if server_duration_ms
            else None,
            "server_p95_ms": round(_nearest_rank(server_duration_ms, 0.95), 3)
            if server_duration_ms
            else None,
        },
        "thresholds": thresholds,
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "scope": "Embedding and deterministic in-memory ranking admission only; no production tenant corpus, persistent index, runtime retrieval route, or physical voice claim.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="nomic-embed-text:latest")
    parser.add_argument("--expected-digest", default=NOMIC_DIGEST)
    parser.add_argument("--latency-runs", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.latency_runs < 3:
        raise ValueError("latency-runs must be at least 3")
    corpus_raw = args.corpus.read_bytes()
    corpus = json.loads(corpus_raw)
    client = OllamaEmbedClient(
        args.endpoint,
        args.model,
        expected_digest=args.expected_digest,
    )
    result = run_admission(
        corpus,
        client,
        corpus_bytes=len(corpus_raw),
        latency_runs=args.latency_runs,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
