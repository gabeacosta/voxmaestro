# WT-VOICE-TOOL-RECOVERY-001 — stable effect identity across runtime restart

## Disposition

**GREEN on a synthetic recovery specimen.** VoxMaestro now has the minimum identity boundary required for a Ceinit-owned recovery path. This is not a live Ceinit integration, literal OS process-kill test, provider E2E, or proof of durable recovery.

## Dependency / target

- Repository: `gabeacosta/voxmaestro`
- Main baseline: `27c734c2c3ded9ce63a0b5cbf854d1a3de24b7a6`
- Logical dependency: WT-VOICE-TOOL-TIMEOUT-001 / PR #49
- Recovery fix head: `d332d738a1e0312916f75bca8216330ac23fd3df`
- PR: #50

PR #50 targets `main` because the repository CI workflow only executes pull-request jobs for a `main` base. Its recovery contract is logically stacked on PR #49.

## Red evidence

GitHub Actions run `37569692383`, Python 3.11, red-first branch `fb3062deb4d823909b753ef214fd7872fac86438`:

- `test_recovery_operation_id_is_forwarded_to_executor`: **FAIL**
- `test_recovery_missing_operation_id_fails_closed_before_dispatch`: **FAIL**
- `test_recovery_restart_same_operation_id_does_not_repeat_sink_effect`: **FAIL**
- `test_recovery_distinct_operation_ids_remain_distinct_effects`: **FAIL**
- Ruff: **PASS**

Observed failure contract:
- executor params did not contain `operation_id`;
- a configured recovery-owned tool still dispatched when the stable operation ID was missing;
- therefore a fresh runtime had no stable executor-facing identity on which a durable authority could perform lookup-before-retry.

The red fail-fast matrix cancelled Python 3.10/3.12 after Python 3.11 failed; no red verdict is claimed for those cancelled jobs.

## Native correction

A tool may declare:

```yaml
operation_id_from_context: operation_id
```

For such a tool, `RuntimeToolBridge` now:

1. resolves the configured metadata key before filler or external dispatch;
2. requires a non-empty string;
3. fails closed if it is missing or invalid;
4. forwards the value unchanged as `params["operation_id"]`.

VoxMaestro does **not** create a journal, inspect sink state, retry an effect, dedupe a provider operation, or decide recovery.

## Recovery specimen

The test uses a synthetic Ceinit-shaped journal and sink that survive two separate `VoxMaestroRuntime` objects.

First runtime:
1. receives stable `op-recover-1`;
2. executor writes `DISPATCH_STARTED`;
3. executor applies one sink effect;
4. simulated connection/process loss happens before the journal outcome is committed.

Fresh runtime:
1. receives the same stable `op-recover-1`;
2. VoxMaestro forwards it unchanged;
3. executor observes prior `DISPATCH_STARTED`;
4. executor performs authoritative synthetic sink lookup;
5. sink confirms the operation already happened;
6. executor records `SINK_CONFIRMED`;
7. no second sink effect is emitted.

Final assertion: exactly one synthetic external effect.

A separate control proves `op-a` and `op-b` remain distinct effects.

## Green evidence

GitHub Actions run `37569782814`, fix head `d332d738a1e0312916f75bca8216330ac23fd3df`:

- Python 3.10: **413 passed / 0 failed**
- Python 3.11: **413 passed / 0 failed**
- Python 3.12: **413 passed / 0 failed**
- Ruff: **PASS**

All four WT recovery fixtures passed on all three interpreters.

## Ownership

- **VoxMaestro:** requires and forwards stable external operation identity for configured recovery-owned tools.
- **Ceinit:** creates/owns the operation identity, authorization, durable `DISPATCH_STARTED`/outcome journal, sink lookup, dedupe, recovery disposition, retry/cancel authority.
- **Veynit:** independently verifies consequential effects.

The in-test journal and sink are **Ceinit-shaped fixtures only**.

## Residual risks / next gates

- This test models restart with fresh runtime objects; it does not kill/restart an OS process.
- The operation ID is supplied through runtime context metadata. This slice does not prove its provenance, signature, tenant binding, caller binding, or unforgeability.
- It does not prove Ceinit persists the ID before dispatch or that a provider supports authoritative lookup.
- It does not prove recovery from `DISPATCH_STARTED` when sink lookup returns no result; Ceinit doctrine requires `EFFECT_UNKNOWN`, never blind retry for non-idempotent sinks.
- It does not bind the operation ID to an authorized effect hash/EEC.
- It does not test two concurrent runtimes racing the same operation ID.
- It does not prove Veynit sees exactly one emitted effect.

Do not infer those properties from this GREEN result.
