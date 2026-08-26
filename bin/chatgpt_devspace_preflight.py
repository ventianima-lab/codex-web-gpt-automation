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
PRO_APP_READ_GATE_SCHEMA = "codex.chatgpt.pro-devspace-app-read-gate/v1"
PRO_APP_READ_GATE_MAX_AGE_SECONDS = 24 * 60 * 60
REGISTERED_APP_ACTION_SNAPSHOT_GATE_ERRORS = frozenset(
    {
        "FINAL_GATE_TOOL_READ_RECEIPTS_MISSING_OR_DUPLICATE",
        "FINAL_GATE_TOOL_READ_RECEIPT_AUDIT_NONCE_MISSING",
        "FINAL_GATE_CONVERSATION_RECEIPT_CHALLENGE_MISSING",
    }
)


REGISTERED_APP_ACTION_SNAPSHOT_GUIDANCE = {
    "ko": [
        "새 일반 비-Pro canary에서 read_chunk 또는 서버 생성 Audit receipt ID가 보이지 않으면, 서버가 아니라 ChatGPT의 등록 앱 Action 스냅샷이 오래된 상태로 취급합니다.",
        "Enterprise/Edu 관리자는 ChatGPT의 Workspace settings > Apps에서 정확한 codex 앱의 더보기 메뉴를 열고 Action control > Refresh를 직접 실행한 뒤 새 Action을 검토·활성화합니다. 이 과정은 자동화하지 않습니다.",
        "Business 또는 Refresh가 없는 UI에서는 같은 정확한 /mcp URL로 앱을 다시 만들고 게시한 뒤, 현재 서버가 노출한 Action을 검토·활성화하고 Owner 승인을 직접 완료합니다.",
        "그 뒤 post-register를 한 번 실행하고, 새 일반 비-Pro auditNonce canary에서 open_workspace, read, read_chunk 및 세 서버 생성 receipt ID를 다시 증명합니다. open_workspace/read만으로는 통과하지 않습니다.",
    ],
    "en": [
        "If a fresh regular non-Pro canary exposes no read_chunk or server-generated Audit receipt IDs, treat the registered ChatGPT app Action snapshot as stale rather than accepting the partial tool surface.",
        "On Enterprise/Edu, an admin must open the exact codex app's overflow menu under Workspace settings > Apps, use Action control > Refresh, and review and enable the new Actions. Do not automate this ChatGPT setting.",
        "On Business, or when Refresh is unavailable, recreate and publish the app with the same exact /mcp URL, review and enable the Actions currently exposed by the server, and complete Owner approval manually.",
        "Then run post-register once and run a fresh regular non-Pro auditNonce canary proving open_workspace, read, read_chunk, and all three server-generated receipt IDs. open_workspace/read alone never passes.",
    ],
}


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


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def ensure_recent_registered_app_read_gate(
    project_root: Path,
    app_name: str,
    *,
    codex_home: Path | None = None,
    devspace_home: Path | None = None,
    now: datetime | None = None,
    max_age_seconds: int = PRO_APP_READ_GATE_MAX_AGE_SECONDS,
    onboarding_loader: Callable[[], Any] = _load_onboarding_module,
) -> dict[str, Any]:
    """Require a recent cryptographic regular-run read proof before Pro.

    The proof is deliberately produced by the ordinary non-Pro onboarding
    final gate.  A local HTTP health check, allowedRoots entry, successful
    ``open_workspace`` call, or model-authored marker is not enough: the gate
    revalidates the exact open/read/read_chunk receipts and conversation echo.
    This function is read-only and is safe to call from ``--dry-run``.
    """
    if not isinstance(max_age_seconds, int) or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a positive integer")
    root = project_root.expanduser().resolve()
    expected_app = str(app_name or "").strip()
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state_file = (
        (codex_home or (Path.home() / ".codex")).expanduser().resolve()
        / "state"
        / "codex-web-gpt-automation"
        / "onboarding"
        / "state.json"
    )

    reason = "missing-or-invalid-final-gate"
    final_gate_error = None
    recorded: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    try:
        onboarding = onboarding_loader()
        state = onboarding.load_state(codex_home=codex_home)
        resolved_home = onboarding._codex_home(codex_home)
        resolved_devspace = (
            devspace_home or (Path.home() / ".devspace")
        ).expanduser().resolve()
        candidate = onboarding._final_gate_receipt(
            resolved_home,
            resolved_devspace,
            state,
        )
        if isinstance(candidate, dict):
            recorded = candidate
    except Exception as exc:
        # Preserve only a stable, non-secret verifier code.  This lets callers
        # distinguish an absent final-gate proof from the common case where a
        # registered app still exposes an old Action snapshot without relaxing
        # the proof requirement.
        candidate_code = str(getattr(exc, "code", "") or "").strip()
        if not candidate_code:
            candidate_code = str(exc).strip()
        if (
            candidate_code.startswith("FINAL_GATE_")
            and candidate_code == candidate_code.upper()
            and candidate_code.replace("_", "").isalnum()
        ):
            final_gate_error = candidate_code
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
        recorded_at = _parse_utc(recorded.get("recorded_at"))
        age_seconds = (
            (checked_at - recorded_at).total_seconds()
            if recorded_at is not None
            else None
        )
        if configured_app != expected_app:
            reason = "registered-app-name-mismatch"
        elif _path_key(root) not in roots:
            reason = "exact-root-not-covered-by-verified-app"
        elif recorded_root is None or _path_key(recorded_root) != _path_key(root):
            reason = "final-gate-root-mismatch"
        elif age_seconds is None or age_seconds < -300:
            reason = "final-gate-time-invalid"
        elif age_seconds > max_age_seconds:
            reason = "final-gate-expired"
        else:
            return {
                "schema": PRO_APP_READ_GATE_SCHEMA,
                "qualified": True,
                "project_root": str(root),
                "app_name": configured_app,
                "evidence_root": str(recorded.get("root") or ""),
                "recorded_at": recorded_at.isoformat(),
                "checked_at": checked_at.isoformat(),
                "age_seconds": max(0, int(age_seconds)),
                "max_age_seconds": max_age_seconds,
                "run_id": str(recorded.get("run_id") or ""),
                "conversation_url": str(recorded.get("conversation_url") or ""),
                "state_path": str(state_file),
                "receipt_count": len(recorded.get("tool_read_receipts") or []),
            }

    manual_snapshot_action_required = (
        final_gate_error in REGISTERED_APP_ACTION_SNAPSHOT_GATE_ERRORS
    )
    evidence = {
        "project_root": str(root),
        "app_name": expected_app,
        "state_path": str(state_file),
        "reason": reason,
        "max_age_seconds": max_age_seconds,
        "required_transport": "devspace",
        "required_model": "gpt-5.6",
        "required_thinking_time": "extra-high",
        "required_tools": ["open_workspace", "read", "read_chunk"],
        "next_action": "RUN_FRESH_REGULAR_NON_PRO_FINAL_GATE_CANARY",
        "instructions": "Complete or refresh onboarding stage 08_final_gate, then rerun the same Pro dry-run.",
        "final_gate_error": final_gate_error,
        "manual_chatgpt_action_required": manual_snapshot_action_required,
        # A failed canary is intentionally not persisted as successful final-gate
        # evidence. Keep the generic error useful without inferring that a
        # manual settings change is authorized: callers may show this branch
        # only when their observed canary matches its explicit condition.
        "conditional_registered_app_action_snapshot_guidance": REGISTERED_APP_ACTION_SNAPSHOT_GUIDANCE,
    }
    if manual_snapshot_action_required:
        evidence.update(
            {
                "registered_app_action_snapshot_guidance": REGISTERED_APP_ACTION_SNAPSHOT_GUIDANCE,
                "post_refresh_actions": [
                    "RUN_POST_REGISTER_ONCE",
                    "RUN_FRESH_REGULAR_NON_PRO_AUDIT_NONCE_CANARY",
                ],
            }
        )

    raise DevSpacePreflightError(
        "PRO_DEVSPACE_APP_READ_GATE_REQUIRED",
        "read-only Pro is blocked until a fresh regular non-Pro registered-app canary proves open_workspace, read, and read_chunk",
        evidence,
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
