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

## Mac mini preparation (read-only commands first)

Verify prerequisites are already installed and the Mac is tailnet connected:

    tailscale status
    tailscale serve status
    tailscale funnel status
    python3 --version
    git -C "$HOME/voxmaestro" status --short
    git -C "$HOME/design-factory" status --short

DO NOT reset, clear, replace or run Serve until reviewing current Serve and
Funnel allocations. **8443** is the requested isolation port, not 443.
If 8443 is occupied, STOP and choose a dedicated alternative release plan
rather than displacing the current config.

Install the exact review branches in **separate worktrees** (avoid changing
current checkouts or mixing production branches):

    git -C "$HOME/voxmaestro" fetch origin feat/wt-voice-tailnet-stage-001
    git -C "$HOME/design-factory" fetch origin feat/vsai-voxmaestro-browser-voice-002
    git -C "$HOME/voxmaestro" worktree add --detach "$HOME/voxmaestro-maya-stage" origin/feat/wt-voice-tailnet-stage-001
    git -C "$HOME/design-factory" worktree add --detach "$HOME/design-factory-maya-stage" origin/feat/vsai-voxmaestro-browser-voice-002

Pin the exact SHA at review signoff. A worktree alone is not evidence that the
process launched the pinned commit.

Create a dedicated venv rather than modifying other Python installations:

    cd "$HOME/voxmaestro-maya-stage"
    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install -e '.[voice_web]'

A **real local kernel** must be listening on 127.0.0.1:7788 with
/v1/intent and /v1/generate and with the models declared in the read-only
YAML. The launcher checks its TCP port but cannot prove model quality; the
iPhone conversation round-trip is an explicit acceptance step.

Get the real tailnet HTTPS origin without guessing:

    tailscale status --json | python3 -c 'import sys,json; d=json.load(sys.stdin); n=d["Self"]["DNSName"].rstrip("."); assert n.endswith(".ts.net"); print("https://"+n+":8443")'

Copy the printed value into ORIGIN. Determine your exact Tailscale
login (email) from the admin console, not a guessed account identifier.

## Start the read-only runtime

From the VoxMaestro staging worktree, with the venv activated, run the
command below in a **foreground terminal**:

    SITE="$HOME/design-factory-maya-stage/workspaces/client-sites/voicescheduleai"
    ORIGIN="https://<exact-machinename>.<exact-tailnet>.ts.net:8443"
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

    tailscale serve --bg --https=8443 http://127.0.0.1:7789
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

Stop the foreground local server. To remove the isolated 8443 mapping,
review current Serve status and ONLY THEN run the command for the specific
port (not a global reset):

    tailscale serve --https=8443 off

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
