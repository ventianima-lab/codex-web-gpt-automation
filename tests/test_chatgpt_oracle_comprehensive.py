from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_comprehensive.py"


def load():
    spec = importlib.util.spec_from_file_location("oracle_comprehensive_test", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def manifest(tmp_path: Path) -> Path:
    os.environ["CODEX_ORACLE_STATE_ROOT"] = str((tmp_path.parent / f"{tmp_path.name}-host-state").resolve())
    mission = tmp_path / "initial.md"
    mission.write_text("Plan the work broadly.", encoding="utf-8")
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-comprehensive/v1",
        "workflow_id": "a" * 32,
        "project_root": str(tmp_path.resolve()),
        "workflow_dir": str((tmp_path / "workflow").resolve()),
        "initial_mission_path": str(mission.resolve()),
        "app_name": "DevSpace",
        "model": "gpt-5.6",
        "local_gate_command": ["python", "-c", "raise SystemExit(0)"],
    }), encoding="utf-8")
    return path


def test_manifest_rejects_non_devspace_app_before_workflow_creation(tmp_path: Path) -> None:
    module = load()
    path = manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["app_name"] = "OtherWorkspace"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(module.WorkflowError, match="exactly DevSpace"):
        module.load_manifest(path)


def test_web_authored_relay_reaches_complete_without_host_semantic_rewrite(tmp_path: Path) -> None:
    module = load()
    order = ["plan", "review", "implementation", "final-web-gate"]
    seen = []

    def fake_execute(path: Path, *, dry_run: bool):
        config = json.loads(path.read_text(encoding="utf-8"))
        assert config["model_strategy"] == "select"
        assert config["thinking_time"] == "heavy"
        mission = Path(config["mission_path"])
        text = mission.read_text(encoding="utf-8")
        stage = next(item for item in order if f"stage={item}\n" in text)
        attempt_id = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("attempt_id="))
        input_sha = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("input_mission_sha256="))
        seen.append(stage)
        stage_dir = mission.parent
        output = stage_dir / "web-output.md"
        output.write_text(f"{stage} output", encoding="utf-8")
        next_stage = order[order.index(stage) + 1] if stage != order[-1] else "complete"
        next_mission = tmp_path / f"next-{stage}.md"
        next_mission.write_text(f"web-authored mission after {stage}", encoding="utf-8")
        receipt = {
            "schema": "codex.chatgpt.oracle-stage-result/v1",
            "workflow_id": "a" * 32,
            "stage": stage,
            "attempt_id": attempt_id,
            "input_mission_sha256": input_sha,
            "status": "PASS",
            "output_path": str(output),
            "output_sha256": module.sha(output),
            "next_stage": next_stage,
            "next_mission_path": str(next_mission),
            "next_mission_sha256": module.sha(next_mission),
            "ready_for_next": True,
            "blocker": "",
        }
        (stage_dir / "stage-result.json").write_text(json.dumps(receipt), encoding="utf-8")
        run_dir = stage_dir / "run"
        run_dir.mkdir()
        return {"ok": True, "run_dir": str(run_dir)}

    result = module.run_workflow(
        manifest(tmp_path),
        oracle_execute=fake_execute,
        local_gate_runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "gate ok", ""),
    )
    assert result["ok"] is True
    assert seen == order
    assert result["status"] == "complete"


def test_pro_stage_runs_oracle_attachment_only_and_materializes_bound_receipt(tmp_path: Path) -> None:
    module = load()
    stages = []

    def regular_receipt(mission: Path, stage: str, next_stage: str, next_mission: Path) -> None:
        text = mission.read_text(encoding="utf-8")
        attempt = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("attempt_id="))
        input_sha = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("input_mission_sha256="))
        output = mission.parent / "regular-output.md"
        output.write_text(stage, encoding="utf-8")
        (mission.parent / "stage-result.json").write_text(json.dumps({
            "schema": module.RECEIPT_SCHEMA, "workflow_id": "a" * 32, "stage": stage,
            "attempt_id": attempt, "input_mission_sha256": input_sha, "status": "PASS",
            "output_path": str(output), "output_sha256": module.sha(output),
            "next_stage": next_stage, "next_mission_path": str(next_mission),
            "next_mission_sha256": module.sha(next_mission), "ready_for_next": True, "blocker": "",
        }), encoding="utf-8")

    def fake_execute(path: Path, *, dry_run: bool):
        payload = json.loads(path.read_text(encoding="utf-8"))
        mission = Path(payload["mission_path"])
        text = mission.read_text(encoding="utf-8")
        stage = next(item for item in ("plan", "pro", "review", "implementation", "final-web-gate") if f"stage={item}\n" in text)
        stages.append(stage)
        if stage == "pro":
            assert payload["transport"] == "pro-attachment-only"
            assert payload["model"] == "pro"
            assert payload["attachments"] == [str(mission)]
            assert "app_name" not in payload
            attempt = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("attempt_id="))
            input_sha = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("input_mission_sha256="))
            oracle_output = mission.parent / "oracle-output.json"
            oracle_output.write_text(json.dumps({
                "schema": module.PRO_OUTPUT_SCHEMA, "workflow_id": "a" * 32, "stage": "pro",
                "attempt_id": attempt, "input_mission_sha256": input_sha, "status": "PASS",
                "output_text": "Pro decision\nsecond line\n", "next_stage": "review",
                "next_mission_text": "Review the Pro decision independently.\nPreserve LF.\n",
                "ready_for_next": True, "blocker": "",
            }), encoding="utf-8")
            return {"ok": True, "run_dir": str(mission.parent / "run"), "output_path": str(oracle_output)}
        next_stage = {
            "plan": "pro", "review": "implementation",
            "implementation": "final-web-gate", "final-web-gate": "complete",
        }[stage]
        next_mission = tmp_path / f"next-{stage}.md"
        next_mission.write_text(f"mission after {stage}", encoding="utf-8")
        regular_receipt(mission, stage, next_stage, next_mission)
        return {"ok": True, "run_dir": str(mission.parent / "run")}

    result = module.run_workflow(
        manifest(tmp_path),
        oracle_execute=fake_execute,
        local_gate_runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )
    assert result["ok"] is True, result
    assert stages == ["plan", "pro", "review", "implementation", "final-web-gate"]
    pro_stage = next((tmp_path / "workflow" / "stages").glob("01-pro-*"))
    assert (pro_stage / "output.md").read_bytes() == b"Pro decision\nsecond line\n"
    assert (pro_stage / "next-mission.md").read_bytes() == b"Review the Pro decision independently.\nPreserve LF.\n"
    receipt = json.loads((pro_stage / "stage-result.json").read_text(encoding="utf-8"))
    assert receipt["stage"] == "pro"
    assert receipt["next_stage"] == "review"


