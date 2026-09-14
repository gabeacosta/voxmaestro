# Mac Mini Runbook — Voice Maestro First Slices

Everything in this repo that required real audio hardware, a real ASR/TTS
model, or a real self-hosted CI runner was left BLOCKED from the cloud
session that did the rest of this work (see `EXECUTION_STATUS.md` and
`FIRST_SLICE_REPORT.md`). That cloud sandbox's network egress policy also
blocks every model-weight registry it tried (HuggingFace, Ollama,
ModelScope — all rejected identically), so nothing acoustic could be
downloaded there either. This runbook is what's left to do on the actual
Mac mini M4/16GB.

Work through it in order. Each step says what to run, what "done" looks
like, and what to do if it doesn't.

---

## 0. Prerequisites

- macOS on the target Mac mini, with admin access to install a
  launchd-managed background service.
- `git`, Python 3.12 (the CI workflow pins this; a local 3.10+ works for
  manual runs per `pyproject.toml`), and `pip`/`uv`.
- Repo admin access to `gabeacosta/voxmaestro` on GitHub (needed once, to
  generate a runner registration token).
- A working microphone and speakers/headphones attached to the Mac mini,
  and a browser (for the real voice-loop test in step 3).

Clone and check out the branch this work landed on:

```bash
git clone https://github.com/gabeacosta/voxmaestro.git
cd voxmaestro
git fetch origin
git checkout claude/voice-maestro-first-slices-8csv4b   # or main, once merged
git rev-parse HEAD
```

---

## 1. Baseline sanity check (repeat of Slice 1, for real this time)

```bash
uv sync --extra dev
uv run pytest -q
```

Expect all tests green — **155 passing** as of this branch (131 baseline +
11 Bonsai worker + 2 PocketTTSBackend regression + 11 crosstalk detector).
If this doesn't pass on a clean checkout, stop and fix it before going
further; nothing downstream is trustworthy otherwise.

---

## 2. Bring the self-hosted GitHub Actions runner online

This is the actual blocker from Slice 3. The workflow
(`.github/workflows/wt-voice-tts-001.yml`) requires a runner labeled
**both** `self-hosted` and `voice`. Confirmed via the GitHub Actions API:
the one run that ever tried to use this workflow queued for exactly 24
hours and was auto-cancelled by GitHub with no step ever executing — no
matching runner has ever been online.

1. On GitHub: `gabeacosta/voxmaestro` → **Settings → Actions → Runners →
   New self-hosted runner** → macOS → follow the generated `config.sh`
   command (it embeds a short-lived registration token — generate it fresh
   when you do this, don't reuse an old one from documentation).
2. When prompted for labels, add **both** `self-hosted` and `voice`
   (the default `self-hosted` label is added automatically; add `voice`
   explicitly).
3. Install it as a persistent service rather than running it in a
   foreground terminal, so it survives reboots and logout:
   ```bash
   ./svc.sh install
   ./svc.sh start
   ./svc.sh status   # should report the service is running
   ```
4. Confirm it's actually visible to GitHub: **Settings → Actions →
   Runners** should show it as "Idle", not offline.

If `svc.sh status` says running but GitHub still shows it offline, check
the runner's own logs (`_diag/` folder in the runner's install directory)
for a network/firewall issue reaching `github.com` and
`*.actions.githubusercontent.com`.

---

## 3. Real physical voice loop (Slice 2, for real this time)

The cloud session could only prove this with a fake ASR/TTS model over
synthetic PCM (`examples/voice_loop_slice2.py`,
`evidence/first-slice/pocket_voice_demo.txt`). Real proof needs real audio:

```bash
uv pip install websockets pocket-tts faster-whisper
uv run python examples/serve_gateway.py --voice --mic
```

This should print `listening on ws://127.0.0.1:7780`. Point a browser
client at that WebSocket (the repo's frontend, if one exists, or a minimal
test page) and exercise the same scenarios the original handoff asked for:

1. a normal English utterance,
2. a normal Spanish utterance (Whisper's shipping languages are en/es —
   see `voxmaestro.asr.SHIPPING_ASR_LANGUAGES`),
