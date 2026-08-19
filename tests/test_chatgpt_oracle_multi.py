from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_multi.py"


def load():
    spec = importlib.util.spec_from_file_location("oracle_multi_test", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_manifest(tmp_path: Path, count: int = 7) -> Path:
    missions = []
    for index in range(count):
        path = tmp_path / f"solver-{index}.md"
        path.write_text(f"solve {index}", encoding="utf-8")
        missions.append({"id": f"s{index}", "mission_path": str(path.resolve())})
    merger = tmp_path / "merge.md"
    merger.write_text("Merge every listed handoff.", encoding="utf-8")
    manifest = tmp_path / "multi.json"
    manifest.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-multi/v1",
        "project_root": str(tmp_path.resolve()),
        "output_dir": str((tmp_path / "out").resolve()),
        "app_name": "DevSpace",
        "model": "gpt-5.6",
        "max_concurrency": 5,
        "solvers": missions,
        "merger_mission_path": str(merger.resolve()),
    }), encoding="utf-8")
    return manifest


def make_strict_manifest(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "tests@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Tests"], check=True)
    (root / "runtime.txt").write_text("base runtime\n", encoding="utf-8")
    (root / "tests.txt").write_text("base tests\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
    output = root / ".workflow" / "ultra"
    output.mkdir(parents=True)
    worktrees = [output / "worktrees" / "runtime", output / "worktrees" / "tests"]
    for worktree in worktrees:
        subprocess.run(
            ["git", "-C", str(root), "worktree", "add", "--detach", str(worktree), "HEAD"],
            check=True,
            capture_output=True,
        )
    missions = []
    for lane in ("runtime", "tests"):
        mission = output / f"{lane}.md"
        mission.write_text(f"Implement {lane} scope.", encoding="utf-8")
        missions.append(mission)
    merger = output / "merge.md"
    merger.write_text("Inspect the combined implementation and write the final receipt.", encoding="utf-8")
    copy_profile = tmp_path / "browser-profile"
    copy_profile.mkdir()
    manifest = output / "multi.json"
    manifest.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-multi/v2",
        "project_root": str(root.resolve()),
        "output_dir": str(output.resolve()),
        "allowed_worktree_roots": [str(path.resolve()) for path in worktrees],
        "app_name": "codex",
        "model": "gpt-5.6",
        "copy_profile": str(copy_profile.resolve()),
        "max_concurrency": 2,
        "all_lanes_required": True,
        "partial_merge_allowed": False,
        "solvers": [
            {"id": "runtime", "mission_path": str(missions[0]), "project_root": str(worktrees[0]), "access": "worktree-write", "owned_paths": ["runtime.txt"]},
            {"id": "tests", "mission_path": str(missions[1]), "project_root": str(worktrees[1]), "access": "worktree-write", "owned_paths": ["tests.txt"]},
        ],
        "merger_mission_path": str(merger.resolve()),
        "next_stage_result_path": str((output / "stage-result.json").resolve()),
    }), encoding="utf-8")
    return manifest


def test_manifest_accepts_configured_workspace_app_name(tmp_path: Path) -> None:
    module = load()
    path = make_manifest(tmp_path, 2)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["app_name"] = "OtherWorkspace"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert module.load_manifest(path)["app_name"] == "OtherWorkspace"


def test_multi_uses_unique_child_manifests_waves_and_merger(tmp_path: Path) -> None:
    module = load()
    calls = []

    def fake_execute(path: Path, *, dry_run: bool):
        value = json.loads(path.read_text(encoding="utf-8"))
        calls.append(value)
        run_dir = path.parent / "fake-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "output.md").write_text(f"answer {path.parent.name}", encoding="utf-8")
        return {"ok": True, "run_dir": str(run_dir)}

    result = module.run_multi(make_manifest(tmp_path), execute=fake_execute)
    assert result["ok"] is True
    assert result["status"] == "complete"
    assert len(result["lanes"]) == 7
    assert len(calls) == 8
    assert len({item["parallel_parent_id"] for item in calls}) == 1
    assert all(item["app_name"] == "DevSpace" for item in calls)
    assert all(item["model"] == "gpt-5.6" for item in calls)
    assert all(item["model_strategy"] == "select" for item in calls)
    assert all(item["thinking_time"] == "extra-high" for item in calls)
    assert all(item["copy_profile"] for item in calls)
    merger_text = Path(calls[-1]["mission_path"]).read_text(encoding="utf-8")
    assert merger_text.count(".md") == 7