def test_pro_exact_recovery_materializes_output_without_resubmission(tmp_path: Path) -> None:
    module = load()
    workflow_path = manifest(tmp_path)
    config = module.load_manifest(workflow_path)
    config["_parallel_parent_id"] = "b" * 64
    attempt = "c" * 32
    source = tmp_path / "pro-source.md"
    source.write_text("Pro review request", encoding="utf-8")
    mission, receipt, input_sha = module._pro_stage_mission(config, "a" * 32, 1, source, attempt)
    oracle_manifest = module._oracle_manifest(config, mission, mission.parent, attempt, stage="pro")
    run_dir = _oracle_running_state(module, oracle_manifest)
    state_path = module._state_path(config, "a" * 32)
    module._write(state_path, {
        "schema": module.STATE_SCHEMA, "status": "attention_required", "workflow_id": "a" * 32,
        "manifest_sha256": config["manifest_sha256"], "current_stage": "pro",
        "current_attempt_id": attempt, "current_input_sha256": input_sha,
        "current_mission_path": str(source), "receipt_path": str(receipt),
        "oracle_run_id": attempt, "oracle_run_dir": str(run_dir),
        "oracle_manifest_path": str(oracle_manifest), "next_index": 1, "records": [],
    })
    oracle_output = run_dir / "recovered-output.json"
    oracle_output.write_text(json.dumps({
        "schema": module.PRO_OUTPUT_SCHEMA, "workflow_id": "a" * 32, "stage": "pro",
        "attempt_id": attempt, "input_mission_sha256": input_sha, "status": "PASS",
        "output_text": "Recovered Pro result", "next_stage": "review",
        "next_mission_text": "Review recovered Pro result.",
        "ready_for_next": True, "blocker": "",
    }), encoding="utf-8")
    submissions = 0

    def no_pro_resubmit(path: Path, *, dry_run: bool):
        nonlocal submissions
        submissions += 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["transport"] == "devspace"
        return {"ok": False, "run_dir": str(_oracle_running_state(module, path))}

    def fake_recover(exact_run_dir: Path, *, action: str, dry_run: bool):
        assert exact_run_dir == run_dir
        assert action == "harvest"
        return {"ok": True, "status": "complete", "run_dir": str(run_dir), "output_path": str(oracle_output)}

    result = module.run_workflow(
        workflow_path, oracle_execute=no_pro_resubmit, oracle_recover=fake_recover
    )
    assert result["status"] == "attention_required"
    assert result["current_stage"] == "review"
    assert submissions == 1
    assert receipt.is_file()
    assert (receipt.parent / "output.md").read_text(encoding="utf-8") == "Recovered Pro result"


@pytest.mark.parametrize("mutation", ["duplicate", "additional"])
def test_pro_output_rejects_duplicate_or_additional_keys(tmp_path: Path, mutation: str) -> None:
    module = load()
    workflow_path = manifest(tmp_path)
    config = module.load_manifest(workflow_path)
    config["_parallel_parent_id"] = "b" * 64
    attempt = "d" * 32
    source = tmp_path / "pro-source.md"
    source.write_text("Pro request", encoding="utf-8")
    mission, receipt, input_sha = module._pro_stage_mission(config, "a" * 32, 1, source, attempt)
    output = tmp_path / "pro-output.json"
    base = {
        "schema": module.PRO_OUTPUT_SCHEMA, "workflow_id": "a" * 32, "stage": "pro",
        "attempt_id": attempt, "input_mission_sha256": input_sha, "status": "PASS",
        "output_text": "result", "next_stage": "review", "next_mission_text": "review",
        "ready_for_next": True, "blocker": "",
    }
    if mutation == "additional":
        base["unexpected"] = "forbidden"
        output.write_text(json.dumps(base), encoding="utf-8")
    else:
        valid = json.dumps(base)
        output.write_text(valid[:-1] + ',"status":"PASS"}', encoding="utf-8")
    with pytest.raises(module.WorkflowError, match="duplicate key|closed key set"):
        module._materialize_pro_receipt(
            config, receipt, "a" * 32, attempt, input_sha,
            {"output_path": str(output)},
        )
    assert not receipt.exists()


def test_missing_receipt_fails_closed_without_duplicate_stage(tmp_path: Path) -> None:
    module = load()
    calls = 0

    def fake_execute(path: Path, *, dry_run: bool):
        nonlocal calls
        calls += 1
        return {"ok": True, "run_dir": str(tmp_path / "run")}

    result = module.run_workflow(manifest(tmp_path), oracle_execute=fake_execute)
    assert result["ok"] is False
    assert result["status"] == "awaiting_receipt"
    assert calls == 1


def test_failing_receipt_cannot_complete(tmp_path: Path) -> None:
    module = load()

    def fake_execute(path: Path, *, dry_run: bool):
        config = json.loads(path.read_text(encoding="utf-8"))
        mission = Path(config["mission_path"])
        text = mission.read_text(encoding="utf-8")
        attempt_id = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("attempt_id="))
        input_sha = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("input_mission_sha256="))
        output = mission.parent / "output.md"
        output.write_text("bad", encoding="utf-8")
        (mission.parent / "stage-result.json").write_text(json.dumps({
            "schema": "codex.chatgpt.oracle-stage-result/v1",
            "workflow_id": "a" * 32,
            "stage": "plan",
            "attempt_id": attempt_id,
            "input_mission_sha256": input_sha,
            "status": "FAIL",
            "output_path": str(output),
            "output_sha256": module.sha(output),
            "next_stage": "review",
            "next_mission_path": str(tmp_path / "none.md"),
            "next_mission_sha256": "0" * 64,
            "ready_for_next": False,
            "blocker": "not ready",
        }), encoding="utf-8")
        return {"ok": True, "run_dir": str(mission.parent / "run")}

    try:
        module.run_workflow(manifest(tmp_path), oracle_execute=fake_execute)
    except module.WorkflowError as exc:
        assert "did not pass" in str(exc)
    else:
        raise AssertionError("FAIL receipt must not advance")


