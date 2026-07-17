# Review Guide

Review the branch in this order:

1. `src/voxmaestro/runtime.py` for call isolation and truthful side effects.
2. `src/voxmaestro/integrations/runtime_stream.py` for hot-path streaming.
3. `tests/test_runtime_truth.py` for state, handoff, and concurrency invariants.
4. `tests/test_runtime_stream.py` for filler-before-tool completion.
