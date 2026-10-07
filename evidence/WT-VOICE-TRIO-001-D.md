# WT-VOICE-TRIO-001-D — Voice runtime truth and teardown races

**Disposition:** Native extraction accepted for review; no Pipecat, LiveKit, or Dograh dependency or code adopted. **PR #47 remains DRAFT; nothing merged or deployed.**

## Scope / comparison

- VoxMaestro baseline: `27c734c2c3ded9ce63a0b5cbf854d1a3de24b7a6`.
- External source-contract inspiration only:
  - Pipecat `25b4bd0e0df143bea06e64d210574480a7832ed2`: repeatable scenario evaluation.
  - LiveKit Agents `76de1759b3f1393ea813c8f55dedf3e68ff890a1`: turn and teardown race fixtures.
  - Dograh `1b6a7eda2b5f0d23c50905c3df71103ee9f6d0c8`: uncertain webhook and delivery semantics.

## Red evidence

1. **001-B / ack loss:** [GitHub Actions run 37564062617](https://github.com/gabeacosta/voxmaestro/actions/runs/37564062617): Python 3.12 `407 passed, 1 failed`. Provider effect followed by acknowledgment loss was classified as definitive `failed`, whereas the evidence permits only `unknown`.
2. **001-D / session races and customer claims:** [GitHub Actions run 37565827725](https://github.com/gabeacosta/voxmaestro/actions/runs/37565827725): Python 3.10 `411 passed, 3 failed`. The fail-fast matrix cancelled Python 3.11/3.12; no passing or failing verdict claimed for their red run. Two fixtures raised `KeyError` on a session ended between greeting yield and resumed speech; a third reproduced the false customer claim “not delivered” when the runtime's receipt status was `unknown`. Ruff succeeded.

## Native fixes

- `src/voxmaestro/runtime.py`: distinguish `HandoffNoEffectError` (explicit adapter-attested no-effect) from generic executor error `unknown`; no new retry mechanism.
- `src/voxmaestro/__init__.py`: export explicit no-effect exception for adapters.
- `src/voxmaestro/integrations/web_session.py`: capture session identity *before* yielding a greeting; verify the original session is still registered before speaking, and after synthesis before sending queued audio. Reused external IDs cannot transfer ownership to a stale generator. Unknown handoff receipts no longer generate a definitive non-delivery claim.
- `tests/test_wt_voice_trio_001.py`: 5 native handoff/acknowledgment fixtures.
- `tests/test_wt_voice_trio_001_d.py`: 6 browser/TTS truth and teardown fixtures, including 24 repeated end-before-resume scenarios, resumed stale generators after session-ID reuse, and closing during synthesis.

## Green evidence

- Intermediate: [run 37565969759](https://github.com/gabeacosta/voxmaestro/actions/runs/37565969759), Python 3.10/3.11/3.12 `414 passed` each, Ruff passed.
- Final native-test head `bb2fe9be472aeca719ed1a55088af269e98268d2`: [run 37566107139](https://github.com/gabeacosta/voxmaestro/actions/runs/37566107139).
  - Python 3.10: **416 passed / 0 failed**.
  - Python 3.11: **416 passed / 0 failed**.
  - Python 3.12: **416 passed / 0 failed**.
  - Ruff: **PASS**.
- CI assertions cover synthetic fixtures, not independent sink or browser E2E playback proof.

## Ownership and residual risks

- **VoxMaestro:** bounded conversational state, speech delivery and honest runtime classification.
- **Ceinit:** authority, stable operation IDs, durable journal and reconciliation/retry decisions. No Ceinit implementation was changed.
- **Veynit:** independent effect verification. No Veynit implementation was changed.
- `HandoffNoEffectError` represents an adapter **assertion**; it is not independent evidence that a sink had no effect. A future adapter policy gate must require authoritative provenance.
- Closing a browser session does not yet cancel or reconcile all external tool operations initiated before close; this separate effect boundary requires an explicit Ceinit-owned contract.
- Async transport sends already accepted by a sink cannot be “unsent” by an in-process turn gate; actual downstream playback cancellation must be tested separately.
- `RuntimeToolBridge` still has separate failure/timeout classification; do not generalize the handoff fix without a distinct specimen.
- No performance/latency improvement or production deployment is claimed.

## Review gate

Do not merge merely because CI is green. Reviewer must validate session ownership, error classification, adapter provenance, and user-visible messaging. No new third-party dependencies are permitted in this extraction slice.
