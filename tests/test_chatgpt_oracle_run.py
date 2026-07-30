from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

RUNNER_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_run.py"


def load_runner():
    name = "chatgpt_oracle_run_test"
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def manifest(tmp_path: Path, **extra) -> Path:
    mission = tmp_path / "mission.md"
    mission.write_text("finish", encoding="utf-8")
    path = tmp_path / "job.json"
    payload = {
        "schema": "codex.chatgpt.oracle-run/v1",
        "project_root": str(tmp_path.resolve()),
        "mission_path": str(mission.resolve()),
        "app_name": "DevSpace",
        "mode": "browser",
        "run_root": str((tmp_path.parent / f"{tmp_path.name}-host-state" / "runs").resolve()),
        "oracle_command": ["oracle"],
    }
    payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.environ["CODEX_ORACLE_STATE_ROOT"] = str((tmp_path.parent / f"{tmp_path.name}-host-state").resolve())
    return path.resolve()


def pro_manifest(tmp_path: Path, **extra) -> Path:
    prompt = tmp_path / "prompt.txt"
    packet = tmp_path / "packet.zip"
    prompt.write_text("pro instructions", encoding="utf-8")
    packet.write_bytes(b"PK\x03\x04packet")
    return manifest(
        tmp_path,
        transport="pro-attachment-only",
        app_name=None,
        model="pro",
        model_strategy="select",
        thinking_time="heavy",
        attachments=[str(prompt.resolve()), str(packet.resolve())],
        mission_path=str(prompt.resolve()),
        **extra,
    )


def version_runner(command, **kwargs):
    return subprocess.CompletedProcess(command, 0, stdout="oracle 0.13.0\n", stderr="")


def execute_run(runner, *args, **kwargs):
    kwargs.setdefault("compat_factory", lambda version: {"ok": True, "version": version})
    kwargs.setdefault(
        "devspace_compat_factory",
        lambda: {"ok": True, "changed": [], "service_restart_required": False},
    )
    return runner.execute_run(*args, **kwargs)


class Process:
    def __init__(self, code: int, events: list[str]):
        self.code = code
        self.events = events

    def wait(self):
        self.events.append("wait")
        return self.code


def popen_for(code: int, output: bytes | None, captured: dict, events: list[str]):
    def popen(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        events.append("popen")
        if output is not None:
            Path(command[command.index("--write-output") + 1]).write_bytes(output)
        kwargs["stdout"].write(b"stdout\n")
        kwargs["stdout"].flush()
        return Process(code, events)
    return popen


def duplicate_prompt_popen(command, **kwargs):
    kwargs["stdout"].write(
        b'oracle 0.16.1\nA session with the same prompt is already running '
        b'(oracle-global-agent-instructio-f39cc47ba5). Reattach with '
        b'"oracle session oracle-global-agent-instructio-f39cc47ba5" or rerun with '
        b'--force to start another run.\n'
    )
    kwargs["stdout"].flush()
    return Process(1, [])


def test_dry_run_never_executes_and_has_no_file_flag(tmp_path: Path) -> None:
    runner = load_runner()
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError
    result = execute_run(runner, manifest(tmp_path), dry_run=True, run_factory=forbidden, popen_factory=forbidden)
    assert result["ok"] is True
    assert result["prompt_first_line"].startswith("@DevSpace ")
    assert str((tmp_path / "mission.md").resolve()) in result["prompt_first_line"]
    assert result["mission_sha256"]
    assert Path(result["mission_path"]).is_absolute()
    assert str((tmp_path / "mission.md").resolve()) in result["argv"][result["argv"].index("--prompt") + 1]
    assert "--file" not in result["argv"]
    assert result["argv"][result["argv"].index("--browser-model-strategy") + 1] == "select"
    assert result["argv"][result["argv"].index("--browser-thinking-time") + 1] == "heavy"
    assert result["argv"].count("--browser-hide-window") == 1
    assert calls == []
    assert not (tmp_path / "runs").exists()


def test_copy_profile_is_first_class_and_outside_project(
    tmp_path: Path, monkeypatch
) -> None:
    runner = load_runner()
    profile = tmp_path.parent / f"{tmp_path.name}-oracle-profile"
    profile.mkdir()
    # Profile copying depends on rsync, which is absent on many Windows hosts.
    # Pin the dependency so this argv contract stays deterministic.
    monkeypatch.setattr(
        runner.STATE.shutil,
        "which",
        lambda name: "/usr/bin/rsync" if name == runner.STATE.PROFILE_COPY_DEPENDENCY else None,
    )
    result = execute_run(runner, manifest(tmp_path, copy_profile=str(profile.resolve())), dry_run=True)
    assert result["argv"][result["argv"].index("--copy-profile") + 1] == str(profile.resolve())


def test_default_signed_in_profile_is_copied_per_run_and_window_is_hidden(
    tmp_path: Path, monkeypatch
) -> None:
    runner = load_runner()
    profile = tmp_path.parent / f"{tmp_path.name}-signed-in-oracle-profile"
    profile.mkdir()
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(profile.resolve()))
    monkeypatch.setattr(
        runner.STATE.shutil,
        "which",
        lambda name: "/usr/bin/rsync" if name == runner.STATE.PROFILE_COPY_DEPENDENCY else None,
    )

    result = execute_run(runner, manifest(tmp_path), dry_run=True)

    assert result["argv"][result["argv"].index("--copy-profile") + 1] == str(profile.resolve())
    assert result["argv"].count("--browser-hide-window") == 1


