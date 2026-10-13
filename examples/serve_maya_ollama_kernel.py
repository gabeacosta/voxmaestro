"""Start a checked, local-only Ollama adapter for Maya staging.

Usage:
    python examples/serve_maya_ollama_kernel.py --intent-model vsai-intent-v7:latest --generation-model INSTALLED_QUALIFIED_CHAT_MODEL

The model must already exist. This does not pull/install or change Ollama
settings and never listens on a public/tailnet interface.
"""
from __future__ import annotations

import argparse
import logging

from aiohttp import web

from voxmaestro.integrations.ollama_kernel import create_ollama_kernel


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Private Maya Ollama kernel bridge")
    parser.add_argument("--intent-model", required=True, help="Exact installed intent model")
    parser.add_argument("--generation-model", required=True, help="Distinct qualified installed chat model")
    parser.add_argument("--port", type=int, default=7788)
    opts = parser.parse_args(argv)
    if opts.port != 7788:
        raise ValueError("Maya staging uses fixed localhost port 7788")
    app = create_ollama_kernel(intent_model=opts.intent_model,
                               generation_model=opts.generation_model)
    logging.getLogger("aiohttp.access").disabled = True
    print("Maya kernel: read-only Ollama bridge; localhost:7788; no tools")
    print("Intent model: " + opts.intent_model)
    print("Conversation model: " + opts.generation_model)
    web.run_app(
        app, host="127.0.0.1", port=opts.port, access_log=None,
        shutdown_timeout=5, print=lambda *_: None,
    )


if __name__ == "__main__":
    main()
