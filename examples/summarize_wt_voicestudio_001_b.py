"""Adjudicate paired real-audio receipts without inferring production readiness."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def compare(vs: dict[str, Any], pocket: dict[str, Any]) -> dict[str, Any]:
    invalid: dict[str, Any] = {
        "schema": "wt-voicestudio-001-b.comparison.v1",
        "qualification": "TEST_INVALID",
        "reason": "missing or incomparable hardware evidence",
    }
    if vs.get("schema") != "wt-voicestudio-001-b.hardware.v1":
        return invalid
    if pocket.get("schema") != vs["schema"]:
        return invalid
    if vs.get("backend") != "voicestudio" or pocket.get("backend") != "pocket":
        return invalid
    if any(x.get("qualification") != "RECORDED_NOT_PROMOTABLE" for x in (vs, pocket)):
        return invalid
    if any(not vs.get(k) or vs.get(k) != pocket.get(k)
           for k in ("hardware", "language", "corpus_sha256")):
        return invalid
    a = [row.get("utterance_id") for row in vs.get("rows", [])]
    b = [row.get("utterance_id") for row in pocket.get("rows", [])]
    if a != b or len(a) < 3 or any(not name for name in a):
        return invalid
    for key in ("complete_p95_ms", "rtf_p95"):
        for item in (vs, pocket):
            value = item.get(key)
            if (
                not isinstance(value, (float, int))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                return invalid
    return {
        "schema": invalid["schema"],
        "qualification": "OBSERVED_NOT_PROMOTABLE",
        "reason": "complete-WAV only; no objective acoustic quality, first audio or cancellation",
        "hardware": vs["hardware"],
        "language": vs["language"],
        "corpus_sha256": vs["corpus_sha256"],
        "utterance_ids": a,
        "voicestudio_complete_p95_ms": vs["complete_p95_ms"],
        "pocket_complete_p95_ms": pocket["complete_p95_ms"],
        "voicestudio_rtf_p95": vs["rtf_p95"],
        "pocket_rtf_p95": pocket["rtf_p95"],
        "complete_p95_delta_ms": round(vs["complete_p95_ms"] - pocket["complete_p95_ms"], 3),
        "complete_p95_ratio": round(vs["complete_p95_ms"] / pocket["complete_p95_ms"], 5),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--vs", type=Path, required=True)
    p.add_argument("--pocket", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    try:
        report = compare(json.loads(args.vs.read_text()), json.loads(args.pocket.read_text()))
    except (OSError, ValueError, KeyError) as exc:
        report = {
            "schema": "wt-voicestudio-001-b.comparison.v1",
            "qualification": "TEST_INVALID",
            "reason": type(exc).__name__,
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"qualification": report["qualification"], "receipt": str(args.out)}))
    return 0 if report["qualification"] == "OBSERVED_NOT_PROMOTABLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
