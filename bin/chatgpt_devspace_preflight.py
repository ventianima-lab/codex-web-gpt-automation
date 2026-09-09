from __future__ import annotations

"""Lightweight first-use exact-root qualification for DevSpace transports."""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


QUALIFICATION_SCHEMA = "codex.chatgpt.devspace-root-qualification/v1"
APP_READ_RESULT_SCHEMA = "codex.chatgpt.registered-app-read-result/v1"
# Compatibility export for callers that have not yet renamed the factory.
PRO_APP_READ_GATE_SCHEMA = APP_READ_RESULT_SCHEMA


class DevSpacePreflightError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


def _load_onboarding_module() -> Any:
    path = Path(__file__).resolve().with_name("codex_web_gpt_onboarding.py")
    name = "codex_web_gpt_onboarding_devspace_gate"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"onboarding verifier unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def ensure_registered_app_read_result(
    project_root: Path,
    app_name: str,
    *,
    codex_home: Path | None = None,
    onboarding_loader: Callable[[], Any] = _load_onboarding_module,
) -> dict[str, Any]:
    """Validate the durable, one-time registered-app read result for a root.

    The setup result is intentionally small: exact root, app identity,
    authenticated access, one observed model, and a terminal outcome.  It does
    not prescribe a tool sequence and does not expire merely because a later
    run starts.
    """
    root = project_root.expanduser().resolve()
    expected_app = str(app_name or "").strip()
    state_file = (
        (codex_home or (Path.home() / ".codex")).expanduser().resolve()
        / "state"
        / "codex-web-gpt-automation"
        / "onboarding"
        / "state.json"
    )

    reason = "missing-or-invalid-app-read-result"
    recorded: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    try:
        onboarding = onboarding_loader()
        state = onboarding.load_state(codex_home=codex_home)
        resolved_home = onboarding._codex_home(codex_home)
        candidate = onboarding._final_gate_receipt(
            resolved_home,
            Path.home() / ".devspace",
            state,
        )
        if isinstance(candidate, dict):
            recorded = candidate
    except Exception:
        recorded = None

    if isinstance(state, dict) and recorded is not None:
        configured_app = str(state.get("app_name") or "").strip()
        roots = {
            _path_key(Path(str(value)).expanduser().resolve())
            for value in state.get("allowed_roots") or []
        }
        try:
            recorded_root = Path(str(recorded.get("root") or "")).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            recorded_root = None
        actual_model = str(recorded.get("actual_model") or "").strip()
        recorded_at = str(recorded.get("recorded_at") or "").strip()
        if configured_app != expected_app:
            reason = "registered-app-name-mismatch"
        elif _path_key(root) not in roots:
            reason = "exact-root-not-covered-by-verified-app"
        elif recorded_root is None or _path_key(recorded_root) != _path_key(root):
            reason = "app-read-root-mismatch"
        elif recorded.get("auth_verified") is not True:
            reason = "app-auth-not-verified"
        elif not actual_model or len(actual_model) > 128 or any(ord(char) < 32 for char in actual_model):
            reason = "actual-model-invalid"
        elif recorded.get("read_ok") is not True or recorded.get("outcome") != "captured":
            reason = "app-read-outcome-not-captured"
        elif not recorded_at:
            reason = "app-read-recorded-at-missing"
        else:
            return {
                "schema": APP_READ_RESULT_SCHEMA,
                "qualified": True,
                "project_root": str(root),
                "app_name": configured_app,
                "auth_verified": True,
                "actual_model": actual_model,
                "outcome": "captured",
                "recorded_at": recorded_at,
                "state_path": str(state_file),
                "setup_once": True,
            }

    evidence = {
        "project_root": str(root),
        "app_name": expected_app,
        "state_path": str(state_file),
        "reason": reason,
        "required": {
            "exact_root": True,
            "registered_app": True,
            "auth_verified": True,
            "actual_model": "one non-empty observed model",
            "outcome": "captured",
        },
        "next_action": "COMPLETE_REGISTERED_APP_READ_CHECK_ONCE",
        "instructions": "Complete onboarding stage 08_final_gate once for this app and exact root.",
    }

    raise DevSpacePreflightError(
        "REGISTERED_APP_READ_RESULT_REQUIRED",
        "the registered app needs one successful authenticated read result for this exact root",
        evidence,
    )


def ensure_recent_registered_app_read_gate(
    project_root: Path,
    app_name: str,
    *,
    codex_home: Path | None = None,
    onboarding_loader: Callable[[], Any] = _load_onboarding_module,
) -> dict[str, Any]:
    """Compatibility alias; the durable setup result has no freshness gate."""
    return ensure_registered_app_read_result(
        project_root,
        app_name,
        codex_home=codex_home,
        onboarding_loader=onboarding_loader,
    )


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _registration_url(bootstrap_path: Path) -> str | None:
    try:
        payload = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        hostname = str(payload.get("hostname") or "").strip().lower().rstrip(".")
        public_port = int(payload.get("public_port") or 443)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not hostname:
        return None
    suffix = "" if public_port == 443 else f":{public_port}"
    return f"https://{hostname}{suffix}/mcp"


