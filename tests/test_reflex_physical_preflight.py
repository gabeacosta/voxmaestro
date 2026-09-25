import json
import subprocess
import sys
from pathlib import Path


def _write_corpus(path: Path) -> None:
    rows = []
    for index in range(60):
        rows.append(
            {
                "id": f"turn-{index}",
                "transcript": f"book appointment {index}",
                "expected_intent": "schedule",
                "expected_tool_needed": index < 59,
                "expected_language": "en",
                "provenance": "real",
            }
        )
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_preflight_failure_never_writes_physical_admission_artifact(tmp_path):
    repo_root = Path(__file__).parents[1]
    corpus = tmp_path / "corpus.jsonl"
    model = tmp_path / "model"
    out = tmp_path / "out"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"not-the-pinned-model")
    _write_corpus(corpus)

    result = subprocess.run(
        [
            sys.executable,
            "examples/run_reflex_physical_admission.py",
            "--corpus",
            str(corpus),
            "--model-path",
            str(model),
            "--out",
            str(out),
            "--preflight-only",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    preflight_path = out / "physical-preflight.json"
    assert preflight_path.exists()
    assert not (out / "physical-admission.json").exists()

    report = json.loads(preflight_path.read_text())
    assert report["contract_version"] == "reflex-physical-preflight.v1"
    assert report["verdict"] == "BLOCKED_RUNTIME_PREREQUISITES"
    assert report["authority"] == "EVIDENCE_ONLY_NOT_ROUTING_AUTHORITY"
