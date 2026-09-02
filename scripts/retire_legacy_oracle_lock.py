#!/usr/bin/env python3
"""Archive one dead, unbound Oracle launch after explicit user cancellation.

This maintenance command releases the active local lock without claiming that
the provider did or did not execute the mission. It never adopts an owner,
changes run evidence, launches a browser, or authorizes a replacement prompt.
Use only when the user explicitly requests retirement of this exact legacy run.
The original directory can be restored from the receipt's archive path during
separately authorized maintenance; all original absolute bindings are preserved.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONFIRMATION = "user-authorized-legacy-lock-retirement"
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "oracle_legacy_retirement_runner", ROOT / "bin/chatgpt_oracle_run.py"
)
assert spec is not None and spec.loader is not None
RUNNER = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = RUNNER
spec.loader.exec_module(RUNNER)
STATE = RUNNER.STATE


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_regular(path: Path) -> bytes:
    require(not path.is_symlink() and path.is_file(), f"regular evidence file required: {path}")
    return path.read_bytes()


def cdp_connection_refused(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) in {
            errno.ECONNREFUSED, getattr(errno, "WSAECONNREFUSED", errno.ECONNREFUSED)
        }


def inspect_candidate(run_dir: Path, expected_state_sha256: str, expected_meta_sha256: str) -> dict[str, Any]:
    require(run_dir.is_absolute() and run_dir.resolve() == run_dir, "exact canonical run directory required")
    require(STATE.is_within(STATE.oracle_state_root(), run_dir), "run must remain inside Oracle host state")
    require(run_dir.parent.name == "runs", "run must be in the active runs directory")
    state_bytes = read_regular(run_dir / "state.json")
    require(hashlib.sha256(state_bytes).hexdigest() == expected_state_sha256, "state hash changed")
    state = STATE.load_state(run_dir / "state.json")
    require(state.get("run_id") == run_dir.name, "run directory identity mismatch")
    require(state.get("status") == "attention_required", "only an unresolved failed launch may be retired")
    require(state.get("session_authority") == "submitted_unknown", "session authority is not eligible")
    require(not state.get("terminal_harvested"), "terminal evidence must use ordinary settlement")
    require(not state.get("parallel_parent_id") and not state.get("followup")
            and not state.get("web_multi_child_provenance"), "direct runs only")
    require(not list(run_dir.glob("followup*")), "follow-up evidence must use ordinary settlement")
    receipt = json.loads(read_regular(run_dir / "ownership-receipt.json"))
    for owner in (state.get("originating_task", {}), state.get("ownership", {}), receipt):
        require(owner.get("binding") == "legacy-unbound" and owner.get("source_thread_id") is None,
                "bound or ambiguous ownership cannot be retired by this command")
    project = Path(state["project_root"])
    require(project.is_absolute() and project.resolve(strict=True) == project, "project root identity mismatch")
    project_hash = hashlib.sha256(str(project).casefold().encode("utf-8")).hexdigest()
    for key, name in {"output": "output.md", "stdout": "stdout.log", "stderr": "stderr.log",
                      "transcript": "transcript.md", "browser_temp": "browser-temp"}.items():
        require(state.get("artifacts", {}).get(key) == str(run_dir / name), f"artifact path mismatch: {key}")
    oracle = state["oracle"]
    slug = oracle["slug"]
    port = state["browser_identity"]["expected_cdp_port"]
    require(isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535, "invalid CDP port")
    require(STATE.oracle_slug(project, run_dir.name) == slug, "slug identity mismatch")
    for key, value in {
        "run_id": run_dir.name, "slug": slug, "project_root": str(project),
        "project_root_sha256": project_hash, "mission_sha256": state["mission"]["sha256"],
        "expected_cdp_port": port, "browser_temp": str(run_dir / "browser-temp"),
    }.items():
        require(receipt.get(key) == value, f"ownership receipt mismatch: {key}")
    require(state["mission"].get("transport_path") == str(run_dir / "mission.md"), "mission path mismatch")
    require(STATE.sha256_file(run_dir / "mission.md") == receipt["mission_sha256"], "mission hash changed")
    require(not (run_dir / "output.md").exists(), "output exists; use ordinary outcome settlement")
    require(not oracle.get("conversation_url"), "conversation exists; use exact recovery")
    require(not (run_dir / "browser-identity-receipt.json").exists(), "browser identity exists; use exact recovery")
    session_root = Path(os.environ.get("ORACLE_SESSION_ROOT") or Path.home() / ".oracle/sessions").resolve()
    meta_path = session_root / slug / "meta.json"
    meta_bytes = read_regular(meta_path)
    require(hashlib.sha256(meta_bytes).hexdigest() == expected_meta_sha256, "Oracle metadata hash changed")
    meta = json.loads(meta_bytes)
    require(meta.get("id") == slug and meta.get("status") == "error" and bool(meta.get("completedAt")),
            "exact ended Oracle controller metadata required")
    browser = meta.get("browser", {})
    require(not browser.get("runtime") and not browser.get("harvest") and not browser.get("archive"),
            "browser/conversation binding exists; use exact recovery")
    require(browser.get("config", {}).get("debugPort") == port, "metadata CDP port mismatch")
    error = f"connect ECONNREFUSED 127.0.0.1:{port}"
    require(meta.get("error", {}).get("message") == error, "only the CDP launch failure is eligible")
    hashes = {p.name: hashlib.sha256(read_regular(p)).hexdigest()
              for p in sorted(run_dir.iterdir()) if p.is_file() or p.is_symlink()}
    stdout = read_regular(run_dir / "stdout.log").decode("utf-8")
    require(f"Session: {slug}" in stdout and error in stdout, "exact failed launch log required")
    recovery_stdout = read_regular(run_dir / "recovery-harvest-stdout.log").decode("utf-8")
    require(f'No live ChatGPT tab matched session "{slug}".' in recovery_stdout, "exact prior harvest required")
    require(RUNNER.exact_recovery_binding_unavailable(
        run_dir / "recovery-harvest-stdout.log", run_dir / "recovery-harvest-stderr.log"
    ), "prior harvest must prove no tab and no saved conversation URL")
    require(not STATE._settlement_logs_have_conversation_url(run_dir / "state.json"),
            "conversation URL observed; use exact recovery")
    pids = set(RUNNER.run_owned_process_ids(run_dir, state)) | {receipt.get("oracle_process_pid")}
    require(pids and all(isinstance(pid, int) and pid > 0 for pid in pids), "complete process identity required")
    pids = sorted(pids)
    require(not any(RUNNER.run_owned_process_is_alive(run_dir, state, pid) for pid in pids),
            "an exact run-owned process is still live or uncertain")
    require(cdp_connection_refused(port), "CDP endpoint must refuse connections")
    return {"project_root": str(project), "run_id": run_dir.name, "slug": slug,
            "state_sha256": expected_state_sha256, "meta_path": str(meta_path),
            "meta_sha256": expected_meta_sha256, "artifact_hashes": hashes,
            "stopped_process_ids": pids, "session_authority_preserved": "submitted_unknown"}


def retire(run_dir: Path, *, expected_state_sha256: str, expected_meta_sha256: str,
           confirmation: str, reason: str, dry_run: bool = False) -> dict[str, Any]:
    require(confirmation == CONFIRMATION and bool(reason.strip()), "explicit user retirement authority and reason required")
    caller = STATE.current_source_thread_id()
    require(caller is not None, "the authorizing Codex task must be identified")
    evidence = inspect_candidate(run_dir, expected_state_sha256, expected_meta_sha256)
    archive_root = run_dir.parent.parent / "retired-runs"
    receipt_root = run_dir.parent.parent / "retired-lock-receipts"
    archive = archive_root / run_dir.name
    intent = receipt_root / (run_dir.name + ".intent.json")
    completion = receipt_root / (run_dir.name + ".complete.json")
    require(not archive_root.is_symlink() and not receipt_root.is_symlink(), "archive parents must not be symlinks")
    require(not any(p.exists() or p.is_symlink() for p in (archive, intent, completion)),
            "a prior retirement exists; inspect its exact receipt without replaying")
    result = {"schema": "codex.chatgpt.oracle-legacy-retirement/v1", "ok": True,
              "status": "dry-run" if dry_run else "legacy-lock-retired",
              "evaluated_from_thread": caller, "target_source_thread_id": None,
              "confirmation": confirmation, "reason": reason.strip(),
              "original_run_dir": str(run_dir), "archive_run_dir": str(archive),
              "intent_receipt": str(intent), "completion_receipt": str(completion),
              "new_submission_authorized": False, "provider_outcome": "unknown", **evidence}
    if dry_run:
        return result
    with STATE.exact_run_recovery_mutex(run_dir, timeout_seconds=2):
        with STATE.project_submit_mutex(Path(evidence["project_root"]), timeout_seconds=2):
            require(inspect_candidate(run_dir, expected_state_sha256, expected_meta_sha256) == evidence,
                    "run evidence changed before retirement")
            require(not archive_root.is_symlink() and not receipt_root.is_symlink(), "archive parents changed")
            require(not any(p.exists() or p.is_symlink() for p in (archive, intent, completion)),
                    "retirement destination changed before the move")
            archive_root.mkdir(exist_ok=True)
            receipt_root.mkdir(exist_ok=True)
            intent_payload = {**result, "status": "retirement-intent", "created_at": datetime.now(timezone.utc).isoformat()}
            intent_hash = STATE._write_append_only_json(intent, intent_payload)
            run_dir.rename(archive)
            require(all(STATE.sha256_file(archive / name) == digest for name, digest in evidence["artifact_hashes"].items()),
                    "archived evidence verification failed; preserve archive and intent")
            result["intent_sha256"] = intent_hash
            result["completed_at"] = datetime.now(timezone.utc).isoformat()
            STATE._write_append_only_json(completion, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--expected-meta-sha256", required=True)
    parser.add_argument("--confirmation", choices=[CONFIRMATION], required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = vars(parser.parse_args())
    try:
        result = retire(**args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
