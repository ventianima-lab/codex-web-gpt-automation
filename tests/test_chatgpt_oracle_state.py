from __future__ import annotations

import importlib.util
import json
import os
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


def test_v1_task_outcome_accepts_exact_provider_reference_footer(tmp_path: Path) -> None:
    state = load_state()
    output = tmp_path / "output.md"
    output.write_bytes(REFERENCE_FOOTER_FIXTURE.read_bytes())

    assert state.classify_task_outcome(
        output,
        contract="v1",
        transport="pro-devspace",
    ) == "executed"


@pytest.mark.parametrize(
    "suffix",
    [
        "Actually no files were changed.\n",
        "[note]: this is ordinary prose, not a URL\n",
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
    prompt = state.composer_prompt(config)
    assert prompt.startswith(f"@DevSpace {mission.resolve()} 파일을 읽고 끝까지 수행하세요.")
    assert "동일한 정확한 루트만 한 번 재시도" in prompt
    assert "상위·하위·현재 활성 작업공간이나 셸 경계 우회" in prompt
    assert "\n" not in prompt


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
            thinking_time="heavy",
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
        "Read the attached prompt/instructions and all attached files, then complete the task. "
        "Task identity: oracle-pro-"
    )
    assert composer.endswith(".")
    assert len(composer.rsplit("oracle-pro-", 1)[1][:-1]) == 24
    assert composer == state.composer_prompt(config)
    assert str(tmp_path.resolve()) not in composer
    assert "@DevSpace" not in composer
    layout = state.create_layout(config, run_id="20260725T151414Z-a3aeba967d99")
    payload = state.state_payload(config, layout, status="prepared", resolved_version="oracle 0.17.1")
    assert payload["transport"] == "pro-attachment-only"
    assert payload["attachments"][1]["sha256"] == state.sha256_file(packet.resolve())


def test_pro_devspace_manifest_is_write_capable_and_stays_inside_project(tmp_path: Path) -> None:
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
        f"@DevSpace Read and execute the mission file: {mission.resolve()}."
    )
    prompt = state.composer_prompt(config)
    assert "create, edit, and remove mission-owned files and run commands" in prompt
    assert "Put every citation, footnote, and Markdown reference definition before" in prompt
    assert prompt.endswith("as the final nonempty line; append nothing after it.")
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

    legacy = state.load_manifest(manifest(
        tmp_path,
        mission.resolve(),
        transport="pro-devspace-readonly",
        app_name="DevSpace",
        model="gpt-5.6-sol",
        model_strategy="select",
        thinking_time="heavy",
        research="off",
        task_outcome_contract="v1",
    ))
    assert state.composer_prompt(legacy).startswith(
        f"@DevSpace Read the read-only mission file: {mission.resolve()}."
    )


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
            thinking_time="heavy",
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
        "thinking_time": "heavy",
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
