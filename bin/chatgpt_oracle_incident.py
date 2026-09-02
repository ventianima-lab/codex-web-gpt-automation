#!/usr/bin/env python
"""Single-owner incident packet contract for Oracle automation repairs.

A project session that hits an automation defect must hand over a bounded
incident packet: the exact run directory, the classified failure bucket, and
the evidence that supports it.  It must not patch automation sources itself.
Cross-session patching is what previously produced duplicate fixes, conflicting
state rules, and repairs aimed at the wrong layer.

This module is read-only with respect to run state: it validates and renders
packets, and never mutates a run, a browser, or a web session.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

BIN = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DIAGNOSE = _load("oracle_incident_diagnose", BIN / "chatgpt_oracle_diagnose.py")
STATE = DIAGNOSE.STATE

LEGACY_SCHEMA = "codex.chatgpt.oracle-incident/v1"
SCHEMA = "codex.chatgpt.oracle-incident/v2"
OPERATIONAL_INSTRUCTION_SCHEMA = "codex.chatgpt.oracle-operational-instruction/v1"
WORKFLOW_STATE_SCHEMA = "codex.chatgpt.oracle-comprehensive-state/v1"
MISSING_LAYOUT_PRE_SUBMIT_SCHEMA = "codex.chatgpt.oracle-missing-layout-pre-submit/v1"

# Exactly one role may edit automation sources.
MAINTENANCE_OWNER = "automation-maintenance-session"
REPORTER_ROLE = "project-session"

REQUIRED_FIELDS = (
    "schema",
    "run_dir",
    "bucket",
    "signature",
    "reporter_role",
    "repair_owner",
)

V2_REQUIRED_FIELDS = (
    "run_owner_source_thread_id",
    "evaluated_from_thread",
    "target_source_thread_id",
    "ownership_scope",
    "operational_instruction",
)


class IncidentError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}

    def envelope(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": str(self), "evidence": self.evidence}}


def _validate_reporter_role(reporter_role: str) -> None:
    if reporter_role != REPORTER_ROLE:
        raise IncidentError(
            "INCIDENT_REPORTER_ROLE_INVALID",
            f"an incident packet is reported by {REPORTER_ROLE}",
            {"reporter_role": reporter_role},
        )


def _report_identity(state: dict[str, Any]) -> tuple[str | None, str | None, str]:
    owner = STATE.source_thread_id_from_state(state)
    evaluator = STATE.current_source_thread_id()
    if evaluator is None:
        raise IncidentError(
            "INCIDENT_EVALUATED_FROM_THREAD_REQUIRED",
            "a v2 incident report requires the exact evaluating Codex task ID",
            {"run_id": state.get("run_id"), "owner_source_thread_id": owner},
        )
    if owner is None:
        scope = "legacy-unbound"
    elif evaluator == owner:
        scope = "same-task"
    else:
        scope = "foreign-task"
    return owner, evaluator, scope


def _operational_instruction(
    state: dict[str, Any],
    *,
    lifecycle: str,
    owner: str | None,
    evaluator: str | None,
    scope: str,
) -> dict[str, Any]:
    """Describe who may act without granting cross-task recovery authority."""
    run_id = str(state.get("run_id") or "")
    oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
    slug = str(oracle.get("slug") or oracle.get("session_locator") or "")
    if lifecycle == "complete":
        action = "none"
        reason = "exact-run-already-terminal"
        executable = False
    elif lifecycle == "pre_submit_settled" and owner is not None and evaluator == owner:
        action = "rerun-settled-workflow"
        reason = "workflow-proof-confirms-oracle-layout-was-never-created"
        executable = True
    elif owner is None:
        action = "none"
        reason = "legacy-run-has-no-task-recovery-authority"
        executable = False
    elif evaluator != owner:
        action = "route-to-owner-task"
        reason = "foreign-task-must-not-operate-on-exact-run"
        executable = False
    else:
        action = "inspect-owned-exact-run"
        reason = "owner-task-must-recheck-current-state-before-any-operation"
        executable = True
    return {
        "schema": OPERATIONAL_INSTRUCTION_SCHEMA,
        "evaluated_from_thread": evaluator,
        "target_source_thread_id": owner,
        "ownership_scope": scope,
        "run_id": run_id,
        "slug": slug,
        "action": action,
        "reason": reason,
        "executable_by_evaluated_thread": executable,
        "fresh_state_check_required": action in {
            "inspect-owned-exact-run",
            "rerun-settled-workflow",
        },
    }


def build_packet(run_dir: Path, *, reporter_role: str = REPORTER_ROLE) -> dict[str, Any]:
    """Build one incident packet from persisted run evidence only."""
    _validate_reporter_role(reporter_role)
    directory = run_dir.expanduser().resolve(strict=True)
    state_path = directory / "state.json"
    if not state_path.is_file():
        raise IncidentError(
            "INCIDENT_RUN_STATE_MISSING",
            "an incident packet requires the exact persisted run state",
            {"run_dir": str(directory)},
        )
    state = STATE.load_state(state_path)
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output_path = Path(str(artifacts.get("output") or (directory / "output.md")))
    verdict = DIAGNOSE.classify_run(
        state,
        stdout_text="\n".join((
            DIAGNOSE._read_text(directory / "stdout.log"),
            DIAGNOSE._read_text(directory / "stderr.log"),
        )),
        has_output=DIAGNOSE._output_is_nonempty(output_path),
        transcript_text=DIAGNOSE._read_text(directory / "transcript.md"),
        output_text=DIAGNOSE._read_text(output_path),
        user_confirmed_no_submission=(
            STATE.proven_user_confirmed_no_submission(state_path) is not None
        ),
        pre_submit_host_failure=STATE.proven_pre_submit_host_failure(state_path),
        pre_submit_session_absence=STATE.proven_pre_submit_session_absence(state_path),
    )
    lifecycle = STATE.resolve_lifecycle(
        state, output_is_present=DIAGNOSE._output_is_nonempty(output_path)
    )
    owner_thread, evaluated_from_thread, ownership_scope = _report_identity(state)
    bucket = str(verdict["bucket"])
    project_root = Path(str(state.get("project_root") or "")).expanduser().resolve(strict=True)
    owners = (
        STATE.unresolved_project_sessions(
            directory.parent,
            project_root,
            exclude_run_id=str(state.get("run_id") or ""),
            source_thread_id=owner_thread,
        )
        if owner_thread is not None
        else []
    )
    # A diagnostic bucket is not authority to replace a run.  In particular,
    # ``submitted_unknown`` can resemble a host/UI failure while still retaining
    # a possible browser submission.  Require the state module's exact durable
    # pre-submit proof before a same-task packet may say a fresh run is safe.
    pre_submit_authority = (
        STATE.proven_pre_submit_failure(state_path)
        or STATE.proven_pre_submit_session_absence(state_path)
    )
    recursive_authority = STATE.proven_recursive_self_observation_fresh_run_authority(
        state_path
    )
    terminal_nonexecution_authority = (
        STATE.proven_terminal_devspace_nonexecution_fresh_run_authority(state_path)
    )
    read_route_refresh_authority = (
        STATE.proven_terminal_devspace_read_route_refresh_fresh_run_authority(
            state_path
        )
    )
    if (
        (
            terminal_nonexecution_authority is not None
            and terminal_nonexecution_authority.get("authorized_source_thread_id")
            == evaluated_from_thread
        )
        or (
            read_route_refresh_authority is not None
            and read_route_refresh_authority.get("authorized_source_thread_id")
            == evaluated_from_thread
        )
    ):
        owners = STATE.unresolved_project_sessions(
            directory.parent,
            project_root,
            exclude_run_id=str(state.get("run_id") or ""),
            source_thread_id=evaluated_from_thread,
        )
    pre_submit_fresh_safe = (
        ownership_scope == "same-task"
        and bucket
        in {
            DIAGNOSE.PRE_SUBMIT_HOST,
            DIAGNOSE.PRE_SUBMIT_UI,
            DIAGNOSE.OWNERSHIP_CONFLICT,
        }
        and pre_submit_authority is not None
        and not owners
    )
    recursive_fresh_safe = (
        str(verdict["signature"]) == "post-submit-recursive-self-observation"
        and recursive_authority is not None
        and not owners
    )
    terminal_nonexecution_fresh_safe = (
        str(verdict["signature"])
        in STATE.TERMINAL_DEVSPACE_NONEXECUTION_SIGNATURES
        and terminal_nonexecution_authority is not None
        and terminal_nonexecution_authority.get("signature")
        == str(verdict["signature"])
        and terminal_nonexecution_authority.get("authorized_source_thread_id")
        == evaluated_from_thread
        and not owners
    )
    read_route_refresh_fresh_safe = (
        str(verdict["signature"])
        == STATE.TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SIGNATURE
        and read_route_refresh_authority is not None
        and read_route_refresh_authority.get("signature")
        == str(verdict["signature"])
        and read_route_refresh_authority.get("authorized_source_thread_id")
        == evaluated_from_thread
        and not owners
    )
    fresh_run_authority = (
        read_route_refresh_authority
        or terminal_nonexecution_authority
        or recursive_authority
        or (pre_submit_authority if pre_submit_fresh_safe else None)
    )
    return {
        "schema": SCHEMA,
        "run_dir": str(directory),
        "project_root": str(state.get("project_root") or ""),
        "bucket": bucket,
        "signature": str(verdict["signature"]),
        "lifecycle": str(lifecycle["lifecycle"]),
        "authority_source": str(lifecycle["authority_source"]),
        "conversation_url": str((state.get("oracle") or {}).get("conversation_url") or "")
        if isinstance(state.get("oracle"), dict)
        else "",
        "reporter_role": reporter_role,
        "repair_owner": MAINTENANCE_OWNER,
        "reporter_may_edit_automation_sources": False,
        "run_owner_source_thread_id": owner_thread,
        "evaluated_from_thread": evaluated_from_thread,
        "target_source_thread_id": owner_thread,
        "ownership_scope": ownership_scope,
        "operational_instruction": _operational_instruction(
            state,
            lifecycle=str(lifecycle["lifecycle"]),
            owner=owner_thread,
            evaluator=evaluated_from_thread,
            scope=ownership_scope,
        ),
        # Pre-submit proof remains the normal fresh-run authority. The only
        # terminal exceptions are exact task-bound append-only receipts.
        "safe_for_fresh_run": (
            (
                ownership_scope == "same-task"
                and (
                    pre_submit_fresh_safe
                    or recursive_fresh_safe
                    or read_route_refresh_fresh_safe
                )
            )
            or terminal_nonexecution_fresh_safe
        ),
        "unresolved_owners": owners,
        "fresh_run_authority": fresh_run_authority,
        "remediation": DIAGNOSE.REMEDIATION.get(bucket, ""),
        "evidence_paths": sorted(
            str(path)
            for path in (
                state_path,
                directory / "stdout.log",
                directory / "stderr.log",
                output_path,
            )
            if path.is_file()
        ),
    }


def build_missing_layout_packet(
    workflow_state_path: Path,
    *,
    reporter_role: str = REPORTER_ROLE,
) -> dict[str, Any]:
    """Report an exact missing-layout incident without granting replacement authority."""
    _validate_reporter_role(reporter_role)
    path = workflow_state_path.expanduser().resolve(strict=True)
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IncidentError(
            "INCIDENT_WORKFLOW_STATE_INVALID",
            "the comprehensive workflow state is not valid UTF-8 JSON",
            {"workflow_state_path": str(path)},
        ) from error
    if not isinstance(workflow, dict) or workflow.get("schema") != WORKFLOW_STATE_SCHEMA:
        raise IncidentError(
            "INCIDENT_WORKFLOW_STATE_INVALID",
            f"workflow state schema must be {WORKFLOW_STATE_SCHEMA}",
            {"workflow_state_path": str(path)},
        )
    records = workflow.get("records") if isinstance(workflow.get("records"), list) else []
    settlement = next(
        (
            record
            for record in reversed(records)
            if isinstance(record, dict)
            and record.get("settlement") == "oracle-layout-not-created-pre-submit"
        ),
        None,
    )
    proof = settlement.get("settlement_proof") if isinstance(settlement, dict) else None
    if (
        not isinstance(proof, dict)
        or proof.get("schema") != MISSING_LAYOUT_PRE_SUBMIT_SCHEMA
        or proof.get("kind") != "oracle-layout-not-created"
        or proof.get("safe_for_fresh_run") is not False
        or str(proof.get("workflow_id") or "") != str(workflow.get("workflow_id") or "")
        or str(proof.get("attempt_id") or "") != str(settlement.get("run_id") or "")
        or str(proof.get("run_dir") or "") != str(settlement.get("run_dir") or "")
    ):
        raise IncidentError(
            "INCIDENT_MISSING_LAYOUT_PROOF_INVALID",
            "workflow state lacks an exact missing-layout pre-submit settlement proof",
            {"workflow_state_path": str(path)},
        )
    run_dir = Path(str(proof["run_dir"])).expanduser()
    manifest_path = Path(str(proof.get("oracle_manifest_path") or "")).expanduser()
    if not run_dir.is_absolute() or run_dir.exists() or run_dir.is_symlink():
        raise IncidentError(
            "INCIDENT_MISSING_LAYOUT_PROOF_INVALID",
            "the settled Oracle run directory must remain absent",
            {"run_dir": str(run_dir)},
        )
    try:
        manifest_path = manifest_path.resolve(strict=True)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        project_root = Path(str(manifest.get("project_root") or "")).expanduser().resolve(strict=True)
    except (OSError, UnicodeDecodeError, TypeError, json.JSONDecodeError) as error:
        raise IncidentError(
            "INCIDENT_MISSING_LAYOUT_BINDING_INVALID",
            "the settled attempt's Oracle manifest binding is unavailable",
            {"oracle_manifest_path": str(manifest_path)},
        ) from error
    attempt_id = str(proof["attempt_id"])
    manifest_run_id = str(manifest.get("run_id") or manifest.get("requested_run_id") or "")
    if not isinstance(manifest, dict) or manifest_run_id != attempt_id or not project_root.is_dir():
        raise IncidentError(
            "INCIDENT_MISSING_LAYOUT_BINDING_INVALID",
            "the settled attempt does not match its Oracle manifest",
            {"oracle_manifest_path": str(manifest_path), "attempt_id": attempt_id},
        )
    owner = str(workflow.get("source_thread_id") or "").strip().casefold() or None
    evaluator = STATE.current_source_thread_id()
    if evaluator is None:
        raise IncidentError(
            "INCIDENT_EVALUATED_FROM_THREAD_REQUIRED",
            "a v2 incident report requires the exact evaluating Codex task ID",
            {"attempt_id": attempt_id, "owner_source_thread_id": owner},
        )
    scope = "legacy-unbound" if owner is None else "same-task" if owner == evaluator else "foreign-task"
    owners = (
        STATE.unresolved_project_sessions(
            run_dir.resolve().parent,
            project_root,
            exclude_run_id=attempt_id,
            source_thread_id=owner,
        )
        if owner is not None
        else []
    )
    state_stub = {
        "run_id": attempt_id,
        "source_thread_id": owner,
        "oracle": {"slug": f"oracle-settled-{attempt_id[:10]}"},
    }
    return {
        "schema": SCHEMA,
        "run_dir": str(run_dir.resolve()),
        "project_root": str(project_root),
        "bucket": DIAGNOSE.PRE_SUBMIT_HOST,
        "signature": "oracle-layout-not-created-pre-submit",
        "lifecycle": "attention_required",
        "authority_source": "workflow-missing-layout-unproven",
        "conversation_url": "",
        "reporter_role": reporter_role,
        "repair_owner": MAINTENANCE_OWNER,
        "reporter_may_edit_automation_sources": False,
        "run_owner_source_thread_id": owner,
        "evaluated_from_thread": evaluator,
        "target_source_thread_id": owner,
        "ownership_scope": scope,
        "operational_instruction": _operational_instruction(
            state_stub,
            lifecycle="attention_required",
            owner=owner,
            evaluator=evaluator,
            scope=scope,
        ),
        "safe_for_fresh_run": False,
        "unresolved_owners": owners,
        "fresh_run_authority": None,
        "remediation": DIAGNOSE.REMEDIATION.get(DIAGNOSE.PRE_SUBMIT_HOST, ""),
        "evidence_paths": [str(path), str(manifest_path)],
    }


def validate_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Reject a packet that is malformed or claims cross-session repair rights."""
    if not isinstance(packet, dict):
        raise IncidentError("INCIDENT_PACKET_INVALID", "an incident packet must be one JSON object")
    missing = [field for field in REQUIRED_FIELDS if not str(packet.get(field) or "").strip()]
    if missing:
        raise IncidentError(
            "INCIDENT_PACKET_INCOMPLETE",
            "an incident packet requires the exact run, bucket, signature, and ownership fields",
            {"missing": missing},
        )
    if packet.get("schema") not in {LEGACY_SCHEMA, SCHEMA}:
        raise IncidentError(
            "INCIDENT_SCHEMA_INVALID",
            f"incident schema must be {LEGACY_SCHEMA} or {SCHEMA}",
        )
    if packet.get("bucket") not in DIAGNOSE.BUCKETS:
        raise IncidentError(
            "INCIDENT_BUCKET_UNKNOWN",
            "an incident packet must carry a classified bucket from the diagnosis report",
            {"bucket": packet.get("bucket")},
        )
    if packet.get("repair_owner") != MAINTENANCE_OWNER:
        raise IncidentError(
            "INCIDENT_REPAIR_OWNER_INVALID",
            f"automation repairs are owned only by {MAINTENANCE_OWNER}",
            {"repair_owner": packet.get("repair_owner")},
        )
    if packet.get("reporter_may_edit_automation_sources") is not False:
        raise IncidentError(
            "INCIDENT_REPORTER_SCOPE_INVALID",
            "a reporting project session must not edit automation sources",
        )
    if not isinstance(packet.get("evidence_paths"), list) or not packet["evidence_paths"]:
        raise IncidentError(
            "INCIDENT_EVIDENCE_MISSING",
            "an incident packet requires at least one existing evidence path",
        )
    if packet.get("schema") == LEGACY_SCHEMA:
        if "operational_instruction" in packet:
            raise IncidentError(
                "INCIDENT_LEGACY_OPERATION_FORBIDDEN",
                "a legacy v1 incident packet is evidence-only and cannot carry an operational instruction",
            )
        return packet
    if packet.get("schema") == SCHEMA:
        missing_v2 = [field for field in V2_REQUIRED_FIELDS if field not in packet]
        if missing_v2:
            raise IncidentError(
                "INCIDENT_ROUTING_INCOMPLETE",
                "a v2 incident packet requires an explicit evaluation and target task routing contract",
                {"missing": missing_v2},
            )
        owner = packet.get("run_owner_source_thread_id")
        evaluator = packet.get("evaluated_from_thread")
        target = packet.get("target_source_thread_id")
        scope = str(packet.get("ownership_scope") or "")
        instruction = packet.get("operational_instruction")
        if not isinstance(instruction, dict):
            raise IncidentError(
                "INCIDENT_OPERATIONAL_INSTRUCTION_INVALID",
                "the operational instruction must be one object",
            )
        if (
            instruction.get("schema") != OPERATIONAL_INSTRUCTION_SCHEMA
            or target != owner
            or instruction.get("target_source_thread_id") != target
            or instruction.get("evaluated_from_thread") != evaluator
            or instruction.get("ownership_scope") != scope
            or instruction.get("run_id") != Path(str(packet["run_dir"])).name
        ):
            raise IncidentError(
                "INCIDENT_OPERATIONAL_INSTRUCTION_MISMATCH",
                "the operational instruction must remain bound to the exact evaluator, owner task, and run",
            )
        if STATE.SOURCE_THREAD_ID_RE.fullmatch(str(evaluator or "")) is None:
            raise IncidentError(
                "INCIDENT_EVALUATED_FROM_THREAD_INVALID",
                "evaluated_from_thread must be one exact Codex task UUID",
            )
        if owner is not None and STATE.SOURCE_THREAD_ID_RE.fullmatch(str(owner)) is None:
            raise IncidentError(
                "INCIDENT_TARGET_SOURCE_THREAD_INVALID",
                "target_source_thread_id must be one exact owner task UUID or null for a legacy-unbound run",
            )
        if scope not in {"same-task", "foreign-task", "legacy-unbound"}:
            raise IncidentError(
                "INCIDENT_OWNERSHIP_SCOPE_INVALID",
                "ownership_scope must be same-task, foreign-task, or legacy-unbound",
            )
        expected_scope = (
            "legacy-unbound"
            if owner is None
            else "same-task"
            if owner == evaluator
            else "foreign-task"
        )
        if scope != expected_scope:
            raise IncidentError(
                "INCIDENT_OWNERSHIP_SCOPE_INVALID",
                "ownership_scope must be derived exactly from the owner and evaluator task IDs",
                {"expected": expected_scope, "actual": scope},
            )
        lifecycle = str(packet.get("lifecycle") or "")
        if lifecycle == "complete":
            expected_instruction = (
                "none",
                "exact-run-already-terminal",
                False,
                False,
            )
        elif lifecycle == "pre_submit_settled" and scope == "same-task":
            expected_instruction = (
                "rerun-settled-workflow",
                "workflow-proof-confirms-oracle-layout-was-never-created",
                True,
                True,
            )
        elif scope == "legacy-unbound":
            expected_instruction = (
                "none",
                "legacy-run-has-no-task-recovery-authority",
                False,
                False,
            )
        elif scope == "foreign-task":
            expected_instruction = (
                "route-to-owner-task",
                "foreign-task-must-not-operate-on-exact-run",
                False,
                False,
            )
        else:
            expected_instruction = (
                "inspect-owned-exact-run",
                "owner-task-must-recheck-current-state-before-any-operation",
                True,
                True,
            )
        actual_instruction = (
            instruction.get("action"),
            instruction.get("reason"),
            instruction.get("executable_by_evaluated_thread"),
            instruction.get("fresh_state_check_required"),
        )
        if actual_instruction != expected_instruction:
            raise IncidentError(
                (
                    "INCIDENT_TERMINAL_OPERATION_FORBIDDEN"
                    if lifecycle == "complete"
                    else "INCIDENT_OPERATIONAL_ACTION_INVALID"
                ),
                "the operational action must match the exact lifecycle and task ownership scope",
                {"expected": expected_instruction, "actual": actual_instruction},
            )
        if packet.get("safe_for_fresh_run") is True and packet.get("bucket") in {
            DIAGNOSE.PRE_SUBMIT_HOST,
            DIAGNOSE.PRE_SUBMIT_UI,
            DIAGNOSE.OWNERSHIP_CONFLICT,
        }:
            state_path = Path(str(packet["run_dir"])) / "state.json"
            proof = (
                STATE.proven_pre_submit_failure(state_path)
                or STATE.proven_pre_submit_session_absence(state_path)
            )
            if proof is None or packet.get("fresh_run_authority") != proof:
                raise IncidentError(
                    "INCIDENT_FRESH_RUN_AUTHORITY_INVALID",
                    "pre-submit fresh-run authority requires exact durable state evidence",
                )
        if packet.get("safe_for_fresh_run") is True and packet.get("bucket") == DIAGNOSE.TASK_NOT_EXECUTED:
            state_path = Path(str(packet["run_dir"])) / "state.json"
            signature = str(packet.get("signature") or "")
            if signature == "post-submit-recursive-self-observation":
                proof = STATE.proven_recursive_self_observation_fresh_run_authority(state_path)
            elif signature in STATE.TERMINAL_DEVSPACE_NONEXECUTION_SIGNATURES:
                proof = STATE.proven_terminal_devspace_nonexecution_fresh_run_authority(state_path)
                if proof is not None and proof.get("authorized_source_thread_id") != evaluator:
                    proof = None
            elif signature == STATE.TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SIGNATURE:
                proof = STATE.proven_terminal_devspace_read_route_refresh_fresh_run_authority(
                    state_path
                )
                if proof is not None and proof.get("authorized_source_thread_id") != evaluator:
                    proof = None
            else:
                proof = None
            claimed = packet.get("fresh_run_authority")
            if (
                proof is None
                or not isinstance(claimed, dict)
                or claimed.get("sha256") != proof.get("sha256")
            ):
                raise IncidentError(
                    "INCIDENT_FRESH_RUN_AUTHORITY_INVALID",
                    "terminal fresh-run authority requires a revalidated, exact append-only receipt",
                )
    return packet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or validate one Oracle incident packet.")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report")
    source = report.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-dir", type=Path)
    source.add_argument("--workflow-state", type=Path)
    check = commands.add_parser("validate")
    check.add_argument("--packet", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "report":
            packet = validate_packet(
                build_packet(args.run_dir)
                if args.run_dir is not None
                else build_missing_layout_packet(args.workflow_state)
            )
        else:
            packet = validate_packet(
                json.loads(args.packet.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
            )
    except IncidentError as error:
        print(json.dumps(error.envelope(), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, "packet": packet}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
