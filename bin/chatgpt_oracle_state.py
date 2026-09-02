from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA = "codex.chatgpt.oracle-run/v1"
STATE_SCHEMA = "codex.chatgpt.oracle-run-state/v1"
STATUSES = {"prepared", "running", "complete", "failed", "attention_required", "abandoned"}
# One bounded lifecycle vocabulary.  The stored `status` values above remain the
# on-disk wire format for compatibility, but every consumer and report should
# reason about these four states instead of the historical five statuses times
# five authorities times terminal_harvested combinations.  That combinatorial
# space is what produced "nothing is running yet everything is locked".
LIFECYCLE_STATES = ("running", "complete", "needs_attention", "abandoned")
_STATUS_TO_LIFECYCLE = {
    "prepared": "running",
    "running": "running",
    "complete": "complete",
    "failed": "needs_attention",
    "attention_required": "needs_attention",
    "abandoned": "abandoned",
}
SESSION_AUTHORITY_RANK = {
    "pre_submit": 0,
    "submitted_unknown": 1,
    "live": 2,
    "terminal_observed": 3,
    "terminal": 4,
    "settled_executed": 5,
}
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 0x102
CREATE_NO_WINDOW = 0x08000000
ATOMIC_REPLACE_MAX_ATTEMPTS = 5
ATOMIC_REPLACE_WINDOWS_TRANSIENT_ERRORS = {5, 32}
ATOMIC_REPLACE_BACKOFF_SECONDS = (0.01, 0.025, 0.05, 0.1)
BLOCKED_OPTIONS = {
    "-f", "--file", "--files", "--path", "--paths", "--include", "-p",
    "--prompt", "--message", "--write-output", "--slug", "-e", "--engine",
    "--mode", "--browser-model-strategy", "--browser-follow-up", "--followup",
    "--dry-run", "--render", "--render-markdown", "--copy",
}
BLOCKED_COMMANDS = {"restart", "session", "status", "serve", "tui"}
SAFE_ORACLE_SWITCHES = {
    "--no-notify",
    "--notify",
    "--no-notify-sound",
    "--notify-sound",
    "--verbose",
    "--browser-hide-window",
}
SAFE_ORACLE_VALUE_OPTIONS = {
    "--heartbeat",
    "--timeout",
    "--zombie-timeout",
    # The promoted Oracle and rollback LKG are patched so this is one browser observation
    # window, including fallback capture.  Reaching it is not terminal evidence:
    # the harness keeps the exact session binding and continues live recovery.
    "--browser-timeout",
    "--browser-recheck-timeout",
}
# The observed provider boundary is 100 minutes.  The 80-minute value below is
# deliberately only a caution/status-audit threshold, never a stop deadline.
DEFAULT_BROWSER_ANSWER_TIMEOUT = "100m"
DEFAULT_BROWSER_ANSWER_CEILING_MINUTES = 100
DEFAULT_EPISODE_POLICY = {
    # Retained as compatibility metadata for older manifests.  Neither field
    # releases ownership, stops a process, or authorizes a replacement run.
    "soft_checkpoint_seconds": 4800,
    "handoff_seconds": 4800,
    "observed_platform_limit_seconds": 6000,
    "max_total_concurrency": 5,
    "web_answer_budget_seconds": 6000,
    "status_audit_seconds": 4800,
}
ORACLE_DUPLICATE_PROMPT_RE = re.compile(
    r'A session with the same prompt is already running '
    r'\((?P<locator>oracle-[a-z0-9-]+)\)\.\s*'
    r'Reattach with "oracle session (?P=locator)" or rerun with --force to start another run\.',
    re.IGNORECASE,
)
ORACLE_NO_SESSION_RE = re.compile(
    r"No session found with ID\s+(?P<locator>oracle-[a-z0-9-]+)\.?",
    re.IGNORECASE,
)
ORACLE_PROMPT_NOT_OBSERVED_MARKER = (
    "Prompt did not appear in conversation before timeout (send may have failed)"
)
ORACLE_PROMPT_TEXTAREA_ABSENT_MARKER = "Prompt textarea did not appear before timeout"
ORACLE_FOLLOWUP_UNARCHIVE_MENU_ABSENT_MARKER = (
    "FOLLOWUP_ARCHIVED_PARENT_UNARCHIVE_FAILED: unarchive-menu-not-found"
)
ORACLE_FOLLOWUP_PRE_COMPOSER_STAGES = frozenset({"resume-conversation"})
ORACLE_SESSION_METADATA_RENAME_RE = re.compile(
    r"^(?P<prefix>.{1,4}\s+)?(?P<code>EPERM|EACCES|EBUSY): "
    r"(?P<message>operation not permitted|permission denied|resource busy or locked), "
    r"rename '(?P<source>[^'\r\n]+)' -> '(?P<destination>[^'\r\n]+)'$"
)
ORACLE_SESSION_METADATA_RENAME_MESSAGES = {
    "EPERM": "operation not permitted",
    "EACCES": "permission denied",
    "EBUSY": "resource busy or locked",
}


def _process_may_be_alive(pid: int) -> bool:
    """Fail closed unless the exact PID is proven to have exited."""
    if pid <= 0:
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        error_invalid_parameter = 87
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return ctypes.get_last_error() != error_invalid_parameter
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return int(exit_code.value) == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _windows_process_snapshot(pid: int) -> dict[str, Any] | None:
    """Return one bounded Windows process identity, or None after exact exit.

    The PID alone is not an identity: Windows may reuse it after Oracle exits.
    Keep this query fixed and pass only the integer PID as data.
    """
    script = r'''
$targetPid = [int]$args[0]
$p = Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid" -ErrorAction Stop
if ($null -eq $p) { exit 3 }
[pscustomobject]@{
  ProcessId = [int]$p.ProcessId
  ParentProcessId = [int]$p.ParentProcessId
  CreationDate = [string]$p.CreationDate
  Name = [string]$p.Name
  ExecutablePath = [string]$p.ExecutablePath
  CommandLine = [string]$p.CommandLine
} | ConvertTo-Json -Compress -Depth 3
'''
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
            str(int(pid)),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode == 3:
        return None
    if completed.returncode != 0:
        raise OSError(completed.stderr.strip() or "Windows process identity query failed")
    raw = completed.stdout.strip()
    if not raw:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict) or int(value.get("ProcessId") or 0) != int(pid):
        raise OSError("Windows process identity query returned an ambiguous record")
    return value


def _normalized_process_identity_text(value: object) -> str:
    return re.sub(r"/+", "/", str(value or "").replace("\\", "/").casefold())


def exact_run_process_may_be_alive(
    run_dir: Path,
    state: dict[str, Any],
    pid: int,
    *,
    process_probe: Any = None,
    windows_snapshot: Any = _windows_process_snapshot,
    platform_name: str | None = None,
) -> bool:
    """Fail closed for a live exact-run process, but reject a reused PID.

    Modern Oracle controller and recovery commands contain the exact slug;
    Oracle Chrome contains the exact run-local browser profile.  A live PID
    whose readable command line contains neither is a different process and
    must not keep an old run locked.  Unreadable or contradictory evidence is
    deliberately still treated as active.
    """
    probe = process_probe or _process_may_be_alive
    if not probe(pid):
        return False
    if (platform_name or os.name) != "nt":
        return True
    try:
        snapshot = windows_snapshot(pid)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError):
        return True
    if snapshot is None:
        return bool(probe(pid))

    command_line = _normalized_process_identity_text(snapshot.get("CommandLine"))
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    slug = str(oracle.get("slug") or "").strip().casefold()
    markers = {
        _normalized_process_identity_text(run_dir.resolve()),
        _normalized_process_identity_text(artifacts.get("browser_temp")),
        _normalized_process_identity_text(artifacts.get("output")),
        _normalized_process_identity_text(Path.home() / ".oracle" / "sessions" / slug)
        if slug
        else "",
        slug,
    }
    markers.discard("")
    if command_line and any(marker in command_line for marker in markers):
        return True
    if command_line:
        return False

    image_name = Path(
        str(snapshot.get("ExecutablePath") or snapshot.get("Name") or "")
    ).name.casefold()
    oracle_runtime_images = {
        "chrome.exe",
        "cmd.exe",
        "node.exe",
        "npx.exe",
        "npx.cmd",
        "powershell.exe",
        "pwsh.exe",
        "python.exe",
        "pythonw.exe",
    }
    if image_name and image_name not in oracle_runtime_images:
        return False
    return True
LEGACY_V1184_FOLLOWUP_MANAGED_HASHES = {
    "bin/chatgpt_oracle_compat.py": "f41d3fb50b911f882a8e23e71cb02a5e9e81ee5ebe16f548ee48e7f815da0ee1",
    "bin/chatgpt_oracle_run.py": "957cd75a52cbe9258a994e66b557987cda4617de561307acaff5632a66fdfdf7",
    "bin/chatgpt_oracle_state.py": "9e045291ce69e2fdff0f9fa4ea78592bb4bcae463448c174f6dc93d10fa32835",
    "bin/oracle-compat/0.17.1/archiveConversation.unarchive-followup.patch": (
        "f693e24dc8cf6483da5d50b2f403157c862a402086a66d9cd04c2257461119bb"
    ),
    "bin/oracle-compat/0.17.1/browserIndex.unarchive-followup.patch": (
        "bf7a6ac55dab047087c71069d8608d222ad2d09d72350ebda3bb785dd8cad58e"
    ),
}
ORACLE_ATTACHMENTS_UPLOAD_TIMEOUT_MARKER = (
    "Attachments did not finish uploading before timeout."
)
ORACLE_NO_LIVE_TAB_MARKER = "No live ChatGPT tab matched session"
ORACLE_NO_RECOVERABLE_URL_MARKER = (
    "session metadata has no recoverable ChatGPT conversation URL"
)
ORACLE_CDP_DISCONNECT_PRE_SUBMIT_ERROR = (
    "Chrome DevTools client disconnected before oracle finished; "
    "the browser target appears still alive."
)
ORACLE_MODEL_SELECTOR_BUTTON_PRE_SUBMIT_ERROR = (
    "Unable to locate the ChatGPT model selector button. If the desired model is already "
    "selected in the browser, retry with --browser-model-strategy current; otherwise retry "
    "with --browser-model-strategy ignore to skip model selection."
)
ORACLE_STANDALONE_PRO_NO_SUBMISSION_VERSIONS = {"0.17.1", "0.18.0"}
ORACLE_CURRENT_VERSION = "0.18.0"
ORACLE_LKG_VERSION = "0.17.1"
ORACLE_COMPATIBLE_VERSIONS = {ORACLE_CURRENT_VERSION, ORACLE_LKG_VERSION}
USER_CONFIRMED_NO_SUBMISSION = "user-confirmed-no-submission"
DEVSPACE_SERVICE_RESTART_REQUIRED_ERROR = (
    "version resolution failed: DEVSPACE_SERVICE_RESTART_REQUIRED: "
    "DevSpace was safely patched before submission and must be restarted once"
)
USER_CONFIRMED_EXECUTION_ENDED = "user-confirmed-task-ended"
USER_AUTHORIZED_FRESH_AFTER_RECURSIVE_SELF_OBSERVATION = (
    "user-authorized-fresh-run-after-recursive-self-observation"
)
RECURSIVE_SELF_OBSERVATION_SETTLEMENT_SCHEMA = (
    "codex.chatgpt.oracle-recursive-self-observation-settlement/v1"
)
USER_AUTHORIZED_FRESH_AFTER_TERMINAL_DEVSPACE_NONEXECUTION = (
    "user-authorized-fresh-run-after-terminal-devspace-nonexecution"
)
TERMINAL_DEVSPACE_NONEXECUTION_SETTLEMENT_SCHEMA = (
    "codex.chatgpt.oracle-terminal-devspace-nonexecution-settlement/v1"
)
TERMINAL_DEVSPACE_NONEXECUTION_SIGNATURES = frozenset((
    "terminal-devspace-checkout-502-no-execution",
    "terminal-devspace-app-tools-unavailable-no-execution",
))
USER_AUTHORIZED_FRESH_AFTER_DEVSPACE_READ_ROUTE_REFRESH = (
    "user-authorized-fresh-run-after-devspace-read-route-refresh"
)
TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SETTLEMENT_SCHEMA = (
    "codex.chatgpt.oracle-terminal-devspace-read-route-refresh-settlement/v1"
)
TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SIGNATURE = (
    "terminal-devspace-read-chunk-unavailable-after-read-only-probe"
)
ORACLE_RECOVERY_STATE_RE = re.compile(r"(?im)^\s*State:\s*[a-z][a-z0-9_-]*\s*$")
ORACLE_PROFILE_COPY_EBUSY_RE = re.compile(
    r"(?im)^(?:ERROR:\s*|User error \(browser-automation\):\s*)?"
    r"EBUSY: resource busy or locked, copyfile ['\"](?P<source>[^'\"]+)['\"] -> ['\"](?P<destination>[^'\"]+)['\"]\s*$"
)
ORACLE_COPY_PROFILE_MANUAL_LOGIN_CONFLICT = (
    "--copy-profile cannot be combined with --browser-manual-login: choose either a "
    "throwaway copied profile or the persistent manual-login profile."
)
ORACLE_PROFILE_COPY_RSYNC_MISSING = (
    "--copy-profile requires rsync on PATH (spawn failed): spawn rsync ENOENT"
)
PROJECT_SESSION_STILL_LIVE_PRELAUNCH_ERROR = (
    "Oracle launch/run failed: PROJECT_SESSION_STILL_LIVE: "
    "an exact Oracle session still owns this project; recover it before submitting\n"
)
ORACLE_MANUAL_LOGIN_PROFILE_UNINITIALIZED_RE = re.compile(
    r"(?m)^(?P<prefix>ERROR|User error \(browser-automation\)):\s+"
    r"ChatGPT browser manual-login profile is not initialized\. "
    r"Browser mode is using Oracle's private Chrome profile at (?P<profile>[^,\r\n]+), "
    r"separate from your normal Chrome profile\. Run first-time setup, sign in there, then retry:"
)
ORACLE_MODEL_SWITCHER_PRE_SUBMIT_RE = re.compile(
    r"Unable to find model option matching .+? in the model switcher\."
    r".*?No cookies were applied;",
    re.IGNORECASE | re.DOTALL,
)
ORACLE_MODEL_OPTION_MISSING_PRE_SUBMIT_RE = re.compile(
    r'^Unable to find model option matching "(?P<desired>[^"\r\n]{1,160})" '
    r'in the model switcher\. Available: (?P<available>[^\r\n]{1,1000})\.$'
)
ORACLE_THINKING_TIME_PRE_SUBMIT_RE = re.compile(
    r"Thinking time: (?:"
    r"(?:chip not found|menu not found|option not found|selection unverified|"
    r"model kind not found(?: for [^();\r\n]+)?) \(requested (?P<requested_status>[^);]+)\)"
    r"|unknown outcome selecting (?P<requested_unknown>[^;\r\n]+)"
    r"|(?P<requested_unavailable>[^;\r\n]{1,160}?) is unavailable on this account "
    r"\([^\r\n]*\)"
    r"); refusing to submit without confirmed (?P<required>[^.]+)\.",
    re.IGNORECASE,
)
# Upstream Oracle copies a signed-in browser profile with rsync.  On POSIX
# hosts without rsync the copy fails after launch, so feasibility is decided
# while loading the manifest instead of crashing mid-launch.  The pinned
# The validated Oracle current/LKG releases use Node's recursive copy on
# Windows, so `nt` needs no external dependency.
# Checking PATH there would drop per-run profile isolation and block every
# parallel Web Multi lane, which is the exact failure this guard must avoid.
PROFILE_COPY_DEPENDENCY = "rsync"
PROFILE_COPY_NATIVE_PLATFORMS = ("nt",)
PRO_TRANSPORTS = frozenset(("pro-attachment-only", "pro-devspace", "pro-devspace-readonly"))
DEVSPACE_TRANSPORTS = frozenset(("devspace", "pro-devspace", "pro-devspace-readonly"))
# New GPT-5.6 Sol Pro launches use the provider's visible fifth effort tier.
# Historical receipts used Oracle's retired compatibility token; read-only
# recovery and follow-up validation must continue to recognize those sealed
# records without ever rewriting them.
PRO_THINKING_TIME = "pro"
COMPATIBLE_PRO_THINKING_TIMES = frozenset((PRO_THINKING_TIME, "heavy"))
VISIBLE_GPT56_SOL_THINKING_TIME_LABELS = (
    "Instant", "Medium", "High", "Extra High", "Pro",
)


def is_pro_transport(transport: str) -> bool:
    return str(transport or "").strip().casefold() in PRO_TRANSPORTS


def is_compatible_pro_thinking_time(value: object) -> bool:
    """Accept only the current Pro tier plus persisted Heavy receipts."""
    return str(value or "").strip().casefold() in COMPATIBLE_PRO_THINKING_TIMES


def is_devspace_transport(transport: str) -> bool:
    return str(transport or "").strip().casefold() in DEVSPACE_TRANSPORTS


def is_pro_readonly_transport(transport: str) -> bool:
    """Return whether a run uses the current read-only Pro transport."""
    return str(transport or "").strip().casefold() == "pro-devspace-readonly"


def is_pro_devspace_transport(transport: str) -> bool:
    return str(transport or "").strip().casefold() in {"pro-devspace", "pro-devspace-readonly"}


def is_attachment_transport(transport: str) -> bool:
    return str(transport or "").strip().casefold() == "pro-attachment-only"


def profile_copy_is_supported(
    *, which_runner: Any = None, platform_name: str | None = None
) -> bool:
    """Report whether Oracle can actually copy a signed-in browser profile."""
    platform = os.name if platform_name is None else platform_name
    if platform in PROFILE_COPY_NATIVE_PLATFORMS:
        return True
    resolver = shutil.which if which_runner is None else which_runner
    return bool(resolver(PROFILE_COPY_DEPENDENCY))
APP_RE = re.compile(r"^[^\r\n]+$")
MODEL_RE = re.compile(r"^[a-zA-Z0-9._ -]+$")
PARENT_ID_RE = re.compile(r"^[a-f0-9]{32,64}$")
RUN_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{7,95}$")
AUDIT_NONCE_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
SOURCE_THREAD_ID_RE = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)
WEB_MULTI_CHILD_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[a-f0-9]{12}$")
CHATGPT_CONVERSATION_URL_RE = re.compile(r"https://chatgpt\.com/c/[A-Za-z0-9_-]+", re.IGNORECASE)
_THREAD_MUTEXES: dict[str, threading.Lock] = {}
_THREAD_MUTEXES_GUARD = threading.Lock()


