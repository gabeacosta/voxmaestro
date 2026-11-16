# Owner Acceptance Packet — VOX-OPHUB-CONVERGENCE-004

## Verdict

**VERIFIED (local read-only voice → real Operator Hub state) — DRAFT PR ONLY. NOT MERGED. NOT DEPLOYED.**

A spoken (and typed) owner question retrieves live `GET /api/v1/owner-workspace` state and produces an audible, evidence-grounded Kokoro answer. Unknown stays unknown. No second control plane. No autonomous dispatch.

## Inspected baseline (re-verified this run)

| Artifact | State |
|---|---|
| VoxMaestro PR [#66](https://github.com/gabeacosta/voxmaestro/pull/66) | Open draft `feat/voice-reliability-002` — reliability + 20/20 endurance (prior) |
| Operator Hub PR [#99](https://github.com/gabeacosta/operator-hub/pull/99) | Open draft `feature/ophub-owner-workspace-convergence-002` — `/owner-workspace` + `/api/v1/owner-workspace` |
| Traycer absorption | Draft PR [#101](https://github.com/gabeacosta/operator-hub/pull/101) `feature/ophub-traycer-absorption-001` — **separate**; not required for this voice slice |
| Owner gateway `:7780` | Left running (PID 21870); not restarted |

## Contract

- **Source of truth for spoken answers:** Operator Hub `operator_hub.owner_workspace.v1` via existing read-only GET.
- **ICM:** Remains canonical organizational context; this slice does not write ICM.
- **Ceinit:** Authority stays external; voice never mints grants or redeems live dispatch.
- **Client:** `voxmaestro.ophub_voice` (classify → fetch → ground → speak).

## Definition of done

| Criterion | State | Evidence |
|---|---|---|
| Real question → real OpHub state → audible answer | **VERIFIED** | `physical_ophub_voice_receipt.json` (5/5 OK + Kokoro TTS) |
| Works when agents unavailable / unknown | **VERIFIED** | Answers speak UNKNOWN; disconnected test in `tests/test_ophub_voice_contract.py` |
| Authority boundaries intact | **VERIFIED** | Governance intents refuse merge/deploy; POST not used; `:7780` untouched |
| Tests before impl | **VERIFIED** | 10 contract tests + governance regression (18 passed combined subset) |

## Measured evidence

| Query | Intent | Status | Answer latency | Kokoro TTFA |
|---|---|---|---|---|
| Which agents are running? | agents_running | OK | ~19 ms | ~7581 ms |
| What is Codex working on? | codex_task | OK | ~10 ms | ~2493 ms |
| Show my active tasks. | active_tasks | OK | ~5 ms | ~2730 ms |
| Which agents are blocked? | agents_blocked | OK | ~8 ms | ~4187 ms |
| What has been independently verified? | independently_verified | OK | ~22 ms | ~3131 ms |

Screenshots: `screenshots/ophub-voice-ui-*.png`, `screenshots/owner-workspace-*.png`.

Offline fixture path also PASS (`offline_ophub_voice_receipt.json`) — marked STALE by design for fixed `fetched_at`.

## Grounding rules (enforced)

- Never claim an agent is running without `independently_observed_session=true` and an observed running state.
- Codex/active tasks → UNKNOWN without session evidence.
- Independently verified → UNKNOWN unless independent GitHub observation is present.
- Merge/deploy/dispatch phrases → explicit refusal.

## Candidates

| Repo | Candidate |
|---|---|
| `gabeacosta/voxmaestro` | Branch `feat/vox-ophub-convergence-004` (this packet + client/UI/runner) — open/update as draft PR stacked on reliability work |
| `gabeacosta/operator-hub` | Existing draft PR #99 (API/UI provider). No OpHub code change required for this slice |

## Known limitations

1. Browser UI on `:7792` may hit CORS when fetching `:8899` directly; **authoritative physical path is the Python runner** (`run_ophub_voice_query.py --speak`).
2. Mic→STT→query E2E through the owner `:7780` gateway was **not** required here and was not attached (gateway left untouched).
3. Traycer PR #101 remains a separate owner decision.

## Rollback

- Close/revert the VoxMaestro convergence draft PR.
- Stop only the temporary OpHub `serve --port 8899` and UI `:7792` helpers if started for review.
- Do not kill `:7780`.

## Owner decisions

| ID | Question | Options |
|---|---|---|
| D1 | Accept read-only voice over owner-workspace? | ACCEPT / HOLD / REJECT |
| D2 | Keep merge/deploy/live voice dispatch blocked? | ACCEPT_HOLD (default) |
| D3 | Merge VoxMaestro and/or OpHub PRs? | Separate explicit grants |
