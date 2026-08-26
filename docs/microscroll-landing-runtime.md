# Microscroll Landing Runtime Envelope v0

Status: draft implementation contract

## Objective

Evolve VoxMaestro from a voice-call-specific proof surface into the deterministic conversation runtime behind a conversational microscroll landing page without turning VoxMaestro into a frontend framework, model host, or provider-specific voice stack.

The first target is a local-first business landing page that can:

1. start as text immediately;
2. add voice when the browser/runtime supports it;
3. answer grounded business questions;
4. qualify intent with short turns;
5. check availability through a bounded tool interface;
6. capture a lead or request a booking only through an explicit executor boundary;
7. hand off when the runtime cannot complete the task safely or confidently.

## Non-goals

VoxMaestro does not own:

- React / Next.js rendering;
- page scroll choreography or visual state;
- browser STT/TTS implementation;
- model weights or model lifecycle;
- direct CRM/calendar credentials;
- arbitrary model-to-model messaging;
- a generic multi-agent framework.

## Architecture

```text
microscroll landing page
        |
        | WebSocket / session events
        v
web session gateway
        |
        v
VoxMaestroRuntime
  |        |        |
  |        |        +--> handoff protocol
  |        +-----------> ToolExecutor boundary
  +--------------------> model/fleet adapters
                           |
                           +-- L0 reflex
                           +-- L1 worker
                           +-- L2 resolver
```

The page is a client. VoxMaestro owns session state, legal transitions, tool ordering, failure truth, and handoff semantics. Model adapters remain replaceable.

## Runtime ownership

### VoxMaestro owns

- session identity;
- conversation state;
- legal transitions;
- turn counters and escalation;
- filler/progress ordering;
- tool invocation requests;
- tool timeout/failure handling;
- handoff state and delivery receipts;
- runtime-facing evidence.

### Fleet adapters own

- intent classification;
- grounded generation;
- bounded extraction;
- optional escalation recommendation;
- model-specific tokenization/inference details.

### Models never own

- durable credentials;
- direct calendar/CRM mutations;
- retry policy for consequential effects;
- authority budgets;
- session truth;
- proof that an external effect occurred.

## Fleet slots

The first deployment envelope uses semantic slots rather than named permanent agents.

| Slot | Purpose | Initial candidate class | Residency intent |
|---|---|---|---|
| `l0_reflex` | intent, extraction, route hint, rewrite | ~1-2B native 1-bit/ternary | always hot |
| `l1_worker` | FAQ, qualification, short bounded reasoning | ~2-4B | hot/warm |
| `l2_resolver` | harder reasoning, repair, complex composition | ~8B or stronger control | cold/demand |
| `embedding` | local retrieval | sub-1B | always hot |
| `asr` | speech ingress | MLX/CPU challenger lanes | hot during voice |
| `tts` | speech egress | local provider | hot during voice |

Concrete models are Wind Tunnel-qualified configuration, not application contracts.

## Routing rule

The runtime must not expose peer-to-peer model calls.

```text
turn
  -> L0
     -> accept
     -> L1
        -> accept
        -> L2
           -> accept / handoff / deny
```

Initial ceilings:

- maximum model escalations per turn: 1;
- maximum same-tier repair attempts: 1;
- no model may recursively invoke another model;
- only the runtime/router may select the next tier.

## Browser session protocol

The existing website transport currently uses `start`, `message`, and `end`. Preserve that compatibility while introducing explicit normalized events behind the gateway.

### Client -> gateway

```json
{"type":"start","sessionId":"...","surface":"microscroll","locale":"en"}
```

```json
{"type":"message","sessionId":"...","text":"Do you have anything Thursday?"}
```

```json
{"type":"end","sessionId":"..."}
```

Future audio ingress is additive; the first runtime slice consumes normalized text.

### Gateway -> client

The public client contract should remain small:

- `greeting`
- `response`
- `booking_confirm`
- `error`

Internally the gateway may consume richer VoxMaestro frames:

- filler/progress;
- state change;
- tool result;
- handoff;
- final response.

