# Acceptance Criteria

- A schedule intent emits filler before the tool completes.
- A successful tool call returns the session to its origin state.
- Tool data is available through a stable generation-context contract.
- Missing executors cannot be mistaken for successful external work.
- Dry-run execution is explicitly simulated.
- Max-turn escalation runs handoff delivery and teardown.
- Concurrent call sessions cannot cross callbacks or tool results.
- Tool-failure handoff never reports delivery unless a handoff executor confirms it.
