from __future__ import annotations

"""Persist the narrow Chrome Local Network Access grant used by DevSpace.

The Windows implementation uses Chrome's documented per-user enterprise policy
and appends one exact origin without replacing unrelated policy entries.
"""

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


CHATGPT_ORIGIN = "https://chatgpt.com"
POLICY_SUBKEY = r"Software\Policies\Google\Chrome\LocalNetworkAccessAllowedForUrls"
POLICY_SUBKEYS = {
    "legacy_allow": POLICY_SUBKEY,
    "legacy_block": r"Software\Policies\Google\Chrome\LocalNetworkAccessBlockedForUrls",
    "local_allow": r"Software\Policies\Google\Chrome\LocalNetworkAllowedForUrls",
    "local_block": r"Software\Policies\Google\Chrome\LocalNetworkBlockedForUrls",
    "loopback_allow": r"Software\Policies\Google\Chrome\LoopbackNetworkAllowedForUrls",
    "loopback_block": r"Software\Policies\Google\Chrome\LoopbackNetworkBlockedForUrls",
}
_PROFILE_LEGACY_KEY = "local_network_access"
_PROFILE_SPLIT_KEYS = ("local_network", "loopback_network")


def _normalized(value: object) -> str:
    return str(value).strip().rstrip("/").casefold()


def policy_contains_origin(values: Mapping[str, object], origin: str = CHATGPT_ORIGIN) -> bool:
    expected = _normalized(origin)
    return any(_normalized(value) == expected for value in values.values())


def _policy_pattern_matches_origin(value: object, origin: str = CHATGPT_ORIGIN) -> bool:
    """Match the bounded Chrome URL-pattern forms relevant to this exact origin."""
    # Profile exceptions use a primary,secondary content-setting pattern such
    # as ``https://chatgpt.com:443,*``; policy lists contain just the primary.
    pattern = _normalized(value).split(",", 1)[0]
    expected = _normalized(origin)
    if pattern in {"*", expected}:
        return True
    parsed = urlsplit(expected)
    host = parsed.hostname or ""
    if pattern in {f"[*.]{host}", f"*.{host}"}:
        return True
    candidate = urlsplit(pattern)
    if not candidate.scheme or candidate.hostname != host or candidate.scheme != parsed.scheme:
        return False
    authority = pattern.split("://", 1)[1].split("/", 1)[0]
    expected_port = "443" if parsed.scheme == "https" else "80" if parsed.scheme == "http" else ""
    return authority in {host, f"{host}:{expected_port}", f"{host}:*"}


def _matching_policy_value_names(
    values: Mapping[str, object], origin: str = CHATGPT_ORIGIN
) -> list[str]:
    return [name for name, value in values.items() if _policy_pattern_matches_origin(value, origin)]


def _profile_setting(entries: object, origin: str = CHATGPT_ORIGIN) -> int | None:
    if not isinstance(entries, dict):
        return None
    matches: list[int] = []
    for pattern, entry in entries.items():
        if not _policy_pattern_matches_origin(pattern, origin) or not isinstance(entry, dict):
            continue
        setting = entry.get("setting")
        if isinstance(setting, int):
            matches.append(setting)
    if 2 in matches:  # ContentSetting::CONTENT_SETTING_BLOCK
        return 2
    if 1 in matches:  # ContentSetting::CONTENT_SETTING_ALLOW
        return 1
    return None


def browser_profile_loopback_allowed(profile_dir: Path) -> bool:
    """Return whether the profile effectively allows chatgpt.com loopback access.

    Chrome 151 splits the legacy local-network-access grant into local_network
    and loopback_network. DevSpace connects to 127.0.0.1, so local_network on
    its own is not sufficient. A legacy grant is accepted only before either
    split preference has been created; this avoids treating a partially migrated
    profile as ready.
    """
    return browser_profile_network_status(profile_dir)["loopback_network"] is True


