from __future__ import annotations

"""Convenience entry point for the single ordinary Oracle execution flow."""

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


EXECUTOR = _load("chatgpt_oracle_dispatch_executor", BIN / "chatgpt_oracle_execute.py")


def compile_manifest(
    *,
    project_root: Path,
    mission_path: Path,
    run_root: Path | None = None,
    run_id: str | None = None,
    source_thread_id: str | None = None,
    model: str = EXECUTOR.DEFAULT_MODEL,
    effort: str = EXECUTOR.DEFAULT_EFFORT,
    app_name: str = EXECUTOR.DEFAULT_APP_NAME,
) -> dict[str, Any]:
    config = EXECUTOR.make_config(
        project_root=project_root,
        mission_path=mission_path,
        run_root=run_root,
        run_id=run_id,
        source_thread_id=source_thread_id,
        model=model,
        effort=effort,
        app_name=app_name,
    )
    return {"config": config, "manifest": EXECUTOR.manifest_payload(config), "contract": EXECUTOR.public_contract(config)}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute one mission through the lean Oracle + @codex flow.")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--mission-path", type=Path, required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--model", choices=EXECUTOR.SUPPORTED_MODELS, default=EXECUTOR.DEFAULT_MODEL)
    parser.add_argument("--effort", choices=EXECUTOR.SUPPORTED_EFFORTS, default=EXECUTOR.DEFAULT_EFFORT)
    parser.add_argument("--app-name", default=EXECUTOR.DEFAULT_APP_NAME)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        compiled = compile_manifest(
            project_root=args.project_root,
            mission_path=args.mission_path,
            run_root=args.run_root,
            run_id=args.run_id,
            model=args.model,
            effort=args.effort,
            app_name=args.app_name,
        )
        run = EXECUTOR.execute_config(compiled.pop("config"), dry_run=args.dry_run)
        value = {"ok": bool(run.get("ok")), **compiled, "run": run}
    except EXECUTOR.ExecutionError as exc:
        value = exc.envelope()
    except Exception as exc:
        value = ExecutionError("ORACLE_DISPATCH_FAILED", str(exc)).envelope()
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value.get("ok") else 1


ExecutionError = EXECUTOR.ExecutionError


if __name__ == "__main__":
    raise SystemExit(main())
