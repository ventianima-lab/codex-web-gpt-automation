from __future__ import annotations

"""Deterministic mode contracts for the Oracle + DevSpace ChatGPT path.

This module deliberately contains no browser, account, attachment, or app-settings
automation.  It only turns a requested mode into the small composer handoff that
the Oracle runner may send after the one-time DevSpace setup has been completed.
"""

import argparse
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REGULAR_REASONING_LEVELS = ("Very High", "High", "Medium")
REGULAR_THINKING_TIME = {
    "Very High": "extra-high",
    "High": "extended",
    "Medium": "standard",
}
WRITE_CAPABLE_REGULAR_MODES = frozenset(("direct", "edit", "orchestrator"))
_CONFIG_SPEC = importlib.util.spec_from_file_location(
    "chatgpt_oracle_profiles_workspace_config",
    Path(__file__).resolve().parent / "chatgpt_workspace_config.py",
)
if _CONFIG_SPEC is None or _CONFIG_SPEC.loader is None:
    raise RuntimeError("workspace app config module unavailable")
WORKSPACE_CONFIG = importlib.util.module_from_spec(_CONFIG_SPEC)
_CONFIG_SPEC.loader.exec_module(WORKSPACE_CONFIG)
DEVSPACE_APP_NAME = WORKSPACE_CONFIG.DEFAULT_APP_NAME
# Current ChatGPT exposes Pro as the maximum effort for GPT-5.6 Sol, not as a
# separate model row.  The validated Oracle current/LKG contracts verify that
# Pro effort independently.
PRO_MODEL = "gpt-5.6-sol"
PRO_THINKING_TIME = "pro"
PRO_COMPOSER_PROMPT = (
    "Read the attached prompt/instructions and all attached files, then provide read-only analysis only. "
    "Do not create, edit, delete, or rename files; do not run commands or change settings, accounts, or external state."
)
PRO_DEVSPACE_COMPOSER_PREFIX = "Read and analyze the read-only mission file"