def _next_action(
    project_root: Path,
    configured_roots: list[Path],
    *,
    registration_url: str | None,
) -> dict[str, Any]:
    roots = [*configured_roots]
    if all(_path_key(root) != _path_key(project_root) for root in roots):
        roots.append(project_root)
    setup_script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "chatgpt-workspace-setup"
        / "scripts"
        / "devspace_tailscale_setup.py"
    )
    setup_argv = [sys.executable, str(setup_script), "setup"]
    for root in roots:
        setup_argv.extend(["--root", str(root)])
    if registration_url:
        hostname = registration_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        setup_argv.extend(["--hostname", hostname])
    setup_argv.append("--dry-run")
    return {
        "next_action": "REGISTER_EXACT_DEVSPACE_ROOT_BEFORE_ORACLE_SUBMISSION",
        "setup_argv": setup_argv,
        "doctor_after_registration": True,
        "registration_url": registration_url,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def ensure_exact_root_qualified(
    project_root: Path,
    *,
    config_path: Path | None = None,
    qualification_root: Path | None = None,
    bootstrap_path: Path | None = None,
    json_loader: Callable[[str], Any] = json.loads,
) -> dict[str, Any]:
    """Qualify an exact root from local config, caching by config byte hash.

    This is deliberately not an endpoint, OAuth, ChatGPT-app, or read probe.
    The first use parses the current allowedRoots.  Later uses reuse the receipt
    while the exact config bytes remain unchanged; a config change revalidates.
    """
    try:
        root = project_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise DevSpacePreflightError(
            "DEVSPACE_EXACT_ROOT_UNAVAILABLE",
            "the exact project root does not exist",
            {"missing_root": str(project_root)},
        ) from exc
    if not root.is_dir():
        raise DevSpacePreflightError(
            "DEVSPACE_EXACT_ROOT_UNAVAILABLE",
            "the exact project root is not a directory",
            {"missing_root": str(root)},
        )

    config_file = (config_path or (Path.home() / ".devspace" / "config.json")).resolve()
    bootstrap_file = (
        bootstrap_path
        or (Path.home() / ".codex" / "config" / "codexpro-devspace-bootstrap.json")
    ).resolve()
    registration_url = _registration_url(bootstrap_file)
    try:
        config_bytes = config_file.read_bytes()
    except OSError as exc:
        action = _next_action(root, [], registration_url=registration_url)
        raise DevSpacePreflightError(
            "DEVSPACE_EXACT_ROOT_UNAVAILABLE",
            "DevSpace config is unavailable before the first submission for this project",
            {"missing_root": str(root), "config_path": str(config_file), **action},
        ) from exc

    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    state_root = (
        qualification_root
        or Path(
            os.environ.get("CODEX_DEVSPACE_QUALIFICATION_ROOT")
            or (Path.home() / ".codex" / "state" / "chatgpt-oracle" / "devspace-qualifications")
        )
    ).resolve()
    receipt_path = state_root / f"{hashlib.sha256(_path_key(root).encode('utf-8')).hexdigest()[:24]}.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        receipt = None
    if (
        isinstance(receipt, dict)
        and receipt.get("schema") == QUALIFICATION_SCHEMA
        and _path_key(Path(str(receipt.get("project_root") or ""))) == _path_key(root)
        and receipt.get("config_sha256") == config_sha256
        and receipt.get("qualified") is True
    ):
        return {**receipt, "cached": True, "receipt_path": str(receipt_path)}

    try:
        payload = json_loader(config_bytes.decode("utf-8", errors="strict"))
        values = payload.get("allowedRoots") if isinstance(payload, dict) else None
        if not isinstance(values, list) or not values:
            raise ValueError("allowedRoots missing")
        configured_roots = [Path(str(value)).expanduser().resolve() for value in values]
    except (UnicodeDecodeError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        action = _next_action(root, [], registration_url=registration_url)
        raise DevSpacePreflightError(
            "DEVSPACE_EXACT_ROOT_UNAVAILABLE",
            "DevSpace allowedRoots cannot be verified",
            {"missing_root": str(root), "config_path": str(config_file), **action},
        ) from exc

    exact = next((item for item in configured_roots if _path_key(item) == _path_key(root)), None)
    if exact is None:
        action = _next_action(root, configured_roots, registration_url=registration_url)
        raise DevSpacePreflightError(
            "DEVSPACE_EXACT_ROOT_UNAVAILABLE",
            "the exact project root is not registered in DevSpace allowedRoots",
            {
                "missing_root": str(root),
                "configured_roots": [str(item) for item in configured_roots],
                "config_path": str(config_file),
                **action,
            },
        )

    receipt = {
        "schema": QUALIFICATION_SCHEMA,
        "qualified": True,
        "project_root": str(root),
        "allowed_root": str(exact),
        "config_path": str(config_file),
        "config_sha256": config_sha256,
        "qualified_at": datetime.now(timezone.utc).isoformat(),
        "registration_url": registration_url,
    }
    _write_json_atomic(receipt_path, receipt)
    return {**receipt, "cached": False, "receipt_path": str(receipt_path)}
