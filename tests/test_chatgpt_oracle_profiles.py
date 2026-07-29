from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_profiles.py"


def load_profiles():
    name = "chatgpt_oracle_profiles_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode", ["direct", "plan", "review", "edit", "orchestrator"])
def test_regular_modes_use_plain_devspace_handoff_and_high_only(tmp_path: Path, mode: str) -> None:
    profiles = load_profiles()
    mission = (tmp_path / "mission.md").resolve()
    contract = profiles.build_launch_contract(mode, mission_path=mission)
    assert contract["route"] == "oracle-devspace"
    assert contract["reasoning_level"] == "Very High"
    assert contract["attachments"] == []
    assert contract["app_picker"] is False
    assert contract["app_settings_automation"] is False
    assert contract["composer_prompt"].startswith(
        f"@DevSpace Read and execute the mission file: {mission}."
    )
    assert "retry that same exact root once" in contract["composer_prompt"]
    assert "never substitute a parent, child, active workspace" in contract["composer_prompt"]
    assert "\n" not in contract["composer_prompt"]


def test_deep_research_is_only_a_mode_flag() -> None:
    profiles = load_profiles()
    contract = profiles.build_launch_contract("deep_research", mission_path=Path.cwd().resolve() / "mission.md")
    assert contract["research"] is True
    assert contract["reasoning_level"] == "Very High"
    assert "research_picker" not in contract
    assert "research_app" not in contract
    assert contract["attachments"] == []


@pytest.mark.parametrize("level", ["Very High", "Extra High", "매우 높음", "heavy"])
def test_regular_reasoning_aliases_normalize_to_very_high(tmp_path: Path, level: str) -> None:
    profiles = load_profiles()
    contract = profiles.build_launch_contract(
        "plan",
        mission_path=(tmp_path / "mission.md").resolve(),
        reasoning_level=level,
    )
    assert contract["reasoning_level"] == "Very High"


@pytest.mark.parametrize("level", ["xhigh", "Medium"])
def test_regular_reasoning_rejects_unsupported_level_without_downgrade(tmp_path: Path, level: str) -> None:
    profiles = load_profiles()
    with pytest.raises(profiles.OracleProfileError) as exc:
        profiles.build_launch_contract("plan", mission_path=(tmp_path / "mission.md").resolve(), reasoning_level=level)
    assert exc.value.code == "REGULAR_REASONING_UNAVAILABLE"
    assert exc.value.evidence["supported"] == ["Very High", "High"]


def test_pro_is_oracle_attachment_only_and_manual_launches_nothing(tmp_path: Path) -> None:
    profiles = load_profiles()
    mission = (tmp_path / "prompt.txt").resolve()
    packet = (tmp_path / "packet.zip").resolve()
    pro = profiles.build_launch_contract("pro", mission_path=mission, attachment_paths=[mission, packet])
    manual = profiles.build_launch_contract("manual")
    assert pro["route"] == "oracle-pro-attachment-only"
    assert pro["app_policy"] == "forbidden"
    assert pro["oracle_launch"] is True
    assert pro["devspace_required"] is False
    assert pro["model"] == "gpt-5.5-pro"
    assert pro["attachment_policy"] == "always"
    assert pro["attachments"] == [str(mission), str(packet)]
    assert pro["composer_prompt"] == "Read the attached prompt/instructions and all attached files, then complete the task."
    assert "@DevSpace" not in pro["composer_prompt"]
    assert manual["route"] == "manual-no-launch"
    assert manual["composer_prompt"] is None
    assert manual["oracle_launch"] is False


def test_pro_includes_mission_once_and_regular_rejects_attachments(tmp_path: Path) -> None:
    profiles = load_profiles()
    mission = (tmp_path / "prompt.txt").resolve()
    pro = profiles.build_launch_contract("pro", mission_path=mission, attachment_paths=[])
    assert pro["attachments"] == [str(mission)]
    with pytest.raises(profiles.OracleProfileError) as exc:
        profiles.build_launch_contract("review", mission_path=mission, attachment_paths=[mission])
    assert exc.value.code == "REGULAR_ATTACHMENTS_FORBIDDEN"


def test_relative_mission_is_rejected(tmp_path: Path) -> None:
    profiles = load_profiles()
    with pytest.raises(profiles.OracleProfileError) as exc:
        profiles.build_launch_contract("edit", mission_path="relative.md")
    assert exc.value.code == "MISSION_PATH_ABSOLUTE_REQUIRED"


def test_cli_resolve_is_machine_readable_and_launch_free(tmp_path: Path) -> None:
    mission = (tmp_path / "mission.md").resolve()
    completed = subprocess.run(
        [sys.executable, str(MODULE_PATH), "resolve", "--mode", "review", "--mission-path", str(mission)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["contract"]["composer_prompt"].startswith("@DevSpace ")
