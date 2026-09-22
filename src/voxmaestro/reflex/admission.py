"""Reflex model-admission evaluator.

This module evaluates a local reflex model against labelled real turns and emits
machine-readable evidence. Passing this gate does not grant routing authority.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import sys
from pathlib import Path
from typing import Any, Iterable

from .backends import LocalSchemaBackend, SCHEMA_ENGINE_MLX_VLM_LLGUIDANCE
from .gate import ReflexGate


_ALLOWED_INTENTS = {
    "schedule",
    "faq",
    "pricing",
    "complaint",
    "disclosure-trigger",
    "off-script",
}
_ALLOWED_LANGUAGES = {"en", "es"}
_REQUIRED_KEYS = {
    "id",
    "transcript",
    "expected_intent",
    "expected_tool_needed",
    "expected_language",
    "provenance",
}


def _validate_row(value: Any, line_number: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"line {line_number}: row must be an object")
    extra = set(value) - (_REQUIRED_KEYS | {"notes"})
    missing = _REQUIRED_KEYS - set(value)
    if extra or missing:
        raise ValueError(
            f"line {line_number}: corpus keys invalid; missing={sorted(missing)} "
            f"extra={sorted(extra)}"
        )
    if not isinstance(value["id"], str) or not value["id"].strip():
        raise ValueError(f"line {line_number}: id is required")
    text = value["transcript"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"line {line_number}: transcript is required")
    if len(text.encode("utf-8")) > 3072:
        raise ValueError(f"line {line_number}: transcript exceeds 3072 bytes")
    if value["expected_intent"] not in _ALLOWED_INTENTS:
        raise ValueError(f"line {line_number}: invalid expected_intent")
    if not isinstance(value["expected_tool_needed"], bool):
        raise ValueError(f"line {line_number}: expected_tool_needed must be boolean")
    if value["expected_language"] not in _ALLOWED_LANGUAGES:
        raise ValueError(f"line {line_number}: invalid expected_language")
    if value["provenance"] not in {"real", "synthetic"}:
        raise ValueError(f"line {line_number}: provenance must be real or synthetic")
    if "notes" in value and not isinstance(value["notes"], str):
        raise ValueError(f"line {line_number}: notes must be a string")
    return dict(value)


def load_corpus(path: Path) -> tuple[list[dict[str, Any]], str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {number}: invalid JSON") from exc
        row = _validate_row(value, number)
        if row["id"] in ids:
            raise ValueError(f"line {number}: duplicate id {row['id']!r}")
        ids.add(row["id"])
        rows.append(row)
    if not rows:
        raise ValueError("corpus is empty")
    return rows, digest


def hash_model_path(path: Path) -> str:
    if not path.exists():
        raise ValueError(f"model path does not exist: {path}")
    digest = hashlib.sha256()
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise ValueError(f"model path contains no files: {path}")
    root = path.parent if path.is_file() else path
    for file_path in files:
        relative = file_path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        size = file_path.stat().st_size
        digest.update(size.to_bytes(8, "big"))
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _nearest_rank(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(percentile * len(ordered)))
    return float(ordered[rank - 1])


async def evaluate_rows(
    rows: list[dict[str, Any]],
    gate: ReflexGate,
    *,
    tool_threshold: float = 0.5,
    load_profile: str = "idle",
    corpus_sha256: str = "in-memory",
    min_real_turns: int = 30,
    min_tool_positive: int = 59,
    max_p95_ms: float = 150.0,
    require_zero_tool_false_negatives: bool = True,
) -> dict[str, Any]:
    if not 0.0 <= tool_threshold <= 1.0:
        raise ValueError("tool_threshold must be in [0, 1]")
    if load_profile not in {"idle", "representative"}:
        raise ValueError("load_profile must be idle or representative")

    real_rows = [row for row in rows if row["provenance"] == "real"]
    synthetic_rows = len(rows) - len(real_rows)
    observations: list[dict[str, Any]] = []

    for row in real_rows:
        decision = await gate.classify(row["transcript"])
        predicted_tool = (
            decision.usable
            and decision.tool_needed_probability is not None
            and decision.tool_needed_probability >= tool_threshold
        )
        observations.append(
            {
                "id": row["id"],
                "input_digest": decision.input_digest,
                "status": decision.status,
                "latency_ms": decision.latency_ms,
                "expected_intent": row["expected_intent"],
                "predicted_intent": decision.intent.value if decision.intent else None,
                "expected_tool_needed": row["expected_tool_needed"],
                "predicted_tool_needed": bool(predicted_tool) if decision.usable else None,
                "tool_needed_probability": decision.tool_needed_probability,
                "expected_language": row["expected_language"],
                "predicted_language": decision.language.value if decision.language else None,
                "fallback_reason": decision.fallback_reason,
            }
        )

    usable = [item for item in observations if item["status"] == "ok"]
    positives = [item for item in observations if item["expected_tool_needed"]]
    false_negatives = [
        item
        for item in positives
        if item["status"] == "ok" and item["predicted_tool_needed"] is False
    ]
    fallback_count = len(observations) - len(usable)
    latencies = [float(item["latency_ms"]) for item in observations]
    p50 = _nearest_rank(latencies, 0.50)
    p95 = _nearest_rank(latencies, 0.95)

    intent_correct = sum(
        item["predicted_intent"] == item["expected_intent"] for item in usable
    )
    language_correct = sum(
        item["predicted_language"] == item["expected_language"] for item in usable
    )
    observed_fnr = len(false_negatives) / len(positives) if positives else None

    checks = {
        "real_turns_at_least_minimum": len(real_rows) >= min_real_turns,
        "representative_load_profile": load_profile == "representative",
        "no_backend_fallbacks": fallback_count == 0,
        "p95_within_budget": p95 is not None and p95 <= max_p95_ms,
        "tool_positive_evidence_sufficient": len(positives) >= min_tool_positive,
        "tool_false_negative_requirement": (
            len(false_negatives) == 0
            if require_zero_tool_false_negatives
            else observed_fnr is not None and observed_fnr <= 0.05
        ),
    }
    passed = all(checks.values())

    return {
        "contract_version": "reflex-admission.v1",
        "verdict": "PASS_REFLEX_MODEL_ADMISSION" if passed else "BLOCKED",
        "authority": "EVIDENCE_ONLY_NOT_ROUTING_AUTHORITY",
        "corpus": {
            "sha256": corpus_sha256,
            "real_rows_evaluated": len(real_rows),
            "synthetic_rows_ignored": synthetic_rows,
        },
        "model": {
            "backend_id": gate.backend.backend_id,
            "model_id": getattr(gate.backend, "model_id", None),
            "model_hash": getattr(gate.backend, "model_hash", None),
        },
        "environment": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "load_profile": load_profile,
        },
        "policy": {
            "tool_threshold": tool_threshold,
            "min_real_turns": min_real_turns,
            "min_tool_positive": min_tool_positive,
            "max_p95_ms": max_p95_ms,
            "require_zero_tool_false_negatives": require_zero_tool_false_negatives,
        },
        "metrics": {
            "usable": len(usable),
            "fallbacks": fallback_count,
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
            "tool_positive_count": len(positives),
            "tool_false_negative_count": len(false_negatives),
            "observed_tool_false_negative_rate": observed_fnr,
            "intent_accuracy": intent_correct / len(usable) if usable else None,
            "language_accuracy": language_correct / len(usable) if usable else None,
        },
        "checks": checks,
        "observations": observations,
    }



def adjudicate_physical(
    admission_report: dict[str, Any],
    voice_evidence: dict[str, Any],
    *,
    voice_alive_through_benchmark: bool,
    admission_process_ok: bool = True,
    voice_process_ok: bool = True,
) -> dict[str, Any]:
    """Deterministically combine model admission with a physical voice-load witness."""

    qualification = voice_evidence.get("qualification")
    lane = voice_evidence.get("lane")
    witness_complete = (
        isinstance(qualification, dict)
        and qualification.get("verdict") == "PASS"
        and isinstance(lane, dict)
        and lane.get("evidence_complete") is True
        and voice_evidence.get("acoustic_crosstalk_measured") is True
        and voice_alive_through_benchmark
        and admission_process_ok
        and voice_process_ok
    )
    checks = {
        "voice_witness_alive_through_benchmark": voice_alive_through_benchmark,
        "voice_witness_process_ok": voice_process_ok,
        "model_admission_process_ok": admission_process_ok,
        "voice_witness_qualification_pass": (
            isinstance(qualification, dict)
            and qualification.get("verdict") == "PASS"
        ),
        "voice_witness_evidence_complete": (
            isinstance(lane, dict) and lane.get("evidence_complete") is True
        ),
        "voice_witness_acoustic_asr_measured": (
            voice_evidence.get("acoustic_crosstalk_measured") is True
        ),
        "reflex_model_admission_pass": (
            admission_report.get("verdict") == "PASS_REFLEX_MODEL_ADMISSION"
        ),
    }
    if not witness_complete:
        verdict = "TEST_INVALID"
    elif not checks["reflex_model_admission_pass"]:
        verdict = "BLOCKED"
    else:
        verdict = "PASS_REFLEX_PHYSICAL_ADMISSION"
    return {
        "contract_version": "reflex-physical-admission.v1",
        "verdict": verdict,
        "authority": "EVIDENCE_ONLY_NOT_ROUTING_AUTHORITY",
        "checks": checks,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VoxMaestro reflex model admission")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8081/v1")
    parser.add_argument("--model-id", required=True)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--model-path", type=Path)
    identity.add_argument("--model-hash")
    parser.add_argument(
        "--schema-engine",
        choices=(SCHEMA_ENGINE_MLX_VLM_LLGUIDANCE,),
        required=True,
    )
    parser.add_argument("--timeout-ms", type=float, default=150.0)
    parser.add_argument("--tool-threshold", type=float, default=0.5)
    parser.add_argument(
        "--load-profile",
        choices=("idle", "representative"),
        default="idle",
        help="Admission requires representative concurrent ASR/TTS load.",
    )
    parser.add_argument("--out", type=Path, required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    rows, corpus_digest = load_corpus(args.corpus)
    model_hash = (
        hash_model_path(args.model_path)
        if args.model_path is not None
        else str(args.model_hash)
    )
    backend = LocalSchemaBackend(
        endpoint=args.endpoint,
        model_id=args.model_id,
        model_hash=model_hash,
        timeout_ms=min(float(args.timeout_ms), 150.0),
        schema_engine=args.schema_engine,
    )
    try:
        await backend.verify_schema_enforcement()
    except Exception as error:
        report = {
            "contract_version": "reflex-admission.v1",
            "verdict": "BLOCKED",
            "authority": "EVIDENCE_ONLY_NOT_ROUTING_AUTHORITY",
            "checks": {"schema_enforcement_verified": False},
            "error": type(error).__name__,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        return 2

    gate = ReflexGate(backend, timeout_ms=min(float(args.timeout_ms), 150.0))
    report = await evaluate_rows(
        rows,
        gate,
        tool_threshold=args.tool_threshold,
        load_profile=args.load_profile,
        corpus_sha256=corpus_digest,
    )
    report["checks"]["schema_enforcement_verified"] = True
    report["model"]["schema_engine"] = args.schema_engine
    if args.model_path is None:
        report["checks"]["model_identity_computed_from_artifact"] = False
        report["verdict"] = "BLOCKED"
    else:
        report["checks"]["model_identity_computed_from_artifact"] = True
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("verdict", "metrics", "checks")}, indent=2))
    return 0 if report["verdict"] == "PASS_REFLEX_MODEL_ADMISSION" else 2


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
