from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

STATE_PATH = Path(__file__).resolve().with_name("chatgpt_oracle_state.py")
COMPAT_PATH = Path(__file__).resolve().with_name("chatgpt_oracle_compat.py")
DEVSPACE_COMPAT_PATH = Path(__file__).resolve().with_name("chatgpt_devspace_compat.py")
DEVSPACE_PREFLIGHT_PATH = Path(__file__).resolve().with_name("chatgpt_devspace_preflight.py")


def load_state_module():
    spec = importlib.util.spec_from_file_location("chatgpt_oracle_state_runtime", STATE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Oracle state module unavailable: {STATE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STATE = load_state_module()


def load_compat_module():
    spec = importlib.util.spec_from_file_location("chatgpt_oracle_compat_runtime", COMPAT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Oracle compatibility module unavailable: {COMPAT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


COMPAT = load_compat_module()


def load_devspace_compat_module():
    spec = importlib.util.spec_from_file_location(
        "chatgpt_devspace_compat_runtime",
        DEVSPACE_COMPAT_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"DevSpace compatibility module unavailable: {DEVSPACE_COMPAT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DEVSPACE_COMPAT = load_devspace_compat_module()


def load_devspace_preflight_module():
    spec = importlib.util.spec_from_file_location(
        "chatgpt_devspace_preflight_runtime",
        DEVSPACE_PREFLIGHT_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"DevSpace preflight module unavailable: {DEVSPACE_PREFLIGHT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DEVSPACE_PREFLIGHT = load_devspace_preflight_module()


class OracleRunError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}

    def envelope(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": str(self), "evidence": self.evidence}}


def build_oracle_argv(
    config,
    layout,
    prompt: str,
    *,
    cdp_port: int | None = None,
    followup_parent_slug: str | None = None,
) -> list[str]:
    lifecycle_args = [] if "--browser-hide-window" in config.oracle_args else ["--browser-hide-window"]
    # This is the browser observer's window, not a run termination deadline.
    # If it expires, the exact slug retains ownership and the harness continues
    # live recovery.  The default is aligned with the observed provider limit;
    # the separate 80-minute status audit never changes session authority.
    answer_budget_seconds = int(getattr(config, "web_answer_budget_seconds", 6000))
    answer_timeout_value = (
        STATE.DEFAULT_BROWSER_ANSWER_TIMEOUT
        if answer_budget_seconds == 6000
        else f"{answer_budget_seconds}s"
    )
    answer_timeout_args = (
        []
        if any(
            item == "--browser-timeout" or item.startswith("--browser-timeout=")
            for item in config.oracle_args
        )
        else ["--browser-timeout", answer_timeout_value]
    )
    command = [
        *config.oracle_command,
        "--engine", "browser",
        "--model", config.model,
        "--browser-model-strategy", config.model_strategy,
        "--browser-thinking-time", config.thinking_time,
        "--browser-research", config.research,
        "--browser-archive", config.archive,
        *lifecycle_args,
        *answer_timeout_args,
        *(["--browser-port", str(cdp_port)] if cdp_port is not None else []),
        *config.oracle_args,
        *(["--followup", followup_parent_slug] if followup_parent_slug is not None else []),
        "--slug", layout.slug,
        "--prompt", prompt,
        "--write-output", str(layout.output_path),
    ]
    if STATE.is_attachment_transport(config.transport):
        attachment_args: list[str] = []
        for path in config.attachments:
            attachment_args.extend(["--file", str(path)])
        command[command.index("--slug"):command.index("--slug")] = [
            "--browser-attachments", "always", *attachment_args,
        ]
    if config.copy_profile is not None:
        command[command.index("--slug"):command.index("--slug")] = ["--copy-profile", str(config.copy_profile)]
    if not STATE.is_pro_transport(config.transport) and any(
        item == "--file" or item.startswith("--file=") or item == "-f" for item in command
    ):
        raise OracleRunError("FILE_TRANSPORT_FORBIDDEN", "general GPT browser runs must not use --file")
    return command


_BROWSER_TIMEOUT_RE = re.compile(r"^(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<unit>ms|s|m|h)?$", re.IGNORECASE)
MAX_BROWSER_OBSERVER_SECONDS = 7 * 24 * 60 * 60
# Keep attachment-only transport below the browser data-transfer ceiling that
# is validated for both the promoted Oracle release and the rollback LKG.
# Context-packet construction may retain its broader configured envelope.
ORACLE_ATTACHMENT_MAX_BYTES = 1024 * 1024


def validate_oracle_attachment_sizes(config) -> None:
    """Reject attachments outside the validated Oracle browser envelope."""
    if not STATE.is_attachment_transport(config.transport):
        return
    oversized = [
        {"path": str(path), "size_bytes": path.stat().st_size, "limit_bytes": ORACLE_ATTACHMENT_MAX_BYTES}
        for path in config.attachments
        if path.stat().st_size > ORACLE_ATTACHMENT_MAX_BYTES
    ]
    if oversized:
        raise OracleRunError(
            "ORACLE_ATTACHMENT_SIZE_PRELAUNCH_FAILED",
            "Oracle browser attachments must not exceed the validated 1 MiB per-file envelope",
            {"limit_bytes": ORACLE_ATTACHMENT_MAX_BYTES, "attachments": oversized},
        )


def browser_observer_timeout_seconds(config, argv: Sequence[str]) -> float:
    """Validate the browser observer window without treating it as terminal."""
    values: list[str] = []
    for index, item in enumerate(argv):
        if item == "--browser-timeout":
            if index + 1 >= len(argv):
                raise OracleRunError("BROWSER_TIMEOUT_INVALID", "--browser-timeout requires a value")
            values.append(str(argv[index + 1]))
        elif item.startswith("--browser-timeout="):
            values.append(item.split("=", 1)[1])
    if len(values) != 1:
        raise OracleRunError(
            "BROWSER_TIMEOUT_INVALID",
            "Oracle runs require exactly one browser timeout",
            {"values": values},
        )
    match = _BROWSER_TIMEOUT_RE.fullmatch(values[0].strip())
    if match is None:
        raise OracleRunError(
            "BROWSER_TIMEOUT_INVALID",
            "browser timeout must be a positive ms/s/m/h duration",
            {"value": values[0]},
        )
    value = float(match.group("value"))
    unit = (match.group("unit") or "ms").casefold()
    multiplier = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    answer_seconds = value * multiplier
    if (
        not math.isfinite(value)
        or not math.isfinite(answer_seconds)
        or answer_seconds <= 0
        or answer_seconds > MAX_BROWSER_OBSERVER_SECONDS
    ):
        raise OracleRunError(
            "BROWSER_TIMEOUT_OUT_OF_RANGE",
            "browser observation window must be finite and at most seven days",
            {"value": values[0]},
        )
    return answer_seconds


def wait_for_oracle_process(
    process: Any,
    status_audit_seconds: float,
    *,
    on_status_audit: Callable[[int], None] | None = None,
    terminal_harvest_probe: Callable[[], bool] | None = None,
    terminate_owned_process: Callable[[Any], None] | None = None,
    runtime_identity_probe: Callable[[], Any] | None = None,
    terminal_probe_interval_seconds: float = 1.0,
) -> int:
    """Wait for one exact Oracle process; time alone never ends the wait."""
    if not math.isfinite(status_audit_seconds) or status_audit_seconds <= 0:
        raise OracleRunError(
            "STATUS_AUDIT_INTERVAL_INVALID",
            "status audit interval must be a positive finite duration",
        )
    if terminal_harvest_probe is not None and (
        not math.isfinite(terminal_probe_interval_seconds)
        or terminal_probe_interval_seconds <= 0
    ):
        raise OracleRunError(
            "TERMINAL_PROBE_INTERVAL_INVALID",
            "terminal harvest probe interval must be a positive finite duration",
        )
    watcher_stop = threading.Event()
    watcher: threading.Thread | None = None
    if terminal_harvest_probe is not None and terminate_owned_process is not None:
        def watch_terminal_harvest() -> None:
            while not watcher_stop.is_set():
                try:
                    if runtime_identity_probe is not None:
                        runtime_identity_probe()
                    if terminal_harvest_probe():
                        terminate_owned_process(process)
                        return
                except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                    # A partial atomic-state observation is never authority to
                    # terminate the exact observer. Retry without changing state.
                    pass
                watcher_stop.wait(terminal_probe_interval_seconds)

        watcher = threading.Thread(
            target=watch_terminal_harvest,
            name="oracle-terminal-harvest-watcher",
            daemon=True,
        )
        watcher.start()
    audit_count = 0
    try:
        while True:
            try:
                return int(process.wait(timeout=status_audit_seconds))
            except subprocess.TimeoutExpired:
                poll = getattr(process, "poll", None)
                if callable(poll):
                    raced_exit_code = poll()
                    if raced_exit_code is not None:
                        return int(raced_exit_code)
                audit_count += 1
                if on_status_audit is not None:
                    on_status_audit(audit_count)
    finally:
        watcher_stop.set()
        if watcher is not None:
            watcher.join(timeout=max(1.0, terminal_probe_interval_seconds * 2))


def exact_run_is_durably_terminal(layout: Any) -> bool:
    """Return true only after exact recovery durably harvested a real output."""
    state = STATE.load_state(layout.state_path)
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output_path = Path(str(artifacts.get("output") or layout.output_path))
    return (
        state.get("status") == "complete"
        and state.get("session_authority") == "terminal"
        and state.get("terminal_harvested") is True
        and STATE.output_is_nonempty(output_path)
    )


def terminate_owned_oracle_process_tree(
    process: Any,
    *,
    platform_name: str | None = None,
    run_factory: Callable[..., Any] = subprocess.run,
) -> None:
    """Stop only the Popen-owned Oracle tree after durable exact recovery."""
    poll = getattr(process, "poll", None)
    if callable(poll) and poll() is not None:
        return
    platform = os.name if platform_name is None else platform_name
    pid = getattr(process, "pid", None)
    if platform == "nt" and isinstance(pid, int) and pid > 0:
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        completed = run_factory(
            [str(taskkill), "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            **STATE.windows_subprocess_kwargs(platform_name=platform),
        )
        if int(getattr(completed, "returncode", 1)) != 0 and (
            not callable(poll) or poll() is None
        ):
            raise OSError("owned Oracle process tree did not stop")
        return
    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        terminate()


ORACLE_VERSION_RESOLUTION_TIMEOUT_SECONDS = 90
ORACLE_CURRENT_VERSION = COMPAT.SUPPORTED_VERSION
ORACLE_LKG_VERSION = COMPAT.LKG_VERSION


def cached_oracle_version(command: Sequence[str]) -> str | None:
    """Resolve the pinned Oracle from its validated npx cache without npm I/O."""
    normalized = tuple(str(item) for item in command)
    executable = Path(normalized[0]).name.casefold() if normalized else ""
    accepted_packages = {
        f"@steipete/oracle@{ORACLE_CURRENT_VERSION}",
        f"@steipete/oracle@{ORACLE_LKG_VERSION}",
    }
    if executable not in {"npx", "npx.cmd", "npx.exe"} or not normalized[1:]:
        return None
    package = normalized[-1]
    if package not in accepted_packages or normalized[1:-1] not in {(), ("-y",), ("--yes",)}:
        return None
    try:
        requested_version = package.rsplit("@", 1)[-1]
        package_root = COMPAT.resolve_package_root(requested_version)
        version = COMPAT.package_version(package_root)
    except Exception:
        return None
    return f"oracle {version}" if version == requested_version else None


def resolve_oracle_version(
    command: Sequence[str], *, run_factory=subprocess.run,
    platform_name: str | None = None,
    cache_resolver: Callable[[Sequence[str]], str | None] = cached_oracle_version,
) -> str:
    """Resolve Oracle before launch with a bounded cold-cache allowance.

    The returned value is still passed immediately to the current-or-LKG exact
    compatibility/hash contract before a browser can be launched.
    """
    completed = run_factory(
        [*command, "--version"],
        cwd=None,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=ORACLE_VERSION_RESOLUTION_TIMEOUT_SECONDS,
        check=False,
        **STATE.windows_subprocess_kwargs(platform_name=platform_name),
    )
    if completed.returncode != 0:
        cached = cache_resolver(command)
        if cached is not None:
            return cached
        raise OracleRunError("ORACLE_VERSION_FAILED", "Oracle version could not be resolved", {"exit_code": completed.returncode})
    lines = [line.strip() for line in f"{completed.stdout or ''}\n{completed.stderr or ''}".splitlines() if line.strip()]
    if not lines:
        raise OracleRunError("ORACLE_VERSION_EMPTY", "Oracle version command returned no version")
    return lines[0]


def dry_run_payload(config, layout, argv: Sequence[str], prompt: str) -> dict[str, Any]:
    observer_seconds = browser_observer_timeout_seconds(config, argv)
    return {
        "ok": True,
        "status": "dry-run",
        "run_id": layout.run_id,
        "run_dir": str(layout.run_dir),
        "argv": STATE.command_for_display(argv),
        "prompt_first_line": prompt.splitlines()[0],
        "mission_path": str(config.mission_path),
        "mission_sha256": config.mission_sha256,
        "transport": config.transport,
        "attachments": [
            {"path": str(path), "sha256": digest}
            for path, digest in zip(config.attachments, config.attachment_sha256s)
        ],
        "output_path": str(layout.output_path),
        "transcript_path": str(layout.transcript_path),
        "stdout_path": str(layout.stdout_path),
        "stderr_path": str(layout.stderr_path),
        "contains_file_flag": "--file" in argv,
        "browser_observer_timeout_seconds": observer_seconds,
        "status_audit_seconds": config.status_audit_seconds,
        "time_alone_is_terminal": False,
    }


def append_error(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write((message.rstrip() + "\n").encode("utf-8", errors="replace"))


def _artifact_observation(path: Path, previous: dict[str, Any] | None) -> dict[str, Any]:
    if not path.exists():
        current = {"exists": False, "size_bytes": 0, "mtime_ns": None}
    else:
        stat = path.stat()
        current = {"exists": True, "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    current["progress_since_prior_audit"] = previous is not None and any(
        current.get(key) != previous.get(key) for key in ("exists", "size_bytes", "mtime_ns")
    )
    return current


def record_exact_run_status_audit(
    layout,
    *,
    process: Any,
    audit_count: int,
    status_audit_seconds: float,
    prior_observations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Persist non-terminal evidence at a caution threshold for one exact run."""
    observations = {
        "stdout": _artifact_observation(layout.stdout_path, prior_observations.get("stdout")),
        "stderr": _artifact_observation(layout.stderr_path, prior_observations.get("stderr")),
        "output": _artifact_observation(layout.output_path, prior_observations.get("output")),
    }
    prior_observations.clear()
    prior_observations.update(observations)
    poll = getattr(process, "poll", None)
    poll_result = poll() if callable(poll) else None
    pid = getattr(process, "pid", None)
    state = STATE.load_state(layout.state_path)
    if exact_run_is_durably_terminal(layout):
        return {
            "threshold_kind": "caution-status-audit",
            "threshold_seconds": status_audit_seconds,
            "audit_count": audit_count,
            "observed_at_unix_seconds": time.time(),
            "exact_slug": str((state.get("oracle") or {}).get("slug") or layout.slug),
            "decision": "exact-recovery-terminal-harvested-stop-owned-observer",
            "time_alone_is_terminal": False,
            "ownership_action": "release-owned-observer",
            "submission_action": "none",
        }
    exact_slug = str((state.get("oracle") or {}).get("slug") or layout.slug)
    audit = {
        "threshold_kind": "caution-status-audit",
        "threshold_seconds": status_audit_seconds,
        "audit_count": audit_count,
        "observed_at_unix_seconds": time.time(),
        "exact_slug": exact_slug,
        "oracle_process_pid": int(pid) if isinstance(pid, int) else None,
        "process_live": poll_result is None,
        "process_poll_result": poll_result,
        "artifacts": observations,
        "conversation_url_known": bool(str((state.get("oracle") or {}).get("conversation_url") or "").strip()),
        "live_tab_probe": "owned-by-running-oracle-process-not-concurrently-reopened",
        "decision": "continue-observing-same-exact-session",
        "time_alone_is_terminal": False,
        "ownership_action": "preserve",
        "submission_action": "none",
    }
    STATE.update_state(
        layout.state_path,
        status="running",
        exit_code=None,
        session_authority=str(state.get("session_authority") or "submitted_unknown"),
        status_audit=audit,
    )
    return audit


SESSION_STATE_RE = re.compile(r"(?im)^\s*State:\s*([a-z][a-z0-9_-]*)\s*$")
SESSION_URL_RE = re.compile(r"(?im)^\s*URL:\s*(https://chatgpt\.com/c/[^\s?#]+)\s*$")
# Oracle's observer may emit ``stalled`` after a quiet DOM interval even while
# ChatGPT is still visibly working in the exact conversation.  It is therefore
# not terminal evidence and must retain the exact-slug lock and live authority.
LIVE_SESSION_STATES = {"running", "streaming", "thinking", "active", "stalled"}
POST_SUBMIT_RESPONSE_TIMEOUT_MARKER = "assistant response timed out before completion"
# This is emitted by ChatGPT's delivery surface after an interrupted response.
# Oracle may still report ``State: completed`` and write the visible error as an
# assistant artifact, but neither is evidence that the DevSpace task settled.
PROVIDER_DELIVERY_TIMEOUT_MARKER = "message delivery timed out. please try again."
RECOVERY_BROWSER_PID_RE = re.compile(r"Launched Chrome \(pid (?P<pid>\d+)\)")
TERMINAL_SESSION_STATES = {
    "complete", "completed", "done", "finished", "failed", "error", "cancelled", "canceled",
}
RECOVERY_BINDING_UNAVAILABLE_MARKERS = (
    'No live ChatGPT tab matched session',
    'session metadata has no recoverable ChatGPT conversation URL',
)


def exact_session_state(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = SESSION_STATE_RE.findall(text)
    return matches[-1].casefold() if matches else None


def exact_session_url(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = SESSION_URL_RE.findall(text)
    return matches[-1] if matches else None


def historical_conversation_url(run_dir: Path, state: dict[str, Any]) -> str | None:
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    persisted = str(oracle.get("conversation_url") or "").strip()
    if persisted:
        return persisted
    for path in sorted(run_dir.glob("recovery-*-stdout.log"), key=lambda item: item.name, reverse=True):
        observed = exact_session_url(path)
        if observed:
            return observed
    return None


def conversation_url_conflict(state: dict[str, Any], observed: str | None) -> dict[str, str] | None:
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    persisted = str(oracle.get("conversation_url") or "").strip()
    candidate = str(observed or "").strip()
    if persisted and candidate and persisted != candidate:
        return {"persisted": persisted, "observed": candidate}
    return None


def exact_recovery_binding_unavailable(*paths: Path) -> bool:
    """Return true only for Oracle's exact no-live-tab plus no-saved-URL proof.

    Supported Oracle current/LKG releases write the no-live-tab line to stdout
    and the missing-URL detail to stderr. Both streams belong to one exact
    recovery attempt.
    """
    chunks: list[str] = []
    for path in paths:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            chunks.append("")
    value = "\n".join(chunks)
    return all(marker in value for marker in RECOVERY_BINDING_UNAVAILABLE_MARKERS)


def require_current_task_owns_run(
    state: dict[str, Any],
    *,
    source_thread_id: str | None = None,
    legacy_code: str = "LEGACY_TASK_OWNER_UNBOUND",
) -> None:
    """Refuse adoption of another task's run or a legacy-unbound run."""
    owner = STATE.source_thread_id_from_state(state)
    caller = str(source_thread_id or "").strip().casefold() or STATE.current_source_thread_id()
    if owner is None:
        # Historical states are deliberately unbound.  Do not guess their
        # origin from project, process, CDP port, or a rollout filename.
        # Recovery/harvest/stop/follow-up need an explicit handoff instead.
        # A no-task local maintenance process carries no authority at all; it
        # cannot bind a run to a task and performs no cross-task adoption.  In
        # every Codex task (CODEX_THREAD_ID present), fail closed.
        if caller:
            raise OracleRunError(
                legacy_code,
                "the exact Oracle run has no persisted task owner; adoption requires an explicit bounded handoff",
                {"run_id": state.get("run_id"), "slug": (state.get("oracle") or {}).get("slug")},
            )
        return
    if caller != owner:
        raise OracleRunError(
            "FOREIGN_TASK_SESSION",
            "the exact Oracle run belongs to a different Codex task; attach, recover, harvest, and stop are forbidden",
            {
                "owner_source_thread_id": owner,
                "caller_source_thread_id": caller,
                "run_id": state.get("run_id"),
                "slug": (state.get("oracle") or {}).get("slug"),
            },
        )


def require_bound_browser_identity(
    state_path: Path,
    state: dict[str, Any],
    *,
    recovery_action: str,
) -> str:
    if STATE.source_thread_id_from_state(state) is None:
        return "legacy-unbound"
    receipt = STATE.proven_browser_identity_receipt(state_path)
    if receipt is not None:
        return "browser-identity-receipt"
    direct_settlement = (
        STATE.followup_archived_parent_settle_without_harvest_evidence(state_path)
        if recovery_action == "harvest"
        else None
    )
    if direct_settlement is not None:
        raise OracleRunError(
            "FOLLOWUP_ARCHIVED_PARENT_HARVEST_NOT_APPLICABLE",
            "the exact follow-up failed before the composer while restoring an archived parent; harvest is not applicable",
            {
                "run_id": state.get("run_id"),
                "slug": (state.get("oracle") or {}).get("slug"),
                "failure_kind": direct_settlement.get("failure_kind"),
                "next_action": "after explicit user confirmation, use settle-no-submission for this exact run",
                "submission_action": "none",
            },
        )
    bounded = (
        STATE.bounded_task_owned_prompt_timeout_harvest_evidence(state_path)
        if recovery_action == "harvest"
        else None
    )
    if bounded is not None:
        if bounded.get("_bounded_harvest_kind") == "direct-devspace-model-option-missing":
            return "bounded-model-option-harvest"
        return "bounded-prompt-timeout-harvest"
    raise OracleRunError(
        "BROWSER_IDENTITY_RECEIPT_REQUIRED",
        "bound task recovery requires the exact persisted Chrome identity receipt",
        {
            "run_id": state.get("run_id"),
            "slug": (state.get("oracle") or {}).get("slug"),
            "recovery_action": recovery_action,
        },
    )


def post_submit_response_timed_out(*paths: Path) -> bool:
    """Return true only for Oracle's explicit post-send assistant timeout.

    This is live evidence, not terminal evidence: ChatGPT can keep working
    after Oracle's observer exhausts its deadline.  The caller must preserve
    the exact session and wait passively instead of launching recovery loops.
    """
    for path in paths:
        try:
            if POST_SUBMIT_RESPONSE_TIMEOUT_MARKER in path.read_text(
                encoding="utf-8", errors="replace"
            ).casefold():
                return True
        except OSError:
            pass
    return False


def provider_delivery_timed_out(*paths: Path) -> bool:
    """Return true for ChatGPT's visible delivery-timeout error in observer streams.

    A delivery timeout is provider-side incomplete evidence, even when Oracle's
    final observer line says ``State: completed``.  It must retain exact-session
    ownership rather than promote that error text to a terminal harvest.
    """
    for path in paths:
        try:
            if PROVIDER_DELIVERY_TIMEOUT_MARKER in path.read_text(
                encoding="utf-8", errors="replace"
            ).casefold():
                return True
        except OSError:
            pass
    return False


def provider_delivery_timeout_evidence(run_dir: Path, state: dict[str, Any]) -> bool:
    """Find exact-run timeout evidence despite later recovery log rotation."""
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    durable_paths = [
        run_dir / "transcript.md",
        Path(str(artifacts.get("output") or "")),
    ]
    recovery_streams = [
        stream
        for pattern in ("recovery-*-stdout.log", "recovery-*-stderr.log")
        for stream in run_dir.glob(pattern)
    ]
    return provider_delivery_timed_out(*recovery_streams, *durable_paths)


def run_owned_process_ids(run_dir: Path, state: dict[str, Any]) -> tuple[int, ...]:
    """Return only PIDs durably attributed to this exact Oracle run."""
    pids: set[int] = set()
    watchdog = state.get("host_watchdog") if isinstance(state.get("host_watchdog"), dict) else {}
    value = watchdog.get("oracle_process_pid")
    if isinstance(value, int) and value > 0:
        pids.add(value)
    observer = state.get("browser_observer") if isinstance(state.get("browser_observer"), dict) else {}
    observer_pid = observer.get("oracle_process_pid")
    if isinstance(observer_pid, int) and observer_pid > 0:
        pids.add(observer_pid)
    for path in run_dir.glob("*.log"):
        try:
            pids.update(int(match.group("pid")) for match in RECOVERY_BROWSER_PID_RE.finditer(
                path.read_text(encoding="utf-8", errors="replace")
            ))
        except OSError:
            continue
    return tuple(sorted(pids))


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


_RAW_PROCESS_IS_ALIVE = process_is_alive


def run_owned_process_is_alive(
    run_dir: Path,
    state: dict[str, Any],
    pid: int,
    *,
    process_alive: Callable[[int], bool] | None = None,
) -> bool:
    """Bind a live PID to this exact run before treating it as active."""
    probe = process_alive or process_is_alive
    if probe is not _RAW_PROCESS_IS_ALIVE:
        return bool(probe(pid))
    return STATE.exact_run_process_may_be_alive(
        run_dir,
        state,
        pid,
        process_probe=probe,
    )


def historical_session_authority(run_dir: Path, state: dict[str, Any]) -> str:
    """Recover the strongest exact-session authority from durable observer logs."""
    current = str(state.get("session_authority") or "submitted_unknown")
    # A previously persisted false terminal must be repairable from its exact
    # recovery evidence.  Do this before honoring terminal_harvested so the
    # state cannot become permanently monotonic on provider error text.
    if provider_delivery_timeout_evidence(run_dir, state):
        return "live"
    if (
        current == "terminal"
        and state.get("terminal_harvested") is True
        and STATE.output_is_nonempty(Path(str(state["artifacts"]["output"])))
    ):
        return "terminal"
    # Recovery logs are exact observer evidence.  A later `running` observation
    # supersedes an earlier provisional `completed`; only a harvested artifact
    # may make terminal authority irreversible.
    strongest = current
    for path in sorted(
        run_dir.glob("recovery-*-stdout.log"), key=lambda item: (item.stat().st_mtime_ns, item.name)
    ):
        observed = exact_session_state(path)
        if observed in TERMINAL_SESSION_STATES:
            strongest = "terminal_observed"
        elif observed in LIVE_SESSION_STATES:
            strongest = "live"
    return strongest


def pro_required_answer_labels(mission_path: Path) -> tuple[str, ...]:
    """Return the explicit structured-answer labels, if a Pro mission requires them."""
    try:
        mission = mission_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return ()
    marker = re.search(r"(?im)^\s*#+\s*Required answer schema\s*$", mission)
    if marker is None:
        return ()
    section = mission[marker.end():]
    next_heading = re.search(r"(?m)^\s*#+\s+", section)
    if next_heading is not None:
        section = section[:next_heading.start()]
    labels = re.findall(
        r"(?m)^\s*\d+\.\s+`([A-Z][A-Z0-9_]+)(?::[^`]*)?`",
        section,
    )
    return tuple(dict.fromkeys(labels))


def pro_output_satisfies_required_schema(state: dict[str, Any], output_path: Path) -> bool:
    """Require every declared Pro section to be a nonempty Markdown heading.

    A body mention is not a schema section: terminal preambles must remain
    ineligible for promotion. Both plain labels and labels wrapped in Markdown
    code ticks are accepted because the Pro response contract uses both forms.
    """
    if not STATE.is_pro_transport(str(state.get("transport") or "")):
        return True
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    mission_path = Path(str(mission.get("transport_path") or mission.get("path") or ""))
    labels = pro_required_answer_labels(mission_path)
    if not labels:
        return True
    try:
        output = output_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return False
    heading_re = re.compile(
        r"(?m)^\s{0,3}(?P<level>#{1,6})\s+(?:\d+\.\s+)?(?:`(?P<ticked>[A-Z][A-Z0-9_]+)(?::[^`]*)?`|(?P<plain>[A-Z][A-Z0-9_]+)(?::\s*[^\r\n]*)?)\s*$"
    )
    headings = list(heading_re.finditer(output))
    sections: dict[str, str] = {}
    for index, heading in enumerate(headings):
        label = (heading.group("ticked") or heading.group("plain") or "").casefold()
        level = len(heading.group("level"))
        next_start = next(
            (item.start() for item in headings[index + 1:] if len(item.group("level")) <= level),
            len(output),
        )
        if label and output[heading.end():next_start].strip():
            sections[label] = "present"
    return all(label.casefold() in sections for label in labels)


SAVED_ASSISTANT_OUTPUT_RE = re.compile(r"^Saved assistant output to (?P<path>.+?)\s*$")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strict_json_regular_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    exact = STATE.exact_regular_file(path, label=label)
    raw = exact.read_bytes()

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate {label} key")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise OracleRunError(
            "SAVED_OUTPUT_JSON_INVALID",
            f"{label} must be strict JSON without duplicate keys",
        ) from exc
    if not isinstance(payload, dict):
        raise OracleRunError("SAVED_OUTPUT_JSON_INVALID", f"{label} must be a JSON object")
    return payload, raw


def _saved_output_receipt_payload(path: Path) -> tuple[dict[str, Any], bytes] | None:
    if not path.exists():
        return None
    return _strict_json_regular_file(path, label="saved_output_settlement")


def _expected_transcript_sha256(stdout_path: Path, stderr_path: Path, output_path: Path) -> str:
    chunks: list[bytes] = []
    for path in (stdout_path, stderr_path, output_path):
        data = path.read_bytes()
        if data:
            chunks.append(data.rstrip() + b"\n")
    return hashlib.sha256(b"".join(chunks)).hexdigest()


def seal_saved_output_browser_identity(
    run_dir: Path,
    *,
    expected_settlement_sha256: str,
    dry_run: bool = False,
    process_alive: Callable[[int], bool] = process_is_alive,
) -> dict[str, Any]:
    """Seal the immutable browser tuple proven by a saved-output settlement."""
    directory = run_dir.expanduser().resolve(strict=True)
    state_path = STATE.exact_regular_file(directory / "state.json", label="saved_identity_state")
    state = STATE.load_state(state_path)
    require_current_task_owns_run(state)
    expected_digest = expected_settlement_sha256.strip().casefold()
    if re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None:
        raise OracleRunError("SAVED_IDENTITY_HASH_INVALID", "expected settlement hash must be exact SHA-256")
    receipt_path = directory / "settlements" / "saved-terminal-output.json"
    settlement, settlement_raw = _strict_json_regular_file(
        receipt_path, label="saved_terminal_output_settlement"
    )
    settlement_sha256 = hashlib.sha256(settlement_raw).hexdigest()
    reference = state.get("saved_terminal_output_settlement")
    if (
        expected_digest != settlement_sha256
        or settlement.get("schema") != "codex.chatgpt.oracle-saved-terminal-output/v1"
        or not isinstance(reference, dict)
        or reference.get("schema") != "codex.chatgpt.oracle-settlement-reference/v1"
        or Path(str(reference.get("path") or "")).resolve() != receipt_path.resolve()
        or reference.get("sha256") != settlement_sha256
        or state.get("status") != "complete"
        or state.get("session_authority") != "terminal"
        or state.get("transport_status") != "complete"
        or state.get("terminal_harvested") is not True
        or state.get("task_outcome") != settlement.get("task_outcome")
    ):
        raise OracleRunError(
            "SAVED_IDENTITY_SETTLEMENT_INVALID",
            "browser identity sealing requires one exact terminal saved-output settlement",
        )
    ownership = STATE.proven_ownership_receipt(state_path)
    binding = STATE.proven_followup_binding(state_path)
    if (
        ownership is None
        or binding is None
        or settlement.get("ownership_receipt_sha256") != ownership.get("sha256")
        or settlement.get("followup_binding_sha256") != binding.get("sha256")
        or settlement.get("source_thread_id") != STATE.source_thread_id_from_state(state)
        or settlement.get("run_id") != state.get("run_id")
        or settlement.get("slug") != (state.get("oracle") or {}).get("slug")
        or settlement.get("mission_sha256") != (state.get("mission") or {}).get("sha256")
    ):
        raise OracleRunError(
            "SAVED_IDENTITY_BINDING_INVALID",
            "saved-output settlement ownership or follow-up binding is invalid",
        )
    existing = STATE.proven_browser_identity_receipt(state_path)
    if existing is not None:
        return {
            "ok": True,
            "status": "saved_output_browser_identity_already_sealed",
            "run_dir": str(directory),
            "browser_identity_receipt_path": existing["path"],
            "browser_identity_receipt_sha256": existing["sha256"],
            "submission_action": "none",
        }
    browser_receipt_path = directory / "browser-identity-receipt.json"
    browser_reference = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    if (
        browser_receipt_path.exists()
        or browser_reference.get("receipt_path") not in {None, ""}
        or browser_reference.get("receipt_sha256") not in {None, ""}
    ):
        raise OracleRunError(
            "SAVED_IDENTITY_RECEIPT_CONFLICT",
            "an unproven browser identity receipt already exists",
        )
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output_path = STATE.exact_regular_file(artifacts.get("output"), label="saved_identity_output")
    stdout_path = STATE.exact_regular_file(artifacts.get("stdout"), label="saved_identity_stdout")
    transcript_path = STATE.exact_regular_file(artifacts.get("transcript"), label="saved_identity_transcript")
    slug = str((state.get("oracle") or {}).get("slug") or "")
    session_root = Path(
        os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
    ).expanduser().resolve()
    meta_path = STATE.exact_regular_file(
        session_root / slug / "meta.json", label="saved_identity_oracle_meta"
    )
    current_hashes = {
        "output_sha256": STATE.sha256_file(output_path),
        "stdout_sha256": STATE.sha256_file(stdout_path),
        "transcript_sha256": STATE.sha256_file(transcript_path),
        "oracle_meta_sha256": STATE.sha256_file(meta_path),
    }
    if any(settlement.get(key) != value for key, value in current_hashes.items()):
        raise OracleRunError(
            "SAVED_IDENTITY_ARTIFACT_DRIFT",
            "saved-output evidence changed after terminal reconciliation",
            {"current": current_hashes},
        )
    meta, _meta_raw = _strict_json_regular_file(meta_path, label="saved_identity_oracle_meta")
    browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
    runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
    identity = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    expected_port = identity.get("expected_cdp_port")
    observed_port = runtime.get("chromePort")
    expected_url = str((binding.get("payload") or {}).get("conversation_url") or "").strip()
    conversation_url = str(runtime.get("tabUrl") or "").strip()
    conversation_id = str(runtime.get("conversationId") or "").strip()
    profile_path = Path(str(runtime.get("userDataDir") or "")).expanduser()
    raw_browser_temp = Path(str(artifacts.get("browser_temp") or "")).expanduser()
    browser_temp = raw_browser_temp.resolve()
    try:
        chrome_pid = int(runtime.get("chromePid"))
        parent_pid = int(runtime.get("controllerPid"))
    except (TypeError, ValueError) as exc:
        raise OracleRunError("SAVED_IDENTITY_RUNTIME_INVALID", "Oracle browser PID identity is invalid") from exc
    if (
        meta.get("status") != "completed"
        or meta.get("error") is not None
        or runtime.get("promptSubmitted") is not True
        or not isinstance(expected_port, int)
        or not isinstance(observed_port, int)
        or expected_port == observed_port
        or STATE.CHATGPT_CONVERSATION_URL_RE.fullmatch(conversation_url) is None
        or conversation_url != expected_url
        or conversation_id != conversation_url.rstrip("/").rsplit("/", 1)[-1]
        or conversation_url != settlement.get("conversation_url")
        or conversation_id != settlement.get("conversation_id")
        or str(runtime.get("chromeTargetId") or "") != settlement.get("chrome_target_id")
        or str(profile_path.resolve()) != settlement.get("profile_path")
        or observed_port != settlement.get("observed_cdp_port")
        or expected_port != settlement.get("expected_cdp_port")
        or not profile_path.is_dir()
        or profile_path.is_symlink()
        or browser_temp != (directory / "browser-temp").resolve()
        or not browser_temp.is_dir()
        or raw_browser_temp.is_symlink()
        or not STATE.is_within(browser_temp, profile_path.resolve())
    ):
        raise OracleRunError(
            "SAVED_IDENTITY_RUNTIME_INVALID",
            "terminal Oracle browser identity is not exactly bound to the saved-output settlement",
        )
    checked_pids = tuple(sorted(set(run_owned_process_ids(directory, state)) | {chrome_pid, parent_pid}))
    active_pids = [
        pid for pid in checked_pids
        if run_owned_process_is_alive(directory, state, pid, process_alive=process_alive)
    ]
    if active_pids:
        raise OracleRunError(
            "SAVED_IDENTITY_PROCESS_ACTIVE",
            "every exact run-owned Oracle, controller, and Chrome process must be stopped",
            {"active_pids": active_pids},
        )
    runtime_identity = {
        "chrome_pid": chrome_pid,
        "browser_parent_pid": parent_pid,
        "profile_path": str(profile_path.resolve()),
        "cdp_port": observed_port,
        "target_id": runtime.get("chromeTargetId"),
        "conversation_url": conversation_url,
    }
    payload = {
        "schema": "codex.chatgpt.oracle-browser-identity-receipt/v2",
        "authority": "saved-terminal-output-reconciliation",
        "source_thread_id": STATE.source_thread_id_from_state(state),
        "project_root_sha256": (state.get("ownership") or {}).get("project_root_sha256"),
        "run_id": state.get("run_id"),
        "mission_sha256": (state.get("mission") or {}).get("sha256"),
        "slug": slug,
        **runtime_identity,
        "expected_cdp_port": expected_port,
        "observed_cdp_port": observed_port,
        "oracle_meta_sha256": current_hashes["oracle_meta_sha256"],
        "oracle_runtime_identity_sha256": hashlib.sha256(
            json.dumps(runtime_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "saved_terminal_output_settlement_path": str(receipt_path),
        "saved_terminal_output_settlement_sha256": settlement_sha256,
        "output_sha256": current_hashes["output_sha256"],
        "stdout_sha256": current_hashes["stdout_sha256"],
        "transcript_sha256": current_hashes["transcript_sha256"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    preview = {
        "ok": True,
        "status": "dry-run" if dry_run else "saved_output_browser_identity_sealed",
        "run_dir": str(directory),
        "browser_identity_receipt_path": str(browser_receipt_path),
        "browser_identity_payload": payload,
        "submission_action": "none",
    }
    if dry_run:
        return preview
    sealed = STATE.persist_saved_output_browser_identity_receipt(state_path, payload=payload)
    if sealed is None:
        raise OracleRunError(
            "SAVED_IDENTITY_PERSIST_FAILED",
            "saved-output browser identity receipt could not be persisted or revalidated",
        )
    return {
        **preview,
        "browser_identity_receipt_sha256": sealed["sha256"],
        "result": STATE.load_state(state_path),
    }


def settle_saved_terminal_output(
    run_dir: Path,
    *,
    expected_state_sha256: str,
    expected_output_sha256: str,
    expected_stdout_sha256: str,
    expected_oracle_meta_sha256: str,
    dry_run: bool = False,
    process_alive: Callable[[int], bool] = process_is_alive,
) -> dict[str, Any]:
    """Reconcile one owner-bound run after Oracle saved terminal output.

    This does not relax recovery's browser-identity receipt gate.  It covers
    only the narrower crash boundary where the exact Oracle session is already
    completed, its official output was durably written, every run-owned process
    has exited, and the outer state transition alone was missed.
    """
    directory = run_dir.expanduser().resolve(strict=True)
    state_path = STATE.exact_regular_file(directory / "state.json", label="saved_output_state")
    state = STATE.load_state(state_path)
    require_current_task_owns_run(state)
    receipt_path = directory / "settlements" / "saved-terminal-output.json"
    existing = _saved_output_receipt_payload(receipt_path)
    pending_receipt: tuple[dict[str, Any], bytes] | None = None
    if existing is not None:
        receipt, raw = existing
        reference = state.get("saved_terminal_output_settlement")
        if (
            receipt.get("schema") == "codex.chatgpt.oracle-saved-terminal-output/v1"
            and isinstance(reference, dict)
            and Path(str(reference.get("path") or "")).resolve() == receipt_path.resolve()
            and reference.get("sha256") == hashlib.sha256(raw).hexdigest()
            and state.get("status") == "complete"
            and state.get("session_authority") == "terminal"
            and state.get("terminal_harvested") is True
            and state.get("artifact_sha256") == receipt.get("output_sha256")
            and state.get("task_outcome") == receipt.get("task_outcome")
        ):
            artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
            output_path = STATE.exact_regular_file(artifacts.get("output"), label="settled_saved_output")
            stdout_path = STATE.exact_regular_file(artifacts.get("stdout"), label="settled_saved_output_stdout")
            stderr_path = STATE.exact_regular_file(artifacts.get("stderr"), label="settled_saved_output_stderr")
            transcript_path = STATE.exact_regular_file(
                artifacts.get("transcript"), label="settled_saved_output_transcript"
            )
            session_root = Path(
                os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
            ).expanduser().resolve()
            slug = str((state.get("oracle") or {}).get("slug") or "")
            meta_path = STATE.exact_regular_file(
                session_root / slug / "meta.json", label="settled_saved_output_oracle_meta"
            )
            current_hashes = {
                "output_sha256": STATE.sha256_file(output_path),
                "stdout_sha256": STATE.sha256_file(stdout_path),
                "oracle_meta_sha256": STATE.sha256_file(meta_path),
                "transcript_sha256": STATE.sha256_file(transcript_path),
            }
            if (
                output_path.resolve() != (directory / "output.md").resolve()
                or stdout_path.resolve() != (directory / "stdout.log").resolve()
                or stderr_path.resolve() != (directory / "stderr.log").resolve()
                or transcript_path.resolve() != (directory / "transcript.md").resolve()
                or stderr_path.read_bytes()
                or any(receipt.get(key) != value for key, value in current_hashes.items())
            ):
                raise OracleRunError(
                    "SAVED_OUTPUT_SETTLEMENT_ARTIFACT_DRIFT",
                    "settled saved-output artifacts changed after reconciliation",
                    {"current": current_hashes},
                )
            identity_result = None
            if receipt.get("expected_cdp_port") != receipt.get("observed_cdp_port"):
                identity_result = seal_saved_output_browser_identity(
                    directory,
                    expected_settlement_sha256=reference["sha256"],
                    dry_run=dry_run,
                    process_alive=process_alive,
                )
            return {
                "ok": True,
                "status": "saved_terminal_output_already_reconciled",
                "run_dir": str(directory),
                "settlement_path": str(receipt_path),
                "settlement_sha256": reference["sha256"],
                "browser_identity": identity_result,
                "result": STATE.load_state(state_path),
            }
        stale_boundary = (
            receipt.get("schema") == "codex.chatgpt.oracle-saved-terminal-output/v1"
            and not isinstance(reference, dict)
            and state.get("status") == "running"
            and state.get("session_authority") == "submitted_unknown"
            and state.get("terminal_harvested") is False
        )
        if stale_boundary:
            pending_receipt = (receipt, raw)
        else:
            raise OracleRunError(
                "SAVED_OUTPUT_SETTLEMENT_CONFLICT",
                "append-only saved-output settlement exists but does not validate against terminal state",
                {"path": str(receipt_path)},
            )

    exact_expected = {
        "state_sha256": expected_state_sha256.strip().casefold(),
        "output_sha256": expected_output_sha256.strip().casefold(),
        "stdout_sha256": expected_stdout_sha256.strip().casefold(),
        "oracle_meta_sha256": expected_oracle_meta_sha256.strip().casefold(),
    }
    if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in exact_expected.values()):
        raise OracleRunError("SAVED_OUTPUT_HASH_INVALID", "every expected hash must be exact SHA-256")
    if (
        state.get("status") != "running"
        or state.get("session_authority") != "submitted_unknown"
        or state.get("transport_status") != "prepared"
        or state.get("terminal_harvested") is not False
        or state.get("task_outcome") != "pending"
    ):
        raise OracleRunError(
            "SAVED_OUTPUT_STALE_STATE_REQUIRED",
            "reconciliation requires the exact stale post-output state boundary",
        )
    if STATE.proven_ownership_receipt(state_path) is None:
        raise OracleRunError("SAVED_OUTPUT_OWNERSHIP_INVALID", "exact ownership receipt is invalid")
    binding = STATE.proven_followup_binding(state_path)
    if binding is None:
        raise OracleRunError("SAVED_OUTPUT_FOLLOWUP_BINDING_INVALID", "exact follow-up binding is invalid")
    browser_reference = state.get("browser_identity") if isinstance(state.get("browser_identity"), dict) else {}
    if (
        browser_reference.get("receipt_path") is not None
        or browser_reference.get("receipt_sha256") is not None
        or (directory / "browser-identity-receipt.json").exists()
    ):
        raise OracleRunError(
            "SAVED_OUTPUT_BROWSER_RECEIPT_PRESENT",
            "runs with a browser identity receipt must use the ordinary exact-session path",
        )
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output_path = STATE.exact_regular_file(artifacts.get("output"), label="saved_output")
    stdout_path = STATE.exact_regular_file(artifacts.get("stdout"), label="saved_output_stdout")
    stderr_path = STATE.exact_regular_file(artifacts.get("stderr"), label="saved_output_stderr")
    if (
        output_path.resolve() != (directory / "output.md").resolve()
        or stdout_path.resolve() != (directory / "stdout.log").resolve()
        or stderr_path.resolve() != (directory / "stderr.log").resolve()
        or stderr_path.read_bytes()
        or not STATE.output_is_nonempty(output_path)
    ):
        raise OracleRunError(
            "SAVED_OUTPUT_ARTIFACT_INVALID",
            "official output/stdout/stderr artifacts do not match the exact run boundary",
        )
    slug = str((state.get("oracle") or {}).get("slug") or "")
    session_root = Path(
        os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")
    ).expanduser().resolve()
    meta_path = session_root / slug / "meta.json"
    meta, meta_raw = _strict_json_regular_file(meta_path, label="saved_output_oracle_meta")
    actual_hashes = {
        "state_sha256": STATE.sha256_file(state_path),
        "output_sha256": STATE.sha256_file(output_path),
        "stdout_sha256": STATE.sha256_file(stdout_path),
        "oracle_meta_sha256": hashlib.sha256(meta_raw).hexdigest(),
    }
    if actual_hashes != exact_expected:
        raise OracleRunError(
            "SAVED_OUTPUT_HASH_MISMATCH",
            "exact saved-output evidence changed before reconciliation",
            {"expected": exact_expected, "actual": actual_hashes},
        )
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="strict")
    saved_paths = []
    clean_nonempty_lines = [
        ANSI_ESCAPE_RE.sub("", line).strip()
        for line in stdout_text.splitlines()
        if ANSI_ESCAPE_RE.sub("", line).strip()
    ]
    for line in clean_nonempty_lines:
        match = SAVED_ASSISTANT_OUTPUT_RE.fullmatch(line)
        if match is not None:
            saved_paths.append(Path(match.group("path")).expanduser().resolve(strict=False))
    if (
        saved_paths != [output_path.resolve()]
        or not clean_nonempty_lines
        or SAVED_ASSISTANT_OUTPUT_RE.fullmatch(clean_nonempty_lines[-1]) is None
    ):
        raise OracleRunError(
            "SAVED_OUTPUT_STDOUT_BINDING_INVALID",
            "stdout must contain exactly one canonical saved-output record for this run",
            {"observed_paths": [str(path) for path in saved_paths]},
        )
    browser = meta.get("browser") if isinstance(meta.get("browser"), dict) else {}
    runtime = browser.get("runtime") if isinstance(browser.get("runtime"), dict) else {}
    conversation_url = str(runtime.get("tabUrl") or "").strip()
    conversation_id = str(runtime.get("conversationId") or "").strip()
    expected_url = str((binding.get("payload") or {}).get("conversation_url") or "").strip()
    expected_id = expected_url.rstrip("/").rsplit("/", 1)[-1] if expected_url else ""
    profile_path = Path(str(runtime.get("userDataDir") or "")).expanduser()
    raw_browser_temp = Path(str(artifacts.get("browser_temp") or "")).expanduser()
    browser_temp = raw_browser_temp.resolve()
    runtime_pids = tuple(
        sorted({
            value for value in (runtime.get("controllerPid"), runtime.get("chromePid"))
            if isinstance(value, int) and value > 0
        })
    )
    runtime_port = runtime.get("chromePort")
    if (
        meta.get("status") != "completed"
        or not str(meta.get("completedAt") or "").strip()
        or meta.get("error") is not None
        or runtime.get("promptSubmitted") is not True
        or STATE.CHATGPT_CONVERSATION_URL_RE.fullmatch(conversation_url) is None
        or conversation_url != expected_url
        or conversation_id != expected_id
        or not isinstance(runtime_port, int)
        or not 1024 <= runtime_port <= 65535
        or not str(runtime.get("chromeTargetId") or "").strip()
        or not profile_path.is_dir()
        or profile_path.is_symlink()
        or browser_temp != (directory / "browser-temp").resolve()
        or not browser_temp.is_dir()
        or raw_browser_temp.is_symlink()
        or not STATE.is_within(browser_temp, profile_path.resolve())
    ):
        raise OracleRunError(
            "SAVED_OUTPUT_ORACLE_META_INVALID",
            "Oracle terminal metadata is not exactly bound to this follow-up conversation and browser profile",
        )
    checked_pids = tuple(sorted(set(run_owned_process_ids(directory, state)) | set(runtime_pids)))
    active_pids = [
        pid for pid in checked_pids
        if run_owned_process_is_alive(directory, state, pid, process_alive=process_alive)
    ]
    if active_pids:
        raise OracleRunError(
            "SAVED_OUTPUT_PROCESS_ACTIVE",
            "every exact run-owned Oracle, controller, and Chrome process must be stopped",
            {"active_pids": active_pids},
        )
    if any(directory.glob("recovery-*-candidate.md")):
        raise OracleRunError(
            "SAVED_OUTPUT_RECOVERY_CONFLICT",
            "a recovery candidate exists; saved-output reconciliation is ambiguous",
        )
    task_outcome = STATE.classify_task_outcome(
        output_path,
        contract=str(state.get("task_outcome_contract") or "legacy"),
        transport=str(state.get("transport") or "devspace"),
    )
    if task_outcome not in {"executed", "not_executed", "blocked"}:
        raise OracleRunError(
            "SAVED_OUTPUT_TASK_OUTCOME_INVALID",
            "official output does not contain one unambiguous terminal task outcome",
        )
    if not pro_output_satisfies_required_schema(state, output_path):
        raise OracleRunError(
            "SAVED_OUTPUT_SCHEMA_INCOMPLETE",
            "official output does not satisfy the exact Pro required-answer schema",
        )
    receipt = {
        "schema": "codex.chatgpt.oracle-saved-terminal-output/v1",
        "source_thread_id": STATE.source_thread_id_from_state(state),
        "project_root": state.get("project_root"),
        "run_id": state.get("run_id"),
        "slug": slug,
        "mission_sha256": (state.get("mission") or {}).get("sha256"),
        **actual_hashes,
        "ownership_receipt_sha256": STATE.proven_ownership_receipt(state_path)["sha256"],
        "followup_binding_sha256": binding["sha256"],
        "conversation_url": conversation_url,
        "conversation_id": conversation_id,
        "chrome_target_id": runtime.get("chromeTargetId"),
        "profile_path": str(profile_path.resolve()),
        "expected_cdp_port": browser_reference.get("expected_cdp_port"),
        "observed_cdp_port": runtime_port,
        "oracle_completed_at": meta.get("completedAt"),
        "run_owned_pids_checked": list(checked_pids),
        "task_outcome": task_outcome,
        "transcript_sha256": _expected_transcript_sha256(stdout_path, stderr_path, output_path),
        "reconciled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    preview = {
        "ok": True,
        "status": "dry-run" if dry_run else "saved_terminal_output_reconciled",
        "run_dir": str(directory),
        "output_path": str(output_path),
        "settlement_path": str(receipt_path),
        "settlement_payload": receipt,
        "submission_action": "none",
    }
    if dry_run:
        return preview
    transcript_path = Path(str(artifacts.get("transcript") or directory / "transcript.md")).resolve()
    if transcript_path != (directory / "transcript.md").resolve() or transcript_path.is_symlink():
        raise OracleRunError("SAVED_OUTPUT_TRANSCRIPT_INVALID", "exact transcript path is unsafe")
    layout = STATE.RunLayout(
        str(state["run_id"]), slug, directory, state_path, output_path, transcript_path,
        stdout_path, stderr_path, browser_temp,
    )
    STATE.write_transcript(layout)
    if STATE.sha256_file(transcript_path) != receipt["transcript_sha256"]:
        raise OracleRunError(
            "SAVED_OUTPUT_TRANSCRIPT_HASH_MISMATCH",
            "exact transcript bytes differ from the planned terminal transcript",
        )
    if receipt_path.parent.exists() and (
        receipt_path.parent.is_symlink() or receipt_path.parent.resolve().parent != directory
    ):
        raise OracleRunError("SAVED_OUTPUT_SETTLEMENT_PATH_UNSAFE", "settlement directory is unsafe")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if pending_receipt is not None:
        recorded, raw = pending_receipt
        recorded_stable = {key: value for key, value in recorded.items() if key != "reconciled_at"}
        planned_stable = {key: value for key, value in receipt.items() if key != "reconciled_at"}
        if recorded_stable != planned_stable:
            raise OracleRunError(
                "SAVED_OUTPUT_SETTLEMENT_CONFLICT",
                "interrupted append-only settlement does not match the exact current evidence",
            )
        encoded = raw
    else:
        try:
            with receipt_path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise OracleRunError("SAVED_OUTPUT_SETTLEMENT_EXISTS", "append-only settlement already exists") from exc
    updated = STATE.update_state(
        state_path,
        status="complete",
        exit_code=state.get("exit_code"),
        session_authority="terminal",
        terminal_harvested=True,
        artifact_sha256=actual_hashes["output_sha256"],
        transport_status="complete",
        task_outcome=task_outcome,
        task_outcome_reason="oracle-saved-terminal-output-reconciliation",
        conversation_url=conversation_url,
    )
    updated["saved_terminal_output_settlement"] = {
        "schema": "codex.chatgpt.oracle-settlement-reference/v1",
        "path": str(receipt_path),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    updated["browser_observer"] = {
        **(updated.get("browser_observer") if isinstance(updated.get("browser_observer"), dict) else {}),
        "status": "process-exited-terminal-output-reconciled",
    }
    STATE.write_json_atomic(state_path, updated)
    identity_result = None
    if receipt.get("expected_cdp_port") != receipt.get("observed_cdp_port"):
        identity_result = seal_saved_output_browser_identity(
            directory,
            expected_settlement_sha256=hashlib.sha256(encoded).hexdigest(),
            process_alive=process_alive,
        )
    return {
        **preview,
        "settlement_sha256": hashlib.sha256(encoded).hexdigest(),
        "output_sha256": actual_hashes["output_sha256"],
        "task_outcome": task_outcome,
        "browser_identity": identity_result,
        "result": STATE.load_state(state_path),
    }


def promote_terminal_harvest_candidate(
    run_dir: Path,
    *,
    candidate_path: Path,
    expected_candidate_sha256: str,
) -> dict[str, Any]:
    """Promote one already observed terminal candidate without launching Oracle."""
    directory = run_dir.expanduser().resolve(strict=True)
    state_path = directory / "state.json"
    state = STATE.load_state(state_path)
    if str(state.get("session_authority") or "") != "terminal_observed":
        raise OracleRunError(
            "PROMOTION_TERMINAL_OBSERVATION_REQUIRED",
            "only an exact terminal observation may promote a harvested candidate",
        )
    if state.get("terminal_harvested") is True:
        raise OracleRunError("PROMOTION_ALREADY_HARVESTED", "the exact run is already harvested")
    candidate = candidate_path.expanduser().resolve(strict=True)
    if not STATE.is_within(directory, candidate) or not re.fullmatch(
        r"recovery-(?:harvest|live)-candidate\.md", candidate.name
    ):
        raise OracleRunError(
            "PROMOTION_CANDIDATE_INVALID",
            "candidate must be the exact run's persisted recovery candidate",
        )
    actual_sha256 = STATE.sha256_file(candidate)
    if actual_sha256 != expected_candidate_sha256.strip().casefold():
        raise OracleRunError(
            "PROMOTION_CANDIDATE_HASH_MISMATCH",
            "candidate bytes differ from the supplied exact hash",
            {"expected": expected_candidate_sha256, "actual": actual_sha256},
        )
    if not STATE.output_is_nonempty(candidate) or not pro_output_satisfies_required_schema(state, candidate):
        raise OracleRunError(
            "PROMOTION_CANDIDATE_SCHEMA_INCOMPLETE",
            "candidate does not satisfy the exact Pro required-answer schema",
        )
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output_path = Path(str(artifacts.get("output") or directory / "output.md")).resolve()
    if output_path != (directory / "output.md").resolve() or output_path.exists():
        raise OracleRunError("PROMOTION_OUTPUT_PATH_INVALID", "exact run output path is unavailable")
    temporary = output_path.with_name(f".{output_path.name}.promote-{os.getpid()}.tmp")
    try:
        with candidate.open("rb") as source, temporary.open("xb") as destination:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    layout = STATE.RunLayout(
        str(state["run_id"]), str((state.get("oracle") or {}).get("slug") or ""), directory,
        state_path, output_path, Path(str(artifacts.get("transcript") or directory / "transcript.md")),
        Path(str(artifacts.get("stdout") or directory / "stdout.log")),
        Path(str(artifacts.get("stderr") or directory / "stderr.log")),
        Path(str(artifacts.get("browser_temp") or directory / "browser-temp")).resolve(),
    )
    STATE.write_transcript(layout)
    task_outcome = STATE.classify_task_outcome(
        output_path,
        contract=str(state.get("task_outcome_contract") or "legacy"),
        transport=str(state.get("transport") or "devspace"),
    )
    updated = STATE.update_state(
        state_path, status="complete", exit_code=state.get("exit_code"), session_authority="terminal",
        terminal_harvested=True, artifact_sha256=actual_sha256, transport_status="complete",
        task_outcome=task_outcome, task_outcome_reason="deterministic-terminal-candidate-promotion",
    )
    return {"ok": True, "status": "complete", "run_dir": str(directory), "output_path": str(output_path),
            "candidate_path": str(candidate), "artifact_sha256": actual_sha256, "result": updated}


def web_multi_devspace_qualification_target(config: STATE.OracleConfig) -> Path:
    """Return the canonical qualified root for a strict derived worktree child."""
    if config.web_multi_child_provenance_path is None:
        return config.project_root
    try:
        provenance = json.loads(config.web_multi_child_provenance_path.read_text(encoding="utf-8"))
        parent_path = Path(str(provenance.get("parent_manifest_path") or "")).resolve(strict=True)
        if STATE.sha256_file(parent_path) != str(provenance.get("parent_manifest_sha256") or ""):
            raise ValueError("parent manifest hash mismatch")
        parent = json.loads(parent_path.read_text(encoding="utf-8"))
        lane_id = str(provenance.get("lane_id") or "")
        lanes = parent.get("solvers") if isinstance(parent.get("solvers"), list) else []
        lane = next((item for item in lanes if isinstance(item, dict) and str(item.get("id") or "") == lane_id), None)
        canonical = Path(str(parent.get("project_root") or "")).resolve(strict=True)
        output_dir = Path(str(parent.get("output_dir") or "")).resolve()
        worktree_parent = (output_dir / "worktrees").resolve()
        if (
            parent.get("schema") != "codex.chatgpt.oracle-multi/v2"
            or not isinstance(lane, dict)
            or str(lane.get("access") or "") != "worktree-write"
            or Path(str(lane.get("project_root") or "")).resolve(strict=True) != config.project_root
            or Path(str(provenance.get("canonical_project_root") or "")).resolve(strict=True) != canonical
        ):
            raise ValueError("strict child binding mismatch")
        config.project_root.relative_to(worktree_parent)
        output_dir.relative_to(canonical)
        return canonical
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise OracleRunError(
            "WEB_MULTI_DERIVED_ROOT_INVALID",
            "strict Web Multi worktree is not safely bound to its canonical qualified root",
            {"project_root": str(config.project_root), "provenance_path": str(config.web_multi_child_provenance_path)},
        ) from exc


def configure_task_outcome_terminal_contract(env: dict[str, str], contract: str) -> None:
    """Expose the bounded terminal watchdog only to exact v1 answer contracts."""
    env.pop("ORACLE_TASK_OUTCOME_TERMINAL_CONTRACT", None)
    env.pop("ORACLE_TERMINAL_MARKER_CONFIRM_CYCLES", None)
    env.pop("ORACLE_TERMINAL_MARKER_MIN_STABLE_MS", None)
    if contract == "v1":
        env["ORACLE_TASK_OUTCOME_TERMINAL_CONTRACT"] = "v1"


def execute_run(
    manifest_path: Path,
    *,
    dry_run: bool = False,
    run_factory: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    platform_name: str | None = None,
    version_resolver: Callable[..., str] = resolve_oracle_version,
    compat_factory: Callable[[str], dict[str, Any]] = COMPAT.ensure_oracle_compatibility,
    devspace_compat_factory: Callable[[], dict[str, Any]] = (
        DEVSPACE_COMPAT.ensure_devspace_compatibility
    ),
    devspace_qualification_factory: Callable[[Path], dict[str, Any]] = (
        DEVSPACE_PREFLIGHT.ensure_exact_root_qualified
    ),
    pro_app_read_gate_factory: Callable[[Path, str], dict[str, Any]] = (
        DEVSPACE_PREFLIGHT.ensure_recent_registered_app_read_gate
    ),
    exact_recovery_factory: Callable[..., dict[str, Any]] | None = None,
    _cdp_port: int | None = None,
    _followup_parent_slug: str | None = None,
    _followup_binding: dict[str, Any] | None = None,
    _expected_manifest_sha256: str | None = None,
    _followup_parent_run_dir: Path | None = None,
    _expected_followup_mission_sha256: str | None = None,
) -> dict[str, Any]:
    manifest_bytes: bytes | None = None
    if _expected_manifest_sha256 is not None:
        if _followup_parent_run_dir is None:
            raise OracleRunError(
                "FOLLOWUP_PARENT_RUN_DIR_REQUIRED",
                "verified follow-up manifests require the exact parent run directory",
            )
        manifest_directory = _assert_followup_artifact_directory(
            _followup_parent_run_dir,
            "followup-manifests",
        )
        if manifest_path.parent != manifest_directory:
            raise OracleRunError(
                "FOLLOWUP_MANIFEST_PATH_INVALID",
                "the exact follow-up manifest escaped the parent artifact directory",
                {"path": str(manifest_path), "expected_parent": str(manifest_directory)},
            )
        if manifest_path.is_symlink():
            raise OracleRunError(
                "FOLLOWUP_MANIFEST_SYMLINK_FORBIDDEN",
                "the exact follow-up manifest must not be a symlink",
                {"path": str(manifest_path)},
            )
        manifest_bytes = manifest_path.read_bytes()
        actual_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_manifest_sha256 != _expected_manifest_sha256:
            raise OracleRunError(
                "FOLLOWUP_MANIFEST_CHANGED_BEFORE_PREPARE",
                "the exact follow-up manifest changed before child preparation",
                {"expected": _expected_manifest_sha256, "actual": actual_manifest_sha256},
            )
    config = STATE.load_manifest(
        manifest_path,
        platform_name=platform_name,
        bind_runtime_task=True,
        raw_bytes=manifest_bytes,
    )
    if (
        _expected_followup_mission_sha256 is not None
        and config.mission_sha256 != _expected_followup_mission_sha256
    ):
        raise OracleRunError(
            "FOLLOWUP_MISSION_CHANGED_BEFORE_PREPARE",
            "the exact follow-up mission changed after round reservation",
            {
                "expected": _expected_followup_mission_sha256,
                "actual": config.mission_sha256,
            },
        )
    if str(config.transport or "").strip().casefold() == "pro-devspace":
        raise OracleRunError(
            "PRO_WRITABLE_TRANSPORT_FROZEN",
            "new writable Pro DevSpace runs are disabled; use pro-devspace-readonly for Pro advice or a regular GPT-5.6 extra-high DevSpace run for writes",
            {
                "transport": config.transport,
                "pro_transport": "pro-devspace-readonly",
                "write_transport": "devspace",
                "write_model": "gpt-5.6",
                "write_thinking_time": "extra-high",
            },
        )
    if (
        STATE.is_pro_transport(str(config.transport or ""))
        and str(config.thinking_time or "").strip().casefold() != STATE.PRO_THINKING_TIME
    ):
        raise OracleRunError(
            "PRO_THINKING_TIME_LEGACY_FORBIDDEN",
            "new Pro launches require the exact visible Pro tier; legacy Heavy is recovery-only",
            {
                "transport": config.transport,
                "thinking_time": config.thinking_time,
                "required_thinking_time": STATE.PRO_THINKING_TIME,
            },
        )
    validate_oracle_attachment_sizes(config)
    layout = STATE.create_layout(config, run_id=config.requested_run_id)
    transport_mission_path = layout.run_dir / "mission.md"
    # The app reads the project mission. The copied bytes below are host-only
    # immutable evidence and are never exposed as the workspace handoff path.
    prompt = STATE.composer_prompt(
        config,
        config.mission_path,
        run_id=layout.run_id,
        slug=layout.slug,
    )
    cdp_port = _cdp_port if _cdp_port is not None else STATE.reserve_loopback_cdp_port()
    if not isinstance(cdp_port, int) or not 1024 <= cdp_port <= 65535:
        raise OracleRunError("CDP_PORT_INVALID", "internal Oracle CDP port must be a valid loopback port")
    argv = build_oracle_argv(
        config,
        layout,
        prompt,
        cdp_port=cdp_port,
        followup_parent_slug=_followup_parent_slug,
    )
    qualification_target = web_multi_devspace_qualification_target(config)
    pro_app_read_gate: dict[str, Any] | None = None
    if dry_run:
        if STATE.is_pro_readonly_transport(config.transport):
            try:
                pro_app_read_gate = pro_app_read_gate_factory(
                    qualification_target,
                    str(config.app_name or ""),
                )
            except DEVSPACE_PREFLIGHT.DevSpacePreflightError as exc:
                raise OracleRunError(exc.code, str(exc), exc.evidence) from exc
        payload = dry_run_payload(config, layout, argv, prompt)
        if pro_app_read_gate is not None:
            payload["pro_app_read_gate"] = pro_app_read_gate
        return payload

    # Direct runs retain their historical fail-before-layout preflight.  A
    # follow-up is different: its parent-side round reservation already exists
    # before execute_run is entered, so every later preflight must have a child
    # state/log home.  Otherwise a bounded local failure consumes the round key
    # while leaving no durable explanation or exact child to settle.
    if _followup_binding is None:
        if STATE.is_pro_readonly_transport(config.transport):
            try:
                pro_app_read_gate = pro_app_read_gate_factory(
                    qualification_target,
                    str(config.app_name or ""),
                )
            except DEVSPACE_PREFLIGHT.DevSpacePreflightError as exc:
                raise OracleRunError(exc.code, str(exc), exc.evidence) from exc
        if STATE.is_devspace_transport(config.transport):
            try:
                devspace_qualification_factory(qualification_target)
            except DEVSPACE_PREFLIGHT.DevSpacePreflightError as exc:
                raise OracleRunError(exc.code, str(exc), exc.evidence) from exc
        STATE.cleanup_prior_boot_browser_temps(config.run_root, platform_name=platform_name)

    browser_timeout_seconds = browser_observer_timeout_seconds(config, argv)
    status_audit_seconds = float(config.status_audit_seconds)
    mission_bytes = config.mission_path.read_bytes()
    actual_mission_sha256 = hashlib.sha256(mission_bytes).hexdigest()
    if actual_mission_sha256 != config.mission_sha256:
        raise OracleRunError(
            "MISSION_CHANGED_BEFORE_PREPARE",
            "mission bytes changed after manifest validation",
            {"expected": config.mission_sha256, "actual": actual_mission_sha256},
        )
    for attachment, expected in zip(config.attachments, config.attachment_sha256s):
        actual = STATE.sha256_file(attachment)
        if actual != expected:
            raise OracleRunError(
                "ATTACHMENT_CHANGED_BEFORE_PREPARE",
                "attachment bytes changed after manifest validation",
                {"path": str(attachment), "expected": expected, "actual": actual},
            )
    layout.run_dir.mkdir(parents=True, exist_ok=False)
    transport_mission_path.write_bytes(mission_bytes)
    STATE.write_json_atomic(
        layout.state_path,
        STATE.state_payload(
            config,
            layout,
            status="prepared",
            resolved_version="unresolved",
            cdp_port=cdp_port,
        ),
    )
    if pro_app_read_gate is not None:
        STATE.update_state(
            layout.state_path,
            pro_app_read_gate=pro_app_read_gate,
        )
    if _followup_binding is not None:
        STATE.persist_followup_binding(layout.state_path, _followup_binding)
    layout.stdout_path.touch()
    layout.stderr_path.touch()
    oracle_env = STATE.browser_temp_environment(layout.browser_temp_path, platform_name=platform_name)
    configure_task_outcome_terminal_contract(oracle_env, config.task_outcome_contract)
    terminal_watchdog_enabled = oracle_env.get("ORACLE_TASK_OUTCOME_TERMINAL_CONTRACT") == "v1"
    STATE.update_state(
        layout.state_path,
        status="prepared",
        terminal_watchdog={
            "schema": "codex.chatgpt.oracle-terminal-watchdog/v1",
            "contract": config.task_outcome_contract,
            "environment_enabled": terminal_watchdog_enabled,
        },
    )
    if config.task_outcome_contract == "v1" and not terminal_watchdog_enabled:
        raise OracleRunError(
            "ORACLE_TERMINAL_WATCHDOG_DISABLED",
            "the exact v1 TASK_OUTCOME contract was not enabled in the Oracle child environment",
        )
    exit_code: int | None = None
    oracle_process_pid: int | None = None
    prior_audit_observations: dict[str, dict[str, Any]] = {}
    try:
        if _followup_binding is not None and STATE.is_pro_readonly_transport(config.transport):
            try:
                pro_app_read_gate = pro_app_read_gate_factory(
                    qualification_target,
                    str(config.app_name or ""),
                )
            except DEVSPACE_PREFLIGHT.DevSpacePreflightError as exc:
                raise OracleRunError(exc.code, str(exc), exc.evidence) from exc
            STATE.update_state(
                layout.state_path,
                pro_app_read_gate=pro_app_read_gate,
            )
        if _followup_binding is not None and STATE.is_devspace_transport(config.transport):
            try:
                devspace_qualification_factory(qualification_target)
            except DEVSPACE_PREFLIGHT.DevSpacePreflightError as exc:
                raise OracleRunError(exc.code, str(exc), exc.evidence) from exc
        if _followup_binding is not None:
            STATE.cleanup_prior_boot_browser_temps(config.run_root, platform_name=platform_name)
        version = version_resolver(
            config.oracle_command,
            run_factory=run_factory,
            platform_name=platform_name,
        )
        compat_factory(version)
        if STATE.is_devspace_transport(config.transport):
            devspace_compat = devspace_compat_factory()
            if devspace_compat.get("service_restart_required"):
                raise OracleRunError(
                    "DEVSPACE_SERVICE_RESTART_REQUIRED",
                    "DevSpace was safely patched before submission and must be restarted once",
                    {"package_roots": devspace_compat.get("package_roots", [])},
                )
        STATE.update_state(layout.state_path, status="prepared", resolved_version=version)
    except Exception as exc:
        code = (
            f"{exc.code}: "
            if isinstance(exc, OracleRunError)
            else "ORACLE_VERSION_TIMEOUT: " if isinstance(exc, subprocess.TimeoutExpired) else ""
        )
        append_error(layout.stderr_path, f"version resolution failed: {code}{exc}")
        STATE.write_transcript(layout)
        failed = STATE.update_state(layout.state_path, status="failed")
        settled = STATE.settle_proven_pre_submit_failure(layout.state_path)
        if settled is not None:
            STATE.cleanup_owned_browser_temp(layout.browser_temp_path)
            return {
                "ok": False,
                "status": "pre_submit_failed",
                "safe_for_fresh_run": True,
                "run_dir": str(layout.run_dir),
                "result": settled,
            }
        return {
            "ok": False,
            "run_dir": str(layout.run_dir),
            "result": failed,
        }

    try:
        with layout.stdout_path.open("wb") as stdout_handle, layout.stderr_path.open("wb") as stderr_handle:
            mutex_root = (
                config.project_root / ".oracle-parallel-submit" / str(config.parallel_parent_id)
                if config.parallel_parent_id
                else config.project_root
            )
            with STATE.project_submit_mutex(
                mutex_root,
                timeout_seconds=config.submit_mutex_timeout_seconds,
                platform_name=platform_name,
                source_thread_id=config.source_thread_id,
            ):
                owners = STATE.unresolved_project_sessions(
                    config.run_root,
                    config.project_root,
                    parallel_parent_id=config.parallel_parent_id,
                    exclude_run_id=layout.run_id,
                    source_thread_id=config.source_thread_id,
                )
                if owners:
                    raise OracleRunError(
                        "PROJECT_SESSION_STILL_LIVE",
                        "an exact Oracle session still owns this project; recover it before submitting",
                        {"owners": owners},
                    )
                original_mission_sha256 = STATE.sha256_file(config.mission_path)
                current_mission_sha256 = STATE.sha256_file(transport_mission_path)
                if original_mission_sha256 != config.mission_sha256 or current_mission_sha256 != config.mission_sha256:
                    raise OracleRunError(
                        "MISSION_CHANGED_BEFORE_SUBMIT",
                        "mission bytes changed after manifest validation",
                        {
                            "expected": config.mission_sha256,
                            "original_actual": original_mission_sha256,
                            "evidence_actual": current_mission_sha256,
                        },
                    )
                for attachment, expected in zip(config.attachments, config.attachment_sha256s):
                    actual = STATE.sha256_file(attachment)
                    if actual != expected:
                        raise OracleRunError(
                            "ATTACHMENT_CHANGED_BEFORE_SUBMIT",
                            "attachment bytes changed after manifest validation",
                            {"path": str(attachment), "expected": expected, "actual": actual},
                        )
                if _expected_manifest_sha256 is not None:
                    assert _followup_parent_run_dir is not None
                    manifest_directory = _assert_followup_artifact_directory(
                        _followup_parent_run_dir,
                        "followup-manifests",
                    )
                    if manifest_path.parent != manifest_directory:
                        raise OracleRunError(
                            "FOLLOWUP_MANIFEST_PATH_INVALID",
                            "the exact follow-up manifest escaped before submit",
                            {"path": str(manifest_path), "expected_parent": str(manifest_directory)},
                        )
                    if manifest_path.is_symlink():
                        raise OracleRunError(
                            "FOLLOWUP_MANIFEST_SYMLINK_FORBIDDEN",
                            "the exact follow-up manifest became a symlink before submit",
                            {"path": str(manifest_path)},
                        )
                    actual_manifest_sha256 = STATE.sha256_file(manifest_path)
                    if actual_manifest_sha256 != _expected_manifest_sha256:
                        raise OracleRunError(
                            "FOLLOWUP_MANIFEST_CHANGED_BEFORE_SUBMIT",
                            "the exact follow-up manifest changed after child preparation",
                            {"expected": _expected_manifest_sha256, "actual": actual_manifest_sha256},
                        )
                if _expected_followup_mission_sha256 is not None:
                    actual_mission_sha256 = STATE.sha256_file(config.mission_path)
                    if actual_mission_sha256 != _expected_followup_mission_sha256:
                        raise OracleRunError(
                            "FOLLOWUP_MISSION_CHANGED_BEFORE_SUBMIT",
                            "the exact follow-up mission changed after child preparation",
                            {
                                "expected": _expected_followup_mission_sha256,
                                "actual": actual_mission_sha256,
                            },
                        )
                process = popen_factory(
                    argv,
                    cwd=str(config.project_root),
                    env=oracle_env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    shell=False,
                    **STATE.windows_subprocess_kwargs(platform_name=platform_name),
                )
                raw_pid = getattr(process, "pid", None)
                oracle_process_pid = int(raw_pid) if isinstance(raw_pid, int) else None
                STATE.update_state(
                    layout.state_path,
                    status="running",
                    resolved_version=version,
                    session_authority="submitted_unknown",
                    browser_observer={
                        "status": "running",
                        "timeout_seconds": browser_timeout_seconds,
                        "timeout_is_terminal": False,
                        "oracle_process_pid": oracle_process_pid,
                    },
                    status_audit={
                        "threshold_kind": "caution-status-audit",
                        "threshold_seconds": status_audit_seconds,
                        "audit_count": 0,
                        "time_alone_is_terminal": False,
                        "decision": "wait-for-first-audit-threshold",
                    },
                )
                # This receipt exists before a submitted browser can be
                # observed.  A later exact metadata probe appends the Chrome
                # PID/profile/port/target/conversation tuple only if every
                # field matches this run's sealed ownership tuple.
                STATE.persist_ownership_receipt(
                    layout.state_path,
                    oracle_process_pid=oracle_process_pid,
                )
                STATE.capture_browser_identity_receipt(layout.state_path)
                audit_callback = lambda count: record_exact_run_status_audit(
                    layout,
                    process=process,
                    audit_count=count,
                    status_audit_seconds=status_audit_seconds,
                    prior_observations=prior_audit_observations,
                )
                if not config.parallel_parent_id:
                    exit_code = wait_for_oracle_process(
                        process,
                        status_audit_seconds,
                        on_status_audit=audit_callback,
                        terminal_harvest_probe=lambda: exact_run_is_durably_terminal(layout),
                        terminate_owned_process=lambda owned: terminate_owned_oracle_process_tree(
                            owned, platform_name=platform_name
                        ),
                        runtime_identity_probe=lambda: STATE.capture_browser_identity_receipt(layout.state_path),
                    )
            if config.parallel_parent_id:
                exit_code = wait_for_oracle_process(
                    process,
                    status_audit_seconds,
                    on_status_audit=audit_callback,
                    terminal_harvest_probe=lambda: exact_run_is_durably_terminal(layout),
                    terminate_owned_process=lambda owned: terminate_owned_oracle_process_tree(
                        owned, platform_name=platform_name
                    ),
                    runtime_identity_probe=lambda: STATE.capture_browser_identity_receipt(layout.state_path),
                )
    except Exception as exc:
        code = f"{exc.code}: " if isinstance(exc, OracleRunError) else ""
        append_error(layout.stderr_path, f"Oracle launch/run failed: {code}{exc}")
        STATE.write_transcript(layout)
        latest = STATE.load_state(layout.state_path)
        if latest.get("session_authority") == "pre_submit":
            STATE.cleanup_owned_browser_temp(layout.browser_temp_path)
        return {"ok": False, "run_dir": str(layout.run_dir), "result": STATE.update_state(layout.state_path, status="failed")}
    STATE.write_transcript(layout)
    STATE.capture_browser_identity_receipt(layout.state_path)
    # Exact recovery is allowed to finish under its own run-scoped mutex while
    # this original observer still owns the submission mutex.  If recovery won
    # that race, the stale observer must not overwrite durable terminal state
    # when its child process eventually exits.
    latest_after_wait = STATE.load_state(layout.state_path)
    latest_output = Path(str(latest_after_wait.get("artifacts", {}).get("output") or layout.output_path))
    if (
        latest_after_wait.get("status") == "complete"
        and latest_after_wait.get("session_authority") == "terminal"
        and latest_after_wait.get("terminal_harvested") is True
        and STATE.output_is_nonempty(latest_output)
    ):
        STATE.cleanup_owned_browser_temp(layout.browser_temp_path)
        return {
            "ok": True,
            "status": "complete",
            "run_dir": str(layout.run_dir),
            "result": latest_after_wait,
            "output_path": str(latest_output),
            "monotonic_race_preserved": True,
        }
    pre_submit_failure = STATE.settle_proven_pre_submit_failure(layout.state_path)
    if pre_submit_failure is not None:
        STATE.cleanup_owned_browser_temp(layout.browser_temp_path)
        status = "pre_submit_rejected" if pre_submit_failure.get("pre_submit_rejection") else "pre_submit_failed"
        return {
            "ok": False,
            "status": status,
            "safe_for_fresh_run": True,
            "run_dir": str(layout.run_dir),
            "result": pre_submit_failure,
        }
    # Once Oracle has been launched, a nonzero local exit does not prove that
    # the exact web session failed or stopped.  In particular, Oracle's
    # explicit assistant-response timeout is evidence that the response was
    # still pending at the observer deadline. Preserve live authority and
    # wait passively; do not prompt a harvest/live relaunch while it works.
    delivery_timeout = provider_delivery_timed_out(layout.stdout_path, layout.stderr_path)
    transport_complete = (
        exit_code == 0
        and STATE.output_is_nonempty(layout.output_path)
        and not delivery_timeout
    )
    task_outcome = (
        STATE.classify_task_outcome(
            layout.output_path,
            contract=config.task_outcome_contract,
            transport=config.transport,
        )
        if transport_complete
        else "pending"
    )
    semantic_complete = task_outcome in {
        "executed",
        "not_applicable",
        "legacy_unclassified",
    }
    status = "complete" if transport_complete and semantic_complete else "attention_required"
    if transport_complete:
        provider_session = STATE.provider_session_evidence(layout.state_path)
        state = STATE.update_state(
            layout.state_path,
            status=status,
            exit_code=exit_code,
            session_authority="terminal",
            terminal_harvested=True,
            artifact_sha256=STATE.sha256_file(layout.output_path),
            transport_status="complete",
            task_outcome=task_outcome,
            task_outcome_reason=(
                "explicit-output-marker"
                if task_outcome in {"executed", "not_executed", "blocked"}
                else task_outcome
            ),
            provider_session=provider_session,
            browser_observer={
                "status": "process-exited",
                "timeout_seconds": browser_timeout_seconds,
                "oracle_process_pid": oracle_process_pid,
                "timeout_is_terminal": False,
            },
        )
        STATE.cleanup_owned_browser_temp(layout.browser_temp_path)
    else:
        response_timeout = post_submit_response_timed_out(
            layout.stdout_path, layout.stderr_path
        )
        provider_session = STATE.provider_session_evidence(layout.state_path)
        state = STATE.update_state(
            layout.state_path,
            status="running" if response_timeout or delivery_timeout else status,
            exit_code=exit_code,
            session_authority="live" if response_timeout or delivery_timeout else "submitted_unknown",
            transport_status=(
                "post_submit_response_timeout"
                if response_timeout
                else "post_submit_provider_delivery_timeout"
                if delivery_timeout
                else "failed" if exit_code else "incomplete"
            ),
            task_outcome=task_outcome,
            task_outcome_reason=(
                "assistant-response-timeout-passive-wait"
                if response_timeout
                else "provider-delivery-timeout-passive-wait"
                if delivery_timeout
                else None
            ),
            provider_session=provider_session,
            browser_observer={
                "status": "process-exited",
                "timeout_seconds": browser_timeout_seconds,
                "oracle_process_pid": oracle_process_pid,
                "timeout_is_terminal": False,
            },
        )
    response_timeout = post_submit_response_timed_out(layout.stdout_path, layout.stderr_path)
    if not transport_complete and (response_timeout or delivery_timeout):
        recovery = exact_recovery_factory or recover_run
        try:
            recovery_kwargs = {
                "action": "live",
                "platform_name": platform_name,
                "settle_timeout_seconds": status_audit_seconds,
            }
            if config.source_thread_id is not None:
                recovery_kwargs["source_thread_id"] = config.source_thread_id
            recovered = recovery(
                layout.run_dir,
                **recovery_kwargs,
            )
        except Exception as exc:
            append_error(layout.stderr_path, f"automatic exact-session live recovery failed: {exc}")
            return {
                "ok": False,
                "status": "exact_session_recovery_unavailable",
                "safe_for_fresh_run": False,
                "run_dir": str(layout.run_dir),
                "next_action": "preserve the exact slug and retry exact-session observation only; never replace or resubmit",
                "result": STATE.load_state(layout.state_path),
            }
        return {
            **recovered,
            "automatic_exact_session_recovery": True,
            "safe_for_fresh_run": False,
            "original_observer_status": (
                "post_submit_response_timeout" if response_timeout else "post_submit_provider_delivery_timeout"
            ),
        }
    return {"ok": status == "complete", "run_dir": str(layout.run_dir), "result": state}


def recovery_argv(command: Sequence[str], locator: str, action: str, output_path: Path) -> list[str]:
    if action not in {"harvest", "live"}:
        raise OracleRunError("RECOVERY_ACTION_INVALID", "recovery action must be harvest or live")
    # Oracle's bounded browser recovery reopens only the exact conversation URL
    # persisted under this slug.  Do not pass --no-recover here: it disables
    # that safe harvest path and leaves a dead CDP endpoint as ECONNREFUSED.
    argv = [*command, "session", locator, f"--{action}", "--write-output", str(output_path)]
    if "restart" in argv or "--prompt" in argv or "-p" in argv:
        raise OracleRunError("RECOVERY_COMMAND_UNSAFE", "recovery must not restart or submit a new prompt")
    return argv


def recovered_browser_observer(
    state: dict[str, Any],
    *,
    action: str,
    exact_session_state: str | None,
    terminal_harvested: bool,
) -> dict[str, Any]:
    """Reconcile the host observer with stronger exact-session recovery evidence.

    The original Oracle process can leave ``browser_observer.status=running``
    after a later exact-slug recovery proves that the provider is terminal.
    Preserve the original PID/timeout as diagnostic history, but never leave a
    live observer label beside terminal-harvested authority.
    """
    prior = state.get("browser_observer")
    observer = dict(prior) if isinstance(prior, dict) else {}
    observer.update({
        "status": (
            "exact-recovery-terminal-harvested"
            if terminal_harvested
            else "exact-recovery-terminal-observed"
        ),
        "timeout_is_terminal": False,
        "recovery_action": action,
        "exact_session_state": exact_session_state,
    })
    return observer


def _recover_run_locked(
    run_dir: Path,
    *,
    action: str,
    dry_run: bool = False,
    oracle_command: Sequence[str] | None = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    platform_name: str | None = None,
    live_settle_timeout_seconds: float = 0,
) -> dict[str, Any]:
    directory = run_dir.expanduser().resolve(strict=True)
    state = STATE.load_state(directory / "state.json")
    pre_submit_failure = STATE.settle_proven_pre_submit_failure(directory / "state.json")
    if pre_submit_failure is not None:
        STATE.cleanup_owned_browser_temp(Path(str(pre_submit_failure["artifacts"]["browser_temp"])))
        status = "pre_submit_rejected" if pre_submit_failure.get("pre_submit_rejection") else "pre_submit_failed"
        return {
            "ok": False,
            "status": status,
            "safe_for_fresh_run": True,
            "run_dir": str(directory),
            "action": "none",
            "result": pre_submit_failure,
        }
    browser_identity_mode = require_bound_browser_identity(
        directory / "state.json",
        state,
        recovery_action=action,
    )
    historical_authority = historical_session_authority(directory, state)
    historical_url = historical_conversation_url(directory, state)
    terminal_evidence_revoked = (
        historical_authority == "live"
        and str(state.get("session_authority") or "") in {"terminal_observed", "terminal"}
    )
    if (
        STATE.SESSION_AUTHORITY_RANK.get(historical_authority, -1)
        > STATE.SESSION_AUTHORITY_RANK.get(str(state.get("session_authority") or ""), -1)
        or (historical_url and not str((state.get("oracle") or {}).get("conversation_url") or "").strip())
        or terminal_evidence_revoked
    ):
        reconciled_status = (
            "running"
            if terminal_evidence_revoked
            else "complete"
            if state.get("status") == "complete"
            and state.get("session_authority") == "terminal"
            and state.get("terminal_harvested") is True
            and STATE.output_is_nonempty(Path(str(state.get("artifacts", {}).get("output") or "")))
            else "attention_required"
        )
        state = STATE.update_state(
            directory / "state.json",
            status=reconciled_status,
            exit_code=state.get("exit_code"),
            session_authority=historical_authority,
            terminal_harvested=False if terminal_evidence_revoked else state.get("terminal_harvested"),
            artifact_sha256=None if terminal_evidence_revoked else state.get("artifact_sha256"),
            transport_status=(
                "post_submit_provider_delivery_timeout"
                if terminal_evidence_revoked
                else state.get("transport_status")
            ),
            task_outcome="pending" if terminal_evidence_revoked else state.get("task_outcome"),
            task_outcome_reason=(
                "provider-delivery-timeout-passive-wait"
                if terminal_evidence_revoked
                else state.get("task_outcome_reason")
            ),
            conversation_url=historical_url,
        )
    if (
        state.get("status") == "complete"
        and state.get("session_authority") == "terminal"
        and state.get("terminal_harvested") is True
        and STATE.output_is_nonempty(Path(str(state["artifacts"]["output"])))
    ):
        outcome = str(state.get("task_outcome") or "legacy_unclassified")
        return {
            "ok": outcome in {"executed", "not_applicable", "legacy_unclassified"},
            "status": "complete",
            "run_dir": str(directory),
            "action": "none",
            "result": state,
            "output_path": str(state["artifacts"]["output"]),
            "monotonic_noop": True,
        }
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
    if not locator:
        raise OracleRunError("SESSION_LOCATOR_MISSING", "run state has no Oracle session locator")
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output_path = Path(str(artifacts.get("output") or (directory / "output.md"))).expanduser().resolve()
    if not STATE.is_within(STATE.oracle_state_root(), output_path):
        raise OracleRunError("RECOVERY_OUTPUT_OUTSIDE_HOST_STATE", "recovery output must remain inside host-only Oracle state")
    stored_command = oracle.get("command")
    command = STATE.validate_oracle_command(list(oracle_command) if oracle_command is not None else stored_command)
    argv_output = directory / f"recovery-{action}-candidate.md"
    argv = recovery_argv(command, locator, action, argv_output)
    if dry_run:
        return {
            "ok": True,
            "status": "dry-run",
            "run_dir": str(directory),
            "action": action,
            "argv": STATE.command_for_display(argv),
            "browser_identity_mode": browser_identity_mode,
        }
    stdout_path = directory / f"recovery-{action}-stdout.log"
    stderr_path = directory / f"recovery-{action}-stderr.log"
    recovery_browser_temp = directory / f"recovery-{action}-browser-temp"
    recovery_env = STATE.browser_temp_environment(recovery_browser_temp, platform_name=platform_name)
    configure_task_outcome_terminal_contract(
        recovery_env,
        str(state.get("task_outcome_contract") or "legacy"),
    )
    if action == "live" and live_settle_timeout_seconds > 0:
        # The compatibility-patched Oracle live tail owns one recovered browser
        # connection until this deadline.  Do not turn a live recovery into a
        # sequence of short probes that each reopen the exact conversation.
        recovery_env["ORACLE_LIVE_TERMINAL_TIMEOUT_MS"] = str(
            max(1, round(live_settle_timeout_seconds * 1000))
        )
    try:
        with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
            process = popen_factory(
                argv,
                cwd=str(state["project_root"]),
                env=recovery_env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                shell=False,
                **STATE.windows_subprocess_kwargs(platform_name=platform_name),
            )
            exit_code = int(process.wait())
    finally:
        STATE.cleanup_owned_browser_temp(recovery_browser_temp)
    pre_submit_absence = STATE.settle_pre_submit_session_absent(
        directory / "state.json",
        locator=locator,
        recovery_stdout=stdout_path,
        recovery_stderr=stderr_path,
    )
    if pre_submit_absence is not None:
        if argv_output.exists():
            argv_output.unlink()
        return {
            "ok": False,
            "status": "pre_submit_session_absent",
            "safe_for_fresh_run": True,
            "run_dir": str(directory),
            "action": action,
            "exit_code": exit_code,
            "result": pre_submit_absence,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }
    observed_session_state = exact_session_state(stdout_path)
    observed_conversation_url = exact_session_url(stdout_path)
    url_conflict = conversation_url_conflict(state, observed_conversation_url)
    if url_conflict is not None:
        if argv_output.exists():
            argv_output.unlink()
        updated = STATE.update_state(
            directory / "state.json",
            status="attention_required",
            exit_code=exit_code,
            session_authority=str(state.get("session_authority") or "submitted_unknown"),
            conversation_url_conflict=url_conflict,
        )
        return {
            "ok": False,
            "status": "recovery_identity_conflict",
            "run_dir": str(directory),
            "action": action,
            "exit_code": exit_code,
            "exact_session_state": observed_session_state,
            "conversation_url_conflict": url_conflict,
            "result": updated,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "next_action": "preserve the persisted exact conversation binding; never replace or resubmit",
        }
    if exact_recovery_binding_unavailable(stdout_path, stderr_path):
        if argv_output.exists():
            argv_output.unlink()
        # Preserve the exact no-live-tab/no-saved-URL observation as immutable
        # evidence.  This does not settle or release the submitted-unknown
        # lock; a later explicit user attestation is still required.
        STATE.persist_direct_devspace_prompt_not_observed_recovery(
            directory / "state.json"
        )
        updated = STATE.update_state(
            directory / "state.json",
            status="attention_required",
            exit_code=exit_code,
            session_authority="submitted_unknown",
            conversation_url=observed_conversation_url,
        )
        return {
            "ok": False,
            "status": "recovery_binding_unavailable",
            "run_dir": str(directory),
            "action": action,
            "exit_code": exit_code,
            "exact_session_state": observed_session_state,
            "result": updated,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "next_action": (
                "preserve the exact run; only an explicit user confirmation of no submission "
                "may settle this no-binding observation, otherwise never replace or resubmit"
            ),
        }
    if provider_delivery_timed_out(stdout_path, stderr_path):
        if argv_output.exists():
            argv_output.unlink()
        updated = STATE.update_state(
            directory / "state.json",
            status="running",
            exit_code=exit_code,
            session_authority="live",
            terminal_harvested=False,
            artifact_sha256=None,
            transport_status="post_submit_provider_delivery_timeout",
            task_outcome="pending",
            task_outcome_reason="provider-delivery-timeout-passive-wait",
            conversation_url=observed_conversation_url,
        )
        return {
            "ok": False,
            "status": "provider_delivery_timeout",
            "run_dir": str(directory),
            "action": action,
            "exit_code": exit_code,
            "exact_session_state": observed_session_state,
            "result": updated,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "next_action": "preserve and continue exact-session live monitoring; never replace or resubmit",
        }
    if observed_session_state in LIVE_SESSION_STATES:
        if argv_output.exists():
            argv_output.unlink()
        prior_authority = str(state.get("session_authority") or "")
        updated = STATE.update_state(
            directory / "state.json",
            status="running",
            exit_code=exit_code,
            session_authority="live",
            conversation_url=observed_conversation_url,
            exact_live_observation=True,
        )
        settle_disagreement = str(updated.get("session_authority") or "") in {
            "terminal_observed", "terminal",
        }
        return {
            "ok": False,
            "status": "terminal_settle_disagreement" if settle_disagreement else "session_live",
            "run_dir": str(directory),
            "action": action,
            "exit_code": exit_code,
            "exact_session_state": observed_session_state,
            "prior_session_authority": prior_authority,
            "session_authority": updated.get("session_authority"),
            "result": updated,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }
    candidate_satisfies_schema = pro_output_satisfies_required_schema(state, argv_output)
    if action == "live" and not (
        exit_code == 0
        and observed_session_state in TERMINAL_SESSION_STATES
        and STATE.output_is_nonempty(argv_output)
        and candidate_satisfies_schema
    ):
        if argv_output.exists():
            argv_output.unlink()
        authority = "terminal_observed" if observed_session_state in TERMINAL_SESSION_STATES else "submitted_unknown"
        updated = STATE.update_state(
            directory / "state.json",
            status="attention_required",
            exit_code=exit_code,
            session_authority=authority,
            conversation_url=observed_conversation_url,
            browser_observer=(
                recovered_browser_observer(
                    state,
                    action=action,
                    exact_session_state=observed_session_state,
                    terminal_harvested=False,
                )
                if authority == "terminal_observed"
                else state.get("browser_observer")
            ),
        )
        return {
            "ok": False,
            "status": "terminal_observed" if authority == "terminal_observed" else "attention_required",
            "run_dir": str(directory),
            "action": action,
            "exit_code": exit_code,
            "exact_session_state": observed_session_state,
            "result": updated,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }
    if (
        exit_code == 0
        and observed_session_state in TERMINAL_SESSION_STATES
        and STATE.output_is_nonempty(argv_output)
        and candidate_satisfies_schema
    ):
        os.replace(argv_output, output_path)
    layout = STATE.RunLayout(
        str(state["run_id"]),
        str(oracle.get("slug") or locator),
        directory,
        directory / "state.json",
        output_path,
        Path(str(artifacts.get("transcript") or (directory / "transcript.md"))),
        Path(str(artifacts.get("stdout") or (directory / "stdout.log"))),
        Path(str(artifacts.get("stderr") or (directory / "stderr.log"))),
        Path(str(artifacts.get("browser_temp") or (directory / "browser-temp"))).resolve(),
    )
    STATE.write_transcript(layout)
    harvested = (
        exit_code == 0
        and observed_session_state in TERMINAL_SESSION_STATES
        and STATE.output_is_nonempty(output_path)
        and candidate_satisfies_schema
    )
    # A failed recovery process is also not web-terminal evidence. Only an
    # exact terminal observation plus a nonempty durable output may complete.
    contract = str(state.get("task_outcome_contract") or "legacy")
    transport = str(state.get("transport") or "devspace")
    task_outcome = (
        STATE.classify_task_outcome(output_path, contract=contract, transport=transport)
        if harvested
        else "pending"
    )
    semantic_complete = task_outcome in {
        "executed",
        "not_applicable",
        "legacy_unclassified",
    }
    status = "complete" if harvested and semantic_complete else "attention_required"
    latest = STATE.load_state(layout.state_path)
    latest_output = Path(str(latest.get("artifacts", {}).get("output") or output_path))
    if latest.get("status") == "complete" and STATE.output_is_nonempty(latest_output):
        return {
            "ok": True,
            "status": "complete",
            "run_dir": str(directory),
            "action": action,
            "exit_code": exit_code,
            "result": latest,
            "output_path": str(latest_output),
            "monotonic_race_preserved": True,
        }
    updated = STATE.update_state(
        layout.state_path,
        status=status,
        exit_code=exit_code,
        session_authority="terminal" if harvested else (
            "terminal_observed" if observed_session_state in TERMINAL_SESSION_STATES else "submitted_unknown"
        ),
        terminal_harvested=harvested,
        artifact_sha256=STATE.sha256_file(output_path) if harvested else None,
        transport_status="complete" if harvested else "incomplete",
        task_outcome=task_outcome,
        task_outcome_reason=(
            "explicit-output-marker"
            if task_outcome in {"executed", "not_executed", "blocked"}
            else task_outcome
        ),
        conversation_url=observed_conversation_url,
        browser_observer=(
            recovered_browser_observer(
                state,
                action=action,
                exact_session_state=observed_session_state,
                terminal_harvested=harvested,
            )
            if observed_session_state in TERMINAL_SESSION_STATES
            else state.get("browser_observer")
        ),
    )
    if harvested:
        STATE.cleanup_owned_browser_temp(layout.browser_temp_path)
    return {
        "ok": status == "complete",
        "status": "pro_output_incomplete" if (
            not harvested
            and observed_session_state in TERMINAL_SESSION_STATES
            and not candidate_satisfies_schema
        ) else status,
        "run_dir": str(directory),
        "action": action,
        "exit_code": exit_code,
        "result": updated,
        "output_path": str(output_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def adjudicate_task_outcome(
    run_dir: Path,
    *,
    expected_output_sha256: str,
    task_outcome: str,
    reason: str,
) -> dict[str, Any]:
    directory = run_dir.expanduser().resolve(strict=True)
    state_path = directory / "state.json"
    state = STATE.load_state(state_path)
    output_path = Path(str((state.get("artifacts") or {}).get("output") or ""))
    if not output_path.is_file() or not STATE.is_within(STATE.oracle_state_root(), output_path.resolve()):
        raise OracleRunError(
            "ADJUDICATION_OUTPUT_INVALID",
            "exact run output is unavailable or outside host state",
        )
    actual = STATE.sha256_file(output_path)
    if actual != expected_output_sha256.strip().casefold():
        raise OracleRunError(
            "ADJUDICATION_OUTPUT_HASH_MISMATCH",
            "exact output changed before task outcome adjudication",
            {"expected": expected_output_sha256, "actual": actual},
        )
    normalized = task_outcome.strip().casefold()
    if normalized not in {"executed", "not_executed", "blocked", "unknown"}:
        raise OracleRunError(
            "ADJUDICATION_TASK_OUTCOME_INVALID",
            "task outcome must be executed, not_executed, blocked, or unknown",
        )
    if (
        str(state.get("session_authority") or "") != "terminal"
        or state.get("terminal_harvested") is not True
    ):
        raise OracleRunError(
            "ADJUDICATION_TERMINAL_REQUIRED",
            "only a durably harvested terminal run may be adjudicated",
        )
    updated = STATE.update_state(
        state_path,
        status=str(state.get("status") or "complete"),
        exit_code=state.get("exit_code"),
        transport_status="complete",
        task_outcome=normalized,
        task_outcome_reason=reason.strip() or "explicit-exact-output-adjudication",
    )
    return {
        "ok": normalized == "executed",
        "status": "task_outcome_adjudicated",
        "run_dir": str(directory),
        "output_path": str(output_path),
        "output_sha256": actual,
        "task_outcome": normalized,
        "safe_for_fresh_retry": normalized == "not_executed",
        "result": updated,
    }


def settle_user_confirmed_delivery_timeout_execution(
    run_dir: Path,
    *,
    expected_output_sha256: str,
    confirmation: str,
    reason: str,
    execution_evidence: Sequence[tuple[Path, str]],
    process_alive: Callable[[int], bool] = process_is_alive,
    platform_name: str | None = None,
) -> dict[str, Any]:
    """Settle one ended, post-submit delivery-timeout run without terminalizing it.

    This is deliberately not a recovery, harvest, or retry path.  It releases
    only a user-confirmed, hash-bound executed task after all run-owned Oracle
    and recovery-browser PIDs are gone.
    """
    if confirmation.strip().casefold() != STATE.USER_CONFIRMED_EXECUTION_ENDED:
        raise OracleRunError(
            "EXECUTION_ENDED_CONFIRMATION_REQUIRED",
            f"confirmation must be exactly {STATE.USER_CONFIRMED_EXECUTION_ENDED}",
        )
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise OracleRunError("EXECUTION_ENDED_REASON_REQUIRED", "user confirmation reason is required")
    directory = run_dir.expanduser().resolve(strict=True)
    state_path = directory / "state.json"
    state = STATE.load_state(state_path)
    timeout_evidence = provider_delivery_timeout_evidence(directory, state)
    direct_timeout_state = (
        str(state.get("transport_status") or "") == "post_submit_provider_delivery_timeout"
        and str(state.get("session_authority") or "") == "live"
    )
    stale_timeout_ledger = (
        timeout_evidence
        and str(state.get("transport_status") or "") == "incomplete"
        and str(state.get("session_authority") or "") in {"terminal_observed", "terminal"}
        and state.get("terminal_harvested") is False
    )
    if not (direct_timeout_state or stale_timeout_ledger) or state.get("terminal_harvested") is True:
        raise OracleRunError(
            "EXECUTION_ENDED_TIMEOUT_STATE_REQUIRED",
            "settlement requires a live provider-timeout state or its exact stale incomplete ledger",
        )
    if not timeout_evidence:
        raise OracleRunError(
            "EXECUTION_ENDED_TIMEOUT_EVIDENCE_REQUIRED",
            "exact run does not retain provider delivery-timeout evidence",
        )
    active_pids = [
        pid for pid in run_owned_process_ids(directory, state)
        if run_owned_process_is_alive(directory, state, pid, process_alive=process_alive)
    ]
    if active_pids:
        raise OracleRunError(
            "EXECUTION_ENDED_PROCESS_ACTIVE",
            "run-owned Oracle or recovery-browser process is still active",
            {"active_pids": active_pids},
        )
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output_path = Path(str(artifacts.get("output") or "")).expanduser().resolve()
    if not output_path.is_file() or not STATE.is_within(STATE.oracle_state_root(), output_path):
        raise OracleRunError("EXECUTION_ENDED_OUTPUT_INVALID", "exact run output is unavailable or outside host state")
    output_sha256 = STATE.sha256_file(output_path)
    if output_sha256 != expected_output_sha256.strip().casefold():
        raise OracleRunError(
            "EXECUTION_ENDED_OUTPUT_HASH_MISMATCH",
            "exact timeout output changed before execution settlement",
            {"expected": expected_output_sha256, "actual": output_sha256},
        )
    project_root = Path(str(state.get("project_root") or "")).expanduser().resolve(strict=True)
    bound_evidence: list[dict[str, str]] = []
    seen_paths: set[Path] = set()
    for candidate, expected_hash in execution_evidence:
        path = candidate.expanduser().resolve(strict=True)
        if candidate.is_symlink() or not path.is_file() or not STATE.is_within(project_root, path):
            raise OracleRunError("EXECUTION_ENDED_EVIDENCE_INVALID", "execution evidence must be a regular project file")
        if path in seen_paths:
            raise OracleRunError("EXECUTION_ENDED_EVIDENCE_DUPLICATE", "execution evidence paths must be unique")
        actual = STATE.sha256_file(path)
        if actual != expected_hash.strip().casefold():
            raise OracleRunError(
                "EXECUTION_ENDED_EVIDENCE_HASH_MISMATCH",
                "execution evidence changed before settlement",
                {"path": str(path), "expected": expected_hash, "actual": actual},
            )
        seen_paths.add(path)
        bound_evidence.append({"path": str(path), "sha256": actual})
    if not bound_evidence:
        raise OracleRunError("EXECUTION_ENDED_EVIDENCE_REQUIRED", "at least one hash-bound execution evidence file is required")
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    conversation_url = str(oracle.get("conversation_url") or "").strip()
    if not conversation_url:
        raise OracleRunError("EXECUTION_ENDED_CONVERSATION_REQUIRED", "exact conversation URL is required")
    recorded = {
        "schema": "codex.chatgpt.oracle-user-confirmed-execution-ended/v1",
        "code": "ORACLE_USER_CONFIRMED_EXECUTION_ENDED",
        "confirmation": STATE.USER_CONFIRMED_EXECUTION_ENDED,
        "reason": normalized_reason,
        "run_id": state.get("run_id"),
        "project_root": str(project_root),
        "conversation_url": conversation_url,
        "output_path": str(output_path),
        "output_sha256": output_sha256,
        "execution_evidence": bound_evidence,
        "run_owned_pids_checked": list(run_owned_process_ids(directory, state)),
    }
    settlement_path = directory / "user-confirmed-execution-ended.json"
    STATE.write_json_atomic(settlement_path, recorded)
    updated = STATE.update_state(
        state_path,
        status="complete",
        exit_code=state.get("exit_code"),
        session_authority="settled_executed",
        terminal_harvested=False,
        artifact_sha256=output_sha256,
        transport_status="post_submit_provider_delivery_timeout_settled",
        task_outcome="executed",
        task_outcome_reason="user-confirmed-execution-ended-after-provider-delivery-timeout",
    )
    updated["user_confirmed_execution_ended"] = {
        "schema": "codex.chatgpt.oracle-settlement-reference/v1",
        "path": str(settlement_path),
        "sha256": STATE.sha256_file(settlement_path),
    }
    STATE.write_json_atomic(state_path, updated)
    return {
        "ok": True,
        "status": "post_submit_execution_user_confirmed",
        "safe_for_fresh_run": True,
        "run_dir": str(directory),
        "output_sha256": output_sha256,
        "result": updated,
    }


def settle_user_confirmed_no_submission(
    run_dir: Path,
    *,
    confirmation: str,
    reason: str,
    platform_name: str | None = None,
) -> dict[str, Any]:
    """Settle one exact ambiguous send without launching or recovering Oracle."""
    directory = run_dir.expanduser().resolve(strict=True)
    state_path = directory / "state.json"
    stored = STATE.load_state(state_path)
    require_current_task_owns_run(stored)
    active_pids = [
        pid for pid in run_owned_process_ids(directory, stored)
        if run_owned_process_is_alive(directory, stored, pid)
    ]
    if active_pids:
        raise OracleRunError(
            "NO_SUBMISSION_PROCESS_ACTIVE",
            "no-submission settlement requires every exact run-owned process to be stopped",
            {"active_pids": active_pids},
        )
    source_thread_id = STATE.source_thread_id_from_state(stored)
    project_root = Path(str(stored.get("project_root") or "")).expanduser().resolve(strict=True)
    parallel_parent_id = str(stored.get("parallel_parent_id") or "").strip().casefold()
    if parallel_parent_id and STATE.PARENT_ID_RE.fullmatch(parallel_parent_id) is None:
        raise OracleRunError(
            "SETTLEMENT_PARALLEL_PARENT_ID_INVALID",
            "stored parallel parent id is invalid",
            {"parallel_parent_id": parallel_parent_id},
        )
    mutex_root = (
        project_root / ".oracle-parallel-submit" / parallel_parent_id
        if parallel_parent_id
        else project_root
    )
    with STATE.project_submit_mutex(
        mutex_root,
        timeout_seconds=30,
        platform_name=platform_name,
        source_thread_id=source_thread_id,
    ):
        settled = STATE.settle_user_confirmed_no_submission(
            state_path,
            confirmation=confirmation,
            reason=reason,
        )
        owners = STATE.unresolved_project_sessions(
            directory.parent,
            project_root,
            exclude_run_id=str(settled.get("run_id") or ""),
            source_thread_id=source_thread_id,
        )
    return {
        "ok": True,
        "status": "pre_submit_user_confirmed",
        "safe_for_fresh_run": not owners,
        "unresolved_owners": owners,
        "run_dir": str(directory),
        "result": settled,
    }


def settle_recursive_self_observation_fresh_run(
    run_dir: Path,
    *,
    confirmation: str,
    reason: str,
    expected_state_sha256: str,
    expected_output_sha256: str,
    expected_transcript_sha256: str,
    dry_run: bool = False,
    platform_name: str | None = None,
) -> dict[str, Any]:
    """Append user authority for one proven terminal self-observation failure."""
    if (
        confirmation.strip().casefold()
        != STATE.USER_AUTHORIZED_FRESH_AFTER_RECURSIVE_SELF_OBSERVATION
    ):
        raise OracleRunError(
            "RECURSIVE_SELF_OBSERVATION_CONFIRMATION_REQUIRED",
            "confirmation does not authorize a fresh run after recursive self-observation",
        )
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise OracleRunError(
            "RECURSIVE_SELF_OBSERVATION_REASON_REQUIRED",
            "an explicit user-authority reason is required",
        )
    directory = run_dir.expanduser().resolve(strict=True)
    state_path = directory / "state.json"
    state = STATE.load_state(state_path)
    require_current_task_owns_run(state)
    source_thread_id = STATE.source_thread_id_from_state(state)
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output_path = Path(str(artifacts.get("output") or directory / "output.md")).resolve(strict=True)
    transcript_path = Path(
        str(artifacts.get("transcript") or directory / "transcript.md")
    ).resolve(strict=True)
    if (
        output_path != (directory / "output.md").resolve()
        or transcript_path != (directory / "transcript.md").resolve()
        or output_path.is_symlink()
        or transcript_path.is_symlink()
    ):
        raise OracleRunError(
            "RECURSIVE_SELF_OBSERVATION_ARTIFACT_UNSAFE",
            "output and transcript must be regular non-symlink run artifacts",
        )
    actual_hashes = {
        "state_sha256": STATE.sha256_file(state_path),
        "output_sha256": STATE.sha256_file(output_path),
        "transcript_sha256": STATE.sha256_file(transcript_path),
    }
    expected_hashes = {
        "state_sha256": expected_state_sha256.strip().casefold(),
        "output_sha256": expected_output_sha256.strip().casefold(),
        "transcript_sha256": expected_transcript_sha256.strip().casefold(),
    }
    if any(actual_hashes[key] != expected_hashes[key] for key in actual_hashes):
        raise OracleRunError(
            "RECURSIVE_SELF_OBSERVATION_HASH_MISMATCH",
            "exact terminal run artifacts changed before authority settlement",
            {"expected": expected_hashes, "actual": actual_hashes},
        )
    output_text = output_path.read_text(encoding="utf-8", errors="strict")
    evidence = STATE.recursive_self_observation_evidence(state, output_text)
    if evidence is None:
        raise OracleRunError(
            "RECURSIVE_SELF_OBSERVATION_EVIDENCE_REQUIRED",
            "exact terminal output does not satisfy the bounded self-observation signature",
        )
    active_pids = [
        pid for pid in run_owned_process_ids(directory, state)
        if run_owned_process_is_alive(directory, state, pid)
    ]
    if active_pids:
        raise OracleRunError(
            "RECURSIVE_SELF_OBSERVATION_PROCESS_ACTIVE",
            "run-owned Oracle or recovery process is still active",
            {"active_pids": active_pids},
        )
    project_root = Path(str(state.get("project_root") or "")).expanduser().resolve(strict=True)
    owners = STATE.unresolved_project_sessions(
        directory.parent,
        project_root,
        exclude_run_id=str(state.get("run_id") or ""),
        source_thread_id=source_thread_id,
    )
    if owners:
        raise OracleRunError(
            "RECURSIVE_SELF_OBSERVATION_OWNER_ACTIVE",
            "another exact project session still owns submission authority",
            {"owners": owners},
        )
    existing = STATE.proven_recursive_self_observation_fresh_run_authority(state_path)
    if existing is not None:
        return {
            "ok": True,
            "status": "recursive_self_observation_fresh_run_authorized",
            "safe_for_fresh_run": True,
            "scope_released": True,
            "auto_retry": False,
            "submission_action": "none",
            "run_dir": str(directory),
            "settlement": existing,
        }
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    receipt = {
        "schema": STATE.RECURSIVE_SELF_OBSERVATION_SETTLEMENT_SCHEMA,
        "confirmation": STATE.USER_AUTHORIZED_FRESH_AFTER_RECURSIVE_SELF_OBSERVATION,
        "reason": normalized_reason,
        "run_id": state.get("run_id"),
        "project_root": str(project_root),
        "slug": oracle.get("slug"),
        "signature": evidence["signature"],
        **actual_hashes,
        "auto_retry": False,
        "submission_action": "none",
        "authorized_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    receipt_path = directory / "settlements" / "recursive-self-observation-fresh-run.json"
    preview = {
        "ok": True,
        "status": "dry-run" if dry_run else "recursive_self_observation_fresh_run_authorized",
        "safe_for_fresh_run": True,
        "scope_released": True,
        "auto_retry": False,
        "submission_action": "none",
        "run_dir": str(directory),
        "settlement_path": str(receipt_path),
        "settlement_payload": receipt,
    }
    if dry_run:
        return preview
    if receipt_path.parent.exists() and (
        receipt_path.parent.is_symlink()
        or receipt_path.parent.resolve().parent != directory
    ):
        raise OracleRunError(
            "RECURSIVE_SELF_OBSERVATION_SETTLEMENT_PATH_UNSAFE",
            "append-only settlement directory is outside the exact run",
        )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with receipt_path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise OracleRunError(
            "RECURSIVE_SELF_OBSERVATION_SETTLEMENT_EXISTS",
            "append-only settlement path already exists but did not validate",
            {"path": str(receipt_path)},
        ) from exc
    proven = STATE.proven_recursive_self_observation_fresh_run_authority(state_path)
    if proven is None:
        raise OracleRunError(
            "RECURSIVE_SELF_OBSERVATION_SETTLEMENT_INVALID",
            "written append-only settlement failed revalidation",
        )
    return {**preview, "settlement": proven, "settlement_sha256": proven["sha256"]}


def settle_terminal_devspace_nonexecution_fresh_run(
    run_dir: Path,
    *,
    confirmation: str,
    reason: str,
    expected_state_sha256: str,
    expected_output_sha256: str,
    expected_transcript_sha256: str,
    expected_stdout_sha256: str,
    expected_stderr_sha256: str,
    expected_mission_sha256: str,
    dry_run: bool = False,
    process_alive: Callable[[int], bool] = process_is_alive,
) -> dict[str, Any]:
    """Append authority for a bounded terminal DevSpace failure with no task work."""
    if (
        confirmation.strip().casefold()
        != STATE.USER_AUTHORIZED_FRESH_AFTER_TERMINAL_DEVSPACE_NONEXECUTION
    ):
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_CONFIRMATION_REQUIRED",
            "confirmation does not authorize a fresh run after terminal DevSpace nonexecution",
        )
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_REASON_REQUIRED",
            "an explicit user-authority reason is required",
        )
    authorized_thread = STATE.current_source_thread_id()
    if authorized_thread is None:
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_TASK_REQUIRED",
            "settlement must be issued from one exact Codex task",
        )
    directory = run_dir.expanduser().resolve(strict=True)
    state_path = directory / "state.json"
    if state_path.is_symlink() or state_path.resolve(strict=True) != (directory / "state.json"):
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_STATE_UNSAFE",
            "state must be the regular state.json in the exact run directory",
        )
    state = STATE.load_state(state_path)
    owner_thread = STATE.source_thread_id_from_state(state)
    if owner_thread is not None and owner_thread != authorized_thread:
        raise OracleRunError(
            "FOREIGN_TASK_SESSION",
            "a task may not settle another task's terminal nonexecution",
            {
                "owner_source_thread_id": owner_thread,
                "caller_source_thread_id": authorized_thread,
                "run_id": state.get("run_id"),
            },
        )
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    raw_paths = {
        "output": Path(str(artifacts.get("output") or directory / "output.md")),
        "transcript": Path(str(artifacts.get("transcript") or directory / "transcript.md")),
        "stdout": Path(str(artifacts.get("stdout") or directory / "stdout.log")),
        "stderr": Path(str(artifacts.get("stderr") or directory / "stderr.log")),
        "mission": directory / "mission.md",
    }
    expected_paths = {name: directory / f"{name}.md" for name in ("output", "transcript", "mission")}
    expected_paths.update({"stdout": directory / "stdout.log", "stderr": directory / "stderr.log"})
    resolved_paths: dict[str, Path] = {}
    try:
        for name, raw in raw_paths.items():
            resolved = raw.resolve(strict=True)
            if raw.is_symlink() or not resolved.is_file() or resolved != expected_paths[name].resolve():
                raise OSError(f"unsafe {name} path")
            resolved_paths[name] = resolved
    except OSError as exc:
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_ARTIFACT_UNSAFE",
            "state, mission, and stream artifacts must be regular files in the exact run",
        ) from exc
    actual_hashes = {
        "state_sha256": STATE.sha256_file(state_path),
        **{
            f"{name}_sha256": STATE.sha256_file(path)
            for name, path in resolved_paths.items()
        },
    }
    expected_hashes = {
        "state_sha256": expected_state_sha256.strip().casefold(),
        "output_sha256": expected_output_sha256.strip().casefold(),
        "transcript_sha256": expected_transcript_sha256.strip().casefold(),
        "stdout_sha256": expected_stdout_sha256.strip().casefold(),
        "stderr_sha256": expected_stderr_sha256.strip().casefold(),
        "mission_sha256": expected_mission_sha256.strip().casefold(),
    }
    if any(actual_hashes[key] != expected_hashes[key] for key in actual_hashes):
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_HASH_MISMATCH",
            "exact terminal run artifacts changed before authority settlement",
            {"expected": expected_hashes, "actual": actual_hashes},
        )
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    if mission.get("sha256") != actual_hashes["mission_sha256"]:
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_MISSION_MISMATCH",
            "the persisted mission copy no longer matches the run's mission binding",
        )
    output_text = resolved_paths["output"].read_text(encoding="utf-8", errors="strict")
    evidence = STATE.terminal_devspace_nonexecution_evidence(state, output_text)
    if evidence is None:
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_EVIDENCE_REQUIRED",
            "terminal output lacks bounded DevSpace failure and explicit nonexecution proof",
        )
    active_pids = [
        pid for pid in run_owned_process_ids(directory, state)
        if run_owned_process_is_alive(directory, state, pid, process_alive=process_alive)
    ]
    if active_pids:
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_PROCESS_ACTIVE",
            "run-owned Oracle or recovery process is still active",
            {"active_pids": active_pids},
        )
    project_root = Path(str(state.get("project_root") or "")).expanduser().resolve(strict=True)
    owners = STATE.unresolved_project_sessions(
        directory.parent,
        project_root,
        exclude_run_id=str(state.get("run_id") or ""),
        source_thread_id=authorized_thread,
    )
    if owners:
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_OWNER_ACTIVE",
            "another exact same-task project session still owns submission authority",
            {"owners": owners},
        )
    existing = STATE.proven_terminal_devspace_nonexecution_fresh_run_authority(state_path)
    if existing is not None:
        if existing.get("authorized_source_thread_id") != authorized_thread:
            raise OracleRunError(
                "TERMINAL_DEVSPACE_NONEXECUTION_AUTHORITY_CONFLICT",
                "append-only fresh-run authority belongs to a different Codex task",
            )
        return {
            "ok": True,
            "status": "terminal_devspace_nonexecution_fresh_run_authorized",
            "safe_for_fresh_run": True,
            "scope_released": True,
            "auto_retry": False,
            "submission_action": "none",
            "run_dir": str(directory),
            "settlement": existing,
        }
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    receipt = {
        "schema": STATE.TERMINAL_DEVSPACE_NONEXECUTION_SETTLEMENT_SCHEMA,
        "confirmation": STATE.USER_AUTHORIZED_FRESH_AFTER_TERMINAL_DEVSPACE_NONEXECUTION,
        "reason": normalized_reason,
        "authorized_source_thread_id": authorized_thread,
        "historical_owner_scope": "same-task" if owner_thread == authorized_thread else "legacy-unbound",
        "run_id": state.get("run_id"),
        "project_root": str(project_root),
        "slug": oracle.get("slug"),
        "transport": state.get("transport"),
        "task_outcome": state.get("task_outcome"),
        "signature": evidence["signature"],
        **actual_hashes,
        "auto_retry": False,
        "submission_action": "none",
        "authorized_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    receipt_path = directory / "settlements" / "terminal-devspace-nonexecution-fresh-run.json"
    preview = {
        "ok": True,
        "status": "dry-run" if dry_run else "terminal_devspace_nonexecution_fresh_run_authorized",
        "safe_for_fresh_run": True,
        "scope_released": True,
        "auto_retry": False,
        "submission_action": "none",
        "run_dir": str(directory),
        "settlement_path": str(receipt_path),
        "settlement_payload": receipt,
    }
    if dry_run:
        return preview
    if receipt_path.parent.exists() and (
        receipt_path.parent.is_symlink() or receipt_path.parent.resolve().parent != directory
    ):
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_SETTLEMENT_PATH_UNSAFE",
            "append-only settlement directory is outside the exact run",
        )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with receipt_path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_SETTLEMENT_EXISTS",
            "append-only settlement path already exists but did not validate",
            {"path": str(receipt_path)},
        ) from exc
    proven = STATE.proven_terminal_devspace_nonexecution_fresh_run_authority(state_path)
    if proven is None:
        raise OracleRunError(
            "TERMINAL_DEVSPACE_NONEXECUTION_SETTLEMENT_INVALID",
            "written append-only settlement failed revalidation",
        )
    return {**preview, "settlement": proven, "settlement_sha256": proven["sha256"]}


def settle_terminal_devspace_read_route_refresh_fresh_run(
    run_dir: Path,
    *,
    confirmation: str,
    reason: str,
    expected_state_sha256: str,
    expected_output_sha256: str,
    expected_transcript_sha256: str,
    expected_stdout_sha256: str,
    expected_stderr_sha256: str,
    expected_mission_sha256: str,
    dry_run: bool = False,
    process_alive: Callable[[int], bool] = process_is_alive,
) -> dict[str, Any]:
    """Authorize one fresh probe after a user-completed app-tool refresh."""
    if (
        confirmation.strip().casefold()
        != STATE.USER_AUTHORIZED_FRESH_AFTER_DEVSPACE_READ_ROUTE_REFRESH
    ):
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_CONFIRMATION_REQUIRED",
            "confirmation does not authorize a fresh probe after the app-tool refresh",
        )
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_REASON_REQUIRED",
            "an explicit user-authority reason is required",
        )
    authorized_thread = STATE.current_source_thread_id()
    if authorized_thread is None:
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_TASK_REQUIRED",
            "settlement must be issued from one exact Codex task",
        )
    directory = run_dir.expanduser().resolve(strict=True)
    state_path = directory / "state.json"
    if state_path.is_symlink() or state_path.resolve(strict=True) != (directory / "state.json"):
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_STATE_UNSAFE",
            "state must be the regular state.json in the exact run directory",
        )
    state = STATE.load_state(state_path)
    owner_thread = STATE.source_thread_id_from_state(state)
    if owner_thread != authorized_thread:
        raise OracleRunError(
            "FOREIGN_TASK_SESSION",
            "a task may settle only its own exact read-route canary",
            {
                "owner_source_thread_id": owner_thread or "legacy-unbound",
                "caller_source_thread_id": authorized_thread,
                "run_id": state.get("run_id"),
            },
        )
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    raw_paths = {
        "output": Path(str(artifacts.get("output") or directory / "output.md")),
        "transcript": Path(str(artifacts.get("transcript") or directory / "transcript.md")),
        "stdout": Path(str(artifacts.get("stdout") or directory / "stdout.log")),
        "stderr": Path(str(artifacts.get("stderr") or directory / "stderr.log")),
        "mission": directory / "mission.md",
    }
    expected_paths = {name: directory / f"{name}.md" for name in ("output", "transcript", "mission")}
    expected_paths.update({"stdout": directory / "stdout.log", "stderr": directory / "stderr.log"})
    resolved_paths: dict[str, Path] = {}
    try:
        for name, raw in raw_paths.items():
            resolved = raw.resolve(strict=True)
            if raw.is_symlink() or not resolved.is_file() or resolved != expected_paths[name].resolve():
                raise OSError(f"unsafe {name} path")
            resolved_paths[name] = resolved
    except OSError as exc:
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_ARTIFACT_UNSAFE",
            "state, mission, and stream artifacts must be regular files in the exact run",
        ) from exc
    actual_hashes = {
        "state_sha256": STATE.sha256_file(state_path),
        **{
            f"{name}_sha256": STATE.sha256_file(path)
            for name, path in resolved_paths.items()
        },
    }
    expected_hashes = {
        "state_sha256": expected_state_sha256.strip().casefold(),
        "output_sha256": expected_output_sha256.strip().casefold(),
        "transcript_sha256": expected_transcript_sha256.strip().casefold(),
        "stdout_sha256": expected_stdout_sha256.strip().casefold(),
        "stderr_sha256": expected_stderr_sha256.strip().casefold(),
        "mission_sha256": expected_mission_sha256.strip().casefold(),
    }
    if any(actual_hashes[key] != expected_hashes[key] for key in actual_hashes):
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_HASH_MISMATCH",
            "exact terminal canary artifacts changed before authority settlement",
            {"expected": expected_hashes, "actual": actual_hashes},
        )
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    mission_text = resolved_paths["mission"].read_text(encoding="utf-8", errors="strict")
    if (
        mission.get("sha256") != actual_hashes["mission_sha256"]
        or not STATE.terminal_devspace_read_route_refresh_mission_contract(mission_text)
    ):
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_MISSION_MISMATCH",
            "the immutable mission is not the bounded read-only qualification contract",
        )
    output_text = resolved_paths["output"].read_text(encoding="utf-8", errors="strict")
    evidence = STATE.terminal_devspace_read_route_refresh_evidence(state, output_text)
    if evidence is None:
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_EVIDENCE_REQUIRED",
            "terminal output does not prove the exact read-only read_chunk exposure failure",
        )
    active_pids = [
        pid for pid in run_owned_process_ids(directory, state)
        if run_owned_process_is_alive(directory, state, pid, process_alive=process_alive)
    ]
    if active_pids:
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_PROCESS_ACTIVE",
            "run-owned Oracle or recovery process is still active",
            {"active_pids": active_pids},
        )
    project_root = Path(str(state.get("project_root") or "")).expanduser().resolve(strict=True)
    owners = STATE.unresolved_project_sessions(
        directory.parent,
        project_root,
        exclude_run_id=str(state.get("run_id") or ""),
        source_thread_id=authorized_thread,
    )
    if owners:
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_OWNER_ACTIVE",
            "another exact same-task project session still owns submission authority",
            {"owners": owners},
        )
    existing = STATE.proven_terminal_devspace_read_route_refresh_fresh_run_authority(
        state_path
    )
    if existing is not None:
        if existing.get("authorized_source_thread_id") != authorized_thread:
            raise OracleRunError(
                "DEVSPACE_READ_ROUTE_REFRESH_AUTHORITY_CONFLICT",
                "append-only fresh-run authority belongs to a different Codex task",
            )
        return {
            "ok": True,
            "status": "terminal_devspace_read_route_refresh_fresh_run_authorized",
            "safe_for_fresh_run": True,
            "scope_released": True,
            "auto_retry": False,
            "submission_action": "none",
            "run_dir": str(directory),
            "settlement": existing,
        }
    settlement_name = "terminal-devspace-read-route-refresh-fresh-run.json"
    for sibling_state in sorted(directory.parent.glob("*/state.json"), key=lambda path: str(path)):
        if sibling_state.parent.resolve() == directory:
            continue
        sibling_receipt = sibling_state.parent / "settlements" / settlement_name
        if not sibling_receipt.exists():
            continue
        prior = STATE.proven_terminal_devspace_read_route_refresh_fresh_run_authority(
            sibling_state
        )
        if prior is None:
            raise OracleRunError(
                "DEVSPACE_READ_ROUTE_REFRESH_PRIOR_SETTLEMENT_INVALID",
                "a prior read-route refresh receipt exists but no longer validates",
                {"path": str(sibling_receipt)},
            )
        if (
            str(prior.get("project_root") or "").casefold() == str(project_root).casefold()
            and prior.get("authorized_source_thread_id") == authorized_thread
        ):
            raise OracleRunError(
                "DEVSPACE_READ_ROUTE_REFRESH_RETRY_ALREADY_USED",
                "the one authorized post-refresh probe was already released for this task and project",
                {"prior_run_id": prior.get("run_id"), "prior_receipt": prior.get("path")},
            )
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    receipt = {
        "schema": STATE.TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SETTLEMENT_SCHEMA,
        "confirmation": STATE.USER_AUTHORIZED_FRESH_AFTER_DEVSPACE_READ_ROUTE_REFRESH,
        "reason": normalized_reason,
        "authorized_source_thread_id": authorized_thread,
        "run_id": state.get("run_id"),
        "project_root": str(project_root),
        "slug": oracle.get("slug"),
        "transport": state.get("transport"),
        "task_outcome": state.get("task_outcome"),
        "app_name": state.get("app_name"),
        "workspace_id": evidence["workspace_id"],
        "signature": evidence["signature"],
        "retry_ordinal": 1,
        **actual_hashes,
        "auto_retry": False,
        "submission_action": "none",
        "authorized_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    receipt_path = directory / "settlements" / settlement_name
    preview = {
        "ok": True,
        "status": "dry-run" if dry_run else "terminal_devspace_read_route_refresh_fresh_run_authorized",
        "safe_for_fresh_run": True,
        "scope_released": True,
        "auto_retry": False,
        "submission_action": "none",
        "run_dir": str(directory),
        "settlement_path": str(receipt_path),
        "settlement_payload": receipt,
    }
    if dry_run:
        return preview
    if receipt_path.parent.exists() and (
        receipt_path.parent.is_symlink() or receipt_path.parent.resolve().parent != directory
    ):
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_SETTLEMENT_PATH_UNSAFE",
            "append-only settlement directory is outside the exact run",
        )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with receipt_path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_SETTLEMENT_EXISTS",
            "append-only settlement path already exists but did not validate",
            {"path": str(receipt_path)},
        ) from exc
    proven = STATE.proven_terminal_devspace_read_route_refresh_fresh_run_authority(
        state_path
    )
    if proven is None:
        raise OracleRunError(
            "DEVSPACE_READ_ROUTE_REFRESH_SETTLEMENT_INVALID",
            "written append-only settlement failed revalidation",
        )
    return {**preview, "settlement": proven, "settlement_sha256": proven["sha256"]}


