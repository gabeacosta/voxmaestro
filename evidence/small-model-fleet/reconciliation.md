# Frozen VSAI architecture reconciliation

Source repository: `gabeacosta/VoiceScheduleAi`, main `031b6d12484cf50986dc0ca333a3d07fe394fa71`; `specs/voice/VOICE_FLEET_V0.md` version `v0.1.0`, hash `5ae7413047fecc19`, dated 2026-08-20. Repository tree consists of README, specs and Wind Tunnel; no operational calendar integration or dental config hierarchy was found there.

Frozen pattern: Silero VAD, Qwen3-0.6B reflex with one weight allocation and router/extractor contracts, deterministic authority boundary, Piper fast TTS service boundary and pluggable control-plane interface. ASR lanes, retrieval and Kokoro are OPEN_BENCHMARK. The spec explicitly requires a new version/hash/changelog even when selecting an open field. This work does not silently promote Bonsai or Nomic into the frozen specification.

Existing `vsai-inference` Qwen2-family service differs from the frozen Qwen3 reflex and the bounded VoxMaestro intent contract. Its healthy MLX endpoint is not sufficient for KEEP admission because it repairs output and automatically cascades providers. Bonsai intent shim is a challenger implementation until real measured admission and a proper spec revision.

WT-VOICE-017 README and harness were inspected via GitHub API. Qwen ASR adapter is wired to `mlx-qwen3-asr`; Parakeet lanes remain stubs. Contract tests inject fake sessions. Mock results are explicitly inadmissible as architecture evidence. Decision gates use caller-observed latency, not accelerated replay compute latency. Physical barge-in is structurally invalid without AEC or the permitted headset-only setup.

## Calendar blocker

Candidate paths outside the VSAI runtime:

- `/Users/clue/governed-mcp-spine/mcp/servers/voice/src/main.py::check_calendar_slots`: `/tools/check_calendar_slots` → provider `/availability` → Google `freebusy().query()`.
- Same file `::book_appointment`: `/tools/book_appointment` → provider `/book`.
- Provider: `/Users/clue/governed-mcp-spine/services/gcal-bridge/src/main.py::book` → Google `events().insert()`.

Current local provider source returns `status=ok, mode=stub, booking_state=confirmed` when Google configuration is missing (line 111). Current voice MCP regards a 2xx calendar response as a confirmed booking. It is unsafe to count this path as a real calendar effect. No call or write was made through this provider.

Prior qualification report `/Users/clue/icm/5-artifacts/output/vsai-provider-qualification-2026-09-07/REPORT.md` records isolated corrective spine commit `7fc1dc0ee28a9c4166399311662d22affb6385ae`, not pushed/deployed, and missing dedicated sandbox/API authorization. The existing provider expects service-account JSON plus delegated user; current credential values were not inspected. Historical synthetic booking artifacts cannot qualify VM-VSAI-001.

Operational booking remains BLOCKED until a configured, governed provider independently proves a real effect and the runtime validates that result before confirmation. Software fixture success cannot substitute for this evidence.
