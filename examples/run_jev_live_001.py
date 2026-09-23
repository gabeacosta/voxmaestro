#!/usr/bin/env python3
"""Freeze and run VM-JEV-LIVE-001 without placing credentials in the repo."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from voxmaestro.reflex.jev_live import (
    LiveAcceptanceError,
    current_source_commit,
    discover_typesafe_models,
    freeze_specimen,
    run_live_acceptance,
    select_catalog_model,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VM-JEV-LIVE-001 acceptance runner")
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser(
        "freeze",
        help="Freeze the TypeSafe model catalog + selected Jev name before any SystemOne POST.",
    )
    freeze.add_argument(
        "--template",
        type=Path,
        default=_default_repo() / "experiments/VM-JEV-LIVE-001/specimen-template.json",
    )
    freeze.add_argument("--out", type=Path, required=True)
    freeze.add_argument(
        "--model",
        help="Optional account-visible Jev name/alias. If omitted, prefer jev-latest from GET /v1/models.",
    )
    freeze.add_argument("--repo", type=Path, default=_default_repo())

    run = sub.add_parser(
        "run",
        help="Run both frozen provider surfaces and seal the evidence bundle.",
    )
    run.add_argument("--specimen", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--repo", type=Path, default=_default_repo())

    return parser


def _freeze(args: argparse.Namespace) -> int:
    source_commit = current_source_commit(args.repo)
    api_key = os.environ.get("TYPESAFE_API_KEY", "")
    if not api_key:
        raise LiveAcceptanceError("TYPESAFE_API_KEY_required_for_catalog_freeze")
    models = discover_typesafe_models(api_key)
    model = select_catalog_model(models, requested=args.model)
    print("model_discovery=" + json.dumps(models, sort_keys=True))
    specimen = freeze_specimen(
        args.template,
        args.out,
        model=model,
        model_catalog=models,
        source_commit=source_commit,
        frozen_at_utc=_utc_now(),
    )
    print(f"FROZEN_{specimen.experiment_id}")
    print(f"model={specimen.model}")
    print(f"model_catalog_sha256={specimen.model_catalog_sha256}")
    print(f"source_commit={specimen.source_commit}")
    print(f"specimen_sha256={specimen.sha256}")
    print(f"specimen={args.out}")
    print("systemone_post_executed=false")
    return 0


def _run(args: argparse.Namespace) -> int:
    result = run_live_acceptance(
        args.specimen,
        args.out,
        typesafe_api_key=os.environ.get("TYPESAFE_API_KEY", ""),
        ai_gateway_api_key=os.environ.get("AI_GATEWAY_API_KEY", ""),
        repo_dir=args.repo,
    )
    print(f"VM_JEV_LIVE_001_{result['status']}")
    print(f"specimen_sha256={result['specimen_sha256']}")
    print(f"seal_sha256={result['seal_sha256']}")
    print(f"evidence={result['out_dir']}")
    print("authority_promoted=false")
    if result["status"] == "PASS":
        return 0
    if result["status"] == "REVIEW_DISAGREEMENT":
        return 2
    return 1


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.command == "freeze":
            return _freeze(args)
        return _run(args)
    except (LiveAcceptanceError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"BLOCKED_VM_JEV_LIVE_001: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
