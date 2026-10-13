# WT-VOICESTUDIO-001-B — physical voice engine qualification

**Date:** 2026-10-08

**Source reference:** debpalash/VoiceStudio @ 06c6e077f0fc35149efefc3561e9be5ae835d916 (0.5.7).
**VoxMaestro base:** 29b0b237d42b6869909592a9bf9db30721c29c1c.
**Scope:** voice/audio qualification only; no VoiceStudio code import, AGPL dependency, Ceinit/Veynit change, or production deployment.

## Preflight
A self-hosted runner with labels 'self-hosted' and 'voice' must actually be the M4/16GB Mac mini. It must already have Python 3, PyYAML, PocketTTS, VoiceStudio 0.5.7 on loopback port 3900, and prewarmed English/Spanish engine weights. The workflow does not install dependencies or download models. Before admitting the workflow, explicitly set VOICESTUDIO_MODEL_PREWARMED=1 on the runner after *both* language paths are ready. Do not commit secrets, voices, or model assets.

## Specimen and measurements
One warmup then three repetitions per engine/language, sequentially, using frozen docs/wt/tts_003_corpus.yaml text. VoiceStudio uses its 'omnivoice' engine and an 'alloy' alias through /v1/audio/speech; Pocket uses native int8 PocketTTS 'alba'. Each run captures a real decoded WAV or float32 PCM, SHA-256 of resulting audio, independent audio duration, warm full-response wall time and RTF, with p95. The comparison reader requires exactly matching host, corpus, and utterance IDs.

**Important:** The two voices are not timbre-matched. Complete-WAV time is *not* streaming time-to-first-audio. This run does not establish browser playback, acoustic intelligibility, interruption/cancel-to-silence, real cross-session crosstalk, peak memory, installed VoiceStudio build provenance, or production readiness. Output classification is OBSERVED_NOT_PROMOTABLE. Missing hardware or one invalid run must be TEST_INVALID, never PASS.

## Manual host reproduction
Run from an existing checkout with prerequisites already installed, without automatically downloading model weights:

    export PYTHONPATH=src
    python3 examples/run_wt_voicestudio_001_b.py --backend voicestudio --language en --voice alloy --model omnivoice --out evidence/wt-voicestudio-001-b/voicestudio-en.json
    python3 examples/run_wt_voicestudio_001_b.py --backend pocket --language en --voice alba --out evidence/wt-voicestudio-001-b/pocket-en.json
    python3 examples/summarize_wt_voicestudio_001_b.py --vs evidence/wt-voicestudio-001-b/voicestudio-en.json --pocket evidence/wt-voicestudio-001-b/pocket-en.json --out evidence/wt-voicestudio-001-b/comparison-en.json

Repeat for es. Do not merge or promote an engine based solely on timing. Use the uploaded CI run artifacts for raw evidence, and preserve source/version attestations separately.

## Status
Software runner and admission tests: see CI. Real physical audio: OPEN pending matching Mac runner and verified results. Feature-branch trigger avoids touching main.
