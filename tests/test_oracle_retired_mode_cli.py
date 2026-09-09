"""Retired public mode commands cannot launch fresh browser work."""
import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("name, entry", [
    ("chatgpt_oracle_multi", "run_multi"),
    ("chatgpt_oracle_comprehensive", "run_workflow"),
])
def test_retired_mode_cli_refuses_before_loading_mission(name, entry, monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / "bin" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"retired_cli_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, entry, lambda *args, **kwargs: pytest.fail("retired mode launched"))
    assert module.main(["--manifest", "does-not-exist.json"]) == 1
    assert "runs are retired" in capsys.readouterr().out
