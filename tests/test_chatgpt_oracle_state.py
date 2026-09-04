from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

STATE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_state.py"
REFERENCE_FOOTER_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "oracle-task-outcome-reference-footer.md"
)


def load_state():
    name = "chatgpt_oracle_state_test"
    spec = importlib.util.spec_from_file_location(name, STATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run_process_state(run_dir: Path) -> dict[str, object]:
    return {
        "oracle": {"slug": "oracle-demo-abc123"},
        "artifacts": {
            "browser_temp": str(run_dir / "browser-temp"),
            "output": str(run_dir / "output.md"),
        },
    }


def test_exact_run_process_identity_rejects_reused_unrelated_pid(tmp_path: Path) -> None:
    state = load_state()
    run_dir = tmp_path / "runs" / "abc123"
    snapshot = {
        "ProcessId": 4242,
        "Name": "csrss.exe",
        "ExecutablePath": r"C:\Windows\System32\csrss.exe",
        "CommandLine": "",
    }

    assert state.exact_run_process_may_be_alive(
        run_dir,
        _run_process_state(run_dir),
        4242,
        process_probe=lambda _pid: True,
        windows_snapshot=lambda _pid: snapshot,
        platform_name="nt",
    ) is False


@pytest.mark.parametrize(
    "command_line",
    [
        r'node.exe oracle --slug oracle-demo-abc123 --write-output C:\result.md',
        r'chrome.exe --user-data-dir="{browser_temp}" --remote-debugging-port=61234',
    ],
)
def test_exact_run_process_identity_keeps_bound_controller_or_chrome_active(
    tmp_path: Path,
    command_line: str,
) -> None:
    state = load_state()
    run_dir = tmp_path / "runs" / "abc123"
    rendered = command_line.format(browser_temp=run_dir / "browser-temp")

    assert state.exact_run_process_may_be_alive(
        run_dir,
        _run_process_state(run_dir),
        4242,
        process_probe=lambda _pid: True,
        windows_snapshot=lambda _pid: {
            "ProcessId": 4242,
            "Name": "node.exe" if rendered.startswith("node") else "chrome.exe",
            "CommandLine": rendered,
        },
        platform_name="nt",
    ) is True


def test_exact_run_process_identity_rejects_live_foreign_runtime(tmp_path: Path) -> None:
    state = load_state()
    run_dir = tmp_path / "runs" / "abc123"

    assert state.exact_run_process_may_be_alive(
        run_dir,
        _run_process_state(run_dir),
        4242,
        process_probe=lambda _pid: True,
        windows_snapshot=lambda _pid: {
            "ProcessId": 4242,
            "Name": "node.exe",
            "CommandLine": "node.exe unrelated-server.mjs --port 8080",
        },
        platform_name="nt",
    ) is False


def test_exact_run_process_identity_keeps_ambiguous_live_pid_blocking(tmp_path: Path) -> None:
    state = load_state()
    run_dir = tmp_path / "runs" / "abc123"

    def denied(_pid: int):
        raise OSError("access denied")

    assert state.exact_run_process_may_be_alive(
        run_dir,
        _run_process_state(run_dir),
        4242,
        process_probe=lambda _pid: True,
        windows_snapshot=denied,
        platform_name="nt",
    ) is True

    assert state.exact_run_process_may_be_alive(
        run_dir,
        _run_process_state(run_dir),
        4242,
        process_probe=lambda _pid: True,
        windows_snapshot=lambda _pid: {
            "ProcessId": 4242,
            "Name": "node.exe",
            "CommandLine": "",
        },
        platform_name="nt",
    ) is True


@pytest.mark.parametrize("winerror", [5, 32])
def test_write_json_atomic_retries_bounded_windows_replace_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winerror: int,
) -> None:
    state = load_state()
    destination = tmp_path / "state.json"
    original_replace = state.os.replace
    calls: list[tuple[Path, Path]] = []
    sleeps: list[float] = []

    def flaky_replace(source, target):
        calls.append((Path(source), Path(target)))
        if len(calls) <= 2:
            error = PermissionError("transient Windows sharing race")
            error.winerror = winerror
            raise error
        return original_replace(source, target)

    monkeypatch.setattr(state.os, "replace", flaky_replace)
    monkeypatch.setattr(state.time, "sleep", sleeps.append)

    state.write_json_atomic(destination, {"status": "attention_required"})

    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "status": "attention_required"
    }
    assert len(calls) == 3
    assert sleeps == list(state.ATOMIC_REPLACE_BACKOFF_SECONDS[:2])
    assert list(tmp_path.glob(".t-*")) == []


def test_write_json_atomic_keeps_permanent_windows_replace_failure_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    destination = tmp_path / "state.json"
    destination.write_text('{"status":"original"}\n', encoding="utf-8")
    calls = 0

    def always_denied(_source, _target):
        nonlocal calls
        calls += 1
        error = PermissionError("persistent Windows sharing race")
        error.winerror = 5
        raise error

    monkeypatch.setattr(state.os, "replace", always_denied)
    monkeypatch.setattr(state.time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError):
        state.write_json_atomic(destination, {"status": "replacement"})

    assert calls == state.ATOMIC_REPLACE_MAX_ATTEMPTS
    assert destination.read_text(encoding="utf-8") == '{"status":"original"}\n'
    assert list(tmp_path.glob(".t-*")) == []


def test_write_json_atomic_does_not_retry_unrecognized_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    destination = tmp_path / "state.json"
    calls = 0

    def denied(_source, _target):
        nonlocal calls
        calls += 1
        error = PermissionError("unrecognized access failure")
        error.winerror = 123
        raise error

    monkeypatch.setattr(state.os, "replace", denied)
    monkeypatch.setattr(
        state.time,
        "sleep",
        lambda _seconds: pytest.fail("unrecognized failures must not retry"),
    )

    with pytest.raises(PermissionError):
        state.write_json_atomic(destination, {"status": "replacement"})

    assert calls == 1
    assert not destination.exists()
    assert list(tmp_path.glob(".t-*")) == []


def test_write_json_atomic_uses_short_same_directory_temp_name(tmp_path: Path) -> None:
    state = load_state()
    directory = tmp_path
    for segment in ("host-state", "projects", "f" * 24, "runs", "a" * 32):
        directory /= segment
    destination = directory / "user-confirmed-no-submission.json"

    state.write_json_atomic(destination, {"status": "settled"})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"status": "settled"}
    assert list(directory.glob(".t-*")) == []