def test_web_multi_branch_is_bound_and_resumes_at_review(tmp_path: Path) -> None:
    module = load()
    workflow_path = manifest(tmp_path)
    lane_one = tmp_path / "lane-one.md"
    lane_two = tmp_path / "lane-two.md"
    merger = tmp_path / "merger.md"
    for path, body in ((lane_one, "one"), (lane_two, "two"), (merger, "merge")):
        path.write_text(body, encoding="utf-8")
    multi_manifest = tmp_path / "multi.json"
    multi_manifest.write_text(json.dumps({
        "schema": module.MULTI.SCHEMA,
        "project_root": str(tmp_path),
        "output_dir": str(tmp_path / "multi-output"),
        "solvers": [
            {"id": "one", "mission_path": str(lane_one)},
            {"id": "two", "mission_path": str(lane_two)},
        ],
        "merger_mission_path": str(merger),
        "next_stage_result_path": str(tmp_path / "multi-next-receipt.json"),
        "next_stage_binding": {"workflow_id": "a" * 32, "stage": "web-multi"},
    }), encoding="utf-8")
    review_mission = tmp_path / "review-after-multi.md"
    review_mission.write_text("review merged advice", encoding="utf-8")
    stages_seen = []

    def write_receipt(mission: Path, stage: str, next_stage: str, next_path: Path) -> None:
        text = mission.read_text(encoding="utf-8")
        attempt = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("attempt_id="))
        input_sha = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("input_mission_sha256="))
        output = mission.parent / "output.md"
        output.write_text(stage, encoding="utf-8")
        (mission.parent / "stage-result.json").write_text(json.dumps({
            "schema": "codex.chatgpt.oracle-stage-result/v1",
            "workflow_id": "a" * 32,
            "stage": stage,
            "attempt_id": attempt,
            "input_mission_sha256": input_sha,
            "status": "PASS",
            "output_path": str(output),
            "output_sha256": module.sha(output),
            "next_stage": next_stage,
            "next_mission_path": str(next_path),
            "next_mission_sha256": module.sha(next_path),
            "ready_for_next": True,
            "blocker": "",
        }), encoding="utf-8")

    def fake_oracle(path: Path, *, dry_run: bool):
        value = json.loads(path.read_text(encoding="utf-8"))
        mission = Path(value["mission_path"])
        stage = next(item for item in ("plan", "review", "implementation", "final-web-gate") if f"stage={item}\n" in mission.read_text(encoding="utf-8"))
        stages_seen.append(stage)
        if stage == "plan":
            write_receipt(mission, stage, "web-multi", multi_manifest)
        else:
            next_stage = {"review": "implementation", "implementation": "final-web-gate", "final-web-gate": "complete"}[stage]
            next_path = tmp_path / f"next-{stage}.md"
            next_path.write_text(f"after {stage}", encoding="utf-8")
            write_receipt(mission, stage, next_stage, next_path)
        return {"ok": True, "run_dir": str(mission.parent / "run")}

    def fake_multi(path: Path, *, dry_run: bool, parent_lock_held: bool):
        assert parent_lock_held is True
        workflow_config = module.load_manifest(workflow_path)
        stored = module._json(module._state_path(workflow_config, "a" * 32))
        assert stored["multi_execution_id"]
        assert stored["multi_manifest_sha256"] == module.sha(multi_manifest)
        assert Path(stored["multi_result_path"]).name == "result.json"
        receipt = tmp_path / "multi-result.json"
        output = tmp_path / "multi-output.md"
        output.write_text("merged", encoding="utf-8")
        receipt.write_text(json.dumps({
            "schema": "codex.chatgpt.oracle-stage-result/v1",
            "workflow_id": "a" * 32,
            "stage": "web-multi",
            "attempt_id": "b" * 64,
            "input_mission_sha256": module.sha(multi_manifest),
            "status": "PASS",
            "output_path": str(output),
            "output_sha256": module.sha(output),
            "next_stage": "review",
            "next_mission_path": str(review_mission),
            "next_mission_sha256": module.sha(review_mission),
            "ready_for_next": True,
            "blocker": "",
        }), encoding="utf-8")
        return {"ok": True, "parent_id": "b" * 64, "next_stage_result_path": str(receipt)}

    result = module.run_workflow(
        workflow_path,
        oracle_execute=fake_oracle,
        multi_execute=fake_multi,
        local_gate_runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )
    assert result["ok"] is True
    assert stages_seen == ["plan", "review", "implementation", "final-web-gate"]


def test_dry_run_leaves_no_host_workflow_state_and_real_run_can_follow(tmp_path: Path) -> None:
    module = load()
    path = manifest(tmp_path)
    previews = []

    def fake_preview(oracle_manifest: Path, *, dry_run: bool):
        previews.append(dry_run)
        return {"ok": True, "status": "dry-run"}

    preview = module.run_workflow(path, dry_run=True, oracle_execute=fake_preview)
    assert preview["ok"] is True
    assert previews == [True]
    config = module.load_manifest(path)
    assert not module._state_path(config, "a" * 32).exists()

    calls = 0

    def fake_real(oracle_manifest: Path, *, dry_run: bool):
        nonlocal calls
        calls += 1
        return {"ok": True, "run_dir": str(tmp_path / "fake-run")}

    real = module.run_workflow(path, oracle_execute=fake_real)
    assert real["status"] == "awaiting_receipt"
    assert calls == 1


