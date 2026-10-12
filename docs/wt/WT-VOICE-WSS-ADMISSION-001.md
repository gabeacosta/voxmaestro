# WT-VOICE-WSS-ADMISSION-001 — authenticated, read-only browser ingress

**Stage: source qualification only; NOT live hosted, not production approved.**

## Threat model and implemented controls

Browser speech recognition and browser speech synthesis are outside VoxMaestro. This implementation admits a browser WebSocket to the existing WebSessionAdapter for a real model-driven conversation, without any effect-capable tools. It does NOT deploy a hosted service, manage identity, own calendar authority, or assert booking completion.

Implemented in src/voxmaestro/integrations/secure_ingress.py:

- POST /api/voice/bootstrap: a required application-supplied verified-session authenticator, exact Origin allowlist and JSON-only request. Returns a random 60-second default bearer ticket, wssUrl, conversation:readonly scope, and no-store headers. Never authenticate by a client-provided username or unverified header.
- GET /session: checks exact Origin and authenticated identity, then atomically redeems the one-use ticket from WebSocket subprotocol vsai.ticket.<ticket> **before** upgrading. Server chooses voxmaestro.v1, never echoes the ticket as selected protocol. This is a single-event-loop in-memory registry, with one concurrent session per principal by default, bounded pending tickets and expired-record cleanup.
- Each socket is bound to a single start sessionId, en-only locale, then bounded message/end turns. Refuses PCM/binary in this browser speech lane, unknown keys, oversized messages, cross-session frames, unsupported frames, and over-budget connections. Drops booking_verified, booking_confirm, effect_verified and raw PCM frames from browser output.
- On disconnect, malformed frame, timeout or end, closes the WebSessionAdapter runtime session and releases the admission slot. This DOES NOT revoke external actions already dispatched in other contexts; this preview forbids effect-capable tools and handoff delivery.
- Refuses a runtime config with any declared tools or handoff delivery before constructing routes.
- Python >=3.10, optional aiohttp dependency. Tests use a mock authenticator **only within tests**, with actual HTTP and WebSocket transport over an ephemeral loopback socket.

## Not shipped / fail-closed boundaries

**Do not expose the test helper or the plain examples/serve_gateway.py on a public network.** The production backend for /api/voice/bootstrap still needs an OAuth/SSO/session validator or trusted authenticated reverse proxy, protected service-to-service identity, SameSite/CSRF assurances, TLS termination, and precise routing to the WSS host.

**Topology limitation:** MemoryVoiceAdmission is single-process and not replicated. Two workers will accept unrelated tickets without a shared active-session budget. No multi-worker or multi-node claims are permitted; use a transactional shared redemption registry before horizontal scaling. One process restart discards its pending tickets (fail closed). Scope is nonconsequential conversation only, so no exactly-once booking implications.

**Web browser limitation:** the currently deployed SSO-protected Vercel static site does not implement /api/voice/bootstrap. It also carries Content-Security-Policy connect-src 'none'. No live microphone/WebSocket can reach this server from that preview yet, and server code has not been deployed or qualified against actual iPhone Safari.

**Voice-vendor limitation:** the browser client in Design Factory PR #6 relies on platform SpeechRecognition and SpeechSynthesis; their functionality and privacy/availability vary by browser. iOS Safari must be measured, not presumed. No on-device or VoxMaestro-native PCM/ASR/TTS assertion here.

## Sequence

1. Browser visitor explicitly accepts microphone/privacy notice.
2. Browser sends POST /api/voice/bootstrap to same-origin server; server verifies actual authenticated identity and exact Origin.
3. Admission issues one-use scope-limited ticket.
4. Browser opens WSS with protocols [voxmaestro.v1, vsai.ticket.<ticket>].
5. Server validates authenticated identity, Origin, ticket, budgets, single-process state **before upgrade**.
6. VoxMaestro receives start/message/end; read-only model replies are projected to browser.
7. Disconnect closes the runtime session and releases slot.
8. No action authority or booking verifier is wired, and no booking confirmation can be displayed.

## Next qualification

1. Run GitHub Actions Python 3.10/3.11/3.12 tests + Ruff against pinned code.
2. Independently inspect aiohttp access logs and proxy routing to ensure tickets are not logged, replayable, echoed or leaked. A malicious browser must never be able to spoof the authenticated principal; runtime must be behind a genuinely protected session mechanism.
3. Qualify two simultaneous socket upgrades with the same ticket; require exactly one succeeds and the other fails BEFORE WebSocket upgrade.
4. Add real encrypted gateway hosting with identity integration under a restricted, single-instance scope; use explicit allowed origins, exact DNS, and real TLS. Do NOT use the shared multi-domain VoiceScheduleAI Vercel project.
5. Only then permit Vercel preview to call the trusted admission host through a correctly authenticated same-origin reverse proxy and update its CSP (currently connect-src none).
6. Collect iPhone Safari + Chromium input/output evidence (permission, ASR timeout, session close, barge-in and replay). Continue to prohibit bookings until Ceinit grants and Veynit sink receipts exist.

## Owners

VoxMaestro handles spoken conversation and session lifecycle. Ceinit retains consequential authorization, durable operation identity, dispatch, retry and recovery. Veynit independently verifies actual sink effects and seals receipts. ICM stores durable decisions/evidence. Models never acquire effect authority.

**Stop condition:** source CI pass does not establish public readiness or a real hosted call. No production VoxMaestro, Veynit, Ceinit or Vercel change is authorized by this slice.