def recover_run(
    run_dir: Path,
    *,
    action: str,
    dry_run: bool = False,
    oracle_command: Sequence[str] | None = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    platform_name: str | None = None,
    settle_timeout_seconds: float = 0,
    settle_interval_seconds: float = 15,
    sleep: Callable[[float], None] = time.sleep,
    source_thread_id: str | None = None,
) -> dict[str, Any]:
    directory = run_dir.expanduser().resolve(strict=True)
    stored = STATE.load_state(directory / "state.json")
    require_current_task_owns_run(stored, source_thread_id=source_thread_id)
    project_root = Path(str(stored.get("project_root") or "")).expanduser().resolve(strict=True)
    parallel_parent_id = str(stored.get("parallel_parent_id") or "").strip().casefold()
    if parallel_parent_id and STATE.PARENT_ID_RE.fullmatch(parallel_parent_id) is None:
        raise OracleRunError(
            "RECOVERY_PARALLEL_PARENT_ID_INVALID",
            "stored parallel parent id is invalid",
            {"parallel_parent_id": parallel_parent_id},
        )
    # Recovery is an exact-slug, prompt-free write to one persisted run.  Do
    # not re-enter the project submit mutex: the original browser observer may
    # still own it after a recoverable CDP disconnect even though the bound
    # provider conversation is already terminal.  A run-scoped mutex prevents
    # competing harvesters, while unresolved_project_sessions keeps every new
    # submission fail-closed until durable terminal recovery completes.
    with STATE.exact_run_recovery_mutex(
        directory,
        timeout_seconds=30,
        platform_name=platform_name,
    ):
        audit_count = 0
        while True:
            result = _recover_run_locked(
                directory,
                action=action,
                dry_run=dry_run,
                oracle_command=oracle_command,
                popen_factory=popen_factory,
                platform_name=platform_name,
                live_settle_timeout_seconds=settle_timeout_seconds if action == "live" else 0,
            )
            continue_exact = (
                action == "live"
                and not dry_run
                and settle_timeout_seconds > 0
                and result.get("status") in {"session_live", "provider_delivery_timeout"}
            )
            if not continue_exact:
                return result
            audit_count += 1
            latest = STATE.load_state(directory / "state.json")
            STATE.update_state(
                directory / "state.json",
                status="running",
                exit_code=latest.get("exit_code"),
                session_authority="live",
                status_audit={
                    "threshold_kind": "caution-status-audit",
                    "threshold_seconds": settle_timeout_seconds,
                    "audit_count": audit_count,
                    "observed_at_unix_seconds": time.time(),
                    "exact_slug": str((latest.get("oracle") or {}).get("slug") or ""),
                    "exact_session_state": result.get("exact_session_state"),
                    "decision": "continue-exact-session-live-recovery",
                    "time_alone_is_terminal": False,
                    "ownership_action": "preserve",
                    "submission_action": "none",
                },
            )
            if settle_interval_seconds > 0:
                sleep(settle_interval_seconds)


