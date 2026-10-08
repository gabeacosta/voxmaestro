# WT-VOICE-EFFECT-CLOSE-002

## Disposition

**GREEN — code gate.** VoxMaestro now blocks a consequential tool dispatch when
session closure wins the final async boundary before the external executor is
entered. If the executor has already started, closure does not invent
cancellation or revocation.

This specimen was originally developed as a logical stack on VoxMaestro PR #48
(`WT-VOICE-EFFECT-CLOSE-001-B`). After #48 merged, the #53-specific delta was
transplanted onto current `main` and requalified independently; see
**Current-main requalification** below.

## Question

Can a closed session start a new consequential effect in the scheduling window
between the existing lifecycle check in `RuntimeToolBridge.execute()` and the
external executor invocation scheduled through `asyncio.wait_for()`?

Required invariant:

- if close wins before executor claim: **0 newly-started effects**;
- the call remains `EXITED`;
- the result is a closed-before-dispatch failure;
- if executor entry wins first: exactly the already-started effect may finish;
- browser/session closure does not create retry, cancellation, or recovery
  authority.

## Red evidence

Specimen commit:

`b2fcc9745480da74a00e0ef497ef18fffe140d8b`

GitHub Actions:

https://github.com/gabeacosta/voxmaestro/actions/runs/37580392097

Python 3.11 reproduced the defect deterministically enough to establish the
boundary:

- **412 passed / 2 failed**;
- the close-first fixture observed
  `[("close-first", "check_availability")]` instead of zero effects;
- the repeated close-first fixture returned a successful tool result;
- the dispatch-first positive control passed;
- Ruff passed.

Therefore the prior PR #48 lifecycle check was necessary but not sufficient:
`asyncio.wait_for()` introduced a later task-entry boundary at which a session
could already be closed while the external executor had not yet begun.

## Correction

Runtime commit:

`20ae80a658188ed921d388136fda013d2779e9d9`

The correction adds one final monotonic lifecycle check at the start of
`RuntimeToolBridge._execute()`, before parameter construction and before the
external executor is invoked.

There is no `await` between that check and entry into the executor. A private
sentinel reports the close-before-dispatch outcome back to `execute()` without
fabricating success or changing authority ownership.

No retry, cancellation, durable journal, Ceinit API, provider inference, or
Veynit authority was added.

## Harness convergence

The first red fixture used `loop.call_soon(call.close)` to place closure ahead
of the task created by `asyncio.wait_for()`. That exposed the real defect on
Python 3.11, but the assumed callback/task ordering was not portable: on Python
3.12 the executor task could claim first.

Intermediate run:

https://github.com/gabeacosta/voxmaestro/actions/runs/37580520633

That run therefore remained red on Python 3.12 because the **test ordering was
scheduler-sensitive**, not because the close-before-claim invariant had been
forced and violated.

The fixture was hardened at:

`db48ba71217a468906bab6aec3724ecc69340e0c`

It now inserts a deterministic test-only gate immediately before the production
`_execute()` claim boundary. The test waits at that gate, closes the call, and
then releases execution into the real production claim check. This forces the
close-first ordering independently of CPython scheduler details.

The positive control separately forces executor-entry first, closes afterward,
and requires the one in-flight effect to complete without changing the call out
of `EXITED`.

## Green evidence

Code/test head:

`db48ba71217a468906bab6aec3724ecc69340e0c`

GitHub Actions:

https://github.com/gabeacosta/voxmaestro/actions/runs/37580770369

Results:

- Python 3.10.22: **414 passed / 0 failed**;
- Python 3.11.17: **414 passed / 0 failed**;
- Python 3.12.14: **414 passed / 0 failed**;
- Ruff: **PASS**.

The 64-iteration close-before-claim fixture emitted zero effects. The
dispatch-first control preserved exactly one already-started effect.

## Current-main requalification

Replacement PR: **#55**

Current-main base:

`858101b0dcd81820f9cd7f16bc10d22e030dd527`

Pre-addendum qualification head:

`94d238cf156105b78ac790214a6c55a5881ce7a3`

Only the #53-specific delta was carried onto current main:

- `src/voxmaestro/runtime.py` — final close-before-executor lifecycle recheck;
- `tests/test_wt_voice_effect_close_002.py` — deterministic race fixture;
- this evidence artifact.

GitHub Actions run:

`37723937031` — **PASS**

- Python 3.10 job: PASS;
- Python 3.11 job: PASS;
- Python 3.12 job: PASS;
- Ruff: PASS.

This closes the stale-stack integration risk from PR #53. It does not claim a
live browser/telephony/provider E2E and does not add Ceinit reconciliation
authority to VoxMaestro.

## Ownership boundary

- **VoxMaestro:** conversation/session lifecycle and the local decision not to
  start a tool executor after the session has already closed.
- **Ceinit:** external-effect authorization, durable operation identity,
  journaling, cancellation/retry/recovery decisions, and reconciliation after
  dispatch.
- **Veynit:** independent verification of consequential effects.

A browser/session close is not Ceinit revocation.

## Residual boundary

This is an asyncio runtime specimen, not a live browser/telephony/provider E2E.
It does not prove cancellation of an effect that already crossed the executor
boundary, nor reconciliation of irreversible effects after close.

That is intentionally the next boundary: **close versus already-dispatched
irreversible effect**.

No deployment is performed by this specimen. Merging the bounded runtime fix does not constitute live browser/telephony/provider deployment qualification.
