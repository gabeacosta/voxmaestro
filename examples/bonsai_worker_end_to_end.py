"""End-to-end Slice 4 proof: config -> fleet_from_config -> RemoteWorkerGenerationAdapter
-> real BonsaiWorkerServer -> VoxMaestro turn.

This loads examples/microscroll_landing_bonsai.yaml through the same
SchemaLoader + fleet_from_config path a real deployment would use (nothing
special-cased for Bonsai), starts the worker service in-process, and runs
one turn to prove the wiring end to end without touching VoxMaestro's state
machine, routing, or fallback behavior.

The worker's actual "model" is EchoInferenceBackend -- a placeholder, not a
real native low-bit runtime (see workers/bonsai_worker.py's module docstring
for why none is available in this environment).

Run:
    python examples/bonsai_worker_end_to_end.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from voxmaestro.conductor import SchemaLoader
from voxmaestro.fleet import fleet_from_config
from voxmaestro.runtime import VoxMaestroRuntime
from voxmaestro.workers.bonsai_worker import BonsaiWorkerServer, EchoInferenceBackend

CONFIG_PATH = Path(__file__).with_name("microscroll_landing_bonsai.yaml")


async def classify(text: str, context) -> str:
    lowered = text.lower()
    if "charge" in lowered or "cost" in lowered or "price" in lowered:
        return "pricing_question"
    return "service_question"


async def main() -> int:
    server = BonsaiWorkerServer(
        worker_id="bonsai-l1-01", slot="l1_worker", backend=EchoInferenceBackend(), port=8091
    )
    server.start()
    print(f"bonsai worker up at {server.url} (EchoInferenceBackend -- not a real model)")
    try:
        config = SchemaLoader.load(CONFIG_PATH)
        _classifier, generator = fleet_from_config(config)
        print(f"generation adapter from config: {type(generator).__name__}")
        print(f"  worker_id={generator.worker_id!r} slot={generator.slot!r} endpoint={generator.endpoint!r}")

        runtime = VoxMaestroRuntime(config, intent_classifier=classify)
        call = runtime.start_call("demo-call")
        result = await call.process_turn("What do you charge for a standard visit?")
        print(f"turn result: state={call.context.current_state!r} action={result.get('action')!r}")

        context = runtime.generation_context(call.context)
        generation_config = runtime.config.get("generation", {})
        response = await generator(
            "What do you charge for a standard visit?", context, generation_config
        )
        print(f"generated response (via real HTTP round trip to the worker): {response!r}")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
