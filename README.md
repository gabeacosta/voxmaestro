<div align="center">

# VoxMaestro

**Deterministic conversation orchestration for voice agents.**

YAML state machines · tool bridges · filler gates · handoff protocol · runtime boundaries

</div>

---

VoxMaestro is a **public alpha** of a voice-agent orchestration layer. It focuses on the control logic between speech/model components and external workflow actions rather than trying to own STT, TTS, telephony, or the LLM itself.

The public repo is useful as an implementation proof surface for:

- declarative conversation state;
- deterministic transition rules;
- mid-turn tool orchestration;
- pre-LLM filler behavior;
- explicit human-handoff state;
- runtime truth / capability boundaries;
- an early Pipecat adapter.

## Current maturity

| Capability | Status |
|---|---|
| YAML schema | Implemented |
| Core state machine | Implemented |
| Tool bridge / dry-run path | Implemented |
| Handoff protocol | Implemented |
| Runtime stream / truth surfaces | Implemented |
| Pipecat adapter | Early adapter |
| PII redaction | Configuration exists; enforcement not complete |
| Production telephony transports | Not included |
| LiveKit / Vocode adapters | Planned |

This is not presented as a turnkey production voice platform.

---

## The problem

A voice stack can have excellent STT, TTS, telephony, and generation while still failing operationally because the conversation-control layer is implicit.

Questions such as these need deterministic ownership:

- Which state is the call in?
- What transitions are legal?
- What happens while a tool call is in flight?
- When should a human take over?
- What does barge-in cancel?
- Which events are hot-path vs durable/cold-path work?
- What can the runtime truthfully claim happened?

VoxMaestro makes those concerns explicit instead of burying them inside prompts or provider-specific callback code.

---

## Architecture

```text
caller audio
    ↓
   STT
    ↓
┌───────────────────────────────┐
│          VoxMaestro           │
│                               │
│ intent → state → tool/handoff │
│          │          │         │
│          └─ filler ─┘         │
└───────────────────────────────┘
    ↓
   LLM
    ↓
   TTS
    ↓
caller audio
```

The implementation separates the **conversation-control state machine** from transports and model providers so those components can be changed without rewriting the state contract.

---

## Quickstart from source

```bash
git clone https://github.com/gabeacosta/voxmaestro.git
cd voxmaestro
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

Load an agent from YAML:

```python
from voxmaestro import VoxMaestro

conductor = VoxMaestro.from_yaml("my_agent.yaml")
ctx = conductor.new_call(call_id="call-001", caller_phone="+15551234567")

result = await conductor.process_turn(
    ctx,
    "Do you have anything Thursday at 3?",
)
```

The exact returned fields depend on the configured state/tool path; the public tests are the stronger source of truth for implemented behavior than README examples.

---

## YAML conversation contract

A minimal configuration looks like:

```yaml
schema_version: "0.1.0"

agent:
  name: "my-agent"
  voice:
    provider: "piper"
    model: "en_US-amy-medium"

intent:
  provider: "ollama"
  endpoint: "http://localhost:11434/v1/chat/completions"
  model: "my-intent-model"
  intents:
    - id: "greeting"
      description: "Hello, hi"
    - id: "question"
      description: "Asking for information"
    - id: "unknown"
      description: "Cannot classify"

generation:
  provider: "ollama"
  endpoint: "http://localhost:11434/v1/chat/completions"
  model: "local-generation-model"
  max_tokens: 150

states:
  initial:
    transitions:
      greeting: conversation
      "*": conversation

  conversation:
    transitions:
      "*": conversation
```

See [`examples/real_estate_agent.yaml`](examples/real_estate_agent.yaml) for a larger example configuration. It is an example contract, not a claim that the repo includes a complete deployed real-estate voice service.

---

## Mid-turn tool bridge

The tool bridge is designed around a simple control rule:

```text
intent says tool needed
        ↓
emit filler / progress signal
        ↓
execute tool asynchronously
        ↓
return tool result to conversation path
        ↓
continue generation/state transition
```

The important property is **ordering**, not a hard-coded latency claim: the filler/progress signal is emitted before waiting for the tool result so the orchestration layer does not require the LLM/tool round trip to begin responding.

Actual end-to-end audio latency depends on the transport, event loop, TTS path, hardware, and provider choices outside this repo.

---

## Human handoff

VoxMaestro models handoff as a protocol rather than a single boolean:

1. **Decision** — the state machine determines that handoff is required and records the reason.
2. **Bridge** — the runtime emits the handoff/progress event and prepares context for the external transfer path.
3. **Teardown** — the transport/customer system owns the actual transfer and lifecycle completion.

The public repo implements the orchestration semantics; it does not include every production telephony integration needed to complete a live transfer.

---

## Hot path vs cold path

The design separates operations that affect the live turn from operations that can be durable/asynchronous.

**Hot path examples**

- intent/state evaluation;
- filler/progress signaling;
- barge-in state changes;
- tool-result routing.

**Cold path examples**

- transcript persistence;
- analytics;
- training-data capture;
- CRM/webhook handoff payloads.

Latency numbers are environment-dependent; the repo expresses the boundary rather than claiming a universal sub-100ms or sub-200ms production result.

---

## Pipecat integration

The repository includes an early adapter at:

[`src/voxmaestro/integrations/pipecat.py`](src/voxmaestro/integrations/pipecat.py)

It maps VoxMaestro control events into Pipecat-oriented frame concepts such as filler, tool result, state change, handoff, and barge-in frames.

Status: **early adapter**, not a claim of full compatibility with every current Pipecat transport/version.

Install the optional dependency when evaluating that path:

```bash
pip install -e ".[pipecat,dev]"
```

---

## Public tests

The repository contains tests for the conductor and runtime-facing behavior under `tests/`, including:

- `test_conductor.py`
- `test_runtime_stream.py`
- `test_runtime_truth.py`

The test suite is the evidence for the specific public behavior it covers. It does not prove production telephony integration, provider uptime, or end-to-end call quality.

---

## What is intentionally not included

- customer data or customer-specific prompts;
- production telephony credentials/transports;
- a complete STT/TTS stack;
- production deployment topology;
- finished PII-redaction enforcement;
- LiveKit/Vocode adapters;
- a visual state-machine editor;
- claims that provider completion equals workflow acceptance.

---

## Why this matters for Forward Deployed Engineering

Voice-agent deployments are a good example of why applied AI work becomes systems work quickly.

A customer asks for “an AI receptionist,” but the engineering surface becomes:

```text
business workflow
  -> conversation state
  -> external tools
  -> latency / failure handling
  -> human escalation
  -> durable system-of-record update
  -> runtime evidence
```

VoxMaestro is the public orchestration slice of that problem.

For the broader deployment and runtime-evaluation story, see the [Forward Deployed Engineering portfolio](https://github.com/gabeacosta/ai-portfolio).

## License

Apache-2.0. The license permits commercial use; it is not a statement of production readiness.
