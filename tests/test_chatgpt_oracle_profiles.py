from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_profiles.py"


@pytest.fixture(autouse=True)
def default_workspace_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_CHATGPT_APP_NAME", "DevSpace")


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
    assert contract["pro_selection_policy"] == "explicit-only"
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
    assert contract["thinking_time"] == "extra-high"
    assert "research_picker" not in contract
    assert "research_app" not in contract
    assert contract["attachments"] == []
    assert contract["pro_selection_policy"] == "explicit-only"


@pytest.mark.parametrize("level", ["low", "Pro"])
def test_regular_reasoning_rejects_unsupported_level_without_downgrade(tmp_path: Path, level: str) -> None:
    profiles = load_profiles()
    with pytest.raises(profiles.OracleProfileError) as exc:
        profiles.build_launch_contract("plan", mission_path=(tmp_path / "mission.md").resolve(), reasoning_level=level)
    assert exc.value.code == "REGULAR_REASONING_UNAVAILABLE"
    assert exc.value.evidence["supported"] == ["Very High", "High", "Medium"]


def test_pro_attachment_is_oracle_attachment_only_and_manual_launches_nothing(tmp_path: Path) -> None:
    profiles = load_profiles()
    mission = (tmp_path / "prompt.txt").resolve()
    packet = (tmp_path / "packet.zip").resolve()
    pro = profiles.build_launch_contract("pro-attachment", mission_path=mission, attachment_paths=[mission, packet])
    manual = profiles.build_launch_contract("manual")
    assert pro["route"] == "oracle-pro-attachment-only"
    assert pro["app_policy"] == "forbidden"
    assert pro["oracle_launch"] is True
    assert pro["devspace_required"] is False
    assert pro["model"] == "gpt-5.6-sol"
    assert pro["task_kind"] == "pro"
    assert pro["thinking_time"] == "pro"
    assert pro["attachment_policy"] == "always"
    assert pro["attachments"] == [str(mission), str(packet)]
    assert pro["composer_prompt"].startswith(
        "Read the attached prompt/instructions and all attached files, then provide read-only analysis only."
    )
    assert "Do not create, edit, delete, or rename files" in pro["composer_prompt"]
    assert "@DevSpace" not in pro["composer_prompt"]
    assert manual["route"] == "manual-no-launch"
    assert manual["composer_prompt"] is None
    assert manual["oracle_launch"] is False


def test_read_oriented_regular_ui_effort_contracts_are_distinct_and_accept_korean_labels(tmp_path: Path) -> None:
    profiles = load_profiles()
    mission = (tmp_path / "mission.md").resolve()
    medium = profiles.build_launch_contract("review", mission_path=mission, reasoning_level="중간")
    high = profiles.build_launch_contract("review", mission_path=mission, reasoning_level="높음")
    very_high = profiles.build_launch_contract("review", mission_path=mission, reasoning_level="xhigh")

    assert medium["thinking_time"] == "standard"
    assert high["thinking_time"] == "extended"
    assert very_high["thinking_time"] == "extra-high"


@pytest.mark.parametrize("mode", ["direct", "edit", "orchestrator"])
def test_write_capable_regular_modes_require_highest_non_pro(tmp_path: Path, mode: str) -> None:
    profiles = load_profiles()
    mission = (tmp_path / "mission.md").resolve()
    with pytest.raises(profiles.OracleProfileError) as exc:
        profiles.build_launch_contract(mode, mission_path=mission, reasoning_level="High")
    assert exc.value.code == "WRITE_REQUIRES_HIGHEST_NON_PRO"
    assert exc.value.evidence["thinking_time"] == "extra-high"

    contract = profiles.build_launch_contract(mode, mission_path=mission)
    assert contract["reasoning_level"] == "Very High"
    assert contract["thinking_time"] == "extra-high"
    assert contract["action_authority"] == "mission-scoped-write"


def test_pro_attachment_includes_mission_once_and_regular_rejects_attachments(tmp_path: Path) -> None:
    profiles = load_profiles()
    mission = (tmp_path / "prompt.txt").resolve()
    pro = profiles.build_launch_contract("pro-attachment", mission_path=mission, attachment_paths=[])
    assert pro["attachments"] == [str(mission)]
    with pytest.raises(profiles.OracleProfileError) as exc:
        profiles.build_launch_contract("review", mission_path=mission, attachment_paths=[mission])
    assert exc.value.code == "REGULAR_ATTACHMENTS_FORBIDDEN"


def test_pro_is_explicit_readonly_devspace_without_attachments(tmp_path: Path) -> None:
    profiles = load_profiles()
    mission = (tmp_path / "mission.md").resolve()
    contract = profiles.build_launch_contract("pro", mission_path=mission)

    assert contract["route"] == "oracle-pro-devspace-readonly"
    assert contract["app_name"] == "DevSpace"
    assert contract["model"] == "gpt-5.6-sol"
    assert contract["model_strategy"] == "select"
    assert contract["thinking_time"] == "pro"
    assert profiles.PRO_THINKING_TIME == "pro"
    assert contract["research"] is False
    assert contract["attachments"] == []
    assert contract["action_authority"] == "read-only"
    assert contract["write_handoff"] == "regular-gpt-5.6-extra-high-devspace"
    assert contract["composer_prompt"].startswith(f"@DevSpace Read and analyze the read-only mission file: {mission}.")
    assert "without creating, editing, deleting, or renaming files" in contract["composer_prompt"]
    assert "separate regular GPT-5.6 extra-high DevSpace stage" in contract["composer_prompt"]
    with pytest.raises(profiles.OracleProfileError) as exc:
        profiles.build_launch_contract("pro", mission_path=mission, attachment_paths=[mission])
    assert exc.value.code == "PRO_DEVSPACE_ATTACHMENTS_FORBIDDEN"


@pytest.mark.parametrize("mode", ["pro-readonly", "pro_readonly", "pro readonly"])
def test_legacy_readonly_mode_names_do_not_silently_select_pro(tmp_path: Path, mode: str) -> None:
    profiles = load_profiles()
    with pytest.raises(profiles.OracleProfileError) as exc:
        profiles.build_launch_contract(mode, mission_path=(tmp_path / "mission.md").resolve())
    assert exc.value.code == "MODE_UNSUPPORTED"


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
