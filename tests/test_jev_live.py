from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from voxmaestro.reflex.jev_backend import (
    QUESTION_CHOICE,
    QUESTION_NOUL,
    DecisionRequest,
    DecisionTrace,
    JevProtocolError,
    JevQuestion,
    hand_off_shadow,
)
from voxmaestro.reflex.jev_live import (
    EXPERIMENT_ID,
    SCHEMA_VERSION,
    FrozenSpecimen,
    LiveAcceptanceError,
    QueueShadowDispatcher,
    discover_typesafe_models,
    freeze_specimen,
    reconcile_provider_reports,
    seal_evidence,
    select_pinned_model,
)


def _specimen() -> FrozenSpecimen:
    return FrozenSpecimen(
        experiment_id=EXPERIMENT_ID,
        schema_version=SCHEMA_VERSION,
        model="jev-1.13.0",
        question_version="q1",
        state="Move the appointment and send confirmation.",
        questions=(
            JevQuestion(
                kind=QUESTION_CHOICE,
                key="intent",
                prompt="Choose intent.",
                options=("schedule", "other"),
            ),
            JevQuestion(
                kind=QUESTION_NOUL,
                key="needs_tool",
                prompt="Does this need a tool?",
                threshold=0.5,
            ),
        ),
        providers=("typesafe", "vercel-typesafe"),
        runs_per_provider=1,
        source_commit="a" * 40,
        frozen_at_utc="2026-09-23T18:00:00Z",
        sha256="b" * 64,
    )


def _report(request_hash: str, intent: str, noul: float) -> dict:
    return {
        "enqueue_latency": {
            "min_ms": 0.1,
            "median_ms": 0.1,
            "p95_ms": 0.1,
            "max_ms": 0.1,
        },
        "remote_latency": {
            "min_ms": 100.0,
            "median_ms": 100.0,
            "p95_ms": 100.0,
            "max_ms": 100.0,
        },
        "results": [
            {
                "turn_id": "turn-001",
                "verdicts": [
                    {
                        "key": "intent",
                        "kind": "choice",
                        "value": intent,
                        "confidence": 0.9,
                        "ok": True,
                    },
                    {
                        "key": "needs_tool",
                        "kind": "noul",
                        "value": noul,
                        "confidence": None,
                        "ok": True,
                    },
                ],
            }
        ],
        "observations": [
            {
                "request_hash": request_hash,
                "ok": True,
                "total_latency_ms": 100.0,
            }
        ],
    }


def test_select_pinned_model_ignores_moving_aliases():
    selected = select_pinned_model(
        (
            {"name": "jev-latest", "release_date": "2026-09-23"},
            {"name": "jev-1.12.0", "release_date": "2026-09-15"},
            {"name": "jev-1.13.0", "release_date": "2026-09-21"},
        )
    )
    assert selected == "jev-1.13.0"


