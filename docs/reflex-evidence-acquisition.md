# VM-REFLEX-001 Evidence Acquisition

The acquisition gate is the next step after the decision-plane and
qualification-compiler slices. It does not create labels, assert provenance, or
promote the Reflex into routing authority.

## One command

From the VoxMaestro repository on the target Mac:

```bash
python examples/run_reflex_evidence_acquisition.py \
  --model-path /path/to/Qwen3-0.6B-4bit
```

By default this reads local legacy training records from
`~/.voxmaestro/training` and writes only local acquisition artifacts under
`~/.voxmaestro/reflex/VM-REFLEX-001`.

The top-level state file never contains transcripts.

## States

### BLOCKED_INSUFFICIENT_REAL_LABELS

There are not enough unique deterministic replay candidates to meet the frozen
evidence shape.

Required shape:

- at least 30 unique replay rows;
- at least 59 unique tool-needed positive rows.

### BLOCKED_PROVENANCE_CLASSIFICATION

The local data has enough candidate replay evidence, but one or more eligible
rows are still unreviewed for provenance.

For mixed real/demo data, do **not** use the blanket
`--assert-replays-are-real-calls` flag.

The importer creates:

```text
~/.voxmaestro/reflex/VM-REFLEX-001/corpus/
  legacy-reflex-provenance-review.jsonl
```

Each row contains:

- `source_digest` — opaque stable identifier derived from the source row;
- a redacted transcript preview;
- the mapped intent/tool-needed label;
- `classification` — `real`, `demo`, or `unreviewed`.

The original call id is not persisted in the review artifact.

Review the file locally and change only the classifications you can establish:

```json
{"source_digest":"...","classification":"real", ...}
{"source_digest":"...","classification":"demo", ...}
{"source_digest":"...","classification":"unreviewed", ...}
```

Only `real` rows enter `reflex-real-turns.jsonl`. Demo and unreviewed rows
remain excluded from admission evidence.

Rerun the same acquisition command after review. If all candidates are reviewed
but the real subset does not meet the frozen evidence floor, the state becomes
`BLOCKED_INSUFFICIENT_REAL_LABELS`.

If the underlying training data changes so a reviewed digest no longer exists,
the importer returns `TEST_INVALID` rather than silently rebinding or dropping
the old provenance decision.

### Homogeneous all-real datasets

`--assert-replays-are-real-calls` remains supported only when the operator has
independently established that the **entire eligible replay set** came from real
calls. It is intentionally the wrong mode for mixed real/demo data.

### BLOCKED_RUNTIME_PREREQUISITES

The corpus is admitted locally, but the physical preflight failed. The physical
runner checks the pinned model artifact, its weight digest, required local
packages, corpus floors, and the fixed loopback port without starting the model
or voice workload.

This is a prerequisite failure, not a failed model-admission result.

### READY_FOR_PHYSICAL_ADMISSION

The corpus and physical prerequisites are ready. No model/voice admission run
has occurred yet.

To execute the bounded physical run:

```bash
python examples/run_reflex_evidence_acquisition.py \
  --model-path /path/to/Qwen3-0.6B-4bit \
  --assert-replays-are-real-calls \
  --execute
```

### PASS_REFLEX_PHYSICAL_ADMISSION

The existing physical-admission runner passed with the real voice witness
resident through the Reflex benchmark.

This remains evidence only. It does not grant routing or effect authority.

## Local artifacts

The acquisition directory contains:

- `acquisition-state.json` — transcript-free state and counts;
- `corpus/legacy-reflex-summary.json` — local corpus summary;
- `corpus/legacy-reflex-staging.jsonl` — local staging records;
- `corpus/legacy-reflex-provenance-review.jsonl` — local redacted selective
  provenance review;
- `corpus/reflex-real-turns.jsonl` — emitted only from eligible rows explicitly
  classified `real` (or from a valid homogeneous all-real assertion);
- `physical/physical-preflight.json` — non-executing runtime prerequisite
  evidence;
- `physical/model-admission.json` — model admission evidence after execution;
- `physical/physical-admission.json` — physical voice admission evidence after
  execution.

Do not commit the local corpus or transcript-bearing staging files.
