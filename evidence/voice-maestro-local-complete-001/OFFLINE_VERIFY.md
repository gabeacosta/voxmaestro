# Offline verify

## Outbound denial probe (agent sandbox)

With network restricted to the Cursor sandbox allowlist:

- `curl https://example.com` → connection failure (`http=000` / exit 56)
- `curl http://127.0.0.1:8880/health` → Kokoro `status=ok`
- `curl http://127.0.0.1:11434/api/tags` → Ollama reachable

## Physical conversation with HF offline

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python examples/run_physical_voice_loop.py --turns 1
```

Result: **PASS** (`physical_voice_receipt.json`)

- Mic WAV captured from CMTECK
- Local Whisper transcribed acoustic stimulus
- Local Ollama generated reply
- Local Kokoro synthesized and Mac speakers played audio
- No Hugging Face download required (`HF_HUB_OFFLINE=1`)

## Governance fail-closed

`GovernedToolExecutor` with no external grant raises `AuthorizationDenied` for `request_booking` / `check_availability`. No self-issued grants accepted.

## Persistence after worker teardown

Process exit leaves evidence files on disk (`physical_voice_receipt.json`, `physical-*-mic.wav`). Restarting the physical runner reloads Kokoro via HTTP and Whisper from local cache without re-provisioning.
