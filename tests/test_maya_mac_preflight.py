"""Read-only Mac preflight regression and false-green prevention."""
from __future__ import annotations

import json
import runpy
from pathlib import Path


SCRIPT = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "examples" / "maya_mac_preflight.py")
)
host = SCRIPT["tailnet_origin"]
occupied = SCRIPT["occupied_serve_port"]
report = SCRIPT["report"]


def test_tailnet_hostname_from_actual_tailscale_json():
    valid = json.dumps({"Self": {"DNSName": "mini.example.ts.net."}})
    assert host(valid) == "https://mini.example.ts.net:8443"
    assert host("not json") is None
    assert host(json.dumps({"Self": {"DNSName": "host.example.com."}})) is None
    assert host(json.dumps({"Self": {"DNSName": "evil@host.ts.net"}})) is None


def test_existing_serve_or_funnel_8443_is_a_hard_block():
    assert occupied('{"HTTPS":{"8443":{"Handlers":{"/":{}}}}}')
    assert occupied("https://mini.example.ts.net:8443")
    assert not occupied('{"TCP":{":443":{}}}')
    assert occupied(None)


def test_uninspectable_tailscale_status_fails_closed(tmp_path):
    repo_root = tmp_path / "voxmaestro"
    site = tmp_path / "design-factory-maya-stage" / "workspaces/client-sites/voicescheduleai"
    repo_root.mkdir()
    (repo_root / "pyproject.toml").write_text("[project]\n")
    (site / "site").mkdir(parents=True)
    (site / "site" / "index.html").write_text("noindex")
    (site / "site" / "voxmaestro-voice-ui.mjs").write_text("export const x = true")
    (site / "voxmaestro-readonly.yaml").write_text("tools: {}\n")

    original = SCRIPT["command_output"]
    old_bind = report.__globals__["port_bindable"]
    old_listen = report.__globals__["tcp_listening"]
    try:
        SCRIPT["command_output"] = lambda *args: (
            False, ""
        ) if "tailscale" in args else (True, "a" * 40)
        # report() uses its own module global dictionary.
        globals_dict = report.__globals__
        globals_dict["command_output"] = SCRIPT["command_output"]
        globals_dict["port_bindable"] = lambda *args: True
        globals_dict["tcp_listening"] = lambda *args: True
        status = report(repo_root, site)
        assert status["result"] == "BLOCKED"
        assert not status["checks"]["serve_state_inspectable"]
        assert not status["checks"]["https_8443_unallocated"]
        assert status["private_origin"] is None
    finally:
        report.__globals__["command_output"] = original
        report.__globals__["port_bindable"] = old_bind
        report.__globals__["tcp_listening"] = old_listen


def test_success_output_does_not_expose_mac_dns_by_default(tmp_path):
    repo = tmp_path / "voxmaestro"
    site = tmp_path / "design-factory-maya-stage" / "workspaces/client-sites/voicescheduleai"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("ok")
    (site / "site").mkdir(parents=True)
    (site / "site" / "index.html").write_text("noindex")
    (site / "site" / "voxmaestro-voice-ui.mjs").write_text("ok")
    (site / "voxmaestro-readonly.yaml").write_text("ok")

    original = report.__globals__["command_output"]
    old_bind = report.__globals__["port_bindable"]
    old_listen = report.__globals__["tcp_listening"]
    try:
        def fake(*args):
            if args[:2] == ("tailscale", "status"):
                return True, json.dumps({"Self": {"DNSName": "mini.example.ts.net."}})
            if args[0] == "tailscale":
                return True, '{"TCP":{":443":{}}}'
            return True, "a" * 40
        report.__globals__["command_output"] = fake
        report.__globals__["port_bindable"] = lambda *args: True
        report.__globals__["tcp_listening"] = lambda *args: True
        # Other host tools may be absent on CI: don't assert global pass.
        status = report(repo, site)
        assert "mini.example.ts.net" not in json.dumps(status)
        assert status["private_origin"] == "[detected; use --show-origin locally]"
        assert status["checks"]["https_8443_unallocated"]
    finally:
        report.__globals__["command_output"] = original
        report.__globals__["port_bindable"] = old_bind
        report.__globals__["tcp_listening"] = old_listen