FOLLOWUP_ROUND_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _followup_archive_contract(state: dict[str, Any], conversation_url: str) -> dict[str, Any]:
    slug = str((state.get("oracle") or {}).get("slug") or "")
    session_root = Path(os.environ.get("ORACLE_SESSION_ROOT") or (Path.home() / ".oracle" / "sessions")).resolve()
    meta_path = session_root / slug / "meta.json"
    try:
        raw = meta_path.read_bytes()
        meta = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OracleRunError(
            "FOLLOWUP_PARENT_ARCHIVE_STATUS_UNVERIFIED",
            "follow-up requires exact terminal Oracle archive metadata",
        ) from exc
    archive = ((meta.get("browser") or {}).get("archive") if isinstance(meta, dict) else None)
    if not isinstance(archive, dict) or not isinstance(archive.get("archived"), bool):
        raise OracleRunError(
            "FOLLOWUP_PARENT_ARCHIVE_STATUS_UNVERIFIED",
            "follow-up parent archive state is absent or ambiguous",
        )
    archived = archive["archived"]
    archive_url = str(archive.get("conversationUrl") or "").strip()
    if archived and archive_url != conversation_url:
        raise OracleRunError(
            "FOLLOWUP_PARENT_ARCHIVE_IDENTITY_INVALID",
            "archived follow-up parent is not bound to the exact conversation URL",
        )
    return {
        "was_archived": archived,
        "conversation_url": archive_url or conversation_url,
        "oracle_meta_path": str(meta_path),
        "oracle_meta_sha256": hashlib.sha256(raw).hexdigest(),
        "restore_policy": "exact-unarchive-then-rearchive" if archived else "no-transition",
    }


