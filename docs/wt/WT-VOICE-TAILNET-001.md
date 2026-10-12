# WT-VOICE-TAILNET-001 — Mac mini staging runbook

**Code review only. No Mac command has been executed remotely. No public gateway exists.**

## Purpose and safety

Run the approved Maya V3 browser voice source from Design Factory PR #6, with
a real read-only VoxMaestro model runtime, on the Mac mini. Tailscale Serve
provides HTTPS and verified Tailscale identity only to authorized tailnet
members. The aiohttp backend is reachable solely on 127.0.0.1:7789 and
checks the Serve-supplied Tailscale-User-Login header against an explicit
allowlist. All static pages and files require that identity.

This is **not** a complete production identity service. We trust the
authenticated Serve reverse proxy AND other local Mac processes. Verify
Tailscale actually strips spoofed identity headers, and that tailnet
shares/ACLs restrict reviewers. Never use Funnel or bind the backend on
0.0.0.0; doing either undermines this trust boundary.

Tailscale upstream guidance: https://tailscale.com/docs/features/tailscale-serve
Identity header names: Tailscale-User-Login, Tailscale-User-Name,
Tailscale-User-Profile-Pic. These are absent for tagged devices and Funnel.
The frontend, backend session bootstrap and WSS endpoint all share the same
HTTPS origin, avoiding cross-domain cookies and permissions.

## Observed host state, 2026-10-12

The Mac-side screenshot confirms existing Tailscale Serve mappings on 443
and 8443 (to ports 8766 and 8778, respectively). Ollama is listening on
127.0.0.1:11434, but its installed model inventory was NOT shown. Ports 7788
and 7789 have no listeners. Port **10000 is NOT YET VERIFIED FREE**: check
both active Serve/Funnel configuration and the socket before any change.
The Tailscale CLI also reported a client/server version mismatch; do not
reset or upgrade the daemon as part of this staging slice.

## Mac mini preparation (read-only commands first)

Verify prerequisites are already installed and the Mac is tailnet connected:

    tailscale status
    tailscale serve status
    tailscale funnel status
    python3 --version
    git -C "$HOME/voxmaestro" status --short
    git -C "$HOME/design-factory" status --short

DO NOT reset, clear, replace or run Serve until reviewing current Serve and
Funnel allocations. **10000** is the requested isolation port, not 443.
The Mac already uses HTTPS 443 and 8443 for other services. Preserve BOTH.
If staging port 10000 is occupied, STOP; never reset or displace existing Serve/Funnel routes.

Install the exact review branches in **separate worktrees** (avoid changing
current checkouts or mixing production branches):

    git -C "$HOME/voxmaestro" fetch origin feat/wt-voice-tailnet-stage-001
    git -C "$HOME/design-factory" fetch origin feat/vsai-voxmaestro-browser-voice-002
    git -C "$HOME/voxmaestro" worktree add --detach "$HOME/voxmaestro-maya-stage" origin/feat/wt-voice-tailnet-stage-001
    git -C "$HOME/design-factory" worktree add --detach "$HOME/design-factory-maya-stage" origin/feat/vsai-voxmaestro-browser-voice-002

Pin the exact SHA at review signoff. A worktree alone is not evidence that the
process launched the pinned commit.

First, run the read-only host diagnostic:

    python3 "$HOME/voxmaestro-maya-stage/examples/maya_mac_preflight.py"

If the report says BLOCKED, do not enable Serve. The report checks port 10000 allocation, local kernel status, existing files, and Tailscale availability without modifying any of them. The default output hides the tailnet hostname. The missing kernel on port 7788 is the one expected initial blocker that can be resolved by the **separately reviewed local Ollama bridge** below. Any missing site files, occupied staging port, or uninspectable Serve/Funnel status requires a stop.

Create a dedicated venv rather than modifying other Python installations:

    cd "$HOME/voxmaestro-maya-stage"
    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install -e '.[voice_web]'

The Mac already has Ollama listening on loopback port 11434. It speaks
/api/chat, not VoxMaestro's /v1/intent and /v1/generate. Use the reviewed,
localhost-only adapter; do NOT point VoxMaestro directly at 11434.

**Read-only inspection first:**

    ollama list
    curl -fsS --max-time 5 http://127.0.0.1:11434/api/version
    lsof -nP -iTCP:10000 -sTCP:LISTEN
    tailscale serve status
    tailscale funnel status

Only after reviewing this output, select the separate **qualified conversation model**
from ollama list. The bridge uses two distinct names; no model download or default selection occurs.

**Observed model qualification — October 12, 2026:** The installed
`vsai-intent-v7:latest` returned `booking_request` correctly. But as a
conversation generator it replied, "I can check what days you know I'm
available." Because no availability/calendar interface exists, this is a
**capability-truth FAIL**, not permission to continue with the same model.
`nomic-embed-text:latest` is an embeddings model, not a chat replacement.

For independent evaluation (NOT YET INSTALLED OR APPROVED), one compact
conversation candidate is `qwen3:4b-instruct` (~2.5 GB on Ollama's official
registry). Do not pull it automatically. Record model hash and measure response
quality/latency on the M4 Mac mini before acceptance. A model's availability
in the registry is not evidence of local installation or fitness.

The Ollama kernel now REQUIRES two distinct installed model names. The first
is `vsai-intent-v7:latest`; a second conversation model must be selected,
installed with explicit operator approval, and qualified separately. Do not
silently use the intent model as generation fallback. For any booking or
availability question, the kernel returns a deterministic no-availability/no-
booking response without sending that question to the generator. The model
output also has a limited unsupported-capability pattern guard; it is NOT
proof that every hallucinated ability is caught. Physical microphone and
conversation qualification are still mandatory.

