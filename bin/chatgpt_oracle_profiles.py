from __future__ import annotations

"""Public description for the single ordinary Oracle execution flow.

Task intent belongs in the mission. Historical mode implementations and state
schemas are not exposed here; exact recovery uses the explicit legacy commands
in ``chatgpt_oracle_run.py``.
"""

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


EXECUTOR = _load("chatgpt_oracle_profiles_executor", BIN / "chatgpt_oracle_execute.py")


def build_execution_contract(
    *,
    project_root: str | Path,
    mission_path: str | Path,
    run_root: str | Path | None = None,
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
    return EXECUTOR.public_contract(config)


def _main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Describe the single ordinary Oracle execution profile.")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--mission-path", type=Path, required=True)
    parser.add_argument("--model", choices=EXECUTOR.SUPPORTED_MODELS, default=EXECUTOR.DEFAULT_MODEL)
    parser.add_argument("--effort", choices=EXECUTOR.SUPPORTED_EFFORTS, default=EXECUTOR.DEFAULT_EFFORT)
    parser.add_argument("--app-name", default=EXECUTOR.DEFAULT_APP_NAME)
    try:
        result = {"ok": True, "contract": build_execution_contract(**vars(parser.parse_args(argv)))}
    except EXECUTOR.ExecutionError as exc:
        result = exc.envelope()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(_main())