def test_missing_copy_dependency_still_launches_without_profile_copy(
    tmp_path: Path, monkeypatch
) -> None:
    runner = load_runner()
    profile = tmp_path.parent / f"{tmp_path.name}-signed-in-oracle-profile"
    profile.mkdir()
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(profile.resolve()))
    monkeypatch.setattr(runner.STATE, "profile_copy_is_supported", lambda: False)

    result = execute_run(runner, manifest(tmp_path), dry_run=True)

    assert "--copy-profile" not in result["argv"]
    assert result["argv"].count("--browser-hide-window") == 1


def test_explicit_hide_window_arg_is_safe_and_not_duplicated(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path, oracle_args=["--browser-hide-window"]),
        dry_run=True,
    )
    assert result["argv"].count("--browser-hide-window") == 1


def test_pro_dry_run_uses_oracle_attachments_and_no_app_mention(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(runner, pro_manifest(tmp_path), dry_run=True)
    argv = result["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    attachments = [argv[index + 1] for index, value in enumerate(argv) if value == "--file"]
    assert result["transport"] == "pro-attachment-only"
    assert result["contains_file_flag"] is True
    assert argv[argv.index("--model") + 1] == "pro"
    assert argv[argv.index("--browser-attachments") + 1] == "always"
    assert attachments == [
        str((tmp_path / "prompt.txt").resolve()),
        str((tmp_path / "packet.zip").resolve()),
    ]
    assert prompt.startswith(
        "Read the attached prompt/instructions and all attached files, then complete the task. "
        "Task identity: oracle-pro-"
    )
    assert prompt.endswith(".")
    assert "@DevSpace" not in prompt
    assert all(item["sha256"] for item in result["attachments"])


def test_complete_requires_zero_exit_and_nonempty_output(tmp_path: Path) -> None:
    runner = load_runner()
    cases = [
        (0, b"answer", "complete", True),
        (0, b" \n", "attention_required", False),
        (3, b"answer", "attention_required", False),
    ]
    for index, (code, output, status, ok) in enumerate(cases):
        root = tmp_path / str(index)
        root.mkdir()
        captured, events = {}, []
        result = execute_run(runner, manifest(root), run_factory=version_runner, popen_factory=popen_for(code, output, captured, events))
        assert result["ok"] is ok
        assert result["result"]["status"] == status
        assert result["result"]["oracle"]["resolved_version"] == "oracle 0.13.0"
        assert "--file" not in captured["command"]
        assert events == ["popen", "wait"]
        assert Path(result["result"]["artifacts"]["transcript"]).is_file()


def test_v1_task_outcome_separates_transport_success_from_execution(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    (tmp_path / "executed").mkdir()
    (tmp_path / "not-executed").mkdir()
    executed = execute_run(
        runner,
        manifest(
            tmp_path / "executed",
            task_outcome_contract="v1",
            run_id="e" * 32,
        ),
        run_factory=version_runner,
        popen_factory=popen_for(0, b"done\nTASK_OUTCOME: EXECUTED\n", {}, []),
    )
    not_executed = execute_run(
        runner,
        manifest(
            tmp_path / "not-executed",
            task_outcome_contract="v1",
            run_id="n" * 32,
        ),
        run_factory=version_runner,
        popen_factory=popen_for(
            0,
            b"workspace open timed out\nTASK_OUTCOME: NOT_EXECUTED\n",
            {},
            [],
        ),
    )

    assert executed["ok"] is True
    assert executed["result"]["status"] == "complete"
    assert executed["result"]["transport_status"] == "complete"
    assert executed["result"]["task_outcome"] == "executed"
    assert not_executed["ok"] is False
    assert not_executed["result"]["status"] == "attention_required"
    assert not_executed["result"]["transport_status"] == "complete"
    assert not_executed["result"]["task_outcome"] == "not_executed"
    assert not_executed["result"]["session_authority"] == "terminal"
    assert not_executed["result"]["terminal_harvested"] is True


def test_v1_missing_task_outcome_marker_never_claims_execution(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path, task_outcome_contract="v1"),
        run_factory=version_runner,
        popen_factory=popen_for(0, b"nonempty but semantically ambiguous", {}, []),
    )

    assert result["ok"] is False
    assert result["result"]["status"] == "attention_required"
    assert result["result"]["transport_status"] == "complete"
    assert result["result"]["task_outcome"] == "unknown"


def test_v1_task_outcome_marker_must_be_the_final_nonempty_line(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path, task_outcome_contract="v1"),
        run_factory=version_runner,
        popen_factory=popen_for(
            0,
            b"TASK_OUTCOME: EXECUTED\nActually no files were changed.\n",
            {},
            [],
        ),
    )

    assert result["ok"] is False
    assert result["result"]["task_outcome"] == "unknown"


def test_devspace_patch_change_blocks_before_submission_until_restart(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    launched = []
    result = runner.execute_run(
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=lambda *args, **kwargs: launched.append(True),
        compat_factory=lambda version: {"ok": True, "version": version},
        devspace_compat_factory=lambda: {
            "ok": True,
            "changed": ["dist/workspaces.js"],
            "package_roots": ["package"],
            "service_restart_required": True,
        },
    )

    assert result["ok"] is False
    assert result["result"]["status"] == "failed"
    assert launched == []
    stderr = Path(result["result"]["artifacts"]["stderr"]).read_text(encoding="utf-8")
    assert "DEVSPACE_SERVICE_RESTART_REQUIRED" in stderr


def test_exact_output_hash_adjudication_marks_legacy_task_not_executed(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    completed = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(0, b"workspace timeout; no files changed", {}, []),
    )
    run_dir = Path(completed["run_dir"])
    output = run_dir / "output.md"
    adjudicated = runner.adjudicate_task_outcome(
        run_dir,
        expected_output_sha256=runner.STATE.sha256_file(output),
        task_outcome="not_executed",
        reason="exact output proves workspace open timeout before file reads",
    )

    assert adjudicated["ok"] is False
    assert adjudicated["safe_for_fresh_retry"] is True
    assert adjudicated["task_outcome"] == "not_executed"
    assert adjudicated["result"]["status"] == "complete"
    assert adjudicated["result"]["transport_status"] == "complete"
    assert adjudicated["result"]["session_authority"] == "terminal"


def test_blocked_adjudication_never_authorizes_fresh_retry(tmp_path: Path) -> None:
    runner = load_runner()
    completed = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(0, b"partial work then blocked", {}, []),
    )
    run_dir = Path(completed["run_dir"])
    output = run_dir / "output.md"
    adjudicated = runner.adjudicate_task_outcome(
        run_dir,
        expected_output_sha256=runner.STATE.sha256_file(output),
        task_outcome="blocked",
        reason="partial execution cannot authorize duplicate side effects",
    )

    assert adjudicated["safe_for_fresh_retry"] is False


def test_post_submit_nonzero_requires_exact_recovery_and_never_restarts(tmp_path: Path) -> None:
    runner = load_runner()
    calls = []
    def popen(command, **kwargs):
        calls.append(list(command))
        return Process(9, [])
    result = execute_run(runner, manifest(tmp_path), run_factory=version_runner, popen_factory=popen)
    assert result["result"]["status"] == "attention_required"
    assert result["result"]["session_authority"] == "submitted_unknown"
    assert len(calls) == 1
    assert "restart" not in calls[0]
    for action in ("harvest", "live"):
        recovery = runner.recover_run(Path(result["run_dir"]), action=action, dry_run=True, oracle_command=["oracle"])
        assert f"--{action}" in recovery["argv"]
        assert "--write-output" in recovery["argv"]
        assert "--no-recover" not in recovery["argv"]
        assert "restart" not in recovery["argv"]
        assert "--prompt" not in recovery["argv"]


def test_pro_recovery_uses_exact_slug_without_attachments_or_resubmit(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        pro_manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(4, None, {}, []),
    )
    state = runner.STATE.load_state(Path(result["run_dir"]) / "state.json")
    recovery = runner.recover_run(
        Path(result["run_dir"]),
        action="harvest",
        dry_run=True,
        oracle_command=["oracle"],
    )
    argv = recovery["argv"]
    assert argv[argv.index("session") + 1] == state["oracle"]["slug"]
    assert "--prompt" not in argv
    assert "--file" not in argv
    assert "--browser-attachments" not in argv
    assert "--no-recover" not in argv


def test_windows_launch_uses_no_window_and_waits(tmp_path: Path) -> None:
    runner = load_runner()
    captured, events = {}, []
    class Mutex:
        def __enter__(self):
            events.append("enter")
        def __exit__(self, *args):
            events.append("exit")
    runner.STATE.project_submit_mutex = lambda *args, **kwargs: Mutex()
    result = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(0, b"answer", captured, events),
        platform_name="nt",
    )
    assert result["ok"] is True
    assert captured["kwargs"]["creationflags"] & runner.STATE.CREATE_NO_WINDOW
    assert Path(captured["kwargs"]["env"]["TEMP"]).name == "browser-temp"
    assert captured["kwargs"]["env"]["TMP"] == captured["kwargs"]["env"]["TEMP"]
    assert not Path(captured["kwargs"]["env"]["TEMP"]).exists()
    assert events == ["enter", "popen", "wait", "exit"]