**In a separate foreground terminal (with the staging venv active):**

    cd "$HOME/voxmaestro-maya-stage"
    python examples/serve_maya_ollama_kernel.py \
      --intent-model "vsai-intent-v7:latest" \
      --generation-model "<separately-installed-qualified-chat-model>"

The bridge confirms /api/tags contains the exact named model before listening
at 127.0.0.1:7788. It sends NO tools, allows only read-only generation, and
refuses a remote Ollama address. Leave this foreground process visible.
The inference model is only a provider; it has no independent effect authority.

After the local bridge starts, run:

    python3 "$HOME/voxmaestro-maya-stage/examples/maya_mac_preflight.py"

Require HOST_CHECKS_PASS and confirm HTTPS port 10000 is not already configured.
If blocked, STOP; preserve 443 and 8443 and their existing proxy targets.

The two local model API routes are now /v1/intent and /v1/generate, as expected
by the read-only YAML. A TCP preflight is not proof of a meaningful model
response; the iPhone conversation round-trip remains an acceptance gate.

Get the real tailnet HTTPS origin without guessing:

    tailscale status --json | python3 -c 'import sys,json; d=json.load(sys.stdin); n=d["Self"]["DNSName"].rstrip("."); assert n.endswith(".ts.net"); print("https://"+n+":10000")'

Copy the printed value into ORIGIN. Determine your exact Tailscale
login (email) from the admin console, not a guessed account identifier.

## Start the read-only runtime

From the VoxMaestro staging worktree, with the venv activated, run the
command below in a **foreground terminal**:

    SITE="$HOME/design-factory-maya-stage/workspaces/client-sites/voicescheduleai"
    ORIGIN="https://<exact-machinename>.<exact-tailnet>.ts.net:10000"
    python examples/serve_maya_tailnet.py \
      --origin "$ORIGIN" \
      --allowed-login "<exact-authorized-tailscale-login>" \
      --site-dir "$SITE/site" \
      --config "$SITE/voxmaestro-readonly.yaml" \
      --port 7789

This command refuses startup if the kernel port is unavailable, the config
contains effect-capable tools or deliveries, or any of the required approved
Maya browser assets are missing. It does not create bookings, leads or
calendar entries.

Once the local server is healthy and the port is confirmed unallocated,
**explicitly configure tailnet-only Serve, never Funnel**, in a second
terminal:

    tailscale serve --bg --https=10000 http://127.0.0.1:7789
    tailscale serve status

Expected: an HTTPS URL at your origin which is accessible only to devices
with tailnet access. No public URL or port forwarding should be configured.
If Tailscale requires approval for HTTPS certificates, use the documented
tailnet approval flow; don't bypass TLS.

## Acceptance checklist on iPhone Safari

With Tailscale ON on the iPhone and with a permitted Tailscale identity:

1. Open the HTTPS tailnet URL, confirm original cinematic Maya V3 appears,
   compare against the approved static Vercel screenshot.
2. Tap the microphone. Verify informed consent BEFORE microphone requests.
   Decline: no audio or backend request initiated.
3. Accept and allow microphone. The bootstrap returns an authenticated
   read-only ticket, WSS opens, VoxMaestro greeting appears and browser speech
   synthesis plays it. If Safari speech recognition is unavailable, record the
   unsupported state as FAIL / BLOCKED, do not fabricate a spoken turn.
4. Speak an ordinary reception question. Observe one real WebSocket message,
   one final VoxMaestro response, and browser TTS. Record user-perceived
   response latency (no invented metrics).
5. Ask for a booking. Expected: no availability claim, no appointment
   confirmation, zero external writes.
6. Lock phone, leave the page, kill the tab, interrupt network and retry.
   Session closes and admission capacity is released; no stale reply.
7. Verify unauthenticated request is denied; a different Tailscale user
   not on the explicit allowlist cannot view the HTML, styles or JS; a stolen
   spent ticket cannot reconnect.
8. Inspect service access logs: no WebSocket subprotocol bearer ticket,
   cookies, transcripts or PII retained. Compare actual outside-tailnet
   behavior with the expected inaccessible service.
9. For FAIL observations, preserve exact pinned commit, tested browser,
   Tailscale app/Serve versions and independently observed results.

**Critical check**: the existing browser consent client from PR #6 relies on
SpeechRecognition/WebKitSpeechRecognition and browser SpeechSynthesis. iOS
support and privacy behavior are not asserted from Chromium CI. Use a physical
phone, do not substitute a simulator or recorded screenshot for speech evidence.

## Safe shutdown

Stop the foreground local server. To remove the isolated 10000 mapping,
review current Serve status and ONLY THEN run the command for the specific
port (not a global reset):

    tailscale serve --https=10000 off

Confirm its effect with tailscale serve status. Do **not** run
tailscale serve reset, since it can remove unrelated services.

## Release policy

- Live tailnet staging is not Internet production or a general user demo.
- Preserve the already deployed noindex SSO Vercel Maya V3 review and the
  shared production VoiceScheduleAI project unchanged.
- Never merge PR #60 / PR #61 / PR #62 simply because local CI passes:
  the trust boundary needs actual Mac+Serve validation and iPhone evidence.
- PROPOSE does not AUTHORIZE, AUTHORIZE does not EXECUTE, and models do not
  verify their own external effects. Ceinit owns future booking permission;
  Veynit verifies sink effects; ICM owns durable evidence.
