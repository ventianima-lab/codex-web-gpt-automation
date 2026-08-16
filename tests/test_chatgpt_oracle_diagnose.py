from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

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


def test_visible_generation_error_has_its_own_terminal_provider_bucket(tmp_path: Path) -> None:
    module = load()
    state_root = tmp_path / "oracle-state"
    write_run(
        state_root,
        "g" * 8,
        status="attention_required",
        stdout=(
            "ERROR: chatgpt-response-generation-failed: "
            "something went wrong while generating the response\n"
        ),
        session_authority="terminal",
    )

    report = module.diagnose(state_root)
    run = report["unresolved_runs"][0]

    assert run["bucket"] == "post-submit-provider-generation-error"
    assert run["signature"] == "chatgpt-visible-generation-error"
    assert "post-submit-provider-generation-error" not in report["safe_for_fresh_run_buckets"]


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