def _require_followup_parent(parent_run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, dict[str, str], dict[str, Any]]:
    """Validate the one narrow parent contract for an in-conversation Pro round.

    This is deliberately stricter than ordinary recovery.  A follow-up is not
    a replacement run: it can exist only for a bound, terminal, read-only Pro
    run whose pre-send task and browser identity receipts still validate.
    """
    state_path = parent_run_dir / "state.json"
    state = STATE.load_state(state_path)
    require_current_task_owns_run(state, legacy_code="FOLLOWUP_PARENT_LEGACY_UNBOUND")
    owner = STATE.source_thread_id_from_state(state)
    if not (
        state.get("status") == "complete"
        and state.get("session_authority") == "terminal"
        and state.get("terminal_harvested") is True
        and str(state.get("task_outcome") or "") == "executed"
    ):
        raise OracleRunError(
            "FOLLOWUP_PARENT_NOT_EXECUTED",
            "follow-up requires one terminal EXECUTED parent run",
            {"status": state.get("status"), "task_outcome": state.get("task_outcome")},
        )
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    if (
        str(state.get("transport") or "") != "pro-devspace-readonly"
        or str(profile.get("model") or "").casefold() != "gpt-5.6-sol"
        or str(profile.get("model_strategy") or "") != "select"
        or not STATE.is_compatible_pro_thinking_time(profile.get("thinking_time"))
    ):
        raise OracleRunError(
            "FOLLOWUP_PARENT_PROFILE_FORBIDDEN",
            "follow-up is limited to terminal GPT-5.6 Sol/Pro pro-devspace-readonly parents",
        )
    ownership = STATE.proven_ownership_receipt(state_path)
    browser = STATE.proven_browser_identity_receipt(state_path)
    if ownership is None or browser is None:
        raise OracleRunError(
            "FOLLOWUP_PARENT_IDENTITY_INVALID",
            "follow-up requires valid immutable ownership and browser identity receipts",
        )
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    conversation_url = str(oracle.get("conversation_url") or "").strip()
    browser_url = str((browser.get("payload") or {}).get("conversation_url") or "").strip()
    if STATE.CHATGPT_CONVERSATION_URL_RE.fullmatch(conversation_url) is None or conversation_url != browser_url:
        raise OracleRunError(
            "FOLLOWUP_PARENT_CONVERSATION_INVALID",
            "follow-up requires one exact persisted ChatGPT conversation URL",
        )
    mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
    expected_mission_sha256 = str(mission.get("sha256") or "")
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    checks: dict[str, str] = {}
    for name, candidate, expected in (
        ("output", artifacts.get("output"), str(state.get("artifact_sha256") or "")),
        ("transcript", artifacts.get("transcript"), ""),
        ("mission", mission.get("transport_path"), expected_mission_sha256),
        ("project_mission", mission.get("path"), expected_mission_sha256),
    ):
        try:
            path = STATE.exact_regular_file(candidate, label=f"followup_{name}")
            digest = STATE.sha256_file(path)
        except STATE.OracleStateError as exc:
            raise OracleRunError("FOLLOWUP_PARENT_ARTIFACT_INVALID", str(exc), {"artifact": name}) from exc
        if not path.read_bytes() or (expected and digest != expected):
            raise OracleRunError(
                "FOLLOWUP_PARENT_ARTIFACT_INVALID",
                "follow-up parent artifacts are absent, empty, or hash-mismatched",
                {"artifact": name, "expected_sha256": expected or None, "actual_sha256": digest},
            )
        checks[name] = digest
    archive_contract = _followup_archive_contract(state, conversation_url)
    return state, ownership, browser, conversation_url, checks, archive_contract


