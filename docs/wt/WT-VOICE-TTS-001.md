# WT-VOICE-TTS-001 — Concurrent-session ceiling

Status: draft (Slice 1)

## Claim

Official Pocket TTS int8 is a **density** feature, not a speed feature.
Dynamic int8 on FlowLM attention and feedforward (float32 retained where
precision matters) measured:

| Metric | fp32 | int8 |
|---|---|---|
| Runtime memory | ~450MB | ~234MB (~48% reduction) |
| x86 throughput | baseline | ~27% faster |
| ARM throughput | baseline | ~23% faster |
| WER delta | — | indistinguishable from noise |

On a Mac mini, 234MB vs 450MB per replica is what raises the concurrent-session
ceiling — more than the 23% ARM speedup.

## Invariants

1. Session capacity is computed from probed `runtime_memory_bytes`, not README.
2. One replica serves one concurrent synthesis (plus reserved headroom for ASR/VAD).
3. French 24l replicas are budgeted separately (slow lane, higher compute).

## Test matrix

| Case | Expect |
|---|---|
| Probe int8 replica RSS | within 15% of 234MB on reference hardware |
| Probe fp replica RSS | within 15% of 450MB |
| Max sessions = floor(usable_ram / replica_rss) - 1 | advertised ceiling matches |
| WER vs fp on pinned corpus | delta within noise of WT-TTS-003 |

## Metrics

- `tts.replica_rss_bytes`
- `tts.sessions_in_flight`
- `tts.sessions_rejected_capacity`
