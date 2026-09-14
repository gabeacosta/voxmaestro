"""Thin ``remote_worker.v0`` HTTP service for a native low-bit (Bonsai) worker.

This is the SERVER side of the contract ``voxmaestro.fleet.RemoteWorkerGenerationAdapter``
already speaks as a client (see that module's docstring for the frozen wire
shape). It does one job: validate the envelope, call an injected
``InferenceBackend``, and return the envelope back. It never chooses a model,
never retries, never talks to another worker, and never receives or forwards
credentials -- there is nothing upstream of this process to authenticate to.

What this module does NOT provide: an actual binary/ternary model runtime.
No native low-bit runtime (Binary Bonsai, Ternary Bonsai, or otherwise) is
available in this environment -- that requires real model weights and a
native inference binary or MLX runtime on the target host (the Mac mini).
``EchoInferenceBackend`` below is an explicit, clearly-labeled placeholder
used only to prove the service contract end to end; wiring in a real Bonsai
runtime means implementing ``InferenceBackend`` against that runtime and
passing it to ``BonsaiWorkerServer`` -- no other part of this module, or of
``voxmaestro.fleet``, changes.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional, Protocol

CONTRACT_VERSION = "remote_worker.v0"


class InferenceBackend(Protocol):
    """The extension point a real native low-bit runtime implements.

    ``generate`` must return plain text or raise. It must never invent a
    successful-looking result when the underlying runtime failed, and it
    must respect ``limits`` on a best-effort basis (the HTTP layer enforces
    ``deadline_ms`` regardless, in a worker thread with a hard timeout).
    """

    def generate(self, text: str, context: Mapping[str, Any], limits: Mapping[str, Any]) -> str:
        """Produce a response for ``text``. Raise on failure; never fabricate."""


class EchoInferenceBackend:
    """Reference/test-only stand-in. NOT a native low-bit model.

    Used to prove the worker's HTTP contract, request validation, and the
    existing ``RemoteWorkerGenerationAdapter`` client wiring, without
    depending on model weights or a native runtime this sandbox does not
    have. Never present this backend's output as evidence of a working
    Bonsai model.
    """

    backend_label = "echo-reference-not-a-model"

    def generate(self, text: str, context: Mapping[str, Any], limits: Mapping[str, Any]) -> str:
        max_tokens = limits.get("max_tokens")
        reply = f"[{self.backend_label}] {text}".strip()
        if isinstance(max_tokens, int) and max_tokens > 0:
            reply = " ".join(reply.split(" ")[:max_tokens])
        return reply


class WorkerRequestError(ValueError):
    """A malformed or out-of-contract request. Maps to HTTP 400."""


def _require_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkerRequestError(f"{field} is required and must be a non-empty string")
    return value


def handle_work_request(
    payload: Mapping[str, Any],
    *,
    worker_id: str,
    slot: str,
    backend: InferenceBackend,
    deadline_ms: float = 5000,
) -> dict[str, Any]:
    """Validate one ``/v1/work`` request and return the response envelope.

    Pure function (no HTTP/socket concerns) so it can be unit-tested
    directly. Fails closed: any validation error or backend exception
    becomes an explicit error response, never a fabricated ``status: ok``.
    """
    if payload.get("contract_version") != CONTRACT_VERSION:
        raise WorkerRequestError(f"unsupported contract_version (expected {CONTRACT_VERSION!r})")
    request_worker_id = _require_str(payload.get("worker_id"), "worker_id")
    if request_worker_id != worker_id:
        raise WorkerRequestError(
            f"worker identity mismatch: this worker is {worker_id!r}, request named {request_worker_id!r}"
        )
    request_slot = _require_str(payload.get("slot"), "slot")
    if request_slot != slot:
        raise WorkerRequestError(f"slot mismatch: this worker serves {slot!r}, request named {request_slot!r}")
    if payload.get("task") != "generate":
        raise WorkerRequestError("only task=='generate' is supported")

    input_block = payload.get("input")
    if not isinstance(input_block, Mapping):
        raise WorkerRequestError("input is required")
    text = _require_str(input_block.get("text"), "input.text")

    context = payload.get("context")
    context = dict(context) if isinstance(context, Mapping) else {}
    limits = payload.get("limits")
    limits = dict(limits) if isinstance(limits, Mapping) else {}

    request_deadline_ms = limits.get("deadline_ms")
    effective_deadline_ms = (
        float(request_deadline_ms) if isinstance(request_deadline_ms, (int, float)) else deadline_ms
    )

    result_holder: dict[str, Any] = {}
    error_holder: dict[str, BaseException] = {}

    def _run() -> None:
        try:
            result_holder["text"] = backend.generate(text, context, limits)
        except BaseException as exc:  # noqa: BLE001 -- surface any backend failure honestly
            error_holder["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=effective_deadline_ms / 1000)

    request_id = payload.get("request_id")
    request_id = str(request_id) if request_id is not None else ""

    if thread.is_alive():
        raise WorkerRequestError(f"backend exceeded deadline_ms={effective_deadline_ms}")
    if "error" in error_holder:
        raise WorkerRequestError(f"backend generation failed: {error_holder['error']}") from error_holder["error"]

    output_text = result_holder.get("text")
    if not isinstance(output_text, str) or not output_text.strip():
        raise WorkerRequestError("backend returned no text")

    return {
        "status": "ok",
        "request_id": request_id,
        "worker_id": worker_id,
        "slot": slot,
        "result": {"text": output_text},
    }


class BonsaiWorkerServer:
    """Stdlib HTTP server exposing ``POST /v1/work`` for one fixed worker/slot.

    No new runtime dependencies: plain ``http.server``, matching the "thin
    process" framing of the handoff contract. Runs in a background thread so
    it can be started and stopped from a test or from a small CLI wrapper.
    """

    def __init__(
        self,
        *,
        worker_id: str,
        slot: str = "l1_worker",
        backend: Optional[InferenceBackend] = None,
        host: str = "127.0.0.1",
        port: int = 0,
        deadline_ms: float = 5000,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        self.worker_id = worker_id
        self.slot = slot
        self.backend = backend or EchoInferenceBackend()
        self.deadline_ms = deadline_ms

        server_self = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return  # keep test/CLI output quiet; add real logging at deploy time

            def do_POST(self) -> None:  # noqa: N802 -- stdlib naming
                if self.path != "/v1/work":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw)
                    if not isinstance(payload, Mapping):
                        raise WorkerRequestError("request body must be a JSON object")
                    response = handle_work_request(
                        payload,
                        worker_id=server_self.worker_id,
                        slot=server_self.slot,
                        backend=server_self.backend,
                        deadline_ms=server_self.deadline_ms,
                    )
                    status = 200
                except WorkerRequestError as exc:
                    response = {"status": "error", "error": str(exc)}
                    status = 400
                except (json.JSONDecodeError, TypeError):
                    response = {"status": "error", "error": "invalid JSON body"}
                    status = 400
                body = json.dumps(response).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        host = "127.0.0.1" if host == "0.0.0.0" else host
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