def _oracle_running_state(module, oracle_manifest: Path) -> Path:
    config = module.RUNNER.STATE.load_manifest(oracle_manifest)
    layout = module.RUNNER.STATE.create_layout(config, run_id=config.requested_run_id)
    layout.run_dir.mkdir(parents=True)
    module.RUNNER.STATE.write_json_atomic(
        layout.state_path,
        module.RUNNER.STATE.state_payload(config, layout, status="running", resolved_version="test"),
    )
    return layout.run_dir


def test_running_oracle_stage_recovers_exact_run_without_resubmission(tmp_path: Path) -> None:
    module = load()
    submitted = 0
    recovered = []

    def fake_execute(oracle_manifest: Path, *, dry_run: bool):
        nonlocal submitted
        submitted += 1
        return {"ok": False, "run_dir": str(_oracle_running_state(module, oracle_manifest))}

    def fake_recover(run_dir: Path, *, action: str, dry_run: bool):
        recovered.append((run_dir, action, dry_run))
        return {"ok": True, "status": "complete", "run_dir": str(run_dir)}

    path = manifest(tmp_path)
    first = module.run_workflow(path, oracle_execute=fake_execute, oracle_recover=fake_recover)
    assert first["status"] == "attention_required"
    assert first["oracle_run_id"] == first["current_attempt_id"]
    second = module.run_workflow(path, oracle_execute=fake_execute, oracle_recover=fake_recover)
    assert second["status"] == "awaiting_receipt"
    assert submitted == 1
    assert [item[1:] for item in recovered] == [("harvest", False)]
    assert second["recovery"]["status"] == "recovered"


def test_unambiguous_app_mention_pre_submit_failure_retries_once(tmp_path: Path) -> None:
    module = load()
    submitted = 0

    def fake_execute(oracle_manifest: Path, *, dry_run: bool):
        nonlocal submitted
        submitted += 1
        run_dir = _oracle_running_state(module, oracle_manifest)
        stdout = run_dir / "stdout.log"
        if submitted == 1:
            stdout.write_text(
                "ERROR: ChatGPT app mention suggestion did not appear.\n",
                encoding="utf-8",
            )
        else:
            stdout.write_text("ERROR: unrelated terminal failure\n", encoding="utf-8")
        return {"ok": False, "run_dir": str(run_dir)}

    result = module.run_workflow(manifest(tmp_path), oracle_execute=fake_execute)

    assert result["status"] == "attention_required"
    assert submitted == 2
    assert result["next_index"] == 0


@pytest.mark.parametrize(
    "marker",
    [
        'Unable to find model option matching "GPT-5.6 Sol" in the model switcher.',
        "--copy-profile requires rsync on PATH (spawn failed): spawn rsync ENOENT",
        "--copy-profile cannot be combined with --browser-manual-login",
    ],
)
def test_launch_time_pre_submit_failures_also_retry_once(tmp_path: Path, marker: str) -> None:
    module = load()
    submitted = 0

    def fake_execute(oracle_manifest: Path, *, dry_run: bool):
        nonlocal submitted
        submitted += 1
        run_dir = _oracle_running_state(module, oracle_manifest)
        stdout = run_dir / "stdout.log"
        if submitted == 1:
            stdout.write_text(f"ERROR: {marker}\n", encoding="utf-8")
        else:
            stdout.write_text("ERROR: unrelated terminal failure\n", encoding="utf-8")
        return {"ok": False, "run_dir": str(run_dir)}

    result = module.run_workflow(manifest(tmp_path), oracle_execute=fake_execute)

    assert submitted == 2
    assert result["status"] == "attention_required"
    assert result["next_index"] == 0


def test_durable_output_prevents_pre_submit_retry_even_with_a_launch_marker(tmp_path: Path) -> None:
    module = load()
    submitted = 0

    def fake_execute(oracle_manifest: Path, *, dry_run: bool):
        nonlocal submitted
        submitted += 1
        run_dir = _oracle_running_state(module, oracle_manifest)
        (run_dir / "stdout.log").write_text(
            'ERROR: Unable to find model option matching "GPT-5.6 Sol" in the model switcher.\n',
            encoding="utf-8",
        )
        (run_dir / "output.md").write_text("partial provider answer", encoding="utf-8")
        return {"ok": False, "run_dir": str(run_dir)}

    result = module.run_workflow(manifest(tmp_path), oracle_execute=fake_execute)

    assert submitted == 1
    assert result["status"] == "attention_required"


def test_running_stage_does_not_trust_existing_receipt_before_terminal_authority(tmp_path: Path) -> None:
    module = load()
    submitted = []
    recovered = []
    next_mission = tmp_path / "review.md"
    next_mission.write_text("review", encoding="utf-8")

    def fake_execute(oracle_manifest: Path, *, dry_run: bool):
        config = json.loads(oracle_manifest.read_text(encoding="utf-8"))
        mission = Path(config["mission_path"])
        submitted.append(mission)
        return {"ok": False, "run_dir": str(_oracle_running_state(module, oracle_manifest))}

    def fake_recover(*args, **kwargs):
        recovered.append((args, kwargs))
        return {"ok": False, "status": "session_live", "run_dir": str(args[0])}

    path = manifest(tmp_path)
    first = module.run_workflow(path, oracle_execute=fake_execute, oracle_recover=fake_recover)
    receipt_path = Path(first["receipt_path"])
    output = receipt_path.parent / "output.md"
    output.write_text("plan", encoding="utf-8")
    receipt_path.write_text(json.dumps({
        "schema": module.RECEIPT_SCHEMA,
        "workflow_id": "a" * 32,
        "stage": "plan",
        "attempt_id": first["current_attempt_id"],
        "input_mission_sha256": first["current_input_sha256"],
        "status": "PASS",
        "output_path": str(output),
        "output_sha256": module.sha(output),
        "next_stage": "review",
        "next_mission_path": str(next_mission),
        "next_mission_sha256": module.sha(next_mission),
        "ready_for_next": True,
        "blocker": "",
    }), encoding="utf-8")

    second = module.run_workflow(path, oracle_execute=fake_execute, oracle_recover=fake_recover)

    assert second["status"] == "running"
    assert second["current_stage"] == "plan"
    assert len(submitted) == 1
    assert len(recovered) == 1
    assert recovered[0][1]["action"] == "harvest"


