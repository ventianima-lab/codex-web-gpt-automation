from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_execute.py"


def load_module():
    name = "chatgpt_oracle_execute_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def executor():
    return load_module()


def test_process_probe_does_not_signal_on_windows(executor, monkeypatch):
    import os

    if os.name == "nt":
        monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("Windows probe must never signal"))
    assert executor._pid_alive(os.getpid()) is True
    assert executor._pid_alive(-1) is False


def test_reconnect_lock_rejects_overlap_and_releases(executor, tmp_path):
    with executor._exact_run_lock(tmp_path):
        with pytest.raises(executor.ExecutionError, match="another reconnect"):
            with executor._exact_run_lock(tmp_path):
                pytest.fail("overlapping reconnect acquired the lock")
    # A persistent lock file is harmless after its OS lock has been released.
    with executor._exact_run_lock(tmp_path):
        pass


@pytest.fixture
def execution_paths(tmp_path: Path, monkeypatch, executor):
    root = tmp_path / "project"
    root.mkdir()
    mission = root / "mission.md"
    mission.write_text("Implement the requested change.\n", encoding="utf-8")
    run_root = tmp_path / "host-state" / "runs"
    session_root = tmp_path / "oracle-sessions"
    profile = tmp_path / "signed-in-profile"
    profile.mkdir()
    monkeypatch.setenv("ORACLE_SESSION_ROOT", str(session_root))
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(profile))
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    return root, mission, run_root, session_root


def test_config_binds_ambient_task_owner(executor, execution_paths, monkeypatch):
    root, mission, run_root, _ = execution_paths
    owner = "01234567-89ab-cdef-0123-456789abcdef"
    monkeypatch.setenv("CODEX_THREAD_ID", owner)
    config = executor.make_config(project_root=root, mission_path=mission, run_root=run_root)
    assert config.source_thread_id == owner
    assert executor.manifest_payload(config)["source_thread_id"] == owner


def test_slug_survives_oracle_normalization(executor, execution_paths):
    root, mission, run_root, _ = execution_paths
    config = executor.make_config(project_root=root, mission_path=mission, run_root=run_root)
    slug = executor._slug(config)
    assert 3 <= len(slug.split("-")) <= 5
    assert all(len(word) <= 10 for word in slug.split("-"))


def test_child_enables_temporary_personalization(executor, monkeypatch):
    monkeypatch.setenv("CODEX_ORACLE_TEMPORARY_PERSONALIZATION", "disabled")
    environment = executor._child_environment()
    assert environment["CODEX_ORACLE_TEMPORARY_PERSONALIZATION"] == "enabled"
    assert environment["CODEX_ORACLE_TEMPORARY_PERSONALIZATION_HELPER"] == MODULE_PATH.with_name("oracle_temporary_personalization.mjs").as_uri()


def test_changed_mission_is_rejected_before_launch(executor, execution_paths):
    root, mission, run_root, _ = execution_paths
    config = executor.make_config(project_root=root, mission_path=mission, run_root=run_root)
    mission.write_text("changed after preparation", encoding="utf-8")
    with pytest.raises(executor.ExecutionError) as exc:
        executor.execute_config(config, command_resolver=lambda: pytest.fail("must not launch"))
    assert exc.value.code == "MISSION_CHANGED"
    assert not (run_root / config.run_id).exists()


def test_retained_profile_copy_preserves_seed_and_normalizes_only_copy(executor, execution_paths):
    root, mission, run_root, _ = execution_paths
    config = executor.make_config(project_root=root, mission_path=mission, run_root=run_root)
    seed = config.copy_profile / "Default"
    seed.mkdir()
    original = json.dumps({"profile": {"exit_type": "Crashed"}, "session": {"restore_on_startup": 1}, "custom": 42}).encode()
    (seed / "Preferences").write_bytes(original)
    (seed / "Cookies").write_bytes(b"fixture-cookie-data")
    (seed / "Cache").mkdir()
    (seed / "Cache" / "cached").write_bytes(b"discardable cache")
    run_dir = run_root / config.run_id
    run_dir.mkdir(parents=True)
    copied = executor._prepare_run_profile(config, run_dir)
    assert (seed / "Preferences").read_bytes() == original
    assert (copied / "Default" / "Cookies").read_bytes() == b"fixture-cookie-data"
    assert not (copied / "Default" / "Cache").exists()
    preferences = json.loads((copied / "Default" / "Preferences").read_text())
    assert preferences["profile"]["exit_type"] == "Normal"
    assert preferences["profile"]["exited_cleanly"] is True
    assert preferences["session"] == {"restore_on_startup": 5, "startup_urls": []}
    assert preferences["custom"] == 42


