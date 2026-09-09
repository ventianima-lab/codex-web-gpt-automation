#!/usr/bin/env python
"""Safely install the recommended global Codex subagent configuration.

The lifecycle installer deploys this helper and its public templates, but does
not mutate user-owned ``config.toml`` or ``AGENTS.md``.  This command performs
that separate, explicit merge with backups, an atomic replacement, and a
machine-readable receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANAGED_BEGIN = "<!-- BEGIN CODEX WEB GPT SUBAGENT POLICY -->"
MANAGED_END = "<!-- END CODEX WEB GPT SUBAGENT POLICY -->"
ROLE_NAMES = ("scout", "implementer", "verifier")
AGENT_SETTINGS = {
    "enabled": "true",
    "max_concurrent_threads_per_session": "3",
    "default_subagent_model": '"gpt-5.6-terra"',
    "default_subagent_reasoning_effort": '"medium"',
}
MANAGED_ROLE_MARKER = "# Managed by Codex Web GPT Automation"


class AgentSetupError(RuntimeError):
    pass


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _template_root(source_root: Path | None = None) -> Path:
    return (source_root or _source_root()) / "docs" / "templates" / "codex-agents"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str | None:
    return _sha256_bytes(path.read_bytes()) if path.exists() else None


def _load_templates(source_root: Path | None = None) -> tuple[dict[str, str], str]:
    root = _template_root(source_root)
    roles: dict[str, str] = {}
    for name in ROLE_NAMES:
        path = root / f"{name}.toml"
        if not path.is_file():
            raise AgentSetupError(f"missing role template: {path}")
        text = path.read_text(encoding="utf-8")
        parsed = tomllib.loads(text)
        if parsed.get("name") != name or MANAGED_ROLE_MARKER not in text:
            raise AgentSetupError(f"invalid managed role template: {path}")
        roles[name] = text.rstrip() + "\n"
    policy_path = root / "global-agents-policy.md"
    if not policy_path.is_file():
        raise AgentSetupError(f"missing policy template: {policy_path}")
    policy = policy_path.read_text(encoding="utf-8").strip()
    if MANAGED_BEGIN not in policy or MANAGED_END not in policy:
        raise AgentSetupError("global policy template is missing managed markers")
    return roles, policy + "\n"


def merge_config(text: str) -> str:
    """Preserve commander settings and merge only the global ``[agents]`` table."""
    merged = text.replace("\r\n", "\n")
    lines = merged.splitlines()
    headers = [i for i, line in enumerate(lines) if re.match(r"^\s*\[", line)]
    agents_headers = [i for i in headers if re.match(r"^\s*\[agents\]\s*(?:#.*)?$", lines[i])]
    if len(agents_headers) > 1:
        raise AgentSetupError("duplicate [agents] tables")
    if not agents_headers:
        block = [
            "",
            "# Managed by Codex Web GPT Automation. Role files live in ~/.codex/agents/.",
            "[agents]",
            *(f"{key} = {value}" for key, value in AGENT_SETTINGS.items()),
        ]
        return "\n".join(lines + block).rstrip() + "\n"

    start = agents_headers[0]
    end = next((i for i in headers if i > start), len(lines))
    body = lines[start + 1 : end]
    found: set[str] = set()
    rewritten: list[str] = []
    for line in body:
        legacy = re.match(r"^\s*max_threads\s*=", line)
        if legacy:
            continue
        matched = False
        for key, value in AGENT_SETTINGS.items():
            if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                if key in found:
                    raise AgentSetupError(f"duplicate [agents] setting: {key}")
                rewritten.append(f"{key} = {value}")
                found.add(key)
                matched = True
                break
        if not matched:
            rewritten.append(line)
    for key, value in AGENT_SETTINGS.items():
        if key not in found:
            rewritten.append(f"{key} = {value}")
    lines[start + 1 : end] = rewritten
    return "\n".join(lines).rstrip() + "\n"


def merge_global_policy(text: str, policy: str) -> str:
    normalized = text.replace("\r\n", "\n")
    begin_count = normalized.count(MANAGED_BEGIN)
    end_count = normalized.count(MANAGED_END)
    if begin_count != end_count or begin_count > 1:
        raise AgentSetupError("malformed managed policy block in global AGENTS.md")
    if begin_count == 1:
        pattern = re.compile(
            rf"{re.escape(MANAGED_BEGIN)}.*?{re.escape(MANAGED_END)}\n?", re.DOTALL
        )
        # Treat Windows paths and other backslashes in the managed policy as
        # literal text instead of regular-expression replacement escapes.
        return pattern.sub(lambda _match: policy, normalized).rstrip() + "\n"
    prefix = normalized.rstrip()
    return (prefix + "\n\n" if prefix else "") + policy


def desired_files(
    codex_home: Path, *, source_root: Path | None = None, replace_existing_roles: bool = False
) -> dict[Path, bytes]:
    config_path = codex_home / "config.toml"
    if not config_path.is_file():
        raise AgentSetupError(f"missing Codex config: {config_path}")
    roles, policy = _load_templates(source_root)

    # Read immediately before deriving the merged output.  Nothing caches the
    # user's config between planning and application.
    config_text = config_path.read_text(encoding="utf-8")
    desired: dict[Path, bytes] = {
        config_path: merge_config(config_text).encode("utf-8"),
        codex_home / "AGENTS.md": merge_global_policy(
            (codex_home / "AGENTS.md").read_text(encoding="utf-8")
            if (codex_home / "AGENTS.md").exists()
            else "",
            policy,
        ).encode("utf-8"),
    }
    for name, role_text in roles.items():
        path = codex_home / "agents" / f"{name}.toml"
        if path.exists():
            current = path.read_text(encoding="utf-8")
            if current != role_text and MANAGED_ROLE_MARKER not in current and not replace_existing_roles:
                raise AgentSetupError(
                    f"unmanaged role exists: {path}; rerun with --replace-existing-roles only after review"
                )
        desired[path] = role_text.encode("utf-8")
    return desired


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def doctor(codex_home: Path, *, source_root: Path | None = None) -> dict[str, Any]:
    roles, policy = _load_templates(source_root)
    config_path = codex_home / "config.toml"
    errors: list[str] = []
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive error reporting
        config = {}
        errors.append(f"CONFIG_TOML_INVALID:{exc}")
    observed_main = {
        "model": config.get("model"),
        "model_reasoning_effort": config.get("model_reasoning_effort"),
    }
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    expected_agents = {
        "enabled": True,
        "max_concurrent_threads_per_session": 3,
        "default_subagent_model": "gpt-5.6-terra",
        "default_subagent_reasoning_effort": "medium",
    }
    for key, value in expected_agents.items():
        if agents.get(key) != value:
            errors.append(f"AGENTS_SETTING_MISMATCH:{key}")
    features = config.get("features") if isinstance(config.get("features"), dict) else {}
    if features.get("multi_agent_v2") is True:
        errors.append("UNSTABLE_MULTI_AGENT_V2_ENABLED")
    for name, expected in roles.items():
        path = codex_home / "agents" / f"{name}.toml"
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            errors.append(f"ROLE_MISMATCH:{name}")
            continue
        try:
            tomllib.loads(expected)
        except tomllib.TOMLDecodeError:
            errors.append(f"ROLE_TOML_INVALID:{name}")
    policy_text = (codex_home / "AGENTS.md").read_text(encoding="utf-8") if (codex_home / "AGENTS.md").exists() else ""
    if policy.strip() not in policy_text or policy_text.count(MANAGED_BEGIN) != 1:
        errors.append("GLOBAL_POLICY_MISMATCH")
    return {
        "schema": "codex.web-gpt.global-agents-doctor/v1",
        "ok": not errors,
        "codex_home": str(codex_home),
        "main": observed_main,
        "defaults": expected_agents,
        "roles": list(ROLE_NAMES),
        "multi_agent_v2_enabled": features.get("multi_agent_v2") is True,
        "errors": errors,
    }


def apply_setup(
    codex_home: Path, *, source_root: Path | None = None, replace_existing_roles: bool = False
) -> dict[str, Any]:
    codex_home = codex_home.expanduser().resolve()
    desired = desired_files(
        codex_home, source_root=source_root, replace_existing_roles=replace_existing_roles
    )
    changed = [path for path, data in desired.items() if not path.exists() or path.read_bytes() != data]
    if not changed:
        return {"ok": doctor(codex_home, source_root=source_root)["ok"], "changed": [], "receipt": None}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nonce = os.urandom(4).hex()
    backup_root = codex_home / "backups" / "codex-web-gpt-agents" / f"{stamp}-{nonce}"
    records: list[dict[str, Any]] = []
    for path in changed:
        relative = path.relative_to(codex_home).as_posix()
        existed = path.exists()
        backup = backup_root / relative
        if existed:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        records.append(
            {
                "path": relative,
                "existed": existed,
                "before_sha256": _sha256_file(path),
                "after_sha256": _sha256_bytes(desired[path]),
                "backup": str(backup) if existed else None,
            }
        )
    try:
        for path in changed:
            _atomic_write(path, desired[path])
        result = doctor(codex_home, source_root=source_root)
        if not result["ok"]:
            raise AgentSetupError("post-apply doctor failed: " + ",".join(result["errors"]))
    except Exception:
        for record in reversed(records):
            destination = codex_home / record["path"]
            if record["existed"]:
                _atomic_write(destination, Path(record["backup"]).read_bytes())
            else:
                destination.unlink(missing_ok=True)
        raise

    receipt = codex_home / "receipts" / f"codex-web-gpt-agents-{stamp}-{nonce}.json"
    receipt_payload = {
        "schema": "codex.web-gpt.global-agents-receipt/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backup_root": str(backup_root),
        "files": records,
    }
    _atomic_write(receipt, (json.dumps(receipt_payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"ok": True, "changed": [record["path"] for record in records], "receipt": str(receipt)}


def plan(codex_home: Path, *, source_root: Path | None = None) -> dict[str, Any]:
    desired = desired_files(codex_home.expanduser().resolve(), source_root=source_root)
    return {
        "schema": "codex.web-gpt.global-agents-plan/v1",
        "codex_home": str(codex_home.expanduser().resolve()),
        "changes": [
            {
                "path": path.relative_to(codex_home.expanduser().resolve()).as_posix(),
                "before_sha256": _sha256_file(path),
                "after_sha256": _sha256_bytes(data),
                "would_change": not path.exists() or path.read_bytes() != data,
            }
            for path, data in desired.items()
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or verify global Codex subagent defaults.")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--doctor", action="store_true")
    parser.add_argument("--replace-existing-roles", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            result = apply_setup(args.codex_home, replace_existing_roles=args.replace_existing_roles)
        elif args.doctor:
            result = doctor(args.codex_home.expanduser().resolve())
        else:
            result = plan(args.codex_home)
    except (AgentSetupError, OSError, tomllib.TOMLDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
