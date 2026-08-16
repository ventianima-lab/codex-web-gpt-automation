#!/usr/bin/env python
"""Read-only Oracle failure-signature report.

This tool never launches Oracle, never touches a browser, and never mutates
run state.  It classifies every persisted run into a small bounded set of
buckets so repairs target the layer that actually fails instead of the layer
that reported the symptom.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Iterable

BIN = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STATE = _load("oracle_diagnose_state", BIN / "chatgpt_oracle_state.py")

SCHEMA = "codex.chatgpt.oracle-diagnosis/v1"

# Ordered, mutually exclusive buckets.  The first matching rule wins, so keep
# pre-submit host/UI causes ahead of post-submit provider causes: a run that
# never reached the composer must never be reported as a recovery defect.
PRE_SUBMIT_HOST = "pre-submit-host-environment"
PRE_SUBMIT_UI = "pre-submit-ui-contract"
BROWSER_LIFETIME = "browser-lifetime-lost"
PROVIDER_INCOMPLETE = "post-submit-provider-incomplete"
PROVIDER_GENERATION_ERROR = "post-submit-provider-generation-error"
RECOVERY_BINDING = "post-submit-recovery-binding"
TASK_NOT_EXECUTED = "terminal-task-not-executed"
COMPLETE = "complete"
LEGACY_COMPLETE = "complete-legacy-ledger"
ACTIVE = "active-or-uncertain"
UNCLASSIFIED = "unclassified"

BUCKETS = (
    COMPLETE,
    LEGACY_COMPLETE,
    ACTIVE,
    PRE_SUBMIT_HOST,
    PRE_SUBMIT_UI,
    BROWSER_LIFETIME,
    PROVIDER_INCOMPLETE,
    PROVIDER_GENERATION_ERROR,
    RECOVERY_BINDING,
    TASK_NOT_EXECUTED,
    UNCLASSIFIED,
)

SIGNATURE_RULES: tuple[tuple[str, str, str], ...] = (
    ("rsync", PRE_SUBMIT_HOST, "oracle-profile-copy-requires-rsync"),
    ("cannot be combined with", PRE_SUBMIT_HOST, "oracle-launch-flags-mutually-exclusive"),
    ("app mention suggestion did not appear", PRE_SUBMIT_UI, "app-mention-suggestion-absent"),
    ("app mention was not confirmed", PRE_SUBMIT_UI, "app-mention-not-confirmed"),
    ("Unable to find model option", PRE_SUBMIT_UI, "model-option-label-missing"),
    ("Thinking time: selection unverified", PRE_SUBMIT_UI, "thinking-time-selection-unverified"),
    ("Thinking time: unknown outcome selecting", PRE_SUBMIT_UI, "thinking-time-selection-unverified"),
    ("Chrome window closed", BROWSER_LIFETIME, "browser-window-closed-early"),
    ("disconnected before completion", BROWSER_LIFETIME, "browser-disconnected-early"),
    ("ECONNREFUSED", RECOVERY_BINDING, "recovery-cdp-connection-refused"),
    (
        "chatgpt-response-generation-failed:",
        PROVIDER_GENERATION_ERROR,
        "chatgpt-visible-generation-error",
    ),
    ("timed out before completion", PROVIDER_INCOMPLETE, "assistant-response-timeout"),
    (
        "Prompt did not appear in conversation before timeout",
        PROVIDER_INCOMPLETE,
        "submission-uncertain-prompt-not-observed",
    ),
)

REMEDIATION = {
    PRE_SUBMIT_HOST: "Fix the local launch contract; no web submission occurred, so a fresh run is safe.",
    PRE_SUBMIT_UI: "Relax or realign the ChatGPT UI contract; no web submission occurred, so a fresh run is safe.",
    BROWSER_LIFETIME: "Keep the Oracle-owned browser alive for the run; recover the exact slug before any retry.",
    PROVIDER_INCOMPLETE: "Resume the exact slug with live recovery; never resubmit.",
    PROVIDER_GENERATION_ERROR: (
        "The provider visibly ended generation with an error. Review that exact conversation before intentionally retrying."
    ),
    RECOVERY_BINDING: "Reopen only the persisted exact conversation URL; never resubmit.",
    TASK_NOT_EXECUTED: "Transport succeeded but the task did not run; inspect the durable output before deciding.",
    COMPLETE: "None.",
    LEGACY_COMPLETE: "None; durable output exists from a run recorded before terminal_harvested was tracked.",
    ACTIVE: "Leave ownership intact and observe the exact slug only.",
    UNCLASSIFIED: "Add a signature rule for this run before repairing anything.",
}


def _read_text(path: Path, *, limit: int = 200_000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace")


def _output_is_nonempty(path: Path) -> bool:
    try:
        return path.is_file() and bool(path.read_bytes().strip())
    except OSError:
        return False


def classify_run(
    state: dict[str, Any],
    *,
    stdout_text: str,
    has_output: bool,
    transcript_text: str = "",
    user_confirmed_no_submission: bool = False,
    pre_submit_host_failure: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return the bucket and signature for one persisted run.

    Ordering matters more than breadth here.  Local exit codes and local
    status never outrank durable evidence, and a pre-submit signature always
    wins over a post-submit interpretation.
    """
    outcome = str(state.get("task_outcome") or "")
    # Single authority source, shared with the runner, so the report and the
    # runner can never disagree about what "finished" means.
    verdict = STATE.resolve_lifecycle(state, output_is_present=has_output)
    lifecycle = str(verdict["lifecycle"])
    source = str(verdict["authority_source"])

    pre_submit_failure = state.get("pre_submit_failure")
    host_failure = pre_submit_failure if isinstance(pre_submit_failure, dict) else pre_submit_host_failure
    if (
        isinstance(host_failure, dict)
        and host_failure.get("output_absent") is True
        and host_failure.get("conversation_url_absent") is True
    ):
        code = str(host_failure.get("code") or "")
        if code == "ORACLE_ATTACHMENT_SIZE_PRELAUNCH_FAILED":
            return {"bucket": PRE_SUBMIT_HOST, "signature": "oracle-attachment-size-prelaunch-limit"}
        if code == "ORACLE_MODEL_SWITCHER_PRE_SUBMIT_FAILED":
            return {"bucket": PRE_SUBMIT_UI, "signature": "model-option-label-missing"}
        if code == "ORACLE_THINKING_TIME_PRE_SUBMIT_FAILED":
            return {"bucket": PRE_SUBMIT_UI, "signature": "thinking-time-selection-unverified"}
        if code == "ORACLE_CDP_DISCONNECT_PRE_SUBMIT_FAILED":
            return {"bucket": PRE_SUBMIT_UI, "signature": "cdp-disconnected-before-prompt-submit"}
        if code != "ORACLE_VERSION_RESOLUTION_PRELAUNCH_FAILED":
            return {"bucket": UNCLASSIFIED, "signature": "unrecognized-pre-submit-host-failure"}
        return {
            "bucket": PRE_SUBMIT_HOST,
            "signature": (
                "oracle-version-resolution-prelaunch-compatibility-drift"
                if host_failure.get("failure_reason") == "compatibility-version-drift"
                else "oracle-version-resolution-prelaunch-timeout"
            ),
        }
    if lifecycle == "complete":
        if source == "exact-terminal-evidence":
            return {"bucket": COMPLETE, "signature": "terminal-harvested-output"}
        return {"bucket": LEGACY_COMPLETE, "signature": "legacy-ledger-durable-output"}
    if lifecycle == "abandoned":
        return {"bucket": ACTIVE, "signature": "explicitly-abandoned"}
    if outcome == "not_executed" and has_output:
        return {"bucket": TASK_NOT_EXECUTED, "signature": "durable-output-reports-no-execution"}
    if user_confirmed_no_submission:
        return {
            "bucket": PRE_SUBMIT_UI,
            "signature": "user-confirmed-no-submission-after-prompt-timeout",
        }
    if str(state.get("transport_status") or "") == "post_submit_watchdog_timeout":
        return {
            "bucket": PROVIDER_INCOMPLETE,
            "signature": "host-wall-clock-expired-process-preserved",
        }

    for needle, bucket, signature in SIGNATURE_RULES:
        if needle in stdout_text:
            return {"bucket": bucket, "signature": signature}

    if lifecycle == "running":
        return {"bucket": ACTIVE, "signature": f"lifecycle-running-via-{source}"}
    if has_output:
        return {"bucket": PROVIDER_INCOMPLETE, "signature": "output-present-without-terminal-settlement"}
    if not has_output and ("Answer:" in stdout_text or "Answer:" in transcript_text):
        # The provider answered, but the durable artifact was never written, so
        # the run is recoverable rather than unknown.
        return {
            "bucket": PROVIDER_INCOMPLETE,
            "signature": "answer-observed-without-durable-output",
        }
    return {"bucket": UNCLASSIFIED, "signature": "no-recognized-signature"}


