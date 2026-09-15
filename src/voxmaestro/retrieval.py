"""Bounded primitives for a fixed local embedding lane.

The caller selects the corpus, model, language, and endpoint. This module only
embeds and ranks already-selected semantic inputs; it has no routing, tool,
retry, fallback, or effect authority.
"""

from __future__ import annotations

import json
import http.client
import ipaddress
import math
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any

CORPUS_SCHEMA_VERSION = "vsai-retrieval-admission.v0"
MAX_LOCAL_RESPONSE_BYTES = 8 * 1024 * 1024
_CREDENTIAL_LIKE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_-]?key|authorization|password|secret|token)\s*[:=]"
    r"|\b(?:sk|ghp|gho|github_pat|hf|xox[baprs])[_-][a-z0-9_-]{8,}"
    r"|\beyJ[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+"
    r"|-----BEGIN [^-]*PRIVATE KEY|[a-z][a-z0-9+.-]*://[^\s/@]+:[^\s/@]+@)"
)


def validate_embedding_text(text: str) -> None:
    """Reject credential-like material before it can reach local inference."""
    if _CREDENTIAL_LIKE.search(text):
        raise ValueError("credential-like embedding input is forbidden")


def validate_embedding_endpoint(endpoint: str) -> str:
    """Return the fixed Ollama embed URL after enforcing loopback-only access."""
    parsed = urllib.parse.urlsplit(endpoint.rstrip("/"))
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") not in {"", "/api/embed"}
    ):
        raise ValueError("embedding endpoint must be a credential-free loopback HTTP URL")
    try:
        if not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise ValueError
        parsed.port
    except ValueError as exc:
        raise ValueError("embedding endpoint must use a numeric loopback host") from exc
    return f"http://{parsed.netloc}/api/embed"


def validate_corpus(corpus: Mapping[str, Any]) -> None:
    """Reject ambiguous or untraceable retrieval corpora before inference."""
    if corpus.get("schema_version") != CORPUS_SCHEMA_VERSION:
        raise ValueError(f"corpus schema_version must be {CORPUS_SCHEMA_VERSION!r}")
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
    source_ids = {
        source.get("id")
        for source in provenance["sources"]
        if isinstance(source, Mapping)
    }
    for document in documents:
        if document.get("language") not in {"en", "es"}:
            raise ValueError("document language must be en or es")
        if not isinstance(document.get("text"), str) or not document["text"].strip():
            raise ValueError("document text must be non-empty")
        validate_embedding_text(document["text"])
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
        validate_embedding_text(query["text"])


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return cosine similarity after strict vector validation."""
    if not left or len(left) != len(right):
        raise ValueError("vectors must have the same non-zero dimension")
    if not all(math.isfinite(value) for value in (*left, *right)):
        raise ValueError("vectors must contain only finite values")
    left_scale = max(abs(value) for value in left)
    right_scale = max(abs(value) for value in right)
    if left_scale == 0 or right_scale == 0:
        raise ValueError("cosine similarity rejects a zero-norm vector")
    left_norm = math.sqrt(math.fsum((value / left_scale) ** 2 for value in left))
    right_norm = math.sqrt(math.fsum((value / right_scale) ** 2 for value in right))
    similarity = math.fsum(
        (a / left_scale / left_norm) * (b / right_scale / right_norm)
        for a, b in zip(left, right, strict=True)
    )
    if not math.isfinite(similarity):
        raise ValueError("cosine similarity produced a non-finite result")
    return similarity


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
    """One fixed model behind one fixed loopback Ollama endpoint."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        expected_digest: str,
        timeout_s: float = 5.0,
        keep_alive: str | int = "5m",
    ) -> None:
        if not model.strip():
            raise ValueError("embedding model is required")
        validate_embedding_text(model)
        if not expected_digest.strip():
            raise ValueError("embedding model digest is required")
        if timeout_s <= 0 or not math.isfinite(timeout_s):
            raise ValueError("embedding timeout must be finite and positive")
        if not (
            (isinstance(keep_alive, str) and keep_alive.strip())
            or (isinstance(keep_alive, int) and keep_alive == -1)
        ):
            raise ValueError("embedding keep_alive must be a duration or numeric -1")
        self.url = validate_embedding_endpoint(endpoint)
        parsed = urllib.parse.urlsplit(self.url)
        self.base_url = self.url.removesuffix("/api/embed")
        self.host = parsed.hostname or ""
        self.port = parsed.port or 80
        self.model = model
        self.expected_digest = expected_digest
        self.timeout_s = timeout_s
        self.keep_alive = keep_alive

    def inventory(self) -> dict[str, str]:
        """Verify the mutable model tag resolves to the admitted Ollama digest."""
        digest = self._verify_digest()
        version_body = self._request_json("GET", "/api/version")
        version = version_body.get("version")
        if not isinstance(version, str) or not version:
            raise RuntimeError("Ollama version response is malformed")
        return {"digest": digest, "ollama_version": version}

    def _verify_digest(self) -> str:
        tags = self._request_json("GET", "/api/tags")
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
        return digest

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
        body = (
            None
            if payload is None
            else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        headers = {} if body is None else {"Content-Type": "application/json"}
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_LOCAL_RESPONSE_BYTES + 1)
        finally:
            connection.close()
        if response.status != 200:
            raise RuntimeError(f"local Ollama returned HTTP {response.status}")
        if len(raw) > MAX_LOCAL_RESPONSE_BYTES:
            raise RuntimeError("local Ollama response exceeded byte limit")
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("local Ollama response was not JSON") from exc
        if not isinstance(decoded, Mapping):
            raise RuntimeError("local Ollama response must be a JSON object")
        return decoded

    def embed(self, texts: Sequence[str]) -> tuple[list[list[float]], dict[str, int | None]]:
        if not texts or len(texts) > 64:
            raise ValueError("embedding batch must contain 1-64 texts")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("embedding inputs must be non-empty strings")
        for text in texts:
            validate_embedding_text(text)
        self._verify_digest()
        payload = {
            "model": self.model,
            "input": list(texts),
            "keep_alive": self.keep_alive,
        }
        body = self._request_json("POST", "/api/embed", payload)
        self._verify_digest()
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
