"""Read-only Mac host preflight for WT-VOICE-TAILNET-001.

Does NOT configure Tailscale Serve or Funnel, fetch code, launch models,
install software, modify credentials, or start a network listener.

Run from a pinned checkout:
    python3 examples/maya_mac_preflight.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

GATEWAY_PORT = 7789
KERNEL_PORT = 7788
SERVE_HTTPS_PORT = 8443


def command_output(*args: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return proc.returncode == 0, proc.stdout


def tcp_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def port_bindable(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
        return True
    except OSError:
        return False


def tailnet_origin(status: str) -> str | None:
    try:
        data = json.loads(status)
    except (ValueError, TypeError):
        return None
    name = data.get("Self", {}).get("DNSName", "")
    if not isinstance(name, str) or not name.rstrip(".").endswith(".ts.net"):
        return None
    # Prevent a malicious/garbled diagnostic from constructing a URL.
    candidate = name.rstrip(".").lower()
    parts = urlsplit("https://" + candidate)
    if (
        not parts.hostname or parts.hostname != candidate
        or not all(ch.isalnum() or ch in "-." for ch in candidate)
    ):
        return None
    return "https://" + candidate + ":" + str(SERVE_HTTPS_PORT)


def occupied_serve_port(status: str, port: int = SERVE_HTTPS_PORT) -> bool:
    """Conservative: any current reference to :8443 in Serve/Funnel state blocks.

    False positives only prevent deployment; no automatic state changes occur.
    """
    if not isinstance(status, str):
        return True
    return (
        ":" + str(port) in status
        or '"' + str(port) + '"' in status
        or "https=" + str(port) in status
    )


def report(repo_root: Path, site_root: Path, show_sensitive: bool = False) -> dict:
    checks = {}
    checks["python_3_10_plus"] = sys.version_info >= (3, 10)
    checks["git_installed"] = shutil.which("git") is not None
    checks["tailscale_cli_installed"] = shutil.which("tailscale") is not None
    checks["voxmaestro_repo_present"] = (repo_root / "pyproject.toml").is_file()
    checks["maya_site_repo_present"] = (site_root / "site" / "index.html").is_file()
    checks["maya_voice_module_present"] = (
        site_root / "site" / "voxmaestro-voice-ui.mjs"
    ).is_file()
    checks["readonly_yaml_present"] = (
        site_root / "voxmaestro-readonly.yaml"
    ).is_file()
    checks["gateway_port_7789_free"] = port_bindable("127.0.0.1", GATEWAY_PORT)
    checks["kernel_port_7788_listening"] = tcp_listening(
        "127.0.0.1", KERNEL_PORT
    )
    ok_tailnet, tailnet = command_output("tailscale", "status", "--json")
    origin = tailnet_origin(tailnet) if ok_tailnet else None
    checks["tailscale_connected_with_https_hostname"] = origin is not None

    ok_serve, serve = command_output("tailscale", "serve", "status", "--json")
    ok_funnel, funnel = command_output("tailscale", "funnel", "status", "--json")
    # If either status command cannot be inspected, deployment must STOP.
    checks["serve_state_inspectable"] = ok_serve
    checks["funnel_state_inspectable"] = ok_funnel
    checks["https_8443_unallocated"] = (
        ok_serve and ok_funnel
        and not occupied_serve_port(serve)
        and not occupied_serve_port(funnel)
    )

    git_refs = {}
    if checks["git_installed"]:
        for label, target in [("voxmaestro", repo_root),
                              ("design_factory", site_root.parent)]:
            ok, sha = command_output("git", "-C", str(target), "rev-parse", "HEAD")
            git_refs[label] = sha.strip()[:12] if ok else "UNAVAILABLE"

    blocker_keys = [key for key, yes in checks.items() if not yes]
    return {
        "specimen": "WT-VOICE-TAILNET-001",
        "operation": "READ_ONLY_MAC_PREFLIGHT",
        "result": "HOST_CHECKS_PASS" if not blocker_keys else "BLOCKED",
        "checks": checks,
        "blockers": blocker_keys,
        "checked_refs_short": git_refs,
        "private_origin": origin if show_sensitive else (
            "[detected; use --show-origin locally]" if origin else None
        ),
        "next_step": (
            "Review tailnet ACL, Serve identity and pinned commits; "
            "no service was started. Manual launch still required."
        ),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    default_site = Path.home() / "design-factory" / "workspaces/client-sites/voicescheduleai"
    show_origin = "--show-origin" in sys.argv[1:]
    record = report(root, default_site, show_sensitive=show_origin)
    print(json.dumps(record, indent=2, sort_keys=True))
    # This preflight intentionally performs NO network modifications.
    return 0 if record["result"] == "HOST_CHECKS_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
