# WT-TTS-003 — Golden-audio conformance gate

Status: draft (Slice 1)

## Claim

The official Pocket TTS Python backend is the behavioral reference.
A pinned corpus of texts × voices × languages is compared across
`pocket-python` vs `pocket-mlx` (and any future backend) by WER and
mel-cepstral distance. This gate is what makes the MLX lane safe to promote.

## Invariants

1. Corpus is version-pinned in-repo (or a hashed external bundle).
2. Backend swap is blocked unless WER and MCD are within agreed bounds
   vs `pocket-python` on the same quantization and sample rate.
3. French runs only on `french_24l` until a 6-layer distill exists.
4. Phrase-accumulator seam check: independent-ASR WER on re-joined chunks
   vs synthesizing the full utterance in one shot.

## Test matrix

| Case | Expect |
|---|---|
| pocket-python vs itself (sanity) | WER 0, MCD ~0 |
| pocket-mlx vs pocket-python | WER/MCD within bound |
| int8 vs fp on python backend | WER delta within noise |
| French 6l requested | rejected |
| Re-joined clause flushes vs one-shot | seam WER within bound |

## Metrics

- `tts.golden_wer`
- `tts.golden_mcd`
- `tts.seam_wer`

## Out of Slice 1

Recording the corpus and wiring CI audio jobs. Slice 1 freezes the contract
and the gate definition so backends cannot promote without it.