class OracleStateError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}

    def envelope(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": str(self), "evidence": self.evidence}}


@dataclass(frozen=True)
class OracleConfig:
    project_root: Path
    mission_path: Path
    mission_sha256: str
    app_name: str | None
    mode: str
    transport: str
    attachments: tuple[Path, ...]
    attachment_sha256s: tuple[str, ...]
    run_root: Path
    oracle_command: tuple[str, ...]
    oracle_args: tuple[str, ...]
    submit_mutex_timeout_seconds: float
    soft_checkpoint_seconds: int
    handoff_seconds: int
    observed_platform_limit_seconds: int
    max_total_concurrency: int
    web_answer_budget_seconds: int
    status_audit_seconds: int
    model: str
    model_strategy: str
    thinking_time: str
    copy_profile: Path | None
    research: str
    archive: str
    task_outcome_contract: str
    parallel_parent_id: str | None
    requested_run_id: str | None
    web_multi_child_provenance_path: Path | None
    web_multi_child_provenance_sha256: str | None
    source_thread_id: str | None
    registered_app_final_gate: bool


@dataclass(frozen=True)
class RunLayout:
    run_id: str
    slug: str
    run_dir: Path
    state_path: Path
    output_path: Path
    transcript_path: Path
    stdout_path: Path
    stderr_path: Path
    browser_temp_path: Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_utf8_strict(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OracleStateError("UTF8_REQUIRED", "file must be valid UTF-8", {"path": str(path), "offset": exc.start}) from exc
    except OSError as exc:
        raise OracleStateError("FILE_READ_FAILED", "file could not be read", {"path": str(path)}) from exc


def absolute_path(value: Any, *, label: str, must_exist: bool) -> Path:
    raw = Path(str(value or "")).expanduser()
    if not raw.is_absolute():
        raise OracleStateError(f"{label.upper()}_ABSOLUTE_REQUIRED", f"{label} must be an absolute path", {"path": str(raw)})
    try:
        return raw.resolve(strict=must_exist)
    except OSError as exc:
        raise OracleStateError(f"{label.upper()}_INVALID", f"{label} could not be resolved", {"path": str(raw)}) from exc


def exact_regular_file(value: Any, *, label: str) -> Path:
    raw = Path(str(value or "")).expanduser()
    code_prefix = label.upper()
    file_code = "MISSION_FILE_INVALID" if label == "mission_path" else f"{code_prefix}_FILE_INVALID"
    if not raw.is_absolute():
        raise OracleStateError(f"{code_prefix}_ABSOLUTE_REQUIRED", f"{label} must be an absolute path", {"path": str(raw)})
    if raw.is_symlink():
        raise OracleStateError(file_code, f"{label} must not be a symlink", {"path": str(raw)})
    path = absolute_path(raw, label=label, must_exist=True)
    if not path.is_file():
        raise OracleStateError(file_code, f"{label} must identify a regular file", {"path": str(path)})
    return path


def is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def oracle_state_root() -> Path:
    override = str(os.environ.get("CODEX_ORACLE_STATE_ROOT") or "").strip()
    return Path(override).expanduser().resolve() if override else (Path.home() / ".codex" / "state" / "chatgpt-oracle").resolve()


def default_oracle_command(platform_name: str | None = None) -> tuple[str, ...]:
    platform = os.name if platform_name is None else platform_name
    return ("npx.cmd" if platform == "nt" else "npx", "-y", f"@steipete/oracle@{ORACLE_CURRENT_VERSION}")


def validate_oracle_command(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list) or not values or not all(isinstance(item, str) and item for item in values):
        raise OracleStateError("ORACLE_COMMAND_INVALID", "oracle_command must be a nonempty list of strings")
    command = tuple(values)
    executable = Path(command[0]).name.casefold()
    if executable in {"oracle", "oracle.cmd", "oracle.exe"} and len(command) == 1:
        return command
    if executable in {"npx", "npx.cmd", "npx.exe"} and command[1:]:
        package = command[-1]
        version = package.rsplit("@", 1)[-1] if package.startswith("@steipete/oracle@") else ""
        if version in ORACLE_COMPATIBLE_VERSIONS and command[1:-1] in {(), ("-y",), ("--yes",)}:
            return command
    raise OracleStateError(
        "ORACLE_COMMAND_FORBIDDEN",
        f"oracle_command must resolve directly to Oracle or pinned current/LKG Oracle {sorted(ORACLE_COMPATIBLE_VERSIONS)}",
        {"command": command_for_display(command)},
    )


def validate_oracle_args(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
        raise OracleStateError("ORACLE_ARGS_INVALID", "oracle_args must be a list of nonempty strings")
    index = 0
    while index < len(values):
        item = values[index]
        option, separator, inline_value = item.partition("=")
        if option in SAFE_ORACLE_SWITCHES and not separator:
            index += 1
            continue
        if option in SAFE_ORACLE_VALUE_OPTIONS:
            if separator:
                if not inline_value:
                    raise OracleStateError("ORACLE_ARG_VALUE_MISSING", "safe Oracle option requires a value", {"argument": item})
                index += 1
                continue
            if index + 1 >= len(values) or values[index + 1].startswith("-"):
                raise OracleStateError("ORACLE_ARG_VALUE_MISSING", "safe Oracle option requires a value", {"argument": item})
            index += 2
            continue
        raise OracleStateError(
            "ORACLE_ARG_FORBIDDEN",
            "oracle_args accepts only bounded timing, heartbeat, verbosity, and notification options",
            {"argument": item},
        )
    return tuple(values)


def load_manifest(
    path: Path,
    *,
    platform_name: str | None = None,
    bind_runtime_task: bool = False,
    raw_bytes: bytes | None = None,
) -> OracleConfig:
    manifest_path = absolute_path(path, label="manifest_path", must_exist=True)
    try:
        manifest_text = (
            read_utf8_strict(manifest_path)
            if raw_bytes is None
            else raw_bytes.decode("utf-8", errors="strict")
        )
        payload = json.loads(manifest_text)
    except UnicodeDecodeError as exc:
        raise OracleStateError(
            "UTF8_REQUIRED",
            "manifest must be valid UTF-8",
            {"path": str(manifest_path), "offset": exc.start},
        ) from exc
    except json.JSONDecodeError as exc:
        raise OracleStateError("MANIFEST_JSON_INVALID", "manifest must contain one JSON object", {"line": exc.lineno, "column": exc.colno}) from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise OracleStateError("MANIFEST_SCHEMA_INVALID", f"manifest schema must be {SCHEMA}")
    project_root = absolute_path(payload.get("project_root"), label="project_root", must_exist=True)
    if not project_root.is_dir():
        raise OracleStateError("PROJECT_ROOT_NOT_DIRECTORY", "project_root must identify a directory")
    mission_path = exact_regular_file(payload.get("mission_path"), label="mission_path")
    read_utf8_strict(mission_path)
    mode = str(payload.get("mode") or "browser").strip().casefold()
    if mode != "browser":
        raise OracleStateError("MODE_INVALID", "Oracle foundation runner supports mode=browser only")
    transport = str(payload.get("transport") or "devspace").strip().casefold()
    if transport not in {"devspace", *PRO_TRANSPORTS}:
        raise OracleStateError(
            "TRANSPORT_INVALID",
            "transport must be devspace, pro-devspace-readonly, pro-attachment-only, or historical pro-devspace",
        )
    app_name_raw = str(payload.get("app_name") or "").strip().lstrip("@").strip()
    if is_devspace_transport(transport):
        if not is_within(project_root, mission_path):
            raise OracleStateError("MISSION_OUTSIDE_PROJECT", "mission_path must stay inside project_root")
        if not app_name_raw or APP_RE.fullmatch(app_name_raw) is None:
            raise OracleStateError("APP_NAME_INVALID", "app_name must be one nonempty line")
        app_name: str | None = app_name_raw
        if payload.get("attachments"):
            raise OracleStateError("REGULAR_ATTACHMENTS_FORBIDDEN", "DevSpace runs must not attach files")
        attachments: tuple[Path, ...] = ()
    elif is_attachment_transport(transport):
        if app_name_raw:
            raise OracleStateError("PRO_APP_FORBIDDEN", "Pro attachment-only runs must not name an app")
        app_name = None
        raw_attachments = payload.get("attachments")
        if not isinstance(raw_attachments, list) or not raw_attachments:
            raise OracleStateError("PRO_ATTACHMENTS_REQUIRED", "Pro requires one or more exact attachment files")
        attachments = tuple(
            exact_regular_file(value, label=f"attachment_{index}")
            for index, value in enumerate(raw_attachments)
        )
        if len(set(attachments)) != len(attachments):
            raise OracleStateError("PRO_ATTACHMENTS_DUPLICATE", "Pro attachment paths must be unique")
        if mission_path not in attachments:
            raise OracleStateError("PRO_MISSION_ATTACHMENT_REQUIRED", "mission_path must be one of the Pro attachments")
    registered_app_final_gate = payload.get("registered_app_final_gate", False)
    if not isinstance(registered_app_final_gate, bool):
        raise OracleStateError(
            "REGISTERED_APP_FINAL_GATE_INVALID",
            "registered_app_final_gate must be a boolean",
        )
    if registered_app_final_gate and transport != "devspace":
        raise OracleStateError(
            "REGISTERED_APP_FINAL_GATE_TRANSPORT_INVALID",
            "registered_app_final_gate requires the regular non-Pro devspace transport",
            {"transport": transport},
        )
    state_root = oracle_state_root()
    if is_within(project_root, state_root) or is_within(state_root, project_root):
        raise OracleStateError(
            "HOST_STATE_OVERLAPS_PROJECT",
            "Oracle host state must be disjoint from the DevSpace-writable project",
        )
    project_key = hashlib.sha256(str(project_root).casefold().encode("utf-8")).hexdigest()[:24]
    run_root = absolute_path(payload.get("run_root") or (state_root / "projects" / project_key / "runs"), label="run_root", must_exist=False)
    if not is_within(state_root, run_root):
        raise OracleStateError("RUN_ROOT_OUTSIDE_HOST_STATE", "run_root must stay inside the host-only Oracle state root")
    command_value = payload.get("oracle_command")
    if command_value is None:
        oracle_command = default_oracle_command(platform_name)
    else:
        oracle_command = validate_oracle_command(command_value)
    try:
        timeout = float(payload.get("submit_mutex_timeout_seconds", 30))
    except (TypeError, ValueError) as exc:
        raise OracleStateError("MUTEX_TIMEOUT_INVALID", "submit_mutex_timeout_seconds must be numeric") from exc
    if not 0 < timeout <= 300:
        raise OracleStateError("MUTEX_TIMEOUT_INVALID", "submit_mutex_timeout_seconds must be within 0..300")
    policy_raw = payload.get("episode_policy") or {}
    if not isinstance(policy_raw, dict):
        raise OracleStateError("EPISODE_POLICY_INVALID", "episode_policy must be one object")
    unknown_policy = set(policy_raw) - set(DEFAULT_EPISODE_POLICY)
    if unknown_policy:
        raise OracleStateError("EPISODE_POLICY_INVALID", "episode_policy contains unknown fields", {"fields": sorted(unknown_policy)})
    policy: dict[str, int] = {}
    for key, default in DEFAULT_EPISODE_POLICY.items():
        value = policy_raw.get(key, default)
        if isinstance(value, bool):
            raise OracleStateError("EPISODE_POLICY_INVALID", f"{key} must be an integer")
        try:
            policy[key] = int(value)
        except (TypeError, ValueError) as exc:
            raise OracleStateError("EPISODE_POLICY_INVALID", f"{key} must be an integer") from exc
    if not (
        60 <= policy["status_audit_seconds"] <= policy["observed_platform_limit_seconds"]
        and 60 <= policy["web_answer_budget_seconds"] <= policy["observed_platform_limit_seconds"]
        and policy["observed_platform_limit_seconds"] <= 7 * 24 * 3600
    ):
        raise OracleStateError(
            "EPISODE_POLICY_INVALID",
            "status audit and browser observation must stay within the observed provider limit",
        )
    if policy["soft_checkpoint_seconds"] < 60 or policy["handoff_seconds"] < 60:
        raise OracleStateError(
            "EPISODE_POLICY_INVALID",
            "legacy checkpoint metadata must be positive compatibility values",
        )
    if not 1 <= policy["max_total_concurrency"] <= 5:
        raise OracleStateError("EPISODE_POLICY_INVALID", "max_total_concurrency must be within 1..5")
    model = str(payload.get("model") or "gpt-5.6").strip()
    if not model or MODEL_RE.fullmatch(model) is None:
        raise OracleStateError("MODEL_INVALID", "model must be one safe Oracle browser model label")
    model_strategy = str(payload.get("model_strategy") or "select").strip().casefold()
    if model_strategy not in {"select", "current", "ignore"}:
        raise OracleStateError("MODEL_STRATEGY_INVALID", "model_strategy must be select, current, or ignore")
    # A missing effort on a new Pro manifest is normalized to the visible Pro
    # tier.  Keep the historical regular default intact for legacy regular
    # manifests that omitted this optional field.
    thinking_time = str(
        payload.get("thinking_time") or (PRO_THINKING_TIME if is_pro_transport(transport) else "heavy")
    ).strip().casefold()
    if thinking_time not in {"light", "standard", "extended", "extra-high", *COMPATIBLE_PRO_THINKING_TIMES}:
        raise OracleStateError(
            "THINKING_TIME_INVALID",
            "thinking_time must be light, standard, extended, extra-high, pro, or legacy heavy",
        )
    if is_pro_transport(transport):
        if model.casefold() != "gpt-5.6-sol":
            raise OracleStateError(
                "PRO_MODEL_INVALID",
                "Pro attachment-only runs require GPT-5.6 Sol with an explicitly verified Pro effort; no downgrade is allowed",
                {"model": model},
            )
        if model_strategy != "select":
            raise OracleStateError("PRO_MODEL_STRATEGY_INVALID", "Pro requires explicit model selection")
        # Parsing remains lossless for already-persisted Heavy-era manifests.
        # The runner's pre-layout launch gate rejects this legacy spelling for
        # every new current Pro execution before it can create a run or submit.
        if not is_compatible_pro_thinking_time(thinking_time):
            raise OracleStateError(
                "PRO_THINKING_TIME_INVALID",
                "Pro requires the explicit Pro reasoning tier",
            )
    copy_profile_raw = str(payload.get("copy_profile") or "").strip()
    if copy_profile_raw:
        copy_profile = absolute_path(copy_profile_raw, label="copy_profile", must_exist=True)
    else:
        # The manually signed-in Oracle profile is the immutable seed for a
        # throwaway per-run copy.  This prevents different projects from
        # sharing one Chrome process and closing each other's live work.
        profile_override = str(os.environ.get("ORACLE_BROWSER_PROFILE_DIR") or "").strip()
        default_profile = Path(profile_override).expanduser().resolve() if profile_override else (
            Path.home() / ".oracle" / "browser-profile"
        ).resolve()
        copy_profile = default_profile if default_profile.is_dir() else None
    if copy_profile is not None:
        if not copy_profile.is_dir():
            raise OracleStateError("COPY_PROFILE_NOT_DIRECTORY", "copy_profile must identify a directory")
        if is_within(project_root, copy_profile) or is_within(copy_profile, project_root):
            raise OracleStateError("COPY_PROFILE_OVERLAPS_PROJECT", "copy_profile must be outside the DevSpace project")
        if not profile_copy_is_supported(platform_name=platform_name):
            # Without the copy dependency Oracle aborts after launch, so every
            # run failed before reaching the composer.  Fall back to the
            # signed-in profile directly instead of forcing that failure.
            if copy_profile_raw:
                raise OracleStateError(
                    "COPY_PROFILE_DEPENDENCY_MISSING",
                    f"copy_profile requires {PROFILE_COPY_DEPENDENCY} on PATH; "
                    "install it or omit copy_profile to reuse the signed-in profile",
                    {"dependency": PROFILE_COPY_DEPENDENCY, "copy_profile": str(copy_profile)},
                )
            copy_profile = None
    research = str(payload.get("research") or "off").strip().casefold()
    if research not in {"off", "deep"}:
        raise OracleStateError("RESEARCH_INVALID", "research must be off or deep")
    if is_pro_transport(transport) and research != "off":
        raise OracleStateError("PRO_RESEARCH_FORBIDDEN", "Pro attachment-only runs do not enable research mode")
    archive = str(payload.get("archive") or "auto").strip().casefold()
    if archive not in {"auto", "always", "never"}:
        raise OracleStateError("ARCHIVE_INVALID", "archive must be auto, always, or never")
    # Read-only Pro advice commonly continues through the task-bound followup
    # command.  Oracle's one-shot `auto` policy archives a successful parent,
    # making the composer unavailable to the next round.  Normalize the
    # default/auto case to `never`; an explicit `always` remains a deliberate
    # single-turn choice and archived historical parents retain their exact
    # state for bounded compatibility recovery.
    if is_pro_readonly_transport(transport) and archive == "auto":
        archive = "never"
    task_outcome_contract = str(payload.get("task_outcome_contract") or "legacy").strip().casefold()
    if task_outcome_contract not in {"legacy", "v1"}:
        raise OracleStateError(
            "TASK_OUTCOME_CONTRACT_INVALID",
            "task_outcome_contract must be legacy or v1",
        )
    if is_attachment_transport(transport) and task_outcome_contract != "legacy":
        raise OracleStateError(
            "PRO_TASK_OUTCOME_CONTRACT_FORBIDDEN",
            "Pro attachment-only output is not wrapped in the DevSpace task outcome contract",
        )
    if is_pro_devspace_transport(transport) and task_outcome_contract != "v1":
        raise OracleStateError(
            "PRO_DEVSPACE_TASK_OUTCOME_CONTRACT_REQUIRED",
            "Pro DevSpace output requires the v1 task outcome contract",
        )
    parallel_parent_raw = str(payload.get("parallel_parent_id") or "").strip().casefold()
    parallel_parent_id = parallel_parent_raw or None
    if parallel_parent_id is not None and PARENT_ID_RE.fullmatch(parallel_parent_id) is None:
        raise OracleStateError("PARALLEL_PARENT_ID_INVALID", "parallel_parent_id must be 32-64 lowercase hex characters")
    requested_run_id = str(payload.get("run_id") or "").strip() or None
    if requested_run_id is not None and RUN_ID_RE.fullmatch(requested_run_id) is None:
        raise OracleStateError("RUN_ID_INVALID", "run_id must be a safe 8-96 character identifier")
    if registered_app_final_gate:
        if model.casefold() != "gpt-5.6" or thinking_time != "extra-high":
            raise OracleStateError(
                "REGISTERED_APP_FINAL_GATE_PROFILE_INVALID",
                "registered_app_final_gate requires regular GPT-5.6 with extra-high reasoning",
                {"model": model, "thinking_time": thinking_time},
            )
        if task_outcome_contract != "v1":
            raise OracleStateError(
                "REGISTERED_APP_FINAL_GATE_CONTRACT_INVALID",
                "registered_app_final_gate requires task_outcome_contract=v1",
            )
    provenance_raw = payload.get("web_multi_child_provenance_path")
    provenance_path = exact_regular_file(provenance_raw, label="web_multi_child_provenance_path") if provenance_raw else None
    provenance_sha256 = sha256_file(provenance_path) if provenance_path else None
    explicit_thread_id = str(payload.get("source_thread_id") or "").strip().casefold()
    environment_thread_id = str(os.environ.get("CODEX_THREAD_ID") or "").strip().casefold()
    if explicit_thread_id and SOURCE_THREAD_ID_RE.fullmatch(explicit_thread_id) is None:
        raise OracleStateError("SOURCE_THREAD_ID_INVALID", "source_thread_id must be one Codex task UUID")
    if environment_thread_id and SOURCE_THREAD_ID_RE.fullmatch(environment_thread_id) is None:
        raise OracleStateError("SOURCE_THREAD_ID_INVALID", "CODEX_THREAD_ID must be one Codex task UUID when set")
    if registered_app_final_gate and (not explicit_thread_id or not environment_thread_id):
        raise OracleStateError(
            "REGISTERED_APP_FINAL_GATE_SOURCE_THREAD_REQUIRED",
            "registered_app_final_gate requires the same explicit manifest source_thread_id and live CODEX_THREAD_ID",
        )
    if (
        bind_runtime_task
        and explicit_thread_id
        and environment_thread_id
        and explicit_thread_id != environment_thread_id
    ):
        raise OracleStateError(
            "SOURCE_THREAD_ID_MISMATCH",
            "manifest source_thread_id does not match the current Codex task",
            {"manifest_source_thread_id": explicit_thread_id, "runtime_source_thread_id": environment_thread_id},
        )
    # Legacy manifests have no task owner.  Never infer one from a project path,
    # process, or a later rollout scan: that would let one task adopt another.
    # Binding happens only for a fresh launch.  Recovery reads the persisted
    # state instead, so a later shell can never adopt a historical unbound run.
    source_thread_id = explicit_thread_id or (
        environment_thread_id if bind_runtime_task and environment_thread_id else None
    )
    return OracleConfig(
        project_root,
        mission_path,
        sha256_file(mission_path),
        app_name,
        mode,
        transport,
        attachments,
        tuple(sha256_file(item) for item in attachments),
        run_root,
        oracle_command,
        validate_oracle_args(payload.get("oracle_args")),
        timeout,
        policy["soft_checkpoint_seconds"],
        policy["handoff_seconds"],
        policy["observed_platform_limit_seconds"],
        policy["max_total_concurrency"],
        policy["web_answer_budget_seconds"],
        policy["status_audit_seconds"],
        model,
        model_strategy,
        thinking_time,
        copy_profile,
        research,
        archive,
        task_outcome_contract,
        parallel_parent_id,
        requested_run_id,
        provenance_path,
        provenance_sha256,
        source_thread_id,
        registered_app_final_gate,
    )


def oracle_slug(project_root: Path, run_id: str) -> str:
    project_words = (re.findall(r"[a-z0-9]+", project_root.name.casefold()) or ["project"])[:3]
    project_token = "-".join(word[:10] for word in project_words)
    run_token = run_id.rsplit("-", 1)[-1][:10]
    return f"oracle-{project_token}-{run_token}"


def self_observation_guard(run_id: str, slug: str) -> str:
    if not run_id or not slug:
        return ""
    return (
        " Do not inspect, read, wait for, poll, invoke, recover, or report on the Oracle controller "
        f"run {run_id} or slug {slug}, including its state.json, output.md, transcript.md, recovery, "
        "observer, or process status. Do not launch a nested Oracle run. Perform the requested mission "
        "directly; if a required project resource is unavailable, report that concrete blocker once."
    )


def connector_identity_guard(app_name: str) -> str:
    """Bind the run to one exact registered app.

    Several ChatGPT plugins can expose identically named workspace tools such as
    ``open_workspace`` and ``read``.  An ``@app`` mention alone does not select
    one of them, so a session could silently open a different plugin's connector,
    fail with an internal error, and burn the whole run on tool self-diagnosis.
    """
    name = str(app_name or "").strip()
    if not name:
        return ""
    return (
        f" Use only the {name} app's workspace tools. If more than one connector exposes a workspace tool of the "
        f"same name, select the one provided by {name} and never substitute another plugin's connector. "
        f"Before reading the mission, state in one line which app provided the workspace tool you called and the "
        "workspace id it returned. If the first workspace call fails, do not investigate your own tool wiring, "
        "search the web, or try another connector; retry the same exact root once with the same app, then report "
        "that concrete blocker and stop."
    )


def composer_prompt(
    config: OracleConfig,
    mission_path: Path | None = None,
    *,
    run_id: str = "",
    slug: str = "",
) -> str:
    if is_attachment_transport(config.transport):
        identity_material = "\0".join((
            str(config.project_root).casefold(),
            config.mission_sha256,
            *config.attachment_sha256s,
        ))
        identity = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:24]
        return (
            "Read the attached prompt/instructions and all attached files, then provide read-only analysis only. "
            "Do not create, edit, delete, or rename files; do not run commands or change settings, accounts, or external state. "
            f"Task identity: oracle-pro-{identity}."
        )
    effective_path = config.mission_path if mission_path is None else mission_path
    if str(config.transport or "").strip().casefold() == "pro-devspace":
        return (
            f"@{config.app_name} First open exactly this project root in checkout mode: {config.project_root}. "
            "Do not open the mission directory, a parent, a child, or the active workspace as a substitute. "
            f"Then read and execute the mission file: {effective_path}. "
            "Read the mission and applicable AGENTS.md fully first. "
            "You may inspect, create, edit, and remove mission-owned files and run commands inside that exact root as "
            "required by the mission. Obey all repository safety rules. Do not change accounts, app settings, or external "
            "state unless the mission explicitly authorizes that action. "
            "Put every citation, footnote, and Markdown reference definition before the outcome marker. "
            "End the final response with exactly one of TASK_OUTCOME: EXECUTED, TASK_OUTCOME: NOT_EXECUTED, or "
            "TASK_OUTCOME: BLOCKED as the final nonempty line; append nothing after it."
            + connector_identity_guard(config.app_name)
            + self_observation_guard(run_id, slug)
        )
    if is_pro_readonly_transport(config.transport):
        return (
            f"@{config.app_name} First open exactly this project root in checkout mode: {config.project_root}. "
            "Do not open the mission directory, a parent, a child, or the active workspace as a substitute. "
            f"Then read the read-only mission file: {effective_path}. "
            "Read the mission and applicable AGENTS.md fully first. "
            "Perform read-only work only; do not modify files, settings, accounts, or external state. "
            "Put every citation, footnote, and Markdown reference definition before the outcome marker. "
            "End the final response with exactly one of TASK_OUTCOME: EXECUTED, TASK_OUTCOME: NOT_EXECUTED, or "
            "TASK_OUTCOME: BLOCKED as the final nonempty line; append nothing after it."
            + connector_identity_guard(config.app_name)
            + self_observation_guard(run_id, slug)
        )
    if config.registered_app_final_gate:
        if AUDIT_NONCE_RE.fullmatch(run_id) is None:
            raise OracleStateError(
                "REGISTERED_APP_FINAL_GATE_RUN_ID_REQUIRED",
                "registered_app_final_gate requires the exact current run_id to satisfy the auditNonce grammar",
                {"run_id": run_id},
            )
        mission_relative = config.mission_path.relative_to(config.project_root).as_posix()
        return (
            f"@{config.app_name} This is a read-only registered-app final gate canary. "
            f"Your first workspace, process, or mutation call must be this exact {config.app_name} app's "
            f"open_workspace for exactly {config.project_root} in checkout mode with auditNonce={run_id}. "
            "Do not call any other workspace connector or any process or mutation tool. "
            f"The host-bound mission identity is {config.mission_path}, but both workspace tool path arguments "
            f"must be the exact workspace-relative path {mission_relative}. Preserve the returned workspaceId. "
            f"With that same workspaceId, separately read exactly {mission_relative}, then read_chunk that same "
            "workspace-relative file from offsetBytes=0 through eof=true. "
            f"Use the exact same auditNonce={run_id} on all three calls. "
            "Echo the three server-generated Audit receipt IDs exactly in the final answer. "
            f"Also echo the exact app name {config.app_name} and exact mission-relative path "
            f"{mission_relative} so the host can bind the returned evidence. "
            "Do not retry any audit call: a retry after a receipt exists would make the three-step chain ambiguous. "
            "Perform read-only work only; do not modify files, settings, accounts, or external state. "
            "If any required audit call fails, report that concrete blocker and stop. "
            "End the final response with exactly one of TASK_OUTCOME: EXECUTED, TASK_OUTCOME: NOT_EXECUTED, or "
            "TASK_OUTCOME: BLOCKED as the final nonempty line; append nothing after it."
            + self_observation_guard(run_id, slug)
        )
    # Keep the Windows npx.cmd prompt in one argument line. A literal newline
    # truncates the prompt after the app mention before Oracle receives it.
    return (
        f"@{config.app_name} 먼저 정확한 프로젝트 루트 {config.project_root}를 checkout 모드로 여세요. "
        "미션 디렉터리·상위·하위·현재 활성 작업공간을 대신 열지 마세요. "
        f"그 다음 미션 파일 {effective_path}를 읽고 끝까지 수행하며 적용되는 AGENTS.md를 먼저 끝까지 읽으세요. "
        "작업공간 열기가 시간 초과되면 동일한 정확한 루트만 한 번 재시도하고 셸 경계 우회로 대체하지 마세요."
        + (
            " 모든 인용, 각주, Markdown 참조 정의를 결과 마커 앞에 배치하세요. 마지막 비어 있지 않은 줄에 "
            "실제 작업 수행 결과를 TASK_OUTCOME: EXECUTED, TASK_OUTCOME: NOT_EXECUTED, "
            "TASK_OUTCOME: BLOCKED 중 하나로 정확히 기록하고 그 뒤에는 아무것도 추가하지 마세요."
            if config.task_outcome_contract == "v1"
            else ""
        )
        + connector_identity_guard(config.app_name)
        + self_observation_guard(run_id, slug)
    )


def create_layout(config: OracleConfig, *, run_id: str | None = None) -> RunLayout:
    actual = run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
    # Oracle accepts 3-5 words and normalizes every word to its first ten
    # characters. Generate that exact locator up front so recovery never
    # stores an alias that Oracle cannot resolve later.
    slug = oracle_slug(config.project_root, actual)
    run_dir = config.run_root / actual
    return RunLayout(
        actual,
        slug,
        run_dir,
        run_dir / "state.json",
        run_dir / "output.md",
        run_dir / "transcript.md",
        run_dir / "stdout.log",
        run_dir / "stderr.log",
        run_dir / "browser-temp",
    )


def state_payload(
    config: OracleConfig,
    layout: RunLayout,
    *,
    status: str,
    resolved_version: str,
    exit_code: int | None = None,
    cdp_port: int | None = None,
) -> dict[str, Any]:
    owner_thread_id = config.source_thread_id
    owner_kind = "bound" if owner_thread_id else "legacy-unbound"
    return {
        "schema": STATE_SCHEMA, "run_id": layout.run_id, "project_root": str(config.project_root),
        "mode": config.mode, "transport": config.transport, "app_name": config.app_name,
        "registered_app_final_gate": config.registered_app_final_gate,
        "profile": {
            "model": config.model,
            "model_strategy": config.model_strategy,
            "thinking_time": config.thinking_time,
            "copy_profile": str(config.copy_profile) if config.copy_profile else None,
            "research": config.research,
            "archive": config.archive,
        },
        "parallel_parent_id": config.parallel_parent_id,
        "requested_run_id": config.requested_run_id,
        "web_multi_child_provenance": (
            {"path": str(config.web_multi_child_provenance_path), "sha256": config.web_multi_child_provenance_sha256}
            if config.web_multi_child_provenance_path else None
        ),
        "originating_task": {
            "schema": "codex.chatgpt.oracle-task-owner/v1",
            "source_thread_id": owner_thread_id,
            "binding": owner_kind,
        },
        "ownership": {
            "schema": "codex.chatgpt.oracle-ownership/v1",
            "source_thread_id": owner_thread_id,
            "binding": owner_kind,
            "project_root_sha256": hashlib.sha256(str(config.project_root).casefold().encode("utf-8")).hexdigest(),
            "run_id": layout.run_id,
            "mission_sha256": config.mission_sha256,
            "slug": layout.slug,
        },
        "transport_status": "prepared",
        "task_outcome_contract": config.task_outcome_contract,
        "task_outcome": "not_applicable" if is_attachment_transport(config.transport) else "pending",
        "task_outcome_reason": None,
        "episode_policy": {
            "soft_checkpoint_seconds": config.soft_checkpoint_seconds,
            "handoff_seconds": config.handoff_seconds,
            "observed_platform_limit_seconds": config.observed_platform_limit_seconds,
            "max_total_concurrency": config.max_total_concurrency,
            "web_answer_budget_seconds": config.web_answer_budget_seconds,
            "status_audit_seconds": config.status_audit_seconds,
        },
        "mission": {
            "path": str(config.mission_path),
            "transport_path": str(layout.run_dir / "mission.md"),
            "sha256": config.mission_sha256,
        },
        "attachments": [
            {"path": str(path), "sha256": digest, "size_bytes": path.stat().st_size}
            for path, digest in zip(config.attachments, config.attachment_sha256s)
        ],
        "oracle": {
            "resolved_version": resolved_version,
            "command": list(config.oracle_command),
            "slug": layout.slug,
            "session_locator": layout.slug,
        },
        "artifacts": {
            "output": str(layout.output_path),
            "transcript": str(layout.transcript_path),
            "stdout": str(layout.stdout_path),
            "stderr": str(layout.stderr_path),
            "browser_temp": str(layout.browser_temp_path),
        },
        "browser_identity": {
            "schema": "codex.chatgpt.oracle-browser-identity/v1",
            "expected_cdp_port": cdp_port,
            "receipt_path": None,
            "receipt_sha256": None,
        },
        "provider_session": {
            "schema": "codex.chatgpt.oracle-provider-session/v1",
            "status": "unobserved",
            "terminal_confirmed": False,
            "binding": "none",
            "reason": "oracle-runtime-not-yet-observed",
        },
        "status": status,
        "exit_code": exit_code,
        "session_authority": "pre_submit",
        "terminal_harvested": False,
        "artifact_sha256": None,
    }


def source_thread_id_from_state(state: dict[str, Any]) -> str | None:
    """Return only a persisted task owner; never infer legacy ownership."""
    owner = state.get("originating_task") if isinstance(state.get("originating_task"), dict) else {}
    value = str(owner.get("source_thread_id") or "").strip().casefold()
    return value if SOURCE_THREAD_ID_RE.fullmatch(value) is not None else None


def current_source_thread_id() -> str | None:
    value = str(os.environ.get("CODEX_THREAD_ID") or "").strip().casefold()
    return value if SOURCE_THREAD_ID_RE.fullmatch(value) is not None else None


def reserve_loopback_cdp_port() -> int:
    """Choose one currently-free loopback port for one isolated Oracle browser."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = int(listener.getsockname()[1])
    if not 1024 <= port <= 65535:
        raise OracleStateError("CDP_PORT_RESERVATION_FAILED", "could not reserve a valid loopback CDP port")
    return port


def ownership_receipt_path(run_dir: Path) -> Path:
    return run_dir / "ownership-receipt.json"


def browser_identity_receipt_path(run_dir: Path) -> Path:
    return run_dir / "browser-identity-receipt.json"


def followup_binding_receipt_path(run_dir: Path) -> Path:
    return run_dir / "followup-binding.json"


def _write_append_only_json(path: Path, payload: dict[str, Any]) -> str:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        existing = path.read_bytes()
        if existing != encoded:
            raise OracleStateError("OWNERSHIP_RECEIPT_CONFLICT", "append-only ownership receipt already differs")
    return hashlib.sha256(encoded).hexdigest()


def persist_ownership_receipt(state_path: Path, *, oracle_process_pid: int | None) -> dict[str, Any]:
    """Persist the task/project/run/mission/slug tuple before a prompt can send."""
    state = load_state(state_path)
    ownership = state.get("ownership") if isinstance(state.get("ownership"), dict) else {}
    browser = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    receipt = {
        "schema": "codex.chatgpt.oracle-ownership-receipt/v1",
        "source_thread_id": source_thread_id_from_state(state),
        "binding": str((state.get("originating_task") or {}).get("binding") or "legacy-unbound"),
        "project_root": str(state.get("project_root") or ""),
        "project_root_sha256": ownership.get("project_root_sha256"),
        "run_id": state.get("run_id"),
        "mission_sha256": (state.get("mission") or {}).get("sha256"),
        "slug": (state.get("oracle") or {}).get("slug"),
        "oracle_process_pid": oracle_process_pid,
        "expected_cdp_port": browser.get("expected_cdp_port"),
        "browser_temp": (state.get("artifacts") or {}).get("browser_temp"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = ownership_receipt_path(state_path.parent.resolve())
    digest = _write_append_only_json(path, receipt)
    return {"path": str(path), "sha256": digest, "payload": receipt}


def persist_followup_binding(state_path: Path, binding: dict[str, Any]) -> dict[str, Any]:
    """Seal the parent/round/child tuple before a follow-up browser can launch."""
    state = load_state(state_path)
    child = binding.get("child") if isinstance(binding.get("child"), dict) else {}
    if (
        binding.get("schema") != "codex.chatgpt.oracle-followup-binding/v1"
        or binding.get("source_thread_id") != source_thread_id_from_state(state)
        or child.get("run_id") != state.get("run_id")
        or child.get("slug") != (state.get("oracle") or {}).get("slug")
        or child.get("mission_sha256") != (state.get("mission") or {}).get("sha256")
        or child.get("expected_cdp_port") != (state.get("browser_identity") or {}).get("expected_cdp_port")
    ):
        raise OracleStateError("FOLLOWUP_BINDING_INVALID", "follow-up binding does not match the exact child state")
    path = followup_binding_receipt_path(state_path.parent.resolve())
    digest = _write_append_only_json(path, binding)
    payload = load_state(state_path)
    payload["followup_binding"] = {
        "schema": "codex.chatgpt.oracle-followup-binding-reference/v1",
        "path": str(path),
        "sha256": digest,
    }
    write_json_atomic(state_path, payload)
    return {"path": str(path), "sha256": digest, "payload": binding}


def _validate_followup_reservation_for_child(
    state_path: Path,
    reservation_path: Path,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any] | None:
    state = load_state(state_path)
    loaded = _strict_json_object(reservation_path)
    if loaded is None:
        return None
    reservation, raw = loaded
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and actual_sha256 != expected_sha256:
        return None
    source_thread_id = source_thread_id_from_state(state)
    child = reservation.get("child") if isinstance(reservation.get("child"), dict) else {}
    parent = reservation.get("parent") if isinstance(reservation.get("parent"), dict) else {}
    round_key = str(reservation.get("round_key") or "")
    run_dir = state_path.parent.resolve()
    parent_dir = reservation_path.parent.parent.resolve()
    parent_state_path = parent_dir / "state.json"
    if (
        reservation.get("schema") != "codex.chatgpt.oracle-followup-round/v1"
        or reservation.get("source_thread_id") != source_thread_id
        or not round_key
        or reservation_path.resolve() != (parent_dir / "followup-rounds" / f"{round_key}.json").resolve()
        or child.get("run_id") != state.get("run_id")
        or child.get("slug") != (state.get("oracle") or {}).get("slug")
        or child.get("mission_sha256") != (state.get("mission") or {}).get("sha256")
        or child.get("expected_cdp_port") != (state.get("browser_identity") or {}).get("expected_cdp_port")
        or Path(str(child.get("run_dir") or "")).resolve() != run_dir
        or parent.get("run_id") != parent_dir.name
        or reservation.get("followup_argv") != ["--followup", parent.get("slug")]
        or CHATGPT_CONVERSATION_URL_RE.fullmatch(str(parent.get("conversation_url") or "")) is None
    ):
        return None
    try:
        parent_state = load_state(parent_state_path)
    except OracleStateError:
        return None
    parent_owner = proven_ownership_receipt(parent_state_path)
    parent_browser = proven_browser_identity_receipt(parent_state_path)
    parent_profile = parent_state.get("profile") if isinstance(parent_state.get("profile"), dict) else {}
    parent_artifacts = parent.get("artifacts") if isinstance(parent.get("artifacts"), dict) else {}
    if (
        source_thread_id_from_state(parent_state) != source_thread_id
        or parent_state.get("run_id") != parent.get("run_id")
        or (parent_state.get("oracle") or {}).get("slug") != parent.get("slug")
        or str((parent_state.get("oracle") or {}).get("conversation_url") or "") != parent.get("conversation_url")
        or parent_state.get("status") != "complete"
        or parent_state.get("session_authority") != "terminal"
        or parent_state.get("terminal_harvested") is not True
        or parent_state.get("task_outcome") != "executed"
        or parent_state.get("transport") != "pro-devspace-readonly"
        or parent_profile.get("model") != "gpt-5.6-sol"
        or parent_profile.get("model_strategy") != "select"
        or not is_compatible_pro_thinking_time(parent_profile.get("thinking_time"))
        or parent_owner is None
        or parent_browser is None
        or parent.get("ownership_receipt_sha256") != parent_owner.get("sha256")
        or parent.get("browser_identity_receipt_sha256") != parent_browser.get("sha256")
        or str((parent_browser.get("payload") or {}).get("conversation_url") or "") != parent.get("conversation_url")
    ):
        return None
    parent_mission = parent_state.get("mission") if isinstance(parent_state.get("mission"), dict) else {}
    state_artifacts = parent_state.get("artifacts") if isinstance(parent_state.get("artifacts"), dict) else {}
    artifact_paths = {
        "output": state_artifacts.get("output"),
        "transcript": state_artifacts.get("transcript"),
        "mission": parent_mission.get("transport_path"),
        "project_mission": parent_mission.get("path"),
    }
    for name, value in artifact_paths.items():
        try:
            candidate = exact_regular_file(value, label=f"followup_parent_{name}")
        except OracleStateError:
            return None
        if sha256_file(candidate) != parent_artifacts.get(name):
            return None
    return {
        "path": str(reservation_path.resolve()),
        "sha256": actual_sha256,
        "payload": reservation,
    }


def proven_followup_binding(state_path: Path) -> dict[str, Any] | None:
    state = load_state(state_path)
    reference = state.get("followup_binding")
    expected_path = followup_binding_receipt_path(state_path.parent.resolve())
    if not isinstance(reference, dict) or expected_path.is_symlink():
        return None
    try:
        raw = expected_path.read_bytes()
        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate follow-up binding key")
                result[key] = value
            return result
        binding = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    actual = hashlib.sha256(raw).hexdigest()
    child = binding.get("child") if isinstance(binding, dict) and isinstance(binding.get("child"), dict) else {}
    if (
        reference.get("schema") != "codex.chatgpt.oracle-followup-binding-reference/v1"
        or Path(str(reference.get("path") or "")).resolve() != expected_path
        or reference.get("sha256") != actual
        or binding.get("schema") != "codex.chatgpt.oracle-followup-binding/v1"
        or binding.get("source_thread_id") != source_thread_id_from_state(state)
        or child.get("run_id") != state.get("run_id")
        or child.get("slug") != (state.get("oracle") or {}).get("slug")
        or child.get("mission_sha256") != (state.get("mission") or {}).get("sha256")
        or child.get("expected_cdp_port") != (state.get("browser_identity") or {}).get("expected_cdp_port")
    ):
        return None
    reservation_path = Path(str(binding.get("reservation_path") or ""))
    reservation = _validate_followup_reservation_for_child(
        state_path, reservation_path, expected_sha256=str(binding.get("reservation_sha256") or "")
    )
    parent = binding.get("parent") if isinstance(binding.get("parent"), dict) else {}
    if (
        reservation is None
        or binding.get("round_key") != reservation["payload"].get("round_key")
        or binding.get("conversation_url") != (reservation["payload"].get("parent") or {}).get("conversation_url")
        or parent != reservation["payload"].get("parent")
        or child != reservation["payload"].get("child")
    ):
        return None
    return {"path": str(expected_path), "sha256": actual, "payload": binding, "reservation": reservation}


def proven_ownership_receipt(state_path: Path) -> dict[str, Any] | None:
    """Validate the immutable fresh-run ownership tuple without inference.

    This intentionally does not consult process lists, project scans, or the
    current environment.  A follow-up/recovery must use the exact persisted
    task/project/run/mission/slug tuple that was sealed before browser launch.
    """
    state = load_state(state_path)
    path = ownership_receipt_path(state_path.parent.resolve())
    try:
        raw = path.read_bytes()
        receipt = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(receipt, dict) or receipt.get("schema") != "codex.chatgpt.oracle-ownership-receipt/v1":
        return None
    ownership = state.get("ownership") if isinstance(state.get("ownership"), dict) else {}
    browser = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    expected = {
        "source_thread_id": source_thread_id_from_state(state),
        "binding": str((state.get("originating_task") or {}).get("binding") or "legacy-unbound"),
        "project_root": str(state.get("project_root") or ""),
        "project_root_sha256": ownership.get("project_root_sha256"),
        "run_id": state.get("run_id"),
        "mission_sha256": (state.get("mission") or {}).get("sha256"),
        "slug": (state.get("oracle") or {}).get("slug"),
        "expected_cdp_port": browser.get("expected_cdp_port"),
        "browser_temp": (state.get("artifacts") or {}).get("browser_temp"),
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        return None
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "payload": receipt}


def _receipt_runtime_profile_path(state_path: Path, value: Any) -> str:
    """Recover this run's profile path after its deterministic POSIX temp alias was cleaned."""
    profile = Path(str(value or "")).expanduser()
    if os.name != "nt":
        browser_temp = state_path.parent.resolve() / "browser-temp"
        digest = hashlib.sha256(str(browser_temp).encode("utf-8")).hexdigest()[:16]
        alias = Path("/tmp/Codex") / f"oracle-{os.getuid()}-{digest}" / "t"
        if not alias.parent.exists() and not alias.parent.is_symlink():
            try:
                relative = profile.relative_to(alias)
            except ValueError:
                pass
            else:
                if relative.parts and ".." not in relative.parts:
                    return str((browser_temp / relative).resolve())
    # A surviving or retargeted alias must pass ordinary filesystem resolution.
    return str(profile.resolve())


def proven_browser_identity_receipt(state_path: Path) -> dict[str, Any] | None:
    state = load_state(state_path)
    path = browser_identity_receipt_path(state_path.parent.resolve())
    try:
        if path.is_symlink():
            return None
        exact_path = exact_regular_file(path, label="browser_identity_receipt")
        raw = exact_path.read_bytes()

        def reject_receipt_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate browser identity receipt key: {key}")
                result[key] = value
            return result

        receipt = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_receipt_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, OracleStateError):
        return None
    if not isinstance(receipt, dict) or receipt.get("schema") not in {
        "codex.chatgpt.oracle-browser-identity-receipt/v1",
        "codex.chatgpt.oracle-browser-identity-receipt/v2",
    }:
        return None
    schema = str(receipt.get("schema") or "")
    actual = hashlib.sha256(raw).hexdigest()
    identity = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    ownership = state.get("ownership") if isinstance(state.get("ownership"), dict) else {}
    if (
        identity.get("receipt_sha256") != actual
        or receipt.get("run_id") != state.get("run_id")
        or receipt.get("slug") != (state.get("oracle") or {}).get("slug")
        or receipt.get("mission_sha256") != (state.get("mission") or {}).get("sha256")
        or receipt.get("project_root_sha256") != ownership.get("project_root_sha256")
        or receipt.get("source_thread_id") != source_thread_id_from_state(state)
    ):
        return None
    if schema.endswith("/v1"):
        if receipt.get("cdp_port") != identity.get("expected_cdp_port"):
            return None
    else:
        reference = state.get("saved_terminal_output_settlement")
        artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
        if (
            receipt.get("authority") != "saved-terminal-output-reconciliation"
            or receipt.get("expected_cdp_port") != identity.get("expected_cdp_port")
            or not isinstance(reference, dict)
            or reference.get("schema") != "codex.chatgpt.oracle-settlement-reference/v1"
            or state.get("status") != "complete"
            or state.get("session_authority") != "terminal"
            or state.get("transport_status") != "complete"
            or state.get("terminal_harvested") is not True
        ):
            return None
        settlement_path = state_path.parent.resolve() / "settlements" / "saved-terminal-output.json"
        try:
            exact_settlement = exact_regular_file(reference.get("path"), label="saved_terminal_output_settlement")
            if exact_settlement.resolve() != settlement_path.resolve() or exact_settlement.is_symlink():
                return None
            settlement_raw = exact_settlement.read_bytes()

            def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError(f"duplicate JSON key: {key}")
                    result[key] = value
                return result

            settlement = json.loads(
                settlement_raw.decode("utf-8", errors="strict"),
                object_pairs_hook=reject_duplicates,
            )
            output_path = exact_regular_file(artifacts.get("output"), label="saved_terminal_output")
            stdout_path = exact_regular_file(artifacts.get("stdout"), label="saved_terminal_output_stdout")
            transcript_path = exact_regular_file(
                artifacts.get("transcript"), label="saved_terminal_output_transcript"
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, OracleStateError):
            return None
        settlement_sha256 = hashlib.sha256(settlement_raw).hexdigest()
        current_artifact_hashes = {
            "output_sha256": sha256_file(output_path),
            "stdout_sha256": sha256_file(stdout_path),
            "transcript_sha256": sha256_file(transcript_path),
        }
        if (
            not isinstance(settlement, dict)
            or settlement.get("schema") != "codex.chatgpt.oracle-saved-terminal-output/v1"
            or reference.get("sha256") != settlement_sha256
            or receipt.get("saved_terminal_output_settlement_sha256") != settlement_sha256
            or receipt.get("saved_terminal_output_settlement_path") != str(settlement_path)
            or settlement.get("source_thread_id") != source_thread_id_from_state(state)
            or settlement.get("run_id") != state.get("run_id")
            or settlement.get("slug") != (state.get("oracle") or {}).get("slug")
            or settlement.get("mission_sha256") != (state.get("mission") or {}).get("sha256")
            or any(settlement.get(key) != value for key, value in current_artifact_hashes.items())
            or any(receipt.get(key) != value for key, value in current_artifact_hashes.items())
        ):
            return None
        current_ownership = proven_ownership_receipt(state_path)
        current_binding = proven_followup_binding(state_path)
        if (
            current_ownership is None
            or current_binding is None
            or settlement.get("ownership_receipt_sha256") != current_ownership.get("sha256")
            or settlement.get("followup_binding_sha256") != current_binding.get("sha256")
        ):
            return None
    slug = str((state.get("oracle") or {}).get("slug") or "")
    session_root = Path(os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")).resolve()
    try:
        raw_session_dir = session_root / slug
        raw_meta_path = raw_session_dir / "meta.json"
        if schema.endswith("/v2") and (raw_session_dir.is_symlink() or raw_meta_path.is_symlink()):
            return None
        meta_path = exact_regular_file(raw_meta_path, label="oracle_browser_identity_meta")
        if schema.endswith("/v2") and meta_path.resolve().parent != raw_session_dir.resolve():
            return None
        meta_bytes = meta_path.read_bytes()
        if schema.endswith("/v2"):
            def reject_meta_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError(f"duplicate Oracle meta key: {key}")
                    result[key] = value
                return result

            meta = json.loads(
                meta_bytes.decode("utf-8", errors="strict"),
                object_pairs_hook=reject_meta_duplicates,
            )
        else:
            meta = json.loads(meta_bytes.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, OracleStateError):
        return None
    browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
    runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
    observed = {
        "chrome_pid": runtime.get("chromePid"),
        "browser_parent_pid": runtime.get("controllerPid"),
        "profile_path": _receipt_runtime_profile_path(state_path, runtime.get("userDataDir")),
        "cdp_port": runtime.get("chromePort"),
        "target_id": runtime.get("chromeTargetId"),
        "conversation_url": runtime.get("tabUrl"),
    }
    observed_identity_sha256 = hashlib.sha256(
        json.dumps(observed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    recorded_meta_sha256 = str(receipt.get("oracle_meta_sha256") or "")
    recorded_identity_sha256 = str(receipt.get("oracle_runtime_identity_sha256") or "")
    # Oracle legitimately appends prompt/archive/completion fields after this
    # receipt is sealed.  Keep the original whole-file hash as capture-time
    # provenance, but authorize only from the immutable runtime identity tuple.
    # Existing v1 receipts lack the canonical tuple hash, so exact field
    # equality remains their bounded compatibility proof.
    if (
        re.fullmatch(r"[a-f0-9]{64}", recorded_meta_sha256) is None
        or any(receipt.get(key) != value for key, value in observed.items())
        or (recorded_identity_sha256 and recorded_identity_sha256 != observed_identity_sha256)
    ):
        return None
    if schema.endswith("/v2"):
        if (
            receipt.get("observed_cdp_port") != observed["cdp_port"]
            or receipt.get("cdp_port") != observed["cdp_port"]
            or receipt.get("expected_cdp_port") == receipt.get("observed_cdp_port")
            or receipt.get("oracle_meta_sha256") != hashlib.sha256(meta_bytes).hexdigest()
            or settlement.get("oracle_meta_sha256") != receipt.get("oracle_meta_sha256")
        ):
            return None
    return {"path": str(path), "sha256": actual, "payload": receipt}


def persist_saved_output_browser_identity_receipt(
    state_path: Path,
    *,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist one v2 identity derived from an exact saved-output settlement."""
    state = load_state(state_path)
    directory = state_path.parent.resolve()
    existing = proven_browser_identity_receipt(state_path)
    if existing is not None:
        return existing
    receipt_path = browser_identity_receipt_path(directory)
    if receipt_path.exists() or payload.get("schema") != "codex.chatgpt.oracle-browser-identity-receipt/v2":
        return None
    identity = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    ownership = state.get("ownership") if isinstance(state.get("ownership"), dict) else {}
    expected = {
        "source_thread_id": source_thread_id_from_state(state),
        "project_root_sha256": ownership.get("project_root_sha256"),
        "run_id": state.get("run_id"),
        "mission_sha256": (state.get("mission") or {}).get("sha256"),
        "slug": (state.get("oracle") or {}).get("slug"),
        "expected_cdp_port": identity.get("expected_cdp_port"),
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return None
    digest = _write_append_only_json(receipt_path, payload)
    state["browser_identity"] = {
        **identity,
        "receipt_path": str(receipt_path),
        "receipt_sha256": digest,
        "observed_cdp_port": payload.get("observed_cdp_port"),
        "authority": "saved-terminal-output-reconciliation",
    }
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    state["oracle"] = {**oracle, "conversation_url": payload.get("conversation_url")}
    write_json_atomic(state_path, state)
    return proven_browser_identity_receipt(state_path)


def capture_browser_identity_receipt(state_path: Path) -> dict[str, Any] | None:
    """Record exactly one Oracle-reported Chrome identity after prompt binding.

    It never enumerates arbitrary CDP targets.  The Oracle metadata must agree
    with this run's dynamic port and browser-temp path before it is persisted.
    """
    state = load_state(state_path)
    directory = state_path.parent.resolve()
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    slug = str(oracle.get("slug") or "")
    session_root = Path(os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")).resolve()
    try:
        meta_bytes = (session_root / slug / "meta.json").read_bytes()
        meta = json.loads(meta_bytes.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
    runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
    identity = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    try:
        chrome_pid = int(runtime.get("chromePid"))
        parent_pid = int(runtime.get("controllerPid"))
        cdp_port = int(runtime.get("chromePort"))
    except (TypeError, ValueError):
        return None
    profile = Path(str(runtime.get("userDataDir") or "")).expanduser()
    browser_temp = Path(str((state.get("artifacts") or {}).get("browser_temp") or "")).expanduser()
    target_id = str(runtime.get("chromeTargetId") or "").strip()
    url = str(runtime.get("tabUrl") or "").strip()
    expected_cdp_port = identity.get("expected_cdp_port")
    if cdp_port != expected_cdp_port:
        candidate_url = url if CHATGPT_CONVERSATION_URL_RE.fullmatch(url) is not None else None
        mismatch = {
            "schema": "codex.chatgpt.oracle-browser-port-mismatch/v1",
            "expected_cdp_port": expected_cdp_port,
            "observed_cdp_port": cdp_port,
            "oracle_meta_path": str(session_root / slug / "meta.json"),
            "conversation_url_candidate": candidate_url,
            "target_id_candidate": target_id or None,
        }
        if identity.get("port_mismatch") != mismatch:
            state["browser_identity"] = {**identity, "port_mismatch": mismatch}
            if candidate_url:
                state["oracle"] = {
                    **oracle,
                    "conversation_url_candidate": candidate_url,
                }
            write_json_atomic(state_path, state)
        return None
    existing = proven_browser_identity_receipt(state_path)
    if existing is not None:
        return existing
    if browser_identity_receipt_path(directory).exists():
        return None
    if (
        chrome_pid <= 0 or parent_pid <= 0
        or not target_id or CHATGPT_CONVERSATION_URL_RE.fullmatch(url) is None
        or not str(browser_temp) or not is_within(browser_temp.resolve(), profile.resolve())
    ):
        return None
    ownership = state.get("ownership") if isinstance(state.get("ownership"), dict) else {}
    receipt = {
        "schema": "codex.chatgpt.oracle-browser-identity-receipt/v1",
        "source_thread_id": source_thread_id_from_state(state),
        "project_root_sha256": ownership.get("project_root_sha256"),
        "run_id": state.get("run_id"),
        "mission_sha256": (state.get("mission") or {}).get("sha256"),
        "slug": slug,
        "chrome_pid": chrome_pid,
        "browser_parent_pid": parent_pid,
        "profile_path": str(profile.resolve()),
        "cdp_port": cdp_port,
        "target_id": target_id,
        "conversation_url": url,
        "oracle_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
        "oracle_runtime_identity_sha256": hashlib.sha256(
            json.dumps(
                {
                    "chrome_pid": chrome_pid,
                    "browser_parent_pid": parent_pid,
                    "profile_path": str(profile.resolve()),
                    "cdp_port": cdp_port,
                    "target_id": target_id,
                    "conversation_url": url,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt_path = browser_identity_receipt_path(directory)
    digest = _write_append_only_json(receipt_path, receipt)
    state["browser_identity"] = {
        **identity,
        "receipt_path": str(receipt_path),
        "receipt_sha256": digest,
    }
    state["oracle"] = {**oracle, "conversation_url": url}
    write_json_atomic(state_path, state)
    return {"path": str(receipt_path), "sha256": digest, "payload": receipt}


def provider_session_evidence(state_path: Path) -> dict[str, Any]:
    """Describe provider terminal state without widening recovery authority.

    Only an immutable browser identity receipt can turn Oracle metadata into
    confirmed remote-session evidence.  A legacy or port-mismatched metadata
    file may expose a forensic candidate URL, but remains explicitly
    unconfirmed and cannot authorize attach, recovery, harvest, or stop.
    """
    state = load_state(state_path)
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    slug = str(oracle.get("slug") or "").strip()
    session_root = Path(
        os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
    ).resolve()
    meta_path = session_root / slug / "meta.json"
    base = {
        "schema": "codex.chatgpt.oracle-provider-session/v1",
        "status": "unobserved",
        "terminal_confirmed": False,
        "binding": "none",
        "reason": "oracle-meta-unavailable",
        "oracle_meta_path": str(meta_path),
    }
    try:
        meta_bytes = meta_path.read_bytes()
        meta = json.loads(meta_bytes.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return base
    if not isinstance(meta, dict):
        return {**base, "reason": "oracle-meta-invalid"}

    browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
    runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
    archive = browser.get("archive") if isinstance(browser.get("archive"), dict) else {}
    runtime_url = str(runtime.get("tabUrl") or "").strip()
    archive_url = str(archive.get("conversationUrl") or "").strip()
    observed_url = next(
        (
            candidate
            for candidate in (runtime_url, archive_url)
            if CHATGPT_CONVERSATION_URL_RE.fullmatch(candidate) is not None
        ),
        "",
    )
    status = str(meta.get("status") or "unknown").strip().casefold()
    completed_at = str(meta.get("completedAt") or "").strip() or None
    receipt = proven_browser_identity_receipt(state_path)
    if receipt is None:
        return {
            **base,
            "status": status,
            "binding": "unconfirmed",
            "reason": "browser-identity-receipt-unavailable",
            "observed_conversation_url": observed_url or None,
            "completed_at": completed_at,
            "oracle_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
        }

    payload = receipt["payload"]
    bound_url = str(payload.get("conversation_url") or "").strip()
    if not observed_url or observed_url != bound_url:
        return {
            **base,
            "status": status,
            "binding": "exact-browser-identity-receipt",
            "reason": "conversation-url-mismatch",
            "observed_conversation_url": observed_url or None,
            "bound_conversation_url": bound_url or None,
            "completed_at": completed_at,
            "browser_identity_receipt_sha256": receipt["sha256"],
            "oracle_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
        }

    # A local/browser error can carry ``completedAt`` even though it proves
    # only that the observer stopped.  It is not provider-terminal evidence.
    terminal = status == "completed" and completed_at is not None
    return {
        **base,
        "status": status,
        "terminal_confirmed": terminal,
        "binding": "exact-browser-identity-receipt",
        "reason": "oracle-meta-terminal" if terminal else "oracle-meta-nonterminal",
        "observed_conversation_url": observed_url,
        "bound_conversation_url": bound_url,
        "completed_at": completed_at,
        "browser_identity_receipt_sha256": receipt["sha256"],
        "oracle_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
    }


def host_uptime_ms(*, platform_name: str | None = None) -> int:
    platform = os.name if platform_name is None else platform_name
    if platform == "nt":
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            # Unit tests exercise Windows argv construction from POSIX hosts.
            # The actual Windows runtime always provides ctypes.WinDLL.
            return int(time.monotonic() * 1000)
        kernel32 = win_dll("kernel32", use_last_error=True)
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return int(kernel32.GetTickCount64())
    return int(time.monotonic() * 1000)


def browser_temp_environment(
    browser_temp_path: Path,
    *,
    platform_name: str | None = None,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    root = browser_temp_path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = {
        "schema": "codex.chatgpt.oracle-browser-temp-owner/v1",
        "controller_pid": os.getpid(),
        "host_uptime_ms": host_uptime_ms(platform_name=platform_name),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json_atomic(root / ".owner.json", marker)
    env = dict(os.environ if base_env is None else base_env)
    value = str(root)
    env.update({"TEMP": value, "TMP": value, "TMPDIR": value})
    return env


def cleanup_owned_browser_temp(browser_temp_path: Path) -> bool:
    root = browser_temp_path.expanduser().resolve()
    if not root.exists():
        return True
    marker = root / ".owner.json"
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload.get("schema") != "codex.chatgpt.oracle-browser-temp-owner/v1":
        return False
    try:
        shutil.rmtree(root)
    except OSError:
        return False
    return not root.exists()


def cleanup_prior_boot_browser_temps(
    run_root: Path,
    *,
    platform_name: str | None = None,
    current_uptime_ms: int | None = None,
) -> list[str]:
    root = run_root.expanduser().resolve()
    if not root.is_dir():
        return []
    now_uptime = host_uptime_ms(platform_name=platform_name) if current_uptime_ms is None else int(current_uptime_ms)
    cleaned: list[str] = []
    for run_dir in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name):
        browser_temp = run_dir / "browser-temp"
        marker = browser_temp / ".owner.json"
        if not marker.is_file():
            continue
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            owner_uptime = int(payload["host_uptime_ms"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        # GetTickCount/monotonic reset on reboot. Only a prior-boot owner is
        # eligible here; same-boot crashes remain preserved for exact recovery.
        if now_uptime >= owner_uptime:
            continue
        if cleanup_owned_browser_temp(browser_temp):
            cleaned.append(str(browser_temp))
    return cleaned


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the same-directory temporary basename deliberately short. Reusing
    # the full destination name plus PID/UUID pushed otherwise valid Windows
    # settlement paths beyond MAX_PATH before the final atomic replace.
    descriptor, temporary_name = tempfile.mkstemp(prefix=".t-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    try:
        for attempt in range(ATOMIC_REPLACE_MAX_ATTEMPTS):
            try:
                os.replace(temporary, path)
                return
            except PermissionError as exc:
                # Windows can briefly deny ReplaceFile semantics while another
                # observer has the destination open.  Retry only the two known
                # sharing/access races; all other failures remain fail-closed.
                if (
                    getattr(exc, "winerror", None)
                    not in ATOMIC_REPLACE_WINDOWS_TRANSIENT_ERRORS
                    or attempt + 1 >= ATOMIC_REPLACE_MAX_ATTEMPTS
                ):
                    raise
                time.sleep(ATOMIC_REPLACE_BACKOFF_SECONDS[attempt])
    finally:
        # A successful replace consumes the temporary path.  On a permanent
        # failure, remove only the uniquely owned temporary file and preserve
        # the original exception.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(read_utf8_strict(absolute_path(path, label="state_path", must_exist=True)))
    except json.JSONDecodeError as exc:
        raise OracleStateError("STATE_JSON_INVALID", "state file is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
        raise OracleStateError("STATE_SCHEMA_INVALID", f"state schema must be {STATE_SCHEMA}")
    return payload


def update_state(
    state_path: Path,
    *,
    status: str | None = None,
    resolved_version: str | None = None,
    exit_code: int | None = None,
    session_authority: str | None = None,
    terminal_harvested: bool | None = None,
    artifact_sha256: str | None = None,
    transport_status: str | None = None,
    task_outcome: str | None = None,
    task_outcome_reason: str | None = None,
    terminal_watchdog: dict[str, Any] | None = None,
    host_watchdog: dict[str, Any] | None = None,
    browser_observer: dict[str, Any] | None = None,
    status_audit: dict[str, Any] | None = None,
    conversation_url: str | None = None,
    conversation_url_conflict: dict[str, str] | None = None,
    pro_app_read_gate: dict[str, Any] | None = None,
    provider_session: dict[str, Any] | None = None,
    exact_live_observation: bool = False,
) -> dict[str, Any]:
    if status is not None and status not in STATUSES:
        raise OracleStateError("STATUS_INVALID", "invalid Oracle run status")
    payload = load_state(state_path)
    if status is not None:
        payload["status"] = status
        payload["exit_code"] = exit_code
    if resolved_version is not None:
        payload["oracle"]["resolved_version"] = resolved_version
    if session_authority is not None:
        current_authority = str(payload.get("session_authority") or "")
        current_rank = SESSION_AUTHORITY_RANK.get(current_authority, -1)
        requested_rank = SESSION_AUTHORITY_RANK.get(session_authority, -1)
        # A terminal observation without a harvested artifact is provisional:
        # an exact later live observation is stronger evidence that the same
        # conversation is still generating.  Never apply this exception to a
        # durably harvested terminal result.
        restore_live = (
            exact_live_observation
            and current_authority == "terminal_observed"
            and session_authority == "live"
            and payload.get("terminal_harvested") is not True
        )
        payload["session_authority"] = (
            session_authority
            if restore_live or current_rank <= requested_rank
            else current_authority
        )
        if current_rank > requested_rank and not restore_live and status == "running":
            payload["status"] = (
                "complete"
                if current_authority == "terminal" and payload.get("terminal_harvested") is True
                else "attention_required"
            )
    if terminal_harvested is not None:
        payload["terminal_harvested"] = terminal_harvested
    if artifact_sha256 is not None:
        payload["artifact_sha256"] = artifact_sha256
    if transport_status is not None:
        payload["transport_status"] = transport_status
    if task_outcome is not None:
        payload["task_outcome"] = task_outcome
    if task_outcome_reason is not None:
        payload["task_outcome_reason"] = task_outcome_reason
    if terminal_watchdog is not None:
        payload["terminal_watchdog"] = terminal_watchdog
    if host_watchdog is not None:
        payload["host_watchdog"] = host_watchdog
    if browser_observer is not None:
        payload["browser_observer"] = browser_observer
    if status_audit is not None:
        payload["status_audit"] = status_audit
    if conversation_url is not None:
        oracle = payload.get("oracle") if isinstance(payload.get("oracle"), dict) else {}
        existing_url = str(oracle.get("conversation_url") or "").strip()
        if existing_url and existing_url != conversation_url:
            payload["conversation_url_conflict"] = {
                "persisted": existing_url,
                "observed": conversation_url,
            }
        else:
            payload["oracle"] = {**oracle, "conversation_url": conversation_url}
    if conversation_url_conflict is not None:
        payload["conversation_url_conflict"] = dict(conversation_url_conflict)
    if pro_app_read_gate is not None:
        payload["pro_app_read_gate"] = dict(pro_app_read_gate)
    if provider_session is not None:
        payload["provider_session"] = dict(provider_session)
    write_json_atomic(state_path, payload)
    return payload


def output_is_nonempty(path: Path) -> bool:
    try:
        return bool(path.read_bytes().strip())
    except OSError:
        return False


def recursive_self_observation_evidence(
    state: dict[str, Any], output_text: str
) -> dict[str, str] | None:
    """Recognize only a terminal answer that recursively reports its own live run."""
    run_id = str(state.get("run_id") or "").strip()
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    slug = str(oracle.get("slug") or "").strip()
    text = str(output_text or "")
    folded = text.casefold()
    if (
        not run_id
        or not slug
        or run_id.casefold() not in folded
        or slug.casefold() not in folded
        or state.get("terminal_harvested") is not True
        or str(state.get("session_authority") or "") != "terminal"
        or str(state.get("task_outcome") or "") != "blocked"
    ):
        return None
    required_groups = (
        ("running", "status=running", "status: running"),
        ("task_outcome: pending", "task_outcome=pending", "task outcome: pending"),
        ("output.md` 미생성", "output.md 미생성", "output absent", "output.md absent", "output 없음"),
        ("continue-observing-same-exact-session",),
    )
    if any(not any(needle in folded for needle in group) for group in required_groups):
        return None
    return {
        "run_id": run_id,
        "slug": slug,
        "signature": "post-submit-recursive-self-observation",
    }


def terminal_devspace_nonexecution_evidence(
    state: dict[str, Any], output_text: str
) -> dict[str, str] | None:
    """Recognize a bounded terminal DevSpace failure with explicit nonexecution.

    This signature is intentionally narrower than a generic BLOCKED answer.  A
    fresh run is safe only when the durable answer binds the exact project and
    either reports the registered-app checkout 502 with explicit no-work proof,
    or reports that the exact app exposed no workspace tools and explicitly
    confirms that it attempted no connector/shell/web fallback and read or
    modified neither the mission nor AGENTS.md.
    """
    run_id = str(state.get("run_id") or "").strip()
    project_root = str(state.get("project_root") or "").strip()
    transport = str(state.get("transport") or "").strip().casefold()
    outcome = str(state.get("task_outcome") or "").strip().casefold()
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    slug = str(oracle.get("slug") or "").strip()
    text = str(output_text or "")
    folded = text.casefold()
    marker_matches = TASK_OUTCOME_RE.findall(text)
    final_nonempty = next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")
    expected_marker = {
        "blocked": "TASK_OUTCOME: BLOCKED",
        "not_executed": "TASK_OUTCOME: NOT_EXECUTED",
    }.get(outcome)
    outage = (
        "502 upstream or external service errors" in folded
        and "checkout" in folded
        and ("workspace id" in folded or "workspaceid" in folded)
    )
    korean_nonexecution = all(
        needle in folded
        for needle in ("미션 파일을 읽거나", "명령 실행", "파일 변경", "수행하지 않았습니다")
    )
    english_nonexecution = all(
        any(needle in folded for needle in alternatives)
        for alternatives in (
            ("did not read the mission", "didn't read the mission"),
            ("did not run commands", "didn't run commands", "no commands were run"),
            ("did not change files", "didn't change files", "no files were changed"),
        )
    )
    app_name = str(state.get("app_name") or "").strip().casefold()
    korean_tools_unavailable = all(
        needle in folded
        for needle in (
            "workspace 도구가 노출되어 있지 않아",
            "열 수 없습니다",
            "다른 workspace 커넥터·셸·웹·oracle 우회는 시도하지 않았",
            "미션 파일이나 agents.md도 읽거나 수정하지 않았",
        )
    )
    english_tools_unavailable = all(
        any(needle in folded for needle in alternatives)
        for alternatives in (
            ("workspace tools are not available", "workspace tools are not exposed"),
            ("could not open", "unable to open"),
            ("did not try another connector", "did not attempt another connector"),
            ("did not use the shell", "did not attempt a shell"),
            ("did not read or modify the mission", "read or modified neither the mission"),
        )
    )
    tools_unavailable = (
        bool(app_name)
        and app_name in folded
        and (korean_tools_unavailable or english_tools_unavailable)
    )
    if (
        not run_id
        or not slug
        or not project_root
        or project_root.casefold() not in folded
        or transport not in DEVSPACE_TRANSPORTS
        or state.get("terminal_harvested") is not True
        or str(state.get("session_authority") or "") != "terminal"
        or outcome not in {"blocked", "not_executed"}
        or len(marker_matches) != 1
        or marker_matches[0].casefold() != outcome
        or final_nonempty != expected_marker
        or not (
            (outage and (korean_nonexecution or english_nonexecution))
            or tools_unavailable
        )
    ):
        return None
    signature = (
        "terminal-devspace-app-tools-unavailable-no-execution"
        if tools_unavailable
        else "terminal-devspace-checkout-502-no-execution"
    )
    return {
        "run_id": run_id,
        "slug": slug,
        "signature": signature,
        "transport": transport,
        "task_outcome": outcome,
    }


def terminal_devspace_read_route_refresh_evidence(
    state: dict[str, Any], output_text: str
) -> dict[str, str] | None:
    """Recognize one exact read-only canary stopped before its command.

    This is not generic BLOCKED authority.  It covers only the first regular
    DevSpace qualification attempt that opened and read the bound workspace,
    found ``read_chunk`` absent from the configured app, and explicitly ran no
    command and performed no write.  A user-completed app-tool refresh may then
    authorize one fresh probe through a separate append-only receipt.
    """
    run_id = str(state.get("run_id") or "").strip()
    project_root = str(state.get("project_root") or "").strip()
    app_name = str(state.get("app_name") or "").strip().casefold()
    transport = str(state.get("transport") or "").strip().casefold()
    outcome = str(state.get("task_outcome") or "").strip().casefold()
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    slug = str(oracle.get("slug") or "").strip()
    text = str(output_text or "")
    folded = text.casefold()
    marker_matches = TASK_OUTCOME_RE.findall(text)
    final_nonempty = next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")
    project_candidates = {
        project_root.casefold(),
        project_root.replace("\\", "\\\\").casefold(),
    }
    workspace_ids = re.findall(
        r"(?im)^\s*\*\s*workspace id:\s*`(?P<workspace>ws_[a-z0-9]+)`\s*$",
        text,
    )
    required_korean = (
        f"* 앱: `{app_name}`",
        "* 모드: `checkout`",
        "* 적용 `agents.md`: 전체 확인 완료",
        "* 미션 파일: 전체 확인 완료",
        "* 보고서 첫 markdown heading:",
        "* 저장소 쓰기 작업: 없음",
        "금지된 oracle controller/run 관련 파일·상태·프로세스: 검사하거나 호출하지 않음",
        f"현재 `{app_name}` 앱이 이 workspace에서 노출한 도구에 `read_chunk`가 없으며",
        "`chunk` 관련 도구가 반환되지 않았습니다",
        "따라서 다음 단계인 정확히 한 번의 `git status --short --branch` 명령도 실행하지 않았습니다",
        "* 명령 실행: **안 함**",
        "* exit code: **미확인**",
        "* command output: **없음**",
    )
    if (
        not run_id
        or not slug
        or not project_root
        or not app_name
        or not any(candidate and candidate in folded for candidate in project_candidates)
        or transport != "devspace"
        or str(profile.get("model") or "").casefold() != "gpt-5.6"
        or str(profile.get("model_strategy") or "") != "select"
        or str(profile.get("thinking_time") or "") != "extra-high"
        or state.get("terminal_harvested") is not True
        or str(state.get("session_authority") or "") != "terminal"
        or outcome != "blocked"
        or len(marker_matches) != 1
        or marker_matches[0].casefold() != outcome
        or final_nonempty != "TASK_OUTCOME: BLOCKED"
        or len(workspace_ids) != 1
        or not all(needle in folded for needle in required_korean)
    ):
        return None
    return {
        "run_id": run_id,
        "slug": slug,
        "signature": TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SIGNATURE,
        "transport": transport,
        "task_outcome": outcome,
        "app_name": app_name,
        "workspace_id": workspace_ids[0],
    }


def terminal_devspace_read_route_refresh_mission_contract(mission_text: str) -> bool:
    """Require the exact read-only qualification boundary before settlement."""
    folded = str(mission_text or "").casefold()
    return all(
        needle in folded
        for needle in (
            "read_chunk",
            "offsetbytes=0",
            "eof=true",
            "git status --short --branch",
            "run exactly one command",
            "run no other command",
            "do not create, edit, delete, rename, stage, commit",
            "if any required operation fails, report the concrete blocker and stop",
        )
    )


def proven_recursive_self_observation_fresh_run_authority(
    state_path: Path,
) -> dict[str, Any] | None:
    """Revalidate the append-only authority receipt without changing historical run state."""
    directory = state_path.parent.resolve(strict=True)
    receipt_path = directory / "settlements" / "recursive-self-observation-fresh-run.json"
    if not receipt_path.is_file() or receipt_path.is_symlink():
        return None
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate settlement key: {key}")
            value[key] = item
        return value
    try:
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(
            receipt_bytes.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate_keys,
        )
        state = load_state(state_path)
        artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
        output_path = Path(str(artifacts.get("output") or directory / "output.md")).resolve(strict=True)
        transcript_path = Path(
            str(artifacts.get("transcript") or directory / "transcript.md")
        ).resolve(strict=True)
        output_text = output_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, OracleStateError, ValueError):
        return None
    if not isinstance(receipt, dict):
        return None
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    if (
        receipt.get("schema") != RECURSIVE_SELF_OBSERVATION_SETTLEMENT_SCHEMA
        or receipt.get("confirmation")
        != USER_AUTHORIZED_FRESH_AFTER_RECURSIVE_SELF_OBSERVATION
        or not str(receipt.get("reason") or "").strip()
        or receipt.get("run_id") != state.get("run_id")
        or receipt.get("project_root") != state.get("project_root")
        or receipt.get("slug") != oracle.get("slug")
        or receipt.get("signature") != "post-submit-recursive-self-observation"
        or output_path != (directory / "output.md").resolve()
        or transcript_path != (directory / "transcript.md").resolve()
        or receipt_path.resolve(strict=True).parent.parent != directory
        or receipt.get("state_sha256") != sha256_file(state_path)
        or receipt.get("output_sha256") != sha256_file(output_path)
        or receipt.get("transcript_sha256") != sha256_file(transcript_path)
        or recursive_self_observation_evidence(state, output_text) is None
        or receipt.get("auto_retry") is not False
        or receipt.get("submission_action") != "none"
    ):
        return None
    return {**receipt, "path": str(receipt_path), "sha256": hashlib.sha256(receipt_bytes).hexdigest()}


def proven_terminal_devspace_nonexecution_fresh_run_authority(
    state_path: Path,
) -> dict[str, Any] | None:
    """Revalidate a task-bound, append-only terminal nonexecution authority."""
    directory = state_path.parent.resolve(strict=True)
    receipt_path = directory / "settlements" / "terminal-devspace-nonexecution-fresh-run.json"
    if not receipt_path.is_file() or receipt_path.is_symlink():
        return None

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate settlement key: {key}")
            value[key] = item
        return value

    try:
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(
            receipt_bytes.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate_keys,
        )
        state = load_state(state_path)
        artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
        output_path = Path(str(artifacts.get("output") or directory / "output.md")).resolve(strict=True)
        transcript_path = Path(
            str(artifacts.get("transcript") or directory / "transcript.md")
        ).resolve(strict=True)
        stdout_path = Path(str(artifacts.get("stdout") or directory / "stdout.log")).resolve(strict=True)
        stderr_path = Path(str(artifacts.get("stderr") or directory / "stderr.log")).resolve(strict=True)
        mission_path = (directory / "mission.md").resolve(strict=True)
        output_text = output_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, OracleStateError, ValueError):
        return None
    if not isinstance(receipt, dict):
        return None
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    exact_paths = {
        "output": (output_path, directory / "output.md"),
        "transcript": (transcript_path, directory / "transcript.md"),
        "stdout": (stdout_path, directory / "stdout.log"),
        "stderr": (stderr_path, directory / "stderr.log"),
        "mission": (mission_path, directory / "mission.md"),
    }
    if any(
        actual != expected.resolve() or expected.is_symlink() or not actual.is_file()
        for actual, expected in exact_paths.values()
    ) or state_path.is_symlink() or state_path.resolve(strict=True) != (directory / "state.json"):
        return None
    evidence = terminal_devspace_nonexecution_evidence(state, output_text)
    authorized_thread = str(receipt.get("authorized_source_thread_id") or "").strip().casefold()
    if (
        receipt.get("schema") != TERMINAL_DEVSPACE_NONEXECUTION_SETTLEMENT_SCHEMA
        or receipt.get("confirmation")
        != USER_AUTHORIZED_FRESH_AFTER_TERMINAL_DEVSPACE_NONEXECUTION
        or not str(receipt.get("reason") or "").strip()
        or SOURCE_THREAD_ID_RE.fullmatch(authorized_thread) is None
        or receipt.get("run_id") != state.get("run_id")
        or receipt.get("project_root") != state.get("project_root")
        or receipt.get("slug") != oracle.get("slug")
        or receipt.get("transport") != state.get("transport")
        or receipt.get("task_outcome") != state.get("task_outcome")
        or receipt.get("signature") not in TERMINAL_DEVSPACE_NONEXECUTION_SIGNATURES
        or receipt_path.resolve(strict=True).parent.parent != directory
        or receipt.get("state_sha256") != sha256_file(state_path)
        or receipt.get("output_sha256") != sha256_file(output_path)
        or receipt.get("transcript_sha256") != sha256_file(transcript_path)
        or receipt.get("stdout_sha256") != sha256_file(stdout_path)
        or receipt.get("stderr_sha256") != sha256_file(stderr_path)
        or receipt.get("mission_sha256") != sha256_file(mission_path)
        or receipt.get("mission_sha256") != mission.get("sha256")
        or evidence is None
        or receipt.get("signature") != evidence.get("signature")
        or receipt.get("auto_retry") is not False
        or receipt.get("submission_action") != "none"
    ):
        return None
    return {
        **receipt,
        "authorized_source_thread_id": authorized_thread,
        "path": str(receipt_path),
        "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
    }


def proven_terminal_devspace_read_route_refresh_fresh_run_authority(
    state_path: Path,
) -> dict[str, Any] | None:
    """Revalidate one task-bound, append-only read-route refresh authority."""
    directory = state_path.parent.resolve(strict=True)
    receipt_path = directory / "settlements" / "terminal-devspace-read-route-refresh-fresh-run.json"
    if not receipt_path.is_file() or receipt_path.is_symlink():
        return None

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate settlement key: {key}")
            value[key] = item
        return value

    try:
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(
            receipt_bytes.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate_keys,
        )
        state = load_state(state_path)
        artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
        output_path = Path(str(artifacts.get("output") or directory / "output.md")).resolve(strict=True)
        transcript_path = Path(
            str(artifacts.get("transcript") or directory / "transcript.md")
        ).resolve(strict=True)
        stdout_path = Path(str(artifacts.get("stdout") or directory / "stdout.log")).resolve(strict=True)
        stderr_path = Path(str(artifacts.get("stderr") or directory / "stderr.log")).resolve(strict=True)
        mission_path = (directory / "mission.md").resolve(strict=True)
        output_text = output_path.read_text(encoding="utf-8", errors="strict")
        mission_text = mission_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, OracleStateError, ValueError):
        return None
    if not isinstance(receipt, dict):
        return None
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    exact_paths = {
        "output": (output_path, directory / "output.md"),
        "transcript": (transcript_path, directory / "transcript.md"),
        "stdout": (stdout_path, directory / "stdout.log"),
        "stderr": (stderr_path, directory / "stderr.log"),
        "mission": (mission_path, directory / "mission.md"),
    }
    if any(
        actual != expected.resolve() or expected.is_symlink() or not actual.is_file()
        for actual, expected in exact_paths.values()
    ) or state_path.is_symlink() or state_path.resolve(strict=True) != (directory / "state.json"):
        return None
    evidence = terminal_devspace_read_route_refresh_evidence(state, output_text)
    authorized_thread = str(receipt.get("authorized_source_thread_id") or "").strip().casefold()
    if (
        receipt.get("schema") != TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SETTLEMENT_SCHEMA
        or receipt.get("confirmation")
        != USER_AUTHORIZED_FRESH_AFTER_DEVSPACE_READ_ROUTE_REFRESH
        or not str(receipt.get("reason") or "").strip()
        or SOURCE_THREAD_ID_RE.fullmatch(authorized_thread) is None
        or source_thread_id_from_state(state) != authorized_thread
        or receipt.get("run_id") != state.get("run_id")
        or receipt.get("project_root") != state.get("project_root")
        or receipt.get("slug") != oracle.get("slug")
        or receipt.get("transport") != state.get("transport")
        or receipt.get("task_outcome") != state.get("task_outcome")
        or receipt.get("app_name") != state.get("app_name")
        or receipt.get("signature") != TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SIGNATURE
        or receipt.get("retry_ordinal") != 1
        or receipt_path.resolve(strict=True).parent.parent != directory
        or receipt.get("state_sha256") != sha256_file(state_path)
        or receipt.get("output_sha256") != sha256_file(output_path)
        or receipt.get("transcript_sha256") != sha256_file(transcript_path)
        or receipt.get("stdout_sha256") != sha256_file(stdout_path)
        or receipt.get("stderr_sha256") != sha256_file(stderr_path)
        or receipt.get("mission_sha256") != sha256_file(mission_path)
        or receipt.get("mission_sha256") != mission.get("sha256")
        or evidence is None
        or receipt.get("signature") != evidence.get("signature")
        or receipt.get("workspace_id") != evidence.get("workspace_id")
        or not terminal_devspace_read_route_refresh_mission_contract(mission_text)
        or receipt.get("auto_retry") is not False
        or receipt.get("submission_action") != "none"
    ):
        return None
    return {
        **receipt,
        "authorized_source_thread_id": authorized_thread,
        "path": str(receipt_path),
        "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
    }


def _state_has_conversation_url(state: dict[str, Any]) -> bool:
    """Recognize URLs that belong to the current Oracle run.

    Qualification, canary, parent, and provenance receipts can legitimately
    preserve URLs from earlier conversations.  Those records are evidence
    inputs, not current-session ownership, so searching the entire state tree
    recursively can retain a false project lock.  Keep this list explicit and
    fail closed for every current provider/oracle URL surface instead.
    """
    current_url_paths = (
        # Legacy top-level current-session fields.
        ("conversation_url",),
        ("conversationUrl",),
        ("canonical_url",),
        ("canonicalUrl",),
        # Canonical Oracle binding and unconfirmed current-run candidates.
        ("oracle", "conversation_url"),
        ("oracle", "conversationUrl"),
        ("oracle", "canonical_url"),
        ("oracle", "canonicalUrl"),
        ("oracle", "conversation_url_candidate"),
        ("oracle", "conversationUrlCandidate"),
        # Current provider observation/binding surfaces.
        ("provider_session", "conversation_url"),
        ("provider_session", "conversationUrl"),
        ("provider_session", "canonical_url"),
        ("provider_session", "canonicalUrl"),
        ("provider_session", "observed_conversation_url"),
        ("provider_session", "bound_conversation_url"),
        # A disagreement still proves that this run observed a conversation.
        ("conversation_url_conflict", "persisted"),
        ("conversation_url_conflict", "observed"),
        # Port-mismatch evidence is unconfirmed for recovery, but is enough to
        # prevent a claim that no current-run conversation URL was observed.
        ("browser_identity", "conversation_url"),
        ("browser_identity", "conversationUrl"),
        ("browser_identity", "conversation_url_candidate"),
        ("browser_identity", "port_mismatch", "conversation_url_candidate"),
    )

    for path in current_url_paths:
        value: Any = state
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if str(value or "").strip():
            return True
    return False


def _artifact_bytes(state: dict[str, Any], name: str) -> tuple[Path, bytes] | None:
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    raw = str(artifacts.get(name) or "").strip()
    if not raw:
        return None
    path = Path(raw)
    try:
        return path, path.read_bytes()
    except OSError:
        return None


def _comprehensive_no_submission_evidence(state_path: Path) -> dict[str, Any] | None:
    """Return exact evidence for a user-adjudicable Oracle composer timeout.

    The Oracle message is not mechanical proof of non-submission.  This helper
    only proves that the run is eligible for an explicit user adjudication: no
    output or conversation URL exists, Oracle reported that the prompt was not
    observed, and exact recovery has neither a live tab nor a saved URL.
    """
    state = load_state(state_path)
    authority = str(state.get("session_authority") or "")
    if authority not in {"submitted_unknown", "pre_submit"}:
        return None
    if state.get("terminal_harvested") is True or _state_has_conversation_url(state):
        return None
    run_dir = state_path.parent
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    if output.resolve() != (run_dir / "output.md").resolve() or output.is_symlink():
        return None
    if str(output) and output_is_nonempty(output):
        return None
    stdout_record = _artifact_bytes(state, "stdout")
    stderr_record = _artifact_bytes(state, "stderr")
    if stdout_record is None or stderr_record is None:
        return None
    stdout_path, stdout_bytes = stdout_record
    stderr_path, stderr_bytes = stderr_record
    if (
        stdout_path.resolve() != (run_dir / "stdout.log").resolve()
        or stderr_path.resolve() != (run_dir / "stderr.log").resolve()
        or stdout_path.is_symlink()
        or stderr_path.is_symlink()
    ):
        return None
    try:
        stdout_text = stdout_bytes.decode("utf-8", errors="strict")
        stderr_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    if not locator or ORACLE_PROMPT_NOT_OBSERVED_MARKER not in stdout_text:
        return None
    if f"Session: {locator}" not in stdout_text:
        return None
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    transport_path = Path(str(mission.get("transport_path") or ""))
    if transport_path.resolve() != (run_dir / "mission.md").resolve() or transport_path.is_symlink():
        return None
    try:
        mission_bytes = transport_path.read_bytes()
        mission_text = mission_bytes.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None
    mission_sha256 = hashlib.sha256(mission_bytes).hexdigest()
    if mission_sha256 != str(mission.get("sha256") or ""):
        return None
    host_marker = "[HOST_STAGE_CONTRACT]"
    workspace_marker = "[DEVSPACE_WORKSPACE_ENTRY_CONTRACT]"
    if mission_text.count(host_marker) != 1 or mission_text.count(workspace_marker) != 1:
        return None
    host_start = mission_text.index(host_marker) + len(host_marker)
    workspace_start = mission_text.index(workspace_marker)
    if workspace_start <= host_start:
        return None
    host_contract = mission_text[host_start:workspace_start]
    binding: dict[str, str] = {}
    for key, pattern in {
        "workflow_id": (
            r"(?m)^workflow_id=((?:[a-f0-9]{32,64}|"
            r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}))\r?$"
        ),
        "stage": r"(?m)^stage=([a-z][a-z0-9-]*)\r?$",
        "attempt_id": r"(?m)^attempt_id=([a-f0-9]{32,64})\r?$",
        "input_mission_sha256": r"(?m)^input_mission_sha256=([a-f0-9]{64})\r?$",
    }.items():
        matches = re.findall(pattern, host_contract)
        if len(matches) != 1:
            return None
        binding[key] = matches[0]
    if binding["attempt_id"] != str(state.get("run_id") or ""):
        return None
    expected_parent = hashlib.sha256(binding["workflow_id"].encode("utf-8")).hexdigest()
    if str(state.get("parallel_parent_id") or "") != expected_parent:
        return None
    contract_paths: dict[str, str] = {}
    for key, pattern in {
        "project_root": r"(?m)^exact_project_root=([^\r\n]+)\r?$",
        "input_mission": r"(?m)^exact_input_mission_path=([^\r\n]+)\r?$",
        "receipt": r"(?m)^Write the small UTF-8 stage receipt to: ([^\r\n]+)\r?$",
    }.items():
        matches = re.findall(pattern, host_contract)
        if len(matches) != 1:
            return None
        contract_paths[key] = matches[0]
    try:
        project_root = Path(str(state.get("project_root") or ""))
        contract_project_root = Path(contract_paths["project_root"])
        if (
            not project_root.is_absolute()
            or not contract_project_root.is_absolute()
            or project_root.resolve(strict=True) != contract_project_root.resolve(strict=True)
            or not project_root.resolve(strict=True).is_dir()
        ):
            return None
        canonical_root = project_root.resolve(strict=True)
        source_mission = Path(str(mission.get("path") or ""))
        input_mission = Path(contract_paths["input_mission"])
        receipt_path = Path(contract_paths["receipt"])
        if (
            not source_mission.is_absolute()
            or source_mission.is_symlink()
            or not input_mission.is_absolute()
            or input_mission.is_symlink()
            or not receipt_path.is_absolute()
            or receipt_path.is_symlink()
        ):
            return None
        source_mission = source_mission.resolve(strict=True)
        input_mission = input_mission.resolve(strict=True)
        receipt_path = receipt_path.resolve(strict=False)
        if (
            not source_mission.is_file()
            or not input_mission.is_file()
            or not is_within(canonical_root, source_mission)
            or not is_within(canonical_root, input_mission)
            or not is_within(canonical_root, receipt_path)
            or receipt_path != source_mission.parent / "stage-result.json"
            or source_mission.read_bytes() != mission_bytes
            or sha256_file(input_mission) != binding["input_mission_sha256"]
        ):
            return None
    except OSError:
        return None
    recovery_records: list[dict[str, str]] = []
    for recovery_stdout in sorted(run_dir.glob("recovery-*-stdout.log"), key=lambda item: item.name):
        recovery_stderr = recovery_stdout.with_name(
            recovery_stdout.name.replace("-stdout.log", "-stderr.log")
        )
        try:
            if recovery_stdout.is_symlink() or recovery_stderr.is_symlink():
                continue
            recovery_stdout_bytes = recovery_stdout.read_bytes()
            recovery_stderr_bytes = recovery_stderr.read_bytes()
            combined = b"\n".join((recovery_stdout_bytes, recovery_stderr_bytes))
            recovery_text = combined.decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            return None
        if ORACLE_RECOVERY_STATE_RE.search(recovery_text):
            return None
        if (
            ORACLE_NO_LIVE_TAB_MARKER not in recovery_text
            or f'"{locator}"' not in recovery_text
            or ORACLE_NO_RECOVERABLE_URL_MARKER not in recovery_text
        ):
            return None
        recovery_records.append({
            "stdout_name": recovery_stdout.name,
            "stdout_sha256": hashlib.sha256(recovery_stdout_bytes).hexdigest(),
            "stderr_name": recovery_stderr.name,
            "stderr_sha256": hashlib.sha256(recovery_stderr_bytes).hexdigest(),
        })
    if not recovery_records:
        return None
    return {
        "project_root": str(state.get("project_root") or ""),
        "run_id": str(state.get("run_id") or ""),
        **binding,
        "mission_sha256": mission_sha256,
        "oracle_locator": locator,
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "recovery_evidence": recovery_records,
        "output_absent": True,
        "conversation_url_absent": True,
        "_augmented_mission_path": str(source_mission),
        "_input_mission_path": str(input_mission),
        "_receipt_path": str(receipt_path),
    }


def _web_multi_child_provenance(
    state: dict[str, Any], run_dir: Path, project_root: Path, source_path: Path, parent_id: str, locator: str,
) -> dict[str, Any] | None:
    """Validate new provenance, or the exact legacy result/lane pair when present."""
    raw = state.get("web_multi_child_provenance")
    candidates: list[tuple[Path, dict[str, Any] | None, Path | None]] = []
    if isinstance(raw, dict):
        path = Path(str(raw.get("path") or ""))
        try:
            if not path.is_absolute() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != str(raw.get("sha256") or ""):
                return None
            candidates.append((path, json.loads(path.read_text(encoding="utf-8", errors="strict")), None))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
    else:
        # Legacy Oracle Multi did not copy lane provenance into state.  Its
        # run-owned result entry and lane manifest are sufficient only when
        # they identify this exact run directory and Oracle locator.
        for result_path in project_root.glob("runtime/*/oracle_output/result.json"):
            try:
                result = json.loads(result_path.read_text(encoding="utf-8", errors="strict"))
                lanes = result.get("lanes") if isinstance(result.get("lanes"), list) else []
                matching = [lane for lane in lanes if isinstance(lane, dict) and Path(str(lane.get("run_dir") or "")).resolve() == run_dir and str(lane.get("session_locator") or "") == locator]
                if result.get("schema") != "codex.chatgpt.oracle-multi-result/v1" or str(result.get("parent_id") or "") != parent_id or len(matching) != 1:
                    continue
                lane_id = str(matching[0].get("id") or "")
                lane_manifest = result_path.parent / "lanes" / lane_id / "oracle.json"
                candidates.append((lane_manifest, json.loads(lane_manifest.read_text(encoding="utf-8", errors="strict")), result_path))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return None
    if len(candidates) != 1:
        return None
    path, value, legacy_result_path = candidates[0]
    if not isinstance(value, dict):
        return None
    if value.get("schema") == "codex.chatgpt.oracle-multi-child-provenance/v1":
        parent_manifest = Path(str(value.get("parent_manifest_path") or ""))
        try:
            if hashlib.sha256(parent_manifest.read_bytes()).hexdigest() != str(value.get("parent_manifest_sha256") or ""):
                return None
            parent = json.loads(parent_manifest.read_text(encoding="utf-8", errors="strict"))
            lanes = parent.get("solvers") if isinstance(parent.get("solvers"), list) else []
            lane = next((item for item in lanes if isinstance(item, dict) and str(item.get("id") or "") == str(value.get("lane_id") or "")), None)
            parent_schema = parent.get("schema")
            if not isinstance(lane, dict) or parent_schema not in {
                "codex.chatgpt.oracle-multi/v1", "codex.chatgpt.oracle-multi/v2"
            }:
                return None
            if parent_schema == "codex.chatgpt.oracle-multi/v1":
                if Path(str(parent.get("project_root") or "")).resolve() != project_root or Path(str(lane.get("mission_path") or "")).resolve() != source_path:
                    return None
            else:
                if (
                    Path(str(lane.get("project_root") or "")).resolve() != project_root
                    or Path(str(value.get("canonical_project_root") or "")).resolve()
                    != Path(str(parent.get("project_root") or "")).resolve()
                    or Path(str(value.get("source_mission_path") or "")).resolve()
                    != Path(str(lane.get("mission_path") or "")).resolve()
                    or Path(str(value.get("mission_path") or "")).resolve() != source_path
                ):
                    return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
    else:
        lane = value
    if str(value.get("parent_id") or value.get("parallel_parent_id") or "") != parent_id:
        return None
    if Path(str(value.get("project_root") or "")).resolve() != project_root or Path(str(value.get("mission_path") or "")).resolve() != source_path:
        return None
    if value.get("schema") == "codex.chatgpt.oracle-multi-child-provenance/v1":
        return {
            "provenance_mode": "new-child-provenance/v1",
            "child_provenance_path": str(path.resolve()), "child_provenance_sha256": sha256_file(path),
            "parent_manifest_path": str(parent_manifest.resolve()), "parent_manifest_sha256": sha256_file(parent_manifest),
        }
    if legacy_result_path is None:
        return None
    return {
        "provenance_mode": "legacy-result-lane/v1",
        "legacy_result_path": str(legacy_result_path.resolve()), "legacy_result_sha256": sha256_file(legacy_result_path),
        "legacy_lane_manifest_path": str(path.resolve()), "legacy_lane_manifest_sha256": sha256_file(path),
    }


def _web_multi_child_no_submission_evidence(state_path: Path) -> dict[str, Any] | None:
    """Return fail-closed settlement evidence for a direct Oracle Multi child."""
    state = load_state(state_path)
    if str(state.get("session_authority") or "") not in {"submitted_unknown", "pre_submit"}:
        return None
    parent_id = str(state.get("parallel_parent_id") or "").strip().casefold()
    run_id = str(state.get("run_id") or "")
    if PARENT_ID_RE.fullmatch(parent_id) is None or WEB_MULTI_CHILD_RUN_ID_RE.fullmatch(run_id) is None or state.get("requested_run_id") is not None:
        return None
    if state.get("terminal_harvested") is True or _state_has_conversation_url(state):
        return None
    run_dir = state_path.parent
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    if output.resolve() != (run_dir / "output.md").resolve() or output.is_symlink() or output_is_nonempty(output):
        return None
    stdout_record = _artifact_bytes(state, "stdout")
    stderr_record = _artifact_bytes(state, "stderr")
    if stdout_record is None or stderr_record is None:
        return None
    stdout_path, stdout_bytes = stdout_record
    stderr_path, stderr_bytes = stderr_record
    if (stdout_path.resolve() != (run_dir / "stdout.log").resolve() or stderr_path.resolve() != (run_dir / "stderr.log").resolve() or stdout_path.is_symlink() or stderr_path.is_symlink()):
        return None
    try:
        stdout_text = stdout_bytes.decode("utf-8", errors="strict")
        stderr_text = stderr_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    transcript_record = _artifact_bytes(state, "transcript")
    if transcript_record is None:
        return None
    transcript_path, transcript_bytes = transcript_record
    try:
        transcript_text = transcript_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if transcript_path.resolve() != (run_dir / "transcript.md").resolve() or transcript_path.is_symlink() or any(CHATGPT_CONVERSATION_URL_RE.search(text) for text in (stdout_text, stderr_text, transcript_text)):
        return None
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    if not locator or ORACLE_PROMPT_NOT_OBSERVED_MARKER not in stdout_text or f"Session: {locator}" not in stdout_text:
        return None
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    source_path = Path(str(mission.get("path") or ""))
    transport_path = Path(str(mission.get("transport_path") or ""))
    try:
        project_root = Path(str(state.get("project_root") or "")).resolve(strict=True)
        if not project_root.is_dir() or not source_path.is_absolute() or not transport_path.is_absolute() or source_path.is_symlink() or transport_path.is_symlink():
            return None
        source_path = source_path.resolve(strict=True)
        transport_path = transport_path.resolve(strict=True)
        if not source_path.is_file() or transport_path != (run_dir / "mission.md").resolve() or not is_within(project_root, source_path):
            return None
        source_bytes = source_path.read_bytes()
        transport_bytes = transport_path.read_bytes()
    except OSError:
        return None
    mission_sha256 = str(mission.get("sha256") or "")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    transport_sha256 = hashlib.sha256(transport_bytes).hexdigest()
    if not re.fullmatch(r"[a-f0-9]{64}", mission_sha256) or source_sha256 != mission_sha256 or transport_sha256 != mission_sha256 or source_bytes != transport_bytes:
        return None
    provenance = _web_multi_child_provenance(state, run_dir, project_root, source_path, parent_id, locator)
    if provenance is None:
        return None
    recovery_records: list[dict[str, str]] = []
    for recovery_stdout in sorted(run_dir.glob("recovery-*-stdout.log"), key=lambda item: item.name):
        recovery_stderr = recovery_stdout.with_name(recovery_stdout.name.replace("-stdout.log", "-stderr.log"))
        try:
            if recovery_stdout.is_symlink() or recovery_stderr.is_symlink():
                continue
            recovery_stdout_bytes = recovery_stdout.read_bytes()
            recovery_stderr_bytes = recovery_stderr.read_bytes()
            recovery_text = b"\n".join((recovery_stdout_bytes, recovery_stderr_bytes)).decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            return None
        if CHATGPT_CONVERSATION_URL_RE.search(recovery_text) or ORACLE_RECOVERY_STATE_RE.search(recovery_text) or ORACLE_NO_LIVE_TAB_MARKER not in recovery_text or f'"{locator}"' not in recovery_text or ORACLE_NO_RECOVERABLE_URL_MARKER not in recovery_text:
            return None
        recovery_records.append({"stdout_name": recovery_stdout.name, "stdout_sha256": hashlib.sha256(recovery_stdout_bytes).hexdigest(), "stderr_name": recovery_stderr.name, "stderr_sha256": hashlib.sha256(recovery_stderr_bytes).hexdigest()})
    if not recovery_records:
        return None
    return {
        "settlement_eligibility": "oracle-web-multi-child/v1",
        "project_root": str(project_root), "run_id": run_id, "parallel_parent_id": parent_id,
        "source_mission_path": str(source_path), "source_mission_sha256": source_sha256,
        "transport_mission_path": str(transport_path), "transport_mission_sha256": transport_sha256,
        "mission_sha256": mission_sha256, "oracle_locator": locator,
        **provenance,
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(), "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "recovery_evidence": recovery_records, "output_absent": True, "conversation_url_absent": True,
        "_source_mission_path": str(source_path), "_transport_mission_path": str(transport_path),
    }


def _settlement_logs_have_conversation_url(state_path: Path) -> bool:
    state = load_state(state_path)
    for name in ("stdout", "stderr"):
        record = _artifact_bytes(state, name)
        if record is None:
            continue
        try:
            if CHATGPT_CONVERSATION_URL_RE.search(record[1].decode("utf-8", errors="strict")):
                return True
        except UnicodeDecodeError:
            return True
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    transcript_raw = str(artifacts.get("transcript") or "").strip()
    canonical_transcript = state_path.parent / "transcript.md"
    if transcript_raw:
        try:
            if Path(transcript_raw).resolve() != canonical_transcript.resolve():
                return True
        except OSError:
            return True
    if canonical_transcript.is_symlink():
        return True
    if canonical_transcript.exists():
        try:
            if CHATGPT_CONVERSATION_URL_RE.search(canonical_transcript.read_text(encoding="utf-8", errors="strict")):
                return True
        except (OSError, UnicodeDecodeError):
            return True
    try:
        for path in state_path.parent.glob("recovery-*-*.log"):
            if path.is_symlink() or CHATGPT_CONVERSATION_URL_RE.search(path.read_text(encoding="utf-8", errors="strict")):
                return True
    except (OSError, UnicodeDecodeError):
        return True
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    if locator:
        session_root = Path(
            os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
        ).resolve()
        meta_path = session_root / locator / "meta.json"
        if meta_path.is_symlink() or meta_path.parent.is_symlink():
            return True
        if meta_path.exists():
            try:
                if CHATGPT_CONVERSATION_URL_RE.search(
                    meta_path.read_text(encoding="utf-8", errors="strict")
                ):
                    return True
            except (OSError, UnicodeDecodeError):
                return True
    return False


def _standalone_pro_attachment_no_submission_evidence(
    state_path: Path,
    *,
    allow_settled_attachment_source_drift: bool = False,
    require_recovery_evidence: bool = True,
) -> dict[str, Any] | None:
    """Return bounded user-adjudication evidence for attachment upload timeout.

    Oracle 0.17.1 reports this exact error before it can submit the prompt, but
    the local observer alone is not allowed to release ownership.  This helper
    therefore only establishes eligibility: the exact user confirmation token
    remains mandatory, and every run, mission, attachment, log, recovery, and
    locator binding is revalidated whenever the settlement is consumed.
    """
    state = load_state(state_path)
    run_dir = state_path.parent
    run_id = str(state.get("run_id") or "")
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    if (
        state.get("schema") != "codex.chatgpt.oracle-run-state/v1"
        or str(state.get("session_authority") or "") not in {"submitted_unknown", "pre_submit"}
        or state.get("status") != "attention_required"
        or state.get("transport_status") not in {"failed", "not_submitted_user_confirmed"}
        or state.get("task_outcome") != "pending"
        or state.get("terminal_harvested") is True
        or _state_has_conversation_url(state)
        or int(state.get("exit_code") or 0) == 0
        or str(state.get("mode") or "") != "browser"
        or not is_attachment_transport(str(state.get("transport") or ""))
        or state.get("app_name") is not None
        or state.get("parallel_parent_id") is not None
        or state.get("requested_run_id") not in (None, run_id)
        or state.get("web_multi_child_provenance") is not None
        or run_dir.name != run_id
        or str(profile.get("model") or "") != "gpt-5.6-sol"
        or str(profile.get("model_strategy") or "") != "select"
        or not is_compatible_pro_thinking_time(profile.get("thinking_time"))
    ):
        return None

    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    version = str(oracle.get("resolved_version") or "").removeprefix("oracle ").strip()
    locator = str(oracle.get("session_locator") or "").strip()
    slug = str(oracle.get("slug") or "").strip()
    project_words = (re.findall(r"[a-z0-9]+", Path(str(state.get("project_root") or "")).name.casefold()) or ["project"])[:3]
    expected_locator = f"oracle-{'-'.join(word[:10] for word in project_words)}-{run_id.rsplit('-', 1)[-1][:10]}"
    if (
        version not in ORACLE_STANDALONE_PRO_NO_SUBMISSION_VERSIONS
        or not locator
        or locator != slug
        or locator != expected_locator
    ):
        return None

    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    if output.resolve() != (run_dir / "output.md").resolve() or output.is_symlink() or output_is_nonempty(output):
        return None
    records = {name: _artifact_bytes(state, name) for name in ("stdout", "stderr", "transcript")}
    if any(record is None for record in records.values()):
        return None
    stdout_path, stdout_bytes = records["stdout"]  # type: ignore[misc]
    stderr_path, stderr_bytes = records["stderr"]  # type: ignore[misc]
    transcript_path, transcript_bytes = records["transcript"]  # type: ignore[misc]
    if (
        stdout_path.resolve() != (run_dir / "stdout.log").resolve()
        or stderr_path.resolve() != (run_dir / "stderr.log").resolve()
        or transcript_path.resolve() != (run_dir / "transcript.md").resolve()
        or any(path.is_symlink() for path in (stdout_path, stderr_path, transcript_path))
        or stderr_bytes
        or transcript_bytes != stdout_bytes
    ):
        return None
    try:
        stdout_text = stdout_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    expected_attachment_marker_lines = {
        f"ERROR: {ORACLE_ATTACHMENTS_UPLOAD_TIMEOUT_MARKER}",
        f"User error (browser-automation): {ORACLE_ATTACHMENTS_UPLOAD_TIMEOUT_MARKER}",
    }
    observed_attachment_marker_lines = {
        line.strip()
        for line in stdout_text.splitlines()
        if ORACLE_ATTACHMENTS_UPLOAD_TIMEOUT_MARKER in line
    }
    expected_prompt_marker_lines = {
        f"ERROR: {ORACLE_PROMPT_NOT_OBSERVED_MARKER}",
        f"User error (browser-automation): {ORACLE_PROMPT_NOT_OBSERVED_MARKER}",
    }
    observed_prompt_marker_lines = {
        line.strip()
        for line in stdout_text.splitlines()
        if ORACLE_PROMPT_NOT_OBSERVED_MARKER in line
    }
    attachment_timeout = (
        observed_attachment_marker_lines == expected_attachment_marker_lines
        and stdout_text.count(ORACLE_ATTACHMENTS_UPLOAD_TIMEOUT_MARKER) == 2
        and not observed_prompt_marker_lines
    )
    prompt_timeout = (
        observed_prompt_marker_lines == expected_prompt_marker_lines
        and stdout_text.count(ORACLE_PROMPT_NOT_OBSERVED_MARKER)
        == len(observed_prompt_marker_lines)
        and not observed_attachment_marker_lines
    )
    if (
        attachment_timeout == prompt_timeout
        or f"Session: {locator}" not in stdout_text
        or CHATGPT_CONVERSATION_URL_RE.search(stdout_text)
    ):
        return None

    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    source_path = Path(str(mission.get("path") or ""))
    transport_path = Path(str(mission.get("transport_path") or ""))
    try:
        project_root = Path(str(state.get("project_root") or "")).resolve(strict=True)
        source_path = source_path.resolve(strict=True)
        transport_path = transport_path.resolve(strict=True)
        if (
            not project_root.is_dir()
            or not source_path.is_file()
            or source_path.is_symlink()
            or transport_path.is_symlink()
            or transport_path != (run_dir / "mission.md").resolve()
        ):
            return None
        source_bytes = source_path.read_bytes()
        transport_bytes = transport_path.read_bytes()
    except OSError:
        return None
    mission_sha256 = str(mission.get("sha256") or "")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    transport_sha256 = hashlib.sha256(transport_bytes).hexdigest()
    if (
        not re.fullmatch(r"[a-f0-9]{64}", mission_sha256)
        or source_sha256 != mission_sha256
        or transport_sha256 != mission_sha256
        or source_bytes != transport_bytes
    ):
        return None

    attachments = state.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return None
    attachment_evidence: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    mission_attachment_found = False
    for item in attachments:
        if not isinstance(item, dict):
            return None
        recorded_path = Path(str(item.get("path") or ""))
        recorded_sha256 = str(item.get("sha256") or "")
        try:
            recorded_size = int(item.get("size_bytes"))
            attachment_path = recorded_path.resolve(
                strict=not allow_settled_attachment_source_drift
            )
            if (
                not recorded_path.is_absolute()
                or not re.fullmatch(r"[a-f0-9]{64}", recorded_sha256)
                or recorded_size < 0
            ):
                return None
        except (OSError, TypeError, ValueError):
            return None
        normalized_path = os.path.normcase(str(attachment_path))
        if normalized_path in seen_paths:
            return None
        seen_paths.add(normalized_path)
        if attachment_path == source_path:
            if not attachment_path.is_file() or attachment_path.is_symlink():
                return None
            mission_attachment_found = (
                recorded_sha256 == mission_sha256 and recorded_size == len(source_bytes)
            )
            actual_sha256 = source_sha256
        elif allow_settled_attachment_source_drift:
            # A user-confirmed settlement binds the original attachment
            # identity in both state.json and its hashed receipt.  Project
            # support files may legitimately evolve later; re-reading those
            # mutable paths would resurrect a false submitted_unknown owner.
            actual_sha256 = recorded_sha256
        else:
            try:
                if not attachment_path.is_file() or attachment_path.is_symlink():
                    return None
                attachment_bytes = attachment_path.read_bytes()
            except OSError:
                return None
            actual_sha256 = hashlib.sha256(attachment_bytes).hexdigest()
            if actual_sha256 != recorded_sha256 or len(attachment_bytes) != recorded_size:
                return None
        attachment_evidence.append({
            "path": str(attachment_path),
            "sha256": actual_sha256,
            "size_bytes": recorded_size,
        })
    if not mission_attachment_found:
        return None
    attachment_manifest_sha256 = hashlib.sha256(
        json.dumps(attachment_evidence, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    recovery_records: list[dict[str, str]] = []
    for recovery_stdout in sorted(run_dir.glob("recovery-*-stdout.log"), key=lambda item: item.name):
        recovery_stderr = recovery_stdout.with_name(recovery_stdout.name.replace("-stdout.log", "-stderr.log"))
        try:
            if recovery_stdout.is_symlink() or recovery_stderr.is_symlink():
                return None
            recovery_stdout_bytes = recovery_stdout.read_bytes()
            recovery_stderr_bytes = recovery_stderr.read_bytes()
            recovery_text = b"\n".join((recovery_stdout_bytes, recovery_stderr_bytes)).decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            return None
        if (
            CHATGPT_CONVERSATION_URL_RE.search(recovery_text)
            or ORACLE_RECOVERY_STATE_RE.search(recovery_text)
            or ORACLE_NO_LIVE_TAB_MARKER not in recovery_text
            or f'"{locator}"' not in recovery_text
            or ORACLE_NO_RECOVERABLE_URL_MARKER not in recovery_text
        ):
            return None
        recovery_records.append({
            "stdout_name": recovery_stdout.name,
            "stdout_sha256": hashlib.sha256(recovery_stdout_bytes).hexdigest(),
            "stderr_name": recovery_stderr.name,
            "stderr_sha256": hashlib.sha256(recovery_stderr_bytes).hexdigest(),
        })
    if require_recovery_evidence and not recovery_records:
        return None
    return {
        "settlement_eligibility": "oracle-standalone-pro-attachment/v1",
        "project_root": str(project_root),
        "run_id": run_id,
        "transport": "pro-attachment-only",
        "source_mission_path": str(source_path),
        "source_mission_sha256": source_sha256,
        "transport_mission_path": str(transport_path),
        "transport_mission_sha256": transport_sha256,
        "mission_sha256": mission_sha256,
        "attachment_evidence": attachment_evidence,
        "attachment_manifest_sha256": attachment_manifest_sha256,
        "oracle_locator": locator,
        "oracle_version": version,
        "oracle_command": list(oracle.get("command") or []),
        "pre_submit_marker": (
            ORACLE_ATTACHMENTS_UPLOAD_TIMEOUT_MARKER
            if attachment_timeout
            else ORACLE_PROMPT_NOT_OBSERVED_MARKER
        ),
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "recovery_evidence": recovery_records,
        "output_absent": True,
        "conversation_url_absent": True,
        "_pre_submit_failure_kind": (
            "attachment-upload-timeout" if attachment_timeout else "prompt-not-observed"
        ),
        "_source_mission_path": str(source_path),
        "_transport_mission_path": str(transport_path),
    }


def _standalone_pro_no_submission_evidence(
    state_path: Path,
    *,
    require_recovery_evidence: bool = True,
) -> dict[str, Any] | None:
    """Return bounded evidence for user adjudication of one qualified Pro run.

    Prompt-observation timeouts remain eligible through their exact transcript.
    A legacy model-selector-button failure additionally requires Oracle 0.17.1's exact
    session ledger to prove the browser never left the ChatGPT home composer and
    ``promptSubmitted`` stayed false.
    """
    state = load_state(state_path)
    run_dir = state_path.parent
    run_id = str(state.get("run_id") or "")
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    if (
        state.get("schema") != "codex.chatgpt.oracle-run-state/v1"
        or str(state.get("session_authority") or "") not in {"submitted_unknown", "pre_submit"}
        or state.get("status") != "attention_required"
        or state.get("transport_status") not in {"failed", "not_submitted_user_confirmed"}
        or state.get("task_outcome") != "pending"
        or state.get("terminal_harvested") is True
        or _state_has_conversation_url(state)
        or int(state.get("exit_code") or 0) == 0
        or str(state.get("mode") or "") != "browser"
        or not is_pro_devspace_transport(str(state.get("transport") or ""))
        or state.get("parallel_parent_id") is not None
        or state.get("requested_run_id") not in (None, run_id)
        or state.get("web_multi_child_provenance") is not None
        or state.get("attachments") not in (None, [])
        or run_dir.name != run_id
        or str(profile.get("model") or "") != "gpt-5.6-sol"
        or str(profile.get("model_strategy") or "") != "select"
        or not is_compatible_pro_thinking_time(profile.get("thinking_time"))
    ):
        return None
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    version = str(oracle.get("resolved_version") or "").removeprefix("oracle ").strip()
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    if version not in ORACLE_STANDALONE_PRO_NO_SUBMISSION_VERSIONS or not locator:
        return None
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    if output.resolve() != (run_dir / "output.md").resolve() or output.is_symlink() or output_is_nonempty(output):
        return None
    records = {name: _artifact_bytes(state, name) for name in ("stdout", "stderr", "transcript")}
    if any(record is None for record in records.values()):
        return None
    stdout_path, stdout_bytes = records["stdout"]  # type: ignore[misc]
    stderr_path, stderr_bytes = records["stderr"]  # type: ignore[misc]
    transcript_path, transcript_bytes = records["transcript"]  # type: ignore[misc]
    if (
        stdout_path.resolve() != (run_dir / "stdout.log").resolve()
        or stderr_path.resolve() != (run_dir / "stderr.log").resolve()
        or transcript_path.resolve() != (run_dir / "transcript.md").resolve()
        or any(path.is_symlink() for path in (stdout_path, stderr_path, transcript_path))
        or stderr_bytes
        or transcript_bytes != stdout_bytes
    ):
        return None
    try:
        stdout_text = stdout_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    prompt_marker_lines = {
        line.strip()
        for line in stdout_text.splitlines()
        if ORACLE_PROMPT_NOT_OBSERVED_MARKER in line
    }
    allowed_prompt_marker_lines = {
        f"ERROR: {ORACLE_PROMPT_NOT_OBSERVED_MARKER}",
        f"User error (browser-automation): {ORACLE_PROMPT_NOT_OBSERVED_MARKER}",
    }
    selector_marker_lines = {
        line.strip()
        for line in stdout_text.splitlines()
        if ORACLE_MODEL_SELECTOR_BUTTON_PRE_SUBMIT_ERROR in line
    }
    allowed_selector_marker_lines = {
        f"ERROR: {ORACLE_MODEL_SELECTOR_BUTTON_PRE_SUBMIT_ERROR}",
        f"User error (browser-automation): {ORACLE_MODEL_SELECTOR_BUTTON_PRE_SUBMIT_ERROR}",
    }
    prompt_marker_valid = (
        bool(prompt_marker_lines)
        and prompt_marker_lines.issubset(allowed_prompt_marker_lines)
        and stdout_text.count(ORACLE_PROMPT_NOT_OBSERVED_MARKER) == len(prompt_marker_lines)
    )
    selector_marker_valid = (
        selector_marker_lines == allowed_selector_marker_lines
        and stdout_text.count(ORACLE_MODEL_SELECTOR_BUTTON_PRE_SUBMIT_ERROR) == 2
    )
    if (
        prompt_marker_valid == selector_marker_valid
        or f"Session: {locator}" not in stdout_text
        or CHATGPT_CONVERSATION_URL_RE.search(stdout_text)
    ):
        return None
    selector_meta_evidence: dict[str, Any] = {}
    if selector_marker_valid:
        lines = stdout_text.splitlines()
        expected_tail = [
            f"ERROR: {ORACLE_MODEL_SELECTOR_BUTTON_PRE_SUBMIT_ERROR}",
            f"User error (browser-automation): {ORACLE_MODEL_SELECTOR_BUTTON_PRE_SUBMIT_ERROR}",
        ]
        if (
            len(lines) != 13
            or re.fullmatch(r".{1,4} oracle 0\.17\.1 .{2,120}", lines[0]) is None
            or lines[1] != f"Session: {locator}"
            or lines[2:6] != [
                "Mode: browser foreground",
                "Models: 1",
                "Detach: no",
                f"Reattach: oracle session {locator}",
            ]
            or not re.fullmatch(
                r"Launching browser mode \(target=GPT-5\.6 Sol; requested=gpt-5\.6-sol\) "
                r"with ~[1-9][0-9]* tokens\.",
                lines[6],
            )
            or lines[7:11] != [
                "This run can take up to an hour (usually ~10 minutes).",
                "[browser] Browser control: launch Chrome in hidden-window mode; may focus/control the browser UI.",
                "[browser] Browser guidance: On macOS, Oracle launches Chrome off-screen while keeping the page rendered.",
                "[browser] Browser guidance: For the calmest shared-desktop flow, prefer --browser-attach-running or --remote-chrome.",
            ]
            or lines[-2:] != expected_tail
        ):
            return None
        session_root = Path(
            os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
        ).resolve()
        meta_path = session_root / locator / "meta.json"
        if meta_path.is_symlink():
            return None
        try:
            meta_bytes = meta_path.read_bytes()
            meta = json.loads(meta_bytes.decode("utf-8", errors="strict"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
        config = browser.get("config") if isinstance(browser.get("config"), dict) else {}
        runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
        error = meta.get("error") if isinstance(meta.get("error"), dict) else {}
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        details_runtime = (
            details.get("runtime") if isinstance(details.get("runtime"), dict) else {}
        )
        options = meta.get("options") if isinstance(meta.get("options"), dict) else {}
        option_browser = (
            options.get("browserConfig")
            if isinstance(options.get("browserConfig"), dict)
            else {}
        )
        try:
            meta_cwd = Path(str(meta.get("cwd") or "")).resolve()
            meta_output = Path(str(options.get("writeOutputPath") or "")).resolve()
            state_profile = Path(str(profile.get("copy_profile") or "")).resolve()
            config_profile = Path(str(config.get("copyProfileSource") or "")).resolve()
            option_profile = Path(str(option_browser.get("copyProfileSource") or "")).resolve()
        except OSError:
            return None
        if not str(profile.get("copy_profile") or "").strip() or not state_profile.is_absolute():
            return None
        if (
            meta.get("id") != locator
            or meta.get("status") != "error"
            or meta.get("model") != "gpt-5.6-sol"
            or meta.get("mode") != "browser"
            or not str(meta.get("completedAt") or "").strip()
            or meta_cwd != Path(str(state.get("project_root") or "")).resolve()
            or meta_output != output.resolve()
            or config_profile != state_profile
            or option_profile != state_profile
            or config.get("desiredModel") != "GPT-5.6 Sol"
            or config.get("modelStrategy") != "select"
            or config.get("thinkingTime") != "heavy"
            or options.get("model") != "gpt-5.6-sol"
            or options.get("slug") != locator
            or option_browser.get("modelStrategy") != "select"
            or option_browser.get("desiredModel") != "GPT-5.6 Sol"
            or option_browser.get("thinkingTime") != "heavy"
            or runtime.get("promptSubmitted") is not False
            or runtime.get("tabUrl") != "https://chatgpt.com/"
            or details_runtime.get("promptSubmitted") not in {None, False}
            or error.get("category") != "browser-automation"
            or error.get("message") != ORACLE_MODEL_SELECTOR_BUTTON_PRE_SUBMIT_ERROR
            or details.get("stage") != "execute-browser"
            or str(meta.get("errorMessage") or "")
            != ORACLE_MODEL_SELECTOR_BUTTON_PRE_SUBMIT_ERROR
            or CHATGPT_CONVERSATION_URL_RE.search(
                meta_bytes.decode("utf-8", errors="strict")
            )
        ):
            return None
        selector_meta_evidence = {
            "pre_submit_marker": "oracle-model-selector-button-missing/v1",
            "oracle_meta_path": str(meta_path),
            "oracle_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
            "oracle_meta_stage": "execute-browser",
            "prompt_submitted": False,
            "tab_url": "https://chatgpt.com/",
        }
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    source_path = Path(str(mission.get("path") or ""))
    transport_path = Path(str(mission.get("transport_path") or ""))
    source_path_is_symlink = source_path.is_symlink()
    transport_path_is_symlink = transport_path.is_symlink()
    try:
        project_root = Path(str(state.get("project_root") or "")).resolve(strict=True)
        source_path = source_path.resolve()
        transport_path = transport_path.resolve(strict=True)
        if (
            not project_root.is_dir()
            or source_path_is_symlink
            or transport_path_is_symlink
            or not is_within(project_root, source_path)
            or transport_path != (run_dir / "mission.md").resolve()
        ):
            return None
        transport_bytes = transport_path.read_bytes()
    except OSError:
        return None
    mission_sha256 = str(mission.get("sha256") or "")
    transport_sha256 = hashlib.sha256(transport_bytes).hexdigest()
    if (
        not re.fullmatch(r"[a-f0-9]{64}", mission_sha256)
        or transport_sha256 != mission_sha256
    ):
        return None
    try:
        source_bytes = source_path.read_bytes()
    except OSError:
        source_bytes = None
    source_matches_run = (
        source_bytes is not None
        and hashlib.sha256(source_bytes).hexdigest() == mission_sha256
        and source_bytes == transport_bytes
    )
    ownership = proven_ownership_receipt(state_path)
    source_bound_by_ownership = (
        not source_matches_run
        and ownership is not None
        and source_thread_id_from_state(state) is not None
        and ownership.get("payload", {}).get("mission_sha256") == mission_sha256
    )
    if source_bound_by_ownership:
        oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
        locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
        session_root = Path(
            os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
        ).resolve()
        meta_path = session_root / locator / "meta.json"
        try:
            if meta_path.is_symlink() or meta_path.parent.is_symlink():
                return None
            meta = json.loads(meta_path.read_text(encoding="utf-8", errors="strict"))
            options = meta.get("options") if isinstance(meta.get("options"), dict) else {}
            submitted_prompt = str(options.get("prompt") or "")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        expected_source_binding = f"read-only mission file: {source_path}."
        if expected_source_binding not in submitted_prompt:
            return None
    if not source_matches_run and not source_bound_by_ownership:
        return None
    source_sha256 = mission_sha256
    recovery_records: list[dict[str, str]] = []
    for recovery_stdout in sorted(run_dir.glob("recovery-*-stdout.log"), key=lambda item: item.name):
        recovery_stderr = recovery_stdout.with_name(recovery_stdout.name.replace("-stdout.log", "-stderr.log"))
        try:
            if recovery_stdout.is_symlink() or recovery_stderr.is_symlink():
                return None
            recovery_stdout_bytes = recovery_stdout.read_bytes()
            recovery_stderr_bytes = recovery_stderr.read_bytes()
            recovery_text = b"\n".join((recovery_stdout_bytes, recovery_stderr_bytes)).decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            return None
        if (
            CHATGPT_CONVERSATION_URL_RE.search(recovery_text)
            or ORACLE_RECOVERY_STATE_RE.search(recovery_text)
            or ORACLE_NO_LIVE_TAB_MARKER not in recovery_text
            or f'"{locator}"' not in recovery_text
            or ORACLE_NO_RECOVERABLE_URL_MARKER not in recovery_text
        ):
            return None
        recovery_records.append({
            "stdout_name": recovery_stdout.name,
            "stdout_sha256": hashlib.sha256(recovery_stdout_bytes).hexdigest(),
            "stderr_name": recovery_stderr.name,
            "stderr_sha256": hashlib.sha256(recovery_stderr_bytes).hexdigest(),
        })
    if require_recovery_evidence and not recovery_records:
        return None
    return {
        "settlement_eligibility": "oracle-standalone-qualified-pro/v1",
        "project_root": str(project_root),
        "run_id": run_id,
        "transport": str(state.get("transport") or ""),
        "source_mission_path": str(source_path),
        "source_mission_sha256": source_sha256,
        "transport_mission_path": str(transport_path),
        "transport_mission_sha256": transport_sha256,
        "mission_sha256": mission_sha256,
        "oracle_locator": locator,
        "oracle_version": version,
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "recovery_evidence": recovery_records,
        "output_absent": True,
        "conversation_url_absent": True,
        "_pre_submit_failure_kind": (
            "model-selector-button-missing" if selector_marker_valid else "prompt-not-observed"
        ),
        "_source_mission_binding": (
            "ownership-receipt" if source_bound_by_ownership else "current-source-bytes"
        ),
        **selector_meta_evidence,
        "_source_mission_path": str(source_path),
        "_transport_mission_path": str(transport_path),
    }


def _bounded_task_owned_prompt_timeout_evidence(
    state_path: Path,
    *,
    allow_recovery_evidence: bool,
) -> dict[str, Any] | None:
    """Validate the exact zero-turn timeout tuple before/after its one harvest.

    Before recovery this admits only zero recovery records; after the exact
    harvest it admits the same direct evidence so the pre-harvest proof can be
    persisted and revalidated for settlement. Callers choose the phase rather
    than weakening the public harvest predicate.
    """
    followup = _followup_no_submission_evidence(state_path, require_recovery_evidence=False)
    if (
        followup is not None
        and followup.get("failure_kind") != "archived-parent-unarchive-menu-absent"
    ):
        return followup
    direct_evidence = _direct_devspace_no_submission_evidence(
        state_path,
        require_persisted_recovery=False,
        require_recovery_evidence=False,
    )
    if (
        direct_evidence is not None
        and direct_evidence.get("pre_submit_marker")
        == "oracle-model-option-missing/v1"
        and direct_evidence.get("recovery_evidence") == []
    ):
        return {
            **direct_evidence,
            "schema": "codex.chatgpt.oracle-bounded-model-option-harvest/v1",
            "_bounded_harvest_kind": "direct-devspace-model-option-missing",
        }
    # The ordinary DevSpace predicate already verifies the exact state/log/mission
    # tuple. It needs recovery evidence for settlement, but the separate zero-turn
    # Oracle ledger proof below permits one exact harvest to create that evidence.
    direct_recovery_evidence = (
        direct_evidence.get("recovery_evidence") if direct_evidence is not None else None
    )
    attachment_evidence = _standalone_pro_attachment_no_submission_evidence(
        state_path,
        require_recovery_evidence=False,
    )
    if (
        direct_evidence is not None
        and isinstance(direct_recovery_evidence, list)
        and (allow_recovery_evidence or direct_recovery_evidence == [])
    ):
        evidence = direct_evidence
    elif (
        attachment_evidence is not None
        and attachment_evidence.get("_pre_submit_failure_kind") == "prompt-not-observed"
        and attachment_evidence.get("recovery_evidence") == []
    ):
        evidence = attachment_evidence
    else:
        evidence = _standalone_pro_no_submission_evidence(
            state_path,
            require_recovery_evidence=False,
        )
    if (
        evidence is None
        or (
            evidence.get("recovery_evidence") != []
            and not (
                allow_recovery_evidence
                and evidence.get("settlement_eligibility") == "oracle-direct-devspace/v1"
                and bool(evidence.get("recovery_evidence"))
            )
        )
    ):
        return None
    if evidence.get("settlement_eligibility") in {
        "oracle-standalone-qualified-pro/v1",
        "oracle-standalone-pro-attachment/v1",
    }:
        if evidence.get("_pre_submit_failure_kind") != "prompt-not-observed":
            return None
    elif evidence.get("settlement_eligibility") != "oracle-direct-devspace/v1":
        return None
    state = load_state(state_path)
    run_dir = state_path.parent.resolve()
    owner = proven_ownership_receipt(state_path)
    source_thread_id = source_thread_id_from_state(state)
    identity = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    receipt_path = browser_identity_receipt_path(run_dir)
    if (
        owner is None
        or not source_thread_id
        or Path(str(owner.get("path") or "")).is_symlink()
        or identity.get("receipt_path") not in {None, ""}
        or identity.get("receipt_sha256") not in {None, ""}
        or receipt_path.exists()
        or receipt_path.is_symlink()
        or proven_browser_identity_receipt(state_path) is not None
    ):
        return None

    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    session_root = Path(
        os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
    ).resolve()
    meta_path = session_root / locator / "meta.json"
    if not locator or meta_path.is_symlink() or meta_path.parent.is_symlink():
        return None
    try:
        meta_bytes = meta_path.read_bytes()
        meta = json.loads(meta_bytes.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict) or CHATGPT_CONVERSATION_URL_RE.search(
        meta_bytes.decode("utf-8", errors="strict")
    ):
        return None

    browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
    config = browser.get("config") if isinstance(browser.get("config"), dict) else {}
    runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
    archive = browser.get("archive")
    error = meta.get("error") if isinstance(meta.get("error"), dict) else {}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    probe = details.get("commitProbe") if isinstance(details.get("commitProbe"), dict) else {}
    options = meta.get("options") if isinstance(meta.get("options"), dict) else {}
    option_browser = (
        options.get("browserConfig") if isinstance(options.get("browserConfig"), dict) else {}
    )
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    model_id = str(profile.get("model") or "")
    expected_model_label = {
        "gpt-5.6": "GPT-5.6 Sol",
        "gpt-5.6-sol": "GPT-5.6 Sol",
    }.get(model_id)
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    try:
        chrome_pid = int(runtime.get("chromePid"))
        controller_pid = int(runtime.get("controllerPid"))
        cdp_port = int(runtime.get("chromePort"))
        prompt_length = int(details.get("promptLength"))
        editor_length = int(probe.get("editorLength"))
        meta_cwd = Path(str(meta.get("cwd") or "")).resolve()
        meta_output = Path(str(options.get("writeOutputPath") or "")).resolve()
        state_output = Path(str(artifacts.get("output") or "")).resolve()
        browser_temp = Path(str(artifacts.get("browser_temp") or "")).resolve()
        runtime_profile = Path(str(runtime.get("userDataDir") or "")).resolve()
        state_profile = Path(str(profile.get("copy_profile") or "")).resolve()
        config_profile = Path(str(config.get("copyProfileSource") or "")).resolve()
        option_profile = Path(str(option_browser.get("copyProfileSource") or "")).resolve()
    except (OSError, TypeError, ValueError):
        return None
    expected_false_probe = (
        "userMatched",
        "prefixMatched",
        "lastMatched",
        "hasNewTurn",
        "stopVisible",
        "assistantVisible",
        "composerCleared",
        "inConversation",
    )
    if (
        expected_model_label is None
        or meta.get("id") != locator
        or meta.get("status") != "error"
        or meta.get("model") != model_id
        or meta.get("mode") != "browser"
        or not str(meta.get("completedAt") or "").strip()
        or meta_cwd != Path(str(state.get("project_root") or "")).resolve()
        or meta_output != state_output
        or chrome_pid <= 0
        or controller_pid <= 0
        or cdp_port != identity.get("expected_cdp_port")
        or config.get("debugPort") != cdp_port
        or option_browser.get("debugPort") != cdp_port
        or not str(runtime.get("chromeTargetId") or "").strip()
        or browser_temp != (run_dir / "browser-temp").resolve()
        or not browser_temp.is_dir()
        or browser_temp.is_symlink()
        or not runtime_profile.is_dir()
        or runtime_profile.is_symlink()
        or not is_within(browser_temp, runtime_profile)
        or not str(profile.get("copy_profile") or "").strip()
        or config_profile != state_profile
        or option_profile != state_profile
        or config.get("desiredModel") != expected_model_label
        or config.get("modelStrategy") != profile.get("model_strategy")
        or config.get("thinkingTime") != profile.get("thinking_time")
        or options.get("model") != model_id
        or options.get("slug") != locator
        or option_browser.get("desiredModel") != expected_model_label
        or option_browser.get("modelStrategy") != profile.get("model_strategy")
        or option_browser.get("thinkingTime") != profile.get("thinking_time")
        or runtime.get("promptSubmitted") is not True
        or runtime.get("tabUrl") != "https://chatgpt.com/"
        or runtime.get("conversationId") not in {None, ""}
        or archive not in (None, "")
        or error.get("category") != "browser-automation"
        or error.get("message") != ORACLE_PROMPT_NOT_OBSERVED_MARKER
        or str(meta.get("errorMessage") or "") != ORACLE_PROMPT_NOT_OBSERVED_MARKER
        or details.get("stage") != "submit-prompt"
        or details.get("code") != "prompt-commit-timeout"
        or probe.get("baseline") != 0
        or probe.get("turnsCount") != 0
        or any(probe.get(key) is not False for key in expected_false_probe)
        or prompt_length <= 0
        or editor_length != prompt_length
        or probe.get("lastTurnLength") != 0
    ):
        return None
    return {
        # Retain the ordinary direct-DevSpace settlement fields so the bridge
        # remains a refinement of that exact predicate, not a parallel weaker
        # eligibility class.  Its schema is deliberately not named ``schema``:
        # this evidence is embedded in a user-settlement artifact with its own
        # outer schema.
        **{
            key: value
            for key, value in evidence.items()
            if not key.startswith("_") and key != "schema"
        },
        "evidence_schema": "codex.chatgpt.oracle-bounded-prompt-timeout-harvest/v1",
        "source_thread_id": source_thread_id,
        "run_id": state.get("run_id"),
        "slug": locator,
        "mission_sha256": (state.get("mission") or {}).get("sha256"),
        "ownership_receipt_sha256": owner.get("sha256"),
        "oracle_meta_path": str(meta_path),
        "oracle_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
        "expected_cdp_port": cdp_port,
        "browser_profile": str(runtime_profile),
        "browser_target_id": str(runtime.get("chromeTargetId")),
        "transport": str(state.get("transport") or ""),
        "profile": {
            key: profile.get(key)
            for key in ("model", "model_strategy", "thinking_time", "research")
        },
        "profile_sha256": hashlib.sha256(
            json.dumps(
                {
                    key: profile.get(key)
                    for key in ("model", "model_strategy", "thinking_time", "research")
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "browser_config": {
            "desired_model": expected_model_label,
            "model_strategy": profile.get("model_strategy"),
            "thinking_time": profile.get("thinking_time"),
        },
        "browser_config_sha256": hashlib.sha256(
            json.dumps(
                {
                    "desired_model": expected_model_label,
                    "model_strategy": profile.get("model_strategy"),
                    "thinking_time": profile.get("thinking_time"),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "recovery_evidence": evidence.get("recovery_evidence"),
        "prompt_submitted_claim": True,
        "commit_probe_turns": 0,
        "conversation_url_absent": True,
        "output_absent": True,
    }


def bounded_task_owned_prompt_timeout_harvest_evidence(
    state_path: Path,
) -> dict[str, Any] | None:
    """Authorize one prompt-free harvest when browser binding never completed.

    A task-bound run normally needs the immutable browser identity receipt before
    any recovery. Oracle can fail while committing the prompt, however, before
    a conversation URL exists and therefore before that receipt can be sealed.
    This predicate never persists, settles, or permits live recovery.
    """
    return _bounded_task_owned_prompt_timeout_evidence(
        state_path,
        allow_recovery_evidence=False,
    )


def followup_archived_parent_settle_without_harvest_evidence(
    state_path: Path,
) -> dict[str, Any] | None:
    """Identify an exact before-composer failure that must go straight to settle.

    An archived-parent restore failure already has task/run/mission/archive and
    no-click evidence.  Reopening the parent conversation cannot prove anything
    about the child, so recovery must not be offered as a prerequisite.  The
    normal explicit user-confirmed settlement gate remains mandatory.
    """
    evidence = _followup_no_submission_evidence(
        state_path,
        require_recovery_evidence=False,
    )
    if (
        evidence is None
        or evidence.get("failure_kind") != "archived-parent-unarchive-menu-absent"
    ):
        return None
    return evidence


def _strict_json_object(path: Path) -> tuple[dict[str, Any], bytes] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = path.read_bytes()
        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate JSON key")
                value[key] = item
            return value
        parsed = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return (parsed, raw) if isinstance(parsed, dict) else None


def _legacy_followup_reservation_for_child(state_path: Path) -> dict[str, Any] | None:
    """Find one exact v1.18.1-v1.18.3 reservation by immutable child identity.

    This compatibility path never chooses a latest project run. It accepts only
    one unique same-task reservation whose child tuple exactly names this run.
    """
    state = load_state(state_path)
    run_dir = state_path.parent.resolve()
    run_root = run_dir.parent
    source_thread_id = source_thread_id_from_state(state)
    child_slug = str((state.get("oracle") or {}).get("slug") or "")
    child_mission = str((state.get("mission") or {}).get("sha256") or "")
    expected_port = (state.get("browser_identity") or {}).get("expected_cdp_port")
    candidates: list[dict[str, Any]] = []
    try:
        parent_dirs = [path for path in run_root.iterdir() if path.is_dir() and not path.is_symlink()]
    except OSError:
        return None
    for parent_dir in parent_dirs:
        rounds = parent_dir / "followup-rounds"
        if not rounds.is_dir() or rounds.is_symlink():
            continue
        for reservation_path in rounds.glob("*.json"):
            if reservation_path.name.endswith(".result.json"):
                continue
            loaded = _strict_json_object(reservation_path)
            if loaded is None:
                continue
            reservation, raw = loaded
            child = reservation.get("child") if isinstance(reservation.get("child"), dict) else {}
            parent = reservation.get("parent") if isinstance(reservation.get("parent"), dict) else {}
            if (
                reservation.get("schema") != "codex.chatgpt.oracle-followup-round/v1"
                or reservation.get("source_thread_id") != source_thread_id
                or child.get("run_id") != state.get("run_id")
                or child.get("slug") != child_slug
                or child.get("mission_sha256") != child_mission
                or child.get("expected_cdp_port") != expected_port
                or Path(str(child.get("run_dir") or "")).resolve() != run_dir
                or parent.get("run_id") != parent_dir.name
            ):
                continue
            validated = _validate_followup_reservation_for_child(state_path, reservation_path)
            if validated is None:
                continue
            candidates.append({
                **validated,
                "binding_mode": "legacy-unique-child-tuple/v1",
            })
    return candidates[0] if len(candidates) == 1 else None


def _proven_legacy_v1184_followup_install_receipt(
    installed_before_at: str,
    *,
    receipt_root: Path | None = None,
) -> dict[str, str] | None:
    try:
        cutoff = datetime.fromisoformat(installed_before_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if cutoff.tzinfo is None:
        return None
    cutoff = cutoff.astimezone(timezone.utc)
    root = (receipt_root or (Path.home() / ".codex" / "receipts")).resolve()
    if not root.is_dir() or root.is_symlink():
        return None
    candidates: list[tuple[datetime, Path, dict[str, Any], bytes]] = []
    for path in root.glob("codexpro-automation-*.json"):
        if not path.is_file() or path.is_symlink():
            continue
        loaded = _strict_json_object(path)
        if loaded is None:
            continue
        receipt, raw = loaded
        if receipt.get("schema") != "codexpro.install-receipt/v3":
            continue
        try:
            installed = datetime.fromisoformat(
                str(receipt.get("installed_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if installed.tzinfo is None:
            continue
        installed = installed.astimezone(timezone.utc)
        if installed <= cutoff:
            candidates.append((installed, path.resolve(), receipt, raw))
    if not candidates:
        return None
    installed, path, receipt, raw = max(candidates, key=lambda item: item[0])
    if receipt.get("manifest_version") != "1.18.4":
        return None
    files = receipt.get("files")
    if not isinstance(files, list):
        return None
    installed_hashes = {
        str(item.get("path") or ""): str(item.get("installed_sha256") or "").casefold()
        for item in files
        if isinstance(item, dict)
    }
    if any(installed_hashes.get(name) != expected for name, expected in LEGACY_V1184_FOLLOWUP_MANAGED_HASHES.items()):
        return None
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "installed_at": installed.isoformat(),
        "manifest_version": "1.18.4",
    }


def _followup_no_submission_evidence(
    state_path: Path,
    *,
    require_recovery_evidence: bool,
) -> dict[str, Any] | None:
    state = load_state(state_path)
    run_dir = state_path.parent.resolve()
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    persisted_binding = state.get("followup_binding")
    binding = proven_followup_binding(state_path)
    if binding is None and persisted_binding is None:
        binding = _legacy_followup_reservation_for_child(state_path)
    owner = proven_ownership_receipt(state_path)
    browser_identity = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    browser_receipt = browser_identity_receipt_path(run_dir)
    if (
        state.get("status") not in {"attention_required", "failed"}
        or state.get("session_authority") not in {"submitted_unknown", "pre_submit"}
        or state.get("transport") != "pro-devspace-readonly"
        or state.get("mode") != "browser"
        or state.get("transport_status") not in {"failed", "not_submitted_user_confirmed"}
        or profile.get("model") != "gpt-5.6-sol"
        or profile.get("model_strategy") != "select"
        or not is_compatible_pro_thinking_time(profile.get("thinking_time"))
        or state.get("task_outcome") != "pending"
        or state.get("terminal_harvested") is True
        or state.get("parallel_parent_id") is not None
        or state.get("web_multi_child_provenance") is not None
        or state.get("attachments") not in (None, [])
        or state.get("run_id") != run_dir.name
        or state.get("exit_code") == 0
        or binding is None
        or owner is None
        or browser_identity.get("receipt_path") not in {None, ""}
        or browser_identity.get("receipt_sha256") not in {None, ""}
        or browser_receipt.exists()
        or browser_receipt.is_symlink()
        or proven_browser_identity_receipt(state_path) is not None
        or _state_has_conversation_url(state)
    ):
        return None
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    records = {name: _artifact_bytes(state, name) for name in ("stdout", "stderr", "transcript")}
    if (
        output.resolve() != (run_dir / "output.md").resolve()
        or output.is_symlink()
        or output_is_nonempty(output)
        or any(value is None for value in records.values())
    ):
        return None
    stdout_path, stdout_bytes = records["stdout"]  # type: ignore[misc]
    stderr_path, stderr_bytes = records["stderr"]  # type: ignore[misc]
    transcript_path, transcript_bytes = records["transcript"]  # type: ignore[misc]
    try:
        ordered_lines = [line.strip() for line in stdout_bytes.decode("utf-8", errors="strict").splitlines() if line.strip()]
        lines = set(ordered_lines)
    except UnicodeDecodeError:
        return None
    locator = str((state.get("oracle") or {}).get("slug") or "")
    session_root = Path(os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")).resolve()
    meta_path = session_root / locator / "meta.json"
    loaded_meta = _strict_json_object(meta_path)
    if loaded_meta is None:
        return None
    meta, meta_raw = loaded_meta
    browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
    runtime = browser.get("runtime")
    error = meta.get("error") if isinstance(meta.get("error"), dict) else {}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    config = browser.get("config") if isinstance(browser.get("config"), dict) else {}
    options = meta.get("options") if isinstance(meta.get("options"), dict) else {}
    option_browser = options.get("browserConfig") if isinstance(options.get("browserConfig"), dict) else {}
    meta_urls = CHATGPT_CONVERSATION_URL_RE.findall(meta_raw.decode("utf-8", errors="strict"))
    structured_pre_composer = (
        details.get("stage") in ORACLE_FOLLOWUP_PRE_COMPOSER_STAGES
        and set(details) == {"stage", "priorTurns", "settled"}
        and isinstance(details.get("priorTurns"), int)
        and not isinstance(details.get("priorTurns"), bool)
        and details.get("priorTurns") > 0
        and details.get("settled") is False
        and runtime in (None, {})
        and bool(str(error.get("message") or "").strip())
    )
    failure_signatures = {
        "textarea-absent": {
            f"ERROR: {ORACLE_PROMPT_TEXTAREA_ABSENT_MARKER}",
            f"User error (browser-automation): {ORACLE_PROMPT_TEXTAREA_ABSENT_MARKER}",
        },
        "archived-parent-unarchive-menu-absent": {
            f"ERROR: {ORACLE_FOLLOWUP_UNARCHIVE_MENU_ABSENT_MARKER}",
            f"User error (browser-automation): {ORACLE_FOLLOWUP_UNARCHIVE_MENU_ABSENT_MARKER}",
        },
    }
    if structured_pre_composer:
        structured_message = str(error["message"])
        failure_signatures["structured-pre-composer"] = {
            f"ERROR: {structured_message}",
            f"User error (browser-automation): {structured_message}",
        }
    matched_failures = [
        (kind, expected)
        for kind, expected in failure_signatures.items()
        if expected.issubset(lines)
    ]
    if len(matched_failures) != 1:
        return None
    failure_kind, expected_lines = matched_failures[0]
    if (
        stdout_path.resolve() != (run_dir / "stdout.log").resolve()
        or stderr_path.resolve() != (run_dir / "stderr.log").resolve()
        or transcript_path.resolve() != (run_dir / "transcript.md").resolve()
        or any(path.is_symlink() for path in (stdout_path, stderr_path, transcript_path))
        or
        not expected_lines.issubset(lines)
        or any(
            line not in expected_lines
            and not line.startswith((
                "🧿 oracle ", "Session: ", "Mode: ", "Models: ", "Detach: ",
                "Reattach: oracle session ", "Launching browser mode ",
                "This run can take up to an hour ", "[browser] Browser control: ",
                "[browser] Browser guidance: ",
            ))
            for line in ordered_lines
        )
        or stderr_bytes
        or transcript_bytes != stdout_bytes
        or any(CHATGPT_CONVERSATION_URL_RE.search(raw.decode("utf-8", errors="ignore")) for raw in (stdout_bytes, stderr_bytes, transcript_bytes))
    ):
        return None
    recovery_records: list[dict[str, Any]] = []
    recovery_paths = (run_dir / "recovery-harvest-stdout.log", run_dir / "recovery-harvest-stderr.log")
    recovery_candidate = run_dir / "recovery-harvest-candidate.md"
    allowed_recovery_names = {
        recovery_paths[0].name,
        recovery_paths[1].name,
        recovery_candidate.name,
    }
    try:
        recovery_artifacts = [
            path
            for path in run_dir.glob("recovery-*")
            if path.exists() or path.is_symlink()
        ]
    except OSError:
        return None
    if any(path.name not in allowed_recovery_names for path in recovery_artifacts):
        return None
    if recovery_candidate.exists() or recovery_candidate.is_symlink():
        try:
            candidate_bytes = recovery_candidate.read_bytes()
        except OSError:
            return None
        if recovery_candidate.is_symlink() or not recovery_candidate.is_file() or candidate_bytes:
            return None
    recovery_presence = [path.exists() or path.is_symlink() for path in recovery_paths]
    if any(recovery_presence):
        if not all(
            path.is_file() and not path.is_symlink()
            for path in recovery_paths
        ):
            return None
        try:
            raw_out, raw_err = (path.read_bytes() for path in recovery_paths)
            stdout_text = raw_out.decode("utf-8", errors="strict")
            stderr_text = raw_err.decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            return None
        text = "\n".join((stdout_text, stderr_text))
        if (
            CHATGPT_CONVERSATION_URL_RE.search(text)
            or ORACLE_RECOVERY_STATE_RE.search(text)
            or ORACLE_NO_LIVE_TAB_MARKER not in text
            or ORACLE_NO_LIVE_TAB_MARKER not in stdout_text
            or f'"{locator}"' not in text
            or f'"{locator}"' not in stdout_text
            or ORACLE_NO_RECOVERABLE_URL_MARKER not in text
            or ORACLE_NO_RECOVERABLE_URL_MARKER not in stderr_text
        ):
            return None
        recovery_records.append({
            "stdout_name": recovery_paths[0].name,
            "stdout_sha256": hashlib.sha256(raw_out).hexdigest(),
            "stderr_name": recovery_paths[1].name,
            "stderr_sha256": hashlib.sha256(raw_err).hexdigest(),
        })
    # A missing textarea has no known conversation and therefore still needs
    # the exact no-tab/no-URL harvest receipt.  An archived-parent menu miss is
    # different: its bounded before-composer taxonomy is authoritative and a
    # new recovery attempt is not applicable.  Preserve settlement eligibility
    # only when an older runner already wrote one exact harmless no-tab/no-URL
    # pair; any candidate, state marker, URL, extra attempt, or partial pair was
    # rejected above.
    if require_recovery_evidence and failure_kind == "textarea-absent" and len(recovery_records) != 1:
        return None
    reservation_record = binding.get("reservation") if isinstance(binding.get("reservation"), dict) else binding
    reservation = reservation_record["payload"]
    parent_url = str((reservation.get("parent") or {}).get("conversation_url") or "")
    common_invalid = (
        meta.get("id") != locator
        or meta.get("status") != "error"
        or not str(meta.get("completedAt") or "").strip()
        or meta.get("mode") != "browser"
        or str(meta.get("model") or "") != "gpt-5.6-sol"
        or error.get("category") != "browser-automation"
        or browser.get("archive") not in (None, "")
        or config.get("resumeConversationUrl") != parent_url
        or option_browser.get("resumeConversationUrl") != parent_url
        or any(url != parent_url for url in meta_urls)
    )
    if common_invalid:
        return None
    if failure_kind == "textarea-absent":
        if (
            error.get("message") != ORACLE_PROMPT_TEXTAREA_ABSENT_MARKER
            or details.get("stage") != "execute-browser"
            or runtime not in (None, {})
        ):
            return None
        evidence_profile = "textarea-absent-with-exact-harvest/v1"
    elif failure_kind == "structured-pre-composer":
        observer = state.get("browser_observer") if isinstance(state.get("browser_observer"), dict) else {}
        observer_pid = observer.get("oracle_process_pid")
        if (
            not structured_pre_composer
            or state.get("task_outcome_reason") not in {
                "followup-conversation-identity-unverified",
                "user-confirmed-no-submission-after-prompt-timeout",
            }
            or observer.get("status") != "process-exited"
            or not isinstance(observer_pid, int)
            or isinstance(observer_pid, bool)
            or observer_pid <= 0
            or exact_run_process_may_be_alive(state_path.parent, state, observer_pid)
            or error.get("category") != "browser-automation"
        ):
            return None
        evidence_profile = "structured-pre-composer-runtime-unbound/v1"
    else:
        parent_archive = (reservation.get("parent") or {}).get("archive_contract")
        structured_before_composer = (
            details.get("stage") == "followup-unarchive-before-composer"
            and details.get("code") == "FOLLOWUP_ARCHIVED_PARENT_UNARCHIVE_FAILED"
            and details.get("reason") == "unarchive-menu-not-found"
            and details.get("expectedConversationUrl") == parent_url
            and details.get("observedConversationUrl") == parent_url
            and details.get("promptSubmitted") is False
            and details.get("composerSubmitAttempted") is False
            and isinstance(details.get("turnCountBefore"), int)
            and details.get("turnCountAfter") == details.get("turnCountBefore")
            and runtime in (None, {})
        )
        # v1.18.4 emitted this exact error only from the no-candidate branch
        # before `ensurePromptReady` and before any unarchive click.  Retain one
        # deliberately narrow compatibility predicate so the already-created
        # exact child can be settled; all future runs use the structured proof.
        owner_payload = owner.get("payload") if isinstance(owner.get("payload"), dict) else {}
        ownership_created_at = str(owner_payload.get("created_at") or "")
        try:
            ownership_created = datetime.fromisoformat(ownership_created_at.replace("Z", "+00:00"))
            meta_completed = datetime.fromisoformat(str(meta.get("completedAt") or "").replace("Z", "+00:00"))
        except ValueError:
            ownership_created = None
            meta_completed = None
        ownership_time_valid = (
            ownership_created is not None
            and meta_completed is not None
            and ownership_created.tzinfo is not None
            and meta_completed.tzinfo is not None
            and ownership_created.astimezone(timezone.utc) <= meta_completed.astimezone(timezone.utc)
        )
        legacy_provenance = (
            _proven_legacy_v1184_followup_install_receipt(ownership_created_at)
            if ownership_time_valid
            else None
        )
        legacy_v1184_no_click = (
            details.get("stage") == "execute-browser"
            and set(details) == {"stage"}
            and runtime in (None, {})
            and isinstance(persisted_binding, dict)
            and legacy_provenance is not None
        )
        if (
            error.get("message") != ORACLE_FOLLOWUP_UNARCHIVE_MENU_ABSENT_MARKER
            or config.get("resumeArchivedConversation") is not True
            or option_browser.get("resumeArchivedConversation") is not True
            or config.get("archiveConversations") != "always"
            or option_browser.get("archiveConversations") != "always"
            or not isinstance(parent_archive, dict)
            or parent_archive.get("was_archived") is not True
            or parent_archive.get("conversation_url") != parent_url
            or parent_archive.get("restore_policy") != "exact-unarchive-then-rearchive"
            or not (structured_before_composer or legacy_v1184_no_click)
        ):
            return None
        evidence_profile = (
            "archived-parent-unarchive-before-composer/v2"
            if structured_before_composer
            else "archived-parent-unarchive-v1.18.4-legacy-no-click/v1"
        )
    evidence = {
        "settlement_eligibility": "oracle-followup-pre-submit-ui/v2",
        "failure_kind": failure_kind,
        "evidence_profile": evidence_profile,
        "project_root": str(state.get("project_root") or ""),
        "run_id": state.get("run_id"),
        "transport": state.get("transport"),
        "mission_sha256": (state.get("mission") or {}).get("sha256"),
        "oracle_locator": (state.get("oracle") or {}).get("slug"),
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "harvest_outcome": (
            "no-live-no-url-no-candidate" if recovery_records else "not-run"
        ),
        "recovery_evidence": recovery_records,
        "output_absent": True,
        "conversation_url_absent": True,
        "followup_binding_mode": binding.get("binding_mode", "child-binding-receipt/v1"),
        "followup_reservation_path": reservation_record.get("path"),
        "followup_reservation_sha256": reservation_record.get("sha256"),
        "parent_run_id": (reservation.get("parent") or {}).get("run_id"),
        "parent_conversation_url": parent_url,
        "round_key": reservation.get("round_key"),
        "oracle_meta_sha256": hashlib.sha256(meta_raw).hexdigest(),
    }
    if failure_kind == "structured-pre-composer":
        evidence["pre_composer_stage"] = details["stage"]
        evidence["prior_turns_observed"] = details["priorTurns"]
    if failure_kind == "archived-parent-unarchive-menu-absent" and legacy_v1184_no_click:
        evidence["legacy_install_receipt_path"] = legacy_provenance["path"]
        evidence["legacy_install_receipt_sha256"] = legacy_provenance["sha256"]
    return evidence


def _direct_devspace_no_submission_evidence(
    state_path: Path,
    *,
    require_persisted_recovery: bool,
    require_recovery_evidence: bool = True,
) -> dict[str, Any] | None:
    """Validate one ordinary DevSpace prompt-observation failure.

    This intentionally remains only *eligibility* evidence.  Oracle's local
    observer cannot prove that a browser send never happened; a user must
    still explicitly attest no submission before the project lock is released.
    The recovery receipt prevents that attestation from being based on mutable
    ad-hoc recovery logs.
    """
    state = load_state(state_path)
    run_dir = state_path.parent
    run_id = str(state.get("run_id") or "")
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    if (
        state.get("schema") != "codex.chatgpt.oracle-run-state/v1"
        or str(state.get("session_authority") or "") not in {"submitted_unknown", "pre_submit"}
        or state.get("status") != "attention_required"
        or str(state.get("transport") or "") != "devspace"
        or str(state.get("mode") or "") != "browser"
        or state.get("parallel_parent_id") is not None
        or state.get("requested_run_id") not in (None, run_id)
        or state.get("web_multi_child_provenance") is not None
        or state.get("attachments") not in (None, [])
        or state.get("transport_status") not in {"failed", "not_submitted_user_confirmed"}
        or state.get("task_outcome") != "pending"
        or state.get("terminal_harvested") is True
        or _state_has_conversation_url(state)
        or int(state.get("exit_code") or 0) == 0
        or run_dir.name != run_id
    ):
        return None
    if not all(isinstance(profile.get(key), str) for key in ("model", "model_strategy", "thinking_time", "research")):
        return None
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    if output.resolve() != (run_dir / "output.md").resolve() or output.is_symlink() or output_is_nonempty(output):
        return None
    records = {name: _artifact_bytes(state, name) for name in ("stdout", "stderr", "transcript")}
    if any(record is None for record in records.values()):
        return None
    stdout_path, stdout_bytes = records["stdout"]  # type: ignore[misc]
    stderr_path, stderr_bytes = records["stderr"]  # type: ignore[misc]
    transcript_path, transcript_bytes = records["transcript"]  # type: ignore[misc]
    if (
        stdout_path.resolve() != (run_dir / "stdout.log").resolve()
        or stderr_path.resolve() != (run_dir / "stderr.log").resolve()
        or transcript_path.resolve() != (run_dir / "transcript.md").resolve()
        or any(path.is_symlink() for path in (stdout_path, stderr_path, transcript_path))
        or stderr_bytes
        or transcript_bytes != stdout_bytes
    ):
        return None
    try:
        stdout_text = stdout_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    marker_lines = {
        line.strip() for line in stdout_text.splitlines()
        if ORACLE_PROMPT_NOT_OBSERVED_MARKER in line
    }
    allowed_marker_lines = {
        f"ERROR: {ORACLE_PROMPT_NOT_OBSERVED_MARKER}",
        f"User error (browser-automation): {ORACLE_PROMPT_NOT_OBSERVED_MARKER}",
    }
    prompt_marker_valid = (
        bool(marker_lines)
        and marker_lines.issubset(allowed_marker_lines)
        and stdout_text.count(ORACLE_PROMPT_NOT_OBSERVED_MARKER) == len(marker_lines)
    )
    lines = stdout_text.splitlines()
    model_option_error = ""
    model_option_match: re.Match[str] | None = None
    if len(lines) >= 2 and lines[-1].startswith("User error (browser-automation): "):
        model_option_error = lines[-1].removeprefix("User error (browser-automation): ")
        if lines[-2] == f"ERROR: {model_option_error}":
            model_option_match = ORACLE_MODEL_OPTION_MISSING_PRE_SUBMIT_RE.fullmatch(
                model_option_error
            )
    model_option_valid = model_option_match is not None
    if (
        not locator
        or prompt_marker_valid == model_option_valid
        or f"Session: {locator}" not in stdout_text
        or CHATGPT_CONVERSATION_URL_RE.search(stdout_text)
    ):
        return None
    desired_model = ""
    if model_option_match is not None:
        desired_model = model_option_match.group("desired")
        expected_head = [
            f"Session: {locator}",
            "Mode: browser foreground",
            "Models: 1",
            "Detach: no",
            f"Reattach: oracle session {locator}",
        ]
        expected_guidance = [
            "This run can take up to an hour (usually ~10 minutes).",
            "[browser] Browser control: launch Chrome in hidden-window mode; may focus/control the browser UI.",
            "[browser] Browser guidance: On macOS, Oracle launches Chrome off-screen while keeping the page rendered.",
            "[browser] Browser guidance: For the calmest shared-desktop flow, prefer --browser-attach-running or --remote-chrome.",
        ]
        if (
            len(lines) != 13
            or re.fullmatch(r".{1,4} oracle 0\.17\.1 .{2,120}", lines[0]) is None
            or lines[1:6] != expected_head
            or re.fullmatch(
                rf"Launching browser mode \(target={re.escape(desired_model)}; "
                rf"requested={re.escape(str(profile['model']))}\) with ~[1-9][0-9]* tokens\.",
                lines[6],
            )
            is None
            or lines[7:11] != expected_guidance
            or lines[-2:] != [
                f"ERROR: {model_option_error}",
                f"User error (browser-automation): {model_option_error}",
            ]
        ):
            return None
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    source_path = Path(str(mission.get("path") or ""))
    transport_path = Path(str(mission.get("transport_path") or ""))
    try:
        project_root = Path(str(state.get("project_root") or "")).resolve(strict=True)
        source_path = source_path.resolve(strict=True)
        transport_path = transport_path.resolve(strict=True)
        if (
            not project_root.is_dir()
            or not source_path.is_file()
            or source_path.is_symlink()
            or transport_path.is_symlink()
            or not is_within(project_root, source_path)
            or transport_path != (run_dir / "mission.md").resolve()
        ):
            return None
        source_bytes = source_path.read_bytes()
        transport_bytes = transport_path.read_bytes()
    except OSError:
        return None
    mission_sha256 = str(mission.get("sha256") or "")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    transport_sha256 = hashlib.sha256(transport_bytes).hexdigest()
    if (
        not re.fullmatch(r"[a-f0-9]{64}", mission_sha256)
        or source_sha256 != mission_sha256
        or transport_sha256 != mission_sha256
        or source_bytes != transport_bytes
    ):
        return None
    expected_recovery = {
        "stdout_name": "recovery-harvest-stdout.log",
        "stderr_name": "recovery-harvest-stderr.log",
    }
    recovery_paths = [
        run_dir / expected_recovery["stdout_name"],
        run_dir / expected_recovery["stderr_name"],
    ]
    recovery_records: list[dict[str, str]] = []
    recovery_exists = [path.exists() or path.is_symlink() for path in recovery_paths]
    if any(recovery_exists):
        if not all(recovery_exists):
            return None
        try:
            if any(path.is_symlink() for path in recovery_paths):
                return None
            recovery_stdout, recovery_stderr = (path.read_bytes() for path in recovery_paths)
            recovery_text = b"\n".join((recovery_stdout, recovery_stderr)).decode(
                "utf-8", errors="strict"
            )
        except (OSError, UnicodeDecodeError):
            return None
        if (
            CHATGPT_CONVERSATION_URL_RE.search(recovery_text)
            or ORACLE_RECOVERY_STATE_RE.search(recovery_text)
            or ORACLE_NO_LIVE_TAB_MARKER not in recovery_text
            or f'"{locator}"' not in recovery_text
            or ORACLE_NO_RECOVERABLE_URL_MARKER not in recovery_text
        ):
            return None
        recovery_records = [{
            **expected_recovery,
            "stdout_sha256": hashlib.sha256(recovery_stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(recovery_stderr).hexdigest(),
        }]
    elif require_recovery_evidence:
        return None
    selector_meta_evidence: dict[str, Any] = {}
    if model_option_match is not None:
        session_root = Path(
            os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
        ).resolve()
        if re.fullmatch(r"oracle-[A-Za-z0-9._-]{1,200}", locator) is None:
            return None
        meta_path = session_root / locator / "meta.json"
        if meta_path.parent.is_symlink() or meta_path.is_symlink():
            return None

        def reject_meta_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            parsed: dict[str, Any] = {}
            for key, value in pairs:
                if key in parsed:
                    raise ValueError(f"duplicate Oracle meta key: {key}")
                parsed[key] = value
            return parsed

        try:
            meta_bytes = meta_path.read_bytes()
            if meta_path.resolve(strict=True).parent.parent != session_root:
                return None
            meta_text = meta_bytes.decode("utf-8", errors="strict")
            meta = json.loads(meta_text, object_pairs_hook=reject_meta_duplicates)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
        config = browser.get("config") if isinstance(browser.get("config"), dict) else {}
        runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
        archive = browser.get("archive")
        error = meta.get("error") if isinstance(meta.get("error"), dict) else {}
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        details_runtime = (
            details.get("runtime") if isinstance(details.get("runtime"), dict) else {}
        )
        options = meta.get("options") if isinstance(meta.get("options"), dict) else {}
        option_browser = (
            options.get("browserConfig")
            if isinstance(options.get("browserConfig"), dict)
            else {}
        )
        identity = (
            state.get("browser_identity")
            if isinstance(state.get("browser_identity"), dict)
            else {}
        )
        ownership = proven_ownership_receipt(state_path)
        source_thread_id = source_thread_id_from_state(state)
        receipt_path = browser_identity_receipt_path(run_dir)
        try:
            copy_profile = Path(str(profile.get("copy_profile") or "")).resolve()
            config_profile = Path(str(config.get("copyProfileSource") or "")).resolve()
            option_profile = Path(str(option_browser.get("copyProfileSource") or "")).resolve()
            meta_cwd = Path(str(meta.get("cwd") or "")).resolve()
            meta_output = Path(str(options.get("writeOutputPath") or "")).resolve()
            chrome_pid = int(runtime.get("chromePid"))
            controller_pid = int(runtime.get("controllerPid"))
            cdp_port = int(runtime.get("chromePort"))
            state_browser_temp = Path(str(artifacts.get("browser_temp") or "")).resolve()
            runtime_profile = Path(str(runtime.get("userDataDir") or "")).resolve()
        except (OSError, TypeError, ValueError):
            return None
        model_id = str(profile.get("model") or "")
        if (
            ownership is None
            or not source_thread_id
            or identity.get("receipt_path") not in {None, ""}
            or identity.get("receipt_sha256") not in {None, ""}
            or receipt_path.exists()
            or receipt_path.is_symlink()
            or not str(profile.get("copy_profile") or "").strip()
            or not copy_profile.is_absolute()
            or meta.get("id") != locator
            or meta.get("status") != "error"
            or meta.get("model") != model_id
            or meta.get("mode") != "browser"
            or not str(meta.get("completedAt") or "").strip()
            or meta_cwd != project_root
            or meta_output != output.resolve()
            or chrome_pid <= 0
            or controller_pid <= 0
            or cdp_port != identity.get("expected_cdp_port")
            or config.get("debugPort") != cdp_port
            or option_browser.get("debugPort") != cdp_port
            or not str(runtime.get("chromeTargetId") or "").strip()
            or runtime.get("conversationId") not in {None, ""}
            or archive not in {None, ""}
            or state_browser_temp != (run_dir / "browser-temp").resolve()
            or not state_browser_temp.is_dir()
            or state_browser_temp.is_symlink()
            or not runtime_profile.is_dir()
            or runtime_profile.is_symlink()
            or not is_within(state_browser_temp, runtime_profile)
            or config_profile != copy_profile
            or option_profile != copy_profile
            or config.get("desiredModel") != desired_model
            or option_browser.get("desiredModel") != desired_model
            or config.get("modelStrategy") != profile.get("model_strategy")
            or option_browser.get("modelStrategy") != profile.get("model_strategy")
            or config.get("thinkingTime") != profile.get("thinking_time")
            or option_browser.get("thinkingTime") != profile.get("thinking_time")
            or config.get("researchMode") != profile.get("research")
            or option_browser.get("researchMode") != profile.get("research")
            or options.get("model") != model_id
            or options.get("models") != [model_id]
            or options.get("effectiveModelId") != model_id
            or options.get("slug") != locator
            or options.get("mode") != "browser"
            or runtime.get("promptSubmitted") is not False
            or runtime.get("tabUrl") != "https://chatgpt.com/"
            or details_runtime.get("promptSubmitted") not in {None, False}
            or error.get("category") != "browser-automation"
            or error.get("message") != model_option_error
            or details.get("stage") != "execute-browser"
            or str(meta.get("errorMessage") or "") != model_option_error
            or CHATGPT_CONVERSATION_URL_RE.search(meta_text)
        ):
            return None
        selector_meta_evidence = {
            "pre_submit_marker": "oracle-model-option-missing/v1",
            "source_thread_id": source_thread_id,
            "ownership_receipt_sha256": ownership.get("sha256"),
            "desired_model": desired_model,
            "oracle_meta_path": str(meta_path),
            "oracle_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
            "oracle_meta_stage": "execute-browser",
            "prompt_submitted": False,
            "tab_url": "https://chatgpt.com/",
            "expected_cdp_port": cdp_port,
            "browser_profile": str(runtime_profile),
            "browser_target_id": str(runtime.get("chromeTargetId")),
        }
    evidence = {
        "settlement_eligibility": "oracle-direct-devspace/v1",
        "project_root": str(project_root),
        "run_id": run_id,
        "transport": "devspace",
        "profile": {key: profile[key] for key in ("model", "model_strategy", "thinking_time", "research")},
        "source_mission_path": str(source_path),
        "source_mission_sha256": source_sha256,
        "transport_mission_path": str(transport_path),
        "transport_mission_sha256": transport_sha256,
        "mission_sha256": mission_sha256,
        "oracle_locator": locator,
        "oracle_version": str(oracle.get("resolved_version") or "").removeprefix("oracle ").strip(),
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "recovery_evidence": recovery_records,
        "output_absent": True,
        "conversation_url_absent": True,
        **selector_meta_evidence,
        "_source_mission_path": str(source_path),
        "_transport_mission_path": str(transport_path),
    }
    if not require_persisted_recovery:
        return evidence
    if not recovery_records:
        return None
    reference = state.get("prompt_not_observed_recovery")
    expected_path = run_dir / "prompt-not-observed-recovery.json"
    if (
        not isinstance(reference, dict)
        or reference.get("schema") != "codex.chatgpt.oracle-prompt-not-observed-recovery-reference/v1"
        or Path(str(reference.get("path") or "")).resolve() != expected_path.resolve()
        or expected_path.is_symlink()
    ):
        return None
    try:
        raw = expected_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != str(reference.get("sha256") or ""):
            return None
        recorded = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    expected_recorded = {
        "schema": "codex.chatgpt.oracle-prompt-not-observed-recovery/v1",
        "code": "ORACLE_PROMPT_NOT_OBSERVED_RECOVERY_BINDING_UNAVAILABLE",
        **{key: value for key, value in evidence.items() if not key.startswith("_")},
    }
    if recorded != expected_recorded:
        return None
    return evidence


def persist_direct_devspace_prompt_not_observed_recovery(state_path: Path) -> dict[str, Any] | None:
    """Persist a hash-bound exact recovery receipt without releasing ownership."""
    bounded = persist_bounded_task_owned_prompt_timeout_harvest(state_path)
    if bounded is not None:
        return bounded
    # Once the exact zero-turn bridge has been recorded, settlement must
    # continue to prove that stronger receipt.  In particular, do not let a
    # subsequently malformed/tampered bridge silently fall back to the older
    # generic direct-DevSpace recovery proof.
    payload = load_state(state_path)
    if payload.get("bounded_prompt_timeout_harvest") is not None:
        return None
    evidence = _direct_devspace_no_submission_evidence(
        state_path, require_persisted_recovery=False
    )
    if evidence is None:
        return None
    if _direct_devspace_no_submission_evidence(state_path, require_persisted_recovery=True) is not None:
        return evidence
    if payload.get("prompt_not_observed_recovery") is not None:
        return None
    receipt_path = state_path.parent / "prompt-not-observed-recovery.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        return None
    recorded = {
        "schema": "codex.chatgpt.oracle-prompt-not-observed-recovery/v1",
        "code": "ORACLE_PROMPT_NOT_OBSERVED_RECOVERY_BINDING_UNAVAILABLE",
        **{key: value for key, value in evidence.items() if not key.startswith("_")},
    }
    write_json_atomic(receipt_path, recorded)
    payload["prompt_not_observed_recovery"] = {
        "schema": "codex.chatgpt.oracle-prompt-not-observed-recovery-reference/v1",
        "path": str(receipt_path),
        "sha256": sha256_file(receipt_path),
    }
    write_json_atomic(state_path, payload)
    return _direct_devspace_no_submission_evidence(
        state_path, require_persisted_recovery=True
    )


def proven_bounded_task_owned_prompt_timeout_harvest(
    state_path: Path,
) -> dict[str, Any] | None:
    """Revalidate the append-only zero-turn proof recorded by exact harvest."""
    evidence = _bounded_task_owned_prompt_timeout_evidence(
        state_path,
        allow_recovery_evidence=True,
    )
    if evidence is None or evidence.get("transport") != "devspace":
        return None
    state = load_state(state_path)
    run_dir = state_path.parent
    reference = state.get("bounded_prompt_timeout_harvest")
    receipt_path = run_dir / "bounded-prompt-timeout-harvest.json"
    if (
        not isinstance(reference, dict)
        or reference.get("schema")
        != "codex.chatgpt.oracle-bounded-prompt-timeout-harvest-reference/v1"
        or Path(str(reference.get("path") or "")).resolve() != receipt_path.resolve()
        or receipt_path.is_symlink()
    ):
        return None
    try:
        raw = receipt_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != str(reference.get("sha256") or ""):
            return None
        recorded = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    expected = {
        "schema": "codex.chatgpt.oracle-bounded-prompt-timeout-harvest/v1",
        "code": "ORACLE_ZERO_TURN_PROMPT_TIMEOUT_HARVEST",
        **{key: value for key, value in evidence.items() if not key.startswith("_")},
    }
    if recorded != expected:
        return None
    return {
        **evidence,
        "bounded_prompt_timeout_harvest": {
            "schema": reference["schema"],
            "path": str(receipt_path),
            "sha256": str(reference["sha256"]),
        },
    }


def persist_bounded_task_owned_prompt_timeout_harvest(
    state_path: Path,
) -> dict[str, Any] | None:
    """Seal the pre-harvest zero-turn proof after its exact harvest completes."""
    evidence = _bounded_task_owned_prompt_timeout_evidence(
        state_path,
        allow_recovery_evidence=True,
    )
    if evidence is None or evidence.get("transport") != "devspace":
        return None
    if not evidence.get("recovery_evidence"):
        return None
    if proven_bounded_task_owned_prompt_timeout_harvest(state_path) is not None:
        return proven_bounded_task_owned_prompt_timeout_harvest(state_path)
    payload = load_state(state_path)
    if payload.get("bounded_prompt_timeout_harvest") is not None:
        return None
    receipt_path = state_path.parent / "bounded-prompt-timeout-harvest.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        return None
    recorded = {
        "schema": "codex.chatgpt.oracle-bounded-prompt-timeout-harvest/v1",
        "code": "ORACLE_ZERO_TURN_PROMPT_TIMEOUT_HARVEST",
        **{key: value for key, value in evidence.items() if not key.startswith("_")},
    }
    write_json_atomic(receipt_path, recorded)
    payload["bounded_prompt_timeout_harvest"] = {
        "schema": "codex.chatgpt.oracle-bounded-prompt-timeout-harvest-reference/v1",
        "path": str(receipt_path),
        "sha256": sha256_file(receipt_path),
    }
    write_json_atomic(state_path, payload)
    return proven_bounded_task_owned_prompt_timeout_harvest(state_path)


def _user_confirmable_no_submission_evidence(state_path: Path) -> dict[str, Any] | None:
    """Return exact evidence for supported user-adjudicable Oracle runs."""
    # A follow-up session necessarily stores its already-existing parent URL in
    # immutable resume configuration. Its dedicated predicate permits only that
    # exact parent URL while rejecting any child runtime/conversation binding.
    followup = _followup_no_submission_evidence(state_path, require_recovery_evidence=True)
    if followup is not None:
        return followup
    if _settlement_logs_have_conversation_url(state_path):
        return None
    comprehensive = _comprehensive_no_submission_evidence(state_path)
    if comprehensive is not None:
        return comprehensive
    pre_submit_host = _pre_submit_host_no_submission_evidence(state_path)
    if pre_submit_host is not None:
        return pre_submit_host
    browser_session_absent = _browser_session_absent_no_submission_evidence(state_path)
    if browser_session_absent is not None:
        return browser_session_absent
    state = load_state(state_path)
    if state.get("bounded_prompt_timeout_harvest") is not None:
        # A run that entered the bounded zero-turn harvest path must keep that
        # stronger append-only proof through settlement. Never fall back to the
        # generic direct DevSpace predicate if its bound receipt is malformed,
        # tampered, or no longer matches the Oracle/ownership tuple.
        return proven_bounded_task_owned_prompt_timeout_harvest(state_path)
    direct_devspace = _direct_devspace_no_submission_evidence(
        state_path, require_persisted_recovery=True
    )
    if direct_devspace is not None:
        return direct_devspace
    standalone_attachment = _standalone_pro_attachment_no_submission_evidence(state_path)
    if standalone_attachment is not None:
        return standalone_attachment
    standalone_pro = _standalone_pro_no_submission_evidence(state_path)
    if standalone_pro is not None:
        return standalone_pro
    try:
        mission_text = (state_path.parent / "mission.md").read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None
    # A partial or malformed comprehensive contract must never fall through.
    if "[HOST_STAGE_CONTRACT]" in mission_text or "[DEVSPACE_WORKSPACE_ENTRY_CONTRACT]" in mission_text:
        return None
    return _web_multi_child_no_submission_evidence(state_path)


def proven_user_confirmed_no_submission(state_path: Path) -> dict[str, Any] | None:
    """Revalidate a persisted user confirmation against immutable run artifacts."""
    state = load_state(state_path)
    reference = state.get("user_confirmed_no_submission")
    if not isinstance(reference, dict):
        return None
    expected_path = state_path.parent / "user-confirmed-no-submission.json"
    if (
        reference.get("schema") != "codex.chatgpt.oracle-settlement-reference/v1"
        or Path(str(reference.get("path") or "")).resolve() != expected_path.resolve()
        or expected_path.is_symlink()
    ):
        return None
    try:
        artifact_bytes = expected_path.read_bytes()
        if hashlib.sha256(artifact_bytes).hexdigest() != str(reference.get("sha256") or ""):
            return None
        recorded = json.loads(artifact_bytes.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        recorded.get("schema") != "codex.chatgpt.oracle-user-confirmed-no-submission/v1"
        or recorded.get("code") != "ORACLE_USER_CONFIRMED_NO_SUBMISSION"
        or recorded.get("confirmation") != USER_CONFIRMED_NO_SUBMISSION
        or not str(recorded.get("reason") or "").strip()
    ):
        return None
    current = _user_confirmable_no_submission_evidence(state_path)
    if (
        current is None
        and recorded.get("settlement_eligibility")
        == "oracle-standalone-pro-attachment/v1"
    ):
        current = _standalone_pro_attachment_no_submission_evidence(
            state_path,
            allow_settled_attachment_source_drift=True,
        )
    if current is None:
        return None
    for key in (
        "project_root",
        "run_id",
        "mission_sha256",
        "oracle_locator",
        "stdout_sha256",
        "stderr_sha256",
        "recovery_evidence",
        "output_absent",
        "conversation_url_absent",
    ):
        if recorded.get(key) != current.get(key):
            return None
    if current.get("settlement_eligibility") == "oracle-standalone-pro-attachment/v1":
        required = (
            "settlement_eligibility", "transport", "source_mission_path",
            "source_mission_sha256", "transport_mission_path", "transport_mission_sha256",
            "attachment_evidence", "attachment_manifest_sha256", "oracle_version",
            "oracle_command", "pre_submit_marker",
        )
    elif current.get("settlement_eligibility") == "oracle-standalone-qualified-pro/v1":
        required = (
            "settlement_eligibility", "transport", "source_mission_path",
            "source_mission_sha256", "transport_mission_path", "transport_mission_sha256",
            "oracle_version",
        )
        if current.get("pre_submit_marker") == "oracle-model-selector-button-missing/v1":
            required += (
                "pre_submit_marker", "oracle_meta_path", "oracle_meta_sha256",
                "oracle_meta_stage", "prompt_submitted", "tab_url",
            )
    elif current.get("settlement_eligibility") == "oracle-direct-devspace/v1":
        required = (
            "settlement_eligibility", "transport", "profile", "source_mission_path",
            "source_mission_sha256", "transport_mission_path", "transport_mission_sha256",
            "oracle_version",
        )
        if current.get("bounded_prompt_timeout_harvest") is not None:
            required += (
                "bounded_prompt_timeout_harvest", "source_thread_id",
                "ownership_receipt_sha256", "oracle_meta_path", "oracle_meta_sha256",
                "expected_cdp_port", "browser_profile", "browser_target_id",
                "profile_sha256", "browser_config", "browser_config_sha256",
                "recovery_evidence", "prompt_submitted_claim", "commit_probe_turns",
            )
        if current.get("pre_submit_marker") == "oracle-model-option-missing/v1":
            required += (
                "pre_submit_marker", "desired_model", "oracle_meta_path",
                "oracle_meta_sha256", "oracle_meta_stage", "prompt_submitted", "tab_url",
                "source_thread_id", "ownership_receipt_sha256", "expected_cdp_port",
                "browser_profile", "browser_target_id",
            )
    elif current.get("settlement_eligibility") in {
        "oracle-followup-pre-submit-ui/v1",
        "oracle-followup-pre-submit-ui/v2",
    }:
        required = (
            "transport", "followup_binding_mode",
            "followup_reservation_path", "followup_reservation_sha256",
            "parent_run_id", "parent_conversation_url", "round_key",
            "oracle_meta_sha256",
        )
        recorded_eligibility = recorded.get("settlement_eligibility")
        current_eligibility = current.get("settlement_eligibility")
        if recorded_eligibility == current_eligibility:
            required += ("settlement_eligibility",)
        else:
            historical_v1_upgrade = (
                recorded_eligibility == "oracle-followup-pre-submit-ui/v1"
                and current_eligibility == "oracle-followup-pre-submit-ui/v2"
                and current.get("failure_kind") == "textarea-absent"
                and current.get("evidence_profile")
                == "textarea-absent-with-exact-harvest/v1"
                and all(
                    key not in recorded
                    for key in ("failure_kind", "evidence_profile", "harvest_outcome")
                )
            )
            if not historical_v1_upgrade:
                return None
        if recorded_eligibility == "oracle-followup-pre-submit-ui/v2":
            required += ("failure_kind", "evidence_profile")
            if recorded.get("evidence_profile") == "archived-parent-unarchive-v1.18.4-legacy-no-click/v1":
                required += ("legacy_install_receipt_path", "legacy_install_receipt_sha256")
            elif recorded.get("evidence_profile") == "structured-pre-composer-runtime-unbound/v1":
                required += ("pre_composer_stage", "prior_turns_observed")
        # v1 settlements predate failure/evidence profile labels.  Their exact
        # artifact hashes and immutable follow-up binding remain authoritative;
        # a predicate-only v1 -> v2 label upgrade must not resurrect ownership.
        # New v2 settlements bind the explicit harmless-harvest outcome while
        # existing v2 receipts that predate this field remain revalidatable.
        if "harvest_outcome" in recorded:
            required += ("harvest_outcome",)
    elif current.get("settlement_eligibility") == "oracle-pre-submit-host/v1":
        required = (
            "settlement_eligibility", "transport", "transport_mission_path",
            "transport_mission_sha256", "transcript_sha256",
        )
        recorded_host_failure = recorded.get("host_failure")
        current_host_failure = current.get("host_failure")
        if recorded_host_failure != current_host_failure:
            # v1.20.12 briefly emitted the exact metadata-rename settlement
            # before the task-bound proof added ``source_thread_id`` to the
            # re-derived host failure.  Preserve only that append-only receipt:
            # every earlier field, including the ownership-receipt hash, must
            # still match byte-for-byte, and the current predicate must have
            # independently re-proven the bound task from state + receipt.
            historical_metadata_task_binding_upgrade = (
                isinstance(recorded_host_failure, dict)
                and isinstance(current_host_failure, dict)
                and recorded_host_failure.get("code")
                == "ORACLE_SESSION_METADATA_RENAME_PRELAUNCH_FAILED"
                and current_host_failure.get("code")
                == "ORACLE_SESSION_METADATA_RENAME_PRELAUNCH_FAILED"
                and "source_thread_id" not in recorded_host_failure
                and isinstance(current_host_failure.get("source_thread_id"), str)
                and bool(
                    SOURCE_THREAD_ID_RE.fullmatch(
                        current_host_failure["source_thread_id"]
                    )
                )
                and recorded_host_failure
                == {
                    key: value
                    for key, value in current_host_failure.items()
                    if key != "source_thread_id"
                }
            )
            if not historical_metadata_task_binding_upgrade:
                return None
    elif current.get("settlement_eligibility") == "oracle-web-multi-child/v1":
        required = (
            "settlement_eligibility", "parallel_parent_id", "source_mission_path",
            "source_mission_sha256", "transport_mission_path", "transport_mission_sha256",
        )
        if current.get("provenance_mode") == "new-child-provenance/v1":
            required += ("provenance_mode", "child_provenance_path", "child_provenance_sha256", "parent_manifest_path", "parent_manifest_sha256")
        elif current.get("provenance_mode") == "legacy-result-lane/v1":
            required += ("provenance_mode", "legacy_result_path", "legacy_result_sha256", "legacy_lane_manifest_path", "legacy_lane_manifest_sha256")
        else:
            return None
    else:
        required = ("workflow_id", "stage", "attempt_id", "input_mission_sha256")
    if any(recorded.get(key) != current.get(key) for key in required):
        return None
    return {**recorded, **{key: value for key, value in current.items() if key.startswith("_")}}


def settle_user_confirmed_no_submission(
    state_path: Path,
    *,
    confirmation: str,
    reason: str,
) -> dict[str, Any]:
    """Release one ambiguous send only after explicit user adjudication.

    Mechanical evidence remains fail-closed: it merely makes the run eligible.
    The exact confirmation token is the authority that resolves non-submission.
    """
    state_owner = source_thread_id_from_state(load_state(state_path))
    caller = current_source_thread_id()
    if state_owner and caller != state_owner:
        raise OracleStateError(
            "FOREIGN_TASK_SESSION",
            "the exact Oracle run belongs to a different Codex task; settlement is forbidden",
        )
    if confirmation.strip().casefold() != USER_CONFIRMED_NO_SUBMISSION:
        raise OracleStateError(
            "NO_SUBMISSION_CONFIRMATION_REQUIRED",
            f"confirmation must be exactly {USER_CONFIRMED_NO_SUBMISSION}",
        )
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise OracleStateError("NO_SUBMISSION_REASON_REQUIRED", "confirmation reason is required")
    payload = load_state(state_path)
    existing = proven_user_confirmed_no_submission(state_path)
    if existing is not None:
        return payload
    authority = str(payload.get("session_authority") or "")
    evidence = _user_confirmable_no_submission_evidence(state_path)
    pre_submit_host_eligible = (
        authority == "pre_submit"
        and isinstance(evidence, dict)
        and evidence.get("settlement_eligibility") == "oracle-pre-submit-host/v1"
    )
    if authority != "submitted_unknown" and not pre_submit_host_eligible:
        raise OracleStateError(
            "NO_SUBMISSION_AUTHORITY_INVALID",
            "only a submitted_unknown run or an exact proven pre-submit host failure may be adjudicated as not submitted",
        )
    if evidence is None:
        # Legacy prompt-observation failures predate the durable recovery
        # receipt.  Only this explicit, exact user-attestation command may
        # backfill it, and only after strict immutable-artifact validation.
        evidence = persist_direct_devspace_prompt_not_observed_recovery(state_path)
        # The receipt helper atomically augments state.  Reload before writing
        # the settlement so that its hash-bound reference is never lost.
        payload = load_state(state_path)
    if evidence is None:
        raise OracleStateError(
            "NO_SUBMISSION_EVIDENCE_INCOMPLETE",
            "run lacks the exact pre-submit UI and recovery-binding evidence required for user adjudication",
        )
    recorded = {
        **{key: value for key, value in evidence.items() if not key.startswith("_")},
        # The eligibility proof may carry its own schema.  Keep the outer
        # settlement artifact unambiguous so it can be proven on a later pass.
        "schema": "codex.chatgpt.oracle-user-confirmed-no-submission/v1",
        "code": "ORACLE_USER_CONFIRMED_NO_SUBMISSION",
        "confirmation": USER_CONFIRMED_NO_SUBMISSION,
        "reason": normalized_reason,
    }
    settlement_path = state_path.parent / "user-confirmed-no-submission.json"
    write_json_atomic(settlement_path, recorded)
    settlement_sha256 = sha256_file(settlement_path)
    payload.update({
        "status": "attention_required",
        "exit_code": int(payload.get("exit_code") or 1),
        "session_authority": "pre_submit",
        "terminal_harvested": False,
        "artifact_sha256": None,
        "transport_status": "not_submitted_user_confirmed",
        "task_outcome": "pending",
        "task_outcome_reason": (
            (
                "user-confirmed-no-submission-after-devspace-restart-required"
                if evidence.get("host_failure", {}).get("failure_reason")
                == "devspace-service-restart-required"
                else "user-confirmed-no-submission-after-oracle-metadata-rename-failure"
                if evidence.get("host_failure", {}).get("failure_reason")
                == "oracle-session-metadata-rename-failed-before-browser"
                else "user-confirmed-no-submission-after-oracle-version-resolution-failure"
            )
            if evidence.get("settlement_eligibility") == "oracle-pre-submit-host/v1"
            else "user-confirmed-no-submission-after-model-selector-failure"
            if evidence.get("pre_submit_marker") in {
                "oracle-model-selector-button-missing/v1",
                "oracle-model-option-missing/v1",
            }
            else "user-confirmed-no-submission-after-prompt-timeout"
        ),
        "user_confirmed_no_submission": {
            "schema": "codex.chatgpt.oracle-settlement-reference/v1",
            "path": str(settlement_path),
            "sha256": settlement_sha256,
        },
    })
    write_json_atomic(state_path, payload)
    return payload


def proven_user_confirmed_execution_ended(state_path: Path) -> dict[str, Any] | None:
    """Revalidate the narrow post-submit timeout settlement before releasing a lock."""
    state = load_state(state_path)
    reference = state.get("user_confirmed_execution_ended")
    expected_path = state_path.parent / "user-confirmed-execution-ended.json"
    if (
        not isinstance(reference, dict)
        or reference.get("schema") != "codex.chatgpt.oracle-settlement-reference/v1"
        or Path(str(reference.get("path") or "")).resolve() != expected_path.resolve()
        or expected_path.is_symlink()
    ):
        return None
    try:
        raw = expected_path.read_bytes()
        if sha256_file(expected_path) != str(reference.get("sha256") or ""):
            return None
        recorded = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        recorded.get("schema") != "codex.chatgpt.oracle-user-confirmed-execution-ended/v1"
        or recorded.get("code") != "ORACLE_USER_CONFIRMED_EXECUTION_ENDED"
        or recorded.get("confirmation") != USER_CONFIRMED_EXECUTION_ENDED
        or not str(recorded.get("reason") or "").strip()
        or str(state.get("session_authority") or "") != "settled_executed"
        or state.get("terminal_harvested") is not False
        or str(state.get("transport_status") or "") != "post_submit_provider_delivery_timeout_settled"
        or str(state.get("task_outcome") or "") != "executed"
    ):
        return None
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    project_root = Path(str(state.get("project_root") or ""))
    if (
        not output.is_file()
        or output.is_symlink()
        or sha256_file(output) != str(recorded.get("output_sha256") or "")
        or not project_root.is_dir()
        or recorded.get("run_id") != state.get("run_id")
        or recorded.get("project_root") != str(project_root.resolve())
        or recorded.get("conversation_url") != str((state.get("oracle") or {}).get("conversation_url") or "")
    ):
        return None
    evidence = recorded.get("execution_evidence")
    if not isinstance(evidence, list) or not evidence:
        return None
    for item in evidence:
        if not isinstance(item, dict):
            return None
        path = Path(str(item.get("path") or ""))
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return None
        if (
            path.is_symlink()
            or not resolved.is_file()
            or not is_within(project_root.resolve(), resolved)
            or sha256_file(resolved) != str(item.get("sha256") or "")
        ):
            return None
    return recorded


def proven_pre_submit_rejection(state_path: Path) -> dict[str, Any] | None:
    """Return immutable evidence only for Oracle's own pre-submit prompt dedup rejection."""
    state = load_state(state_path)
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    if str(output) and output_is_nonempty(output):
        return None
    stdout = Path(str(artifacts.get("stdout") or ""))
    try:
        stdout_bytes = stdout.read_bytes()
        stdout_text = stdout_bytes.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None
    match = ORACLE_DUPLICATE_PROMPT_RE.search(stdout_text)
    if match is None:
        return None
    return {
        "schema": "codex.chatgpt.oracle-pre-submit-rejection/v1",
        "code": "ORACLE_GLOBAL_PROMPT_DUPLICATE",
        "oracle_locator": match.group("locator"),
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "output_absent": True,
    }


def proven_pre_submit_manual_login_profile_uninitialized(
    state_path: Path,
) -> dict[str, Any] | None:
    """Prove Oracle 0.17.1 stopped before opening its manual-login profile.

    This intentionally recognizes one exact upstream failure transcript.  A
    different version, transport, profile, artifact layout, extra output, or
    any conversation URL remains submitted-unknown and keeps the project lock.
    """
    state = load_state(state_path)
    if str(state.get("session_authority") or "") not in {"pre_submit", "submitted_unknown"}:
        return None
    if state.get("terminal_harvested") is True or _state_has_conversation_url(state):
        return None
    if str(state.get("mode") or "") != "browser" or not is_pro_devspace_transport(
        str(state.get("transport") or "")
    ):
        return None

    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    if (
        str(profile.get("model") or "") != "gpt-5.6-sol"
        or str(profile.get("model_strategy") or "") != "select"
        or str(profile.get("copy_profile") or "").strip()
        or str(oracle.get("resolved_version") or "").removeprefix("oracle ").strip() != "0.17.1"
        or not locator
    ):
        return None

    run_dir = state_path.parent.resolve()
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    canonical = {
        "output": run_dir / "output.md",
        "stdout": run_dir / "stdout.log",
        "stderr": run_dir / "stderr.log",
        "transcript": run_dir / "transcript.md",
    }
    resolved: dict[str, Path] = {}
    try:
        for name, expected in canonical.items():
            path = Path(str(artifacts.get(name) or ""))
            if not path.is_absolute() or path.is_symlink() or path.resolve() != expected:
                return None
            resolved[name] = path
        # The known failure never creates output.md.  Even an empty output file
        # is treated as contradictory evidence rather than guessed away.
        if resolved["output"].exists():
            return None
        stdout_bytes = resolved["stdout"].read_bytes()
        stderr_bytes = resolved["stderr"].read_bytes()
        transcript_bytes = resolved["transcript"].read_bytes()
        stdout_text = stdout_bytes.decode("utf-8", errors="strict")
        stderr_text = stderr_bytes.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None
    if stderr_bytes or stderr_text or transcript_bytes != stdout_bytes:
        return None
    if _settlement_logs_have_conversation_url(state_path) or CHATGPT_CONVERSATION_URL_RE.search(
        stdout_text
    ):
        return None

    expected_profile = (Path.home() / ".oracle" / "browser-profile").resolve()
    matches = list(ORACLE_MANUAL_LOGIN_PROFILE_UNINITIALIZED_RE.finditer(stdout_text))
    if [match.group("prefix") for match in matches] != ["ERROR", "User error (browser-automation)"]:
        return None
    try:
        if any(Path(match.group("profile")).resolve() != expected_profile for match in matches):
            return None
    except OSError:
        return None

    escaped_profile = str(expected_profile).replace("\\", "\\\\")
    failure_tail = (
        "ChatGPT browser manual-login profile is not initialized. "
        f"Browser mode is using Oracle's private Chrome profile at {expected_profile}, "
        "separate from your normal Chrome profile. Run first-time setup, sign in there, then retry: "
        "oracle --engine browser --browser-manual-login --browser-keep-browser "
        f'--browser-manual-login-profile-dir "{escaped_profile}" -p "HI". '
        "If you want to reuse an already signed-in Chrome instead, use --browser-attach-running."
    )
    expected_errors = [f"ERROR: {failure_tail}", f"User error (browser-automation): {failure_tail}"]
    lines = stdout_text.splitlines()
    if lines[-2:] != expected_errors:
        return None

    prefix_lines = lines[:-2]
    if len(prefix_lines) != 11:
        return None
    banner_ok = bool(re.fullmatch(r".{1,4} oracle 0\.17\.1 .{2,120}", prefix_lines[0]))
    launch_ok = bool(
        re.fullmatch(
            r"Launching browser mode \(target=GPT-5\.6 Sol; requested=gpt-5\.6-sol\) "
            r"with ~[1-9][0-9]* tokens\.",
            prefix_lines[6],
        )
    )
    if not (
        banner_ok
        and prefix_lines[1] == f"Session: {locator}"
        and prefix_lines[2:6]
        == [
            "Mode: browser foreground",
            "Models: 1",
            "Detach: no",
            f"Reattach: oracle session {locator}",
        ]
        and launch_ok
        and prefix_lines[7:]
        == [
            "This run can take up to an hour (usually ~10 minutes).",
            "[browser] Browser control: launch Chrome in hidden-window mode; may focus/control the browser UI.",
            "[browser] Browser guidance: On macOS, Oracle launches Chrome off-screen while keeping the page rendered.",
            "[browser] Browser guidance: For the calmest shared-desktop flow, prefer --browser-attach-running or --remote-chrome.",
        ]
    ):
        return None

    return {
        "schema": "codex.chatgpt.oracle-pre-submit-host-failure/v1",
        "code": "ORACLE_MANUAL_LOGIN_PROFILE_UNINITIALIZED_PRELAUNCH_FAILED",
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "transcript_sha256": hashlib.sha256(transcript_bytes).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
        "resolved_version": "0.17.1",
        "oracle_locator": locator,
        "failure_reason": "oracle-manual-login-profile-uninitialized",
        "manual_login_profile": str(expected_profile),
    }


def proven_pre_submit_cdp_disconnect(state_path: Path) -> dict[str, Any] | None:
    """Prove the bounded Oracle 0.17.1 CDP disconnect happened before send.

    Oracle's generic disconnect text is not sufficient.  The external session
    ledger must also bind the exact slug, record ``promptSubmitted=false`` on
    both runtime copies, remain on the ChatGPT home URL, and contain the exact
    recoverable-disconnect classification.  Any output, conversation URL,
    changed version/profile, or contradictory metadata keeps the run locked.
    """
    state = load_state(state_path)
    if str(state.get("session_authority") or "") not in {"pre_submit", "submitted_unknown"}:
        return None
    if state.get("terminal_harvested") is True or _state_has_conversation_url(state):
        return None
    if str(state.get("mode") or "") != "browser" or not is_pro_devspace_transport(
        str(state.get("transport") or "")
    ):
        return None

    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    expected_profile = (Path.home() / ".oracle" / "browser-profile").resolve()
    try:
        copy_profile = Path(str(profile.get("copy_profile") or "")).resolve()
    except OSError:
        return None
    if (
        str(profile.get("model") or "") != "gpt-5.6-sol"
        or str(profile.get("model_strategy") or "") != "select"
        or not is_compatible_pro_thinking_time(profile.get("thinking_time"))
        or copy_profile != expected_profile
        or str(oracle.get("resolved_version") or "").removeprefix("oracle ").strip() != "0.17.1"
        or not locator
    ):
        return None

    run_dir = state_path.parent.resolve()
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    canonical = {
        "output": run_dir / "output.md",
        "stdout": run_dir / "stdout.log",
        "stderr": run_dir / "stderr.log",
        "transcript": run_dir / "transcript.md",
    }
    resolved: dict[str, Path] = {}
    try:
        for name, expected in canonical.items():
            path = Path(str(artifacts.get(name) or ""))
            if not path.is_absolute() or path.is_symlink() or path.resolve() != expected:
                return None
            resolved[name] = path
        if resolved["output"].exists():
            return None
        stdout_bytes = resolved["stdout"].read_bytes()
        stderr_bytes = resolved["stderr"].read_bytes()
        transcript_bytes = resolved["transcript"].read_bytes()
        stdout_text = stdout_bytes.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None
    if stderr_bytes or transcript_bytes != stdout_bytes:
        return None
    if _settlement_logs_have_conversation_url(state_path) or CHATGPT_CONVERSATION_URL_RE.search(
        stdout_text
    ):
        return None

    failure_lines = [
        f"ERROR: {ORACLE_CDP_DISCONNECT_PRE_SUBMIT_ERROR}",
        f"User error (browser-automation): {ORACLE_CDP_DISCONNECT_PRE_SUBMIT_ERROR}",
    ]
    lines = stdout_text.splitlines()
    if len(lines) != 13 or lines[-2:] != failure_lines:
        return None
    if not (
        re.fullmatch(r".{1,4} oracle 0\.17\.1 .{2,120}", lines[0])
        and lines[1] == f"Session: {locator}"
        and lines[2:6]
        == [
            "Mode: browser foreground",
            "Models: 1",
            "Detach: no",
            f"Reattach: oracle session {locator}",
        ]
        and re.fullmatch(
            r"Launching browser mode \(target=GPT-5\.6 Sol; requested=gpt-5\.6-sol\) "
            r"with ~[1-9][0-9]* tokens\.",
            lines[6],
        )
        and lines[7:11]
        == [
            "This run can take up to an hour (usually ~10 minutes).",
            "[browser] Browser control: launch Chrome in hidden-window mode; may focus/control the browser UI.",
            "[browser] Browser guidance: On macOS, Oracle launches Chrome off-screen while keeping the page rendered.",
            "[browser] Browser guidance: For the calmest shared-desktop flow, prefer --browser-attach-running or --remote-chrome.",
        ]
    ):
        return None

    session_root = Path(
        os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
    ).resolve()
    meta_path = session_root / locator / "meta.json"
    if meta_path.is_symlink():
        return None
    try:
        meta_bytes = meta_path.read_bytes()
        meta = json.loads(meta_bytes.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
    config = browser.get("config") if isinstance(browser.get("config"), dict) else {}
    runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
    error = meta.get("error") if isinstance(meta.get("error"), dict) else {}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    error_runtime = details.get("runtime") if isinstance(details.get("runtime"), dict) else {}
    options = meta.get("options") if isinstance(meta.get("options"), dict) else {}
    option_browser = (
        options.get("browserConfig") if isinstance(options.get("browserConfig"), dict) else {}
    )
    project_root = Path(str(state.get("project_root") or ""))
    try:
        meta_cwd = Path(str(meta.get("cwd") or "")).resolve()
        output_path = Path(str(options.get("writeOutputPath") or "")).resolve()
        config_profile = Path(str(config.get("copyProfileSource") or "")).resolve()
        option_profile = Path(str(option_browser.get("copyProfileSource") or "")).resolve()
    except OSError:
        return None
    if (
        meta.get("id") != locator
        or meta.get("status") != "error"
        or meta.get("model") != "gpt-5.6-sol"
        or meta.get("mode") != "browser"
        or not str(meta.get("completedAt") or "").strip()
        or meta_cwd != project_root.resolve()
        or config_profile != expected_profile
        or option_profile != expected_profile
        or config.get("desiredModel") != "GPT-5.6 Sol"
        or config.get("modelStrategy") != "select"
        or config.get("thinkingTime") != "heavy"
        or options.get("model") != "gpt-5.6-sol"
        or options.get("slug") != locator
        or output_path != canonical["output"]
        or runtime.get("promptSubmitted") is not False
        or runtime.get("tabUrl") not in {None, "https://chatgpt.com/"}
        or error.get("category") != "browser-automation"
        or error.get("message") != ORACLE_CDP_DISCONNECT_PRE_SUBMIT_ERROR
        or details.get("stage") != "connection-lost"
        or details.get("recoverableDisconnect") is not True
        or details.get("disconnectCause") != "cdp-client-disconnect"
        or error_runtime.get("promptSubmitted") is not False
        or error_runtime.get("tabUrl") != "https://chatgpt.com/"
        or str(meta.get("errorMessage") or "") != ORACLE_CDP_DISCONNECT_PRE_SUBMIT_ERROR
        or CHATGPT_CONVERSATION_URL_RE.search(meta_bytes.decode("utf-8", errors="strict"))
    ):
        return None

    return {
        "schema": "codex.chatgpt.oracle-pre-submit-ui-failure/v1",
        "code": "ORACLE_CDP_DISCONNECT_PRE_SUBMIT_FAILED",
        "oracle_locator": locator,
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "transcript_sha256": hashlib.sha256(transcript_bytes).hexdigest(),
        "oracle_meta_path": str(meta_path),
        "oracle_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
        "prompt_submitted": False,
        "resolved_version": "0.17.1",
        "failure_reason": "oracle-cdp-client-disconnect-before-submit",
    }


def proven_pre_submit_oracle_metadata_rename_failure(
    state_path: Path,
) -> dict[str, Any] | None:
    """Prove Oracle 0.18.0 failed writing its pending ledger before Chrome.

    This is intentionally narrower than a generic EPERM classifier.  It binds
    the exact task/run/mission/locator, the immutable ownership receipt, the
    pending Oracle ledger, the normal pre-browser banner, and the exact
    ``meta.json.<pid>.<uuid>.tmp -> meta.json`` replacement.  Any browser
    runtime, receipt, URL, output, recovery attempt, live process, or path
    mismatch keeps the run submitted-unknown.
    """
    state = load_state(state_path)
    run_dir = state_path.parent.resolve()
    run_id = str(state.get("run_id") or "").strip()
    originating_task = (
        state.get("originating_task")
        if isinstance(state.get("originating_task"), dict)
        else {}
    )
    source_thread_id = source_thread_id_from_state(state)
    state_ownership = (
        state.get("ownership") if isinstance(state.get("ownership"), dict) else {}
    )
    authority = str(state.get("session_authority") or "")
    transport_status = str(state.get("transport_status") or "")
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    command = tuple(str(item) for item in (oracle.get("command") or []))
    try:
        validated_command = validate_oracle_command(list(command))
    except OracleStateError:
        validated_command = ()
    if (
        state.get("schema") != STATE_SCHEMA
        or not run_id
        or run_dir.name != run_id
        or source_thread_id is None
        or originating_task.get("schema") != "codex.chatgpt.oracle-task-owner/v1"
        or originating_task.get("binding") != "bound"
        or originating_task.get("source_thread_id") != source_thread_id
        or state_ownership.get("schema") != "codex.chatgpt.oracle-ownership/v1"
        or state_ownership.get("binding") != "bound"
        or state_ownership.get("source_thread_id") != source_thread_id
        or authority not in {"submitted_unknown", "pre_submit"}
        or state.get("status") != "attention_required"
        or transport_status
        not in {"failed", "failed_pre_submit", "not_submitted_user_confirmed"}
        or state.get("task_outcome") != "pending"
        or state.get("terminal_harvested") is not False
        or int(state.get("exit_code") or 0) == 0
        or str(state.get("mode") or "") != "browser"
        or not is_devspace_transport(str(state.get("transport") or ""))
        or state.get("parallel_parent_id") is not None
        or state.get("requested_run_id") not in (None, run_id)
        or state.get("web_multi_child_provenance") is not None
        or state.get("attachments") not in (None, [])
        or not locator
        or oracle.get("slug") != locator
        or str(oracle.get("resolved_version") or "").removeprefix("oracle ").strip()
        != "0.18.0"
        or validated_command != command
        or _state_has_conversation_url(state)
    ):
        return None

    ownership = proven_ownership_receipt(state_path)
    if ownership is None:
        return None
    ownership_payload = ownership.get("payload") if isinstance(ownership.get("payload"), dict) else {}
    controller_pid = int(ownership_payload.get("oracle_process_pid") or 0)
    observer = state.get("browser_observer") if isinstance(state.get("browser_observer"), dict) else {}
    if (
        controller_pid <= 0
        or int(observer.get("oracle_process_pid") or 0) != controller_pid
        or observer.get("status") != "process-exited"
        or _process_may_be_alive(controller_pid)
    ):
        return None

    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    mission_sha256 = str(mission.get("sha256") or "").casefold()
    transport_path = Path(str(mission.get("transport_path") or ""))
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output_path = Path(str(artifacts.get("output") or ""))
    browser_temp = Path(str(artifacts.get("browser_temp") or ""))
    records = {name: _artifact_bytes(state, name) for name in ("stdout", "stderr", "transcript")}
    if (
        not re.fullmatch(r"[a-f0-9]{64}", mission_sha256)
        or transport_path.resolve() != (run_dir / "mission.md").resolve()
        or transport_path.is_symlink()
        or output_path.resolve() != (run_dir / "output.md").resolve()
        or output_path.exists()
        or output_path.is_symlink()
        or browser_temp.resolve() != (run_dir / "browser-temp").resolve()
        or browser_temp.is_symlink()
        or any(record is None for record in records.values())
        or any(run_dir.glob("recovery-*-stdout.log"))
        or any(run_dir.glob("recovery-*-stderr.log"))
    ):
        return None
    try:
        project_root = Path(str(state.get("project_root") or "")).resolve(strict=True)
        transport_bytes = transport_path.read_bytes()
        if not browser_temp.is_dir():
            return None
    except OSError:
        return None
    if (
        not project_root.is_dir()
        or ownership_payload.get("source_thread_id") != source_thread_id
        or ownership_payload.get("binding") != "bound"
        or ownership_payload.get("project_root_sha256")
        != hashlib.sha256(str(project_root).casefold().encode("utf-8")).hexdigest()
        or hashlib.sha256(transport_bytes).hexdigest() != mission_sha256
        or ownership_payload.get("mission_sha256") != mission_sha256
        or ownership_payload.get("run_id") != run_id
        or ownership_payload.get("slug") != locator
        or ownership_payload.get("project_root") != str(state.get("project_root") or "")
    ):
        return None

    stdout_path, stdout_bytes = records["stdout"]  # type: ignore[misc]
    stderr_path, stderr_bytes = records["stderr"]  # type: ignore[misc]
    transcript_path, transcript_bytes = records["transcript"]  # type: ignore[misc]
    if (
        stdout_path.resolve() != (run_dir / "stdout.log").resolve()
        or stderr_path.resolve() != (run_dir / "stderr.log").resolve()
        or transcript_path.resolve() != (run_dir / "transcript.md").resolve()
        or any(path.is_symlink() for path in (stdout_path, stderr_path, transcript_path))
        or transcript_bytes != stdout_bytes + stderr_bytes
    ):
        return None
    try:
        stdout_text = stdout_bytes.decode("utf-8", errors="strict")
        stderr_text = stderr_bytes.decode("utf-8", errors="strict")
        transcript_text = transcript_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    lines = stdout_text.splitlines()
    if (
        len(lines) != 6
        or re.fullmatch(r".{1,4}\s+oracle 0\.18\.0\s+.{2,160}", lines[0]) is None
        or lines[1:] != [
            f"Session: {locator}",
            "Mode: browser foreground",
            "Models: 1",
            "Detach: no",
            f"Reattach: oracle session {locator}",
        ]
        or CHATGPT_CONVERSATION_URL_RE.search(transcript_text)
    ):
        return None
    rename = ORACLE_SESSION_METADATA_RENAME_RE.fullmatch(stderr_text.strip())
    if (
        rename is None
        or ORACLE_SESSION_METADATA_RENAME_MESSAGES.get(rename.group("code"))
        != rename.group("message")
    ):
        return None

    session_root = Path(
        os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
    ).resolve()
    meta_path = session_root / locator / "meta.json"
    if meta_path.is_symlink() or meta_path.parent.is_symlink():
        return None
    try:
        meta_bytes = meta_path.read_bytes()
        meta = json.loads(meta_bytes.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    source_text = rename.group("source")
    destination_text = rename.group("destination")
    expected_destination = str(meta_path)
    source_match = re.fullmatch(
        re.escape(expected_destination)
        + r"\.(?P<pid>[1-9][0-9]*)\.(?P<nonce>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.tmp",
        source_text,
        re.IGNORECASE,
    )
    if destination_text != expected_destination or source_match is None:
        return None
    writer_pid = int(source_match.group("pid"))
    if _process_may_be_alive(writer_pid) or Path(source_text).exists():
        return None

    browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
    config = browser.get("config") if isinstance(browser.get("config"), dict) else {}
    options = meta.get("options") if isinstance(meta.get("options"), dict) else {}
    option_browser = options.get("browserConfig") if isinstance(options.get("browserConfig"), dict) else {}
    models = meta.get("models") if isinstance(meta.get("models"), list) else []
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    browser_identity = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    provider = state.get("provider_session") if isinstance(state.get("provider_session"), dict) else {}
    try:
        meta_cwd = Path(str(meta.get("cwd") or "")).resolve()
        meta_output = Path(str(options.get("writeOutputPath") or "")).resolve()
        state_profile = Path(str(profile.get("copy_profile") or "")).resolve()
        config_profile = Path(str(config.get("copyProfileSource") or "")).resolve()
        option_profile = Path(str(option_browser.get("copyProfileSource") or "")).resolve()
        provider_meta = Path(str(provider.get("oracle_meta_path") or "")).resolve()
    except OSError:
        return None

    forbidden_runtime_keys = {"promptSubmitted", "conversationId", "tabUrl", "commitProbe"}

    def contains_forbidden_runtime_key(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                key in forbidden_runtime_keys or contains_forbidden_runtime_key(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(contains_forbidden_runtime_key(item) for item in value)
        return False

    expected_port = int(browser_identity.get("expected_cdp_port") or 0)
    browser_receipt = run_dir / "browser-identity-receipt.json"
    if (
        meta.get("id") != locator
        or meta.get("status") != "pending"
        or meta.get("model") != "gpt-5.6"
        or meta.get("mode") != "browser"
        or meta_cwd != project_root
        or not str(meta.get("createdAt") or "").strip()
        or models != [{"model": "gpt-5.6", "status": "pending", "log": {"path": "models\\gpt-5.6.log"}}]
        or "runtime" in browser
        or contains_forbidden_runtime_key(meta)
        or expected_port <= 0
        or profile.get("model") != "gpt-5.6"
        or profile.get("model_strategy") != "select"
        or profile.get("thinking_time") != "extra-high"
        or config.get("debugPort") != expected_port
        or option_browser.get("debugPort") != expected_port
        or config.get("desiredModel") != "GPT-5.6 Sol"
        or config.get("modelStrategy") != "select"
        or config.get("thinkingTime") != "extra-high"
        or options.get("model") != "gpt-5.6"
        or options.get("slug") != locator
        or options.get("mode") != "browser"
        or meta_output != output_path.resolve()
        or state_profile != config_profile
        or state_profile != option_profile
        or browser_identity.get("receipt_path") is not None
        or browser_identity.get("receipt_sha256") is not None
        or browser_receipt.exists()
        or browser_receipt.is_symlink()
        or provider.get("status") != "pending"
        or provider.get("terminal_confirmed") is not False
        or provider.get("binding") != "unconfirmed"
        or provider.get("observed_conversation_url") is not None
        or provider_meta != meta_path.resolve()
        or provider.get("oracle_meta_sha256") != hashlib.sha256(meta_bytes).hexdigest()
    ):
        return None

    return {
        "schema": "codex.chatgpt.oracle-pre-submit-host-failure/v1",
        "code": "ORACLE_SESSION_METADATA_RENAME_PRELAUNCH_FAILED",
        "oracle_locator": locator,
        "oracle_version": "0.18.0",
        "rename_error_code": rename.group("code"),
        "rename_source": source_text,
        "rename_destination": destination_text,
        "rename_writer_pid": writer_pid,
        "controller_pid": controller_pid,
        "source_thread_id": source_thread_id,
        "ownership_receipt_sha256": ownership["sha256"],
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "transcript_sha256": hashlib.sha256(transcript_bytes).hexdigest(),
        "oracle_meta_path": str(meta_path),
        "oracle_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
        "resolved_version": "0.18.0",
        "failure_reason": "oracle-session-metadata-rename-failed-before-browser",
    }


def proven_pre_submit_project_session_still_live(
    state_path: Path,
) -> dict[str, Any] | None:
    """Prove a same-task owner blocked this run before Oracle could launch.

    The controller emits this exact error while it still holds ``pre_submit``
    authority and before creating either an Oracle process or browser receipt.
    The blocked run is releasable only after the exact task has no remaining
    submitted owner; until then the older run, not this failed attempt, retains
    fresh-submission authority.
    """
    state = load_state(state_path)
    run_id = str(state.get("run_id") or "")
    owner_thread = source_thread_id_from_state(state)
    try:
        run_dir = state_path.parent.resolve(strict=True)
        project_root = Path(str(state.get("project_root") or "")).resolve(strict=True)
    except OSError:
        return None
    lifecycle_shape = (
        str(state.get("status") or ""),
        str(state.get("transport_status") or ""),
        str(state.get("task_outcome") or ""),
    )
    if (
        state_path.is_symlink()
        or state_path.resolve() != run_dir / "state.json"
        or not run_id
        or run_dir.name != run_id
        or owner_thread is None
        or not project_root.is_dir()
        or str(state.get("session_authority") or "") != "pre_submit"
        or state.get("terminal_harvested") is not False
        or state.get("artifact_sha256") is not None
        or lifecycle_shape
        not in {
            ("failed", "prepared", "pending"),
            ("attention_required", "failed_pre_submit", "not_executed"),
        }
        or _state_has_conversation_url(state)
    ):
        return None

    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    canonical = {
        "output": run_dir / "output.md",
        "stdout": run_dir / "stdout.log",
        "stderr": run_dir / "stderr.log",
        "transcript": run_dir / "transcript.md",
        "browser_temp": run_dir / "browser-temp",
    }
    resolved: dict[str, Path] = {}
    try:
        for name, expected in canonical.items():
            path = Path(str(artifacts.get(name) or ""))
            if not path.is_absolute() or path.is_symlink() or path.resolve() != expected:
                return None
            resolved[name] = path
        if resolved["output"].exists() or resolved["browser_temp"].exists():
            return None
        stdout_bytes = resolved["stdout"].read_bytes()
        stderr_bytes = resolved["stderr"].read_bytes()
        transcript_bytes = resolved["transcript"].read_bytes()
    except OSError:
        return None
    if (
        stdout_bytes
        or stderr_bytes != PROJECT_SESSION_STILL_LIVE_PRELAUNCH_ERROR.encode("utf-8")
        or transcript_bytes != stderr_bytes
        or ownership_receipt_path(run_dir).exists()
        or browser_identity_receipt_path(run_dir).exists()
        or any(run_dir.glob("recovery-*.log"))
    ):
        return None

    browser = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    provider = state.get("provider_session") if isinstance(state.get("provider_session"), dict) else {}
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    if (
        not locator
        or str(oracle.get("resolved_version") or "").strip() in {"", "unresolved"}
        or browser.get("receipt_path") is not None
        or browser.get("receipt_sha256") is not None
        or provider.get("status") != "unobserved"
        or provider.get("terminal_confirmed") is not False
        or provider.get("binding") != "none"
        or provider.get("reason") != "oracle-runtime-not-yet-observed"
    ):
        return None

    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    ownership = state.get("ownership") if isinstance(state.get("ownership"), dict) else {}
    mission_path = Path(str(mission.get("transport_path") or ""))
    mission_sha256 = str(mission.get("sha256") or "").casefold()
    try:
        mission_bytes = mission_path.read_bytes()
    except OSError:
        return None
    if (
        not re.fullmatch(r"[a-f0-9]{64}", mission_sha256)
        or not mission_path.is_absolute()
        or mission_path.is_symlink()
        or mission_path.resolve() != run_dir / "mission.md"
        or hashlib.sha256(mission_bytes).hexdigest() != mission_sha256
        or ownership.get("schema") != "codex.chatgpt.oracle-ownership/v1"
        or ownership.get("binding") != "bound"
        or str(ownership.get("source_thread_id") or "").casefold() != owner_thread
        or ownership.get("run_id") != run_id
        or ownership.get("mission_sha256") != mission_sha256
        or ownership.get("slug") != locator
        or ownership.get("project_root_sha256")
        != hashlib.sha256(str(project_root).casefold().encode("utf-8")).hexdigest()
    ):
        return None

    owners = unresolved_project_sessions(
        run_dir.parent,
        project_root,
        parallel_parent_id=str(state.get("parallel_parent_id") or "") or None,
        exclude_run_id=run_id,
        source_thread_id=owner_thread,
    )
    if owners:
        return None
    return {
        "schema": "codex.chatgpt.oracle-pre-submit-ownership-conflict/v1",
        "code": "PROJECT_SESSION_STILL_LIVE_PRELAUNCH_FAILED",
        "project_root": str(project_root),
        "run_id": run_id,
        "source_thread_id": owner_thread,
        "oracle_locator": locator,
        "mission_sha256": mission_sha256,
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "transcript_sha256": hashlib.sha256(transcript_bytes).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
        "cleared_owner_count": 0,
        "failure_reason": "same-task-project-session-still-live-before-oracle-launch",
    }


def proven_pre_submit_host_failure(state_path: Path) -> dict[str, Any] | None:
    """Prove a host failure happened before Oracle/browser launch.

    `execute_run` emits the version-resolution prefix itself before the Oracle
    process is created.  The additional immutable-state checks keep this from
    reclassifying a real submitted or live session.
    """
    ownership_conflict = proven_pre_submit_project_session_still_live(state_path)
    if ownership_conflict is not None:
        return ownership_conflict
    metadata_rename = proven_pre_submit_oracle_metadata_rename_failure(state_path)
    if metadata_rename is not None:
        return metadata_rename
    manual_login = proven_pre_submit_manual_login_profile_uninitialized(state_path)
    if manual_login is not None:
        return manual_login
    state = load_state(state_path)
    authority = str(state.get("session_authority") or "")
    if authority not in {"pre_submit", "submitted_unknown"}:
        return None
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    if _state_has_conversation_url(state):
        return None
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    if str(output) and output_is_nonempty(output):
        return None
    stdout_record = _artifact_bytes(state, "stdout")
    stderr_record = _artifact_bytes(state, "stderr")
    if stdout_record is None or stderr_record is None:
        return None
    _, stdout_bytes = stdout_record
    _, stderr_bytes = stderr_record
    # Oracle prints this version banner before validating local attachments;
    # it is not browser/session evidence.  Any other stdout remains fail-closed.
    stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    attachment_limit_banner_only = stdout_text == "🧿 oracle 0.17.1 — Questions in, clarity out."
    if stdout_text and not attachment_limit_banner_only:
        return None
    try:
        stderr_text = stderr_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    normalized_error = stderr_text.lstrip()
    if (
        is_attachment_transport(str(state.get("transport") or ""))
        and "The following files exceed the 1 MB limit:" in normalized_error
        and attachment_limit_banner_only
    ):
        attachments = state.get("attachments")
        if not isinstance(attachments, list) or not any(
            isinstance(item, dict) and int(item.get("size_bytes") or 0) > 1024 * 1024
            for item in attachments
        ):
            return None
        failure_reason = "oracle-attachment-size-limit"
        code = "ORACLE_ATTACHMENT_SIZE_PRELAUNCH_FAILED"
    elif str(oracle.get("resolved_version") or "") != "unresolved":
        return None
    elif not normalized_error.startswith("version resolution failed:"):
        return None
    elif normalized_error.strip() == DEVSPACE_SERVICE_RESTART_REQUIRED_ERROR:
        lifecycle_shape = (
            (state.get("status"), str(state.get("transport_status") or ""))
            in {
                ("failed", "prepared"),
                ("attention_required", "failed_pre_submit"),
                ("attention_required", "not_submitted_user_confirmed"),
            }
        )
        if (
            authority != "pre_submit"
            or not lifecycle_shape
            or state.get("terminal_harvested") is not False
            or str(state.get("transport") or "") != "devspace"
            or str(state.get("mode") or "") != "browser"
            or str(state.get("task_outcome") or "") != "pending"
            or stdout_bytes
        ):
            return None
        failure_reason = "devspace-service-restart-required"
        code = "DEVSPACE_SERVICE_RESTART_PRELAUNCH_FAILED"
    elif normalized_error.strip() == (
        "version resolution failed: ORACLE_VERSION_FAILED: "
        "Oracle version could not be resolved"
    ):
        lifecycle_shape = (
            (state.get("status"), str(state.get("transport_status") or ""))
            in {
                ("failed", "prepared"),
                ("attention_required", "failed_pre_submit"),
                ("attention_required", "not_submitted_user_confirmed"),
            }
        )
        command = tuple(str(item) for item in (oracle.get("command") or []))
        try:
            validated_command = validate_oracle_command(list(command))
        except OracleStateError:
            validated_command = ()
        locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
        if (
            authority != "pre_submit"
            or not lifecycle_shape
            or state.get("terminal_harvested") is not False
            or not is_devspace_transport(str(state.get("transport") or ""))
            or str(state.get("mode") or "") != "browser"
            or str(state.get("task_outcome") or "") != "pending"
            or stdout_bytes
            or validated_command != command
            or not locator
        ):
            return None
        failure_reason = "oracle-version-command-failed-before-launch"
        code = "ORACLE_VERSION_RESOLUTION_PRELAUNCH_FAILED"
    elif "Oracle compatibility is validated only for the tested version" in normalized_error:
        failure_reason = "compatibility-version-drift"
        code = "ORACLE_VERSION_RESOLUTION_PRELAUNCH_FAILED"
    elif (
        "ORACLE_VERSION_TIMEOUT:" in normalized_error
        or ("--version" in normalized_error and "timed out after 30 seconds" in normalized_error)
    ):
        failure_reason = "version-resolution-timeout"
        code = "ORACLE_VERSION_RESOLUTION_PRELAUNCH_FAILED"
    else:
        return None
    return {
        "schema": "codex.chatgpt.oracle-pre-submit-host-failure/v1",
        "code": code,
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
        "resolved_version": "unresolved",
        "failure_reason": failure_reason,
    }


ORACLE_BROWSER_SESSION_ABSENT_RE = re.compile(
    r"ChatGPT session not detected\.\s*Login button detected on page\.",
    re.IGNORECASE,
)
ORACLE_BROWSER_COOKIES_ABSENT_RE = re.compile(
    r"No ChatGPT cookies were applied",
    re.IGNORECASE,
)


def _browser_session_absent_no_submission_evidence(state_path: Path) -> dict[str, Any] | None:
    """Bind a pre-submit failure caused by an unauthenticated Oracle browser.

    Oracle stops before the composer when its dedicated profile has no ChatGPT
    session, so no conversation can exist.  Without this eligibility type the
    project lock survived forever: the run holds ``submitted_unknown`` authority
    while every other detector rejects it, and no recovery binding can ever be
    backfilled because the provider was never reached.
    """
    state = load_state(state_path)
    run_dir = state_path.parent
    run_id = str(state.get("run_id") or "")
    if (
        not run_id
        or run_dir.name != run_id
        # Settlement rewrites both fields, so revalidation must accept the
        # settled pair as well.  Otherwise a recorded settlement can never be
        # reproven and the project lock survives forever.
        or str(state.get("transport_status") or "") not in {"failed", "not_submitted_user_confirmed"}
        or str(state.get("session_authority") or "") not in {"submitted_unknown", "pre_submit"}
        or bool(state.get("terminal_harvested"))
        or _artifact_bytes(state, "output") is not None
    ):
        return None
    if _settlement_logs_have_conversation_url(state_path):
        return None
    stdout_record = _artifact_bytes(state, "stdout")
    if stdout_record is None:
        return None
    stdout_path, stdout_bytes = stdout_record
    try:
        stdout_text = stdout_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if (
        stdout_path.resolve() != (run_dir / "stdout.log").resolve()
        or stdout_path.is_symlink()
        or ORACLE_BROWSER_SESSION_ABSENT_RE.search(stdout_text) is None
        or ORACLE_BROWSER_COOKIES_ABSENT_RE.search(stdout_text) is None
        or "chatgpt.com/c/" in stdout_text
    ):
        return None
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    transport_path = Path(str(mission.get("transport_path") or ""))
    mission_sha256 = str(mission.get("sha256") or "").casefold()
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    if (
        not locator
        or not re.fullmatch(r"[a-f0-9]{64}", mission_sha256)
        or transport_path.resolve() != (run_dir / "mission.md").resolve()
        or transport_path.is_symlink()
    ):
        return None
    try:
        transport_bytes = transport_path.read_bytes()
        project_root = Path(str(state.get("project_root") or "")).resolve(strict=True)
    except OSError:
        return None
    if (
        not project_root.is_dir()
        or hashlib.sha256(transport_bytes).hexdigest() != mission_sha256
    ):
        return None
    recovery: list[dict[str, Any]] = []
    for recovery_stdout in sorted(run_dir.glob("recovery-*-stdout.log"), key=lambda item: item.name):
        try:
            recovery_bytes = recovery_stdout.read_bytes()
            recovery_text = recovery_bytes.decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            return None
        if recovery_stdout.is_symlink() or "chatgpt.com/c/" in recovery_text:
            return None
        recovery.append({
            "stdout_name": recovery_stdout.name,
            "stdout_sha256": hashlib.sha256(recovery_bytes).hexdigest(),
        })
    return {
        "settlement_eligibility": "oracle-browser-session-absent-pre-submit/v1",
        "project_root": str(project_root),
        "run_id": run_id,
        "transport": str(state.get("transport") or ""),
        "transport_mission_path": str(transport_path),
        "transport_mission_sha256": mission_sha256,
        "mission_sha256": mission_sha256,
        "oracle_locator": locator,
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "recovery_evidence": recovery,
        "output_absent": True,
        "conversation_url_absent": True,
        "browser_session_absent": True,
    }


def _pre_submit_host_no_submission_evidence(state_path: Path) -> dict[str, Any] | None:
    """Bind an exact pre-submit host failure without reading project files."""
    failure = proven_pre_submit_host_failure(state_path)
    if (
        failure is None
        or failure.get("code") not in {
            "DEVSPACE_SERVICE_RESTART_PRELAUNCH_FAILED",
            "ORACLE_SESSION_METADATA_RENAME_PRELAUNCH_FAILED",
            "ORACLE_VERSION_RESOLUTION_PRELAUNCH_FAILED",
        }
    ):
        return None
    state = load_state(state_path)
    run_dir = state_path.parent
    run_id = str(state.get("run_id") or "")
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    transport_path = Path(str(mission.get("transport_path") or ""))
    mission_sha256 = str(mission.get("sha256") or "").casefold()
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    transcript_record = _artifact_bytes(state, "transcript")
    if (
        not run_id
        or run_dir.name != run_id
        or not locator
        or not re.fullmatch(r"[a-f0-9]{64}", mission_sha256)
        or transport_path.resolve() != (run_dir / "mission.md").resolve()
        or transport_path.is_symlink()
        or transcript_record is None
        or not is_devspace_transport(str(state.get("transport") or ""))
    ):
        return None
    transcript_path, transcript_bytes = transcript_record
    try:
        transport_bytes = transport_path.read_bytes()
        project_root = Path(str(state.get("project_root") or "")).resolve(strict=True)
    except OSError:
        return None
    if (
        not project_root.is_dir()
        or transcript_path.resolve() != (run_dir / "transcript.md").resolve()
        or transcript_path.is_symlink()
        or hashlib.sha256(transport_bytes).hexdigest() != mission_sha256
        or hashlib.sha256(transcript_bytes).hexdigest()
        != failure.get("transcript_sha256", failure["stderr_sha256"])
    ):
        return None
    return {
        "settlement_eligibility": "oracle-pre-submit-host/v1",
        "project_root": str(project_root),
        "run_id": run_id,
        "transport": str(state.get("transport") or ""),
        "transport_mission_path": str(transport_path),
        "transport_mission_sha256": mission_sha256,
        "mission_sha256": mission_sha256,
        "oracle_locator": locator,
        "stdout_sha256": failure["stdout_sha256"],
        "stderr_sha256": failure["stderr_sha256"],
        "transcript_sha256": hashlib.sha256(transcript_bytes).hexdigest(),
        "recovery_evidence": [],
        "output_absent": True,
        "conversation_url_absent": True,
        "host_failure": failure,
    }


def proven_pre_submit_copy_profile_manual_login_conflict(state_path: Path) -> dict[str, Any] | None:
    """Prove Oracle rejected mutually exclusive profile modes before browser launch."""
    state = load_state(state_path)
    if str(state.get("session_authority") or "") not in {"pre_submit", "submitted_unknown"}:
        return None
    if state.get("terminal_harvested") is True or _state_has_conversation_url(state):
        return None
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    copy_profile = str(profile.get("copy_profile") or "").strip()
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    stdout_record = _artifact_bytes(state, "stdout")
    stderr_record = _artifact_bytes(state, "stderr")
    if (
        not copy_profile
        or str(oracle.get("resolved_version") or "").removeprefix("oracle ").strip() != "0.17.1"
        or output_is_nonempty(output)
        or stdout_record is None
        or stderr_record is None
    ):
        return None
    _, stdout_bytes = stdout_record
    _, stderr_bytes = stderr_record
    combined = (stdout_bytes + b"\n" + stderr_bytes).decode("utf-8", errors="replace")
    if CHATGPT_CONVERSATION_URL_RE.search(combined):
        return None
    if ORACLE_COPY_PROFILE_MANUAL_LOGIN_CONFLICT not in combined:
        return None
    return {
        "schema": "codex.chatgpt.oracle-pre-submit-host-failure/v1",
        "code": "ORACLE_LAUNCH_FLAGS_MUTUALLY_EXCLUSIVE_PRELAUNCH_FAILED",
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
        "failure_reason": "copy-profile-manual-login-default-conflict",
        "copy_profile": str(Path(copy_profile).resolve()),
    }


def proven_pre_submit_profile_copy_rsync_missing(state_path: Path) -> dict[str, Any] | None:
    """Prove Windows profile copy failed before Chrome because Oracle invoked rsync."""
    state = load_state(state_path)
    if str(state.get("session_authority") or "") not in {"pre_submit", "submitted_unknown"}:
        return None
    if state.get("terminal_harvested") is True or _state_has_conversation_url(state):
        return None
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    copy_profile = str(profile.get("copy_profile") or "").strip()
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    stdout_record = _artifact_bytes(state, "stdout")
    stderr_record = _artifact_bytes(state, "stderr")
    if (
        not copy_profile
        or str(oracle.get("resolved_version") or "").removeprefix("oracle ").strip() != "0.17.1"
        or output_is_nonempty(output)
        or stdout_record is None
        or stderr_record is None
    ):
        return None
    _, stdout_bytes = stdout_record
    _, stderr_bytes = stderr_record
    combined = (stdout_bytes + b"\n" + stderr_bytes).decode("utf-8", errors="replace")
    if CHATGPT_CONVERSATION_URL_RE.search(combined):
        return None
    if ORACLE_PROFILE_COPY_RSYNC_MISSING not in combined:
        return None
    return {
        "schema": "codex.chatgpt.oracle-pre-submit-host-failure/v1",
        "code": "ORACLE_PROFILE_COPY_RSYNC_PRELAUNCH_FAILED",
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
        "failure_reason": "oracle-profile-copy-requires-rsync-on-windows",
        "copy_profile": str(Path(copy_profile).resolve()),
    }


def proven_pre_submit_profile_copy_ebusy(state_path: Path) -> dict[str, Any] | None:
    """Prove Oracle failed while copying its profile, before it could open ChatGPT."""
    state = load_state(state_path)
    if str(state.get("session_authority") or "") not in {"pre_submit", "submitted_unknown"}:
        return None
    if state.get("terminal_harvested") is True or _state_has_conversation_url(state):
        return None
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    copy_profile = Path(str(profile.get("copy_profile") or ""))
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    browser_temp = Path(str(artifacts.get("browser_temp") or ""))
    output = Path(str(artifacts.get("output") or ""))
    stdout_record = _artifact_bytes(state, "stdout")
    stderr_record = _artifact_bytes(state, "stderr")
    if (
        not str(copy_profile)
        or not str(browser_temp)
        or output_is_nonempty(output)
        or stdout_record is None
        or stderr_record is None
    ):
        return None
    _, stdout_bytes = stdout_record
    _, stderr_bytes = stderr_record
    text = (stdout_bytes + b"\n" + stderr_bytes).decode("utf-8", errors="replace")
    if "https://chatgpt.com/c/" in text.casefold():
        return None
    match = ORACLE_PROFILE_COPY_EBUSY_RE.search(text)
    if match is None:
        return None
    source = Path(match.group("source"))
    destination = Path(match.group("destination"))
    expected_source = copy_profile / "Default" / "Network" / "Cookies"
    if (
        source.resolve() != expected_source.resolve()
        or not is_within(browser_temp.resolve(), destination.resolve())
        or destination.name.casefold() != "cookies"
    ):
        return None
    return {
        "schema": "codex.chatgpt.oracle-pre-submit-host-failure/v1",
        "code": "ORACLE_PROFILE_COPY_EBUSY_PRELAUNCH_FAILED",
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
        "failure_reason": "oracle-profile-copy-ebusy",
        "copy_source": str(source.resolve()),
        "copy_destination": str(destination.resolve()),
    }


def proven_pre_submit_thinking_time_failure(state_path: Path) -> dict[str, Any] | None:
    """Prove the strict effort selector failed before Oracle could send a prompt."""
    state = load_state(state_path)
    if str(state.get("session_authority") or "") not in {"pre_submit", "submitted_unknown"}:
        return None
    if state.get("terminal_harvested") is True or _state_has_conversation_url(state):
        return None
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    stdout_record = _artifact_bytes(state, "stdout")
    stderr_record = _artifact_bytes(state, "stderr")
    if output_is_nonempty(output) or stdout_record is None or stderr_record is None:
        return None
    _, stdout_bytes = stdout_record
    _, stderr_bytes = stderr_record
    combined = (stdout_bytes + b"\n" + stderr_bytes).decode("utf-8", errors="replace")
    if CHATGPT_CONVERSATION_URL_RE.search(combined):
        return None
    match = ORACLE_THINKING_TIME_PRE_SUBMIT_RE.search(combined)
    if match is None:
        return None
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    requested_level = next(
        value.strip()
        for value in (
            match.group("requested_status"),
            match.group("requested_unknown"),
            match.group("requested_unavailable"),
        )
        if value is not None
    )
    is_current_pro = (
        is_pro_transport(str(state.get("transport") or ""))
        and str(profile.get("model") or "").casefold() == "gpt-5.6-sol"
        and str(profile.get("model_strategy") or "").casefold() == "select"
        and str(profile.get("thinking_time") or "").casefold() == PRO_THINKING_TIME
    )
    # A current Pro launch may only settle this exact pre-submit refusal when
    # Oracle itself says it requested Pro.  A different confirmed tier is the
    # failure we must preserve fail-closed.  Heavy-era receipts retain the
    # older same-level rule.
    if is_current_pro and requested_level.casefold() != PRO_THINKING_TIME:
        return None
    if not is_current_pro and requested_level.casefold() != match.group("required").strip().casefold():
        return None
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    if not locator:
        return None
    return {
        "schema": "codex.chatgpt.oracle-pre-submit-ui-failure/v1",
        "code": (
            "ORACLE_PRO_TIER_NOT_SELECTED"
            if is_current_pro else "ORACLE_THINKING_TIME_PRE_SUBMIT_FAILED"
        ),
        "oracle_locator": locator,
        "requested_level": requested_level,
        "required_level": match.group("required").strip(),
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
        "failure_reason": (
            "oracle-pro-tier-not-selected"
            if is_current_pro else "oracle-thinking-time-selection-unverified"
        ),
    }


def proven_pre_submit_model_switcher_failure(state_path: Path) -> dict[str, Any] | None:
    """Prove Oracle failed selecting a model before it could send a prompt.

    This intentionally accepts only Oracle's exact model-switcher/no-cookie
    diagnostic, with both output and conversation evidence absent.  A generic
    browser error, a recorded conversation URL, or any durable output remains
    submitted-unknown and therefore keeps the project lock fail-closed.
    """
    state = load_state(state_path)
    if str(state.get("session_authority") or "") not in {"pre_submit", "submitted_unknown"}:
        return None
    if state.get("terminal_harvested") is True or _state_has_conversation_url(state):
        return None
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    stdout_record = _artifact_bytes(state, "stdout")
    stderr_record = _artifact_bytes(state, "stderr")
    if output_is_nonempty(output) or stdout_record is None or stderr_record is None:
        return None
    _, stdout_bytes = stdout_record
    _, stderr_bytes = stderr_record
    combined = (stdout_bytes + b"\n" + stderr_bytes).decode("utf-8", errors="replace")
    if CHATGPT_CONVERSATION_URL_RE.search(combined):
        return None
    if ORACLE_MODEL_SWITCHER_PRE_SUBMIT_RE.search(combined) is None:
        return None
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    if not locator:
        return None
    return {
        "schema": "codex.chatgpt.oracle-pre-submit-ui-failure/v1",
        "code": "ORACLE_MODEL_SWITCHER_PRE_SUBMIT_FAILED",
        "oracle_locator": locator,
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
        "failure_reason": "oracle-model-switcher-no-cookies",
    }


def proven_pre_submit_failure(state_path: Path) -> dict[str, Any] | None:
    return (
        proven_pre_submit_rejection(state_path)
        or proven_pre_submit_copy_profile_manual_login_conflict(state_path)
        or proven_pre_submit_profile_copy_rsync_missing(state_path)
        or proven_pre_submit_profile_copy_ebusy(state_path)
        or proven_pre_submit_thinking_time_failure(state_path)
        or proven_pre_submit_model_switcher_failure(state_path)
        or proven_pre_submit_cdp_disconnect(state_path)
        or proven_pre_submit_host_failure(state_path)
        or proven_user_confirmed_no_submission(state_path)
    )


def settle_proven_pre_submit_rejection(state_path: Path) -> dict[str, Any] | None:
    """Correct submitted_unknown only when exact Oracle stdout proves no send occurred."""
    evidence = proven_pre_submit_rejection(state_path)
    if evidence is None:
        return None
    payload = load_state(state_path)
    payload.update({
        "status": "attention_required",
        "exit_code": int(payload.get("exit_code") or 1),
        "session_authority": "pre_submit",
        "terminal_harvested": False,
        "artifact_sha256": None,
        "transport_status": "rejected_pre_submit",
        "task_outcome": "pending",
        "task_outcome_reason": "oracle-global-prompt-duplicate",
        "pre_submit_rejection": evidence,
    })
    write_json_atomic(state_path, payload)
    return payload


def settle_proven_pre_submit_failure(state_path: Path) -> dict[str, Any] | None:
    """Settle either supported immutable proof without preserving a false lock."""
    rejection = proven_pre_submit_rejection(state_path)
    if rejection is not None:
        return settle_proven_pre_submit_rejection(state_path)
    confirmed = proven_user_confirmed_no_submission(state_path)
    if confirmed is not None:
        return load_state(state_path)
    evidence = proven_pre_submit_copy_profile_manual_login_conflict(state_path)
    if evidence is None:
        evidence = proven_pre_submit_profile_copy_rsync_missing(state_path)
    if evidence is None:
        evidence = proven_pre_submit_host_failure(state_path)
    if evidence is None:
        evidence = proven_pre_submit_profile_copy_ebusy(state_path)
    if evidence is None:
        evidence = proven_pre_submit_thinking_time_failure(state_path)
    if evidence is None:
        evidence = proven_pre_submit_model_switcher_failure(state_path)
    if evidence is None:
        evidence = proven_pre_submit_cdp_disconnect(state_path)
    if evidence is None:
        return None
    payload = load_state(state_path)
    payload.update({
        "status": "attention_required",
        "exit_code": int(payload.get("exit_code") or 1),
        "session_authority": "pre_submit",
        "terminal_harvested": False,
        "artifact_sha256": None,
        "transport_status": "failed_pre_submit",
        "task_outcome": (
            "not_executed"
            if evidence["code"] in {
                "ORACLE_PROFILE_COPY_EBUSY_PRELAUNCH_FAILED",
                "ORACLE_PROFILE_COPY_RSYNC_PRELAUNCH_FAILED",
                "ORACLE_THINKING_TIME_PRE_SUBMIT_FAILED",
                "ORACLE_PRO_TIER_NOT_SELECTED",
                "PROJECT_SESSION_STILL_LIVE_PRELAUNCH_FAILED",
                "ORACLE_LAUNCH_FLAGS_MUTUALLY_EXCLUSIVE_PRELAUNCH_FAILED",
                "ORACLE_MANUAL_LOGIN_PROFILE_UNINITIALIZED_PRELAUNCH_FAILED",
                "ORACLE_CDP_DISCONNECT_PRE_SUBMIT_FAILED",
                "ORACLE_SESSION_METADATA_RENAME_PRELAUNCH_FAILED",
            }
            else "pending"
        ),
        "task_outcome_reason": (
            "oracle-profile-copy-ebusy-pre-submit"
            if evidence["code"] == "ORACLE_PROFILE_COPY_EBUSY_PRELAUNCH_FAILED"
            else "oracle-profile-copy-rsync-pre-submit"
            if evidence["code"] == "ORACLE_PROFILE_COPY_RSYNC_PRELAUNCH_FAILED"
            else "oracle-thinking-time-pre-submit"
            if evidence["code"] == "ORACLE_THINKING_TIME_PRE_SUBMIT_FAILED"
            else "oracle-pro-tier-not-selected-pre-submit"
            if evidence["code"] == "ORACLE_PRO_TIER_NOT_SELECTED"
            else "project-session-still-live-pre-submit"
            if evidence["code"] == "PROJECT_SESSION_STILL_LIVE_PRELAUNCH_FAILED"
            else "oracle-launch-flags-mutually-exclusive-pre-submit"
            if evidence["code"] == "ORACLE_LAUNCH_FLAGS_MUTUALLY_EXCLUSIVE_PRELAUNCH_FAILED"
            else "oracle-manual-login-profile-uninitialized-pre-submit"
            if evidence["code"] == "ORACLE_MANUAL_LOGIN_PROFILE_UNINITIALIZED_PRELAUNCH_FAILED"
            else "oracle-cdp-disconnect-pre-submit"
            if evidence["code"] == "ORACLE_CDP_DISCONNECT_PRE_SUBMIT_FAILED"
            else "oracle-session-metadata-rename-pre-submit"
            if evidence["code"] == "ORACLE_SESSION_METADATA_RENAME_PRELAUNCH_FAILED"
            else "oracle-model-switcher-pre-submit"
            if evidence["code"] == "ORACLE_MODEL_SWITCHER_PRE_SUBMIT_FAILED"
            else "prelaunch-host-failure"
        ),
        "pre_submit_failure": evidence,
    })
    write_json_atomic(state_path, payload)
    return payload


def settle_pre_submit_session_absent(
    state_path: Path,
    *,
    locator: str,
    recovery_stdout: Path,
    recovery_stderr: Path,
) -> dict[str, Any] | None:
    """Keep pre-submit authority when exact recovery proves no Oracle session exists."""
    payload = load_state(state_path)
    if str(payload.get("session_authority") or "") != "pre_submit":
        return None
    if _state_has_conversation_url(payload):
        return None
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    if str(output) and output_is_nonempty(output):
        return None
    chunks: list[bytes] = []
    for path in (recovery_stdout, recovery_stderr):
        try:
            chunks.append(path.read_bytes())
        except OSError:
            chunks.append(b"")
    combined = b"\n".join(chunks)
    try:
        text = combined.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    matches = [match.group("locator") for match in ORACLE_NO_SESSION_RE.finditer(text)]
    if matches != [locator]:
        return None
    evidence = {
        "schema": "codex.chatgpt.oracle-pre-submit-session-absence/v1",
        "code": "ORACLE_EXACT_SESSION_NOT_FOUND",
        "oracle_locator": locator,
        "recovery_sha256": hashlib.sha256(combined).hexdigest(),
        "output_absent": True,
        "conversation_url_absent": True,
    }
    payload.update({
        "status": "attention_required",
        "exit_code": int(payload.get("exit_code") or 1),
        "session_authority": "pre_submit",
        "terminal_harvested": False,
        "artifact_sha256": None,
        "transport_status": "not_submitted",
        "task_outcome": "pending",
        "task_outcome_reason": "exact-session-absent-before-submit",
        "pre_submit_session_absence": evidence,
    })
    write_json_atomic(state_path, payload)
    return payload


def proven_pre_submit_session_absence(state_path: Path) -> dict[str, Any] | None:
    """Revalidate exact-session absence without granting post-submit authority."""
    payload = load_state(state_path)
    evidence = payload.get("pre_submit_session_absence")
    if (
        not isinstance(evidence, dict)
        or str(payload.get("session_authority") or "") != "pre_submit"
        or evidence.get("schema")
        != "codex.chatgpt.oracle-pre-submit-session-absence/v1"
        or evidence.get("code") != "ORACLE_EXACT_SESSION_NOT_FOUND"
        or evidence.get("output_absent") is not True
        or evidence.get("conversation_url_absent") is not True
        or _state_has_conversation_url(payload)
        or _settlement_logs_have_conversation_url(state_path)
    ):
        return None
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    output = Path(str(artifacts.get("output") or ""))
    if str(output) and output_is_nonempty(output):
        return None
    oracle = payload.get("oracle") if isinstance(payload.get("oracle"), dict) else {}
    locator = str(evidence.get("oracle_locator") or "")
    if (
        not locator
        or locator != str(oracle.get("session_locator") or "")
        or locator != str(oracle.get("slug") or "")
    ):
        return None
    expected_sha256 = str(evidence.get("recovery_sha256") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        return None
    run_dir = state_path.parent
    for recovery_stdout in sorted(run_dir.glob("recovery-*-stdout.log"), key=lambda item: item.name):
        recovery_stderr = recovery_stdout.with_name(
            recovery_stdout.name.replace("-stdout.log", "-stderr.log")
        )
        try:
            if recovery_stdout.is_symlink() or recovery_stderr.is_symlink():
                continue
            combined = b"\n".join((recovery_stdout.read_bytes(), recovery_stderr.read_bytes()))
            text = combined.decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        matches = [match.group("locator") for match in ORACLE_NO_SESSION_RE.finditer(text)]
        if hashlib.sha256(combined).hexdigest() == expected_sha256 and matches == [locator]:
            return {
                **evidence,
                "recovery_stdout": str(recovery_stdout),
                "recovery_stderr": str(recovery_stderr),
            }
    return None


def resolve_lifecycle(state: dict[str, Any], *, output_is_present: bool | None = None) -> dict[str, Any]:
    """Collapse the stored run record into one bounded lifecycle verdict.

    Authority order is fixed and single-sourced: exact terminal web evidence
    outranks a durable stored artifact, which outranks the local ledger.  PIDs,
    heartbeats, locks and poll results are diagnostics and never appear here.
    """
    status = str(state.get("status") or "")
    authority = str(state.get("session_authority") or "")
    harvested = state.get("terminal_harvested") is True
    outcome = str(state.get("task_outcome") or "")
    if output_is_present is None:
        artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
        output_path = Path(str(artifacts.get("output") or ""))
        has_output = bool(str(output_path)) and output_is_nonempty(output_path)
    else:
        has_output = bool(output_is_present)

    if status == "abandoned":
        return {"lifecycle": "abandoned", "authority_source": "explicit-abandonment"}
    # 1. Exact terminal web evidence.
    if authority == "settled_executed" and outcome == "executed":
        return {"lifecycle": "complete", "authority_source": "user-confirmed-execution-settlement"}
    if authority == "terminal" and harvested and has_output:
        if outcome == "not_executed":
            return {"lifecycle": "needs_attention", "authority_source": "exact-terminal-evidence"}
        return {"lifecycle": "complete", "authority_source": "exact-terminal-evidence"}
    # 2. Durable stored artifact, including ledgers written before authority
    #    tracking existed.  A finished answer on disk is not a defect.
    if has_output and status == "complete":
        if outcome == "not_executed":
            return {"lifecycle": "needs_attention", "authority_source": "durable-artifact"}
        return {"lifecycle": "complete", "authority_source": "durable-artifact"}
    # 3. An owned session that is still live keeps running regardless of a
    #    local nonzero exit; only web state may end it.
    if authority in {"live", "submitted_unknown", "terminal_observed"}:
        return {"lifecycle": "running", "authority_source": "exact-session-ownership"}
    # 4. Local ledger, lowest authority.
    if status == "complete":
        # A ledger that claims completion without a durable artifact has not
        # proven anything.  Never let the weakest authority assert completion.
        return {"lifecycle": "needs_attention", "authority_source": "local-ledger"}
    return {
        "lifecycle": _STATUS_TO_LIFECYCLE.get(status, "needs_attention"),
        "authority_source": "local-ledger",
    }


TASK_OUTCOME_RE = re.compile(
    r"TASK_OUTCOME:\s*(EXECUTED|NOT_EXECUTED|BLOCKED)",
    re.IGNORECASE,
)

MARKDOWN_HTTP_REFERENCE_DEFINITION_RE = re.compile(
    r"\[[^\]\r\n]+\]:[ \t]+(?:<https?://[^>\s]+>|https?://\S+)"
    r"(?:[ \t]+(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|\([^\)\r\n]*\)))?[ \t]*",
    re.IGNORECASE,
)

_RENDERED_FILE_REFERENCE = r"(?:(?:[\w.-]+/)*[\w.-]+\.[\w.-]+)"
_RENDERED_DIRECTORY_REFERENCE = r"(?:(?:[\w.-]+/)+)"
RENDERED_REFERENCE_FOOTER_RE = re.compile(
    rf"^(?:"
    rf"{_RENDERED_FILE_REFERENCE}(?:;\s*{_RENDERED_FILE_REFERENCE})*"
    rf"|{_RENDERED_FILE_REFERENCE}, section \"[^\"\r\n]{{1,200}}\""
    rf"|{_RENDERED_FILE_REFERENCE}; checksum-verified [\w .-]{{1,200}} "
    rf"inputs under {_RENDERED_DIRECTORY_REFERENCE}"
    rf")\.[ \t]↩$"
)


def _only_bounded_reference_definitions(lines: list[str]) -> bool:
    return all(
        not line.strip()
        or MARKDOWN_HTTP_REFERENCE_DEFINITION_RE.fullmatch(line.strip()) is not None
        for line in lines
    )


def _only_bounded_rendered_reference_footers(lines: list[str]) -> bool:
    nonempty = [line.strip() for line in lines if line.strip()]
    return len(nonempty) <= 32 and all(
        len(line) <= 512 and RENDERED_REFERENCE_FOOTER_RE.fullmatch(line) is not None
        for line in nonempty
    )


def classify_task_outcome(path: Path, *, contract: str, transport: str) -> str:
    if is_attachment_transport(transport):
        return "not_applicable"
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return "unknown"
    # The v1 marker is normally the final nonempty line. Some provider renderers
    # move Markdown link definitions below it. Accept only that bounded,
    # non-semantic appendix: one marker in the entire artifact followed solely
    # by single-line HTTP(S) reference definitions and blank lines.
    if len(TASK_OUTCOME_RE.findall(text)) != 1:
        return "unknown" if contract == "v1" else "legacy_unclassified"
    lines = text.splitlines()
    marker_lines = [
        (index, marker)
        for index, line in enumerate(lines)
        if (marker := TASK_OUTCOME_RE.fullmatch(line.strip())) is not None
    ]
    if len(marker_lines) == 1:
        index, marker = marker_lines[0]
        footer = lines[index + 1 :]
        if _only_bounded_reference_definitions(footer) or _only_bounded_rendered_reference_footers(footer):
            return marker.group(1).casefold()
    return "unknown" if contract == "v1" else "legacy_unclassified"


def unresolved_project_sessions(
    run_root: Path,
    project_root: Path,
    *,
    parallel_parent_id: str | None = None,
    exclude_run_id: str | None = None,
    source_thread_id: str | None = None,
) -> list[dict[str, str]]:
    """Return exact submitted sessions that still own this project.

    A local Oracle exit is not web-terminal authority.  Ownership therefore
    survives ``running``/``attention_required`` host states until exact-session
    recovery records terminal completion.  Parallel children from the same
    persisted parent are allowed to coexist; a different parent is not.
    """
    root = run_root.expanduser().resolve()
    expected_project = str(project_root.expanduser().resolve()).casefold()
    expected_parent = str(parallel_parent_id or "").strip().casefold()
    expected_thread = str(source_thread_id or "").strip().casefold() or None
    if expected_thread is not None and SOURCE_THREAD_ID_RE.fullmatch(expected_thread) is None:
        raise OracleStateError("SOURCE_THREAD_ID_INVALID", "source_thread_id must be one Codex task UUID")
    active_authorities = {"submitted_unknown", "live", "terminal_observed"}
    owners: list[dict[str, str]] = []
    if not root.is_dir():
        return owners
    for candidate in sorted(root.glob("*/state.json"), key=lambda item: str(item)):
        try:
            payload = load_state(candidate)
        except (OSError, OracleStateError):
            continue
        run_id = str(payload.get("run_id") or "")
        if run_id == exclude_run_id or str(payload.get("project_root") or "").casefold() != expected_project:
            continue
        authority = str(payload.get("session_authority") or "").strip().casefold()
        owner_thread = source_thread_id_from_state(payload)
        settlement_artifact = candidate.parent / "user-confirmed-no-submission.json"
        execution_settlement_artifact = candidate.parent / "user-confirmed-execution-ended.json"
        settlement_derived = (
            "user_confirmed_no_submission" in payload
            or str(payload.get("transport_status") or "") == "not_submitted_user_confirmed"
            or str(payload.get("task_outcome_reason") or "")
            == "user-confirmed-no-submission-after-prompt-timeout"
            or settlement_artifact.exists()
        )
        execution_settlement_derived = (
            "user_confirmed_execution_ended" in payload
            or str(payload.get("transport_status") or "")
            == "post_submit_provider_delivery_timeout_settled"
            or execution_settlement_artifact.exists()
        )
        invalid_settlement = False
        if (
            authority == "pre_submit"
            and settlement_derived
            and proven_user_confirmed_no_submission(candidate) is None
        ):
            # A missing or changed settlement artifact revokes the release and
            # restores fail-closed ownership before any new submission.
            authority = "submitted_unknown"
            invalid_settlement = True
        if (
            authority == "settled_executed"
            and execution_settlement_derived
            and proven_user_confirmed_execution_ended(candidate) is None
        ):
            authority = "live"
            invalid_settlement = True
        # Legacy running records fail closed because the provider may still be
        # active. Legacy attention-required records predate explicit session
        # authority and must not become permanent project locks; new runs
        # persist submitted_unknown/live explicitly before reaching attention.
        if not authority and str(payload.get("status") or "").casefold() == "running":
            authority = "submitted_unknown"
        if authority not in active_authorities:
            continue
        # A task-scoped caller can only block on its own exact task owner.
        # Bound foreign tasks and old unbound runs are reported elsewhere but
        # are never silently adopted as this task's recoverable authority.
        if expected_thread is not None:
            if owner_thread != expected_thread:
                continue
        owner_parent = str(payload.get("parallel_parent_id") or "").strip().casefold()
        if expected_parent and owner_parent == expected_parent and not invalid_settlement:
            continue
        owners.append({
            "run_id": run_id,
            "session_locator": str((payload.get("oracle") or {}).get("session_locator") or ""),
            "session_authority": authority,
            "state_path": str(candidate),
            "source_thread_id": owner_thread or "legacy-unbound",
            "ownership_scope": "same-task" if owner_thread == expected_thread else "legacy-unbound",
        })
    return owners


def foreign_project_sessions(
    run_root: Path,
    project_root: Path,
    *,
    source_thread_id: str,
    exclude_run_id: str | None = None,
) -> list[dict[str, str]]:
    """Describe, but never grant authority over, another task's live sessions."""
    caller = str(source_thread_id or "").strip().casefold()
    if SOURCE_THREAD_ID_RE.fullmatch(caller) is None:
        raise OracleStateError("SOURCE_THREAD_ID_INVALID", "source_thread_id must be one Codex task UUID")
    root = run_root.expanduser().resolve()
    expected_project = str(project_root.expanduser().resolve()).casefold()
    rows: list[dict[str, str]] = []
    if not root.is_dir():
        return rows
    for candidate in sorted(root.glob("*/state.json"), key=lambda item: str(item)):
        try:
            payload = load_state(candidate)
        except (OSError, OracleStateError):
            continue
        if str(payload.get("run_id") or "") == exclude_run_id or str(payload.get("project_root") or "").casefold() != expected_project:
            continue
        authority = str(payload.get("session_authority") or "").strip().casefold()
        if authority not in {"submitted_unknown", "live", "terminal_observed"}:
            continue
        owner = source_thread_id_from_state(payload)
        if owner == caller:
            continue
        rows.append({
            "run_id": str(payload.get("run_id") or ""),
            "session_locator": str((payload.get("oracle") or {}).get("session_locator") or ""),
            "session_authority": authority,
            "source_thread_id": owner or "legacy-unbound",
            "classification": "FOREIGN_TASK_SESSION",
            "state_path": str(candidate),
        })
    return rows


def write_transcript(layout: RunLayout) -> None:
    chunks = []
    for source in (layout.stdout_path, layout.stderr_path):
        try:
            data = source.read_bytes()
        except OSError:
            data = b""
        if data:
            chunks.append(data.rstrip() + b"\n")
    if layout.output_path.is_file():
        data = layout.output_path.read_bytes()
        if data:
            chunks.append(data.rstrip() + b"\n")
    layout.transcript_path.write_bytes(b"".join(chunks))


def windows_subprocess_kwargs(*, platform_name: str | None = None) -> dict[str, Any]:
    if (os.name if platform_name is None else platform_name) != "nt":
        return {}
    kwargs: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW)}
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs


def mutex_wait_succeeded(wait_result: int) -> bool:
    return wait_result in {WAIT_OBJECT_0, WAIT_ABANDONED}


def submit_mutex_name(project_root: Path, *, source_thread_id: str | None = None) -> str:
    scope = str(source_thread_id or "legacy-unbound").strip().casefold()
    digest = hashlib.sha256(f"{project_root!s}".casefold().encode("utf-8") + b"\0" + scope.encode("utf-8")).hexdigest()[:32]
    return f"Local\\codexpro-oracle-submit-{digest}"


def recovery_mutex_name(run_dir: Path) -> str:
    """Return a mutex name scoped to one immutable Oracle run directory."""
    digest = hashlib.sha256(str(run_dir).casefold().encode("utf-8")).hexdigest()[:32]
    return f"Local\\codexpro-oracle-recovery-{digest}"


class WindowsSubmitMutex(AbstractContextManager["WindowsSubmitMutex"]):
    def __init__(self, name: str, timeout_seconds: float):
        self.name, self.timeout_seconds, self.handle, self.acquired = name, timeout_seconds, None, False

    def __enter__(self) -> "WindowsSubmitMutex":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise OracleStateError("SUBMIT_MUTEX_CREATE_FAILED", "Windows submit mutex could not be created")
        self.handle = int(handle)
        result = int(kernel32.WaitForSingleObject(handle, max(1, int(self.timeout_seconds * 1000))))
        if not mutex_wait_succeeded(result):
            kernel32.CloseHandle(handle)
            self.handle = None
            raise OracleStateError("SUBMIT_MUTEX_TIMEOUT" if result == WAIT_TIMEOUT else "SUBMIT_MUTEX_WAIT_FAILED", "project submit mutex could not be acquired")
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            if self.acquired:
                kernel32.ReleaseMutex(ctypes.c_void_p(self.handle))
            kernel32.CloseHandle(ctypes.c_void_p(self.handle))
        self.handle, self.acquired = None, False
        return None


class ThreadSubmitMutex(AbstractContextManager["ThreadSubmitMutex"]):
    def __init__(self, name: str, timeout_seconds: float):
        self.name, self.timeout_seconds, self.lock = name, timeout_seconds, None

    def __enter__(self) -> "ThreadSubmitMutex":
        with _THREAD_MUTEXES_GUARD:
            lock = _THREAD_MUTEXES.setdefault(self.name, threading.Lock())
        if not lock.acquire(timeout=self.timeout_seconds):
            raise OracleStateError("SUBMIT_MUTEX_TIMEOUT", "project submit mutex could not be acquired")
        self.lock = lock
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.lock is not None:
            self.lock.release()
        self.lock = None
        return None


class FileSubmitMutex(AbstractContextManager["FileSubmitMutex"]):
    def __init__(self, name: str, timeout_seconds: float):
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
        self.path = Path(tempfile.gettempdir()) / f"codexpro-oracle-submit-{digest}.lock"
        self.timeout_seconds = timeout_seconds
        self.handle = None

    def __enter__(self) -> "FileSubmitMutex":
        import fcntl

        self.handle = self.path.open("a+b")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise OracleStateError("SUBMIT_MUTEX_TIMEOUT", "project submit mutex could not be acquired")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.handle is not None:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
        self.handle = None
        return None


def project_submit_mutex(
    project_root: Path,
    *,
    timeout_seconds: float,
    platform_name: str | None = None,
    source_thread_id: str | None = None,
) -> AbstractContextManager[Any]:
    name = submit_mutex_name(project_root, source_thread_id=source_thread_id)
    platform = os.name if platform_name is None else platform_name
    if platform == "nt":
        return WindowsSubmitMutex(name, timeout_seconds)
    return FileSubmitMutex(name, timeout_seconds)


def exact_run_recovery_mutex(
    run_dir: Path,
    *,
    timeout_seconds: float,
    platform_name: str | None = None,
) -> AbstractContextManager[Any]:
    """Serialize recovery writers without re-entering the submission mutex.

    Recovery commands are prompt-free and bound to one persisted run/slug.  A
    stale original observer can legitimately keep the project submission mutex
    while the provider has already become terminal, so exact recovery needs its
    own lock.  The unresolved run state continues to block every fresh submit.
    """
    name = recovery_mutex_name(run_dir)
    platform = os.name if platform_name is None else platform_name
    if platform == "nt":
        return WindowsSubmitMutex(name, timeout_seconds)
    return FileSubmitMutex(name, timeout_seconds)


def command_for_display(command: Sequence[str]) -> list[str]:
    return [str(item) for item in command]