def test_multi_preserves_partial_results_and_rejects_over_capacity(tmp_path: Path) -> None:
    module = load()
    manifest = make_manifest(tmp_path, 3)
    def fake_execute(path: Path, *, dry_run: bool):
        run_dir = path.parent / "fake-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        if path.parent.name == "s1":
            return {"ok": False, "run_dir": str(run_dir)}
        (run_dir / "output.md").write_text("ok", encoding="utf-8")
        return {"ok": True, "run_dir": str(run_dir)}

    result = module.run_multi(manifest, execute=fake_execute)
    assert result["status"] == "partial"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["max_concurrency"] = 6
    manifest.write_text(json.dumps(value), encoding="utf-8")
    try:
        module.load_manifest(manifest)
    except module.MultiError:
        pass
    else:
        raise AssertionError("capacity > 5 must fail")


def test_multi_rejects_lane_path_traversal(tmp_path: Path) -> None:
    module = load()
    manifest = make_manifest(tmp_path, 2)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["solvers"][0]["id"] = "../../outside"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    try:
        module.load_manifest(manifest)
    except module.MultiError:
        pass
    else:
        raise AssertionError("unsafe lane id must fail")


def test_multi_accepts_parallel_strict_worktrees_and_injects_exact_ownership(tmp_path: Path) -> None:
    module = load()
    manifest = make_strict_manifest(tmp_path)
    config = module.load_manifest(manifest)

    child = module._child_manifest(config, config["solvers"][0], "f" * 64)
    child_value = json.loads(child.read_text(encoding="utf-8"))
    effective = Path(child_value["mission_path"])
    text = effective.read_text(encoding="utf-8")
    provenance = json.loads((child.parent / "child-provenance.json").read_text(encoding="utf-8"))

    assert "[WEB_MULTI_STRICT_WORKTREE_WRITE_CONTRACT]" in text
    assert "runtime.txt" in text
    assert provenance["access"] == "worktree-write"
    assert provenance["owned_paths"] == ["runtime.txt"]
    assert provenance["mission_path"] == str(effective)
    assert provenance["canonical_project_root"] == str(config["project_root"])
    child_config = module.STATE.load_manifest(child)
    assert module.RUNNER.web_multi_devspace_qualification_target(child_config) == config["project_root"]