def test_transport_mission_change_blocks_before_oracle_launch(tmp_path: Path) -> None:
    runner = load_runner()
    launched = []

    class MutatingMutex:
        def __enter__(self):
            transport = next((tmp_path / "runs").glob("*/mission.md"))
            transport.write_text("changed", encoding="utf-8")

        def __exit__(self, *args):
            return None

    runner.STATE.project_submit_mutex = lambda *args, **kwargs: MutatingMutex()

    def forbidden_popen(*args, **kwargs):
        launched.append(True)
        raise AssertionError("Oracle must not launch with changed mission bytes")

    result = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=forbidden_popen,
    )
    assert result["ok"] is False
    assert result["result"]["status"] == "failed"
    assert launched == []


def test_pro_attachment_change_blocks_before_submit(tmp_path: Path) -> None:
    runner = load_runner()
    launched = []

    class MutatingMutex:
        def __enter__(self):
            (tmp_path / "packet.zip").write_bytes(b"changed")

        def __exit__(self, *args):
            return None

    runner.STATE.project_submit_mutex = lambda *args, **kwargs: MutatingMutex()

    def forbidden_popen(*args, **kwargs):
        launched.append(True)
        raise AssertionError("Oracle must not launch with changed attachments")

    result = execute_run(
        runner,
        pro_manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=forbidden_popen,
    )
    assert result["ok"] is False
    assert result["result"]["status"] == "failed"
    assert result["result"]["session_authority"] == "pre_submit"
    assert launched == []


