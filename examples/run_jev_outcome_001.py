#!/usr/bin/env python3
"""Run VM-JEV-OUTCOME-001 as a blind completion-judgment comparison."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from voxmaestro.outcome import OutcomeGate, OutcomeStatus, TaskContract, TaskRequirement
from voxmaestro.outcome_jev import JevOutcomeVerifier, QUESTION_VERSION
from voxmaestro.reflex.jev_backend import (
    TYPESAFE_NATIVE_ENDPOINT,
    JevHttpConfig,
    JevHttpTransport,
    JevReflexBackend,
    make_jsonl_observer,
)
from voxmaestro.reflex.jev_live import (
    current_source_commit,
    discover_typesafe_models,
    select_catalog_model,
    sha256_json,
)

EXPERIMENT_ID = "VM-JEV-OUTCOME-001"
SCHEMA_VERSION = "vm-jev-outcome-001.v1"


def _default_repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec",
        type=Path,
        default=_default_repo() / "experiments" / EXPERIMENT_ID / "cases.json",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--timeout-s", type=float, default=5.0)
    return parser


def _load_spec(path: Path) -> Mapping[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("spec_not_mapping")
    if raw.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("wrong_experiment_id")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("wrong_schema_version")
    if raw.get("question_version") != QUESTION_VERSION:
        raise ValueError("wrong_question_version")

    threshold = raw.get("uncertainty_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("invalid_uncertainty_threshold")
    if not 0.0 <= float(threshold) <= 1.0:
        raise ValueError("invalid_uncertainty_threshold")

    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases_required")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("case_not_mapping")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("invalid_or_duplicate_case_id")
        seen.add(case_id)
        if case.get("agent_claim") not in {"done", "not_done"}:
            raise ValueError(f"invalid_agent_claim:{case_id}")
        if not isinstance(case.get("reference_done"), bool):
            raise ValueError(f"reference_done_required:{case_id}")
    return raw


def _contract(raw: Mapping[str, Any]) -> TaskContract:
    requirements = raw.get("requirements")
    if not isinstance(requirements, list):
        raise ValueError("requirements_required")
    return TaskContract(
        task_id=str(raw["task_id"]),
        objective=str(raw["objective"]),
        requirements=tuple(
            TaskRequirement(
                requirement_id=str(item["requirement_id"]),
                statement=str(item["statement"]),
            )
            for item in requirements
        ),
        constraints=tuple(str(item) for item in raw.get("constraints", [])),
        prohibited_effects=tuple(str(item) for item in raw.get("prohibited_effects", [])),
    )


def _write_json_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, ensure_ascii=False)
        handle.write("\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("observation_not_mapping")
            records.append(value)
    return records


def _seal(out_dir: Path, names: tuple[str, ...]) -> None:
    files = []
    for name in sorted(names):
        payload = (out_dir / name).read_bytes()
        files.append(
            {
                "path": name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    _write_json_new(out_dir / "seal.json", {"algorithm": "sha256", "files": files})


async def _run(args: argparse.Namespace) -> int:
    if args.timeout_s <= 0:
        raise ValueError("timeout_s_must_be_positive")
    api_key = os.environ.get("TYPESAFE_API_KEY", "")
    if not api_key:
        raise RuntimeError("TYPESAFE_API_KEY_required")

    spec = _load_spec(args.spec)
    source_commit = current_source_commit(_default_repo())

    args.out.mkdir(parents=True, exist_ok=False)
    _write_json_new(
        args.out / "input-spec.json",
        {
            "source_commit": source_commit,
            "spec_sha256": sha256_json(spec),
            "spec": spec,
        },
    )

    catalog_pre = discover_typesafe_models(api_key, timeout_s=args.timeout_s)
    selected_model = select_catalog_model(catalog_pre, requested=args.model)
    catalog_pre_value = {
        "model": selected_model,
        "catalog_sha256": sha256_json(list(catalog_pre)),
        "catalog": list(catalog_pre),
    }
    _write_json_new(args.out / "model-catalog-pre.json", catalog_pre_value)

    observation_path = args.out / "jev-observations.jsonl"
    backend = JevReflexBackend(
        JevHttpTransport(
            JevHttpConfig(
                api_key=api_key,
                endpoint=TYPESAFE_NATIVE_ENDPOINT,
                timeout_s=args.timeout_s,
                provider="typesafe",
            )
        ),
        model=selected_model,
        observer=make_jsonl_observer(str(observation_path)),
        allow_model_alias=True,
    )
    gate = OutcomeGate(
        JevOutcomeVerifier(
            backend,
            uncertainty_threshold=float(spec["uncertainty_threshold"]),
        )
    )

    results = []
    for raw_case in spec["cases"]:
        contract_raw = raw_case.get("contract")
        workflow_result = raw_case.get("workflow_result")
        evidence = raw_case.get("evidence")
        if not isinstance(contract_raw, Mapping):
            raise ValueError(f"contract_required:{raw_case['case_id']}")
        if not isinstance(workflow_result, Mapping):
            raise ValueError(f"workflow_result_required:{raw_case['case_id']}")
        if not isinstance(evidence, list) or not all(
            isinstance(item, Mapping) for item in evidence
        ):
            raise ValueError(f"evidence_required:{raw_case['case_id']}")

        # Blind boundary: agent_claim and reference_done are intentionally not
        # passed into the verifier request.
        attestation = await gate.verify(
            _contract(contract_raw),
            workflow_result,
            evidence=tuple(evidence),
        )
        jev_done = attestation.status is OutcomeStatus.SATISFIED
        agent_done = raw_case["agent_claim"] == "done"
        reference_done = bool(raw_case["reference_done"])
        results.append(
            {
                "case_id": raw_case["case_id"],
                "agent_claim": raw_case["agent_claim"],
                "reference_done": reference_done,
                "jev_status": attestation.status.value,
                "jev_claim": "done" if jev_done else "not_done",
                "diverged": jev_done != agent_done,
                "agent_correct": agent_done == reference_done,
                "jev_correct": jev_done == reference_done,
                "findings": [
                    {
                        "requirement_id": finding.requirement_id,
                        "status": finding.status.value,
                        "uncertainty": finding.uncertainty,
                        "summary": finding.summary,
                    }
                    for finding in attestation.findings
                ],
            }
        )

    _write_json_new(args.out / "comparison-results.json", {"cases": results})

    catalog_post = discover_typesafe_models(api_key, timeout_s=args.timeout_s)
    catalog_post_value = {
        "catalog_sha256": sha256_json(list(catalog_post)),
        "catalog": list(catalog_post),
    }
    _write_json_new(args.out / "model-catalog-post.json", catalog_post_value)

    observations = _read_jsonl(observation_path)
    expected_count = len(spec["cases"])
    provider_ok = (
        len(observations) == expected_count
        and all(record.get("ok") is True for record in observations)
    )
    requested_models = {record.get("requested_model") for record in observations}
    resolved_models = {
        record.get("model")
        for record in observations
        if record.get("ok") is True
    }
    model_stable = (
        requested_models == {selected_model}
        and len(resolved_models) == 1
        and None not in resolved_models
    )
    catalog_stable = (
        catalog_pre_value["catalog_sha256"] == catalog_post_value["catalog_sha256"]
    )

    if not catalog_stable:
        terminal_status = "FAIL_MODEL_CATALOG_DRIFT"
    elif not model_stable:
        terminal_status = "FAIL_MODEL_DRIFT"
    elif not provider_ok:
        terminal_status = "FAIL_PROVIDER"
    else:
        terminal_status = "MEASURED"

    metrics_valid = terminal_status == "MEASURED"
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "schema_version": SCHEMA_VERSION,
        "terminal_status": terminal_status,
        "metrics_valid": metrics_valid,
        "source_commit": source_commit,
        "requested_model": selected_model,
        "resolved_models": sorted(str(item) for item in resolved_models),
        "catalog_sha256": catalog_pre_value["catalog_sha256"],
        "case_count": expected_count,
        "agent_jev_disagreements": sum(1 for item in results if item["diverged"]),
        "agent_reference_correct": sum(1 for item in results if item["agent_correct"]),
        "jev_reference_correct": sum(1 for item in results if item["jev_correct"]),
        "uncertainty_threshold": spec["uncertainty_threshold"],
        "promotion_authority": "NONE",
    }
    _write_json_new(args.out / "summary.json", summary)
    _seal(
        args.out,
        (
            "input-spec.json",
            "model-catalog-pre.json",
            "jev-observations.jsonl",
            "comparison-results.json",
            "model-catalog-post.json",
            "summary.json",
        ),
    )

    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0 if terminal_status == "MEASURED" else 2


def main() -> int:
    return asyncio.run(_run(_build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
