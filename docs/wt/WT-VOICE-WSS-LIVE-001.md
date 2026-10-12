# WT-VOICE-WSS-LIVE-001 — pre-live network qualification

**Disposition: test-only. Do not present as a live hosted Maya conversation.**

Extends code-qualified single-process admission gate (WT-VOICE-WSS-ADMISSION-001) with actual competing aiohttp socket-upgrade qualification. All tests run on loopback. They do NOT provision public TLS, production SSO, a persistent WSS host, browser speech providers, or an iPhone device.

## Five hostile sequences

1. Thirty-two independent WebSocket upgrades race one authenticated fixture ticket. Require exactly one upgrade, 31 HTTP 403 denials BEFORE upgrade, no duplicate runtime session, and released budget.
2. An idle open session exceeds its configured lifetime. Require WebSessionAdapter closure and release of active-principal slot.
3. An admitted socket sends a different session ID. Require rejection before runtime.
4. A redeemed ticket is replayed after clean end. Require HTTP 403 and no state resurrection.
5. A browser injects an effect-shaped control field. Require closure, zero effect-shaped input accepted by runtime.
6. A model/runtime turn blocks past the session deadline. Require timeout, cancellation of its pending delivery, transport closure, and release of the admission slot.

## Trust boundaries

The parent PR #60 uses X-Test-Identity ONLY in test fixtures. That is not real authentication and must never be deployed. No public service is deployed as part of this slice. The approved Maya V3 static review remains SSO-gated with CSP connect-src none, and neither Vercel project changes.

The connected Vercel projects include the static Maya review and shared production website. No independently verified persistent VoxMaestro WebSocket host was located. Static hosting is not a persistent authenticated WebSocket listener.

## Required before any hosted iPhone test

- A persistent single-instance TLS VoxMaestro host, trusted independently verified identity on BOTH HTTP bootstrap and WS upgrade. Never trust caller-supplied X-User, mock headers or anonymous demo auth.
- Both /api/voice/bootstrap and /session reachable through the exact approved authentication boundary. Browser auth cookies must be appropriately scoped, Secure, HttpOnly, SameSite; CSRF/origin controls are mandatory.
- Logs must not retain WS subprotocol ticket bearer values, cookies, bodies or user transcripts.
- Run exactly ONE gateway process while admission state is memory-local. Multi-node requires shared transactional redemption and session budgets before scaling.
- Qualified voice provider and explicit consent; real iPhone Safari microphone permission, browser speech availability, interrupted generation, tab closure, barge-in and recovery.
- Zero consequential tools/handoffs. Ceinit retains effect authorization; Veynit independently verifies sink effects.
- Only then consider widening preview CSP connect-src none to exact host allowlist on isolated review; require separate explicit authorization for production.

## Acceptance

Code: Python 3.10/3.11/3.12 matrix plus Ruff, and hostile socket race tests.
Live: trusted TLS, real identity, independent bearer leakage check, iPhone audio evidence, shutdown recovery, zero unauthorized effects.

**Never mark LIVE E2E GREEN based on loopback tests alone.**