@pytest.mark.parametrize("claims", [
    ("src", "src"),
    ("src", "src/feature.py"),
    ("tests/test_feature.py", "tests"),
])
def test_multi_rejects_overlapping_strict_writer_claims(tmp_path: Path, claims: tuple[str, str]) -> None:
    module = load()
    manifest = make_strict_manifest(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    for index, lane in enumerate(value["solvers"]):
        lane["owned_paths"] = [claims[index]]
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(module.MultiError, match="pairwise non-overlapping"):
        module.load_manifest(manifest)


def test_multi_rejects_strict_writer_without_owned_paths(tmp_path: Path) -> None:
    module = load()
    manifest = make_strict_manifest(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["solvers"][0]["owned_paths"] = []
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(module.MultiError, match="nonempty owned_paths"):
        module.load_manifest(manifest)


def strict_executor(module, tmp_path: Path, *, failed_lane: str | None = None, rogue_lane: str | None = None):
    calls: list[tuple[str, bool]] = []

    def execute(path: Path, *, dry_run: bool):
        value = json.loads(path.read_text(encoding="utf-8"))
        lane_id = path.parent.name
        calls.append((lane_id, dry_run))
        if dry_run:
            return {"ok": True, "preview": value}
        if lane_id == failed_lane:
            return {"ok": False, "run_dir": str(path.parent / "failed-run")}
        if lane_id in {"runtime", "tests"}:
            lane_root = Path(value["project_root"])
            owned = "runtime.txt" if lane_id == "runtime" else "tests.txt"
            (lane_root / owned).write_text(f"{lane_id} changed\n", encoding="utf-8")
            if lane_id == rogue_lane:
                (lane_root / "rogue.txt").write_text("out of scope\n", encoding="utf-8")
        run_dir = path.parent / "fake-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "output.md").write_text(f"answer {lane_id}", encoding="utf-8")
        if lane_id == "merger":
            provenance = json.loads(Path(value["web_multi_child_provenance_path"]).read_text(encoding="utf-8"))
            config = module.load_manifest(Path(provenance["parent_manifest_path"]))
            config["next_stage_result_path"].write_text("{}\n", encoding="utf-8")
        return {"ok": True, "run_dir": str(run_dir)}

    return execute, calls


def test_strict_multi_applies_only_after_all_lane_audits_then_runs_merger(tmp_path: Path) -> None:
    module = load()
    manifest = make_strict_manifest(tmp_path)
    config = module.load_manifest(manifest)
    execute, calls = strict_executor(module, tmp_path)

    result = module.run_multi(manifest, execute=execute)

    assert result["ok"] is True
    assert result["status"] == "complete"
    assert (config["project_root"] / "runtime.txt").read_text(encoding="utf-8") == "runtime changed\n"
    assert (config["project_root"] / "tests.txt").read_text(encoding="utf-8") == "tests changed\n"
    assert calls[-1] == ("merger", False)
    assert sum(1 for _, dry_run in calls if dry_run) == 2
    ledger = json.loads((config["output_dir"] / "result.json").read_text(encoding="utf-8"))
    assert ledger["schema"] == module.STRICT_RESULT_SCHEMA
    assert ledger["manifest_sha256"] == config["manifest_sha256"]
    assert all(lane["audit"]["changed_paths"] for lane in ledger["lanes"])


@pytest.mark.parametrize(("failed_lane", "rogue_lane"), [("tests", None), (None, "runtime")])
def test_strict_multi_blocks_partial_or_out_of_scope_results_before_canonical_apply(
    tmp_path: Path, failed_lane: str | None, rogue_lane: str | None
) -> None:
    module = load()
    manifest = make_strict_manifest(tmp_path)
    config = module.load_manifest(manifest)
    execute, calls = strict_executor(module, tmp_path, failed_lane=failed_lane, rogue_lane=rogue_lane)

    result = module.run_multi(manifest, execute=execute)

    assert result["ok"] is False
    assert result["status"] == "writers_attention_required"
    assert (config["project_root"] / "runtime.txt").read_text(encoding="utf-8") == "base runtime\n"
    assert (config["project_root"] / "tests.txt").read_text(encoding="utf-8") == "base tests\n"
    assert not any(lane == "merger" and not dry_run for lane, dry_run in calls)


def test_strict_multi_existing_ledger_requires_exact_recovery(tmp_path: Path) -> None:
    module = load()
    manifest = make_strict_manifest(tmp_path)
    config = module.load_manifest(manifest)
    module._write_json(config["output_dir"] / "result.json", {
        "schema": module.STRICT_RESULT_SCHEMA,
        "status": "writers_running",
        "parent_id": "a" * 64,
        "manifest_sha256": config["manifest_sha256"],
        "lanes": [],
    })

    with pytest.raises(module.MultiError, match="EXISTING_LEDGER_REQUIRES_EXACT_RECOVERY"):
        module.run_multi(manifest, execute=lambda *_args, **_kwargs: {"ok": True})


def test_reconcile_recovered_lanes_restores_stable_order_without_submission(tmp_path: Path, monkeypatch) -> None:
    module = load()
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str((tmp_path / "state").resolve()))
    manifest = make_manifest(tmp_path, 3)
    config = module.load_manifest(manifest)
    parent_id = "a" * 64
    recorded = []
    for lane in reversed(config["solvers"]):
        run_dir = tmp_path / "state" / lane["id"]
        run_dir.mkdir(parents=True)
        output = run_dir / "output.md"
        output.write_text(f"answer {lane['id']}", encoding="utf-8")
        artifact_sha = module.hashlib.sha256(output.read_bytes()).hexdigest()
        locator = f"oracle-{lane['id']}"
        (run_dir / "state.json").write_text(json.dumps({
            "project_root": str(tmp_path.resolve()),
            "parallel_parent_id": parent_id,
            "status": "complete",
            "terminal_harvested": True,
            "artifact_sha256": artifact_sha,
            "mission": {"sha256": module.hashlib.sha256(lane["mission_path"].read_bytes()).hexdigest()},
            "oracle": {"session_locator": locator},
        }), encoding="utf-8")
        recorded.append({"id": lane["id"], "ok": False, "run_dir": str(run_dir), "session_locator": locator})
    module._write_json(config["output_dir"] / "result.json", {
        "schema": module.RESULT_SCHEMA,
        "status": "failed",
        "parent_id": parent_id,
        "lanes": recorded,
        "merger_run_dir": str(tmp_path / "failed-pre-submit-merger"),
    })

    result = module.reconcile_recovered_lanes(manifest)

    assert result["status"] == "merger_ready"
    assert [lane["id"] for lane in result["lanes"]] == ["s0", "s1", "s2"]
    assert result["successful_lane_count"] == 3
    merger_text = Path(result["merger_mission_path"]).read_text(encoding="utf-8")
    positions = [
        merger_text.index(str(config["output_dir"] / "handoffs" / f"s{index}.md"))
        for index in range(3)
    ]
    assert positions == sorted(positions)
    assert result["merger_run_dir"].endswith("failed-pre-submit-merger")


def test_reconcile_recovered_lanes_rejects_parent_identity_mismatch(tmp_path: Path, monkeypatch) -> None:
    module = load()
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str(tmp_path.resolve()))
    manifest = make_manifest(tmp_path, 2)
    config = module.load_manifest(manifest)
    module._write_json(config["output_dir"] / "result.json", {
        "schema": module.RESULT_SCHEMA,
        "status": "failed",
        "parent_id": "a" * 64,
        "lanes": [
            {"id": lane["id"], "run_dir": str(tmp_path / lane["id"]), "session_locator": f"oracle-{lane['id']}"}
            for lane in config["solvers"]
        ],
    })
    first = config["solvers"][0]
    run_dir = tmp_path / first["id"]
    run_dir.mkdir()
    output = run_dir / "output.md"
    output.write_text("answer", encoding="utf-8")
    (run_dir / "state.json").write_text(json.dumps({
        "project_root": str(tmp_path.resolve()),
        "parallel_parent_id": "b" * 64,
        "status": "complete",
        "terminal_harvested": True,
        "artifact_sha256": module.hashlib.sha256(output.read_bytes()).hexdigest(),
        "mission": {"sha256": module.hashlib.sha256(first["mission_path"].read_bytes()).hexdigest()},
        "oracle": {"session_locator": f"oracle-{first['id']}"},
    }), encoding="utf-8")

    with pytest.raises(module.MultiError, match="parent identity mismatch"):
        module.reconcile_recovered_lanes(manifest)


