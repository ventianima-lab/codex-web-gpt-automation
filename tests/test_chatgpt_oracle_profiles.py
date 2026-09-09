from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_profiles.py"


def load_module():
    name = "chatgpt_oracle_profiles_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    root.mkdir()
    mission = root / "mission.md"
    mission.write_text("Do the requested task.\n", encoding="utf-8")
    return root, mission


def test_public_profile_is_one_execution_flow(tmp_path: Path):
    profiles = load_module()
    root, mission = project(tmp_path)

    contract = profiles.build_execution_contract(project_root=root, mission_path=mission)

    assert contract["schema"] == "codex.chatgpt.oracle-execution/v1"
    assert contract["model"] == "latest"
    assert contract["effort"] == "pro"
    assert contract["app_name"] == "codex"
    assert contract["oracle_carrier"] == {"model": "gpt-5.6-sol", "model_strategy": "current"}
    assert contract["archive"] == "never"
    assert contract["temporary_chat"] is True
    assert "mode" not in contract


@pytest.mark.parametrize("effort", ["pro", "extra-high"])
def test_validated_effort_choice(tmp_path: Path, effort: str):
    profiles = load_module()
    root, mission = project(tmp_path)
    assert profiles.build_execution_contract(
        project_root=root, mission_path=mission, effort=effort
    )["effort"] == effort


def test_explicit_gpt56_uses_select_without_sending_latest_slug(tmp_path: Path):
    profiles = load_module()
    root, mission = project(tmp_path)
    contract = profiles.build_execution_contract(
        project_root=root, mission_path=mission, model="gpt-5.6-sol"
    )
    assert contract["oracle_carrier"] == {"model": "gpt-5.6-sol", "model_strategy": "select"}


def test_retired_mode_api_is_not_public(tmp_path: Path):
    profiles = load_module()
    assert not hasattr(profiles, "resolve_profile")
    assert not hasattr(profiles, "build_launch_contract")


def test_app_name_is_configurable_but_defaults_to_codex(tmp_path: Path):
    profiles = load_module()
    root, mission = project(tmp_path)
    assert profiles.build_execution_contract(
        project_root=root, mission_path=mission, app_name="git-bot"
    )["app_name"] == "git-bot"


def test_mission_must_stay_inside_approved_root(tmp_path: Path):
    profiles = load_module()
    root, _ = project(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("no\n", encoding="utf-8")
    with pytest.raises(profiles.EXECUTOR.ExecutionError) as exc:
        profiles.build_execution_contract(project_root=root, mission_path=outside)
    assert exc.value.code == "MISSION_OUTSIDE_APPROVED_ROOT"
