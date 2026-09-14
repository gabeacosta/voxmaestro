"""Worker-side services implementing the ``remote_worker.v0`` HTTP contract.

Everything under ``voxmaestro.fleet`` is the *client* boundary the runtime
calls through (``RemoteWorkerGenerationAdapter``). Everything under
``voxmaestro.workers`` is the *server* side of that same contract: a thin
process that translates the frozen request/response envelope into a call
against an actual model runtime. VoxMaestro never imports this package --
routing, provider choice, and fallback stay entirely on the client side.
"""
