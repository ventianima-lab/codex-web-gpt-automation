#!/usr/bin/env python3
"""Portable, receipt-backed lifecycle for Codex Web GPT Automation.

The codexpro-* module, receipt, and schema identifiers are stable compatibility
IDs retained for exact rollback and recovery of older installations.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import locale as locale_module
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


RECEIPT_SCHEMA = "codexpro.install-receipt/v3"
WAL_SCHEMA = "codexpro.install-wal/v1"
# A journal in any of these states is already settled.  The PowerShell installer
# skipped ROLLED_BACK_AFTER_ERROR while this path did not, so a journal written
# by one implementation could be replayed as an interrupted install by the other
# and fail closed with INSTALL_CRASH_RECOVERY_CONFLICT.
WAL_TERMINAL_STATUSES = frozenset({
    "COMPLETE",
    "ROLLED_BACK_AFTER_CRASH",
    "ROLLED_BACK_AFTER_ERROR",
    "ROLLED_BACK_AFTER_FAILURE",
})
SUPPORTED_ROOTS = {
    "bin",
    "skills",
    "mcp_servers",
    "scripts",
    "contracts",
    "docs",
    "tests",
    "plugins",
    "marketplace",
}
ROOT_FILE_ALLOWLIST = frozenset(
    {"upstream-runtime-policy.json", "upstream-runtime-maintainer-automation.json"}
)
ORACLE_SUPPORTED_VERSION = "0.18.0"
ORACLE_VERSION_PROBE_TIMEOUT_SECONDS = 30


class LifecycleError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def _copy_file_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        shutil.copymode(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return candidate != root
    except ValueError:
        return False


def safe_child(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute() or any(part in {"", ".", ".."} for part in Path(relative).parts):
        raise LifecycleError(f"unsafe relative path: {relative}")
    root = root.expanduser().resolve()
    candidate = (root / relative).resolve(strict=False)
    if not _is_within(root, candidate):
        raise LifecycleError(f"path escapes root: {relative}")
    cursor = candidate.parent
    while cursor != root:
        if cursor.exists() and cursor.is_symlink():
            raise LifecycleError(f"symlink path refused: {cursor}")
        cursor = cursor.parent
    if candidate.exists() and candidate.is_symlink():
        raise LifecycleError(f"symlink destination refused: {candidate}")
    return candidate


def manifest_files(repo_root: Path, *, include_local_multi_gpt: bool = False) -> list[str]:
    manifest = json.loads((repo_root / "install-manifest.json").read_text(encoding="utf-8"))
    result: set[str] = set()
    patterns = list(manifest.get("include", []))
    if include_local_multi_gpt:
        patterns.extend(manifest.get("optional_components", {}).get("local_multi_gpt", {}).get("include", []))
    for pattern in patterns:
        path = Path(str(pattern))
        if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
            raise LifecycleError(f"unsafe manifest pattern: {pattern}")
        if not path.parts or (path.parts[0] not in SUPPORTED_ROOTS and str(path) not in ROOT_FILE_ALLOWLIST):
            raise LifecycleError(f"unsupported manifest root: {pattern}")
        matches = [item for item in repo_root.glob(str(pattern)) if item.is_file()]
        if not matches:
            raise LifecycleError(f"manifest pattern matched no files: {pattern}")
        for item in matches:
            if item.is_symlink():
                raise LifecycleError(f"manifest refuses symlink: {item}")
            result.add(item.relative_to(repo_root).as_posix())
    return sorted(result)


def manifest_retirements(repo_root: Path) -> list[str]:
    manifest = json.loads((repo_root / "install-manifest.json").read_text(encoding="utf-8"))
    retire = manifest.get("retire") or {}
    if not isinstance(retire, dict):
        raise LifecycleError("manifest retire must be an object")
    paths = retire.get("receipt_owned_files") or []
    if not isinstance(paths, list):
        raise LifecycleError("manifest retire.receipt_owned_files must be an array")
    result: set[str] = set()
    for value in paths:
        if not isinstance(value, str) or not value or "\\" in value:
            raise LifecycleError(f"invalid retirement path: {value}")
        path = Path(value)
        if (
            path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(character in value for character in "*?[]")
            or not path.parts
            or path.parts[0] not in SUPPORTED_ROOTS
        ):
            raise LifecycleError(f"unsafe retirement path: {value}")
        result.add(path.as_posix())
    return sorted(result)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise LifecycleError(f"JSON object required: {path}")
    return value


def _active_wals(codex_home: Path) -> Iterable[Path]:
    backup_root = codex_home / "backups"
    if not backup_root.is_dir():
        return ()
    return sorted(backup_root.glob("**/install.wal.json"))


def _receipt_owned_hashes(codex_home: Path, wanted: set[str]) -> dict[str, dict[str, str]]:
    owned: dict[str, dict[str, str]] = {relative: {} for relative in wanted}
    receipt_root = codex_home / "receipts"
    if not receipt_root.is_dir():
        return owned
    for receipt_path in sorted(receipt_root.glob("codexpro-automation-*.json"), reverse=True):
        if receipt_path.is_symlink():
            continue
        try:
            receipt = _read_json(receipt_path)
        except LifecycleError:
            continue
        if receipt.get("schema") not in {"codexpro.install-receipt/v2", RECEIPT_SCHEMA}:
            continue
        backup_text = str(receipt.get("backup") or "")
        try:
            backup_root = Path(backup_text).expanduser().resolve()
        except OSError:
            continue
        if not _is_within((codex_home / "backups").resolve(), backup_root):
            continue
        for record in receipt.get("files") or []:
            if not isinstance(record, dict) or record.get("action") not in {"created", "overwritten"}:
                continue
            relative = str(record.get("path") or "")
            digest = str(record.get("installed_sha256") or "").lower()
            if relative in owned and len(digest) == 64 and all(character in "0123456789abcdef" for character in digest):
                owned[relative].setdefault(digest, str(receipt_path))
    return owned


def plan_retirements(codex_home: Path, paths: Sequence[str]) -> list[dict[str, str]]:
    wanted = set(paths)
    owned = _receipt_owned_hashes(codex_home, wanted)
    planned: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for relative in sorted(wanted):
        destination = safe_child(codex_home, relative)
        if not destination.exists():
            continue
        if not destination.is_file():
            conflicts.append({"path": relative, "action": "preserved_non_file_retirement_target"})
            continue
        actual = sha256_file(destination)
        source_receipt = owned[relative].get(actual)
        if source_receipt is None:
            conflicts.append({"path": relative, "action": "preserved_unowned_or_modified_retirement_target"})
            continue
        planned.append({"path": relative, "retired_sha256": actual, "source_receipt": source_receipt})
    if conflicts:
        raise LifecycleError("RETIREMENT_CONFLICT: " + json.dumps(conflicts, separators=(",", ":")))
    return planned


def recover_pending_installs(codex_home: Path) -> list[str]:
    recovered: list[str] = []
    for wal_path in _active_wals(codex_home):
        wal = _read_json(wal_path)
        if wal.get("schema") != WAL_SCHEMA or wal.get("status") in WAL_TERMINAL_STATUSES:
            continue
        backup = Path(str(wal.get("backup") or "")).expanduser().resolve()
        if not _is_within((codex_home / "backups").resolve(), backup):
            raise LifecycleError("interrupted install backup escapes CODEX_HOME")
        conflicts: list[str] = []
        for entry in wal.get("files") or []:
            if entry.get("action") != "retired":
                continue
            relative = str(entry.get("path") or "")
            destination = safe_child(codex_home, relative)
            retired_hash = str(entry.get("retired_sha256") or entry.get("installed_sha256") or "")
            if destination.exists():
                if not destination.is_file() or sha256_file(destination) != retired_hash:
                    conflicts.append(relative)
                continue
            source = safe_child(backup, relative)
            if not source.is_file() or sha256_file(source) != entry.get("backup_sha256"):
                conflicts.append(relative)
        if conflicts:
            raise LifecycleError(f"INSTALL_CRASH_RECOVERY_CONFLICT: {','.join(conflicts)}")
        for entry in reversed(list(wal.get("files") or [])):
            relative = str(entry.get("path") or "")
            destination = safe_child(codex_home, relative)
            if entry.get("action") == "retired":
                retired_hash = str(entry.get("retired_sha256") or entry.get("installed_sha256") or "")
                if destination.exists():
                    if destination.is_file() and sha256_file(destination) == retired_hash:
                        continue
                    conflicts.append(relative)
                    continue
                source = safe_child(backup, relative)
                if not source.is_file() or sha256_file(source) != entry.get("backup_sha256"):
                    conflicts.append(relative)
                    continue
                _copy_file_atomic(source, destination)
                continue
            installed_hash = str(entry.get("installed_sha256") or "")
            if not destination.exists():
                continue
            actual = sha256_file(destination)
            if entry.get("phase") == "INTENT" and actual != installed_hash:
                continue
            if actual != installed_hash:
                conflicts.append(relative)
                continue
            if entry.get("action") == "created":
                destination.unlink()
            else:
                source = safe_child(backup, relative)
                if not source.is_file() or sha256_file(source) != entry.get("backup_sha256"):
                    conflicts.append(relative)
                    continue
                _copy_file_atomic(source, destination)
        if conflicts:
            raise LifecycleError(f"INSTALL_CRASH_RECOVERY_CONFLICT: {','.join(conflicts)}")
        wal["status"] = "ROLLED_BACK_AFTER_CRASH"
        wal["recovered_at"] = utc_now()
        _write_json_atomic(wal_path, wal)
        recovered.append(str(wal_path))
    return recovered


def install(repo_root: Path, codex_home: Path, *, dry_run: bool = False, local_multi_gpt: bool = False) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    codex_home = codex_home.expanduser().resolve()
    files = manifest_files(repo_root, include_local_multi_gpt=local_multi_gpt)
    retirement_paths = manifest_retirements(repo_root)
    overlap = sorted(set(files) & set(retirement_paths))
    if overlap:
        raise LifecycleError("manifest installs and retires the same path: " + ",".join(overlap))
    if dry_run:
        retirements = plan_retirements(codex_home, retirement_paths)
        return {"ok": True, "action": "install-plan", "codex_home": str(codex_home), "files": files, "retirements": retirements}
    codex_home.mkdir(parents=True, exist_ok=True)
    recovered = recover_pending_installs(codex_home)
    retirements = plan_retirements(codex_home, retirement_paths)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S%f")
    nonce = uuid.uuid4().hex
    backup_root = codex_home / "backups" / f"codexpro-automation-{stamp}-{nonce}"
    receipt_path = codex_home / "receipts" / f"codexpro-automation-{stamp}-{nonce}.json"
    wal_path = backup_root / "install.wal.json"
    wal: dict[str, Any] = {
        "schema": WAL_SCHEMA,
        "status": "ACTIVE",
        "backup": str(backup_root),
        "created_at": utc_now(),
        "files": [],
    }
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="codexpro-stage-") as stage_text:
        stage_root = Path(stage_text)
        for relative in files:
            source = safe_child(repo_root, relative)
            staged = safe_child(stage_root, relative)
            _copy_file_atomic(source, staged)
            if sha256_file(source) != sha256_file(staged):
                raise LifecycleError(f"staging hash verification failed: {relative}")
        _write_json_atomic(wal_path, wal)
        try:
            for index, relative in enumerate(files):
                destination = safe_child(codex_home, relative)
                staged = safe_child(stage_root, relative)
                action = "created"
                backup_hash: str | None = None
                if destination.exists():
                    action = "overwritten"
                    backup = safe_child(backup_root, relative)
                    _copy_file_atomic(destination, backup)
                    backup_hash = sha256_file(backup)
                installed_hash = sha256_file(staged)
                replacement_path = backup_root / "steps" / str(index) / "replacement.json"
                entry: dict[str, Any] = {
                    "path": relative,
                    "action": action,
                    "installed_sha256": installed_hash,
                    "backup_sha256": backup_hash,
                    "phase": "INTENT",
                    "transitions": ["INTENT"],
                    "replacement": str(replacement_path),
                }
                wal["files"].append(entry)
                _write_json_atomic(wal_path, wal)
                _copy_file_atomic(staged, destination)
                entry["phase"] = "MUTATED"
                entry["transitions"].append("MUTATED")
                _write_json_atomic(wal_path, wal)
                _write_json_atomic(replacement_path, {
                    "schema": "codexpro.install-replacement/v1",
                    "path": relative,
                    "action": action,
                    "installed_sha256": installed_hash,
                    "backup_sha256": backup_hash,
                    "mutated_at": utc_now(),
                })
                if sha256_file(destination) != installed_hash:
                    raise LifecycleError(f"commit hash verification failed: {relative}")
                entry["phase"] = "VERIFIED"
                entry["transitions"].append("VERIFIED")
                _write_json_atomic(wal_path, wal)
                entry["phase"] = "COMPLETE"
                entry["transitions"].append("COMPLETE")
                _write_json_atomic(wal_path, wal)
                records.append({key: entry[key] for key in ("path", "action", "installed_sha256", "backup_sha256")})
            for retirement in retirements:
                relative = retirement["path"]
                destination = safe_child(codex_home, relative)
                retired_hash = retirement["retired_sha256"]
                if not destination.is_file() or sha256_file(destination) != retired_hash:
                    raise LifecycleError(f"RETIREMENT_CONFLICT: changed during install: {relative}")
                backup = safe_child(backup_root, relative)
                _copy_file_atomic(destination, backup)
                backup_hash = sha256_file(backup)
                replacement_path = backup_root / "steps" / str(len(wal["files"])) / "replacement.json"
                entry = {
                    "path": relative,
                    "action": "retired",
                    "installed_sha256": retired_hash,
                    "retired_sha256": retired_hash,
                    "backup_sha256": backup_hash,
                    "source_receipt": retirement["source_receipt"],
                    "phase": "INTENT",
                    "transitions": ["INTENT"],
                    "replacement": str(replacement_path),
                }
                wal["files"].append(entry)
                _write_json_atomic(wal_path, wal)
                receipt_record = {
                    key: entry[key]
                    for key in ("path", "action", "installed_sha256", "retired_sha256", "backup_sha256", "source_receipt")
                }
                records.append(receipt_record)
                destination.unlink()
                entry["phase"] = "MUTATED"
                entry["transitions"].append("MUTATED")
                _write_json_atomic(wal_path, wal)
                _write_json_atomic(replacement_path, {
                    "schema": "codexpro.install-replacement/v1",
                    **receipt_record,
                    "mutated_at": utc_now(),
                })
                if destination.exists():
                    raise LifecycleError(f"retirement verification failed: {relative}")
                entry["phase"] = "VERIFIED"
                entry["transitions"].append("VERIFIED")
                _write_json_atomic(wal_path, wal)
                entry["phase"] = "COMPLETE"
                entry["transitions"].append("COMPLETE")
                _write_json_atomic(wal_path, wal)
        except Exception:
            _rollback_records(codex_home, backup_root, records)
            raise
    wal["status"] = "COMPLETE"
    wal["completed_at"] = utc_now()
    _write_json_atomic(wal_path, wal)
    registration_receipt: Path | None = None
    try:
        local_multi_result: dict[str, Any] = {"enabled": local_multi_gpt, "mode": "skipped", "reason": "not-selected"}
        if local_multi_gpt:
            completed = subprocess.run(
                [sys.executable, str(repo_root / "bin" / "codex_local_multi_gpt_setup.py"), "enable", "--codex-home", str(codex_home)],
                text=True, encoding="utf-8", errors="replace", capture_output=True, check=False, timeout=60,
            )
            if completed.returncode != 0:
                raise LifecycleError("Local Multi-GPT MCP registration failed: " + (completed.stderr.strip() or completed.stdout.strip()))
            try:
                setup = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise LifecycleError("Local Multi-GPT MCP setup produced invalid output") from exc
            registration_receipt = Path(setup["receipt"]) if setup.get("receipt") else None
            local_multi_result = {
                "enabled": True,
                "mode": "registered" if setup.get("changed") else "preserved",
                "reason": None,
                "receipt": setup.get("receipt"),
                "cli": setup.get("cli"),
                "cli_version": setup.get("cli_version"),
            }
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "installed_at": utc_now(),
            "manifest_version": json.loads((repo_root / "install-manifest.json").read_text(encoding="utf-8"))["version"],
            "backup": str(backup_root),
            "files": records,
            "dependency": {"mode": "skipped", "reason": "legacy-recovery-dependencies-frozen"},
            "optional_components": {"local_multi_gpt": local_multi_result},
            "wal": str(wal_path),
            "installer": "python-portable",
        }
        _write_json_atomic(receipt_path, receipt)
    except Exception:
        if registration_receipt:
            subprocess.run(
                [sys.executable, str(repo_root / "bin" / "codex_local_multi_gpt_setup.py"), "rollback", "--codex-home", str(codex_home), "--receipt", str(registration_receipt)],
                text=True, encoding="utf-8", errors="replace", capture_output=True, check=False, timeout=60,
            )
        _rollback_records(codex_home, backup_root, records)
        wal["status"] = "ROLLED_BACK_AFTER_FAILURE"
        _write_json_atomic(wal_path, wal)
        raise
    return {"ok": True, "action": "installed", "count": len(records), "retired": [item["path"] for item in retirements], "receipt": str(receipt_path), "recovered": recovered}


def _rollback_records(codex_home: Path, backup_root: Path, records: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    records = list(records)
    conflicts: list[dict[str, str]] = []
    for record in records:
        if record.get("action") != "retired":
            continue
        relative = str(record["path"])
        destination = safe_child(codex_home, relative)
        retired_hash = str(record.get("retired_sha256") or record.get("installed_sha256") or "")
        if destination.exists():
            if not destination.is_file() or sha256_file(destination) != retired_hash:
                conflicts.append({"path": relative, "action": "preserved_recreated_retired_path"})
            continue
        backup = safe_child(backup_root, relative)
        if not backup.is_file() or sha256_file(backup) != record.get("backup_sha256"):
            conflicts.append({"path": relative, "action": "missing_retirement_backup"})
    if conflicts:
        return conflicts
    for record in reversed(records):
        relative = str(record["path"])
        destination = safe_child(codex_home, relative)
        if record.get("action") == "retired":
            retired_hash = str(record.get("retired_sha256") or record.get("installed_sha256") or "")
            if destination.exists():
                if destination.is_file() and sha256_file(destination) == retired_hash:
                    continue
                conflicts.append({"path": relative, "action": "preserved_recreated_retired_path"})
                continue
            backup = safe_child(backup_root, relative)
            if not backup.is_file() or sha256_file(backup) != record.get("backup_sha256"):
                conflicts.append({"path": relative, "action": "missing_retirement_backup"})
                continue
            _copy_file_atomic(backup, destination)
            continue
        if not destination.exists() or sha256_file(destination) != record.get("installed_sha256"):
            conflicts.append({"path": relative, "action": "preserved_modified_or_missing"})
            continue
        if record.get("action") == "created":
            destination.unlink()
            continue
        backup = safe_child(backup_root, relative)
        if not backup.is_file() or sha256_file(backup) != record.get("backup_sha256"):
            conflicts.append({"path": relative, "action": "missing_backup"})
            continue
        _copy_file_atomic(backup, destination)
    return conflicts


def latest_receipt(codex_home: Path) -> Path:
    receipts = sorted((codex_home / "receipts").glob("codexpro-automation-*.json"), key=lambda path: path.stat().st_mtime_ns)
    if not receipts:
        raise LifecycleError("RECEIPT_MISSING")
    return receipts[-1]


def rollback(codex_home: Path, receipt_path: Path | None = None) -> dict[str, Any]:
    codex_home = codex_home.expanduser().resolve()
    receipt_path = (receipt_path or latest_receipt(codex_home)).expanduser().resolve()
    receipt_root = (codex_home / "receipts").resolve()
    if not _is_within(receipt_root, receipt_path):
        raise LifecycleError("receipt must be owned by this CODEX_HOME")
    receipt = _read_json(receipt_path)
    if receipt.get("schema") not in {"codexpro.install-receipt/v2", RECEIPT_SCHEMA}:
        raise LifecycleError("unsupported install receipt schema")
    backup_root = Path(str(receipt.get("backup") or "")).expanduser().resolve()
    if not _is_within((codex_home / "backups").resolve(), backup_root):
        raise LifecycleError("receipt backup must be owned by this CODEX_HOME")
    local_receipt_text = receipt.get("optional_components", {}).get("local_multi_gpt", {}).get("receipt")
    local_receipt = Path(str(local_receipt_text)).expanduser().resolve() if local_receipt_text else None
    helper = Path(__file__).with_name("codex_local_multi_gpt_setup.py")
    if local_receipt:
        preflight = subprocess.run(
            [sys.executable, str(helper), "rollback", "--dry-run", "--codex-home", str(codex_home), "--receipt", str(local_receipt)],
            text=True, encoding="utf-8", errors="replace", capture_output=True, check=False, timeout=60,
        )
        if preflight.returncode != 0:
            return {"ok": False, "status": "CONFLICT", "receipt": str(receipt_path), "conflicts": [{"path": "config.toml", "action": "local_multi_gpt_registration_preflight_incomplete", "detail": preflight.stderr.strip()}]}
    conflicts = _rollback_records(codex_home, backup_root, receipt.get("files") or [])
    if not conflicts and local_receipt:
        completed = subprocess.run(
            [sys.executable, str(helper), "rollback", "--codex-home", str(codex_home), "--receipt", str(local_receipt)],
            text=True, encoding="utf-8", errors="replace", capture_output=True, check=False, timeout=60,
        )
        if completed.returncode != 0:
            conflicts.append({"path": "config.toml", "action": "local_multi_gpt_registration_rollback_incomplete", "detail": completed.stderr.strip()})
    status = "CONFLICT" if conflicts else "COMPLETE"
    return {"ok": not conflicts, "status": status, "receipt": str(receipt_path), "conflicts": conflicts}


def _resolve_python_tool(*, platform_name: str = os.name, active_executable: str = sys.executable) -> str | None:
    python_tool = shutil.which("python3")
    if python_tool is None and platform_name == "nt":
        python_tool = shutil.which("python") or shutil.which("py")
        if python_tool is None and active_executable and Path(active_executable).is_file():
            python_tool = str(Path(active_executable).resolve())
    return python_tool


def _load_oracle_runtime_module(codex_home: Path) -> Any:
    helper = codex_home / "bin" / "chatgpt_oracle_runtime.py"
    if not helper.is_file():
        raise LifecycleError(f"Oracle runtime resolver missing: {helper}")
    spec = importlib.util.spec_from_file_location(
        f"chatgpt_oracle_runtime_doctor_{hashlib.sha256(str(helper).encode('utf-8')).hexdigest()[:12]}",
        helper,
    )
    if spec is None or spec.loader is None:
        raise LifecycleError(f"Oracle runtime resolver unavailable: {helper}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _oracle_version_from_output(stdout: str | None, stderr: str | None) -> str | None:
    for line in f"{stdout or ''}\n{stderr or ''}".splitlines():
        value = line.strip().removeprefix("oracle ").strip()
        if value:
            return value
    return None


def _probe_oracle_runtime(
    command: Sequence[str],
    *,
    run_factory: Callable[..., Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    evidence: dict[str, Any] = {
        "command": list(command),
        "version": None,
        "exit_code": None,
        "timeout_seconds": ORACLE_VERSION_PROBE_TIMEOUT_SECONDS,
    }
    try:
        completed = run_factory(
            [*command, "--version"],
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=ORACLE_VERSION_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return evidence, {
            "code": "ORACLE_VERSION_TIMEOUT",
            "timeout_seconds": ORACLE_VERSION_PROBE_TIMEOUT_SECONDS,
        }
    except OSError as exc:
        return evidence, {"code": "ORACLE_EXECUTION_FAILED", "detail": str(exc)}
    evidence["exit_code"] = int(completed.returncode)
    evidence["version"] = _oracle_version_from_output(completed.stdout, completed.stderr)
    if completed.returncode != 0:
        return evidence, {"code": "ORACLE_VERSION_FAILED", "exit_code": int(completed.returncode)}
    if evidence["version"] != ORACLE_SUPPORTED_VERSION:
        return evidence, {
            "code": "ORACLE_VERSION_MISMATCH",
            "actual": evidence["version"],
            "expected": ORACLE_SUPPORTED_VERSION,
        }
    return evidence, None


def doctor(
    codex_home: Path,
    *,
    oracle_resolver: Callable[[], list[str]] | None = None,
    oracle_run_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    codex_home = codex_home.expanduser().resolve()
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    receipt_path: Path | None = None
    local_multi_gpt: dict[str, Any] = {"enabled": False, "doctor": None}
    devspace_native_runtime: dict[str, Any] | None = None
    oracle_runtime: dict[str, Any] = {
        "command": None,
        "version": None,
        "exit_code": None,
        "timeout_seconds": ORACLE_VERSION_PROBE_TIMEOUT_SECONDS,
    }
    try:
        receipt_path = latest_receipt(codex_home)
        receipt = _read_json(receipt_path)
        local_multi_gpt["enabled"] = bool(receipt.get("optional_components", {}).get("local_multi_gpt", {}).get("enabled"))
        for record in receipt.get("files") or []:
            path = safe_child(codex_home, str(record.get("path") or ""))
            if record.get("action") == "retired":
                if path.exists() or path.is_symlink():
                    issues.append({"code": "RETIRED_FILE_REAPPEARED", "path": str(record.get("path"))})
                continue
            if not path.is_file():
                issues.append({"code": "FILE_MISSING", "path": str(record.get("path"))})
            elif sha256_file(path) != record.get("installed_sha256"):
                issues.append({"code": "HASH_MISMATCH", "path": str(record.get("path"))})
    except LifecycleError as exc:
        issues.append({"code": "RECEIPT_INVALID", "detail": str(exc)})
    if local_multi_gpt["enabled"]:
        helper = codex_home / "bin" / "codex_local_multi_gpt_setup.py"
        completed = subprocess.run(
            [sys.executable, str(helper), "doctor", "--codex-home", str(codex_home)],
            text=True, encoding="utf-8", errors="replace", capture_output=True, check=False, timeout=60,
        ) if helper.is_file() else None
        try:
            local_multi_gpt["doctor"] = json.loads(completed.stdout) if completed else None
        except json.JSONDecodeError:
            local_multi_gpt["doctor"] = None
        if completed is None or completed.returncode != 0 or not local_multi_gpt["doctor"] or not local_multi_gpt["doctor"].get("ok"):
            issues.append({"code": "LOCAL_MULTI_GPT_MCP_INVALID", "detail": completed.stderr.strip() if completed else "helper missing"})
    required = {"python3": _resolve_python_tool(), "node": shutil.which("node"), "npx": shutil.which("npx")}
    if required["python3"] is None:
        issues.append({"code": "TOOL_MISSING", "tool": "python3"})
    try:
        if oracle_resolver is None:
            oracle_resolver = _load_oracle_runtime_module(codex_home).resolve_default_oracle_command
        command = oracle_resolver()
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(value, str) and value for value in command)
            or not Path(command[0]).is_absolute()
        ):
            raise LifecycleError("Oracle runtime resolver returned an invalid command")
        oracle_runtime, oracle_issue = _probe_oracle_runtime(
            command,
            run_factory=oracle_run_factory or subprocess.run,
        )
        if oracle_issue is not None:
            issues.append(oracle_issue)
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "ORACLE_RUNTIME_UNRESOLVED")
        issues.append({"code": code, "detail": str(exc)})
    compat_helper = codex_home / "bin" / "chatgpt_devspace_compat.py"
    if compat_helper.is_file() and required.get("node"):
        native = subprocess.run(
            [
                sys.executable,
                str(compat_helper),
                "--check-native-runtime",
                "--allow-package-absent",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=45,
        )
        try:
            devspace_native_runtime = json.loads(native.stdout)
        except json.JSONDecodeError:
            devspace_native_runtime = None
        if native.returncode != 0 or not isinstance(devspace_native_runtime, dict) or not devspace_native_runtime.get("ok"):
            error = (
                devspace_native_runtime.get("error")
                if isinstance(devspace_native_runtime, dict)
                else None
            )
            issues.append(
                {
                    "code": "DEVSPACE_NATIVE_RUNTIME_INVALID",
                    "detail": error or (native.stderr or "invalid native-runtime probe").strip()[-1200:],
                }
            )
    if os.name != "nt" and shutil.which("rsync") is None:
        issues.append({"code": "ORACLE_PROFILE_COPY_RSYNC_MISSING"})
    if sys.platform == "darwin":
        for tool in ("launchctl", "plutil", "lsof", "ps"):
            if shutil.which(tool) is None:
                issues.append({"code": "MACOS_TOOL_MISSING", "tool": tool})
        if shutil.which("tailscale") is None:
            warnings.append({"code": "TAILSCALE_NOT_INSTALLED", "next_action": "install tailscale-app and sign in"})
    if shutil.which("agbrowse") is None:
        warnings.append({"code": "LEGACY_AGBROWSE_MISSING", "detail": "legacy recovery only"})
    return {
        "schema": "codexpro.doctor/v3",
        "status": "FAIL" if issues else "PASS",
        "platform": sys.platform,
        "codex_home": str(codex_home),
        "receipt": str(receipt_path) if receipt_path else None,
        "issues": issues,
        "warnings": warnings,
        "tools": required,
        "oracle_runtime": oracle_runtime,
        "local_multi_gpt": local_multi_gpt,
        "devspace_native_runtime": devspace_native_runtime,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    def common(value: argparse.ArgumentParser) -> None:
        value.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
        value.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    for name in ("install", "update"):
        value = commands.add_parser(name)
        common(value)
        value.add_argument("--dry-run", action="store_true")
        choice = value.add_mutually_exclusive_group()
        choice.add_argument("--enable-local-multi-gpt", action="store_true")
        choice.add_argument("--disable-local-multi-gpt", action="store_true")
    value = commands.add_parser("doctor")
    common(value)
    for name in ("rollback", "uninstall"):
        value = commands.add_parser(name)
        common(value)
        value.add_argument("--receipt", type=Path)
    return parser


def localized_optional_prompt(optional: Mapping[str, Any], environment: Mapping[str, str] | None = None) -> str:
    values = environment if environment is not None else os.environ
    explicit_language = str(values.get("CODEX_ONBOARDING_LANG") or "").strip()
    system_locale = ""
    if environment is None and not explicit_language:
        try:
            system_locale = str(locale_module.getlocale()[0] or "")
        except (ValueError, TypeError):
            system_locale = ""
    locale = (
        explicit_language
        if explicit_language
        else " ".join([
            system_locale,
            *(str(values.get(name) or "") for name in ("LC_ALL", "LC_MESSAGES", "LANG")),
        ])
    ).casefold()
    prompt_key = "prompt_ko" if "ko" in locale or "korean" in locale else "prompt_en"
    return str(optional.get(prompt_key) or optional.get("prompt") or "Install optional Local Multi-GPT too? [y/N]")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"install", "update"}:
            requested = True if args.enable_local_multi_gpt else False if args.disable_local_multi_gpt else None
            if requested is None:
                try:
                    prior = _read_json(latest_receipt(args.codex_home)).get("optional_components", {}).get("local_multi_gpt", {}).get("enabled")
                except LifecycleError:
                    prior = None
                if prior is not None:
                    requested = bool(prior)
                elif not args.dry_run and sys.stdin.isatty():
                    optional = json.loads((args.repo_root / "install-manifest.json").read_text(encoding="utf-8"))["optional_components"]["local_multi_gpt"]
                    prompt = localized_optional_prompt(optional)
                    requested = input(prompt + " ").strip().lower() in {"y", "yes", "예", "네"}
                else:
                    requested = False
            result = install(args.repo_root, args.codex_home, dry_run=args.dry_run, local_multi_gpt=requested)
        elif args.command == "doctor":
            result = doctor(args.codex_home)
        else:
            result = rollback(args.codex_home, args.receipt)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", result.get("status") == "PASS") else 2
    except LifecycleError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