def test_profile_is_required_only_for_live_execution(executor, execution_paths, monkeypatch, tmp_path):
    root, mission, run_root, _ = execution_paths
    monkeypatch.setenv("ORACLE_BROWSER_PROFILE_DIR", str(tmp_path / "missing-profile"))
    config = executor.make_config(project_root=root, mission_path=mission, run_root=run_root)
    assert executor.execute_config(config, dry_run=True)["writes_performed"] is False
    assert not run_root.exists()
    with pytest.raises(executor.ExecutionError) as exc:
        executor.execute_config(config, command_resolver=lambda: pytest.fail("must not launch"))
    assert exc.value.code == "SIGNED_IN_PROFILE_UNAVAILABLE"


def test_profile_copy_failure_is_definitely_before_submission(executor, execution_paths, monkeypatch):
    root, mission, run_root, _ = execution_paths
    config = executor.make_config(project_root=root, mission_path=mission, run_root=run_root)
    def failed_copy(*args):
        raise OSError("profile copy failed")
    monkeypatch.setattr(executor, "_prepare_run_profile", failed_copy)
    result = executor.execute_config(config, command_resolver=lambda: ["oracle"],
        version_resolver=lambda command: "oracle 0.18.0", compat_factory=lambda version: {},
        popen_factory=lambda *args, **kwargs: pytest.fail("must not start Oracle"))
    assert result["result"]["submission"] == "not_observed"
    assert result["result"]["failure_stage"] == "profile-preparation"


def test_profile_copy_handles_long_windows_destination(executor, execution_paths):
    root, mission, run_root, _ = execution_paths
    config = executor.make_config(project_root=root, mission_path=mission, run_root=run_root)
    source = config.copy_profile / "Default" / "nested-profile-assets"
    source.mkdir(parents=True)
    name = "asset-" + "x" * 100 + ".txt"
    (source / name).write_bytes(b"fixture")
    run_dir = run_root / ("long-run-" + "x" * 60)
    copied = executor._prepare_run_profile(config, run_dir)
    target = copied / "Default" / "nested-profile-assets" / name
    if executor.os.name == "nt":
        target = Path("\\\\?\\" + str(target.absolute()))
    try:
        assert target.read_bytes() == b"fixture"
    finally:
        # tempfile cleanup does not use the Windows extended-length prefix.
        target.unlink()


def test_ordinary_execution_does_not_require_legacy_devspace_config(executor, execution_paths, monkeypatch):
    root, mission, run_root, _ = execution_paths
    config = executor.make_config(project_root=root, mission_path=mission, run_root=run_root)
    assert not hasattr(executor, "DEVSPACE_PREFLIGHT")
    def reached_command_resolution():
        raise RuntimeError("resolved without legacy setup")
    with pytest.raises(RuntimeError, match="resolved without legacy setup"):
        executor.execute_config(config, command_resolver=reached_command_resolution)
    assert not (run_root / config.run_id).exists()


def test_filesystem_root_is_not_an_approved_project(executor, execution_paths):
    root, mission, run_root, _ = execution_paths
    with pytest.raises(executor.ExecutionError) as exc:
        executor.make_config(project_root=Path(root.anchor), mission_path=mission, run_root=run_root)
    assert exc.value.code == "PROJECT_ROOT_TOO_BROAD"


def test_selected_node_is_forwarded_to_compatibility(executor, execution_paths, tmp_path, monkeypatch):
    root, mission, run_root, _ = execution_paths
    config = executor.make_config(project_root=root, mission_path=mission, run_root=run_root)
    node = tmp_path / "bundled node" / "node.exe"
    node.parent.mkdir()
    node.write_bytes(b"fixture")
    command = [str(node), str(tmp_path / "oracle-cli.js")]
    observed = []
    def verify(version, **kwargs):
        observed.append((version, kwargs))
        raise RuntimeError("stop before browser or profile copy")
    with pytest.raises(RuntimeError, match="stop before browser"):
        executor.execute_config(config, command_resolver=lambda: command,
            version_resolver=lambda value: "oracle 0.18.0", compat_factory=verify)
    assert observed == [("oracle 0.18.0", {"node_executable": str(node)})]
    assert not (run_root / config.run_id).exists()


