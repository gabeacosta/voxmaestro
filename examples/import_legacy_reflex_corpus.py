"""Import local legacy VoxMaestro training data into reflex corpus staging.

This tool never reads GitHub or uploads transcripts. It operates only on local
JSONL under ~/.voxmaestro/training (or an explicitly supplied directory).

Automatic admission output is deliberately narrow:
- bland_replay only
- legacy confidence approximately 1.0
- deterministic intent mapping only
- language present in the row or explicitly asserted with --default-language
- operator explicitly asserts the replay source contains real calls

bland_live and ambiguous legacy intents remain staging-only for review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from voxmaestro.reflex.admission import (
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--default-language", choices=("en", "es"))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--assert-replays-are-real-calls",
        action="store_true",
        help=(
            "Required before final admission rows are emitted. This is an operator "
            "assertion about the provenance of local bland_replay records."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir.expanduser()
    staging_path = out_dir / "legacy-reflex-staging.jsonl"
    corpus_path = out_dir / "reflex-real-turns.jsonl"
    summary_path = out_dir / "legacy-reflex-summary.json"

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
    final, summary = finalize_legacy_replay_rows(
        staged,
        assert_replays_are_real_calls=args.assert_replays_are_real_calls,
    )
    summary.update(
        {
            "verdict": (
                "READY_FOR_VM_REFLEX_001"
                if summary["admission_shape_sufficient"]
                else "BLOCKED_INSUFFICIENT_REAL_LABELS"
            ),
            "input_dir": str(args.input_dir.expanduser()),
            "source_rows": len(source_rows),
            "parse_errors": parse_errors,
            "staging_path": str(staging_path),
            "corpus_path": str(corpus_path),
            "privacy": {
                "output_scope": "local-home-directory-by-default",
                "call_ids_persisted": False,
                "email_phone_redaction": True,
            },
        }
    )

    _write_jsonl(staging_path, staged)
    _write_jsonl(corpus_path, final)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["admission_shape_sufficient"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