def _followup_manifest_payload(
    parent: dict[str, Any],
    *,
    mission_path: Path,
    run_id: str,
    archive_contract: dict[str, Any],
) -> dict[str, Any]:
    profile = parent.get("profile") if isinstance(parent.get("profile"), dict) else {}
    policy = parent.get("episode_policy") if isinstance(parent.get("episode_policy"), dict) else {}
    payload: dict[str, Any] = {
        "schema": STATE.SCHEMA,
        "project_root": parent.get("project_root"),
        "mission_path": str(mission_path),
        "app_name": parent.get("app_name"),
        "mode": parent.get("mode"),
        "transport": "pro-devspace-readonly",
        "run_root": str(Path(str(parent.get("mission", {}).get("transport_path") or "")).parent.parent),
        "oracle_command": (parent.get("oracle") or {}).get("command"),
        "submit_mutex_timeout_seconds": 30,
        "episode_policy": policy,
        "model": "gpt-5.6-sol",
        "model_strategy": "select",
        # A child is a new Pro submission even when its sealed parent used
        # Oracle's historical Heavy spelling.
        "thinking_time": STATE.PRO_THINKING_TIME,
        "research": str(profile.get("research") or "off"),
        "archive": "always" if archive_contract.get("was_archived") is True else "never",
        "task_outcome_contract": "v1",
        "run_id": run_id,
        "source_thread_id": STATE.source_thread_id_from_state(parent),
    }
    # The transport mission is in the parent run directory.  Its grandparent
    # is the configured host-only run root; derive it only from that sealed
    # host artifact rather than a caller-controlled path.
    return payload