def test_review_revise_receipt_is_terminal_legacy_compatibility(tmp_path: Path) -> None:
    module = load()
    output = tmp_path / "review-output.md"
    output.write_text("revise", encoding="utf-8")
    next_mission = tmp_path / "next-plan.md"
    next_mission.write_text("fix the plan", encoding="utf-8")
    receipt = tmp_path / "stage-result.json"
    receipt.write_text(json.dumps({
        "schema": module.RECEIPT_SCHEMA,
        "workflow_id": "a" * 32,
        "stage": "review",
        "attempt_id": "b" * 32,
        "input_mission_sha256": "c" * 64,
        "status": "REVISE",
        "output_path": str(output),
        "output_sha256": module.sha(output),
        "next_stage": "plan",
        "next_mission_path": str(next_mission),
        "next_mission_sha256": module.sha(next_mission),
        "ready_for_next": True,
        "blocker": "",
    }), encoding="utf-8")

    value = module._validate_receipt(
        {"project_root": tmp_path},
        receipt,
        "a" * 32,
        "review",
        "b" * 32,
        "c" * 64,
    )

    assert value["status"] == "REVISE"
    assert value["next_stage"] == "plan"
    assert value["_next_mission"] is None
    assert "cannot create a new plan" in value["_terminal_attention"]


def _review_receipt(
    module,
    tmp_path: Path,
    *,
    status: str,
    attempt: str,
    ids: list[str],
    next_stage: str,
    blocker: str = "",
) -> Path:
    output = tmp_path / f"{attempt}-output.md"
    output.write_text(status, encoding="utf-8")
    next_mission = tmp_path / f"{attempt}-next.md"
    next_mission.write_text(next_stage, encoding="utf-8")
    receipt = tmp_path / f"{attempt}-receipt.json"
    value = {
        "schema": module.RECEIPT_SCHEMA,
        "workflow_id": "a" * 32,
        "stage": "review",
        "attempt_id": attempt,
        "input_mission_sha256": "c" * 64,
        "status": status,
        "output_path": str(output),
        "output_sha256": module.sha(output),
        "next_stage": next_stage,
        "next_mission_path": str(next_mission),
        "next_mission_sha256": module.sha(next_mission),
        "ready_for_next": status != "FAIL",
        "blocker": blocker,
        "critical_finding_ids": ids,
        "critical_findings_sha256": module._finding_hash(ids),
    }
    receipt.write_text(json.dumps(value), encoding="utf-8")
    return receipt


def test_legacy_revise_never_creates_another_plan(tmp_path: Path) -> None:
    module = load()
    config = {
        "project_root": tmp_path,
        "_review_policy": module._default_review_policy(),
    }
    first = _review_receipt(
        module, tmp_path, status="REVISE", attempt="1" * 32,
        ids=["critical-input"], next_stage="plan",
    )
    second = _review_receipt(
        module, tmp_path, status="REVISE", attempt="2" * 32,
        ids=["critical-input"], next_stage="plan",
    )
    third = _review_receipt(
        module, tmp_path, status="REVISE", attempt="3" * 32,
        ids=["critical-input"], next_stage="plan",
    )

    values = [
        module._validate_receipt(config, first, "a" * 32, "review", "1" * 32, "c" * 64),
        module._validate_receipt(config, second, "a" * 32, "review", "2" * 32, "c" * 64),
        module._validate_receipt(config, third, "a" * 32, "review", "3" * 32, "c" * 64),
    ]

    assert all(value["_next_mission"] is None for value in values)
    assert all("cannot create a new plan" in value["_terminal_attention"] for value in values)
    assert config["_review_policy"]["plan_revisions_used"] == 0
    assert config["_review_policy"]["plan_revisions_remaining"] == 2


def test_review_mission_assigns_inline_plan_repair_and_exact_workspace_entry(tmp_path: Path) -> None:
    module = load()
    source = tmp_path / "검토-입력.md"
    source.write_text("계획을 검토하세요.", encoding="utf-8")
    config = {
        "project_root": tmp_path,
        "workflow_dir": tmp_path / "workflow",
        "_review_policy": {
            **module._default_review_policy(),
            "plan_revisions_used": 2,
            "plan_revisions_remaining": 0,
        },
    }

    mission, _, _ = module._stage_mission(
        config, "a" * 32, 2, "review", source, "b" * 32
    )
    text = mission.read_text(encoding="utf-8")

    assert f"exact_project_root={tmp_path}" in text
    assert f"exact_input_mission_path={source}" in text
    assert "retry the same exact root at most once" in text
    assert "Never substitute a parent root, child directory" in text
    assert "plan repair and finalization owner" in text
    assert "write the corrected final plan as your output" in text
    assert "next_stage=implementation" in text
    assert "REVISE is legacy compatibility only" in text
    assert "review_repair_owner=review" in text
    assert "new_plan_transition_allowed=false" in text
    assert "plan_revisions_remaining=0" in text


def test_pass_with_notes_proceeds_to_implementation(tmp_path: Path) -> None:
    module = load()
    receipt = _review_receipt(
        module, tmp_path, status="PASS_WITH_NOTES", attempt="4" * 32,
        ids=[], next_stage="implementation",
    )
    value = module._validate_receipt(
        {"project_root": tmp_path},
        receipt,
        "a" * 32,
        "review",
        "4" * 32,
        "c" * 64,
    )
    assert value["status"] == "PASS_WITH_NOTES"
    assert value["next_stage"] == "implementation"


