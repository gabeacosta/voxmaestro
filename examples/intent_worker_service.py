"""Serve the challenger L0 slot; this does not promote a frozen VSAI reflex."""
import argparse
import threading

from voxmaestro.workers.intent_worker import IntentWorker, IntentWorkerServer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8081/v1")
    parser.add_argument("--model", default="prism-ml/Ternary-Bonsai-1.7B-mlx-2bit")
    parser.add_argument("--timeout-s", type=float, default=1.0)
    parser.add_argument("--min-confidence", type=float, default=0.5)
    args = parser.parse_args()
    worker = IntentWorker(endpoint=args.endpoint, model=args.model,
                          timeout_s=args.timeout_s, min_confidence=args.min_confidence)
    server = IntentWorkerServer(worker=worker, host=args.host, port=args.port)
    server.start()
    print(f"L0 intent slot listening on {server.url}/v1/intent")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
