from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_dispatch.py"


@pytest.fixture(autouse=True)
def default_workspace_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_CHATGPT_APP_NAME", "DevSpace")


def load():
    spec = importlib.util.spec_from_file_location("oracle_dispatch_test", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_regular_and_deep_research_compile_to_oracle_without_attachments(tmp_path: Path) -> None:
    module = load()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    for mode, research in (("direct", "off"), ("edit", "off"), ("orchestrator", "off"), ("deep-research", "deep")):
        target = tmp_path / f"{mode}.json"
        result = module.compile_manifest(
            mode=mode, project_root=tmp_path, mission_path=mission, output_path=target
        )
        value = json.loads(target.read_text(encoding="utf-8"))
        assert result["contract"]["attachments"] == []
        assert value["app_name"] == "DevSpace"
        assert value["task_outcome_contract"] == "v1"
        assert value["model"] == "gpt-5.6"
        assert value["model_strategy"] == "select"
        assert value["thinking_time"] == "extra-high"
        assert value["research"] == research


def test_regular_high_is_forwarded_as_the_visible_high_tier(tmp_path: Path) -> None:
    module = load()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    target = tmp_path / "high.json"

    result = module.compile_manifest(
        mode="direct",
        project_root=tmp_path,
        mission_path=mission,
        output_path=target,
        reasoning_level="High",
    )

    value = json.loads(target.read_text(encoding="utf-8"))
    assert result["contract"]["reasoning_level"] == "High"
    assert result["contract"]["thinking_time"] == "extended"
    assert value["thinking_time"] == "extended"


def test_configured_app_name_is_forwarded_to_manifest_and_composer(tmp_path: Path) -> None:
    module = load()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    target = tmp_path / "custom-app.json"

    result = module.compile_manifest(
        mode="direct",
        project_root=tmp_path,
        mission_path=mission,
        output_path=target,
        app_name="codex",
    )

    value = json.loads(target.read_text(encoding="utf-8"))
    assert value["app_name"] == "codex"
    assert result["contract"]["composer_prompt"].startswith("@codex ")


def test_regular_medium_is_forwarded_as_the_visible_medium_tier(tmp_path: Path) -> None:
    module = load()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    target = tmp_path / "medium.json"

    result = module.compile_manifest(
        mode="direct",
        project_root=tmp_path,
        mission_path=mission,
        output_path=target,
        reasoning_level="Medium",
    )

    value = json.loads(target.read_text(encoding="utf-8"))
    assert result["contract"]["reasoning_level"] == "Medium"
    assert result["contract"]["thinking_time"] == "standard"
    assert value["thinking_time"] == "standard"


def test_pro_attachment_compiles_attachment_only_oracle_and_manual_never_launches(tmp_path: Path) -> None:
    module = load()
    prompt = tmp_path / "prompt.txt"
    packet = tmp_path / "packet.zip"
    prompt.write_text("instructions", encoding="utf-8")
    packet.write_bytes(b"PK\x03\x04packet")
    pro_target = tmp_path / "pro.json"
    pro = module.compile_manifest(
        mode="pro-attachment",
        project_root=tmp_path,
        mission_path=prompt,
        output_path=pro_target,
        attachment_paths=[prompt, packet],
    )
    value = json.loads(pro_target.read_text(encoding="utf-8"))
    assert pro["contract"]["route"] == "oracle-pro-attachment-only"
    assert pro["contract"]["task_kind"] == "pro"
    assert pro["contract"]["thinking_time"] == "heavy"
    assert value["transport"] == "pro-attachment-only"
    assert value["task_kind"] == "pro"
    assert value["model"] == "gpt-5.6-sol"
    assert value["thinking_time"] == "heavy"
    assert value["attachments"] == [str(prompt.resolve()), str(packet.resolve())]
    assert "app_name" not in value

    manual_target = tmp_path / "manual.json"
    manual = module.compile_manifest(
        mode="manual", project_root=tmp_path, mission_path=None, output_path=manual_target
    )
    assert manual["oracle_manifest_path"] is None
    assert not manual_target.exists()


def test_pro_defaults_to_devspace_without_attachments(tmp_path: Path) -> None:
    module = load()
    mission = tmp_path / "mission.md"
    mission.write_text("read only", encoding="utf-8")
    target = tmp_path / "pro-devspace.json"

    result = module.compile_manifest(
        mode="pro", project_root=tmp_path, mission_path=mission, output_path=target
    )

    value = json.loads(target.read_text(encoding="utf-8"))
    assert result["contract"]["route"] == "oracle-pro-devspace"
    assert value["transport"] == "pro-devspace"
    assert value["app_name"] == "DevSpace"
    assert value["model"] == "gpt-5.6-sol"
    assert value["model_strategy"] == "select"
    assert value["thinking_time"] == "heavy"
    assert value["research"] == "off"
    assert value["task_outcome_contract"] == "v1"
    assert "attachments" not in value


def test_pro_cli_dry_run_validates_compiled_manifest_without_submission(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    module = load()
    prompt = tmp_path / "prompt.txt"
    packet = tmp_path / "packet.zip"
    target = tmp_path / "pro-dry-run.json"
    prompt.write_text("instructions", encoding="utf-8")
    packet.write_bytes(b"PK\x03\x04packet")
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str(tmp_path.parent / "host-state-pro-dry-run"))

    exit_code = module.main([
        "--mode", "pro-attachment",
        "--project-root", str(tmp_path),
        "--mission-path", str(prompt),
        "--attachment", str(packet),
        "--manifest-output", str(target),
        "--dry-run",
    ])

    assert exit_code == 0
    emitted = json.loads(capsys.readouterr().out)
    manifest = json.loads(target.read_text(encoding="utf-8"))
    assert emitted["ok"] is True
    assert emitted["run"]["status"] == "dry-run"
    assert emitted["run"]["transport"] == "pro-attachment-only"
    assert manifest["task_kind"] == "pro"
    assert manifest["model"] == "gpt-5.6-sol"
    assert manifest["thinking_time"] == "heavy"
