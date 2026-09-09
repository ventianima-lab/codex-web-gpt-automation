from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_dispatch.py"


def load_module():
    name = "chatgpt_oracle_dispatch_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def project(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "project"
    root.mkdir()
    mission = root / "mission.md"
    mission.write_text("Implement the task.\n", encoding="utf-8")
    return root, mission, tmp_path / "host-state" / "runs"


def test_compile_manifest_has_only_lean_fields(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    dispatch = load_module()
    root, mission, run_root = project(tmp_path)

    compiled = dispatch.compile_manifest(
        project_root=root,
        mission_path=mission,
        run_root=run_root,
        run_id="ordinary-run-0001",
        model="latest",
        effort="extra-high",
        app_name="codex",
    )

    assert compiled["manifest"] == {
        "schema": "codex.chatgpt.oracle-execution/v1",
        "project_root": str(root.resolve()),
        "mission_path": str(mission.resolve()),
        "model": "latest",
        "effort": "extra-high",
        "app_name": "codex",
        "run_root": str(run_root.resolve()),
        "run_id": "ordinary-run-0001",
    }
    assert "mode" not in compiled["manifest"]
    assert "task_outcome_contract" not in compiled["manifest"]
    assert "browser_intent" not in compiled["manifest"]


def test_dispatch_dry_run_uses_ordinary_executor(tmp_path: Path, capsys):
    dispatch = load_module()
    root, mission, run_root = project(tmp_path)

    code = dispatch.main(
        [
            "--project-root", str(root),
            "--mission-path", str(mission),
            "--run-root", str(run_root),
            "--run-id", "ordinary-run-0002",
            "--dry-run",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["run"]["status"] == "dry-run"
    assert payload["run"]["writes_performed"] is False
    argv = payload["run"]["argv"]
    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
    assert argv[argv.index("--browser-model-strategy") + 1] == "current"
    assert argv[argv.index("--browser-archive") + 1] == "never"
    assert argv[argv.index("--chatgpt-url") + 1] == "https://chatgpt.com/?temporary-chat=true"
    assert "--mode" not in argv
