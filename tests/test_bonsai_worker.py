from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from voxmaestro.fleet import RemoteWorkerGenerationAdapter
from voxmaestro.workers.bonsai_worker import (
    BonsaiWorkerServer,
    EchoInferenceBackend,
    WorkerRequestError,
    handle_work_request,
)


def _payload(**overrides: object) -> dict:
    base = {
        "contract_version": "remote_worker.v0",
        "request_id": "r1",
        "worker_id": "bonsai-l1-01",
        "slot": "l1_worker",
        "task": "generate",
        "input": {"text": "hello"},
        "context": {},
        "limits": {"deadline_ms": 2000},
    }
    base.update(overrides)
    return base


def test_handle_work_request_round_trip() -> None:
    response = handle_work_request(
        _payload(), worker_id="bonsai-l1-01", slot="l1_worker", backend=EchoInferenceBackend()
    )
    assert response["status"] == "ok"
    assert response["worker_id"] == "bonsai-l1-01"
    assert response["slot"] == "l1_worker"
    assert response["request_id"] == "r1"
    assert "hello" in response["result"]["text"]


def test_worker_id_mismatch_rejected() -> None:
    with pytest.raises(WorkerRequestError, match="identity mismatch"):
        handle_work_request(
            _payload(worker_id="someone-else"),
            worker_id="bonsai-l1-01",
            slot="l1_worker",
            backend=EchoInferenceBackend(),
        )


def test_slot_mismatch_rejected() -> None:
    with pytest.raises(WorkerRequestError, match="slot mismatch"):
        handle_work_request(
            _payload(slot="l2_resolver"),
            worker_id="bonsai-l1-01",
            slot="l1_worker",
            backend=EchoInferenceBackend(),
        )


def test_wrong_contract_version_rejected() -> None:
    with pytest.raises(WorkerRequestError, match="contract_version"):
        handle_work_request(
            _payload(contract_version="remote_worker.v1"),
            worker_id="bonsai-l1-01",
            slot="l1_worker",
            backend=EchoInferenceBackend(),
        )


def test_missing_input_text_rejected() -> None:
    with pytest.raises(WorkerRequestError, match="input.text"):
        handle_work_request(
            _payload(input={}), worker_id="bonsai-l1-01", slot="l1_worker", backend=EchoInferenceBackend()
        )


def test_backend_failure_is_not_fabricated_success() -> None:
    class BoomBackend:
        def generate(self, text, context, limits):
            raise RuntimeError("native runtime crashed")

    with pytest.raises(WorkerRequestError, match="backend generation failed"):
        handle_work_request(
            _payload(), worker_id="bonsai-l1-01", slot="l1_worker", backend=BoomBackend()
        )


def test_deadline_exceeded_is_not_fabricated_success() -> None:
    class SlowBackend:
        def generate(self, text, context, limits):
            import time

            time.sleep(0.2)
            return "too slow"

    with pytest.raises(WorkerRequestError, match="deadline_ms"):
        handle_work_request(
            _payload(limits={"deadline_ms": 10}),
            worker_id="bonsai-l1-01",
            slot="l1_worker",
            backend=SlowBackend(),
        )


def test_max_tokens_is_respected_best_effort() -> None:
    response = handle_work_request(
        _payload(input={"text": "one two three four five"}, limits={"max_tokens": 2}),
        worker_id="bonsai-l1-01",
        slot="l1_worker",
        backend=EchoInferenceBackend(),
    )
    # EchoInferenceBackend prefixes a label token; just confirm truncation happened.
    assert len(response["result"]["text"].split(" ")) <= 2


class TestBonsaiWorkerServerIntegration:
    """Real HTTP, real socket, real background thread -- only the model is fake."""

    def test_real_client_real_server_round_trip(self) -> None:
        server = BonsaiWorkerServer(worker_id="bonsai-l1-01", slot="l1_worker")
        server.start()
        try:
            request = urllib.request.Request(
                f"{server.url}/v1/work",
                data=json.dumps(_payload()).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as resp:
                body = json.loads(resp.read())
            assert body["status"] == "ok"
            assert body["worker_id"] == "bonsai-l1-01"
        finally:
            server.stop()

    def test_remote_worker_generation_adapter_end_to_end(self) -> None:
        """The existing, unmodified client adapter talking to this worker over
        a real loopback socket -- proves the fleet.py <-> workers/ wiring."""
        server = BonsaiWorkerServer(worker_id="bonsai-l1-01", slot="l1_worker")
        server.start()
        try:
            adapter = RemoteWorkerGenerationAdapter(
                server.url, worker_id="bonsai-l1-01", slot="l1_worker"
            )

            async def _call():
                return await adapter(
                    "What are your hours?",
                    {"call_id": "c1", "api_key": "must-not-be-forwarded"},
                    {"max_tokens": 32},
                )

            import asyncio

            result = asyncio.run(_call())
            assert "What are your hours?" in result
        finally:
            server.stop()

    def test_worker_rejects_unknown_path(self) -> None:
        server = BonsaiWorkerServer(worker_id="bonsai-l1-01", slot="l1_worker")
        server.start()
        try:
            request = urllib.request.Request(f"{server.url}/other", method="POST", data=b"{}")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(request, timeout=5)
            assert exc_info.value.code == 404
        finally:
            server.stop()