3. interrupting the assistant mid-response (barge-in) and confirming the
   audio actually stops, with no stale audio continuing after,
4. a second utterance right after the interruption, to confirm ASR/turn
   cycling resumes cleanly,
5. one route that requires a model-generated response (not just a tool
   result),
6. one reproducible dependency failure (e.g. kill the `check_availability`
   endpoint mid-call) and confirm the failure is surfaced honestly, never
   reported as a fabricated success.

**Known fixed issue**: an earlier version of `serve_gateway.py` had a
mic/ASR sample-rate mismatch (gateway defaulted to 24000Hz, matching TTS
output, while `WhisperASRBackend` expects 16000Hz) that would have crashed
on the very first real mic frame. Already fixed on this branch — if you're
running from an older checkout, pull latest first.

**Known real, still-open finding**: `PocketTTSBackend._cancel` used to
permanently silence a reused `turn_id` (e.g. every session's `"greeting"`)
after the second session used it — also already fixed on this branch, with
regression tests. If you see a session's greeting go completely silent
with no error on a long-running server, that's the symptom; confirm you're
on a checkout with the fix (`git log --oneline -- src/voxmaestro/tts/worker.py`
should show the fix commit).

Capture whatever the original handoff's evidence format expects (transcript
of ASR finals, turn transitions, generation results, TTS start/first-audio,
barge-in event, cancellation, resume) the same way
`evidence/first-slice/pocket_voice_demo.txt` does for the software version,
so there's a real artifact to point at.

---

## 4. WT-VOICE-TTS-001 hardware qualification (Slice 3, for real)

### 4a. Bounded first lane

Once the runner is online (step 2), trigger the workflow with the smallest
useful lane before the full matrix:

- GitHub UI: **Actions → wt-voice-tts-001 → Run workflow** → `runs: 3`,
  `full_matrix: false`.
- Or via the `gh` CLI / GitHub API `workflow_dispatch` if you have it
  configured locally.

This runs:
```bash
python examples/run_wt_voice_tts_001.py --language en --sessions 1 --runs 3 --quantize
```

`sessions=1` has nothing to cross-talk into, so **this one lane now
produces a real `PASS`/`FAIL` verdict on its own** — no crosstalk flag
needed. Check the uploaded artifact
(`wt-voice-tts-001-<sha>` under the workflow run) for
`evidence/wt-voice-tts-001/pocket-int8-en-s1.json` and confirm
`"qualification": {"verdict": "PASS"}` (or a `FAIL` with findings you can
explain — either is real evidence; `TEST_INVALID` here would mean
something is still wrong with the setup, since `sessions=1` shouldn't hit
that path).

If this bounded run doesn't complete, diagnose before touching the full
matrix — same rule as the original handoff: fix the smallest cause
(dependency install, runner resource limits, pocket-tts model download)
rather than jumping to a bigger run.

### 4b. Full matrix, with real acoustic crosstalk evidence

Once the bounded lane is clean, the CI workflow's full-matrix path already
does the right thing (this branch added it): for every `sessions > 1` lane
it installs `faster-whisper` and passes `--use-whisper-crosstalk-check`
automatically. Trigger it with `full_matrix: true`.

Manually, the equivalent for one lane is:
```bash
python examples/run_wt_voice_tts_001.py \
  --language en --sessions 4 --runs 3 --quantize --use-whisper-crosstalk-check
```

What this actually checks now (`voxmaestro.tts.crosstalk`): each of the 4
concurrent sessions in a run gets a **distinct** corpus utterance
(`docs/wt/tts_003_corpus.yaml` has 6 per language — at `sessions=8` two
sessions per run unavoidably share a line, which weakens but doesn't
eliminate detection for that pair only), each session's captured audio is
transcribed with `faster-whisper`, and a session is flagged only when its
transcript demonstrably matches a *different* session's assigned line
better than its own — never from turn_id/handle bookkeeping alone (that
approach was considered and explicitly rejected in this repo already).

