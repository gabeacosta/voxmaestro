"""WT-VOICE-INTENT-CAL-001 harness: capture keeps no text; fit is pure and gated.

Synthetic records exercise the harness only. A PASS here is not model admission.
"""
import json
import math
import random
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "fit_intent_calibration.py"
INTENTS = ["availability_question", "human_request", "price_question", "unknown"]


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def synthetic_records(n, overconfidence, seed, scoreable=1.0):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        logits = {k: rng.gauss(0, 2) for k in INTENTS}
        norm = math.log(sum(math.exp(v) for v in logits.values()))
        probs = {k: math.exp(v - norm) for k, v in logits.items()}
        gold = rng.choices(list(probs), weights=list(probs.values()))[0]
        # The "model" reports logits sharpened by `overconfidence`, then top-k logprobs.
        sharp = {k: v * overconfidence for k, v in logits.items()}
        snorm = math.log(sum(math.exp(v) for v in sharp.values()))
        lp = {k: v - snorm for k, v in sharp.items()} if rng.random() < scoreable else None
        rows.append({"text_sha256": f"{i:064x}", "split": "fit" if i % 2 else "eval",
                     "gold": gold, "logprobs": lp, "latency_ms": 90.0 + i % 40,
                     "verbalized": {"intent": max(probs, key=probs.get), "confidence": 0.95}})
    return rows


def write(tmp_path, rows):
    path = tmp_path / "records.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def fit(tmp_path, records, *extra):
    return run("fit", "--model", "m", "--records", str(records),
               "--artifact", str(tmp_path / "artifact.json"),
               "--report", str(tmp_path / "report.json"), *extra)


def test_overconfident_model_gets_temperature_above_one_and_report_compares_ece(tmp_path):
    result = fit(tmp_path, write(tmp_path, synthetic_records(1200, 2.5, seed=4)),
                 "--max-ece", "0.2", "--min-coverage", "0.3")
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["temperature"] > 1.5
    assert report["eval_calibrated"]["ece"] < report["eval_uncalibrated"]["ece"]
    assert report["gate"]["threshold"] == 0.7  # judged-against values are recorded
    assert report["latency_ms"]["placement"] == "WITHIN_BUDGET"
    assert result.returncode == (0 if report["verdict"] == "PASS" else 1)
    assert (tmp_path / "artifact.json").exists() == (report["verdict"] == "PASS")


def test_endpoint_without_logprobs_is_test_invalid_and_writes_no_artifact(tmp_path):
    rows = synthetic_records(400, 1.0, seed=5, scoreable=0.5)
    result = fit(tmp_path, write(tmp_path, rows))
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["verdict"] == "TEST_INVALID" and result.returncode == 1
    assert not (tmp_path / "artifact.json").exists()


def test_slow_scoring_is_placed_cold_path_only(tmp_path):
    rows = synthetic_records(400, 1.0, seed=6)
    for r in rows:
        r["latency_ms"] = 450.0
    fit(tmp_path, write(tmp_path, rows))
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["latency_ms"]["placement"] == "COLD_PATH_ONLY"


def test_capture_refuses_corpus_with_illegal_gold(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({"text": "hi", "intents": ["a_b"], "gold": "zzz"}) + "\n")
    result = run("capture", "--model", "m", "--corpus", str(corpus),
                 "--records", str(tmp_path / "r.jsonl"))
    assert result.returncode == 2


@pytest.mark.parametrize("text", ["my number is 702 555 0100, call me tomorrow"])
def test_capture_records_digest_not_transcript(tmp_path, text):
    # Endpoint is unreachable: every row is unscoreable, but the record shape still holds.
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({"text": text, "intents": INTENTS,
                                  "gold": "availability_question"}) + "\n")
    records = tmp_path / "r.jsonl"
    result = run("capture", "--model", "m", "--corpus", str(corpus), "--records", str(records),
                 "--endpoint", "http://127.0.0.1:9/v1", "--timeout-s", "0.5")
    assert result.returncode == 0
    saved = records.read_text()
    assert "702" not in saved and "tomorrow" not in saved
    assert json.loads(saved)["logprobs"] is None
