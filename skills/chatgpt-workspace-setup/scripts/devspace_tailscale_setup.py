from __future__ import annotations

"""Explicit DevSpace/Tailscale setup and read-only endpoint diagnostics.

This module deliberately contains no ChatGPT UI or browser automation.  It is a
one-time local setup helper; normal GPT execution consumes only its printed MCP
URL and must not invoke it.
"""

import argparse
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


DEFAULT_PORT = 7676
APP_NAME = "codex"
DEVSPACE_PACKAGE = "@waishnav/devspace@1.0.4"
DEVSPACE_TOOL_MODE = "full"
DEVSPACE_OAUTH_SCOPES = "devspace,offline_access"
SECRET_PATTERN = re.compile(r"(?i)(password|token|secret|authorization)\s*([:=])\s*[^\s,;]+")
HOSTNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*\.ts\.net$", re.IGNORECASE)
WINDOWS_BOOTSTRAP_RUN_NAME = "Codex Web GPT DevSpace Bootstrap"


class SetupError(ValueError):
    pass


@dataclass(frozen=True)
class SetupConfig:
    roots: tuple[Path, ...]
    hostname: str
    local_port: int = DEFAULT_PORT
    public_port: int = 443

    @property
    def public_origin(self) -> str:
        suffix = "" if self.public_port == 443 else f":{self.public_port}"
        return f"https://{self.hostname}{suffix}"

    @property
    def registration_url(self) -> str:
        return f"{self.public_origin}/mcp"

    @property
    def local_mcp_url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}/mcp"


def redact(value: str) -> str:
    return SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)


def _is_volume_root(path: Path) -> bool:
    anchor = Path(path.anchor)
    return bool(path.anchor) and path == anchor


def validate_config(
    roots: Sequence[str],
    hostname: str,
    local_port: int = DEFAULT_PORT,
    public_port: int = 443,
) -> SetupConfig:
    if not roots:
        raise SetupError("ALLOWED_ROOT_REQUIRED")
    resolved: list[Path] = []
    for raw_root in roots:
        path = Path(raw_root)
        if not path.is_absolute():
            raise SetupError("ALLOWED_ROOT_ABSOLUTE_REQUIRED")
        if not path.is_dir():
            raise SetupError("ALLOWED_ROOT_NOT_DIRECTORY")
        path = path.resolve()
        if _is_volume_root(path):
            raise SetupError("ALLOWED_ROOT_TOO_BROAD")
        if path not in resolved:
            resolved.append(path)
    hostname = hostname.strip().lower().rstrip(".")
    if not HOSTNAME_PATTERN.fullmatch(hostname):
        raise SetupError("TAILSCALE_HOSTNAME_REQUIRED")
    if not 1 <= local_port <= 65535:
        raise SetupError("LOCAL_PORT_INVALID")
    if public_port not in {443, 8443, 10000}:
        raise SetupError("TAILSCALE_FUNNEL_PORT_INVALID")
    return SetupConfig(tuple(resolved), hostname, local_port, public_port)


def git_bash_path() -> Path:
    candidate = Path(os.environ.get("DEVSPACE_GIT_BASH") or r"C:\Program Files\Git\bin\bash.exe")
    if not candidate.is_file():
        raise SetupError("GIT_BASH_NOT_FOUND")
    return candidate


