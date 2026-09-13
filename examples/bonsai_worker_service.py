"""Run the thin Bonsai worker service standalone (Slice 4, WT-VOICE handoff).

This starts the ``remote_worker.v0`` HTTP contract server from
``voxmaestro.workers.bonsai_worker`` on a fixed worker/slot identity. Pair it
with a VoxMaestro config's ``generation`` block:

    generation:
      provider: remote_worker
      endpoint: http://127.0.0.1:8091
      worker_id: bonsai-l1-01
      slot: l1_worker

``voxmaestro.fleet.fleet_from_config`` (unmodified) builds a
``RemoteWorkerGenerationAdapter`` from that block automatically.

IMPORTANT: this ships with ``EchoInferenceBackend``, an explicit
reference/test placeholder -- NOT a native low-bit model. No binary/ternary
Bonsai runtime is available in this repo or this environment yet (that needs
real model weights and a native inference binary or MLX runtime on the
target Mac mini). Swapping in a real one means implementing
``voxmaestro.workers.bonsai_worker.InferenceBackend`` against that runtime
and passing it here -- nothing else in this file, ``bonsai_worker.py``, or
``fleet.py`` needs to change.

Run:
    python examples/bonsai_worker_service.py --worker-id bonsai-l1-01 --port 8091
"""

from __future__ import annotations

import argparse
import time

from voxmaestro.workers.bonsai_worker import BonsaiWorkerServer, EchoInferenceBackend


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--worker-id", default="bonsai-l1-01")
    parser.add_argument("--slot", default="l1_worker")
    args = parser.parse_args()

    server = BonsaiWorkerServer(
        worker_id=args.worker_id,
        slot=args.slot,
        backend=EchoInferenceBackend(),
        host=args.host,
        port=args.port,
    )
    server.start()
    print(f"bonsai worker (REFERENCE BACKEND, NOT a real model) listening on {server.url}/v1/work")
    print(f"worker_id={args.worker_id!r} slot={args.slot!r}")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
