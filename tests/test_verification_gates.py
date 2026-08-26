from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_fast_gate_targets_exist_and_cover_the_pre_submit_contracts() -> None:
    gate = load("fast_gate_test", SCRIPTS / "run_fast_gate.py")

    for target in gate.FAST_TARGETS:
        assert (ROOT / target.split("::", 1)[0]).is_file(), target

    covered = {target.split("::", 1)[0] for target in gate.FAST_TARGETS}
    # The buckets that actually blocked runs before submission must be gated.
    assert "tests/test_chatgpt_oracle_state.py" in covered
    assert "tests/test_chatgpt_oracle_run.py" in covered
    assert "tests/test_chatgpt_oracle_compat.py" in covered
    assert "tests/test_chatgpt_oracle_incident.py" in covered
    assert "tests/test_chatgpt_oracle_diagnose.py" in covered
    selected_runner_contracts = {
        target for target in gate.FAST_TARGETS
        if target.startswith("tests/test_chatgpt_oracle_run.py::")
    }
    assert len(selected_runner_contracts) >= 20
    assert any("fresh_app_read_gate" in target for target in selected_runner_contracts)
    assert any("dynamic_cdp_port" in target for target in selected_runner_contracts)
    assert any("foreign_task_recovery" in target for target in selected_runner_contracts)
    assert "tests/test_chatgpt_oracle_run.py" not in gate.FAST_TARGETS
    assert gate.FAST_DESELECTS == [
        "tests/test_chatgpt_oracle_compat.py::test_archived_parent_direct_restore_requires_exact_control_and_composer_transition"
    ]
    deselected_path = gate.FAST_DESELECTS[0].split("::", 1)[0]
    assert (ROOT / deselected_path).is_file()
    # The gate must stay fast enough to run after every batch of edits. Pin an
    # upper bound instead of one exact value so added coverage does not require
    # editing this contract, while a runaway budget still fails.
    assert 30.0 <= gate.DEFAULT_BUDGET_SECONDS <= 120.0


def test_fast_gate_is_a_strict_subset_of_the_full_suite() -> None:
    gate = load("fast_gate_subset_test", SCRIPTS / "run_fast_gate.py")
    all_tests = {
        f"tests/{path.name}" for path in (ROOT / "tests").glob("test_*.py")
    }

    covered = {target.split("::", 1)[0] for target in gate.FAST_TARGETS}
    assert covered < all_tests


def test_fast_gate_hides_windows_console_windows() -> None:
    gate = load("fast_gate_window_test", SCRIPTS / "run_fast_gate.py")

    source = (SCRIPTS / "run_fast_gate.py").read_text(encoding="utf-8")
    assert "CREATE_NO_WINDOW" in source
    assert "SW_HIDE" in source
    assert callable(gate._hidden_process_kwargs)


def test_golden_path_smoke_passes_against_the_source_tree() -> None:
    smoke = load("golden_path_smoke_test", SCRIPTS / "run_golden_path_smoke.py")

    result = smoke.run_smoke(bin_root=ROOT / "bin")

    assert result["ok"] is True, result["failed_checks"]
    assert result["submitted_question"] is False
    names = [item["check"] for item in result["checks"]]
    for required in (
        "mode_contract_compiles",
        "manifest_loads",
        "devspace_transport_selected",
        "prompt_is_one_line_with_app_mention",
        "dry_run_preview_ok",
        "argv_never_submits_files",
        "argv_hides_browser_window",
        "argv_selects_a_model",
        "profile_copy_matches_host_capability",
        "lifecycle_vocabulary_is_bounded",
    ):
        assert required in names


def test_golden_path_smoke_never_submits_or_launches_a_browser() -> None:
    source = (SCRIPTS / "run_golden_path_smoke.py").read_text(encoding="utf-8")

    assert "dry_run=True" in source
    assert "dry_run=False" not in source
    assert '"submitted_question": False' in source


def test_ci_workflow_runs_the_fast_gate_and_golden_path_smoke() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-portability.yml").read_text(encoding="utf-8")

    assert "scripts/run_fast_gate.py" in workflow
    assert "scripts/run_golden_path_smoke.py" in workflow
    # The fast gate must run before the long suite so a broken launch contract
    # fails in seconds instead of minutes.
    assert workflow.index("run_fast_gate.py") < workflow.index("run_v4_contract_tests.py --full")


def test_release_manifest_ships_the_new_verification_scripts() -> None:
    manifest = json.loads((ROOT / "install-manifest.json").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert "bin/chatgpt_oracle_incident.py" in manifest["include"]
    assert "bin/chatgpt_oracle_incident.py" in package["files"]
