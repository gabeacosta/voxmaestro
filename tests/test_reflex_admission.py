from __future__ import annotations

import hashlib
import json

import pytest

from voxmaestro.reflex.admission import (
    adjudicate_physical,
    evaluate_rows,
    finalize_legacy_replay_rows,
    redact_reflex_text,
    stage_legacy_training_row,
)
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



def _physical_voice_evidence(*, verdict="PASS", complete=True, acoustic=True):
    return {
        "qualification": {"verdict": verdict},
        "lane": {"evidence_complete": complete},
        "acoustic_crosstalk_measured": acoustic,
    }


def test_physical_admission_requires_complete_voice_witness():
    model = {"verdict": "PASS_REFLEX_MODEL_ADMISSION"}

    report = adjudicate_physical(
        model,
        _physical_voice_evidence(),
        voice_alive_through_benchmark=True,
    )

    assert report["verdict"] == "PASS_REFLEX_PHYSICAL_ADMISSION"
    assert all(report["checks"].values())


@pytest.mark.parametrize(
    ("voice", "alive"),
    [
        (_physical_voice_evidence(verdict="FAIL"), True),
        (_physical_voice_evidence(complete=False), True),
        (_physical_voice_evidence(acoustic=False), True),
        (_physical_voice_evidence(), False),
    ],
)
def test_physical_admission_marks_incomplete_or_bad_witness_invalid(voice, alive):
    report = adjudicate_physical(
        {"verdict": "PASS_REFLEX_MODEL_ADMISSION"},
        voice,
        voice_alive_through_benchmark=alive,
    )

    assert report["verdict"] == "TEST_INVALID"


def test_physical_admission_blocks_model_after_valid_voice_witness():
    report = adjudicate_physical(
        {"verdict": "BLOCKED"},
        _physical_voice_evidence(),
        voice_alive_through_benchmark=True,
    )

    assert report["verdict"] == "BLOCKED"



@pytest.mark.parametrize(
    ("admission_ok", "voice_ok"),
    [(False, True), (True, False), (False, False)],
)
def test_physical_admission_rejects_child_process_failure(admission_ok, voice_ok):
    report = adjudicate_physical(
        {"verdict": "PASS_REFLEX_MODEL_ADMISSION"},
        _physical_voice_evidence(),
        voice_alive_through_benchmark=True,
        admission_process_ok=admission_ok,
        voice_process_ok=voice_ok,
    )

    assert report["verdict"] == "TEST_INVALID"



def _legacy_row(**overrides):
    row = {
        "text": "Can you book me Thursday at 3? Call 702-555-1212",
        "intent": "book_appointment",
        "source": "bland_replay",
        "call_id": "call-secret-123",
        "state": "qualification",
        "confidence": 1.0,
        "agent_name": "dealiq-qualifier",
    }
    row.update(overrides)
    return row


def test_legacy_replay_maps_to_reflex_without_persisting_call_id():
    staged = stage_legacy_training_row(_legacy_row(), default_language="en")

    assert staged["status"] == "ready_replay"
    assert staged["proposed_intent"] == "schedule"
    assert staged["proposed_tool_needed"] is True
    assert staged["expected_language"] == "en"
    assert staged["language_source"] == "operator-default"
    assert "call-secret-123" not in json.dumps(staged)
    assert "[phone]" in staged["transcript"]


@pytest.mark.parametrize(
    ("legacy_intent", "intent", "tool"),
    [
        ("check_availability", "schedule", True),
        ("reschedule", "schedule", True),
        ("general_inquiry", "faq", True),
        ("pricing", "pricing", False),
        ("complaints", "complaint", False),
        ("unknown", "off-script", False),
    ],
)
def test_legacy_mapping_is_explicit(legacy_intent, intent, tool):
    staged = stage_legacy_training_row(
        _legacy_row(intent=legacy_intent),
        default_language="en",
    )

    assert staged["status"] == "ready_replay"
    assert staged["proposed_intent"] == intent
    assert staged["proposed_tool_needed"] is tool


@pytest.mark.parametrize("legacy_intent", ["transfer_agent", "opt_out", "callback_request"])
def test_legacy_ambiguous_intents_are_quarantined(legacy_intent):
    staged = stage_legacy_training_row(
        _legacy_row(intent=legacy_intent),
        default_language="en",
    )

    assert staged["status"] == "needs_intent_review"


def test_legacy_live_labels_cannot_become_ground_truth_automatically():
    staged = stage_legacy_training_row(
        _legacy_row(source="bland_live"),
        default_language="en",
    )

    assert staged["status"] == "needs_label_review"


def test_legacy_replay_requires_language_resolution():
    staged = stage_legacy_training_row(_legacy_row())

    assert staged["status"] == "needs_language"


def test_legacy_replay_requires_ground_truth_confidence():
    staged = stage_legacy_training_row(
        _legacy_row(confidence=0.8),
        default_language="en",
    )

    assert staged["status"] == "needs_label_review"


def test_finalize_requires_explicit_real_call_assertion():
    staged = [
        stage_legacy_training_row(
            _legacy_row(call_id=f"call-{index}"),
            default_language="en",
        )
        for index in range(60)
    ]

    final, summary = finalize_legacy_replay_rows(
        staged,
        assert_replays_are_real_calls=False,
    )

    assert final == []
    assert summary["final_rows"] == 0
    assert summary["real_call_assertion"] is False
    assert summary["admission_shape_sufficient"] is False


def test_finalize_can_meet_shape_gate_from_ground_truth_tool_replays():
    staged = [
        stage_legacy_training_row(
            _legacy_row(call_id=f"call-{index}", text=f"Book me slot {index}"),
            default_language="en",
        )
        for index in range(59)
    ]

    final, summary = finalize_legacy_replay_rows(
        staged,
        assert_replays_are_real_calls=True,
    )

    assert len(final) == 59
    assert summary["final_tool_positive_rows"] == 59
    assert summary["admission_shape_sufficient"] is True
    assert all(row["provenance"] == "real" for row in final)
    assert all("call_id" not in row for row in final)


def test_redact_reflex_text_removes_common_phone_and_email():
    text = redact_reflex_text("Email Gabe@example.com or call (702) 555-1212.")

    assert "Gabe@example.com" not in text
    assert "702" not in text
    assert "[email]" in text
    assert "[phone]" in text



def test_legacy_replay_without_call_id_is_not_admissible():
    staged = stage_legacy_training_row(
        _legacy_row(call_id=""),
        default_language="en",
    )

    assert staged["status"] == "needs_provenance"


def test_finalize_deduplicates_same_harvested_replay():
    row = stage_legacy_training_row(_legacy_row(), default_language="en")
    staged = [row for _ in range(100)]

    final, summary = finalize_legacy_replay_rows(
        staged,
        assert_replays_are_real_calls=True,
    )

    assert len(final) == 1
    assert summary["ready_replay_rows"] == 100
    assert summary["duplicate_ready_rows_ignored"] == 99
    assert summary["unique_ready_replay_rows"] == 1
    assert summary["admission_shape_sufficient"] is False