def picker_proof(effort: str = "pro") -> dict:
    ordinal = 5 if effort == "pro" else 4
    return {
        "schema": "codex.oracle.picker-dom-proof/v1",
        "latestClicked": True,
        "stableReads": 2,
        "modelRows": [
            {
                "text": "Latest",
                "ariaLabel": None,
                "role": "menuitemradio",
                "checked": "true",
                "expanded": None,
                "visible": True,
            }
        ],
        "composer": {
            "text": "6 Pro" if effort == "pro" else "Thinking effort",
            "ariaLabel": None,
            "role": None,
            "checked": None,
            "expanded": "true",
            "visible": True,
        },
        "modelSignals": [],
        "slider": {
            "minimum": 0,
            "maximum": 4,
            "current": ordinal - 1,
            "ordinal": ordinal,
            "total": 5,
            "displayOrdinal": ordinal,
            "displayTotal": 5,
            "atMaximum": ordinal == 5,
            "text": f"{ordinal} of 5",
            "visible": True,
        },
    }


def write_session_meta(
    session_root: Path,
    slug: str,
    *,
    status: str,
    submitted: bool = True,
    port: int = 43123,
) -> dict:
    target_id = "A" * 32
    url = "https://chatgpt.com/c/owned-temporary-run"
    directory = session_root / slug
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "browser": {
            "runtime": {
                "chromeHost": "127.0.0.1",
                "chromePort": port,
                "chromeTargetId": target_id,
                "tabUrl": url,
                "promptSubmitted": submitted,
            }
        },
    }
    (directory / "meta.json").write_text(json.dumps(payload), encoding="utf-8")
    return {"target_id": target_id, "conversation_url": url}


class Process:
    pid = 4242

    def __init__(self, code: int = 0):
        self.code = code

    def wait(self):
        return self.code


def test_manifest_is_minimal_and_rejects_retired_fields(executor, execution_paths, tmp_path: Path):
    root, mission, run_root, _ = execution_paths
    config = executor.make_config(
        project_root=root,
        mission_path=mission,
        run_root=run_root,
        run_id="ordinary-run-0001",
    )
    manifest = executor.manifest_payload(config)
    assert set(manifest) == {
        "schema", "project_root", "mission_path", "run_root", "run_id", "model", "effort", "app_name"
    }

    manifest["mode"] = "review"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(executor.ExecutionError) as exc:
        executor.load_manifest(path)
    assert exc.value.code == "MANIFEST_FIELDS_INVALID"


def test_dry_run_has_temp_personalized_route_and_no_writes(executor, execution_paths):
    root, mission, run_root, _ = execution_paths
    config = executor.make_config(
        project_root=root,
        mission_path=mission,
        run_root=run_root,
        run_id="ordinary-run-0002",
        app_name="git-app",
    )
    result = executor.execute_config(config, dry_run=True)
    argv = result["argv"]

    assert result["writes_performed"] is False
    assert not run_root.exists()
    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
    assert argv[argv.index("--browser-model-strategy") + 1] == "current"
    assert argv[argv.index("--browser-thinking-time") + 1] == "pro"
    assert argv[argv.index("--chatgpt-url") + 1] == "https://chatgpt.com/?temporary-chat=true"
    assert argv[argv.index("--browser-archive") + 1] == "never"
    assert "--browser-keep-browser" in argv
    assert "--browser-hide-window" in argv
    assert "--copy-profile" not in argv
    assert Path(argv[argv.index("--browser-manual-login-profile-dir") + 1]) == run_root / config.run_id / "browser-profile"
    assert argv[argv.index("--prompt") + 1] == "<mission-handoff>"
    assert "TASK_OUTCOME" not in " ".join(argv)


@pytest.mark.parametrize("effort", ["pro", "extra-high"])
def test_one_latest_model_check_covers_model_and_effort(executor, tmp_path: Path, effort: str):
    stdout = tmp_path / "stdout.log"
    stdout.write_text(
        f"[browser] Picker DOM proof: {json.dumps(picker_proof(effort))}\n",
        encoding="utf-8",
    )
    check = executor.observed_model_check(stdout, model="latest", effort=effort)
    assert check == {
        "verified": True,
        "model": "latest",
        "actual_model": "6 Pro" if effort == "pro" else None,
        "effort": effort,
        "source": "oracle-picker-dom-log",
    }


