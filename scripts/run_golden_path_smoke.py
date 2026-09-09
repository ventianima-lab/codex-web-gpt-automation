#!/usr/bin/env python
"""Golden-path smoke for the installed Oracle + DevSpace deployment.

Historically a broken launch contract, model label, profile-copy dependency, or
app-mention rule was only discovered by a real 40-minute web run.  This smoke
exercises the same code path end to end - mode contract, manifest compilation,
manifest loading, compatibility identity, and the exact Oracle argv - without
submitting a question or touching a browser, so those regressions surface in
seconds.

Use `--check-installed` to verify the deployed copy under `%USERPROFILE%\\.codex`
instead of the source tree.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

SOURCE_BIN = Path(__file__).resolve().parents[1] / "bin"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def installed_bin() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return (home / "bin").expanduser().resolve()


def run_smoke(*, bin_root: Path) -> dict[str, Any]:
    """Compile and preview the current mission contract without browser work."""
    dispatch = _load("golden_path_dispatch", bin_root / "chatgpt_oracle_dispatch.py")
    executor = dispatch.EXECUTOR
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    with tempfile.TemporaryDirectory(prefix="codex-oracle-golden-path-") as workspace:
        base = Path(workspace)
        project = base / "project"
        project.mkdir()
        mission = project / "mission.md"
        mission.write_text("Golden path smoke mission. Do nothing.\\n", encoding="utf-8")
        host_state = base / "host-state"
        compiled = dispatch.compile_manifest(
            project_root=project, mission_path=mission, run_root=host_state,
        )
        config = compiled["config"]
        record("mission_contract_compiles", compiled["manifest"]["schema"] == executor.MANIFEST_SCHEMA)
        record("explicit_latest_pro_default", config.model == "latest" and config.effort == "pro")
        record("registered_app_selected", config.app_name == "codex")
        prompt = executor._composer_prompt(config)
        record("prompt_has_exact_app_and_mission", "@codex" in prompt and str(mission.resolve()) in prompt)
        preview = executor.execute_config(config, dry_run=True)
        argv = preview["argv"]
        record("dry_run_preview_ok", bool(preview.get("ok")) and not host_state.exists())
        record("argv_never_submits_files", "--file" not in argv)
        record("owned_browser_preflight_shipped", (bin_root / "oracle_temporary_personalization_preflight.mjs").is_file())
        record("argv_selects_a_model", "--model" in argv and "--browser-model-strategy" in argv)
        record("temporary_chat_selected", argv[argv.index("--chatgpt-url") + 1] == executor.CHATGPT_URL)
        # Startup now belongs to the wrapper, not Oracle's own browser launcher.
        target = "A" * 32
        bound = executor.build_oracle_argv(config, ["oracle"], base / "output.md", "smoke",
                                          cdp_port=12345, browser_tab=target)
        record("argv_reuses_preflight_tab", bound[bound.index("--browser-tab") + 1] == target
               and bound[bound.index("--remote-chrome") + 1] == "127.0.0.1:12345")
        seed = base / "seed"
        (seed / "Default").mkdir(parents=True)
        seed_preferences = json.dumps({"profile": {"exit_type": "Crashed", "exited_cleanly": False}})
        (seed / "Default/Preferences").write_text(seed_preferences, encoding="utf-8")
        copied = executor._prepare_run_profile(replace(config, copy_profile=seed), Path(preview["run_dir"]))
        prepared = json.loads((copied / "Default/Preferences").read_text(encoding="utf-8"))
        record("profile_supports_reconnect", copied.is_dir() and "--copy-profile" not in bound
               and "--browser-manual-login-profile-dir" not in bound
               and prepared["profile"]["exit_type"] == "Normal"
               and prepared["session"]["restore_on_startup"] == 5
               and (seed / "Default/Preferences").read_text(encoding="utf-8") == seed_preferences)
        record("no_archive_phase", argv[argv.index("--browser-archive") + 1] == "never")
    ok = all(item["ok"] for item in checks)
    return {
        "schema": "codex.chatgpt.oracle-golden-path/v1",
        "ok": ok,
        "bin_root": str(bin_root),
        "submitted_question": False,
        "checks": checks,
        "failed_checks": [item["check"] for item in checks if not item["ok"]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the no-submission Oracle golden-path smoke.")
    parser.add_argument("--check-installed", action="store_true")
    args = parser.parse_args(argv)
    bin_root = installed_bin() if args.check_installed else SOURCE_BIN
    try:
        result = run_smoke(bin_root=bin_root)
    except Exception as exc:  # noqa: BLE001 - the smoke must report, not traceback
        result = {
            "schema": "codex.chatgpt.oracle-golden-path/v1",
            "ok": False,
            "bin_root": str(bin_root),
            "submitted_question": False,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
