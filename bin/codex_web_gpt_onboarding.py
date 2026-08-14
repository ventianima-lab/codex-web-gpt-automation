from __future__ import annotations

"""Fail-closed first-install planner and readiness check.

This module deliberately never accepts or reads the DevSpace Owner password.
It orders the non-secret setup stages and checks the resulting public contract.
"""

import argparse
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence


PRODUCT_NAME = "Agent Web GPT Automation"
APP_NAME = "codex"
DEFAULT_LOCAL_PORT = 7676
PROVIDERS = ("tailscale", "cloudflare", "ngrok", "custom")


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
    stages = [
        {
            "id": "01_install",
            "owner": "agent",
            "complete_when": "receipt-backed install and doctor both succeed",
            "commands": [
                f"{python_executable} install.py --dry-run",
                f"{python_executable} install.py",
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
                    "@steipete/oracle@0.17.1",
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
        "stages": stages,
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


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
    browser_profile = Path.home() / ".oracle" / "browser-profile"
    browser_profile_initialized = browser_profile.is_dir() and any(browser_profile.iterdir())
    checks = {
        "exact_roots_configured": exact_roots_configured,
        "bootstrap_matches_config": bootstrap_matches,
        "app_name_matches_expected": workspace.get("app_name") == plan["app_name"],
        "local_mcp_oauth_challenge": bool(local.get("ok")),
        "public_mcp_oauth_challenge": bool(public.get("ok")),
        "oracle_profile_initialized": browser_profile_initialized,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{PRODUCT_NAME} first-install planner")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status"):
        current = commands.add_parser(name)
        current.add_argument("--provider", choices=PROVIDERS, required=True)
        current.add_argument("--public-url", required=True, help="Stable public HTTPS URL ending in /mcp")
        current.add_argument("--root", action="append", required=True, dest="roots")
        current.add_argument("--app-name", default=APP_NAME)
    configure = commands.add_parser("configure-app-name")
    configure.add_argument("--codex-home", type=Path)
    configure.add_argument("--app-name", default=APP_NAME)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            result = onboarding_plan(
                provider=args.provider,
                registration_url=args.public_url,
                roots=args.roots,
                app_name=args.app_name,
            )
        elif args.command == "status":
            result = readiness_status(
                provider=args.provider,
                registration_url=args.public_url,
                roots=args.roots,
                app_name=args.app_name,
            )
        else:
            path = configure_app_name(codex_home=args.codex_home, app_name=args.app_name)
            result = {"ok": True, "app_name": normalize_app_name(args.app_name), "path": str(path)}
    except OnboardingError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ready", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