def _oracle_metadata_rename_fixture(
    tmp_path: Path,
    state,
    *,
    source_thread_id: str = "019ff05c-bad3-7770-a902-6b1b62588a7d",
) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    run_id = "20260827T075513Z-23acae337024"
    locator = "oracle-project-23acae3370"
    run_dir = tmp_path / "state" / "projects" / "project" / "runs" / run_id
    browser_temp = run_dir / "browser-temp"
    session_root = tmp_path / "oracle-sessions"
    meta_path = session_root / locator / "meta.json"
    profile_path = tmp_path / "oracle-profile"
    project_root.mkdir()
    run_dir.mkdir(parents=True)
    browser_temp.mkdir()
    profile_path.mkdir()
    meta_path.parent.mkdir(parents=True)
    mission_bytes = b"verify registered app read route\n"
    mission_sha256 = hashlib.sha256(mission_bytes).hexdigest()
    (run_dir / "mission.md").write_bytes(mission_bytes)
    output_path = run_dir / "output.md"
    expected_cdp_port = 56853
    controller_pid = 10796
    writer_pid = 12312
    nonce = "97ee88b0-7c8d-46d0-bfee-5aa74901def3"
    stdout_bytes = (
        "🧿 oracle 0.18.0 — Fine, I'll write the test for the AI too.\n"
        f"Session: {locator}\n"
        "Mode: browser foreground\n"
        "Models: 1\n"
        "Detach: no\n"
        f"Reattach: oracle session {locator}\n"
    ).encode("utf-8")
    stderr_bytes = (
        "✖ EPERM: operation not permitted, rename "
        f"'{meta_path}.{writer_pid}.{nonce}.tmp' -> '{meta_path}'\n"
    ).encode("utf-8")
    (run_dir / "stdout.log").write_bytes(stdout_bytes)
    (run_dir / "stderr.log").write_bytes(stderr_bytes)
    (run_dir / "transcript.md").write_bytes(stdout_bytes + stderr_bytes)
    browser_config = {
        "debugPort": expected_cdp_port,
        "copyProfileSource": str(profile_path),
        "desiredModel": "GPT-5.6 Sol",
        "modelStrategy": "select",
        "thinkingTime": "extra-high",
    }
    meta = {
        "id": locator,
        "createdAt": "2026-08-27T07:55:18.733Z",
        "status": "pending",
        "model": "gpt-5.6",
        "models": [
            {"model": "gpt-5.6", "status": "pending", "log": {"path": "models\\gpt-5.6.log"}}
        ],
        "cwd": str(project_root),
        "mode": "browser",
        "browser": {"config": browser_config},
        "options": {
            "model": "gpt-5.6",
            "slug": locator,
            "mode": "browser",
            "writeOutputPath": str(output_path),
            "browserConfig": dict(browser_config),
        },
    }
    meta_bytes = json.dumps(meta, indent=2).encode("utf-8")
    meta_path.write_bytes(meta_bytes)
    project_hash = hashlib.sha256(
        str(project_root).casefold().encode("utf-8")
    ).hexdigest()
    payload = {
        "schema": state.STATE_SCHEMA,
        "run_id": run_id,
        "project_root": str(project_root),
        "mode": "browser",
        "transport": "devspace",
        "app_name": "codex",
        "profile": {
            "model": "gpt-5.6",
            "model_strategy": "select",
            "thinking_time": "extra-high",
            "copy_profile": str(profile_path),
        },
        "parallel_parent_id": None,
        "requested_run_id": None,
        "web_multi_child_provenance": None,
        "originating_task": {
            "schema": "codex.chatgpt.oracle-task-owner/v1",
            "source_thread_id": source_thread_id,
            "binding": "bound",
        },
        "ownership": {
            "schema": "codex.chatgpt.oracle-ownership/v1",
            "source_thread_id": source_thread_id,
            "binding": "bound",
            "project_root_sha256": project_hash,
            "run_id": run_id,
            "mission_sha256": mission_sha256,
            "slug": locator,
        },
        "transport_status": "failed",
        "task_outcome_contract": "v1",
        "task_outcome": "pending",
        "mission": {
            "path": str(project_root / "mission-source.md"),
            "transport_path": str(run_dir / "mission.md"),
            "sha256": mission_sha256,
        },
        "attachments": [],
        "oracle": {
            "resolved_version": "0.18.0",
            "command": ["npx.cmd", "-y", "@steipete/oracle@0.18.0"],
            "slug": locator,
            "session_locator": locator,
        },
        "artifacts": {
            "output": str(output_path),
            "transcript": str(run_dir / "transcript.md"),
            "stdout": str(run_dir / "stdout.log"),
            "stderr": str(run_dir / "stderr.log"),
            "browser_temp": str(browser_temp),
        },
        "browser_identity": {
            "schema": "codex.chatgpt.oracle-browser-identity/v1",
            "expected_cdp_port": expected_cdp_port,
            "receipt_path": None,
            "receipt_sha256": None,
        },
        "provider_session": {
            "schema": "codex.chatgpt.oracle-provider-session/v1",
            "status": "pending",
            "terminal_confirmed": False,
            "binding": "unconfirmed",
            "reason": "browser-identity-receipt-unavailable",
            "oracle_meta_path": str(meta_path),
            "observed_conversation_url": None,
            "completed_at": None,
            "oracle_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
        },
        "status": "attention_required",
        "exit_code": 1,
        "session_authority": "submitted_unknown",
        "terminal_harvested": False,
        "artifact_sha256": None,
        "browser_observer": {
            "status": "process-exited",
            "oracle_process_pid": controller_pid,
            "timeout_is_terminal": False,
        },
    }
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    ownership_receipt = {
        "schema": "codex.chatgpt.oracle-ownership-receipt/v1",
        "source_thread_id": source_thread_id,
        "binding": "bound",
        "project_root": str(project_root),
        "project_root_sha256": project_hash,
        "run_id": run_id,
        "mission_sha256": mission_sha256,
        "slug": locator,
        "oracle_process_pid": controller_pid,
        "expected_cdp_port": expected_cdp_port,
        "browser_temp": str(browser_temp),
        "created_at": "2026-08-27T07:55:16.726047+00:00",
    }
    (run_dir / "ownership-receipt.json").write_text(
        json.dumps(ownership_receipt), encoding="utf-8"
    )
    return state_path, meta_path


def test_oracle_metadata_rename_prelaunch_failure_is_exactly_settleable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    source_thread_id = "019ff05c-bad3-7770-a902-6b1b62588a7d"
    state_path, _ = _oracle_metadata_rename_fixture(
        tmp_path, state, source_thread_id=source_thread_id
    )
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(tmp_path / "oracle-sessions"))
    monkeypatch.setenv("CODEX_THREAD_ID", source_thread_id)
    monkeypatch.setattr(state, "_process_may_be_alive", lambda _pid: False)

    failure = state.proven_pre_submit_oracle_metadata_rename_failure(state_path)
    assert failure is not None
    assert failure["code"] == "ORACLE_SESSION_METADATA_RENAME_PRELAUNCH_FAILED"
    assert state._pre_submit_host_no_submission_evidence(state_path) is not None

    settled = state.settle_user_confirmed_no_submission(
        state_path,
        confirmation=state.USER_CONFIRMED_NO_SUBMISSION,
        reason="exact Oracle metadata replacement exhausted before browser launch",
    )
    assert settled["session_authority"] == "pre_submit"
    assert settled["transport_status"] == "not_submitted_user_confirmed"
    assert settled["task_outcome_reason"] == (
        "user-confirmed-no-submission-after-oracle-metadata-rename-failure"
    )
    assert state.proven_user_confirmed_no_submission(state_path) is not None


def test_oracle_metadata_rename_settlement_revalidates_pre_task_field_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    source_thread_id = "019ff05c-bad3-7770-a902-6b1b62588a7d"
    state_path, _ = _oracle_metadata_rename_fixture(
        tmp_path, state, source_thread_id=source_thread_id
    )
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(tmp_path / "oracle-sessions"))
    monkeypatch.setenv("CODEX_THREAD_ID", source_thread_id)
    monkeypatch.setattr(state, "_process_may_be_alive", lambda _pid: False)

    state.settle_user_confirmed_no_submission(
        state_path,
        confirmation=state.USER_CONFIRMED_NO_SUBMISSION,
        reason="exact Oracle metadata replacement exhausted before browser launch",
    )
    run_dir = state_path.parent
    artifact_path = run_dir / "user-confirmed-no-submission.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    del artifact["host_failure"]["source_thread_id"]
    artifact_bytes = json.dumps(artifact, ensure_ascii=False, indent=2).encode("utf-8")
    artifact_path.write_bytes(artifact_bytes)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["user_confirmed_no_submission"]["sha256"] = hashlib.sha256(
        artifact_bytes
    ).hexdigest()
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    proven = state.proven_user_confirmed_no_submission(state_path)
    assert proven is not None
    assert proven["host_failure"]["ownership_receipt_sha256"]
    assert state.unresolved_project_sessions(
        run_dir.parent,
        Path(payload["project_root"]),
        source_thread_id=source_thread_id,
    ) == []


def test_oracle_metadata_rename_legacy_settlement_rejects_other_field_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    source_thread_id = "019ff05c-bad3-7770-a902-6b1b62588a7d"
    state_path, _ = _oracle_metadata_rename_fixture(
        tmp_path, state, source_thread_id=source_thread_id
    )
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(tmp_path / "oracle-sessions"))
    monkeypatch.setenv("CODEX_THREAD_ID", source_thread_id)
    monkeypatch.setattr(state, "_process_may_be_alive", lambda _pid: False)

    state.settle_user_confirmed_no_submission(
        state_path,
        confirmation=state.USER_CONFIRMED_NO_SUBMISSION,
        reason="exact Oracle metadata replacement exhausted before browser launch",
    )
    artifact_path = state_path.parent / "user-confirmed-no-submission.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    del artifact["host_failure"]["source_thread_id"]
    artifact["host_failure"]["ownership_receipt_sha256"] = "0" * 64
    artifact_bytes = json.dumps(artifact, ensure_ascii=False, indent=2).encode("utf-8")
    artifact_path.write_bytes(artifact_bytes)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["user_confirmed_no_submission"]["sha256"] = hashlib.sha256(
        artifact_bytes
    ).hexdigest()
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    assert state.proven_user_confirmed_no_submission(state_path) is None


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-path",
        "output",
        "browser-runtime",
        "browser-receipt",
        "live-controller",
        "legacy-unbound",
    ],
)
def test_oracle_metadata_rename_prelaunch_failure_rejects_contradictions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    state = load_state()
    state_path, meta_path = _oracle_metadata_rename_fixture(tmp_path, state)
    run_dir = state_path.parent
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(tmp_path / "oracle-sessions"))
    monkeypatch.setenv("CODEX_THREAD_ID", "019ff05c-bad3-7770-a902-6b1b62588a7d")
    monkeypatch.setattr(state, "_process_may_be_alive", lambda pid: mutation == "live-controller" and pid == 10796)

    if mutation == "wrong-path":
        stderr = (run_dir / "stderr.log").read_bytes().replace(
            str(meta_path).encode("utf-8"), str(meta_path.with_name("other.json")).encode("utf-8")
        )
        (run_dir / "stderr.log").write_bytes(stderr)
        (run_dir / "transcript.md").write_bytes((run_dir / "stdout.log").read_bytes() + stderr)
    elif mutation == "output":
        (run_dir / "output.md").write_text("unexpected provider output", encoding="utf-8")
    elif mutation == "browser-runtime":
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["browser"]["runtime"] = {"promptSubmitted": False}
        meta_bytes = json.dumps(meta, indent=2).encode("utf-8")
        meta_path.write_bytes(meta_bytes)
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        payload["provider_session"]["oracle_meta_sha256"] = hashlib.sha256(meta_bytes).hexdigest()
        state_path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "browser-receipt":
        (run_dir / "browser-identity-receipt.json").write_text("{}", encoding="utf-8")
    elif mutation == "legacy-unbound":
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        payload["originating_task"] = {
            "schema": "codex.chatgpt.oracle-task-owner/v1",
            "source_thread_id": None,
            "binding": "legacy-unbound",
        }
        payload["ownership"]["source_thread_id"] = None
        payload["ownership"]["binding"] = "legacy-unbound"
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        receipt_path = run_dir / "ownership-receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["source_thread_id"] = None
        receipt["binding"] = "legacy-unbound"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    assert state.proven_pre_submit_oracle_metadata_rename_failure(state_path) is None
    assert state._pre_submit_host_no_submission_evidence(state_path) is None