Check the emitted evidence JSON's `acoustic_crosstalk_measured` (should be
`true`) and `lane.evidence_complete` (should be `true` if every transcript
came back conclusive). If `evidence_complete` is `false` for a
`sessions>1` lane, either the flag wasn't passed or `faster-whisper`'s
transcript for at least one session was inconclusive (garbled audio, wrong
language) — check `acoustic_crosstalk_findings` in the JSON for which
session and why (`own_similarity` / `best_other_similarity` both low means
inconclusive, not a crosstalk verdict).

A cloud-sandbox software-only version of this same wiring (fake model,
fake transcriber) is at `evidence/wt-voice-tts-001/software-wiring-demo.json`
— useful as a reference for what the JSON shape looks like, **not** as
real evidence.

### 4c. Preserve evidence

Per the original handoff, keep everything under `evidence/wt-voice-tts-001/`:
commit, runner identity, platform, per-run measurements, aggregation, and
verdict. The workflow already uploads it as a build artifact
(`if-no-files-found: error`, so a silently-empty run fails loudly); pull
that artifact down and commit the JSON files into the repo's
`evidence/wt-voice-tts-001/` directory so they're not lost when the
artifact retention window expires.

---

## 5. First native low-bit (Bonsai) worker (Slice 4, for real)

The cloud session built and fully tested the **contract and wiring** for
this (`src/voxmaestro/workers/bonsai_worker.py`,
`tests/test_bonsai_worker.py`,
`examples/bonsai_worker_end_to_end.py`) — that part needs no further work.
What's missing is a real native low-bit model behind it; nothing else
changes when you add one.

1. Get an actual Binary Bonsai or Ternary Bonsai runtime running on the Mac
   mini (native binary, or MLX). This is outside what any of this repo's
   code can do for you — it's a model/runtime acquisition step.
2. Implement `voxmaestro.workers.bonsai_worker.InferenceBackend`:
   ```python
   class RealBonsaiBackend:
       def generate(self, text: str, context: dict, limits: dict) -> str:
           # call the real native runtime here; raise on failure, never
           # fabricate a response.
           ...
   ```
3. Pass it into `BonsaiWorkerServer(worker_id=..., slot="l1_worker", backend=RealBonsaiBackend())`
   in place of the default `EchoInferenceBackend()` — see
   `examples/bonsai_worker_service.py` for the exact wiring.
4. Re-run `examples/bonsai_worker_end_to_end.py` (or the real
   `serve_gateway.py` flow with `examples/microscroll_landing_bonsai.yaml`)
   and confirm real generated text comes back through the existing,
   unmodified `RemoteWorkerGenerationAdapter` — nothing in `fleet.py` or
   the state machine needs to change.

Do not go further than one admitted worker (Section 12 non-goal: no
model-to-model traffic, no recursive spawning, no model choosing another
model). Section 11's admission test (grounded Q&A, hallucination check,
latency, peak memory, cancellation behavior, malformed-response handling)
is the right bar before calling this worker "admitted."

---

## 6. Update the status docs

Once each step above produces real evidence, update `EXECUTION_STATUS.md`
and `FIRST_SLICE_REPORT.md` in place — change `BLOCKED` to `VERIFIED` (or
`FAILED`, honestly, if something doesn't work) and point at the real
evidence files, the same way this document's predecessor sections did for
the software-only proofs. Don't leave both a "BLOCKED, needs Mac mini" note
and real hardware evidence in the same file — supersede the blocked note.

---

## Quick reference: commands in order

```bash
# 0-1
git checkout claude/voice-maestro-first-slices-8csv4b
uv sync --extra dev && uv run pytest -q

# 2 (after registering + starting the runner via GitHub UI)
./svc.sh status

# 3
uv pip install websockets pocket-tts faster-whisper
uv run python examples/serve_gateway.py --voice --mic

# 4a (via GitHub UI: Actions -> wt-voice-tts-001 -> Run workflow, full_matrix=false)
# 4b (via GitHub UI: full_matrix=true) or manually:
python examples/run_wt_voice_tts_001.py --language en --sessions 4 --runs 3 --quantize --use-whisper-crosstalk-check
```
