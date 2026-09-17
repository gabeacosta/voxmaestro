from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.serve_gateway import CONFIG_PATH, build_tool_payload, parse_args
from voxmaestro.conductor import SchemaLoader


def test_default_config_unchanged():
    assert parse_args([]).config == CONFIG_PATH
    assert CONFIG_PATH.name == "microscroll_landing.yaml"
    assert SchemaLoader.load(CONFIG_PATH)


def test_explicit_bonsai_config():
    path = Path("examples/microscroll_landing_bonsai.yaml")
    assert parse_args(["--config", str(path)]).config == path
    assert SchemaLoader.load(path)["generation"]["provider"] == "remote_worker"


def test_small_fleet_config_pins_admitted_qwen_worker():
    path = Path("examples/microscroll_landing_small_fleet.yaml")
    config = SchemaLoader.load(path)

    assert config["generation"]["provider"] == "remote_worker"
    assert config["generation"]["worker_id"] == "qwen-l1-01"
    assert "successful request_booking" in config["generation"]["system_prompt"]
    service_intent = next(
        item for item in config["intent"]["intents"] if item["id"] == "service_question"
    )
    retrieval = config["tools"]["retrieve_business_context"]
    assert service_intent["tool"] == "retrieve_business_context"
    assert retrieval["endpoint"] == "http://127.0.0.1:8092/v1/retrieve"
    assert retrieval["on_failure"] == {
        "message": "I cannot verify that information right now."
    }


def test_retrieval_tool_payload_uses_runtime_turn_and_session_language():
    tool = {
        "request_context": {
            "latest_caller_text_as": "query",
            "session_language_as": "language",
            "default_language": "en",
        }
    }
    context = SimpleNamespace(
        conversation_history=[
            {"role": "caller", "content": "Earlier"},
            {"role": "assistant", "content": "Response"},
            {"role": "caller", "content": "¿Aceptan pacientes nuevos?"},
        ],
        metadata={"locale": "es-MX"},
    )

    assert build_tool_payload(tool, {"tenant": "dental-demo"}, context) == {
        "tenant": "dental-demo",
        "query": "¿Aceptan pacientes nuevos?",
        "language": "es",
    }


def test_missing_config_fails(tmp_path):
    args = parse_args(["--config", str(tmp_path / "missing.yaml")])
    with pytest.raises(FileNotFoundError):
        SchemaLoader.load(args.config)