def test_freeze_refuses_moving_alias(tmp_path: Path):
    template = tmp_path / "template.json"
    template.write_text(
        json.dumps(
            {
                "experiment_id": EXPERIMENT_ID,
                "schema_version": SCHEMA_VERSION,
                "model": "__PINNED_MODEL__",
                "question_version": "q1",
                "state": "synthetic state",
                "questions": [
                    {
                        "kind": "noul",
                        "key": "needs_tool",
                        "prompt": "Needs tool?",
                        "threshold": 0.5,
                    }
                ],
                "providers": ["typesafe", "vercel-typesafe"],
                "runs_per_provider": 1,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(LiveAcceptanceError, match="moving_model_alias_forbidden"):
        freeze_specimen(
            template,
            tmp_path / "frozen.json",
            model="jev-latest",
            source_commit="a" * 40,
            frozen_at_utc="2026-09-23T18:00:00Z",
        )


def test_model_discovery_rejects_redirect(monkeypatch):
    class RedirectingOpener:
        def open(self, _request, timeout):
            assert timeout == 5.0
            raise JevProtocolError("unexpected_redirect:302")

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *_handlers: RedirectingOpener(),
    )

    with pytest.raises(LiveAcceptanceError, match="unexpected_redirect:302"):
        discover_typesafe_models("secret-test-key")


def test_stale_hash_blocks_freeze_before_specimen_write(tmp_path: Path):
    template = tmp_path / "template.json"
    template.write_text(
        json.dumps(
            {
                "experiment_id": EXPERIMENT_ID,
                "schema_version": SCHEMA_VERSION,
                "model": "__PINNED_MODEL__",
                "question_version": "q1",
                "state": "synthetic state",
                "questions": [
                    {
                        "kind": "noul",
                        "key": "needs_tool",
                        "prompt": "Needs tool?",
                        "threshold": 0.5,
                    }
                ],
                "providers": ["typesafe", "vercel-typesafe"],
                "runs_per_provider": 1,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "frozen.json"
    output.with_suffix(".json.sha256").write_text("stale\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="frozen_specimen_already_exists"):
        freeze_specimen(
            template,
            output,
            model="jev-1.13.0",
            source_commit="a" * 40,
            frozen_at_utc="2026-09-23T18:00:00Z",
        )

    assert not output.exists()


def test_freeze_is_create_only(tmp_path: Path):
    template = tmp_path / "template.json"
    template.write_text(
        json.dumps(
            {
                "experiment_id": EXPERIMENT_ID,
                "schema_version": SCHEMA_VERSION,
                "model": "__PINNED_MODEL__",
                "question_version": "q1",
                "state": "synthetic state",
                "questions": [
                    {
                        "kind": "noul",
                        "key": "needs_tool",
                        "prompt": "Needs tool?",
                        "threshold": 0.5,
                    }
                ],
                "providers": ["typesafe", "vercel-typesafe"],
                "runs_per_provider": 1,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "frozen.json"
    freeze_specimen(
        template,
        output,
        model="jev-1.13.0",
        source_commit="a" * 40,
        frozen_at_utc="2026-09-23T18:00:00Z",
    )
    with pytest.raises(FileExistsError):
        freeze_specimen(
            template,
            output,
            model="jev-1.13.0",
            source_commit="a" * 40,
            frozen_at_utc="2026-09-23T18:01:00Z",
        )


def test_queue_backed_handoff_returns_before_remote_work_finishes():
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def worker(_request: DecisionRequest) -> None:
        entered.set()
        release.wait(1.0)
        finished.set()

    dispatcher = QueueShadowDispatcher(worker)
    request = DecisionRequest(
        state="synthetic",
        questions=(),
        trace=DecisionTrace(
            session_id="session",
            turn_id="turn",
            question_version="q1",
        ),
    )

    hand_off_shadow(request, dispatcher.dispatch)

    assert entered.wait(0.5)
    assert not finished.is_set()
    release.set()
    dispatcher.close()
    assert finished.is_set()


def test_reconciliation_passes_same_semantic_decisions():
    specimen = _specimen()
    reports = {
        "typesafe": _report("same-hash", "schedule", 0.91),
        "vercel-typesafe": _report("same-hash", "schedule", 0.87),
    }
    result = reconcile_provider_reports(specimen, reports)
    assert result["status"] == "PASS"
    assert result["request_identity"] is True
    assert result["semantic_agreement"] is True
    assert result["authority_promoted"] is False


def test_reconciliation_requires_review_on_semantic_disagreement():
    specimen = _specimen()
    reports = {
        "typesafe": _report("same-hash", "schedule", 0.91),
        "vercel-typesafe": _report("same-hash", "other", 0.87),
    }
    result = reconcile_provider_reports(specimen, reports)
    assert result["status"] == "REVIEW_DISAGREEMENT"
    assert result["authority_promoted"] is False


def test_reconciliation_fails_request_drift():
    specimen = _specimen()
    reports = {
        "typesafe": _report("left-hash", "schedule", 0.91),
        "vercel-typesafe": _report("right-hash", "schedule", 0.91),
    }
    result = reconcile_provider_reports(specimen, reports)
    assert result["status"] == "FAIL_REQUEST_DRIFT"


def test_seal_is_content_addressed_and_create_only(tmp_path: Path):
    (tmp_path / "frozen-specimen.json").write_text('{"a":1}\n', encoding="utf-8")
    (tmp_path / "reconciliation.json").write_text('{"status":"PASS"}\n', encoding="utf-8")

    seal = seal_evidence(tmp_path, "PASS")

    assert len(seal["seal_sha256"]) == 64
    assert set(seal["files"]) == {"frozen-specimen.json", "reconciliation.json"}
    with pytest.raises(FileExistsError):
        seal_evidence(tmp_path, "PASS")