def iter_run_dirs(state_root: Path) -> Iterable[Path]:
    projects = state_root / "projects"
    if not projects.is_dir():
        return ()
    return sorted(path.parent for path in projects.glob("*/runs/*/state.json"))


def diagnose(state_root: Path | None = None) -> dict[str, Any]:
    root = (state_root or STATE.oracle_state_root()).expanduser().resolve()
    runs: list[dict[str, Any]] = []
    for run_dir in iter_run_dirs(root):
        try:
            state = STATE.load_state(run_dir / "state.json")
        except Exception as exc:  # noqa: BLE001 - a corrupt run must stay visible
            runs.append({
                "run_dir": str(run_dir),
                "bucket": UNCLASSIFIED,
                "signature": "state-unreadable",
                "detail": type(exc).__name__,
            })
            continue
        verdict = classify_run(
            state,
            stdout_text=_read_text(run_dir / "stdout.log"),
            has_output=STATE.durable_output_is_present(state),
            transcript_text=_read_text(run_dir / "transcript.md"),
            user_confirmed_no_submission=(
                STATE.proven_user_confirmed_no_submission(run_dir / "state.json") is not None
            ),
            pre_submit_host_failure=STATE.proven_pre_submit_host_failure(run_dir / "state.json"),
        )
        runs.append({
            "run_dir": str(run_dir),
            "project_root": str(state.get("project_root") or ""),
            "status": str(state.get("status") or ""),
            "session_authority": str(state.get("session_authority") or ""),
            **verdict,
        })

    counts = {bucket: 0 for bucket in BUCKETS}
    for run in runs:
        counts[str(run["bucket"])] = counts.get(str(run["bucket"]), 0) + 1
    unresolved = [run for run in runs if run["bucket"] not in {COMPLETE, ACTIVE}]
    return {
        "schema": SCHEMA,
        "state_root": str(root),
        "total_runs": len(runs),
        "bucket_counts": {name: count for name, count in counts.items() if count},
        "top_buckets": [
            {"bucket": name, "count": count, "remediation": REMEDIATION.get(name, "")}
            for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            if count
        ],
        "safe_for_fresh_run_buckets": [PRE_SUBMIT_HOST, PRE_SUBMIT_UI],
        "unresolved_runs": unresolved,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Oracle failure-signature report.")
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--summary-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = diagnose(args.state_root)
    if args.summary_only:
        report = {key: value for key, value in report.items() if key != "unresolved_runs"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