def test_legacy_revise_is_terminal_and_duplicate_finding_ids_are_rejected(tmp_path: Path) -> None:
    module = load()
    config = {
        "project_root": tmp_path,
        "_review_policy": {
            **module._default_review_policy(),
            "plan_revisions_used": 1,
            "plan_revisions_remaining": 1,
            "baseline_critical_finding_ids": ["fixed-a"],
            "baseline_critical_findings_sha256": module._finding_hash(["fixed-a"]),
        },
    }
    added = _review_receipt(
        module, tmp_path, status="REVISE", attempt="5" * 32,
        ids=["new-b"], next_stage="plan",
    )
    added_value = module._validate_receipt(
        config, added, "a" * 32, "review", "5" * 32, "c" * 64
    )
    assert added_value["_next_mission"] is None
    assert "cannot create a new plan" in added_value["_terminal_attention"]

    duplicate = json.loads(added.read_text(encoding="utf-8"))
    duplicate["attempt_id"] = "6" * 32
    duplicate["critical_finding_ids"] = ["fixed-a", "fixed-a"]
    duplicate["critical_findings_sha256"] = module._finding_hash(["fixed-a", "fixed-a"])
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(module.WorkflowError, match="unique and sorted"):
        module._validate_receipt(
            config, duplicate_path, "a" * 32, "review", "6" * 32, "c" * 64
        )


def test_active_scope_blocks_retry_workflow_and_exposes_revision_budget(tmp_path: Path) -> None:
    module = load()
    first_path = manifest(tmp_path)
    first = module.load_manifest(first_path)
    first["_review_policy"] = {
        **module._default_review_policy(),
        "plan_revisions_used": 2,
        "plan_revisions_remaining": 0,
    }
    module._claim_scope(first, first["workflow_id"])
    scope = module._json(module._scope_path(first))
    assert scope["review_policy"]["plan_revisions_remaining"] == 0

    second = dict(first)
    second["workflow_id"] = "b" * 32
    with pytest.raises(module.WorkflowError, match="recover that exact workflow"):
        module._claim_scope(second, second["workflow_id"])


def test_review_history_budget_spans_retry_workflow_directories(tmp_path: Path) -> None:
    module = load()
    root = tmp_path / "project"
    root.mkdir()
    for index, attempt in enumerate(("1" * 32, "2" * 32, "3" * 32), start=1):
        stage_dir = tmp_path / f"workflow-retry{index}" / "stages" / f"001-review-{attempt}"
        stage_dir.mkdir(parents=True)
        output = stage_dir / "review.md"
        output.write_text("critical", encoding="utf-8")
        receipt = {
            "schema": module.RECEIPT_SCHEMA,
            "workflow_id": str(index) * 32,
            "stage": "review",
            "attempt_id": attempt,
            "input_mission_sha256": "c" * 64,
            "status": "REVISE",
            "output_path": str(output),
            "output_sha256": module.sha(output),
            "next_stage": "plan",
            "next_mission_path": str(output),
            "next_mission_sha256": module.sha(output),
            "ready_for_next": True,
            "blocker": "",
        }
        (stage_dir / "stage-result.json").write_text(json.dumps(receipt), encoding="utf-8")

    config = {
        "project_root": root,
        "workflow_dir": tmp_path / "workflow-retry11",
    }
    policy = module._review_policy_from_history(config)

    assert policy["plan_revisions_used"] == 3
    assert policy["plan_revisions_remaining"] == 0
    assert policy["baseline_critical_finding_ids"] == [
        f"legacy-{module.sha(tmp_path / 'workflow-retry1' / 'stages' / ('001-review-' + '1' * 32) / 'review.md')[:24]}"
    ]


def test_blocked_plan_receipt_can_continue_to_bound_source_repair_plan(tmp_path: Path) -> None:
    module = load()
    output = tmp_path / "blocked-plan.md"
    output.write_text("source evidence is incomplete", encoding="utf-8")
    next_mission = tmp_path / "source-repair.md"
    next_mission.write_text("repair the source evidence", encoding="utf-8")
    receipt = tmp_path / "stage-result.json"
    receipt.write_text(json.dumps({
        "schema": module.RECEIPT_SCHEMA,
        "workflow_id": "a" * 32,
        "stage": "plan",
        "attempt_id": "b" * 32,
        "input_mission_sha256": "c" * 64,
        "status": "BLOCKED_PLAN",
        "output_path": str(output),
        "output_sha256": module.sha(output),
        "next_stage": "plan",
        "next_mission_path": str(next_mission),
        "next_mission_sha256": module.sha(next_mission),
        "ready_for_next": True,
        "blocker": "first-party historical rule evidence is incomplete",
    }), encoding="utf-8")

    value = module._validate_receipt(
        {"project_root": tmp_path},
        receipt,
        "a" * 32,
        "plan",
        "b" * 32,
        "c" * 64,
    )

    assert value["status"] == "BLOCKED_PLAN"
    assert value["next_stage"] == "plan"


def test_source_repair_plan_ready_receipt_can_continue_to_review(tmp_path: Path) -> None:
    module = load()
    output = tmp_path / "source-repair-plan.md"
    output.write_text("ready", encoding="utf-8")
    next_mission = tmp_path / "next-review.md"
    next_mission.write_text("review the source repair", encoding="utf-8")
    receipt = tmp_path / "stage-result.json"
    receipt.write_text(json.dumps({
        "schema": module.RECEIPT_SCHEMA,
        "workflow_id": "a" * 32,
        "stage": "plan",
        "attempt_id": "b" * 32,
        "input_mission_sha256": "c" * 64,
        "status": "SOURCE_REPAIR_PLAN_READY",
        "output_path": str(output),
        "output_sha256": module.sha(output),
        "next_stage": "review",
        "next_mission_path": str(next_mission),
        "next_mission_sha256": module.sha(next_mission),
        "ready_for_next": True,
        "blocker": "",
    }), encoding="utf-8")

    value = module._validate_receipt(
        {"project_root": tmp_path},
        receipt,
        "a" * 32,
        "plan",
        "b" * 32,
        "c" * 64,
    )

    assert value["status"] == "SOURCE_REPAIR_PLAN_READY"
    assert value["next_stage"] == "review"


