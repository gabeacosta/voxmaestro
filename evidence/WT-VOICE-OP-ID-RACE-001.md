# WT-VOICE-OP-ID-RACE-001 — concurrent operation identity binding

## Disposition

**GREEN, NO VOXMAESTRO RUNTIME CHANGE REQUIRED.**

The current VoxMaestro contract correctly preserves an atomic executor-side binding winner/block under concurrent runtimes. The concurrency primitive itself belongs in Ceinit. Adding a second binding registry or lock inside VoxMaestro would duplicate authority and violate the architecture boundary.

## Target / dependencies

- Repository: `gabeacosta/voxmaestro`
- Logical dependencies: PR #49, PR #50, PR #51
- Test head: `7dc226198ed0e6238de3254f3519792fa644ed1d`
- PR: #52

## Race contract

Two fresh `VoxMaestroRuntime` instances concurrently submit the same `operation_id`.

The synthetic Ceinit-shaped authority atomically binds that identity to effect meaning:

- tool name;
- method;
- resource/endpoint;
- consequential params.

Required outcome for conflicting effects:

1. exactly one binding winner;
2. exactly one authoritative `ToolEffectBindingError` block;
3. exactly one sink effect;
4. zero fallback effects.

Required outcome for identical replay:

1. both callers may resolve successfully;
2. exactly one sink effect.

## Positive concurrency fixtures

### Same ID, different params

Two runtimes race `op-race` with Thursday vs Friday.

Observed:
- one success;
- one blocked result;
- one binding;
- one sink effect;
- zero handoffs.

### Same ID, same effect

Two runtimes race `op-same` with identical effect meaning.

Observed:
- both resolve successfully through send + dedupe;
- one binding;
- one sink effect.

### Same ID, different resource

Two runtimes race `op-resource-race` against distinct endpoints.

Observed:
- one success;
- one blocked result;
- one sink effect;
- zero handoffs.

## Sensitivity / negative control

A deliberately non-atomic authority performs:

`read binding -> yield -> write binding`

Both competing runtimes can observe the operation ID as unbound and both emit.

Observed and asserted:
- **two sink effects**.

This proves the specimen is capable of detecting the exact race that the atomic authority is intended to prevent. A block-everything implementation would not satisfy the positive controls, and a check-then-set registry fails the negative control boundary.

## CI evidence

GitHub Actions run `37571952525`:

- Python 3.10: **421 passed / 0 failed**
- Python 3.11: **421 passed / 0 failed**
- Python 3.12: **421 passed / 0 failed**
- Ruff: **PASS**

All four WT race fixtures passed on all three interpreters.

## Architecture conclusion

### VoxMaestro

Current responsibilities are sufficient:
- forward stable operation identity;
- forward effect inputs;
- preserve typed authoritative block;
- never translate the block into fallback execution.

**No local operation registry, mutex, distributed lock, or dedupe store should be added.**

### Ceinit

Must provide the production equivalent of the atomic authority:
- durable operation-to-effect binding;
- atomic compare/create/admission for a previously unseen operation ID;
- deterministic equality against canonical effect meaning;
- conflict -> authoritative block;
- one winning `DISPATCH_STARTED` path;
- lookup-before-retry/recovery.

For a distributed deployment, an in-memory `asyncio.Lock` is only a test oracle. Production admission must be atomic at the durable authority boundary, e.g. transactional uniqueness/CAS semantics around the operation identity and canonical effect binding.

### Veynit

Remains independent verification. It should observe/verify that emitted effects do not exceed the authorized effect class/budget; it is not the concurrency lock.

## Adoption decision

- **VoxMaestro:** reference/contract-only; no runtime change.
- **Ceinit:** adopt as an explicit concurrency acceptance requirement.
- **Veynit:** reference as downstream verification specimen.

## Residual gates

- This is in-process concurrency between two runtime objects, not separate OS processes or hosts.
- The atomic authority is synthetic and memory-backed, not the real Ceinit journal/database.
- It does not test transaction isolation, database failover, split brain, network partition, or multi-region writers.
- It does not test canonical effect hash portability across languages.
- It does not test crash exactly between atomic binding and `DISPATCH_STARTED`.
- It does not test whether the sink itself honors an idempotency key.
- It does not independently verify the emitted effect with Veynit.

The next meaningful concurrency gate belongs against Ceinit's actual durable effect journal, not as another VoxMaestro patch.
