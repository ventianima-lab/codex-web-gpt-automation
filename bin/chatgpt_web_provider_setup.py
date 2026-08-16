#!/usr/bin/env python3
from __future__ import annotations

"""Install and diagnose the local OpenCodex -> Web ChatGPT provider route."""

import argparse
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA = "codex.web-chatgpt-provider/v1"
PROVIDER_ID = "web-chatgpt"
MODEL_ID = "web-gpt-codex"
CUSTOM_MODEL_ID = "174cfa6c-0b4d-5bfa-b8ab-2145f0da5c86"
DEFAULT_PORT = 10101
WINDOWS_RUN_NAME = "CodexWebChatGPTBridge"


class SetupError(RuntimeError):
    pass


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError(f"JSON_UNREADABLE:{path}") from exc
    if not isinstance(value, dict):
        raise SetupError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _config_paths(codex_home: Path | None = None, opencodex_home: Path | None = None) -> tuple[Path, Path]:
    codex = (codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")).expanduser().resolve()
    ocx = (opencodex_home or Path.home() / ".opencodex").expanduser().resolve()
    return codex / "config" / "web-chatgpt-provider.json", ocx / "config.json"


def apply_live_provider(provider: dict[str, Any], *, port: int = 10100, opener: Callable[..., Any] = urllib.request.urlopen) -> bool:
    """Apply one provider to a running OpenCodex without restarting active clients."""
    body = json.dumps({"name": PROVIDER_ID, "provider": provider, "setDefault": False}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    admin_token_path = Path.home() / ".opencodex" / "admin-api-token"
    try:
        metadata = admin_token_path.lstat()
        admin_token = (
            admin_token_path.read_text(encoding="utf-8").strip()
            if stat.S_ISREG(metadata.st_mode) and not admin_token_path.is_symlink() and metadata.st_size <= 512
            else ""
        )
    except OSError:
        admin_token = ""
    if re.fullmatch(r"ocx_admin_[A-Za-z0-9_-]{43}", admin_token):
        headers["X-OpenCodex-API-Key"] = admin_token
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/providers",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with opener(request, timeout=15) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise SetupError(f"OPENCODEX_LIVE_PROVIDER_REJECTED:{exc.code}") from exc
    except (OSError, urllib.error.URLError):
        return False
    except json.JSONDecodeError as exc:
        raise SetupError("OPENCODEX_LIVE_RESPONSE_INVALID") from exc
    if not isinstance(result, dict) or result.get("success") is not True:
        raise SetupError("OPENCODEX_LIVE_PROVIDER_REJECTED")
    return True


def configure(
    *,
    project_root: Path,
    port: int = DEFAULT_PORT,
    app_name: str = "codex",
    reasoning_level: str = "Very High",
    codex_home: Path | None = None,
    opencodex_home: Path | None = None,
    python_executable: Path | None = None,
    live_apply: Callable[[dict[str, Any]], bool] = apply_live_provider,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise SetupError("PROJECT_ROOT_NOT_DIRECTORY")
    if not 1024 <= port <= 65535:
        raise SetupError("PORT_INVALID")
    bridge_config_path, ocx_config_path = _config_paths(codex_home, opencodex_home)
    if not ocx_config_path.is_file():
        raise SetupError("OPENCODEX_CONFIG_MISSING")
    codex_root = bridge_config_path.parent.parent
    dispatch = codex_root / "bin" / "chatgpt_oracle_dispatch.py"
    bridge = codex_root / "bin" / "chatgpt_web_provider_bridge.py"
    launcher = codex_root / "scripts" / "start_chatgpt_web_provider_bridge.ps1"
    for path in (dispatch, bridge, launcher):
        if not path.is_file():
            raise SetupError(f"INSTALLED_COMPONENT_MISSING:{path.name}")
    existing_bridge = _json_object(bridge_config_path) if bridge_config_path.is_file() else {}
    token = str(existing_bridge.get("auth_token") or secrets.token_urlsafe(36))
    payload = {
        "schema": SCHEMA,
        "host": "127.0.0.1",
        "port": port,
        "auth_token": token,
        "project_root": str(root),
        "app_name": app_name,
        "reasoning_level": reasoning_level,
        "python_executable": str((python_executable or Path(sys.executable)).expanduser().resolve(strict=True)),
        "dispatch_script": str(dispatch.resolve(strict=True)),
        "request_root": str(root / ".codex-tmp" / "web-chatgpt-provider"),
        "log_root": str(codex_root / "logs" / "web-chatgpt-provider"),
        "keepalive_seconds": 15,
    }
    ocx_config = _json_object(ocx_config_path)
    backup_root = ocx_config_path.parent / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / f"config.pre-web-chatgpt-{time.strftime('%Y%m%d-%H%M%S')}.json"
    shutil.copy2(ocx_config_path, backup)
    provider = {
        "adapter": "openai-chat",
        "baseUrl": f"http://127.0.0.1:{port}/v1",
        "authMode": "key",
        "apiKey": token,
        "allowPrivateNetwork": True,
        "liveModels": True,
        "models": [MODEL_ID],
        "selectedModels": [MODEL_ID],
        "noReasoningModels": [MODEL_ID],
        "noTemperatureModels": [MODEL_ID],
        "noTopPModels": [MODEL_ID],
        "noPenaltyModels": [MODEL_ID],
        "note": "Regular Web ChatGPT via Oracle + DevSpace; uses Web ChatGPT allocation, not Codex/API quota.",
    }
    live_applied = live_apply(provider)
    # A live OpenCodex POST persists its in-memory config. Reload those bytes
    # before the final merge so unrelated runtime-side changes are retained.
    if live_applied:
        ocx_config = _json_object(ocx_config_path)
    providers = ocx_config.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise SetupError("OPENCODEX_PROVIDERS_INVALID")
    providers[PROVIDER_ID] = provider
    custom_models = ocx_config.setdefault("customModels", [])
    if not isinstance(custom_models, list):
        raise SetupError("OPENCODEX_CUSTOM_MODELS_INVALID")
    custom_models[:] = [
        item for item in custom_models
        if not (isinstance(item, dict) and (item.get("id") == CUSTOM_MODEL_ID or (item.get("provider") == PROVIDER_ID and item.get("modelId") == MODEL_ID)))
    ]
    custom_models.append({
        "id": CUSTOM_MODEL_ID,
        "provider": PROVIDER_ID,
        "modelId": MODEL_ID,
        "displayName": "Web ChatGPT Codex (DevSpace)",
        "contextWindow": 150000,
        "inputModalities": ["text"],
        "addedAt": "2026-08-16T00:00:00.000Z",
    })
    _atomic_json(bridge_config_path, payload)
    _atomic_json(ocx_config_path, ocx_config)
    return {
        "ok": True,
        "provider": PROVIDER_ID,
        "model": f"{PROVIDER_ID}/{MODEL_ID}",
        "bridge_config": str(bridge_config_path),
        "opencodex_config": str(ocx_config_path),
        "backup": str(backup),
        "host": "127.0.0.1",
        "port": port,
        "live_applied": live_applied,
    }


def windows_command(codex_home: Path | None = None) -> str:
    root = (codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")).expanduser().resolve()
    powershell = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = root / "scripts" / "start_chatgpt_web_provider_bridge.ps1"
    return f'"{powershell}" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{script}" -Mode Watch'


def register_autostart(
    *,
    codex_home: Path | None = None,
    runner: Callable[..., Any] = subprocess.run,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    platform_name: str | None = None,
) -> dict[str, Any]:
    platform = platform_name or os.name
    if platform != "nt":
        return {"ok": True, "changed": False, "mode": "manual-non-windows"}
    root = (codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")).expanduser().resolve()
    command = windows_command(root)
    runner([
        "reg.exe", "ADD", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
        "/v", WINDOWS_RUN_NAME, "/t", "REG_SZ", "/d", command, "/f",
    ], check=True, text=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    powershell = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    launcher = root / "scripts" / "start_chatgpt_web_provider_bridge.ps1"
    popen_factory([
        str(powershell), "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
        "-File", str(launcher), "-Mode", "Watch",
    ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"ok": True, "changed": True, "mode": "per-user-login-watchdog", "run_name": WINDOWS_RUN_NAME}


def probe(port: int, timeout: float = 3.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            value = json.loads(response.read())
            return {"ok": response.status == 200 and bool(value.get("ok")), "status": response.status, "busy": value.get("busy")}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": type(exc).__name__}


def status(*, codex_home: Path | None = None, opencodex_home: Path | None = None) -> dict[str, Any]:
    bridge_path, ocx_path = _config_paths(codex_home, opencodex_home)
    bridge = _json_object(bridge_path) if bridge_path.is_file() else {}
    ocx = _json_object(ocx_path) if ocx_path.is_file() else {}
    provider = (ocx.get("providers") or {}).get(PROVIDER_ID) if isinstance(ocx.get("providers"), dict) else None
    configured = (
        bridge.get("schema") == SCHEMA
        and isinstance(provider, dict)
        and provider.get("baseUrl") == f"http://127.0.0.1:{bridge.get('port')}/v1"
        and provider.get("apiKey") == bridge.get("auth_token")
    )
    health = probe(int(bridge.get("port") or DEFAULT_PORT)) if configured else {"ok": False, "error": "NOT_CONFIGURED"}
    return {
        "ok": configured and health.get("ok") is True,
        "configured": configured,
        "health": health,
        "provider": PROVIDER_ID,
        "model": f"{PROVIDER_ID}/{MODEL_ID}",
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure OpenCodex to use regular Web ChatGPT through Oracle.")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("--project-root", type=Path, required=True)
    install.add_argument("--port", type=int, default=DEFAULT_PORT)
    install.add_argument("--app-name", default="codex")
    install.add_argument("--reasoning-level", choices=("Very High", "High", "Medium"), default="Very High")
    install.add_argument("--no-start", action="store_true")
    commands.add_parser("status")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "install":
            result = configure(project_root=args.project_root, port=args.port, app_name=args.app_name, reasoning_level=args.reasoning_level)
            if not args.no_start:
                result["autostart"] = register_autostart()
        else:
            result = status()
    except SetupError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
