"""Serve the admitted Nomic retrieval lane as one fixed semantic service.

Build the persistent index explicitly once:

    uv run python examples/retrieval_worker_service.py --rebuild-index

Subsequent launches validate the corpus hash and model digest before serving
``POST /v1/retrieve`` on loopback. The service never selects a model, tool,
provider, route, fallback, or effect.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from voxmaestro.retrieval import OllamaEmbedClient
from voxmaestro.workers.retrieval_worker import RetrievalWorker, RetrievalWorkerServer

ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "docs" / "wt" / "vsai_retrieval_v0.json"
DEFAULT_INDEX = ROOT / "evidence" / "small-model-fleet" / "vsai_retrieval_v0.index.json"
DEFAULT_DIGEST = "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="nomic-embed-text:latest")
    parser.add_argument("--expected-digest", default=DEFAULT_DIGEST)
    parser.add_argument("--timeout-s", type=float, default=2.0)
    parser.add_argument("--top-k", type=int, default=1)
    parser.add_argument("--min-score", type=float, default=0.6)
    parser.add_argument("--min-margin", type=float, default=0.03)
    parser.add_argument("--rebuild-index", action="store_true")
    args = parser.parse_args()

    client = OllamaEmbedClient(
        args.endpoint,
        args.model,
        expected_digest=args.expected_digest,
        timeout_s=args.timeout_s,
        keep_alive=-1,
    )
    worker = RetrievalWorker.from_files(
        args.corpus,
        args.index,
        client=client,
        rebuild_index=args.rebuild_index,
        top_k=args.top_k,
        min_score=args.min_score,
        min_margin=args.min_margin,
    )
    worker.warm()
    server = RetrievalWorkerServer(worker=worker, host=args.host, port=args.port)
    server.start()
    print(
        f"retrieval worker model={args.model!r} digest={worker.model_digest} "
        f"listening on {server.url}/v1/retrieve"
    )
    print(f"corpus_sha256={worker.corpus_sha256} index={args.index}")
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
