from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_diagnose.py"


def load():
    name = "chatgpt_oracle_diagnose_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_run(
    state_root: Path,
    run_id: str,
    *,
    status: str,
    stdout: str = "",
    output: str | None = None,
    session_authority: str = "",
    terminal_harvested: bool = False,
    task_outcome: str = "",
) -> Path:
    run_dir = state_root / "projects" / "projectkey" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "output.md"
    if output is not None:
        output_path.write_text(output, encoding="utf-8")
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    (run_dir / "state.json").write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-run-state/v1",
        "status": status,
        "run_id": run_id,
        "project_root": str(state_root / "project"),
        "session_authority": session_authority,
        "terminal_harvested": terminal_harvested,
        "task_outcome": task_outcome,
        "artifacts": {"output": str(output_path), "stdout": str(stdout_path), "stderr": str(stderr_path)},
        "oracle": {"slug": f"oracle-project-{run_id[:10]}"},
    }), encoding="utf-8")
    return run_dir


def test_report_buckets_pre_submit_ui_and_host_causes_separately(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "a" * 8,
        status="failed",
        stdout="ERROR: ChatGPT app mention was not confirmed in the composer.\n",
    )
    write_run(
        state_root,
        "b" * 8,
        status="failed",
        stdout="ERROR: --copy-profile requires rsync on PATH (spawn failed): spawn rsync ENOENT\n",
    )
    write_run(
        state_root,
        "c" * 8,
        status="failed",
        stdout="ERROR: Chrome window closed before oracle finished.\n",
    )

    report = module.diagnose(state_root)

    assert report["schema"] == "codex.chatgpt.oracle-diagnosis/v1"
    assert report["total_runs"] == 3
    assert report["bucket_counts"] == {
        "pre-submit-host-environment": 1,
        "pre-submit-ui-contract": 1,
        "browser-lifetime-lost": 1,
    }
    assert len(report["bucket_counts"]) <= 10
    assert report["safe_for_fresh_run_buckets"] == [
        "pre-submit-host-environment",
        "pre-submit-ui-contract",
    ]


@pytest.mark.parametrize(
    ("stderr", "signature"),
    [
        (
            "Oracle launch/run failed: project submit mutex could not be acquired",
            "project-submit-mutex-held",
        ),
        (
            "PROJECT_SESSION_STILL_LIVE: an exact Oracle session still owns this project",
            "same-task-project-session-still-live",
        ),
    ],
)
def test_pre_submit_ownership_conflicts_are_classified_but_never_retry_safe(
    tmp_path: Path,
    stderr: str,
    signature: str,
) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_dir = write_run(
        state_root,
        "o" * 8,
        status="failed",
        session_authority="pre_submit",
    )
    (run_dir / "stderr.log").write_text(stderr, encoding="utf-8")

    report = module.diagnose(state_root)
    run = report["unresolved_runs"][0]

    assert run["bucket"] == "submission-ownership-conflict"
    assert run["signature"] == signature
    assert "submission-ownership-conflict" not in report["safe_for_fresh_run_buckets"]


def test_pre_submit_signature_outranks_post_submit_interpretation(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "d" * 8,
        status="attention_required",
        stdout=(
            "ERROR: ChatGPT app mention suggestion did not appear.\n"
            "note: ECONNREFUSED 127.0.0.1:1234\n"
        ),
        session_authority="submitted_unknown",
    )

    report = module.diagnose(state_root)
    run = report["unresolved_runs"][0]

    assert run["bucket"] == "pre-submit-ui-contract"
    assert run["signature"] == "app-mention-suggestion-absent"


