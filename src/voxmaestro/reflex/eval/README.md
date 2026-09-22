# Reflex eval corpus

This directory defines the admission corpus format. It intentionally does not
ship fabricated "real turn" evidence.

A development corpus needs at least 30 labelled real turns before the reflex is
considered beyond smoke-test status. Tool-routing admission must separately
contain enough positive tool-needed examples to make the false-downgrade claim
meaningful; 30 total turns are not sufficient evidence for a 5% false-negative
bound.

Each JSONL row must validate against corpus.schema.json and include:

- a stable opaque id
- transcript text
- expected reflex intent
- expected tool_needed boolean
- expected language
- provenance marked real or synthetic
- optional notes

Synthetic examples can test parser/runtime behavior but do not count toward
deployment admission.

The reflex remains shadow-only until a reviewed real-turn corpus exists and the
routing admission criteria are frozen separately.
