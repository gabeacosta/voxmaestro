# WT-VOICE-OP-ID-PROVENANCE-001 — bind operation identity to effect meaning

## Disposition

**GREEN on native synthetic fixtures.** Stable operation identity is no longer treated as sufficient by itself: an authoritative executor-side effect-binding rejection is preserved as a block and cannot cascade into a fallback effect.

This is not a live Ceinit, provider, or Veynit integration.

## Target / dependencies

- Repository: `gabeacosta/voxmaestro`
- Main baseline: `27c734c2c3ded9ce63a0b5cbf854d1a3de24b7a6`
- Logical dependencies: PR #49 (executor-owned timeout) and PR #50 (stable operation identity)
- Fix head: `75f112ed3890659e06712b219173ba1d76c8fd21`
- PR: #51

PR #51 targets `main` for GitHub Actions execution but is logically stacked on the prior Wind Tunnel slices.

## Adversarial contract

The synthetic Ceinit-shaped executor binds each `operation_id` to an effect meaning:

- tool name;
- method;
- resource/endpoint;
- consequential params.

Required behavior:

- same operation ID + same effect -> dedupe / one sink effect;
- same operation ID + changed params -> BLOCK;
- same operation ID + changed resource -> BLOCK;
- distinct operation IDs -> distinct effects allowed.

A block must not be translated into a fallback handoff or any other second consequential effect.

## Red evidence

GitHub Actions run `37570278812`, Python 3.10, red head `ed6b74047347e5e5b962a5451f0b83d787dba0b5`:

- same ID + same effect dedupe: **PASS**
- same ID + changed params blocks without fallback: **FAIL**
- same ID + changed resource blocks without fallback: **FAIL**
- distinct IDs remain distinct: **PASS**
- aggregate: **415 passed / 2 failed**
- Ruff: **PASS**

Observed failure:
- the synthetic authority correctly rejected the binding mismatch;
- `RuntimeToolBridge` treated that rejection as an ordinary tool failure;
- configured `on_failure -> handoff` then executed a fallback handoff;
- therefore the blocked first effect caused a second consequential effect.

The fail-fast matrix cancelled other red interpreter jobs; no red verdict is claimed for them.

## Native correction

Added `ToolEffectBindingError`, an explicit executor/authority exception type.

`RuntimeToolBridge` now:
1. catches only this typed authoritative binding block separately from generic executor failures;
2. returns `RuntimeToolResult(... blocked=True)`;
3. records the block without inferring authority from error text.

`VoxMaestroRuntime._process_turn` now:
1. exposes `blocked` in tool metrics;
2. treats a blocked tool result as terminal for the requested effect;
3. sets `should_handoff=False`;
4. does not execute configured fallback handoff.

Generic executor exceptions continue to use ordinary failure behavior. Voice Maestro does not compute or own the effect binding.

## Green evidence

GitHub Actions run `37570423102`, fix head `75f112ed3890659e06712b219173ba1d76c8fd21`:

- Python 3.10: **417 passed / 0 failed**
- Python 3.11: **417 passed / 0 failed**
- Python 3.12: **417 passed / 0 failed**
- Ruff: **PASS**

All four provenance fixtures passed on all three interpreters.

## Ownership

- **VoxMaestro:** transports stable operation identity and effect inputs; preserves an explicit authoritative block; prevents a block from creating fallback effects.
- **Ceinit:** owns the operation-to-effect binding, authorization, canonical effect identity, durable journal, conflict decision, lookup/recovery and retry/cancel authority.
- **Veynit:** independently verifies emitted effects against authorized effect equivalence/budget.

The in-test binding table and sink are Ceinit-shaped fixtures only.

## Residual risks / next gates

- The specimen binding tuple is not the production Ceinit canonical effect hash/EEC.
- It does not prove cryptographic provenance, signature verification, tenant/caller binding, expiry, or challenge binding.
- It does not prove two concurrent runtimes racing the same operation ID and different effects.
- It does not prove a malicious adapter cannot falsely raise `ToolEffectBindingError`; adapter provenance/policy remains required.
- It does not prove operation ID + effect binding is durably committed before dispatch.
- It does not prove Veynit independently detects an emitted effect that differs from the authorized binding.
- It does not cover cross-language canonicalization of effect meaning.

Do not infer those properties from this GREEN result.
