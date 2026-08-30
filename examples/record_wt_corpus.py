"""Record the WT-TTS-003 golden corpus with a real Pocket TTS backend.

Requires: pip install pocket-tts (pulls torch). Not run in CI.

    python examples/record_wt_corpus.py

Writes golden/<voice>/<language>/<id>.wav (mono int16 at the probed sample
rate) plus golden/manifest.sha256.json. The WER/MCD comparison gate lands
with a real ASR backend; this script only pins and records the corpus.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import wave
from array import array
from pathlib import Path

import yaml

from voxmaestro.tts.contract import ConsentRecord, LanguageLane, SynthesizeRequest, VoiceManifest
from voxmaestro.tts.pocket import PocketTTSBackend

CORPUS_PATH = Path(__file__).parent.parent / "docs" / "wt" / "tts_003_corpus.yaml"
OUT_DIR = Path("golden")
SESSION_ID = "wt-corpus"


def to_int16(pcm_f32: bytes) -> bytes:
    samples = array("f")
    samples.frombytes(pcm_f32)
    clipped = array("h", (int(max(-1.0, min(1.0, s)) * 32767) for s in samples))
    return clipped.tobytes()


def record(backend: PocketTTSBackend, language: str, corpus: dict) -> dict:
    caps = backend.capabilities()
    voice = VoiceManifest(
        voice_id=corpus["voice"],
        language=language,
        lane=LanguageLane.FAST,
        sample_rate=caps.sample_rates[0],
        backend_id=caps.backend_id,
        backend_version=caps.backend_version,
        quantization="int8" if "int8" in caps.quantizations else caps.quantizations[0],
        consent_record=ConsentRecord(
            voice_id=corpus["voice"],
            source="kyutai-demo",
            granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            license="demo",
            notes="WT-TTS-003 corpus recording only",
        ),
        session_id=SESSION_ID,
    )
    backend.open_session(SESSION_ID, voice)
    hashes: dict[str, str] = {}
    try:
        for utt in corpus["utterances"]:
            if utt["language"] != language:
                continue
            req = SynthesizeRequest(
                text=utt["text"],
                turn_id=utt["id"],
                session_id=SESSION_ID,
                voice=voice,
                language=language,
            )
            pcm = b"".join(chunk.pcm for chunk in backend.synthesize(req))
            path = OUT_DIR / corpus["voice"] / language / f"{utt['id']}.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(caps.sample_rates[0])
                wav.writeframes(to_int16(pcm))
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            print(f"recorded {path}")
    finally:
        backend.close_session(SESSION_ID)
    return hashes


def main() -> int:
    corpus = yaml.safe_load(CORPUS_PATH.read_text())
    languages = sorted({utt["language"] for utt in corpus["utterances"]})
    manifest: dict[str, str] = {}
    for language in languages:
        backend = PocketTTSBackend(language=language, quantize=True)
        manifest.update(record(backend, language, corpus))
    OUT_DIR.mkdir(exist_ok=True)
    manifest_path = OUT_DIR / "manifest.sha256.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"\nwrote {manifest_path} ({len(manifest)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
