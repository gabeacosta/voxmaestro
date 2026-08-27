# WT-TTS-001 — Server-side turn invalidation

Status: draft (Slice 1)

## Claim

Tag every chunk `(turn_id, seq)` at generation. The WebSocket writer drops
anything where `turn_id != current_turn`. Browser FLUSH handles playback;
the writer check kills the in-flight race.

## Invariants

1. `AudioChunk.turn_id` and `AudioChunk.seq` are required.
2. Pocket TTS `synthesize()` is a blocking generator and MUST run in `TTSWorker`
   (thread + queue). Never iterate it on the asyncio loop that runs VAD.
3. Cancellation is cooperative: `cancel(turn_id)`, drain the queue, rely on
   `WriterGate` for stragglers.
4. Phrase accumulator flushes on clause/punctuation boundaries, not raw word
   counts. Independent-ASR WER on re-joined audio is a WT metric (prosody seams).

## Test matrix

| Case | Expect |
|---|---|
| Chunk missing turn_id | construct fails |
| Barge-in: new turn starts while old generator still yields | old chunks dropped |
| WriterGate current_turn is None | all chunks dropped |
| seq monotonic per turn | 0..n, last has is_last |
| Blocking synthesize does not run on the event loop | worker thread name `tts-worker-*` |

## Metrics

- `tts.chunks_emitted`
- `tts.chunks_dropped_stale_turn`
- `tts.cancel_to_silence_ms`
- `tts.rejoined_asr_wer` (seam check)