def test_awaiting_receipt_rebind_advances_to_next_stage_without_replaying_plan(tmp_path: Path) -> None:
    module = load()
    calls = []
    review = tmp_path / "review.md"
    review.write_text("review", encoding="utf-8")

    def fake_execute(oracle_manifest: Path, *, dry_run: bool):
        config = json.loads(oracle_manifest.read_text(encoding="utf-8"))
        mission = Path(config["mission_path"])
        text = mission.read_text(encoding="utf-8")
        stage = "plan" if "stage=plan\n" in text else "review"
        calls.append(stage)
        run_dir = _oracle_running_state(module, oracle_manifest)
        if stage == "plan":
            attempt = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("attempt_id="))
            input_sha = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("input_mission_sha256="))
            output = mission.parent / "out.md"
            output.write_text("plan", encoding="utf-8")
            (mission.parent / "stage-result.json").write_text(json.dumps({
                "schema": "codex.chatgpt.oracle-stage-result/v1", "workflow_id": "a" * 32,
                "stage": "plan", "attempt_id": attempt, "input_mission_sha256": input_sha,
                "status": "PASS", "output_path": str(output), "output_sha256": module.sha(output),
                "next_stage": "review", "next_mission_path": str(review), "next_mission_sha256": module.sha(review),
                "ready_for_next": True, "blocker": "",
            }), encoding="utf-8")
            return {"ok": True, "run_dir": str(run_dir)}
        return {"ok": False, "run_dir": str(run_dir)}

    result = module.run_workflow(manifest(tmp_path), oracle_execute=fake_execute)
    assert result["status"] == "attention_required"
    assert result["current_stage"] == "review"
    assert calls == ["plan", "review"]
    assert result["next_index"] == 1


def test_running_web_multi_rebinds_only_persisted_parent_result(tmp_path: Path) -> None:
    module = load()
    path = manifest(tmp_path)
    config = module.load_manifest(path)
    multi_source = tmp_path / "multi.json"
    multi_source.write_text("{}", encoding="utf-8")
    review = tmp_path / "review.md"
    review.write_text("review", encoding="utf-8")
    output = tmp_path / "multi-output.md"
    output.write_text("merged", encoding="utf-8")
    receipt = tmp_path / "multi-receipt.json"
    parent_id = "b" * 64
    receipt.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-stage-result/v1", "workflow_id": "a" * 32,
        "stage": "web-multi", "attempt_id": parent_id, "input_mission_sha256": module.sha(multi_source),
        "status": "PASS", "output_path": str(output), "output_sha256": module.sha(output),
        "next_stage": "review", "next_mission_path": str(review), "next_mission_sha256": module.sha(review),
        "ready_for_next": True, "blocker": "",
    }), encoding="utf-8")
    result_path = tmp_path / "multi-result.json"
    result_path.write_text(json.dumps({
        "schema": module.MULTI.RESULT_SCHEMA, "status": "complete", "parent_id": parent_id,
        "next_stage_result_path": str(receipt),
    }), encoding="utf-8")
    state_path = module._state_path(config, "a" * 32)
    module._write(state_path, {
        "schema": module.STATE_SCHEMA, "status": "running", "workflow_id": "a" * 32,
        "manifest_sha256": config["manifest_sha256"], "current_stage": "web-multi",
        "current_mission_path": str(multi_source), "next_index": 0, "records": [],
        "multi_execution_id": "c" * 64, "multi_manifest_sha256": module.sha(multi_source),
        "multi_result_path": str(result_path), "multi_receipt_path": str(receipt),
    })
    calls = 0

    def fake_oracle(oracle_manifest: Path, *, dry_run: bool):
        nonlocal calls
        calls += 1
        return {"ok": False, "run_dir": str(_oracle_running_state(module, oracle_manifest))}

    def never_multi(*args, **kwargs):
        raise AssertionError("stored Web Multi result must be rebound, not resubmitted")

    result = module.run_workflow(path, oracle_execute=fake_oracle, multi_execute=never_multi)
    assert result["status"] == "attention_required"
    assert result["current_stage"] == "review"
    assert calls == 1
    assert result["records"][0]["parent_id"] == parent_id


def test_default_recovery_uses_the_persisted_parallel_child_mutex(monkeypatch, tmp_path: Path) -> None:
    module = load()
    calls = []
    run_dir = tmp_path / "exact-run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        json.dumps({"schema": module.RUNNER.STATE.STATE_SCHEMA, "parallel_parent_id": "a" * 64}),
        encoding="utf-8",
    )

    def fake_recover(run_dir: Path, *, action: str, dry_run: bool):
        calls.append((run_dir, action, dry_run))
        return {"ok": True}

    monkeypatch.setattr(module.RUNNER, "recover_run", fake_recover)
    value = module._recover_oracle_under_workflow_mutex(run_dir, action="harvest", dry_run=False)
    assert value["ok"] is True
    assert calls == [(run_dir.resolve(), "harvest", False)]


def test_default_recovery_rejects_a_nonparallel_child(tmp_path: Path) -> None:
    module = load()
    run_dir = tmp_path / "exact-run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        json.dumps({"schema": module.RUNNER.STATE.STATE_SCHEMA, "run_id": "x"}),
        encoding="utf-8",
    )
    value = module._recover_oracle_under_workflow_mutex(run_dir, action="harvest", dry_run=False)
    assert value["ok"] is False
    assert value["error"] == "ORACLE_RECOVERY_PARALLEL_PARENT_MISSING"


