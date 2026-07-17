# Runtime Truth Contract

The `VoxMaestroRuntime` path is the additive v0.2 execution boundary.

It guarantees:

- mutable state and callbacks belong to one `CallSession`;
- filler events can leave the runtime before a tool finishes;
- missing tool executors fail instead of reporting simulated success;
- dry-run results are explicitly marked `simulated` and are never successful;
- transient `tool_call` states return to their configured origin;
- max-turn escalation executes the handoff protocol;
- handoff delivery receipts distinguish `delivered`, `failed`, `simulated`, and `not_delivered`.

The existing `VoxMaestro` conductor remains available during the compatibility period. After this runtime slice is validated in real integrations, the alpha conductor should become a thin wrapper over `VoxMaestroRuntime`.