def test_oracle_global_prompt_duplicate_is_proven_pre_submit_and_releases_project(tmp_path: Path) -> None:
    runner = load_runner()
    first = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="a" * 32),
        run_factory=version_runner,
        popen_factory=duplicate_prompt_popen,
    )
    first_state = runner.STATE.load_state(Path(first["run_dir"]) / "state.json")
    assert first["status"] == "pre_submit_rejected"
    assert first["safe_for_fresh_run"] is True
    assert first_state["session_authority"] == "pre_submit"
    assert first_state["transport_status"] == "rejected_pre_submit"
    assert first_state["pre_submit_rejection"]["code"] == "ORACLE_GLOBAL_PROMPT_DUPLICATE"
    assert first_state["pre_submit_rejection"]["output_absent"] is True
    assert runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(pro_manifest(tmp_path)).run_root,
        tmp_path,
    ) == []

    launches: list[list[str]] = []
    second = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="b" * 32),
        run_factory=version_runner,
        popen_factory=popen_for(0, b"answer", {}, launches),
    )
    assert second["ok"] is True
    assert launches


def test_recovery_settles_legacy_duplicate_prompt_lock_without_oracle_call(tmp_path: Path) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="a" * 32),
        run_factory=version_runner,
        popen_factory=duplicate_prompt_popen,
    )
    run_dir = Path(initial["run_dir"])
    state_path = run_dir / "state.json"
    legacy = json.loads(state_path.read_text(encoding="utf-8"))
    legacy["session_authority"] = "submitted_unknown"
    legacy["transport_status"] = "incomplete"
    legacy.pop("pre_submit_rejection", None)
    state_path.write_text(json.dumps(legacy), encoding="utf-8")
    calls = []

    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=lambda *args, **kwargs: calls.append(True),
    )
    settled = runner.STATE.load_state(state_path)
    assert recovered["status"] == "pre_submit_rejected"
    assert recovered["safe_for_fresh_run"] is True
    assert settled["session_authority"] == "pre_submit"
    assert calls == []