def test_oracle_metadata_rename_settlement_rejects_foreign_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    owner_thread_id = "019ff05c-bad3-7770-a902-6b1b62588a7d"
    state_path, _ = _oracle_metadata_rename_fixture(
        tmp_path, state, source_thread_id=owner_thread_id
    )
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(tmp_path / "oracle-sessions"))
    monkeypatch.setenv("CODEX_THREAD_ID", "01a028dd-843a-76c2-b316-376f10c53ddd")
    monkeypatch.setattr(state, "_process_may_be_alive", lambda _pid: False)

    assert state.proven_pre_submit_oracle_metadata_rename_failure(state_path) is not None
    with pytest.raises(state.OracleStateError) as caught:
        state.settle_user_confirmed_no_submission(
            state_path,
            confirmation=state.USER_CONFIRMED_NO_SUBMISSION,
            reason="foreign task must not settle the exact run",
        )
    assert caught.value.code == "FOREIGN_TASK_SESSION"


def test_v1_task_outcome_accepts_exact_provider_reference_footer(tmp_path: Path) -> None:
    state = load_state()
    output = tmp_path / "output.md"
    output.write_bytes(REFERENCE_FOOTER_FIXTURE.read_bytes())

    assert state.classify_task_outcome(
        output,
        contract="v1",
        transport="pro-devspace",
    ) == "executed"


def test_v1_task_outcome_accepts_bounded_rendered_reference_backlinks(tmp_path: Path) -> None:
    state = load_state()
    output = tmp_path / "output.md"
    output.write_text(
        "answer\nTASK_OUTCOME: EXECUTED\n"
        "evidence/a.json; skills/check/SKILL.md. ↩\n"
        "AGENTS.md, section \"Computation delegation, user instruction 2026-08-24\". ↩\n"
        ".codex-tmp/lane-markout/run_markout.py; checksum-verified Binance bookTicker "
        "and aggTrades inputs under .codex-tmp/lane-markout/raw/. ↩\n"
        "검증/결과.json. ↩\n",
        encoding="utf-8",
    )

    assert state.classify_task_outcome(
        output,
        contract="v1",
        transport="pro-devspace-readonly",
    ) == "executed"


@pytest.mark.parametrize(
    "suffix",
    [
        "Actually no files were changed.\n",
        "[note]: this is ordinary prose, not a URL\n",
        "continue observing ↩\n",
        "AGENTS.md arbitrary imperative prose should not be accepted. ↩\n",
        "TASK_OUTCOME: BLOCKED\n",
    ],
)
def test_v1_task_outcome_reference_footer_stays_fail_closed(
    tmp_path: Path,
    suffix: str,
) -> None:
    state = load_state()
    output = tmp_path / "output.md"
    fixture = REFERENCE_FOOTER_FIXTURE.read_text(encoding="utf-8")
    output.write_text(f"{fixture}{suffix}", encoding="utf-8")

    assert state.classify_task_outcome(
        output,
        contract="v1",
        transport="pro-devspace",
    ) == "unknown"


def manifest(tmp_path: Path, mission_path: Path | str, **extra) -> Path:
    os.environ["CODEX_ORACLE_STATE_ROOT"] = str((tmp_path.parent / f"{tmp_path.name}-host-state").resolve())
    value = {
        "schema": "codex.chatgpt.oracle-run/v1",
        "project_root": str(tmp_path.resolve()),
        "mission_path": str(mission_path),
        "app_name": "DevSpace",
        "mode": "browser",
        "oracle_command": ["oracle"],
    }
    value.update(extra)
    path = tmp_path / "job.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path.resolve()


def test_invalid_utf8_and_relative_mission_are_rejected(tmp_path: Path) -> None:
    state = load_state()
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff")
    with pytest.raises(state.OracleStateError) as exc:
        state.load_manifest(manifest(tmp_path, bad.resolve()))
    assert exc.value.code == "UTF8_REQUIRED"
    good = tmp_path / "good.md"
    good.write_text("work", encoding="utf-8")
    with pytest.raises(state.OracleStateError) as exc:
        state.load_manifest(manifest(tmp_path, "good.md"))
    assert exc.value.code == "MISSION_PATH_ABSOLUTE_REQUIRED"


