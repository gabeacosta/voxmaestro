# WT-VOICE-INTENT-CAL-001 — Calibrated confidence at the L0 intent seam

Status: harness implemented; no model admitted. Synthetic runs prove wiring only.

## Finding that motivates this

Abstention already exists. `IntentWorker` and `KernelIntentClassifier` both drop
to `"unknown"` below `min_confidence`, and `"unknown"` falls through to the `*`
transition. The defect is the input to that gate: `confidence` is a number the
model *writes* into its JSON answer. Nothing makes it a probability, so
`min_confidence=0.5` is a threshold over generated text.

## Claim

Reading confidence from the model's next-token distribution over legal option
labels, then rescaling with a temperature fitted on labeled turns, yields a
confidence whose ECE is low enough that the existing abstention gate prevents
wrong transitions at an acceptable coverage cost.

## Seam (why the blast radius is one worker)

The kernel contract is unchanged: `POST /v1/intent -> {"intent", "confidence"}`.
Only what `confidence` *means* changes, and only when a calibration artifact is
supplied. Untouched: `conductor.py`, `runtime.py`, `fleet.py`,
`integrations/pipecat.py`, reflex, TTS.

Any future decision model (for example a fine-tuned typed-decision model) is a
second implementation of the same `/v1/intent` contract. It is admitted by
passing this same harness, not by changing the conductor.

## Invariants

1. Calibrated mode never parses a model-written number as confidence.
2. A `CalibrationArtifact` is pinned to one model identity; a worker started
   with a different model refuses it at construction.
3. No logprobs, empty top-k, malformed logprobs, tool calls, or raw label mass
   below `min_label_mass` → `UNKNOWN`. Labels absent from top-k are never guessed.
4. `"unknown"` as top option abstains; abstention is a policy, not a class win.
5. Capture records store `sha256(text)`, never transcripts.
6. `fit` writes an artifact only on `PASS`. Gate values are recorded in every report.

## Gate (defaults are proposals; override per run)

| Check | Default | Verdict on miss |
|---|---|---|
| eval rows scoreable | n ≥ 100 and rate ≥ 0.95 | TEST_INVALID |
| calibrated ECE | ≤ 0.08 | FAIL |
| coverage at threshold 0.7 | ≥ 0.80 | FAIL |
| selective accuracy | ≥ raw accuracy | FAIL |
| scoring p95 | ≤ 200 ms → hot path, else COLD_PATH_ONLY | placement only |

The report also gives `verbalized_confidence_ece` on the same eval rows: that
is the number this WT exists to beat.

## Run (Mac mini)

```
python examples/fit_intent_calibration.py capture --model <pinned-id> \
  --endpoint http://127.0.0.1:8081/v1 --corpus turns.jsonl --records records.jsonl
python examples/fit_intent_calibration.py fit --model <pinned-id> \
  --records records.jsonl --artifact intent-cal.json --report report.json
```

Corpus rows: `{"text", "intents": [...], "gold"}` from real call turns,
including the Spanish slice. Labels by a person, not by the model under test.

## Known unknowns

- Whether the pinned local server returns `logprobs` / `top_logprobs` on
  `/v1/chat/completions` for this model. `TEST_INVALID` is the expected
  verdict if it does not; that is a serving finding, not a calibration result.
- Letter labels cap the option set at 26 per call.
- `integrations/pipecat.py` defines its own frame classes and does not subclass
  Pipecat's `FrameProcessor`. There is no real Pipecat seam yet. Out of scope here.

## Non-goals

No conductor or state-machine change, no new routing authority, no cloud
fallback, no change to the default verbalized mode, no claim of model admission.
