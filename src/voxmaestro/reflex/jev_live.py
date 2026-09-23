"""Frozen live-acceptance harness for the Jev reflex challenger.

This module is experiment infrastructure. It does not grant Jev routing,
tool, state-transition, or effect authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from voxmaestro.reflex.jev_backend import (
    QUESTION_CHOICE,
    QUESTION_NOUL,
    QUESTION_SCORE,
    DecisionRequest,
    DecisionTrace,
    JevProtocolError,
    JevQuestion,
    _NoRedirect,
    hand_off_shadow,
    jev_backend_from_config,
)

EXPERIMENT_ID = "VM-JEV-LIVE-001"
SCHEMA_VERSION = "vm-jev-live-001.v1"
PROVIDERS = ("typesafe", "vercel-typesafe")
MODEL_DISCOVERY_ENDPOINT = "https://api.typesafe.ai/v1/models"
FORBIDDEN_MODEL_ALIASES = frozenset({"jev-latest", "jev-preview"})
PLACEHOLDER_MODEL_VALUES = frozenset({"", "__PINNED_MODEL__", "__REPLACE_WITH_PINNED_MODEL__"})


class LiveAcceptanceError(RuntimeError):
    """Raised when VM-JEV-LIVE-001 cannot produce valid acceptance evidence."""


@dataclass(frozen=True)
class FrozenSpecimen:
    """Immutable semantic inputs shared by both provider surfaces."""

    experiment_id: str
    schema_version: str
    model: str
    question_version: str
    state: str
    questions: tuple[JevQuestion, ...]
    providers: tuple[str, ...]
    runs_per_provider: int
    source_commit: str
    frozen_at_utc: str
    sha256: str


class QueueShadowDispatcher:
    """Single-worker queue that keeps remote shadow work off the caller's stack."""

    def __init__(
        self,
        worker: Callable[[DecisionRequest], None],
        *,
        maxsize: int = 128,
        thread_name: str = "jev-shadow",
    ) -> None:
        if maxsize < 1:
            raise ValueError("shadow_queue_maxsize_must_be_positive")
        self._worker = worker
        self._queue: queue.Queue[object] = queue.Queue(maxsize=maxsize)
        self._sentinel = object()
        self._errors: list[BaseException] = []
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name=thread_name,
            daemon=True,
        )
        self._thread.start()

    @property
    def errors(self) -> tuple[BaseException, ...]:
        """Return worker exceptions captured after enqueue."""

        return tuple(self._errors)

    def dispatch(self, request: DecisionRequest) -> None:
        """Enqueue one request without waiting for remote evaluation."""

        if self._closed:
            raise RuntimeError("shadow_queue_closed")
        try:
            self._queue.put_nowait(request)
        except queue.Full as exc:
            raise RuntimeError("shadow_queue_full") from exc

    def join(self, timeout_s: float) -> None:
        """Wait until all accepted queue items have finished or fail closed."""

        deadline = time.monotonic() + timeout_s
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                raise LiveAcceptanceError("shadow_queue_drain_timeout")
            time.sleep(0.01)
        if self._errors:
            raise LiveAcceptanceError(
                "shadow_worker_error:" + ",".join(type(exc).__name__ for exc in self._errors)
            )

    def close(self, timeout_s: float = 5.0) -> None:
        """Stop the worker after prior items have drained."""

        if self._closed:
            return
        self.join(timeout_s)
        self._closed = True
        try:
            self._queue.put_nowait(self._sentinel)
        except queue.Full as exc:
            raise LiveAcceptanceError("shadow_queue_close_full") from exc
        self._thread.join(timeout_s)
        if self._thread.is_alive():
            raise LiveAcceptanceError("shadow_worker_stop_timeout")

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._sentinel:
                    return
                assert isinstance(item, DecisionRequest)
                self._worker(item)
            except BaseException as exc:  # pragma: no cover - defensive boundary
                self._errors.append(exc)
            finally:
                self._queue.task_done()


