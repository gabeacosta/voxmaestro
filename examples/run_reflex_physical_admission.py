"""One-shot Mac Mini physical admission for the reflex challenger.

This runner does not install packages or download model weights. It starts the
already-installed local mlx-vlm server against an already-downloaded model
artifact, starts the repo's real Pocket TTS + faster-whisper acoustic workload,
runs reflex model admission while that workload remains active, then writes a
top-level evidence manifest. Missing or incomplete physical evidence is
TEST_INVALID, never PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from voxmaestro.reflex.admission import (
    adjudicate_physical,
    load_corpus,
)

CANDIDATE_MODEL_ID = "mlx-community/Qwen3-0.6B-4bit"
CANDIDATE_WEIGHT_SHA256 = "392e8d466d56100ada00eb82031fb854297fc9e389b7d303eba3af114e87bce2"
DEFAULT_OUT = Path("evidence/reflex-admission/VM-REFLEX-001")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path) if path.is_file() else ""


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _preflight(corpus: Path, model_path: Path) -> dict[str, Any]:
    missing = [
        name
        for name in ("mlx_vlm", "pocket_tts", "faster_whisper")
        if importlib.util.find_spec(name) is None
    ]
    rows, corpus_sha = load_corpus(corpus)
    real = [row for row in rows if row["provenance"] == "real"]
    positives = [row for row in real if row["expected_tool_needed"]]
    weight = model_path / "model.safetensors"
    checks = {
        "dependencies_present": not missing,
        "model_directory_present": model_path.is_dir(),
        "candidate_weight_present": weight.is_file(),
        "candidate_weight_sha256_match": (
            weight.is_file()
            and _file_sha256(weight) == CANDIDATE_WEIGHT_SHA256
        ),
        "real_turns_at_least_30": len(real) >= 30,
        "tool_positive_turns_at_least_59": len(positives) >= 59,
    }
    return {
        "checks": checks,
        "missing_dependencies": missing,
        "corpus_sha256": corpus_sha,
        "real_turns": len(real),
        "tool_positive_turns": len(positives),
        "candidate_weight_sha256": _file_sha256(weight) if weight.is_file() else None,
    }


def _require_port_free(host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            raise RuntimeError(f"{host}:{port} is already in use")
    except ConnectionRefusedError:
        return
    except OSError:
        return


def _wait_tcp(host: str, port: int, process: subprocess.Popen, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"model server exited early with {process.returncode}")
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("model server did not become reachable")


def _wait_file(path: Path, process: subprocess.Popen, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            raise RuntimeError(f"voice witness exited early with {process.returncode}")
        time.sleep(0.1)
    raise TimeoutError("voice witness did not become ready")


def _terminate(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--voice-runs", type=int, default=10)
    args = parser.parse_args()
    if args.voice_runs < 3:
        parser.error("--voice-runs must be >= 3")
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be in [1024, 65535]")
    return args


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    final_path = args.out / "physical-admission.json"
    try:
        preflight = _preflight(args.corpus, args.model_path)
        _require_port_free("127.0.0.1", args.port)
    except Exception as error:
        report = {
            "contract_version": "reflex-physical-admission.v1",
            "verdict": "TEST_INVALID",
            "authority": "EVIDENCE_ONLY_NOT_ROUTING_AUTHORITY",
            "stage": "preflight",
            "error": f"{type(error).__name__}: {error}",
        }
        _write(final_path, report)
        print(json.dumps({"evidence": str(final_path), "verdict": report["verdict"]}))
        return 2

    if not all(preflight["checks"].values()):
        report = {
            "contract_version": "reflex-physical-admission.v1",
            "verdict": "TEST_INVALID",
            "authority": "EVIDENCE_ONLY_NOT_ROUTING_AUTHORITY",
            "stage": "preflight",
            "preflight": preflight,
        }
        _write(final_path, report)
        print(json.dumps({"evidence": str(final_path), "verdict": report["verdict"]}))
        return 2

    server_log_path = args.out / "mlx-vlm-server.log"
    voice_log_path = args.out / "voice-load.log"
    ready_path = args.out / "voice-load-ready.json"
    admission_path = args.out / "model-admission.json"
    voice_dir = args.out / "voice-witness"
    voice_evidence_path = voice_dir / "pocket-int8-en-s2.json"
    for stale in (ready_path, admission_path, voice_evidence_path):
        stale.unlink(missing_ok=True)

    server: subprocess.Popen | None = None
    voice: subprocess.Popen | None = None
    started = time.time()
    try:
        with server_log_path.open("w") as server_log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "mlx_vlm.server",
                    "--model",
                    str(args.model_path.resolve()),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.port),
                ],
                stdout=server_log,
                stderr=subprocess.STDOUT,
                cwd=Path.cwd(),
            )
            _wait_tcp("127.0.0.1", args.port, server, 180)

            with voice_log_path.open("w") as voice_log:
                voice = subprocess.Popen(
                    [
                        sys.executable,
                        "examples/run_wt_voice_tts_001.py",
                        "--language",
                        "en",
                        "--sessions",
                        "2",
                        "--runs",
                        str(args.voice_runs),
                        "--quantize",
                        "--use-whisper-crosstalk-check",
                        "--ready-file",
                        str(ready_path),
                        "--out",
                        str(voice_dir),
                    ],
                    stdout=voice_log,
                    stderr=subprocess.STDOUT,
                    cwd=Path.cwd(),
                )
                _wait_file(ready_path, voice, 180)

                benchmark_started = time.time()
                admission = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "voxmaestro.reflex.admission",
                        "--corpus",
                        str(args.corpus),
                        "--endpoint",
                        f"http://127.0.0.1:{args.port}/v1",
                        "--schema-engine",
                        "mlx-vlm-llguidance",
                        "--model-id",
                        CANDIDATE_MODEL_ID,
                        "--model-path",
                        str(args.model_path),
                        "--load-profile",
                        "representative",
                        "--out",
                        str(admission_path),
                    ],
                    cwd=Path.cwd(),
                    check=False,
                )
                benchmark_ended = time.time()
                voice_alive_through_benchmark = voice.poll() is None
                try:
                    voice_rc = voice.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    _terminate(voice)
                    voice_rc = -1

        admission_report = (
            _json(admission_path)
            if admission_path.exists()
            else {"verdict": "BLOCKED", "error": "missing admission evidence"}
        )
        voice_evidence = (
            _json(voice_evidence_path)
            if voice_evidence_path.exists()
            else {}
        )
        decision = adjudicate_physical(
            admission_report,
            voice_evidence,
            voice_alive_through_benchmark=voice_alive_through_benchmark,
        )
        decision.update(
            {
                "program": "VM-REFLEX-001",
                "candidate_model_id": CANDIDATE_MODEL_ID,
                "candidate_weight_sha256": CANDIDATE_WEIGHT_SHA256,
                "preflight": preflight,
                "processes": {
                    "model_server_pid": server.pid if server else None,
                    "voice_witness_pid": voice.pid if voice else None,
                    "admission_returncode": admission.returncode,
                    "voice_witness_returncode": voice_rc,
                },
                "timing": {
                    "run_started_epoch_s": started,
                    "benchmark_started_epoch_s": benchmark_started,
                    "benchmark_ended_epoch_s": benchmark_ended,
                    "run_ended_epoch_s": time.time(),
                },
                "evidence": {
                    "model_admission": str(admission_path),
                    "voice_witness": str(voice_evidence_path),
                    "voice_ready": str(ready_path),
                    "model_server_log": str(server_log_path),
                    "voice_load_log": str(voice_log_path),
                    "model_admission_sha256": (
                        _sha256(admission_path) if admission_path.exists() else None
                    ),
                    "voice_witness_sha256": (
                        _sha256(voice_evidence_path)
                        if voice_evidence_path.exists()
                        else None
                    ),
                },
            }
        )
        _write(final_path, decision)
        print(json.dumps({"evidence": str(final_path), "verdict": decision["verdict"]}))
        return 0 if decision["verdict"] == "PASS_REFLEX_PHYSICAL_ADMISSION" else 2
    except Exception as error:
        report = {
            "contract_version": "reflex-physical-admission.v1",
            "verdict": "TEST_INVALID",
            "authority": "EVIDENCE_ONLY_NOT_ROUTING_AUTHORITY",
            "stage": "orchestration",
            "error": f"{type(error).__name__}: {error}",
            "preflight": preflight,
        }
        _write(final_path, report)
        print(json.dumps({"evidence": str(final_path), "verdict": report["verdict"]}))
        return 2
    finally:
        _terminate(voice)
        _terminate(server)


if __name__ == "__main__":
    raise SystemExit(main())
