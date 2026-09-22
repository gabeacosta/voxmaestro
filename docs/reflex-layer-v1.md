# VoxMaestro Reflex Layer v1

## Status

Experimental, shadow-only. Not admitted as routing authority.

## Purpose

The reflex layer is a bounded System-1 sensor on final ASR turns. It produces
three typed observations without mutating conversation state:

1. intent: schedule, faq, pricing, complaint, disclosure-trigger, or off-script
2. tool-needed probability in [0, 1]
3. language: en or es

The deterministic VoxMaestro runtime remains the authority for transitions,
tool execution, disclosures, handoff and effects.

## Runtime boundary

ASR final -> ReflexGate (shadow) -> existing intent/runtime path

Reflex failure is fail-neutral: timeout, malformed output, backend busy state,
or local inference failure records a fallback observation and the pre-existing
runtime path continues unchanged.

There is no hosted fallback in the live path.

## Backend contract

LocalSchemaBackend talks only to a numeric loopback OpenAI-compatible /v1
endpoint. The model id and model hash are pinned at construction. It makes one
request, never retries, never follows redirects, exposes no tool surface, and
requires JSON-schema constrained output.

The backend deadline is at most 150 ms. ReflexGate also enforces an outer
deadline of at most 150 ms.

## Observability

No transcript is copied into reflex telemetry. The runtime emits:

- reflex_latency_ms
- reflex_decision (1 for a usable typed proposal, 0 for fallback)

Tags include schema version, status, backend identity, SHA-256 input digest,
intent, tool-needed probability, language, pinned model identity/hash, and any
fallback reason.

These records are telemetry, not effect receipts.

## Admission

Shadow mode is mandatory first. The eval directory contains only the corpus
format; it does not claim that a real-turn corpus exists.

Do not let reflex output change routing until:

- at least 30 labelled real turns exist for development evaluation
- the critical tool-needed positive set is large enough for a meaningful
  false-downgrade bound
- p95 reflex latency stays below 150 ms under representative concurrent ASR/TTS
  load on the target Mac Mini
- malformed output, timeout, backend-busy and backend-down tests all preserve
  the existing runtime path
- model_id and model_hash are pinned in the deployed configuration

## Deliberate non-goals

- no hosted Jev/OpenRouter call in the audio path
- no state mutation from the reflex
- no tool execution from the reflex
- no plugin/factory abstraction
- no free-text reflex output