@pytest.mark.parametrize("label, verified", [("Thinking effort", True), ("추론 수준", True), ("5.6 Pro", False)])
def test_latest_pro_accepts_actual_menu_signal(executor, tmp_path, label, verified):
    proof = picker_proof()
    proof["composer"]["text"] = label
    proof["modelSignals"] = [{"text": "6Pro", "visible": True}]
    stdout = tmp_path / "stdout.log"
    stdout.write_text(executor.PICKER_PROOF_PREFIX + json.dumps(proof), encoding="utf-8")
    result = executor.observed_model_check(stdout, model="latest", effort="pro")
    assert result["verified"] is verified


def test_explicit_model_uses_observed_select_logs(executor, tmp_path: Path):
    stdout = tmp_path / "stdout.log"
    stdout.write_text(
        "[browser] Model selection evidence: requestedKey=gpt-5.6-sol; target=GPT-5.6 Sol; "
        "resolvedLabel=GPT-5.6 Sol; status=switched; strategy=select; verified=yes; source=chatgpt-model-picker\n"
        "[browser] Thinking time: Pro, 5 of 5\n",
        encoding="utf-8",
    )
    assert executor.observed_model_check(stdout, model="gpt-5.6-sol", effort="pro")["verified"] is True


def test_close_owned_tab_targets_only_exact_recorded_target(executor):
    urls: list[str] = []
    target_id = "B" * 32

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(self.payload).encode()

    calls = 0

    def opener(request, timeout):
        nonlocal calls
        calls += 1
        urls.append(request.full_url)
        if calls == 1:
            return Response([
                {"id": target_id, "url": "https://chatgpt.com/c/owned", "type": "page"},
                {"id": "C" * 32, "url": "https://chatgpt.com/c/foreign", "type": "page"},
            ])
        if calls == 2:
            return Response({})
        return Response([{"id": "C" * 32, "url": "https://chatgpt.com/c/foreign", "type": "page"}])

    result = executor.close_owned_tab(
        {
            "host": "127.0.0.1",
            "port": 43123,
            "target_id": target_id,
            "conversation_url": "https://chatgpt.com/c/owned",
        },
        opener=opener,
    )

    assert result == {"status": "closed", "target_id": target_id}
    assert urls[1].endswith(f"/json/close/{target_id}")
    assert all(("C" * 32) not in url for url in urls)


def test_capture_state_is_durable_before_owned_tab_close(executor, execution_paths):
    root, mission, run_root, session_root = execution_paths
    config = executor.make_config(
        project_root=root,
        mission_path=mission,
        run_root=run_root,
        run_id="ordinary-run-0003",
    )
    state_path = run_root / config.run_id / "state.json"

    def popen(argv, **kwargs):
        output = Path(argv[argv.index("--write-output") + 1])
        output.write_text("Complete captured answer.\n", encoding="utf-8")
        kwargs["stdout"].write(
            f"[browser] Picker DOM proof: {json.dumps(picker_proof())}\n".encode()
        )
        kwargs["stdout"].flush()
        write_session_meta(
            session_root,
            argv[argv.index("--slug") + 1],
            status="completed",
            port=int(argv[argv.index("--browser-port") + 1]),
        )
        return Process(0)

    close_calls = []

    def closer(binding):
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        assert persisted["status"] == "captured"
        assert persisted["capture"] == "durable"
        assert persisted["artifacts"]["output_sha256"]
        close_calls.append(binding["target_id"])
        return {"status": "closed", "target_id": binding["target_id"]}

    result = executor.execute_config(
        config,
        command_resolver=lambda: ["oracle"],
        version_resolver=lambda command: "oracle 0.18.0",
        compat_factory=lambda version: {"ok": True},
        popen_factory=popen,
        tab_closer=closer,
    )

    assert result["ok"] is True
    assert result["status"] == "captured"
    assert result["result"]["semantic_outcome"] == "unknown"
    assert result["result"]["tab_close"]["status"] == "closed"
    assert close_calls == ["A" * 32]


