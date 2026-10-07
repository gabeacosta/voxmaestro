# WT-VOICE-EFFECT-CLOSE-001-B — post-close secondary-effect prevention

## Disposition

**GREEN on synthetic native fixtures.** Native VoxMaestro correction is suitable for review. This does not prove live Ceinit, provider, telephony, or browser playback behavior. No external framework was adopted.

## Pinned target

- Repository: `gabeacosta/voxmaestro`
- Baseline main: `27c734c2c3ded9ce63a0b5cbf854d1a3de24b7a6`
- Fix head: `84684ec603632dae20fb38c52cb692041b45627c`
- PR: #48

## Red evidence

### Initial close-boundary specimens

GitHub Actions run `37566670482` reproduced three failures on the unmodified runtime:

1. A queued message could dispatch its tool after the browser session had already closed.
2. A tool already dispatched could complete and emit client events after close.
3. A stale result from an old session could leak after the same external session ID was reused.

The active-session positive control and explicit consumer-disconnect control passed.

### Follow-up secondary-effect specimen

After the first session-identity guard went green, a deeper fixture was added:

`test_effect_close_failed_inflight_tool_cannot_start_fallback_handoff`

GitHub Actions run `37567051166` on Python 3.12:

- **410 passed**
- **1 failed**
- failure: `handoff_effects == ['closed-tool']`, expected `[]`

Meaning: browser close suppressed output, but when the already-dispatched tool later failed, VoxMaestro still executed its configured fallback handoff. That handoff is a second consequential effect and must not start from an exited conversation.

## Native correction

### Runtime

- Added `CallSession.close()`, which moves the conversation to `CallPhase.EXITED` without cancelling already-dispatched effects.
- `RuntimeToolBridge` treats `EXITED` as monotonic:
  - does not dispatch a tool if closure occurred while filler yielded;
  - late success, timeout, or error does not restore `ACTIVE`.
- `VoxMaestroRuntime._process_turn` checks for `EXITED` after the in-flight tool returns and before evaluating fallback behavior. The turn returns `action=ignored` and cannot start a fallback handoff.

### Browser adapter

- `end` removes the session, calls `CallSession.close()`, then performs audio/backend teardown.
- A message waiting on the session lock must still own the exact registered session object before processing.
- Events produced after replacement/close are not emitted.

## Green evidence

GitHub Actions run `37568004494`:

- Python 3.10: **411 passed / 0 failed**
- Python 3.11: **411 passed / 0 failed**
- Python 3.12: **411 passed / 0 failed**
- Ruff: **PASS**

All six WT fixtures passed on all three interpreters:

1. active-session positive control;
2. close before dispatch prevents tool start;
3. close after dispatch preserves executor-owned uncertain reconciliation;
4. stale old-session result cannot leak into reused session ID;
5. explicit consumer disconnect does not cancel the dispatched effect;
6. failed in-flight tool after close cannot launch fallback handoff.

## Authority boundaries

- **VoxMaestro:** conversation lifecycle, turn liveness, client-output suppression, prevention of new conversation-derived fallback effects after closure.
- **Ceinit:** authorization, stable operation IDs, durable effect journal, sink lookup/reconciliation, and any retry/cancel decision.
- **Veynit:** independent effect verification.

The test's `operation_id` and journal are in-memory **Ceinit-shaped specimens**, not a real Ceinit API.

## Residual risks / next gates

- `asyncio.wait_for` in `RuntimeToolBridge` still imposes a local tool timeout and can cancel the executor coroutine. Whether consequential providers may be safely subjected to coroutine cancellation is a separate authority/cancellation specimen.
- This slice does not prove crash/restart recovery after `DISPATCH_STARTED`.
- This slice does not prove provider lookup-before-retry or durable idempotency.
- This slice does not cover a handoff already dispatched before browser close.
- This slice does not test bytes/events already accepted by an external transport sink.

Do not infer those properties from this GREEN result.