def browser_profile_network_status(profile_dir: Path) -> dict[str, Any]:
    preferences_path = profile_dir.expanduser().resolve() / "Default" / "Preferences"
    try:
        preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "profile_dir": str(profile_dir.expanduser().resolve()),
            "preferences_path": str(preferences_path),
            "initialized": False,
            "local_network": False,
            "loopback_network": False,
            "exit_type": None,
            "exited_cleanly": False,
            "crash_restore_ready": False,
        }
    profile_value = preferences.get("profile", {}) if isinstance(preferences, dict) else {}
    if not isinstance(profile_value, dict):
        profile_value = {}
    exceptions = (
        preferences.get("profile", {}).get("content_settings", {}).get("exceptions", {})
        if isinstance(preferences, dict)
        else {}
    )
    if not isinstance(exceptions, dict):
        exceptions = {}
    split_present = any(key in exceptions for key in _PROFILE_SPLIT_KEYS)
    legacy_setting = _profile_setting(exceptions.get(_PROFILE_LEGACY_KEY))
    local_setting = _profile_setting(exceptions.get("local_network"))
    loopback_setting = _profile_setting(exceptions.get("loopback_network"))
    if not split_present:
        local_setting = local_setting if local_setting is not None else legacy_setting
        loopback_setting = loopback_setting if loopback_setting is not None else legacy_setting
    return {
        "profile_dir": str(profile_dir.expanduser().resolve()),
        "preferences_path": str(preferences_path),
        "initialized": True,
        "local_network": local_setting == 1,
        "loopback_network": loopback_setting == 1,
        "exit_type": profile_value.get("exit_type"),
        "exited_cleanly": profile_value.get("exited_cleanly") is True,
        "crash_restore_ready": (
            profile_value.get("exit_type") == "Normal"
            and profile_value.get("exited_cleanly") is True
        ),
    }


def next_policy_value_name(values: Mapping[str, object]) -> str:
    used = {int(name) for name in values if str(name).isdigit() and int(name) > 0}
    candidate = 1
    while candidate in used:
        candidate += 1
    return str(candidate)


def _read_windows_policy(subkey: str = POLICY_SUBKEY) -> dict[str, str]:
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return {}
    values: dict[str, str] = {}
    with key:
        index = 0
        while True:
            try:
                name, value, _kind = winreg.EnumValue(key, index)
            except OSError:
                break
            values[str(name)] = str(value)
            index += 1
    return values


def policy_status() -> dict[str, Any]:
    if sys.platform != "win32":
        return {
            "schema": "codex-web-gpt.chrome-local-network/v1",
            "supported": False,
            "enabled": False,
            "origin": CHATGPT_ORIGIN,
            "reason": "WINDOWS_CHROME_POLICY_ONLY",
        }
    policies = {name: _read_windows_policy(subkey) for name, subkey in POLICY_SUBKEYS.items()}
    matches = {name: _matching_policy_value_names(values) for name, values in policies.items()}
    # Chrome's split-policy precedence for a loopback request is specific
    # loopback block/allow, then legacy block/allow. Local-network entries are
    # intentionally reported but cannot make a 127.0.0.1 DevSpace ready.
    if matches["loopback_block"]:
        effective = "blocked"
    elif matches["loopback_allow"]:
        effective = "allowed"
    elif matches["legacy_block"]:
        effective = "blocked"
    elif matches["legacy_allow"]:
        effective = "allowed"
    else:
        effective = "unset"
    return {
        "schema": "codex-web-gpt.chrome-local-network/v1",
        "supported": True,
        "enabled": effective == "allowed",
        "origin": CHATGPT_ORIGIN,
        "policy_subkey": POLICY_SUBKEY,
        "matching_value_names": matches["legacy_allow"],
        "entry_count": len(policies["legacy_allow"]),
        "effective_permission": "loopback_network",
        "effective_policy": effective,
        "policy_matches": matches,
        "policy_entry_counts": {name: len(values) for name, values in policies.items()},
    }


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_profile_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chrome_timestamp() -> str:
    chrome_epoch = dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)
    return str(int((dt.datetime.now(dt.timezone.utc) - chrome_epoch).total_seconds() * 1_000_000))


