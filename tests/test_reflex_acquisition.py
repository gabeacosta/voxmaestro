import json
import subprocess
import sys
from pathlib import Path

import pytest

from voxmaestro.reflex.acquisition import (
    BLOCKED_INSUFFICIENT_REAL_LABELS,
    BLOCKED_OPERATOR_PROVENANCE_ASSERTION,
    BLOCKED_PHYSICAL_ADMISSION,
    BLOCKED_RUNTIME_PREREQUISITES,
    PASS_REFLEX_PHYSICAL_ADMISSION,
    READY_FOR_PHYSICAL_ADMISSION,
    AcquisitionStateError,
    classify_acquisition,
)


def _summary(
    *,
    asserted: bool,
    unique_ready: int = 60,
    unique_positive: int = 59,
    final_rows: int = 60,
    final_positive: int = 59,
    sufficient: bool = True,
):
    return {
        "real_call_assertion": asserted,
        "unique_ready_replay_rows": unique_ready,
        "unique_ready_tool_positive_rows": unique_positive,
        "final_rows": final_rows if asserted else 0,
        "final_tool_positive_rows": final_positive if asserted else 0,
        "admission_shape_sufficient": sufficient if asserted else False,
    }


def test_counts_cannot_substitute_for_operator_provenance_assertion():
    state = classify_acquisition(_summary(asserted=False))

    assert state["state"] == BLOCKED_OPERATOR_PROVENANCE_ASSERTION


def test_insufficient_candidate_rows_stay_blocked_before_assertion():
    state = classify_acquisition(
        _summary(asserted=False, unique_ready=29, unique_positive=29)
    )

    assert state["state"] == BLOCKED_INSUFFICIENT_REAL_LABELS


def test_asserted_corpus_requires_runtime_preflight():
    state = classify_acquisition(_summary(asserted=True))

    assert state["state"] == BLOCKED_RUNTIME_PREREQUISITES
    assert state["preflight"] == "NOT_RUN"


def test_failed_runtime_preflight_is_not_model_admission_failure():
    state = classify_acquisition(
        _summary(asserted=True),
        physical_preflight={"verdict": "BLOCKED_RUNTIME_PREREQUISITES"},
    )

    assert state["state"] == BLOCKED_RUNTIME_PREREQUISITES


def test_ready_preflight_stops_before_execution_by_default():
    state = classify_acquisition(
        _summary(asserted=True),
        physical_preflight={"verdict": READY_FOR_PHYSICAL_ADMISSION},
    )

    assert state["state"] == READY_FOR_PHYSICAL_ADMISSION


def test_execute_requires_a_physical_result():
    state = classify_acquisition(
        _summary(asserted=True),
        physical_preflight={"verdict": READY_FOR_PHYSICAL_ADMISSION},
        execute=True,
    )

    assert state["state"] == BLOCKED_PHYSICAL_ADMISSION
    assert state["physical"] == "NOT_RUN"


def test_physical_pass_is_preserved_without_upgrading_authority():
    state = classify_acquisition(
        _summary(asserted=True),
        physical_preflight={"verdict": READY_FOR_PHYSICAL_ADMISSION},
        execute=True,
        physical_admission={"verdict": PASS_REFLEX_PHYSICAL_ADMISSION},
    )

    assert state["state"] == PASS_REFLEX_PHYSICAL_ADMISSION


def test_bad_summary_shape_is_rejected():
    summary = _summary(asserted=True)
    summary["unique_ready_tool_positive_rows"] = True

    with pytest.raises(AcquisitionStateError):
        classify_acquisition(summary)


def test_one_shot_stops_at_provenance_before_model_preflight(tmp_path):
    repo_root = Path(__file__).parents[1]
    training = tmp_path / "training"
    training.mkdir()
    source = training / "examples_fixture.jsonl"
    rows = [
        {
            "text": f"Book me slot {index}",
            "intent": "book_appointment",
            "source": "bland_replay",
            "call_id": f"call-{index}",
            "confidence": 1.0,
        }
        for index in range(59)
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))

    out = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "examples/run_reflex_evidence_acquisition.py",
            "--input-dir",
            str(training),
            "--default-language",
            "en",
            "--model-path",
            str(tmp_path / "model-does-not-exist"),
            "--out",
            str(out),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    state = json.loads((out / "acquisition-state.json").read_text())
    assert state["state"] == BLOCKED_OPERATOR_PROVENANCE_ASSERTION
    assert state["counts"]["unique_ready_replay_rows"] == 59
    assert state["counts"]["unique_ready_tool_positive_rows"] == 59
    assert not (out / "physical" / "physical-preflight.json").exists()
