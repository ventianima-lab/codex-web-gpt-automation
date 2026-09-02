from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    path = Path(__file__).resolve().parents[1] / "scripts/retire_legacy_oracle_lock.py"
    spec = importlib.util.spec_from_file_location("legacy_lock_retirement_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setenv("CODEX_THREAD_ID", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str(tmp_path / "ledger"))
    project = tmp_path / "project"
    project.mkdir()
    mission = project / "mission.md"
    mission.write_text("Read the project.", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": module.STATE.SCHEMA, "project_root": str(project),
        "mission_path": str(mission), "app_name": "codex", "mode": "browser",
        "run_root": str(tmp_path / "ledger/runs"),
    }))
    config = module.STATE.load_manifest(manifest)
    layout = module.STATE.create_layout(config, run_id="legacy-run-12345678")
    layout.run_dir.mkdir(parents=True)
    state = module.STATE.state_payload(config, layout, status="attention_required",
                                       resolved_version="0.18.0", cdp_port=43101)
    state.update(session_authority="submitted_unknown", transport_status="failed",
                 browser_observer={"oracle_process_pid": 2_000_000_001})
    state["mission"]["transport_path"] = str(layout.run_dir / "mission.md")
    module.STATE.write_json_atomic(layout.state_path, state)
    module.STATE.persist_ownership_receipt(layout.state_path, oracle_process_pid=2_000_000_001)
    (layout.run_dir / "mission.md").write_bytes(mission.read_bytes())
    (layout.run_dir / "stdout.log").write_text(
        f"Session: {layout.slug}\nERROR: connect ECONNREFUSED 127.0.0.1:43101\n")
    (layout.run_dir / "stderr.log").write_text("")
    (layout.run_dir / "recovery-harvest-stdout.log").write_text(
        f'No live ChatGPT tab matched session "{layout.slug}". Attempting recovery by reopening the saved conversation URL.\n')
    (layout.run_dir / "recovery-harvest-stderr.log").write_text(
        "Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL "
        "(expected browser.harvest.url or browser.runtime.tabUrl to be a chatgpt.com/c/<id> URL).\n")
    session_root = tmp_path / "sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))
    meta = session_root / layout.slug / "meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({
        "id": layout.slug, "status": "error", "completedAt": "2026-09-02T00:00:00Z",
        "error": {"message": "connect ECONNREFUSED 127.0.0.1:43101"},
        "browser": {"config": {"debugPort": 43101}},
    }))
    monkeypatch.setattr(module.RUNNER, "run_owned_process_is_alive", lambda *args: False)
    monkeypatch.setattr(module, "cdp_connection_refused", lambda port: True)
    args = {"expected_state_sha256": module.STATE.sha256_file(layout.state_path),
            "expected_meta_sha256": module.STATE.sha256_file(meta),
            "confirmation": module.CONFIRMATION,
            "reason": "User explicitly authorized retirement of this exact legacy lock."}
    return module, layout, meta, args


def test_retirement_removes_global_lock_and_preserves_exact_evidence(candidate):
    module, layout, meta, args = candidate
    before = {p.name: p.read_bytes() for p in layout.run_dir.iterdir()}
    meta_before = meta.read_bytes()
    project = Path(module.STATE.load_state(layout.state_path)["project_root"])
    assert len(module.STATE.unresolved_project_sessions(layout.run_dir.parent, project)) == 1
    preview = module.retire(layout.run_dir, **args, dry_run=True)
    assert preview["status"] == "dry-run"
    assert not Path(preview["archive_run_dir"]).parent.exists()
    assert not Path(preview["intent_receipt"]).parent.exists()
    result = module.retire(layout.run_dir, **args)
    archive = Path(result["archive_run_dir"])
    assert not layout.run_dir.exists()
    assert {p.name: p.read_bytes() for p in archive.iterdir()} == before
    assert meta.read_bytes() == meta_before
    assert module.STATE.unresolved_project_sessions(layout.run_dir.parent, project) == []
    assert result["provider_outcome"] == "unknown"
    assert result["new_submission_authorized"] is False
    completion = json.loads(Path(result["completion_receipt"]).read_text())
    assert completion["intent_sha256"] == module.STATE.sha256_file(Path(result["intent_receipt"]))
    assert completion["evaluated_from_thread"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert completion["target_source_thread_id"] is None


def test_retirement_preserves_other_tasks_lock(candidate):
    module, layout, _, args = candidate
    foreign = layout.run_dir.parent / "foreign-run-12345678"
    foreign.mkdir()
    state = module.STATE.load_state(layout.state_path)
    project = Path(state["project_root"])
    state["run_id"] = foreign.name
    state["originating_task"].update(binding="bound", source_thread_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    module.STATE.write_json_atomic(foreign / "state.json", state)
    before = (foreign / "state.json").read_bytes()
    module.retire(layout.run_dir, **args)
    assert (foreign / "state.json").read_bytes() == before
    assert [entry["run_id"] for entry in module.STATE.unresolved_project_sessions(
        layout.run_dir.parent, project
    )] == [foreign.name]


@pytest.mark.parametrize("failure", [
    "state-hash", "meta-hash", "state-owner", "receipt-owner", "live-process", "live-port",
    "output", "conversation", "wrong-slug", "mission-hash", "confirmation", "no-caller",
    "archive-exists", "archive-symlink",
])
def test_retirement_fails_closed_without_moving_the_run(candidate, monkeypatch, failure):
    module, layout, meta, args = candidate
    if failure in {"state-hash", "meta-hash"}:
        args["expected_" + failure.split("-")[0] + "_sha256"] = "0" * 64
    elif failure == "state-owner":
        state = module.STATE.load_state(layout.state_path)
        state["originating_task"].update(binding="bound", source_thread_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        module.STATE.write_json_atomic(layout.state_path, state)
        args["expected_state_sha256"] = module.STATE.sha256_file(layout.state_path)
    elif failure == "receipt-owner":
        receipt_path = layout.run_dir / "ownership-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["source_thread_id"] = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        receipt_path.write_text(json.dumps(receipt))
    elif failure == "live-process":
        monkeypatch.setattr(module.RUNNER, "run_owned_process_is_alive", lambda *args: True)
    elif failure == "live-port":
        monkeypatch.setattr(module, "cdp_connection_refused", lambda port: False)
    elif failure == "output":
        (layout.run_dir / "output.md").write_text("answer")
    elif failure in {"conversation", "wrong-slug"}:
        value = json.loads(meta.read_text())
        if failure == "conversation":
            value["browser"]["runtime"] = {"tabUrl": "https://chatgpt.com/c/exact"}
        else:
            value["id"] = "another-session"
        meta.write_text(json.dumps(value))
        args["expected_meta_sha256"] = module.STATE.sha256_file(meta)
    elif failure == "mission-hash":
        (layout.run_dir / "mission.md").write_text("changed")
    elif failure == "confirmation":
        args["confirmation"] = "force"
    elif failure == "no-caller":
        monkeypatch.delenv("CODEX_THREAD_ID")
    elif failure == "archive-exists":
        (layout.run_dir.parent.parent / "retired-runs" / layout.run_id).mkdir(parents=True)
    elif failure == "archive-symlink":
        try:
            (layout.run_dir.parent.parent / "retired-runs").symlink_to(layout.run_dir.parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation unavailable on this test host")
    state_before = layout.state_path.read_bytes()
    with pytest.raises(ValueError):
        module.retire(layout.run_dir, **args)
    assert layout.state_path.read_bytes() == state_before
    assert not (layout.run_dir.parent.parent / "retired-lock-receipts").exists()
