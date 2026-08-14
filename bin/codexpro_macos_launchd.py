#!/usr/bin/env python3
"""Install and diagnose user-scoped macOS launchd services for CodexPro."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Sequence


LABELS = {
    "devspace": "com.ventianima.codexpro-automation.devspace",
    "supervisor": "com.ventianima.codexpro-automation.supervisor",
    "funnel": "com.ventianima.codexpro-automation.tailscale-ensure",
}


class LaunchdError(RuntimeError):
    pass


def _write_plist_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            plistlib.dump(value, stream, fmt=plistlib.FMT_XML, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def service_plists(
    *,
    codex_home: Path,
    project_root: Path,
    python: str,
    npx: str,
) -> dict[str, dict[str, Any]]:
    logs = codex_home / "logs" / "codexpro-automation"
    state = codex_home / "state" / "codexpro-harness"
    common: dict[str, Any] = {"ProcessType": "Background", "CodexProManaged": True}
    return {
        "devspace": {
            **common,
            "Label": LABELS["devspace"],
            "ProgramArguments": [npx, "--yes", "@waishnav/devspace@1.0.4", "serve"],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 15,
            "EnvironmentVariables": {
                "DEVSPACE_TOOL_MODE": "full",
                "DEVSPACE_OAUTH_SCOPES": "devspace,offline_access",
                "PATH": str(Path(npx).parent) + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            },
            "WorkingDirectory": str(project_root),
            "StandardOutPath": str(logs / "devspace.out.log"),
            "StandardErrorPath": str(logs / "devspace.err.log"),
        },
        "supervisor": {
            **common,
            "Label": LABELS["supervisor"],
            "ProgramArguments": [python, str(codex_home / "bin" / "codexpro_harness.py"), "--state-root", str(state), "supervise", "--execute-resume"],
            "RunAtLoad": True,
            "StartInterval": 60,
            "WorkingDirectory": str(project_root),
            "StandardOutPath": str(logs / "supervisor.out.log"),
            "StandardErrorPath": str(logs / "supervisor.err.log"),
        },
        "funnel": {
            **common,
            "Label": LABELS["funnel"],
            "ProgramArguments": [
                python,
                str(codex_home / "skills" / "chatgpt-workspace-setup" / "scripts" / "devspace_tailscale_setup.py"),
                "ensure", "--root", str(project_root), "--public-port", "443",
            ],
            "EnvironmentVariables": {
                "PATH": "/opt/homebrew/bin:/usr/local/bin:" + str(Path.home() / ".local" / "bin") + ":/usr/bin:/bin",
            },
            "RunAtLoad": True,
            "StartInterval": 300,
            "WorkingDirectory": str(project_root),
            "StandardOutPath": str(logs / "tailscale.out.log"),
            "StandardErrorPath": str(logs / "tailscale.err.log"),
        },
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *argv], capture_output=True, text=True, check=False)


def install_services(
    *,
    codex_home: Path,
    project_root: Path,
    launch_agents: Path,
    load: bool,
) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise LaunchdError("MACOS_REQUIRED")
    codex_home = codex_home.expanduser().resolve()
    project_root = project_root.expanduser().resolve()
    if not project_root.is_dir():
        raise LaunchdError("PROJECT_ROOT_INVALID")
    python = shutil.which("python3")
    npx = shutil.which("npx")
    if not python or not npx:
        raise LaunchdError("PYTHON_AND_NPX_REQUIRED")
    (codex_home / "logs" / "codexpro-automation").mkdir(parents=True, exist_ok=True)
    values = service_plists(codex_home=codex_home, project_root=project_root, python=python, npx=npx)
    installed: list[str] = []
    for name, value in values.items():
        path = launch_agents / f"{LABELS[name]}.plist"
        if path.exists():
            try:
                prior = plistlib.loads(path.read_bytes())
            except (OSError, plistlib.InvalidFileException) as exc:
                raise LaunchdError(f"UNREADABLE_EXISTING_PLIST: {path}") from exc
            if not prior.get("CodexProManaged"):
                raise LaunchdError(f"UNMANAGED_LABEL_CONFLICT: {LABELS[name]}")
        _write_plist_atomic(path, value)
        check = subprocess.run(["plutil", "-lint", str(path)], capture_output=True, text=True, check=False)
        if check.returncode != 0:
            raise LaunchdError(f"PLIST_INVALID: {path}: {check.stderr or check.stdout}")
        installed.append(str(path))
        if load:
            _launchctl("bootout", _domain(), str(path))
            result = _launchctl("bootstrap", _domain(), str(path))
            if result.returncode != 0:
                raise LaunchdError(f"LAUNCHCTL_BOOTSTRAP_FAILED: {LABELS[name]}: {result.stderr.strip()}")
    return {"ok": True, "installed": installed, "loaded": load, "labels": list(LABELS.values())}


def doctor_services(*, launch_agents: Path) -> dict[str, Any]:
    services: list[dict[str, Any]] = []
    for name, label in LABELS.items():
        path = launch_agents / f"{label}.plist"
        item: dict[str, Any] = {"name": name, "label": label, "path": str(path), "installed": path.is_file()}
        if path.is_file():
            try:
                value = plistlib.loads(path.read_bytes())
                item["managed"] = value.get("CodexProManaged") is True and value.get("Label") == label
            except (OSError, plistlib.InvalidFileException):
                item["managed"] = False
            current = _launchctl("print", f"{_domain()}/{label}")
            item["loaded"] = current.returncode == 0
        services.append(item)
    return {"ok": all(item.get("installed") and item.get("managed") for item in services), "services": services}


def remove_services(*, launch_agents: Path) -> dict[str, Any]:
    removed: list[str] = []
    conflicts: list[str] = []
    for label in LABELS.values():
        path = launch_agents / f"{label}.plist"
        if not path.exists():
            continue
        try:
            value = plistlib.loads(path.read_bytes())
        except (OSError, plistlib.InvalidFileException):
            conflicts.append(str(path))
            continue
        if value.get("CodexProManaged") is not True or value.get("Label") != label:
            conflicts.append(str(path))
            continue
        _launchctl("bootout", _domain(), str(path))
        path.unlink()
        removed.append(str(path))
    return {"ok": not conflicts, "removed": removed, "conflicts": conflicts}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--launch-agents", type=Path, default=Path.home() / "Library" / "LaunchAgents")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("--project-root", type=Path, required=True)
    install.add_argument("--load", action="store_true")
    commands.add_parser("doctor")
    commands.add_parser("uninstall")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "install":
            result = install_services(codex_home=args.codex_home, project_root=args.project_root, launch_agents=args.launch_agents.expanduser().resolve(), load=args.load)
        elif args.command == "doctor":
            result = doctor_services(launch_agents=args.launch_agents.expanduser().resolve())
        else:
            result = remove_services(launch_agents=args.launch_agents.expanduser().resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    except LaunchdError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