def test_strict_reconcile_audits_all_exact_runs_before_apply_and_merger_resume(tmp_path: Path, monkeypatch) -> None:
    module = load()
    state_root = (tmp_path / "oracle-state").resolve()
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str(state_root))
    manifest = make_strict_manifest(tmp_path)
    config = module.load_manifest(manifest)
    parent_id = "d" * 64
    baselines = {lane["id"]: module._strict_git_identity(lane["project_root"]) for lane in config["solvers"]}
    recorded = []
    for lane in config["solvers"]:
        relative = lane["owned_paths"][0]
        (lane["project_root"] / relative).write_text(f"recovered {lane['id']}\n", encoding="utf-8")
        effective = module._effective_lane_mission(config, lane)
        run_dir = state_root / "runs" / lane["id"]
        run_dir.mkdir(parents=True)
        output = run_dir / "output.md"
        output.write_text(f"answer {lane['id']}\n", encoding="utf-8")
        locator = f"oracle-{lane['id']}"
        (run_dir / "state.json").write_text(json.dumps({
            "project_root": str(lane["project_root"]),
            "parallel_parent_id": parent_id,
            "status": "complete",
            "terminal_harvested": True,
            "artifact_sha256": module.hashlib.sha256(output.read_bytes()).hexdigest(),
            "mission": {"sha256": module.hashlib.sha256(effective.read_bytes()).hexdigest()},
            "oracle": {"session_locator": locator},
        }), encoding="utf-8")
        recorded.append({"id": lane["id"], "run_dir": str(run_dir), "session_locator": locator})
    module._write_json(config["output_dir"] / "result.json", {
        "schema": module.STRICT_RESULT_SCHEMA,
        "status": "writers_attention_required",
        "parent_id": parent_id,
        "manifest_sha256": config["manifest_sha256"],
        "strict_baselines": baselines,
        "lanes": recorded,
    })

    reconciled = module.reconcile_recovered_lanes(manifest)

    assert reconciled["status"] == "merger_ready"
    assert all(lane["audit"]["changed_paths"] for lane in reconciled["lanes"])
    assert (config["project_root"] / "runtime.txt").read_text(encoding="utf-8") == "recovered runtime\n"
    assert (config["project_root"] / "tests.txt").read_text(encoding="utf-8") == "recovered tests\n"
    assert module.reconcile_recovered_lanes(manifest)["status"] == "merger_ready"

    def merger_execute(path: Path, *, dry_run: bool):
        assert dry_run is False
        run_dir = path.parent / "recovered-merger-run"
        run_dir.mkdir(parents=True)
        (run_dir / "output.md").write_text("merged\n", encoding="utf-8")
        return {"ok": True, "run_dir": str(run_dir)}

    resumed = module.resume_recovered_merger(manifest, execute=merger_execute)
    assert resumed["ok"] is True
    assert resumed["status"] == "complete"
    assert resumed["schema"] == module.STRICT_RESULT_SCHEMA