def _profile_in_use(profile_dir: Path) -> bool:
    if sys.platform != "win32":
        return False
    env = os.environ.copy()
    env["CODEX_ORACLE_PROFILE_TARGET"] = str(profile_dir.expanduser().resolve())
    script = (
        "$target=[Environment]::GetEnvironmentVariable('CODEX_ORACLE_PROFILE_TARGET');"
        "$count=@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" -ErrorAction Stop | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($target, "
        "[StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count; Write-Output $count"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=env,
            creationflags=0x08000000,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("ORACLE_CHROME_PROFILE_PROCESS_CHECK_FAILED") from exc
    if completed.returncode != 0 or not completed.stdout.strip().isdigit():
        raise RuntimeError("ORACLE_CHROME_PROFILE_PROCESS_CHECK_FAILED")
    return int(completed.stdout.strip()) > 0


def enable_profile_permission(
    *,
    profile_dir: Path,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    profile = profile_dir.expanduser().resolve()
    preferences_path = profile / "Default" / "Preferences"
    if _profile_in_use(profile):
        raise RuntimeError("ORACLE_CHROME_PROFILE_IN_USE")
    try:
        preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("ORACLE_CHROME_PROFILE_PREFERENCES_UNREADABLE") from exc
    if not isinstance(preferences, dict):
        raise RuntimeError("ORACLE_CHROME_PROFILE_PREFERENCES_INVALID")
    root = (codex_home or Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))).resolve()
    before_sha256 = _sha256(preferences_path)
    before_status = browser_profile_network_status(profile)
    changed = not (
        before_status["local_network"]
        and before_status["loopback_network"]
        and before_status["crash_restore_ready"]
    )
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S%f")
    backup_path: Path | None = None
    if changed:
        backup_path = root / "backups" / "chrome-local-network" / stamp / "Preferences"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(preferences_path, backup_path)
        profile_value = preferences.setdefault("profile", {})
        if not isinstance(profile_value, dict):
            raise RuntimeError("ORACLE_CHROME_PROFILE_SECTION_INVALID")
        # This seed is copied into isolated Oracle Chrome profiles. Mark only
        # the seed as clean; never change the user's ordinary Chrome profile.
        profile_value["exit_type"] = "Normal"
        profile_value["exited_cleanly"] = True
        content_settings = profile_value.setdefault("content_settings", {})
        if not isinstance(content_settings, dict):
            raise RuntimeError("ORACLE_CHROME_CONTENT_SETTINGS_INVALID")
        exceptions = content_settings.setdefault("exceptions", {})
        if not isinstance(exceptions, dict):
            raise RuntimeError("ORACLE_CHROME_CONTENT_EXCEPTIONS_INVALID")
        modified = _chrome_timestamp()
        pattern = "https://chatgpt.com:443,*"
        for permission in _PROFILE_SPLIT_KEYS:
            entries = exceptions.setdefault(permission, {})
            if not isinstance(entries, dict):
                raise RuntimeError("ORACLE_CHROME_NETWORK_PERMISSION_INVALID")
            existing = entries.get(pattern)
            entry = dict(existing) if isinstance(existing, dict) else {}
            entry.update({"last_modified": modified, "setting": 1})
            entries[pattern] = entry
        _write_profile_json_atomic(preferences_path, preferences)
    after_status = browser_profile_network_status(profile)
    if not (
        after_status["local_network"]
        and after_status["loopback_network"]
        and after_status["crash_restore_ready"]
    ):
        raise RuntimeError("ORACLE_CHROME_LOCAL_NETWORK_PERMISSION_NOT_DURABLE")
    receipt = root / "receipts" / f"chatgpt-local-network-profile-{stamp}.json"
    payload = {
        "schema": "codex-web-gpt.chrome-local-network-profile-receipt/v2",
        "applied_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "origin": CHATGPT_ORIGIN,
        "profile_dir": str(profile),
        "preferences_path": str(preferences_path),
        "changed": changed,
        "backup_path": str(backup_path) if backup_path else None,
        "before_sha256": before_sha256,
        "after_sha256": _sha256(preferences_path),
        "local_network": True,
        "loopback_network": True,
        "exit_type": "Normal",
        "exited_cleanly": True,
        "crash_restore_ready": True,
    }
    _write_json_atomic(receipt, payload)
    return {**after_status, "enabled": True, "mode": "oracle-seed-profile", "changed": changed, "receipt": str(receipt)}


def durable_status(*, profile_dir: Path) -> dict[str, Any]:
    policy = policy_status()
    profile = browser_profile_network_status(profile_dir)
    profile_enabled = bool(profile["local_network"] and profile["loopback_network"])
    policy_blocked = policy.get("effective_policy") == "blocked"
    return {
        **policy,
        "enabled": False if policy_blocked else bool(policy.get("enabled")) or profile_enabled,
        "mode": (
            "blocked-by-chrome-policy"
            if policy_blocked
            else "chrome-policy"
            if policy.get("enabled")
            else "oracle-seed-profile"
            if profile_enabled
            else "unset"
        ),
        "oracle_profile": profile,
    }


def enable_durable_permission(*, profile_dir: Path, codex_home: Path | None = None) -> dict[str, Any]:
    try:
        policy = enable_policy(codex_home=codex_home)
    except PermissionError:
        policy = policy_status()
        if policy.get("effective_policy") == "blocked":
            raise RuntimeError("CHATGPT_LOCAL_NETWORK_POLICY_BLOCKED")
        return enable_profile_permission(profile_dir=profile_dir, codex_home=codex_home)
    if policy.get("enabled"):
        profile = enable_profile_permission(profile_dir=profile_dir, codex_home=codex_home)
        return {
            **durable_status(profile_dir=profile_dir),
            "changed": bool(policy.get("changed")) or bool(profile.get("changed")),
            "receipt": profile.get("receipt"),
            "policy_receipt": policy.get("receipt"),
        }
    if policy.get("effective_policy") == "blocked":
        raise RuntimeError("CHATGPT_LOCAL_NETWORK_POLICY_BLOCKED")
    return enable_profile_permission(profile_dir=profile_dir, codex_home=codex_home)


def enable_policy(*, codex_home: Path | None = None) -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("WINDOWS_CHROME_POLICY_ONLY")
    import winreg

    before = _read_windows_policy()
    changed = not policy_contains_origin(before)
    value_name: str | None = None
    if changed:
        value_name = next_policy_value_name(before)
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            POLICY_SUBKEY,
            0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, CHATGPT_ORIGIN)
    after = _read_windows_policy()
    if not policy_contains_origin(after):
        raise RuntimeError("CHATGPT_LOCAL_NETWORK_POLICY_NOT_DURABLE")
    root = (codex_home or Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))).resolve()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S%f")
    receipt = root / "receipts" / f"chatgpt-local-network-policy-{stamp}.json"
    payload = {
        "schema": "codex-web-gpt.chrome-local-network-receipt/v1",
        "applied_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "origin": CHATGPT_ORIGIN,
        "policy_subkey": POLICY_SUBKEY,
        "changed": changed,
        "created_value_name": value_name,
        "preserved_entry_count": len(before),
        "enabled": True,
    }
    _write_json_atomic(receipt, payload)
    return {**policy_status(), "changed": changed, "receipt": str(receipt)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage exact chatgpt.com Chrome Local Network Access")
    parser.add_argument("command", choices=("status", "enable"))
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--profile-dir", type=Path, default=Path.home() / ".oracle" / "browser-profile")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = (
            durable_status(profile_dir=args.profile_dir)
            if args.command == "status"
            else enable_durable_permission(profile_dir=args.profile_dir, codex_home=args.codex_home)
        )
    except PermissionError:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "CHROME_POLICY_WRITE_DENIED",
                    "next_action": (
                        "Close the dedicated Oracle browser profile and rerun this command."
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 2
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("enabled") else 3


if __name__ == "__main__":
    raise SystemExit(main())
