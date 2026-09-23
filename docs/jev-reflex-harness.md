# Jev as a Challenger Backend for the VoxMaestro Reflex Layer

**Status:** Sketch v3 — review-hardened; ready for deterministic tests
**Targets:** `src/voxmaestro/reflex/jev_backend.py` (new)
**Base:** main @ 85f4978 (post-#37)

## 1. Authority Model

```
LOCAL REFLEX                          REMOTE JEV
├── authoritative                     ├── shadow only
├── deterministic fallback            ├── no effect authority
└── always available (offline floor)  ├── compared against local, same state
                                      ├── collects latency/cost/disagreement
                                      └── any failure => UNKNOWN
```

No observation exists without its corpus record: if telemetry persistence
fails, every question returns UNKNOWN.

## 2. Latency Boundary

The user-facing turn never awaits Jev. ASR final → local reflex continues
the turn synchronously; the same request is handed to an async shadow queue.
Socket timeout is not a wall-clock guarantee; the queue boundary is.

## 3. One Codec, Two Endpoints

Per the September 21, 2026 Vercel compatibility release, AI Gateway exposes
a TypeSafe-compatible surface at `ai-gateway.vercel.sh/typesafe`, preserving
the systemOne/noul/response shapes. Therefore:

- **One SystemOne codec**, certified once
- **One `JevHttpTransport`**, differing only by endpoint + credentials
  (`typesafe` native vs `vercel-typesafe` compatibility)
- `/v1/evaluate` excluded: it repackages noul as boolean/probability and
  buys this experiment nothing

The second-adapter architecture from v2 is deleted, not extended.

## 4. Wire + Response Contract

Request: `model` (pinned, no `-latest` aliases), `state`, `questions` object
keyed by question ID (`type`/`instructions`/`criteria`).

Response: top-level `model`, `answers`, `usage` (`input_tokens`,
`output_tokens`, integers only). The returned `model` must equal the pinned
request model — mismatch is provider drift and closes the entire batch.
Decode by primitive: `choice` (must be declared option), `score` (fractional
rubric position, preserved raw), `noul` (0..1 probability = the uncertainty
signal; no separate confidence).

## 5. Hardening Invariants

- Atomic batch: any malformed/missing/type-mismatched sibling closes ALL.
- Redirect-free transport: authenticated 3xx is drift, not a Location to
  follow (`_NoRedirect` handler raises → closed batch).
- `DecisionTrace` is a fixed shape (session_id, turn_id, question_version,
  local_decision_id). Arbitrary caller metadata cannot reach telemetry.
- Canonical digest covers model + state + encoded questions; criteria edits
  change the hash. State plaintext never enters telemetry.
- No audit observer → no backend (construction refuses).

## 6. Evidence Layers (honest naming)

```
ShadowObservation  ->  JSONL telemetry  ->  corpus reconciliation  ->  sealed experiment evidence
   (per call)          (mutable file)         (offline agreement)         (receipt plane)
```

The JSONL is corpus material, not tamper-evident evidence, until something
hashes/seals it. Model drift, persistence failure, and protocol defects are
all recorded as closed batches with error strings.

## 7. Acceptance Gate (deterministic tests first)

- [x] PASS: exact pinned model returned
- [x] FAIL: returned model differs from requested → entire batch UNKNOWN
- [x] PASS: complete valid batch + observation persisted
- [x] FAIL: complete valid batch + write failure → UNKNOWN
- [x] PASS: malformed usage + valid answers → answers survive, usage=None
- [x] FAIL: malformed/missing answer → entire batch UNKNOWN
- [x] FAIL: redirect → entire batch UNKNOWN
- [x] FAIL: arbitrary data cannot enter trace
- [x] PASS: TypeSafe-native and Vercel-compatible run the same codec fixtures
- [x] FAIL: timeout / 4xx / 5xx / invalid JSON → UNKNOWN
- [x] FAIL: oversized state / duplicate keys → rejected pre-network
- [x] PASS: local turn completes without awaiting Jev (by construction:
  no sync call exists on the turn path; `hand_off_shadow` only)

Verified: 26 passed (Python 3.12, pytest 8.3).

## 8. Next Slice

Frozen live-acceptance specimen: immutable stimulus, immutable
`question_version`, provider pair (TypeSafe native + Vercel compat),
reconciliation schema, sealing criteria. Promotion study comes only after
that passes and live shadow latency stays clear of the turn budget.
Rejection with evidence is a successful outcome.