def _followup_path_is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _assert_followup_artifact_directory(parent_run_dir: Path, name: str) -> Path:
    parent = parent_run_dir.resolve(strict=True)
    directory = parent / name
    if (
        not directory.exists()
        or _followup_path_is_link_or_junction(directory)
        or not directory.is_dir()
        or directory.resolve(strict=True) != directory
    ):
        raise OracleRunError(
            "FOLLOWUP_ARTIFACT_DIRECTORY_INVALID",
            "follow-up artifact directory must be a real directory inside the exact parent run",
            {"path": str(directory)},
        )
    return directory


def _write_followup_round_receipt(
    path: Path,
    payload: dict[str, Any],
    *,
    parent_run_dir: Path | None = None,
) -> str:
    if parent_run_dir is not None:
        directory = _assert_followup_artifact_directory(parent_run_dir, "followup-rounds")
        if path.parent != directory or _followup_path_is_link_or_junction(path):
            raise OracleRunError(
                "FOLLOWUP_RECEIPT_PATH_INVALID",
                "follow-up receipt escaped the exact parent artifact directory",
                {"path": str(path), "expected_parent": str(directory)},
            )
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise OracleRunError(
            "FOLLOWUP_ROUND_DUPLICATE",
            "this parent conversation already has the requested follow-up round key",
            {"round_receipt": str(path)},
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _prepare_followup_artifact_directory(parent_run_dir: Path, name: str) -> Path:
    """Create one parent-local artifact directory without following a link."""
    directory = parent_run_dir / name
    if directory.exists() and (_followup_path_is_link_or_junction(directory) or not directory.is_dir()):
        raise OracleRunError(
            "FOLLOWUP_ARTIFACT_DIRECTORY_INVALID",
            "follow-up artifact directory must be a real directory inside the exact parent run",
            {"path": str(directory)},
        )
    directory.mkdir(parents=False, exist_ok=True)
    return _assert_followup_artifact_directory(parent_run_dir, name)


def _followup_artifact_observation(value: Any) -> dict[str, Any]:
    """Describe one child artifact without following links or inventing bytes."""
    path_text = str(value or "").strip()
    if not path_text:
        return {"present": False, "reason": "path-absent"}
    path = Path(path_text).expanduser()
    try:
        if path.is_symlink() or not path.is_file():
            return {"path": str(path), "present": False, "reason": "not-regular-file"}
        raw = path.read_bytes()
    except OSError:
        return {"path": str(path), "present": False, "reason": "unreadable"}
    return {
        "path": str(path.resolve()), "present": bool(raw), "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest() if raw else None,
    }


def _persist_followup_completion_receipt(
    receipt_path: Path,
    *,
    reservation_sha256: str,
    child_state_path: Path,
    parent_conversation_url: str,
    browser_receipt: dict[str, Any] | None,
    identity_status: str,
    archive_transition: dict[str, Any] | None = None,
    parent_run_dir: Path | None = None,
) -> dict[str, Any]:
    """Append the actual child round outcome without rewriting reservation data."""
    state = STATE.load_state(child_state_path)
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output = _followup_artifact_observation(artifacts.get("output"))
    transcript = _followup_artifact_observation(artifacts.get("transcript"))
    state_bytes = child_state_path.read_bytes()
    observed_url = str(((browser_receipt or {}).get("payload") or {}).get("conversation_url") or "").strip()
    payload = {
        "schema": "codex.chatgpt.oracle-followup-round-result/v1",
        "reservation_receipt_sha256": reservation_sha256,
        "child": {
            "run_id": state.get("run_id"),
            "slug": (state.get("oracle") or {}).get("slug"),
            "state_sha256": hashlib.sha256(state_bytes).hexdigest(),
            "status": state.get("status"),
            "transport_status": state.get("transport_status"),
            "task_outcome": state.get("task_outcome"),
            "session_authority": state.get("session_authority"),
            "terminal_harvested": state.get("terminal_harvested"),
            "output": output,
            "transcript": transcript,
            "artifact_sha256_matches_output": (
                output.get("sha256") == state.get("artifact_sha256")
                if output.get("sha256") else state.get("artifact_sha256") in {None, ""}
            ),
        },
        "conversation_binding": {
            "parent_conversation_url": parent_conversation_url,
            "observed_child_conversation_url": observed_url or None,
            "identity_status": identity_status,
            "browser_identity_receipt_sha256": (browser_receipt or {}).get("sha256"),
        },
        "archive_transition": archive_transition,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    digest = _write_followup_round_receipt(
        receipt_path,
        payload,
        parent_run_dir=parent_run_dir,
    )
    return {"path": str(receipt_path), "sha256": digest, "payload": payload}


def followup_run(
    parent_run_dir: Path,
    *,
    mission_path: Path,
    round_key: str,
    run_id: str | None = None,
    dry_run: bool = False,
    **execute_kwargs: Any,
) -> dict[str, Any]:
    """Continue one qualifying read-only Pro conversation without a new tab.

    It is intentionally an internal runner command.  Manifest ``oracle_args``
    remains unable to pass Oracle's generic follow-up switches.
    """
    directory = parent_run_dir.expanduser().resolve(strict=True)
    normalized_round_key = str(round_key or "").strip().casefold()
    if FOLLOWUP_ROUND_KEY_RE.fullmatch(normalized_round_key) is None:
        raise OracleRunError("FOLLOWUP_ROUND_KEY_INVALID", "round_key must be a safe 1-64 character identifier")
    parent, ownership, browser, conversation_url, artifact_hashes, archive_contract = _require_followup_parent(directory)
    parent_slug = str((parent.get("oracle") or {}).get("slug") or "")
    project_root = Path(str(parent.get("project_root") or "")).resolve(strict=True)
    child_mission = STATE.exact_regular_file(mission_path, label="followup_mission")
    if not STATE.is_within(project_root, child_mission):
        raise OracleRunError("FOLLOWUP_MISSION_OUTSIDE_PROJECT", "follow-up mission must remain in the exact parent project root")
    STATE.read_utf8_strict(child_mission)
    run_root = directory.parent.resolve()
    receipt_path = directory / "followup-rounds" / f"{normalized_round_key}.json"
    if receipt_path.exists():
        raise OracleRunError(
            "FOLLOWUP_ROUND_DUPLICATE",
            "this parent conversation already has the requested follow-up round key",
            {
                "round_receipt": str(receipt_path),
                "remediation": (
                    "preserve the immutable reservation; after proving the prior controller ended, "
                    "use a new round_key rather than deleting or replaying this key"
                ),
            },
        )
    child_run_id = str(run_id or f"followup-{uuid.uuid4().hex}").strip().casefold()
    if STATE.RUN_ID_RE.fullmatch(child_run_id) is None:
        raise OracleRunError("FOLLOWUP_RUN_ID_INVALID", "follow-up run_id must be a safe identifier")
    child_slug = STATE.oracle_slug(project_root, child_run_id)
    expected_port = STATE.reserve_loopback_cdp_port()
    manifest_payload = _followup_manifest_payload(
        parent, mission_path=child_mission, run_id=child_run_id, archive_contract=archive_contract
    )
    manifest_payload["run_root"] = str(run_root)
    manifest_path = directory / "followup-manifests" / f"{child_run_id}.json"
    receipt = {
        "schema": "codex.chatgpt.oracle-followup-round/v1",
        "round_key": normalized_round_key,
        "source_thread_id": STATE.source_thread_id_from_state(parent),
        "parent": {
            "run_id": parent.get("run_id"), "slug": parent_slug,
            "conversation_url": conversation_url,
            "ownership_receipt_sha256": ownership.get("sha256"),
            "browser_identity_receipt_sha256": browser.get("sha256"),
            "artifacts": artifact_hashes,
            # Existing terminal state stores the output hash, but not a
            # transcript-hash field.  Preserve the current regular-file hash
            # in this reservation without falsely claiming it was sealed by
            # the legacy terminal-state schema.
            "transcript_baseline": {
                "sha256": artifact_hashes["transcript"],
                "state_terminal_hash_field": "absent-in-parent-schema",
            },
            "archive_contract": archive_contract,
        },
        "child": {
            "run_id": child_run_id, "slug": child_slug,
            "mission_path": str(child_mission), "mission_sha256": STATE.sha256_file(child_mission),
            "run_dir": str(run_root / child_run_id), "expected_cdp_port": expected_port,
        },
        "followup_argv": ["--followup", parent_slug],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if dry_run:
        return {
            "ok": True,
            "status": "followup_dry_run",
            "parent_conversation_url": conversation_url,
            "manifest_path": str(manifest_path),
            "round_receipt_path": str(receipt_path),
            "round_receipt_plan": receipt,
            "argv_plan": ["--followup", parent_slug, "--slug", child_slug],
            "submitted_question": False,
        }
    # One exact parent conversation accepts at most one active follow-up
    # controller, regardless of round key. Hold this lock through execute_run:
    # a detached controller in the reservation-to-child-layout gap must block a
    # second key from reaching the same composer.
    with STATE.exact_run_recovery_mutex(
        directory,
        timeout_seconds=30,
        platform_name=execute_kwargs.get("platform_name"),
    ):
        if receipt_path.exists():
            raise OracleRunError(
                "FOLLOWUP_ROUND_DUPLICATE",
                "this parent conversation already has the requested follow-up round key",
                {"round_receipt": str(receipt_path)},
            )
        manifest_directory = _prepare_followup_artifact_directory(directory, "followup-manifests")
        receipt_directory = _prepare_followup_artifact_directory(directory, "followup-rounds")
        manifest_path = manifest_directory / manifest_path.name
        receipt_path = receipt_directory / receipt_path.name
        encoded_manifest = (json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        try:
            with manifest_path.open("xb") as handle:
                handle.write(encoded_manifest)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise OracleRunError("FOLLOWUP_MANIFEST_CONFLICT", "follow-up manifest path already exists", {"manifest": str(manifest_path)}) from exc
        try:
            receipt_sha256 = _write_followup_round_receipt(
                receipt_path,
                receipt,
                parent_run_dir=directory,
            )
        except Exception:
            # The manifest was created by this exact locked attempt. Remove
            # only that still byte-identical, unreferenced file; changed or
            # redirected evidence remains preserved fail-closed.
            try:
                if (
                    not _followup_path_is_link_or_junction(manifest_path)
                    and manifest_path.is_file()
                    and manifest_path.read_bytes() == encoded_manifest
                    and _assert_followup_artifact_directory(
                        directory,
                        "followup-manifests",
                    ) == manifest_path.parent
                ):
                    manifest_path.unlink()
            except OSError:
                pass
            raise
        return _launch_followup_reserved_round(
            directory=directory,
            parent=parent,
            parent_slug=parent_slug,
            conversation_url=conversation_url,
            archive_contract=archive_contract,
            normalized_round_key=normalized_round_key,
            receipt=receipt,
            receipt_path=receipt_path,
            receipt_sha256=receipt_sha256,
            manifest_path=manifest_path,
            run_root=run_root,
            child_run_id=child_run_id,
            child_slug=child_slug,
            expected_port=expected_port,
            execute_kwargs=execute_kwargs,
        )


def _launch_followup_reserved_round(
    *,
    directory: Path,
    parent: dict[str, Any],
    parent_slug: str,
    conversation_url: str,
    archive_contract: dict[str, Any],
    normalized_round_key: str,
    receipt: dict[str, Any],
    receipt_path: Path,
    receipt_sha256: str,
    manifest_path: Path,
    run_root: Path,
    child_run_id: str,
    child_slug: str,
    expected_port: int,
    execute_kwargs: dict[str, Any],
) -> dict[str, Any]:
    followup_binding = {
        "schema": "codex.chatgpt.oracle-followup-binding/v1",
        "source_thread_id": STATE.source_thread_id_from_state(parent),
        "round_key": normalized_round_key,
        "reservation_path": str(receipt_path),
        "reservation_sha256": receipt_sha256,
        "parent": receipt["parent"],
        "child": receipt["child"],
        "conversation_url": conversation_url,
    }
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    launch_attempt_id = uuid.uuid4().hex
    launch_path = receipt_path.with_name(
        f"{normalized_round_key}.{launch_attempt_id}.launch.json"
    )
    launch_payload = {
        "schema": "codex.chatgpt.oracle-followup-launch/v1",
        "source_thread_id": STATE.source_thread_id_from_state(parent),
        "round_key": normalized_round_key,
        "launch_attempt_id": launch_attempt_id,
        "reservation_sha256": receipt_sha256,
        "manifest_sha256": manifest_sha256,
        "parent_run_id": parent.get("run_id"),
        "child_run_id": child_run_id,
        "child_slug": child_slug,
        "child_run_dir": str(run_root / child_run_id),
        "phase": "execute-run-entered",
        "submission_action": "not-reached-at-receipt",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    launch_sha256 = _write_followup_round_receipt(
        launch_path,
        launch_payload,
        parent_run_dir=directory,
    )
    try:
        result = execute_run(
            manifest_path,
            dry_run=False,
            _cdp_port=expected_port,
            _followup_parent_slug=parent_slug,
            _followup_binding=followup_binding,
            _expected_manifest_sha256=manifest_sha256,
            _followup_parent_run_dir=directory,
            _expected_followup_mission_sha256=receipt["child"]["mission_sha256"],
            **execute_kwargs,
        )
    except Exception as exc:
        # If a child layout exists, seal what is known before propagating the
        # launcher failure.  The parent ownership is never changed here.
        child_state_path = run_root / child_run_id / "state.json"
        if child_state_path.exists():
            _persist_followup_completion_receipt(
                receipt_path.with_suffix(".result.json"),
                reservation_sha256=receipt_sha256,
                child_state_path=child_state_path,
                parent_conversation_url=conversation_url,
                browser_receipt=None,
                identity_status="launcher-exception-identity-unavailable",
                archive_transition=None,
                parent_run_dir=directory,
            )
        else:
            failure_path = receipt_path.with_name(
                f"{normalized_round_key}.{launch_attempt_id}.prelaunch-failure.json"
            )
            error_payload = (
                exc.envelope()["error"]
                if isinstance(exc, OracleRunError)
                else {"code": "ORACLE_RUN_FAILED", "message": str(exc), "evidence": {}}
            )
            proven_pre_layout_codes = {
                "FOLLOWUP_ARTIFACT_DIRECTORY_INVALID",
                "FOLLOWUP_MANIFEST_CHANGED_BEFORE_PREPARE",
                "FOLLOWUP_MANIFEST_PATH_INVALID",
                "FOLLOWUP_MANIFEST_SYMLINK_FORBIDDEN",
                "FOLLOWUP_MISSION_CHANGED_BEFORE_PREPARE",
                "FOLLOWUP_PARENT_RUN_DIR_REQUIRED",
            }
            submission_action = (
                "none"
                if str(error_payload.get("code") or "") in proven_pre_layout_codes
                else "submitted_unknown"
            )
            failure_payload = {
                "schema": "codex.chatgpt.oracle-followup-prelaunch-failure/v1",
                "source_thread_id": STATE.source_thread_id_from_state(parent),
                "round_key": normalized_round_key,
                "launch_attempt_id": launch_attempt_id,
                "reservation_sha256": receipt_sha256,
                "manifest_sha256": manifest_sha256,
                "launch_receipt_sha256": launch_sha256,
                "child_run_id": child_run_id,
                "child_slug": child_slug,
                "child_run_dir": str(child_state_path.parent),
                "child_state_absent": True,
                "submission_action": submission_action,
                "error": error_payload,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_followup_round_receipt(
                failure_path,
                failure_payload,
                parent_run_dir=directory,
            )
        raise
    result["followup_round_receipt"] = {"path": str(receipt_path), "sha256": receipt_sha256}
    result["followup_launch_receipt"] = {"path": str(launch_path), "sha256": launch_sha256}
    child_state_path = run_root / child_run_id / "state.json"
    if child_state_path.exists():
        child_state = STATE.load_state(child_state_path)
        pre_submit = str(child_state.get("session_authority") or "") == "pre_submit"
        child_browser = None if pre_submit else STATE.proven_browser_identity_receipt(child_state_path)
        child_url = str(((child_browser or {}).get("payload") or {}).get("conversation_url") or "").strip()
        identity_status = "not-applicable-pre-submit" if pre_submit else (
            "same-exact-conversation" if child_url == conversation_url else "unverified-or-different-conversation"
        )
        identity_error = not pre_submit and (child_browser is None or child_url != conversation_url)
        archive_transition = None
        if not pre_submit:
            try:
                archive_transition = _followup_archive_contract(child_state, conversation_url)
            except OracleRunError:
                archive_transition = {"was_archived": False, "restore_policy": "unverified"}
        archive_error = (
            archive_contract.get("was_archived") is True
            and not pre_submit
            and not identity_error
            and archive_transition.get("was_archived") is not True
        )
        if identity_error or archive_error:
            STATE.update_state(
                child_state_path,
                status="attention_required",
                task_outcome_reason=(
                    "followup-archive-restoration-unverified" if archive_error
                    else "followup-conversation-identity-unverified"
                ),
                conversation_url_conflict=(
                    {"persisted": conversation_url, "observed": child_url}
                    if child_url and child_url != conversation_url else None
                ),
            )
            child_state = STATE.load_state(child_state_path)
        completion = _persist_followup_completion_receipt(
            receipt_path.with_suffix(".result.json"),
            reservation_sha256=receipt_sha256,
            child_state_path=child_state_path,
            parent_conversation_url=conversation_url,
            browser_receipt=child_browser,
            identity_status=identity_status,
            archive_transition=archive_transition,
            parent_run_dir=directory,
        )
        result["followup_round_result_receipt"] = {"path": completion["path"], "sha256": completion["sha256"]}
        if identity_error or archive_error:
            raise OracleRunError(
                "FOLLOWUP_ARCHIVE_RESTORATION_UNVERIFIED" if archive_error else "FOLLOWUP_CONVERSATION_IDENTITY_UNVERIFIED",
                "follow-up did not prove the exact conversation and original archive state",
                {
                    "parent_conversation_url": conversation_url,
                    "child_conversation_url": child_url or None,
                    "child_run_dir": str(child_state_path.parent),
                },
            )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run additive Oracle browser missions without modifying agbrowse routing.")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--dry-run", action="store_true")
    followup_parser = commands.add_parser("followup")
    followup_parser.add_argument("--parent-run-dir", type=Path, required=True)
    followup_parser.add_argument("--mission-path", type=Path, required=True)
    followup_parser.add_argument("--round-key", required=True)
    followup_parser.add_argument("--run-id")
    followup_parser.add_argument("--dry-run", action="store_true")
    recover_parser = commands.add_parser("recover")
    recover_parser.add_argument("--run-dir", type=Path, required=True)
    recover_parser.add_argument("--action", choices=("harvest", "live"), required=True)
    recover_parser.add_argument("--oracle-command", nargs="+")
    recover_parser.add_argument("--dry-run", action="store_true")
    recover_parser.add_argument(
        "--settle-timeout-seconds",
        "--status-audit-seconds",
        dest="settle_timeout_seconds",
        type=float,
        default=4800,
        help=(
            "For live recovery, audit the exact slug at this caution interval and automatically "
            "continue the same-session observation; this is never a termination deadline."
        ),
    )
    recover_parser.add_argument(
        "--settle-interval-seconds",
        type=float,
        default=15,
    )
    adjudicate_parser = commands.add_parser("adjudicate")
    adjudicate_parser.add_argument("--run-dir", type=Path, required=True)
    adjudicate_parser.add_argument("--expected-output-sha256", required=True)
    adjudicate_parser.add_argument(
        "--task-outcome",
        choices=("executed", "not_executed", "blocked", "unknown"),
        required=True,
    )
    adjudicate_parser.add_argument("--reason", required=True)
    promote_parser = commands.add_parser("promote-harvest-candidate")
    promote_parser.add_argument("--run-dir", type=Path, required=True)
    promote_parser.add_argument("--candidate-path", type=Path, required=True)
    promote_parser.add_argument("--expected-candidate-sha256", required=True)
    saved_output_parser = commands.add_parser("settle-saved-output")
    saved_output_parser.add_argument("--run-dir", type=Path, required=True)
    saved_output_parser.add_argument("--expected-state-sha256", required=True)
    saved_output_parser.add_argument("--expected-output-sha256", required=True)
    saved_output_parser.add_argument("--expected-stdout-sha256", required=True)
    saved_output_parser.add_argument("--expected-oracle-meta-sha256", required=True)
    saved_output_parser.add_argument("--dry-run", action="store_true")
    saved_identity_parser = commands.add_parser("seal-saved-output-browser-identity")
    saved_identity_parser.add_argument("--run-dir", type=Path, required=True)
    saved_identity_parser.add_argument("--expected-settlement-sha256", required=True)
    saved_identity_parser.add_argument("--dry-run", action="store_true")
    settle_parser = commands.add_parser("settle-no-submission")
    settle_parser.add_argument("--run-dir", type=Path, required=True)
    settle_parser.add_argument(
        "--confirmation",
        choices=(STATE.USER_CONFIRMED_NO_SUBMISSION,),
        required=True,
    )
    settle_parser.add_argument("--reason", required=True)
    execution_settle_parser = commands.add_parser("settle-executed-timeout")
    execution_settle_parser.add_argument("--run-dir", type=Path, required=True)
    execution_settle_parser.add_argument("--expected-output-sha256", required=True)
    execution_settle_parser.add_argument(
        "--confirmation",
        choices=(STATE.USER_CONFIRMED_EXECUTION_ENDED,),
        required=True,
    )
    execution_settle_parser.add_argument("--reason", required=True)
    execution_settle_parser.add_argument(
        "--execution-evidence",
        action="append",
        metavar="PATH=SHA256",
        required=True,
    )
    recursive_parser = commands.add_parser("settle-recursive-self-observation")
    recursive_parser.add_argument("--run-dir", type=Path, required=True)
    recursive_parser.add_argument("--expected-state-sha256", required=True)
    recursive_parser.add_argument("--expected-output-sha256", required=True)
    recursive_parser.add_argument("--expected-transcript-sha256", required=True)
    recursive_parser.add_argument(
        "--confirmation",
        choices=(STATE.USER_AUTHORIZED_FRESH_AFTER_RECURSIVE_SELF_OBSERVATION,),
        required=True,
    )
    recursive_parser.add_argument("--reason", required=True)
    recursive_parser.add_argument("--dry-run", action="store_true")
    terminal_nonexecution_parser = commands.add_parser(
        "settle-terminal-devspace-nonexecution"
    )
    terminal_nonexecution_parser.add_argument("--run-dir", type=Path, required=True)
    terminal_nonexecution_parser.add_argument("--expected-state-sha256", required=True)
    terminal_nonexecution_parser.add_argument("--expected-output-sha256", required=True)
    terminal_nonexecution_parser.add_argument("--expected-transcript-sha256", required=True)
    terminal_nonexecution_parser.add_argument("--expected-stdout-sha256", required=True)
    terminal_nonexecution_parser.add_argument("--expected-stderr-sha256", required=True)
    terminal_nonexecution_parser.add_argument("--expected-mission-sha256", required=True)
    terminal_nonexecution_parser.add_argument(
        "--confirmation",
        choices=(STATE.USER_AUTHORIZED_FRESH_AFTER_TERMINAL_DEVSPACE_NONEXECUTION,),
        required=True,
    )
    terminal_nonexecution_parser.add_argument("--reason", required=True)
    terminal_nonexecution_parser.add_argument("--dry-run", action="store_true")
    read_route_refresh_parser = commands.add_parser(
        "settle-terminal-devspace-read-route-refresh"
    )
    read_route_refresh_parser.add_argument("--run-dir", type=Path, required=True)
    read_route_refresh_parser.add_argument("--expected-state-sha256", required=True)
    read_route_refresh_parser.add_argument("--expected-output-sha256", required=True)
    read_route_refresh_parser.add_argument("--expected-transcript-sha256", required=True)
    read_route_refresh_parser.add_argument("--expected-stdout-sha256", required=True)
    read_route_refresh_parser.add_argument("--expected-stderr-sha256", required=True)
    read_route_refresh_parser.add_argument("--expected-mission-sha256", required=True)
    read_route_refresh_parser.add_argument(
        "--confirmation",
        choices=(STATE.USER_AUTHORIZED_FRESH_AFTER_DEVSPACE_READ_ROUTE_REFRESH,),
        required=True,
    )
    read_route_refresh_parser.add_argument("--reason", required=True)
    read_route_refresh_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            payload = execute_run(args.manifest, dry_run=args.dry_run)
        elif args.command == "followup":
            payload = followup_run(
                args.parent_run_dir,
                mission_path=args.mission_path,
                round_key=args.round_key,
                run_id=args.run_id,
                dry_run=args.dry_run,
            )
        elif args.command == "recover":
            payload = recover_run(
                args.run_dir,
                action=args.action,
                dry_run=args.dry_run,
                oracle_command=args.oracle_command,
                settle_timeout_seconds=args.settle_timeout_seconds,
                settle_interval_seconds=args.settle_interval_seconds,
            )
        elif args.command == "adjudicate":
            payload = adjudicate_task_outcome(
                args.run_dir,
                expected_output_sha256=args.expected_output_sha256,
                task_outcome=args.task_outcome,
                reason=args.reason,
            )
        elif args.command == "promote-harvest-candidate":
            payload = promote_terminal_harvest_candidate(
                args.run_dir,
                candidate_path=args.candidate_path,
                expected_candidate_sha256=args.expected_candidate_sha256,
            )
        elif args.command == "settle-saved-output":
            payload = settle_saved_terminal_output(
                args.run_dir,
                expected_state_sha256=args.expected_state_sha256,
                expected_output_sha256=args.expected_output_sha256,
                expected_stdout_sha256=args.expected_stdout_sha256,
                expected_oracle_meta_sha256=args.expected_oracle_meta_sha256,
                dry_run=args.dry_run,
            )
        elif args.command == "seal-saved-output-browser-identity":
            payload = seal_saved_output_browser_identity(
                args.run_dir,
                expected_settlement_sha256=args.expected_settlement_sha256,
                dry_run=args.dry_run,
            )
        elif args.command == "settle-no-submission":
            payload = settle_user_confirmed_no_submission(
                args.run_dir,
                confirmation=args.confirmation,
                reason=args.reason,
            )
        elif args.command == "settle-executed-timeout":
            evidence: list[tuple[Path, str]] = []
            for value in args.execution_evidence:
                path_text, separator, digest = value.rpartition("=")
                if not separator or not path_text.strip() or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    raise OracleRunError(
                        "EXECUTION_ENDED_EVIDENCE_ARGUMENT_INVALID",
                        "execution evidence must use PATH=64-character-SHA256",
                    )
                evidence.append((Path(path_text), digest.casefold()))
            payload = settle_user_confirmed_delivery_timeout_execution(
                args.run_dir,
                expected_output_sha256=args.expected_output_sha256,
                confirmation=args.confirmation,
                reason=args.reason,
                execution_evidence=evidence,
            )
        elif args.command == "settle-recursive-self-observation":
            payload = settle_recursive_self_observation_fresh_run(
                args.run_dir,
                confirmation=args.confirmation,
                reason=args.reason,
                expected_state_sha256=args.expected_state_sha256,
                expected_output_sha256=args.expected_output_sha256,
                expected_transcript_sha256=args.expected_transcript_sha256,
                dry_run=args.dry_run,
            )
        elif args.command == "settle-terminal-devspace-nonexecution":
            payload = settle_terminal_devspace_nonexecution_fresh_run(
                args.run_dir,
                confirmation=args.confirmation,
                reason=args.reason,
                expected_state_sha256=args.expected_state_sha256,
                expected_output_sha256=args.expected_output_sha256,
                expected_transcript_sha256=args.expected_transcript_sha256,
                expected_stdout_sha256=args.expected_stdout_sha256,
                expected_stderr_sha256=args.expected_stderr_sha256,
                expected_mission_sha256=args.expected_mission_sha256,
                dry_run=args.dry_run,
            )
        else:
            payload = settle_terminal_devspace_read_route_refresh_fresh_run(
                args.run_dir,
                confirmation=args.confirmation,
                reason=args.reason,
                expected_state_sha256=args.expected_state_sha256,
                expected_output_sha256=args.expected_output_sha256,
                expected_transcript_sha256=args.expected_transcript_sha256,
                expected_stdout_sha256=args.expected_stdout_sha256,
                expected_stderr_sha256=args.expected_stderr_sha256,
                expected_mission_sha256=args.expected_mission_sha256,
                dry_run=args.dry_run,
            )
    except STATE.OracleStateError as exc:
        payload = exc.envelope()
    except OracleRunError as exc:
        payload = exc.envelope()
    except Exception as exc:
        payload = OracleRunError("ORACLE_RUN_FAILED", str(exc)).envelope()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
