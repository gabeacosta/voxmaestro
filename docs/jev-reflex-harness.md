# Jev as a Challenger Backend for the VoxMaestro Reflex Layer

**Status:** Sketch v3 — deterministic contract implemented; live transport acceptance pending
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

The user-facing turn must never await Jev. This adapter is not wired into the
live turn path.

`hand_off_shadow` delegates synchronously to the supplied dispatch callable.
Therefore it is non-blocking only when that callable is itself a non-blocking
enqueue operation. A production queue handoff is still pending and must be
proven before live shadow wiring.

`timeout_s` is passed to the urllib opener, but it is not a complete wall-clock
deadline and does not independently bound DNS resolution. Live acceptance must
measure end-to-end shadow latency outside the user-facing turn path.

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
- [x] PASS: provider factory maps TypeSafe-native and Vercel-compatible to
  the intended endpoints while using the same SystemOne codec
- [x] FAIL: unknown provider is rejected before network access
- [x] FAIL: oversized state / duplicate keys → rejected pre-network
- [ ] PENDING: redirect rejection through the full HTTP transport path
  (the redirect handler itself has deterministic unit coverage)
- [ ] PENDING: explicit test that caller-supplied arbitrary metadata cannot
  enter trace/telemetry; the current `DecisionTrace` shape is fixed but that
  boundary is not yet independently exercised
- [ ] PENDING: direct `JevHttpTransport` coverage for HTTP 4xx/5xx,
  unreachable transport, timeout, invalid JSON, and non-mapping bodies
  (backend closure is covered with `FakeTransport`)
- [x] IMPLEMENTED/DETERMINISTIC: `VM-JEV-LIVE-001` supplies
  `hand_off_shadow` with a queue-backed dispatcher and tests that the helper
  returns before remote worker completion. Live provider evidence is still
  pending.
- [ ] PENDING: live wall-clock latency characterization, including DNS and
  connection establishment outside the turn-critical path

Do not promote the live-acceptance slice from these checklist statements alone.
GitHub CI is the deterministic code gate; physical/live evidence is separate.

## 8. Next Slice

`VM-JEV-LIVE-001` now implements the frozen specimen, provider pair,
queue-backed dispatch, reconciliation schema, and content-addressed evidence
seal. See [jev-live-001.md](jev-live-001.md).

The remaining step is physical live execution on the Mac Mini with both
credentials present. Promotion study comes only after that evidence exists.
Rejection or disagreement with evidence is a successful experimental outcome.
