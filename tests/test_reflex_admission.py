from __future__ import annotations

import hashlib

import pytest

from voxmaestro.reflex.admission import evaluate_rows
from voxmaestro.reflex.shapes import GateDecision, Language, ReflexIntent


def _rows(tool_positive: int = 59, ordinary: int = 1):
    rows = []
    for index in range(tool_positive):
        rows.append(
            {
                "id": f"tool-{index}",
                "transcript": f"book appointment {index}",
                "expected_intent": "schedule",
                "expected_tool_needed": True,
                "expected_language": "en",
                "provenance": "real",
            }
        )
    for index in range(ordinary):
        rows.append(
            {
                "id": f"faq-{index}",
                "transcript": f"what are your hours {index}",
                "expected_intent": "faq",
                "expected_tool_needed": False,
                "expected_language": "en",
                "provenance": "real",
            }
        )
    return rows


class FakeGate:
    def __init__(self, *, false_negative_id=None, fallback_id=None, latency_ms=10.0):
        self.false_negative_id = false_negative_id
        self.fallback_id = fallback_id
        self.latency_ms = latency_ms
        self.backend = type(
            "Backend",
            (),
            {
                "backend_id": "fake",
                "model_id": "fake-model",
                "model_hash": "sha256:fake",
            },
        )()

    async def classify(self, text):
        digest = hashlib.sha256(text.encode()).hexdigest()
        index = text.rsplit(" ", 1)[-1]
        row_id = f"tool-{index}" if text.startswith("book") else f"faq-{index}"
        if row_id == self.fallback_id:
            return GateDecision(
                status="fallback",
                latency_ms=self.latency_ms,
                backend_id="fake",
                input_digest=digest,
                model_id="fake-model",
                model_hash="sha256:fake",
                fallback_reason="timeout",
            )
        is_tool = text.startswith("book") and row_id != self.false_negative_id
        return GateDecision(
            status="ok",
            latency_ms=self.latency_ms,
            backend_id="fake",
            input_digest=digest,
            intent=ReflexIntent.SCHEDULE if text.startswith("book") else ReflexIntent.FAQ,
            tool_needed_probability=0.9 if is_tool else 0.1,
            language=Language.EN,
            model_id="fake-model",
            model_hash="sha256:fake",
        )


@pytest.mark.asyncio
async def test_admission_pass_requires_representative_load_and_evidence():
    report = await evaluate_rows(_rows(), FakeGate(), load_profile="representative")

    assert report["verdict"] == "PASS_REFLEX_MODEL_ADMISSION"
    assert report["metrics"]["tool_positive_count"] == 59
    assert report["metrics"]["tool_false_negative_count"] == 0
    assert all(report["checks"].values())


@pytest.mark.asyncio
async def test_admission_blocks_false_downgrade():
    report = await evaluate_rows(
        _rows(),
        FakeGate(false_negative_id="tool-0"),
        load_profile="representative",
    )

    assert report["verdict"] == "BLOCKED"
    assert report["metrics"]["tool_false_negative_count"] == 1
    assert report["checks"]["tool_false_negative_requirement"] is False


@pytest.mark.asyncio
async def test_admission_blocks_backend_fallback_and_idle_benchmark():
    report = await evaluate_rows(
        _rows(),
        FakeGate(fallback_id="tool-0"),
        load_profile="idle",
    )

    assert report["verdict"] == "BLOCKED"
    assert report["checks"]["no_backend_fallbacks"] is False
    assert report["checks"]["representative_load_profile"] is False


@pytest.mark.asyncio
async def test_synthetic_rows_do_not_count_as_admission_evidence():
    rows = _rows(tool_positive=1, ordinary=0)
    rows.extend(
        {
            **rows[0],
            "id": f"synthetic-{index}",
            "provenance": "synthetic",
        }
        for index in range(100)
    )

    report = await evaluate_rows(rows, FakeGate(), load_profile="representative")

    assert report["verdict"] == "BLOCKED"
    assert report["corpus"]["real_rows_evaluated"] == 1
    assert report["corpus"]["synthetic_rows_ignored"] == 100
