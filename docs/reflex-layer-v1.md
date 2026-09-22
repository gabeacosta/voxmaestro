# VoxMaestro Reflex Layer v1

## Status

Experimental, shadow-only. Not admitted as routing authority.

## Purpose

The reflex layer is a bounded System-1 sensor on final ASR turns. It produces
three typed observations without mutating conversation state:

1. intent: schedule, faq, pricing, complaint, disclosure-trigger, or off-script
2. tool-needed probability in [0, 1]
3. language: en or es

The deterministic VoxMaestro runtime remains the authority for transitions,
tool execution, disclosures, handoff and effects.

## Runtime boundary

ASR final -> ReflexGate (shadow) -> existing intent/runtime path

Reflex failure is fail-neutral: timeout, malformed output, backend busy state,
or local inference failure records a fallback observation and the pre-existing
runtime path continues unchanged.

There is no hosted fallback in the live path.

## Backend contract

LocalSchemaBackend talks only to a numeric loopback /v1 endpoint backed by an
explicitly admitted schema-constrained serving engine. For v1 the only admitted
engine contract is `mlx-vlm-llguidance`; generic `mlx-lm.server` is rejected
because accepting `response_format` is not evidence that decoding is constrained.

Before classification, the backend runs a conflicting-instruction capability
probe whose required sentinel exists only in the JSON schema. Classification is
disabled until that probe passes. The model id and model hash are pinned at
construction. Inference makes one request, never retries, never follows
redirects, and exposes no tool surface.

The backend deadline is at most 150 ms. ReflexGate also enforces an outer
deadline of at most 150 ms.

## Observability

No transcript is copied into reflex telemetry. The runtime emits:

- reflex_latency_ms
- reflex_decision (1 for a usable typed proposal, 0 for fallback)

Tags include schema version, status, backend identity, SHA-256 input digest,
intent, tool-needed probability, language, pinned model identity/hash, and any
fallback reason.

These records are telemetry, not effect receipts.

## Admission

Shadow mode is mandatory first. The eval directory contains only the corpus
format; it does not claim that a real-turn corpus exists.

Do not let reflex output change routing until:

- at least 30 labelled real turns exist for development evaluation
- the critical tool-needed positive set is large enough for a meaningful
  false-downgrade bound
- p95 reflex latency stays below 150 ms under representative concurrent ASR/TTS
  load on the target Mac Mini
- malformed output, timeout, backend-busy and backend-down tests all preserve
  the existing runtime path
- model_id and model_hash are pinned in the deployed configuration

## Deliberate non-goals

- no hosted Jev/OpenRouter call in the audio path
- no state mutation from the reflex
- no tool execution from the reflex
- no plugin/factory abstraction
- no free-text reflex output


## Model admission runner

After this code is merged, run the local model under the same concurrent ASR/TTS
load expected in production. The runner refuses to produce an admission PASS
from an idle benchmark or from an operator-asserted model hash.

Example:

```bash
python -m voxmaestro.reflex.admission \
  --corpus path/to/reflex-real-turns.jsonl \
  --endpoint http://127.0.0.1:8081/v1 \
  --schema-engine mlx-vlm-llguidance \
  --model-id <local-model-id> \
  --model-path <path-to-local-model-artifact-or-directory> \
  --load-profile representative \
  --out evidence/reflex-admission/reflex-admission.json
```

A PASS currently requires all of the following:

- at least 30 real labelled turns
- at least 59 real tool-needed positives
- zero observed false downgrades on those positives
- zero reflex backend fallbacks
- p95 latency <= 150 ms
- `--load-profile representative`
- model identity hashed directly from the local artifact path

Synthetic rows are ignored for admission evidence. The report stores per-turn
digests and labels but does not copy transcripts into the evidence output.

`PASS_REFLEX_MODEL_ADMISSION` means the model cleared this evidence gate. It
does not grant routing or tool authority.


### First challenger

The first benchmark challenger is `mlx-community/Qwen3-0.6B-4bit`, not an
adopted dependency. It is small enough to justify a physical test and can be
served through `mlx_vlm.server`. Do not add it to the permanent fleet unless it
passes this admission gate under representative load.

Do not use the existing `mlx-lm.server` path for this reflex benchmark.


## VM-REFLEX-001 physical admission

The bounded physical challenger is frozen as:

- model: `mlx-community/Qwen3-0.6B-4bit`
- expected `model.safetensors` SHA-256:
  `392e8d466d56100ada00eb82031fb854297fc9e389b7d303eba3af114e87bce2`
- serving engine: `mlx-vlm-llguidance`
- no speculative decoding
- no KV-cache quantization
- sequential reflex requests
- no hosted fallback

The one-shot Mac runner does not install or download anything:

```bash
python examples/run_reflex_physical_admission.py \
  --corpus /path/to/reflex-real-turns.jsonl \
  --model-path /path/to/Qwen3-0.6B-4bit
```

It starts the pinned local model server, loads the real Pocket TTS and
faster-whisper acoustic witness, waits until that witness is resident and ready,
then runs model admission while the voice workload remains alive. The top-level
result is `TEST_INVALID` if the witness ends before the reflex benchmark,
lacks acoustic ASR evidence, or does not itself qualify.

The runner refuses to create the model download or real-turn corpus. Those are
inputs, not evidence the benchmark is allowed to manufacture.
