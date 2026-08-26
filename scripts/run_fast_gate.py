#!/usr/bin/env python
"""Sub-minute gate for Oracle automation changes.

The full suite takes many minutes, which pushed every repair into one-incident-
at-a-time edits.  This gate covers the contracts that actually broke runs before
submission - launch arguments, lifecycle authority, incident ownership,
compatibility patch shape, and release packaging - and must finish well inside
its wall-clock budget so it can run after every batch of edits.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAST_TARGETS = [
    "tests/test_chatgpt_oracle_state.py",
    # The full runner module now contains hundreds of exhaustive lifecycle
    # contradiction permutations and takes about 65 seconds by itself.  Keep a
    # bounded cross-section of the launch, ownership, read-gate, completion,
    # restart, and recovery contracts here; the full v4/CI suite still runs
    # every permutation.
    "tests/test_chatgpt_oracle_run.py::test_default_oracle_command_is_pinned_to_the_hash_validated_version",
    "tests/test_chatgpt_oracle_run.py::test_conversation_url_helpers_preserve_exact_binding_and_detect_conflicts",
    "tests/test_chatgpt_oracle_run.py::test_new_runs_use_dynamic_cdp_port_instead_of_global_9222",
    "tests/test_chatgpt_oracle_run.py::test_fresh_execution_binds_runtime_task_but_plain_manifest_loading_stays_unbound",
    "tests/test_chatgpt_oracle_run.py::test_task_outcome_terminal_watchdog_is_exactly_v1_and_scrubs_inherited_state",
    "tests/test_chatgpt_oracle_run.py::test_foreign_task_recovery_is_fail_closed_before_browser_or_oracle_access",
    "tests/test_chatgpt_oracle_run.py::test_legacy_unbound_recovery_is_not_adopted_by_the_current_task",
    "tests/test_chatgpt_oracle_run.py::test_dry_run_never_executes_and_has_no_file_flag",
    "tests/test_chatgpt_oracle_run.py::test_default_signed_in_profile_is_copied_per_run_and_window_is_hidden",
    "tests/test_chatgpt_oracle_run.py::test_regular_runs_use_provider_window_and_nonterminal_status_audit",
    "tests/test_chatgpt_oracle_run.py::test_pro_readonly_dry_run_fails_before_layout_without_fresh_app_read_gate",
    "tests/test_chatgpt_oracle_run.py::test_pro_readonly_dry_run_reports_bound_app_read_gate",
    "tests/test_chatgpt_oracle_run.py::test_new_writable_pro_manifest_is_rejected_before_layout_or_browser",
    "tests/test_chatgpt_oracle_run.py::test_d_coin_missing_exact_root_blocks_before_oracle_or_run_creation",
    "tests/test_chatgpt_oracle_run.py::test_complete_requires_zero_exit_and_nonempty_output",
    "tests/test_chatgpt_oracle_run.py::test_v1_task_outcome_separates_transport_success_from_execution",
    "tests/test_chatgpt_oracle_run.py::test_devspace_patch_change_blocks_before_submission_until_restart",
    "tests/test_chatgpt_oracle_run.py::test_post_submit_nonzero_requires_exact_recovery_and_never_restarts",
    "tests/test_chatgpt_oracle_run.py::test_recovery_captures_output_and_updates_state",
    "tests/test_chatgpt_oracle_run.py::test_unresolved_exact_session_blocks_different_parent_submission",
    "tests/test_chatgpt_oracle_run.py::test_recovery_never_downgrades_durable_complete",
    "tests/test_chatgpt_oracle_diagnose.py",
    "tests/test_chatgpt_oracle_incident.py",
    "tests/test_chatgpt_oracle_compat.py",
    "tests/test_chatgpt_oracle_profiles.py",
    "tests/test_global_gpt_browser_policy.py",
    "tests/test_release_packaging.py",
    "tests/test_docs_contract.py",
    "tests/test_codex_web_gpt_onboarding.py",
    "tests/test_codex_global_agents_setup.py",
    "tests/test_codex_runtime_identity.py",
    "tests/test_codexpro_cloudflared_launchd.py",
    "tests/test_ultra_economy_mode.py",
]

# This LKG archived-parent DOM integration test intentionally exercises several
# real browser polling windows and takes about 12 seconds by itself. The full
# v4/CI suite still runs it; the fast pre-submit gate keeps the surrounding
# hash/patch/ownership tests while omitting only this terminal follow-up UI
# simulation so normal host jitter cannot consume the entire gate budget.
FAST_DESELECTS = [
    "tests/test_chatgpt_oracle_compat.py::test_archived_parent_direct_restore_requires_exact_control_and_composer_transition",
]

# The onboarding wizard added resumable-state, language, fail-closed
# confirmation, and forged-evidence coverage to this gate, so the wall-clock
# ceiling moved from 60s to 100s.  It must still finish fast enough to run
# after every batch of edits.
DEFAULT_BUDGET_SECONDS = 100.0


def _hidden_process_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startupinfo}


def run_fast_gate(*, budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> dict[str, object]:
    environment = dict(os.environ)
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    with tempfile.TemporaryDirectory(prefix="codex-oracle-fast-gate-") as basetemp:
        command = [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            *FAST_TARGETS,
            *(f"--deselect={item}" for item in FAST_DESELECTS),
            "--basetemp", basetemp,
        ]
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            check=False,
            env=environment,
            **_hidden_process_kwargs(),
        )
        elapsed = time.monotonic() - started
    return {
        "exit_code": int(completed.returncode),
        "elapsed_seconds": round(elapsed, 2),
        "budget_seconds": budget_seconds,
        "within_budget": elapsed <= budget_seconds,
        "targets": list(FAST_TARGETS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the sub-minute Oracle automation gate.")
    parser.add_argument("--budget-seconds", type=float, default=DEFAULT_BUDGET_SECONDS)
    parser.add_argument(
        "--enforce-budget",
        action="store_true",
        help="Fail when the gate exceeds its wall-clock budget even if tests pass.",
    )
    args = parser.parse_args(argv)
    result = run_fast_gate(budget_seconds=args.budget_seconds)
    print(
        f"fast-gate exit={result['exit_code']} "
        f"elapsed={result['elapsed_seconds']}s budget={result['budget_seconds']}s "
        f"within_budget={result['within_budget']}"
    )
    if result["exit_code"] != 0:
        return int(result["exit_code"])
    if args.enforce_budget and not result["within_budget"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
