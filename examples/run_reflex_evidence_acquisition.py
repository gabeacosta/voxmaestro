"""One-shot local evidence acquisition gate for VM-REFLEX-001.

This command never invents provenance. It can stage local legacy replay records,
report whether the corpus is shape-sufficient, run the physical prerequisite
preflight, and optionally execute the existing physical admission runner.

No transcript content is copied into the top-level acquisition state.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from voxmaestro.reflex.acquisition import (
    PASS_REFLEX_PHYSICAL_ADMISSION,
    READY_FOR_PHYSICAL_ADMISSION,
    classify_acquisition,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = Path("~/.voxmaestro/training").expanduser()
DEFAULT_OUT = Path("~/.voxmaestro/reflex/VM-REFLEX-001").expanduser()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _run(command: list[str]) -> int:
    return subprocess.run(command, cwd=REPO_ROOT, check=False).returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--default-language", choices=("en", "es"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--assert-replays-are-real-calls",
        action="store_true",
        help="Operator provenance assertion required before real corpus emission.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run physical admission after corpus + runtime preflight are ready.",
    )
    args = parser.parse_args()
    if args.execute and not args.assert_replays_are_real_calls:
        parser.error("--execute requires --assert-replays-are-real-calls")
    return args


def main() -> int:
    args = parse_args()
    out = args.out.expanduser()
    corpus_out = out / "corpus"
    physical_out = out / "physical"
    state_path = out / "acquisition-state.json"
    out.mkdir(parents=True, exist_ok=True)

    import_command = [
        sys.executable,
        "examples/import_legacy_reflex_corpus.py",
        "--input-dir",
        str(args.input_dir.expanduser()),
        "--out-dir",
        str(corpus_out),
    ]
    if args.default_language:
        import_command.extend(["--default-language", args.default_language])
    if args.assert_replays_are_real_calls:
        import_command.append("--assert-replays-are-real-calls")

    _run(import_command)
    summary_path = corpus_out / "legacy-reflex-summary.json"
    if not summary_path.exists():
        state = {
            "state": "TEST_INVALID",
            "reason": "CORPUS_SUMMARY_MISSING",
            "paths": {"summary": str(summary_path)},
        }
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        print(json.dumps(state, indent=2, sort_keys=True))
        return 2

    summary = _json(summary_path)
    preflight: dict[str, Any] | None = None
    physical: dict[str, Any] | None = None

    if summary.get("admission_shape_sufficient") is True:
        corpus_path = corpus_out / "reflex-real-turns.jsonl"
        preflight_command = [
            sys.executable,
            "examples/run_reflex_physical_admission.py",
            "--corpus",
            str(corpus_path),
            "--model-path",
            str(args.model_path.expanduser()),
            "--out",
            str(physical_out),
            "--preflight-only",
        ]
        _run(preflight_command)
        preflight_path = physical_out / "physical-preflight.json"
        if preflight_path.exists():
            preflight = _json(preflight_path)

        if (
            args.execute
            and preflight is not None
            and preflight.get("verdict") == READY_FOR_PHYSICAL_ADMISSION
        ):
            physical_command = [
                sys.executable,
                "examples/run_reflex_physical_admission.py",
                "--corpus",
                str(corpus_path),
                "--model-path",
                str(args.model_path.expanduser()),
                "--out",
                str(physical_out),
            ]
            _run(physical_command)
            physical_path = physical_out / "physical-admission.json"
            if physical_path.exists():
                physical = _json(physical_path)

    state = classify_acquisition(
        summary,
        physical_preflight=preflight,
        execute=args.execute,
        physical_admission=physical,
    )
    state["paths"] = {
        "summary": str(summary_path),
        "corpus": str(corpus_out / "reflex-real-turns.jsonl"),
        "preflight": str(physical_out / "physical-preflight.json"),
        "physical_admission": str(physical_out / "physical-admission.json"),
    }
    state["privacy"] = {
        "top_level_transcripts": False,
        "local_output_only": True,
    }
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    print(json.dumps(state, indent=2, sort_keys=True))

    return 0 if state["state"] in {
        READY_FOR_PHYSICAL_ADMISSION,
        PASS_REFLEX_PHYSICAL_ADMISSION,
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