def test_recovery_captures_output_and_updates_state(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(4, None, {}, []),
    )
    run_dir = Path(result["run_dir"])

    def recovery_popen(command, **kwargs):
        captured_env.update(kwargs["env"])
        output = Path(command[command.index("--write-output") + 1])
        output.write_text("recovered answer", encoding="utf-8")
        kwargs["stdout"].write(b"State: complete\n")
        kwargs["stdout"].flush()
        return Process(0, [])

    captured_env = {}
    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=recovery_popen,
    )
    assert recovered["ok"] is True
    assert recovered["status"] == "complete"
    assert Path(recovered["output_path"]).read_text(encoding="utf-8") == "recovered answer"
    assert recovered["result"]["status"] == "complete"
    assert Path(captured_env["TEMP"]).name == "recovery-harvest-browser-temp"
    assert not Path(captured_env["TEMP"]).exists()
    transcript = Path(recovered["result"]["artifacts"]["transcript"]).read_text(encoding="utf-8")
    assert "recovered answer" in transcript


def test_running_exact_session_cannot_publish_partial_harvest(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(0, None, {}, []),
    )
    run_dir = Path(result["run_dir"])

    def live_harvest(command, **kwargs):
        candidate = Path(command[command.index("--write-output") + 1])
        candidate.write_text("partial answer still flushing", encoding="utf-8")
        kwargs["stdout"].write(b"State: running\nSignals: stop=yes send=no\n")
        kwargs["stdout"].flush()
        return Process(0, [])

    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=live_harvest,
    )

    state = runner.STATE.load_state(run_dir / "state.json")
    assert recovered["status"] == "session_live"
    assert recovered["ok"] is False
    assert state["session_authority"] == "live"
    assert state["terminal_harvested"] is False
    assert not Path(state["artifacts"]["output"]).exists()
    assert not (run_dir / "recovery-harvest-candidate.md").exists()