def test_web_multi_preflight_failure_stays_prepared_and_rejects_changed_mission(tmp_path: Path) -> None:
    module = load()
    workflow_path = manifest(tmp_path)
    invalid_multi = tmp_path / "multi.json"
    invalid_multi.write_text(json.dumps({"next_stage_binding": {"workflow_id": "wrong", "stage": "web-multi"}}), encoding="utf-8")

    def fake_plan(oracle_manifest: Path, *, dry_run: bool):
        payload = json.loads(oracle_manifest.read_text(encoding="utf-8"))
        mission = Path(payload["mission_path"])
        text = mission.read_text(encoding="utf-8")
        assert "stage=plan\n" in text
        attempt = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("attempt_id="))
        input_sha = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("input_mission_sha256="))
        output = mission.parent / "plan-out.md"
        output.write_text("plan", encoding="utf-8")
        (mission.parent / "stage-result.json").write_text(json.dumps({
            "schema": module.RECEIPT_SCHEMA, "workflow_id": "a" * 32, "stage": "plan",
            "attempt_id": attempt, "input_mission_sha256": input_sha, "status": "PASS",
            "output_path": str(output), "output_sha256": module.sha(output), "next_stage": "web-multi",
            "next_mission_path": str(invalid_multi), "next_mission_sha256": module.sha(invalid_multi),
            "ready_for_next": True, "blocker": "",
        }), encoding="utf-8")
        return {"ok": True, "run_dir": str(mission.parent / "run")}

    with pytest.raises(module.MULTI.MultiError):
        module.run_workflow(workflow_path, oracle_execute=fake_plan)
    config = module.load_manifest(workflow_path)
    stored = module._json(module._state_path(config, "a" * 32))
    assert stored["status"] == "prepared"
    assert stored["next_stage"] == "web-multi"
    assert "multi_execution_id" not in stored

    lane_one = tmp_path / "one.md"
    lane_two = tmp_path / "two.md"
    merger = tmp_path / "merger.md"
    for path in (lane_one, lane_two, merger):
        path.write_text(path.stem, encoding="utf-8")
    invalid_multi.write_text(json.dumps({
        "schema": module.MULTI.SCHEMA, "project_root": str(tmp_path),
        "output_dir": str(tmp_path / "multi-output"),
        "solvers": [{"id": "one", "mission_path": str(lane_one)}, {"id": "two", "mission_path": str(lane_two)}],
        "merger_mission_path": str(merger),
        "next_stage_binding": {"workflow_id": "a" * 32, "stage": "web-multi"},
    }), encoding="utf-8")
    calls = 0

    def fake_multi(path: Path, *, dry_run: bool, parent_lock_held: bool):
        nonlocal calls
        calls += 1
        return {"ok": False, "parent_id": "d" * 64}

    with pytest.raises(module.WorkflowError, match="prepared next mission changed"):
        module.run_workflow(workflow_path, oracle_execute=fake_plan, multi_execute=fake_multi)
    assert calls == 0


def test_stage_contract_preserves_upstream_input_mission_hash_semantics(tmp_path: Path) -> None:
    module = load()
    path = manifest(tmp_path)
    config = module.load_manifest(path)
    source = config["initial_mission_path"]
    mission, _, input_sha = module._stage_mission(
        config,
        config["workflow_id"],
        0,
        "plan",
        source,
        "b" * 32,
    )
    text = mission.read_text(encoding="utf-8")

    assert input_sha == module.sha(source)
    assert f"input_mission_sha256={input_sha}" in text
    assert "binds the upstream source mission bytes" in text
    assert "do not replace it with a hash of this augmented mission.md" in text


def test_receipt_accepts_legacy_schema_version_but_keeps_upstream_hash_binding(tmp_path: Path) -> None:
    module = load()
    path = manifest(tmp_path)
    config = module.load_manifest(path)
    source = config["initial_mission_path"]
    mission, receipt_path, input_sha = module._stage_mission(
        config, config["workflow_id"], 0, "plan", source, "b" * 32
    )
    output = config["project_root"] / "plan.md"
    output.write_text("plan", encoding="utf-8")
    next_mission = config["project_root"] / "review.md"
    next_mission.write_text("review", encoding="utf-8")
    receipt_path.write_text(json.dumps({
        "schema_version": module.RECEIPT_SCHEMA,
        "workflow_id": config["workflow_id"],
        "stage": "plan",
        "attempt_id": "b" * 32,
        "input_mission_sha256": input_sha,
        "status": "PLAN_READY",
        "output_path": str(output),
        "output_sha256": module.sha(output),
        "next_stage": "review",
        "next_mission_path": str(next_mission),
        "next_mission_sha256": module.sha(next_mission),
        "ready_for_next": True,
        "blocker": None,
    }), encoding="utf-8")

    receipt = module._validate_receipt(
        config, receipt_path, config["workflow_id"], "plan", "b" * 32, input_sha
    )
    assert receipt["_next_mission"] == next_mission

    value = json.loads(receipt_path.read_text(encoding="utf-8"))
    value["input_mission_sha256"] = module.sha(mission)
    receipt_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.WorkflowError, match="stage receipt identity mismatch"):
        module._validate_receipt(
            config, receipt_path, config["workflow_id"], "plan", "b" * 32, input_sha
        )


def test_receipt_rejects_conflicting_schema_aliases(tmp_path: Path) -> None:
    module = load()
    path = manifest(tmp_path)
    config = module.load_manifest(path)
    receipt = config["project_root"] / "stage-result.json"
    receipt.write_text(json.dumps({
        "schema": module.RECEIPT_SCHEMA,
        "schema_version": "different",
    }), encoding="utf-8")
    with pytest.raises(module.WorkflowError, match="schema keys conflict"):
        module._validate_receipt(
            config, receipt, config["workflow_id"], "plan", "b" * 32, "c" * 64
        )

    receipt.write_text(json.dumps({
        "schema": None,
        "schema_version": module.RECEIPT_SCHEMA,
    }), encoding="utf-8")
    with pytest.raises(module.WorkflowError, match="schema keys conflict"):
        module._validate_receipt(
            config, receipt, config["workflow_id"], "plan", "b" * 32, "c" * 64
        )


def test_awaiting_receipt_preserves_source_and_augmented_mission_bindings(tmp_path: Path) -> None:
    module = load()
    path = manifest(tmp_path)

    def fake_oracle(oracle_manifest: Path, *, dry_run: bool):
        data = json.loads(oracle_manifest.read_text(encoding="utf-8"))
        mission = Path(data["mission_path"])
        contract = mission.read_text(encoding="utf-8")
        receipt_path = Path(next(
            line.split(": ", 1)[1]
            for line in contract.splitlines()
            if line.startswith("Write the small UTF-8 stage receipt to: ")
        ))
        return {"ok": True, "run_dir": str(receipt_path.parent / "oracle-run")}

    result = module.run_workflow(path, oracle_execute=fake_oracle)
    assert result["status"] == "awaiting_receipt"
    source = Path(result["current_binding_source_path"])
    augmented = Path(result["current_augmented_mission_path"])
    assert result["current_binding_source_sha256"] == module.sha(source)
    assert result["current_augmented_mission_sha256"] == module.sha(augmented)
    assert result["current_binding_source_sha256"] != result["current_augmented_mission_sha256"]
