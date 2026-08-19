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
import time
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


def build_oracle_argv(config, layout, prompt: str) -> list[str]:
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
        *config.oracle_args,
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
# Oracle 0.17.1 rejects an individual browser attachment above this upstream
# input limit before it can create a ChatGPT conversation.  Keep this narrow:
# context-packet construction may retain its broader configured envelope.
ORACLE_0161_ATTACHMENT_MAX_BYTES = 1024 * 1024


def validate_oracle_attachment_sizes(config) -> None:
    """Reject Pro attachments Oracle 0.17.1 cannot submit before any launch."""
    if not STATE.is_attachment_transport(config.transport):
        return
    oversized = [
        {"path": str(path), "size_bytes": path.stat().st_size, "limit_bytes": ORACLE_0161_ATTACHMENT_MAX_BYTES}
        for path in config.attachments
        if path.stat().st_size > ORACLE_0161_ATTACHMENT_MAX_BYTES
    ]
    if oversized:
        raise OracleRunError(
            "ORACLE_ATTACHMENT_SIZE_PRELAUNCH_FAILED",
            "Oracle 0.17.1 Pro attachments must not exceed 1 MiB each",
            {"limit_bytes": ORACLE_0161_ATTACHMENT_MAX_BYTES, "attachments": oversized},
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
) -> int:
    """Wait for one exact Oracle process; time alone never ends the wait."""
    if not math.isfinite(status_audit_seconds) or status_audit_seconds <= 0:
        raise OracleRunError(
            "STATUS_AUDIT_INTERVAL_INVALID",
            "status audit interval must be a positive finite duration",
        )
    audit_count = 0
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


ORACLE_VERSION_RESOLUTION_TIMEOUT_SECONDS = 90


def resolve_oracle_version(command: Sequence[str], *, run_factory=subprocess.run, platform_name: str | None = None) -> str:
    """Resolve Oracle before launch with a bounded cold-cache allowance.

    The returned value is still passed immediately to the exact 0.17.1
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
            for path, digest in zip(config.attachments, config.attachment_sha256s, strict=True)
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

    Oracle 0.17.1 writes the no-live-tab line to stdout and the missing-URL
    detail to stderr.  Both streams belong to one exact recovery attempt.
    """
    chunks: list[str] = []
    for path in paths:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            chunks.append("")
    value = "\n".join(chunks)
    return all(marker in value for marker in RECOVERY_BINDING_UNAVAILABLE_MARKERS)


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


