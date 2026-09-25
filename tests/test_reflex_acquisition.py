import json
import subprocess
import sys
from pathlib import Path

import pytest

from voxmaestro.reflex.acquisition import (
    BLOCKED_INSUFFICIENT_REAL_LABELS,
    BLOCKED_PHYSICAL_ADMISSION,
    BLOCKED_PROVENANCE_CLASSIFICATION,
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
    unreviewed: int | None = None,
    marked_real: int | None = None,
):
    if unreviewed is None:
        unreviewed = 0 if asserted else unique_ready
    if marked_real is None:
        marked_real = final_rows if asserted else 0
    return {
        "real_call_assertion": asserted,
        "provenance_mode": "all-real-assertion" if asserted else "selective-manifest",
        "unique_ready_replay_rows": unique_ready,
        "unique_ready_tool_positive_rows": unique_positive,
        "marked_real_rows": marked_real,
        "unreviewed_ready_rows": unreviewed,
        "final_rows": final_rows if (asserted or marked_real) else 0,
        "final_tool_positive_rows": final_positive if (asserted or marked_real) else 0,
        "admission_shape_sufficient": sufficient if (asserted or marked_real) else False,
    }


def test_candidate_counts_do_not_substitute_for_selective_provenance_review():
    state = classify_acquisition(_summary(asserted=False))

    assert state["state"] == BLOCKED_PROVENANCE_CLASSIFICATION


def test_insufficient_candidate_rows_stay_blocked_before_assertion():
    state = classify_acquisition(
        _summary(asserted=False, unique_ready=29, unique_positive=29)
    )

    assert state["state"] == BLOCKED_INSUFFICIENT_REAL_LABELS


def test_selectively_reviewed_corpus_can_require_runtime_preflight():
    state = classify_acquisition(
        _summary(
            asserted=False,
            marked_real=60,
            unreviewed=0,
            final_rows=60,
            final_positive=59,
            sufficient=True,
        )
    )

    assert state["state"] == BLOCKED_RUNTIME_PREREQUISITES
    assert state["preflight"] == "NOT_RUN"


def test_fully_reviewed_mixed_dataset_below_real_floor_is_insufficient():
    state = classify_acquisition(
        _summary(
            asserted=False,
            unique_ready=80,
            unique_positive=70,
            marked_real=20,
            unreviewed=0,
            final_rows=20,
            final_positive=18,
            sufficient=False,
        )
    )

    assert state["state"] == BLOCKED_INSUFFICIENT_REAL_LABELS


def test_selective_real_rows_can_satisfy_shape_without_blanket_assertion():
    state = classify_acquisition(
        _summary(
            asserted=False,
            unique_ready=80,
            unique_positive=70,
            marked_real=65,
            unreviewed=15,
            final_rows=65,
            final_positive=59,
            sufficient=True,
        )
    )

    assert state["state"] == BLOCKED_RUNTIME_PREREQUISITES


def test_failed_runtime_preflight_is_not_model_admission_failure():
    state = classify_acquisition(
        _summary(
            asserted=False,
            marked_real=60,
            unreviewed=0,
            final_rows=60,
            final_positive=59,
            sufficient=True,
        ),
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
    assert state["state"] == BLOCKED_PROVENANCE_CLASSIFICATION
    assert state["counts"]["unique_ready_replay_rows"] == 59
    assert state["counts"]["unique_ready_tool_positive_rows"] == 59
    assert not (out / "physical" / "physical-preflight.json").exists()


def test_one_shot_selective_manifest_excludes_demo_and_advances_to_preflight(tmp_path):
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
        for index in range(60)
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))

    out = tmp_path / "out"
    base_command = [
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
    ]

    first = subprocess.run(
        base_command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert first.returncode == 2

    review_path = out / "corpus" / "legacy-reflex-provenance-review.jsonl"
    review = [json.loads(line) for line in review_path.read_text().splitlines() if line.strip()]
    assert len(review) == 60
    for index, row in enumerate(review):
        row["classification"] = "real" if index < 59 else "demo"
        assert "call_id" not in row
    review_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in review)
    )

    second = subprocess.run(
        base_command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert second.returncode == 2

    state = json.loads((out / "acquisition-state.json").read_text())
    assert state["state"] == BLOCKED_RUNTIME_PREREQUISITES
    assert state["counts"]["marked_real_rows"] == 59
    assert state["counts"]["unreviewed_ready_rows"] == 0
    assert state["counts"]["final_rows"] == 59
    assert state["counts"]["final_tool_positive_rows"] == 59

    corpus = [
        json.loads(line)
        for line in (out / "corpus" / "reflex-real-turns.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(corpus) == 59
    assert all(row["provenance"] == "real" for row in corpus)
    assert (out / "physical" / "physical-preflight.json").exists()