def test_settled_model_switcher_no_cookie_failure_remains_retry_safe(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_dir = write_run(
        state_root,
        "m" * 8,
        status="attention_required",
        session_authority="pre_submit",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pre_submit_failure"] = {
        "code": "ORACLE_MODEL_SWITCHER_PRE_SUBMIT_FAILED",
        "output_absent": True,
        "conversation_url_absent": True,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    report = module.diagnose(state_root)
    run = report["unresolved_runs"][0]

    assert run["bucket"] == "pre-submit-ui-contract"
    assert run["signature"] == "model-option-label-missing"


def test_settled_thinking_time_failure_remains_retry_safe(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_dir = write_run(
        state_root,
        "t" * 8,
        status="attention_required",
        session_authority="pre_submit",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pre_submit_failure"] = {
        "code": "ORACLE_THINKING_TIME_PRE_SUBMIT_FAILED",
        "output_absent": True,
        "conversation_url_absent": True,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    report = module.diagnose(state_root)
    run = report["unresolved_runs"][0]

    assert run["bucket"] == "pre-submit-ui-contract"
    assert run["signature"] == "thinking-time-selection-unverified"


def test_settled_cdp_disconnect_before_prompt_submit_remains_retry_safe(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_dir = write_run(
        state_root,
        "c" * 8,
        status="attention_required",
        session_authority="pre_submit",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pre_submit_failure"] = {
        "code": "ORACLE_CDP_DISCONNECT_PRE_SUBMIT_FAILED",
        "output_absent": True,
        "conversation_url_absent": True,
        "prompt_submitted": False,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    report = module.diagnose(state_root)
    run = report["unresolved_runs"][0]

    assert run["bucket"] == "pre-submit-ui-contract"
    assert run["signature"] == "cdp-disconnected-before-prompt-submit"


def test_proven_exact_session_absence_is_classified_as_pre_submit_host_failure() -> None:
    module = load()
    verdict = module.classify_run(
        {
            "status": "attention_required",
            "session_authority": "pre_submit",
            "transport_status": "not_submitted",
            "task_outcome": "pending",
        },
        stdout_text="",
        has_output=False,
        pre_submit_session_absence={
            "code": "ORACLE_EXACT_SESSION_NOT_FOUND",
            "output_absent": True,
            "conversation_url_absent": True,
        },
    )

    assert verdict == {
        "bucket": "pre-submit-host-environment",
        "signature": "exact-session-absent-before-submit",
    }


def test_durable_terminal_run_is_complete_and_not_executed_is_separated(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "e" * 8,
        status="complete",
        output="answer",
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="executed",
    )
    write_run(
        state_root,
        "f" * 8,
        status="complete",
        output="TASK_OUTCOME: not_executed",
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="not_executed",
    )

    report = module.diagnose(state_root)

    assert report["bucket_counts"] == {"complete": 1, "terminal-task-not-executed": 1}
    assert [run["bucket"] for run in report["unresolved_runs"]] == ["terminal-task-not-executed"]


def test_terminal_blocked_oauth_503_is_not_misreported_as_complete(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_dir = write_run(
        state_root,
        "o" * 8,
        status="attention_required",
        output="@codex failed: OAuth token request failed 503\nTASK_OUTCOME: BLOCKED\n",
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["browser_observer"] = {"status": "running", "oracle_process_pid": 36252}
    state_path.write_text(json.dumps(state), encoding="utf-8")

    report = module.diagnose(state_root)
    verdict = report["unresolved_runs"][0]

    assert verdict["bucket"] == "terminal-task-not-executed"
    assert verdict["signature"] == "registered-app-oauth-token-request-503"
    assert verdict["anomalies"] == ["terminal-harvested-browser-observer-stale"]


def test_terminal_recursive_self_observation_has_bounded_signature(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_id = "r" * 12
    slug = f"oracle-project-{run_id[:10]}"
    output = (
        f"run ID: {run_id}\nexact slug: {slug}\nstatus: running\n"
        "task_outcome: pending\noutput.md absent\n"
        "continue-observing-same-exact-session\nTASK_OUTCOME: BLOCKED\n"
    )
    write_run(
        state_root,
        run_id,
        status="attention_required",
        output=output,
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )

    report = module.diagnose(state_root)

    assert report["unresolved_runs"][0]["signature"] == "post-submit-recursive-self-observation"


def test_general_terminal_blocked_or_simple_identity_mention_stays_generic(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_id = "s" * 12
    slug = f"oracle-project-{run_id[:10]}"
    write_run(
        state_root,
        run_id,
        status="attention_required",
        output=f"run ID: {run_id}\nslug: {slug}\nconcrete project blocker\nTASK_OUTCOME: BLOCKED\n",
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )

    report = module.diagnose(state_root)

    assert report["unresolved_runs"][0]["signature"] == "durable-output-reports-blocked"


@pytest.mark.parametrize(
    "missing_line",
    [
        "status: running",
        "task_outcome: pending",
        "output.md absent",
        "continue-observing-same-exact-session",
    ],
)
def test_recursive_signature_requires_every_bounded_self_observation_line(
    tmp_path: Path, missing_line: str
) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_id = "t" * 12
    slug = f"oracle-project-{run_id[:10]}"
    lines = [
        f"run ID: {run_id}", f"exact slug: {slug}", "status: running",
        "task_outcome: pending", "output.md absent",
        "continue-observing-same-exact-session", "TASK_OUTCOME: BLOCKED",
    ]
    output = "\n".join(line for line in lines if line != missing_line) + "\n"
    write_run(
        state_root, run_id, status="attention_required", output=output,
        session_authority="terminal", terminal_harvested=True, task_outcome="blocked",
    )

    report = module.diagnose(state_root)

    assert report["unresolved_runs"][0]["signature"] == "durable-output-reports-blocked"


def test_recursive_signature_rejects_a_different_slug(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_id = "u" * 12
    output = (
        f"run ID: {run_id}\nexact slug: oracle-project-someoneelse\nstatus: running\n"
        "task_outcome: pending\noutput.md absent\n"
        "continue-observing-same-exact-session\nTASK_OUTCOME: BLOCKED\n"
    )
    write_run(
        state_root, run_id, status="attention_required", output=output,
        session_authority="terminal", terminal_harvested=True, task_outcome="blocked",
    )

    report = module.diagnose(state_root)

    assert report["unresolved_runs"][0]["signature"] == "durable-output-reports-blocked"


def test_live_run_keeps_ownership_and_is_not_reported_as_failure(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "1" * 8,
        status="running",
        session_authority="live",
        stdout="status=response streaming\n",
    )

    report = module.diagnose(state_root)

    assert report["bucket_counts"] == {"active-or-uncertain": 1}
    assert report["unresolved_runs"] == []


def test_legacy_complete_ledger_is_not_reported_as_post_submit_defect(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "7" * 8,
        status="complete",
        output="legacy answer",
        session_authority="",
        terminal_harvested=False,
    )
    write_run(
        state_root,
        "8" * 8,
        status="complete",
        output="TASK_OUTCOME: not_executed",
        session_authority="",
        terminal_harvested=False,
        task_outcome="not_executed",
    )

    report = module.diagnose(state_root)

    assert report["bucket_counts"] == {
        "complete-legacy-ledger": 1,
        "terminal-task-not-executed": 1,
    }
    assert "post-submit-provider-incomplete" not in report["bucket_counts"]


def test_complete_status_without_output_is_not_treated_as_completion(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "6" * 8,
        status="complete",
        output=None,
    )

    report = module.diagnose(state_root)

    assert report["bucket_counts"] == {"unclassified": 1}
    assert report["unresolved_runs"][0]["signature"] == "no-recognized-signature"


def test_unreadable_state_stays_visible_as_unclassified(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_dir = state_root / "projects" / "projectkey" / "runs" / "broken00"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("{not json", encoding="utf-8")

    report = module.diagnose(state_root)

    assert report["bucket_counts"] == {"unclassified": 1}
    assert report["unresolved_runs"][0]["signature"] == "state-unreadable"


def test_uncertain_submission_timeout_is_a_post_submit_bucket(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "2" * 8,
        status="failed",
        stdout="ERROR: Prompt did not appear in conversation before timeout (send may have failed)\n",
    )

    report = module.diagnose(state_root)
    run = report["unresolved_runs"][0]

    assert run["bucket"] == "post-submit-provider-incomplete"
    assert run["signature"] == "submission-uncertain-prompt-not-observed"
    # An uncertain send must never be advertised as safe to repeat.
    assert "post-submit-provider-incomplete" not in report["safe_for_fresh_run_buckets"]


def test_host_watchdog_transition_is_post_submit_and_never_retry_safe() -> None:
    module = load()
    verdict = module.classify_run(
        {
            "status": "attention_required",
            "session_authority": "submitted_unknown",
            "terminal_harvested": False,
            "transport_status": "post_submit_watchdog_timeout",
            "task_outcome": "pending",
        },
        stdout_text="response streaming",
        has_output=False,
    )

    assert verdict == {
        "bucket": "post-submit-provider-incomplete",
        "signature": "host-wall-clock-expired-process-preserved",
    }


def test_version_resolution_prelaunch_failure_is_host_safe_only_with_absence_proof() -> None:
    module = load()
    verdict = module.classify_run(
        {
            "status": "attention_required",
            "session_authority": "pre_submit",
            "terminal_harvested": False,
            "task_outcome": "pending",
            "pre_submit_failure": {
                "code": "ORACLE_VERSION_RESOLUTION_PRELAUNCH_FAILED",
                "output_absent": True,
                "conversation_url_absent": True,
            },
        },
        stdout_text="",
        has_output=False,
    )

    assert verdict == {
        "bucket": "pre-submit-host-environment",
        "signature": "oracle-version-resolution-prelaunch-timeout",
    }


def test_devspace_restart_prelaunch_failure_has_a_bounded_signature() -> None:
    module = load()
    verdict = module.classify_run(
        {
            "status": "failed",
            "session_authority": "pre_submit",
            "terminal_harvested": False,
            "task_outcome": "pending",
            "pre_submit_failure": {
                "code": "DEVSPACE_SERVICE_RESTART_PRELAUNCH_FAILED",
                "output_absent": True,
                "conversation_url_absent": True,
            },
        },
        stdout_text="",
        has_output=False,
    )

    assert verdict == {
        "bucket": "pre-submit-host-environment",
        "signature": "devspace-service-restart-required",
    }


def test_oracle_attachment_size_preflight_is_host_safe_with_no_conversation() -> None:
    module = load()
    verdict = module.classify_run(
        {
            "status": "attention_required",
            "session_authority": "submitted_unknown",
            "terminal_harvested": False,
            "task_outcome": "pending",
            "pre_submit_failure": {
                "code": "ORACLE_ATTACHMENT_SIZE_PRELAUNCH_FAILED",
                "output_absent": True,
                "conversation_url_absent": True,
            },
        },
        stdout_text="",
        has_output=False,
    )

    assert verdict == {
        "bucket": "pre-submit-host-environment",
        "signature": "oracle-attachment-size-prelaunch-limit",
    }


def test_version_compatibility_drift_is_a_retry_safe_pre_submit_host_failure(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(state_root, "d" * 8, status="failed")
    run_dir = state_root / "projects" / "projectkey" / "runs" / ("d" * 8)
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["oracle"] = {"resolved_version": "unresolved"}
    state["session_authority"] = "pre_submit"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (run_dir / "stderr.log").write_text(
        "version resolution failed: Oracle compatibility is validated only for the tested version\n",
        encoding="utf-8",
    )

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["bucket"] == "pre-submit-host-environment"
    assert verdict["signature"] == "oracle-version-resolution-prelaunch-compatibility-drift"


def test_user_confirmed_no_submission_overrides_prompt_timeout_only_with_validated_proof() -> None:
    module = load()
    state = {
        "status": "attention_required",
        "session_authority": "pre_submit",
        "terminal_harvested": False,
        "task_outcome": "pending",
    }
    verdict = module.classify_run(
        state,
        stdout_text="ERROR: Prompt did not appear in conversation before timeout (send may have failed)\n",
        has_output=False,
        user_confirmed_no_submission=True,
    )

    assert verdict == {
        "bucket": "pre-submit-ui-contract",
        "signature": "user-confirmed-no-submission-after-prompt-timeout",
    }


def test_answer_without_a_durable_artifact_is_recoverable_not_unknown(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_dir = write_run(
        state_root,
        "3" * 8,
        status="attention_required",
        stdout="[browser] Released ChatGPT browser slot.\n",
    )
    (run_dir / "transcript.md").write_text("Answer:\nDevSpace 연결 가능합니다.\n", encoding="utf-8")

    report = module.diagnose(state_root)
    run = report["unresolved_runs"][0]

    assert run["bucket"] == "post-submit-provider-incomplete"
    assert run["signature"] == "answer-observed-without-durable-output"


def test_unreadable_state_is_the_only_unclassified_path(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_dir = state_root / "projects" / "projectkey" / "runs" / "broken00"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("{not json", encoding="utf-8")

    report = module.diagnose(state_root)

    assert report["bucket_counts"] == {"unclassified": 1}
    assert {run["signature"] for run in report["unresolved_runs"]} == {"state-unreadable"}


def test_report_is_read_only_for_persisted_runs(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_dir = write_run(
        state_root,
        "9" * 8,
        status="failed",
        stdout="ERROR: ChatGPT app mention was not confirmed in the composer.\n",
    )
    before = {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in sorted(state_root.rglob("*"))
        if path.is_file()
    }

    module.diagnose(state_root)

    after = {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in sorted(state_root.rglob("*"))
        if path.is_file()
    }
    assert after == before
    assert (run_dir / "state.json").is_file()


@pytest.mark.parametrize(
    ("evidence_text", "expected"),
    [
        (
            "\n".join((
                "api_tool Search plugins",
                '요청하기 {query: "DevSpace"}',
                "응답 {plugins: }",
                "Codex/DevSpace 작업공간 호출이 이 세션에서 사용 가능한 "
                "workspaceId 또는 파일 읽기 결과를 반환하지 않아",
            )),
            True,
        ),
        (
            'Search plugins {query: "DevSpace"} -> {plugins: }\n'
            "did not return a usable workspaceId",
            True,
        ),
        (
            "Opened the qualified workspace, read AGENTS.md, and was blocked "
            "by the genuine mission dependency.",
            False,
        ),
        ("DevSpace was mentioned in passing during the mission review.", False),
        ('Search plugins {query: "DevSpace"} found the registered connector.', False),
        ("SEARCH PLUGINS {PLUGINS: }", True),
    ],
)
def test_foreign_connector_search_evidence_requires_search_and_failure(
    evidence_text: str, expected: bool
) -> None:
    module = load()

    assert module._foreign_connector_search_evidence(evidence_text) is expected


@pytest.mark.parametrize(
    ("evidence_text", "expected"),
    [
        (
            "codex open_workspace succeeded with workspace id ws_123. "
            "The separate read `AGENTS.md` failed with mcp_network_error: Connection failed.",
            True,
        ),
        (
            "codex open_workspace 성공, workspace id ws_123. "
            "이후 파일 읽기가 mcp_network_error: Connection failed로 실패했습니다.",
            True,
        ),
        ("open_workspace succeeded with workspace id ws_123 and read AGENTS.md succeeded", False),
        ("read AGENTS.md failed with mcp_network_error: Connection failed", False),
    ],
)
def test_same_workspace_read_network_failure_requires_open_and_read_evidence(
    evidence_text: str, expected: bool
) -> None:
    module = load()

    assert module._same_workspace_read_network_failure_evidence(evidence_text) is expected


def test_terminal_blocked_same_workspace_read_network_failure_has_bounded_signature(
    tmp_path: Path,
) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "r" * 8,
        status="attention_required",
        output=(
            "codex open_workspace succeeded with workspace id ws_350cbc6826.\n"
            "The separate read `AGENTS.md` failed with mcp_network_error: Connection failed.\n"
            "TASK_OUTCOME: BLOCKED\n"
        ),
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["bucket"] == "terminal-task-not-executed"
    assert verdict["signature"] == "registered-app-read-network-failure-after-workspace-open"


def test_terminal_blocked_foreign_connector_search_has_bounded_signature(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "w" * 8,
        status="attention_required",
        output=(
            "api_tool Search plugins\n"
            '요청하기 {query: "DevSpace"}\n'
            "응답 {plugins: }\n"
            "Codex/DevSpace 작업공간 호출이 이 세션에서 사용 가능한 workspaceId 또는 "
            "파일 읽기 결과를 반환하지 않아\n"
            "TASK_OUTCOME: BLOCKED\n"
        ),
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["bucket"] == "terminal-task-not-executed"
    assert verdict["signature"] == "foreign-workspace-connector-substituted"


def test_terminal_not_executed_foreign_connector_search_has_bounded_signature(
    tmp_path: Path,
) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "x" * 8,
        status="attention_required",
        output=(
            'Search plugins {query: "DevSpace"} -> {plugins: }\n'
            "did not return a usable workspaceId\n"
            "TASK_OUTCOME: NOT_EXECUTED\n"
        ),
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="not_executed",
    )

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["bucket"] == "terminal-task-not-executed"
    assert verdict["signature"] == "foreign-workspace-connector-substituted"


def test_terminal_blocked_without_foreign_connector_evidence_stays_generic(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "y" * 8,
        status="attention_required",
        output="Opened the qualified workspace and read AGENTS.md.\nTASK_OUTCOME: BLOCKED\n",
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["signature"] == "durable-output-reports-blocked"


def test_terminal_blocked_detects_foreign_connector_evidence_in_transcript(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_dir = write_run(
        state_root,
        "z" * 8,
        status="attention_required",
        output="TASK_OUTCOME: BLOCKED\n",
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )
    (run_dir / "transcript.md").write_text(
        'Search plugins {query: "DevSpace"} -> {plugins: }\n'
        "did not return a usable workspaceId\n",
        encoding="utf-8",
    )

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["bucket"] == "terminal-task-not-executed"
    assert verdict["signature"] == "foreign-workspace-connector-substituted"


def test_oauth_503_outranks_foreign_connector_search_evidence(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "q" * 8,
        status="attention_required",
        output=(
            "OAuth token request failed: 503\n"
            'Search plugins {query: "DevSpace"} -> {plugins: }\n'
            "did not return a usable workspaceId\n"
            "TASK_OUTCOME: BLOCKED\n"
        ),
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["signature"] == "registered-app-oauth-token-request-503"


def test_terminal_devspace_checkout_502_nonexecution_has_a_bounded_signature(
    tmp_path: Path,
) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    project_root = state_root / "project"
    run_dir = write_run(
        state_root,
        "x" * 12,
        status="attention_required",
        output=(
            f"I opened the exact project root {project_root} in checkout mode.\n"
            "The checkout failed with 502 Upstream or external service errors and no workspace ID.\n"
            "I did not read the mission, did not run commands, and did not change files.\n"
            "TASK_OUTCOME: BLOCKED\n"
        ),
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["transport"] = "pro-devspace"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["bucket"] == "terminal-task-not-executed"
    assert verdict["signature"] == "terminal-devspace-checkout-502-no-execution"


def test_terminal_devspace_app_tools_unavailable_has_its_own_bounded_signature(
    tmp_path: Path,
) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    project_root = state_root / "project"
    run_dir = write_run(
        state_root,
        "y" * 12,
        status="attention_required",
        output=(
            "이 세션에는 dev 앱이 제공하는 workspace 도구가 노출되어 있지 않아 "
            f"지정한 {project_root}를 dev checkout 모드로 열 수 없습니다. "
            "사용자가 금지한 다른 workspace 커넥터·셸·웹·Oracle 우회는 시도하지 않았으며, "
            "따라서 미션 파일이나 AGENTS.md도 읽거나 수정하지 않았습니다.\n"
            "TASK_OUTCOME: BLOCKED\n"
        ),
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({"transport": "devspace", "app_name": "dev"})
    state_path.write_text(json.dumps(state), encoding="utf-8")

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["bucket"] == "terminal-task-not-executed"
    assert verdict["signature"] == (
        "terminal-devspace-app-tools-unavailable-no-execution"
    )


def test_terminal_devspace_read_chunk_exposure_failure_has_bounded_signature(
    tmp_path: Path,
) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    project_root = state_root / "project"
    escaped_root = str(project_root).replace("\\", "\\\\")
    output = (
        "* 앱: `dev`\n"
        "* Workspace ID: `ws_a0770e8338`\n"
        f"* 정확한 루트: `{escaped_root}`\n"
        "* 모드: `checkout`\n"
        "* 적용 `AGENTS.md`: 전체 확인 완료\n"
        "* 미션 파일: 전체 확인 완료\n"
        "* 보고서 첫 Markdown heading: `# Example`\n"
        "* 저장소 쓰기 작업: 없음\n"
        "* 금지된 Oracle controller/run 관련 파일·상태·프로세스: 검사하거나 호출하지 않음\n"
        "현재 `dev` 앱이 이 workspace에서 노출한 도구에 `read_chunk`가 없으며, "
        "`chunk` 관련 도구가 반환되지 않았습니다.\n"
        "따라서 다음 단계인 정확히 한 번의 `git status --short --branch` 명령도 실행하지 않았습니다.\n"
        "* 명령 실행: **안 함**\n"
        "* exit code: **미확인**\n"
        "* command output: **없음**\n"
        "TASK_OUTCOME: BLOCKED\n"
    )
    run_dir = write_run(
        state_root,
        "r" * 12,
        status="attention_required",
        output=output,
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({
        "transport": "devspace",
        "app_name": "dev",
        "profile": {
            "model": "gpt-5.6",
            "model_strategy": "select",
            "thinking_time": "extra-high",
        },
    })
    state_path.write_text(json.dumps(state), encoding="utf-8")

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["bucket"] == "terminal-task-not-executed"
    assert verdict["signature"] == (
        "terminal-devspace-read-chunk-unavailable-after-read-only-probe"
    )


def test_recursive_self_observation_outranks_foreign_connector_search_evidence(
    tmp_path: Path,
) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    run_id = "v" * 12
    slug = f"oracle-project-{run_id[:10]}"
    write_run(
        state_root,
        run_id,
        status="attention_required",
        output=(
            f"run ID: {run_id}\nexact slug: {slug}\nstatus: running\n"
            "task_outcome: pending\noutput.md absent\n"
            "continue-observing-same-exact-session\n"
            'Search plugins {query: "DevSpace"} -> {plugins: }\n'
            "did not return a usable workspaceId\n"
            "TASK_OUTCOME: BLOCKED\n"
        ),
        session_authority="terminal",
        terminal_harvested=True,
        task_outcome="blocked",
    )

    verdict = module.diagnose(state_root)["unresolved_runs"][0]

    assert verdict["signature"] == "post-submit-recursive-self-observation"
