from __future__ import annotations

import pytest

from examples.run_vsai_retrieval_admission import (
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