def test_timeout_keeps_same_tab_and_never_resubmits(executor, execution_paths):
    root, mission, run_root, session_root = execution_paths
    config = executor.make_config(
        project_root=root,
        mission_path=mission,
        run_root=run_root,
        run_id="ordinary-run-0004",
    )
    launches: list[list[str]] = []

    def popen(argv, **kwargs):
        launches.append(list(argv))
        write_session_meta(
            session_root,
            argv[argv.index("--slug") + 1],
            status="running",
            port=int(argv[argv.index("--browser-port") + 1]),
        )
        return Process(1)

    result = executor.execute_config(
        config,
        command_resolver=lambda: ["oracle"],
        version_resolver=lambda command: "oracle 0.18.0",
        compat_factory=lambda version: {"ok": True},
        popen_factory=popen,
        tab_closer=lambda binding: pytest.fail("incomplete run must not close its tab"),
    )

    assert result["ok"] is False
    assert result["status"] == "attention_required"
    assert result["result"]["submission"] == "observed"
    assert result["result"]["tab_close"]["status"] == "not_attempted"
    assert len(launches) == 1
    assert launches[0].count("--prompt") == 1


def test_reconnect_is_prompt_free_and_uses_original_slug(executor, execution_paths):
    root, mission, run_root, session_root = execution_paths
    config = executor.make_config(
        project_root=root,
        mission_path=mission,
        run_root=run_root,
        run_id="ordinary-run-0005",
    )

    def initial_popen(argv, **kwargs):
        kwargs["stdout"].write(
            f"[browser] Picker DOM proof: {json.dumps(picker_proof())}\n".encode()
        )
        kwargs["stdout"].flush()
        write_session_meta(
            session_root,
            argv[argv.index("--slug") + 1],
            status="running",
            port=int(argv[argv.index("--browser-port") + 1]),
        )
        return Process(1)

    first = executor.execute_config(
        config,
        command_resolver=lambda: ["oracle"],
        version_resolver=lambda command: "oracle 0.18.0",
        compat_factory=lambda version: {"ok": True},
        popen_factory=initial_popen,
        tab_closer=lambda binding: pytest.fail("initial incomplete run must retain tab"),
    )
    run_dir = Path(first["run_dir"])
    original = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    slug = original["oracle"]["slug"]
    before_preview = {path.name: path.read_bytes() for path in run_dir.iterdir() if path.is_file()}
    preview = executor.reconnect_run(run_dir, dry_run=True)
    assert preview["resubmit"] is False
    assert before_preview == {path.name: path.read_bytes() for path in run_dir.iterdir() if path.is_file()}
    recovery_argv: list[str] = []

    def reconnect_popen(argv, **kwargs):
        recovery_argv.extend(argv)
        Path(original["artifacts"]["output"]).write_text("Recovered complete answer.\n", encoding="utf-8")
        write_session_meta(
            session_root,
            slug,
            status="completed",
            port=int(original["oracle"]["expected_cdp_port"]),
        )
        return Process(0)

    recovered = executor.reconnect_run(
        run_dir,
        popen_factory=reconnect_popen,
        tab_closer=lambda binding: {"status": "closed", "target_id": binding["target_id"]},
    )

    assert recovered["ok"] is True
    assert recovery_argv[:3] == ["oracle", "session", slug]
    assert "--live" in recovery_argv
    assert "--prompt" not in recovery_argv
    assert "-p" not in recovery_argv


def test_unresolved_observed_run_blocks_duplicate_submission(executor, execution_paths):
    root, mission, run_root, session_root = execution_paths

    def popen(argv, **kwargs):
        write_session_meta(
            session_root,
            argv[argv.index("--slug") + 1],
            status="running",
            port=int(argv[argv.index("--browser-port") + 1]),
        )
        return Process(1)

    first_config = executor.make_config(
        project_root=root, mission_path=mission, run_root=run_root, run_id="ordinary-run-0006"
    )
    executor.execute_config(
        first_config,
        command_resolver=lambda: ["oracle"],
        version_resolver=lambda command: "oracle 0.18.0",
        compat_factory=lambda version: {"ok": True},
        popen_factory=popen,
    )
    mission.write_text("A changed mission must not bypass an uncertain submission.\n", encoding="utf-8")
    second_config = executor.make_config(
        project_root=root, mission_path=mission, run_root=run_root, run_id="ordinary-run-0007"
    )
    with pytest.raises(executor.ExecutionError) as exc:
        executor.execute_config(
            second_config,
            command_resolver=lambda: pytest.fail("duplicate must fail before resolving Oracle"),
        )
    assert exc.value.code == "RUN_RECONNECT_REQUIRED"
