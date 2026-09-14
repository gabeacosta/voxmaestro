"""Run the thin Bonsai worker service standalone.

Default mode remains the explicit echo/reference backend. For a real native
Bonsai runtime exposing an OpenAI-compatible API (for example a local
llama-server or MLX server), select ``--backend openai`` and pin the endpoint
and model identity.

Example:
    python examples/bonsai_worker_service.py \
      --worker-id bonsai-l1-01 \
      --backend openai \
      --endpoint http://127.0.0.1:8080/v1 \
      --model bonsai-local

Pair the worker with a VoxMaestro config's generation block:

    generation:
      provider: remote_worker
      endpoint: http://127.0.0.1:8091
      worker_id: bonsai-l1-01
      slot: l1_worker

VoxMaestro's existing ``RemoteWorkerGenerationAdapter`` remains unchanged.
The model cannot choose another model, another worker, or a fallback route.
"""

from __future__ import annotations

import argparse
import time

from voxmaestro.workers.bonsai_worker import BonsaiWorkerServer, EchoInferenceBackend
from voxmaestro.workers.openai_compat_backend import OpenAICompatInferenceBackend


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--worker-id", default="bonsai-l1-01")
    parser.add_argument("--slot", default="l1_worker")
    parser.add_argument("--backend", choices=("echo", "openai"), default="echo")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="bonsai-local")
    parser.add_argument("--timeout-s", type=float, default=30.0)
    args = parser.parse_args()

    if args.backend == "openai":
        backend = OpenAICompatInferenceBackend(
            endpoint=args.endpoint,
            model=args.model,
            timeout_s=args.timeout_s,
        )
        backend_label = f"openai-compatible endpoint={args.endpoint!r} model={args.model!r}"
    else:
        backend = EchoInferenceBackend()
        backend_label = "REFERENCE BACKEND, NOT a real model"

    server = BonsaiWorkerServer(
        worker_id=args.worker_id,
        slot=args.slot,
        backend=backend,
        host=args.host,
        port=args.port,
    )
    server.start()
    print(f"bonsai worker ({backend_label}) listening on {server.url}/v1/work")
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
