# VM-JEV-LIVE-001 — Frozen Jev Provider-Pair Acceptance

## Status

Implemented harness. Live evidence not yet collected.

This experiment measures the Jev shadow challenger through both supported
SystemOne surfaces:

- TypeSafe native: `https://api.typesafe.ai/v1/systemone`
- Vercel TypeSafe-compatible: `https://ai-gateway.vercel.sh/typesafe/v1/systemone`

It does **not** promote Jev to routing, tool, state-transition, or effect
authority.

## Why the specimen freezes before inference

TypeSafe exposes moving aliases such as `jev-latest`. The acceptance harness
refuses moving aliases. Before the first SystemOne POST, the operator freezes:

- exact versioned model;
- exact synthetic state;
- exact question version;
- exact question definitions and criteria;
- provider order;
- repetitions per provider;
- exact VoxMaestro source commit.

The optional discovery step performs only `GET /v1/models`; it does not invoke
Jev. The resulting frozen specimen is create-only and receives a canonical
SHA-256.

## Credential boundary

Credentials are read only from environment variables:

- `TYPESAFE_API_KEY`
- `AI_GATEWAY_API_KEY`

They are never copied into the specimen, JSONL observations, reconciliation
report, or seal.

## Queue boundary

The live runner supplies `hand_off_shadow` with a real single-worker
`QueueShadowDispatcher.dispatch` function.

That changes the proof from:

> a synchronous helper exists

to:

> the helper returns after a bounded local enqueue while remote evaluation
> continues on a different worker thread.

The deterministic test holds the worker open and proves that
`hand_off_shadow(...)` returns before the worker can finish.

Provider latency is still recorded separately. It is not treated as
user-facing turn latency.

## Reconciliation

Both providers receive the same:

- pinned model;
- state bytes;
- encoded questions;
- question version.

The existing request digest therefore must match across provider pairs.

Raw values are always preserved. Semantic comparison uses only the bounded
question contract:

- Choice: selected option must match.
- Noul: both values must land on the same side of the frozen threshold.
- Score: both values must map to the same nearest frozen rubric level.

Possible terminal statuses:

- `PASS` — both providers returned valid responses, request hashes match, and
  semantic decisions agree.
- `REVIEW_DISAGREEMENT` — transport/evidence succeeded but provider decisions
  disagree semantically. Human review required; exit code 2.
- `FAIL_REQUEST_DRIFT` — the providers did not receive identical request
  semantics.
- `FAIL_PROVIDER` — one or more provider observations closed.

No status grants authority automatically.

## Evidence bundle

Each run directory is create-only and contains:

- `frozen-specimen.json`
- `typesafe.jsonl`
- `vercel-typesafe.jsonl`
- `provider-results.json`
- `reconciliation.json`
- `seal.json`

`seal.json` hashes every other evidence file and includes a canonical
`seal_sha256`. This is a content-addressed hash seal, not a signature and not
a SCITT receipt.

## Mac Mini runbook

Run from a clean checkout of the exact branch/commit under test.

First freeze the specimen. Keep it outside the Git worktree:

```bash
cd ~/voxmaestro
export TYPESAFE_API_KEY='...'

SPEC_ROOT="$HOME/voxmaestro-evidence/VM-JEV-LIVE-001"
mkdir -p "$SPEC_ROOT"

uv run python examples/run_jev_live_001.py freeze \
  --out "$SPEC_ROOT/frozen-specimen.json"
```

The command prints the selected versioned model, source commit, specimen hash,
and:

```text
systemone_post_executed=false
```

If the account cannot use metadata discovery, pass an exact versioned model
explicitly:

```bash
uv run python examples/run_jev_live_001.py freeze \
  --model jev-X.Y.Z \
  --out "$SPEC_ROOT/frozen-specimen.json"
```

Never use `jev-latest` or `jev-preview`.

Then provide the Vercel key and run the frozen pair into a new directory:

```bash
export AI_GATEWAY_API_KEY='...'

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
uv run python examples/run_jev_live_001.py run \
  --specimen "$SPEC_ROOT/frozen-specimen.json" \
  --out "$SPEC_ROOT/runs/$RUN_ID"
```

A second run must use a new output directory. Existing evidence is never
overwritten.

## Promotion boundary

A `PASS` proves only that this frozen provider-pair experiment completed
without request drift and without semantic disagreement under the measured
conditions.

It does not prove:

- production latency under representative voice load;
- provider availability;
- calibration quality on real VoxMaestro traffic;
- routing superiority over the local Reflex;
- permission to change runtime authority.

Promotion requires a separate corpus study and an explicit authority decision.
