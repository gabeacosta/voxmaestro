"""WT-VOICE-INTENT-CAL-001: capture label logprobs, fit temperature, report the gate.

Two steps so fitting never needs the model again:

  capture  corpus.jsonl -> records.jsonl   (talks to the pinned local endpoint)
  fit      records.jsonl -> artifact.json + report.json   (pure, reproducible)

Corpus rows: {"text": str, "intents": [id | {"id","description"}], "gold": id}
Records store a text digest, never the text, so call transcripts stay out of
evidence files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

from voxmaestro.workers.calibration import (
    CalibrationArtifact,
    expected_calibration_error,
    fit_temperature,
    option_probabilities,
)
from voxmaestro.workers.intent_worker import IntentWorker


def _split(text: str, fit_frac: float) -> str:
    bucket = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) / 2**32
    return "fit" if bucket < fit_frac else "eval"


def capture(args: argparse.Namespace) -> int:
    scorer = IntentWorker(endpoint=args.endpoint, model=args.model,
                          timeout_s=args.timeout_s, top_logprobs=args.top_logprobs)
    verbal = IntentWorker(endpoint=args.endpoint, model=args.model,
                          timeout_s=args.timeout_s, min_confidence=0.0)
    out = Path(args.records).open("w")
    rows = [json.loads(line) for line in Path(args.corpus).read_text().splitlines() if line]
    for i, row in enumerate(rows):
        ids = [x["id"] if isinstance(x, dict) else x for x in row["intents"]]
        if row.get("gold") not in ids:
            print(f"row {i}: gold not in intents; refusing corpus", file=sys.stderr)
            return 2
        started = time.monotonic()
        raw = scorer.score_raw(row)
        latency_ms = (time.monotonic() - started) * 1000
        verbalized = verbal.classify(row)
        out.write(json.dumps({
            "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
            "split": _split(row["text"], args.fit_frac),
            "gold": row["gold"],
            "logprobs": raw,
            "latency_ms": round(latency_ms, 2),
            "verbalized": verbalized,
        }) + "\n")
    out.close()
    print(f"captured {len(rows)} records -> {args.records}")
    return 0


def _evaluate(rows: list[dict], temperature: float, min_label_mass: float,
              threshold: float) -> dict:
    confs, oks, acted_wrong, prevented, lost = [], [], 0, 0, 0
    for r in rows:
        probs = option_probabilities(r["logprobs"], temperature=temperature,
                                     min_label_mass=min_label_mass) if r["logprobs"] else None
        if not probs:
            continue
        pick = max(probs, key=probs.get)
        ok, conf = pick == r["gold"], probs[pick]
        confs.append(conf)
        oks.append(ok)
        abstain = pick == "unknown" or conf < threshold
        acted_wrong += (not ok) and not abstain
        prevented += (not ok) and abstain
        lost += ok and abstain
    n = len(confs)
    acted = n - prevented - lost
    return {
        "n": n,
        "accuracy": sum(oks) / n if n else None,
        "ece": expected_calibration_error(confs, oks) if n else None,
        "coverage": acted / n if n else None,
        "selective_accuracy": (acted - acted_wrong) / acted if acted else None,
        "errors_acted_on": acted_wrong,
        "errors_prevented_by_abstention": prevented,
        "correct_answers_abstained": lost,
    }


def _verbalized_ece(rows: list[dict]) -> float | None:
    pairs = [(r["verbalized"]["confidence"], r["verbalized"]["intent"] == r["gold"])
             for r in rows if r.get("verbalized", {}).get("intent", "unknown") != "unknown"]
    if not pairs:
        return None
    return expected_calibration_error([c for c, _ in pairs], [ok for _, ok in pairs])


def fit(args: argparse.Namespace) -> int:
    rows = [json.loads(line) for line in Path(args.records).read_text().splitlines() if line]
    scored = [r for r in rows if r["logprobs"]]
    fit_rows = [r for r in scored if r["split"] == "fit"]
    eval_rows = [r for r in scored if r["split"] == "eval"]
    gold_unranked = sum(r["gold"] not in r["logprobs"] for r in scored)
    latencies = sorted(r["latency_ms"] for r in rows)

    temperature = fit_temperature([(r["logprobs"], r["gold"]) for r in fit_rows])
    before = _evaluate(eval_rows, 1.0, args.min_label_mass, args.threshold)
    after = _evaluate(eval_rows, temperature, args.min_label_mass, args.threshold)
    scoreable_rate = len(scored) / len(rows) if rows else 0.0

    gate = {"min_eval_n": args.min_eval_n, "min_scoreable_rate": args.min_scoreable_rate,
            "max_ece": args.max_ece, "min_coverage": args.min_coverage,
            "threshold": args.threshold, "hot_path_p95_budget_ms": args.p95_budget_ms}
    reasons = []
    if after["n"] < args.min_eval_n or scoreable_rate < args.min_scoreable_rate:
        verdict = "TEST_INVALID"
        reasons.append("too few scoreable eval rows; endpoint may not return logprobs")
    else:
        if after["ece"] > args.max_ece:
            reasons.append("calibrated ECE above gate")
        if after["coverage"] < args.min_coverage:
            reasons.append("abstains too often for a phone call")
        if not after["selective_accuracy"] or after["selective_accuracy"] < after["accuracy"]:
            reasons.append("abstention does not improve accuracy on acted turns")
        verdict = "FAIL" if reasons else "PASS"
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else None
    hot_path = ("WITHIN_BUDGET" if p95 is not None and p95 <= args.p95_budget_ms
                else "COLD_PATH_ONLY")

    artifact = CalibrationArtifact(
        model=args.model, temperature=round(temperature, 4),
        min_label_mass=args.min_label_mass, fitted_n=len(fit_rows), eval_n=after["n"],
        metrics={"ece_before": before["ece"], "ece_after": after["ece"],
                 "accuracy": after["accuracy"], "verdict": verdict})
    report = {
        "wt": "WT-VOICE-INTENT-CAL-001", "model": args.model, "verdict": verdict,
        "reasons": reasons, "gate": gate, "temperature": temperature,
        "records": len(rows), "scoreable_rate": scoreable_rate,
        "gold_unranked_in_top_k": gold_unranked,
        "eval_uncalibrated": before, "eval_calibrated": after,
        "verbalized_confidence_ece": _verbalized_ece(eval_rows),
        "latency_ms": {"p50": statistics.median(latencies) if latencies else None,
                       "p95": p95, "placement": hot_path},
        "artifact_digest": artifact.digest,
    }
    if verdict == "PASS":
        Path(args.artifact).write_text(artifact.to_json() + "\n")
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("verdict", "reasons", "temperature")}, indent=2))
    return 0 if verdict == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--endpoint", default="http://127.0.0.1:8081/v1")
    cap.add_argument("--model", required=True)
    cap.add_argument("--corpus", required=True)
    cap.add_argument("--records", required=True)
    cap.add_argument("--fit-frac", type=float, default=0.5)
    cap.add_argument("--timeout-s", type=float, default=2.0)
    cap.add_argument("--top-logprobs", type=int, default=10)
    fi = sub.add_parser("fit")
    fi.add_argument("--model", required=True)
    fi.add_argument("--records", required=True)
    fi.add_argument("--artifact", required=True)
    fi.add_argument("--report", required=True)
    fi.add_argument("--min-label-mass", type=float, default=0.5)
    fi.add_argument("--threshold", type=float, default=0.7)
    # Gate defaults are proposals; every run records the values it was judged against.
    fi.add_argument("--min-eval-n", type=int, default=100)
    fi.add_argument("--min-scoreable-rate", type=float, default=0.95)
    fi.add_argument("--max-ece", type=float, default=0.08)
    fi.add_argument("--min-coverage", type=float, default=0.8)
    fi.add_argument("--p95-budget-ms", type=float, default=200.0)
    args = parser.parse_args()
    return capture(args) if args.cmd == "capture" else fit(args)


if __name__ == "__main__":
    sys.exit(main())
