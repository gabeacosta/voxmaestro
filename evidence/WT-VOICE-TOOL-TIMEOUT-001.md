# WT-VOICE-TOOL-TIMEOUT-001 — local deadline vs executor cancellation authority

## Disposition

**GREEN on native synthetic fixtures.** This slice extracts a bounded VoxMaestro runtime improvement; it does not add Pipecat, LiveKit, Dograh, a provider SDK, or a real Ceinit dependency.

## Target

- Repository: `gabeacosta/voxmaestro`
- Baseline main: `27c734c2c3ded9ce63a0b5cbf854d1a3de24b7a6`
- Fix head: `2c08d691eb0c600121384781f3391e747f5bd08b`
- PR: #49

## Red evidence

GitHub Actions run `37569043148` executed the red-first specimen on the unmodified runtime.

Python 3.10:
- fast tool positive control: PASS;
- `test_local_timeout_must_not_cancel_dispatched_executor`: FAIL;
- `test_timeout_cannot_launch_fallback_handoff_as_second_effect`: FAIL.

Observed behavior:
1. `asyncio.wait_for` injected `CancelledError` into the executor after the specimen had crossed `DISPATCH_STARTED`.
2. VoxMaestro interpreted the local timeout as an ordinary tool failure and executed the configured fallback handoff, creating a second consequential effect.
3. Ruff passed. The fail-fast matrix cancelled the other red interpreter jobs; no red verdict is claimed for them.

## Native correction

### Explicit cancellation authority

Tools may now declare:

`cancellation_authority: executor`

The default remains:

`cancellation_authority: runtime`

This is intentionally opt-in. Existing runtime-owned or read-only work preserves the previous timeout cancellation behavior.

### Executor-owned deadline

For `cancellation_authority: executor`:
- VoxMaestro creates the executor task and observes it through a local deadline without cancelling it.
- When the local deadline expires, the result is `success=False, uncertain=True`.
- The in-flight task is retained until completion and its eventual exception is consumed/logged to avoid an unobserved-task failure.
- The caller may receive an uncertain response, but timeout alone cannot launch a fallback handoff.

### Runtime-owned deadline

For the default `runtime` policy:
- `asyncio.wait_for` remains in place;
- timeout still cancels the executor coroutine;
- `uncertain=False`.

## Green evidence

GitHub Actions run `37569196642` on fix head `2c08d691eb0c600121384781f3391e747f5bd08b`:

- Python 3.10: **409 passed / 0 failed**
- Python 3.11: **409 passed / 0 failed**
- Python 3.12: **409 passed / 0 failed**
- Ruff: **PASS**

All four WT fixtures passed on each interpreter:
1. fast tool positive control;
2. executor-owned local timeout does not cancel the dispatched executor;
3. uncertain timeout cannot launch fallback handoff;
4. runtime-owned timeout still cancels the executor.

## Authority boundary

- **VoxMaestro:** caller-latency deadline, local response truth, and whether its own timeout is allowed to cancel a coroutine.
- **Ceinit:** authorization, stable operation identity, durable effect journal, lookup/reconciliation, and consequential retry/cancel authority.
- **Veynit:** independent effect verification.

The specimen journal is an in-memory Ceinit-shaped fixture, not a real Ceinit API.

## Residual risks

- The retained task is in-process only. Process crash/restart can still abandon it from VoxMaestro's perspective; durable recovery belongs in Ceinit.
- The policy does not prove the executor actually uses Ceinit or that its cancellation path is authorized.
- A task that never finishes remains retained in memory; production Ceinit adapters must have their own durable lifecycle and bounded resource contract.
- Event-loop shutdown can still cancel pending tasks; this slice does not make Python task lifetime durable.
- This slice does not prove provider lookup-before-retry, sink idempotency, or Veynit effect equivalence.
- PR #48's session-close lifecycle changes are separate and are not included in this branch.

Do not infer those properties from this GREEN result.
