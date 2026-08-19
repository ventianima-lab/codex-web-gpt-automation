from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ultra-gpt-mode" / "SKILL.md"
UI = ROOT / "skills" / "ultra-gpt-mode" / "agents" / "openai.yaml"
DOC = ROOT / "docs" / "ULTRA_GPT_MODE.md"


def load_comprehensive():
    path = ROOT / "bin" / "chatgpt_oracle_comprehensive.py"
    spec = importlib.util.spec_from_file_location("ultra_gpt_comprehensive_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def workflow_manifest(tmp_path: Path, **overrides: object) -> Path:
    os.environ["CODEX_ORACLE_STATE_ROOT"] = str(
        (tmp_path.parent / f"{tmp_path.name}-host").resolve()
    )
    mission = tmp_path / "mission.md"
    mission.write_text("Plan the bounded implementation.", encoding="utf-8")
    value: dict[str, object] = {
        "schema": "codex.chatgpt.oracle-comprehensive/v1",
        "workflow_id": "a" * 32,
        "workflow_profile": "ultra-gpt",
        "initial_stage": "plan",
        "allow_pro": False,
        "project_root": str(tmp_path.resolve()),
        "workflow_dir": str((tmp_path / "workflow").resolve()),
        "initial_mission_path": str(mission.resolve()),
        "app_name": "codex",
        "model": "gpt-5.6",
        "max_stages": 5,
        "local_gate_command": ["python", "-c", "raise SystemExit(0)"],
    }
    value.update(overrides)
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_ultra_gpt_skill_replaces_native_subagents_with_web_sessions() -> None:
    text = " ".join(SKILL.read_text(encoding="utf-8").split())
    assert "Do not spawn native Codex subagents" in text
    assert "2..5 parallel worktree-write web implementers" in text
    assert "owned_paths" in text
    assert "pairwise disjoint" in text
    assert "deterministic controller" in text
    assert "final web PASS receipt" in text


def test_ultra_gpt_ui_and_documentation_are_discoverable() -> None:
    ui = UI.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")
    assert 'display_name: "Ultra GPT Mode"' in ui
    assert "$ultra-gpt-mode" in ui
    assert "울트라 GPT 모드" in doc
    assert '"workflow_profile": "ultra-gpt"' in doc


def test_ultra_gpt_dry_run_is_regular_plan_with_forced_review_contract(tmp_path: Path) -> None:
    module = load_comprehensive()
    seen: dict[str, object] = {}

    def preview(path: Path, *, dry_run: bool):
        assert dry_run is True
        seen.update(json.loads(path.read_text(encoding="utf-8")))
        mission = Path(str(seen["mission_path"])).read_text(encoding="utf-8")
        assert "[ULTRA_GPT_WEB_AGENT_CONTRACT]" in mission
        assert "next_stage=review" in mission
        return {"ok": True}

    result = module.run_workflow(
        workflow_manifest(tmp_path), dry_run=True, oracle_execute=preview
    )
    assert result["stage"] == "plan"
    assert result["workflow_profile"] == "ultra-gpt"
    assert seen["transport"] == "devspace"
    assert seen["thinking_time"] == "extra-high"


def test_ultra_gpt_review_authors_parallel_scoped_write_manifest(tmp_path: Path) -> None:
    module = load_comprehensive()
    config = module.load_manifest(workflow_manifest(tmp_path))
    review = tmp_path / "review-input.md"
    review.write_text("Review and partition the implementation.", encoding="utf-8")
    mission, _, _ = module._stage_mission(
        config, "a" * 32, 2, "review", review, "b" * 32
    )
    text = mission.read_text(encoding="utf-8")

    assert "[ULTRA_GPT_PARALLEL_IMPLEMENTATION_CONTRACT]" in text
    assert "next_stage=web-multi" in text
    assert "access=worktree-write" in text
    assert "nonempty project-relative owned_paths" in text
    assert "next_stage=implementation" not in text


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"initial_stage": "pro"}, "ULTRA_GPT_INITIAL_STAGE_REQUIRED"),
        ({"allow_pro": True}, "ULTRA_GPT_PRO_IS_SEPARATE"),
        ({"max_stages": 4}, "ULTRA_GPT_STAGE_BUDGET_TOO_SMALL"),
        ({"model": "gpt-5.6-sol"}, "ULTRA_GPT_REGULAR_MODEL_REQUIRED"),
    ],
)
def test_ultra_gpt_manifest_fails_closed(tmp_path: Path, overrides: dict[str, object], message: str) -> None:
    module = load_comprehensive()
    with pytest.raises(module.WorkflowError, match=message):
        module.load_manifest(workflow_manifest(tmp_path, **overrides))