def test_terminal_observation_cannot_regress_to_live_and_later_harvest_settles(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(0, None, {}, []),
    )
    run_dir = Path(result["run_dir"])

    def observation(state: str, answer: str | None = None):
        def popen(command, **kwargs):
            if answer is not None:
                Path(command[command.index("--write-output") + 1]).write_text(answer, encoding="utf-8")
            kwargs["stdout"].write(f"State: {state}\n".encode())
            kwargs["stdout"].flush()
            return Process(0, [])
        return popen

    terminal = runner.recover_run(
        run_dir,
        action="live",
        oracle_command=["oracle"],
        popen_factory=observation("completed"),
    )
    # Reproduce state already regressed by the previously installed runner;
    # the durable exact live-observer log must restore terminal authority.
    regressed = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    regressed["status"] = "running"
    regressed["session_authority"] = "live"
    (run_dir / "state.json").write_text(json.dumps(regressed), encoding="utf-8")
    disagreement = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=observation("running", "partial"),
    )
    output_absent_during_disagreement = not Path(
        disagreement["result"]["artifacts"]["output"]
    ).exists()
    duplicate_launches: list[list[str]] = []
    blocked_duplicate = execute_run(
        runner,
        manifest(tmp_path, run_id="b" * 32),
        run_factory=version_runner,
        popen_factory=popen_for(0, b"duplicate", {}, duplicate_launches),
    )
    settled = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=observation("completed", "durable answer"),
    )

    assert terminal["status"] == "terminal_observed"
    assert terminal["result"]["session_authority"] == "terminal_observed"
    assert disagreement["status"] == "terminal_settle_disagreement"
    assert disagreement["result"]["status"] == "attention_required"
    assert disagreement["result"]["session_authority"] == "terminal_observed"
    assert disagreement["result"]["terminal_harvested"] is False
    assert output_absent_during_disagreement
    assert blocked_duplicate["ok"] is False
    assert duplicate_launches == []
    assert "still owns this project" in Path(
        blocked_duplicate["result"]["artifacts"]["stderr"]
    ).read_text(encoding="utf-8")
    assert settled["ok"] is True
    assert settled["status"] == "complete"
    assert settled["result"]["session_authority"] == "terminal"
    assert Path(settled["output_path"]).read_text(encoding="utf-8") == "durable answer"


def test_live_recovery_settles_stalled_inside_one_exact_slug_process(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(7, None, {}, []),
    )
    run_dir = Path(initial["run_dir"])
    runner.STATE.update_state(
        run_dir / "state.json",
        status="running",
        exit_code=7,
        session_authority="submitted_unknown",
    )
    calls: list[str] = []

    def recovery(command, **kwargs):
        action = "harvest" if "--harvest" in command else "live"
        calls.append(action)
        if calls == ["live"]:
            kwargs["stdout"].write(b"State: stalled\n")
        elif action == "live":
            kwargs["stdout"].write(b"State: completed\n")
        else:
            candidate = Path(command[command.index("--write-output") + 1])
            candidate.write_text("durable exact answer", encoding="utf-8")
            kwargs["stdout"].write(b"State: completed\n")
        kwargs["stdout"].flush()
        return Process(0, [])

    settled = runner.recover_run(
        run_dir,
        action="live",
        oracle_command=["oracle"],
        popen_factory=recovery,
        settle_timeout_seconds=5,
        settle_interval_seconds=0,
        sleep=lambda _: None,
    )

    assert calls == ["live", "live", "harvest"]
    assert settled["ok"] is True
    assert settled["status"] == "complete"
    assert settled["result"]["session_authority"] == "terminal"
    assert settled["result"]["terminal_harvested"] is True
    assert Path(settled["output_path"]).read_text(encoding="utf-8") == "durable exact answer"