def canonical_json_bytes(value: Any) -> bytes:
    """Encode JSON deterministically for specimen and evidence hashing."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    """Return lowercase SHA-256 hex for bytes."""

    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    """Return SHA-256 over canonical JSON bytes."""

    return sha256_bytes(canonical_json_bytes(value))


def _atomic_write(path: Path, data: bytes) -> None:
    """Create a file atomically without replacing existing evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def current_source_commit(repo_dir: Path) -> str:
    """Read the current Git commit and reject a dirty experiment checkout."""

    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise LiveAcceptanceError("dirty_worktree")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def discover_typesafe_models(api_key: str, *, timeout_s: float = 5.0) -> tuple[dict[str, str], ...]:
    """Read account-visible TypeSafe model metadata without invoking a model."""

    request = urllib.request.Request(
        MODEL_DISCOVERY_ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_s) as response:
            raw = response.read()
    except JevProtocolError as exc:
        raise LiveAcceptanceError(str(exc)) from exc
    except urllib.error.HTTPError as exc:
        raise LiveAcceptanceError(f"model_discovery_http_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise LiveAcceptanceError(f"model_discovery_unreachable:{exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise LiveAcceptanceError("model_discovery_timeout") from exc
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveAcceptanceError("model_discovery_invalid_json") from exc
    models = body.get("models") if isinstance(body, Mapping) else None
    if not isinstance(models, list):
        raise LiveAcceptanceError("model_discovery_invalid_shape")
    normalized: list[dict[str, str]] = []
    for item in models:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        release_date = item.get("release_date")
        if isinstance(name, str) and isinstance(release_date, str):
            normalized.append({"name": name, "release_date": release_date})
    if not normalized:
        raise LiveAcceptanceError("model_discovery_empty")
    return tuple(normalized)


def select_pinned_model(models: Sequence[Mapping[str, str]]) -> str:
    """Select the newest versioned Jev model while refusing moving aliases."""

    candidates = [
        item
        for item in models
        if item.get("name") not in FORBIDDEN_MODEL_ALIASES
        and str(item.get("name", "")).startswith("jev-")
    ]
    if not candidates:
        raise LiveAcceptanceError("no_versioned_jev_model_available")
    selected = max(candidates, key=lambda item: (str(item.get("release_date", "")), str(item["name"])))
    return str(selected["name"])


def _validate_pinned_model(model: Any) -> str:
    if not isinstance(model, str) or model in PLACEHOLDER_MODEL_VALUES:
        raise LiveAcceptanceError("pinned_model_required")
    if model in FORBIDDEN_MODEL_ALIASES or model.endswith("-latest"):
        raise LiveAcceptanceError("moving_model_alias_forbidden")
    if not model.startswith("jev-"):
        raise LiveAcceptanceError("unexpected_jev_model_name")
    return model


def freeze_specimen(
    template_path: Path,
    output_path: Path,
    *,
    model: str,
    source_commit: str,
    frozen_at_utc: str,
) -> FrozenSpecimen:
    """Freeze a template exactly once before any SystemOne POST is allowed."""

    template = json.loads(template_path.read_text(encoding="utf-8"))
    if not isinstance(template, Mapping):
        raise LiveAcceptanceError("specimen_template_not_mapping")
    frozen = dict(template)
    frozen["model"] = _validate_pinned_model(model)
    frozen["source_commit"] = source_commit
    frozen["frozen_at_utc"] = frozen_at_utc
    hash_path = output_path.with_suffix(output_path.suffix + ".sha256")
    if output_path.exists() or hash_path.exists():
        raise FileExistsError("frozen_specimen_already_exists")
    payload = json.dumps(frozen, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    _atomic_write(output_path, payload)
    digest = sha256_bytes(canonical_json_bytes(frozen))
    _atomic_write(
        hash_path,
        (digest + "  " + output_path.name + "\n").encode("utf-8"),
    )
    return load_frozen_specimen(output_path)


def load_frozen_specimen(path: Path) -> FrozenSpecimen:
    """Load and strictly validate a frozen live-acceptance specimen."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise LiveAcceptanceError("frozen_specimen_not_mapping")
    if raw.get("experiment_id") != EXPERIMENT_ID:
        raise LiveAcceptanceError("wrong_experiment_id")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise LiveAcceptanceError("wrong_schema_version")
    model = _validate_pinned_model(raw.get("model"))
    question_version = raw.get("question_version")
    state = raw.get("state")
    providers = raw.get("providers")
    runs_per_provider = raw.get("runs_per_provider")
    source_commit = raw.get("source_commit")
    frozen_at_utc = raw.get("frozen_at_utc")
    if not isinstance(question_version, str) or not question_version:
        raise LiveAcceptanceError("question_version_required")
    if not isinstance(state, str) or not state:
        raise LiveAcceptanceError("state_required")
    if providers != list(PROVIDERS):
        raise LiveAcceptanceError("provider_pair_must_be_frozen")
    if isinstance(runs_per_provider, bool) or not isinstance(runs_per_provider, int):
        raise LiveAcceptanceError("runs_per_provider_must_be_integer")
    if not 1 <= runs_per_provider <= 50:
        raise LiveAcceptanceError("runs_per_provider_out_of_range")
    if not isinstance(source_commit, str) or len(source_commit) != 40:
        raise LiveAcceptanceError("source_commit_required")
    if not isinstance(frozen_at_utc, str) or not frozen_at_utc:
        raise LiveAcceptanceError("frozen_at_utc_required")
    questions = _parse_questions(raw.get("questions"))
    return FrozenSpecimen(
        experiment_id=EXPERIMENT_ID,
        schema_version=SCHEMA_VERSION,
        model=model,
        question_version=question_version,
        state=state,
        questions=questions,
        providers=PROVIDERS,
        runs_per_provider=runs_per_provider,
        source_commit=source_commit,
        frozen_at_utc=frozen_at_utc,
        sha256=sha256_json(raw),
    )


def _parse_questions(raw_questions: Any) -> tuple[JevQuestion, ...]:
    if not isinstance(raw_questions, list) or not raw_questions:
        raise LiveAcceptanceError("questions_required")
    questions: list[JevQuestion] = []
    seen: set[str] = set()
    for raw in raw_questions:
        if not isinstance(raw, Mapping):
            raise LiveAcceptanceError("question_not_mapping")
        key = raw.get("key")
        kind = raw.get("kind")
        prompt = raw.get("prompt")
        if not isinstance(key, str) or not key or key in seen:
            raise LiveAcceptanceError("invalid_or_duplicate_question_key")
        if not isinstance(prompt, str) or not prompt:
            raise LiveAcceptanceError(f"question_prompt_required:{key}")
        seen.add(key)
        if kind == QUESTION_CHOICE:
            options = raw.get("options")
            if not isinstance(options, list) or not options or not all(
                isinstance(item, str) and item for item in options
            ):
                raise LiveAcceptanceError(f"choice_options_required:{key}")
            questions.append(
                JevQuestion(kind=kind, key=key, prompt=prompt, options=tuple(options))
            )
        elif kind == QUESTION_SCORE:
            levels = raw.get("levels")
            if not isinstance(levels, list) or not 2 <= len(levels) <= 10:
                raise LiveAcceptanceError(f"score_levels_required:{key}")
            normalized_levels = []
            for level in levels:
                if (
                    not isinstance(level, list)
                    or len(level) != 2
                    or not all(isinstance(item, str) and item for item in level)
                ):
                    raise LiveAcceptanceError(f"invalid_score_level:{key}")
                normalized_levels.append((level[0], level[1]))
            questions.append(
                JevQuestion(kind=kind, key=key, prompt=prompt, levels=tuple(normalized_levels))
            )
        elif kind == QUESTION_NOUL:
            threshold = raw.get("threshold")
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
                raise LiveAcceptanceError(f"noul_threshold_required:{key}")
            threshold = float(threshold)
            if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
                raise LiveAcceptanceError(f"invalid_noul_threshold:{key}")
            questions.append(
                JevQuestion(kind=kind, key=key, prompt=prompt, threshold=threshold)
            )
        else:
            raise LiveAcceptanceError(f"unknown_question_kind:{kind}")
    return tuple(questions)


def _project_decision(question: JevQuestion, value: Any) -> Any:
    if value is None:
        return None
    if question.kind == QUESTION_CHOICE:
        return value
    if question.kind == QUESTION_NOUL:
        assert question.threshold is not None
        return bool(float(value) >= question.threshold)
    if question.kind == QUESTION_SCORE:
        index = int(math.floor(float(value) + 0.5))
        return max(0, min(index, len(question.levels) - 1))
    raise LiveAcceptanceError(f"unsupported_question_kind:{question.kind}")


def _percentile(values: Sequence[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(float(ordered[index]), 3)


def _latency_summary(values: Sequence[float]) -> dict[str, Optional[float]]:
    if not values:
        return {"min_ms": None, "median_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "min_ms": round(min(values), 3),
        "median_ms": round(statistics.median(values), 3),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": round(max(values), 3),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise LiveAcceptanceError("audit_record_not_mapping")
            records.append(value)
    return records


def _run_provider(
    specimen: FrozenSpecimen,
    provider: str,
    api_key: str,
    out_dir: Path,
) -> dict[str, Any]:
    audit_path = out_dir / f"{provider}.jsonl"
    backend = jev_backend_from_config(
        {
            "provider": provider,
            "api_key": api_key,
            "model": specimen.model,
            "audit_log": str(audit_path),
            "timeout_s": 0.8,
        }
    )
    results: queue.Queue[dict[str, Any]] = queue.Queue()

    def worker(request: DecisionRequest) -> None:
        verdicts = backend.decide(request)
        results.put(
            {
                "turn_id": request.trace.turn_id,
                "verdicts": [
                    {
                        "key": verdict.key,
                        "kind": verdict.kind,
                        "value": verdict.value,
                        "confidence": verdict.confidence,
                        "ok": verdict.ok,
                    }
                    for verdict in verdicts
                ],
            }
        )

    dispatcher = QueueShadowDispatcher(worker, thread_name=f"jev-shadow-{provider}")
    enqueue_ms: list[float] = []
    for index in range(specimen.runs_per_provider):
        request = DecisionRequest(
            state=specimen.state,
            questions=specimen.questions,
            trace=DecisionTrace(
                session_id=specimen.experiment_id,
                turn_id=f"{provider}-{index:03d}",
                question_version=specimen.question_version,
                local_decision_id=f"frozen-{index:03d}",
            ),
        )
        started = time.monotonic()
        hand_off_shadow(request, dispatcher.dispatch)
        enqueue_ms.append((time.monotonic() - started) * 1000.0)

    dispatcher.close(timeout_s=max(5.0, specimen.runs_per_provider * 2.0))
    if dispatcher.errors:
        raise LiveAcceptanceError(f"shadow_worker_failed:{provider}")

    delivered = [results.get_nowait() for _ in range(results.qsize())]
    observations = _read_jsonl(audit_path)
    if len(delivered) != specimen.runs_per_provider:
        raise LiveAcceptanceError(f"result_count_mismatch:{provider}")
    if len(observations) != specimen.runs_per_provider:
        raise LiveAcceptanceError(f"audit_count_mismatch:{provider}")
    remote_ms = [
        float(record["total_latency_ms"])
        for record in observations
        if isinstance(record.get("total_latency_ms"), (int, float))
    ]
    return {
        "provider": provider,
        "enqueue_latency": _latency_summary(enqueue_ms),
        "remote_latency": _latency_summary(remote_ms),
        "results": delivered,
        "observations": observations,
        "audit_file": audit_path.name,
    }


def reconcile_provider_reports(
    specimen: FrozenSpecimen,
    reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare provider results without granting either provider authority."""

    if set(reports) != set(PROVIDERS):
        raise LiveAcceptanceError("provider_reports_incomplete")
    left = reports["typesafe"]
    right = reports["vercel-typesafe"]
    left_obs = list(left["observations"])
    right_obs = list(right["observations"])
    if len(left_obs) != specimen.runs_per_provider or len(right_obs) != specimen.runs_per_provider:
        raise LiveAcceptanceError("observation_count_mismatch")

    provider_valid = all(
        bool(record.get("ok"))
        for record in left_obs + right_obs
    )
    hash_pairs = [
        {
            "run": index,
            "typesafe": left_obs[index].get("request_hash"),
            "vercel_typesafe": right_obs[index].get("request_hash"),
            "same": left_obs[index].get("request_hash") == right_obs[index].get("request_hash"),
        }
        for index in range(specimen.runs_per_provider)
    ]
    request_identity = all(pair["same"] and pair["typesafe"] for pair in hash_pairs)

    question_map = {question.key: question for question in specimen.questions}
    comparisons: list[dict[str, Any]] = []
    semantic_agreement = True
    for index in range(specimen.runs_per_provider):
        left_values = {item["key"]: item for item in left["results"][index]["verdicts"]}
        right_values = {item["key"]: item for item in right["results"][index]["verdicts"]}
        for key, question in question_map.items():
            left_item = left_values.get(key, {})
            right_item = right_values.get(key, {})
            left_value = left_item.get("value")
            right_value = right_item.get("value")
            left_projection = _project_decision(question, left_value)
            right_projection = _project_decision(question, right_value)
            agrees = left_projection == right_projection and left_projection is not None
            semantic_agreement = semantic_agreement and agrees
            raw_delta = None
            if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
                raw_delta = round(abs(float(left_value) - float(right_value)), 6)
            comparisons.append(
                {
                    "run": index,
                    "question": key,
                    "typesafe_value": left_value,
                    "vercel_typesafe_value": right_value,
                    "typesafe_projection": left_projection,
                    "vercel_typesafe_projection": right_projection,
                    "semantic_agreement": agrees,
                    "raw_numeric_delta": raw_delta,
                }
            )

    if not provider_valid:
        status = "FAIL_PROVIDER"
    elif not request_identity:
        status = "FAIL_REQUEST_DRIFT"
    elif not semantic_agreement:
        status = "REVIEW_DISAGREEMENT"
    else:
        status = "PASS"

    return {
        "experiment_id": specimen.experiment_id,
        "specimen_sha256": specimen.sha256,
        "model": specimen.model,
        "status": status,
        "provider_valid": provider_valid,
        "request_identity": request_identity,
        "semantic_agreement": semantic_agreement,
        "hash_pairs": hash_pairs,
        "comparisons": comparisons,
        "latency": {
            provider: {
                "enqueue": reports[provider]["enqueue_latency"],
                "remote": reports[provider]["remote_latency"],
            }
            for provider in PROVIDERS
        },
        "authority_promoted": False,
    }


def _write_json_once(path: Path, value: Any) -> None:
    _atomic_write(
        path,
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n",
    )


def seal_evidence(out_dir: Path, status: str) -> dict[str, Any]:
    """Hash the immutable evidence files and write a content-addressed seal."""

    evidence_files = sorted(
        path
        for path in out_dir.iterdir()
        if path.is_file() and path.name != "seal.json"
    )
    hashes = {path.name: sha256_bytes(path.read_bytes()) for path in evidence_files}
    material = {
        "schema_version": "vm-jev-live-001.seal.v1",
        "experiment_id": EXPERIMENT_ID,
        "status": status,
        "files": hashes,
    }
    seal_sha256 = sha256_json(material)
    seal = {**material, "seal_sha256": seal_sha256}
    _write_json_once(out_dir / "seal.json", seal)
    return seal


def run_live_acceptance(
    specimen_path: Path,
    out_dir: Path,
    *,
    typesafe_api_key: str,
    ai_gateway_api_key: str,
    repo_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Run both live provider surfaces against identical frozen semantics."""

    specimen = load_frozen_specimen(specimen_path)
    if not typesafe_api_key:
        raise LiveAcceptanceError("TYPESAFE_API_KEY_required")
    if not ai_gateway_api_key:
        raise LiveAcceptanceError("AI_GATEWAY_API_KEY_required")
    if out_dir.exists():
        raise LiveAcceptanceError("evidence_directory_already_exists")
    out_dir.mkdir(parents=True, mode=0o700)
    if repo_dir is not None:
        actual_commit = current_source_commit(repo_dir)
        if actual_commit != specimen.source_commit:
            raise LiveAcceptanceError(
                f"source_commit_drift:specimen={specimen.source_commit}:actual={actual_commit}"
            )

    frozen_copy = json.loads(specimen_path.read_text(encoding="utf-8"))
    _write_json_once(out_dir / "frozen-specimen.json", frozen_copy)

    reports = {
        "typesafe": _run_provider(specimen, "typesafe", typesafe_api_key, out_dir),
        "vercel-typesafe": _run_provider(
            specimen,
            "vercel-typesafe",
            ai_gateway_api_key,
            out_dir,
        ),
    }
    safe_reports = {
        provider: {
            key: value
            for key, value in report.items()
            if key != "observations"
        }
        for provider, report in reports.items()
    }
    _write_json_once(out_dir / "provider-results.json", safe_reports)
    reconciliation = reconcile_provider_reports(specimen, reports)
    _write_json_once(out_dir / "reconciliation.json", reconciliation)
    seal = seal_evidence(out_dir, reconciliation["status"])
    return {
        "status": reconciliation["status"],
        "specimen_sha256": specimen.sha256,
        "seal_sha256": seal["seal_sha256"],
        "out_dir": str(out_dir),
        "authority_promoted": False,
    }
