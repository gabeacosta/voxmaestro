"""Start a checked, local-only Ollama adapter for Maya staging.

Usage:
    python examples/serve_maya_ollama_kernel.py --model EXACT_NAME_FROM_OLLAMA_LIST

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
    parser.add_argument("--model", required=True, help="Exact model from ollama list")
    parser.add_argument("--port", type=int, default=7788)
    opts = parser.parse_args(argv)
    if opts.port != 7788:
        raise ValueError("Maya staging uses fixed localhost port 7788")
    app = create_ollama_kernel(model=opts.model)
    logging.getLogger("aiohttp.access").disabled = True
    print("Maya kernel: read-only Ollama bridge; localhost:7788; no tools")
    print("Requested installed model: " + opts.model)
    web.run_app(
        app, host="127.0.0.1", port=opts.port, access_log=None,
        shutdown_timeout=5, print=lambda *_: None,
    )


if __name__ == "__main__":
    main()
