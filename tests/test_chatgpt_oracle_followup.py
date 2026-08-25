from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


RUNNER_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_run.py"
OWNER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
FOREIGN = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def load_runner():
    name = "chatgpt_oracle_followup_test"
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def make_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = load_runner()
    project = tmp_path / "project"
    project.mkdir()
    mission = project / "parent.md"
    mission.write_text("parent task", encoding="utf-8")
    followup = project / "followup.md"
    followup.write_text("follow up only", encoding="utf-8")
    host = tmp_path / "host"
    run_root = host / "runs"
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str(host))
    monkeypatch.setenv("CODEX_THREAD_ID", OWNER)
    manifest = tmp_path / "parent.json"
    manifest.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-run/v1",
        "project_root": str(project), "mission_path": str(mission), "app_name": "codex",
        "mode": "browser", "transport": "pro-devspace-readonly", "run_root": str(run_root),
        "oracle_command": ["oracle"], "model": "gpt-5.6-sol", "model_strategy": "select",
        "thinking_time": "heavy", "research": "off", "task_outcome_contract": "v1",
        "source_thread_id": OWNER,
    }), encoding="utf-8")
    config = runner.STATE.load_manifest(manifest, bind_runtime_task=True)
    layout = runner.STATE.create_layout(config, run_id="parent-followup-0001")
    layout.run_dir.mkdir(parents=True)
    layout.browser_temp_path.mkdir()
    (layout.browser_temp_path / "profile").mkdir()
    layout.output_path.write_text("answer\nTASK_OUTCOME: EXECUTED\n", encoding="utf-8")
    layout.transcript_path.write_text("terminal transcript", encoding="utf-8")
    Path(str(layout.run_dir / "mission.md")).write_bytes(mission.read_bytes())
    runner.STATE.write_json_atomic(
        layout.state_path,
        runner.STATE.state_payload(config, layout, status="prepared", resolved_version="oracle 0.17.1", cdp_port=43101),
    )
    runner.STATE.persist_ownership_receipt(layout.state_path, oracle_process_pid=100)
    session_root = tmp_path / "sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))
    url = "https://chatgpt.com/c/exact-parent-conversation"
    meta = {"browser": {"runtime": {
        "chromePid": 101, "controllerPid": 100, "chromePort": 43101,
        "userDataDir": str(layout.browser_temp_path / "profile"), "chromeTargetId": "exact-target", "tabUrl": url,
    }}}
    meta_path = session_root / layout.slug / "meta.json"
    meta_path.parent.mkdir(parents=True)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert runner.STATE.capture_browser_identity_receipt(layout.state_path) is not None
    meta["browser"]["runtime"]["promptSubmitted"] = True
    meta["browser"]["archive"] = {
        "mode": "auto", "attempted": True, "archived": True, "conversationUrl": url,
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    runner.STATE.update_state(
        layout.state_path, status="complete", session_authority="terminal", terminal_harvested=True,
        transport_status="complete", task_outcome="executed", artifact_sha256=hashlib.sha256(layout.output_path.read_bytes()).hexdigest(),
    )
    return runner, layout, followup


def test_browser_identity_port_mismatch_is_persisted_and_never_becomes_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, _ = make_parent(tmp_path, monkeypatch)
    state = runner.STATE.load_state(parent.state_path)
    receipt_path = Path(state["browser_identity"]["receipt_path"])
    assert receipt_path.is_file()
    meta_path = Path(os.environ["ORACLE_SESSION_ROOT"]) / parent.slug / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["browser"]["runtime"]["chromePort"] = 43102
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert runner.STATE.capture_browser_identity_receipt(parent.state_path) is None
    observed = runner.STATE.load_state(parent.state_path)["browser_identity"]
    assert observed["receipt_path"] == str(receipt_path)
    assert runner.STATE.proven_browser_identity_receipt(parent.state_path) is None
    assert observed["port_mismatch"] == {
        "schema": "codex.chatgpt.oracle-browser-port-mismatch/v1",
        "expected_cdp_port": 43101,
        "observed_cdp_port": 43102,
        "oracle_meta_path": str(meta_path),
        "conversation_url_candidate": "https://chatgpt.com/c/exact-parent-conversation",
        "target_id_candidate": "exact-target",
    }


def test_followup_child_executes_with_durable_v1_watchdog_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    original_execute = runner.execute_run
    captured: dict = {}

    class ChildProcess:
        pid = 22334

        @staticmethod
        def wait(timeout=None):
            return 0

    def child_popen(command, **kwargs):
        captured["command"] = list(command)
        captured["env"] = dict(kwargs["env"])
        output_path = Path(command[command.index("--write-output") + 1])
        child_state = runner.STATE.load_state(output_path.parent / "state.json")
        slug = command[command.index("--slug") + 1]
        port = int(command[command.index("--browser-port") + 1])
        profile = Path(child_state["artifacts"]["browser_temp"]) / "child-profile"
        profile.mkdir(parents=True)
        meta_path = Path(os.environ["ORACLE_SESSION_ROOT"]) / slug / "meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps({
            "browser": {
                "runtime": {
                    "chromePid": 22335,
                    "controllerPid": 22334,
                    "chromePort": port,
                    "userDataDir": str(profile),
                    "chromeTargetId": "followup-child-target",
                    "tabUrl": "https://chatgpt.com/c/exact-parent-conversation",
                    "promptSubmitted": True,
                },
                "archive": {
                    "mode": "always",
                    "attempted": True,
                    "archived": True,
                    "conversationUrl": "https://chatgpt.com/c/exact-parent-conversation",
                },
            }
        }), encoding="utf-8")
        output_path.write_text("child answer\nTASK_OUTCOME: EXECUTED\n", encoding="utf-8")
        kwargs["stdout"].write(b"child stdout\n")
        kwargs["stdout"].flush()
        return ChildProcess()

    def execute_child(manifest_path, **kwargs):
        return original_execute(
            manifest_path,
            **kwargs,
            run_factory=lambda command, **_kwargs: subprocess.CompletedProcess(
                command, 0, stdout="oracle 0.17.1\n", stderr=""
            ),
            popen_factory=child_popen,
            compat_factory=lambda version: {"ok": True, "version": version},
            devspace_compat_factory=lambda: {
                "ok": True, "changed": [], "service_restart_required": False,
            },
            devspace_qualification_factory=lambda root: {
                "qualified": True, "project_root": str(root),
            },
            pro_app_read_gate_factory=lambda root, app_name: {
                "schema": "codex.devspace.pro-app-read-gate/v1",
                "qualified": True,
                "project_root": str(root),
                "app_name": app_name,
            },
        )

    monkeypatch.setenv("ORACLE_TASK_OUTCOME_TERMINAL_CONTRACT", "stale-parent-value")
    monkeypatch.setenv("ORACLE_TERMINAL_MARKER_CONFIRM_CYCLES", "1")
    monkeypatch.setenv("ORACLE_TERMINAL_MARKER_MIN_STABLE_MS", "1")
    monkeypatch.setattr(runner, "execute_run", execute_child)

    result = runner.followup_run(
        parent.run_dir,
        mission_path=mission,
        round_key="watchdog-contract",
        run_id="followup-watchdog-contract-0001",
    )

    child_state = runner.STATE.load_state(Path(result["run_dir"]) / "state.json")
    assert captured["env"]["ORACLE_TASK_OUTCOME_TERMINAL_CONTRACT"] == "v1"
    assert "ORACLE_TERMINAL_MARKER_CONFIRM_CYCLES" not in captured["env"]
    assert "ORACLE_TERMINAL_MARKER_MIN_STABLE_MS" not in captured["env"]
    assert child_state["terminal_watchdog"] == {
        "schema": "codex.chatgpt.oracle-terminal-watchdog/v1",
        "contract": "v1",
        "environment_enabled": True,
    }


