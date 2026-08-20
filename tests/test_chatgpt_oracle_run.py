from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
        model="gpt-5.6-sol",
        model_strategy="select",
        thinking_time="heavy",
        attachments=[str(prompt.resolve()), str(packet.resolve())],
        mission_path=str(prompt.resolve()),
        **extra,
    )


def pro_readonly_manifest(tmp_path: Path, **extra) -> Path:
    return manifest(
        tmp_path,
        transport="pro-devspace",
        app_name="DevSpace",
        model="gpt-5.6-sol",
        model_strategy="select",
        thinking_time="heavy",
        research="off",
        task_outcome_contract="v1",
        **extra,
    )


def version_runner(command, **kwargs):
    return subprocess.CompletedProcess(command, 0, stdout="oracle 0.13.0\n", stderr="")


def version_0171_runner(command, **kwargs):
    return subprocess.CompletedProcess(command, 0, stdout="oracle 0.17.1\n", stderr="")


def version_timeout_runner(command, **kwargs):
    raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 30))


def test_version_resolution_allows_a_bounded_slow_valid_oracle_0161() -> None:
    runner = load_runner()
    captured = {}

    def slow_valid(command, **kwargs):
        captured["command"] = command
        captured["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(command, 0, stdout="oracle 0.17.1\n", stderr="")

    assert runner.resolve_oracle_version(["npx.cmd", "-y", "@steipete/oracle@0.17.1"], run_factory=slow_valid) == "oracle 0.17.1"
    assert captured == {
        "command": ["npx.cmd", "-y", "@steipete/oracle@0.17.1", "--version"],
        "timeout": runner.ORACLE_VERSION_RESOLUTION_TIMEOUT_SECONDS,
    }
    assert runner.ORACLE_VERSION_RESOLUTION_TIMEOUT_SECONDS == 90


def test_default_oracle_command_is_pinned_to_the_hash_validated_version() -> None:
    runner = load_runner()

    assert runner.STATE.default_oracle_command(platform_name="nt") == (
        "npx.cmd", "-y", "@steipete/oracle@0.17.1",
    )
    with pytest.raises(runner.STATE.OracleStateError, match="0.17.1"):
        runner.STATE.validate_oracle_command(["npx.cmd", "-y", "@steipete/oracle@0.17.0"])


def test_conversation_url_helpers_preserve_exact_binding_and_detect_conflicts(tmp_path: Path) -> None:
    runner = load_runner()
    observer = tmp_path / "recovery-live-stdout.log"
    observer.write_text(
        "URL: https://chatgpt.com/c/oracle-old\nURL: https://chatgpt.com/c/oracle-current\n",
        encoding="utf-8",
    )
    state = {"oracle": {"conversation_url": "https://chatgpt.com/c/oracle-current"}}

    assert runner.exact_session_url(observer) == "https://chatgpt.com/c/oracle-current"
    assert runner.historical_conversation_url(tmp_path, state) == "https://chatgpt.com/c/oracle-current"
    assert runner.conversation_url_conflict(state, "https://chatgpt.com/c/oracle-other") == {
        "persisted": "https://chatgpt.com/c/oracle-current",
        "observed": "https://chatgpt.com/c/oracle-other",
    }


def execute_run(runner, *args, **kwargs):
    kwargs.setdefault("compat_factory", lambda version: {"ok": True, "version": version})
    kwargs.setdefault(
        "devspace_compat_factory",
        lambda: {"ok": True, "changed": [], "service_restart_required": False},
    )
    kwargs.setdefault(
        "devspace_qualification_factory",
        lambda root: {"qualified": True, "project_root": str(root)},
    )
    return runner.execute_run(*args, **kwargs)


class Process:
    def __init__(self, code: int, events: list[str]):
        self.code = code
        self.events = events
        self.pid = 1234
        self.wait_timeout = None

    def wait(self, timeout=None):
        self.wait_timeout = timeout
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


def prompt_not_observed_popen(command, **kwargs):
    slug = command[command.index("--slug") + 1]
    kwargs["stdout"].write(
        (
            f"Session: {slug}\n"
            "ERROR: Prompt did not appear in conversation before timeout (send may have failed)\n"
            "User error (browser-automation): Prompt did not appear in conversation before timeout (send may have failed)\n"
        ).encode()
    )
    kwargs["stdout"].flush()
    return Process(1, [])


def attachment_upload_timeout_popen(command, **kwargs):
    slug = command[command.index("--slug") + 1]
    kwargs["stdout"].write(
        (
            f"Session: {slug}\n"
            "ERROR: Attachments did not finish uploading before timeout.\n"
            "User error (browser-automation): Attachments did not finish uploading before timeout.\n"
        ).encode()
    )
    kwargs["stdout"].flush()
    return Process(1, [])


def recovery_binding_unavailable_popen(command, **kwargs):
    slug = command[command.index("session") + 1]
    kwargs["stdout"].write(
        f'No live ChatGPT tab matched session "{slug}". Attempting recovery by reopening the saved conversation URL.\n'.encode()
    )
    kwargs["stderr"].write(
        b"Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL.\n"
    )
    kwargs["stdout"].flush()
    kwargs["stderr"].flush()
    return Process(1, [])


def cdp_disconnect_pre_submit_popen(session_root: Path, *, variation: str | None = None):
    def popen(command, **kwargs):
        slug = command[command.index("--slug") + 1]
        output_path = Path(command[command.index("--write-output") + 1]).resolve()
        expected_profile = (Path.home() / ".oracle" / "browser-profile").resolve()
        error_message = (
            "Chrome DevTools client disconnected before oracle finished; "
            "the browser target appears still alive."
        )
        if variation == "different-error":
            error_message = "Chrome DevTools client disconnected after an unknown browser event."
        lines = [
            "? oracle 0.17.1 deterministic fixture",
            f"Session: {slug}",
            "Mode: browser foreground",
            "Models: 1",
            "Detach: no",
            f"Reattach: oracle session {slug}",
            "Launching browser mode (target=GPT-5.6 Sol; requested=gpt-5.6-sol) with ~135 tokens.",
            "This run can take up to an hour (usually ~10 minutes).",
            "[browser] Browser control: launch Chrome in hidden-window mode; may focus/control the browser UI.",
            "[browser] Browser guidance: On macOS, Oracle launches Chrome off-screen while keeping the page rendered.",
            "[browser] Browser guidance: For the calmest shared-desktop flow, prefer --browser-attach-running or --remote-chrome.",
            f"ERROR: {error_message}",
            f"User error (browser-automation): {error_message}",
        ]
        kwargs["stdout"].write(("\n".join(lines) + "\n").encode())
        kwargs["stdout"].flush()
        prompt_submitted = variation == "prompt-submitted"
        tab_url = (
            "https://chatgpt.com/c/existing-conversation"
            if variation == "conversation-url"
            else "https://chatgpt.com/"
        )
        meta = {
            "id": slug,
            "createdAt": "2026-08-13T00:23:55.772Z",
            "status": "error",
            "model": "gpt-5.6-sol",
            "models": [{"model": "gpt-5.6-sol", "status": "running"}],
            "cwd": str(Path(kwargs["cwd"]).resolve()),
            "mode": "browser",
            "browser": {
                "config": {
                    "copyProfileSource": str(expected_profile),
                    "desiredModel": "GPT-5.6 Sol",
                    "modelStrategy": "select",
                    "thinkingTime": "heavy",
                },
                "runtime": {
                    "chromePid": 12816,
                    "chromePort": 9222,
                    "chromeHost": "127.0.0.1",
                    "promptSubmitted": prompt_submitted,
                    "controllerPid": 30236,
                },
            },
            "options": {
                "model": "gpt-5.6-sol",
                "slug": slug,
                "writeOutputPath": str(output_path),
                "browserConfig": {
                    "copyProfileSource": str(expected_profile),
                    "desiredModel": "GPT-5.6 Sol",
                    "modelStrategy": "select",
                    "thinkingTime": "heavy",
                },
            },
            "completedAt": "2026-08-13T00:24:24.835Z",
            "errorMessage": error_message,
            "error": {
                "category": "browser-automation",
                "message": error_message,
                "details": {
                    "stage": "connection-lost",
                    "recoverableDisconnect": True,
                    "disconnectCause": "cdp-client-disconnect",
                    "runtime": {
                        "chromePid": 12816,
                        "chromePort": 9222,
                        "chromeHost": "127.0.0.1",
                        "tabUrl": tab_url,
                        "promptSubmitted": prompt_submitted,
                        "controllerPid": 30236,
                    },
                },
            },
        }
        meta_path = session_root / slug / "meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        if variation == "output-present":
            output_path.write_text("contradictory output", encoding="utf-8")
        return Process(1, [])

    return popen


def model_selector_button_pre_submit_popen(
    session_root: Path,
    *,
    variation: str | None = None,
):
    def popen(command, **kwargs):
        slug = command[command.index("--slug") + 1]
        output_path = Path(command[command.index("--write-output") + 1]).resolve()
        expected_profile = (Path.home() / ".oracle" / "browser-profile").resolve()
        error_message = (
            "Unable to locate the ChatGPT model selector button. If the desired model is "
            "already selected in the browser, retry with --browser-model-strategy current; "
            "otherwise retry with --browser-model-strategy ignore to skip model selection."
        )
        emitted_error = (
            "Unable to locate a different browser control."
            if variation == "different-error"
            else error_message
        )
        lines = [
            "? oracle 0.17.1 deterministic fixture",
            f"Session: {slug}",
            "Mode: browser foreground",
            "Models: 1",
            "Detach: no",
            f"Reattach: oracle session {slug}",
            "Launching browser mode (target=GPT-5.6 Sol; requested=gpt-5.6-sol) with ~200 tokens.",
            "This run can take up to an hour (usually ~10 minutes).",
            "[browser] Browser control: launch Chrome in hidden-window mode; may focus/control the browser UI.",
            "[browser] Browser guidance: On macOS, Oracle launches Chrome off-screen while keeping the page rendered.",
            "[browser] Browser guidance: For the calmest shared-desktop flow, prefer --browser-attach-running or --remote-chrome.",
            f"ERROR: {emitted_error}",
            f"User error (browser-automation): {emitted_error}",
        ]
        kwargs["stdout"].write(("\n".join(lines) + "\n").encode("utf-8"))
        kwargs["stdout"].flush()
        tab_url = (
            "https://chatgpt.com/c/existing-conversation"
            if variation == "conversation-url"
            else "https://chatgpt.com/"
        )
        prompt_submitted = variation == "prompt-submitted"
        meta = {
            "id": slug,
            "createdAt": "2026-08-20T00:09:39.348Z",
            "startedAt": "2026-08-20T00:09:39.393Z",
            "completedAt": "2026-08-20T00:10:07.368Z",
            "status": "error",
            "model": "gpt-5.6-sol",
            "cwd": str(Path(kwargs["cwd"]).resolve()),
            "mode": "browser",
            "browser": {
                "config": {
                    "copyProfileSource": str(expected_profile),
                    "desiredModel": "GPT-5.6 Sol",
                    "modelStrategy": "select",
                    "thinkingTime": "heavy",
                },
                "runtime": {
                    "chromePid": 16424,
                    "chromePort": 9222,
                    "chromeHost": "127.0.0.1",
                    "tabUrl": tab_url,
                    "promptSubmitted": prompt_submitted,
                    "controllerPid": 27784,
                },
            },
            "options": {
                "model": "gpt-5.6-sol",
                "slug": slug,
                "writeOutputPath": str(output_path),
                "browserConfig": {
                    "copyProfileSource": str(expected_profile),
                    "desiredModel": "GPT-5.6 Sol",
                    "modelStrategy": "select",
                    "thinkingTime": "heavy",
                },
            },
            "errorMessage": error_message,
            "error": {
                "category": "browser-automation",
                "message": error_message,
                "details": {
                    "stage": "different-stage" if variation == "different-stage" else "execute-browser",
                },
            },
        }
        if variation != "missing-meta":
            meta_path = session_root / slug / "meta.json"
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        if variation == "output-present":
            output_path.write_text("contradictory output", encoding="utf-8")
        return Process(1, [])

    return popen


def isolated_default_oracle_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Create the exact default copied profile without relying on the CI host."""
    home = tmp_path.parent / f"{tmp_path.name}-oracle-home"
    profile = home / ".oracle" / "browser-profile"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return profile


def duplicate_prompt_popen(command, **kwargs):
    kwargs["stdout"].write(
        b'oracle 0.17.1\nA session with the same prompt is already running '
        b'(oracle-global-agent-instructio-f39cc47ba5). Reattach with '
        b'"oracle session oracle-global-agent-instructio-f39cc47ba5" or rerun with '
        b'--force to start another run.\n'
    )
    kwargs["stdout"].flush()
    return Process(1, [])


def copy_profile_manual_login_conflict_popen(command, **kwargs):
    kwargs["stdout"].write(
        b"oracle 0.17.1\n"
        b"Launching browser mode (gpt-5.6-sol) with 2 files.\n"
        b"ERROR: --copy-profile cannot be combined with --browser-manual-login: choose either a "
        b"throwaway copied profile or the persistent manual-login profile.\n"
    )
    kwargs["stdout"].flush()
    return Process(1, [])


def profile_copy_rsync_missing_popen(command, **kwargs):
    kwargs["stdout"].write(
        b"oracle 0.17.1\n"
        b"Session: oracle-test-profile-rsync\n"
        b"Launching browser mode (target=GPT-5.6 Sol; requested=gpt-5.6-sol) with 2 files.\n"
        b"ERROR: --copy-profile requires rsync on PATH (spawn failed): spawn rsync ENOENT\n"
        b"User error (browser-automation): --copy-profile requires rsync on PATH "
        b"(spawn failed): spawn rsync ENOENT\n"
    )
    kwargs["stdout"].flush()
    return Process(1, [])


def manual_login_profile_uninitialized_popen(command, **kwargs):
    return manual_login_profile_uninitialized_variant(command, **kwargs)


def manual_login_profile_uninitialized_variant(command, *, variation=None, **kwargs):
    locator = command[command.index("--slug") + 1]
    expected_profile = (Path.home() / ".oracle" / "browser-profile").resolve()
    reported_profile = (
        (expected_profile.parent / "different-profile").resolve()
        if variation == "different-profile"
        else expected_profile
    )
    escaped_profile = str(reported_profile).replace("\\", "\\\\")
    failure_tail = (
        "ChatGPT browser manual-login profile is not initialized. "
        f"Browser mode is using Oracle's private Chrome profile at {reported_profile}, "
        "separate from your normal Chrome profile. Run first-time setup, sign in there, then retry: "
        "oracle --engine browser --browser-manual-login --browser-keep-browser "
        f'--browser-manual-login-profile-dir "{escaped_profile}" -p "HI". '
        "If you want to reuse an already signed-in Chrome instead, use --browser-attach-running."
    )
    lines = [
        "🧿 oracle 0.17.1 — Silent run, loud receipts.",
        f"Session: {locator}",
        "Mode: browser foreground",
        "Models: 1",
        "Detach: no",
        f"Reattach: oracle session {locator}",
        "Launching browser mode (target=GPT-5.6 Sol; requested=gpt-5.6-sol) with ~108 tokens.",
        "This run can take up to an hour (usually ~10 minutes).",
        "[browser] Browser control: launch Chrome in hidden-window mode; may focus/control the browser UI.",
        "[browser] Browser guidance: On macOS, Oracle launches Chrome off-screen while keeping the page rendered.",
        "[browser] Browser guidance: For the calmest shared-desktop flow, prefer --browser-attach-running or --remote-chrome.",
        f"ERROR: {failure_tail}",
    ]
    if variation == "conversation-url":
        lines.append("URL: https://chatgpt.com/c/submitted-session")
    if variation != "missing-user-error":
        lines.append(f"User error (browser-automation): {failure_tail}")
    if variation == "output-exists":
        Path(command[command.index("--write-output") + 1]).write_text("unexpected output", encoding="utf-8")
    kwargs["stdout"].write(("\n".join(lines) + "\n").encode("utf-8"))
    kwargs["stdout"].flush()
    return Process(1, [])


def thinking_time_selection_unverified_popen(command, **kwargs):
    kwargs["stdout"].write(
        b"oracle 0.17.1\n"
        b"Session: oracle-test-thinking-time\n"
        b"Launching browser mode (target=GPT-5.6 Sol; requested=gpt-5.6-sol) with 2 files.\n"
        b"ERROR: Thinking time: selection unverified (requested Heavy); "
        b"refusing to submit without confirmed Heavy.\n"
        b"User error (browser-automation): Thinking time: selection unverified "
        b"(requested Heavy); refusing to submit without confirmed Heavy.\n"
    )
    kwargs["stdout"].flush()
    return Process(1, [])


def thinking_time_unknown_outcome_popen(command, **kwargs):
    kwargs["stdout"].write(
        b"oracle 0.17.1\n"
        b"Session: oracle-test-thinking-time-unknown\n"
        b"Launching browser mode (target=GPT-5.6 Sol; requested=gpt-5.6-sol) with 2 files.\n"
        b"ERROR: Thinking time: unknown outcome selecting Heavy; "
        b"refusing to submit without confirmed Heavy.\n"
        b"User error (browser-automation): Thinking time: unknown outcome selecting Heavy; "
        b"refusing to submit without confirmed Heavy.\n"
    )
    kwargs["stdout"].flush()
    return Process(1, [])


def profile_copy_ebusy_popen(command, **kwargs):
    source = Path(command[command.index("--copy-profile") + 1]) / "Default" / "Network" / "Cookies"
    destination = Path(kwargs["env"]["TEMP"]) / "oracle-browser-test" / "Default" / "Network" / "Cookies"
    kwargs["stdout"].write(
        f"ERROR: EBUSY: resource busy or locked, copyfile '{source}' -> '{destination}'\n".encode("utf-8")
    )
    kwargs["stdout"].flush()
    return Process(1, [])


def model_switcher_no_cookie_popen(command, **kwargs):
    kwargs["stdout"].write(
        b'ERROR: Unable to find model option matching "Pro" in the model switcher. '
        b'Available: Advanced, ModelGPT-5.6 Sol, EffortHigh. No cookies were applied; '
        b'log in to ChatGPT in Chrome or provide inline cookies.\n'
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


def test_missing_posix_copy_dependency_still_launches_without_profile_copy(
    tmp_path: Path, monkeypatch
) -> None:
    runner = load_runner()
    profile = tmp_path.parent / f"{tmp_path.name}-signed-in-oracle-profile"
    profile.mkdir()
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(profile.resolve()))
    monkeypatch.setattr(runner.STATE.shutil, "which", lambda name: None)

    result = execute_run(
        runner, manifest(tmp_path), dry_run=True, platform_name="posix"
    )

    assert "--copy-profile" not in result["argv"]
    assert result["argv"].count("--browser-hide-window") == 1


def test_windows_lanes_keep_profile_isolation_without_rsync(
    tmp_path: Path, monkeypatch
) -> None:
    """Windows uses the pinned native profile copy, so lanes stay isolated.

    Probing PATH for rsync here dropped `--copy-profile` and blocked parallel
    Web Multi lanes before submission.
    """
    runner = load_runner()
    profile = tmp_path.parent / f"{tmp_path.name}-signed-in-oracle-profile"
    profile.mkdir()
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(profile.resolve()))
    monkeypatch.setattr(runner.STATE.shutil, "which", lambda name: None)

    result = execute_run(runner, manifest(tmp_path), dry_run=True, platform_name="nt")

    assert result["argv"][result["argv"].index("--copy-profile") + 1] == str(
        profile.resolve()
    )
    assert result["argv"].count("--browser-hide-window") == 1


def test_explicit_hide_window_arg_is_safe_and_not_duplicated(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path, oracle_args=["--browser-hide-window"]),
        dry_run=True,
    )
    assert result["argv"].count("--browser-hide-window") == 1


def test_regular_runs_use_provider_window_and_nonterminal_status_audit(
    tmp_path: Path,
) -> None:
    """The browser window is separate from the non-terminal 80-minute audit."""
    runner = load_runner()

    result = execute_run(runner, manifest(tmp_path), dry_run=True)

    argv = result["argv"]
    assert argv.count("--browser-timeout") == 1
    assert argv[argv.index("--browser-timeout") + 1] == runner.STATE.DEFAULT_BROWSER_ANSWER_TIMEOUT
    assert runner.STATE.DEFAULT_BROWSER_ANSWER_TIMEOUT == "100m"
    assert runner.STATE.DEFAULT_BROWSER_ANSWER_CEILING_MINUTES == 100
    assert result["browser_observer_timeout_seconds"] == 6000
    assert result["status_audit_seconds"] == 4800
    assert result["time_alone_is_terminal"] is False


def test_explicit_answer_timeout_is_honored_without_duplication(tmp_path: Path) -> None:
    runner = load_runner()

    result = execute_run(
        runner,
        manifest(tmp_path, oracle_args=["--browser-timeout", "100m"]),
        dry_run=True,
    )

    argv = result["argv"]
    assert argv.count("--browser-timeout") == 1
    assert argv[argv.index("--browser-timeout") + 1] == "100m"
    assert result["browser_observer_timeout_seconds"] == 6000


def test_shorter_browser_observer_is_not_misreported_as_a_terminal_deadline(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path, oracle_args=["--browser-timeout", "35m"]),
        dry_run=True,
    )
    assert result["browser_observer_timeout_seconds"] == 2100
    assert result["status_audit_seconds"] == 4800
    assert result["time_alone_is_terminal"] is False


@pytest.mark.parametrize("duration", ["9d", "999999999h", "9" * 400])
def test_answer_timeout_must_produce_a_finite_bounded_host_deadline(
    tmp_path: Path,
    duration: str,
) -> None:
    runner = load_runner()

    with pytest.raises(runner.OracleRunError) as exc:
        execute_run(
            runner,
            manifest(tmp_path, oracle_args=["--browser-timeout", duration]),
            dry_run=True,
        )

    assert exc.value.code in {"BROWSER_TIMEOUT_INVALID", "BROWSER_TIMEOUT_OUT_OF_RANGE"}


def test_pro_uses_the_bounded_original_session_answer_wait(tmp_path: Path) -> None:
    runner = load_runner()

    result = execute_run(runner, pro_manifest(tmp_path), dry_run=True)

    assert result["argv"].count("--browser-timeout") == 1
    assert result["argv"][result["argv"].index("--browser-timeout") + 1] == "100m"
    assert result["browser_observer_timeout_seconds"] == 6000


def test_pro_dry_run_uses_oracle_attachments_and_no_app_mention(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(runner, pro_manifest(tmp_path), dry_run=True)
    argv = result["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    attachments = [argv[index + 1] for index, value in enumerate(argv) if value == "--file"]
    assert result["transport"] == "pro-attachment-only"
    assert result["contains_file_flag"] is True
    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
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


def test_pro_devspace_dry_run_uses_write_capable_handoff_without_file_transport(tmp_path: Path) -> None:
    runner = load_runner()
    preflight_calls = []
    captured = {}
    result = runner.execute_run(
        pro_readonly_manifest(tmp_path),
        run_factory=version_0171_runner,
        popen_factory=popen_for(0, b"completed answer\n", captured, []),
        compat_factory=lambda version: {"ok": True, "version": version},
        devspace_compat_factory=lambda: preflight_calls.append(True) or {
            "ok": True, "changed": [], "service_restart_required": False,
        },
        devspace_qualification_factory=lambda root: {"qualified": True, "project_root": str(root)},
    )

    argv = captured["command"]
    prompt = argv[argv.index("--prompt") + 1]
    assert result["result"]["transport"] == "pro-devspace"
    assert "--browser-attachments" not in argv
    assert "--file" not in argv
    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
    assert argv[argv.index("--browser-thinking-time") + 1] == "heavy"
    assert prompt.startswith(f"@DevSpace First open exactly this project root in checkout mode: {tmp_path}.")
    assert f"Then read and execute the mission file: {tmp_path / 'mission.md'}." in prompt
    assert "Do not open the mission directory, a parent, a child" in prompt
    assert prompt.index(str(tmp_path)) < prompt.index(str(tmp_path / "mission.md"))
    assert "create, edit, and remove mission-owned files and run commands" in prompt
    assert preflight_calls == [True]


def test_d_coin_missing_exact_root_blocks_before_oracle_or_run_creation(tmp_path: Path) -> None:
    runner = load_runner()
    calls: list[str] = []

    def missing_root(_root: Path):
        calls.append("qualification")
        raise runner.DEVSPACE_PREFLIGHT.DevSpacePreflightError(
            "DEVSPACE_EXACT_ROOT_UNAVAILABLE",
            "the exact project root is not registered in DevSpace allowedRoots",
            {
                "missing_root": r"D:\Coin",
                "registration_url": "https://device.tailnet.ts.net/mcp",
                "next_action": "REGISTER_EXACT_DEVSPACE_ROOT_BEFORE_ORACLE_SUBMISSION",
            },
        )

    with pytest.raises(runner.OracleRunError) as exc:
        runner.execute_run(
            pro_readonly_manifest(tmp_path, run_id="5" * 32),
            run_factory=lambda *args, **kwargs: calls.append("version"),
            popen_factory=lambda *args, **kwargs: calls.append("oracle"),
            compat_factory=lambda *args, **kwargs: calls.append("compat"),
            devspace_compat_factory=lambda: calls.append("devspace-compat"),
            devspace_qualification_factory=missing_root,
        )

    assert exc.value.code == "DEVSPACE_EXACT_ROOT_UNAVAILABLE"
    assert exc.value.evidence["missing_root"] == r"D:\Coin"
    assert exc.value.evidence["registration_url"].endswith("/mcp")
    assert calls == ["qualification"]
    assert not (tmp_path.parent / f"{tmp_path.name}-host-state" / "runs").exists()


def test_pro_attachment_limit_is_exactly_one_mib_and_blocks_before_oracle_launch(tmp_path: Path) -> None:
    runner = load_runner()
    packet = tmp_path / "packet.zip"
    exact_manifest = pro_manifest(tmp_path)
    packet.write_bytes(b"x" * runner.ORACLE_0161_ATTACHMENT_MAX_BYTES)
    assert execute_run(runner, exact_manifest, dry_run=True)["ok"] is True

    packet.write_bytes(b"x" * (runner.ORACLE_0161_ATTACHMENT_MAX_BYTES + 1))
    calls: list[bool] = []
    with pytest.raises(runner.OracleRunError) as exc:
        execute_run(
            runner,
            exact_manifest,
            dry_run=True,
            run_factory=lambda *args, **kwargs: calls.append(True),
            popen_factory=lambda *args, **kwargs: calls.append(True),
        )
    assert exc.value.code == "ORACLE_ATTACHMENT_SIZE_PRELAUNCH_FAILED"
    assert exc.value.evidence["limit_bytes"] == 1024 * 1024
    assert calls == []
    assert not (tmp_path / "runs").exists()


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


def test_pro_devspace_terminal_no_tool_output_is_not_success(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        pro_readonly_manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(
            0,
            (
                b"DevSpace namespace exists but exposes zero callable functions; "
                b"the mission and root were not read.\nTASK_OUTCOME: NOT_EXECUTED\n"
            ),
            {},
            [],
        ),
    )

    assert result["ok"] is False
    assert result["result"]["status"] == "attention_required"
    assert result["result"]["transport_status"] == "complete"
    assert result["result"]["task_outcome"] == "not_executed"
    assert result["result"]["session_authority"] == "terminal"
    assert result["result"]["terminal_harvested"] is True


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
        devspace_qualification_factory=lambda root: {"qualified": True, "project_root": str(root)},
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


def test_post_submit_response_timeout_starts_exact_session_live_recovery(tmp_path: Path) -> None:
    runner = load_runner()
    launches: list[list[str]] = []

    def response_timeout_popen(command, **kwargs):
        launches.append(list(command))
        kwargs["stdout"].write(b"Session: exact\nprompt submitted; response streaming\n")
        kwargs["stderr"].write(
            b"ERROR: Assistant response timed out before completion; reattach later to capture the answer.\n"
        )
        kwargs["stdout"].flush()
        kwargs["stderr"].flush()
        return Process(1, [])

    recoveries: list[tuple[Path, dict]] = []

    def exact_recovery(run_dir, **kwargs):
        recoveries.append((Path(run_dir), dict(kwargs)))
        state = runner.STATE.load_state(Path(run_dir) / "state.json")
        return {"ok": False, "status": "session_live", "run_dir": str(run_dir), "result": state}

    result = execute_run(
        runner,
        pro_manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=response_timeout_popen,
        exact_recovery_factory=exact_recovery,
    )

    state = result["result"]
    assert result["ok"] is False
    assert result["status"] == "session_live"
    assert result["automatic_exact_session_recovery"] is True
    assert result["original_observer_status"] == "post_submit_response_timeout"
    assert result["safe_for_fresh_run"] is False
    assert len(launches) == 1
    assert recoveries == [(Path(result["run_dir"]), {
        "action": "live",
        "platform_name": None,
        "settle_timeout_seconds": 4800.0,
    })]
    assert state["status"] == "running"
    assert state["session_authority"] == "live"
    assert state["terminal_harvested"] is False
    assert state["transport_status"] == "post_submit_response_timeout"
    assert state["task_outcome_reason"] == "assistant-response-timeout-passive-wait"


@pytest.mark.parametrize("parallel_parent_id", [None, "d" * 64])
def test_status_audit_keeps_same_process_until_it_exits(
    tmp_path: Path,
    parallel_parent_id: str | None,
) -> None:
    runner = load_runner()
    waits: list[float | None] = []
    process_actions: list[str] = []
    launches: list[list[str]] = []

    class AuditedProcess:
        pid = 4242

        def __init__(self, stdout):
            self.wait_count = 0
            self.stdout = stdout

        def wait(self, timeout=None):
            waits.append(timeout)
            self.wait_count += 1
            if self.wait_count == 2:
                self.stdout.write(b"still streaming exact response\n")
                self.stdout.flush()
            if self.wait_count <= 2:
                raise subprocess.TimeoutExpired("oracle", timeout)
            return 7

        def poll(self):
            return None

        def terminate(self):
            process_actions.append("terminate")

        def kill(self):
            process_actions.append("kill")

    def hung_popen(command, **kwargs):
        launches.append(list(command))
        kwargs["stdout"].write(b"Session: exact\nprompt submitted; response streaming\n")
        kwargs["stdout"].flush()
        return AuditedProcess(kwargs["stdout"])

    extras = {
        "run_id": "4" * 32,
    }
    if parallel_parent_id is not None:
        extras["parallel_parent_id"] = parallel_parent_id
    result = execute_run(
        runner,
        manifest(tmp_path, **extras),
        run_factory=version_runner,
        popen_factory=hung_popen,
    )
    state = result["result"]

    assert result["ok"] is False
    assert result["result"]["status"] == "attention_required"
    assert waits == [4800.0, 4800.0, 4800.0]
    assert process_actions == []
    assert len(launches) == 1
    assert state["status"] == "attention_required"
    assert state["exit_code"] == 7
    assert state["session_authority"] == "submitted_unknown"
    assert state["terminal_harvested"] is False
    assert state["transport_status"] == "failed"
    assert state["status_audit"]["threshold_kind"] == "caution-status-audit"
    assert state["status_audit"]["audit_count"] == 2
    assert state["status_audit"]["process_live"] is True
    assert state["status_audit"]["artifacts"]["stdout"]["progress_since_prior_audit"] is True
    assert state["status_audit"]["decision"] == "continue-observing-same-exact-session"
    assert state["status_audit"]["time_alone_is_terminal"] is False
    assert state["status_audit"]["ownership_action"] == "preserve"
    assert state["status_audit"]["submission_action"] == "none"
    assert Path(state["artifacts"]["browser_temp"]).is_dir()
    assert not Path(state["artifacts"]["output"]).exists()
    assert not list(Path(result["run_dir"]).glob("recovery-*-stdout.log"))


def test_status_audit_race_accepts_a_process_that_already_exited(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    output_path: Path | None = None

    class RacedExitProcess:
        pid = 4343

        def wait(self, timeout=None):
            assert timeout == 4800
            assert output_path is not None
            output_path.write_text("durable answer\nTASK_OUTCOME: EXECUTED\n", encoding="utf-8")
            raise subprocess.TimeoutExpired("oracle", timeout)

        def poll(self):
            return 0

    def raced_popen(command, **kwargs):
        nonlocal output_path
        output_path = Path(command[command.index("--write-output") + 1])
        return RacedExitProcess()

    result = execute_run(
        runner,
        manifest(
            tmp_path,
            run_id="5" * 32,
            task_outcome_contract="v1",
        ),
        run_factory=version_runner,
        popen_factory=raced_popen,
    )

    assert result["ok"] is True
    assert result["result"]["status"] == "complete"
    assert result["result"]["session_authority"] == "terminal"
    assert result["result"]["terminal_harvested"] is True
    assert result["result"]["browser_observer"]["status"] == "process-exited"


def test_original_observer_exit_cannot_overwrite_concurrent_exact_harvest(
    tmp_path: Path,
) -> None:
    runner = load_runner()

    class StaleObserverProcess:
        pid = 4545

        def __init__(self, output_path: Path):
            self.output_path = output_path

        def wait(self, timeout=None):
            self.output_path.write_text(
                "durable exact answer\nTASK_OUTCOME: EXECUTED\n",
                encoding="utf-8",
            )
            state_path = self.output_path.parent / "state.json"
            runner.STATE.update_state(
                state_path,
                status="complete",
                exit_code=0,
                session_authority="terminal",
                terminal_harvested=True,
                artifact_sha256=runner.STATE.sha256_file(self.output_path),
                transport_status="complete",
                task_outcome="executed",
                task_outcome_reason="explicit-output-marker",
            )
            return 7

    def stale_popen(command, **kwargs):
        output_path = Path(command[command.index("--write-output") + 1])
        return StaleObserverProcess(output_path)

    result = execute_run(
        runner,
        manifest(
            tmp_path,
            run_id="6" * 32,
            task_outcome_contract="v1",
        ),
        run_factory=version_runner,
        popen_factory=stale_popen,
    )

    assert result["ok"] is True
    assert result["status"] == "complete"
    assert result["monotonic_race_preserved"] is True
    assert result["result"]["session_authority"] == "terminal"
    assert result["result"]["terminal_harvested"] is True
    assert result["result"]["task_outcome"] == "executed"


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


def test_copy_profile_manual_login_conflict_is_proven_pre_submit_and_releases_project(tmp_path: Path) -> None:
    runner = load_runner()
    seed = tmp_path.parent / f"{tmp_path.name}-profile"
    seed.mkdir(parents=True)
    result = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="d" * 32, copy_profile=str(seed)),
        run_factory=version_0171_runner,
        popen_factory=copy_profile_manual_login_conflict_popen,
    )
    run_dir = Path(result["run_dir"])
    state = runner.STATE.load_state(run_dir / "state.json")

    assert result["status"] == "pre_submit_failed"
    assert result["safe_for_fresh_run"] is True
    assert state["session_authority"] == "pre_submit"
    assert state["transport_status"] == "failed_pre_submit"
    assert state["task_outcome"] == "not_executed"
    assert state["task_outcome_reason"] == "oracle-launch-flags-mutually-exclusive-pre-submit"
    assert state["pre_submit_failure"]["code"] == "ORACLE_LAUNCH_FLAGS_MUTUALLY_EXCLUSIVE_PRELAUNCH_FAILED"
    assert runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(pro_manifest(tmp_path)).run_root,
        tmp_path,
    ) == []


def test_profile_copy_rsync_missing_is_proven_pre_submit_and_releases_project(tmp_path: Path) -> None:
    runner = load_runner()
    seed = tmp_path.parent / f"{tmp_path.name}-profile"
    seed.mkdir(parents=True)
    result = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="e" * 32, copy_profile=str(seed)),
        run_factory=version_0171_runner,
        popen_factory=profile_copy_rsync_missing_popen,
    )
    run_dir = Path(result["run_dir"])
    state = runner.STATE.load_state(run_dir / "state.json")

    assert result["status"] == "pre_submit_failed"
    assert result["safe_for_fresh_run"] is True
    assert state["session_authority"] == "pre_submit"
    assert state["transport_status"] == "failed_pre_submit"
    assert state["task_outcome"] == "not_executed"
    assert state["task_outcome_reason"] == "oracle-profile-copy-rsync-pre-submit"
    assert state["pre_submit_failure"]["code"] == "ORACLE_PROFILE_COPY_RSYNC_PRELAUNCH_FAILED"
    assert runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(pro_manifest(tmp_path)).run_root,
        tmp_path,
    ) == []


def test_manual_login_profile_uninitialized_is_proven_pre_submit_and_releases_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = load_runner()
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(tmp_path / "profile-not-created"))
    result = execute_run(
        runner,
        pro_readonly_manifest(tmp_path, run_id="2" * 32),
        run_factory=version_0171_runner,
        popen_factory=manual_login_profile_uninitialized_popen,
    )
    run_dir = Path(result["run_dir"])
    state = runner.STATE.load_state(run_dir / "state.json")

    assert result["status"] == "pre_submit_failed"
    assert result["safe_for_fresh_run"] is True
    assert state["session_authority"] == "pre_submit"
    assert state["transport_status"] == "failed_pre_submit"
    assert state["task_outcome"] == "not_executed"
    assert state["task_outcome_reason"] == "oracle-manual-login-profile-uninitialized-pre-submit"
    assert (
        state["pre_submit_failure"]["code"]
        == "ORACLE_MANUAL_LOGIN_PROFILE_UNINITIALIZED_PRELAUNCH_FAILED"
    )
    assert runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(pro_readonly_manifest(tmp_path)).run_root,
        tmp_path,
    ) == []


@pytest.mark.parametrize(
    "variation",
    ["missing-user-error", "different-profile", "output-exists", "conversation-url"],
)
def test_manual_login_profile_uninitialized_keeps_lock_when_proof_is_incomplete(
    tmp_path: Path,
    variation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = load_runner()
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(tmp_path / "profile-not-created"))

    def variant_popen(command, **kwargs):
        return manual_login_profile_uninitialized_variant(command, variation=variation, **kwargs)

    result = execute_run(
        runner,
        pro_readonly_manifest(tmp_path, run_id="3" * 32),
        run_factory=version_0171_runner,
        popen_factory=variant_popen,
    )
    state = runner.STATE.load_state(Path(result["run_dir"]) / "state.json")

    assert result["result"]["status"] == "attention_required"
    assert state["session_authority"] == "submitted_unknown"
    assert "pre_submit_failure" not in state
    owners = runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(pro_readonly_manifest(tmp_path)).run_root,
        tmp_path,
    )
    assert [owner["run_id"] for owner in owners] == ["3" * 32]


def test_thinking_time_selection_unverified_is_proven_pre_submit_and_releases_project(tmp_path: Path) -> None:
    runner = load_runner()
    seed = tmp_path.parent / f"{tmp_path.name}-profile"
    seed.mkdir(parents=True)
    result = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="f" * 32, copy_profile=str(seed)),
        run_factory=version_0171_runner,
        popen_factory=thinking_time_selection_unverified_popen,
    )
    run_dir = Path(result["run_dir"])
    state = runner.STATE.load_state(run_dir / "state.json")

    assert result["status"] == "pre_submit_failed"
    assert result["safe_for_fresh_run"] is True
    assert state["session_authority"] == "pre_submit"
    assert state["transport_status"] == "failed_pre_submit"
    assert state["task_outcome"] == "not_executed"
    assert state["task_outcome_reason"] == "oracle-thinking-time-pre-submit"
    assert state["pre_submit_failure"]["code"] == "ORACLE_THINKING_TIME_PRE_SUBMIT_FAILED"
    assert state["pre_submit_failure"]["requested_level"] == "Heavy"
    assert runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(pro_manifest(tmp_path)).run_root,
        tmp_path,
    ) == []


def test_thinking_time_unknown_outcome_is_proven_pre_submit_and_releases_project(tmp_path: Path) -> None:
    runner = load_runner()
    seed = tmp_path.parent / f"{tmp_path.name}-profile"
    seed.mkdir(parents=True)
    result = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="1" * 32, copy_profile=str(seed)),
        run_factory=version_0171_runner,
        popen_factory=thinking_time_unknown_outcome_popen,
    )
    run_dir = Path(result["run_dir"])
    state = runner.STATE.load_state(run_dir / "state.json")

    assert result["status"] == "pre_submit_failed"
    assert result["safe_for_fresh_run"] is True
    assert state["session_authority"] == "pre_submit"
    assert state["transport_status"] == "failed_pre_submit"
    assert state["task_outcome"] == "not_executed"
    assert state["task_outcome_reason"] == "oracle-thinking-time-pre-submit"
    assert state["pre_submit_failure"]["code"] == "ORACLE_THINKING_TIME_PRE_SUBMIT_FAILED"
    assert state["pre_submit_failure"]["requested_level"] == "Heavy"
    assert runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(pro_manifest(tmp_path)).run_root,
        tmp_path,
    ) == []


def test_profile_copy_ebusy_is_proven_pre_submit_and_releases_project(tmp_path: Path) -> None:
    runner = load_runner()
    seed = tmp_path.parent / f"{tmp_path.name}-profile"
    cookies = seed / "Default" / "Network" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_text("seed", encoding="utf-8")
    result = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="c" * 32, copy_profile=str(seed)),
        run_factory=version_runner,
        popen_factory=profile_copy_ebusy_popen,
    )
    run_dir = Path(result["run_dir"])
    state = runner.STATE.load_state(run_dir / "state.json")

    assert result["status"] == "pre_submit_failed"
    assert result["safe_for_fresh_run"] is True
    assert state["session_authority"] == "pre_submit"
    assert state["transport_status"] == "failed_pre_submit"
    assert state["task_outcome"] == "not_executed"
    assert state["pre_submit_failure"]["code"] == "ORACLE_PROFILE_COPY_EBUSY_PRELAUNCH_FAILED"
    assert runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(pro_manifest(tmp_path)).run_root,
        tmp_path,
    ) == []


def test_model_switcher_no_cookie_failure_is_proven_pre_submit_and_releases_project(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="f" * 32),
        run_factory=version_runner,
        popen_factory=model_switcher_no_cookie_popen,
    )
    run_dir = Path(result["run_dir"])
    state = runner.STATE.load_state(run_dir / "state.json")

    assert result["status"] == "pre_submit_failed"
    assert result["safe_for_fresh_run"] is True
    assert state["session_authority"] == "pre_submit"
    assert state["transport_status"] == "failed_pre_submit"
    assert state["task_outcome"] == "pending"
    assert state["pre_submit_failure"]["code"] == "ORACLE_MODEL_SWITCHER_PRE_SUBMIT_FAILED"
    assert runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(pro_manifest(tmp_path)).run_root,
        tmp_path,
    ) == []


def test_model_switcher_failure_with_a_conversation_url_does_not_release_lock(tmp_path: Path) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="g" * 32),
        run_factory=version_runner,
        popen_factory=model_switcher_no_cookie_popen,
    )
    state_path = Path(initial["run_dir"]) / "state.json"
    legacy = runner.STATE.load_state(state_path)
    legacy["session_authority"] = "submitted_unknown"
    legacy["oracle"]["conversation_url"] = "https://chatgpt.com/c/exact-submitted-session"
    legacy.pop("pre_submit_failure", None)
    runner.STATE.write_json_atomic(state_path, legacy)
    assert runner.STATE.settle_proven_pre_submit_failure(state_path) is None
    assert runner.STATE.load_state(state_path)["session_authority"] == "submitted_unknown"


def test_user_confirmed_model_selector_button_failure_is_hash_bound_and_releases_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = load_runner()
    isolated_default_oracle_profile(tmp_path, monkeypatch)
    session_root = tmp_path / "oracle-sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))
    initial = execute_run(
        runner,
        pro_readonly_manifest(tmp_path, run_id="7" * 32),
        run_factory=version_0171_runner,
        popen_factory=model_selector_button_pre_submit_popen(session_root),
    )
    run_dir = Path(initial["run_dir"])
    state_path = run_dir / "state.json"
    state = runner.STATE.load_state(state_path)
    slug = state["oracle"]["slug"]
    (run_dir / "recovery-live-stdout.log").write_text(
        f'No live ChatGPT tab matched session "{slug}". Attempting recovery by reopening the saved conversation URL.\n',
        encoding="utf-8",
    )
    (run_dir / "recovery-live-stderr.log").write_text(
        "Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL.\n",
        encoding="utf-8",
    )

    assert initial["result"]["status"] == "attention_required"
    settled = runner.settle_user_confirmed_no_submission(
        run_dir,
        confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
        reason="user confirmed the exact selector failure created no prompt or conversation",
    )
    proof = runner.STATE.proven_user_confirmed_no_submission(state_path)
    receipt = json.loads(
        (run_dir / "user-confirmed-no-submission.json").read_text(encoding="utf-8")
    )

    assert settled["ok"] is True
    assert settled["safe_for_fresh_run"] is True
    assert settled["result"]["session_authority"] == "pre_submit"
    assert settled["result"]["task_outcome_reason"] == (
        "user-confirmed-no-submission-after-model-selector-failure"
    )
    assert proof is not None
    assert proof["pre_submit_marker"] == "oracle-model-selector-button-missing/v1"
    assert proof["prompt_submitted"] is False
    assert proof["tab_url"] == "https://chatgpt.com/"
    assert receipt["oracle_meta_sha256"] == hashlib.sha256(
        (session_root / slug / "meta.json").read_bytes()
    ).hexdigest()
    assert runner.STATE.unresolved_project_sessions(run_dir.parent, tmp_path) == []

    meta_path = session_root / slug / "meta.json"
    tampered = json.loads(meta_path.read_text(encoding="utf-8"))
    tampered["browser"]["runtime"]["promptSubmitted"] = True
    meta_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert runner.STATE.proven_user_confirmed_no_submission(state_path) is None
    assert [
        owner["run_id"]
        for owner in runner.STATE.unresolved_project_sessions(run_dir.parent, tmp_path)
    ] == ["7" * 32]


@pytest.mark.parametrize(
    "variation",
    (
        "prompt-submitted",
        "conversation-url",
        "different-stage",
        "different-error",
        "output-present",
        "missing-meta",
    ),
)
def test_model_selector_button_user_settlement_keeps_lock_on_incomplete_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variation: str,
) -> None:
    runner = load_runner()
    isolated_default_oracle_profile(tmp_path, monkeypatch)
    session_root = tmp_path / "oracle-sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))
    initial = execute_run(
        runner,
        pro_readonly_manifest(tmp_path, run_id="8" * 32),
        run_factory=version_0171_runner,
        popen_factory=model_selector_button_pre_submit_popen(
            session_root,
            variation=variation,
        ),
    )
    run_dir = Path(initial["run_dir"])
    state_path = run_dir / "state.json"
    state = runner.STATE.load_state(state_path)
    slug = state["oracle"]["slug"]
    (run_dir / "recovery-live-stdout.log").write_text(
        f'No live ChatGPT tab matched session "{slug}". Attempting recovery by reopening the saved conversation URL.\n',
        encoding="utf-8",
    )
    (run_dir / "recovery-live-stderr.log").write_text(
        "Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL.\n",
        encoding="utf-8",
    )

    with pytest.raises(
        runner.STATE.OracleStateError,
        match="run lacks the exact pre-submit UI",
    ):
        runner.settle_user_confirmed_no_submission(
            run_dir,
            confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
            reason="insufficient exact evidence must remain fail closed",
        )
    assert runner.STATE.load_state(state_path)["session_authority"] == "submitted_unknown"


def test_recovery_repairs_legacy_profile_copy_ebusy_without_oracle_call(tmp_path: Path) -> None:
    runner = load_runner()
    seed = tmp_path.parent / f"{tmp_path.name}-profile"
    cookies = seed / "Default" / "Network" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_text("seed", encoding="utf-8")
    initial = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="d" * 32, copy_profile=str(seed)),
        run_factory=version_runner,
        popen_factory=profile_copy_ebusy_popen,
    )
    run_dir = Path(initial["run_dir"])
    state_path = run_dir / "state.json"
    legacy = runner.STATE.load_state(state_path)
    legacy.update({"session_authority": "submitted_unknown", "transport_status": "failed", "task_outcome": "pending"})
    legacy.pop("pre_submit_failure", None)
    runner.STATE.write_json_atomic(state_path, legacy)
    calls: list[bool] = []

    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=lambda *args, **kwargs: calls.append(True),
    )
    settled = runner.STATE.load_state(state_path)

    assert recovered["status"] == "pre_submit_failed"
    assert recovered["safe_for_fresh_run"] is True
    assert settled["session_authority"] == "pre_submit"
    assert settled["task_outcome"] == "not_executed"
    assert calls == []


def test_recovery_repairs_legacy_manual_login_profile_lock_without_oracle_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = load_runner()
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(tmp_path / "profile-not-created"))
    initial = execute_run(
        runner,
        pro_readonly_manifest(tmp_path, run_id="4" * 32),
        run_factory=version_0171_runner,
        popen_factory=manual_login_profile_uninitialized_popen,
    )
    run_dir = Path(initial["run_dir"])
    state_path = run_dir / "state.json"
    legacy = runner.STATE.load_state(state_path)
    legacy.update(
        {
            "status": "attention_required",
            "session_authority": "submitted_unknown",
            "transport_status": "failed",
            "task_outcome": "pending",
            "task_outcome_reason": None,
        }
    )
    legacy.pop("pre_submit_failure", None)
    runner.STATE.write_json_atomic(state_path, legacy)
    calls: list[bool] = []

    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=lambda *args, **kwargs: calls.append(True),
    )
    settled = runner.STATE.load_state(state_path)

    assert recovered["status"] == "pre_submit_failed"
    assert recovered["safe_for_fresh_run"] is True
    assert settled["session_authority"] == "pre_submit"
    assert settled["task_outcome"] == "not_executed"
    assert (
        settled["pre_submit_failure"]["code"]
        == "ORACLE_MANUAL_LOGIN_PROFILE_UNINITIALIZED_PRELAUNCH_FAILED"
    )
    assert calls == []


def test_cdp_disconnect_with_exact_unsent_oracle_ledger_is_pre_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_default_oracle_profile(tmp_path, monkeypatch)
    runner = load_runner()
    monkeypatch.setattr(runner.STATE, "profile_copy_is_supported", lambda **kwargs: True)
    session_root = tmp_path / "oracle-sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))

    result = execute_run(
        runner,
        pro_readonly_manifest(tmp_path, run_id="c" * 32),
        run_factory=version_0171_runner,
        popen_factory=cdp_disconnect_pre_submit_popen(session_root),
    )
    state = runner.STATE.load_state(Path(result["run_dir"]) / "state.json")

    assert result["status"] == "pre_submit_failed"
    assert result["safe_for_fresh_run"] is True
    assert state["session_authority"] == "pre_submit"
    assert state["transport_status"] == "failed_pre_submit"
    assert state["task_outcome"] == "not_executed"
    assert state["task_outcome_reason"] == "oracle-cdp-disconnect-pre-submit"
    assert state["pre_submit_failure"]["code"] == "ORACLE_CDP_DISCONNECT_PRE_SUBMIT_FAILED"
    assert state["pre_submit_failure"]["prompt_submitted"] is False


@pytest.mark.parametrize(
    "variation",
    ["prompt-submitted", "conversation-url", "output-present", "different-error"],
)
def test_cdp_disconnect_keeps_lock_when_unsent_proof_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variation: str,
) -> None:
    isolated_default_oracle_profile(tmp_path, monkeypatch)
    runner = load_runner()
    monkeypatch.setattr(runner.STATE, "profile_copy_is_supported", lambda **kwargs: True)
    session_root = tmp_path / "oracle-sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))

    result = execute_run(
        runner,
        pro_readonly_manifest(tmp_path, run_id=(variation[0] * 32)),
        run_factory=version_0171_runner,
        popen_factory=cdp_disconnect_pre_submit_popen(session_root, variation=variation),
    )
    state = runner.STATE.load_state(Path(result["run_dir"]) / "state.json")

    assert result["ok"] is False
    assert result["result"]["status"] == "attention_required"
    assert state["session_authority"] == "submitted_unknown"
    assert "pre_submit_failure" not in state


def test_recovery_repairs_legacy_unsent_cdp_disconnect_without_oracle_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_default_oracle_profile(tmp_path, monkeypatch)
    runner = load_runner()
    monkeypatch.setattr(runner.STATE, "profile_copy_is_supported", lambda **kwargs: True)
    session_root = tmp_path / "oracle-sessions"
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))
    initial = execute_run(
        runner,
        pro_readonly_manifest(tmp_path, run_id="e" * 32),
        run_factory=version_0171_runner,
        popen_factory=cdp_disconnect_pre_submit_popen(session_root),
    )
    run_dir = Path(initial["run_dir"])
    state_path = run_dir / "state.json"
    legacy = runner.STATE.load_state(state_path)
    legacy.update(
        {
            "status": "attention_required",
            "session_authority": "submitted_unknown",
            "transport_status": "failed",
            "task_outcome": "pending",
            "task_outcome_reason": None,
        }
    )
    legacy.pop("pre_submit_failure", None)
    runner.STATE.write_json_atomic(state_path, legacy)
    calls: list[bool] = []

    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=lambda *args, **kwargs: calls.append(True),
    )
    settled = runner.STATE.load_state(state_path)

    assert recovered["status"] == "pre_submit_failed"
    assert recovered["safe_for_fresh_run"] is True
    assert settled["session_authority"] == "pre_submit"
    assert settled["task_outcome"] == "not_executed"
    assert settled["pre_submit_failure"]["code"] == "ORACLE_CDP_DISCONNECT_PRE_SUBMIT_FAILED"
    assert calls == []


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


def test_version_resolution_timeout_is_proven_pre_submit_and_releases_project(tmp_path: Path) -> None:
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path, run_id="c" * 32),
        run_factory=version_timeout_runner,
        popen_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Oracle must not launch after version timeout")
        ),
    )
    run_dir = Path(result["run_dir"])
    state = runner.STATE.load_state(run_dir / "state.json")

    assert result["status"] == "pre_submit_failed"
    assert result["safe_for_fresh_run"] is True
    assert state["session_authority"] == "pre_submit"
    assert state["transport_status"] == "failed_pre_submit"
    assert state["pre_submit_failure"]["code"] == "ORACLE_VERSION_RESOLUTION_PRELAUNCH_FAILED"
    assert state["pre_submit_failure"]["conversation_url_absent"] is True
    assert runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(manifest(tmp_path)).run_root,
        tmp_path,
    ) == []


def test_recovery_repairs_legacy_version_timeout_authority_without_oracle_call(tmp_path: Path) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        manifest(tmp_path, run_id="d" * 32),
        run_factory=version_timeout_runner,
    )
    run_dir = Path(initial["run_dir"])
    state_path = run_dir / "state.json"
    legacy = json.loads(state_path.read_text(encoding="utf-8"))
    legacy["session_authority"] = "submitted_unknown"
    legacy["transport_status"] = "incomplete"
    legacy.pop("pre_submit_failure", None)
    state_path.write_text(json.dumps(legacy), encoding="utf-8")
    calls: list[bool] = []

    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=lambda *args, **kwargs: calls.append(True),
    )
    settled = runner.STATE.load_state(state_path)

    assert recovered["status"] == "pre_submit_failed"
    assert recovered["safe_for_fresh_run"] is True
    assert settled["session_authority"] == "pre_submit"
    assert calls == []


def test_recovery_no_session_keeps_pre_submit_authority_and_allows_fresh_attempt(tmp_path: Path) -> None:
    runner = load_runner()
    config = runner.STATE.load_manifest(manifest(tmp_path, run_id="e" * 32))
    layout = runner.STATE.create_layout(config, run_id=config.requested_run_id)
    layout.run_dir.mkdir(parents=True)
    runner.STATE.write_json_atomic(
        layout.state_path,
        runner.STATE.state_payload(config, layout, status="failed", resolved_version="oracle 0.17.1"),
    )
    for path in (layout.stdout_path, layout.stderr_path):
        path.touch()

    def no_session(command, **kwargs):
        kwargs["stderr"].write(f"No session found with ID {layout.slug}.\n".encode())
        kwargs["stderr"].flush()
        return Process(1, [])

    recovered = runner.recover_run(
        layout.run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=no_session,
    )
    settled = runner.STATE.load_state(layout.state_path)

    assert recovered["status"] == "pre_submit_session_absent"
    assert recovered["safe_for_fresh_run"] is True
    assert settled["session_authority"] == "pre_submit"
    assert settled["pre_submit_session_absence"]["oracle_locator"] == layout.slug
    proof = runner.STATE.proven_pre_submit_session_absence(layout.state_path)
    assert proof is not None
    assert proof["code"] == "ORACLE_EXACT_SESSION_NOT_FOUND"
    (layout.run_dir / "recovery-harvest-stderr.log").write_text(
        "No session found with ID oracle-other-session.\n", encoding="utf-8"
    )
    assert runner.STATE.proven_pre_submit_session_absence(layout.state_path) is None


def test_recovery_no_session_never_releases_submitted_unknown_run(tmp_path: Path) -> None:
    runner = load_runner()
    config = runner.STATE.load_manifest(manifest(tmp_path, run_id="f" * 32))
    layout = runner.STATE.create_layout(config, run_id=config.requested_run_id)
    layout.run_dir.mkdir(parents=True)
    state = runner.STATE.state_payload(config, layout, status="attention_required", resolved_version="oracle 0.17.1")
    state["session_authority"] = "submitted_unknown"
    runner.STATE.write_json_atomic(layout.state_path, state)
    for path in (layout.stdout_path, layout.stderr_path):
        path.touch()

    def no_session(command, **kwargs):
        kwargs["stderr"].write(f"No session found with ID {layout.slug}.\n".encode())
        kwargs["stderr"].flush()
        return Process(1, [])

    recovered = runner.recover_run(
        layout.run_dir,
        action="live",
        oracle_command=["oracle"],
        popen_factory=no_session,
    )
    settled = runner.STATE.load_state(layout.state_path)

    assert recovered["status"] == "attention_required"
    assert recovered.get("safe_for_fresh_run") is not True
    assert settled["session_authority"] == "submitted_unknown"


def test_user_confirmed_no_submission_is_hash_bound_idempotent_and_fail_closed(tmp_path: Path) -> None:
    runner = load_runner()
    run_id = "a" * 32
    workflow_id = "b4362f04-3cf2-4f5e-b6a2-8d9443175298"
    parallel_parent_id = hashlib.sha256(workflow_id.encode("utf-8")).hexdigest()
    manifest_path = manifest(
        tmp_path,
        run_id=run_id,
        parallel_parent_id=parallel_parent_id,
    )
    input_mission = tmp_path / "input.md"
    input_mission.write_text("bound input", encoding="utf-8")
    input_sha = hashlib.sha256(input_mission.read_bytes()).hexdigest()
    (tmp_path / "mission.md").write_text(
        "\n".join((
            "mission body",
            "",
            "[HOST_STAGE_CONTRACT]",
            f"workflow_id={workflow_id}",
            "stage=implementation",
            f"attempt_id={run_id}",
            f"input_mission_sha256={input_sha}",
            f"exact_project_root={tmp_path.resolve()}",
            f"exact_input_mission_path={input_mission.resolve()}",
            f"Write the small UTF-8 stage receipt to: {(tmp_path / 'stage-result.json').resolve()}",
            "",
            "[DEVSPACE_WORKSPACE_ENTRY_CONTRACT]",
            "workspace body",
            "",
        )),
        encoding="utf-8",
    )

    def prompt_not_observed(command, **kwargs):
        slug = command[command.index("--slug") + 1]
        kwargs["stdout"].write(
            (
                f"Session: {slug}\n"
                "ERROR: Prompt did not appear in conversation before timeout (send may have failed)\n"
            ).encode()
        )
        kwargs["stdout"].flush()
        return Process(1, [])

    failed = execute_run(
        runner,
        manifest_path,
        run_factory=version_runner,
        popen_factory=prompt_not_observed,
    )
    run_dir = Path(failed["run_dir"])
    state_path = run_dir / "state.json"
    state = runner.STATE.load_state(state_path)
    slug = state["oracle"]["slug"]
    recovery_stdout = run_dir / "recovery-harvest-stdout.log"
    recovery_stderr = run_dir / "recovery-harvest-stderr.log"
    recovery_stdout.write_text(
        f'No live ChatGPT tab matched session "{slug}". Attempting recovery.\n',
        encoding="utf-8",
    )
    recovery_stderr.write_text(
        "Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL.\n",
        encoding="utf-8",
    )

    assert runner.exact_recovery_binding_unavailable(recovery_stdout, recovery_stderr) is True
    settled = runner.settle_user_confirmed_no_submission(
        run_dir,
        confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
        reason="user inspected the exact ChatGPT state and confirmed no submission",
    )
    settlement_path = run_dir / "user-confirmed-no-submission.json"
    proof = runner.STATE.proven_user_confirmed_no_submission(state_path)

    assert settled["ok"] is True
    assert settled["safe_for_fresh_run"] is True
    assert settled["result"]["session_authority"] == "pre_submit"
    assert proof is not None
    assert proof["workflow_id"] == workflow_id
    assert proof["stage"] == "implementation"
    assert proof["attempt_id"] == run_id
    assert proof["input_mission_sha256"] == input_sha
    assert settlement_path.is_file()
    # Repeating the exact adjudication is idempotent and launches nothing.
    repeated = runner.settle_user_confirmed_no_submission(
        run_dir,
        confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
        reason="user inspected the exact ChatGPT state and confirmed no submission",
    )
    assert repeated["result"] == settled["result"]
    other_run_id = "9" * 32
    other_state_path = run_dir.parent / other_run_id / "state.json"
    other_state_path.parent.mkdir()
    other_state = {
        "schema": "codex.chatgpt.oracle-run-state/v1",
        "run_id": other_run_id,
        "project_root": str(tmp_path.resolve()),
        "status": "running",
        "session_authority": "submitted_unknown",
        "oracle": {"session_locator": "oracle-project-other"},
    }
    runner.STATE.write_json_atomic(other_state_path, other_state)
    blocked = runner.settle_user_confirmed_no_submission(
        run_dir,
        confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
        reason="user inspected the exact ChatGPT state and confirmed no submission",
    )
    assert blocked["safe_for_fresh_run"] is False
    assert [owner["run_id"] for owner in blocked["unresolved_owners"]] == [other_run_id]
    other_state.update({"status": "attention_required", "session_authority": "pre_submit"})
    runner.STATE.write_json_atomic(other_state_path, other_state)
    assert runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(manifest_path).run_root,
        tmp_path,
        parallel_parent_id="e" * 64,
    ) == []

    reference = settled["result"]["user_confirmed_no_submission"]
    missing_reference_state = runner.STATE.load_state(state_path)
    missing_reference_state.pop("user_confirmed_no_submission")
    runner.STATE.write_json_atomic(state_path, missing_reference_state)
    owners = runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(manifest_path).run_root,
        tmp_path,
        parallel_parent_id=parallel_parent_id,
    )
    assert owners[0]["run_id"] == run_id
    restored = runner.STATE.load_state(state_path)
    restored["user_confirmed_no_submission"] = reference
    runner.STATE.write_json_atomic(state_path, restored)

    # Any contradictory later recovery revokes the release even though the
    # original no-tab/no-URL recovery still exists.
    (run_dir / "recovery-live-stdout.log").write_text(
        "State: running\n",
        encoding="utf-8",
    )
    (run_dir / "recovery-live-stderr.log").write_text("", encoding="utf-8")
    assert runner.STATE.proven_user_confirmed_no_submission(state_path) is None
    owners = runner.STATE.unresolved_project_sessions(
        runner.STATE.load_manifest(manifest_path).run_root,
        tmp_path,
        parallel_parent_id="e" * 64,
    )
    assert owners[0]["run_id"] == run_id


def test_standalone_qualified_pro_prompt_timeout_can_be_user_settled_and_unlocks(tmp_path: Path) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        pro_readonly_manifest(tmp_path, run_id="b" * 32),
        run_factory=version_0171_runner,
        popen_factory=prompt_not_observed_popen,
    )
    run_dir = Path(initial["run_dir"])
    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=recovery_binding_unavailable_popen,
    )

    assert recovered["status"] == "recovery_binding_unavailable"
    settled = runner.settle_user_confirmed_no_submission(
        run_dir,
        confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
        reason="user inspected the exact ChatGPT history and confirmed no prompt or response",
    )
    proof = runner.STATE.proven_user_confirmed_no_submission(run_dir / "state.json")

    assert settled["safe_for_fresh_run"] is True
    assert settled["unresolved_owners"] == []
    assert settled["result"]["session_authority"] == "pre_submit"
    assert proof is not None
    assert proof["settlement_eligibility"] == "oracle-standalone-qualified-pro/v1"
    assert proof["transport"] == "pro-devspace"
    assert proof["oracle_version"] == "0.17.1"
    assert proof["source_mission_sha256"] == proof["transport_mission_sha256"]


def test_standalone_pro_attachment_upload_timeout_is_hash_bound_and_user_settled(tmp_path: Path) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="a" * 32),
        run_factory=version_0171_runner,
        popen_factory=attachment_upload_timeout_popen,
    )
    run_dir = Path(initial["run_dir"])
    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=recovery_binding_unavailable_popen,
    )

    assert recovered["status"] == "recovery_binding_unavailable"
    with pytest.raises(runner.STATE.OracleStateError) as exc:
        runner.settle_user_confirmed_no_submission(
            run_dir,
            confirmation="not-the-user-token",
            reason="user inspected the exact ChatGPT history",
        )
    assert exc.value.code == "NO_SUBMISSION_CONFIRMATION_REQUIRED"

    settled = runner.settle_user_confirmed_no_submission(
        run_dir,
        confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
        reason="user inspected the exact ChatGPT history and confirmed no prompt or response",
    )
    proof = runner.STATE.proven_user_confirmed_no_submission(run_dir / "state.json")

    assert settled["safe_for_fresh_run"] is True
    assert settled["unresolved_owners"] == []
    assert settled["result"]["session_authority"] == "pre_submit"
    assert proof is not None
    assert proof["settlement_eligibility"] == "oracle-standalone-pro-attachment/v1"
    assert proof["transport"] == "pro-attachment-only"
    assert proof["oracle_version"] == "0.17.1"
    assert proof["pre_submit_marker"] == "Attachments did not finish uploading before timeout."
    assert proof["source_mission_sha256"] == proof["transport_mission_sha256"]
    assert len(proof["attachment_evidence"]) == 2
    assert len(proof["attachment_manifest_sha256"]) == 64


def test_settled_attachment_run_survives_later_non_mission_source_edits(tmp_path: Path) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="b" * 32),
        run_factory=version_0171_runner,
        popen_factory=attachment_upload_timeout_popen,
    )
    run_dir = Path(initial["run_dir"])
    runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=recovery_binding_unavailable_popen,
    )
    runner.settle_user_confirmed_no_submission(
        run_dir,
        confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
        reason="user inspected the exact ChatGPT history and confirmed no prompt or response",
    )
    state_path = run_dir / "state.json"
    state = runner.STATE.load_state(state_path)

    Path(state["attachments"][1]["path"]).write_text(
        "later legitimate project policy edit", encoding="utf-8"
    )

    proof = runner.STATE.proven_user_confirmed_no_submission(state_path)
    owners = runner.STATE.unresolved_project_sessions(run_dir.parent, tmp_path)

    assert proof is not None
    assert proof["attachment_evidence"] == json.loads(
        (run_dir / "user-confirmed-no-submission.json").read_text(encoding="utf-8")
    )["attachment_evidence"]
    assert owners == []


def test_settled_attachment_run_still_locks_when_bound_metadata_is_changed(tmp_path: Path) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="c" * 32),
        run_factory=version_0171_runner,
        popen_factory=attachment_upload_timeout_popen,
    )
    run_dir = Path(initial["run_dir"])
    runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=recovery_binding_unavailable_popen,
    )
    runner.settle_user_confirmed_no_submission(
        run_dir,
        confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
        reason="user inspected the exact ChatGPT history and confirmed no prompt or response",
    )
    state_path = run_dir / "state.json"
    state = runner.STATE.load_state(state_path)
    state["attachments"][1]["sha256"] = "0" * 64
    runner.STATE.write_json_atomic(state_path, state)

    assert runner.STATE.proven_user_confirmed_no_submission(state_path) is None
    owners = runner.STATE.unresolved_project_sessions(run_dir.parent, tmp_path)
    assert len(owners) == 1
    assert owners[0]["session_authority"] == "submitted_unknown"


@pytest.mark.parametrize(
    "contradiction",
    (
        "output", "conversation_url", "version", "locator", "mission_transport",
        "attachment_bytes", "attachment_hash", "attachment_size", "recovery_state",
        "stderr", "marker",
    ),
)
def test_standalone_pro_attachment_upload_timeout_keeps_lock_on_any_contradiction(
    tmp_path: Path,
    contradiction: str,
) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        pro_manifest(tmp_path, run_id="d" * 32),
        run_factory=version_0171_runner,
        popen_factory=attachment_upload_timeout_popen,
    )
    run_dir = Path(initial["run_dir"])
    runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=recovery_binding_unavailable_popen,
    )
    state_path = run_dir / "state.json"
    state = runner.STATE.load_state(state_path)
    if contradiction == "output":
        (run_dir / "output.md").write_text("unexpected durable answer", encoding="utf-8")
    elif contradiction == "conversation_url":
        state["oracle"]["conversation_url"] = "https://chatgpt.com/c/submitted"
        runner.STATE.write_json_atomic(state_path, state)
    elif contradiction == "version":
        state["oracle"]["resolved_version"] = "0.17.2"
        runner.STATE.write_json_atomic(state_path, state)
    elif contradiction == "locator":
        state["oracle"]["session_locator"] = "oracle-other-deadbeef00"
        runner.STATE.write_json_atomic(state_path, state)
    elif contradiction == "mission_transport":
        (run_dir / "mission.md").write_text("changed transport mission", encoding="utf-8")
    elif contradiction == "attachment_bytes":
        Path(state["attachments"][1]["path"]).write_bytes(b"changed attachment")
    elif contradiction == "attachment_hash":
        state["attachments"][1]["sha256"] = "0" * 64
        runner.STATE.write_json_atomic(state_path, state)
    elif contradiction == "attachment_size":
        state["attachments"][1]["size_bytes"] += 1
        runner.STATE.write_json_atomic(state_path, state)
    elif contradiction == "recovery_state":
        (run_dir / "recovery-harvest-stdout.log").write_text("State: running\n", encoding="utf-8")
    elif contradiction == "stderr":
        (run_dir / "stderr.log").write_text("unexpected browser error\n", encoding="utf-8")
    elif contradiction == "marker":
        changed = (run_dir / "stdout.log").read_text(encoding="utf-8").replace(
            "Attachments did not finish uploading before timeout.",
            "Attachments may still be uploading.",
        )
        (run_dir / "stdout.log").write_text(changed, encoding="utf-8")
        (run_dir / "transcript.md").write_text(changed, encoding="utf-8")

    with pytest.raises(runner.STATE.OracleStateError) as exc:
        runner.settle_user_confirmed_no_submission(
            run_dir,
            confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
            reason="user inspected the exact ChatGPT history and confirmed no prompt or response",
        )

    assert exc.value.code == "NO_SUBMISSION_EVIDENCE_INCOMPLETE"
    assert runner.STATE.load_state(state_path)["session_authority"] == "submitted_unknown"


@pytest.mark.parametrize(
    "contradiction",
    ("output", "conversation_url", "version", "mission", "recovery_state", "stderr", "transport"),
)
def test_standalone_qualified_pro_prompt_timeout_keeps_lock_when_evidence_is_incomplete(
    tmp_path: Path,
    contradiction: str,
) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        pro_readonly_manifest(tmp_path, run_id="c" * 32),
        run_factory=version_0171_runner,
        popen_factory=prompt_not_observed_popen,
    )
    run_dir = Path(initial["run_dir"])
    runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=recovery_binding_unavailable_popen,
    )
    state_path = run_dir / "state.json"
    state = runner.STATE.load_state(state_path)
    if contradiction == "output":
        (run_dir / "output.md").write_text("unexpected durable answer", encoding="utf-8")
    elif contradiction == "conversation_url":
        state["oracle"]["conversation_url"] = "https://chatgpt.com/c/exact-submitted-session"
        runner.STATE.write_json_atomic(state_path, state)
    elif contradiction == "version":
        state["oracle"]["resolved_version"] = "0.17.2"
        runner.STATE.write_json_atomic(state_path, state)
    elif contradiction == "mission":
        Path(state["mission"]["path"]).write_text("changed mission", encoding="utf-8")
    elif contradiction == "recovery_state":
        (run_dir / "recovery-harvest-stdout.log").write_text("State: running\n", encoding="utf-8")
    elif contradiction == "stderr":
        (run_dir / "stderr.log").write_text("unexpected browser error\n", encoding="utf-8")
    elif contradiction == "transport":
        state["transport"] = "regular-devspace"
        runner.STATE.write_json_atomic(state_path, state)

    with pytest.raises(runner.STATE.OracleStateError) as exc:
        runner.settle_user_confirmed_no_submission(
            run_dir,
            confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
            reason="user inspected the exact ChatGPT history and confirmed no prompt or response",
        )

    assert exc.value.code == "NO_SUBMISSION_EVIDENCE_INCOMPLETE"
    assert runner.STATE.load_state(state_path)["session_authority"] == "submitted_unknown"


def test_user_confirmation_rejects_bare_bindings_without_host_contract(tmp_path: Path) -> None:
    runner = load_runner()
    run_id = "e" * 32
    workflow_id = "b4362f04-3cf2-4f5e-b6a2-8d9443175298"
    parent_id = hashlib.sha256(workflow_id.encode("utf-8")).hexdigest()
    manifest_path = manifest(tmp_path, run_id=run_id, parallel_parent_id=parent_id)
    (tmp_path / "mission.md").write_text(
        "\n".join((
            f"workflow_id={workflow_id}",
            "stage=implementation",
            f"attempt_id={run_id}",
            f"input_mission_sha256={'f' * 64}",
            "",
        )),
        encoding="utf-8",
    )

    def prompt_not_observed(command, **kwargs):
        slug = command[command.index("--slug") + 1]
        kwargs["stdout"].write(
            (
                f"Session: {slug}\n"
                "ERROR: Prompt did not appear in conversation before timeout (send may have failed)\n"
            ).encode()
        )
        kwargs["stdout"].flush()
        return Process(1, [])

    failed = execute_run(
        runner,
        manifest_path,
        run_factory=version_runner,
        popen_factory=prompt_not_observed,
    )
    run_dir = Path(failed["run_dir"])
    state = runner.STATE.load_state(run_dir / "state.json")
    slug = state["oracle"]["slug"]
    (run_dir / "recovery-harvest-stdout.log").write_text(
        f'No live ChatGPT tab matched session "{slug}". Attempting recovery.\n',
        encoding="utf-8",
    )
    (run_dir / "recovery-harvest-stderr.log").write_text(
        "Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL.\n",
        encoding="utf-8",
    )

    with pytest.raises(runner.STATE.OracleStateError) as exc:
        runner.settle_user_confirmed_no_submission(
            run_dir,
            confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
            reason="user said no submission",
        )
    assert exc.value.code == "NO_SUBMISSION_EVIDENCE_INCOMPLETE"


def test_user_confirmation_cannot_replace_missing_recovery_evidence(tmp_path: Path) -> None:
    runner = load_runner()
    config = runner.STATE.load_manifest(manifest(tmp_path, run_id="f" * 32))
    layout = runner.STATE.create_layout(config, run_id=config.requested_run_id)
    layout.run_dir.mkdir(parents=True)
    state = runner.STATE.state_payload(config, layout, status="attention_required", resolved_version="0.17.1")
    state["session_authority"] = "submitted_unknown"
    runner.STATE.write_json_atomic(layout.state_path, state)
    for path in (layout.stdout_path, layout.stderr_path):
        path.touch()

    with pytest.raises(runner.STATE.OracleStateError) as exc:
        runner.settle_user_confirmed_no_submission(
            layout.run_dir,
            confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
            reason="user said no submission",
        )
    assert exc.value.code == "NO_SUBMISSION_EVIDENCE_INCOMPLETE"


def test_direct_devspace_prompt_not_observed_recovery_is_hash_bound_before_user_settlement(tmp_path: Path) -> None:
    runner = load_runner()
    failed = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_0171_runner,
        popen_factory=prompt_not_observed_popen,
    )
    run_dir = Path(failed["run_dir"])
    state_path = run_dir / "state.json"

    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=recovery_binding_unavailable_popen,
    )

    assert recovered["status"] == "recovery_binding_unavailable"
    state = runner.STATE.load_state(state_path)
    assert state["session_authority"] == "submitted_unknown"
    receipt = run_dir / "prompt-not-observed-recovery.json"
    assert receipt.is_file()
    assert state["prompt_not_observed_recovery"]["sha256"] == runner.STATE.sha256_file(receipt)

    settled = runner.settle_user_confirmed_no_submission(
        run_dir,
        confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
        reason="user inspected the exact ChatGPT history and confirmed no prompt or response",
    )
    proof = runner.STATE.proven_user_confirmed_no_submission(state_path)

    assert settled["result"]["session_authority"] == "pre_submit"
    assert proof is not None
    assert proof["settlement_eligibility"] == "oracle-direct-devspace/v1"
    original = (run_dir / "recovery-harvest-stdout.log").read_text(encoding="utf-8")
    (run_dir / "recovery-harvest-stdout.log").write_text(
        original + "https://chatgpt.com/c/observed-after-the-fact\n", encoding="utf-8"
    )
    assert runner.STATE.proven_user_confirmed_no_submission(state_path) is None


def test_direct_devspace_user_confirmation_backfills_only_exact_legacy_recovery_evidence(tmp_path: Path) -> None:
    runner = load_runner()
    failed = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_0171_runner,
        popen_factory=prompt_not_observed_popen,
    )
    run_dir = Path(failed["run_dir"])
    state_path = run_dir / "state.json"
    slug = runner.STATE.load_state(state_path)["oracle"]["slug"]
    (run_dir / "recovery-harvest-stdout.log").write_text(
        f'No live ChatGPT tab matched session "{slug}". Attempting recovery.\n', encoding="utf-8"
    )
    (run_dir / "recovery-harvest-stderr.log").write_text(
        "Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL.\n",
        encoding="utf-8",
    )

    settled = runner.settle_user_confirmed_no_submission(
        run_dir,
        confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
        reason="user confirmed this exact legacy run has no conversation",
    )

    assert settled["result"]["session_authority"] == "pre_submit"
    assert (run_dir / "prompt-not-observed-recovery.json").is_file()
    assert runner.STATE.proven_user_confirmed_no_submission(state_path) is not None


def test_direct_devspace_live_recovery_stays_fail_closed_even_with_user_confirmation(tmp_path: Path) -> None:
    runner = load_runner()
    failed = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_0171_runner,
        popen_factory=prompt_not_observed_popen,
    )
    run_dir = Path(failed["run_dir"])
    (run_dir / "recovery-harvest-stdout.log").write_text("State: running\n", encoding="utf-8")
    (run_dir / "recovery-harvest-stderr.log").write_text("", encoding="utf-8")

    with pytest.raises(runner.STATE.OracleStateError) as exc:
        runner.settle_user_confirmed_no_submission(
            run_dir,
            confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION,
            reason="user confirmation cannot override observed live session",
        )

    assert exc.value.code == "NO_SUBMISSION_EVIDENCE_INCOMPLETE"
    assert runner.STATE.load_state(run_dir / "state.json")["session_authority"] == "submitted_unknown"


def test_direct_web_multi_child_no_submission_settlement_is_hash_bound(tmp_path: Path) -> None:
    runner = load_runner()
    parent_id = "d" * 64
    manifest_path = manifest(tmp_path, parallel_parent_id=parent_id)
    (tmp_path / "mission.md").write_text("direct web multi lane", encoding="utf-8")

    def prompt_not_observed(command, **kwargs):
        slug = command[command.index("--slug") + 1]
        kwargs["stdout"].write((f"Session: {slug}\nERROR: Prompt did not appear in conversation before timeout (send may have failed)\n").encode())
        kwargs["stdout"].flush()
        return Process(1, [])

    failed = execute_run(runner, manifest_path, run_factory=version_runner, popen_factory=prompt_not_observed)
    run_dir = Path(failed["run_dir"])
    state_path = run_dir / "state.json"
    state = runner.STATE.load_state(state_path)
    slug = state["oracle"]["slug"]
    oracle_output = tmp_path / "runtime" / "legacy" / "oracle_output"
    lane_dir = oracle_output / "lanes" / "lane"
    lane_dir.mkdir(parents=True)
    (lane_dir / "oracle.json").write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-run/v1", "project_root": str(tmp_path.resolve()),
        "mission_path": str((tmp_path / "mission.md").resolve()), "parallel_parent_id": parent_id,
    }), encoding="utf-8")
    (oracle_output / "result.json").write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-multi-result/v1", "parent_id": parent_id,
        "lanes": [{"id": "lane", "run_dir": str(run_dir), "session_locator": slug}],
    }), encoding="utf-8")
    (run_dir / "recovery-harvest-stdout.log").write_text(
        f'No live ChatGPT tab matched session "{slug}". Attempting recovery.\n', encoding="utf-8"
    )
    (run_dir / "recovery-harvest-stderr.log").write_text(
        "Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL.\n", encoding="utf-8"
    )

    settled = runner.settle_user_confirmed_no_submission(
        run_dir, confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION, reason="exact child inspected"
    )
    proof = runner.STATE.proven_user_confirmed_no_submission(state_path)

    assert settled["result"]["session_authority"] == "pre_submit"
    assert proof is not None
    assert proof["settlement_eligibility"] == "oracle-web-multi-child/v1"
    assert proof["provenance_mode"] == "legacy-result-lane/v1"
    assert proof["parallel_parent_id"] == parent_id
    assert proof["source_mission_sha256"] == proof["transport_mission_sha256"]
    assert proof["legacy_result_sha256"]
    assert proof["legacy_lane_manifest_sha256"]
    for path in (
        run_dir / "stdout.log", run_dir / "stderr.log", run_dir / "transcript.md",
        run_dir / "recovery-harvest-stdout.log", run_dir / "recovery-harvest-stderr.log",
    ):
        original = path.read_text(encoding="utf-8")
        path.write_text(original + "https://chatgpt.com/c/exact-child\n", encoding="utf-8")
        assert runner.STATE.proven_user_confirmed_no_submission(state_path) is None
        path.write_text(original, encoding="utf-8")
    for path, replacement in (
        (tmp_path / "mission.md", "changed source"),
        (run_dir / "mission.md", "changed transport"),
        (oracle_output / "lanes" / "lane" / "oracle.json", "{}"),
        (oracle_output / "result.json", "{}"),
    ):
        original = path.read_text(encoding="utf-8")
        path.write_text(replacement, encoding="utf-8")
        assert runner.STATE.proven_user_confirmed_no_submission(state_path) is None
        path.write_text(original, encoding="utf-8")
    (run_dir / "output.md").write_text("unexpected output", encoding="utf-8")
    assert runner.STATE.proven_user_confirmed_no_submission(state_path) is None


def test_direct_web_multi_child_settlement_requires_recovery_pair(tmp_path: Path) -> None:
    runner = load_runner()
    parent_id = "f" * 64
    config = runner.STATE.load_manifest(manifest(tmp_path, parallel_parent_id=parent_id))
    layout = runner.STATE.create_layout(config)
    layout.run_dir.mkdir(parents=True)
    layout.output_path.touch()
    layout.stdout_path.write_text(f"Session: {layout.slug}\nERROR: Prompt did not appear in conversation before timeout (send may have failed)\n", encoding="utf-8")
    layout.stderr_path.touch()
    layout.transcript_path.touch()
    (layout.run_dir / "mission.md").write_bytes(config.mission_path.read_bytes())
    oracle_output = tmp_path / "runtime" / "legacy" / "oracle_output"
    lane_dir = oracle_output / "lanes" / "lane"
    lane_dir.mkdir(parents=True)
    (lane_dir / "oracle.json").write_text(json.dumps({"schema": "codex.chatgpt.oracle-run/v1", "project_root": str(tmp_path.resolve()), "mission_path": str(config.mission_path), "parallel_parent_id": parent_id}), encoding="utf-8")
    (oracle_output / "result.json").write_text(json.dumps({"schema": "codex.chatgpt.oracle-multi-result/v1", "parent_id": parent_id, "lanes": [{"id": "lane", "run_dir": str(layout.run_dir), "session_locator": layout.slug}]}), encoding="utf-8")
    state = runner.STATE.state_payload(config, layout, status="attention_required", resolved_version="0.17.1")
    state["session_authority"] = "submitted_unknown"
    runner.STATE.write_json_atomic(layout.state_path, state)

    with pytest.raises(runner.STATE.OracleStateError) as exc:
        runner.settle_user_confirmed_no_submission(layout.run_dir, confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION, reason="no recovery")
    assert exc.value.code == "NO_SUBMISSION_EVIDENCE_INCOMPLETE"


def test_settlement_transcript_scan_uses_canonical_path_not_state_mapping(tmp_path: Path) -> None:
    runner = load_runner()
    config = runner.STATE.load_manifest(manifest(tmp_path))
    layout = runner.STATE.create_layout(config)
    layout.run_dir.mkdir(parents=True)
    layout.stdout_path.touch()
    layout.stderr_path.touch()
    state = runner.STATE.state_payload(config, layout, status="attention_required", resolved_version="0.17.1")
    runner.STATE.write_json_atomic(layout.state_path, state)
    layout.transcript_path.write_text("https://chatgpt.com/c/hidden-in-canonical\n", encoding="utf-8")
    state["artifacts"].pop("transcript")
    runner.STATE.write_json_atomic(layout.state_path, state)
    assert runner.STATE._settlement_logs_have_conversation_url(layout.state_path) is True

    layout.transcript_path.unlink()
    state["artifacts"]["transcript"] = str(tmp_path / "foreign.md")
    runner.STATE.write_json_atomic(layout.state_path, state)
    assert runner.STATE._settlement_logs_have_conversation_url(layout.state_path) is True
    state["artifacts"]["transcript"] = str(layout.transcript_path)
    runner.STATE.write_json_atomic(layout.state_path, state)
    assert runner.STATE._settlement_logs_have_conversation_url(layout.state_path) is False

    layout.transcript_path.write_bytes(b"\xff")
    assert runner.STATE._settlement_logs_have_conversation_url(layout.state_path) is True
    layout.transcript_path.unlink()
    target = tmp_path / "transcript-target.md"
    target.write_text("no url", encoding="utf-8")
    try:
        layout.transcript_path.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    assert runner.STATE._settlement_logs_have_conversation_url(layout.state_path) is True


@pytest.mark.parametrize("field", ["parallel_parent_id", "mission_sha256", "oracle_locator", "requested_run_id"])
def test_direct_web_multi_child_settlement_rejects_identity_mismatch(tmp_path: Path, field: str) -> None:
    runner = load_runner()
    config = runner.STATE.load_manifest(manifest(tmp_path, parallel_parent_id="b" * 64))
    layout = runner.STATE.create_layout(config, run_id=config.requested_run_id)
    layout.run_dir.mkdir(parents=True)
    (layout.run_dir / "mission.md").write_bytes(config.mission_path.read_bytes())
    layout.output_path.touch()
    layout.transcript_path.touch()
    layout.stdout_path.write_text(f"Session: {layout.slug}\nERROR: Prompt did not appear in conversation before timeout (send may have failed)\n", encoding="utf-8")
    layout.stderr_path.touch()
    (layout.run_dir / "recovery-harvest-stdout.log").write_text(f'No live ChatGPT tab matched session "{layout.slug}".\n', encoding="utf-8")
    (layout.run_dir / "recovery-harvest-stderr.log").write_text("Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL.\n", encoding="utf-8")
    oracle_output = tmp_path / "runtime" / "legacy" / "oracle_output"
    lane_dir = oracle_output / "lanes" / "lane"
    lane_dir.mkdir(parents=True)
    (lane_dir / "oracle.json").write_text(json.dumps({"schema": "codex.chatgpt.oracle-run/v1", "project_root": str(tmp_path.resolve()), "mission_path": str(config.mission_path), "parallel_parent_id": "b" * 64}), encoding="utf-8")
    (oracle_output / "result.json").write_text(json.dumps({"schema": "codex.chatgpt.oracle-multi-result/v1", "parent_id": "b" * 64, "lanes": [{"id": "lane", "run_dir": str(layout.run_dir), "session_locator": layout.slug}]}), encoding="utf-8")
    state = runner.STATE.state_payload(config, layout, status="attention_required", resolved_version="0.17.1")
    state["session_authority"] = "submitted_unknown"
    if field == "parallel_parent_id":
        state[field] = "invalid"
    elif field == "mission_sha256":
        state["mission"]["sha256"] = "0" * 64
    else:
        if field == "oracle_locator":
            state["oracle"]["session_locator"] = "oracle-foreign"
        else:
            state["requested_run_id"] = layout.run_id
    runner.STATE.write_json_atomic(layout.state_path, state)

    with pytest.raises((runner.STATE.OracleStateError, runner.OracleRunError)) as exc:
        runner.settle_user_confirmed_no_submission(layout.run_dir, confirmation=runner.STATE.USER_CONFIRMED_NO_SUBMISSION, reason="identity mismatch")
    assert exc.value.code in {"NO_SUBMISSION_EVIDENCE_INCOMPLETE", "SETTLEMENT_PARALLEL_PARENT_ID_INVALID"}


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


def test_delivery_timeout_after_visible_work_cannot_settle_a_terminal_harvest(tmp_path: Path) -> None:
    """Regression: ChatGPT can keep executing after Oracle sees this error text."""
    runner = load_runner()
    result = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(4, None, {}, []),
    )
    run_dir = Path(result["run_dir"])

    def timed_out_recovery(command, **kwargs):
        candidate = Path(command[command.index("--write-output") + 1])
        candidate.write_text("Message delivery timed out. Please try again.", encoding="utf-8")
        kwargs["stdout"].write(
            b"State: running\n"
            b"State: completed\n"
            b"Message delivery timed out. Please try again.\n"
        )
        kwargs["stdout"].flush()
        return Process(0, [])

    recovered = runner.recover_run(
        run_dir,
        action="live",
        oracle_command=["oracle"],
        popen_factory=timed_out_recovery,
    )
    state = runner.STATE.load_state(run_dir / "state.json")

    assert recovered["ok"] is False
    assert recovered["status"] == "provider_delivery_timeout"
    assert state["status"] == "running"
    assert state["session_authority"] == "live"
    assert state["terminal_harvested"] is False
    assert state["transport_status"] == "post_submit_provider_delivery_timeout"
    assert state["task_outcome"] == "pending"
    assert not Path(state["artifacts"]["output"]).exists()
    assert not (run_dir / "recovery-live-candidate.md").exists()


def provider_delivery_timeout_settlement_fixture(tmp_path: Path):
    runner = load_runner()
    initial = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(4, None, {}, []),
    )
    run_dir = Path(initial["run_dir"])
    state_path = run_dir / "state.json"
    state = runner.STATE.load_state(state_path)
    output = Path(state["artifacts"]["output"])
    output.write_text("Message delivery timed out. Please try again.", encoding="utf-8")
    (run_dir / "recovery-live-stdout.log").write_text(
        "State: running\nState: completed\nMessage delivery timed out. Please try again.\n",
        encoding="utf-8",
    )
    state.update({
        "status": "running",
        "session_authority": "live",
        "terminal_harvested": False,
        "transport_status": "post_submit_provider_delivery_timeout",
        "task_outcome": "pending",
    })
    state["oracle"]["conversation_url"] = "https://chatgpt.com/c/exact-timeout-settlement"
    runner.STATE.write_json_atomic(state_path, state)
    evidence = tmp_path / "execution-proof.json"
    evidence.write_text('{"executed":true}', encoding="utf-8")
    return runner, run_dir, output, evidence


def test_user_confirmed_delivery_timeout_execution_settlement_releases_exact_run(tmp_path: Path) -> None:
    runner, run_dir, output, evidence = provider_delivery_timeout_settlement_fixture(tmp_path)

    settled = runner.settle_user_confirmed_delivery_timeout_execution(
        run_dir,
        expected_output_sha256=runner.STATE.sha256_file(output),
        confirmation=runner.STATE.USER_CONFIRMED_EXECUTION_ENDED,
        reason="user confirmed the exact ChatGPT task ended after its BB-local artifacts were written",
        execution_evidence=[(evidence, runner.STATE.sha256_file(evidence))],
        process_alive=lambda _: False,
    )

    state = runner.STATE.load_state(run_dir / "state.json")
    assert settled["ok"] is True
    assert settled["safe_for_fresh_run"] is True
    assert state["session_authority"] == "settled_executed"
    assert state["terminal_harvested"] is False
    assert state["transport_status"] == "post_submit_provider_delivery_timeout_settled"
    assert state["task_outcome"] == "executed"
    assert state["oracle"]["conversation_url"]
    assert runner.STATE.proven_user_confirmed_execution_ended(run_dir / "state.json") is not None
    assert runner.STATE.unresolved_project_sessions(run_dir.parent, tmp_path) == []


def test_delivery_timeout_execution_settlement_reconstructs_stale_incomplete_terminal_ledger(tmp_path: Path) -> None:
    runner, run_dir, output, evidence = provider_delivery_timeout_settlement_fixture(tmp_path)
    # Timeline: an initial false terminal was repaired to the timeout state, then
    # a later harvest replaced the top-level ledger and rotated its stream logs.
    state = runner.STATE.load_state(run_dir / "state.json")
    state.update({"status": "attention_required", "session_authority": "terminal", "transport_status": "incomplete"})
    runner.STATE.write_json_atomic(run_dir / "state.json", state)
    state.update({"status": "running", "session_authority": "live", "transport_status": "post_submit_provider_delivery_timeout"})
    runner.STATE.write_json_atomic(run_dir / "state.json", state)
    (run_dir / "transcript.md").write_text(
        "State: running\nState: completed\nMessage delivery timed out. Please try again.\n",
        encoding="utf-8",
    )
    for name in ("recovery-live-stdout.log", "recovery-harvest-stdout.log"):
        (run_dir / name).write_text("No live ChatGPT tab matched session\n", encoding="utf-8")
    state = runner.STATE.load_state(run_dir / "state.json")
    state.update({
        "status": "attention_required",
        "session_authority": "terminal",
        "terminal_harvested": False,
        "transport_status": "incomplete",
        "task_outcome": "pending",
    })
    runner.STATE.write_json_atomic(run_dir / "state.json", state)

    settled = runner.settle_user_confirmed_delivery_timeout_execution(
        run_dir,
        expected_output_sha256=runner.STATE.sha256_file(output),
        confirmation=runner.STATE.USER_CONFIRMED_EXECUTION_ENDED,
        reason="user confirmed the exact task ended after its execution evidence was produced",
        execution_evidence=[(evidence, runner.STATE.sha256_file(evidence))],
        process_alive=lambda _: False,
    )

    assert settled["ok"] is True
    assert settled["result"]["session_authority"] == "settled_executed"


def test_delivery_timeout_execution_settlement_rejects_active_owned_process(tmp_path: Path) -> None:
    runner, run_dir, output, evidence = provider_delivery_timeout_settlement_fixture(tmp_path)
    state = runner.STATE.load_state(run_dir / "state.json")
    state["host_watchdog"] = {"oracle_process_pid": 4242}
    runner.STATE.write_json_atomic(run_dir / "state.json", state)

    with pytest.raises(runner.OracleRunError) as exc:
        runner.settle_user_confirmed_delivery_timeout_execution(
            run_dir,
            expected_output_sha256=runner.STATE.sha256_file(output),
            confirmation=runner.STATE.USER_CONFIRMED_EXECUTION_ENDED,
            reason="user confirmed completion",
            execution_evidence=[(evidence, runner.STATE.sha256_file(evidence))],
            process_alive=lambda pid: pid == 4242,
        )
    assert exc.value.code == "EXECUTION_ENDED_PROCESS_ACTIVE"


def test_delivery_timeout_execution_settlement_rejects_wrong_hash_or_missing_confirmation(tmp_path: Path) -> None:
    runner, run_dir, output, evidence = provider_delivery_timeout_settlement_fixture(tmp_path)
    with pytest.raises(runner.OracleRunError) as wrong_hash:
        runner.settle_user_confirmed_delivery_timeout_execution(
            run_dir,
            expected_output_sha256="0" * 64,
            confirmation=runner.STATE.USER_CONFIRMED_EXECUTION_ENDED,
            reason="user confirmed completion",
            execution_evidence=[(evidence, runner.STATE.sha256_file(evidence))],
            process_alive=lambda _: False,
        )
    assert wrong_hash.value.code == "EXECUTION_ENDED_OUTPUT_HASH_MISMATCH"
    with pytest.raises(runner.OracleRunError) as missing_confirmation:
        runner.settle_user_confirmed_delivery_timeout_execution(
            run_dir,
            expected_output_sha256=runner.STATE.sha256_file(output),
            confirmation="",
            reason="user confirmed completion",
            execution_evidence=[(evidence, runner.STATE.sha256_file(evidence))],
            process_alive=lambda _: False,
        )
    assert missing_confirmation.value.code == "EXECUTION_ENDED_CONFIRMATION_REQUIRED"


def test_delivery_timeout_execution_settlement_rejects_absent_evidence_and_active_pending_run(tmp_path: Path) -> None:
    runner, run_dir, output, evidence = provider_delivery_timeout_settlement_fixture(tmp_path)
    with pytest.raises(runner.OracleRunError) as absent_evidence:
        runner.settle_user_confirmed_delivery_timeout_execution(
            run_dir,
            expected_output_sha256=runner.STATE.sha256_file(output),
            confirmation=runner.STATE.USER_CONFIRMED_EXECUTION_ENDED,
            reason="user confirmed completion",
            execution_evidence=[],
            process_alive=lambda _: False,
        )
    assert absent_evidence.value.code == "EXECUTION_ENDED_EVIDENCE_REQUIRED"
    state = runner.STATE.load_state(run_dir / "state.json")
    state["transport_status"] = "incomplete"
    runner.STATE.write_json_atomic(run_dir / "state.json", state)
    with pytest.raises(runner.OracleRunError) as active_pending:
        runner.settle_user_confirmed_delivery_timeout_execution(
            run_dir,
            expected_output_sha256=runner.STATE.sha256_file(output),
            confirmation=runner.STATE.USER_CONFIRMED_EXECUTION_ENDED,
            reason="user confirmed completion",
            execution_evidence=[(evidence, runner.STATE.sha256_file(evidence))],
            process_alive=lambda _: False,
        )
    assert active_pending.value.code == "EXECUTION_ENDED_TIMEOUT_STATE_REQUIRED"


def test_later_exact_live_observation_restores_provisional_terminal_authority(tmp_path: Path) -> None:
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
    # A later exact live observer is stronger than the provisional terminal
    # observation because there is still no durable terminal artifact.
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
    assert disagreement["status"] == "session_live"
    assert disagreement["result"]["status"] == "running"
    assert disagreement["result"]["session_authority"] == "live"
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


def test_pro_structured_mission_rejects_short_terminal_preamble(tmp_path: Path) -> None:
    runner = load_runner()
    manifest_path = pro_manifest(tmp_path)
    (tmp_path / "prompt.txt").write_text(
        "# Mission\n\n## Required answer schema\n\n"
        "1. `DIRECTION_VERDICT`: decision.\n"
        "2. `NEXT_ACTION`: action.\n",
        encoding="utf-8",
    )
    initial = execute_run(
        runner,
        manifest_path,
        run_factory=version_runner,
        popen_factory=popen_for(7, None, {}, []),
    )
    run_dir = Path(initial["run_dir"])

    def completed_preamble(command, **kwargs):
        candidate = Path(command[command.index("--write-output") + 1])
        candidate.write_text("I'll cross-check the evidence, then deliver the decision.", encoding="utf-8")
        kwargs["stdout"].write(b"State: completed\n")
        kwargs["stdout"].flush()
        return Process(0, [])

    rejected = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=completed_preamble,
    )
    state = runner.STATE.load_state(run_dir / "state.json")

    assert rejected["ok"] is False
    assert rejected["status"] == "pro_output_incomplete"
    assert state["session_authority"] == "terminal_observed"
    assert state["terminal_harvested"] is False
    assert not Path(state["artifacts"]["output"]).exists()

    def running_observer(command, **kwargs):
        kwargs["stdout"].write(b"State: running\n")
        kwargs["stdout"].flush()
        return Process(0, [])

    restored = runner.recover_run(
        run_dir,
        action="live",
        oracle_command=["oracle"],
        popen_factory=running_observer,
    )
    assert restored["status"] == "session_live"
    assert restored["result"]["session_authority"] == "live"


def test_pro_terminal_candidate_with_all_ticked_sections_promotes_without_browser(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    manifest_path = pro_manifest(tmp_path)
    (tmp_path / "prompt.txt").write_text(
        "# Mission\n\n## Required answer schema\n\n"
        "1. `DIRECTION_VERDICT`: decision.\n"
        "2. `WEB_MULTI_NEEDED: YES|NO`: reason.\n"
        "3. `WHY`: evidence.\n"
        "4. `SELECTED_ROUTE_ID`: route.\n"
        "5. `SEED_AUTHORITY`: authority.\n"
        "6. `MANAGER_AUTHORITY`: authority.\n"
        "7. `DATA_AND_WINDOW`: data.\n"
        "8. `COST_AND_FUNDING`: costs.\n"
        "9. `PRE_PNL_GATES`: gates.\n"
        "10. `CONTROLS`: controls.\n"
        "11. `RISK_AND_LEVERAGE`: risk.\n"
        "12. `RESOURCE_CONTRACT`: resources.\n"
        "13. `NO_RETUNE_BOUNDARY`: boundary.\n"
        "14. `EXACT_TERMINAL_VERDICTS`: verdicts.\n"
        "15. `REGULAR_WEB_IMPLEMENTATION_MISSION`: mission.\n"
        "16. `NEXT_ACTION`: action.\n",
        encoding="utf-8",
    )
    initial = execute_run(
        runner,
        manifest_path,
        run_factory=version_runner,
        popen_factory=popen_for(7, None, {}, []),
    )
    run_dir = Path(initial["run_dir"])
    labels = (
        "DIRECTION_VERDICT", "WEB_MULTI_NEEDED: NO", "WHY", "SELECTED_ROUTE_ID",
        "SEED_AUTHORITY", "MANAGER_AUTHORITY", "DATA_AND_WINDOW", "COST_AND_FUNDING",
        "PRE_PNL_GATES", "CONTROLS", "RISK_AND_LEVERAGE", "RESOURCE_CONTRACT",
        "NO_RETUNE_BOUNDARY", "EXACT_TERMINAL_VERDICTS",
        "REGULAR_WEB_IMPLEMENTATION_MISSION", "NEXT_ACTION",
    )
    answer = "\n\n".join(
        f"## {index}. `{label}`\n\ncontent {index}"
        for index, label in enumerate(labels, start=1)
    )

    candidate = run_dir / "recovery-harvest-candidate.md"
    candidate.write_text(answer, encoding="utf-8")
    runner.STATE.update_state(
        run_dir / "state.json",
        status="attention_required",
        session_authority="terminal_observed",
        terminal_harvested=False,
    )
    state = runner.STATE.load_state(run_dir / "state.json")
    expected_sha256 = runner.STATE.sha256_file(candidate)

    assert state["session_authority"] == "terminal_observed"
    assert runner.pro_output_satisfies_required_schema(state, candidate) is True

    promoted = runner.promote_terminal_harvest_candidate(
        run_dir,
        candidate_path=candidate,
        expected_candidate_sha256=expected_sha256,
    )
    completed = runner.STATE.load_state(run_dir / "state.json")

    assert promoted["ok"] is True
    assert promoted["artifact_sha256"] == expected_sha256
    assert candidate.is_file()
    assert Path(promoted["output_path"]).read_text(encoding="utf-8") == answer
    assert completed["status"] == "complete"
    assert completed["session_authority"] == "terminal"
    assert completed["terminal_harvested"] is True


def test_live_recovery_holds_one_exact_slug_connection_until_terminal(
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
    live_timeout_ms: list[str] = []

    def recovery(command, **kwargs):
        assert "--live" in command
        calls.append("live")
        live_timeout_ms.append(kwargs["env"]["ORACLE_LIVE_TERMINAL_TIMEOUT_MS"])
        candidate = Path(command[command.index("--write-output") + 1])
        candidate.write_text("durable exact answer", encoding="utf-8")
        # The compatibility-patched live tail keeps one recovered browser and
        # observes both states before it returns a terminal harvest.
        kwargs["stdout"].write(b"State: running\nState: completed\n")
        kwargs["stdout"].flush()
        return Process(0, [])

    settled = runner.recover_run(
        run_dir,
        action="live",
        oracle_command=["oracle"],
        popen_factory=recovery,
        settle_timeout_seconds=5,
        settle_interval_seconds=0,
    )

    assert calls == ["live"]
    assert live_timeout_ms == ["5000"]
    assert settled["ok"] is True
    assert settled["status"] == "complete"
    assert settled["result"]["session_authority"] == "terminal"
    assert settled["result"]["terminal_harvested"] is True
    assert settled["result"]["browser_observer"]["status"] == "exact-recovery-terminal-harvested"
    assert settled["result"]["browser_observer"]["exact_session_state"] == "completed"
    assert Path(settled["output_path"]).read_text(encoding="utf-8") == "durable exact answer"


def test_live_recovery_slow_working_page_keeps_one_recovered_tab_until_terminal(
    tmp_path: Path,
) -> None:
    """E2E-like recovery fixture: a recovered Pro page works before it is ready."""
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
        session_authority="live",
    )
    calls: list[list[str]] = []
    live_timeout_ms: list[str] = []

    def slow_working_recovery(command, **kwargs):
        calls.append(list(command))
        live_timeout_ms.append(kwargs["env"]["ORACLE_LIVE_TERMINAL_TIMEOUT_MS"])
        candidate = Path(command[command.index("--write-output") + 1])
        candidate.write_text("durable exact answer after slow readiness", encoding="utf-8")
        kwargs["stdout"].write(
            b"[browser] Recovery: Chrome listening on 127.0.0.1:53582; tab loaded.\n"
            b"[2026-08-04T12:55:00.000Z] state=working stop=yes send=no model=Pro snippet=\n"
            b"State: running\n"
            b"State: completed\n"
        )
        kwargs["stdout"].flush()
        return Process(0, [])

    settled = runner.recover_run(
        run_dir,
        action="live",
        oracle_command=["oracle"],
        popen_factory=slow_working_recovery,
        settle_timeout_seconds=3600,
    )

    assert len(calls) == 1
    assert "--live" in calls[0]
    assert live_timeout_ms == ["3600000"]
    assert settled["ok"] is True
    assert settled["status"] == "complete"
    assert settled["result"]["session_authority"] == "terminal"


def test_stalled_exact_observation_retains_live_authority_and_project_lock(
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

    def stalled_observer(command, **kwargs):
        kwargs["stdout"].write(b"State: stalled\n")
        kwargs["stdout"].flush()
        return Process(0, [])

    recovered = runner.recover_run(
        run_dir,
        action="live",
        oracle_command=["oracle"],
        popen_factory=stalled_observer,
        settle_timeout_seconds=0,
    )

    state = runner.STATE.load_state(run_dir / "state.json")
    assert recovered["ok"] is False
    assert recovered["status"] == "session_live"
    assert recovered["exact_session_state"] == "stalled"
    assert state["status"] == "running"
    assert state["session_authority"] == "live"
    assert state["terminal_harvested"] is False


def test_terminal_recovery_reconciles_stale_running_browser_observer(tmp_path: Path) -> None:
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
        session_authority="live",
        browser_observer={
            "status": "running",
            "timeout_seconds": 10_000,
            "oracle_process_pid": 36252,
            "timeout_is_terminal": False,
        },
    )

    def completed_recovery(command, **kwargs):
        candidate = Path(command[command.index("--write-output") + 1])
        candidate.write_text("durable exact answer", encoding="utf-8")
        kwargs["stdout"].write(b"State: completed\n")
        kwargs["stdout"].flush()
        return Process(0, [])

    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=completed_recovery,
    )
    observer = recovered["result"]["browser_observer"]

    assert recovered["result"]["session_authority"] == "terminal"
    assert recovered["result"]["terminal_harvested"] is True
    assert observer == {
        "status": "exact-recovery-terminal-harvested",
        "timeout_seconds": 10_000,
        "oracle_process_pid": 36252,
        "timeout_is_terminal": False,
        "recovery_action": "harvest",
        "exact_session_state": "completed",
    }


def test_live_recovery_cli_defaults_to_eighty_minute_status_audit() -> None:
    runner = load_runner()
    args = runner.build_parser().parse_args([
        "recover", "--run-dir", r"C:\host-state\exact-run", "--action", "live",
    ])
    assert args.settle_timeout_seconds == 4800
    assert args.settle_interval_seconds == 15


def test_live_recovery_reopens_only_the_exact_slug_after_each_status_audit(
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
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def exact_live_then_terminal(command, **kwargs):
        calls.append(list(command))
        if len(calls) == 1:
            kwargs["stdout"].write(b"State: running\n")
        else:
            kwargs["stdout"].write(b"State: completed\n")
            candidate = Path(command[command.index("--write-output") + 1])
            candidate.write_text("durable exact-session answer\n", encoding="utf-8")
        kwargs["stdout"].flush()
        return Process(0, [])

    result = runner.recover_run(
        run_dir,
        action="live",
        oracle_command=["oracle"],
        popen_factory=exact_live_then_terminal,
        settle_timeout_seconds=4800,
        settle_interval_seconds=15,
        sleep=sleeps.append,
    )

    slug = runner.STATE.load_state(run_dir / "state.json")["oracle"]["slug"]
    assert result["ok"] is True
    assert result["status"] == "complete"
    assert len(calls) == 2
    assert all(call[call.index("session") + 1] == slug for call in calls)
    assert all("--prompt" not in call and "restart" not in call for call in calls)
    assert sleeps == [15]
    assert runner.STATE.load_state(run_dir / "state.json")["session_authority"] == "terminal"


def test_live_recovery_returns_once_when_exact_binding_is_unavailable(
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
    calls: list[str] = []
    sleeps: list[float] = []

    def no_binding(command, **kwargs):
        calls.append("live")
        kwargs["stdout"].write(
            b'No live ChatGPT tab matched session "exact". Attempting recovery by reopening the saved conversation URL.\n'
            b'Cannot recover conversation: session metadata has no recoverable ChatGPT conversation URL.\n'
        )
        kwargs["stdout"].flush()
        return Process(1, [])

    result = runner.recover_run(
        run_dir,
        action="live",
        oracle_command=["oracle"],
        popen_factory=no_binding,
        settle_timeout_seconds=5400,
        settle_interval_seconds=15,
        sleep=sleeps.append,
    )

    assert calls == ["live"]
    assert sleeps == []
    assert result["ok"] is False
    assert result["status"] == "recovery_binding_unavailable"
    assert result["exact_session_state"] is None
    assert "never replace or resubmit" in result["next_action"]
    assert result["result"]["status"] == "attention_required"
    assert result["result"]["session_authority"] == "submitted_unknown"
    assert result["result"]["terminal_harvested"] is False
    assert not (run_dir / "recovery-live-candidate.md").exists()


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


def test_parallel_recovery_uses_exact_run_mutex_without_reentering_submit_mutex(tmp_path: Path) -> None:
    runner = load_runner()
    parent_id = "a" * 32
    submit_roots: list[Path] = []
    recovery_roots: list[Path] = []

    class Mutex:
        def __init__(self, root: Path):
            self.root = root

        def __enter__(self):
            submit_roots.append(self.root)

        def __exit__(self, *args):
            return None

    runner.STATE.project_submit_mutex = lambda root, **kwargs: Mutex(root)

    class RecoveryMutex(Mutex):
        def __enter__(self):
            recovery_roots.append(self.root)

    runner.STATE.exact_run_recovery_mutex = lambda root, **kwargs: RecoveryMutex(root)
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
    assert submit_roots == [expected]
    assert recovery_roots == [Path(result["run_dir"]).resolve()]


def test_exact_recovery_bypasses_live_submit_mutex_and_harvests_same_slug(tmp_path: Path) -> None:
    runner = load_runner()
    initial = execute_run(
        runner,
        manifest(tmp_path),
        run_factory=version_runner,
        popen_factory=popen_for(7, None, {}, []),
    )
    run_dir = Path(initial["run_dir"])

    def forbidden_submit_mutex(*args, **kwargs):
        raise AssertionError("exact recovery must not wait on the project submit mutex")

    class RecoveryMutex:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    runner.STATE.project_submit_mutex = forbidden_submit_mutex
    runner.STATE.exact_run_recovery_mutex = lambda root, **kwargs: RecoveryMutex()

    def completed_recovery(command, **kwargs):
        candidate = Path(command[command.index("--write-output") + 1])
        candidate.write_text("durable exact answer", encoding="utf-8")
        kwargs["stdout"].write(
            b"State: completed\nURL: https://chatgpt.com/c/exact-stale-observer\n"
        )
        kwargs["stdout"].flush()
        return Process(0, [])

    recovered = runner.recover_run(
        run_dir,
        action="harvest",
        oracle_command=["oracle"],
        popen_factory=completed_recovery,
    )

    assert recovered["ok"] is True
    assert recovered["status"] == "complete"
    assert recovered["result"]["session_authority"] == "terminal"
    assert recovered["result"]["terminal_harvested"] is True
    assert recovered["result"]["oracle"]["conversation_url"] == (
        "https://chatgpt.com/c/exact-stale-observer"
    )
