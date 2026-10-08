# WT-VOICE-EFFECT-RECONCILE-001

## Disposition

**GREEN — no VoxMaestro runtime patch.**

The specimen proves the post-dispatch ownership boundary: after an irreversible
external effect has crossed the executor boundary, browser/session closure ends
conversation liveness but does not create retry, cancellation, or recovery
authority inside VoxMaestro.

Logical baseline: WT-VOICE-EFFECT-CLOSE-002 final evidence head
`0e3e2ba4de6384e995ccbf54d7ce4f6621375527`.

## Question

What happens when:

1. the executor records `DISPATCH_STARTED`;
2. the external sink applies the effect;
3. the provider acknowledgment is lost;
4. the browser/session closes before executor return;
5. the outcome becomes `EFFECT_UNKNOWN`;
6. a fresh runtime later presents the same Ceinit-owned operation identity?

Required invariant:

- closed session emits no trailing client output;
- no fallback handoff starts after close;
- VoxMaestro performs no blind retry;
- exactly one total sink effect exists;
- authoritative sink lookup converges `EFFECT_UNKNOWN` to `SINK_CONFIRMED`;
- a blind-retry negative control duplicates the sink effect, proving specimen
  sensitivity.

## Specimen

Commit:

`79ae67ae7e2fd9534a8b3e9f522faad1d6f39f04`

Added only:

`tests/test_wt_voice_effect_reconcile_001.py`

No production/runtime files changed relative to WT-VOICE-EFFECT-CLOSE-002.

The in-memory Ceinit-shaped authority is a test specimen, not a production
Ceinit implementation. It owns the synthetic journal and sink lookup.

## Observed path

First runtime:

`DISPATCH_STARTED -> sink applied -> browser close -> EFFECT_UNKNOWN`

Observed:

- trailing client events: zero;
- fallback handoff effects: zero;
- sink effects: exactly one.

Fresh runtime:

- same operation identity is presented;
- authority performs sink lookup;
- prior sink effect is recognized;
- journal records `SINK_CONFIRMED`;
- no second sink effect is emitted.

Negative control:

- two blind attempts without lookup-before-retry produce two sink effects.

## CI evidence

GitHub Actions run:

https://github.com/gabeacosta/voxmaestro/actions/runs/37581576944

Results:

- Python 3.10: **416 passed / 0 failed**
- Python 3.11: **416 passed / 0 failed**
- Python 3.12: **416 passed / 0 failed**
- Ruff: **PASS**

Both reconciliation fixtures passed on all three Python versions.

## Architecture disposition

- **VoxMaestro:** conversation/session liveness and post-close output suppression.
- **Ceinit:** durable operation identity, effect journal, authoritative sink
  lookup, lookup-before-retry, reconciliation, retry/cancel authority.
- **Veynit:** independent verification of consequential effects.

The Wind Tunnel result is therefore **do not add reconciliation machinery to
VoxMaestro**. The correct product requirement is a Ceinit acceptance invariant:
an `EFFECT_UNKNOWN` operation may never be retried blindly; authoritative sink
lookup must precede any recovery action.

## Residual risk

This is an in-memory authority specimen, not a live Ceinit + real provider E2E.
It proves the interface/ownership behavior but not provider-specific lookup
quality, sink identity derivation, crash durability, or ambiguous lookup
handling.

Those remain Ceinit-side verification targets.

No merge or deployment performed.