def execute_run(
    manifest_path: Path,
    *,
    dry_run: bool = False,
    run_factory: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    platform_name: str | None = None,
    compat_factory: Callable[[str], dict[str, Any]] = COMPAT.ensure_oracle_compatibility,
    devspace_compat_factory: Callable[[], dict[str, Any]] = (
        DEVSPACE_COMPAT.ensure_devspace_compatibility
    ),
    devspace_qualification_factory: Callable[[Path], dict[str, Any]] = (
        DEVSPACE_PREFLIGHT.ensure_exact_root_qualified
    ),
    exact_recovery_factory: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = STATE.load_manifest(manifest_path, platform_name=platform_name)
    validate_oracle_attachment_sizes(config)
    layout = STATE.create_layout(config, run_id=config.requested_run_id)
    transport_mission_path = layout.run_dir / "mission.md"
    # The app reads the project mission. The copied bytes below are host-only
    # immutable evidence and are never exposed as the workspace handoff path.
    prompt = STATE.composer_prompt(config, config.mission_path)
    argv = build_oracle_argv(config, layout, prompt)
    if dry_run:
        return dry_run_payload(config, layout, argv, prompt)

    qualification_target = web_multi_devspace_qualification_target(config)

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
    for attachment, expected in zip(config.attachments, config.attachment_sha256s, strict=True):
        actual = STATE.sha256_file(attachment)
        if actual != expected:
            raise OracleRunError(
                "ATTACHMENT_CHANGED_BEFORE_PREPARE",
                "attachment bytes changed after manifest validation",
                {"path": str(attachment), "expected": expected, "actual": actual},
            )
    layout.run_dir.mkdir(parents=True, exist_ok=False)
    transport_mission_path.write_bytes(mission_bytes)
    STATE.write_json_atomic(layout.state_path, STATE.state_payload(config, layout, status="prepared", resolved_version="unresolved"))
    layout.stdout_path.touch()
    layout.stderr_path.touch()
    oracle_env = STATE.browser_temp_environment(layout.browser_temp_path, platform_name=platform_name)
    exit_code: int | None = None
    oracle_process_pid: int | None = None
    prior_audit_observations: dict[str, dict[str, Any]] = {}
    try:
        version = resolve_oracle_version(config.oracle_command, run_factory=run_factory, platform_name=platform_name)
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
            with STATE.project_submit_mutex(mutex_root, timeout_seconds=config.submit_mutex_timeout_seconds, platform_name=platform_name):
                owners = STATE.unresolved_project_sessions(
                    config.run_root,
                    config.project_root,
                    parallel_parent_id=config.parallel_parent_id,
                    exclude_run_id=layout.run_id,
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
                for attachment, expected in zip(config.attachments, config.attachment_sha256s, strict=True):
                    actual = STATE.sha256_file(attachment)
                    if actual != expected:
                        raise OracleRunError(
                            "ATTACHMENT_CHANGED_BEFORE_SUBMIT",
                            "attachment bytes changed after manifest validation",
                            {"path": str(attachment), "expected": expected, "actual": actual},
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
                audit_callback = lambda count: record_exact_run_status_audit(
                    layout,
                    process=process,
                    audit_count=count,
                    status_audit_seconds=status_audit_seconds,
                    prior_observations=prior_audit_observations,
                )
                if not config.parallel_parent_id:
                    exit_code = wait_for_oracle_process(
                        process, status_audit_seconds, on_status_audit=audit_callback
                    )
            if config.parallel_parent_id:
                exit_code = wait_for_oracle_process(
                    process, status_audit_seconds, on_status_audit=audit_callback
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
            recovered = recovery(
                layout.run_dir,
                action="live",
                platform_name=platform_name,
                settle_timeout_seconds=status_audit_seconds,
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
        return {"ok": True, "status": "dry-run", "run_dir": str(directory), "action": action, "argv": STATE.command_for_display(argv)}
    stdout_path = directory / f"recovery-{action}-stdout.log"
    stderr_path = directory / f"recovery-{action}-stderr.log"
    recovery_browser_temp = directory / f"recovery-{action}-browser-temp"
    recovery_env = STATE.browser_temp_environment(recovery_browser_temp, platform_name=platform_name)
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
    active_pids = [pid for pid in run_owned_process_ids(directory, state) if process_alive(pid)]
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
    with STATE.project_submit_mutex(mutex_root, timeout_seconds=30, platform_name=platform_name):
        settled = STATE.settle_user_confirmed_no_submission(
            state_path,
            confirmation=confirmation,
            reason=reason,
        )
        owners = STATE.unresolved_project_sessions(
            directory.parent,
            project_root,
            exclude_run_id=str(settled.get("run_id") or ""),
        )
    return {
        "ok": True,
        "status": "pre_submit_user_confirmed",
        "safe_for_fresh_run": not owners,
        "unresolved_owners": owners,
        "run_dir": str(directory),
        "result": settled,
    }


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
) -> dict[str, Any]:
    directory = run_dir.expanduser().resolve(strict=True)
    stored = STATE.load_state(directory / "state.json")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run additive Oracle browser missions without modifying agbrowse routing.")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--dry-run", action="store_true")
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            payload = execute_run(args.manifest, dry_run=args.dry_run)
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
        elif args.command == "settle-no-submission":
            payload = settle_user_confirmed_no_submission(
                args.run_dir,
                confirmation=args.confirmation,
                reason=args.reason,
            )
        else:
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
