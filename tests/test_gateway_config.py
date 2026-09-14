from pathlib import Path

import pytest

from examples.serve_gateway import CONFIG_PATH, parse_args
from voxmaestro.conductor import SchemaLoader


def test_default_config_unchanged():
    assert parse_args([]).config == CONFIG_PATH
    assert CONFIG_PATH.name == "microscroll_landing.yaml"
    assert SchemaLoader.load(CONFIG_PATH)


def test_explicit_bonsai_config():
    path = Path("examples/microscroll_landing_bonsai.yaml")
    assert parse_args(["--config", str(path)]).config == path
    assert SchemaLoader.load(path)["generation"]["provider"] == "remote_worker"


def test_missing_config_fails(tmp_path):
    args = parse_args(["--config", str(tmp_path / "missing.yaml")])
    with pytest.raises(FileNotFoundError):
        SchemaLoader.load(args.config)
