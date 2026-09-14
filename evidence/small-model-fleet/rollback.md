# Migration rollback boundary

Inventory captured before any active VSAI file edits. Existing source `/Users/clue/vsai-inference/server.py` SHA256: `7802db53d565d24acf979c0c46e097e883682229029982e0dd3cb99985d49021`. Existing model config SHA256: `cc876f8ca9790f7f4e591986b287e10e31f1969bad1a85cf7e47e6abe5041382`.

Implementation is isolated in `/Users/clue/voxmaestro-small-fleet` from baseline `df6b154df20a8f10b7407466338c9c66d40c8126`. No active VSAI source, model weights, LaunchAgent or calendar provider was modified by the inventory task. Existing production services must remain running.

Rollback consists of stopping only new, positively identified fleet processes started for this branch, restoring any branch-local configuration changes, and reverting this branch's coherent commits if required. Do not kill by broad process name or port alone. Do not reset another user's checkout, delete caches, or remove existing models manually.

Qwen3:8b has active dependencies and is retained. The Nomic smoke probe uses the existing Ollama service and requested one-minute model keep-alive; it does not install a service or change persistent configuration.

Any later active-runtime mutation needs its own recorded inventory, exact changed files and restoration procedure before execution; this document does not claim such a mutation or rollout happened.
