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

### BLOCKED_OPERATOR_PROVENANCE_ASSERTION

The local data has enough candidate evidence, but the operator has not asserted
that the eligible `bland_replay` rows came from real calls.

Counts do not substitute for this assertion.

After independently verifying provenance, rerun with:

```bash
python examples/run_reflex_evidence_acquisition.py \
  --model-path /path/to/Qwen3-0.6B-4bit \
  --assert-replays-are-real-calls
```

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
- `corpus/legacy-reflex-staging.jsonl` — local staging/review records;
- `corpus/reflex-real-turns.jsonl` — emitted only from explicitly asserted
  eligible real replays;
- `physical/physical-preflight.json` — non-executing runtime prerequisite
  evidence;
- `physical/model-admission.json` — model admission evidence after execution;
- `physical/physical-admission.json` — physical voice admission evidence after
  execution.

Do not commit the local corpus or transcript-bearing staging files.
