# WT-VOICE-TTS-001 hardware run: diagnosis (Slice 3)

Status: **BLOCKED** (root cause identified; fix is outside this session's
reach -- it requires action on the physical Mac mini runner host, which this
cloud execution container cannot access).

## What was checked

Queried the GitHub Actions API (`gabeacosta/voxmaestro`) for
`.github/workflows/wt-voice-tts-001.yml` run history:

```
run id:        34042183158
trigger:       push to main (.github/wt-voice-tts-001.trigger, 2026-09-06)
job:           qualify
required labels: [self-hosted, voice]
created_at:    2026-09-06T15:24:03Z
started_at:    2026-09-06T15:24:03Z   <- identical to created_at
completed_at:  2026-09-07T15:24:03Z   <- exactly 24h later
conclusion:    cancelled
```

Only one run exists for this workflow, ever.

## Root cause

`started_at == created_at` and the run sat for **exactly 24 hours** before
GitHub auto-cancelled it. That is GitHub Actions' hard queue timeout for a
job that never gets an eligible runner -- not a workflow bug, not a
dependency-install failure, not a benchmark-code failure. No step in the job
ever ran (no logs beyond the queued state).

This means: no self-hosted runner registered with **both** the `self-hosted`
and `voice` labels was online and polling for jobs at any point during that
24-hour window. Per the handoff's own diagnostic list (Section 5), this is
squarely **"runner offline"** or **"runner never registered"** -- the two
causes that produce exactly this signature (immediate queue, no step logs,
timeout-cancel).

## What this session could NOT determine

This cloud container has no access to:
- the Mac mini itself (to check the runner service's status/logs),
- the repository's Settings -> Actions -> Runners page (needs a browser
  session / different GitHub permission scope than the GITHUB_SHA-scoped
  Actions API this session used),
- any registration token or runner configuration.

So the exact reason the runner is offline (never installed, installed but
not started, network/firewall issue, expired registration) could not be
narrowed further than "no matching runner was ever online for this job."

## What is NOT the problem

- The workflow YAML itself is fine: correct label selector
  (`runs-on: [self-hosted, voice]`), correct trigger (push to the trigger
  file, or `workflow_dispatch`), correct artifact upload with
  `if-no-files-found: error` (so a truly empty run would fail loudly, not
  silently pass).
- `examples/run_wt_voice_tts_001.py`'s benchmark logic was not exercised by
  this cancelled run at all (it never started), so this diagnosis makes no
  claim about the benchmark code's correctness on real hardware.

## A separate, pre-existing finding: this benchmark cannot emit PASS today

Independent of runner availability, `examples/run_wt_voice_tts_001.py` (see
its own module docstring and `run_lane()`) deliberately forces
`evidence_complete=False` on every lane it produces, because it has no
acoustic session-crosstalk detector. Per `docs/wt-voice-tts-001.md`'s frozen
invariant #3 ("missing or incomplete evidence produces `TEST_INVALID`, never
`PASS`"), **every run of this benchmark -- on this container, on the real
Mac mini, anywhere -- currently adjudicates `TEST_INVALID` by design**, not
`PASS` or `FAIL`. This is correct, intentional fail-closed behavior, not a
bug, but it means restoring the runner alone does not get WT-VOICE-TTS-001 to
a `PASS` verdict; a real cross-talk detector still needs to be implemented
first. That is out of scope for this slice (Section 12 non-goal: "do not
weaken qualification gates" -- the honest fix is implementing the detector,
not loosening the invariant).

## Smallest required action (cannot be performed from this session)

1. On the Mac mini: confirm the GitHub Actions self-hosted runner service is
   installed, running, and registered with labels `self-hosted` and `voice`
   for `gabeacosta/voxmaestro` (`./svc.sh status` / `./run.sh` if running
   manually, or check the LaunchAgent/daemon if installed as a service).
2. Once online, re-fire the workflow via `workflow_dispatch` with the
   bounded first lane described in the handoff (`runs=3`, `full_matrix=false`)
   before attempting the full 16-lane matrix.
3. Separately (not blocking S3's runner fix): implement a real
   session-crosstalk detector so the benchmark can adjudicate something
   other than `TEST_INVALID`.

## Verdict

```
status = BLOCKED
reason = no self-hosted+voice-labeled runner has ever been online for this
         workflow; requires physical/administrative access to the Mac mini
         and the repository's runner settings, neither of which this cloud
         session has.
```
