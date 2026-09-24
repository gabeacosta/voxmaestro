# VM-JEV-OUTCOME-001

## Purpose

Measure whether a Jev SystemOne decision can challenge an agent's "done" claim
without giving Jev execution authority.

This is a cold-path experiment over the existing outcome-attestation seam:

```text
agent self-declaration ───────────────┐
                                     ├─ compare after both decisions
task contract + workflow evidence ─┐ │
                                   ▼ ▼
                              Jev challenger
                                   │
                                   ▼
                         OutcomeAttestation
```

The Jev request never receives `agent_claim` or `reference_done`. Those fields
exist only in the frozen comparison specimen and are joined back after Jev has
produced its attestation.

## Decision contract

Each task requirement produces exactly two typed questions:

1. `NOUL` — uncertainty that the requirement is decidable from the supplied
   workflow result and fresh evidence.
2. `CHOICE` — `satisfied | unsatisfied`, evaluated only when uncertainty is
   below the frozen threshold.

The deterministic consumer owns the consequence:

```text
Jev batch unavailable
    -> INSUFFICIENT_EVIDENCE

uncertainty >= 0.50
    -> INSUFFICIENT_EVIDENCE

uncertainty < 0.50 + choice=satisfied
    -> SATISFIED

uncertainty < 0.50 + choice=unsatisfied
    -> UNSATISFIED
```

Jev does not choose the threshold and does not decide what an outcome status
causes downstream.

## Frozen specimen

`experiments/VM-JEV-OUTCOME-001/cases.json` contains six synthetic cases:

- correct `done`;
- premature `done` with missing notification evidence;
- premature `done` with an ambiguous time window;
- conservative `not_done` despite complete evidence;
- incorrect `done` with the wrong calendar day;
- correct `not_done` with no completion evidence.

Each case carries a frozen reference label so the run measures both
agent-vs-Jev disagreement and each side against the same synthetic reference.

The reference is never included in the Jev state.

## Run

Use a clean checkout of the exact commit to be measured.

```bash
cd ~/voxmaestro
git switch main
git pull --ff-only
uv sync --extra dev

export TYPESAFE_API_KEY='...'

RUN="evidence/VM-JEV-OUTCOME-001/$(date -u +%Y%m%dT%H%M%SZ)"
uv run python examples/run_jev_outcome_001.py --out "$RUN"
```

The runner:

- rejects a dirty worktree;
- reads the authenticated TypeSafe model catalog before inference;
- selects an account-visible Jev model/alias;
- records the catalog hash;
- runs one atomic Jev batch per frozen case;
- persists raw shadow observations;
- re-reads the model catalog after the run;
- requires one stable provider-reported resolved model identity;
- writes comparison results and a content-addressed SHA-256 seal.

Possible terminal statuses:

- `MEASURED`
- `FAIL_PROVIDER`
- `FAIL_MODEL_DRIFT`
- `FAIL_MODEL_CATALOG_DRIFT`

A `MEASURED` result means the experiment produced valid comparison evidence.
It is not an adoption decision and grants no runtime authority.

## Evidence bundle

A successful run creates a new directory containing:

- `input-spec.json`
- `model-catalog-pre.json`
- `jev-observations.jsonl`
- `comparison-results.json`
- `model-catalog-post.json`
- `summary.json`
- `seal.json`

The seal is a content-addressed manifest, not a signature or SCITT receipt.

## Promotion boundary

This slice is intentionally narrow.

It does **not**:

- invoke outcome verification from `process_turn()`;
- change state-machine acceptance;
- authorize repair or retry;
- execute tools or effects;
- promote Jev into routing authority;
- tune the uncertainty threshold from the same six cases;
- claim production accuracy from synthetic evidence.

The next decision should be evidence-driven. If Jev materially catches false
completion without creating unacceptable false negatives, freeze a larger
blind corpus before considering any advisory authority. Otherwise discard the
adapter and keep the generic outcome seam.
