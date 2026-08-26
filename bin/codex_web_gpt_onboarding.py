from __future__ import annotations

"""Fail-closed first-install planner and readiness check.

This module deliberately never accepts or reads the DevSpace Owner password.
It orders the non-secret setup stages and checks the resulting public contract.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

from chatgpt_chrome_local_network import policy_status
import codex_local_multi_gpt_setup as LOCAL_MULTI_GPT_SETUP


PRODUCT_NAME = "Codex Web GPT Automation"
APP_NAME = "codex"
DEFAULT_LOCAL_PORT = 7676
PROVIDERS = ("tailscale", "cloudflare", "ngrok", "custom")
STATE_SCHEMA = "codex-web-gpt.onboarding-wizard/v1"
STATE_RELATIVE = Path("state") / "codex-web-gpt-automation" / "onboarding" / "state.json"
SETUP_SCRIPT = Path("skills") / "chatgpt-workspace-setup" / "scripts" / "devspace_tailscale_setup.py"
STAGE_IDS = (
    "01_install",
    "02_stable_endpoint",
    "03_devspace_init",
    "04_reboot_service",
    "05_endpoint_check",
    "06_oracle_login",
    "06b_local_network_access",
    "07_chatgpt_app",
    "08_final_gate",
)
USER_OWNED_STAGES = (
    "02_stable_endpoint",
    "03_devspace_init",
    "06_oracle_login",
    "06b_local_network_access",
    "07_chatgpt_app",
)
USER_CONFIRMATION_STAGES = (
    "02_stable_endpoint",
    "04_reboot_service",
    "06_oracle_login",
    "07_chatgpt_app",
)
FINAL_GATE_TRANSPORTS = ("regular-non-pro-oracle",)
FINAL_GATE_MIN_EVIDENCE = 16
FINAL_GATE_RECEIPT_SCHEMA = "codex.devspace.tool-read-receipt/v1"
FINAL_GATE_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "receiptId",
        "auditNonce",
        "auditStep",
        "tool",
        "workspaceId",
        "canonicalRoot",
        "requestedRelativePath",
        "readChunkSha256",
        "readChunkOffsetBytes",
        "readChunkBytesReturned",
        "readChunkTotalBytes",
        "readChunkEof",
        "conversationScopeId",
        "timestamp",
    }
)
FINAL_GATE_RECEIPT_TOOLS = ("open_workspace", "read", "read_chunk")
FINAL_GATE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
FINAL_GATE_RECEIPT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
FINAL_GATE_MANIFEST_SCHEMA = "codex.chatgpt.oracle-run/v1"
FINAL_GATE_MANIFEST_RELATIVE = (
    Path("state") / "codex-web-gpt-automation" / "onboarding" / "final-gate-manifests"
)
SOURCE_THREAD_ID_RE = re.compile(
    r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE
)
COMPLETION_STATES_BY_LANGUAGE = {
    "ko": {
        "installed": "로컬 설치·연결 설정 진행 중",
        "awaiting_chatgpt": "ChatGPT 연결 대기",
        "awaiting_verification": "앱 등록 사용자 확인·기능 검증 대기",
        "verified": "전체 설치 및 실제 프로젝트 연결 검증 완료",
    },
    "en": {
        "installed": "Local install and connection setup in progress",
        "awaiting_chatgpt": "Awaiting ChatGPT connection",
        "awaiting_verification": "App registration user-confirmed; awaiting functional verification",
        "verified": "Full install and real project-root read verified",
    },
}
COMPLETION_STATES = COMPLETION_STATES_BY_LANGUAGE["ko"]


class OnboardingError(ValueError):
    pass


def normalize_app_name(value: str) -> str:
    name = value.strip()
    if (
        not name
        or len(name) > 64
        or name.startswith("@")
        or any(ord(character) < 32 or character in "@/\\" for character in name)
    ):
        raise OnboardingError("APP_NAME_INVALID")
    return name


def _is_volume_root(path: Path) -> bool:
    return bool(path.anchor) and path == Path(path.anchor)


def normalize_roots(values: Sequence[str]) -> tuple[Path, ...]:
    if not values:
        raise OnboardingError("ALLOWED_ROOT_REQUIRED")
    roots: list[Path] = []
    identities: set[str] = set()
    for value in values:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise OnboardingError("ALLOWED_ROOT_ABSOLUTE_REQUIRED")
        if not path.is_dir():
            raise OnboardingError("ALLOWED_ROOT_NOT_DIRECTORY")
        path = path.resolve()
        if _is_volume_root(path):
            raise OnboardingError("ALLOWED_ROOT_TOO_BROAD")
        identity = os.path.normcase(str(path))
        if identity not in identities:
            identities.add(identity)
            roots.append(path)
    return tuple(roots)


def normalize_registration_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise OnboardingError("PUBLIC_HTTPS_MCP_URL_REQUIRED")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OnboardingError("PUBLIC_MCP_URL_MUST_NOT_CONTAIN_CREDENTIALS_OR_QUERY")
    if parsed.path.rstrip("/") != "/mcp":
        raise OnboardingError("PUBLIC_MCP_URL_MUST_END_IN_MCP")
    return urllib.parse.urlunsplit(("https", parsed.netloc, "/mcp", "", ""))


def validate_provider_url(provider: str, registration_url: str) -> None:
    if provider not in PROVIDERS:
        raise OnboardingError("TUNNEL_PROVIDER_UNSUPPORTED")
    hostname = (urllib.parse.urlsplit(registration_url).hostname or "").casefold()
    if provider == "tailscale" and not hostname.endswith(".ts.net"):
        raise OnboardingError("TAILSCALE_STABLE_TS_NET_URL_REQUIRED")
    if provider == "cloudflare" and hostname.endswith(".trycloudflare.com"):
        raise OnboardingError("CLOUDFLARE_NAMED_TUNNEL_REQUIRED")
    if provider == "ngrok" and hostname.endswith(".ngrok-free.app"):
        raise OnboardingError("NGROK_STATIC_DOMAIN_REQUIRED")


def public_origin(registration_url: str) -> str:
    parsed = urllib.parse.urlsplit(registration_url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _quoted_command(argv: Sequence[str]) -> str:
    def quote(value: str) -> str:
        return f'"{value}"' if any(ch.isspace() for ch in value) else value

    return " ".join(quote(value) for value in argv)


def onboarding_plan(
    *,
    provider: str,
    registration_url: str,
    roots: Sequence[str],
    app_name: str = APP_NAME,
    python_executable: str = "python",
    enable_local_multi_gpt: bool = False,
) -> dict[str, Any]:
    normalized_roots = normalize_roots(roots)
    registration_url = normalize_registration_url(registration_url)
    validate_provider_url(provider, registration_url)
    app_name = normalize_app_name(app_name)
    root_args = [part for root in normalized_roots for part in ("--root", str(root))]
    setup_script = "skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py"
    host = urllib.parse.urlsplit(registration_url).hostname or ""
    if provider == "tailscale":
        tunnel_commands = {
            "preview": _quoted_command(
                [python_executable, setup_script, "setup", *root_args, "--hostname", host, "--dry-run"]
            ),
            "apply": _quoted_command(
                [python_executable, setup_script, "setup", *root_args, "--hostname", host, "--apply"]
            ),
            "doctor": _quoted_command(
                [python_executable, setup_script, "doctor", *root_args, "--hostname", host]
            ),
        }
    else:
        tunnel_commands = {
            "preview": "Start a stable provider-managed HTTPS tunnel to http://127.0.0.1:7676.",
            "apply": "Persist that tunnel as an OS login service before registering the ChatGPT app.",
            "doctor": f"Verify that {registration_url} returns an OAuth challenge (normally HTTP 401).",
        }
    profile = str(Path.home() / ".oracle" / "browser-profile")
    install_flag = " --enable-local-multi-gpt" if enable_local_multi_gpt else ""
    stages = [
        {
            "id": "01_install",
            "owner": "agent",
            "complete_when": "receipt-backed install and doctor both succeed",
            "commands": [
                f"{python_executable} install.py --dry-run{install_flag}",
                f"{python_executable} install.py{install_flag}",
                f"{python_executable} doctor.py",
            ],
        },
        {
            "id": "02_stable_endpoint",
            "owner": "agent_then_user_approval",
            "complete_when": "the chosen stable HTTPS /mcp URL is fixed before app registration",
            "commands": tunnel_commands,
        },
        {
            "id": "03_devspace_init",
            "owner": "user_interactive_secret",
            "complete_when": "all exact roots and the public origin are persisted; Owner password is stored only by DevSpace",
            "public_origin": public_origin(registration_url),
            "allowed_roots": [str(root) for root in normalized_roots],
            "secret_rule": "Never pass, print, copy, or commit the DevSpace Owner password.",
            "command": tunnel_commands["apply"],
        },
        {
            "id": "04_reboot_service",
            "owner": "agent",
            "complete_when": (
                "the per-user login watchdog is registered and continuously restores DevSpace "
                "and the stable tunnel with identical roots"
            ),
            "windows_watchdog": {
                "registered_by": "the Tailscale setup --apply command",
                "mode": "Watch",
                "health_interval_seconds": 300,
                "root_source": "%USERPROFILE%\\.devspace\\config.json",
            },
            "environment": {
                "DEVSPACE_TOOL_MODE": "full",
                "DEVSPACE_OAUTH_SCOPES": "devspace,offline_access",
                "DEVSPACE_SUBAGENTS": "false",
            },
        },
        {
            "id": "05_endpoint_check",
            "owner": "agent",
            "complete_when": "local and public /mcp endpoints both return an OAuth challenge and roots match bootstrap",
            "local_url": f"http://127.0.0.1:{DEFAULT_LOCAL_PORT}/mcp",
            "public_url": registration_url,
            "healthy_http_statuses": [401],
        },
        {
            "id": "06_oracle_login",
            "owner": "user_interactive_login",
            "complete_when": "the dedicated Oracle profile is signed in to ChatGPT once",
            "command": _quoted_command(
                [
                    "npx",
                    "--yes",
                    "@steipete/oracle@0.18.0",
                    "--engine",
                    "browser",
                    "--browser-manual-login",
                    "--browser-keep-browser",
                    "--browser-manual-login-profile-dir",
                    profile,
                    "-p",
                    "HI",
                ]
            ),
        },
        {
            "id": "06b_local_network_access",
            "owner": "agent_after_explicit_user_consent",
            "complete_when": (
                "chatgpt.com has a persistent Local Network Access grant before disposable Oracle profiles are copied"
            ),
            "windows_command": f"{python_executable} bin/chatgpt_chrome_local_network.py enable",
            "status_command": f"{python_executable} bin/chatgpt_chrome_local_network.py status",
            "scope": "exact origin https://chatgpt.com only; preserve unrelated Chrome policy entries",
            "non_windows": "Grant Local network once in the dedicated persistent Oracle browser profile, then fully exit Chrome.",
        },
        {
            "id": "07_chatgpt_app",
            "owner": "user_manual_chatgpt_ui",
            "complete_when": "ChatGPT discovers the tools and Owner approval succeeds",
            "app_name": app_name,
            "mcp_url": registration_url,
            "configure_command": _quoted_command(
                [python_executable, "onboard.py", "configure-app-name", "--app-name", app_name]
            ),
            "rule": "Do not automate ChatGPT settings, app creation, permissions, or tool selection.",
        },
        {
            "id": "08_final_gate",
            "owner": "agent",
            "complete_when": "status is ready and first exact project-root qualification passes before submission",
            "command": f"{python_executable} onboard.py status --provider {provider} --public-url {registration_url} "
            + " ".join(_quoted_command(["--root", str(root)]) for root in normalized_roots)
            + " "
            + _quoted_command(["--app-name", app_name]),
        },
    ]
    return {
        "schema": "codex-web-gpt.onboarding-plan/v1",
        "product": PRODUCT_NAME,
        "compatibility": "codexpro-* state, receipt, schema, and recovery identifiers remain stable legacy IDs",
        "provider": provider,
        "app_name": app_name,
        "registration_url": registration_url,
        "public_origin": public_origin(registration_url),
        "allowed_roots": [str(root) for root in normalized_roots],
        "enable_local_multi_gpt": bool(enable_local_multi_gpt),
        "stages": stages,
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _persisted_allowed_roots(devspace_home: Path) -> tuple[str, ...]:
    """Read the DevSpace source-of-truth roots without silently dropping bad state."""
    path = devspace_home / "config.json"
    if not path.exists():
        return ()
    value = _load_json(path)
    if value is None:
        raise OnboardingError("DEVSPACE_CONFIG_INVALID")
    roots = value.get("allowedRoots")
    if not isinstance(roots, list) or not all(isinstance(item, str) and item.strip() for item in roots):
        raise OnboardingError("DEVSPACE_CONFIG_ROOTS_INVALID")
    return tuple(roots)


def browser_profile_local_network_allowed(profile_dir: Path) -> bool:
    preferences = _load_json(profile_dir / "Default" / "Preferences") or {}
    exceptions = (
        preferences.get("profile", {})
        .get("content_settings", {})
        .get("exceptions", {})
        .get("local_network", {})
    )
    if not isinstance(exceptions, dict):
        return False
    expected = "https://chatgpt.com:443,*"
    return any(
        str(pattern).casefold() == expected
        and isinstance(entry, dict)
        and entry.get("setting") == 1
        for pattern, entry in exceptions.items()
    )


def _root_identities(values: Sequence[Any]) -> list[str]:
    return [os.path.normcase(str(Path(str(value)).expanduser().resolve())) for value in values]


def probe_http(url: str, timeout: float = 5.0) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
    except (OSError, urllib.error.URLError) as exc:
        return {"ok": False, "status": None, "error": type(exc).__name__}
    return {"ok": status == 401, "status": status, "expected": 401}


def readiness_status(
    *,
    provider: str,
    registration_url: str,
    roots: Sequence[str],
    app_name: str = APP_NAME,
    codex_home: Path | None = None,
    devspace_home: Path | None = None,
    http_probe: Any = probe_http,
    oracle_profile_dir: Path | None = None,
    local_network_policy_probe: Any = policy_status,
) -> dict[str, Any]:
    plan = onboarding_plan(
        provider=provider,
        registration_url=registration_url,
        roots=roots,
        app_name=app_name,
    )
    codex_home = (codex_home or Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))).resolve()
    devspace_home = (devspace_home or (Path.home() / ".devspace")).resolve()
    devspace_config = _load_json(devspace_home / "config.json") or {}
    bootstrap = _load_json(codex_home / "config" / "codexpro-devspace-bootstrap.json") or {}
    workspace = _load_json(codex_home / "chatgpt-workspace.json") or {}
    desired = _root_identities(plan["allowed_roots"])
    configured = _root_identities(devspace_config.get("allowedRoots") or [])
    bootstrapped = _root_identities(bootstrap.get("roots") or [])
    exact_roots_configured = desired == configured
    bootstrap_matches = configured == bootstrapped
    local = http_probe(f"http://127.0.0.1:{DEFAULT_LOCAL_PORT}/mcp")
    public = http_probe(plan["registration_url"])
    browser_profile = (oracle_profile_dir or (Path.home() / ".oracle" / "browser-profile")).resolve()
    browser_profile_initialized = browser_profile.is_dir() and any(browser_profile.iterdir())
    local_network_policy = local_network_policy_probe()
    local_network_allowed = bool(local_network_policy.get("enabled")) or browser_profile_local_network_allowed(
        browser_profile
    )
    checks = {
        "exact_roots_configured": exact_roots_configured,
        "bootstrap_matches_config": bootstrap_matches,
        "app_name_matches_expected": workspace.get("app_name") == plan["app_name"],
        "local_mcp_oauth_challenge": bool(local.get("ok")),
        "public_mcp_oauth_challenge": bool(public.get("ok")),
        "oracle_profile_initialized": browser_profile_initialized,
        "chatgpt_local_network_allowed": local_network_allowed,
    }
    return {
        "schema": "codex-web-gpt.onboarding-status/v1",
        "ready": all(checks.values()),
        "checks": checks,
        "registration_url": plan["registration_url"],
        "expected_app_name": plan["app_name"],
        "configured_app_name": workspace.get("app_name"),
        "configured_roots": [str(value) for value in devspace_config.get("allowedRoots") or []],
        "bootstrap_roots": [str(value) for value in bootstrap.get("roots") or []],
        "local_endpoint": local,
        "public_endpoint": public,
        "chatgpt_local_network": local_network_policy,
        "next_action": "READY" if all(checks.values()) else "COMPLETE_THE_FIRST_FAILED_STAGE_IN_PLAN",
    }


def configure_app_name(*, codex_home: Path | None = None, app_name: str = APP_NAME) -> Path:
    app_name = normalize_app_name(app_name)
    root = (codex_home or Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / "chatgpt-workspace.json"
    payload = json.dumps({"app_name": app_name}, ensure_ascii=True, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return target


def _codex_home(codex_home: Path | None = None) -> Path:
    return (codex_home or Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))).expanduser().resolve()


def state_path(*, codex_home: Path | None = None) -> Path:
    return _codex_home(codex_home) / STATE_RELATIVE


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_state(state: dict[str, Any], *, codex_home: Path | None = None) -> Path:
    target = state_path(codex_home=codex_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return target


def _write_json_atomic(target: Path, value: Mapping[str, Any]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return target


def load_state(*, codex_home: Path | None = None) -> dict[str, Any]:
    value = _load_json(state_path(codex_home=codex_home))
    if not value:
        raise OnboardingError("ONBOARDING_NOT_STARTED")
    if value.get("schema") != STATE_SCHEMA:
        raise OnboardingError("ONBOARDING_STATE_CORRUPT")
    stages = value.get("stages")
    if not isinstance(stages, dict) or set(stages) != set(STAGE_IDS):
        raise OnboardingError("ONBOARDING_STATE_CORRUPT")
    for name in ("provider", "registration_url", "app_name"):
        if not isinstance(value.get(name), str) or not value[name].strip():
            raise OnboardingError("ONBOARDING_STATE_CORRUPT")
    roots = value.get("allowed_roots")
    if not isinstance(roots, list) or not roots or not all(isinstance(item, str) and item.strip() for item in roots):
        raise OnboardingError("ONBOARDING_STATE_CORRUPT")
    for stage_id, stage in stages.items():
        if not isinstance(stage, dict):
            raise OnboardingError("ONBOARDING_STATE_CORRUPT")
        stage.setdefault("status", "pending")
        stage.setdefault("verified_at", None)
        stage.setdefault("evidence", None)
    value.setdefault("requested_roots", list(roots))
    value.setdefault("enable_local_multi_gpt", False)
    return value


def _secret_free(state: dict[str, Any]) -> None:
    dumped = json.dumps(state, ensure_ascii=False).casefold()
    for banned in ("password", "secret", "token", "cookie", "authorization"):
        if banned in dumped:
            raise OnboardingError("ONBOARDING_STATE_MUST_NOT_CARRY_SECRETS")


def start_onboarding(
    *,
    provider: str,
    roots: Sequence[str],
    registration_url: str | None = None,
    app_name: str = APP_NAME,
    codex_home: Path | None = None,
    devspace_home: Path | None = None,
    hostname_discovery: Any = None,
    enable_local_multi_gpt: bool = False,
) -> dict[str, Any]:
    """Create resumable, secret-free onboarding state and return the first step."""
    if provider not in PROVIDERS:
        raise OnboardingError("TUNNEL_PROVIDER_UNSUPPORTED")
    resolved_url = (registration_url or "").strip()
    if not resolved_url:
        if provider != "tailscale":
            raise OnboardingError("PUBLIC_HTTPS_MCP_URL_REQUIRED")
        discover = hostname_discovery or _discover_tailscale_hostname
        resolved_url = f"https://{discover()}/mcp"
    requested_roots = normalize_roots(roots)
    resolved_devspace_home = (devspace_home or (Path.home() / ".devspace")).expanduser().resolve()
    merged_roots = normalize_roots([
        *_persisted_allowed_roots(resolved_devspace_home),
        *(str(root) for root in requested_roots),
    ])
    plan = onboarding_plan(
        provider=provider,
        registration_url=resolved_url,
        roots=[str(root) for root in merged_roots],
        app_name=app_name,
        enable_local_multi_gpt=enable_local_multi_gpt,
    )
    state = {
        "schema": STATE_SCHEMA,
        "product": PRODUCT_NAME,
        "provider": provider,
        "registration_url": plan["registration_url"],
        "public_origin": plan["public_origin"],
        "allowed_roots": plan["allowed_roots"],
        "requested_roots": [str(root) for root in requested_roots],
        "app_name": plan["app_name"],
        "enable_local_multi_gpt": bool(enable_local_multi_gpt),
        "started_at": _now(),
        "updated_at": _now(),
        "completion_state": "installed",
        "stages": {
            stage_id: {"status": "pending", "verified_at": None, "evidence": None}
            for stage_id in STAGE_IDS
        },
    }
    _secret_free(state)
    _write_state(state, codex_home=codex_home)
    return state


def _discover_tailscale_hostname() -> str:
    executable = shutil.which("tailscale")
    if executable is None:
        raise OnboardingError("TAILSCALE_NOT_INSTALLED")
    try:
        completed = subprocess.run(
            [executable, "status", "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        value = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise OnboardingError("TAILSCALE_HOSTNAME_UNAVAILABLE") from exc
    hostname = str((value.get("Self") or {}).get("DNSName") or "").strip().casefold().rstrip(".")
    if not hostname.endswith(".ts.net"):
        raise OnboardingError("TAILSCALE_HOSTNAME_UNAVAILABLE")
    return hostname


STAGE_VERIFIERS: dict[str, str] = {
    "01_install": "installation_contract_satisfied",
    "02_stable_endpoint": "stable_endpoint_user_confirmed",
    "03_devspace_init": "exact_roots_configured",
    "04_reboot_service": "restart_persistence_verified",
    "05_endpoint_check": "local_mcp_oauth_challenge",
    "06_oracle_login": "oracle_login_confirmed",
    "06b_local_network_access": "chatgpt_local_network_allowed",
    "07_chatgpt_app": "chatgpt_app_registration_confirmed",
    "08_final_gate": "exact_root_read_verified",
}


def _stage_user_confirmed(state: dict[str, Any], stage_id: str) -> bool:
    evidence = ((state.get("stages") or {}).get(stage_id) or {}).get("evidence")
    return bool(
        isinstance(evidence, dict)
        and evidence.get("kind") == "user-confirmed"
        and isinstance(evidence.get("confirmed_at"), str)
        and evidence["confirmed_at"].strip()
    )


def _stage_user_consented(state: dict[str, Any], stage_id: str) -> bool:
    evidence = ((state.get("stages") or {}).get(stage_id) or {}).get("evidence")
    return bool(
        isinstance(evidence, dict)
        and evidence.get("kind") == "user-consent"
        and isinstance(evidence.get("confirmed_at"), str)
        and evidence["confirmed_at"].strip()
    )


def _local_multi_gpt_ready(codex_home: Path) -> bool:
    try:
        result = LOCAL_MULTI_GPT_SETUP.doctor(codex_home)
    except (OSError, subprocess.SubprocessError, LOCAL_MULTI_GPT_SETUP.SetupError):
        return False
    return bool(result.get("ok") and result.get("enabled") and result.get("registered_exactly"))


def _install_receipt_present(codex_home: Path) -> bool:
    receipts = codex_home / "receipts"
    if not receipts.is_dir():
        return False
    return any(receipts.glob("codexpro-automation-*.json"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _conversation_url(run_state: dict[str, Any]) -> str:
    oracle = run_state.get("oracle") if isinstance(run_state.get("oracle"), dict) else {}
    browser = run_state.get("browser") if isinstance(run_state.get("browser"), dict) else {}
    runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
    harvest = browser.get("harvest") if isinstance(browser.get("harvest"), dict) else {}
    for value in (
        oracle.get("conversation_url"),
        runtime.get("tabUrl"),
        harvest.get("url"),
    ):
        candidate = str(value or "").strip()
        parsed = urllib.parse.urlsplit(candidate)
        if parsed.scheme == "https" and parsed.hostname == "chatgpt.com" and parsed.path.startswith("/c/"):
            return candidate
    return ""


def _receipt_timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_TIMESTAMP_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_TIMESTAMP_INVALID")
    return parsed


def _receipt_directory(devspace_home: Path) -> Path:
    return devspace_home.expanduser().resolve() / "state" / "tool-read-receipts"


def _strict_receipt_json(payload: str) -> dict[str, Any]:
    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    decoded = json.loads(payload, object_pairs_hook=no_duplicate_keys)
    if not isinstance(decoded, dict):
        raise ValueError("receipt JSON must be an object")
    return decoded


def _tool_read_receipts(
    *,
    devspace_home: Path,
    audit_nonce: str,
    expected_root: Path,
    mission_path: Path,
    mission_sha256: str,
) -> dict[str, Any]:
    receipt_directory = _receipt_directory(devspace_home)
    if receipt_directory.is_symlink() or not receipt_directory.is_dir():
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_DIRECTORY_INVALID")
    matching: list[dict[str, Any]] = []
    try:
        entries = sorted(receipt_directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_DIRECTORY_INVALID") from exc
    for path in entries:
        if path.is_symlink():
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_SYMLINK_FORBIDDEN")
        if path.suffix.casefold() != ".json":
            continue
        if not path.is_file():
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_NOT_REGULAR")
        try:
            payload = _strict_receipt_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_INVALID") from exc
        if set(payload) != FINAL_GATE_RECEIPT_KEYS:
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_KEYSET_INVALID")
        if payload.get("schema") != FINAL_GATE_RECEIPT_SCHEMA:
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_SCHEMA_INVALID")
        if not isinstance(payload.get("receiptId"), str) or not FINAL_GATE_RECEIPT_ID_RE.fullmatch(payload["receiptId"]):
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_ID_INVALID")
        if not isinstance(payload.get("auditNonce"), str) or not isinstance(payload.get("tool"), str):
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_INVALID")
        if (
            not isinstance(payload.get("auditStep"), int)
            or isinstance(payload.get("auditStep"), bool)
            or payload["auditStep"] not in (1, 2, 3)
        ):
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_STEP_INVALID")
        if not isinstance(payload.get("workspaceId"), str) or not payload["workspaceId"].strip():
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_WORKSPACE_INVALID")
        if not isinstance(payload.get("canonicalRoot"), str) or not payload["canonicalRoot"].strip():
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_ROOT_INVALID")
        if payload.get("requestedRelativePath") is not None and not isinstance(payload.get("requestedRelativePath"), str):
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_PATH_INVALID")
        if payload.get("readChunkSha256") is not None and (
            not isinstance(payload.get("readChunkSha256"), str)
            or not FINAL_GATE_SHA256_RE.fullmatch(payload["readChunkSha256"])
        ):
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_SHA_INVALID")
        if not isinstance(payload.get("conversationScopeId"), str) or not payload["conversationScopeId"].strip():
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_SCOPE_INVALID")
        parsed_timestamp = _receipt_timestamp(payload.get("timestamp"))
        if payload["auditNonce"] == audit_nonce:
            matching.append(
                {
                    "path": path,
                    "sha256": _sha256_file(path),
                    "payload": payload,
                    "timestamp": parsed_timestamp,
                }
            )
    if len(matching) != len(FINAL_GATE_RECEIPT_TOOLS):
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPTS_MISSING_OR_DUPLICATE")
    by_tool: dict[str, dict[str, Any]] = {}
    for item in matching:
        tool = item["payload"]["tool"]
        if tool not in FINAL_GATE_RECEIPT_TOOLS or tool in by_tool:
            raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPTS_MISSING_OR_DUPLICATE")
        by_tool[tool] = item
    if set(by_tool) != set(FINAL_GATE_RECEIPT_TOOLS):
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPTS_MISSING_OR_DUPLICATE")
    ordered = [by_tool[tool] for tool in FINAL_GATE_RECEIPT_TOOLS]
    if [item["payload"]["auditStep"] for item in ordered] != [1, 2, 3]:
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_ORDER_INVALID")
    workspace_ids = {item["payload"]["workspaceId"] for item in ordered}
    canonical_roots = {item["payload"]["canonicalRoot"] for item in ordered}
    scopes = {item["payload"]["conversationScopeId"] for item in ordered}
    expected_canonical_root = str(expected_root.expanduser().resolve())
    if len(workspace_ids) != 1:
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_WORKSPACE_MISMATCH")
    if len(canonical_roots) != 1 or os.path.normcase(next(iter(canonical_roots))) != os.path.normcase(expected_canonical_root):
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_ROOT_MISMATCH")
    if len(scopes) != 1:
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_SCOPE_MISMATCH")
    # `openai/session` is an opaque per-conversation scope (not the public
    # ChatGPT /c/<id>). DevSpace enforces that auditNonce open_workspace is the
    # first workspace/process/mutation action in that scope and issues the exact
    # 1→2→3 sequence. The server-generated receipt IDs are returned only through
    # those tool responses; the terminal Oracle conversation must echo all three
    # below, which challenge-binds the opaque scope to the public conversation.
    mission_relative = mission_path.relative_to(expected_root).as_posix()
    if ordered[0]["payload"]["requestedRelativePath"] is not None:
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_PATH_MISMATCH")
    if any(item["payload"]["requestedRelativePath"] != mission_relative for item in ordered[1:]):
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_PATH_MISMATCH")
    if ordered[0]["payload"]["readChunkSha256"] is not None or ordered[1]["payload"]["readChunkSha256"] is not None:
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_SHA_MISMATCH")
    chunk_fields = (
        "readChunkOffsetBytes",
        "readChunkBytesReturned",
        "readChunkTotalBytes",
        "readChunkEof",
    )
    if any(item["payload"][field] is not None for item in ordered[:2] for field in chunk_fields):
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_CHUNK_METADATA_INVALID")
    chunk = ordered[2]["payload"]
    numeric_chunk_fields = (
        "readChunkOffsetBytes",
        "readChunkBytesReturned",
        "readChunkTotalBytes",
    )
    if any(
        not isinstance(chunk[field], int) or isinstance(chunk[field], bool) or chunk[field] < 0
        for field in numeric_chunk_fields
    ) or not isinstance(chunk["readChunkEof"], bool):
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_CHUNK_METADATA_INVALID")
    if (
        chunk["readChunkOffsetBytes"] != 0
        or chunk["readChunkEof"] is not True
        or chunk["readChunkBytesReturned"] != chunk["readChunkTotalBytes"]
    ):
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_CHUNK_METADATA_INVALID")
    if str(ordered[2]["payload"]["readChunkSha256"]).casefold() != mission_sha256.casefold():
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_SHA_MISMATCH")
    return {
        "workspace_id": next(iter(workspace_ids)),
        "conversation_scope_id": next(iter(scopes)),
        "tool_read_receipts": [
            {
                "tool": item["payload"]["tool"],
                "receipt_id": item["payload"]["receiptId"],
                "path": str(item["path"]),
                "sha256": item["sha256"],
            }
            for item in ordered
        ],
    }


def _oracle_final_gate_binding(
    *,
    codex_home: Path,
    devspace_home: Path,
    run_dir: Path,
    expected_root: str,
    expected_app_name: str,
    listing: Sequence[str],
    require_current_task: bool = False,
) -> dict[str, Any]:
    try:
        directory = run_dir.expanduser().resolve(strict=True)
        state_root = (codex_home / "state").resolve(strict=True)
    except OSError as exc:
        raise OnboardingError("FINAL_GATE_ORACLE_RUN_UNREADABLE") from exc
    if not directory.is_dir() or not _inside(state_root, directory):
        raise OnboardingError("FINAL_GATE_ORACLE_RUN_OUTSIDE_CODEX_STATE")
    state_path = directory / "state.json"
    try:
        run_state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OnboardingError("FINAL_GATE_ORACLE_STATE_INVALID") from exc
    if not isinstance(run_state, dict) or run_state.get("schema") != "codex.chatgpt.oracle-run-state/v1":
        raise OnboardingError("FINAL_GATE_ORACLE_STATE_INVALID")
    exact_root = str(Path(str(run_state.get("project_root") or "")).expanduser().resolve())
    if os.path.normcase(exact_root) != os.path.normcase(str(Path(expected_root).expanduser().resolve())):
        raise OnboardingError("FINAL_GATE_ORACLE_ROOT_MISMATCH")
    profile = run_state.get("profile") if isinstance(run_state.get("profile"), dict) else {}
    terminal = (
        run_state.get("transport") == "devspace"
        and run_state.get("app_name") == expected_app_name
        and profile.get("model") == "gpt-5.6"
        and profile.get("thinking_time") == "extra-high"
        and run_state.get("status") == "complete"
        and run_state.get("transport_status") == "complete"
        and run_state.get("session_authority") == "terminal"
        and run_state.get("terminal_harvested") is True
        and run_state.get("task_outcome") == "executed"
    )
    if not terminal:
        raise OnboardingError("FINAL_GATE_REGULAR_NON_PRO_ORACLE_NOT_TERMINAL_EXECUTED")
    registered_app_final_gate = run_state.get("registered_app_final_gate") is True
    ownership = run_state.get("ownership") if isinstance(run_state.get("ownership"), dict) else {}
    source_thread_id = str(ownership.get("source_thread_id") or "").strip()
    if registered_app_final_gate and SOURCE_THREAD_ID_RE.fullmatch(source_thread_id) is None:
        raise OnboardingError("FINAL_GATE_SOURCE_TASK_BINDING_MISSING")
    if registered_app_final_gate and require_current_task:
        evaluated_from_thread = str(os.environ.get("CODEX_THREAD_ID") or "").strip()
        if SOURCE_THREAD_ID_RE.fullmatch(evaluated_from_thread) is None:
            raise OnboardingError("FINAL_GATE_CURRENT_TASK_BINDING_REQUIRED")
        if source_thread_id.casefold() != evaluated_from_thread.casefold():
            raise OnboardingError("FINAL_GATE_FOREIGN_TASK_RUN")
    artifacts = run_state.get("artifacts") if isinstance(run_state.get("artifacts"), dict) else {}
    try:
        output_path = Path(str(artifacts.get("output") or "")).expanduser().resolve(strict=True)
    except OSError as exc:
        raise OnboardingError("FINAL_GATE_ORACLE_OUTPUT_MISSING") from exc
    if not output_path.is_file() or not _inside(directory, output_path):
        raise OnboardingError("FINAL_GATE_ORACLE_OUTPUT_INVALID")
    output_bytes = output_path.read_bytes()
    if not output_bytes.strip():
        raise OnboardingError("FINAL_GATE_ORACLE_OUTPUT_INVALID")
    try:
        output = output_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OnboardingError("FINAL_GATE_ORACLE_OUTPUT_INVALID") from exc
    output_sha256 = hashlib.sha256(output_bytes).hexdigest()
    if run_state.get("artifact_sha256") != output_sha256:
        raise OnboardingError("FINAL_GATE_ORACLE_OUTPUT_HASH_MISMATCH")
    nonempty = [line.strip() for line in output.splitlines() if line.strip()]
    if not nonempty or nonempty[-1] != "TASK_OUTCOME: EXECUTED":
        raise OnboardingError("FINAL_GATE_ORACLE_OUTCOME_MARKER_INVALID")
    folded = output.casefold()
    entries = [str(item).strip() for item in listing if str(item).strip()]
    if not entries or any(entry.casefold() not in folded for entry in entries):
        raise OnboardingError("FINAL_GATE_LISTING_NOT_BOUND_TO_ORACLE_OUTPUT")
    if expected_app_name.casefold() not in folded:
        raise OnboardingError("FINAL_GATE_CONNECTOR_IDENTITY_MISSING")
    mission = run_state.get("mission") if isinstance(run_state.get("mission"), dict) else {}
    try:
        mission_path = Path(str(mission.get("path") or "")).expanduser().resolve(strict=True)
    except OSError as exc:
        raise OnboardingError("FINAL_GATE_READ_PROOF_MISSION_UNREADABLE") from exc
    root_path = Path(expected_root).expanduser().resolve()
    if not mission_path.is_file() or not _inside(root_path, mission_path):
        raise OnboardingError("FINAL_GATE_READ_PROOF_MISSION_INVALID")
    mission_sha256 = _sha256_file(mission_path)
    if mission.get("sha256") != mission_sha256:
        raise OnboardingError("FINAL_GATE_READ_PROOF_MISSION_HASH_MISMATCH")
    run_id = str(run_state.get("run_id") or "").strip()
    if not run_id:
        raise OnboardingError("FINAL_GATE_TOOL_READ_RECEIPT_AUDIT_NONCE_MISSING")
    conversation_url = _conversation_url(run_state)
    if not conversation_url:
        raise OnboardingError("FINAL_GATE_CONVERSATION_BINDING_MISSING")
    parsed_conversation = urllib.parse.urlsplit(conversation_url)
    match = re.fullmatch(r"/c/([^/]+)", parsed_conversation.path)
    if not match:
        raise OnboardingError("FINAL_GATE_CONVERSATION_BINDING_MISSING")
    receipt_binding = _tool_read_receipts(
        devspace_home=devspace_home,
        audit_nonce=run_id,
        expected_root=root_path,
        mission_path=mission_path,
        mission_sha256=mission_sha256,
    )
    receipt_ids = [item["receipt_id"] for item in receipt_binding["tool_read_receipts"]]
    if any(receipt_id not in output for receipt_id in receipt_ids):
        raise OnboardingError("FINAL_GATE_CONVERSATION_RECEIPT_CHALLENGE_MISSING")
    oracle = run_state.get("oracle") if isinstance(run_state.get("oracle"), dict) else {}
    return {
        "run_dir": str(directory),
        "run_id": str(run_state.get("run_id") or ""),
        "slug": str(oracle.get("slug") or ""),
        "conversation_url": conversation_url,
        "state_sha256": _sha256_file(state_path),
        "output_path": str(output_path),
        "output_sha256": output_sha256,
        "workspace_id": receipt_binding["workspace_id"],
        "conversation_scope_id": receipt_binding["conversation_scope_id"],
        "tool_read_receipts": receipt_binding["tool_read_receipts"],
        "separate_read_verified": True,
        "read_file_path": str(mission_path),
        "read_file_sha256": mission_sha256,
        "cryptographic_read_verified": True,
        **(
            {
                "registered_app_final_gate": True,
                "source_thread_id": source_thread_id,
            }
            if registered_app_final_gate
            else {}
        ),
        "transport": "regular-non-pro-oracle",
    }


def _final_gate_receipt(
    codex_home: Path,
    devspace_home: Path,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    recorded = ((state.get("stages") or {}).get("08_final_gate") or {}).get("evidence")
    if not isinstance(recorded, dict):
        return None
    if recorded.get("transport") not in FINAL_GATE_TRANSPORTS or recorded.get("read_ok") is not True:
        return None
    summary = str(recorded.get("evidence") or "").strip()
    listing = recorded.get("listing_sample")
    entries = [str(item).strip() for item in listing if str(item).strip()] if isinstance(listing, list) else []
    root = str(recorded.get("root") or "").strip()
    identities = _root_identities(state.get("allowed_roots") or [])
    if not root or os.path.normcase(str(Path(root).expanduser())) not in identities:
        return None
    if len(summary) < FINAL_GATE_MIN_EVIDENCE or not entries:
        return None
    if not isinstance(recorded.get("recorded_at"), str) or not recorded["recorded_at"].strip():
        return None
    try:
        binding = _oracle_final_gate_binding(
            codex_home=codex_home,
            devspace_home=devspace_home,
            run_dir=Path(str(recorded.get("run_dir") or "")),
            expected_root=root,
            expected_app_name=str(state.get("app_name") or ""),
            listing=entries,
        )
    except OnboardingError:
        return None
    for key, value in binding.items():
        if recorded.get(key) != value:
            return None
    return recorded


def evaluate_stages(
    state: dict[str, Any],
    *,
    codex_home: Path | None = None,
    devspace_home: Path | None = None,
    http_probe: Any = probe_http,
    oracle_profile_dir: Path | None = None,
    local_network_policy_probe: Any = policy_status,
) -> dict[str, Any]:
    """Recompute every stage from live evidence instead of trusting confirmations."""
    resolved_home = _codex_home(codex_home)
    status = readiness_status(
        provider=state["provider"],
        registration_url=state["registration_url"],
        roots=state["allowed_roots"],
        app_name=state["app_name"],
        codex_home=resolved_home,
        devspace_home=devspace_home,
        http_probe=http_probe,
        oracle_profile_dir=oracle_profile_dir,
        local_network_policy_probe=local_network_policy_probe,
    )
    checks = dict(status["checks"])
    checks["install_receipt_present"] = _install_receipt_present(resolved_home)
    checks["local_multi_gpt_ready"] = (
        not bool(state.get("enable_local_multi_gpt")) or _local_multi_gpt_ready(resolved_home)
    )
    checks["installation_contract_satisfied"] = bool(
        checks["install_receipt_present"] and checks["local_multi_gpt_ready"]
    )
    checks["stable_endpoint_user_confirmed"] = _stage_user_confirmed(state, "02_stable_endpoint")
    checks["restart_persistence_verified"] = bool(
        checks.get("bootstrap_matches_config")
        and (
            state.get("provider") == "tailscale"
            or _stage_user_confirmed(state, "04_reboot_service")
        )
    )
    checks["oracle_login_confirmed"] = bool(
        checks.get("oracle_profile_initialized") and _stage_user_confirmed(state, "06_oracle_login")
    )
    checks["chatgpt_app_registration_confirmed"] = bool(
        checks.get("app_name_matches_expected") and _stage_user_confirmed(state, "07_chatgpt_app")
    )
    resolved_devspace_home = (devspace_home or (Path.home() / ".devspace")).expanduser().resolve()
    gate = _final_gate_receipt(resolved_home, resolved_devspace_home, state)
    checks["exact_root_read_verified"] = bool(status["ready"]) and bool(gate and gate.get("read_ok"))
    stages: dict[str, Any] = {}
    for stage_id in STAGE_IDS:
        satisfied = bool(checks.get(STAGE_VERIFIERS[stage_id]))
        owner = "user" if stage_id in USER_OWNED_STAGES else "agent"
        if stage_id == "04_reboot_service" and state.get("provider") != "tailscale":
            owner = "user"
        if stage_id == "06b_local_network_access" and (
            checks.get("chatgpt_local_network_allowed") or _stage_user_consented(state, stage_id)
        ):
            owner = "agent"
        stages[stage_id] = {
            "status": "complete" if satisfied else "pending",
            "owner": owner,
            "verifier": STAGE_VERIFIERS[stage_id],
            "verified": satisfied,
            "evidence_level": (
                "functional-proof"
                if stage_id == "08_final_gate" and satisfied
                else "user-attestation"
                if stage_id in {"06_oracle_login", "07_chatgpt_app"} and satisfied
                else "machine-check"
            ),
        }
    return {"checks": checks, "stages": stages, "readiness": status}


def _completion_state(stages: dict[str, Any]) -> str:
    if stages["08_final_gate"]["verified"]:
        return "verified"
    if stages["07_chatgpt_app"]["verified"]:
        return "awaiting_verification"
    if stages["06b_local_network_access"]["verified"]:
        return "awaiting_chatgpt"
    return "installed"


def next_step(
    *,
    codex_home: Path | None = None,
    devspace_home: Path | None = None,
    http_probe: Any = probe_http,
    oracle_profile_dir: Path | None = None,
    local_network_policy_probe: Any = policy_status,
    language: str | None = None,
) -> dict[str, Any]:
    """Return exactly one actionable step without skipping or repeating stages."""
    resolved_language = resolve_language(language)
    state = load_state(codex_home=codex_home)
    evaluated = evaluate_stages(
        state,
        codex_home=codex_home,
        devspace_home=devspace_home,
        http_probe=http_probe,
        oracle_profile_dir=oracle_profile_dir,
        local_network_policy_probe=local_network_policy_probe,
    )
    stages = evaluated["stages"]
    completion = _completion_state(stages)
    for stage_id in STAGE_IDS:
        state["stages"][stage_id]["status"] = stages[stage_id]["status"]
        if stages[stage_id]["verified"] and not state["stages"][stage_id]["verified_at"]:
            state["stages"][stage_id]["verified_at"] = _now()
    state["completion_state"] = completion
    state["updated_at"] = _now()
    _secret_free(state)
    _write_state(state, codex_home=codex_home)
    current = next((stage_id for stage_id in STAGE_IDS if not stages[stage_id]["verified"]), None)
    started = STAGE_IDS.index(current) if current else len(STAGE_IDS)
    pending = list(STAGE_IDS[started:])
    plan = onboarding_plan(
        provider=state["provider"],
        registration_url=state["registration_url"],
        roots=state["allowed_roots"],
        app_name=state["app_name"],
        enable_local_multi_gpt=bool(state.get("enable_local_multi_gpt")),
    )
    detail = next((stage for stage in plan["stages"] if stage["id"] == current), None)
    consent_needed = bool(
        current == "06b_local_network_access"
        and not _stage_user_consented(state, "06b_local_network_access")
        and not stages["06b_local_network_access"]["verified"]
    )
    return {
        "schema": "codex-web-gpt.onboarding-next/v1",
        "language": resolved_language,
        "completion_state": completion,
        "completion_label": COMPLETION_STATES_BY_LANGUAGE[resolved_language][completion],
        "done": current is None,
        "current_stage": current,
        "owner": stages[current]["owner"] if current else None,
        "needs_user_action": bool(current and stages[current]["owner"] == "user"),
        "confirm_command": (
            "onboard.py consent 06b_local_network_access"
            if consent_needed
            else f"onboard.py confirm {current}"
            if current and stages[current]["owner"] == "user"
            else None
        ),
        "instructions": (
            stage_instructions(current, state, resolved_language)
            if current
            else [
                "모든 단계가 검증되었습니다."
                if resolved_language == "ko"
                else "Every stage is verified."
            ]
        ),
        "stage_detail": detail,
        "checks": evaluated["checks"],
        "pending_stages": pending,
        "registration_url": state["registration_url"],
        "app_name": state["app_name"],
        "chatgpt_ui_paths": (
            CHATGPT_UI_PATHS_BY_LANGUAGE[resolved_language] if current == "07_chatgpt_app" else None
        ),
        "missing_create_button_triage": (
            CHATGPT_MISSING_CREATE_TRIAGE_BY_LANGUAGE[resolved_language]
            if current == "07_chatgpt_app"
            else None
        ),
    }


LANGUAGES = ("ko", "en")
DEFAULT_LANGUAGE = "en"
CHATGPT_UI_PATHS_BY_LANGUAGE = {
    "ko": (
        "설정 → 플러그인 → (맨 아래) 개발자 모드, 이후 왼쪽 메뉴 플러그인 → +",
        "설정 → 앱 → 고급 설정 → 개발자 모드, 이후 앱 → 만들기",
        "관리형 워크스페이스: 워크스페이스 설정 → 앱 → 만들기",
    ),
    "en": (
        "Settings > Plugins > (bottom) Developer mode, then left menu Plugins > +",
        "Settings > Apps > Advanced settings > Developer mode, then Apps > Create",
        "Managed workspace: Workspace settings > Apps > Create",
    ),
}
CHATGPT_MISSING_CREATE_TRIAGE_BY_LANGUAGE = {
    "ko": (
        "ChatGPT 웹에서 접속했는지 확인",
        "개인 계정과 관리형 워크스페이스 중 어디인지 확인",
        "개발자 모드 토글이 실제로 켜졌는지 확인",
        "앱 메뉴 대신 플러그인 메뉴가 제공되는지 확인",
    ),
    "en": (
        "Confirm you are on ChatGPT web",
        "Confirm whether this is a personal or managed workspace",
        "Confirm the developer-mode toggle is actually on",
        "Confirm whether the account exposes Plugins instead of Apps",
    ),
}
CHATGPT_UI_PATHS = CHATGPT_UI_PATHS_BY_LANGUAGE["ko"]
CHATGPT_MISSING_CREATE_TRIAGE = CHATGPT_MISSING_CREATE_TRIAGE_BY_LANGUAGE["ko"]


def resolve_language(explicit: str | None = None, environment: Mapping[str, str] | None = None) -> str:
    """Pick Korean or English from an explicit flag, then the shell locale."""
    if explicit:
        candidate = explicit.strip().casefold()
        if candidate not in LANGUAGES:
            raise OnboardingError("ONBOARDING_LANGUAGE_UNSUPPORTED")
        return candidate
    source = environment if environment is not None else os.environ
    for name in ("CODEX_ONBOARDING_LANG", "LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = str(source.get(name) or "").strip().casefold()
        if not value:
            continue
        if value.startswith("ko") or "korean" in value:
            return "ko"
        if value.startswith("en") or "english" in value:
            return "en"
    return _platform_language(source)


def _platform_language(source: Mapping[str, str]) -> str:
    if sys.platform != "win32":
        return DEFAULT_LANGUAGE
    try:
        import ctypes

        language_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - locale detection must never break onboarding
        return DEFAULT_LANGUAGE
    return "ko" if (int(language_id) & 0x3FF) == 0x12 else DEFAULT_LANGUAGE


def stage_instructions(stage_id: str, state: dict[str, Any], language: str = "ko") -> list[str]:
    """Return plain-language instructions so no one has to interpret raw JSON."""
    if language not in LANGUAGES:
        raise OnboardingError("ONBOARDING_LANGUAGE_UNSUPPORTED")
    url = state["registration_url"]
    origin = state.get("public_origin") or public_origin(url)
    roots = " ".join(f'--root "{root}"' for root in state["allowed_roots"])
    host = urllib.parse.urlsplit(url).hostname or ""
    setup = f"python {SETUP_SCRIPT.as_posix()}"
    tailscale = state["provider"] == "tailscale"
    install_flag = " --enable-local-multi-gpt" if state.get("enable_local_multi_gpt") else ""
    profile = str(Path.home() / ".oracle" / "browser-profile")
    oracle_login = _quoted_command([
        "npx", "--yes", "@steipete/oracle@0.18.0", "--engine", "browser",
        "--browser-manual-login", "--browser-keep-browser",
        "--browser-manual-login-profile-dir", profile, "-p", "HI",
    ])
    status_command = _quoted_command([
        "python", "onboard.py", "status", "--provider", state["provider"],
        "--public-url", url,
        *(part for root in state["allowed_roots"] for part in ("--root", str(root))),
        "--app-name", state["app_name"],
    ])
    korean: dict[str, list[str]] = {
        "01_install": [
            "수명주기 설치를 먼저 끝냅니다.",
            f"python install.py --dry-run{install_flag}",
            f"python install.py{install_flag}",
            "python doctor.py",
        ],
        "02_stable_endpoint": [
            f"{state['provider']} 고정 공개 주소 {url} 와 전체 허용 루트를 검토합니다.",
            (
                f"{setup} setup {roots} --hostname {host} --dry-run"
                if tailscale
                else f"{state['provider']}에서 재부팅 후에도 유지되는 고정 HTTPS 주소가 http://127.0.0.1:{DEFAULT_LOCAL_PORT}로 연결되도록 계획합니다."
            ),
            "주소와 루트 계획이 맞으면 이 단계 확인 명령을 실행합니다. 아직 서비스나 DevSpace init을 실행하지 않습니다.",
        ],
        "03_devspace_init": [
            (
                f"{setup} setup {roots} --hostname {host} --apply"
                if tailscale
                else "devspace init"
            ),
            "사용자 작업입니다. 위 명령이 여는 DevSpace init 화면을 현재 터미널에서 완료합니다.",
            f"표시된 전체 exact root 와 public origin {origin} 을 입력합니다.",
            "public origin 에는 /mcp 를 붙이지 않습니다.",
            "생성된 Owner 암호는 즉시 암호 관리자에 저장합니다. 에이전트에게 알려주지 않습니다.",
        ],
        "04_reboot_service": [
            (
                "재부팅 후에도 살아나도록 Tailscale/DevSpace 로그인 watchdog 을 확인합니다."
                if tailscale
                else f"{state['provider']} 터널과 DevSpace를 OS 로그인 서비스로 등록했음을 확인합니다. 임시 터널은 허용되지 않습니다."
            ),
            (f"{setup} doctor {roots} --hostname {host}" if tailscale else status_command),
            "현재 config roots 와 bootstrap roots 가 완전히 같아야 합니다.",
        ],
        "05_endpoint_check": [
            "로컬과 공개 DevSpace endpoint 를 모두 확인합니다.",
            f"인증 없는 http://127.0.0.1:{DEFAULT_LOCAL_PORT}/mcp 는 HTTP 401 이 정상입니다.",
            f"인증 없는 {url} 도 HTTP 401 이 정상입니다.",
            "연결 거부나 timeout 은 정상이 아닙니다.",
        ],
        "06_oracle_login": [
            "사용자 작업입니다. Oracle 전용 브라우저에서 ChatGPT 에 한 번 로그인합니다.",
            "일상 Chrome 프로필이 아니라 전용 프로필을 사용합니다.",
            oracle_login,
            "로그인을 마친 뒤 onboard.py confirm 06_oracle_login 을 실행합니다.",
        ],
        "06b_local_network_access": [
            *(
                ["chatgpt.com 전용 Local network 정책 변경에 동의하면 아래 동의 명령을 실행합니다."]
                if not _stage_user_consented(state, "06b_local_network_access")
                else [
                    "동의가 기록되었습니다. chatgpt.com 의 Local network 권한만 영속 등록합니다.",
                    "python bin/chatgpt_chrome_local_network.py enable",
                    "python bin/chatgpt_chrome_local_network.py status",
                    "Windows 정책 쓰기가 거부되면 전용 Oracle 프로필에서 직접 한 번 허용합니다.",
                ]
            ),
        ],
        "07_chatgpt_app": [
            "사용자 작업입니다. ChatGPT 개발자 모드를 켜고 앱을 직접 등록합니다.",
            f"앱 이름: {state['app_name']}",
            f"등록 주소: {url}",
            f"python onboard.py configure-app-name --app-name {state['app_name']}",
            "계정에 따라 메뉴가 다르므로 아래 경로를 모두 확인합니다.",
            *(f"경로: {path}" for path in CHATGPT_UI_PATHS_BY_LANGUAGE["ko"]),
            "등록과 Owner 승인을 마친 뒤 onboard.py confirm 07_chatgpt_app 을 실행합니다.",
        ],
        "08_final_gate": [
            "마지막으로 실제 프로젝트를 읽을 수 있는지 확인합니다.",
            (f"{setup} post-register {roots} --hostname {host}" if tailscale else status_command),
            "프로젝트 안의 짧은 읽기 전용 canary 미션을 준비한 뒤 다음 명령으로 정확한 manifest와 dry-run/live 명령을 생성합니다: python onboard.py prepare-final-gate --root <루트> --mission-path <루트>\\missions\\onboarding-final-gate.md",
            f"새 일반 비-Pro Oracle 실행에서 @{state['app_name']} 로 exact root 를 열고 반환된 workspaceId를 보존합니다.",
            "Oracle run_id를 세 호출의 auditNonce로 쓰고 open_workspace를 그 대화의 첫 workspace/process/mutation 호출로 실행합니다. 같은 workspaceId로 미션 파일을 별도 read 호출한 뒤 같은 파일 전체를 offset 0의 read_chunk로 읽습니다.",
            "각 도구 결과가 돌려준 서버 생성 Audit receipt ID 3개를 최종 답변에 정확히 다시 적습니다. 이 challenge-response와 ~/.devspace/state/tool-read-receipts의 open_workspace → read → read_chunk 영수증을 함께 검증하며, 임의 ID/SHA 자기진술만으로는 통과하지 않습니다.",
            f"새 canary에 read_chunk 또는 서버 생성 Audit receipt ID가 없으면 ChatGPT의 정확한 @{state['app_name']} 앱 Action 스냅샷이 오래된 것입니다. Enterprise/Edu 관리자는 워크스페이스 설정 → 앱에서 {state['app_name']} 앱의 더보기 메뉴를 열고 Action control → Refresh를 직접 실행한 뒤 새 Action을 검토·활성화합니다. 자동화가 이 설정을 조작하지 않습니다.",
            "Business 또는 Refresh가 보이지 않는 UI에서는 같은 exact /mcp URL로 앱을 다시 만들고 게시한 뒤 현재 서버 Action을 검토·활성화하고 Owner 승인을 직접 완료합니다. 그 뒤 위 post-register를 한 번 실행하고 새 regular non-Pro auditNonce canary를 실행합니다. open_workspace/read만으로는 절대 통과하지 않습니다.",
            "Codex Desktop 내장 DevSpace 플러그인 결과는 증거로 쓰지 않습니다.",
            "성공하면 onboard.py record-final-gate --run-dir <Oracle run 디렉터리> --root <루트> --evidence <요약> --listing <항목> 을 실행합니다.",
        ],
    }
    english: dict[str, list[str]] = {
        "01_install": [
            "Finish the lifecycle install first.",
            f"python install.py --dry-run{install_flag}",
            f"python install.py{install_flag}",
            "python doctor.py",
        ],
        "02_stable_endpoint": [
            f"Review the {state['provider']} stable public URL {url} and the complete allowed-root list.",
            (
                f"{setup} setup {roots} --hostname {host} --dry-run"
                if tailscale
                else f"Plan a reboot-persistent {state['provider']} HTTPS service from this URL to http://127.0.0.1:{DEFAULT_LOCAL_PORT}."
            ),
            "Confirm this stage only after the URL and roots are correct. Do not start the service or DevSpace init yet.",
        ],
        "03_devspace_init": [
            (f"{setup} setup {roots} --hostname {host} --apply" if tailscale else "devspace init"),
            "User step. Complete the DevSpace init prompt opened by the command above in this terminal.",
            f"Enter every exact root shown plus the public origin {origin}.",
            "Do not append /mcp to the public origin.",
            "Save the generated Owner password in a password manager. Never share it with the agent.",
        ],
        "04_reboot_service": [
            (
                "Confirm the Tailscale/DevSpace login watchdog so setup survives a reboot."
                if tailscale
                else f"Confirm the {state['provider']} tunnel and DevSpace are registered as OS login services; an ephemeral tunnel is forbidden."
            ),
            (f"{setup} doctor {roots} --hostname {host}" if tailscale else status_command),
            "Current config roots and bootstrap roots must match exactly.",
        ],
        "05_endpoint_check": [
            "Check both the local and public DevSpace endpoints.",
            f"An unauthenticated http://127.0.0.1:{DEFAULT_LOCAL_PORT}/mcp returning HTTP 401 is healthy.",
            f"An unauthenticated {url} must also return HTTP 401.",
            "Connection refused or timeout is not healthy.",
        ],
        "06_oracle_login": [
            "User step. Sign in to ChatGPT once in the dedicated Oracle browser.",
            "Use the dedicated profile, not your everyday Chrome profile.",
            oracle_login,
            "After signing in, run onboard.py confirm 06_oracle_login.",
        ],
        "06b_local_network_access": [
            *(
                ["If you consent to changing only the chatgpt.com Local Network policy, run the consent command below."]
                if not _stage_user_consented(state, "06b_local_network_access")
                else [
                    "Consent is recorded. Persist the Local Network grant for chatgpt.com only.",
                    "python bin/chatgpt_chrome_local_network.py enable",
                    "python bin/chatgpt_chrome_local_network.py status",
                    "If the Windows policy write is denied, grant it once in the dedicated Oracle profile.",
                ]
            ),
        ],
        "07_chatgpt_app": [
            "User step. Turn on ChatGPT developer mode and register the app yourself.",
            f"App name: {state['app_name']}",
            f"Connection URL: {url}",
            f"python onboard.py configure-app-name --app-name {state['app_name']}",
            "Menus differ by account, so check every path below.",
            *(f"Path: {path}" for path in CHATGPT_UI_PATHS_BY_LANGUAGE["en"]),
            "After registration and Owner approval, run onboard.py confirm 07_chatgpt_app.",
        ],
        "08_final_gate": [
            "Finally confirm the real project root is readable.",
            (f"{setup} post-register {roots} --hostname {host}" if tailscale else status_command),
            "Prepare a short read-only canary mission inside the project, then generate the exact manifest and dry-run/live commands with: python onboard.py prepare-final-gate --root <root> --mission-path <root>\\missions\\onboarding-final-gate.md",
            f"In a fresh regular non-Pro Oracle run, open the exact root with @{state['app_name']} and preserve the returned workspaceId.",
            "Use the Oracle run_id as auditNonce for all three calls and make open_workspace the conversation's first workspace/process/mutation call. With that same workspaceId, separately read the mission and then read_chunk the complete same file from offset zero.",
            "Echo the three server-generated Audit receipt IDs returned by the tool calls exactly in the final answer. The gate verifies that challenge-response together with DevSpace's ~/.devspace/state/tool-read-receipts open_workspace → read → read_chunk chain; arbitrary output ID/SHA claims alone never pass.",
            f"If the fresh canary exposes no read_chunk or server-generated Audit receipt ID, the exact @{state['app_name']} ChatGPT app Action snapshot is stale. On Enterprise/Edu, an admin must open that app's overflow menu under Workspace settings > Apps, use Action control > Refresh, and review and enable the new Actions; automation must not change this ChatGPT setting.",
            "On Business, or when Refresh is unavailable, recreate and publish the app with the same exact /mcp URL, review and enable the current server Actions, and complete Owner approval manually. Then run the post-register command above once and run a fresh regular non-Pro auditNonce canary. open_workspace/read alone never passes.",
            "The built-in Codex Desktop DevSpace plugin is not valid evidence.",
            "On success run onboard.py record-final-gate --run-dir <Oracle run directory> --root <root> --evidence <summary> --listing <entry>.",
        ],
    }
    guides = korean if language == "ko" else english
    fallback = "다음 단계 안내를 찾을 수 없습니다." if language == "ko" else "No instructions found for this stage."
    return guides.get(stage_id, [fallback])


def confirm_stage(
    stage_id: str,
    *,
    codex_home: Path | None = None,
    devspace_home: Path | None = None,
    http_probe: Any = probe_http,
    oracle_profile_dir: Path | None = None,
    local_network_policy_probe: Any = policy_status,
    language: str | None = None,
) -> dict[str, Any]:
    """Accept a user confirmation only when live evidence also proves the stage."""
    resolved_language = resolve_language(language)
    if stage_id not in STAGE_IDS:
        raise OnboardingError("ONBOARDING_STAGE_UNKNOWN")
    state = load_state(codex_home=codex_home)
    evaluated = evaluate_stages(
        state,
        codex_home=codex_home,
        devspace_home=devspace_home,
        http_probe=http_probe,
        oracle_profile_dir=oracle_profile_dir,
        local_network_policy_probe=local_network_policy_probe,
    )
    blocking = next(
        (
            earlier
            for earlier in STAGE_IDS[: STAGE_IDS.index(stage_id)]
            if not evaluated["stages"][earlier]["verified"]
        ),
        None,
    )
    if blocking is None and stage_id in USER_CONFIRMATION_STAGES:
        prerequisite = {
            "02_stable_endpoint": True,
            "04_reboot_service": bool(evaluated["checks"].get("bootstrap_matches_config")),
            "06_oracle_login": bool(evaluated["checks"].get("oracle_profile_initialized")),
            "07_chatgpt_app": bool(evaluated["checks"].get("app_name_matches_expected")),
        }[stage_id]
        if prerequisite:
            state["stages"][stage_id]["evidence"] = {
                "kind": "user-confirmed",
                "confirmed_at": _now(),
            }
            _secret_free(state)
            _write_state(state, codex_home=codex_home)
            evaluated = evaluate_stages(
                state,
                codex_home=codex_home,
                devspace_home=devspace_home,
                http_probe=http_probe,
                oracle_profile_dir=oracle_profile_dir,
                local_network_policy_probe=local_network_policy_probe,
            )
    verified = bool(evaluated["stages"][stage_id]["verified"]) and blocking is None
    state["stages"][stage_id]["status"] = "complete" if verified else "pending"
    state["stages"][stage_id]["confirmed_at"] = _now()
    if verified:
        state["stages"][stage_id]["verified_at"] = _now()
    state["completion_state"] = _completion_state(evaluated["stages"])
    state["updated_at"] = _now()
    _secret_free(state)
    _write_state(state, codex_home=codex_home)
    return {
        "schema": "codex-web-gpt.onboarding-confirm/v1",
        "stage": stage_id,
        "accepted": verified,
        "verifier": STAGE_VERIFIERS[stage_id],
        "reason": (
            None
            if verified
            else "STAGE_OUT_OF_ORDER_EARLIER_STAGE_PENDING"
            if blocking is not None
            else "STAGE_CONFIRMATION_NOT_PROVEN_BY_EVIDENCE"
        ),
        "blocking_stage": blocking,
        "checks": evaluated["checks"],
        "completion_state": state["completion_state"],
        "completion_label": COMPLETION_STATES_BY_LANGUAGE[resolved_language][state["completion_state"]],
    }


def consent_stage(
    stage_id: str,
    *,
    codex_home: Path | None = None,
    devspace_home: Path | None = None,
    http_probe: Any = probe_http,
    oracle_profile_dir: Path | None = None,
    local_network_policy_probe: Any = policy_status,
) -> dict[str, Any]:
    """Persist explicit non-secret consent before an agent changes Chrome policy."""
    if stage_id != "06b_local_network_access":
        raise OnboardingError("ONBOARDING_CONSENT_STAGE_UNSUPPORTED")
    state = load_state(codex_home=codex_home)
    evaluated = evaluate_stages(
        state,
        codex_home=codex_home,
        devspace_home=devspace_home,
        http_probe=http_probe,
        oracle_profile_dir=oracle_profile_dir,
        local_network_policy_probe=local_network_policy_probe,
    )
    blocking = next(
        (
            earlier
            for earlier in STAGE_IDS[: STAGE_IDS.index(stage_id)]
            if not evaluated["stages"][earlier]["verified"]
        ),
        None,
    )
    if blocking is not None:
        return {
            "schema": "codex-web-gpt.onboarding-consent/v1",
            "stage": stage_id,
            "accepted": False,
            "reason": "STAGE_OUT_OF_ORDER_EARLIER_STAGE_PENDING",
            "blocking_stage": blocking,
        }
    state["stages"][stage_id]["evidence"] = {
        "kind": "user-consent",
        "confirmed_at": _now(),
        "scope": "persist-local-network-access-for-https://chatgpt.com-only",
    }
    state["updated_at"] = _now()
    _secret_free(state)
    _write_state(state, codex_home=codex_home)
    return {
        "schema": "codex-web-gpt.onboarding-consent/v1",
        "stage": stage_id,
        "accepted": True,
        "next_command": "python onboard.py next",
    }


def render_step(step: dict[str, Any]) -> str:
    """Render one wizard step as a short readable block."""
    language = step.get("language") if step.get("language") in LANGUAGES else DEFAULT_LANGUAGE
    words = {
        "ko": {
            "user": "사용자 작업 필요",
            "auto": "자동 진행",
            "state": "현재 상태",
            "none_left": "남은 단계가 없습니다.",
            "triage": "생성 버튼이 없으면 아래 순서로 확인합니다.",
            "after": "완료 후",
            "then": "이어서",
            "remaining": "남은 단계",
        },
        "en": {
            "user": "user action required",
            "auto": "automatic",
            "state": "Current state",
            "none_left": "No stages remain.",
            "triage": "If the create button is missing, check in this order.",
            "after": "After finishing",
            "then": "Next",
            "remaining": "Remaining",
        },
    }[language]
    total = len(STAGE_IDS)
    lines: list[str] = []
    if step.get("done"):
        lines.append(f"[{total}/{total}] {step['completion_label']}")
        lines.append(words["none_left"])
        return "\n".join(lines)
    current = step["current_stage"]
    index = STAGE_IDS.index(current) + 1
    owner = words["user"] if step["needs_user_action"] else words["auto"]
    lines.append(f"[{index}/{total}] {current}  ({owner})")
    lines.append(f"{words['state']}: {step['completion_label']}")
    lines.append("")
    for instruction in step.get("instructions") or []:
        lines.append(f"  {instruction}")
    if step.get("missing_create_button_triage"):
        lines.append("")
        lines.append(f"  {words['triage']}")
        for item in step["missing_create_button_triage"]:
            lines.append(f"    - {item}")
    lines.append("")
    if step.get("confirm_command"):
        lines.append(f"{words['after']}: python {step['confirm_command']}")
    else:
        lines.append(f"{words['then']}: python onboard.py next")
    remaining = [stage for stage in step.get("pending_stages") or [] if stage != current]
    if remaining:
        lines.append(f"{words['remaining']}: {', '.join(remaining)}")
    return "\n".join(lines)


def record_final_gate(
    *,
    read_ok: bool,
    root: str,
    evidence: str,
    run_dir: Path | None = None,
    codex_home: Path | None = None,
    devspace_home: Path | None = None,
    transport: str = "regular-non-pro-oracle",
    listing: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Record the non-Pro Oracle exact-root read result that closes onboarding."""
    state = load_state(codex_home=codex_home)
    identities = _root_identities(state["allowed_roots"])
    if os.path.normcase(str(Path(root).expanduser().resolve())) not in identities:
        raise OnboardingError("FINAL_GATE_ROOT_NOT_IN_ALLOWED_ROOTS")
    if transport not in FINAL_GATE_TRANSPORTS:
        raise OnboardingError("FINAL_GATE_TRANSPORT_MUST_BE_REGULAR_NON_PRO_ORACLE")
    summary = evidence.strip()
    entries = [str(item).strip() for item in (listing or []) if str(item).strip()]
    if read_ok and (len(summary) < FINAL_GATE_MIN_EVIDENCE or not entries):
        raise OnboardingError("FINAL_GATE_EVIDENCE_INSUFFICIENT")
    binding: dict[str, Any] = {}
    if read_ok:
        if run_dir is None:
            raise OnboardingError("FINAL_GATE_ORACLE_RUN_REQUIRED")
        binding = _oracle_final_gate_binding(
            codex_home=_codex_home(codex_home),
            devspace_home=(devspace_home or (Path.home() / ".devspace")).expanduser().resolve(),
            run_dir=run_dir,
            expected_root=root,
            expected_app_name=state["app_name"],
            listing=entries,
            require_current_task=True,
        )
    state["stages"]["08_final_gate"]["evidence"] = {
        "read_ok": bool(read_ok),
        "root": str(Path(root).expanduser().resolve()),
        "evidence": summary[:400],
        "listing_sample": entries[:10],
        "recorded_at": _now(),
        "transport": transport,
        **binding,
    }
    state["updated_at"] = _now()
    _secret_free(state)
    _write_state(state, codex_home=codex_home)
    return state["stages"]["08_final_gate"]["evidence"]


