"""Import local legacy VoxMaestro training data into reflex corpus staging.

This tool never reads GitHub or uploads transcripts. It operates only on local
JSONL under ~/.voxmaestro/training (or an explicitly supplied directory).

Automatic admission output is deliberately narrow:
- bland_replay only
- legacy confidence approximately 1.0
- deterministic intent mapping only
- language present in the row or explicitly asserted with --default-language
- real-call provenance is either an all-real operator assertion or a selective
  local provenance manifest keyed by opaque source_digest

bland_live, demo rows, unreviewed rows, and ambiguous legacy intents remain
staging-only for admission.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from voxmaestro.reflex.admission import (
    build_legacy_provenance_review,
    finalize_legacy_replay_rows,
    stage_legacy_training_row,
)

DEFAULT_INPUT = Path("~/.voxmaestro/training").expanduser()
DEFAULT_OUT = Path("~/.voxmaestro/reflex").expanduser()


def _load_rows(directory: Path) -> tuple[list[Any], list[str]]:
    rows: list[Any] = []
    errors: list[str] = []
    for path in sorted(directory.glob("examples_*.jsonl")):
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                errors.append(f"{path.name}:{line_number}:invalid_json")
    return rows, errors


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    )


def _load_provenance_review(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    classifications: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"{path.name}:{line_number}:invalid_json"
            ) from error
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number}:row_not_object")
        digest = value.get("source_digest")
        classification = value.get("classification")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(f"{path.name}:{line_number}:invalid_source_digest")
        if classification not in {"real", "demo", "unreviewed"}:
            raise ValueError(f"{path.name}:{line_number}:invalid_classification")
        if digest in classifications:
            raise ValueError(f"{path.name}:{line_number}:duplicate_source_digest")
        classifications[digest] = classification
    return classifications


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--default-language", choices=("en", "es"))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--provenance-review",
        type=Path,
        help=(
            "Local JSONL provenance review. Defaults to "
            "<out-dir>/legacy-reflex-provenance-review.jsonl."
        ),
    )
    parser.add_argument(
        "--assert-replays-are-real-calls",
        action="store_true",
        help=(
            "Required before final admission rows are emitted. This is an operator "
            "assertion about the provenance of local bland_replay records."
        ),
    )
    args = parser.parse_args()
    if args.assert_replays_are_real_calls and args.provenance_review is not None:
        parser.error(
            "--assert-replays-are-real-calls cannot be combined with --provenance-review"
        )
    return args


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir.expanduser()
    staging_path = out_dir / "legacy-reflex-staging.jsonl"
    corpus_path = out_dir / "reflex-real-turns.jsonl"
    summary_path = out_dir / "legacy-reflex-summary.json"
    provenance_path = (
        args.provenance_review.expanduser()
        if args.provenance_review is not None
        else out_dir / "legacy-reflex-provenance-review.jsonl"
    )

    try:
        source_rows, parse_errors = _load_rows(args.input_dir.expanduser())
    except OSError as error:
        summary = {
            "verdict": "TEST_INVALID",
            "error": f"{type(error).__name__}: {error}",
            "input_dir": str(args.input_dir.expanduser()),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return 2

    staged = [
        stage_legacy_training_row(row, default_language=args.default_language)
        for row in source_rows
    ]

    try:
        prior_classifications = _load_provenance_review(provenance_path)
    except ValueError as error:
        summary = {
            "verdict": "TEST_INVALID",
            "error": str(error),
            "input_dir": str(args.input_dir.expanduser()),
            "provenance_review_path": str(provenance_path),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 2

    try:
        review_rows = build_legacy_provenance_review(
            staged,
            prior_classifications=prior_classifications,
        )
        classifications = {
            row["source_digest"]: row["classification"]
            for row in review_rows
        }
        final, summary = finalize_legacy_replay_rows(
            staged,
            assert_replays_are_real_calls=args.assert_replays_are_real_calls,
            provenance_by_digest=(
                None if args.assert_replays_are_real_calls else classifications
            ),
        )
    except ValueError as error:
        summary = {
            "verdict": "TEST_INVALID",
            "error": str(error),
            "input_dir": str(args.input_dir.expanduser()),
            "provenance_review_path": str(provenance_path),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 2
    summary.update(
        {
            "verdict": (
                "READY_FOR_VM_REFLEX_001"
                if summary["admission_shape_sufficient"]
                else (
                    "BLOCKED_PROVENANCE_CLASSIFICATION"
                    if (
                        summary["unique_ready_replay_rows"] >= 30
                        and summary["unique_ready_tool_positive_rows"] >= 59
                        and summary["unreviewed_ready_rows"] > 0
                    )
                    else "BLOCKED_INSUFFICIENT_REAL_LABELS"
                )
            ),
            "input_dir": str(args.input_dir.expanduser()),
            "source_rows": len(source_rows),
            "parse_errors": parse_errors,
            "staging_path": str(staging_path),
            "corpus_path": str(corpus_path),
            "provenance_review_path": str(provenance_path),
            "privacy": {
                "output_scope": "local-home-directory-by-default",
                "call_ids_persisted": False,
                "email_phone_redaction": True,
            },
        }
    )

    _write_jsonl(staging_path, staged)
    _write_jsonl(provenance_path, review_rows)
    _write_jsonl(corpus_path, final)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["admission_shape_sufficient"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
