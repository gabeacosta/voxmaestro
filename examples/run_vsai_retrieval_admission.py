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
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from voxmaestro.retrieval import (
    OllamaEmbedClient,
    rank_documents,
    validate_corpus,
)

DEFAULT_CORPUS = Path(__file__).parent.parent / "docs" / "wt" / "vsai_retrieval_v0.json"
DEFAULT_OUTPUT = (
    Path(__file__).parent.parent
    / "evidence"
    / "small-model-fleet"
    / "nomic_retrieval_admission.json"
)
SCHEMA_VERSION = "vsai-retrieval-admission.v0"
NOMIC_DIGEST = "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"


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