def prepare_final_gate(
    *,
    root: str,
    mission_path: Path,
    codex_home: Path | None = None,
    python_executable: str = "python",
) -> dict[str, Any]:
    """Create the exact host-state manifest and commands for the registered-app final gate."""
    state = load_state(codex_home=codex_home)
    source_thread_id = str(os.environ.get("CODEX_THREAD_ID") or "").strip().casefold()
    if SOURCE_THREAD_ID_RE.fullmatch(source_thread_id) is None:
        raise OnboardingError("FINAL_GATE_CODEX_TASK_REQUIRED")
    root_path = Path(root).expanduser().resolve(strict=True)
    if os.path.normcase(str(root_path)) not in _root_identities(state["allowed_roots"]):
        raise OnboardingError("FINAL_GATE_ROOT_NOT_IN_ALLOWED_ROOTS")
    try:
        mission = mission_path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise OnboardingError("FINAL_GATE_MISSION_UNREADABLE") from exc
    if not mission.is_file() or not _inside(root_path, mission):
        raise OnboardingError("FINAL_GATE_MISSION_MUST_BE_INSIDE_EXACT_ROOT")
    mission_sha256 = _sha256_file(mission)
    root_hash = hashlib.sha256(os.path.normcase(str(root_path)).encode("utf-8")).hexdigest()[:16]
    source_thread_hash = hashlib.sha256(source_thread_id.encode("ascii")).hexdigest()[:16]
    target = (
        _codex_home(codex_home)
        / FINAL_GATE_MANIFEST_RELATIVE
        / f"{source_thread_hash}-{root_hash}-{mission_sha256[:16]}.json"
    )
    manifest = {
        "schema": FINAL_GATE_MANIFEST_SCHEMA,
        "project_root": str(root_path),
        "mission_path": str(mission),
        "app_name": state["app_name"],
        "mode": "browser",
        "transport": "devspace",
        "model": "gpt-5.6",
        "model_strategy": "select",
        "thinking_time": "extra-high",
        "research": "off",
        "task_outcome_contract": "v1",
        "archive": "never",
        "registered_app_final_gate": True,
        "source_thread_id": source_thread_id,
    }
    _write_json_atomic(target, manifest)
    runner = _codex_home(codex_home) / "bin" / "chatgpt_oracle_run.py"
    base = [python_executable, str(runner), "run", "--manifest", str(target)]
    return {
        "ok": True,
        "schema": "codex-web-gpt.onboarding-final-gate-plan/v1",
        "root": str(root_path),
        "mission_path": str(mission),
        "mission_sha256": mission_sha256,
        "app_name": state["app_name"],
        "source_thread_id": source_thread_id,
        "manifest_path": str(target),
        "manifest_sha256": _sha256_file(target),
        "dry_run_command": _quoted_command([*base, "--dry-run"]),
        "run_command": _quoted_command(base),
        "record_command_template": _quoted_command(
            [
                python_executable,
                "onboard.py",
                "record-final-gate",
                "--run-dir",
                "<EXACT_ORACLE_RUN_DIR>",
                "--root",
                str(root_path),
                "--evidence",
                "<VERIFIED_SUMMARY>",
                "--listing",
                mission.relative_to(root_path).as_posix(),
            ]
        ),
        "submission_action": "none",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{PRODUCT_NAME} first-install planner")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of the readable summary")
    parser.add_argument("--lang", choices=LANGUAGES, help="Force Korean or English output; defaults to the shell locale")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status"):
        current = commands.add_parser(name)
        current.add_argument("--provider", choices=PROVIDERS, required=True)
        current.add_argument("--public-url", required=True, help="Stable public HTTPS URL ending in /mcp")
        current.add_argument("--root", action="append", required=True, dest="roots")
        current.add_argument("--app-name", default=APP_NAME)
        if name == "plan":
            current.add_argument("--enable-local-multi-gpt", action="store_true")
    configure = commands.add_parser("configure-app-name")
    configure.add_argument("--codex-home", type=Path)
    configure.add_argument("--app-name", default=APP_NAME)
    start = commands.add_parser("start")
    start.add_argument("--provider", choices=PROVIDERS, default="tailscale")
    start.add_argument("--public-url", help="Stable public HTTPS URL ending in /mcp; auto-discovered for tailscale")
    start.add_argument("--root", action="append", required=True, dest="roots")
    start.add_argument("--app-name", default=APP_NAME)
    start.add_argument("--enable-local-multi-gpt", action="store_true")
    start.add_argument("--reset", action="store_true", help="Discard existing onboarding progress and start over")
    for name in ("next", "resume"):
        current = commands.add_parser(name)
        current.add_argument("--json", action="store_true", dest="json", default=None)
        current.add_argument("--lang", choices=LANGUAGES, dest="lang", default=None)
    confirm = commands.add_parser("confirm")
    confirm.add_argument("stage")
    confirm.add_argument("--lang", choices=LANGUAGES, dest="lang", default=None)
    consent = commands.add_parser("consent")
    consent.add_argument("stage")
    gate = commands.add_parser("record-final-gate")
    gate.add_argument("--root", required=True)
    gate.add_argument("--evidence", required=True)
    gate.add_argument("--run-dir", type=Path)
    gate.add_argument("--devspace-home", type=Path)
    gate.add_argument("--listing", action="append", default=[], help="One observed directory entry; repeatable")
    gate.add_argument("--failed", action="store_true")
    prepare = commands.add_parser("prepare-final-gate")
    prepare.add_argument("--root", required=True)
    prepare.add_argument("--mission-path", type=Path, required=True)
    prepare.add_argument("--codex-home", type=Path)
    return parser