def make_saved_terminal_output_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    child_run_id = "followup-saved-terminal-output-a1b2c3d4"
    plan = runner.followup_run(
        parent.run_dir,
        mission_path=mission,
        round_key="saved-terminal-output",
        run_id=child_run_id,
        dry_run=True,
    )
    reservation_path = Path(plan["round_receipt_path"])
    reservation_path.parent.mkdir(parents=True)
    reservation_path.write_text(
        json.dumps(plan["round_receipt_plan"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    reservation_sha256 = hashlib.sha256(reservation_path.read_bytes()).hexdigest()
    parent_state = runner.STATE.load_state(parent.state_path)
    manifest_payload = runner._followup_manifest_payload(
        parent_state,
        mission_path=mission,
        run_id=child_run_id,
        archive_contract=plan["round_receipt_plan"]["parent"]["archive_contract"],
    )
    manifest_payload["run_root"] = str(parent.run_dir.parent)
    manifest = tmp_path / "saved-terminal-child.json"
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    config = runner.STATE.load_manifest(manifest, bind_runtime_task=True)
    child = runner.STATE.create_layout(config, run_id=child_run_id)
    child.run_dir.mkdir()
    child.browser_temp_path.mkdir()
    profile = child.browser_temp_path / "profile"
    profile.mkdir()
    (child.run_dir / "mission.md").write_bytes(mission.read_bytes())
    child.output_path.write_text("saved answer\nTASK_OUTCOME: EXECUTED\n", encoding="utf-8")
    child.stdout_path.write_text(
        f"1h42m · gpt-5.6-sol[browser]\nSaved assistant output to {child.output_path}\n",
        encoding="utf-8",
    )
    child.stderr_path.write_bytes(b"")
    state = runner.STATE.state_payload(
        config,
        child,
        status="running",
        resolved_version="oracle 0.17.1",
        cdp_port=plan["round_receipt_plan"]["child"]["expected_cdp_port"],
    )
    state.update({
        "session_authority": "submitted_unknown",
        "transport_status": "prepared",
        "task_outcome": "pending",
        "terminal_harvested": False,
        "browser_observer": {"status": "running", "oracle_process_pid": 45001},
    })
    runner.STATE.write_json_atomic(child.state_path, state)
    binding = {
        "schema": "codex.chatgpt.oracle-followup-binding/v1",
        "source_thread_id": OWNER,
        "round_key": "saved-terminal-output",
        "reservation_path": str(reservation_path),
        "reservation_sha256": reservation_sha256,
        "parent": plan["round_receipt_plan"]["parent"],
        "child": plan["round_receipt_plan"]["child"],
        "conversation_url": plan["parent_conversation_url"],
    }
    runner.STATE.persist_followup_binding(child.state_path, binding)
    runner.STATE.persist_ownership_receipt(child.state_path, oracle_process_pid=45001)
    meta_path = Path(os.environ["ORACLE_SESSION_ROOT"]) / child.slug / "meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    conversation_url = plan["parent_conversation_url"]
    meta_path.write_text(json.dumps({
        "status": "completed",
        "completedAt": "2026-08-24T09:04:40.25Z",
        "error": None,
        "browser": {
            "runtime": {
                "chromePid": 45003,
                "controllerPid": 45002,
                "chromePort": 43122,
                "userDataDir": str(profile),
                "chromeTargetId": "saved-terminal-target",
                "tabUrl": conversation_url,
                "conversationId": conversation_url.rsplit("/", 1)[-1],
                "promptSubmitted": True,
            },
            "archive": {
                "mode": "never",
                "attempted": False,
                "archived": False,
                "conversationUrl": conversation_url,
            },
        },
    }), encoding="utf-8")
    hashes = {
        "expected_state_sha256": runner.STATE.sha256_file(child.state_path),
        "expected_output_sha256": runner.STATE.sha256_file(child.output_path),
        "expected_stdout_sha256": runner.STATE.sha256_file(child.stdout_path),
        "expected_oracle_meta_sha256": runner.STATE.sha256_file(meta_path),
    }
    return runner, parent, child, meta_path, hashes


def test_saved_terminal_output_reconciliation_is_owner_bound_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, child, _meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    before = child.state_path.read_bytes()
    preview = runner.settle_saved_terminal_output(
        child.run_dir, dry_run=True, process_alive=lambda _pid: False, **hashes
    )
    assert preview["status"] == "dry-run"
    assert child.state_path.read_bytes() == before
    assert not Path(preview["settlement_path"]).exists()

    result = runner.settle_saved_terminal_output(
        child.run_dir, process_alive=lambda _pid: False, **hashes
    )
    assert result["task_outcome"] == "executed"
    settled = runner.STATE.load_state(child.state_path)
    assert settled["status"] == "complete"
    assert settled["session_authority"] == "terminal"
    assert settled["terminal_harvested"] is True
    assert settled["transport_status"] == "complete"
    assert settled["oracle"]["conversation_url"] == "https://chatgpt.com/c/exact-parent-conversation"
    assert runner.STATE.unresolved_project_sessions(
        child.run_dir.parent,
        Path(settled["project_root"]),
        source_thread_id=OWNER,
    ) == []
    settlement_sha256 = result["settlement_sha256"]
    assert result["browser_identity"]["status"] == "saved_output_browser_identity_sealed"
    proven = runner.STATE.proven_browser_identity_receipt(child.state_path)
    assert proven is not None
    assert proven["payload"]["schema"] == "codex.chatgpt.oracle-browser-identity-receipt/v2"
    assert proven["payload"]["expected_cdp_port"] != proven["payload"]["observed_cdp_port"]
    next_mission = Path(settled["project_root"]) / "round-after-reconciliation.md"
    next_mission.write_text("verify saved output reconciliation", encoding="utf-8")
    next_round = runner.followup_run(
        child.run_dir,
        mission_path=next_mission,
        round_key="after-saved-output-reconciliation",
        dry_run=True,
    )
    assert next_round["ok"] is True
    sealed_again = runner.seal_saved_output_browser_identity(
        child.run_dir,
        expected_settlement_sha256=settlement_sha256,
        process_alive=lambda _pid: False,
    )
    assert sealed_again["status"] == "saved_output_browser_identity_already_sealed"
    with pytest.raises(runner.OracleRunError) as wrong_hash:
        runner.seal_saved_output_browser_identity(
            child.run_dir,
            expected_settlement_sha256="f" * 64,
            process_alive=lambda _pid: False,
        )
    assert wrong_hash.value.code == "SAVED_IDENTITY_SETTLEMENT_INVALID"
    repeated = runner.settle_saved_terminal_output(
        child.run_dir, process_alive=lambda _pid: False, **hashes
    )
    assert repeated["status"] == "saved_terminal_output_already_reconciled"


def test_saved_output_browser_identity_seals_a_pre_v1196_terminal_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    result = runner.settle_saved_terminal_output(
        child.run_dir, process_alive=lambda _pid: False, **hashes
    )
    receipt_path = child.run_dir / "browser-identity-receipt.json"
    receipt_path.unlink()
    state = runner.STATE.load_state(child.state_path)
    state["browser_identity"] = {
        **state["browser_identity"],
        "receipt_path": None,
        "receipt_sha256": None,
    }
    state["browser_identity"].pop("observed_cdp_port", None)
    state["browser_identity"].pop("authority", None)
    runner.STATE.write_json_atomic(child.state_path, state)

    preview = runner.seal_saved_output_browser_identity(
        child.run_dir,
        expected_settlement_sha256=result["settlement_sha256"],
        dry_run=True,
        process_alive=lambda _pid: False,
    )
    assert preview["status"] == "dry-run"
    assert not receipt_path.exists()
    sealed = runner.seal_saved_output_browser_identity(
        child.run_dir,
        expected_settlement_sha256=result["settlement_sha256"],
        process_alive=lambda _pid: False,
    )
    assert sealed["status"] == "saved_output_browser_identity_sealed"
    assert runner.STATE.proven_browser_identity_receipt(child.state_path) is not None


def test_saved_output_browser_identity_seal_rejects_foreign_live_and_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    settled = runner.settle_saved_terminal_output(
        child.run_dir, process_alive=lambda _pid: False, **hashes
    )
    settlement_sha256 = settled["settlement_sha256"]
    (child.run_dir / "browser-identity-receipt.json").unlink()
    state = runner.STATE.load_state(child.state_path)
    state["browser_identity"] = {
        **state["browser_identity"],
        "receipt_path": None,
        "receipt_sha256": None,
    }
    state["browser_identity"].pop("observed_cdp_port", None)
    state["browser_identity"].pop("authority", None)
    runner.STATE.write_json_atomic(child.state_path, state)
    monkeypatch.setenv("CODEX_THREAD_ID", FOREIGN)
    with pytest.raises(runner.OracleRunError) as foreign:
        runner.seal_saved_output_browser_identity(
            child.run_dir,
            expected_settlement_sha256=settlement_sha256,
            process_alive=lambda _pid: False,
        )
    assert foreign.value.code == "FOREIGN_TASK_SESSION"
    monkeypatch.setenv("CODEX_THREAD_ID", OWNER)
    with pytest.raises(runner.OracleRunError) as live:
        runner.seal_saved_output_browser_identity(
            child.run_dir,
            expected_settlement_sha256=settlement_sha256,
            process_alive=lambda pid: pid == 45003,
        )
    assert live.value.code == "SAVED_IDENTITY_PROCESS_ACTIVE"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["browser"]["runtime"]["chromePort"] = 43123
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(runner.OracleRunError) as drift:
        runner.seal_saved_output_browser_identity(
            child.run_dir,
            expected_settlement_sha256=settlement_sha256,
            process_alive=lambda _pid: False,
        )
    assert drift.value.code == "SAVED_IDENTITY_ARTIFACT_DRIFT"


def test_saved_output_browser_identity_v2_fails_closed_after_settlement_or_state_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    settled = runner.settle_saved_terminal_output(
        child.run_dir, process_alive=lambda _pid: False, **hashes
    )
    runner.seal_saved_output_browser_identity(
        child.run_dir,
        expected_settlement_sha256=settled["settlement_sha256"],
        process_alive=lambda _pid: False,
    )
    assert runner.STATE.proven_browser_identity_receipt(child.state_path) is not None
    state = runner.STATE.load_state(child.state_path)
    state["browser_identity"]["expected_cdp_port"] += 1
    runner.STATE.write_json_atomic(child.state_path, state)
    assert runner.STATE.proven_browser_identity_receipt(child.state_path) is None


def test_saved_output_browser_identity_v2_rejects_post_seal_meta_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    settled = runner.settle_saved_terminal_output(
        child.run_dir, process_alive=lambda _pid: False, **hashes
    )
    assert settled["browser_identity"]["status"] == "saved_output_browser_identity_sealed"
    original = meta_path.read_bytes()
    preserved = tmp_path / "preserved-meta.json"
    preserved.write_bytes(original)
    meta_path.unlink()
    try:
        meta_path.symlink_to(preserved)
    except OSError as exc:
        meta_path.write_bytes(original)
        pytest.skip(f"file symlinks unavailable: {exc}")
    assert runner.STATE.proven_browser_identity_receipt(child.state_path) is None


def test_saved_output_browser_identity_v2_rejects_post_seal_receipt_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    settled = runner.settle_saved_terminal_output(
        child.run_dir, process_alive=lambda _pid: False, **hashes
    )
    assert settled["browser_identity"]["status"] == "saved_output_browser_identity_sealed"
    receipt_path = child.run_dir / "browser-identity-receipt.json"
    original = receipt_path.read_bytes()
    preserved = tmp_path / "preserved-browser-identity-receipt.json"
    preserved.write_bytes(original)
    receipt_path.unlink()
    try:
        receipt_path.symlink_to(preserved)
    except OSError as exc:
        receipt_path.write_bytes(original)
        pytest.skip(f"file symlinks unavailable: {exc}")
    assert runner.STATE.proven_browser_identity_receipt(child.state_path) is None


def test_saved_output_browser_identity_v2_rejects_duplicate_receipt_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    settled = runner.settle_saved_terminal_output(
        child.run_dir, process_alive=lambda _pid: False, **hashes
    )
    assert settled["browser_identity"]["status"] == "saved_output_browser_identity_sealed"
    receipt_path = child.run_dir / "browser-identity-receipt.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    duplicate = encoded.replace(
        "{",
        '{\n  "schema": "codex.chatgpt.oracle-browser-identity-receipt/v2",',
        1,
    ) + "\n"
    receipt_path.write_text(duplicate, encoding="utf-8")
    state = runner.STATE.load_state(child.state_path)
    state["browser_identity"]["receipt_sha256"] = runner.STATE.sha256_file(receipt_path)
    runner.STATE.write_json_atomic(child.state_path, state)
    assert runner.STATE.proven_browser_identity_receipt(child.state_path) is None


def test_saved_terminal_output_reconciliation_rejects_foreign_and_live_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", FOREIGN)
    with pytest.raises(runner.OracleRunError) as foreign:
        runner.settle_saved_terminal_output(
            child.run_dir, process_alive=lambda _pid: False, **hashes
        )
    assert foreign.value.code == "FOREIGN_TASK_SESSION"
    monkeypatch.setenv("CODEX_THREAD_ID", OWNER)
    with pytest.raises(runner.OracleRunError) as active:
        runner.settle_saved_terminal_output(
            child.run_dir, process_alive=lambda pid: pid == 45003, **hashes
        )
    assert active.value.code == "SAVED_OUTPUT_PROCESS_ACTIVE"


def test_saved_terminal_output_reconciliation_rejects_drift_and_conversation_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    child.stdout_path.write_text("Saved assistant output to C:\\elsewhere\\output.md\n", encoding="utf-8")
    changed = {**hashes, "expected_stdout_sha256": runner.STATE.sha256_file(child.stdout_path)}
    with pytest.raises(runner.OracleRunError) as wrong_path:
        runner.settle_saved_terminal_output(
            child.run_dir, process_alive=lambda _pid: False, **changed
        )
    assert wrong_path.value.code == "SAVED_OUTPUT_STDOUT_BINDING_INVALID"

    second = tmp_path / "second"
    second.mkdir()
    runner, _parent, child, meta_path, hashes = make_saved_terminal_output_child(second, monkeypatch)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["browser"]["runtime"]["tabUrl"] = "https://chatgpt.com/c/foreign-conversation"
    meta["browser"]["runtime"]["conversationId"] = "foreign-conversation"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    changed = {**hashes, "expected_oracle_meta_sha256": runner.STATE.sha256_file(meta_path)}
    with pytest.raises(runner.OracleRunError) as mismatch:
        runner.settle_saved_terminal_output(
            child.run_dir, process_alive=lambda _pid: False, **changed
        )
    assert mismatch.value.code == "SAVED_OUTPUT_ORACLE_META_INVALID"


def test_saved_terminal_output_reconciliation_resumes_after_receipt_only_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    preview = runner.settle_saved_terminal_output(
        child.run_dir, dry_run=True, process_alive=lambda _pid: False, **hashes
    )
    receipt_path = Path(preview["settlement_path"])
    receipt_path.parent.mkdir()
    receipt_path.write_text(
        json.dumps(preview["settlement_payload"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result = runner.settle_saved_terminal_output(
        child.run_dir, process_alive=lambda _pid: False, **hashes
    )
    assert result["status"] == "saved_terminal_output_reconciled"
    assert runner.STATE.load_state(child.state_path)["terminal_harvested"] is True


def test_saved_terminal_output_reconciliation_rechecks_idempotent_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    runner.settle_saved_terminal_output(
        child.run_dir, process_alive=lambda _pid: False, **hashes
    )
    child.output_path.write_text("tampered\nTASK_OUTCOME: EXECUTED\n", encoding="utf-8")
    with pytest.raises(runner.OracleRunError) as drift:
        runner.settle_saved_terminal_output(
            child.run_dir, process_alive=lambda _pid: False, **hashes
        )
    assert drift.value.code == "SAVED_OUTPUT_SETTLEMENT_ARTIFACT_DRIFT"


def test_saved_terminal_output_reconciliation_rejects_external_browser_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    external = tmp_path / "foreign-browser-temp"
    profile = external / "profile"
    profile.mkdir(parents=True)
    state = runner.STATE.load_state(child.state_path)
    state["artifacts"]["browser_temp"] = str(external)
    runner.STATE.write_json_atomic(child.state_path, state)
    ownership_path = child.run_dir / "ownership-receipt.json"
    ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
    ownership["browser_temp"] = str(external)
    ownership_path.write_text(json.dumps(ownership, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["browser"]["runtime"]["userDataDir"] = str(profile)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    changed = {
        **hashes,
        "expected_state_sha256": runner.STATE.sha256_file(child.state_path),
        "expected_oracle_meta_sha256": runner.STATE.sha256_file(meta_path),
    }
    with pytest.raises(runner.OracleRunError) as outside:
        runner.settle_saved_terminal_output(
            child.run_dir, process_alive=lambda _pid: False, **changed
        )
    assert outside.value.code == "SAVED_OUTPUT_ORACLE_META_INVALID"


def test_saved_terminal_output_reconciliation_rejects_browser_temp_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    alias = tmp_path / "browser-temp-alias"
    try:
        alias.symlink_to(child.browser_temp_path, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    state = runner.STATE.load_state(child.state_path)
    state["artifacts"]["browser_temp"] = str(alias)
    runner.STATE.write_json_atomic(child.state_path, state)
    ownership_path = child.run_dir / "ownership-receipt.json"
    ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
    ownership["browser_temp"] = str(alias)
    ownership_path.write_text(json.dumps(ownership, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    changed = {**hashes, "expected_state_sha256": runner.STATE.sha256_file(child.state_path)}
    with pytest.raises(runner.OracleRunError) as linked:
        runner.settle_saved_terminal_output(
            child.run_dir, process_alive=lambda _pid: False, **changed
        )
    assert linked.value.code == "SAVED_OUTPUT_ORACLE_META_INVALID"


def test_saved_terminal_output_reconciliation_rejects_ambiguous_output_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path, hashes = make_saved_terminal_output_child(tmp_path, monkeypatch)
    child.output_path.write_text(
        "saved answer\nTASK_OUTCOME: EXECUTED\nordinary trailing prose\n",
        encoding="utf-8",
    )
    changed = {**hashes, "expected_output_sha256": runner.STATE.sha256_file(child.output_path)}
    with pytest.raises(runner.OracleRunError) as ambiguous:
        runner.settle_saved_terminal_output(
            child.run_dir, process_alive=lambda _pid: False, **changed
        )
    assert ambiguous.value.code == "SAVED_OUTPUT_TASK_OUTCOME_INVALID"

    second = tmp_path / "stderr"
    second.mkdir()
    runner, _parent, child, _meta_path, hashes = make_saved_terminal_output_child(second, monkeypatch)
    child.stderr_path.write_text("late error\n", encoding="utf-8")
    with pytest.raises(runner.OracleRunError) as stderr:
        runner.settle_saved_terminal_output(
            child.run_dir, process_alive=lambda _pid: False, **hashes
        )
    assert stderr.value.code == "SAVED_OUTPUT_ARTIFACT_INVALID"


def make_archived_parent_unarchive_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    structured: bool,
):
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    child_run_id = "followup-d5b70a899fe24da4884c0abea21efaf6"
    plan = runner.followup_run(
        parent.run_dir,
        mission_path=mission,
        round_key="round5-costs",
        run_id=child_run_id,
        dry_run=True,
    )
    reservation_path = Path(plan["round_receipt_path"])
    reservation_path.parent.mkdir(parents=True)
    reservation_path.write_text(
        json.dumps(plan["round_receipt_plan"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    reservation_sha256 = hashlib.sha256(reservation_path.read_bytes()).hexdigest()
    parent_state = runner.STATE.load_state(parent.state_path)
    archive_contract = plan["round_receipt_plan"]["parent"]["archive_contract"]
    payload = runner._followup_manifest_payload(
        parent_state,
        mission_path=mission,
        run_id=child_run_id,
        archive_contract=archive_contract,
    )
    payload["run_root"] = str(parent.run_dir.parent)
    manifest_path = tmp_path / "archived-child.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    config = runner.STATE.load_manifest(manifest_path, bind_runtime_task=True)
    child = runner.STATE.create_layout(config, run_id=child_run_id)
    child.run_dir.mkdir()
    (child.run_dir / "mission.md").write_bytes(mission.read_bytes())
    marker = "FOLLOWUP_ARCHIVED_PARENT_UNARCHIVE_FAILED: unarchive-menu-not-found"
    stdout = f"ERROR: {marker}\nUser error (browser-automation): {marker}\n"
    child.stdout_path.write_text(stdout, encoding="utf-8")
    child.stderr_path.write_bytes(b"")
    child.transcript_path.write_text(stdout, encoding="utf-8")
    state = runner.STATE.state_payload(
        config,
        child,
        status="attention_required",
        resolved_version="oracle 0.17.1",
        exit_code=1,
        cdp_port=plan["round_receipt_plan"]["child"]["expected_cdp_port"],
    )
    state.update({
        "session_authority": "submitted_unknown",
        "transport_status": "failed",
        "task_outcome": "pending",
        "terminal_harvested": False,
    })
    runner.STATE.write_json_atomic(child.state_path, state)
    binding = {
        "schema": "codex.chatgpt.oracle-followup-binding/v1",
        "source_thread_id": OWNER,
        "round_key": "round5-costs",
        "reservation_path": str(reservation_path),
        "reservation_sha256": reservation_sha256,
        "parent": plan["round_receipt_plan"]["parent"],
        "child": plan["round_receipt_plan"]["child"],
        "conversation_url": plan["parent_conversation_url"],
    }
    runner.STATE.persist_followup_binding(child.state_path, binding)
    runner.STATE.persist_ownership_receipt(child.state_path, oracle_process_pid=100)
    ownership_created_at = json.loads(
        (child.run_dir / "ownership-receipt.json").read_text(encoding="utf-8")
    )["created_at"]
    parent_url = plan["parent_conversation_url"]
    details = {"stage": "execute-browser"}
    if structured:
        details = {
            "stage": "followup-unarchive-before-composer",
            "code": "FOLLOWUP_ARCHIVED_PARENT_UNARCHIVE_FAILED",
            "reason": "unarchive-menu-not-found",
            "expectedConversationUrl": parent_url,
            "observedConversationUrl": parent_url,
            "promptSubmitted": False,
            "composerSubmitAttempted": False,
            "turnCountBefore": 5,
            "turnCountAfter": 5,
        }
    meta_path = Path(os.environ["ORACLE_SESSION_ROOT"]) / child.slug / "meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    browser_config = {
        "resumeConversationUrl": parent_url,
        "resumeArchivedConversation": True,
        "archiveConversations": "always",
    }
    meta_path.write_text(json.dumps({
        "id": child.slug,
        "status": "error",
        "completedAt": ownership_created_at,
        "mode": "browser",
        "model": "gpt-5.6-sol",
        "browser": {"config": browser_config},
        "options": {"browserConfig": browser_config},
        "error": {
            "category": "browser-automation",
            "message": marker,
            "details": details,
        },
    }), encoding="utf-8")
    if not structured:
        receipt_root = tmp_path / "install-receipts"
        receipt_root.mkdir()
        receipt_path = receipt_root / "codexpro-automation-v1184.json"
        receipt_path.write_text(json.dumps({
            "schema": "codexpro.install-receipt/v3",
            "manifest_version": "1.18.4",
            "installed_at": "2026-08-23T00:30:00Z",
            "files": [
                {"path": name, "installed_sha256": sha256}
                for name, sha256 in runner.STATE.LEGACY_V1184_FOLLOWUP_MANAGED_HASHES.items()
            ],
        }), encoding="utf-8")
        prove = runner.STATE._proven_legacy_v1184_followup_install_receipt
        monkeypatch.setattr(
            runner.STATE,
            "_proven_legacy_v1184_followup_install_receipt",
            lambda completed_at: prove(completed_at, receipt_root=receipt_root),
        )
    return runner, parent, child, meta_path


def write_exact_harmless_archived_parent_harvest(child, runner) -> None:
    """Write the only legacy recovery pair that remains harmless for this child.

    A follow-up keeps a *parent* conversation URL by design.  This pair must
    therefore prove only that the child has no live tab and no recoverable
    child URL; it must never turn a recovery of the parent into child evidence.
    """
    slug = runner.STATE.load_state(child.state_path)["oracle"]["slug"]
    (child.run_dir / "recovery-harvest-stdout.log").write_text(
        f'No live ChatGPT tab matched session "{slug}". '
        "Attempting recovery by reopening the saved conversation URL.\n",
        encoding="utf-8",
    )
    (child.run_dir / "recovery-harvest-stderr.log").write_text(
        "Cannot recover conversation: session metadata has no recoverable "
        "ChatGPT conversation URL (expected browser.harvest.url or "
        "browser.runtime.tabUrl to be a chatgpt.com/c/<id> URL).\n",
        encoding="utf-8",
    )


def make_followup_textarea_absent_with_harvest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a current v2 textarea child that can stand in for a v1 receipt."""
    runner, parent, child, meta_path = make_archived_parent_unarchive_failure(
        tmp_path, monkeypatch, structured=False
    )
    marker = runner.STATE.ORACLE_PROMPT_TEXTAREA_ABSENT_MARKER
    stdout = f"ERROR: {marker}\nUser error (browser-automation): {marker}\n"
    child.stdout_path.write_text(stdout, encoding="utf-8")
    child.transcript_path.write_text(stdout, encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["error"] = {
        "category": "browser-automation",
        "message": marker,
        "details": {"stage": "execute-browser"},
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    write_exact_harmless_archived_parent_harvest(child, runner)
    return runner, parent, child


def make_structured_resume_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner, parent, child, meta_path = make_archived_parent_unarchive_failure(
        tmp_path, monkeypatch, structured=True
    )
    monkeypatch.setattr(runner.STATE, "_process_may_be_alive", lambda _pid: False)
    marker = (
        "A future Oracle wording that is not in a text whitelist but is bound "
        "to structured pre-composer evidence."
    )
    stdout = f"ERROR: {marker}\nUser error (browser-automation): {marker}\n"
    child.stdout_path.write_text(stdout, encoding="utf-8")
    child.transcript_path.write_text(stdout, encoding="utf-8")
    state = runner.STATE.load_state(child.state_path)
    state["exit_code"] = None
    state["task_outcome_reason"] = "followup-conversation-identity-unverified"
    state["browser_observer"] = {
        "status": "process-exited",
        "oracle_process_pid": 43644,
        "timeout_is_terminal": False,
    }
    runner.STATE.write_json_atomic(child.state_path, state)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["browser"].pop("runtime", None)
    meta["error"] = {
        "category": "browser-automation",
        "message": marker,
        "details": {
            "stage": "resume-conversation",
            "priorTurns": 8,
            "settled": False,
        },
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return runner, parent, child, meta_path


def rewrite_settlement_as_official_followup_v1(child, runner, *, mutate=None) -> dict:
    """Simulate an immutable old official v1 receipt, not ad-hoc user tampering."""
    runner.STATE.settle_user_confirmed_no_submission(
        child.state_path,
        confirmation="user-confirmed-no-submission",
        reason="create a synthetic official historical v1 receipt",
    )
    settlement_path = child.run_dir / "user-confirmed-no-submission.json"
    recorded = json.loads(settlement_path.read_text(encoding="utf-8"))
    recorded["settlement_eligibility"] = "oracle-followup-pre-submit-ui/v1"
    for key in ("failure_kind", "evidence_profile", "harvest_outcome"):
        recorded.pop(key, None)
    if mutate is not None:
        mutate(recorded)
    runner.STATE.write_json_atomic(settlement_path, recorded)
    state = runner.STATE.load_state(child.state_path)
    state["user_confirmed_no_submission"]["sha256"] = hashlib.sha256(
        settlement_path.read_bytes()
    ).hexdigest()
    runner.STATE.write_json_atomic(child.state_path, state)
    return recorded


def test_followup_dry_run_is_same_task_and_same_conversation_without_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner, layout, mission = make_parent(tmp_path, monkeypatch)

    result = runner.followup_run(layout.run_dir, mission_path=mission, round_key="round-1", dry_run=True)

    assert result["ok"] is True
    assert result["submitted_question"] is False
    assert result["parent_conversation_url"] == "https://chatgpt.com/c/exact-parent-conversation"
    assert result["argv_plan"][:2] == ["--followup", layout.slug]
    assert not Path(result["manifest_path"]).exists()
    assert not Path(result["round_receipt_path"]).exists()
    assert result["round_receipt_plan"]["parent"]["archive_contract"]["was_archived"] is True
    payload = runner._followup_manifest_payload(
        runner.STATE.load_state(layout.state_path), mission_path=mission,
        run_id="followup-archive-plan-0001",
        archive_contract=result["round_receipt_plan"]["parent"]["archive_contract"],
    )
    assert payload["archive"] == "always"


def test_followup_actual_preflight_failure_has_child_state_logs_and_result_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    original_execute = runner.execute_run

    def execute_child(manifest_path, **kwargs):
        def fail_read_gate(_root, _app_name):
            raise runner.DEVSPACE_PREFLIGHT.DevSpacePreflightError(
                "PRO_DEVSPACE_APP_READ_GATE_REQUIRED",
                "regular app read proof is unavailable",
                {"reason": "test-preflight-failure"},
            )

        return original_execute(
            manifest_path,
            **kwargs,
            pro_app_read_gate_factory=fail_read_gate,
            devspace_qualification_factory=lambda root: {"qualified": True, "project_root": str(root)},
            version_resolver=lambda *_args, **_kwargs: "oracle 0.18.0",
            compat_factory=lambda version: {"ok": True, "version": version},
            devspace_compat_factory=lambda: {
                "ok": True, "changed": [], "service_restart_required": False,
            },
        )

    monkeypatch.setattr(runner, "execute_run", execute_child)
    result = runner.followup_run(
        parent.run_dir,
        mission_path=mission,
        round_key="preflight-evidence",
        run_id="followup-preflight-evidence-0001",
    )

    child = parent.run_dir.parent / "followup-preflight-evidence-0001"
    state = runner.STATE.load_state(child / "state.json")
    assert result["ok"] is False
    assert child.is_dir()
    assert (child / "stdout.log").is_file()
    assert "PRO_DEVSPACE_APP_READ_GATE_REQUIRED" in (child / "stderr.log").read_text(encoding="utf-8")
    assert state["session_authority"] == "pre_submit"
    assert state["status"] == "failed"
    result_receipt = parent.run_dir / "followup-rounds" / "preflight-evidence.result.json"
    assert result_receipt.is_file()
    recorded = json.loads(result_receipt.read_text(encoding="utf-8"))
    assert recorded["conversation_binding"]["identity_status"] == "not-applicable-pre-submit"
    assert recorded["child"]["status"] == "failed"


def test_followup_pre_layout_exception_has_parent_launch_and_failure_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    dry = runner.followup_run(
        parent.run_dir,
        mission_path=mission,
        round_key="prelayout-exception",
        run_id="followup-33333333333333333333333333333333",
        dry_run=True,
    )
    assert not Path(dry["round_receipt_path"]).exists()

    observed: dict[str, str] = {}

    def fail_before_layout(manifest_path, **kwargs):
        observed["manifest_sha256"] = hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()
        observed["expected_manifest_sha256"] = kwargs["_expected_manifest_sha256"]
        raise runner.OracleRunError(
            "TEST_PRELAYOUT_FAILURE",
            "simulated failure before child layout creation",
        )

    monkeypatch.setattr(runner, "execute_run", fail_before_layout)
    with pytest.raises(runner.OracleRunError) as exc:
        runner.followup_run(
            parent.run_dir,
            mission_path=mission,
            round_key="prelayout-exception",
            run_id="followup-33333333333333333333333333333333",
        )
    assert exc.value.code == "TEST_PRELAYOUT_FAILURE"
    assert not (parent.run_dir.parent / "followup-33333333333333333333333333333333").exists()
    launches = list((parent.run_dir / "followup-rounds").glob("prelayout-exception.*.launch.json"))
    failures = list((parent.run_dir / "followup-rounds").glob("prelayout-exception.*.prelaunch-failure.json"))
    assert len(launches) == 1
    assert len(failures) == 1
    launch = json.loads(launches[0].read_text(encoding="utf-8"))
    failure = json.loads(failures[0].read_text(encoding="utf-8"))
    assert launch["submission_action"] == "not-reached-at-receipt"
    assert failure["submission_action"] == "submitted_unknown"
    assert failure["error"]["code"] == "TEST_PRELAYOUT_FAILURE"
    assert failure["launch_receipt_sha256"] == hashlib.sha256(launches[0].read_bytes()).hexdigest()
    assert observed["manifest_sha256"] == observed["expected_manifest_sha256"]


def test_followup_manifest_swap_after_reservation_fails_before_child_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    original_execute = runner.execute_run

    def swap_manifest(manifest_path, **kwargs):
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        payload["app_name"] = "foreign-app"
        Path(manifest_path).write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return original_execute(manifest_path, **kwargs)

    monkeypatch.setattr(runner, "execute_run", swap_manifest)
    with pytest.raises(runner.OracleRunError) as exc:
        runner.followup_run(
            parent.run_dir,
            mission_path=mission,
            round_key="manifest-swap",
            run_id="followup-manifest-swap-0001",
        )
    assert exc.value.code == "FOLLOWUP_MANIFEST_CHANGED_BEFORE_PREPARE"
    assert not (parent.run_dir.parent / "followup-manifest-swap-0001").exists()
    failures = list((parent.run_dir / "followup-rounds").glob("manifest-swap.*.prelaunch-failure.json"))
    assert len(failures) == 1
    failure = json.loads(failures[0].read_text(encoding="utf-8"))
    assert failure["submission_action"] == "none"
    assert failure["error"]["code"] == "FOLLOWUP_MANIFEST_CHANGED_BEFORE_PREPARE"


def test_followup_mission_swap_after_reservation_fails_before_child_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    original_execute = runner.execute_run

    def swap_mission(manifest_path, **kwargs):
        mission.write_text("changed after reservation\n", encoding="utf-8")
        return original_execute(manifest_path, **kwargs)

    monkeypatch.setattr(runner, "execute_run", swap_mission)
    with pytest.raises(runner.OracleRunError) as exc:
        runner.followup_run(
            parent.run_dir,
            mission_path=mission,
            round_key="mission-swap",
            run_id="followup-mission-swap-0001",
        )
    assert exc.value.code == "FOLLOWUP_MISSION_CHANGED_BEFORE_PREPARE"
    assert not (parent.run_dir.parent / "followup-mission-swap-0001").exists()
    failures = list((parent.run_dir / "followup-rounds").glob("mission-swap.*.prelaunch-failure.json"))
    assert len(failures) == 1
    failure = json.loads(failures[0].read_text(encoding="utf-8"))
    assert failure["submission_action"] == "none"
    assert failure["error"]["code"] == "FOLLOWUP_MISSION_CHANGED_BEFORE_PREPARE"


def test_followup_reservation_write_failure_cleans_only_own_unreferenced_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    original_write = runner._write_followup_round_receipt

    def fail_reservation(path, payload, **kwargs):
        if path.name == "reservation-write-failure.json":
            raise runner.OracleRunError(
                "TEST_RESERVATION_WRITE_FAILED",
                "simulated reservation write failure",
            )
        return original_write(path, payload, **kwargs)

    monkeypatch.setattr(runner, "_write_followup_round_receipt", fail_reservation)
    with pytest.raises(runner.OracleRunError) as exc:
        runner.followup_run(
            parent.run_dir,
            mission_path=mission,
            round_key="reservation-write-failure",
            run_id="followup-reservation-write-failure-0001",
        )
    assert exc.value.code == "TEST_RESERVATION_WRITE_FAILED"
    assert list((parent.run_dir / "followup-manifests").iterdir()) == []
    assert list((parent.run_dir / "followup-rounds").iterdir()) == []
    assert not (parent.run_dir.parent / "followup-reservation-write-failure-0001").exists()


def test_followup_unknown_exception_without_child_state_stays_submitted_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)

    def ambiguous_failure(_manifest_path, **_kwargs):
        raise RuntimeError("controller disappeared at an unknown boundary")

    monkeypatch.setattr(runner, "execute_run", ambiguous_failure)
    with pytest.raises(RuntimeError):
        runner.followup_run(
            parent.run_dir,
            mission_path=mission,
            round_key="ambiguous-no-child-state",
            run_id="followup-ambiguous-no-child-state-0001",
        )
    failures = list(
        (parent.run_dir / "followup-rounds").glob(
            "ambiguous-no-child-state.*.prelaunch-failure.json"
        )
    )
    assert len(failures) == 1
    failure = json.loads(failures[0].read_text(encoding="utf-8"))
    assert failure["submission_action"] == "submitted_unknown"
    assert failure["error"]["code"] == "ORACLE_RUN_FAILED"


def test_followup_manifest_config_is_parsed_from_the_verified_byte_buffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    original_execute = runner.execute_run
    original_load = runner.STATE.load_manifest
    observed: dict[str, str] = {}

    def execute_with_parse_race(manifest_path, **kwargs):
        original_bytes = Path(manifest_path).read_bytes()

        def racing_load(path, **load_kwargs):
            payload = json.loads(original_bytes.decode("utf-8"))
            payload["app_name"] = "foreign-app"
            Path(path).write_text(json.dumps(payload) + "\n", encoding="utf-8")
            try:
                config = original_load(path, **load_kwargs)
                observed["app_name"] = str(config.app_name)
                return config
            finally:
                Path(path).write_bytes(original_bytes)

        monkeypatch.setattr(runner.STATE, "load_manifest", racing_load)
        try:
            def record_gate(_root, app):
                observed["gate_app"] = app
                return {"ok": True, "app_name": app}

            return original_execute(
                manifest_path,
                **kwargs,
                pro_app_read_gate_factory=record_gate,
                devspace_qualification_factory=lambda root: {"qualified": True, "project_root": str(root)},
                version_resolver=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    runner.OracleRunError("TEST_STOP_BEFORE_SUBMIT", "stop after parsing")
                ),
            )
        finally:
            monkeypatch.setattr(runner.STATE, "load_manifest", original_load)

    monkeypatch.setattr(runner, "execute_run", execute_with_parse_race)
    result = runner.followup_run(
        parent.run_dir,
        mission_path=mission,
        round_key="manifest-parse-race",
        run_id="followup-manifest-parse-race-0001",
    )
    assert result["ok"] is False
    assert observed == {"app_name": "codex", "gate_app": "codex"}


@pytest.mark.parametrize("artifact_name", ["followup-rounds", "followup-manifests"])
def test_followup_rejects_symlinked_parent_artifact_directory(
    artifact_name: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    external = tmp_path / "external-rounds"
    external.mkdir()
    link = parent.run_dir / artifact_name
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(runner.OracleRunError) as caught:
        runner.followup_run(
            parent.run_dir,
            mission_path=mission,
            round_key="symlink-escape",
            run_id="followup-symlink-escape-0001",
        )
    assert caught.value.code == "FOLLOWUP_ARTIFACT_DIRECTORY_INVALID"
    assert list(external.iterdir()) == []


def test_followup_same_round_concurrency_creates_one_reservation_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "execute_run", lambda *_args, **_kwargs: {"ok": False})

    def invoke() -> str:
        try:
            runner.followup_run(parent.run_dir, mission_path=mission, round_key="same-key-race")
            return "winner"
        except runner.OracleRunError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: invoke(), range(2)))

    assert sorted(outcomes) == ["FOLLOWUP_ROUND_DUPLICATE", "winner"]
    assert len(list((parent.run_dir / "followup-rounds").glob("same-key-race.json"))) == 1
    assert len(list((parent.run_dir / "followup-manifests").glob("*.json"))) == 1


def test_followup_different_round_keys_share_one_parent_controller_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, parent, mission = make_parent(tmp_path, monkeypatch)
    entered_first = threading.Event()
    release_first = threading.Event()
    entered: list[str] = []

    def blocked_execute(manifest_path, **_kwargs):
        entered.append(Path(manifest_path).name)
        if len(entered) == 1:
            entered_first.set()
            assert release_first.wait(timeout=10)
        return {"ok": False}

    monkeypatch.setattr(runner, "execute_run", blocked_execute)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            runner.followup_run,
            parent.run_dir,
            mission_path=mission,
            round_key="different-key-one",
        )
        assert entered_first.wait(timeout=10)
        second = pool.submit(
            runner.followup_run,
            parent.run_dir,
            mission_path=mission,
            round_key="different-key-two",
        )
        assert not second.done()
        assert len(entered) == 1
        release_first.set()
        assert first.result(timeout=10)["ok"] is False
        assert second.result(timeout=10)["ok"] is False

    assert len(entered) == 2
    reservations = [
        path
        for path in (parent.run_dir / "followup-rounds").glob("different-key-*.json")
        if ".launch." not in path.name
    ]
    assert len(reservations) == 2


def test_followup_archived_parent_url_mismatch_fails_before_child_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, layout, mission = make_parent(tmp_path, monkeypatch)
    meta_path = Path(os.environ["ORACLE_SESSION_ROOT"]) / layout.slug / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["browser"]["archive"]["conversationUrl"] = "https://chatgpt.com/c/other-conversation"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(runner.OracleRunError) as exc:
        runner.followup_run(layout.run_dir, mission_path=mission, round_key="archive-mismatch", dry_run=True)

    assert exc.value.code == "FOLLOWUP_PARENT_ARCHIVE_IDENTITY_INVALID"
    assert not (layout.run_dir / "followup-rounds").exists()


def test_followup_child_binding_is_append_only_and_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner, layout, mission = make_parent(tmp_path, monkeypatch)
    plan = runner.followup_run(
        layout.run_dir, mission_path=mission, round_key="bound-round",
        run_id="followup-bound-child-0001", dry_run=True,
    )
    reservation_path = Path(plan["round_receipt_path"])
    reservation_path.parent.mkdir(parents=True)
    reservation_path.write_text(
        json.dumps(plan["round_receipt_plan"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    reservation_sha256 = hashlib.sha256(reservation_path.read_bytes()).hexdigest()
    parent = runner.STATE.load_state(layout.state_path)
    archive_contract = runner._followup_archive_contract(
        parent, "https://chatgpt.com/c/exact-parent-conversation"
    )
    manifest_payload = runner._followup_manifest_payload(
        parent, mission_path=mission, run_id="followup-bound-child-0001",
        archive_contract=archive_contract,
    )
    manifest_payload["run_root"] = str(layout.run_dir.parent)
    manifest = tmp_path / "child.json"
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    config = runner.STATE.load_manifest(manifest, bind_runtime_task=True)
    child = runner.STATE.create_layout(config, run_id=config.requested_run_id)
    child.run_dir.mkdir()
    expected_port = plan["round_receipt_plan"]["child"]["expected_cdp_port"]
    child.state_path.write_text(json.dumps(runner.STATE.state_payload(
        config, child, status="prepared", resolved_version="oracle 0.17.1", cdp_port=expected_port
    )), encoding="utf-8")
    binding = {
        "schema": "codex.chatgpt.oracle-followup-binding/v1",
        "source_thread_id": OWNER,
        "round_key": "bound-round",
        "reservation_path": str(reservation_path),
        "reservation_sha256": reservation_sha256,
        "parent": plan["round_receipt_plan"]["parent"],
        "child": plan["round_receipt_plan"]["child"],
        "conversation_url": "https://chatgpt.com/c/exact-parent-conversation",
    }

    recorded = runner.STATE.persist_followup_binding(child.state_path, binding)

    assert runner.STATE.proven_followup_binding(child.state_path)["sha256"] == recorded["sha256"]
    binding["round_key"] = "tampered"
    with pytest.raises(runner.STATE.OracleStateError):
        runner.STATE.persist_followup_binding(child.state_path, binding)


@pytest.mark.parametrize("mutation, code", [
    ("foreign", "FOREIGN_TASK_SESSION"),
    ("legacy", "FOLLOWUP_PARENT_LEGACY_UNBOUND"),
    ("nonterminal", "FOLLOWUP_PARENT_NOT_EXECUTED"),
    ("writable", "FOLLOWUP_PARENT_PROFILE_FORBIDDEN"),
    ("attachment", "FOLLOWUP_PARENT_PROFILE_FORBIDDEN"),
    ("missing-url", "FOLLOWUP_PARENT_CONVERSATION_INVALID"),
    ("tamper-output", "FOLLOWUP_PARENT_ARTIFACT_INVALID"),
])
def test_followup_rejects_foreign_or_nonqualifying_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, code: str
) -> None:
    runner, layout, mission = make_parent(tmp_path, monkeypatch)
    if mutation == "foreign":
        monkeypatch.setenv("CODEX_THREAD_ID", FOREIGN)
    elif mutation == "tamper-output":
        layout.output_path.write_text("different bytes", encoding="utf-8")
    else:
        state = runner.STATE.load_state(layout.state_path)
        if mutation == "legacy":
            state["originating_task"] = {"schema": "codex.chatgpt.oracle-task-owner/v1", "source_thread_id": None, "binding": "legacy-unbound"}
            state["ownership"]["source_thread_id"] = None
            state["ownership"]["binding"] = "legacy-unbound"
        elif mutation == "nonterminal":
            state["status"] = "running"
            state["terminal_harvested"] = False
        elif mutation == "writable":
            state["transport"] = "pro-devspace"
        elif mutation == "attachment":
            state["transport"] = "pro-attachment-only"
        elif mutation == "missing-url":
            state["oracle"].pop("conversation_url", None)
        runner.STATE.write_json_atomic(layout.state_path, state)

    with pytest.raises(runner.OracleRunError) as exc:
        runner.followup_run(layout.run_dir, mission_path=mission, round_key="round-1", dry_run=True)

    assert exc.value.code == code


def test_followup_duplicate_round_and_new_conversation_are_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner, layout, mission = make_parent(tmp_path, monkeypatch)
    receipt_path = layout.run_dir / "followup-rounds" / "round-1.json"
    receipt_path.parent.mkdir()
    receipt_path.write_text("{}", encoding="utf-8")
    with pytest.raises(runner.OracleRunError) as duplicate:
        runner.followup_run(layout.run_dir, mission_path=mission, round_key="round-1", dry_run=True)
    assert duplicate.value.code == "FOLLOWUP_ROUND_DUPLICATE"
    receipt_path.unlink()

    original_receipt = runner.STATE.proven_browser_identity_receipt
    parent_dir = layout.run_dir

    def fake_execute(manifest_path, **kwargs):
        child_run = Path(json.loads(Path(manifest_path).read_text(encoding="utf-8"))["run_root"]) / "followup-child-0001"
        child_run.mkdir(parents=True)
        (child_run / "state.json").write_text(
            json.dumps({"schema": "codex.chatgpt.oracle-run-state/v1", "oracle": {}}), encoding="utf-8"
        )
        return {"ok": True, "run_dir": str(child_run)}

    def fake_receipt(state_path):
        if Path(state_path).parent == parent_dir:
            return original_receipt(state_path)
        return {"payload": {"conversation_url": "https://chatgpt.com/c/a-different-conversation"}}

    monkeypatch.setattr(runner, "execute_run", fake_execute)
    monkeypatch.setattr(runner.STATE, "proven_browser_identity_receipt", fake_receipt)
    with pytest.raises(runner.OracleRunError) as mismatch:
        runner.followup_run(
            layout.run_dir, mission_path=mission, round_key="round-2", run_id="followup-child-0001"
        )
    assert mismatch.value.code == "FOLLOWUP_CONVERSATION_IDENTITY_UNVERIFIED"
    assert (layout.run_dir / "followup-rounds" / "round-2.result.json").is_file()


@pytest.mark.parametrize("state_fields", [
    {"status": "complete", "session_authority": "terminal", "terminal_harvested": True, "task_outcome": "executed"},
    {"status": "attention_required", "session_authority": "terminal", "terminal_harvested": True, "task_outcome": "blocked"},
    {"status": "failed", "session_authority": "submitted_unknown", "terminal_harvested": False, "task_outcome": "pending"},
])
def test_followup_non_pre_submit_outcomes_always_seal_a_reverifiable_result_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_fields: dict
) -> None:
    runner, layout, mission = make_parent(tmp_path, monkeypatch)
    original_receipt = runner.STATE.proven_browser_identity_receipt

    def fake_execute(manifest_path, **kwargs):
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        child_run = Path(payload["run_root"]) / payload["run_id"]
        child_run.mkdir(parents=True)
        child_slug = runner.STATE.oracle_slug(Path(payload["project_root"]), payload["run_id"])
        child_state = {"schema": "codex.chatgpt.oracle-run-state/v1", "run_id": payload["run_id"], "oracle": {"slug": child_slug}, **state_fields}
        (child_run / "state.json").write_text(json.dumps(child_state), encoding="utf-8")
        child_meta = Path(os.environ["ORACLE_SESSION_ROOT"]) / child_slug / "meta.json"
        child_meta.parent.mkdir(parents=True, exist_ok=True)
        child_meta.write_text(json.dumps({"browser": {"archive": {
            "mode": "always", "attempted": True, "archived": True,
            "conversationUrl": "https://chatgpt.com/c/exact-parent-conversation",
        }}}), encoding="utf-8")
        return {"ok": state_fields["task_outcome"] == "executed", "run_dir": str(child_run)}

    def fake_receipt(state_path):
        if Path(state_path).parent == layout.run_dir:
            return original_receipt(state_path)
        return {"sha256": "c" * 64, "payload": {"conversation_url": "https://chatgpt.com/c/exact-parent-conversation"}}

    monkeypatch.setattr(runner, "execute_run", fake_execute)
    monkeypatch.setattr(runner.STATE, "proven_browser_identity_receipt", fake_receipt)
    result = runner.followup_run(
        layout.run_dir, mission_path=mission, round_key=f"round-{state_fields['task_outcome']}",
        run_id=f"followup-{state_fields['task_outcome']}-0001",
    )

    receipt = Path(result["followup_round_result_receipt"]["path"])
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["conversation_binding"]["identity_status"] == "same-exact-conversation"
    assert payload["child"]["task_outcome"] == state_fields["task_outcome"]
    assert payload["child"]["output"]["present"] is False


def test_followup_missing_child_browser_receipt_is_uncertain_and_locks_only_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, layout, mission = make_parent(tmp_path, monkeypatch)
    original_receipt = runner.STATE.proven_browser_identity_receipt

    def fake_execute(manifest_path, **kwargs):
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        child_run = Path(payload["run_root"]) / payload["run_id"]
        child_run.mkdir(parents=True)
        (child_run / "state.json").write_text(json.dumps({
            "schema": "codex.chatgpt.oracle-run-state/v1", "run_id": payload["run_id"], "oracle": {},
            "status": "failed", "session_authority": "submitted_unknown", "terminal_harvested": False,
            "task_outcome": "pending",
        }), encoding="utf-8")
        return {"ok": False, "run_dir": str(child_run)}

    def fake_receipt(state_path):
        return original_receipt(state_path) if Path(state_path).parent == layout.run_dir else None

    monkeypatch.setattr(runner, "execute_run", fake_execute)
    monkeypatch.setattr(runner.STATE, "proven_browser_identity_receipt", fake_receipt)
    with pytest.raises(runner.OracleRunError) as exc:
        runner.followup_run(layout.run_dir, mission_path=mission, round_key="round-absence", run_id="followup-absence-0001")

    assert exc.value.code == "FOLLOWUP_CONVERSATION_IDENTITY_UNVERIFIED"
    child_state = json.loads((layout.run_dir.parent / "followup-absence-0001" / "state.json").read_text(encoding="utf-8"))
    assert child_state["status"] == "attention_required"
    assert (layout.run_dir / "followup-rounds" / "round-absence.result.json").is_file()


def test_followup_textarea_absent_requires_harvest_and_owner_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, layout, mission = make_parent(tmp_path, monkeypatch)

    def fake_execute(manifest_path, **kwargs):
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        config = runner.STATE.load_manifest(Path(manifest_path), bind_runtime_task=True)
        child = runner.STATE.create_layout(config, run_id=config.requested_run_id)
        child.run_dir.mkdir()
        child_mission = child.run_dir / "mission.md"
        child_mission.write_bytes(Path(payload["mission_path"]).read_bytes())
        text = (
            f"🧿 oracle 0.17.1 — test\nSession: {child.slug}\nMode: browser foreground\n"
            "Models: 1\nDetach: no\n"
            f"Reattach: oracle session {child.slug}\n"
            "Launching browser mode (target=GPT-5.6 Sol; requested=gpt-5.6-sol) with ~1 tokens.\n"
            "This run can take up to an hour (usually ~10 minutes).\n"
            "ERROR: Prompt textarea did not appear before timeout\n"
            "User error (browser-automation): Prompt textarea did not appear before timeout\n"
        )
        child.stdout_path.write_text(text, encoding="utf-8")
        child.stderr_path.write_bytes(b"")
        child.transcript_path.write_text(text, encoding="utf-8")
        state = runner.STATE.state_payload(
            config, child, status="attention_required", resolved_version="oracle 0.17.1",
            exit_code=1, cdp_port=kwargs["_cdp_port"],
        )
        state.update({
            "session_authority": "submitted_unknown", "transport_status": "failed",
            "task_outcome": "pending", "terminal_harvested": False,
        })
        runner.STATE.write_json_atomic(child.state_path, state)
        runner.STATE.persist_followup_binding(child.state_path, kwargs["_followup_binding"])
        runner.STATE.persist_ownership_receipt(child.state_path, oracle_process_pid=100)
        meta_path = Path(os.environ["ORACLE_SESSION_ROOT"]) / child.slug / "meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        parent_url = kwargs["_followup_binding"]["conversation_url"]
        meta_path.write_text(json.dumps({
            "id": child.slug, "status": "error", "completedAt": "2026-08-23T00:00:00Z", "mode": "browser", "model": "gpt-5.6-sol",
            "browser": {"config": {"resumeConversationUrl": parent_url}},
            "options": {"browserConfig": {"resumeConversationUrl": parent_url}},
            "error": {"category": "browser-automation", "message": "Prompt textarea did not appear before timeout", "details": {"stage": "execute-browser"}},
        }), encoding="utf-8")
        return {"ok": False, "run_dir": str(child.run_dir)}

    monkeypatch.setattr(runner, "execute_run", fake_execute)
    with pytest.raises(runner.OracleRunError):
        runner.followup_run(
            layout.run_dir, mission_path=mission, round_key="textarea-absent",
            run_id="followup-b2d1aed7ba6145db8f2e56c111d4a856",
        )
    child_state = layout.run_dir.parent / "followup-b2d1aed7ba6145db8f2e56c111d4a856" / "state.json"
    assert runner.STATE.bounded_task_owned_prompt_timeout_harvest_evidence(child_state) is not None
    assert runner.STATE._user_confirmable_no_submission_evidence(child_state) is None
    child_slug = runner.STATE.load_state(child_state)["oracle"]["slug"]
    (child_state.parent / "recovery-harvest-stdout.log").write_text(
        f'No live ChatGPT tab matched session "{child_slug}".\n', encoding="utf-8"
    )
    (child_state.parent / "recovery-harvest-stderr.log").write_text(
        "Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL\n",
        encoding="utf-8",
    )
    assert runner.STATE._user_confirmable_no_submission_evidence(child_state) is not None
    monkeypatch.setenv("CODEX_THREAD_ID", FOREIGN)
    with pytest.raises(runner.STATE.OracleStateError, match="different Codex task"):
        runner.STATE.settle_user_confirmed_no_submission(
            child_state, confirmation="user-confirmed-no-submission", reason="foreign must fail",
        )
    monkeypatch.setenv("CODEX_THREAD_ID", OWNER)
    settled = runner.STATE.settle_user_confirmed_no_submission(
        child_state, confirmation="user-confirmed-no-submission", reason="exact textarea absent",
    )
    assert settled["transport_status"] == "not_submitted_user_confirmed"
    assert runner.STATE.proven_user_confirmed_no_submission(child_state) is not None
    with pytest.raises(runner.OracleRunError) as duplicate:
        runner.followup_run(
            layout.run_dir, mission_path=mission, round_key="textarea-absent", dry_run=True,
        )
    assert duplicate.value.code == "FOLLOWUP_ROUND_DUPLICATE"
    tampered = runner.STATE.load_state(child_state)
    tampered["followup_binding"]["sha256"] = "b" * 64
    runner.STATE.write_json_atomic(child_state, tampered)
    assert runner.STATE.proven_user_confirmed_no_submission(child_state) is None
    assert runner.STATE._legacy_followup_reservation_for_child(child_state) is not None
    assert runner.STATE._followup_no_submission_evidence(
        child_state, require_recovery_evidence=True
    ) is None


@pytest.mark.parametrize(
    "structured, expected_profile",
    [
        (False, "archived-parent-unarchive-v1.18.4-legacy-no-click/v1"),
        (True, "archived-parent-unarchive-before-composer/v2"),
    ],
)
def test_followup_unarchive_menu_absent_is_bounded_before_composer_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    structured: bool,
    expected_profile: str,
) -> None:
    runner, _parent, child, _meta_path = make_archived_parent_unarchive_failure(
        tmp_path, monkeypatch, structured=structured
    )

    evidence = runner.STATE._followup_no_submission_evidence(
        child.state_path, require_recovery_evidence=True
    )

    assert evidence is not None
    assert evidence["settlement_eligibility"] == "oracle-followup-pre-submit-ui/v2"
    assert evidence["failure_kind"] == "archived-parent-unarchive-menu-absent"
    assert evidence["evidence_profile"] == expected_profile
    assert evidence["recovery_evidence"] == []
    assert evidence["parent_conversation_url"] == "https://chatgpt.com/c/exact-parent-conversation"
    if not structured:
        assert evidence["legacy_install_receipt_sha256"]
    settled = runner.STATE.settle_user_confirmed_no_submission(
        child.state_path,
        confirmation="user-confirmed-no-submission",
        reason="synthetic exact before-composer failure",
    )
    assert settled["transport_status"] == "not_submitted_user_confirmed"
    assert runner.STATE.proven_user_confirmed_no_submission(child.state_path) is not None
    assert runner.STATE.unresolved_project_sessions(
        child.run_dir.parent,
        Path(settled["project_root"]),
        source_thread_id=OWNER,
    ) == []


@pytest.mark.parametrize(
    "field, value",
    [
        ("stage", "execute-browser"),
        ("code", "OTHER_FAILURE"),
        ("reason", "unarchive-not-confirmed"),
        ("observedConversationUrl", "https://chatgpt.com/c/foreign"),
        ("promptSubmitted", True),
        ("composerSubmitAttempted", True),
        ("turnCountAfter", 6),
    ],
)
def test_followup_unarchive_structured_evidence_rejects_ambiguity_or_submission_risk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value,
) -> None:
    runner, _parent, child, meta_path = make_archived_parent_unarchive_failure(
        tmp_path, monkeypatch, structured=True
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["error"]["details"][field] = value
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert runner.STATE._followup_no_submission_evidence(
        child.state_path, require_recovery_evidence=True
    ) is None


def test_followup_unarchive_no_submission_settlement_requires_inactive_exact_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path = make_archived_parent_unarchive_failure(
        tmp_path, monkeypatch, structured=True
    )
    monkeypatch.setattr(runner, "run_owned_process_ids", lambda *_args: (4242,))
    monkeypatch.setattr(runner, "process_is_alive", lambda pid: pid == 4242)

    with pytest.raises(runner.OracleRunError) as active:
        runner.settle_user_confirmed_no_submission(
            child.run_dir,
            confirmation="user-confirmed-no-submission",
            reason="synthetic exact before-composer failure",
        )

    assert active.value.code == "NO_SUBMISSION_PROCESS_ACTIVE"


def test_followup_unarchive_legacy_evidence_rejects_missing_install_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path = make_archived_parent_unarchive_failure(
        tmp_path, monkeypatch, structured=False
    )
    receipt_path = next((tmp_path / "install-receipts").glob("*.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["installed_at"] = "2099-01-01T00:00:00Z"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    assert runner.STATE._followup_no_submission_evidence(
        child.state_path, require_recovery_evidence=True
    ) is None


def test_followup_unarchive_structured_evidence_rejects_runtime_contradiction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, meta_path = make_archived_parent_unarchive_failure(
        tmp_path, monkeypatch, structured=True
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["browser"]["runtime"] = {
        "promptSubmitted": True,
        "tabUrl": "https://chatgpt.com/c/exact-parent-conversation",
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert runner.STATE._followup_no_submission_evidence(
        child.state_path, require_recovery_evidence=True
    ) is None


def test_followup_structured_pre_composer_failure_is_message_independent_and_settleable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path = make_structured_resume_failure(
        tmp_path, monkeypatch
    )

    evidence = runner.STATE._followup_no_submission_evidence(
        child.state_path, require_recovery_evidence=True
    )

    assert evidence is not None
    assert evidence["failure_kind"] == "structured-pre-composer"
    assert evidence["evidence_profile"] == "structured-pre-composer-runtime-unbound/v1"
    assert evidence["pre_composer_stage"] == "resume-conversation"
    assert evidence["prior_turns_observed"] == 8
    assert evidence["recovery_evidence"] == []
    settled = runner.STATE.settle_user_confirmed_no_submission(
        child.state_path,
        confirmation="user-confirmed-no-submission",
        reason="exact structured pre-composer runtime-unbound failure",
    )
    assert settled["transport_status"] == "not_submitted_user_confirmed"
    assert runner.STATE.proven_user_confirmed_no_submission(child.state_path) is not None
    assert runner.STATE.unresolved_project_sessions(
        child.run_dir.parent,
        Path(settled["project_root"]),
        source_thread_id=OWNER,
    ) == []


@pytest.mark.parametrize(
    "mutation",
    (
        "runtime-present",
        "post-composer-stage",
        "settled",
        "zero-prior-turns",
        "extra-detail",
        "observer-live",
        "missing-pid",
        "pid-still-alive",
        "reason-mismatch",
        "stdout-meta-mismatch",
    ),
)
def test_followup_structured_pre_composer_evidence_rejects_ambiguity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    runner, _parent, child, meta_path = make_structured_resume_failure(
        tmp_path, monkeypatch
    )
    state = runner.STATE.load_state(child.state_path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if mutation == "runtime-present":
        meta["browser"]["runtime"] = {"promptSubmitted": False}
    elif mutation == "post-composer-stage":
        meta["error"]["details"]["stage"] = "submit-prompt"
    elif mutation == "settled":
        meta["error"]["details"]["settled"] = True
    elif mutation == "zero-prior-turns":
        meta["error"]["details"]["priorTurns"] = 0
    elif mutation == "extra-detail":
        meta["error"]["details"]["promptSubmitted"] = False
    elif mutation == "observer-live":
        state["browser_observer"]["status"] = "running"
    elif mutation == "missing-pid":
        state["browser_observer"]["oracle_process_pid"] = None
    elif mutation == "pid-still-alive":
        monkeypatch.setattr(runner.STATE, "_process_may_be_alive", lambda _pid: True)
    elif mutation == "reason-mismatch":
        state["task_outcome_reason"] = "some-other-reason"
    elif mutation == "stdout-meta-mismatch":
        meta["error"]["message"] = "different structured error"
    runner.STATE.write_json_atomic(child.state_path, state)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert runner.STATE._followup_no_submission_evidence(
        child.state_path, require_recovery_evidence=True
    ) is None


def test_followup_archived_parent_legacy_harmless_harvest_keeps_settlement_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path = make_archived_parent_unarchive_failure(
        tmp_path, monkeypatch, structured=False
    )
    write_exact_harmless_archived_parent_harvest(child, runner)

    evidence = runner.STATE._followup_no_submission_evidence(
        child.state_path, require_recovery_evidence=True
    )

    assert evidence is not None
    assert evidence["failure_kind"] == "archived-parent-unarchive-menu-absent"
    assert len(evidence["recovery_evidence"]) == 1
    settled = runner.STATE.settle_user_confirmed_no_submission(
        child.state_path,
        confirmation="user-confirmed-no-submission",
        reason="exact harmless no-tab/no-url harvest still proves no child submission",
    )
    assert settled["transport_status"] == "not_submitted_user_confirmed"
    assert runner.STATE.proven_user_confirmed_no_submission(child.state_path) is not None


@pytest.mark.parametrize(
    "mutation",
    (
        "partial-pair",
        "wrong-slug",
        "child-conversation-url",
        "nonempty-candidate",
        "additional-recovery-log",
        "symlink",
    ),
)
def test_followup_archived_parent_harvest_evidence_fails_closed_on_any_ambiguity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    runner, _parent, child, _meta_path = make_archived_parent_unarchive_failure(
        tmp_path, monkeypatch, structured=False
    )
    write_exact_harmless_archived_parent_harvest(child, runner)
    stdout_path = child.run_dir / "recovery-harvest-stdout.log"
    stderr_path = child.run_dir / "recovery-harvest-stderr.log"

    if mutation == "partial-pair":
        stderr_path.unlink()
    elif mutation == "wrong-slug":
        stdout_path.write_text(
            stdout_path.read_text(encoding="utf-8").replace(
                runner.STATE.load_state(child.state_path)["oracle"]["slug"],
                "oracle-coin-wrong-slug",
            ),
            encoding="utf-8",
        )
    elif mutation == "child-conversation-url":
        stderr_path.write_text(
            stderr_path.read_text(encoding="utf-8")
            + "https://chatgpt.com/c/a-real-child-conversation\n",
            encoding="utf-8",
        )
    elif mutation == "nonempty-candidate":
        (child.run_dir / "recovery-harvest-candidate.md").write_text(
            "candidate output must revoke no-submission evidence\n", encoding="utf-8"
        )
    elif mutation == "additional-recovery-log":
        (child.run_dir / "recovery-live-stdout.log").write_text(
            "another recovery attempt is ambiguous\n", encoding="utf-8"
        )
    else:
        target = child.run_dir / "outside-recovery.log"
        target.write_text("not an owned recovery record\n", encoding="utf-8")
        stdout_path.unlink()
        try:
            stdout_path.symlink_to(target)
        except OSError:
            pytest.skip("the current Windows test host does not permit symlinks")

    assert runner.STATE._followup_no_submission_evidence(
        child.state_path, require_recovery_evidence=True
    ) is None
    assert runner.STATE.proven_user_confirmed_no_submission(child.state_path) is None


def test_structured_archived_parent_failure_rejects_harvest_and_directs_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child, _meta_path = make_archived_parent_unarchive_failure(
        tmp_path, monkeypatch, structured=True
    )

    with pytest.raises(runner.OracleRunError) as exc:
        runner.recover_run(
            child.run_dir,
            action="harvest",
            dry_run=True,
            oracle_command=["oracle"],
    )

    assert exc.value.code == "FOLLOWUP_ARCHIVED_PARENT_HARVEST_NOT_APPLICABLE"
    assert exc.value.evidence["submission_action"] == "none"
    assert "settle-no-submission" in exc.value.evidence["next_action"]
    assert runner.STATE._user_confirmable_no_submission_evidence(child.state_path) is not None


def test_followup_official_v1_settlement_revalidates_against_exact_v2_textarea_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _parent, child = make_followup_textarea_absent_with_harvest(tmp_path, monkeypatch)
    recorded = rewrite_settlement_as_official_followup_v1(child, runner)

    assert recorded["settlement_eligibility"] == "oracle-followup-pre-submit-ui/v1"
    assert not {"failure_kind", "evidence_profile", "harvest_outcome"} & set(recorded)
    proof = runner.STATE.proven_user_confirmed_no_submission(child.state_path)

    assert proof is not None
    assert proof["settlement_eligibility"] == "oracle-followup-pre-submit-ui/v1"
    assert runner.STATE.unresolved_project_sessions(
        child.run_dir.parent,
        Path(runner.STATE.load_state(child.state_path)["project_root"]),
        source_thread_id=OWNER,
    ) == []


@pytest.mark.parametrize(
    "field, replacement",
    (
        ("project_root", "C:/not-the-exact-project"),
        ("run_id", "other-child-run"),
        ("stdout_sha256", "0" * 64),
        ("stderr_sha256", "1" * 64),
        ("recovery_evidence", []),
        ("output_absent", False),
        ("conversation_url_absent", False),
        ("mission_sha256", "2" * 64),
        ("oracle_locator", "oracle-coin-other-child"),
        ("followup_binding_mode", "foreign-task-binding/v1"),
        ("followup_reservation_sha256", "3" * 64),
        ("parent_run_id", "other-parent-run"),
        ("parent_conversation_url", "https://chatgpt.com/c/other-parent"),
        ("round_key", "other-round"),
        ("oracle_meta_sha256", "4" * 64),
    ),
)
def test_followup_v1_to_v2_revalidation_fails_closed_on_any_core_or_binding_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, replacement
) -> None:
    runner, _parent, child = make_followup_textarea_absent_with_harvest(tmp_path, monkeypatch)
    rewrite_settlement_as_official_followup_v1(
        child, runner, mutate=lambda recorded: recorded.__setitem__(field, replacement)
    )

    assert runner.STATE.proven_user_confirmed_no_submission(child.state_path) is None
    owners = runner.STATE.unresolved_project_sessions(
        child.run_dir.parent,
        Path(runner.STATE.load_state(child.state_path)["project_root"]),
        source_thread_id=OWNER,
    )
    assert [owner["run_id"] for owner in owners] == [child.run_dir.name]


@pytest.mark.parametrize(
    "kind",
    ("archived-parent-current-v2", "unexpected-v2-field", "wrong-recorded-eligibility"),
)
def test_followup_v1_receipt_compatibility_is_not_a_general_v2_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    if kind == "archived-parent-current-v2":
        runner, _parent, child, _meta_path = make_archived_parent_unarchive_failure(
            tmp_path, monkeypatch, structured=True
        )
    else:
        runner, _parent, child = make_followup_textarea_absent_with_harvest(tmp_path, monkeypatch)
    recorded = rewrite_settlement_as_official_followup_v1(child, runner)
    if kind == "unexpected-v2-field":
        recorded["failure_kind"] = "textarea-absent"
    elif kind == "wrong-recorded-eligibility":
        recorded["settlement_eligibility"] = "oracle-followup-pre-submit-ui/v2"
    settlement_path = child.run_dir / "user-confirmed-no-submission.json"
    runner.STATE.write_json_atomic(settlement_path, recorded)
    state = runner.STATE.load_state(child.state_path)
    state["user_confirmed_no_submission"]["sha256"] = hashlib.sha256(
        settlement_path.read_bytes()
    ).hexdigest()
    runner.STATE.write_json_atomic(child.state_path, state)

    assert runner.STATE.proven_user_confirmed_no_submission(child.state_path) is None