Do not expose internal model identities or tool credentials to the browser.

## Microscroll UX contract

The landing page is not a long chat application. Conversation and page state cooperate.

Suggested visual progression:

```text
01 HERO
   Ask / speak immediately

02 UNDERSTAND
   Page reacts to detected visitor intent

03 PROOF
   Show the specific capability or evidence relevant to that intent

04 ACTION
   Availability / qualification / lead capture

05 CONFIRM
   Booking request, handoff, or next step
```

The runtime does not control scrolling directly. It emits semantic state. The frontend maps state to presentation.

Example:

```text
runtime state: qualification
frontend presentation: reveal proof + qualification card

runtime state: tool_call
frontend presentation: progress/filler state

runtime state: handoff
frontend presentation: human follow-up panel
```

This avoids coupling business workflow semantics to React component names.

## Tool authority boundary

`RuntimeToolBridge` already fails when no executor exists and marks dry-run results as simulated. Preserve that property.

For the landing-page slice:

- read-only availability checks may be implemented first;
- lead creation and booking writes require an explicit `ToolExecutor`;
- the model may propose parameters but never receives credentials;
- executor results are the only source of external-action truth;
- failed/simulated tool work must never render as a confirmed booking.

The first implementation should prefer `request_booking` over an irreversible booking write until explicit authorization/effect-budget enforcement is wired.

## State classes

### Ephemeral

- prompt/input tokens;
- model KV cache;
- current partial generation.

### Session

Owned by VoxMaestro:

- current state;
- previous state;
- intent history;
- bounded conversation history;
- tool results;
- qualification progress;
- pending action proposal.

### Durable

Owned by the external system of record:

- lead/customer records;
- appointments;
- CRM state;
- effect receipts.

Durable business truth must not live only in model context.

## Context envelope

Each model call receives only the context needed for the current task:

```text
system contract
+ current task
+ relevant session state
+ bounded retrieval
+ capability declaration
+ output contract
```

Do not pass the entire site corpus or entire transcript to every tier.

## First vertical slice

Build only this path first:

```text
browser text
 -> existing WebSocket contract
 -> web session gateway
 -> VoxMaestroRuntime
 -> L0 intent adapter
 -> L1 generation adapter
 -> optional availability ToolExecutor
 -> response
```

Voice, L2, embeddings, and multi-model residency remain behind adapters and can be added after the text path is verified.

## Acceptance conditions for Slice 1

1. Existing VoxMaestro runtime truth tests remain green.
2. One browser session maps to exactly one isolated `CallSession`.
3. Two concurrent browser sessions cannot share mutable conversation state.
4. Missing tool executor produces failure, never simulated success.
5. Dry-run tool results are visibly non-successful to the gateway.
6. Filler/progress can reach the client before a slow tool completes.
7. A tool timeout returns a bounded failure path.
8. Handoff produces a truthful delivery status.
9. The browser receives no model credentials or tool secrets.
10. Model selection is replaceable without changing the page protocol.
11. The first path works text-only with no STT/TTS dependency.
12. Consequential writes are not enabled until an explicit authority layer is present.

## Repository boundaries

### `voxmaestro`

Add:

- transport-neutral web-session adapter;
- gateway event mapping tests;
- fleet/model adapter interfaces;
- microscroll example configuration;
- runtime evidence for routing/model selection.

Do not add:

- Next.js;
- page CSS;
- browser microphone code;
- model binaries.

### website repo

Keep:

- microscroll composition;
- browser microphone UX;
- text input;
- animation/reveal behavior;
- WebSocket client;
- mapping runtime semantic state to page presentation.

Replace the legacy backend endpoint with the VoxMaestro gateway only after the gateway contract is tested.

## Next implementation slice

Implement a dependency-light `WebSessionAdapter` over `VoxMaestroRuntime` and `RuntimeStreamProcessor`, with tests that map the existing browser `start/message/end` protocol into one isolated runtime session and map runtime frames back into the existing `greeting/response/booking_confirm/error` client contract.