def test_prompt_is_plain_app_plus_absolute_mission_instruction(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    config = state.load_manifest(manifest(tmp_path, mission.resolve()))
    layout = state.create_layout(config, run_id="exact-run-12345678")
    prompt = state.composer_prompt(
        config, run_id=layout.run_id, slug=layout.slug
    )
    assert prompt.startswith(
        f"@DevSpace 먼저 정확한 프로젝트 루트 {tmp_path.resolve()}를 checkout 모드로 여세요."
    )
    assert f"그 다음 미션 파일 {mission.resolve()}를 읽고 끝까지 수행" in prompt
    assert "미션 디렉터리·상위·하위·현재 활성 작업공간을 대신 열지 마세요" in prompt
    assert prompt.index(str(tmp_path.resolve())) < prompt.index(str(mission.resolve()))
    assert "동일한 정확한 루트만 한 번 재시도" in prompt
    assert "셸 경계 우회로 대체하지 마세요" in prompt
    assert f"run {layout.run_id} or slug {layout.slug}" in prompt
    assert "Do not launch a nested Oracle run" in prompt
    assert "state.json, output.md, transcript.md, recovery" in prompt
    assert "\n" not in prompt


def test_readonly_pro_auto_archive_normalizes_to_never_for_followup(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("read-only advice", encoding="utf-8")

    automatic = state.load_manifest(manifest(
        tmp_path,
        mission.resolve(),
        app_name="codex",
        transport="pro-devspace-readonly",
        model="gpt-5.6-sol",
        model_strategy="select",
        thinking_time="pro",
        archive="auto",
        task_outcome_contract="v1",
    ))
    explicit_single_turn = state.load_manifest(manifest(
        tmp_path,
        mission.resolve(),
        app_name="codex",
        transport="pro-devspace-readonly",
        model="gpt-5.6-sol",
        model_strategy="select",
        thinking_time="pro",
        archive="always",
        task_outcome_contract="v1",
    ))

    assert automatic.archive == "never"
    assert explicit_single_turn.archive == "always"

    ordinary_mission = tmp_path / "ordinary.md"
    ordinary_mission.write_text("ordinary", encoding="utf-8")
    ordinary = state.load_manifest(manifest(
        tmp_path,
        ordinary_mission.resolve(),
        app_name="codex",
        transport="devspace",
        archive="auto",
    ))
    assert ordinary.archive == "auto"


def test_pro_manifest_is_attachment_only_and_hashes_exact_files(tmp_path: Path) -> None:
    state = load_state()
    prompt = tmp_path / "prompt.txt"
    packet = tmp_path / "packet.zip"
    prompt.write_text("instructions", encoding="utf-8")
    packet.write_bytes(b"PK\x03\x04packet")
    config = state.load_manifest(
        manifest(
            tmp_path,
            prompt.resolve(),
            transport="pro-attachment-only",
            app_name=None,
            model="gpt-5.6-sol",
            thinking_time="pro",
            attachments=[str(prompt.resolve()), str(packet.resolve())],
        )
    )
    assert config.app_name is None
    assert config.transport == "pro-attachment-only"
    assert config.attachments == (prompt.resolve(), packet.resolve())
    assert config.attachment_sha256s == (
        state.sha256_file(prompt.resolve()),
        state.sha256_file(packet.resolve()),
    )
    composer = state.composer_prompt(config)
    assert composer.startswith(
        "Read the attached prompt/instructions and all attached files, then provide read-only analysis only. "
        "Do not create, edit, delete, or rename files;"
    )
    assert "Task identity: oracle-pro-" in composer
    assert composer.endswith(".")
    assert len(composer.rsplit("oracle-pro-", 1)[1][:-1]) == 24
    assert composer == state.composer_prompt(config)
    assert str(tmp_path.resolve()) not in composer
    assert "@DevSpace" not in composer
    layout = state.create_layout(config, run_id="20260725T151414Z-a3aeba967d99")
    payload = state.state_payload(config, layout, status="prepared", resolved_version="oracle 0.17.1")
    assert payload["transport"] == "pro-attachment-only"
    assert payload["attachments"][1]["sha256"] == state.sha256_file(packet.resolve())


def test_historical_writable_pro_stays_loadable_and_current_pro_is_readonly(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("implement the change", encoding="utf-8")
    config = state.load_manifest(manifest(
        tmp_path,
        mission.resolve(),
        transport="pro-devspace",
        app_name="DevSpace",
        model="gpt-5.6-sol",
        model_strategy="select",
        thinking_time="heavy",
        research="off",
        task_outcome_contract="v1",
    ))
    assert state.is_pro_transport(config.transport)
    assert state.is_devspace_transport(config.transport)
    assert config.attachments == ()
    assert state.composer_prompt(config).startswith(
        f"@DevSpace First open exactly this project root in checkout mode: {tmp_path.resolve()}."
    )
    prompt = state.composer_prompt(config)
    assert f"Then read and execute the mission file: {mission.resolve()}." in prompt
    assert "Do not open the mission directory, a parent, a child" in prompt
    assert prompt.index(str(tmp_path.resolve())) < prompt.index(str(mission.resolve()))
    assert "create, edit, and remove mission-owned files and run commands" in prompt
    assert "Put every citation, footnote, and Markdown reference definition before" in prompt
    assert "as the final nonempty line; append nothing after it." in prompt
    assert prompt.index("as the final nonempty line; append nothing after it.") < prompt.index(
        "Use only the DevSpace app's workspace tools."
    )
    layout = state.create_layout(config, run_id="20260725T151414Z-a3aeba967d99")
    assert state.state_payload(config, layout, status="prepared", resolved_version="oracle 0.17.1")["task_outcome"] == "pending"

    outside = (tmp_path.parent / "outside.md").resolve()
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(state.OracleStateError) as exc:
        state.load_manifest(manifest(
            tmp_path, outside, transport="pro-devspace", app_name="DevSpace",
            model="gpt-5.6-sol", thinking_time="heavy", task_outcome_contract="v1",
        ))
    assert exc.value.code == "MISSION_OUTSIDE_PROJECT"

    with pytest.raises(state.OracleStateError) as exc:
        state.load_manifest(manifest(
            tmp_path, mission.resolve(), transport="pro-devspace", app_name="DevSpace",
            model="gpt-5.6-sol", thinking_time="heavy", task_outcome_contract="legacy",
        ))
    assert exc.value.code == "PRO_DEVSPACE_TASK_OUTCOME_CONTRACT_REQUIRED"

    current = state.load_manifest(manifest(
        tmp_path,
        mission.resolve(),
        transport="pro-devspace-readonly",
        app_name="DevSpace",
        model="gpt-5.6-sol",
        model_strategy="select",
        thinking_time="pro",
        research="off",
        task_outcome_contract="v1",
    ))
    current_prompt = state.composer_prompt(current)
    assert current_prompt.startswith(
        f"@DevSpace First open exactly this project root in checkout mode: {tmp_path.resolve()}."
    )
    assert f"Then read the read-only mission file: {mission.resolve()}." in current_prompt
    assert "Perform read-only work only; do not modify files, settings, accounts, or external state." in current_prompt
    assert "create, edit, and remove mission-owned files and run commands" not in current_prompt


def test_pro_tier_uses_visible_pro_and_parses_legacy_heavy_losslessly(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("read only", encoding="utf-8")

    common = {
        "transport": "pro-devspace-readonly",
        "app_name": "DevSpace",
        "model": "gpt-5.6-sol",
        "model_strategy": "select",
        "research": "off",
        "task_outcome_contract": "v1",
    }
    current = state.load_manifest(manifest(tmp_path, mission.resolve(), thinking_time="pro", **common))
    legacy = state.load_manifest(manifest(tmp_path, mission.resolve(), thinking_time="heavy", **common))

    assert current.thinking_time == "pro"
    assert legacy.thinking_time == "heavy"
    assert state.is_compatible_pro_thinking_time("pro") is True
    # Historical state/receipt validators still recognize the sealed spelling.
    assert state.is_compatible_pro_thinking_time("heavy") is True
    assert state.is_compatible_pro_thinking_time("extra-high") is False
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "oracle-0180-gpt56-sol-effort-menu.json")
        .read_text(encoding="utf-8")
    )
    observed_labels = tuple(item["label"] for item in fixture["items"])
    assert observed_labels == state.VISIBLE_GPT56_SOL_THINKING_TIME_LABELS
    assert "Heavy" not in observed_labels
    assert next(item for item in fixture["items"] if item["ariaChecked"])["label"] == "Extra High"
    assert next(item for item in fixture["items"] if item["label"] == "Pro")["ariaChecked"] is False


def test_missing_new_pro_effort_normalizes_to_visible_pro(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("read only", encoding="utf-8")
    config = state.load_manifest(manifest(
        tmp_path,
        mission.resolve(),
        transport="pro-devspace-readonly",
        app_name="DevSpace",
        model="gpt-5.6-sol",
        model_strategy="select",
        research="off",
        task_outcome_contract="v1",
    ))
    assert config.thinking_time == "pro"


def test_pro_composer_identity_changes_with_project_or_attachment_bytes(tmp_path: Path) -> None:
    state = load_state()
    prompt = tmp_path / "prompt.txt"
    packet = tmp_path / "packet.zip"
    prompt.write_text("instructions", encoding="utf-8")
    packet.write_bytes(b"first")

    def load_for(root: Path):
        root.mkdir(parents=True, exist_ok=True)
        return state.load_manifest(manifest(
            root,
            prompt.resolve(),
            transport="pro-attachment-only",
            app_name=None,
            model="gpt-5.6-sol",
            thinking_time="pro",
            attachments=[str(prompt.resolve()), str(packet.resolve())],
        ))

    first = load_for(tmp_path / "project-one")
    other_project = load_for(tmp_path / "project-two")
    first_prompt = state.composer_prompt(first)
    assert first_prompt != state.composer_prompt(other_project)

    packet.write_bytes(b"second")
    changed_packet = load_for(tmp_path / "project-one")
    assert first_prompt != state.composer_prompt(changed_packet)


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"attachments": []}, "PRO_ATTACHMENTS_REQUIRED"),
        ({"attachments": None}, "PRO_ATTACHMENTS_REQUIRED"),
        ({"attachments": ["missing.txt"]}, "ATTACHMENT_0_ABSOLUTE_REQUIRED"),
        ({"model": "gpt-5.6"}, "PRO_MODEL_INVALID"),
        ({"model_strategy": "current"}, "PRO_MODEL_STRATEGY_INVALID"),
        ({"thinking_time": "extended"}, "PRO_THINKING_TIME_INVALID"),
        ({"research": "deep"}, "PRO_RESEARCH_FORBIDDEN"),
        ({"app_name": "DevSpace"}, "PRO_APP_FORBIDDEN"),
    ],
)
def test_pro_manifest_fails_closed_without_exact_contract(tmp_path: Path, extra: dict, code: str) -> None:
    state = load_state()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("instructions", encoding="utf-8")
    value = {
        "transport": "pro-attachment-only",
        "app_name": None,
        "model": "gpt-5.6-sol",
        "thinking_time": "pro",
        "attachments": [str(prompt.resolve())],
    }
    value.update(extra)
    with pytest.raises(state.OracleStateError) as exc:
        state.load_manifest(manifest(tmp_path, prompt.resolve(), **value))
    assert exc.value.code == code