def test_live_recovery_cli_defaults_to_one_ninety_minute_settle_process() -> None:
    runner = load_runner()
    args = runner.build_parser().parse_args([
        "recover", "--run-dir", r"C:\host-state\exact-run", "--action", "live",
    ])
    assert args.settle_timeout_seconds == 5400
    assert args.settle_interval_seconds == 15


def test_unresolved_exact_session_blocks_different_parent_submission(tmp_path: Path) -> None:
    runner = load_runner()
    first_parent = "a" * 64
    second_parent = "b" * 64
    first = execute_run(
        runner,
        manifest(tmp_path, run_id="a" * 32, parallel_parent_id=first_parent),
        run_factory=version_runner,
        popen_factory=popen_for(0, None, {}, []),
    )
    launches: list[list[str]] = []

    def forbidden_launch(command, **kwargs):
        launches.append(list(command))
        raise AssertionError("a different workflow must not submit while the exact session owns the project")

    second = execute_run(
        runner,
        manifest(tmp_path, run_id="b" * 32, parallel_parent_id=second_parent),
        run_factory=version_runner,
        popen_factory=forbidden_launch,
    )

    assert first["result"]["session_authority"] == "submitted_unknown"
    assert second["ok"] is False
    assert second["result"]["status"] == "failed"
    assert launches == []
    assert "still owns this project" in Path(second["result"]["artifacts"]["stderr"]).read_text(encoding="utf-8")


def test_legacy_attention_without_session_authority_is_not_a_permanent_project_lock(tmp_path: Path) -> None:
    runner = load_runner()
    first = execute_run(
        runner,
        manifest(tmp_path, run_id="a" * 32),
        run_factory=version_runner,
        popen_factory=popen_for(0, None, {}, []),
    )
    first_state_path = Path(first["run_dir"]) / "state.json"
    first_state = json.loads(first_state_path.read_text(encoding="utf-8"))
    first_state["status"] = "attention_required"
    first_state.pop("session_authority", None)
    first_state_path.write_text(json.dumps(first_state), encoding="utf-8")

    launches: list[list[str]] = []
    second = execute_run(
        runner,
        manifest(tmp_path, run_id="b" * 32),
        run_factory=version_runner,
        popen_factory=popen_for(0, b"answer", {}, launches),
    )

    assert second["ok"] is True
    assert launches


def test_recovery_never_downgrades_durable_complete(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(0, b"answer", {}, []),
    )
    calls = []
    recovered = runner.recover_run(
        Path(result["run_dir"]),
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=lambda *args, **kwargs: calls.append(True),
    )
    assert recovered["ok"] is True
    assert recovered["monotonic_noop"] is True
    assert calls == []


def test_parallel_recovery_reuses_the_parent_scoped_submit_mutex(tmp_path: Path) -> None:
    runner = load_runner()
    parent_id = "a" * 32
    roots: list[Path] = []

    class Mutex:
        def __init__(self, root: Path):
            self.root = root

        def __enter__(self):
            roots.append(self.root)

        def __exit__(self, *args):
            return None

    runner.STATE.project_submit_mutex = lambda root, **kwargs: Mutex(root)
    result = execute_run(
        runner,
        manifest(tmp_path, parallel_parent_id=parent_id),
        run_factory=version_runner,
        popen_factory=popen_for(4, None, {}, []),
    )
    recovered = runner.recover_run(Path(result["run_dir"]), action="harvest", dry_run=True, oracle_command=["oracle"])
    expected = tmp_path.resolve() / ".oracle-parallel-submit" / parent_id
    assert result["result"]["status"] == "attention_required"
    assert recovered["status"] == "dry-run"
    assert roots == [expected, expected]
