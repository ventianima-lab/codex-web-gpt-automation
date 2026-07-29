from __future__ import annotations

"""Deterministic mode contracts for the Oracle + DevSpace ChatGPT path.

This module deliberately contains no browser, account, attachment, or app-settings
automation.  It only turns a requested mode into the small composer handoff that
the Oracle runner may send after the one-time DevSpace setup has been completed.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REGULAR_REASONING_LEVELS = ("Very High", "High")
DEVSPACE_APP_NAME = "DevSpace"
PRO_MODEL = "gpt-5.5-pro"
PRO_COMPOSER_PROMPT = "Read the attached prompt/instructions and all attached files, then complete the task."


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
    "pro": OracleModeProfile("pro", "pro", True, False),
}
_ALIASES = {
    "deep_research": "deep-research",
    "deep research": "deep-research",
    "pro": "pro",
    "gpt-pro": "pro",
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
    if normalized in {"very high", "very-high", "extra high", "extra-high", "매우 높음", "heavy"}:
        return "Very High"
    if normalized == "high":
        return "High"
    raise OracleProfileError(
        "REGULAR_REASONING_UNAVAILABLE",
        "requested regular reasoning level is unavailable; no downgrade was made",
        {"requested": str(requested), "supported": list(REGULAR_REASONING_LEVELS)},
    )


def composer_handoff(mission_path: str | Path) -> str:
    """The only regular-GPT composer text: app mention plus the absolute mission."""
    mission = _absolute_mission_path(mission_path)
    return (
        f"@{DEVSPACE_APP_NAME} Read and execute the mission file: {mission}. "
        "Use only the exact project root recorded there; read the mission and applicable AGENTS.md fully first. "
        "If workspace opening times out, retry that same exact root once; never substitute a parent, child, active "
        "workspace, or shell boundary workaround."
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
) -> dict[str, Any]:
    """Build an immutable, browser-agnostic launch contract for parent runners.

    `manual` intentionally produces a non-launch contract. Pro uses the same
    Oracle browser engine but has a distinct attachment-only transport.
    """
    profile = resolve_profile(mode)
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
    if profile.mode == "pro":
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
            "mission_path": str(mission),
            "composer_prompt": PRO_COMPOSER_PROMPT,
        })
        return result
    if attachment_paths:
        raise OracleProfileError(
            "REGULAR_ATTACHMENTS_FORBIDDEN",
            "non-Pro Oracle modes use DevSpace and must not attach files",
        )
    result.update({
        "route": "oracle-devspace",
        "app_policy": "prompt-mention-only",
        "app_name": DEVSPACE_APP_NAME,
        "reasoning_level": _resolve_reasoning(reasoning_level),
        "mission_path": str(mission),
        "composer_prompt": composer_handoff(mission),
    })
    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve Oracle + DevSpace mode profiles without launching ChatGPT.")
    parser.add_argument("command", choices=("resolve", "list"))
    parser.add_argument("--mode")
    parser.add_argument("--mission-path")
    parser.add_argument("--reasoning-level")
    parser.add_argument("--attachment", action="append", default=[])
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
                ),
            }
    except OracleProfileError as exc:
        print(json.dumps(exc.envelope(), ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
