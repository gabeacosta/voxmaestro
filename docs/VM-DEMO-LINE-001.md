# VM-DEMO-LINE-001 — Prospect inbound demo contract

Status: SPECIFIED; NOT IMPLEMENTED OR HARDWARE VERIFIED
Issue: https://github.com/gabeacosta/voxmaestro/issues/63
Base: main@9d32726f00036cb2cd3fd00d7e592675bc3337ab

## Product behavior
A prospect calls one demo number, hears a clear AI demo disclosure, selects or is assigned a business scenario, describes a realistic service need, and receives a useful response. The session captures structured lead fields and produces a reviewable demo outcome. Appointment and dispatch actions are simulated unless a separately authorized integration confirms them.

## Ownership boundaries
- Telephony/Pipecat adapter: inbound/outbound audio, call lifecycle, disconnect, cancellation, media timestamps. It cannot mutate conversation authority.
- VoxMaestro: authoritative per-call state, transitions, tool intent, handoff, and barge-in semantics.
- Tiny local classifier: advisory intent and slot candidates; may abstain. No effect authority.
- Conversation model: response proposals bounded by scenario knowledge and state.
- Effect gateway: separately authorizes and confirms external actions; uncertain outcomes remain UNKNOWN and are never automatically retried.
- Telemetry: cold-path, redacted, per-call keyed evidence; not a dependency of speech hot path.

## Demo scenarios
- Plumbing: emergency severity, leak status, ZIP/service address, callback number.
- HVAC: no cooling/heating, equipment type, urgency, callback.
- Mobile diesel: breakdown location, vehicle type, mobility, callback.
- Dental consultation: requested service, scheduling preference, callback; no diagnosis.

Each scenario has an isolated knowledge/config namespace. Never reuse previous caller's state, tool results, audio, or personal data.

## State flow
CONNECTED -> DISCLOSED -> SCENARIO_SELECTED -> QUALIFYING -> SUMMARY -> COMPLETED
Any active state -> HUMAN_HANDOFF_REQUESTED or DISCONNECTED.
A booking request -> PROPOSED only until externally authorized and confirmed.
Interruption cancels outstanding speech for the current turn without dropping already accepted caller data.

## Acceptance tests
1. Caller hears AI disclosure and can interrupt greeting.
2. Two simultaneous calls do not share scenario, contact details, transcripts, or audio.
3. Caller correction replaces a provisional slot with explicit provenance.
4. Local classifier abstention safely falls back to stronger model; provider timeout does not imply cognitive escalation.
5. Telephony disconnect cancels audio generation and releases call resources.
6. Unknown booking/dispatch acknowledgment produces UNKNOWN, no retry or false confirmation.
7. PII is redacted from persisted telemetry and access is scoped; consent/retention policy is explicit.
8. Real device proves audible model speech, caller interruption, and end-to-end inbound transport; software tests alone cannot close this gate.
9. Capture p50/p95 time-to-first-audio, turn completion, false tool activation, process peak RSS, and recovery from repeated calls.

## Deployment constraints
No changes to existing Mac ports 443, 8443, 7789, or Tailscale Serve 10000 without explicit migration approval. No secrets in source. No automatic production activation. Use isolated local test ports and reversible configuration.

## Next implementation boundary
Inspect existing src/voxmaestro/integrations/pipecat.py, WebSocket gateway, tests, and PR #62 stacking before editing any runtime code. Create a transport contract test using synthetic audio and disconnect events, then run actual phone hardware qualification.