def test_resume_recovered_merger_submits_only_stable_order_merger(tmp_path: Path, monkeypatch) -> None:
    module = load()
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str((tmp_path / "state").resolve()))
    manifest = make_manifest(tmp_path, 2)
    config = module.load_manifest(manifest)
    parent_id = "c" * 64
    recorded = []
    for lane in config["solvers"]:
        run_dir = tmp_path / "state" / lane["id"]
        run_dir.mkdir(parents=True)
        output = run_dir / "output.md"
        output.write_text(f"answer {lane['id']}", encoding="utf-8")
        artifact_sha = module.hashlib.sha256(output.read_bytes()).hexdigest()
        locator = f"oracle-{lane['id']}"
        (run_dir / "state.json").write_text(json.dumps({
            "project_root": str(tmp_path.resolve()),
            "parallel_parent_id": parent_id,
            "status": "complete",
            "terminal_harvested": True,
            "artifact_sha256": artifact_sha,
            "mission": {"sha256": module.hashlib.sha256(lane["mission_path"].read_bytes()).hexdigest()},
            "oracle": {"session_locator": locator},
        }), encoding="utf-8")
        recorded.append({"id": lane["id"], "run_dir": str(run_dir), "session_locator": locator})
    module._write_json(config["output_dir"] / "result.json", {
        "schema": module.RESULT_SCHEMA,
        "status": "failed",
        "parent_id": parent_id,
        "lanes": recorded,
        "merger_run_dir": str(tmp_path / "old-pre-submit-merger"),
    })
    module.reconcile_recovered_lanes(manifest)
    calls = []

    def fake_execute(path: Path, *, dry_run: bool):
        calls.append(json.loads(path.read_text(encoding="utf-8")))
        return {"ok": True, "run_dir": str(tmp_path / "new-merger-run")}

    result = module.resume_recovered_merger(manifest, execute=fake_execute)

    assert result["status"] == "complete"
    assert len(calls) == 1
    assert calls[0]["parallel_parent_id"] == parent_id
    assert Path(calls[0]["mission_path"]).name == "mission.md"
    assert result["merger_run_dir"].endswith("new-merger-run")
    assert result["prior_merger_run_dirs"] == [str(tmp_path / "old-pre-submit-merger")]
