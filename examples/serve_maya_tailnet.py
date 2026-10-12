"""Start Maya V3 on localhost for tailnet-only HTTPS via Tailscale Serve.

Preconditions:
    pip install -e '.[voice_web]'
    python examples/serve_maya_tailnet.py \
      --origin https://<mac>.<tailnet>.ts.net:8443 \
      --allowed-login <your-tailnet-user-login> \
      --site-dir <path-to-approved-design-factory-Maya-V3-voice-site> \
      --config <path-to-voxmaestro-readonly.yaml>

Then, in a separate terminal, ONLY after checking Serve/Funnel status:
    tailscale serve --https=8443 --bg http://127.0.0.1:7789

Never run tailscale funnel. This server has no real booking capabilities.
"""
from __future__ import annotations

import argparse
import copy
import logging
import socket
from pathlib import Path
from urllib.parse import urlsplit

from voxmaestro import VoxMaestroRuntime
from voxmaestro.conductor import SchemaLoader
from voxmaestro.fleet import fleet_from_config
from voxmaestro.integrations.tailnet_stage import make_tailnet_stage
from voxmaestro.integrations.web_session import WebSessionAdapter


def readonly_kernel_config(filename: Path) -> dict:
    """Qualify actual local model endpoints and deny every tool/effect path."""
    config = copy.deepcopy(SchemaLoader.load(filename))
    if not isinstance(config, dict):
        raise ValueError("VoxMaestro YAML must decode to a mapping")
    if config.get("tools") or (config.get("handoff") or {}).get("delivery"):
        raise ValueError("No effect-capable tools or delivery are allowed")
    for key, suffix in (("intent", "/v1/intent"), ("generation", "/v1/generate")):
        section = config.get(key)
        if not isinstance(section, dict) or section.get("provider") != "kernel":
            raise ValueError("Tailnet stage requires pinned local kernel " + key)
        endpoint = section.get("endpoint")
        if not isinstance(endpoint, str):
            raise ValueError(key + " endpoint missing")
        u = urlsplit(endpoint)
        if (
            u.scheme != "http" or u.hostname not in {"localhost", "127.0.0.1"}
            or not u.port or u.username or u.password or u.query or u.fragment
            or u.path not in {"", "/", suffix}
        ):
            raise ValueError(key + " must target the local HTTP kernel only")
        # Kernel fleet adapters append the /v1/... path themselves.
        section["endpoint"] = f"http://127.0.0.1:{u.port}"
    if config["intent"]["endpoint"] != config["generation"]["endpoint"]:
        raise ValueError("Single local-kernel endpoint required for staging")
    return config


def kernel_preflight(config: dict) -> None:
    endpoint = urlsplit(config["generation"]["endpoint"])
    with socket.create_connection(("127.0.0.1", endpoint.port), timeout=2):
        pass  # TCP availability only. A full model turn is still an E2E gate.


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Private tailnet-only Maya staging")
    parser.add_argument("--origin", required=True, help="Exact HTTPS tailnet origin on :8443")
    parser.add_argument("--allowed-login", required=True, action="append",
                        help="Tailscale-User-Login allowed to use this instance")
    parser.add_argument("--site-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--port", type=int, default=7789)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    from aiohttp import web

    args = parse_args(argv)
    if not 1024 <= args.port <= 65535:
        raise ValueError("Staging port must be nonprivileged")
    config = readonly_kernel_config(args.config)
    kernel_preflight(config)
    classifier, generator = fleet_from_config(config)
    if not classifier or not generator:
        raise ValueError("Local intent and generation adapters must be available")
    runtime = VoxMaestroRuntime(config, intent_classifier=classifier)
    adapter = WebSessionAdapter(runtime, generation_adapter=generator)
    app = make_tailnet_stage(
        adapter=adapter,
        site_dir=args.site_dir,
        origin=args.origin,
        allowed_logins=set(args.allowed_login),
    )
    logging.getLogger("aiohttp.access").disabled = True
    print("Maya voice staging: localhost only; conversation:readonly; effects disabled")
    print("Local socket: http://127.0.0.1:" + str(args.port))
    print("Expected tailnet origin: " + args.origin)
    print("Use Tailscale Serve ONLY after verifying its port 8443 is free.")
    web.run_app(
        app,
        host="127.0.0.1",
        port=args.port,
        access_log=None,
        shutdown_timeout=5,
        print=lambda *_: None,
    )


if __name__ == "__main__":
    main()