def test_ultra_gpt_plan_cannot_skip_review(tmp_path: Path) -> None:
    module = load_comprehensive()
    config = module.load_manifest(workflow_manifest(tmp_path))
    output = tmp_path / "plan-output.md"
    output.write_text("plan", encoding="utf-8")
    next_mission = tmp_path / "review.md"
    next_mission.write_text("review", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({
        "schema": module.RECEIPT_SCHEMA,
        "workflow_id": "a" * 32,
        "stage": "plan",
        "attempt_id": "b" * 32,
        "input_mission_sha256": "c" * 64,
        "status": "PLAN_READY",
        "output_path": str(output),
        "output_sha256": module.sha(output),
        "next_stage": "web-multi",
        "next_mission_path": str(next_mission),
        "next_mission_sha256": module.sha(next_mission),
        "ready_for_next": True,
        "blocker": "",
    }), encoding="utf-8")
    with pytest.raises(module.WorkflowError, match="ULTRA_GPT_STAGE_ORDER_REQUIRED"):
        module._validate_receipt(
            config, receipt, "a" * 32, "plan", "b" * 32, "c" * 64
        )


def strict_multi_config(config: dict[str, object], *, solvers: list[dict[str, object]], max_concurrency: int = 3) -> dict[str, object]:
    return {
        "strict": True,
        "project_root": config["project_root"],
        "app_name": config["app_name"],
        "model": config["model"],
        "copy_profile": config["copy_profile"],
        "next_stage_result_path": Path(str(config["workflow_dir"])) / "stage-result.json",
        "solvers": solvers,
        "max_concurrency": max_concurrency,
    }


def test_ultra_gpt_multi_accepts_bounded_parallel_worktree_writers(tmp_path: Path) -> None:
    module = load_comprehensive()
    config = module.load_manifest(workflow_manifest(tmp_path))
    module._validate_ultra_gpt_multi(config, strict_multi_config(config, solvers=[
        {"id": "runtime", "access": "worktree-write", "owned_paths": ["bin/runtime.py"]},
        {"id": "tests", "access": "worktree-write", "owned_paths": ["tests/test_runtime.py"]},
        {"id": "docs", "access": "worktree-write", "owned_paths": ["docs/runtime.md"]},
    ]))


@pytest.mark.parametrize(
    ("multi", "message"),
    [
        ({"solvers": [{"access": "worktree-write", "owned_paths": ["a"]}] * 2, "max_concurrency": 4}, "ULTRA_GPT_CONCURRENCY_EXCEEDED"),
        ({"solvers": [{"access": "worktree-write", "owned_paths": ["a"]}] * 6, "max_concurrency": 3}, "ULTRA_GPT_SOLVER_COUNT_INVALID"),
        ({"solvers": [{"access": "read-only", "owned_paths": []}, {"access": "worktree-write", "owned_paths": ["b"]}], "max_concurrency": 2}, "ULTRA_GPT_PARALLEL_WRITERS_REQUIRED"),
    ],
)
def test_ultra_gpt_multi_fails_closed_for_unbounded_or_write_lanes(
    tmp_path: Path, multi: dict[str, object], message: str
) -> None:
    module = load_comprehensive()
    config = module.load_manifest(workflow_manifest(tmp_path))
    with pytest.raises(module.WorkflowError, match=message):
        module._validate_ultra_gpt_multi(
            config,
            strict_multi_config(
                config,
                solvers=list(multi["solvers"]),
                max_concurrency=int(multi["max_concurrency"]),
            ),
        )
