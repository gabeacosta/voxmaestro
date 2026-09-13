# software-wiring-demo.json — what this is and isn't

**This is NOT real hardware evidence.** It was produced in the cloud
execution container, not the Mac mini, using a `PocketTTSBackend` bound to
a fake model (`backend_version: "software-wiring-demo-not-real-pocket-tts"`)
that bakes the requested text directly into its "audio" bytes, and a fake
transcriber that reads that text straight back — a wiring proof, not ASR.

**What it does prove**: the acoustic crosstalk detector added to
`examples/run_wt_voice_tts_001.py` (`voxmaestro.tts.crosstalk.detect_crosstalk`)
is correctly wired end to end — distinct per-session utterance assignment →
per-session transcription → crosstalk comparison → `session_crosstalk_events`
on each `TTSRunObservation` → lane aggregation → `evidence_complete` →
`qualify_tts_lane`'s real verdict. With two clean, non-crosstalking fake
sessions, `evidence_complete` is `true` (previously always forced `false`
for any `sessions > 1` lane) and the adjudicator returns a real `FAIL`
verdict — not `TEST_INVALID` — driven entirely by `realtime_factor_p95`
(expected and irrelevant here: the fake model's synthesis is near-instant
against a tiny text-derived PCM buffer, so its realtime factor is not
representative of anything real). `session_crosstalk_events: 0` here is
correctly and honestly measured, not defaulted.

The full path — real content leaking between two concurrent sessions
getting caught, and a lane with no transcriber configured correctly staying
`TEST_INVALID` — is covered by `tests/test_wt_voice_tts_executor.py`
(`test_real_content_leak_between_sessions_is_caught`,
`test_multi_session_without_transcriber_stays_evidence_incomplete`).

**To get real evidence**: run on the Mac mini with
`--use-whisper-crosstalk-check` (requires `faster-whisper`), which uses
`voxmaestro.tts.crosstalk.WhisperAcousticTranscriber` against real Pocket
TTS audio.