def test_regular_manifest_accepts_configured_workspace_app(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")

    config = state.load_manifest(manifest(tmp_path, mission.resolve(), app_name="OtherWorkspace"))
    assert config.app_name == "OtherWorkspace"


def test_layout_uses_oracle_exact_ten_character_session_suffix(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    config = state.load_manifest(manifest(tmp_path, mission.resolve()))
    layout = state.create_layout(config, run_id="20260725T151414Z-a3aeba967d99")
    assert layout.slug == "oracle-test-layout-uses-a3aeba967d"


def test_nonempty_output_mutex_and_windows_flags(tmp_path: Path) -> None:
    state = load_state()
    output = tmp_path / "output.md"
    assert state.output_is_nonempty(output) is False
    output.write_text(" \n", encoding="utf-8")
    assert state.output_is_nonempty(output) is False
    output.write_text("answer", encoding="utf-8")
    assert state.output_is_nonempty(output) is True
    assert state.mutex_wait_succeeded(state.WAIT_ABANDONED) is True
    assert state.mutex_wait_succeeded(state.WAIT_TIMEOUT) is False
    assert state.windows_subprocess_kwargs(platform_name="nt")["creationflags"] & state.CREATE_NO_WINDOW


def test_exact_recovery_mutex_is_distinct_from_project_submit_mutex(tmp_path: Path) -> None:
    state = load_state()
    project_root = tmp_path / "project"
    run_dir = tmp_path / "state" / "runs" / ("a" * 32)

    submit_name = state.submit_mutex_name(project_root)
    recovery_name = state.recovery_mutex_name(run_dir)

    assert submit_name.startswith("Local\\codexpro-oracle-submit-")
    assert recovery_name.startswith("Local\\codexpro-oracle-recovery-")
    assert recovery_name != submit_name


def test_task_scoped_owners_do_not_block_other_tasks_and_foreign_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    task_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    task_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    config_a = state.load_manifest(manifest(tmp_path, mission.resolve(), source_thread_id=task_a))
    layout_a = state.create_layout(config_a, run_id="task-a-run-123456")
    layout_a.run_dir.mkdir(parents=True)
    payload = state.state_payload(config_a, layout_a, status="running", resolved_version="0.17.1", cdp_port=43101)
    payload["session_authority"] = "submitted_unknown"
    state.write_json_atomic(layout_a.state_path, payload)

    assert [item["run_id"] for item in state.unresolved_project_sessions(
        config_a.run_root, tmp_path, source_thread_id=task_a
    )] == [layout_a.run_id]
    assert state.unresolved_project_sessions(config_a.run_root, tmp_path, source_thread_id=task_b) == []
    foreign = state.foreign_project_sessions(config_a.run_root, tmp_path, source_thread_id=task_b)
    assert foreign == [{
        "run_id": layout_a.run_id,
        "session_locator": layout_a.slug,
        "session_authority": "submitted_unknown",
        "source_thread_id": task_a,
        "classification": "FOREIGN_TASK_SESSION",
        "state_path": str(layout_a.state_path),
    }]
    assert state.submit_mutex_name(tmp_path, source_thread_id=task_a) != state.submit_mutex_name(
        tmp_path, source_thread_id=task_b
    )


def test_browser_identity_receipt_binds_exact_task_run_profile_port_target_and_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    task_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    config = state.load_manifest(manifest(tmp_path, mission.resolve(), source_thread_id=task_id))
    layout = state.create_layout(config, run_id="identity-run-123456")
    layout.run_dir.mkdir(parents=True)
    layout.browser_temp_path.mkdir()
    state.write_json_atomic(
        layout.state_path,
        state.state_payload(config, layout, status="running", resolved_version="0.17.1", cdp_port=43101),
    )
    state.persist_ownership_receipt(layout.state_path, oracle_process_pid=101)
    session_root = tmp_path / "oracle-sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))
    profile = layout.browser_temp_path / "oracle-browser-isolated"
    meta = {"browser": {"runtime": {
        "chromePid": 102,
        "controllerPid": 101,
        "chromePort": 43101,
        "userDataDir": str(profile),
        "chromeTargetId": "target-exact",
        "tabUrl": "https://chatgpt.com/c/exact-conversation",
    }}}
    meta_path = session_root / layout.slug / "meta.json"
    meta_path.parent.mkdir(parents=True)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    captured = state.capture_browser_identity_receipt(layout.state_path)
    assert captured is not None
    assert captured["payload"]["source_thread_id"] == task_id
    assert captured["payload"]["profile_path"] == str(profile.resolve())
    assert captured["payload"]["cdp_port"] == 43101
    assert captured["payload"]["target_id"] == "target-exact"
    assert captured["payload"]["conversation_url"] == "https://chatgpt.com/c/exact-conversation"
    assert re.fullmatch(r"[a-f0-9]{64}", captured["payload"]["oracle_runtime_identity_sha256"])
    assert state.proven_browser_identity_receipt(layout.state_path) is not None

    terminal_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    terminal_meta["browser"]["runtime"]["promptSubmitted"] = True
    terminal_meta["browser"]["archive"] = {
        "mode": "auto",
        "attempted": True,
        "archived": True,
        "conversationUrl": "https://chatgpt.com/c/exact-conversation",
    }
    meta_path.write_text(json.dumps(terminal_meta), encoding="utf-8")
    assert hashlib.sha256(meta_path.read_bytes()).hexdigest() != captured["payload"]["oracle_meta_sha256"]
    assert state.proven_browser_identity_receipt(layout.state_path) is not None

    terminal_meta["browser"]["runtime"]["chromeTargetId"] = "target-other"
    meta_path.write_text(json.dumps(terminal_meta), encoding="utf-8")
    assert state.proven_browser_identity_receipt(layout.state_path) is None

    terminal_meta["browser"]["runtime"]["chromeTargetId"] = "target-exact"
    meta_path.write_text(json.dumps(terminal_meta), encoding="utf-8")
    assert state.proven_browser_identity_receipt(layout.state_path) is not None

    receipt = state.browser_identity_receipt_path(layout.run_dir)
    receipt.write_text(receipt.read_text(encoding="utf-8").replace("target-exact", "target-other"), encoding="utf-8")
    assert state.proven_browser_identity_receipt(layout.state_path) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX temp alias")
def test_browser_identity_receipt_survives_owned_temp_alias_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    task_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    config = state.load_manifest(manifest(tmp_path, mission.resolve(), source_thread_id=task_id))
    layout = state.create_layout(config, run_id="identity-alias-run-123456")
    layout.run_dir.mkdir(parents=True)
    layout.browser_temp_path.mkdir()
    state.write_json_atomic(
        layout.state_path,
        state.state_payload(config, layout, status="running", resolved_version="0.18.0", cdp_port=43101),
    )
    state.persist_ownership_receipt(layout.state_path, oracle_process_pid=101)
    session_root = tmp_path / "oracle-sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))
    digest = hashlib.sha256(str(layout.browser_temp_path.resolve()).encode("utf-8")).hexdigest()[:16]
    alias = Path("/tmp/Codex") / f"oracle-{os.getuid()}-{digest}" / "t"
    alias.parent.mkdir(mode=0o700, parents=True)
    alias.symlink_to(layout.browser_temp_path.resolve(), target_is_directory=True)
    profile = alias / "oracle-browser-isolated"
    meta = {"browser": {"runtime": {
        "chromePid": 102,
        "controllerPid": 101,
        "chromePort": 43101,
        "userDataDir": str(profile),
        "chromeTargetId": "target-exact",
        "tabUrl": "https://chatgpt.com/c/exact-conversation",
    }}}
    meta_path = session_root / layout.slug / "meta.json"
    meta_path.parent.mkdir(parents=True)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    captured = state.capture_browser_identity_receipt(layout.state_path)
    assert captured is not None
    alias.unlink()
    alias.parent.rmdir()
    assert state.proven_browser_identity_receipt(layout.state_path) is not None

    changed = json.loads(meta_path.read_text(encoding="utf-8"))
    foreign = tmp_path / "foreign" / "browser-temp"
    foreign_digest = hashlib.sha256(str(foreign).encode("utf-8")).hexdigest()[:16]
    changed["browser"]["runtime"]["userDataDir"] = str(
        Path("/tmp/Codex") / f"oracle-{os.getuid()}-{foreign_digest}" / "t" / profile.name
    )
    meta_path.write_text(json.dumps(changed), encoding="utf-8")
    assert state.proven_browser_identity_receipt(layout.state_path) is None

    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    alias.parent.mkdir(mode=0o700)
    alias.symlink_to(foreign, target_is_directory=True)
    try:
        assert state.proven_browser_identity_receipt(layout.state_path) is None
    finally:
        alias.unlink()
        alias.parent.rmdir()


def test_provider_session_terminal_requires_exact_browser_identity_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    task_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    config = state.load_manifest(manifest(tmp_path, mission.resolve(), source_thread_id=task_id))
    layout = state.create_layout(config, run_id="provider-run-123456")
    layout.run_dir.mkdir(parents=True)
    layout.browser_temp_path.mkdir()
    state.write_json_atomic(
        layout.state_path,
        state.state_payload(config, layout, status="running", resolved_version="0.18.0", cdp_port=43101),
    )
    state.persist_ownership_receipt(layout.state_path, oracle_process_pid=101)
    session_root = tmp_path / "oracle-sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))
    profile = layout.browser_temp_path / "oracle-browser-isolated"
    meta = {
        "status": "completed",
        "completedAt": "2026-08-25T12:00:00Z",
        "browser": {
            "runtime": {
                "chromePid": 102,
                "controllerPid": 101,
                "chromePort": 43101,
                "userDataDir": str(profile),
                "chromeTargetId": "target-exact",
                "tabUrl": "https://chatgpt.com/c/exact-conversation",
                "promptSubmitted": True,
            }
        },
    }
    meta_path = session_root / layout.slug / "meta.json"
    meta_path.parent.mkdir(parents=True)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    unbound = state.provider_session_evidence(layout.state_path)
    assert unbound["terminal_confirmed"] is False
    assert unbound["binding"] == "unconfirmed"
    assert unbound["reason"] == "browser-identity-receipt-unavailable"
    assert unbound["observed_conversation_url"] == "https://chatgpt.com/c/exact-conversation"

    assert state.capture_browser_identity_receipt(layout.state_path) is not None
    bound = state.provider_session_evidence(layout.state_path)
    assert bound["terminal_confirmed"] is True
    assert bound["binding"] == "exact-browser-identity-receipt"
    assert bound["reason"] == "oracle-meta-terminal"
    assert bound["bound_conversation_url"] == "https://chatgpt.com/c/exact-conversation"

    meta["status"] = "error"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    browser_error = state.provider_session_evidence(layout.state_path)
    assert browser_error["terminal_confirmed"] is False
    assert browser_error["binding"] == "exact-browser-identity-receipt"
    assert browser_error["reason"] == "oracle-meta-nonterminal"


def test_update_state_can_append_gate_and_provider_evidence_without_status_transition(
    tmp_path: Path,
) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    config = state.load_manifest(manifest(tmp_path, mission.resolve()))
    layout = state.create_layout(config, run_id="metadata-only-123456")
    layout.run_dir.mkdir(parents=True)
    state.write_json_atomic(
        layout.state_path,
        state.state_payload(config, layout, status="prepared", resolved_version="0.18.0", cdp_port=43101),
    )

    updated = state.update_state(
        layout.state_path,
        pro_app_read_gate={"qualified": True, "run_id": "canary"},
        provider_session={"terminal_confirmed": False, "binding": "none"},
    )

    assert updated["status"] == "prepared"
    assert updated["exit_code"] is None
    assert updated["pro_app_read_gate"]["qualified"] is True
    assert updated["provider_session"]["binding"] == "none"


def test_port_mismatch_persists_forensic_candidate_without_granting_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    config = state.load_manifest(manifest(tmp_path, mission.resolve()))
    layout = state.create_layout(config, run_id="mismatch-run-123456")
    layout.run_dir.mkdir(parents=True)
    layout.browser_temp_path.mkdir()
    state.write_json_atomic(
        layout.state_path,
        state.state_payload(config, layout, status="running", resolved_version="0.18.0", cdp_port=43101),
    )
    session_root = tmp_path / "oracle-sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))
    meta_path = session_root / layout.slug / "meta.json"
    meta_path.parent.mkdir(parents=True)
    meta_path.write_text(
        json.dumps(
            {
                "browser": {
                    "runtime": {
                        "chromePid": 102,
                        "controllerPid": 101,
                        "chromePort": 43102,
                        "userDataDir": str(layout.browser_temp_path / "oracle-browser"),
                        "chromeTargetId": "target-candidate",
                        "tabUrl": "https://chatgpt.com/c/candidate-conversation",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert state.capture_browser_identity_receipt(layout.state_path) is None
    stored = state.load_state(layout.state_path)
    assert stored["oracle"]["conversation_url_candidate"] == "https://chatgpt.com/c/candidate-conversation"
    assert stored["browser_identity"]["port_mismatch"]["observed_cdp_port"] == 43102
    assert stored["browser_identity"]["receipt_path"] is None
    assert state.proven_browser_identity_receipt(layout.state_path) is None


def test_run_owned_browser_temp_is_removed_and_prior_boot_orphans_are_swept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = load_state()
    run_root = tmp_path / "runs"
    stale = run_root / "old-run" / "browser-temp"
    live = run_root / "live-run" / "browser-temp"
    monkeypatch.setattr(state, "host_uptime_ms", lambda **kwargs: 500)
    state.browser_temp_environment(stale)
    state.browser_temp_environment(live)
    stale_marker = json.loads((stale / ".owner.json").read_text(encoding="utf-8"))
    stale_marker["host_uptime_ms"] = 900
    state.write_json_atomic(stale / ".owner.json", stale_marker)

    cleaned = state.cleanup_prior_boot_browser_temps(run_root, current_uptime_ms=600)

    assert cleaned == [str(stale.resolve())]
    assert not stale.exists()
    assert live.exists()
    assert state.cleanup_owned_browser_temp(live) is True
    assert not live.exists()


def test_unsafe_oracle_args_are_rejected(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    for unsafe in (
        ["--file", "x"],
        ["restart"],
        ["--browser-tab", "current"],
        ["--followup", "oracle-foreign-parent"],
        ["--browser-follow-up", "oracle-foreign-parent"],
        ["--force"],
        ["--chatgpt-url=https://chatgpt.com/c/foreign"],
    ):
        with pytest.raises(state.OracleStateError) as exc:
            state.load_manifest(manifest(tmp_path, mission.resolve(), oracle_args=unsafe))
        assert exc.value.code == "ORACLE_ARG_FORBIDDEN"
    config = state.load_manifest(
        manifest(
            tmp_path,
            mission.resolve(),
            oracle_args=["--timeout", "45m", "--no-notify", "--heartbeat=20", "--browser-hide-window"],
        )
    )
    assert config.oracle_args == (
        "--timeout",
        "45m",
        "--no-notify",
        "--heartbeat=20",
        "--browser-hide-window",
    )
    assert config.model_strategy == "select"
    assert config.thinking_time == "heavy"
    with pytest.raises(state.OracleStateError) as exc:
        state.load_manifest(manifest(tmp_path, mission.resolve(), thinking_time="xhigh"))
    assert exc.value.code == "THINKING_TIME_INVALID"
    with pytest.raises(state.OracleStateError) as exc:
        state.load_manifest(manifest(tmp_path, mission.resolve(), oracle_command=["powershell", "-Command", "echo unsafe"]))
    assert exc.value.code == "ORACLE_COMMAND_FORBIDDEN"


def test_control_state_must_be_outside_devspace_project(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    with pytest.raises(state.OracleStateError) as exc:
        state.load_manifest(
            manifest(tmp_path, mission.resolve(), run_root=str((tmp_path / ".ai-bridge" / "runs").resolve()))
        )
    assert exc.value.code in {"RUN_ROOT_OUTSIDE_HOST_STATE", "HOST_STATE_OVERLAPS_PROJECT"}
    mission = tmp_path / "mission.md"
    overlap_manifest = manifest(tmp_path, mission.resolve())
    os.environ["CODEX_ORACLE_STATE_ROOT"] = str((tmp_path / "host-state").resolve())
    with pytest.raises(state.OracleStateError) as overlap:
        state.load_manifest(overlap_manifest)
    assert overlap.value.code == "HOST_STATE_OVERLAPS_PROJECT"


def test_default_profile_copy_is_skipped_when_the_copy_dependency_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    seed = tmp_path.parent / f"{tmp_path.name}-oracle-profile"
    seed.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(seed.resolve()))
    monkeypatch.setattr(state.shutil, "which", lambda name: None)

    config = state.load_manifest(manifest(tmp_path, mission.resolve()), platform_name="posix")

    assert config.copy_profile is None


def test_default_profile_copy_is_used_when_the_copy_dependency_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    seed = tmp_path.parent / f"{tmp_path.name}-oracle-profile"
    seed.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(seed.resolve()))
    monkeypatch.setattr(
        state.shutil,
        "which",
        lambda name: "/usr/bin/rsync" if name == state.PROFILE_COPY_DEPENDENCY else None,
    )

    config = state.load_manifest(manifest(tmp_path, mission.resolve()), platform_name="posix")

    assert config.copy_profile == seed.resolve()


def test_windows_profile_copy_needs_no_external_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinned Windows compat patch copies profiles without rsync.

    Requiring rsync on `nt` silently removed per-run profile isolation and
    blocked every parallel Web Multi lane before submission.
    """
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    seed = tmp_path.parent / f"{tmp_path.name}-windows-profile"
    seed.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(seed.resolve()))
    monkeypatch.setattr(state.shutil, "which", lambda name: None)

    assert state.profile_copy_is_supported(platform_name="nt") is True
    assert state.profile_copy_is_supported(platform_name="posix") is False

    default_config = state.load_manifest(
        manifest(tmp_path, mission.resolve()), platform_name="nt"
    )
    assert default_config.copy_profile == seed.resolve()

    explicit = tmp_path.parent / f"{tmp_path.name}-windows-explicit"
    explicit.mkdir(parents=True, exist_ok=True)
    explicit_config = state.load_manifest(
        manifest(tmp_path, mission.resolve(), copy_profile=str(explicit.resolve())),
        platform_name="nt",
    )
    assert explicit_config.copy_profile == explicit.resolve()


def test_explicit_profile_copy_fails_closed_without_the_copy_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    seed = tmp_path.parent / f"{tmp_path.name}-explicit-profile"
    seed.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(state.shutil, "which", lambda name: None)

    with pytest.raises(state.OracleStateError) as exc:
        state.load_manifest(
            manifest(tmp_path, mission.resolve(), copy_profile=str(seed.resolve())),
            platform_name="posix",
        )

    assert exc.value.code == "COPY_PROFILE_DEPENDENCY_MISSING"
    assert exc.value.evidence["dependency"] == state.PROFILE_COPY_DEPENDENCY


def test_lifecycle_vocabulary_is_bounded_to_four_states() -> None:
    state = load_state()

    assert state.LIFECYCLE_STATES == ("running", "complete", "needs_attention", "abandoned")
    assert set(state._STATUS_TO_LIFECYCLE) == state.STATUSES
    assert set(state._STATUS_TO_LIFECYCLE.values()) <= set(state.LIFECYCLE_STATES)


def test_exact_terminal_web_evidence_outranks_stored_artifact_and_ledger(tmp_path: Path) -> None:
    state = load_state()
    output = tmp_path / "output.md"
    output.write_text("answer", encoding="utf-8")

    verdict = state.resolve_lifecycle({
        "status": "failed",
        "session_authority": "terminal",
        "terminal_harvested": True,
        "artifacts": {"output": str(output)},
    })

    assert verdict == {"lifecycle": "complete", "authority_source": "exact-terminal-evidence"}


def test_durable_artifact_outranks_ledger_for_legacy_records(tmp_path: Path) -> None:
    state = load_state()
    output = tmp_path / "output.md"
    output.write_text("legacy answer", encoding="utf-8")

    verdict = state.resolve_lifecycle({
        "status": "complete",
        "session_authority": "",
        "terminal_harvested": False,
        "artifacts": {"output": str(output)},
    })

    assert verdict == {"lifecycle": "complete", "authority_source": "durable-artifact"}


def test_owned_live_session_stays_running_despite_local_failure(tmp_path: Path) -> None:
    state = load_state()

    verdict = state.resolve_lifecycle(
        {
            "status": "failed",
            "session_authority": "submitted_unknown",
            "terminal_harvested": False,
            "artifacts": {"output": str(tmp_path / "missing.md")},
        },
        output_is_present=False,
    )

    assert verdict == {"lifecycle": "running", "authority_source": "exact-session-ownership"}


def test_not_executed_outcome_needs_attention_even_when_terminal(tmp_path: Path) -> None:
    state = load_state()
    output = tmp_path / "output.md"
    output.write_text("TASK_OUTCOME: not_executed", encoding="utf-8")

    verdict = state.resolve_lifecycle({
        "status": "complete",
        "session_authority": "terminal",
        "terminal_harvested": True,
        "task_outcome": "not_executed",
        "artifacts": {"output": str(output)},
    })

    assert verdict["lifecycle"] == "needs_attention"


def test_local_ledger_is_the_lowest_authority(tmp_path: Path) -> None:
    state = load_state()

    running = state.resolve_lifecycle({"status": "prepared"}, output_is_present=False)
    failed = state.resolve_lifecycle({"status": "failed"}, output_is_present=False)
    abandoned = state.resolve_lifecycle({"status": "abandoned"}, output_is_present=False)

    assert running == {"lifecycle": "running", "authority_source": "local-ledger"}
    assert failed == {"lifecycle": "needs_attention", "authority_source": "local-ledger"}
    assert abandoned == {"lifecycle": "abandoned", "authority_source": "explicit-abandonment"}


def test_abandoned_is_a_valid_persisted_status(tmp_path: Path) -> None:
    state = load_state()

    assert "abandoned" in state.STATUSES


def test_ledger_completion_without_a_durable_artifact_is_not_complete() -> None:
    state = load_state()

    verdict = state.resolve_lifecycle({"status": "complete"}, output_is_present=False)

    assert verdict == {"lifecycle": "needs_attention", "authority_source": "local-ledger"}


def test_connector_identity_guard_binds_the_named_app() -> None:
    state = load_state()

    guard = state.connector_identity_guard("codex")

    assert "codex" in guard
    assert "never substitute another plugin's connector" in guard


def test_connector_identity_guard_omits_blank_app_names() -> None:
    state = load_state()

    assert state.connector_identity_guard("") == ""
    assert state.connector_identity_guard(" \t ") == ""


@pytest.mark.parametrize("app_name", ["dongju", "my-app"])
def test_connector_identity_guard_interpolates_arbitrary_app_names(app_name: str) -> None:
    state = load_state()

    assert app_name in state.connector_identity_guard(app_name)


@pytest.mark.parametrize(
    ("transport", "extra"),
    [
        (
            "pro-devspace",
            {
                "model": "gpt-5.6-sol",
                "model_strategy": "select",
                "thinking_time": "pro",
                "research": "off",
                "task_outcome_contract": "v1",
            },
        ),
        (
            "pro-devspace-readonly",
            {
                "model": "gpt-5.6-sol",
                "model_strategy": "select",
                "thinking_time": "pro",
                "research": "off",
                "task_outcome_contract": "v1",
            },
        ),
        ("devspace", {}),
    ],
)
def test_composer_prompt_includes_connector_identity_guard_for_workspace_transports(
    tmp_path: Path,
    transport: str,
    extra: dict,
) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    config = state.load_manifest(
        manifest(
            tmp_path,
            mission.resolve(),
            transport=transport,
            app_name="dongju",
            **extra,
        )
    )

    prompt = state.composer_prompt(config)

    assert state.connector_identity_guard(config.app_name) in prompt
    assert prompt.startswith(f"@{config.app_name}")


def test_attachment_prompt_omits_connector_identity_guard(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    config = state.load_manifest(
        manifest(
            tmp_path,
            mission.resolve(),
            transport="pro-attachment-only",
            app_name=None,
            model="gpt-5.6-sol",
            thinking_time="pro",
            attachments=[str(mission.resolve())],
        )
    )

    prompt = state.composer_prompt(config)

    assert "never substitute another plugin's connector" not in prompt


def test_composer_prompt_combines_connector_and_self_observation_guards(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    config = state.load_manifest(manifest(tmp_path, mission.resolve()))

    prompt = state.composer_prompt(config, run_id="run-123", slug="oracle-test-run")

    assert state.connector_identity_guard(config.app_name) in prompt
    assert "run run-123 or slug oracle-test-run" in prompt


def test_pro_devspace_connector_guard_prompt_stays_single_line(tmp_path: Path) -> None:
    state = load_state()
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    config = state.load_manifest(
        manifest(
            tmp_path,
            mission.resolve(),
            transport="pro-devspace",
            model="gpt-5.6-sol",
            model_strategy="select",
            thinking_time="pro",
            research="off",
            task_outcome_contract="v1",
        )
    )

    prompt = state.composer_prompt(config)

    assert "\n" not in prompt
    assert "\r" not in prompt


def browser_session_absent_run(
    tmp_path: Path,
    state,
    *,
    stdout_text: str = (
        "ERROR: ChatGPT session not detected. Login button detected on page.\n"
        "No ChatGPT cookies were applied\n"
    ),
) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    mission = project_root / "mission.md"
    mission.write_text("perform no work", encoding="utf-8")
    manifest_path = manifest(project_root, mission.resolve())
    host_state = tmp_path / "h"
    os.environ["CODEX_ORACLE_STATE_ROOT"] = str(host_state.resolve())
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["run_root"] = str((host_state / "r").resolve())
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    config = state.load_manifest(manifest_path)
    layout = state.create_layout(config, run_id="run-1234")
    layout.run_dir.mkdir(parents=True)
    transport_mission = layout.run_dir / "mission.md"
    transport_mission.write_bytes(mission.read_bytes())
    layout.stdout_path.write_text(stdout_text, encoding="utf-8")

    payload = state.state_payload(
        config, layout, status="failed", resolved_version="0.17.1", exit_code=1
    )
    payload["transport_status"] = "failed"
    payload["session_authority"] = "submitted_unknown"
    payload["terminal_harvested"] = False
    state.write_json_atomic(layout.state_path, payload)
    return layout.state_path, layout.run_dir


def test_browser_session_absent_pre_submit_evidence_is_hash_bound(
    tmp_path: Path,
) -> None:
    state = load_state()
    state_path, run_dir = browser_session_absent_run(tmp_path, state)

    evidence = state._browser_session_absent_no_submission_evidence(state_path)

    assert evidence is not None
    assert evidence["settlement_eligibility"] == (
        "oracle-browser-session-absent-pre-submit/v1"
    )
    assert evidence["browser_session_absent"] is True
    assert evidence["output_absent"] is True
    assert evidence["conversation_url_absent"] is True
    assert evidence["stdout_sha256"] == state.sha256_file(run_dir / "stdout.log")


def test_user_confirmable_evidence_includes_browser_session_absent_detector(
    tmp_path: Path,
) -> None:
    state = load_state()
    state_path, _ = browser_session_absent_run(tmp_path, state)

    direct = state._browser_session_absent_no_submission_evidence(state_path)
    adjudicated = state._user_confirmable_no_submission_evidence(state_path)

    assert direct is not None
    assert adjudicated == direct


@pytest.mark.parametrize(
    "rejection",
    [
        "missing-session-marker",
        "missing-cookies-marker",
        "conversation-url-in-stdout",
        "transport-status",
        "session-authority",
        "terminal-harvested",
        "output-present",
        "mission-sha256-mismatch",
        "mission-transport-outside-run",
        "empty-oracle-locator",
        "missing-project-root",
        "conversation-url-in-recovery",
    ],
)
def test_browser_session_absent_pre_submit_rejects_inconsistent_evidence(
    tmp_path: Path,
    rejection: str,
) -> None:
    state = load_state()
    state_path, run_dir = browser_session_absent_run(tmp_path, state)
    payload = state.load_state(state_path)

    if rejection == "missing-session-marker":
        (run_dir / "stdout.log").write_text(
            "No ChatGPT cookies were applied\n", encoding="utf-8"
        )
    elif rejection == "missing-cookies-marker":
        (run_dir / "stdout.log").write_text(
            "ERROR: ChatGPT session not detected. Login button detected on page.\n",
            encoding="utf-8",
        )
    elif rejection == "conversation-url-in-stdout":
        (run_dir / "stdout.log").write_text(
            "ERROR: ChatGPT session not detected. Login button detected on page.\n"
            "No ChatGPT cookies were applied\n"
            "https://chatgpt.com/c/conversation-may-exist\n",
            encoding="utf-8",
        )
    elif rejection == "transport-status":
        payload["transport_status"] = "prepared"
    elif rejection == "session-authority":
        # `pre_submit` is a legitimate post-settlement authority, so the
        # rejection case must use one that implies a reachable provider.
        payload["session_authority"] = "terminal"
    elif rejection == "terminal-harvested":
        payload["terminal_harvested"] = True
    elif rejection == "output-present":
        (run_dir / "output.md").write_text("provider output", encoding="utf-8")
    elif rejection == "mission-sha256-mismatch":
        payload["mission"]["sha256"] = "0" * 64
    elif rejection == "mission-transport-outside-run":
        outside = tmp_path / "outside-mission.md"
        outside.write_text("perform no work", encoding="utf-8")
        payload["mission"]["transport_path"] = str(outside.resolve())
    elif rejection == "empty-oracle-locator":
        payload["oracle"]["session_locator"] = ""
        payload["oracle"]["slug"] = ""
    elif rejection == "missing-project-root":
        payload["project_root"] = str((tmp_path / "missing-project").resolve())
    elif rejection == "conversation-url-in-recovery":
        (run_dir / "recovery-20260822-stdout.log").write_text(
            "https://chatgpt.com/c/conversation-may-exist\n", encoding="utf-8"
        )
    else:
        raise AssertionError(f"unexpected rejection case: {rejection}")
    state.write_json_atomic(state_path, payload)

    assert state._browser_session_absent_no_submission_evidence(state_path) is None


def test_browser_session_absent_markers_are_case_insensitive(tmp_path: Path) -> None:
    state = load_state()
    stdout = (
        "error: cHATgpt SESSION NOT DETECTED. lOGIN BUTTON DETECTED ON PAGE.\n"
        "nO cHATgpt COOKIES WERE APPLIED\n"
    )
    state_path, _ = browser_session_absent_run(tmp_path, state, stdout_text=stdout)

    evidence = state._browser_session_absent_no_submission_evidence(state_path)

    assert evidence is not None
    assert evidence["browser_session_absent"] is True


@pytest.mark.parametrize(
    ("transport_status", "session_authority"),
    [
        ("failed", "submitted_unknown"),
        ("not_submitted_user_confirmed", "pre_submit"),
    ],
)
def test_browser_session_absent_evidence_accepts_initial_and_settled_states(
    tmp_path: Path,
    transport_status: str,
    session_authority: str,
) -> None:
    state = load_state()
    state_path, _ = browser_session_absent_run(tmp_path, state)
    payload = state.load_state(state_path)
    payload["transport_status"] = transport_status
    payload["session_authority"] = session_authority
    state.write_json_atomic(state_path, payload)

    evidence = state._browser_session_absent_no_submission_evidence(state_path)

    assert evidence is not None
    assert evidence["settlement_eligibility"] == (
        "oracle-browser-session-absent-pre-submit/v1"
    )


@pytest.mark.parametrize(
    ("transport_status", "session_authority"),
    [
        ("complete", "pre_submit"),
        ("failed", "terminal"),
        ("not_submitted_user_confirmed", "terminal"),
    ],
)
def test_browser_session_absent_evidence_rejects_other_settlement_states(
    tmp_path: Path,
    transport_status: str,
    session_authority: str,
) -> None:
    state = load_state()
    state_path, _ = browser_session_absent_run(tmp_path, state)
    payload = state.load_state(state_path)
    payload["transport_status"] = transport_status
    payload["session_authority"] = session_authority
    state.write_json_atomic(state_path, payload)

    assert state._browser_session_absent_no_submission_evidence(state_path) is None


def test_browser_session_absent_settlement_round_trip_revalidates(
    tmp_path: Path,
) -> None:
    state = load_state()
    state_path, _ = browser_session_absent_run(tmp_path, state)

    settled = state.settle_user_confirmed_no_submission(
        state_path,
        confirmation=state.USER_CONFIRMED_NO_SUBMISSION,
        reason="browser profile had no ChatGPT session before the composer",
    )

    settlement_path = state_path.parent / "user-confirmed-no-submission.json"
    assert settlement_path.is_file()
    assert settled["user_confirmed_no_submission"]["path"] == str(settlement_path)
    assert state.proven_user_confirmed_no_submission(state_path)


def test_browser_session_absent_settlement_revalidation_rejects_recovery_url(
    tmp_path: Path,
) -> None:
    state = load_state()
    state_path, run_dir = browser_session_absent_run(tmp_path, state)
    state.settle_user_confirmed_no_submission(
        state_path,
        confirmation=state.USER_CONFIRMED_NO_SUBMISSION,
        reason="browser profile had no ChatGPT session before the composer",
    )
    (run_dir / "recovery-20260822-stdout.log").write_text(
        "https://chatgpt.com/c/conversation-invalidates-settlement\n",
        encoding="utf-8",
    )

    assert state.proven_user_confirmed_no_submission(state_path) is None


def test_browser_session_absent_settlement_releases_project_ownership(
    tmp_path: Path,
) -> None:
    state = load_state()
    state_path, _ = browser_session_absent_run(tmp_path, state)
    payload = state.load_state(state_path)
    run_root = state_path.parent.parent
    project_root = Path(payload["project_root"])

    before_settlement = state.unresolved_project_sessions(run_root, project_root)
    state.settle_user_confirmed_no_submission(
        state_path,
        confirmation=state.USER_CONFIRMED_NO_SUBMISSION,
        reason="browser profile had no ChatGPT session before the composer",
    )
    after_settlement = state.unresolved_project_sessions(run_root, project_root)

    assert [owner["run_id"] for owner in before_settlement] == ["run-1234"]
    assert after_settlement == []
