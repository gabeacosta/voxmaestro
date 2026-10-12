"""WT-VOICE-TAILNET-001: the actual Mac launcher refuses unsafe kernels."""
from __future__ import annotations

import copy
import runpy
import socket
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPT = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "examples" / "serve_maya_tailnet.py")
)
normalize = SCRIPT["readonly_kernel_config"]
kernel_preflight = SCRIPT["kernel_preflight"]

BASE = {
    "tools": {},
    "handoff": {"delivery": []},
    "intent": {
        "provider": "kernel",
        "endpoint": "http://127.0.0.1:7788/v1/intent",
    },
    "generation": {
        "provider": "kernel",
        "endpoint": "http://127.0.0.1:7788/v1/generate",
    },
}


def check(source):
    with patch("voxmaestro.conductor.SchemaLoader.load", return_value=source):
        return normalize(Path("/unused-test-configuration.yaml"))


def test_normalizes_readonly_kernel_endpoints_without_mutating_source():
    source = copy.deepcopy(BASE)
    result = check(source)
    assert result["intent"]["endpoint"] == "http://127.0.0.1:7788"
    assert result["generation"]["endpoint"] == "http://127.0.0.1:7788"
    assert source == BASE


@pytest.mark.parametrize(
    "intent, generation, tools, handoff",
    [
        ("https://malicious.example/v1/intent",
         "http://127.0.0.1:7788/v1/generate", {}, {"delivery": []}),
        ("http://127.0.0.1:7788/v1/intent",
         "http://127.0.0.1:7790/v1/generate", {}, {"delivery": []}),
        ("http://127.0.0.1:7788/v1/intent?token=abc",
         "http://127.0.0.1:7788/v1/generate", {}, {"delivery": []}),
        ("http://127.0.0.1:7788/v1/intent",
         "http://127.0.0.1:7788/v1/generate",
         {"book": {"endpoint": "https://calendar.example"}}, {"delivery": []}),
        ("http://127.0.0.1:7788/v1/intent",
         "http://127.0.0.1:7788/v1/generate",
         {}, {"delivery": [{"channel": "crm"}]}),
    ],
)
def test_refuses_remote_model_endpoints_and_effect_capable_config(
    intent, generation, tools, handoff
):
    source = copy.deepcopy(BASE)
    source["intent"]["endpoint"] = intent
    source["generation"]["endpoint"] = generation
    source["tools"] = tools
    source["handoff"] = handoff
    with pytest.raises(ValueError):
        check(source)


def test_local_kernel_tcp_preflight_rejects_unreachable_service():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        kernel_preflight({"generation": {"endpoint": f"http://127.0.0.1:{port}"}})
    with pytest.raises(OSError):
        kernel_preflight({"generation": {"endpoint": f"http://127.0.0.1:{port}"}})
