from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_APP_NAME = "codex"
CONFIG_FILE = "chatgpt-workspace.json"


def normalize_app_name(value: object) -> str:
    name = str(value or "").strip()
    if not name or name.startswith("@") or len(name) > 128 or any(ch in name for ch in "\r\n"):
        raise ValueError("app_name must be 1..128 characters without @ or line breaks")
    return name


def config_path() -> Path:
    root = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    return root / CONFIG_FILE


def configured_app_name() -> str:
    override = str(os.environ.get("CODEX_CHATGPT_APP_NAME") or "").strip()
    if override:
        return normalize_app_name(override)
    path = config_path()
    if not path.is_file():
        return DEFAULT_APP_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"workspace app config is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"workspace app config must be a JSON object: {path}")
    return normalize_app_name(value.get("app_name") or DEFAULT_APP_NAME)
