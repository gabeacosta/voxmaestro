"""Loopback-only retrieval service over one pinned local embedding model.

The service exposes one semantic operation: retrieve passages from one
operator-selected corpus/index pair. It cannot select a model, route, tool,
provider, retry, fallback, or effect. VoxMaestro remains the caller and owns
all of those decisions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from voxmaestro.retrieval import (
    OllamaEmbedClient,
    rank_documents,
    validate_corpus,
    validate_embedding_text,
)

INDEX_SCHEMA_VERSION = "vsai-retrieval-index.v0"
MAX_BODY_BYTES = 16_384
MAX_QUERY_BYTES = 2_048


class RetrievalWorkerError(ValueError):
    """The request, corpus, or persistent index violated its contract."""


class RetrievalBackendError(RuntimeError):
    """The pinned embedding backend failed or violated its response contract."""


class RetrievalBusyError(RuntimeError):
    """The single admitted inference slot is already occupied."""


def _corpus_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _validate_index(
    index: Mapping[str, Any],
    *,
    corpus_sha256: str,
    model: str,
    model_digest: str,
    documents: list[dict[str, Any]],
) -> list[list[float]]:
    if index.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise RetrievalWorkerError("retrieval index schema version mismatch")
    if index.get("corpus_sha256") != corpus_sha256:
        raise RetrievalWorkerError("retrieval index corpus hash mismatch")
    if index.get("model") != model:
        raise RetrievalWorkerError("retrieval index model identity mismatch")
    if index.get("model_digest") != model_digest:
        raise RetrievalWorkerError("retrieval index model digest mismatch")
    if index.get("document_ids") != [document["id"] for document in documents]:
        raise RetrievalWorkerError("retrieval index document order mismatch")
    vectors = index.get("vectors")
    if not isinstance(vectors, list) or len(vectors) != len(documents):
        raise RetrievalWorkerError("retrieval index vector count mismatch")
    dimensions = {len(vector) for vector in vectors if isinstance(vector, list)}
    if len(dimensions) != 1 or not dimensions or 0 in dimensions:
        raise RetrievalWorkerError("retrieval index dimensions are invalid")
    if any(
        not isinstance(value, (int, float)) or not math.isfinite(value)
        for vector in vectors
        for value in vector
    ):
        raise RetrievalWorkerError("retrieval index contains a non-finite value")
    declared_dimensions = index.get("dimensions")
    if declared_dimensions != next(iter(dimensions)):
        raise RetrievalWorkerError("retrieval index declared dimension mismatch")
    return vectors


class RetrievalWorker:
    """Serve one persistent index using one fixed embedding client."""

    def __init__(
        self,
        *,
        documents: list[dict[str, Any]],
        document_vectors: list[list[float]],
        client: OllamaEmbedClient,
        corpus_sha256: str,
        model_digest: str,
        top_k: int = 1,
        min_score: float = 0.6,
        min_margin: float = 0.03,
        subject_terms: tuple[str, ...] = ("VoiceScheduleAI",),
    ) -> None:
        if top_k < 1 or top_k > 3:
            raise ValueError("retrieval top_k must be between 1 and 3")
        if not math.isfinite(min_score) or not -1 <= min_score <= 1:
            raise ValueError("retrieval min_score must be finite and between -1 and 1")
        if not math.isfinite(min_margin) or not 0 <= min_margin <= 2:
            raise ValueError("retrieval min_margin must be finite and between 0 and 2")
        if any(not term.strip() for term in subject_terms):
            raise ValueError("retrieval subject terms must be non-empty")
        self.documents = documents
        self.document_vectors = document_vectors
        self.client = client
        self.corpus_sha256 = corpus_sha256
        self.model_digest = model_digest
        self.top_k = top_k
        self.min_score = min_score
        self.min_margin = min_margin
        self.subject_terms = subject_terms
        self.dimensions = len(document_vectors[0])
        self._inference_slot = threading.BoundedSemaphore(1)

    @classmethod
    def from_files(
        cls,
        corpus_path: Path,
        index_path: Path,
        *,
        client: OllamaEmbedClient,
        rebuild_index: bool = False,
        top_k: int = 1,
        min_score: float = 0.6,
        min_margin: float = 0.03,
        subject_terms: tuple[str, ...] = ("VoiceScheduleAI",),
    ) -> RetrievalWorker:
        raw = corpus_path.read_bytes()
        corpus = json.loads(raw)
        if not isinstance(corpus, Mapping):
            raise RetrievalWorkerError("retrieval corpus must be a JSON object")
        validate_corpus(corpus)
        documents = [dict(document) for document in corpus["documents"]]
        corpus_sha256 = _corpus_sha256(raw)
        inventory = client.inventory()
        model_digest = inventory["digest"]

        if rebuild_index:
            vectors, _ = client.embed(
                [f"search_document: {document['text']}" for document in documents]
            )
            index = {
                "schema_version": INDEX_SCHEMA_VERSION,
                "corpus_sha256": corpus_sha256,
                "model": client.model,
                "model_digest": model_digest,
                "dimensions": len(vectors[0]),
                "document_ids": [document["id"] for document in documents],
                "vectors": vectors,
            }
            index_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = index_path.with_name(f".{index_path.name}.tmp")
            temporary.write_text(json.dumps(index, separators=(",", ":")) + "\n")
            temporary.replace(index_path)
        elif not index_path.is_file():
            raise RetrievalWorkerError(
                "retrieval index is missing; rebuild it explicitly before serving"
            )

        index = json.loads(index_path.read_text())
        if not isinstance(index, Mapping):
            raise RetrievalWorkerError("retrieval index must be a JSON object")
        document_vectors = _validate_index(
            index,
            corpus_sha256=corpus_sha256,
            model=client.model,
            model_digest=model_digest,
            documents=documents,
        )
        return cls(
            documents=documents,
            document_vectors=document_vectors,
            client=client,
            corpus_sha256=corpus_sha256,
            model_digest=model_digest,
            top_k=top_k,
            min_score=min_score,
            min_margin=min_margin,
            subject_terms=subject_terms,
        )

    def retrieve(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise RetrievalWorkerError("request body must be a JSON object")
        if set(payload) != {"query", "language"}:
            raise RetrievalWorkerError("request must contain only query and language")
        query = payload.get("query")
        language = payload.get("language")
        if not isinstance(query, str) or not query.strip():
            raise RetrievalWorkerError("query must be a non-empty string")
        if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
            raise RetrievalWorkerError("query exceeds 2048 bytes")
        try:
            validate_embedding_text(query)
        except ValueError as exc:
            raise RetrievalWorkerError(str(exc)) from exc
        if language not in {"en", "es"}:
            raise RetrievalWorkerError("language must be en or es")
        normalized_query = query
        for term in self.subject_terms:
            normalized_query = re.sub(
                rf"\b{re.escape(term)}\b",
                " ",
                normalized_query,
                flags=re.IGNORECASE,
            )
        normalized_query = " ".join(normalized_query.split())
        if not any(character.isalnum() for character in normalized_query):
            return self._response(language, [])
        if not self._inference_slot.acquire(blocking=False):
            raise RetrievalBusyError("retrieval worker is busy")
        try:
            query_vectors, _ = self.client.embed([f"search_query: {normalized_query}"])
        except Exception as exc:
            raise RetrievalBackendError("fixed embedding backend failed") from exc
        finally:
            self._inference_slot.release()
        query_vector = query_vectors[0]
        if len(query_vector) != self.dimensions:
            raise RetrievalBackendError("query embedding dimension mismatch")
        try:
            ranked = rank_documents(
                query_vector,
                self.documents,
                self.document_vectors,
                language=language,
            )
        except ValueError as exc:
            raise RetrievalBackendError("deterministic ranking failed") from exc
        best_score = ranked[0]["score"] if ranked else -1.0
        second_score = ranked[1]["score"] if len(ranked) > 1 else -1.0
        if best_score < self.min_score or best_score - second_score < self.min_margin:
            return self._response(language, [])
        by_id = {document["id"]: document for document in self.documents}
        matches = []
        for item in ranked:
            if len(matches) >= self.top_k or item["score"] < self.min_score:
                break
            document = by_id[item["id"]]
            matches.append(
                {
                    "id": document["id"],
                    "language": document["language"],
                    "category": document.get("category"),
                    "text": document["text"],
                    "source_ids": list(document["source_ids"]),
                    "score": round(item["score"], 8),
                }
            )
        return self._response(language, matches)

    def warm(self) -> None:
        """Load and pin the fixed model before accepting realtime traffic."""
        if not self._inference_slot.acquire(blocking=False):
            raise RetrievalBusyError("retrieval worker is busy")
        try:
            vectors, _ = self.client.embed(["search_query: readiness probe"])
        except Exception as exc:
            raise RetrievalBackendError("fixed embedding backend failed readiness") from exc
        finally:
            self._inference_slot.release()
        if len(vectors[0]) != self.dimensions:
            raise RetrievalBackendError("readiness embedding dimension mismatch")

    def _response(self, language: str, matches: list[dict[str, Any]]) -> dict[str, Any]:
        matched = bool(matches)
        return {
            "status": "ok" if matched else "no_match",
            "success": matched,
            "model": self.client.model,
            "model_digest": self.model_digest,
            "corpus_sha256": self.corpus_sha256,
            "language": language,
            "matches": matches,
        }


class RetrievalWorkerServer:
    """Expose one RetrievalWorker at ``POST /v1/retrieve`` on loopback."""

    def __init__(
        self,
        *,
        worker: RetrievalWorker,
        host: str = "127.0.0.1",
        port: int = 8092,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("retrieval worker must bind loopback-only")
        self.worker = worker
        server_self = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def _send(self, status: int, body: Mapping[str, Any]) -> None:
                encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self) -> None:  # noqa: N802
                if self.path != "/health":
                    self._send(404, {"status": "error", "error": "not found"})
                    return
                self._send(
                    200,
                    {
                        "status": "ok",
                        "model": server_self.worker.client.model,
                        "model_digest": server_self.worker.model_digest,
                        "corpus_sha256": server_self.worker.corpus_sha256,
                        "dimensions": server_self.worker.dimensions,
                        "top_k": server_self.worker.top_k,
                        "min_score": server_self.worker.min_score,
                        "min_margin": server_self.worker.min_margin,
                    },
                )

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/v1/retrieve":
                    self._send(404, {"status": "error", "error": "not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, {"status": "error", "error": "invalid content length"})
                    return
                if length <= 0 or length > MAX_BODY_BYTES:
                    self._send(400, {"status": "error", "error": "invalid request body size"})
                    return
                try:
                    payload = json.loads(self.rfile.read(length))
                    response = server_self.worker.retrieve(payload)
                except (json.JSONDecodeError, RetrievalWorkerError) as exc:
                    self._send(400, {"status": "error", "error": str(exc)})
                    return
                except RetrievalBusyError as exc:
                    self._send(503, {"status": "error", "error": str(exc)})
                    return
                except RetrievalBackendError as exc:
                    self._send(502, {"status": "error", "error": str(exc)})
                    return
                except Exception:
                    self._send(502, {"status": "error", "error": "retrieval backend failed"})
                    return
                self._send(200 if response["success"] else 404, response)

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
