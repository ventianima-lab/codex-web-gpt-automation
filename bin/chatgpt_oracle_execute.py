from __future__ import annotations

"""Small ordinary Oracle executor.

This is intentionally separate from the historical workflow/state machinery in
``chatgpt_oracle_run.py`` and ``chatgpt_oracle_state.py``.  New executions have
one mission, one owned browser tab, one model/effort check, and one durable
capture.  Historical commands remain available only through their explicit
recovery entry points.
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence


BIN = Path(__file__).resolve().parent
MANIFEST_SCHEMA = "codex.chatgpt.oracle-execution/v1"
STATE_SCHEMA = "codex.chatgpt.oracle-execution-state/v1"
CHATGPT_URL = "https://chatgpt.com/?temporary-chat=true"
DEFAULT_APP_NAME = "codex"
DEFAULT_MODEL = "latest"
DEFAULT_EFFORT = "pro"
SUPPORTED_MODELS = ("latest", "gpt-5.6-sol")
SUPPORTED_EFFORTS = ("pro", "extra-high")
ORACLE_CARRIER_MODEL = "gpt-5.6-sol"
ORACLE_CURRENT_STRATEGY = "current"
ORACLE_EXPLICIT_STRATEGY = "select"
TERMINAL_ORACLE_STATES = frozenset({"complete", "completed", "done", "finished"})
UNRESOLVED_STATUSES = frozenset({"prepared", "running", "attention_required"})
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,95}$")
THREAD_ID_RE = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)
APP_NAME_RE = re.compile(r"^[^\r\n@][^\r\n]*$")
TARGET_ID_RE = re.compile(r"^[A-Fa-f0-9]{8,64}$")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PICKER_PROOF_PREFIX = "[browser] Picker DOM proof:"
MODEL_EVIDENCE_PREFIX = "[browser] Model selection evidence:"
THINKING_PREFIX = "[browser] Thinking time:"
ALLOWED_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "project_root",
        "mission_path",
        "run_root",
        "run_id",
        "source_thread_id",
        "model",
        "effort",
        "app_name",
    }
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNTIME = _load("chatgpt_oracle_execute_runtime", BIN / "chatgpt_oracle_runtime.py")
COMPAT = _load("chatgpt_oracle_execute_compat", BIN / "chatgpt_oracle_compat.py")


class ExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = dict(evidence or {})

    def envelope(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": str(self), "evidence": self.evidence}}


@dataclass(frozen=True)
class ExecutionConfig:
    project_root: Path
    mission_path: Path
    mission_sha256: str
    run_root: Path
    run_id: str
    source_thread_id: str | None
    model: str
    effort: str
    app_name: str
    copy_profile: Path


def _absolute_path(value: str | Path | None, *, label: str, must_exist: bool) -> Path:
    raw = Path(str(value or "")).expanduser()
    if not raw.is_absolute():
        raise ExecutionError(f"{label.upper()}_ABSOLUTE_REQUIRED", f"{label} must be absolute", {"path": str(raw)})
    try:
        return raw.resolve(strict=must_exist)
    except OSError as exc:
        raise ExecutionError(f"{label.upper()}_INVALID", f"{label} could not be resolved", {"path": str(raw)}) from exc


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_model(value: str | None) -> str:
    model = str(value or DEFAULT_MODEL).strip().casefold()
    if model not in SUPPORTED_MODELS:
        raise ExecutionError("MODEL_UNSUPPORTED", "model is not supported by the lean Oracle route", {"supported": list(SUPPORTED_MODELS)})
    return model


def _normalize_effort(value: str | None) -> str:
    effort = str(value or DEFAULT_EFFORT).strip().casefold().replace("_", "-")
    if effort not in SUPPORTED_EFFORTS:
        raise ExecutionError("EFFORT_UNSUPPORTED", "effort is not supported by the lean Oracle route", {"supported": list(SUPPORTED_EFFORTS)})
    return effort


def _normalize_app_name(value: str | None) -> str:
    app_name = str(value or DEFAULT_APP_NAME).strip().lstrip("@").strip()
    if not app_name or APP_NAME_RE.fullmatch(app_name) is None:
        raise ExecutionError("APP_NAME_INVALID", "app_name must be one nonempty line without a leading @")
    return app_name


def _default_run_root(project_root: Path) -> Path:
    base = Path(os.environ.get("CODEX_ORACLE_STATE_ROOT") or (Path.home() / ".codex" / "state" / "chatgpt-oracle")).expanduser().resolve()
    key = hashlib.sha256(str(project_root).casefold().encode("utf-8")).hexdigest()[:24]
    return base / "ordinary" / "projects" / key / "runs"


def make_config(
    *,
    project_root: str | Path,
    mission_path: str | Path,
    run_root: str | Path | None = None,
    run_id: str | None = None,
    source_thread_id: str | None = None,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    app_name: str = DEFAULT_APP_NAME,
) -> ExecutionConfig:
    root = _absolute_path(project_root, label="project_root", must_exist=True)
    if not root.is_dir():
        raise ExecutionError("PROJECT_ROOT_NOT_DIRECTORY", "project_root must identify a directory")
    if root.parent == root:
        raise ExecutionError("PROJECT_ROOT_TOO_BROAD", "project_root must not be a filesystem or drive root")
    raw_mission = Path(str(mission_path)).expanduser()
    if raw_mission.is_symlink():
        raise ExecutionError("MISSION_FILE_INVALID", "mission_path must not be a symlink", {"path": str(raw_mission)})
    mission = _absolute_path(raw_mission, label="mission_path", must_exist=True)
    if not mission.is_file() or not _is_within(root, mission):
        raise ExecutionError("MISSION_OUTSIDE_APPROVED_ROOT", "mission_path must be a regular file inside project_root")
    try:
        mission.read_bytes().decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExecutionError("MISSION_UTF8_REQUIRED", "mission_path must contain valid UTF-8", {"offset": exc.start}) from exc
    actual_run_root = _absolute_path(run_root, label="run_root", must_exist=False) if run_root else _default_run_root(root)
    if _is_within(root, actual_run_root) or _is_within(actual_run_root, root):
        raise ExecutionError("RUN_ROOT_OVERLAPS_PROJECT", "run_root must be disjoint from the approved project root")
    actual_run_id = str(run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}").strip()
    if RUN_ID_RE.fullmatch(actual_run_id) is None:
        raise ExecutionError("RUN_ID_INVALID", "run_id must be a safe 8-96 character identifier")
    explicit_thread = str(source_thread_id or "").strip().casefold()
    environment_thread = str(os.environ.get("CODEX_THREAD_ID") or "").strip().casefold()
    if explicit_thread and THREAD_ID_RE.fullmatch(explicit_thread) is None:
        raise ExecutionError("SOURCE_THREAD_ID_INVALID", "source_thread_id must be a Codex task UUID")
    if environment_thread and THREAD_ID_RE.fullmatch(environment_thread) is None:
        raise ExecutionError("SOURCE_THREAD_ID_INVALID", "CODEX_THREAD_ID must be a Codex task UUID when set")
    if explicit_thread and environment_thread and explicit_thread != environment_thread:
        raise ExecutionError("SOURCE_THREAD_ID_MISMATCH", "manifest task owner does not match the current Codex task")
    profile_override = str(os.environ.get("ORACLE_BROWSER_PROFILE_DIR") or "").strip()
    copy_profile = (
        Path(profile_override).expanduser().absolute()
        if profile_override
        else (Path.home() / ".oracle" / "browser-profile").absolute()
    )
    if copy_profile.is_symlink():
        raise ExecutionError(
            "SIGNED_IN_PROFILE_UNAVAILABLE",
            "the signed-in Oracle profile seed is unavailable or unsafe",
            {"copy_profile": str(copy_profile)},
        )
    if _is_within(root, copy_profile) or _is_within(copy_profile, root):
        raise ExecutionError("COPY_PROFILE_OVERLAPS_PROJECT", "profile seed must be outside project_root")
    return ExecutionConfig(
        root,
        mission,
        _sha256(mission),
        actual_run_root,
        actual_run_id,
        explicit_thread or environment_thread or None,
        _normalize_model(model),
        _normalize_effort(effort),
        _normalize_app_name(app_name),
        copy_profile,
    )


def manifest_payload(config: ExecutionConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "project_root": str(config.project_root),
        "mission_path": str(config.mission_path),
        "model": config.model,
        "effort": config.effort,
        "app_name": config.app_name,
    }
    if config.run_root != _default_run_root(config.project_root):
        payload["run_root"] = str(config.run_root)
    if config.run_id:
        payload["run_id"] = config.run_id
    if config.source_thread_id:
        payload["source_thread_id"] = config.source_thread_id
    return payload


def load_manifest(path: Path) -> ExecutionConfig:
    manifest_path = _absolute_path(path, label="manifest_path", must_exist=True)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutionError("MANIFEST_INVALID", "manifest must be one valid UTF-8 JSON object") from exc
    if not isinstance(payload, dict) or payload.get("schema") != MANIFEST_SCHEMA:
        raise ExecutionError("MANIFEST_SCHEMA_INVALID", f"manifest schema must be {MANIFEST_SCHEMA}")
    unknown = sorted(set(payload) - ALLOWED_MANIFEST_FIELDS)
    if unknown:
        raise ExecutionError("MANIFEST_FIELDS_INVALID", "ordinary manifests contain retired or unknown fields", {"fields": unknown})
    return make_config(
        project_root=payload.get("project_root"),
        mission_path=payload.get("mission_path"),
        run_root=payload.get("run_root"),
        run_id=payload.get("run_id"),
        source_thread_id=payload.get("source_thread_id"),
        model=payload.get("model", DEFAULT_MODEL),
        effort=payload.get("effort", DEFAULT_EFFORT),
        app_name=payload.get("app_name", DEFAULT_APP_NAME),
    )


def public_contract(config: ExecutionConfig) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "project_root": str(config.project_root),
        "mission_path": str(config.mission_path),
        "model": config.model,
        "effort": config.effort,
        "app_name": config.app_name,
        "oracle_carrier": {"model": ORACLE_CARRIER_MODEL, "model_strategy": _model_strategy(config.model)},
        "chatgpt_url": CHATGPT_URL,
        "archive": "never",
        "temporary_chat": True,
        "personalization": "enabled-before-submit",
    }


def _model_strategy(model: str) -> str:
    return ORACLE_CURRENT_STRATEGY if model == "latest" else ORACLE_EXPLICIT_STRATEGY


def _composer_prompt(config: ExecutionConfig) -> str:
    return (
        f"@{config.app_name} Open exactly this approved project root in checkout mode: {config.project_root}. "
        f"Read and execute the mission file: {config.mission_path}. "
        "The mission defines the task intent and action authority; read it and applicable AGENTS.md fully before acting. "
        "Do not substitute another root or connector, and do not change ChatGPT account, privacy, app, or permission settings. Temporary-chat personalization is enabled by the runner before submission."
    )


def _slug(config: ExecutionConfig) -> str:
    words = (re.findall(r"[a-z0-9]+", config.project_root.name.casefold()) or ["project"])[:3]
    identity = hashlib.sha256(
        (str(config.project_root).casefold() + "\0" + config.run_id + "\0" + str(config.source_thread_id or "cli")).encode("utf-8")
    ).hexdigest()[:16]
    # Oracle truncates each slug word to ten characters. Split the identity so
    # our persisted name is exactly the session directory Oracle creates.
    return f"oracle-{words[0][:10]}-{identity[:8]}-{identity[8:]}"


def build_oracle_argv(
    config: ExecutionConfig,
    command: Sequence[str],
    output_path: Path,
    slug: str,
    *,
    cdp_port: int | None = None,
) -> list[str]:
    return [
        *command,
        "--engine", "browser",
        "--model", ORACLE_CARRIER_MODEL,
        "--browser-model-strategy", _model_strategy(config.model),
        "--browser-thinking-time", config.effort,
        "--chatgpt-url", CHATGPT_URL,
        "--browser-archive", "never",
        "--browser-manual-login",
        "--browser-keep-browser",
        "--browser-hide-window",
        "--browser-manual-login-profile-dir", str(output_path.parent / "browser-profile"),
        *(["--browser-port", str(cdp_port)] if cdp_port is not None else []),
        "--browser-timeout", "100m",
        "--verbose",
        "--slug", slug,
        "--prompt", _composer_prompt(config),
        "--write-output", str(output_path),
    ]


def _redacted_argv(argv: Sequence[str]) -> list[str]:
    value = list(argv)
    if "--prompt" in value:
        value[value.index("--prompt") + 1] = "<mission-handoff>"
    return value


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutionError("RUN_STATE_INVALID", "run state is unavailable or invalid", {"path": str(path)}) from exc
    if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
        raise ExecutionError("RUN_STATE_SCHEMA_INVALID", f"run state schema must be {STATE_SCHEMA}")
    return payload


def _initial_state(
    config: ExecutionConfig,
    run_dir: Path,
    slug: str,
    output_path: Path,
    command: Sequence[str],
    *,
    cdp_port: int,
) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "run_id": config.run_id,
        "source_thread_id": config.source_thread_id,
        "project_root": str(config.project_root),
        "approved_roots": [str(config.project_root)],
        "mission": {"path": str(config.mission_path), "sha256": config.mission_sha256},
        "selection": {
            "model": config.model,
            "effort": config.effort,
            "app_name": config.app_name,
            "carrier_model": ORACLE_CARRIER_MODEL,
            "carrier_strategy": _model_strategy(config.model),
        },
        "status": "prepared",
        "submission": "not_observed",
        "capture": "absent",
        "semantic_outcome": "unknown",
        "model_check": {"verified": False, "source": None},
        "oracle": {
            "version": RUNTIME.SUPPORTED_VERSION,
            "command": list(command),
            "slug": slug,
            "copy_profile": str(config.copy_profile),
            "manual_login_profile": str(run_dir / "browser-profile"),
            "expected_cdp_port": cdp_port,
            "binding": None,
        },
        "artifacts": {
            "output": str(output_path),
            "stdout": str(run_dir / "stdout.log"),
            "stderr": str(run_dir / "stderr.log"),
            "output_sha256": None,
            "output_bytes": 0,
        },
        "tab_close": {"status": "not_attempted"},
        "recovery": {"kind": "same-session-only", "resubmit": False},
    }


def _unresolved_duplicate(config: ExecutionConfig) -> dict[str, Any] | None:
    if not config.run_root.is_dir():
        return None
    for state_path in sorted(config.run_root.glob("*/state.json")):
        try:
            state = _load_state(state_path)
        except ExecutionError:
            continue
        if (
            state.get("project_root") == str(config.project_root)
            and state.get("source_thread_id") == config.source_thread_id
            and state.get("status") in UNRESOLVED_STATUSES
            and state.get("submission") in {"unknown", "observed"}
        ):
            return {"run_dir": str(state_path.parent), "status": state.get("status"), "submission": state.get("submission")}
    return None


@contextmanager
def _submit_lock(config: ExecutionConfig, timeout_seconds: float = 30.0) -> Iterator[None]:
    lock_key = hashlib.sha256(
        (str(config.project_root).casefold() + "\0" + str(config.source_thread_id or "local")).encode("utf-8")
    ).hexdigest()
    lock_path = config.run_root / ".locks" / f"{lock_key}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    try:
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise ExecutionError("SUBMIT_LOCK_TIMEOUT", "another execution owns this task/root scope")
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def _exact_run_lock(run_dir: Path) -> Iterator[None]:
    lock_path = run_dir / ".reconnect.lock"
    handle = lock_path.open("a+b")
    acquired = False
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise ExecutionError("RUN_RECONNECT_ACTIVE", "another reconnect already owns this exact run") from exc
        yield
    finally:
        try:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _pid_alive(value: Any) -> bool:
    try:
        pid = int(value)
        if pid <= 0:
            return False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetExitCodeProcess.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return ctypes.get_last_error() != 87  # Unknown/access-denied is not proof of exit.
            try:
                code = wintypes.DWORD()
                return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
            finally:
                kernel.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False


def _subprocess_kwargs() -> dict[str, Any]:
    if os.name != "nt" or not hasattr(subprocess, "STARTUPINFO"):
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"creationflags": 0x08000000, "startupinfo": startup}


def _reserve_cdp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _resolve_version(command: Sequence[str], run_factory: Callable[..., Any] = subprocess.run) -> str:
    completed = run_factory(
        [*command, "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
        **_subprocess_kwargs(),
    )
    text = f"{getattr(completed, 'stdout', '') or ''}\n{getattr(completed, 'stderr', '') or ''}"
    if int(getattr(completed, "returncode", 1)) != 0 or RUNTIME.SUPPORTED_VERSION not in text:
        raise ExecutionError("ORACLE_VERSION_INVALID", "installed Oracle does not match the supported version")
    return f"oracle {RUNTIME.SUPPORTED_VERSION}"


def _session_meta(slug: str) -> tuple[Path, dict[str, Any]] | None:
    session_root = Path(os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")).expanduser().resolve()
    candidate = session_root / slug / "meta.json"
    if candidate.is_symlink() or candidate.parent.is_symlink():
        return None
    path = candidate.resolve()
    if not _is_within(session_root, path) or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return (path, payload) if isinstance(payload, dict) else None


def _binding_from_meta(meta_path: Path, meta: Mapping[str, Any]) -> dict[str, Any] | None:
    browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
    runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
    host = str(runtime.get("chromeHost") or "").strip()
    target_id = str(runtime.get("chromeTargetId") or "").strip()
    try:
        port = int(runtime.get("chromePort"))
    except (TypeError, ValueError):
        port = 0
    if host not in {"127.0.0.1", "localhost", "::1"} or not 1 <= port <= 65535 or TARGET_ID_RE.fullmatch(target_id) is None:
        return None
    return {
        "session_meta_path": str(meta_path),
        "session_status": str(meta.get("status") or "").strip().casefold(),
        "host": host,
        "port": port,
        "target_id": target_id,
        "conversation_url": str(runtime.get("tabUrl") or "").strip() or None,
        "prompt_submitted": runtime.get("promptSubmitted") is True,
    }


def _clean_lines(path: Path) -> list[str]:
    try:
        return [ANSI_RE.sub("", line).strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    except OSError:
        return []


def _selected_latest_row(rows: Any) -> bool:
    if not isinstance(rows, list):
        return False
    selected = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("visible") is True
        and str(row.get("checked") or "").casefold() == "true"
    ]
    return len(selected) == 1 and re.sub(r"\s+", "", str(selected[0].get("text") or "")).casefold() in {"latest", "최신"}


def observed_model_check(stdout_path: Path, *, model: str, effort: str) -> dict[str, Any]:
    lines = _clean_lines(stdout_path)
    expected_ordinal = 5 if effort == "pro" else 4
    if model == "latest":
        for line in reversed(lines):
            if PICKER_PROOF_PREFIX not in line:
                continue
            try:
                proof = json.loads(line.split(PICKER_PROOF_PREFIX, 1)[1].strip())
            except json.JSONDecodeError:
                continue
            if not isinstance(proof, dict):
                continue
            slider = proof.get("slider") if isinstance(proof, dict) and isinstance(proof.get("slider"), dict) else {}
            composer = proof.get("composer") if isinstance(proof, dict) and isinstance(proof.get("composer"), dict) else {}
            signals = proof.get("modelSignals") if isinstance(proof.get("modelSignals"), list) else []
            composer_label = re.sub(r"\s+", "", str(composer.get("text") or "")).casefold()
            pro_visible = composer.get("visible") is True and (
                composer_label == "6pro" or (
                    composer_label in {"thinkingeffort", "추론수준", "사고수준", "성능", "pro"}
                    and any(isinstance(signal, dict) and signal.get("visible") is True
                            and re.sub(r"\s+", "", str(signal.get("text") or "")).casefold() == "6pro"
                            for signal in signals)
                )
            )
            verified = bool(
                proof.get("schema") == "codex.oracle.picker-dom-proof/v1"
                and proof.get("latestClicked") is True
                and isinstance(proof.get("stableReads"), int) and proof["stableReads"] >= 2
                and _selected_latest_row(proof.get("modelRows"))
                and slider.get("visible") is True
                and slider.get("ordinal") == expected_ordinal
                and slider.get("total") == 5
                and slider.get("displayOrdinal") == expected_ordinal
                and slider.get("displayTotal") == 5
                and (
                    effort != "pro"
                    or pro_visible
                )
            )
            return {"verified": verified, "model": model, "actual_model": "6 Pro" if verified and effort == "pro" else None,
                    "effort": effort, "source": "oracle-picker-dom-log"}
        return {"verified": False, "model": model, "effort": effort, "source": None}

    evidence_line = next((line for line in reversed(lines) if MODEL_EVIDENCE_PREFIX in line), "")
    thinking_line = next((line for line in reversed(lines) if THINKING_PREFIX in line), "")
    compact_evidence = re.sub(r"\s+", "", evidence_line).casefold()
    compact_thinking = re.sub(r"\s+", "", thinking_line).casefold()
    model_verified = all(
        token in compact_evidence
        for token in ("resolvedlabel=gpt-5.6sol", "strategy=select", "verified=yes")
    )
    effort_verified = (
        (effort == "pro" and ("pro,5of5" in compact_thinking or compact_thinking.endswith(":pro")))
        or (effort == "extra-high" and ("extrahigh" in compact_thinking or "4of5" in compact_thinking))
    )
    return {
        "verified": bool(model_verified and effort_verified),
        "model": model,
        "effort": effort,
        "source": "oracle-observed-selection-log" if model_verified and effort_verified else None,
    }


def _capture(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        return {"status": "absent", "sha256": None, "bytes": 0}
    with path.open("r+b") as handle:
        data = handle.read()
        os.fsync(handle.fileno())
    if not data.strip():
        return {"status": "absent", "sha256": None, "bytes": len(data)}
    return {"status": "durable", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _urlopen_json(url: str, opener: Callable[..., Any]) -> Any:
    with opener(urllib.request.Request(url, method="GET"), timeout=5) as response:
        return json.loads(response.read().decode("utf-8", errors="strict"))


def close_owned_tab(binding: Mapping[str, Any], *, opener: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    host = str(binding.get("host") or "")
    port = int(binding.get("port") or 0)
    target_id = str(binding.get("target_id") or "")
    if host not in {"127.0.0.1", "localhost", "::1"} or not 1 <= port <= 65535 or TARGET_ID_RE.fullmatch(target_id) is None:
        return {"status": "invalid-binding"}
    authority = f"http://{'[::1]' if host == '::1' else host}:{port}"
    try:
        before = _urlopen_json(f"{authority}/json/list", opener)
        targets = [
            item
            for item in before
            if isinstance(item, dict)
            and (item.get("id") == target_id or item.get("targetId") == target_id)
            and item.get("type") == "page"
        ]
        if not targets:
            return {"status": "already-closed", "target_id": target_id}
        expected_url = str(binding.get("conversation_url") or "")
        actual_url = str(targets[0].get("url") or "")
        if not expected_url or actual_url != expected_url:
            return {"status": "binding-mismatch", "target_id": target_id}
        with opener(
            urllib.request.Request(f"{authority}/json/close/{urllib.parse.quote(target_id, safe='')}", method="GET"),
            timeout=5,
        ) as response:
            response.read()
        after = _urlopen_json(f"{authority}/json/list", opener)
        if any(
            isinstance(item, dict)
            and (item.get("id") == target_id or item.get("targetId") == target_id)
            and item.get("type") == "page"
            for item in after
        ):
            return {"status": "close-unconfirmed", "target_id": target_id}
        return {"status": "closed", "target_id": target_id}
    except Exception as exc:
        return {"status": "close-failed", "target_id": target_id, "error": str(exc)}


def _finalize_capture(
    state_path: Path,
    state: dict[str, Any],
    *,
    stdout_path: Path,
    output_path: Path,
    tab_closer: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    session = _session_meta(str((state.get("oracle") or {}).get("slug") or ""))
    binding = _binding_from_meta(*session) if session else None
    expected_port = int((state.get("oracle") or {}).get("expected_cdp_port") or 0)
    if binding and binding.get("port") != expected_port:
        binding = None
    capture = _capture(output_path)
    selection = state["selection"]
    model_check = observed_model_check(stdout_path, model=selection["model"], effort=selection["effort"])
    if binding and binding.get("prompt_submitted"):
        submission = "observed"
    elif binding:
        submission = "not_observed"
    else:
        submission = str(state.get("submission") or "unknown")
    oracle_terminal = bool(binding and binding.get("session_status") in TERMINAL_ORACLE_STATES)
    captured = capture["status"] == "durable" and model_check["verified"] and oracle_terminal
    state.update(
        {
            "status": "captured" if captured else "attention_required",
            "submission": submission,
            "capture": capture["status"],
            "semantic_outcome": "unknown",
            "model_check": model_check,
        }
    )
    state["oracle"]["binding"] = binding
    state["artifacts"].update(
        {"output_sha256": capture["sha256"], "output_bytes": capture["bytes"]}
    )
    # Persist the complete capture evidence before touching the browser target.
    _write_json_atomic(state_path, state)
    if not captured or binding is None:
        return state
    close_result = tab_closer(binding)
    state["tab_close"] = close_result
    if close_result.get("status") not in {"closed", "already-closed"}:
        state["status"] = "attention_required"
    _write_json_atomic(state_path, state)
    return state


def _child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["CODEX_ORACLE_TEMPORARY_PERSONALIZATION"] = "enabled"
    environment["CODEX_ORACLE_TEMPORARY_PERSONALIZATION_HELPER"] = Path(__file__).with_name("oracle_temporary_personalization.mjs").resolve().as_uri()
    for key in (
        "ORACLE_TASK_OUTCOME_TERMINAL_CONTRACT",
        "ORACLE_TERMINAL_MARKER_CONFIRM_CYCLES",
        "ORACLE_TERMINAL_MARKER_MIN_STABLE_MS",
    ):
        environment.pop(key, None)
    return environment


def _prepare_run_profile(config: ExecutionConfig, run_dir: Path) -> Path:
    """Copy the seed into an owned, retained profile; never pass Oracle copy-profile."""
    destination = run_dir / "browser-profile"
    excluded = {"Cache", "Code Cache", "GPUCache", "ShaderCache", "Crashpad", "Sessions",
                "SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort", "LOCK"}

    def ignore(directory: str, names: list[str]) -> list[str]:
        ignored = []
        for name in names:
            candidate = Path(directory) / name
            attributes = getattr(candidate.lstat(), "st_file_attributes", 0)
            if name in excluded or candidate.is_symlink() or attributes & 0x400:
                ignored.append(name)
        return ignored

    def native_path(path: Path) -> Path:
        value = str(path.absolute())
        if os.name != "nt" or value.startswith("\\\\?\\"):
            return path
        return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)

    copy_destination = native_path(destination)
    shutil.copytree(native_path(config.copy_profile), copy_destination, ignore=ignore)
    for preferences in copy_destination.glob("*/Preferences"):
        value = json.loads(preferences.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or any(
            key in value and not isinstance(value[key], dict) for key in ("profile", "session")
        ):
            raise ExecutionError("PROFILE_PREFERENCES_INVALID", "copied startup preferences are invalid")
        value.setdefault("profile", {}).update(exit_type="Normal", exited_cleanly=True)
        value.setdefault("session", {}).update(restore_on_startup=5, startup_urls=[])
        _write_json_atomic(preferences, value)
    return destination


def execute_config(
    config: ExecutionConfig,
    *,
    dry_run: bool = False,
    command_resolver: Callable[[], list[str]] = RUNTIME.resolve_default_oracle_command,
    version_resolver: Callable[[Sequence[str]], str] = _resolve_version,
    compat_factory: Callable[..., Mapping[str, Any]] = COMPAT.ensure_oracle_compatibility,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    tab_closer: Callable[[Mapping[str, Any]], dict[str, Any]] = close_owned_tab,
) -> dict[str, Any]:
    logical_command = ["npx", "-y", f"@steipete/oracle@{RUNTIME.SUPPORTED_VERSION}"]
    run_dir = config.run_root / config.run_id
    output_path = run_dir / "output.md"
    slug = _slug(config)
    cdp_port = _reserve_cdp_port()
    if dry_run:
        argv = build_oracle_argv(config, logical_command, output_path, slug, cdp_port=cdp_port)
        return {
            "ok": True,
            "status": "dry-run",
            "run_dir": str(run_dir),
            "contract": public_contract(config),
            "argv": _redacted_argv(argv),
            "writes_performed": False,
        }
    with _submit_lock(config):
        if not config.copy_profile.is_dir() or config.copy_profile.is_symlink():
            raise ExecutionError("SIGNED_IN_PROFILE_UNAVAILABLE", "the signed-in Oracle profile seed is unavailable or unsafe")
        # The manifest binds the user-authorized root; the chosen app enforces
        # its own access. Do not require an unrelated legacy DevSpace config or
        # write a qualification receipt for every ordinary mission.
        if not config.project_root.is_dir() or config.project_root.resolve() != config.project_root:
            raise ExecutionError("PROJECT_ROOT_UNAVAILABLE", "the exact project root changed before launch")
        if (
            config.mission_path.is_symlink()
            or config.mission_path.resolve() != config.mission_path
            or not _is_within(config.project_root, config.mission_path)
        ):
            raise ExecutionError("MISSION_OUTSIDE_APPROVED_ROOT", "mission_path changed or escaped project_root before launch")
        if _sha256(config.mission_path) != config.mission_sha256:
            raise ExecutionError("MISSION_CHANGED", "mission changed after configuration; prepare it again before submitting")
        duplicate = _unresolved_duplicate(config)
        if duplicate:
            raise ExecutionError(
                "RUN_RECONNECT_REQUIRED",
                "an unresolved execution already owns this task/root; reconnect it instead of resubmitting",
                duplicate,
            )
        if run_dir.exists():
            raise ExecutionError("RUN_ID_EXISTS", "run_id already exists", {"run_dir": str(run_dir)})
        command = command_resolver()
        version = version_resolver(command)
        compat_factory(version, **COMPAT.node_runtime_kwargs(command))
        argv = build_oracle_argv(config, command, output_path, slug, cdp_port=cdp_port)
        run_dir.mkdir(parents=True, exist_ok=False)
        state_path = run_dir / "state.json"
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        state = _initial_state(config, run_dir, slug, output_path, command, cdp_port=cdp_port)
        _write_json_atomic(state_path, state)
        stdout_path.touch()
        stderr_path.touch()
        launch_attempted = False
        try:
            _prepare_run_profile(config, run_dir)
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                launch_attempted = True
                process = popen_factory(
                    argv,
                    cwd=str(config.project_root),
                    env=_child_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    shell=False,
                    **_subprocess_kwargs(),
                )
                state.update({"status": "running", "submission": "unknown", "oracle_process_pid": getattr(process, "pid", None)})
                _write_json_atomic(state_path, state)
                state["exit_code"] = int(process.wait())
        except Exception as exc:
            state.update({"status": "attention_required",
                          "submission": "unknown" if launch_attempted else "not_observed",
                          "failure_stage": "oracle-launch-or-observation" if launch_attempted else "profile-preparation",
                          "error": str(exc)})
            _write_json_atomic(state_path, state)
            return {"ok": False, "status": state["status"], "run_dir": str(run_dir), "result": state}
        state = _finalize_capture(
            state_path,
            state,
            stdout_path=stdout_path,
            output_path=output_path,
            tab_closer=tab_closer,
        )
        return {"ok": state["status"] == "captured", "status": state["status"], "run_dir": str(run_dir), "result": state}


def execute_manifest(path: Path, **kwargs: Any) -> dict[str, Any]:
    return execute_config(load_manifest(path), **kwargs)


def reconnect_run(
    run_dir: Path,
    *,
    dry_run: bool = False,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    tab_closer: Callable[[Mapping[str, Any]], dict[str, Any]] = close_owned_tab,
) -> dict[str, Any]:
    directory = _absolute_path(run_dir, label="run_dir", must_exist=True)
    state_path = directory / "state.json"
    if dry_run:
        return _reconnect_locked(
            directory, state_path, _load_state(state_path), dry_run=True,
            popen_factory=popen_factory, tab_closer=tab_closer,
        )
    with _exact_run_lock(directory):
        state = _load_state(state_path)
        return _reconnect_locked(
            directory,
            state_path,
            state,
            dry_run=dry_run,
            popen_factory=popen_factory,
            tab_closer=tab_closer,
        )


def _reconnect_locked(
    directory: Path,
    state_path: Path,
    state: dict[str, Any],
    *,
    dry_run: bool,
    popen_factory: Callable[..., Any],
    tab_closer: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    owner = str(state.get("source_thread_id") or "").strip().casefold()
    current = str(os.environ.get("CODEX_THREAD_ID") or "").strip().casefold()
    if owner and owner != current:
        raise ExecutionError("FOREIGN_TASK_RUN", "only the owning Codex task may reconnect this execution")
    if state.get("status") == "captured":
        raise ExecutionError("RUN_ALREADY_CAPTURED", "captured executions do not need reconnect")
    if state.get("status") == "running" and _pid_alive(state.get("oracle_process_pid")):
        raise ExecutionError("RUN_STILL_ACTIVE", "the original Oracle process is still active; do not start another observer")
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    command = oracle.get("command")
    slug = str(oracle.get("slug") or "")
    output_path = Path(str((state.get("artifacts") or {}).get("output") or ""))
    if output_path != directory / "output.md" or output_path.is_symlink():
        raise ExecutionError("RUN_RECOVERY_BINDING_INVALID", "output must belong to the exact run directory")
    stdout_path = Path(str((state.get("artifacts") or {}).get("stdout") or ""))
    if stdout_path != directory / "stdout.log" or stdout_path.is_symlink():
        raise ExecutionError("RUN_RECOVERY_BINDING_INVALID", "model observation must belong to the exact run directory")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command) or not slug:
        raise ExecutionError("RUN_RECOVERY_BINDING_INVALID", "run has no exact Oracle command/slug binding")
    argv = [*command, "session", slug, "--live", "--write-output", str(output_path)]
    if "--prompt" in argv or "-p" in argv:
        raise ExecutionError("RECOVERY_PROMPT_FORBIDDEN", "reconnect must never contain a prompt")
    if dry_run:
        return {"ok": True, "status": "dry-run", "run_dir": str(directory), "argv": argv, "resubmit": False}
    reconnect_stdout = directory / "reconnect-stdout.log"
    reconnect_stderr = directory / "reconnect-stderr.log"
    try:
        with reconnect_stdout.open("ab") as stdout, reconnect_stderr.open("ab") as stderr:
            process = popen_factory(
                argv,
                cwd=str(state["project_root"]),
                env=_child_environment(),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                **_subprocess_kwargs(),
            )
            state.update({"status": "running", "reconnect_process_pid": getattr(process, "pid", None)})
            _write_json_atomic(state_path, state)
            state["reconnect_exit_code"] = int(process.wait())
    except Exception as exc:
        state.update({"status": "attention_required", "error": str(exc)})
        _write_json_atomic(state_path, state)
        return {"ok": False, "status": state["status"], "run_dir": str(directory), "result": state}
    # The original stdout contains the one model/effort proof; reconnect never
    # requests or repeats that check.
    state = _finalize_capture(
        state_path,
        state,
        stdout_path=Path(str(state["artifacts"]["stdout"])),
        output_path=output_path,
        tab_closer=tab_closer,
    )
    return {"ok": state["status"] == "captured", "status": state["status"], "run_dir": str(directory), "result": state}