def windows_subprocess_kwargs(platform_name: str | None = None) -> dict[str, Any]:
    if (platform_name or os.name) != "nt":
        return {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not hasattr(subprocess, "STARTUPINFO"):
        return {"creationflags": creationflags}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {"creationflags": creationflags, "startupinfo": startupinfo}


def bash_argv(command: Sequence[str]) -> list[str]:
    return [str(git_bash_path()), "-lc", "exec " + " ".join(shlex.quote(part) for part in command)]


def command_argv(command: Sequence[str], *, platform_name: str | None = None) -> list[str]:
    if (platform_name or os.name) == "nt":
        return bash_argv(command)
    return list(command)


def devspace_compat_argv(
    *,
    confirm_restarted: bool = False,
    stop_exact_service: bool = False,
    local_port: int = DEFAULT_PORT,
) -> list[str]:
    script = Path(__file__).resolve().parents[3] / "bin" / "chatgpt_devspace_compat.py"
    if not script.is_file():
        raise SetupError("DEVSPACE_COMPAT_MODULE_MISSING")
    argv = [sys.executable, str(script)]
    if confirm_restarted:
        argv.append("--confirm-service-restarted")
    if stop_exact_service:
        argv.append("--stop-exact-service")
    if local_port != DEFAULT_PORT:
        argv.extend(["--local-port", str(local_port)])
    return argv


def devspace_native_argv(*, allow_package_absent: bool = False) -> list[str]:
    argv = devspace_compat_argv()
    argv.append("--check-native-runtime")
    if allow_package_absent:
        argv.append("--allow-package-absent")
    return argv


def setup_plan(
    config: SetupConfig,
    *,
    platform_name: str | None = None,
    requested_roots: Sequence[Path] | None = None,
    preserved_existing_roots: Sequence[Path] = (),
) -> dict[str, Any]:
    requested = tuple(requested_roots or config.roots)
    preserved = tuple(preserved_existing_roots)
    return {
        "action": "explicit_setup_only",
        "platform": platform_name or os.name,
        "allowed_roots": [str(root) for root in config.roots],
        "requested_roots": [str(root) for root in requested],
        "preserved_existing_roots": [str(root) for root in preserved],
        "root_merge_applied": bool(preserved),
        "root_safety": "existing allowedRoots are preserved; setup must use the complete displayed list",
        "devspace_init": command_argv(["npx", "--yes", DEVSPACE_PACKAGE, "init"], platform_name=platform_name),
        "devspace_serve": command_argv(["npx", "--yes", DEVSPACE_PACKAGE, "serve"], platform_name=platform_name),
        "managed_service_environment": {
            "DEVSPACE_TOOL_MODE": DEVSPACE_TOOL_MODE,
            "DEVSPACE_OAUTH_SCOPES": DEVSPACE_OAUTH_SCOPES,
        },
        "startup_watchdog": {
            "windows_mode": "per-user login watchdog",
            "health_interval_seconds": 300,
            "runtime_root_source": str(Path.home() / ".devspace" / "config.json"),
        },
        "tailscale_funnel": [
            "tailscale",
            "funnel",
            "--bg",
            f"--https={config.public_port}",
            f"http://127.0.0.1:{config.local_port}",
        ],
        "public_origin_for_devspace_init": config.public_origin,
        "recommended_app_name": APP_NAME,
        "registration_url": config.registration_url,
        "requires_developer_mode": True,
        "requires_owner_approval": True,
    }


def run_checked(argv: Sequence[str], *, runner: Callable[..., Any] = subprocess.run) -> None:
    runner(list(argv), check=True, text=True, **windows_subprocess_kwargs())


def run_interactive_checked(
    argv: Sequence[str],
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Run a bounded setup prompt attached to the user's current terminal."""
    runner(list(argv), check=True, text=True)


def interactive_terminal_available() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _validate_custom_owner_password(value: str) -> str:
    if len(value) < 16 or len(value) > 256 or any(character.isspace() for character in value):
        raise SetupError("DEVSPACE_OWNER_PASSWORD_STRENGTH_INVALID")
    classes = (
        any(character.islower() for character in value),
        any(character.isupper() for character in value),
        any(character.isdigit() for character in value),
        any(not character.isalnum() for character in value),
    )
    if sum(classes) < 3 or value.isdigit():
        raise SetupError("DEVSPACE_OWNER_PASSWORD_STRENGTH_INVALID")
    return value


def review_owner_password_interactive(
    *,
    auth_path: Path | None = None,
    input_fn: Callable[[str], str] = input,
    getpass_fn: Callable[[str], str] = getpass.getpass,
    output_fn: Callable[[str], None] = print,
    interactive: bool | None = None,
) -> dict[str, Any]:
    """Keep or replace the Owner password without exposing it to automation logs."""
    if interactive is None:
        interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
    if not interactive:
        raise SetupError("DEVSPACE_OWNER_PASSWORD_REVIEW_REQUIRES_INTERACTIVE_TTY")
    target = (
        auth_path
        or Path(os.environ.get("DEVSPACE_CONFIG_DIR") or (Path.home() / ".devspace"))
        / "auth.json"
    ).expanduser().resolve()
    if target.is_symlink():
        raise SetupError("DEVSPACE_AUTH_SYMLINK_UNSUPPORTED")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SetupError("DEVSPACE_AUTH_UNREADABLE") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("ownerToken"), str) or not payload["ownerToken"]:
        raise SetupError("DEVSPACE_OWNER_PASSWORD_MISSING")
    choice = input_fn(
        "Keep the generated Owner password (recommended), or set a custom one? [K/c]: "
    ).strip().casefold()
    changed = False
    if choice in {"", "k", "keep"}:
        owner_password = payload["ownerToken"]
    elif choice in {"c", "custom"}:
        first = _validate_custom_owner_password(getpass_fn("New Owner password: "))
        second = getpass_fn("Confirm Owner password: ")
        if first != second:
            raise SetupError("DEVSPACE_OWNER_PASSWORD_CONFIRMATION_MISMATCH")
        owner_password = first
        replacement = dict(payload)
        replacement["ownerToken"] = owner_password
        temporary = target.with_name(f".{target.name}.tmp-{time.time_ns()}")
        try:
            temporary.write_text(
                json.dumps(replacement, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        changed = True
    else:
        raise SetupError("DEVSPACE_OWNER_PASSWORD_CHOICE_INVALID")
    output_fn("Owner password (save this now in a password manager):")
    output_fn(owner_password)
    output_fn("It will not be written to Codex logs, receipts, manifests, or shell history.")
    return {
        "ok": True,
        "changed": changed,
        "auth_path": str(target),
        "password_displayed_interactively": True,
    }


def devspace_service_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    environment["DEVSPACE_TOOL_MODE"] = DEVSPACE_TOOL_MODE
    environment["DEVSPACE_OAUTH_SCOPES"] = DEVSPACE_OAUTH_SCOPES
    return environment


def launch_hidden(
    argv: Sequence[str],
    *,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    environment: dict[str, str] | None = None,
    platform_name: str | None = None,
) -> Any:
    platform = platform_name or os.name
    return popen_factory(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        env=environment,
        start_new_session=platform != "nt",
        **windows_subprocess_kwargs(platform),
    )


def apply_setup(
    config: SetupConfig,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    runner: Callable[..., Any] = subprocess.run,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    sleeper: Callable[[float], None] = time.sleep,
    platform_name: str | None = None,
    config_path: Path | None = None,
    owner_password_reviewer: Callable[..., dict[str, Any]] = review_owner_password_interactive,
    terminal_check: Callable[[], bool] = interactive_terminal_available,
) -> None:
    # Init remains DevSpace's own interactive prompt so it can safely retain its
    # Owner credential.  The root list/public origin are displayed before this call.
    slot = funnel_status(config, runner=runner, allow_absent=True)
    if slot.get("mapping") == "conflict":
        raise SetupError("TAILSCALE_FUNNEL_PORT_IN_USE")
    if config_path is not None and config_path.exists():
        persist_existing_setup_config(config_path, config)
    else:
        if not terminal_check():
            raise SetupError("DEVSPACE_FIRST_INIT_REQUIRES_INTERACTIVE_TTY")
        run_interactive_checked(
            command_argv(["npx", "--yes", DEVSPACE_PACKAGE, "init"], platform_name=platform_name),
            runner=runner,
        )
        owner_password_reviewer()
    if config_path is not None:
        persisted = persisted_allowed_roots(config_path)
        missing = [
            root
            for root in config.roots
            if not any(os.path.normcase(str(root)) == os.path.normcase(str(item)) for item in persisted)
        ]
        if missing:
            raise SetupError("DEVSPACE_SETUP_DID_NOT_PERSIST_COMPLETE_ALLOWED_ROOTS")
    run_checked(devspace_native_argv(), runner=runner)
    run_checked(devspace_compat_argv(), runner=runner)
    run_checked(
        devspace_compat_argv(stop_exact_service=True, local_port=config.local_port),
        runner=runner,
    )
    launch_hidden(
        command_argv(["npx", "--yes", DEVSPACE_PACKAGE, "serve"], platform_name=platform_name),
        popen_factory=popen_factory,
        environment=devspace_service_environment(),
        platform_name=platform_name,
    )
    run_checked(
        devspace_compat_argv(confirm_restarted=True, local_port=config.local_port),
        runner=runner,
    )
    ensure_public_route(config, opener=opener, runner=runner, sleeper=sleeper)


def recover_service(
    config: SetupConfig,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    runner: Callable[..., Any] = subprocess.run,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    sleeper: Callable[[float], None] = time.sleep,
    platform_name: str | None = None,
) -> dict[str, Any]:
    """Idempotently restore the managed DevSpace service and its exact Funnel."""
    local = http_probe(config.local_mcp_url, opener=opener)
    service_started = not local.get("ok")
    if service_started:
        run_checked(devspace_native_argv(), runner=runner)
        run_checked(devspace_compat_argv(), runner=runner)
        run_checked(
            devspace_compat_argv(stop_exact_service=True, local_port=config.local_port),
            runner=runner,
        )
        launch_hidden(
            command_argv(["npx", "--yes", DEVSPACE_PACKAGE, "serve"], platform_name=platform_name),
            popen_factory=popen_factory,
            environment=devspace_service_environment(),
            platform_name=platform_name,
        )
        run_checked(
            devspace_compat_argv(confirm_restarted=True, local_port=config.local_port),
            runner=runner,
        )
    result = ensure_public_route(config, opener=opener, runner=runner, sleeper=sleeper)
    return {**result, "service_started": service_started}


def refresh_after_app_registration(
    config: SetupConfig,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    runner: Callable[..., Any] = subprocess.run,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    sleeper: Callable[[float], None] = time.sleep,
    platform_name: str | None = None,
) -> dict[str, Any]:
    """Recycle the managed server after manual ChatGPT OAuth registration.

    DevSpace 1.0.4 can leave a newly approved ChatGPT connector unable to
    create its first tool session until the server is recycled.  This command
    is deliberately explicit: it never opens ChatGPT settings and it preserves
    the existing config, Owner credential, OAuth database, roots, and Funnel
    hostname.
    """
    run_checked(devspace_native_argv(), runner=runner)
    run_checked(devspace_compat_argv(), runner=runner)
    run_checked(
        devspace_compat_argv(stop_exact_service=True, local_port=config.local_port),
        runner=runner,
    )
    launch_hidden(
        command_argv(["npx", "--yes", DEVSPACE_PACKAGE, "serve"], platform_name=platform_name),
        popen_factory=popen_factory,
        environment=devspace_service_environment(),
        platform_name=platform_name,
    )
    run_checked(
        devspace_compat_argv(confirm_restarted=True, local_port=config.local_port),
        runner=runner,
    )
    result = refresh_exact_public_route(config, opener=opener, runner=runner, sleeper=sleeper)
    return {
        **result,
        "service_restarted": True,
        "credentials_preserved": True,
        "next_action": "VERIFY_REGISTERED_CHATGPT_APP_WITH_ORACLE",
        "verification_boundary": (
            "Use a fresh regular Oracle @codex read-only probe; Codex Desktop's "
            "DevSpace plugin tools are a different connector and are not proof of "
            "the manually registered ChatGPT app."
        ),
    }


def _exclusive_exact_funnel_entry(config: SetupConfig, entry: Any) -> bool:
    """Return true only for one root handler owned by this managed route."""
    if not isinstance(entry, dict):
        return False
    handlers = entry.get("Handlers")
    if not isinstance(handlers, dict) or set(handlers) != {"/"}:
        return False
    root = handlers.get("/")
    if not isinstance(root, dict):
        return False
    proxy = str(root.get("Proxy") or "").rstrip("/").casefold()
    expected = f"http://127.0.0.1:{config.local_port}".casefold()
    return proxy == expected


def refresh_exact_public_route(
    config: SetupConfig,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    runner: Callable[..., Any] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Recycle only the exact managed HTTPS slot before reasserting it.

    A matching local Funnel status can survive while its public relay path is
    stale.  Never use the global ``funnel reset`` command here: it would erase
    unrelated ports.  If port 443 has any additional path handlers, preserve
    it and fall back to the non-destructive idempotent check.
    """
    wait_for_local_service(config, opener=opener, sleeper=sleeper)
    current = funnel_status(config, runner=runner, allow_absent=True)
    if current.get("mapping") == "conflict":
        raise SetupError("TAILSCALE_FUNNEL_PORT_IN_USE")
    recycled = current.get("mapping") == "match" and _exclusive_exact_funnel_entry(
        config, current.get("status")
    )
    if recycled:
        run_checked(
            ["tailscale", "funnel", "--bg", f"--https={config.public_port}", "off"],
            runner=runner,
        )
    result = ensure_public_route(config, opener=opener, runner=runner, sleeper=sleeper)
    return {
        **result,
        "exact_funnel_recycled": recycled,
        "funnel_recycle_scope": f"https:{config.public_port}" if recycled else None,
    }


def windows_bootstrap_watchdog_command(codex_home: Path | None = None) -> str:
    root = (codex_home or Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))).resolve()
    script = root / "scripts" / "start_devspace_bootstrap.ps1"
    powershell = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return (
        f'"{powershell}" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass '
        f'-File "{script}" -Mode Watch -WatchIntervalSeconds 300'
    )


def register_windows_bootstrap_watchdog(
    *,
    codex_home: Path | None = None,
    runner: Callable[..., Any] = subprocess.run,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    platform_name: str | None = None,
) -> dict[str, Any]:
    platform = platform_name or os.name
    if platform != "nt":
        return {"ok": True, "changed": False, "platform": platform, "mode": "external-login-service"}
    root = (codex_home or Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))).resolve()
    script = root / "scripts" / "start_devspace_bootstrap.ps1"
    if not script.is_file():
        raise SetupError("DEVSPACE_BOOTSTRAP_WATCHDOG_SCRIPT_MISSING")
    command = windows_bootstrap_watchdog_command(root)
    runner(
        [
            "reg.exe",
            "ADD",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
            "/v",
            WINDOWS_BOOTSTRAP_RUN_NAME,
            "/t",
            "REG_SZ",
            "/d",
            command,
            "/f",
        ],
        check=True,
        text=True,
        capture_output=True,
        **windows_subprocess_kwargs(platform),
    )
    launch_hidden(
        [
            str(Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Mode",
            "Watch",
            "-WatchIntervalSeconds",
            "300",
        ],
        popen_factory=popen_factory,
        platform_name=platform,
    )
    return {
        "ok": True,
        "changed": True,
        "platform": platform,
        "mode": "per-user-login-watchdog",
        "run_name": WINDOWS_BOOTSTRAP_RUN_NAME,
        "watch_interval_seconds": 300,
    }


def wait_for_local_service(
    config: SetupConfig,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    attempts: int = 60,
    delay_seconds: float = 2.0,
) -> dict[str, Any]:
    """Wait for the exact loopback MCP endpoint without accepting a port-only signal."""
    last: dict[str, Any] = {"ok": False, "error": "DEVSPACE_LOCAL_SERVICE_NOT_READY"}
    for index in range(max(1, attempts)):
        last = http_probe(config.local_mcp_url, opener=opener)
        if last.get("ok"):
            return last
        if index + 1 < attempts:
            sleeper(delay_seconds)
    raise SetupError("DEVSPACE_LOCAL_SERVICE_NOT_READY")


def wait_for_public_service(
    config: SetupConfig,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    attempts: int = 30,
    delay_seconds: float = 2.0,
) -> dict[str, Any]:
    """Wait for Funnel relay propagation while retaining the last redacted probe."""
    last: dict[str, Any] = {"ok": False, "error": "DEVSPACE_PUBLIC_ENDPOINT_NOT_READY"}
    for index in range(max(1, attempts)):
        last = http_probe(config.registration_url, opener=opener)
        if last.get("ok"):
            return last
        if index + 1 < attempts:
            sleeper(delay_seconds)
    raise SetupError(
        "DEVSPACE_PUBLIC_ENDPOINT_NOT_READY:" + json.dumps(last, ensure_ascii=True, sort_keys=True)
    )


def ensure_public_route(
    config: SetupConfig,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    runner: Callable[..., Any] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Idempotently restore the exact Funnel mapping after service or network restart."""
    local = wait_for_local_service(config, opener=opener, sleeper=sleeper)
    current = funnel_status(config, runner=runner, allow_absent=True)
    if current.get("mapping") == "conflict":
        raise SetupError("TAILSCALE_FUNNEL_PORT_IN_USE")
    changed = current.get("mapping") != "match"
    if changed:
        run_checked(
            ["tailscale", "funnel", "--bg", f"--https={config.public_port}", f"http://127.0.0.1:{config.local_port}"],
            runner=runner,
        )
    final = funnel_status(config, runner=runner)
    if not final.get("ok"):
        raise SetupError("TAILSCALE_FUNNEL_RESTORE_FAILED")
    public = wait_for_public_service(config, opener=opener, sleeper=sleeper)
    return {"ok": True, "changed": changed, "local": local, "funnel": final, "public": public}


def http_probe(url: str, *, opener: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json, text/plain;q=0.8"})
    try:
        with opener(request, timeout=5) as response:
            return {"ok": response.status in {200, 401, 403, 405, 406}, "status": response.status, "url": url}
    except urllib.error.HTTPError as error:
        return {"ok": error.code in {401, 403, 405, 406}, "status": error.code, "url": url}
    except OSError as error:
        return {"ok": False, "error": type(error).__name__, "url": url}


def persisted_config(config_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SetupError("DEVSPACE_CONFIG_UNREADABLE") from error
    if not isinstance(payload, dict):
        raise SetupError("DEVSPACE_CONFIG_UNREADABLE")
    return payload


def persisted_allowed_roots(config_path: Path) -> tuple[Path, ...]:
    payload = persisted_config(config_path)
    values = payload.get("allowedRoots")
    if not isinstance(values, list) or not values:
        raise SetupError("DEVSPACE_CONFIG_ALLOWED_ROOTS_MISSING")
    roots: list[Path] = []
    for value in values:
        candidate = Path(str(value)).expanduser()
        if not candidate.is_absolute():
            raise SetupError("DEVSPACE_CONFIG_ALLOWED_ROOT_INVALID")
        roots.append(candidate.resolve())
    return tuple(roots)


def persist_existing_setup_config(config_path: Path, config: SetupConfig) -> Path:
    """Atomically update non-secret DevSpace config while preserving auth state."""
    if config_path.is_symlink():
        raise SetupError("DEVSPACE_CONFIG_SYMLINK_UNSUPPORTED")
    payload = persisted_config(config_path)
    backup_path = config_path.with_name(f"{config_path.name}.bak-{time.time_ns()}")
    shutil.copy2(config_path, backup_path)
    payload.update(
        {
            "host": payload.get("host") or "127.0.0.1",
            "port": config.local_port,
            "allowedRoots": [str(root) for root in config.roots],
            "publicBaseUrl": config.public_origin,
        }
    )
    temporary = config_path.with_name(f".{config_path.name}.tmp-{time.time_ns()}")
    try:
        temporary.write_text(
            # Keep persisted configuration ASCII-only. Windows PowerShell 5.1
            # decodes BOM-less Get-Content input with the active ANSI code page;
            # JSON escapes preserve Unicode roots under both that reader and UTF-8.
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        # Parse the staged bytes strictly before replacing the live file.
        persisted_config(temporary)
        os.replace(temporary, config_path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup_path


def synchronize_existing_bootstrap_config(bootstrap_path: Path, config: SetupConfig) -> Path | None:
    """Keep the diagnostic bootstrap mirror aligned without making it runtime authority."""
    if not bootstrap_path.exists():
        return None
    if bootstrap_path.is_symlink():
        raise SetupError("DEVSPACE_BOOTSTRAP_CONFIG_SYMLINK_UNSUPPORTED")
    payload = persisted_config(bootstrap_path)
    if payload.get("schema") != "codexpro.devspace-bootstrap/v1":
        raise SetupError("DEVSPACE_BOOTSTRAP_CONFIG_SCHEMA_UNSUPPORTED")
    backup_path = bootstrap_path.with_name(f"{bootstrap_path.name}.bak-{time.time_ns()}")
    shutil.copy2(bootstrap_path, backup_path)
    payload.update(
        {
            "roots": [str(root) for root in config.roots],
            "hostname": config.hostname,
            "local_port": config.local_port,
            "public_port": config.public_port,
        }
    )
    temporary = bootstrap_path.with_name(f".{bootstrap_path.name}.tmp-{time.time_ns()}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        persisted_config(temporary)
        os.replace(temporary, bootstrap_path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup_path


def persisted_tool_mode(config_path: Path) -> str | None:
    payload = persisted_config(config_path)
    value = payload.get("toolMode") if "toolMode" in payload else payload.get("tool_mode")
    return str(value).casefold() if isinstance(value, str) else None


def merge_persisted_setup_roots(
    config: SetupConfig,
    config_path: Path,
) -> tuple[SetupConfig, tuple[Path, ...]]:
    """Preserve current allowedRoots when setup is invoked with only new roots."""
    if not config_path.exists():
        return config, ()
    existing = persisted_allowed_roots(config_path)
    merged = list(existing)
    preserved: list[Path] = []
    keys = {os.path.normcase(os.path.normpath(str(root))) for root in merged}
    requested_keys = {
        os.path.normcase(os.path.normpath(str(root)))
        for root in config.roots
    }
    for root in existing:
        if os.path.normcase(os.path.normpath(str(root))) not in requested_keys:
            preserved.append(root)
    for root in config.roots:
        key = os.path.normcase(os.path.normpath(str(root)))
        if key not in keys:
            merged.append(root)
            keys.add(key)
    merged_config = validate_config(
        [str(root) for root in merged],
        config.hostname,
        config.local_port,
        config.public_port,
    )
    return merged_config, tuple(preserved)


def funnel_status(
    config: SetupConfig | None = None,
    *,
    runner: Callable[..., Any] = subprocess.run,
    allow_absent: bool = False,
) -> dict[str, Any]:
    try:
        result = runner(
            ["tailscale", "funnel", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            **windows_subprocess_kwargs(),
        )
    except OSError as error:
        return {"ok": False, "error": type(error).__name__}
    if result.returncode != 0:
        return {"ok": False, "returncode": result.returncode, "stderr": redact(result.stderr or "")}
    try:
        status = json.loads(result.stdout)
        if config is not None:
            web = status.get("Web") if isinstance(status, dict) else {}
            key = f"{config.hostname}:{config.public_port}"
            entry = web.get(key) if isinstance(web, dict) else None
            if entry is None:
                return {
                    "ok": bool(allow_absent),
                    "mapping": "absent",
                    "error": None if allow_absent else "TAILSCALE_FUNNEL_MAPPING_MISSING",
                }
            flattened = json.dumps(entry, ensure_ascii=False).casefold()
            if str(config.local_port) not in flattened:
                return {"ok": False, "mapping": "conflict", "error": "TAILSCALE_FUNNEL_MAPPING_MISMATCH"}
            return {"ok": True, "mapping": "match", "status": entry}
        return {"ok": True, "status": status}
    except json.JSONDecodeError:
        return {"ok": False, "error": "TAILSCALE_STATUS_JSON_INVALID"}


def discover_tailscale_hostname(*, runner: Callable[..., Any] = subprocess.run) -> str:
    try:
        result = runner(
            ["tailscale", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            **windows_subprocess_kwargs(),
        )
    except OSError as exc:
        raise SetupError("TAILSCALE_NOT_INSTALLED") from exc
    if result.returncode != 0:
        raise SetupError("TAILSCALE_NOT_CONNECTED")
    try:
        value = json.loads(result.stdout)
        hostname = str((value.get("Self") or {}).get("DNSName") or "").strip().lower().rstrip(".")
    except (AttributeError, json.JSONDecodeError) as exc:
        raise SetupError("TAILSCALE_STATUS_JSON_INVALID") from exc
    if not HOSTNAME_PATTERN.fullmatch(hostname):
        raise SetupError("TAILSCALE_HOSTNAME_UNAVAILABLE")
    return hostname


def doctor(config: SetupConfig, *, opener: Callable[..., Any] = urllib.request.urlopen, runner: Callable[..., Any] = subprocess.run, chatgpt_call_failed: bool = False, config_path: Path | None = None) -> dict[str, Any]:
    tool_mode: dict[str, Any] = {
        "required": DEVSPACE_TOOL_MODE,
        "managed_launch": DEVSPACE_TOOL_MODE,
        "configured": None,
        "effective": None,
        "effective_observable": False,
    }
    local = http_probe(config.local_mcp_url, opener=opener)
    if not local.get("ok"):
        return {
            "local": local,
            "tool_mode": tool_mode,
            "registration_url": config.registration_url,
            "recommended_app_name": APP_NAME,
            "next_action": "CHECK_DEVSPACE_LOCAL_SERVICE",
        }
    if config_path is not None:
        try:
            configured_roots = persisted_allowed_roots(config_path)
            configured_tool_mode = persisted_tool_mode(config_path)
        except SetupError as error:
            return {
                "local": local,
                "config": {"ok": False, "error": str(error), "path": str(config_path)},
                "tool_mode": tool_mode,
                "registration_url": config.registration_url,
                "recommended_app_name": APP_NAME,
                "next_action": "CHECK_DEVSPACE_CONFIG",
            }
        missing = [root for root in config.roots if root not in configured_roots]
        if missing:
            return {
                "local": local,
                "config": {
                    "ok": False,
                    "path": str(config_path),
                    "configured_roots": [str(root) for root in configured_roots],
                    "missing_roots": [str(root) for root in missing],
                },
                "tool_mode": tool_mode,
                "registration_url": config.registration_url,
                "recommended_app_name": APP_NAME,
                "next_action": "CHECK_DEVSPACE_ALLOWED_ROOTS",
            }
        tool_mode["configured"] = configured_tool_mode
        # DevSpace gives its process environment precedence over this persisted
        # value, and a generic HTTP probe cannot recover a running process
        # environment. Therefore the effective mode stays explicitly unknown.
        if configured_tool_mode is not None and configured_tool_mode != DEVSPACE_TOOL_MODE:
            return {
                "local": local,
                "config": {"ok": True, "path": str(config_path), "configured_roots": [str(root) for root in configured_roots]},
                "tool_mode": tool_mode,
                "registration_url": config.registration_url,
                "recommended_app_name": APP_NAME,
                "next_action": "CHECK_DEVSPACE_TOOL_MODE",
            }
    funnel = funnel_status(config, runner=runner)
    if not funnel.get("ok"):
        return {
            "local": local,
            "funnel": funnel,
            "tool_mode": tool_mode,
            "registration_url": config.registration_url,
            "recommended_app_name": APP_NAME,
            "next_action": "CHECK_TAILSCALE_FUNNEL",
        }
    public = http_probe(config.registration_url, opener=opener)
    report: dict[str, Any] = {
        "local": local,
        "funnel": funnel,
        "public": public,
        "registration_url": config.registration_url,
        "recommended_app_name": APP_NAME,
        "tool_mode": tool_mode,
    }
    if public.get("ok") and chatgpt_call_failed:
        report["next_action"] = "POST_REGISTER_REFRESH_OR_EXTERNAL_APP_CHECK"
        report["message"] = (
            "Public endpoint is healthy. If manual registration or reconnect just completed, "
            "run post-register once and verify the registered app with a fresh regular Oracle "
            "@codex read-only probe. Do not use Codex Desktop DevSpace plugin tools as proof, "
            "and do not automate or repeat app registration."
        )
    elif not public.get("ok"):
        report["next_action"] = "CHECK_PUBLIC_FUNNEL_ENDPOINT"
    else:
        report["next_action"] = "READY"
    return report


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    sub = value.add_subparsers(dest="command", required=True)
    for name in ("setup", "doctor", "ensure", "recover", "post-register"):
        command = sub.add_parser(name)
        command.add_argument("--root", action="append", default=[], help="Narrow allowed DevSpace root; repeat as needed")
        command.add_argument("--hostname", help="Tailscale MagicDNS hostname; auto-detected when omitted")
        command.add_argument("--local-port", type=int, default=DEFAULT_PORT)
        command.add_argument("--public-port", type=int, default=443)
    setup = sub.choices["setup"]
    setup.add_argument("--dry-run", action="store_true")
    setup.add_argument("--apply", action="store_true")
    sub.choices["doctor"].add_argument("--chatgpt-call-failed", action="store_true")
    owner = sub.add_parser("owner-password")
    owner.add_argument("--auth-path", type=Path)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "owner-password":
            result = review_owner_password_interactive(auth_path=args.auth_path)
            print(json.dumps(result, ensure_ascii=True, indent=2))
            return 0
        hostname = args.hostname or discover_tailscale_hostname()
        config = validate_config(args.root, hostname, args.local_port, args.public_port)
        if args.command == "setup":
            if args.dry_run == args.apply:
                raise SetupError("CHOOSE_EXACTLY_ONE_OF_DRY_RUN_OR_APPLY")
            config_path = Path.home() / ".devspace" / "config.json"
            requested_roots = config.roots
            config, preserved_roots = merge_persisted_setup_roots(config, config_path)
            plan = setup_plan(
                config,
                requested_roots=requested_roots,
                preserved_existing_roots=preserved_roots,
            )
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            if args.apply:
                apply_setup(config, config_path=config_path)
                synchronize_existing_bootstrap_config(
                    Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
                    / "config"
                    / "codexpro-devspace-bootstrap.json",
                    config,
                )
                register_windows_bootstrap_watchdog()
            return 0
        if args.command == "ensure":
            print(json.dumps(ensure_public_route(config), ensure_ascii=False, indent=2))
            return 0
        if args.command == "recover":
            print(json.dumps(recover_service(config), ensure_ascii=False, indent=2))
            return 0
        if args.command == "post-register":
            print(json.dumps(refresh_after_app_registration(config), ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(doctor(
            config,
            chatgpt_call_failed=args.chatgpt_call_failed,
            config_path=Path.home() / ".devspace" / "config.json",
        ), ensure_ascii=False, indent=2))
        return 0
    except SetupError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
