"""Physical / offline spoken OpHub owner-workspace queries.

Read-only. Does not restart the owner VoxMaestro gateway on :7780.

    # Offline fixture (no OpHub process):
    HF_HUB_OFFLINE=1 .venv/bin/python examples/run_ophub_voice_query.py --fixture \\
      tests/fixtures/ophub_owner_workspace_fixture.json

    # Live OpHub serve (separate process on :8899) + Kokoro speak:
    HF_HUB_OFFLINE=1 .venv/bin/python examples/run_ophub_voice_query.py \\
      --ophub-url http://127.0.0.1:8899 --speak
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voxmaestro.ophub_voice import (  # noqa: E402
    OphubVoiceClient,
    classify_owner_query,
    load_fixture,
    render_spoken_answer,
    workspace_is_stale,
)

EVIDENCE = ROOT / "evidence" / "vox-ophub-convergence-004"

DEFAULT_QUERIES = [
    "Which agents are running?",
    "What is Codex working on?",
    "Show my active tasks.",
    "Which agents are blocked?",
    "What has been independently verified?",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--ophub-url", default="http://127.0.0.1:8899")
    p.add_argument("--fixture", type=Path, default=None)
    p.add_argument("--speak", action="store_true", help="Play answers via local Kokoro")
    p.add_argument("--query", action="append", default=[])
    return p.parse_args()


def _speak(text: str) -> dict:
    from voxmaestro.tts.contract import (
        ConsentRecord,
        LanguageLane,
        SynthesizeRequest,
        VoiceManifest,
    )
    from voxmaestro.tts.kokoro import KokoroTTSBackend
    import numpy as np
    import sounddevice as sd

    tts = KokoroTTSBackend(default_voice="bm_george", http_timeout_s=180.0)
    caps = tts.capabilities()
    session = "ophub-voice"
    voice = VoiceManifest(
        voice_id="bm_george",
        language="en",
        lane=LanguageLane.FAST,
        sample_rate=caps.sample_rates[0],
        backend_id=caps.backend_id,
        backend_version=caps.backend_version,
        quantization=caps.quantizations[0],
        consent_record=ConsentRecord(
            "bm_george",
            "kokoro-onnx-local",
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "Apache-2.0",
        ),
        session_id=session,
    )
    t0 = time.monotonic()
    tts.open_session(session, voice)
    chunks = list(
        tts.synthesize(
            SynthesizeRequest(
                text=text,
                turn_id="ophub-answer",
                session_id=session,
                voice=voice,
                language="en",
            )
        )
    )
    tts.close_session(session)
    ttfa_ms = (time.monotonic() - t0) * 1000 if chunks else None
    pcm = b"".join(c.pcm for c in chunks)
    if pcm:
        n = len(pcm) // 4
        samples = np.frombuffer(pcm[: n * 4], dtype="<f4")
        sd.play(samples, samplerate=caps.sample_rates[0])
        sd.wait()
    return {"tts_chunks": len(chunks), "ttfa_ms": ttfa_ms}


def main() -> int:
    args = parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    queries = args.query or DEFAULT_QUERIES
    receipt: dict = {
        "mission": "VOX-OPHUB-CONVERGENCE-004",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ophub_url": str(args.ophub_url),
        "fixture": str(args.fixture) if args.fixture else None,
        "speak": bool(args.speak),
        "turns": [],
        "owner_gateway_7780_touched": False,
    }

    client = OphubVoiceClient(base_url=args.ophub_url)
    for q in queries:
        t0 = time.monotonic()
        if args.fixture:
            workspace = load_fixture(str(args.fixture))
            intent = classify_owner_query(q)
            stale = workspace_is_stale(workspace, max_age_s=client.max_age_s)
            spoken = render_spoken_answer(intent, workspace, stale=stale)
            result = {
                "status": "STALE" if stale else "OK_FIXTURE",
                "intent": intent,
                "spoken": spoken,
                "workspace": {
                    "schema": workspace.get("schema"),
                    "ok": workspace.get("ok"),
                    "fetched_at": workspace.get("fetched_at"),
                },
            }
        else:
            result = client.answer(q)
        latency_ms = (time.monotonic() - t0) * 1000
        turn = {
            "query": q,
            "intent": result["intent"],
            "status": result["status"],
            "spoken": result["spoken"],
            "answer_latency_ms": latency_ms,
            "workspace": result.get("workspace"),
        }
        if args.speak and result.get("spoken"):
            try:
                turn["tts"] = _speak(result["spoken"])
            except Exception as exc:  # noqa: BLE001
                turn["tts"] = {"error": str(exc)}
        receipt["turns"].append(turn)
        print(f"Q: {q}")
        print(f"A ({result['status']}): {result['spoken']}")
        print("---")

    receipt["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    receipt["status"] = (
        "PASS"
        if all(t["status"] in {"OK", "OK_FIXTURE", "STALE"} for t in receipt["turns"])
        else "FAIL"
    )
    out = EVIDENCE / (
        "physical_ophub_voice_receipt.json"
        if not args.fixture
        else "offline_ophub_voice_receipt.json"
    )
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"wrote {out} status={receipt['status']}")
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
