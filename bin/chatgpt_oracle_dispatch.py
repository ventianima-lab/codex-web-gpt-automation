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


PROFILES = _load("oracle_dispatch_profiles", BIN / "chatgpt_oracle_profiles.py")
RUNNER = _load("oracle_dispatch_runner", BIN / "chatgpt_oracle_run.py")


def compile_manifest(
    *,
    mode: str,
    project_root: Path,
    mission_path: Path | None,
    output_path: Path,
    reasoning_level: str | None = None,
    attachment_paths: Iterable[Path] | None = None,
    app_name: str | None = None,
) -> dict[str, Any]:
    contract = PROFILES.build_launch_contract(
        mode,
        mission_path=mission_path,
        reasoning_level=reasoning_level,
        attachment_paths=list(attachment_paths or ()),
        app_name=app_name,
    )
    result = {"ok": True, "contract": contract, "oracle_manifest_path": None}
    if not contract["oracle_launch"]:
        return result
    root = project_root.expanduser().resolve(strict=True)
    target = output_path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": RUNNER.STATE.SCHEMA,
        "project_root": str(root),
        "mission_path": contract["mission_path"],
        "mode": "browser",
        "task_kind": contract["task_kind"],
        "transport": {
            "oracle-pro-attachment-only": "pro-attachment-only",
            "oracle-pro-devspace": "pro-devspace",
            "oracle-pro-devspace-readonly": "pro-devspace-readonly",
            "oracle-devspace": "devspace",
        }[contract["route"]],
        "model": contract.get("model") or "gpt-5.6",
        "model_strategy": "select",
        "thinking_time": contract["thinking_time"],
        "research": "deep" if contract["research"] else "off",
        "archive": "auto",
    }
    if contract["route"] == "oracle-pro-attachment-only":
        manifest["attachments"] = contract["attachments"]
    else:
        manifest["app_name"] = contract["app_name"]
        manifest["task_outcome_contract"] = "v1"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["oracle_manifest_path"] = str(target)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a GPT mode and dispatch it to Oracle + DevSpace.")
    parser.add_argument("--mode", required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--mission-path", type=Path)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--reasoning-level")
    parser.add_argument("--attachment", type=Path, action="append", default=[])
    parser.add_argument("--app-name")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        compiled = compile_manifest(
            mode=args.mode,
            project_root=args.project_root,
            mission_path=args.mission_path,
            output_path=args.manifest_output,
            reasoning_level=args.reasoning_level,
            attachment_paths=args.attachment,
            app_name=args.app_name,
        )
        if compiled["oracle_manifest_path"]:
            run = RUNNER.execute_run(Path(compiled["oracle_manifest_path"]), dry_run=args.dry_run)
            value = {**compiled, "run": run, "ok": bool(run.get("ok"))}
        else:
            value = compiled
    except Exception as exc:
        value = {"ok": False, "error": {"code": "ORACLE_DISPATCH_FAILED", "message": str(exc)}}
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
