"""Resolve an already-installed Oracle only at live/version-probe boundaries.

Manifest parsing, planning, and dry-run code must retain the deterministic
logical pinned argv and must not call this module.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any


COMPAT_PATH = Path(__file__).resolve().with_name("chatgpt_oracle_compat.py")
PACKAGE_NAME = "@steipete/oracle"
ORACLE_ENTRY = "dist/bin/oracle-cli.js"
NODE_PROBE_TIMEOUT_SECONDS = 10
NPX_PROBE_TIMEOUT_SECONDS = 30


class OracleRuntimeResolutionError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.evidence = dict(evidence or {})


def _load_compat_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("chatgpt_oracle_compat_for_runtime", COMPAT_PATH)
    if spec is None or spec.loader is None:
        raise OracleRuntimeResolutionError(
            "ORACLE_COMPAT_UNAVAILABLE",
            "Oracle compatibility resolver is unavailable",
            {"path": str(COMPAT_PATH)},
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


COMPAT = _load_compat_module()
SUPPORTED_VERSION = str(COMPAT.SUPPORTED_VERSION)
PINNED_PACKAGE = f"{PACKAGE_NAME}@{SUPPORTED_VERSION}"


def _windows_subprocess_kwargs(platform_name: str) -> dict[str, Any]:
    if platform_name != "nt" or not hasattr(subprocess, "STARTUPINFO"):
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"creationflags": 0x08000000, "startupinfo": startup}


def _run(
    run_factory: Callable[..., Any],
    command: Sequence[str],
    *,
    timeout: int,
    platform_name: str,
    environment: Mapping[str, str] | None = None,
) -> Any:
    return run_factory(
        list(command),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
        env=dict(environment) if environment is not None else None,
        **_windows_subprocess_kwargs(platform_name),
    )


def _validated_oracle_entry(package_root: Path) -> Path:
    root = Path(package_root).expanduser().resolve()
    metadata_path = root / "package.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OracleRuntimeResolutionError(
            "ORACLE_PACKAGE_METADATA_INVALID",
            "Oracle package metadata is unreadable",
            {"path": str(metadata_path)},
        ) from exc
    if not isinstance(metadata, dict):
        raise OracleRuntimeResolutionError(
            "ORACLE_PACKAGE_METADATA_INVALID",
            "Oracle package metadata must be one object",
            {"path": str(metadata_path)},
        )
    package_bin = metadata.get("bin")
    identity = {
        "name": metadata.get("name"),
        "version": metadata.get("version"),
        "entry": package_bin.get("oracle") if isinstance(package_bin, dict) else None,
    }
    expected = {"name": PACKAGE_NAME, "version": SUPPORTED_VERSION, "entry": ORACLE_ENTRY}
    if identity != expected:
        raise OracleRuntimeResolutionError(
            "ORACLE_PACKAGE_IDENTITY_MISMATCH",
            "Cached Oracle package identity does not match the supported runtime",
            {"actual": identity, "expected": expected},
        )
    try:
        entry = (root / ORACLE_ENTRY).resolve(strict=True)
        entry.relative_to(root)
    except (OSError, ValueError) as exc:
        raise OracleRuntimeResolutionError(
            "ORACLE_ENTRY_INVALID",
            "Oracle executable entry escapes or is absent from its package",
            {"root": str(root), "entry": ORACLE_ENTRY},
        ) from exc
    if not entry.is_file() or entry.suffix.casefold() != ".js" or entry.stat().st_size <= 0:
        raise OracleRuntimeResolutionError(
            "ORACLE_ENTRY_INVALID",
            "Oracle executable entry is not a nonempty JavaScript file",
            {"path": str(entry)},
        )
    try:
        with entry.open("r", encoding="utf-8", errors="strict") as stream:
            first_line = stream.readline(128).strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise OracleRuntimeResolutionError(
            "ORACLE_ENTRY_INVALID",
            "Oracle executable entry is unreadable",
            {"path": str(entry)},
        ) from exc
    if first_line != "#!/usr/bin/env node":
        raise OracleRuntimeResolutionError(
            "ORACLE_ENTRY_INVALID",
            "Oracle executable entry does not declare the Node runtime",
            {"path": str(entry)},
        )
    return entry


def _runtime_version_key(path: Path) -> tuple[int, ...]:
    match = re.search(r"node-v(\d+(?:\.\d+)*)", path.parent.name, flags=re.IGNORECASE)
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def _node_candidates(
    *,
    which: Callable[[str], str | None],
    environment: Mapping[str, str],
    platform_name: str,
) -> list[Path]:
    candidates: list[Path] = []
    path_node = which("node.exe" if platform_name == "nt" else "node") or which("node")
    if path_node:
        candidates.append(Path(path_node))
    if platform_name == "nt":
        local_app_data = str(environment.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            local_root = Path(local_app_data).expanduser()
            runtime_roots = (
                local_root / "Codex" / "Runtimes",
                local_root / "OpenAI" / "Codex" / "Runtimes",
            )
            bundled: list[Path] = []
            for runtime_root in runtime_roots:
                try:
                    bundled.extend(
                        child / "node.exe"
                        for child in runtime_root.iterdir()
                        if child.is_dir() and child.name.casefold().startswith("node-v")
                    )
                except OSError:
                    continue
            candidates.extend(sorted(bundled, key=_runtime_version_key, reverse=True))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).casefold() if platform_name == "nt" else str(resolved)
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _suitable_node(
    candidates: Sequence[Path],
    *,
    run_factory: Callable[..., Any],
    platform_name: str,
) -> Path | None:
    minimum, maximum = COMPAT.CURRENT_NODE_MAJOR_RANGE
    for candidate in candidates:
        if not candidate.is_file() or (platform_name != "nt" and not os.access(candidate, os.X_OK)):
            continue
        try:
            completed = _run(
                run_factory,
                [str(candidate), "--version"],
                timeout=NODE_PROBE_TIMEOUT_SECONDS,
                platform_name=platform_name,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        value = str(getattr(completed, "stdout", "") or "").strip().removeprefix("v")
        try:
            major = int(value.split(".", 1)[0])
        except (TypeError, ValueError):
            major = -1
        if int(getattr(completed, "returncode", 1)) == 0 and minimum <= major < maximum:
            return candidate
    return None


def _npx_candidates(*, which: Callable[[str], str | None], platform_name: str) -> list[Path]:
    names = ("npx.cmd", "npx.exe", "npx") if platform_name == "nt" else ("npx",)
    values: list[Path] = []
    seen: set[str] = set()
    for name in names:
        candidate = which(name)
        if not candidate:
            continue
        try:
            resolved = Path(candidate).expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).casefold() if platform_name == "nt" else str(resolved)
        if key not in seen and resolved.is_file():
            seen.add(key)
            values.append(resolved)
    return values


def _normalized_oracle_version(completed: Any) -> str | None:
    if int(getattr(completed, "returncode", 1)) != 0:
        return None
    output = f"{getattr(completed, 'stdout', '') or ''}\n{getattr(completed, 'stderr', '') or ''}"
    for line in output.splitlines():
        value = line.strip().removeprefix("oracle ").strip()
        if value == SUPPORTED_VERSION:
            return value
    return None


def _resolve_default_oracle_command(
    *,
    package_resolver: Callable[[str], Path],
    which: Callable[[str], str | None],
    environment: Mapping[str, str],
    run_factory: Callable[..., Any],
    platform_name: str,
) -> list[str]:
    try:
        package_root = Path(package_resolver(SUPPORTED_VERSION))
    except Exception as exc:
        raise OracleRuntimeResolutionError(
            "ORACLE_PACKAGE_NOT_FOUND",
            "The exact supported Oracle package is not present in the local npm cache",
            {"package": PINNED_PACKAGE},
        ) from exc
    entry = _validated_oracle_entry(package_root)
    node = _suitable_node(
        _node_candidates(which=which, environment=environment, platform_name=platform_name),
        run_factory=run_factory,
        platform_name=platform_name,
    )
    if node is not None:
        return [str(node), str(entry)]

    offline_environment = dict(environment)
    offline_environment["NPM_CONFIG_OFFLINE"] = "true"
    offline_environment["NO_UPDATE_NOTIFIER"] = "1"
    for npx in _npx_candidates(which=which, platform_name=platform_name):
        command = [str(npx), "--offline", "--yes", PINNED_PACKAGE]
        try:
            completed = _run(
                run_factory,
                [*command, "--version"],
                timeout=NPX_PROBE_TIMEOUT_SECONDS,
                platform_name=platform_name,
                environment=offline_environment,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if _normalized_oracle_version(completed) == SUPPORTED_VERSION:
            return command
    raise OracleRuntimeResolutionError(
        "ORACLE_RUNTIME_UNAVAILABLE",
        "No validated Node runtime or offline exact-pinned npx route can execute Oracle",
        {"package": PINNED_PACKAGE},
    )


def resolve_default_oracle_command() -> list[str]:
    """Resolve the installed Oracle without installation or network fallback.

    This performs host discovery and executable probes, so callers must defer it
    until an actual live launch or doctor/version probe.
    """
    return _resolve_default_oracle_command(
        package_resolver=COMPAT.resolve_package_root,
        which=shutil.which,
        environment=os.environ,
        run_factory=subprocess.run,
        platform_name=os.name,
    )
