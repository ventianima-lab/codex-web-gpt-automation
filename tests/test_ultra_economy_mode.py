from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ultra-economy-mode" / "SKILL.md"
UI = ROOT / "skills" / "ultra-economy-mode" / "agents" / "openai.yaml"


def load_comprehensive():
    path = ROOT / "bin" / "chatgpt_oracle_comprehensive.py"
    spec = importlib.util.spec_from_file_location("ultra_economy_comprehensive_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def workflow_manifest(tmp_path: Path) -> Path:
    os.environ["CODEX_ORACLE_STATE_ROOT"] = str(
        (tmp_path.parent / f"{tmp_path.name}-host").resolve()
    )
    mission = tmp_path / "mission.md"
    mission.write_text("Design the implementation.", encoding="utf-8")
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-comprehensive/v1",
        "workflow_id": "a" * 32,
        "workflow_profile": "ultra-economy",
        "initial_stage": "pro",
        "allow_pro": True,
        "project_root": str(tmp_path.resolve()),
        "workflow_dir": str((tmp_path / "workflow").resolve()),
        "initial_mission_path": str(mission.resolve()),
        "app_name": "codex",
        "model": "gpt-5.6",
        "max_stages": 4,
        "local_gate_command": ["python", "-c", "raise SystemExit(0)"],
    }), encoding="utf-8")
    return path


def test_ultra_economy_skill_has_one_time_user_activation_handshake() -> None:
    text = SKILL.read_text(encoding="utf-8")
    compact = " ".join(text.split())
    assert "first" in text and "exactly one concise instruction" in compact
    assert "gpt-5.6-luna" in text and "`max`" in text
    assert "Do not inspect, infer, or verify" in text
    assert "including after compaction, recovery, stage transitions" in compact
    assert "asking again" in text
    assert "Never rewrite the user's global model defaults" in text


def test_ultra_economy_skill_forces_fresh_luna_max_workers_and_web_stages() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "fresh `default`" in text
    assert "Do not use the globally configured scout" in text
    assert "qualified Pro design" in text
    assert "regular web implementation" in text
    assert "separate regular web final verification" in text
    assert "zero-exit local" in text


def test_ultra_economy_skill_ui_metadata_is_discoverable() -> None:
    text = UI.read_text(encoding="utf-8")
    assert 'display_name: "Ultra Economy Mode"' in text
    assert "$ultra-economy-mode" in text


def test_ultra_economy_runtime_does_not_reinspect_model_or_reasoning(tmp_path: Path, monkeypatch) -> None:
    module = load_comprehensive()
    path = workflow_manifest(tmp_path)
    assert not hasattr(module, "RUNTIME_IDENTITY")

    seen: dict[str, object] = {}

    def preview(oracle_manifest: Path, *, dry_run: bool):
        seen.update(json.loads(oracle_manifest.read_text(encoding="utf-8")))
        return {"ok": True}

    result = module.run_workflow(path, dry_run=True, oracle_execute=preview)
    assert result["stage"] == "pro"
    assert seen["transport"] == "pro-devspace-readonly"


def test_ultra_economy_runtime_dry_run_is_pro_first_and_readonly(tmp_path: Path, monkeypatch) -> None:
    module = load_comprehensive()
    seen: dict[str, object] = {}

    def preview(path: Path, *, dry_run: bool):
        assert dry_run is True
        seen.update(json.loads(path.read_text(encoding="utf-8")))
        mission = Path(str(seen["mission_path"])).read_text(encoding="utf-8")
        assert "[ULTRA_ECONOMY_DESIGN_CONTRACT]" in mission
        return {"ok": True}

    result = module.run_workflow(workflow_manifest(tmp_path), dry_run=True, oracle_execute=preview)
    assert result["stage"] == "pro"
    assert seen["transport"] == "pro-devspace-readonly"
    assert seen["thinking_time"] == "pro"


def test_ultra_economy_requires_separate_explicit_pro_authority(tmp_path: Path) -> None:
    module = load_comprehensive()
    path = workflow_manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["allow_pro"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(module.WorkflowError, match="ULTRA_ECONOMY_PRO_AUTHORIZATION_REQUIRED"):
        module.load_manifest(path)