def _global_language_flag(arguments: Sequence[str]) -> str | None:
    """Recover a pre-subcommand --lang value that a subparser default cleared."""
    for index, value in enumerate(arguments):
        if value == "--lang" and index + 1 < len(arguments):
            candidate = arguments[index + 1].strip().casefold()
            return candidate if candidate in LANGUAGES else None
        if value.startswith("--lang="):
            candidate = value.split("=", 1)[1].strip().casefold()
            return candidate if candidate in LANGUAGES else None
    return None


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(arguments)
    if getattr(args, "lang", None) is None:
        args.lang = _global_language_flag(arguments)
    if not getattr(args, "json", False):
        args.json = "--json" in arguments
    try:
        if args.command == "plan":
            result = onboarding_plan(
                provider=args.provider,
                registration_url=args.public_url,
                roots=args.roots,
                app_name=args.app_name,
                enable_local_multi_gpt=args.enable_local_multi_gpt,
            )
        elif args.command == "status":
            result = readiness_status(
                provider=args.provider,
                registration_url=args.public_url,
                roots=args.roots,
                app_name=args.app_name,
            )
        elif args.command == "start":
            if not args.reset:
                try:
                    load_state()
                except OnboardingError:
                    if state_path().exists():
                        print(
                            json.dumps(
                                {
                                    "ok": False,
                                    "error": "ONBOARDING_STATE_CORRUPT",
                                    "hint": "python onboard.py start --reset",
                                },
                                ensure_ascii=False,
                            )
                        )
                        return 2
                else:
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "error": "ONBOARDING_ALREADY_STARTED",
                                "hint": "python onboard.py resume",
                            },
                            ensure_ascii=False,
                        )
                    )
                    return 2
            start_onboarding(
                provider=args.provider,
                registration_url=args.public_url,
                roots=args.roots,
                app_name=args.app_name,
                enable_local_multi_gpt=args.enable_local_multi_gpt,
            )
            result = next_step(language=args.lang)
        elif args.command in ("next", "resume"):
            result = next_step(language=args.lang)
        elif args.command == "confirm":
            result = confirm_stage(args.stage, language=args.lang)
        elif args.command == "consent":
            result = consent_stage(args.stage)
        elif args.command == "record-final-gate":
            result = record_final_gate(
                read_ok=not args.failed,
                root=args.root,
                evidence=args.evidence,
                run_dir=args.run_dir,
                devspace_home=args.devspace_home,
                listing=args.listing,
            )
        elif args.command == "prepare-final-gate":
            result = prepare_final_gate(
                root=args.root,
                mission_path=args.mission_path,
                codex_home=args.codex_home,
            )
        else:
            path = configure_app_name(codex_home=args.codex_home, app_name=args.app_name)
            result = {"ok": True, "app_name": normalize_app_name(args.app_name), "path": str(path)}
    except OnboardingError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.command in ("start", "next", "resume") and not args.json:
        print(render_step(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("ready") is False:
        return 3
    if result.get("accepted") is False:
        return 3
    if "done" in result and not result["done"]:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