class OracleProfileError(ValueError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}

    def envelope(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": str(self), "evidence": self.evidence}}


@dataclass(frozen=True)
class OracleModeProfile:
    mode: str
    task_kind: str
    oracle_launch: bool
    devspace_required: bool
    research: bool = False
    legacy_route: str | None = None


_PROFILES = {
    "direct": OracleModeProfile("direct", "direct", True, True),
    "plan": OracleModeProfile("plan", "plan", True, True),
    "review": OracleModeProfile("review", "review", True, True),
    "edit": OracleModeProfile("edit", "edit", True, True),
    "orchestrator": OracleModeProfile("orchestrator", "orchestrator", True, True),
    "deep-research": OracleModeProfile("deep-research", "deep-research", True, True, research=True),
    "manual": OracleModeProfile("manual", "manual", False, False),
    "pro": OracleModeProfile("pro", "pro", True, True),
    "pro-attachment": OracleModeProfile("pro-attachment", "pro", True, False),
}
_ALIASES = {
    "deep_research": "deep-research",
    "deep research": "deep-research",
    "pro": "pro",
    "gpt-pro": "pro",
    "pro_attachment": "pro-attachment",
    "pro attachment": "pro-attachment",
}


def _normalize_mode(value: str) -> str:
    requested = str(value or "").strip().casefold()
    normalized = _ALIASES.get(requested, requested)
    if normalized not in _PROFILES:
        raise OracleProfileError("MODE_UNSUPPORTED", "Oracle mode is not supported", {"requested": value, "supported": list(_PROFILES)})
    return normalized


def resolve_profile(mode: str) -> OracleModeProfile:
    """Return a named mode profile without starting a browser or process."""
    return _PROFILES[_normalize_mode(mode)]


def _absolute_mission_path(value: str | Path | None) -> Path:
    if value is None or not str(value).strip():
        raise OracleProfileError("MISSION_PATH_REQUIRED", "Oracle launch modes require an absolute mission path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise OracleProfileError("MISSION_PATH_ABSOLUTE_REQUIRED", "mission path must be absolute", {"mission_path": str(path)})
    return path.resolve(strict=False)


def _resolve_reasoning(requested: str | None) -> str:
    if requested is None or not str(requested).strip():
        return REGULAR_REASONING_LEVELS[0]
    normalized = str(requested).strip().casefold()
    if normalized in {"very high", "very-high", "extra high", "extra-high", "xhigh", "매우 높음"}:
        return "Very High"
    if normalized in {"high", "높음"}:
        return "High"
    if normalized in {"medium", "중간"}:
        return "Medium"
    raise OracleProfileError(
        "REGULAR_REASONING_UNAVAILABLE",
        "requested regular reasoning level is unavailable; no downgrade was made",
        {"requested": str(requested), "supported": list(REGULAR_REASONING_LEVELS)},
    )


def composer_handoff(mission_path: str | Path, app_name: str | None = None) -> str:
    """The only regular-GPT composer text: app mention plus the absolute mission."""
    mission = _absolute_mission_path(mission_path)
    return (
        f"@{WORKSPACE_CONFIG.normalize_app_name(app_name or WORKSPACE_CONFIG.configured_app_name())} Read and execute the mission file: {mission}. "
        "Use only the exact project root recorded there; read the mission and applicable AGENTS.md fully first. "
        "If workspace opening times out, retry that same exact root once; never substitute a parent, child, active "
        "workspace, or shell boundary workaround."
    )


def pro_devspace_composer_handoff(mission_path: str | Path, app_name: str | None = None) -> str:
    """The qualified Pro DevSpace handoff, restricted to read-only advisory work."""
    mission = _absolute_mission_path(mission_path)
    return (
        f"@{WORKSPACE_CONFIG.normalize_app_name(app_name or WORKSPACE_CONFIG.configured_app_name())} {PRO_DEVSPACE_COMPOSER_PREFIX}: {mission}. "
        "Use only the exact project root recorded there; read the mission and applicable AGENTS.md fully first. "
        "Inspect and reason over project evidence without creating, editing, deleting, or renaming files and without "
        "running commands or changing settings, accounts, or external state. If the requested task requires writes or "
        "commands, return an implementation-ready handoff for a separate regular GPT-5.6 extra-high DevSpace stage."
    )


def _attachment_paths(values: list[str | Path] | tuple[str | Path, ...] | None) -> list[Path]:
    result: list[Path] = []
    for value in values or ():
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise OracleProfileError(
                "ATTACHMENT_PATH_ABSOLUTE_REQUIRED",
                "Pro attachment paths must be absolute",
                {"attachment_path": str(path)},
            )
        result.append(path.resolve(strict=False))
    return result


def build_launch_contract(
    mode: str,
    *,
    mission_path: str | Path | None = None,
    reasoning_level: str | None = None,
    attachment_paths: list[str | Path] | tuple[str | Path, ...] | None = None,
    app_name: str | None = None,
) -> dict[str, Any]:
    """Build an immutable, browser-agnostic launch contract for parent runners.

    `manual` intentionally produces a non-launch contract. The default Pro
    route uses DevSpace; the explicit pro-attachment mode preserves the
    attachment-only transport for frozen external evidence.
    """
    profile = resolve_profile(mode)
    resolved_app_name = WORKSPACE_CONFIG.normalize_app_name(
        app_name or WORKSPACE_CONFIG.configured_app_name()
    )
    result: dict[str, Any] = {
        "schema": "codex.chatgpt.oracle-mode-profile/v1",
        "mode": profile.mode,
        "task_kind": profile.task_kind,
        "oracle_launch": profile.oracle_launch,
        "devspace_required": profile.devspace_required,
        "research": profile.research,
        "attachments": [],
        "app_picker": False,
        "app_settings_automation": False,
        "pro_selection_policy": "explicit-only",
    }
    if not profile.oracle_launch:
        result.update({
            "route": "manual-no-launch",
            "app_policy": "not-applicable",
            "reasoning_level": None,
            "composer_prompt": None,
            "mission_path": None,
        })
        return result
    mission = _absolute_mission_path(mission_path)
    if profile.mode == "pro-attachment":
        attachments = _attachment_paths(attachment_paths)
        if mission not in attachments:
            attachments.insert(0, mission)
        if not attachments:
            raise OracleProfileError("PRO_ATTACHMENTS_REQUIRED", "Pro requires at least one exact attachment")
        result.update({
            "route": "oracle-pro-attachment-only",
            "app_policy": "forbidden",
            "attachment_policy": "always",
            "attachments": [str(path) for path in attachments],
            "model": PRO_MODEL,
            "reasoning_level": "Pro",
            # The current ChatGPT effort menu exposes the fifth tier as Pro.
            # Keep it explicit so parent runners cannot fall back to regular
            # Extra High or the retired Heavy compatibility spelling.
            "thinking_time": PRO_THINKING_TIME,
            "mission_path": str(mission),
            "composer_prompt": PRO_COMPOSER_PROMPT,
        })
        return result
    if profile.mode == "pro":
        if attachment_paths:
            raise OracleProfileError(
                "PRO_DEVSPACE_ATTACHMENTS_FORBIDDEN",
                "Pro DevSpace runs must not attach files",
            )
        result.update({
            "route": "oracle-pro-devspace-readonly",
            "app_policy": "prompt-mention-only",
            "attachment_policy": "forbidden",
            "app_name": resolved_app_name,
            "model": PRO_MODEL,
            "model_strategy": "select",
            "reasoning_level": "Pro",
            "thinking_time": PRO_THINKING_TIME,
            "action_authority": "read-only",
            "write_handoff": "regular-gpt-5.6-extra-high-devspace",
            "mission_path": str(mission),
            "composer_prompt": pro_devspace_composer_handoff(mission, resolved_app_name),
        })
        return result
    if attachment_paths:
        raise OracleProfileError(
            "REGULAR_ATTACHMENTS_FORBIDDEN",
            "non-Pro Oracle modes use DevSpace and must not attach files",
        )
    reasoning = _resolve_reasoning(reasoning_level)
    if profile.mode in WRITE_CAPABLE_REGULAR_MODES and reasoning != "Very High":
        raise OracleProfileError(
            "WRITE_REQUIRES_HIGHEST_NON_PRO",
            "write-capable work must use GPT-5.6 at the highest supported non-Pro reasoning tier",
            {
                "mode": profile.mode,
                "requested": reasoning,
                "required": "Very High",
                "thinking_time": "extra-high",
            },
        )
    result.update({
        "route": "oracle-devspace",
        "app_policy": "prompt-mention-only",
        "app_name": resolved_app_name,
        "reasoning_level": reasoning,
        # The validated Oracle current/LKG contracts keep `extra-high` distinct from the separate Pro
        # effort. Keep this in the mode contract so dispatch cannot silently
        # turn a requested High run into Extra High or Pro.
        "thinking_time": REGULAR_THINKING_TIME[reasoning],
        "action_authority": "mission-scoped-write" if profile.mode in WRITE_CAPABLE_REGULAR_MODES else "mission-scoped",
        "mission_path": str(mission),
        "composer_prompt": composer_handoff(mission, resolved_app_name),
    })
    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve Oracle + DevSpace mode profiles without launching ChatGPT.")
    parser.add_argument("command", choices=("resolve", "list"))
    parser.add_argument("--mode")
    parser.add_argument("--mission-path")
    parser.add_argument("--reasoning-level")
    parser.add_argument("--attachment", action="append", default=[])
    parser.add_argument("--app-name")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            result: dict[str, Any] = {"ok": True, "profiles": [asdict(item) for item in _PROFILES.values()]}
        else:
            if not args.mode:
                raise OracleProfileError("MODE_REQUIRED", "--mode is required for resolve")
            result = {
                "ok": True,
                "contract": build_launch_contract(
                    args.mode,
                    mission_path=args.mission_path,
                    reasoning_level=args.reasoning_level,
                    attachment_paths=args.attachment,
                    app_name=args.app_name,
                ),
            }
    except OracleProfileError as exc:
        print(json.dumps(exc.envelope(), ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
