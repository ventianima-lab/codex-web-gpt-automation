from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA = "codex.chatgpt.oracle-comprehensive/v1"
RECEIPT_SCHEMA = "codex.chatgpt.oracle-stage-result/v1"
PRO_OUTPUT_SCHEMA = "codex.chatgpt.oracle-pro-stage-output/v1"
REGULAR_OUTPUT_SCHEMA = "codex.chatgpt.oracle-regular-stage-output/v1"
REGULAR_OUTPUT_BEGIN = "[ORACLE_STAGE_OUTPUT]"
REGULAR_OUTPUT_END = "[/ORACLE_STAGE_OUTPUT]"
REGULAR_OUTPUT_KEYS = {
    "schema", "workflow_id", "stage", "attempt_id", "input_mission_sha256",
    "status", "output_text", "next_stage", "next_mission_text", "ready_for_next",
    "blocker", "critical_finding_ids", "critical_findings_sha256",
}
PRO_ATTACHMENT_SCHEMA = "codex.chatgpt.oracle-pro-attachments/v1"
PRO_ATTACHMENT_BEGIN = "[PRO_ATTACHMENT_CONTRACT]"
PRO_ATTACHMENT_END = "[/PRO_ATTACHMENT_CONTRACT]"
PRO_OUTPUT_KEYS = {
    "schema", "workflow_id", "stage", "attempt_id", "input_mission_sha256",
    "status", "output_text", "next_stage", "next_mission_text", "ready_for_next", "blocker",
}
PRO_OUTPUT_PREFIX_KEYS = (
    "schema", "workflow_id", "stage", "attempt_id", "input_mission_sha256", "status",
)
PRO_OUTPUT_RECOVERY_SCHEMA = "codex.chatgpt.oracle-pro-output-recovery/v1"
STATE_SCHEMA = "codex.chatgpt.oracle-comprehensive-state/v1"
SCOPE_SCHEMA = "codex.chatgpt.oracle-comprehensive-scope/v1"
MISSING_LAYOUT_PRE_SUBMIT_SCHEMA = "codex.chatgpt.oracle-missing-layout-pre-submit/v1"
USER_STOP_SETTLEMENT_SCHEMA = "codex.chatgpt.oracle-comprehensive-user-stop/v1"
USER_STOP_COMPLETION_SCHEMA = "codex.chatgpt.oracle-comprehensive-user-stop-completion/v1"
USER_STOP_CONFIRMATION = "user-confirmed-provider-stop"
PRE_SUBMIT_CANCEL_CONFIRMATION = "user-confirmed-pre-submit-workflow-cancel"
PRE_SUBMIT_DEVSPACE_BRIDGE_TIMEOUT = (
    "version resolution failed: DevSpace large single-line read bridge check timed out"
)
PRE_SUBMIT_DEVSPACE_SERVICE_RESTART = (
    "version resolution failed: DEVSPACE_SERVICE_RESTART_REQUIRED: "
    "DevSpace was safely patched before submission and must be restarted once"
)
PRE_SUBMIT_CANCEL_ERRORS = {
    PRE_SUBMIT_DEVSPACE_BRIDGE_TIMEOUT: "pre-submit-devspace-bridge-timeout",
    PRE_SUBMIT_DEVSPACE_SERVICE_RESTART: "pre-submit-devspace-service-restart-required",
}
TERMINAL_SCOPE_STATUSES = {"complete", "canceled", "blocked"}
USER_STOPPABLE_OUTCOMES = {"blocked", "not_executed", "pending", "unknown"}
STANDARD_PROFILE = "standard"
ULTRA_ECONOMY_PROFILE = "ultra-economy"
ULTRA_GPT_PROFILE = "ultra-gpt"
# Compatibility-only input accepted for manifests created before v1.19.0.
# New workflows select ultra-gpt and add a closed_audit contract instead of
# presenting audit policy as another execution mode.
STRICT_ULTRA_PROFILE = "strict-ultra"
MAX_PLAN_REVISIONS = 2
REVIEW_STATUSES = {"PASS", "PASS_WITH_NOTES", "REVISE", "FAIL"}
UNAMBIGUOUS_PRE_SUBMIT_MARKERS = (
    "ChatGPT app mention suggestion did not appear.",
    "ChatGPT app mention was not confirmed in the composer.",
    "Exact ChatGPT app suggestion could not be clicked.",
    # These launch-time failures also happen strictly before the composer can
    # send anything, so they are safe to retry once with the same mission.
    "Unable to find model option matching",
    "--copy-profile requires rsync on PATH",
    "--copy-profile cannot be combined with",
)
STAGES = {"plan", "pro", "web-multi", "review", "implementation", "final-web-gate"}
TRANSITIONS = {
    "plan": {"plan", "review", "web-multi", "pro"},
    "web-multi": {"review"},
    "pro": {"review"},
    "review": {"implementation"},
    "implementation": {"final-web-gate"},
    "final-web-gate": {"complete", "implementation"},
}
BIN = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load("oracle_comprehensive_runner", BIN / "chatgpt_oracle_run.py")
MULTI = _load("oracle_comprehensive_multi", BIN / "chatgpt_oracle_multi.py")
STRICT_ULTRA = _load("oracle_comprehensive_strict_ultra", BIN / "chatgpt_strict_ultra.py")
WORKSPACE_CONFIG = _load("oracle_comprehensive_workspace_config", BIN / "chatgpt_workspace_config.py")


def _is_ultra_gpt(config_or_profile: dict[str, Any] | str) -> bool:
    profile = (
        str(config_or_profile.get("workflow_profile") or "")
        if isinstance(config_or_profile, dict)
        else str(config_or_profile or "")
    ).strip().casefold()
    return profile in {ULTRA_GPT_PROFILE, STRICT_ULTRA_PROFILE}


def _closed_audit_enabled(config: dict[str, Any]) -> bool:
    return config.get("closed_audit_enabled") is True


class WorkflowError(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WorkflowError(f"JSON object required: {path}")
    return value


def _json_object_no_duplicates(text: str, *, label: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise WorkflowError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=pairs)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"{label} must be strict JSON") from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be a JSON object")
    return value


def _inside(root: Path, value: Any, *, exists: bool = True) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        raise WorkflowError("workflow paths must be absolute")
    path = path.resolve(strict=exists)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WorkflowError(f"path outside project: {path}") from exc
    return path


def _receipt_path(root: Path, value: Any, *, exists: bool = True) -> tuple[Path, bool]:
    """Resolve a receipt artifact, compatibly anchoring legacy relative paths to project_root."""
    raw = str(value or "").strip()
    if not raw:
        raise WorkflowError("workflow path is required")
    candidate = Path(raw).expanduser()
    relative_compat = not candidate.is_absolute()
    if relative_compat:
        if candidate.drive:
            raise WorkflowError("drive-relative workflow paths are forbidden")
        candidate = root / candidate
    path = candidate.resolve(strict=exists)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WorkflowError(f"path outside project: {path}") from exc
    return path, relative_compat


def load_manifest(path: Path) -> dict[str, Any]:
    value = _json(path.resolve(strict=True))
    if value.get("schema") != SCHEMA:
        raise WorkflowError(f"schema must be {SCHEMA}")
    root = Path(str(value.get("project_root") or "")).expanduser().resolve(strict=True)
    workflow_dir = _inside(root, value.get("workflow_dir"), exists=False)
    mission = _inside(root, value.get("initial_mission_path"))
    maximum = int(value.get("max_stages", 8))
    if not 1 <= maximum <= 12:
        raise WorkflowError("max_stages must be within 1..12")
    workflow_profile = str(value.get("workflow_profile") or STANDARD_PROFILE).strip().casefold()
    if workflow_profile not in {STANDARD_PROFILE, ULTRA_ECONOMY_PROFILE, ULTRA_GPT_PROFILE, STRICT_ULTRA_PROFILE}:
        raise WorkflowError(
            "workflow_profile must be standard, ultra-economy, or ultra-gpt; "
            "strict-ultra is accepted only as a deprecated compatibility alias"
        )
    legacy_strict_alias = workflow_profile == STRICT_ULTRA_PROFILE
    closed_audit_present = "closed_audit" in value
    closed_audit_raw = value.get("closed_audit")
    if closed_audit_present and not isinstance(closed_audit_raw, dict):
        raise WorkflowError("closed_audit must be an object with contract_path and contract_sha256")
    closed_audit_enabled = legacy_strict_alias or closed_audit_present
    if closed_audit_present and workflow_profile != ULTRA_GPT_PROFILE:
        raise WorkflowError("CLOSED_WORKFLOW_AUDIT_REQUIRES_ULTRA_GPT")
    if isinstance(closed_audit_raw, dict) and set(closed_audit_raw) != {"contract_path", "contract_sha256"}:
        raise WorkflowError("CLOSED_WORKFLOW_AUDIT_KEYSET_MISMATCH")
    allow_pro_raw = value.get("allow_pro", False)
    if not isinstance(allow_pro_raw, bool):
        raise WorkflowError("allow_pro must be a boolean explicit opt-in")
    allow_pro = allow_pro_raw
    regular_model = str(value.get("model") or "gpt-5.6").strip()
    initial_stage = str(
        value.get("initial_stage")
        or ("pro" if workflow_profile == ULTRA_ECONOMY_PROFILE else "plan")
    ).strip().casefold()
    if "local_runtime_contract" in value:
        raise WorkflowError(
            "local_runtime_contract is not accepted; ultra-economy activation is a one-time conversational handshake"
        )
    if workflow_profile == ULTRA_ECONOMY_PROFILE:
        if not allow_pro:
            raise WorkflowError(
                "ULTRA_ECONOMY_PRO_AUTHORIZATION_REQUIRED: Ultra Economy activation does not authorize Pro; "
                "require a separate explicit user authorization and allow_pro=true"
            )
        if initial_stage != "pro":
            raise WorkflowError("ULTRA_ECONOMY_INITIAL_STAGE_REQUIRED: initial_stage must be pro")
        if maximum < 4:
            raise WorkflowError("ULTRA_ECONOMY_STAGE_BUDGET_TOO_SMALL: max_stages must be at least 4")
        if regular_model != "gpt-5.6":
            raise WorkflowError(
                "COMPREHENSIVE_REGULAR_MODEL_REQUIRED: regular write stages must use gpt-5.6 at extra-high"
            )
    elif _is_ultra_gpt(workflow_profile):
        if allow_pro:
            raise WorkflowError(
                "ULTRA_GPT_PRO_IS_SEPARATE: use at most one explicitly authorized Pro design advisory before "
                "the ultra-gpt workflow; the workflow itself remains regular non-Pro"
            )
        if initial_stage != "plan":
            raise WorkflowError("ULTRA_GPT_INITIAL_STAGE_REQUIRED: initial_stage must be plan")
        if maximum < 5:
            raise WorkflowError("ULTRA_GPT_STAGE_BUDGET_TOO_SMALL: max_stages must be at least 5")
        if regular_model != "gpt-5.6":
            raise WorkflowError("ULTRA_GPT_REGULAR_MODEL_REQUIRED: model must be gpt-5.6")
    else:
        if initial_stage != "plan":
            raise WorkflowError("standard workflow initial_stage must be plan")
        if regular_model != "gpt-5.6":
            raise WorkflowError(
                "COMPREHENSIVE_REGULAR_MODEL_REQUIRED: regular write stages must use gpt-5.6 at extra-high"
            )
    local_gate = value.get("local_gate_command")
    if not isinstance(local_gate, list) or not local_gate or not all(isinstance(item, str) and item for item in local_gate):
        raise WorkflowError("local_gate_command must be a nonempty string list")
    state_root = RUNNER.STATE.oracle_state_root()
    if RUNNER.STATE.is_within(root, state_root) or RUNNER.STATE.is_within(state_root, root):
        raise WorkflowError("host state must be disjoint from project")
    workflow_id = str(value.get("workflow_id") or "").strip()
    if not workflow_id or not all(character in "0123456789abcdef-" for character in workflow_id.casefold()):
        raise WorkflowError("workflow_id must be stable hex/UUID text")
    try:
        app_name = WORKSPACE_CONFIG.normalize_app_name(
            value.get("app_name") or WORKSPACE_CONFIG.configured_app_name()
        )
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc
    explicit_source_thread_id = str(value.get("source_thread_id") or "").strip().casefold() or None
    runtime_source_thread_id = RUNNER.STATE.current_source_thread_id()
    if (
        explicit_source_thread_id is not None
        and runtime_source_thread_id is not None
        and explicit_source_thread_id != runtime_source_thread_id
    ):
        raise WorkflowError(
            "SOURCE_THREAD_ID_MISMATCH: comprehensive manifest belongs to a different Codex task"
        )
    source_thread_id = explicit_source_thread_id or runtime_source_thread_id
    if source_thread_id is not None and RUNNER.STATE.SOURCE_THREAD_ID_RE.fullmatch(source_thread_id) is None:
        raise WorkflowError("source_thread_id must be one Codex task UUID")
    normalized = {
        **value,
        "project_root": root,
        "workflow_dir": workflow_dir,
        "initial_mission_path": mission,
        "max_stages": maximum,
        "workflow_profile": workflow_profile,
        "workflow_profile_canonical": ULTRA_GPT_PROFILE if legacy_strict_alias else workflow_profile,
        "workflow_profile_legacy_alias": legacy_strict_alias,
        "closed_audit_enabled": closed_audit_enabled,
        "allow_pro": allow_pro,
        "initial_stage": initial_stage,
        "app_name": app_name,
        "model": regular_model,
        "copy_profile": Path(
            str(value.get("copy_profile") or (Path.home() / ".oracle" / "browser-profile"))
        ).expanduser().resolve(),
        "local_gate_command": list(local_gate),
        "manifest_sha256": sha(path.resolve(strict=True)),
        "workflow_id": workflow_id,
        "source_thread_id": source_thread_id,
    }
    if closed_audit_enabled:
        legacy_allowed = {
            "schema", "workflow_id", "project_root", "workflow_dir", "initial_mission_path",
            "workflow_profile", "initial_stage", "max_stages", "allow_pro", "app_name", "model",
            "copy_profile", "local_gate_command", "strict_ultra_contract_path",
            "strict_ultra_contract_sha256", "source_thread_id",
        }
        integrated_allowed = {
            "schema", "workflow_id", "project_root", "workflow_dir", "initial_mission_path",
            "workflow_profile", "closed_audit", "initial_stage", "max_stages", "allow_pro",
            "app_name", "model", "copy_profile", "local_gate_command", "source_thread_id",
        }
        allowed = legacy_allowed if legacy_strict_alias else integrated_allowed
        extra = sorted(set(value) - allowed)
        if extra:
            raise WorkflowError(f"CLOSED_WORKFLOW_AUDIT_MANIFEST_EXTRA_KEYS: {extra}")
        if allow_pro:
            raise WorkflowError("CLOSED_WORKFLOW_AUDIT_PRO_FORBIDDEN: pre-workflow Pro is separate and advisory-only")
        contract_path = (
            value.get("strict_ultra_contract_path")
            if legacy_strict_alias
            else closed_audit_raw.get("contract_path")
        )
        contract_sha256 = (
            value.get("strict_ultra_contract_sha256")
            if legacy_strict_alias
            else closed_audit_raw.get("contract_sha256")
        )
        try:
            normalized["strict_ultra"] = STRICT_ULTRA.load_contract(
                root, contract_path, contract_sha256
            )
        except STRICT_ULTRA.StrictUltraError as exc:
            raise WorkflowError(str(exc)) from exc
    return normalized


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid.uuid4().hex[:12]}.tmp")
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _finding_hash(ids: list[str]) -> str:
    payload = json.dumps(ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _receipt_finding_ids(value: dict[str, Any], *, legacy_fallback: bool) -> list[str]:
    raw = value.get("critical_finding_ids")
    if raw is None and legacy_fallback:
        output_hash = str(value.get("output_sha256") or "")
        return [f"legacy-{output_hash[:24]}"] if output_hash else []
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise WorkflowError("review critical_finding_ids must be a string list")
    normalized = [item.strip() for item in raw]
    if normalized != sorted(set(normalized)):
        raise WorkflowError("review critical finding IDs must be unique and sorted")
    claimed = str(value.get("critical_findings_sha256") or "")
    if claimed and claimed != _finding_hash(normalized):
        raise WorkflowError("review critical findings hash mismatch")
    return normalized


def _default_review_policy() -> dict[str, Any]:
    return {
        "max_plan_revisions": MAX_PLAN_REVISIONS,
        "plan_revisions_used": 0,
        "plan_revisions_remaining": MAX_PLAN_REVISIONS,
        "baseline_critical_finding_ids": [],
        "baseline_critical_findings_sha256": None,
    }


def _review_policy_from_history(config: dict[str, Any]) -> dict[str, Any]:
    if "workflow_dir" not in config:
        return _default_review_policy()
    parent = config["workflow_dir"].parent
    receipts: list[tuple[int, Path, dict[str, Any]]] = []
    seen_attempts: set[str] = set()
    for workflow_dir in sorted(
        (item for item in parent.iterdir() if item.is_dir() and item.name.startswith("workflow")),
        key=lambda item: item.name,
    ):
        stages = workflow_dir / "stages"
        if not stages.is_dir():
            continue
        for path in sorted(stages.glob("*-review-*/stage-result.json")):
            try:
                value = _json(path)
                attempt = str(value.get("attempt_id") or "")
                if (
                    value.get("schema") != RECEIPT_SCHEMA
                    or value.get("stage") != "review"
                    or not attempt
                    or attempt in seen_attempts
                    or value.get("status") not in REVIEW_STATUSES
                ):
                    continue
                seen_attempts.add(attempt)
                receipts.append((path.stat().st_mtime_ns, path, value))
            except (OSError, ValueError, WorkflowError, json.JSONDecodeError):
                continue
    receipts.sort(key=lambda item: (item[0], str(item[1])))
    revisions = [value for _, _, value in receipts if value.get("status") == "REVISE"]
    baseline_ids: list[str] = []
    if revisions:
        baseline_ids = _receipt_finding_ids(revisions[0], legacy_fallback=True)
    return {
        "max_plan_revisions": MAX_PLAN_REVISIONS,
        "plan_revisions_used": len(revisions),
        "plan_revisions_remaining": max(0, MAX_PLAN_REVISIONS - len(revisions)),
        "baseline_critical_finding_ids": baseline_ids,
        "baseline_critical_findings_sha256": _finding_hash(baseline_ids) if baseline_ids else None,
    }


def _validate_ultra_gpt_multi(config: dict[str, Any], multi_config: dict[str, Any]) -> None:
    if not _is_ultra_gpt(config):
        return
    if not 2 <= len(multi_config["solvers"]) <= 5:
        raise WorkflowError("ULTRA_GPT_SOLVER_COUNT_INVALID: web-multi requires 2..5 lanes")
    if multi_config["max_concurrency"] > 3:
        raise WorkflowError("ULTRA_GPT_CONCURRENCY_EXCEEDED: max_concurrency must be at most 3")
    if not multi_config.get("strict"):
        raise WorkflowError("ULTRA_GPT_STRICT_MULTI_V2_REQUIRED")
    if multi_config["project_root"] != config["project_root"]:
        raise WorkflowError("ULTRA_GPT_CANONICAL_ROOT_MISMATCH")
    if multi_config["app_name"] != config["app_name"] or multi_config["model"] != config["model"]:
        raise WorkflowError("ULTRA_GPT_PROVIDER_BINDING_MISMATCH")
    if multi_config["copy_profile"] != config["copy_profile"]:
        raise WorkflowError("ULTRA_GPT_PROFILE_BINDING_MISMATCH")
    if multi_config.get("next_stage_result_path") is None:
        raise WorkflowError("ULTRA_GPT_RESULT_RECEIPT_REQUIRED")
    if any(lane["access"] != "worktree-write" or not lane.get("owned_paths") for lane in multi_config["solvers"]):
        raise WorkflowError(
            "ULTRA_GPT_PARALLEL_WRITERS_REQUIRED: every solver must use an isolated worktree with owned_paths"
        )


def _allowed_transitions(config: dict[str, Any], stage: str) -> set[str]:
    if _is_ultra_gpt(config):
        return {
            "plan": {"review"},
            "review": {"web-multi"},
            "web-multi": {"final-web-gate"},
            "final-web-gate": {"complete"},
        }.get(stage, set())
    return TRANSITIONS[stage]


def _scope_path(config: dict[str, Any]) -> Path:
    project_key = hashlib.sha256(str(config["project_root"]).casefold().encode("utf-8")).hexdigest()[:24]
    scope_material = (
        f"{config['project_root']}|{config['workflow_dir'].parent}|"
        f"{config.get('source_thread_id') or 'legacy-unbound'}"
    ).casefold()
    scope_key = hashlib.sha256(scope_material.encode("utf-8")).hexdigest()[:32]
    return RUNNER.STATE.oracle_state_root() / "comprehensive-scopes" / project_key / f"{scope_key}.json"


def _claim_scope(config: dict[str, Any], workflow_id: str) -> None:
    path = _scope_path(config)
    if path.is_file():
        stored = _json(path)
        active = str(stored.get("active_workflow_id") or "")
        status = str(stored.get("status") or "")
        if active == workflow_id and status in TERMINAL_SCOPE_STATUSES:
            return
        if active and active != workflow_id and status not in TERMINAL_SCOPE_STATUSES:
            raise WorkflowError(
                f"comprehensive scope already belongs to active workflow {active}; recover that exact workflow"
            )
    _write(path, {
        "schema": SCOPE_SCHEMA,
        "status": "active",
        "active_workflow_id": workflow_id,
        "project_root": str(config["project_root"]),
        "workflow_parent": str(config["workflow_dir"].parent),
        "source_thread_id": config.get("source_thread_id"),
        "review_policy": config["_review_policy"],
    })


def _write_workflow_state(path: Path, config: dict[str, Any], value: dict[str, Any]) -> None:
    payload = {
        **value,
        "source_thread_id": config.get("source_thread_id"),
        "workflow_profile": config["workflow_profile"],
        "workflow_profile_canonical": config["workflow_profile_canonical"],
        "workflow_profile_legacy_alias": config["workflow_profile_legacy_alias"],
        "closed_audit_enabled": config["closed_audit_enabled"],
        "review_policy": dict(config["_review_policy"]),
    }
    _write(path, payload)
    scope_status = str(payload.get("status") or "active")
    scope_payload = {
        "schema": SCOPE_SCHEMA,
        "status": scope_status if scope_status in {"complete", "canceled", "blocked", "attention_required", "failed"} else "active",
        "active_workflow_id": config["workflow_id"],
        "project_root": str(config["project_root"]),
        "workflow_parent": str(config["workflow_dir"].parent),
        "source_thread_id": config.get("source_thread_id"),
        "workflow_profile": config["workflow_profile"],
        "workflow_profile_canonical": config["workflow_profile_canonical"],
        "workflow_profile_legacy_alias": config["workflow_profile_legacy_alias"],
        "closed_audit_enabled": config["closed_audit_enabled"],
        "workflow_state_path": str(path),
        "review_policy": dict(config["_review_policy"]),
    }
    if (
        scope_status == "blocked"
        and payload.get("terminal") is True
        and payload.get("scope_released") is True
        and str(payload.get("terminal_status") or "")
    ):
        scope_payload.update({
            "terminal": True,
            "terminal_status": payload.get("terminal_status"),
            "scope_released": True,
        })
        if payload.get("review_receipt_sha256"):
            scope_payload["review_receipt_sha256"] = payload.get("review_receipt_sha256")
    _write(_scope_path(config), scope_payload)
    if _closed_audit_enabled(config):
        try:
            STRICT_ULTRA.sync_identity_ledger(config, payload)
        except STRICT_ULTRA.StrictUltraError as exc:
            raise WorkflowError(str(exc)) from exc


def _finalize_complete_workflow(
    state_path: Path,
    config: dict[str, Any],
    complete: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    """Seal optional audit before making terminal success authoritative."""
    result = {**complete, "local_gate": gate}
    if not _closed_audit_enabled(config):
        _write_workflow_state(state_path, config, result)
        return result
    try:
        # Append the final identity event without publishing a completed state.
        # The later state write sees the same event and therefore cannot move the
        # ledger hash sealed into the audit. If audit creation fails, the prior
        # recoverable workflow state remains authoritative.
        STRICT_ULTRA.sync_identity_ledger(config, result)
        audit_path = STRICT_ULTRA.write_workflow_audit(config, result, gate)
    except STRICT_ULTRA.StrictUltraError as exc:
        raise WorkflowError(str(exc)) from exc
    result = {
        **result,
        "workflow_audit_path": str(audit_path),
        "workflow_audit_sha256": sha(audit_path),
    }
    _write_workflow_state(state_path, config, result)
    return result


def _required_sha256(value: str, *, label: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise WorkflowError(f"{label} must be an exact SHA-256")
    return normalized


def _load_expected_json(path: Path, expected_sha256: str, *, label: str) -> tuple[dict[str, Any], str]:
    if path.is_symlink():
        raise WorkflowError(f"{label} must not be a symlink")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        raise WorkflowError(f"{label} SHA-256 mismatch")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WorkflowError(f"{label} must be strict UTF-8") from exc
    return _json_object_no_duplicates(text, label=label), digest


def _relative_state_path(path: Path, root: Path, *, label: str) -> tuple[str, ...]:
    try:
        return path.relative_to(root).parts
    except ValueError as exc:
        raise WorkflowError(f"{label} must remain inside the Oracle state root") from exc


def _user_stop_receipt_path(state_root: Path, project_key: str, workflow_id: str, run_id: str) -> Path:
    return (
        state_root
        / "workflow-user-stop-settlements"
        / project_key
        / workflow_id
        / f"{run_id}.json"
    )


def _user_stop_completion_path(state_root: Path, project_key: str, workflow_id: str, run_id: str) -> Path:
    return (
        state_root
        / "workflow-user-stop-settlements"
        / project_key
        / workflow_id
        / f"{run_id}.completion.json"
    )


def _binding_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decode_preimage(value: Any, *, expected_sha256: str, label: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        raise WorkflowError(f"{label} preimage is missing")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise WorkflowError(f"{label} preimage is not valid base64") from exc
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise WorkflowError(f"{label} preimage SHA-256 mismatch")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WorkflowError(f"{label} preimage must be strict UTF-8") from exc
    return _json_object_no_duplicates(text, label=f"{label} preimage")


def _user_stop_evidence_mode(run_state: dict[str, Any], run_dir: Path, confirmation: str) -> str:
    if confirmation == USER_STOP_CONFIRMATION:
        if (
            run_state.get("schema") != RUNNER.STATE.STATE_SCHEMA
            or run_state.get("status") != "attention_required"
            or run_state.get("session_authority") != "terminal"
            or run_state.get("terminal_harvested") is not True
            or run_state.get("task_outcome") not in USER_STOPPABLE_OUTCOMES
            or run_state.get("transport_status") not in {"complete", "failed"}
        ):
            raise WorkflowError("Oracle run is not a bounded terminal user-stop candidate")
        return "provider-terminal-user-stop"
    if confirmation != PRE_SUBMIT_CANCEL_CONFIRMATION:
        raise WorkflowError(
            f"confirmation must be {USER_STOP_CONFIRMATION} or {PRE_SUBMIT_CANCEL_CONFIRMATION}"
        )
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    output_path = run_dir / "output.md"
    stderr_text = (
        stderr_path.read_text(encoding="utf-8", errors="strict").strip()
        if stderr_path.is_file() and not stderr_path.is_symlink()
        else ""
    )
    if (
        run_state.get("schema") != RUNNER.STATE.STATE_SCHEMA
        or run_state.get("status") != "failed"
        or run_state.get("session_authority") != "pre_submit"
        or run_state.get("terminal_harvested") is not False
        or run_state.get("transport_status") != "prepared"
        or run_state.get("task_outcome") != "pending"
        or run_state.get("exit_code") is not None
        or str(run_state.get("conversation_url") or "").strip()
        or output_path.exists()
        or not stdout_path.is_file()
        or stdout_path.is_symlink()
        or stdout_path.stat().st_size != 0
        or stderr_text not in PRE_SUBMIT_CANCEL_ERRORS
    ):
        raise WorkflowError("Oracle run is not a bounded pre-submit DevSpace compatibility failure")
    return PRE_SUBMIT_CANCEL_ERRORS[stderr_text]


def settle_user_stopped_workflow(
    *,
    workflow_state_path: Path,
    scope_state_path: Path,
    run_dir: Path,
    workflow_id: str,
    run_id: str,
    expected_workflow_sha256: str,
    expected_scope_sha256: str,
    expected_run_state_sha256: str,
    confirmation: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    if confirmation not in {USER_STOP_CONFIRMATION, PRE_SUBMIT_CANCEL_CONFIRMATION}:
        raise WorkflowError(
            f"confirmation must be {USER_STOP_CONFIRMATION} or {PRE_SUBMIT_CANCEL_CONFIRMATION}"
        )
    if not re.fullmatch(r"[0-9a-f]{32}", workflow_id):
        raise WorkflowError("workflow_id must be exact 32-character lowercase hex")
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise WorkflowError("run_id must be exact 32-character lowercase hex")
    expected_workflow_sha256 = _required_sha256(expected_workflow_sha256, label="workflow state")
    expected_scope_sha256 = _required_sha256(expected_scope_sha256, label="scope state")
    expected_run_state_sha256 = _required_sha256(expected_run_state_sha256, label="Oracle run state")

    state_root = RUNNER.STATE.oracle_state_root().resolve(strict=True)
    workflow_path = workflow_state_path.expanduser().resolve(strict=True)
    scope_path = scope_state_path.expanduser().resolve(strict=True)
    directory = run_dir.expanduser().resolve(strict=True)
    run_state_path = (directory / "state.json").resolve(strict=True)
    for candidate, label in (
        (workflow_path, "workflow state"),
        (scope_path, "scope state"),
        (run_state_path, "Oracle run state"),
    ):
        if not candidate.is_file() or candidate.is_symlink():
            raise WorkflowError(f"{label} must be a regular non-symlink file")

    workflow_parts = _relative_state_path(workflow_path, state_root, label="workflow state")
    scope_parts = _relative_state_path(scope_path, state_root, label="scope state")
    run_parts = _relative_state_path(directory, state_root, label="Oracle run directory")
    if len(workflow_parts) != 3 or workflow_parts[0] != "workflows" or workflow_path.name != f"{workflow_id}.json":
        raise WorkflowError("workflow state path identity mismatch")
    project_key = workflow_parts[1]
    if (
        len(scope_parts) != 3
        or scope_parts[0] != "comprehensive-scopes"
        or scope_parts[1] != project_key
        or scope_path.suffix != ".json"
    ):
        raise WorkflowError("scope state path identity mismatch")
    if (
        len(run_parts) != 4
        or run_parts[0] != "projects"
        or run_parts[1] != project_key
        or run_parts[2] != "runs"
        or run_parts[3] != run_id
    ):
        raise WorkflowError("Oracle run directory identity mismatch")

    run_state, _ = _load_expected_json(
        run_state_path, expected_run_state_sha256, label="Oracle run state"
    )
    project_root = Path(str(run_state.get("project_root") or "")).expanduser()
    if not project_root.is_absolute():
        raise WorkflowError("Oracle run project_root is not absolute")
    expected_project_key = hashlib.sha256(str(project_root.resolve()).casefold().encode("utf-8")).hexdigest()[:24]
    if expected_project_key != project_key:
        raise WorkflowError("Oracle run project binding mismatch")

    receipt_path = _user_stop_receipt_path(state_root, project_key, workflow_id, run_id)
    source_thread_id = RUNNER.STATE.source_thread_id_from_state(run_state)
    current_thread_id = RUNNER.STATE.current_source_thread_id()
    workflow_owner = str(_json(workflow_path).get("source_thread_id") or "").strip().casefold() or None
    scope_owner = str(_json(scope_path).get("source_thread_id") or "").strip().casefold() or None
    if (
        current_thread_id is None
        or source_thread_id is None
        or workflow_owner is None
        or scope_owner is None
        or len({current_thread_id, source_thread_id, workflow_owner, scope_owner}) != 1
    ):
        raise WorkflowError(
            "FOREIGN_TASK_SESSION: user-stop settlement requires the current task, Oracle run, "
            "workflow, and scope to have one exact source_thread_id"
        )
    with RUNNER.STATE.project_submit_mutex(
        project_root,
        timeout_seconds=30,
        source_thread_id=source_thread_id,
    ):
        run_state, _ = _load_expected_json(
            run_state_path, expected_run_state_sha256, label="Oracle run state"
        )
        if run_state.get("run_id") != run_id:
            raise WorkflowError("Oracle run ID mismatch")
        evidence_mode = _user_stop_evidence_mode(run_state, directory, confirmation)

        receipt: dict[str, Any] | None = None
        receipt_sha256 = ""
        completion_path = _user_stop_completion_path(state_root, project_key, workflow_id, run_id)
        if receipt_path.exists() or receipt_path.is_symlink():
            if receipt_path.is_symlink() or not receipt_path.is_file():
                raise WorkflowError("user-stop settlement receipt path is unsafe")
            receipt = _json_object_no_duplicates(
                receipt_path.read_text(encoding="utf-8", errors="strict"),
                label="user-stop settlement receipt",
            )
            receipt_sha256 = sha(receipt_path)

        workflow_current_bytes = workflow_path.read_bytes()
        scope_current_bytes = scope_path.read_bytes()
        workflow_current = _json_object_no_duplicates(
            workflow_current_bytes.decode("utf-8", errors="strict"), label="workflow state"
        )
        scope_current = _json_object_no_duplicates(
            scope_current_bytes.decode("utf-8", errors="strict"), label="scope state"
        )
        workflow_current_sha = hashlib.sha256(workflow_current_bytes).hexdigest()
        scope_current_sha = hashlib.sha256(scope_current_bytes).hexdigest()

        if receipt is None:
            workflow_before = workflow_current
            scope_before = scope_current
        else:
            workflow_before = _decode_preimage(
                receipt.get("workflow_state_preimage_base64"),
                expected_sha256=expected_workflow_sha256,
                label="workflow state",
            )
            scope_before = _decode_preimage(
                receipt.get("scope_state_preimage_base64"),
                expected_sha256=expected_scope_sha256,
                label="scope state",
            )

        binding = {
            "confirmation": confirmation,
            "source_thread_id": source_thread_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "project_key": project_key,
            "manifest_sha256": str(workflow_before.get("manifest_sha256") or ""),
            "workflow_state_sha256": expected_workflow_sha256,
            "scope_state_sha256": expected_scope_sha256,
            "run_state_sha256": expected_run_state_sha256,
            "current_stage": str(workflow_before.get("current_stage") or ""),
            "current_attempt_id": str(workflow_before.get("current_attempt_id") or ""),
            "current_input_sha256": str(workflow_before.get("current_input_sha256") or ""),
        }
        if evidence_mode != "provider-terminal-user-stop":
            binding["evidence_mode"] = evidence_mode
        binding_sha256 = _binding_sha256(binding)

        if receipt is not None:
            expected_receipt = {
                "schema": USER_STOP_SETTLEMENT_SCHEMA,
                "status": "CANCELED",
                "authority": confirmation,
                "authority_binding_sha256": binding_sha256,
                **binding,
            }
            if any(receipt.get(key) != value for key, value in expected_receipt.items()):
                raise WorkflowError("existing user-stop settlement receipt binding mismatch")
            if receipt.get("workflow_state_path") != str(workflow_path):
                raise WorkflowError("existing settlement workflow path mismatch")
            if receipt.get("scope_state_path") != str(scope_path):
                raise WorkflowError("existing settlement scope path mismatch")
            if receipt.get("run_state_path") != str(run_state_path):
                raise WorkflowError("existing settlement run path mismatch")
            if dry_run:
                return {
                    "ok": True,
                    "status": "dry-run",
                    "terminal_status": "CANCELED",
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "authority_binding_sha256": binding_sha256,
                    "settlement_path": str(receipt_path),
                    "submission_action": "none",
                }
        else:
            if workflow_current_sha != expected_workflow_sha256:
                raise WorkflowError("workflow state SHA-256 mismatch")
            if scope_current_sha != expected_scope_sha256:
                raise WorkflowError("scope state SHA-256 mismatch")
            if (
                workflow_current.get("schema") != STATE_SCHEMA
                or workflow_current.get("status") not in {"running", "attention_required"}
                or workflow_current.get("workflow_id") != workflow_id
                or workflow_current.get("current_attempt_id") != run_id
                or workflow_current.get("oracle_run_id") != run_id
                or Path(str(workflow_current.get("oracle_run_dir") or "")).expanduser().resolve() != directory
                or not re.fullmatch(r"[0-9a-f]{64}", str(workflow_current.get("manifest_sha256") or ""))
                or not re.fullmatch(r"[0-9a-f]{64}", str(workflow_current.get("current_input_sha256") or ""))
            ):
                raise WorkflowError("workflow state is not bound to the exact Oracle run")
            records = workflow_current.get("records")
            if not isinstance(records, list) or not any(
                isinstance(record, dict)
                and Path(str(record.get("run_dir") or "")).expanduser().resolve() == directory
                for record in records
            ):
                raise WorkflowError("workflow records do not bind the exact Oracle run")
            if (
                scope_current.get("schema") != SCOPE_SCHEMA
                or scope_current.get("status") in TERMINAL_SCOPE_STATUSES
                or scope_current.get("active_workflow_id") != workflow_id
            ):
                raise WorkflowError("scope state is not owned by the exact workflow")
            receipt = {
                "schema": USER_STOP_SETTLEMENT_SCHEMA,
                "status": "CANCELED",
                "authority": confirmation,
                "authority_binding_sha256": binding_sha256,
                **binding,
                "workflow_state_path": str(workflow_path),
                "scope_state_path": str(scope_path),
                "run_state_path": str(run_state_path),
                "workflow_state_preimage_base64": base64.b64encode(workflow_current_bytes).decode("ascii"),
                "scope_state_preimage_base64": base64.b64encode(scope_current_bytes).decode("ascii"),
                "settled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            if dry_run:
                return {
                    "ok": True,
                    "status": "dry-run",
                    "terminal_status": "CANCELED",
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "authority_binding_sha256": binding_sha256,
                    "settlement_path": str(receipt_path),
                    "submission_action": "none",
                }
            _atomic_write(receipt_path, receipt)
            receipt_sha256 = sha(receipt_path)

        if not receipt_sha256:
            receipt_sha256 = sha(receipt_path)
        reference = {
            "schema": USER_STOP_SETTLEMENT_SCHEMA,
            "status": "CANCELED",
            "run_id": run_id,
            "path": str(receipt_path),
            "sha256": receipt_sha256,
            "authority_binding_sha256": binding_sha256,
        }
        canceled_workflow = {
            **workflow_before,
            "status": "canceled",
            "terminal": True,
            "terminal_status": "CANCELED",
            "blocker": (
                "user explicitly stopped the provider response and canceled this workflow"
                if evidence_mode == "provider-terminal-user-stop"
                else "user explicitly canceled this workflow after a proven pre-submit failure"
            ),
            "user_stop_settlement": reference,
        }
        canceled_scope = {
            **scope_before,
            "status": "canceled",
            "terminal": True,
            "terminal_status": "CANCELED",
            "scope_released": True,
            "user_stop_settlement": reference,
        }
        if workflow_current_sha == expected_workflow_sha256:
            _atomic_write(workflow_path, canceled_workflow)
        elif workflow_current != canceled_workflow:
            raise WorkflowError("workflow state changed outside the user-stop settlement")

        if scope_current_sha == expected_scope_sha256:
            _atomic_write(scope_path, canceled_scope)
        elif scope_current != canceled_scope:
            raise WorkflowError("scope state changed outside the user-stop settlement")

        final_workflow = _json(workflow_path)
        final_scope = _json(scope_path)
        if final_workflow.get("status") != "canceled" or final_scope.get("status") != "canceled":
            raise WorkflowError("user-stop settlement did not reach a terminal canceled state")
        workflow_final_sha = sha(workflow_path)
        scope_final_sha = sha(scope_path)
        if completion_path.exists() or completion_path.is_symlink():
            if completion_path.is_symlink() or not completion_path.is_file():
                raise WorkflowError("user-stop completion receipt path is unsafe")
            completion = _json_object_no_duplicates(
                completion_path.read_text(encoding="utf-8", errors="strict"),
                label="user-stop completion receipt",
            )
            if (
                completion.get("schema") != USER_STOP_COMPLETION_SCHEMA
                or completion.get("settlement_sha256") != receipt_sha256
                or completion.get("workflow_state_sha256") != workflow_final_sha
                or completion.get("scope_state_sha256") != scope_final_sha
            ):
                raise WorkflowError("user-stop completion receipt binding mismatch")
        else:
            completion = {
                "schema": USER_STOP_COMPLETION_SCHEMA,
                "status": "CANCELED",
                "workflow_id": workflow_id,
                "run_id": run_id,
                "settlement_path": str(receipt_path),
                "settlement_sha256": receipt_sha256,
                "workflow_state_path": str(workflow_path),
                "workflow_state_sha256": workflow_final_sha,
                "scope_state_path": str(scope_path),
                "scope_state_sha256": scope_final_sha,
                "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            _atomic_write(completion_path, completion)
        completion_sha256 = sha(completion_path)
        return {
            "ok": True,
            "status": "canceled",
            "terminal_status": "CANCELED",
            "workflow_id": workflow_id,
            "run_id": run_id,
            "authority_binding_sha256": binding_sha256,
            "settlement_path": str(receipt_path),
            "settlement_sha256": receipt_sha256,
            "completion_path": str(completion_path),
            "completion_sha256": completion_sha256,
            "workflow_state_sha256": workflow_final_sha,
            "scope_state_sha256": scope_final_sha,
            "scope_released": True,
            "submission_action": "none",
        }


def _stage_mission(
    config: dict[str, Any],
    workflow_id: str,
    index: int,
    stage: str,
    source: Path,
    attempt_id: str,
) -> tuple[Path, Path, str]:
    stage_dir = config["workflow_dir"] / "stages" / f"{index:02d}-{stage}-{attempt_id[:12]}"
    receipt = stage_dir / "stage-result.json"
    target = stage_dir / "mission.md"
    stage_dir.mkdir(parents=True, exist_ok=True)
    body = source.read_text(encoding="utf-8")
    input_sha = sha(source)
    protocol = (
        "\n\n[HOST_STAGE_CONTRACT]\n"
        f"workflow_id={workflow_id}\nstage={stage}\nstage_index={index}\n"
        f"attempt_id={attempt_id}\ninput_mission_sha256={input_sha}\n"
        f"exact_project_root={config['project_root']}\n"
        f"exact_input_mission_path={source}\n"
        f"Write the small UTF-8 stage receipt to: {receipt}\n"
        "Receipt JSON must use the key schema=codex.chatgpt.oracle-stage-result/v1. Include workflow_id, "
        "stage, attempt_id, input_mission_sha256, status, output_path, output_sha256, next_stage, next_mission_path, "
        "next_mission_sha256, ready_for_next, blocker. output_path and next_mission_path MUST be absolute paths "
        "inside exact_project_root; project-relative paths are invalid. Write the next mission itself; "
        "the host will validate bytes and hashes but will not rewrite its meaning. "
        "The supplied input_mission_sha256 binds the upstream source mission bytes before this HOST_STAGE_CONTRACT "
        "was appended; copy it exactly into the receipt and do not replace it with a hash of this augmented mission.md.\n"
        "\n[HOST_STAGE_MATERIALIZATION_FALLBACK]\n"
        "If and only if the registered DevSpace surface does not expose write/edit/bash, do not stop merely because "
        "you cannot create output_path, next_mission_path, or stage-result.json. Return those three artifacts through "
        "the host bridge instead. Emit exactly one [ORACLE_STAGE_OUTPUT] line, one strict JSON object, one "
        "[/ORACLE_STAGE_OUTPUT] line, then TASK_OUTCOME: EXECUTED as the final nonempty line. The JSON exact keys are "
        "schema, workflow_id, stage, attempt_id, input_mission_sha256, status, output_text, next_stage, "
        "next_mission_text, ready_for_next, blocker, critical_finding_ids, critical_findings_sha256. schema must be "
        f"{REGULAR_OUTPUT_SCHEMA}; copy the bound identity fields exactly. output_text is the complete output file; "
        "next_mission_text is the complete next mission, or an empty string only for a terminal transition. "
        "critical_finding_ids is a sorted unique string array and critical_findings_sha256 is its compact-JSON "
        "SHA-256 for review, otherwise use [] and an empty string. Do not both write the receipt/files and return "
        "this fallback envelope. The host preserves UTF-8 bytes, creates only its workflow-owned stage artifacts, "
        "and runs the same receipt and transition validation.\n"
        "\n[DEVSPACE_WORKSPACE_ENTRY_CONTRACT]\n"
        "Use only exact_project_root as the project workspace. Reuse an already registered DevSpace workspace whose "
        "normalized root is exactly equal to it, or open that exact root. Windows separators and Unicode characters "
        "must be preserved. Read the exact input mission completely and then the applicable AGENTS.md chain before "
        "project exploration or edits. If the exact workspace open times out, inspect the registered workspace list "
        "and retry the same exact root at most once. Never substitute a parent root, child directory, similarly named "
        "workspace, active workspace, or shell-based boundary workaround. Never register, repair, or delete the app "
        "during a stage. If the exact root remains unavailable, stop this stage with a concise external workspace "
        "blocker instead of changing scope. Keep progress narration compact; tool activity and the durable receipt "
        "are the authority.\n"
        "\n[ORACLE_SELF_OBSERVATION_GUARD]\n"
        f"exact_oracle_run_id={attempt_id}\n"
        f"exact_oracle_slug={RUNNER.STATE.oracle_slug(config['project_root'], attempt_id)}\n"
        "Do not inspect, read, wait for, poll, invoke, recover, or report on this exact Oracle run or slug, "
        "including its state.json, output.md, transcript.md, recovery, observer, or process status. Do not launch "
        "a nested Oracle run. Perform this stage directly; if a required project resource is unavailable, return "
        "one concrete blocker without observing the host controller.\n"
    )
    if stage == "plan":
        pro_selection_instruction = (
            "A next_stage=pro transition is permitted when it is genuinely useful.\n"
            if config["allow_pro"]
            else (
                "Do not emit next_stage=pro; continue with review.\n"
                if _is_ultra_gpt(config)
                else "Do not emit next_stage=pro; continue with review or an authorized web-multi stage.\n"
            )
        )
        protocol += (
            "\n[PRO_SELECTION_POLICY]\n"
            f"pro_selection_allowed={'true' if config['allow_pro'] else 'false'}\n"
            "Pro is quota-limited, read-only, and may be selected only when this manifest explicitly authorizes it. "
            "Use Pro only for architecture, research, advice, or review; route every write or command task to the "
            "separate regular GPT-5.6 extra-high implementation stage. "
            f"{pro_selection_instruction}"
            "\n[PRO_ATTACHMENT_AUTHORING_CONTRACT]\n"
            "Use the default read-only Pro DevSpace route unless frozen external evidence is unavailable or inappropriate "
            "through the live exact root. If and only if next_stage=pro requires such evidence files, the authored "
            "next mission must contain exactly "
            "one closed [PRO_ATTACHMENT_CONTRACT] block. Its body must be one JSON object with "
            f"schema={PRO_ATTACHMENT_SCHEMA} and an attachments array. Each attachment entry contains an absolute "
            "path and may contain its lowercase SHA-256. Paths must name regular non-symlink files inside "
            "exact_project_root. Do not describe a required packet only in prose, and do not add this block for "
            "review, web-multi, implementation, or final-web-gate transitions. Example: "
            f"{PRO_ATTACHMENT_BEGIN}{{\"schema\":\"{PRO_ATTACHMENT_SCHEMA}\",\"attachments\":[{{\"path\":"
            "\"C:\\\\exact-root\\\\packet.zip\",\"sha256\":\"<64 lowercase hex>\"}]}}"
            f"{PRO_ATTACHMENT_END}\n"
            "Canonical plan receipt status is PLAN_READY. The legacy status completed is accepted only when the "
            "receipt is otherwise a complete, hash-valid, blocker-free ready transition to review, web-multi, or pro.\n"
        )
        if _is_ultra_gpt(config):
            protocol += (
                "\n[ULTRA_GPT_WEB_AGENT_CONTRACT]\n"
                "This workflow replaces every cognitive native Codex subagent with a separate web GPT session. "
                "The local commander is a deterministic controller only and must not receive semantic residual work. "
                "Author the separate adversarial review mission and return next_stage=review. The reviewer will repair "
                "the plan and partition implementation into disjoint path ownership for parallel web writers. Do not "
                "select Pro inside this workflow.\n"
            )
    if stage == "review":
        config.setdefault("_review_policy", _review_policy_from_history(config))
        policy = config["_review_policy"]
        review_handoff = (
            "Then author the complete bound Oracle Multi implementation manifest and return PASS or "
            "PASS_WITH_NOTES with next_stage=web-multi. Notes must travel inside the lane and merger missions. "
            if _is_ultra_gpt(config)
            else "Then write a complete implementation mission and return PASS or PASS_WITH_NOTES with "
            "next_stage=implementation. Notes are non-blocking and must travel inside that implementation mission. "
        )
        protocol += (
            "\n[REVIEW_ADJUDICATION_CONTRACT]\n"
            "For new work, use PASS, PASS_WITH_NOTES, or FAIL. REVISE is legacy compatibility only and must not be "
            "emitted. You are the plan repair and finalization owner, not only a critic. Inspect the proposed plan, "
            "directly repair every defect that can be resolved from the mission, DevSpace workspace, project rules, "
            "or available evidence, and write the corrected final plan as your output. "
            f"{review_handoff}"
            "Do not request a new planning stage "
            "for wording, structure, omitted checks, weak sequencing, locally discoverable facts, or any other defect "
            "you can repair yourself. FAIL is allowed only when unavailable external input or authority, an unresolved "
            "safety boundary, or genuine execution impossibility prevents a safe corrected plan; include the concrete "
            "external blocker. "
            "Include sorted unique critical_finding_ids and critical_findings_sha256, where the hash is SHA-256 of the "
            "compact UTF-8 JSON array. PASS and PASS_WITH_NOTES must have no remaining critical finding IDs. FAIL must "
            "include a nonempty blocker and stops the workflow. A legacy REVISE receipt is terminal attention only and "
            "can never create another plan.\n"
            "review_repair_owner=review\n"
            "new_plan_transition_allowed=false\n"
            f"plan_revisions_used={policy['plan_revisions_used']}\n"
            f"plan_revisions_max={policy['max_plan_revisions']}\n"
            f"plan_revisions_remaining={policy['plan_revisions_remaining']}\n"
            f"baseline_critical_finding_ids={json.dumps(policy['baseline_critical_finding_ids'], ensure_ascii=False, separators=(',', ':'))}\n"
            f"baseline_critical_findings_sha256={policy['baseline_critical_findings_sha256'] or ''}\n"
        )
        if _is_ultra_gpt(config):
            protocol += (
                "\n[ULTRA_GPT_PARALLEL_IMPLEMENTATION_CONTRACT]\n"
                "After repairing and finalizing the plan, author a bound Oracle Multi manifest and return "
                "next_stage=web-multi. Define two to five parallel implementation lanes with max_concurrency no "
                "greater than three. Use schema codex.chatgpt.oracle-multi/v2. Every lane must use "
                "access=worktree-write at a distinct pre-created, exact-root-qualified Git worktree and list "
                "nonempty project-relative owned_paths. Ownership must be pairwise disjoint, including "
                "ancestor/descendant overlap. Set all_lanes_required=true and partial_merge_allowed=false. "
                "Place every worktree below <output_dir>/worktrees and create it detached at the canonical current "
                "HEAD before writing the manifest. The runner qualifies the canonical project root and accepts only "
                "this hash-bound derived-worktree relation; it does not register dynamic roots or change DevSpace. "
                "Lanes may write only their owned paths and must not mutate Git state. The host audits and applies every "
                "successful lane only after an all-lanes barrier. The merger inspects the combined result, resolves "
                "only integration defects within "
                "its mission authority, writes the bound final verification mission, and returns next_stage="
                "final-web-gate.\n"
            )
    if stage == "final-web-gate":
        protocol += (
            "\n[FINAL_GATE_RECEIPT_CONTRACT]\n"
            "A passing final gate must use status=PASS, next_stage=complete, ready_for_next=true, "
            "and blocker=\"\". Complete is a transition to the mandatory host local gate, not a claim "
            "that the workflow has already completed. For this terminal transition only, set "
            "next_mission_path=null and next_mission_sha256=null; do not create an empty terminal mission. "
            "For the materialization fallback use next_mission_text=\"\". Never use an empty next_stage "
            "or ready_for_next=false for PASS. If verification cannot pass, report the concrete mismatch "
            "without asserting PASS; never convert missing evidence into completion.\n"
        )
    target.write_text(body.rstrip() + protocol, encoding="utf-8")
    return target, receipt, input_sha


def _pro_stage_mission(
    config: dict[str, Any],
    workflow_id: str,
    index: int,
    source: Path,
    attempt_id: str,
) -> tuple[Path, Path, str]:
    stage_dir = config["workflow_dir"] / "stages" / f"{index:02d}-pro-{attempt_id[:12]}"
    receipt = stage_dir / "stage-result.json"
    target = stage_dir / "mission.md"
    stage_dir.mkdir(parents=True, exist_ok=True)
    input_sha = sha(source)
    body = source.read_text(encoding="utf-8")
    protocol = (
        "\n\n[HOST_STAGE_CONTRACT]\n"
        f"workflow_id={workflow_id}\nstage=pro\nstage_index={index}\n"
        f"attempt_id={attempt_id}\ninput_mission_sha256={input_sha}\n"
        "Return exactly one JSON object and no surrounding prose or Markdown fences. "
        f"The schema must be {PRO_OUTPUT_SCHEMA}. Include workflow_id, stage, attempt_id, "
        "input_mission_sha256, status, output_text, next_stage, next_mission_text, ready_for_next, blocker. "
        "A passing result requires status=PASS, next_stage=review, ready_for_next=true, an empty blocker, "
        "and nonempty output_text and next_mission_text. The host will preserve those two strings exactly, "
        "materialize them as UTF-8 files, and validate their hashes without rewriting their meaning. "
        "The response must be strict JSON: escape every double quote and backslash inside output_text and "
        "next_mission_text. Never paste a nested JSON document into either string with raw, unescaped quotes; "
        "encode it as string content with JSON escaping.\n"
    )
    if config.get("workflow_profile") == ULTRA_ECONOMY_PROFILE:
        protocol += (
            "\n[ULTRA_ECONOMY_DESIGN_CONTRACT]\n"
            "You are the mandatory architecture and design owner. Remain read-only and produce the complete, "
            "implementation-ready design. The next mission must target a separate review session, which must "
            "repair the design and author the implementation mission. Implementation and final web verification "
            "must remain separate later sessions. Do not ask the local Luna commander to perform project analysis, "
            "implementation, or semantic review.\n"
        )
    protocol += (
        "\n[PRO_READ_ONLY_AUTHORITY]\n"
        "This Pro stage is advisory and read-only. Do not create, edit, delete, or rename project files; do not "
        "run commands or change settings, accounts, or external state. Return analysis and an implementation-ready "
        "next mission only. A separate regular GPT-5.6 extra-high DevSpace implementation stage owns all writes "
        "and commands.\n"
    )
    target.write_text(body.rstrip() + protocol, encoding="utf-8")
    return target, receipt, input_sha


def _oracle_manifest(
    config: dict[str, Any],
    mission: Path,
    stage_dir: Path,
    run_id: str,
    *,
    stage: str,
    pro_attachments: Iterable[Path] = (),
) -> Path:
    pro_attachments = tuple(pro_attachments)
    path = stage_dir / "oracle.json"
    payload: dict[str, Any] = {
        "schema": RUNNER.STATE.SCHEMA,
        "project_root": str(config["project_root"]),
        "mission_path": str(mission),
        "mode": "browser",
        "model": "gpt-5.6-sol" if stage == "pro" else config["model"],
        "model_strategy": "select",
        # Pro is the explicit highest effort in the current GPT-5.6 Sol UI;
        # regular comprehensive stages use the separately verified Extra High.
        "thinking_time": "pro" if stage == "pro" else "extra-high",
        "research": "off",
        "archive": "auto",
        "parallel_parent_id": config["_parallel_parent_id"],
        "run_id": run_id,
        "source_thread_id": config.get("source_thread_id"),
    }
    if stage == "pro":
        if pro_attachments:
            payload["transport"] = "pro-attachment-only"
            payload["attachments"] = [str(mission), *(str(item) for item in pro_attachments)]
        else:
            payload["transport"] = "pro-devspace-readonly"
            payload["app_name"] = config["app_name"]
            payload["task_outcome_contract"] = "v1"
    else:
        payload["transport"] = "devspace"
        payload["app_name"] = config["app_name"]
        payload["task_outcome_contract"] = "v1"
    _write(path, payload)
    return path


def _declared_pro_attachments(config: dict[str, Any], source: Path) -> tuple[Path, ...]:
    """Return only packets declared in one closed machine-readable mission block."""
    text = source.read_text(encoding="utf-8")
    begin_count = text.count(PRO_ATTACHMENT_BEGIN)
    end_count = text.count(PRO_ATTACHMENT_END)
    if begin_count == end_count == 0:
        return ()
    if begin_count != 1 or end_count != 1:
        raise WorkflowError("Pro attachment contract must contain exactly one closed block")
    start = text.index(PRO_ATTACHMENT_BEGIN) + len(PRO_ATTACHMENT_BEGIN)
    end = text.index(PRO_ATTACHMENT_END, start)
    try:
        payload = json.loads(text[start:end].strip())
    except json.JSONDecodeError as exc:
        raise WorkflowError("Pro attachment contract must contain one JSON object") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "attachments"}:
        raise WorkflowError("Pro attachment contract has an invalid closed key set")
    if payload.get("schema") != PRO_ATTACHMENT_SCHEMA or not isinstance(payload.get("attachments"), list):
        raise WorkflowError("Pro attachment contract schema or attachments is invalid")
    paths: list[Path] = []
    seen: set[Path] = set()
    for item in payload["attachments"]:
        if not isinstance(item, dict) or set(item) - {"path", "sha256"} or not isinstance(item.get("path"), str):
            raise WorkflowError("Pro attachment entries must contain path and optional sha256 only")
        raw = Path(item["path"]).expanduser()
        if not raw.is_absolute() or raw.is_symlink() or not raw.is_file():
            raise WorkflowError("Pro attachment must be an absolute regular non-symlink file")
        path = _inside(config["project_root"], raw)
        if path in seen:
            raise WorkflowError("Pro attachment contract contains a duplicate path")
        declared_hash = item.get("sha256")
        if declared_hash is not None:
            if not isinstance(declared_hash, str) or len(declared_hash) != 64 or any(char not in "0123456789abcdef" for char in declared_hash.lower()):
                raise WorkflowError("Pro attachment sha256 must be a 64-character hexadecimal digest")
            if sha(path).lower() != declared_hash.lower():
                raise WorkflowError("Pro attachment hash mismatch")
        seen.add(path)
        paths.append(path)
    return tuple(paths)


def _mission_contains_pro_attachment_contract(source: Path) -> bool:
    text = source.read_text(encoding="utf-8")
    return PRO_ATTACHMENT_BEGIN in text or PRO_ATTACHMENT_END in text


def _oracle_output_path(result: dict[str, Any], run_dir: Any = None) -> Path | None:
    direct = str(result.get("output_path") or "").strip()
    if direct:
        return Path(direct).expanduser()
    nested = result.get("result")
    if isinstance(nested, dict):
        artifacts = nested.get("artifacts")
        if isinstance(artifacts, dict) and str(artifacts.get("output") or "").strip():
            return Path(str(artifacts["output"])).expanduser()
    if run_dir:
        state_path = Path(str(run_dir)).expanduser() / "state.json"
        if state_path.is_file():
            state = RUNNER.STATE.load_state(state_path)
            artifacts = state.get("artifacts")
            if isinstance(artifacts, dict) and str(artifacts.get("output") or "").strip():
                return Path(str(artifacts["output"])).expanduser()
    return None


def _skip_json_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _expect_json_token(text: str, index: int, token: str) -> int:
    index = _skip_json_whitespace(text, index)
    if not text.startswith(token, index):
        raise WorkflowError("Pro Oracle output recovery structure is ambiguous")
    return index + len(token)


def _decode_recoverable_json_string(raw: str) -> str:
    """Decode one JSON string whose only defect may be unescaped double quotes."""
    repaired: list[str] = []
    index = 0
    while index < len(raw):
        character = raw[index]
        if character == '"':
            repaired.append('\\"')
            index += 1
            continue
        if character != "\\":
            repaired.append(character)
            index += 1
            continue
        if index + 1 >= len(raw):
            raise WorkflowError("Pro Oracle output recovery found a truncated escape")
        escaped = raw[index + 1]
        if escaped in '"\\/bfnrt':
            repaired.extend(("\\", escaped))
            index += 2
            continue
        if escaped == "u" and index + 5 < len(raw) and all(
            item in "0123456789abcdefABCDEF" for item in raw[index + 2:index + 6]
        ):
            repaired.append(raw[index:index + 6])
            index += 6
            continue
        raise WorkflowError("Pro Oracle output recovery found an ambiguous backslash escape")
    try:
        value = json.loads('"' + "".join(repaired) + '"')
    except json.JSONDecodeError as exc:
        raise WorkflowError("Pro Oracle output recovery could not decode string content") from exc
    if not isinstance(value, str):
        raise WorkflowError("Pro Oracle output recovery did not produce string content")
    return value


def _parse_recovered_pro_tail(text: str, index: int) -> tuple[dict[str, Any], int]:
    decoder = json.JSONDecoder()
    values: dict[str, Any] = {}
    for position, key in enumerate(("next_stage", "next_mission_text", "ready_for_next", "blocker")):
        parsed_key, index = decoder.raw_decode(text, _skip_json_whitespace(text, index))
        if parsed_key != key:
            raise WorkflowError("Pro Oracle output recovery key order is ambiguous")
        index = _expect_json_token(text, index, ":")
        if key == "next_mission_text":
            index = _skip_json_whitespace(text, index)
            if index >= len(text) or text[index] != '"':
                raise WorkflowError("Pro Oracle output recovery next mission is not a string")
            start = index + 1
            matches = list(re.finditer(r'"\s*,\s*"ready_for_next"\s*:', text[start:]))
            candidates: list[tuple[str, int]] = []
            for match in matches:
                boundary = start + match.start()
                try:
                    recovered = _decode_recoverable_json_string(text[start:boundary])
                    probe = start + match.end()
                    ready, probe = decoder.raw_decode(text, _skip_json_whitespace(text, probe))
                    probe = _expect_json_token(text, probe, ",")
                    blocker_key, probe = decoder.raw_decode(text, _skip_json_whitespace(text, probe))
                    if blocker_key != "blocker":
                        continue
                    probe = _expect_json_token(text, probe, ":")
                    blocker, probe = decoder.raw_decode(text, _skip_json_whitespace(text, probe))
                    probe = _expect_json_token(text, probe, "}")
                    if _skip_json_whitespace(text, probe) != len(text):
                        continue
                    if not isinstance(ready, bool) or not isinstance(blocker, str):
                        continue
                    candidates.append((recovered, boundary))
                except (json.JSONDecodeError, WorkflowError):
                    continue
            if len(candidates) != 1:
                raise WorkflowError("Pro Oracle output recovery next mission boundary is ambiguous")
            recovered, boundary = candidates[0]
            values["next_mission_text"] = recovered
            index = start + next(
                match.end() for match in matches if start + match.start() == boundary
            )
            values["ready_for_next"], index = decoder.raw_decode(text, _skip_json_whitespace(text, index))
            index = _expect_json_token(text, index, ",")
            blocker_key, index = decoder.raw_decode(text, _skip_json_whitespace(text, index))
            if blocker_key != "blocker":
                raise WorkflowError("Pro Oracle output recovery blocker key is missing")
            index = _expect_json_token(text, index, ":")
            values["blocker"], index = decoder.raw_decode(text, _skip_json_whitespace(text, index))
            index = _expect_json_token(text, index, "}")
            return values, index
        values[key], index = decoder.raw_decode(text, _skip_json_whitespace(text, index))
        if position < 3:
            index = _expect_json_token(text, index, ",")
    raise WorkflowError("Pro Oracle output recovery tail is incomplete")


def _recover_pro_envelope(text: str) -> dict[str, Any]:
    """Recover only the canonical envelope with unescaped quotes in its two text fields."""
    decoder = json.JSONDecoder()
    index = _expect_json_token(text, 0, "{")
    envelope: dict[str, Any] = {}
    for key in PRO_OUTPUT_PREFIX_KEYS:
        parsed_key, index = decoder.raw_decode(text, _skip_json_whitespace(text, index))
        if parsed_key != key:
            raise WorkflowError("Pro Oracle output recovery prefix identity is ambiguous")
        index = _expect_json_token(text, index, ":")
        envelope[key], index = decoder.raw_decode(text, _skip_json_whitespace(text, index))
        index = _expect_json_token(text, index, ",")
    parsed_key, index = decoder.raw_decode(text, _skip_json_whitespace(text, index))
    if parsed_key != "output_text":
        raise WorkflowError("Pro Oracle output recovery output_text key is missing")
    index = _expect_json_token(text, index, ":")
    index = _skip_json_whitespace(text, index)
    if index >= len(text) or text[index] != '"':
        raise WorkflowError("Pro Oracle output recovery output_text is not a string")
    start = index + 1
    matches = list(re.finditer(r'"\s*,\s*"next_stage"\s*:', text[start:]))
    candidates: list[dict[str, Any]] = []
    for match in matches:
        boundary = start + match.start()
        try:
            output_text = _decode_recoverable_json_string(text[start:boundary])
            tail, end = _parse_recovered_pro_tail(text, start + match.end() - len('"next_stage":'))
            if _skip_json_whitespace(text, end) != len(text):
                continue
            candidates.append({**envelope, "output_text": output_text, **tail})
        except (json.JSONDecodeError, WorkflowError):
            continue
    if len(candidates) != 1 or set(candidates[0]) != PRO_OUTPUT_KEYS:
        raise WorkflowError("Pro Oracle output recovery is ambiguous or incomplete")
    return candidates[0]


def _load_pro_envelope(output_path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise WorkflowError(f"Pro Oracle output contains duplicate key: {key}")
            value[key] = item
        return value

    try:
        text = output_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowError("Pro Oracle output must be UTF-8") from exc
    try:
        envelope = json.loads(text, object_pairs_hook=reject_duplicate_keys)
        return envelope, None
    except json.JSONDecodeError as exc:
        envelope = _recover_pro_envelope(text)
        return envelope, {
            "schema": PRO_OUTPUT_RECOVERY_SCHEMA,
            "method": "canonical-envelope-unescaped-quotes/v1",
            "source_output_sha256": sha(output_path),
            "strict_error_position": int(exc.pos),
        }


def _materialize_pro_receipt(
    config: dict[str, Any],
    receipt_path: Path,
    workflow_id: str,
    attempt_id: str,
    input_sha: str,
    oracle_result: dict[str, Any],
    *,
    run_dir: Any = None,
) -> None:
    output_path = _oracle_output_path(oracle_result, run_dir)
    if output_path is None or not output_path.resolve(strict=True).is_file():
        raise WorkflowError("Pro Oracle output is unavailable")

    envelope, recovery = _load_pro_envelope(output_path)
    if not isinstance(envelope, dict) or set(envelope) != PRO_OUTPUT_KEYS:
        raise WorkflowError("Pro Oracle output must contain the exact closed key set")
    if (
        envelope.get("schema") != PRO_OUTPUT_SCHEMA
        or envelope.get("workflow_id") != workflow_id
        or envelope.get("stage") != "pro"
        or envelope.get("attempt_id") != attempt_id
        or envelope.get("input_mission_sha256") != input_sha
    ):
        raise WorkflowError("Pro Oracle output identity mismatch")
    output_text = envelope.get("output_text")
    next_mission_text = envelope.get("next_mission_text")
    if (
        envelope.get("status") != "PASS"
        or envelope.get("next_stage") != "review"
        or envelope.get("ready_for_next") is not True
        or envelope.get("blocker")
        or not isinstance(output_text, str)
        or not output_text.strip()
        or not isinstance(next_mission_text, str)
        or not next_mission_text.strip()
    ):
        raise WorkflowError("Pro Oracle output did not pass")
    stage_dir = receipt_path.parent
    materialized_output = stage_dir / "output.md"
    next_mission = stage_dir / "next-mission.md"
    materialized_output.write_bytes(output_text.encode("utf-8"))
    next_mission.write_bytes(next_mission_text.encode("utf-8"))
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "workflow_id": workflow_id,
        "stage": "pro",
        "attempt_id": attempt_id,
        "input_mission_sha256": input_sha,
        "status": "PASS",
        "output_path": str(materialized_output),
        "output_sha256": sha(materialized_output),
        "next_stage": "review",
        "next_mission_path": str(next_mission),
        "next_mission_sha256": sha(next_mission),
        "ready_for_next": True,
        "blocker": "",
    }
    if recovery is not None:
        receipt["pro_output_recovery"] = recovery
    _write(receipt_path, receipt)


def _materialize_bound_text(path: Path, text: str) -> None:
    data = text.encode("utf-8")
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != data:
            raise WorkflowError(f"host materialization target already exists with different bytes: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def _load_regular_envelope(output_path: Path) -> dict[str, Any]:
    try:
        text = output_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowError("regular Oracle output must be UTF-8") from exc
    if text.count(REGULAR_OUTPUT_BEGIN) != 1 or text.count(REGULAR_OUTPUT_END) != 1:
        raise WorkflowError("regular Oracle output has no unambiguous stage envelope")
    start = text.index(REGULAR_OUTPUT_BEGIN) + len(REGULAR_OUTPUT_BEGIN)
    end = text.index(REGULAR_OUTPUT_END, start)
    envelope = _json_object_no_duplicates(text[start:end].strip(), label="regular stage envelope")
    if set(envelope) != REGULAR_OUTPUT_KEYS:
        raise WorkflowError("regular stage envelope must contain the exact closed key set")
    return envelope


def _materialize_regular_receipt(
    config: dict[str, Any],
    receipt_path: Path,
    workflow_id: str,
    stage: str,
    attempt_id: str,
    input_sha: str,
    oracle_result: dict[str, Any],
    *,
    run_dir: Any = None,
) -> None:
    output_path = _oracle_output_path(oracle_result, run_dir)
    if output_path is None or not output_path.resolve(strict=True).is_file():
        raise WorkflowError("regular Oracle output is unavailable")
    envelope = _load_regular_envelope(output_path)
    if (
        envelope.get("schema") != REGULAR_OUTPUT_SCHEMA
        or envelope.get("workflow_id") != workflow_id
        or envelope.get("stage") != stage
        or envelope.get("attempt_id") != attempt_id
        or envelope.get("input_mission_sha256") != input_sha
    ):
        raise WorkflowError("regular stage envelope identity mismatch")
    output_text = envelope.get("output_text")
    next_mission_text = envelope.get("next_mission_text")
    finding_ids = envelope.get("critical_finding_ids")
    finding_hash = envelope.get("critical_findings_sha256")
    if (
        not isinstance(output_text, str)
        or not output_text.strip()
        or not isinstance(next_mission_text, str)
        or not isinstance(finding_ids, list)
        or any(not isinstance(item, str) for item in finding_ids)
        or not isinstance(finding_hash, str)
    ):
        raise WorkflowError("regular stage envelope artifact fields are invalid")
    if finding_ids != sorted(set(finding_ids)):
        raise WorkflowError("regular stage envelope critical findings are not sorted unique")
    if stage == "review":
        if finding_hash != _finding_hash(finding_ids):
            raise WorkflowError("regular stage envelope critical findings hash mismatch")
    elif finding_ids or finding_hash:
        raise WorkflowError("non-review stage envelope cannot carry critical findings")
    stage_dir = receipt_path.parent
    materialized_output = stage_dir / "output.md"
    _materialize_bound_text(materialized_output, output_text)
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "workflow_id": workflow_id,
        "stage": stage,
        "attempt_id": attempt_id,
        "input_mission_sha256": input_sha,
        "status": envelope.get("status"),
        "output_path": str(materialized_output),
        "output_sha256": sha(materialized_output),
        "next_stage": envelope.get("next_stage"),
        "ready_for_next": envelope.get("ready_for_next"),
        "blocker": envelope.get("blocker"),
    }
    if stage == "review":
        receipt["critical_finding_ids"] = finding_ids
        receipt["critical_findings_sha256"] = finding_hash
    if next_mission_text:
        next_mission = stage_dir / "next-mission.md"
        _materialize_bound_text(next_mission, next_mission_text)
        receipt["next_mission_path"] = str(next_mission)
        receipt["next_mission_sha256"] = sha(next_mission)
    _write(receipt_path, receipt)


def _validate_receipt(
    config: dict[str, Any],
    receipt_path: Path,
    workflow_id: str,
    stage: str,
    attempt_id: str,
    input_sha: str,
) -> dict[str, Any]:
    value = (
        STRICT_ULTRA.load_json(receipt_path)
        if _closed_audit_enabled(config)
        else _json(receipt_path)
    )
    if _closed_audit_enabled(config):
        allowed_receipt_keys = {
            "schema", "workflow_id", "stage", "attempt_id", "input_mission_sha256",
            "status", "output_path", "output_sha256", "next_stage", "next_mission_path",
            "next_mission_sha256", "ready_for_next", "blocker", "critical_finding_ids",
            "critical_findings_sha256",
        }
        extra = sorted(set(value) - allowed_receipt_keys)
        if extra:
            raise WorkflowError(f"STRICT_ULTRA_RECEIPT_EXTRA_KEYS: {extra}")
    has_schema = "schema" in value
    has_legacy_schema = "schema_version" in value
    schema = value.get("schema")
    legacy_schema = value.get("schema_version")
    if not has_schema and legacy_schema == RECEIPT_SCHEMA:
        schema = legacy_schema
    elif has_schema and has_legacy_schema and schema != legacy_schema:
        raise WorkflowError("stage receipt schema keys conflict")
    if (
        schema != RECEIPT_SCHEMA
        or value.get("workflow_id") != workflow_id
        or value.get("stage") != stage
        or value.get("attempt_id") != attempt_id
        or value.get("input_mission_sha256") != input_sha
    ):
        raise WorkflowError("stage receipt identity mismatch")
    raw_status = str(value.get("status") or "")
    next_stage = str(value.get("next_stage") or "")
    if _is_ultra_gpt(config):
        required_next = {
            "plan": "review", "review": "web-multi", "web-multi": "final-web-gate",
            "final-web-gate": "complete",
        }.get(stage)
        terminal_review_fail = (
            stage == "review"
            and raw_status == "FAIL"
            and not next_stage
            and value.get("ready_for_next") is False
            and bool(value.get("blocker"))
        )
        if required_next and next_stage != required_next and not terminal_review_fail:
            raise WorkflowError(
                f"ULTRA_GPT_STAGE_ORDER_REQUIRED: {stage} must proceed to {required_next}"
            )
    if stage == "plan" and next_stage == "pro" and not config["allow_pro"]:
        raise WorkflowError("PRO_EXPLICIT_OPT_IN_REQUIRED: set allow_pro=true only after an explicit Pro request")
    completed_plan_compat = (
        stage == "plan"
        and raw_status.casefold() == "completed"
        and next_stage in {"review", "web-multi", "pro"}
        and value.get("ready_for_next") is True
        and not value.get("blocker")
    )
    status = "PLAN_READY" if completed_plan_compat else raw_status
    if stage == "review":
        if status not in REVIEW_STATUSES:
            raise WorkflowError("review status must be PASS, PASS_WITH_NOTES, REVISE, or FAIL")
        required_review_next = (
            "web-multi" if _is_ultra_gpt(config) else "implementation"
        )
        if status in {"PASS", "PASS_WITH_NOTES"} and next_stage != required_review_next:
            raise WorkflowError(f"passing review must proceed to {required_review_next}")
        if status == "REVISE" and next_stage != "plan":
            raise WorkflowError("REVISE review must return to plan")
        if status == "FAIL":
            if value.get("ready_for_next") is not False or not value.get("blocker"):
                raise WorkflowError("FAIL review must stop with a nonempty blocker")
        else:
            if value.get("ready_for_next") is not True or value.get("blocker"):
                raise WorkflowError("non-FAIL review receipt has invalid readiness")
    revision_transition = (
        status == "REVISE"
        and ((stage == "review" and next_stage == "plan")
             or (stage == "final-web-gate" and next_stage == "implementation"))
    )
    blocked_plan_continuation = (
        status == "BLOCKED_PLAN"
        and stage == "plan"
        and next_stage == "plan"
        and bool(value.get("blocker"))
    )
    ready_plan_transition = (
        status.endswith("PLAN_READY")
        and stage == "plan"
        and next_stage in {"review", "web-multi", "pro"}
        and not value.get("blocker")
    )
    if (
        status not in {"PASS", "PASS_WITH_NOTES", "COMPLETE"}
        and not revision_transition
        and not blocked_plan_continuation
        and not ready_plan_transition
        and not (stage == "review" and status == "FAIL")
    ) or (
        not (stage == "review" and status == "FAIL")
        and value.get("ready_for_next") is not True
    ) or (
        value.get("blocker")
        and not blocked_plan_continuation
        and not (stage == "review" and status == "FAIL")
    ):
        raise WorkflowError("stage receipt did not pass")
    output_raw = value.get("output_path")
    output, output_relative = _receipt_path(config["project_root"], output_raw)
    if not output.is_file() or not output.read_bytes().strip() or value.get("output_sha256") != sha(output):
        raise WorkflowError("stage output is missing or hash-mismatched")
    path_compatibility: dict[str, dict[str, str]] = {}
    if output_relative:
        path_compatibility["output_path"] = {"source": str(output_raw), "resolved": str(output)}
    compatibility = (
        {"_receipt_status_original": raw_status, "_receipt_status_normalized": "PLAN_READY"}
        if completed_plan_compat else {}
    )
    normalized = {
        **value,
        **compatibility,
        "output_path": str(output),
        "_receipt_path": str(receipt_path.resolve(strict=True)),
        "_receipt_sha256": sha(receipt_path),
        **({"_receipt_path_compatibility": path_compatibility} if path_compatibility else {}),
    }
    if stage == "review":
        config.setdefault("_review_policy", _review_policy_from_history(config))
        finding_ids = _receipt_finding_ids(value, legacy_fallback=status == "REVISE")
        policy = config["_review_policy"]
        if status in {"PASS", "PASS_WITH_NOTES"} and finding_ids:
            raise WorkflowError("passing review cannot retain critical findings")
        if status == "FAIL":
            return {
                **normalized,
                "_next_mission": None,
                "_terminal_attention": str(value["blocker"]),
            }
        if status == "REVISE":
            return {
                **normalized,
                "_next_mission": None,
                "_terminal_attention": (
                    "legacy REVISE cannot create a new plan; the review stage owns all locally repairable plan "
                    "defects and must return PASS_WITH_NOTES, while a concrete external blocker must return FAIL"
                ),
            }
    if next_stage not in _allowed_transitions(config, stage):
        raise WorkflowError(f"invalid transition {stage}->{next_stage}")
    if next_stage == "complete":
        return {
            **normalized,
            **({"_receipt_path_compatibility": path_compatibility} if path_compatibility else {}),
            "_next_mission": None,
        }
    next_mission_raw = value.get("next_mission_path")
    next_mission, next_mission_relative = _receipt_path(config["project_root"], next_mission_raw)
    if value.get("next_mission_sha256") != sha(next_mission):
        raise WorkflowError("next mission hash mismatch")
    if next_mission_relative:
        path_compatibility["next_mission_path"] = {
            "source": str(next_mission_raw),
            "resolved": str(next_mission),
        }
    return {
        **normalized,
        "next_mission_path": str(next_mission),
        **({"_receipt_path_compatibility": path_compatibility} if path_compatibility else {}),
        "_next_mission": next_mission,
    }


def _state_path(config: dict[str, Any], workflow_id: str) -> Path:
    project_key = hashlib.sha256(str(config["project_root"]).casefold().encode("utf-8")).hexdigest()[:24]
    return RUNNER.STATE.oracle_state_root() / "workflows" / project_key / f"{workflow_id}.json"


def _is_unambiguous_pre_submit_failure(run_dir: Path) -> bool:
    state_path = run_dir / "state.json"
    if state_path.is_file():
        try:
            if RUNNER.STATE.proven_pre_submit_failure(state_path) is not None:
                return True
        except RUNNER.STATE.OracleStateError:
            pass
    output = run_dir / "output.md"
    if output.is_file() and output.read_bytes().strip():
        return False
    stdout = run_dir / "stdout.log"
    if not stdout.is_file():
        return False
    text = stdout.read_text(encoding="utf-8", errors="replace")
    return any(marker in text for marker in UNAMBIGUOUS_PRE_SUBMIT_MARKERS)


def _missing_layout_pre_submit_proof(
    stored: dict[str, Any],
    *,
    config: dict[str, Any],
    workflow_id: str,
) -> dict[str, Any] | None:
    """Classify an absent bound run layout without inferring non-submission.

    A missing run directory proves neither browser non-submission nor a safe
    replacement by itself.  Keep the exact manifest binding for diagnosis, but
    leave fresh-run authority fail-closed until a durable pre-submit run-state
    proof exists.
    """
    attempt_id = str(stored.get("current_attempt_id") or "").strip()
    run_id = str(stored.get("oracle_run_id") or "").strip()
    raw_run_dir = str(stored.get("oracle_run_dir") or "").strip()
    raw_manifest = str(stored.get("oracle_manifest_path") or "").strip()
    if not attempt_id or run_id != attempt_id or not raw_run_dir or not raw_manifest:
        return None
    if str(stored.get("workflow_id") or "") != workflow_id:
        return None

    run_dir = Path(raw_run_dir).expanduser()
    manifest_path = Path(raw_manifest).expanduser()
    if (
        not run_dir.is_absolute()
        or run_dir.exists()
        or run_dir.is_symlink()
        or not manifest_path.is_absolute()
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
    ):
        return None
    receipt_path = Path(str(stored.get("receipt_path") or "")).expanduser()
    if receipt_path.is_file():
        return None

    augmented_candidate = Path(str(stored.get("current_augmented_mission_path") or "")).expanduser()
    binding_candidate = Path(str(stored.get("current_binding_source_path") or "")).expanduser()
    current_candidate = Path(str(stored.get("current_mission_path") or "")).expanduser()
    if any(path.is_symlink() for path in (augmented_candidate, binding_candidate, current_candidate)):
        return None
    try:
        oracle_config = RUNNER.STATE.load_manifest(manifest_path)
        expected_layout = RUNNER.STATE.create_layout(oracle_config, run_id=attempt_id)
        augmented_mission = augmented_candidate.resolve(strict=True)
        binding_source = binding_candidate.resolve(strict=True)
        current_mission = current_candidate.resolve(strict=True)
    except (OSError, ValueError, TypeError, RUNNER.STATE.OracleStateError):
        return None
    if not all(path.is_file() for path in (augmented_mission, binding_source, current_mission)):
        return None
    if (
        expected_layout.run_dir.resolve() != run_dir.resolve()
        or str(getattr(oracle_config, "requested_run_id", "") or "") != attempt_id
        or Path(oracle_config.project_root).resolve() != config["project_root"]
        or Path(oracle_config.mission_path).resolve() != augmented_mission
        or binding_source != current_mission
        or str(getattr(oracle_config, "mission_sha256", "") or "") != sha(augmented_mission)
        or str(stored.get("current_augmented_mission_sha256") or "") != sha(augmented_mission)
        or str(stored.get("current_input_sha256") or "") != sha(binding_source)
        or str(stored.get("current_binding_source_sha256") or "") != sha(binding_source)
    ):
        return None

    for record in stored.get("records") or []:
        if not isinstance(record, dict):
            return None
        record_run_id = str(record.get("run_id") or "").strip()
        record_run_dir = str(record.get("run_dir") or "").strip()
        same_run = record_run_id == attempt_id
        if record_run_dir:
            try:
                same_run = same_run or Path(record_run_dir).resolve() == run_dir.resolve()
            except OSError:
                return None
        if not same_run:
            continue
        if "ok" in record or record.get("recovered") is not True:
            return None
        if record.get("recovery_status") not in {None, ""}:
            return None

    return {
        "schema": MISSING_LAYOUT_PRE_SUBMIT_SCHEMA,
        "kind": "oracle-layout-not-created",
        "safe_for_fresh_run": False,
        "workflow_id": workflow_id,
        "attempt_id": attempt_id,
        "run_dir": str(run_dir.resolve()),
        "oracle_manifest_path": str(manifest_path.resolve()),
        "reason": "the exact bound Oracle layout was never created and no execution result was recorded",
    }


def _missing_layout_pre_submit_record(
    proof: dict[str, Any],
    *,
    stage: str,
    input_sha256: str,
    failure_code: str,
    failure_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "run_id": proof["attempt_id"],
        "run_dir": proof["run_dir"],
        "pre_submit_failure": True,
        "pre_submit_retry_consumed": False,
        "input_mission_sha256": input_sha256,
        "failure_code": failure_code,
        "failure_evidence": dict(failure_evidence or {}),
        "settlement": "oracle-layout-not-created-pre-submit",
        "settlement_proof": proof,
    }


def _pre_submit_retry_count(
    records: list[dict[str, Any]],
    *,
    stage: str,
    input_sha256: str,
    current_run_dir: Path,
    legacy_total: int = 0,
) -> int:
    """Count replacement attempts for one stage plus immutable input binding.

    Older workflows stored one global counter.  Records are the durable,
    stage-scoped ledger, so legacy retry records without an input hash bind to
    their recorded stage.  A user-confirmed settlement whose run differs from
    the current run proves that its one replacement has already started.
    """
    current = str(current_run_dir.resolve()).casefold()
    count = 0
    attributed_total = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        record_stage = str(record.get("stage") or "")
        binding = str(record.get("input_mission_sha256") or "")
        if record.get("pre_submit_retry_consumed") is True:
            attributed_total += 1
            if record_stage == stage and (not binding or binding == input_sha256):
                count += 1
            continue
        if record.get("pre_submit_failure") is True:
            attributed_total += 1
            if record_stage == stage and (not binding or binding == input_sha256):
                count += 1
            continue
        if record_stage == stage and record.get("settlement") == "user-confirmed-no-submission":
            settled_run = str(record.get("run_dir") or "").strip()
            if settled_run and str(Path(settled_run).resolve()).casefold() != current:
                count += 1
                attributed_total += 1
    # Old versions persisted only one workflow-global integer.  If records do
    # not attribute all of it to a stage, fail closed instead of guessing that
    # the current binding still owns another attempt.
    if max(0, legacy_total) > attributed_total:
        return max(count, 1)
    return count


def _user_confirmed_retry_binding_matches(
    run_dir: Path,
    *,
    config: dict[str, Any],
    workflow_id: str,
    stage: str,
    attempt_id: str,
    input_sha256: str,
    augmented_mission_path: Path,
    augmented_mission_sha256: str,
    binding_source_path: Path,
) -> bool:
    proof = RUNNER.STATE.proven_user_confirmed_no_submission(run_dir / "state.json")
    if proof is None:
        return True
    try:
        proof_project = Path(str(proof.get("project_root") or "")).resolve(strict=True)
        proof_augmented = Path(str(proof.get("_augmented_mission_path") or ""))
        proof_input = Path(str(proof.get("_input_mission_path") or ""))
        expected_augmented = augmented_mission_path.expanduser()
        expected_input = binding_source_path.expanduser()
        if (
            proof_augmented.is_symlink()
            or proof_input.is_symlink()
            or expected_augmented.is_symlink()
            or expected_input.is_symlink()
        ):
            return False
        proof_augmented = proof_augmented.resolve(strict=True)
        proof_input = proof_input.resolve(strict=True)
        expected_augmented = expected_augmented.resolve(strict=True)
        expected_input = expected_input.resolve(strict=True)
    except OSError:
        return False
    return all((
        proof_project == config["project_root"],
        str(proof.get("workflow_id") or "") == workflow_id,
        str(proof.get("stage") or "") == stage,
        str(proof.get("attempt_id") or "") == attempt_id,
        str(proof.get("run_id") or "") == attempt_id,
        str(proof.get("input_mission_sha256") or "") == input_sha256,
        proof_augmented == expected_augmented,
        proof_input == expected_input,
        str(proof.get("mission_sha256") or "") == augmented_mission_sha256,
        sha(expected_augmented) == augmented_mission_sha256,
        sha(expected_input) == input_sha256,
    ))


def _pre_submit_retry_record(
    run_dir: Path,
    *,
    stage: str,
    input_sha256: str,
    config: dict[str, Any],
    workflow_id: str,
    attempt_id: str,
    augmented_mission_path: Path,
    augmented_mission_sha256: str,
    binding_source_path: Path,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "stage": stage,
        "run_dir": str(run_dir),
        "pre_submit_failure": True,
        "pre_submit_retry_consumed": True,
        "input_mission_sha256": input_sha256,
    }
    proof = RUNNER.STATE.proven_user_confirmed_no_submission(run_dir / "state.json")
    if proof is not None:
        if not _user_confirmed_retry_binding_matches(
            run_dir,
            config=config,
            workflow_id=workflow_id,
            stage=stage,
            attempt_id=attempt_id,
            input_sha256=input_sha256,
            augmented_mission_path=augmented_mission_path,
            augmented_mission_sha256=augmented_mission_sha256,
            binding_source_path=binding_source_path,
        ):
            raise WorkflowError("user-confirmed no-submission settlement binding mismatch")
        run_state = RUNNER.STATE.load_state(run_dir / "state.json")
        reference = run_state.get("user_confirmed_no_submission")
        if isinstance(reference, dict):
            record.update({
                "settlement": "user-confirmed-no-submission",
                "settlement_path": reference.get("path"),
                "settlement_sha256": reference.get("sha256"),
            })
    return record


def _run_local_gate(config: dict[str, Any], runner: Callable[..., Any]) -> dict[str, Any]:
    command = list(config["local_gate_command"])
    bound_path = None
    if _closed_audit_enabled(config):
        bound_path = config["strict_ultra"]["research_governor_path"]
        bound_sha = sha(bound_path)
        if not any("{artifact_path}" in item for item in command) or not any(
            "{artifact_sha256}" in item for item in command
        ):
            raise WorkflowError(
                "STRICT_ULTRA_LOCAL_GATE_BINDING_REQUIRED: command must contain {artifact_path} and {artifact_sha256}"
            )
        command = [
            item.replace("{artifact_path}", str(bound_path)).replace("{artifact_sha256}", bound_sha)
            for item in command
        ]
    completed = runner(
        command,
        cwd=str(config["project_root"]),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        **RUNNER.STATE.windows_subprocess_kwargs(),
    )
    result = {
        "exit_code": int(completed.returncode),
        "stdout_sha256": hashlib.sha256((completed.stdout or "").encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256((completed.stderr or "").encode("utf-8")).hexdigest(),
    }
    if bound_path is not None and completed.returncode == 0:
        try:
            receipt = STRICT_ULTRA.validate_gate_receipt(config, completed.stdout or "", bound_path)
            STRICT_ULTRA.write_json_atomic(config["strict_ultra"]["local_gate_receipt_path"], receipt)
        except STRICT_ULTRA.StrictUltraError as exc:
            raise WorkflowError(str(exc)) from exc
        result.update({
            "receipt_path": str(config["strict_ultra"]["local_gate_receipt_path"]),
            "receipt_sha256": sha(config["strict_ultra"]["local_gate_receipt_path"]),
            "opened_path": receipt["opened_path"],
            "opened_sha256": receipt["opened_sha256"],
            "validator": receipt["validator"],
        })
    return result


def _terminal_review_state(
    config: dict[str, Any],
    workflow_id: str,
    receipt: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    blocker = str(receipt.get("_terminal_attention") or "").strip()
    if not blocker:
        return None
    return {
        "schema": STATE_SCHEMA,
        "status": "blocked",
        "terminal": True,
        "terminal_status": "REVIEW_FAILED",
        "scope_released": True,
        "workflow_id": workflow_id,
        "manifest_sha256": config["manifest_sha256"],
        "records": records,
        "review_status": receipt.get("status"),
        "review_output_path": receipt.get("output_path"),
        "review_receipt_path": receipt.get("_receipt_path"),
        "review_receipt_sha256": receipt.get("_receipt_sha256"),
        "critical_findings_sha256": receipt.get("critical_findings_sha256"),
        "critical_finding_count": len(receipt.get("critical_finding_ids") or []),
        "blocker": "review returned a valid terminal FAIL receipt",
        "next_stage": None,
    }


def _terminal_recursive_self_observation_state(
    config: dict[str, Any],
    workflow_id: str,
    run_dir: Path,
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Terminalize only the exact bounded provider self-observation signature."""
    try:
        state_path = run_dir / "state.json"
        run_state = RUNNER.STATE.load_state(state_path)
        artifacts = (
            run_state.get("artifacts")
            if isinstance(run_state.get("artifacts"), dict)
            else {}
        )
        output_path = Path(
            str(artifacts.get("output") or run_dir / "output.md")
        ).resolve(strict=True)
        output_text = output_path.read_text(encoding="utf-8", errors="strict")
        evidence = RUNNER.STATE.recursive_self_observation_evidence(
            run_state, output_text
        )
    except (OSError, UnicodeDecodeError, RUNNER.STATE.OracleStateError):
        return None
    if evidence is None:
        return None
    return {
        "schema": STATE_SCHEMA,
        "status": "blocked",
        "terminal": True,
        "terminal_status": "ORACLE_RECURSIVE_SELF_OBSERVATION",
        "scope_released": True,
        "workflow_id": workflow_id,
        "manifest_sha256": config["manifest_sha256"],
        "records": records,
        "oracle_run_id": run_state.get("run_id"),
        "oracle_run_dir": str(run_dir),
        "oracle_output_sha256": sha(output_path),
        "incident_signature": evidence["signature"],
        "safe_for_fresh_run": False,
        "auto_retry": False,
        "submission_action": "none",
        "blocker": "Oracle stage recursively observed its own controller run instead of executing the mission",
        "next_stage": None,
    }


def _recover_exact_oracle_stage(
    stored: dict[str, Any],
    *,
    oracle_recover: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Recover only the persisted Oracle run; this path never submits a prompt."""
    run_dir = Path(str(stored.get("oracle_run_dir") or "")).expanduser()
    expected_run_id = str(stored.get("oracle_run_id") or stored.get("current_attempt_id") or "")
    if not run_dir.is_absolute() or not expected_run_id:
        return {"ok": False, "error": "ORACLE_RECOVERY_IDENTITY_MISSING"}
    try:
        directory = run_dir.resolve(strict=True)
        run_state = RUNNER.STATE.load_state(directory / "state.json")
    except Exception as exc:
        return {"ok": False, "error": "ORACLE_RECOVERY_RUN_UNAVAILABLE", "detail": str(exc)}
    if str(run_state.get("run_id") or "") != expected_run_id:
        return {"ok": False, "error": "ORACLE_RECOVERY_IDENTITY_MISMATCH"}
    # Continue one exact-slug live observation.  The runner audits at the
    # caution threshold and automatically reconnects the same saved session;
    # it never submits a replacement merely because time elapsed.
    return oracle_recover(directory, action="live", dry_run=False)


def _recover_oracle_under_workflow_mutex(
    run_dir: Path,
    *,
    action: str,
    dry_run: bool,
) -> dict[str, Any]:
    """Recover a comprehensive child through its persisted parent mutex.

    The workflow already owns the canonical project mutex, while every
    comprehensive child is launched with ``parallel_parent_id`` and therefore
    owns a distinct parent-scoped mutex.  The public recovery entry point reads
    that persisted identity and acquires the same child mutex.  A missing parent
    identity fails closed instead of falling back to the non-reentrant project
    mutex or bypassing the live child lock.
    """
    directory = run_dir.expanduser().resolve(strict=True)
    state = RUNNER.STATE.load_state(directory / "state.json")
    if not str(state.get("parallel_parent_id") or "").strip():
        return {"ok": False, "error": "ORACLE_RECOVERY_PARALLEL_PARENT_MISSING"}
    return RUNNER.recover_run(directory, action=action, dry_run=dry_run)


def _recover_exact_multi_stage(stored: dict[str, Any]) -> dict[str, Any]:
    """Read a persisted Multi result only; absent identity is never a retry signal."""
    result_path = Path(str(stored.get("multi_result_path") or "")).expanduser()
    expected_manifest_sha = str(stored.get("multi_manifest_sha256") or "")
    if not result_path.is_absolute() or not expected_manifest_sha:
        return {"ok": False, "error": "MULTI_RECOVERY_IDENTITY_MISSING"}
    try:
        result = _json(result_path.resolve(strict=True))
    except Exception as exc:
        return {"ok": False, "error": "MULTI_RESULT_UNAVAILABLE", "detail": str(exc)}
    schema = result.get("schema")
    if schema not in {MULTI.RESULT_SCHEMA, MULTI.STRICT_RESULT_SCHEMA} or not str(result.get("parent_id") or ""):
        return {"ok": False, "error": "MULTI_RESULT_IDENTITY_INVALID"}
    if schema == MULTI.STRICT_RESULT_SCHEMA and result.get("manifest_sha256") != expected_manifest_sha:
        return {"ok": False, "error": "MULTI_RESULT_MANIFEST_MISMATCH"}
    return {
        "ok": result.get("status") == "complete" if schema == MULTI.STRICT_RESULT_SCHEMA else result.get("status") in {"complete", "partial"},
        "parent_id": str(result["parent_id"]),
        "next_stage_result_path": result.get("next_stage_result_path"),
        "status": result.get("status"),
        "result_path": str(result_path.resolve()),
        "manifest_sha256": expected_manifest_sha,
    }


def _run_workflow_locked(
    manifest_path: Path,
    *,
    dry_run: bool = False,
    oracle_execute: Callable[..., dict[str, Any]] = RUNNER.execute_run,
    oracle_recover: Callable[..., dict[str, Any]] = _recover_oracle_under_workflow_mutex,
    multi_execute: Callable[..., dict[str, Any]] = MULTI.run_multi,
    local_gate_runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    config = load_manifest(manifest_path)
    workflow_id = config["workflow_id"]
    config["_review_policy"] = _review_policy_from_history(config)
    config["_parallel_parent_id"] = hashlib.sha256(workflow_id.encode("utf-8")).hexdigest()
    config["workflow_dir"].mkdir(parents=True, exist_ok=True)
    if dry_run:
        attempt_id = uuid.uuid4().hex
        initial_stage = config["initial_stage"]
        if initial_stage == "pro":
            mission, receipt_path, input_sha = _pro_stage_mission(
                config, workflow_id, 0, config["initial_mission_path"], attempt_id
            )
            pro_attachments = _declared_pro_attachments(config, config["initial_mission_path"])
        else:
            mission, receipt_path, input_sha = _stage_mission(
                config, workflow_id, 0, initial_stage, config["initial_mission_path"], attempt_id
            )
            pro_attachments = ()
        oracle_manifest = _oracle_manifest(
            config,
            mission,
            mission.parent,
            attempt_id,
            stage=initial_stage,
            pro_attachments=pro_attachments,
        )
        preview = oracle_execute(oracle_manifest, dry_run=True)
        return {
            "ok": bool(preview.get("ok")),
            "schema": STATE_SCHEMA,
            "status": "dry-run",
            "workflow_id": workflow_id,
            "stage": initial_stage,
            "workflow_profile": config["workflow_profile"],
            "workflow_profile_canonical": config["workflow_profile_canonical"],
            "workflow_profile_legacy_alias": config["workflow_profile_legacy_alias"],
            "closed_audit_enabled": config["closed_audit_enabled"],
            "warnings": (
                ["strict-ultra is a deprecated input alias; use ultra-gpt with closed_audit"]
                if config["workflow_profile_legacy_alias"]
                else []
            ),
            "attempt_id": attempt_id,
            "input_mission_sha256": input_sha,
            "receipt_path": str(receipt_path),
            "oracle_preview": preview,
        }
    _claim_scope(config, workflow_id)
    state_path = _state_path(config, workflow_id)
    if state_path.is_file():
        stored = _json(state_path)
        if stored.get("manifest_sha256") != config["manifest_sha256"]:
            raise WorkflowError("workflow manifest changed after preparation")
        if stored.get("status") == "complete":
            return {"ok": True, **stored}
        if stored.get("status") == "canceled":
            return {"ok": False, **stored}
        if stored.get("status") == "blocked" and stored.get("terminal_status") == "REVIEW_FAILED":
            return {"ok": False, **stored}
        if stored.get("status") == "awaiting_receipt":
            stored_receipt = Path(str(stored["receipt_path"])).resolve()
            if not stored_receipt.is_file():
                return {"ok": False, **stored}
            receipt = _validate_receipt(
                config,
                stored_receipt,
                workflow_id,
                str(stored["current_stage"]),
                str(stored["current_attempt_id"]),
                str(stored["current_input_sha256"]),
            )
            records = list(stored.get("records") or [])
            terminal_review = _terminal_review_state(config, workflow_id, receipt, records)
            if terminal_review is not None:
                _write_workflow_state(state_path, config, terminal_review)
                return {"ok": False, **terminal_review}
            if receipt["next_stage"] == "complete":
                gate = _run_local_gate(config, local_gate_runner)
                if gate["exit_code"] != 0:
                    blocked = {**stored, "status": "attention_required", "blocker": "deterministic local gate failed", "local_gate": gate}
                    _write_workflow_state(state_path, config, blocked)
                    return {"ok": False, **blocked}
                complete = {
                    **stored, "status": "complete", "final_output_path": receipt["output_path"], "local_gate": gate
                }
                complete = _finalize_complete_workflow(state_path, config, complete, gate)
                return {"ok": True, **complete}
            stage = str(receipt["next_stage"])
            source = receipt["_next_mission"]
            start_index = int(stored["next_index"]) + 1
            _write_workflow_state(state_path, config, {
                "schema": STATE_SCHEMA, "status": "prepared", "workflow_id": workflow_id,
                "manifest_sha256": config["manifest_sha256"], "next_stage": stage,
                "next_mission_path": str(source), "next_mission_sha256": receipt["next_mission_sha256"],
                "next_index": start_index, "records": records,
            })
        elif stored.get("status") == "attention_required" and stored.get("next_stage") == "pro":
            pro_receipt = Path(str(stored.get("receipt_path") or "")).resolve()
            if not pro_receipt.is_file():
                return {"ok": False, **stored}
            receipt = _validate_receipt(
                config,
                pro_receipt,
                workflow_id,
                "pro",
                str(stored["current_attempt_id"]),
                str(stored["current_input_sha256"]),
            )
            stage = str(receipt["next_stage"])
            source = receipt["_next_mission"]
            records = list(stored.get("records") or []) + [{"stage": "pro", "receipt_path": str(pro_receipt)}]
            start_index = int(stored["next_index"]) + 1
            _write_workflow_state(state_path, config, {
                "schema": STATE_SCHEMA, "status": "prepared", "workflow_id": workflow_id,
                "manifest_sha256": config["manifest_sha256"], "next_stage": stage,
                "next_mission_path": str(source), "next_mission_sha256": receipt["next_mission_sha256"],
                "next_index": start_index, "records": records,
            })
        elif stored.get("status") in {"running", "attention_required"} and stored.get("current_stage") == "web-multi":
            recovered = _recover_exact_multi_stage(stored)
            records = list(stored.get("records") or [])
            if not recovered.get("ok"):
                blocked = {
                    **stored,
                    "status": "attention_required",
                    "blocker": "web-multi exact result is not ready; no retry was submitted",
                    "recovery": recovered,
                    "records": records,
                }
                _write_workflow_state(state_path, config, blocked)
                return {"ok": False, **blocked}
            result_path = Path(str(recovered.get("next_stage_result_path") or ""))
            if not result_path.is_file():
                blocked = {
                    **stored,
                    "status": "attention_required",
                    "blocker": "web-multi result has no bound stage receipt",
                    "recovery": recovered,
                    "records": records,
                }
                _write_workflow_state(state_path, config, blocked)
                return {"ok": False, **blocked}
            attempt_id = str(recovered["parent_id"])
            receipt = _validate_receipt(config, result_path, workflow_id, "web-multi", attempt_id, sha(Path(str(stored["current_mission_path"]))))
            records.append({"stage": "web-multi", "parent_id": attempt_id, "result_path": recovered["result_path"], "recovered": True})
            prepared = {
                "schema": STATE_SCHEMA, "status": "prepared", "workflow_id": workflow_id,
                "manifest_sha256": config["manifest_sha256"], "next_stage": str(receipt["next_stage"]),
                "next_mission_path": str(receipt["_next_mission"]),
                "next_mission_sha256": receipt["next_mission_sha256"],
                "next_index": int(stored["next_index"]) + 1,
                "records": records,
            }
            _write_workflow_state(state_path, config, prepared)
            return _run_workflow_locked(
                manifest_path, oracle_execute=oracle_execute, oracle_recover=oracle_recover,
                multi_execute=multi_execute, local_gate_runner=local_gate_runner,
            )
        elif stored.get("status") in {"running", "attention_required"} and stored.get("current_stage"):
            persisted_receipt = Path(str(stored.get("receipt_path") or "")).resolve()
            persisted_run_dir = Path(str(stored.get("oracle_run_dir") or "")).resolve()
            pre_submit_retries = int(stored.get("pre_submit_retries") or 0)
            stored_records = list(stored.get("records") or [])
            stored_stage = str(stored["current_stage"])
            stored_input_sha = str(stored.get("current_input_sha256") or "")
            retry_count = _pre_submit_retry_count(
                stored_records,
                stage=stored_stage,
                input_sha256=stored_input_sha,
                current_run_dir=persisted_run_dir,
                legacy_total=pre_submit_retries,
            )
            missing_layout_proof = _missing_layout_pre_submit_proof(
                stored,
                config=config,
                workflow_id=workflow_id,
            )
            if retry_count < 1 and missing_layout_proof is not None:
                source = Path(str(stored["current_mission_path"])).resolve(strict=True)
                retry_record = _missing_layout_pre_submit_record(
                    missing_layout_proof,
                    stage=stored_stage,
                    input_sha256=stored_input_sha,
                    failure_code="ORACLE_LAYOUT_NOT_CREATED_PRE_SUBMIT",
                )
                blocked = {
                    "schema": STATE_SCHEMA,
                    "status": "attention_required",
                    "workflow_id": workflow_id,
                    "manifest_sha256": config["manifest_sha256"],
                    "next_index": int(stored["next_index"]),
                    "records": stored_records + [retry_record],
                    "pre_submit_retries": pre_submit_retries,
                    "blocker": "the exact Oracle run layout is absent; no durable pre-submit evidence authorizes a replacement",
                }
                _write_workflow_state(state_path, config, blocked)
                return {
                    "ok": False,
                    **blocked,
                    "safe_for_fresh_run": False,
                    "settlement": "oracle-layout-not-created-pre-submit",
                }
            if (
                retry_count < 1
                and persisted_run_dir.is_dir()
                and _is_unambiguous_pre_submit_failure(persisted_run_dir)
                and _user_confirmed_retry_binding_matches(
                    persisted_run_dir,
                    config=config,
                    workflow_id=workflow_id,
                    stage=stored_stage,
                    attempt_id=str(stored.get("current_attempt_id") or stored.get("oracle_run_id") or ""),
                    input_sha256=stored_input_sha,
                    augmented_mission_path=Path(str(stored.get("current_augmented_mission_path") or "")),
                    augmented_mission_sha256=str(stored.get("current_augmented_mission_sha256") or ""),
                    binding_source_path=Path(str(stored.get("current_binding_source_path") or "")),
                )
            ):
                source = Path(str(stored["current_mission_path"])).resolve(strict=True)
                retry_record = _pre_submit_retry_record(
                    persisted_run_dir,
                    stage=stored_stage,
                    input_sha256=stored_input_sha,
                    config=config,
                    workflow_id=workflow_id,
                    attempt_id=str(stored.get("current_attempt_id") or stored.get("oracle_run_id") or ""),
                    augmented_mission_path=Path(str(stored.get("current_augmented_mission_path") or "")),
                    augmented_mission_sha256=str(stored.get("current_augmented_mission_sha256") or ""),
                    binding_source_path=Path(str(stored.get("current_binding_source_path") or "")),
                )
                prepared = {
                    "schema": STATE_SCHEMA,
                    "status": "prepared",
                    "workflow_id": workflow_id,
                    "manifest_sha256": config["manifest_sha256"],
                    "next_stage": str(stored["current_stage"]),
                    "next_mission_path": str(source),
                    "next_mission_sha256": sha(source),
                    "next_index": int(stored["next_index"]),
                    "records": stored_records + [retry_record],
                    "pre_submit_retries": pre_submit_retries + 1,
                }
                _write_workflow_state(state_path, config, prepared)
                return _run_workflow_locked(
                    manifest_path, oracle_execute=oracle_execute, oracle_recover=oracle_recover,
                    multi_execute=multi_execute, local_gate_runner=local_gate_runner,
                )
            recovered = _recover_exact_oracle_stage(stored, oracle_recover=oracle_recover)
            records = list(stored.get("records") or [])
            records.append({
                "stage": stored["current_stage"], "run_id": stored.get("oracle_run_id") or stored.get("current_attempt_id"),
                "run_dir": stored.get("oracle_run_dir"), "recovered": True, "recovery_status": recovered.get("status"),
            })
            if not recovered.get("ok"):
                blocked = {
                    **stored,
                    "status": "running" if recovered.get("status") == "session_live" else "attention_required",
                    "blocker": (
                        "exact Oracle session is still live; project ownership and archive:auto remain bound"
                        if recovered.get("status") == "session_live"
                        else "exact Oracle recovery did not prove terminal output; no retry was submitted"
                    ),
                    "recovery": recovered,
                    "records": records,
                }
                _write_workflow_state(state_path, config, blocked)
                return {"ok": False, **blocked}
            if stored.get("current_stage") == "pro" and not Path(str(stored["receipt_path"])).is_file():
                _materialize_pro_receipt(
                    config,
                    Path(str(stored["receipt_path"])),
                    workflow_id,
                    str(stored["current_attempt_id"]),
                    str(stored["current_input_sha256"]),
                    recovered,
                    run_dir=stored.get("oracle_run_dir"),
                )
            awaiting = {
                **stored, "status": "awaiting_receipt", "records": records,
                "recovery": {"status": "recovered", "run_id": stored.get("oracle_run_id") or stored.get("current_attempt_id"), "run_dir": stored.get("oracle_run_dir")},
            }
            _write_workflow_state(state_path, config, awaiting)
            return _run_workflow_locked(
                manifest_path, oracle_execute=oracle_execute, oracle_recover=oracle_recover,
                multi_execute=multi_execute, local_gate_runner=local_gate_runner,
            )
        elif stored.get("status") in {"running", "attention_required"}:
            return {"ok": False, **stored}
        else:
            _verify_prepared_final_receipt_retry(config, stored)
            stage = str(stored["next_stage"])
            source = Path(str(stored["next_mission_path"])).resolve(strict=True)
            if str(stored.get("next_mission_sha256") or "") != sha(source):
                raise WorkflowError("prepared next mission changed after receipt verification")
            records = list(stored.get("records") or [])
            start_index = int(stored.get("next_index") or 0)
    else:
        stage, source, records, start_index = config["initial_stage"], config["initial_mission_path"], [], 0
        _write_workflow_state(state_path, config, {
            "schema": STATE_SCHEMA, "status": "prepared", "workflow_id": workflow_id,
            "manifest_sha256": config["manifest_sha256"], "next_stage": stage,
            "next_mission_path": str(source), "next_mission_sha256": sha(source),
            "next_index": 0, "records": records, "workflow_profile": config["workflow_profile"],
        })
    for index in range(start_index, config["max_stages"]):
        if stage == "web-multi":
            # This complete preflight is intentionally before the send-boundary
            # state write.  An invalid Multi manifest is a retryable pre-submit
            # error, not an active/uncertain provider workflow.
            multi_config = MULTI.load_manifest(source)
            _validate_ultra_gpt_multi(config, multi_config)
            multi_source = _json(source)
            binding = multi_source.get("next_stage_binding") if isinstance(multi_source.get("next_stage_binding"), dict) else {}
            if binding.get("workflow_id") != workflow_id or binding.get("stage") != "web-multi":
                raise WorkflowError("web-multi manifest is not bound to this workflow")
            multi_result_path = multi_config["output_dir"] / "result.json"
            multi_receipt_path = multi_config.get("next_stage_result_path")
            multi_execution_id = hashlib.sha256(
                f"{workflow_id}:{index}:{sha(source)}".encode("utf-8")
            ).hexdigest()
            _write_workflow_state(state_path, config, {
                "schema": STATE_SCHEMA, "status": "running", "workflow_id": workflow_id,
                "manifest_sha256": config["manifest_sha256"], "current_stage": stage,
                "current_mission_path": str(source), "next_index": index, "records": records,
                "multi_execution_id": multi_execution_id, "multi_manifest_sha256": sha(source),
                "multi_result_path": str(multi_result_path),
                "multi_receipt_path": str(multi_receipt_path) if multi_receipt_path else None,
            })
            multi_result = multi_execute(source, dry_run=False, parent_lock_held=True)
            records.append({"stage": stage, "result": multi_result})
            if not multi_result.get("ok"):
                break
            result_path = Path(str(multi_result.get("next_stage_result_path") or ""))
            if not result_path.is_file():
                return {"ok": False, "status": "attention_required", "workflow_id": workflow_id,
                        "error": "web-multi merger did not provide next_stage_result_path", "records": records}
            attempt_id = str(multi_result.get("parent_id") or "")
            receipt = _validate_receipt(config, result_path, workflow_id, "web-multi", attempt_id, sha(source))
            stage, source = str(receipt["next_stage"]), receipt["_next_mission"]
            _write_workflow_state(state_path, config, {
                "schema": STATE_SCHEMA, "status": "prepared", "workflow_id": workflow_id,
                "manifest_sha256": config["manifest_sha256"], "next_stage": stage,
                "next_mission_path": str(source), "next_mission_sha256": receipt["next_mission_sha256"],
                "next_index": index + 1, "records": records,
            })
            continue
        attempt_id = uuid.uuid4().hex
        if stage == "pro":
            pro_attachments = _declared_pro_attachments(config, source)
            mission, receipt_path, input_sha = _pro_stage_mission(
                config, workflow_id, index, source, attempt_id
            )
        else:
            if _mission_contains_pro_attachment_contract(source):
                raise WorkflowError("Pro attachment contract is forbidden for regular DevSpace stages")
            pro_attachments = ()
            mission, receipt_path, input_sha = _stage_mission(
                config, workflow_id, index, stage, source, attempt_id
            )
        stage_dir = mission.parent
        oracle_manifest = _oracle_manifest(
            config, mission, stage_dir, attempt_id, stage=stage, pro_attachments=pro_attachments
        )
        oracle_config = RUNNER.STATE.load_manifest(oracle_manifest)
        oracle_layout = RUNNER.STATE.create_layout(oracle_config, run_id=attempt_id)
        stage_pre_submit_retries = int(_json(state_path).get("pre_submit_retries") or 0)
        _write_workflow_state(state_path, config, {
            "schema": STATE_SCHEMA, "status": "running", "workflow_id": workflow_id,
            "manifest_sha256": config["manifest_sha256"], "current_stage": stage,
            "current_attempt_id": attempt_id, "current_input_sha256": input_sha,
            "current_mission_path": str(source), "receipt_path": str(receipt_path),
            "current_binding_source_path": str(source),
            "current_binding_source_sha256": input_sha,
            "current_augmented_mission_path": str(mission),
            "current_augmented_mission_sha256": sha(mission),
            "oracle_run_id": attempt_id, "oracle_run_dir": str(oracle_layout.run_dir), "oracle_manifest_path": str(oracle_manifest),
            "next_index": index, "records": records, "pre_submit_retries": stage_pre_submit_retries,
        })
        try:
            run = oracle_execute(oracle_manifest, dry_run=False)
        except RUNNER.OracleRunError as error:
            if error.code != "DEVSPACE_EXACT_ROOT_UNAVAILABLE" or oracle_layout.run_dir.exists():
                raise
            current = _json(state_path)
            proof = _missing_layout_pre_submit_proof(
                current,
                config=config,
                workflow_id=workflow_id,
            )
            if proof is None:
                raise
            retry_record = _missing_layout_pre_submit_record(
                proof,
                stage=stage,
                input_sha256=input_sha,
                failure_code=error.code,
                failure_evidence=error.evidence,
            )
            blocked = {
                "schema": STATE_SCHEMA,
                "status": "attention_required",
                "workflow_id": workflow_id,
                "manifest_sha256": config["manifest_sha256"],
                "next_index": index,
                "records": records + [retry_record],
                "pre_submit_retries": stage_pre_submit_retries,
                "blocker": "the exact Oracle run layout is absent; no durable pre-submit evidence authorizes a replacement",
            }
            _write_workflow_state(state_path, config, blocked)
            return {
                "ok": False,
                **blocked,
                "safe_for_fresh_run": False,
                "settlement": "oracle-layout-not-created-pre-submit",
            }
        records.append({"stage": stage, "run_dir": run.get("run_dir"), "ok": bool(run.get("ok"))})
        if stage == "pro" and run.get("ok") and not receipt_path.is_file():
            _materialize_pro_receipt(
                config,
                receipt_path,
                workflow_id,
                attempt_id,
                input_sha,
                run,
                run_dir=run.get("run_dir"),
            )
        if stage != "pro" and run.get("ok") and not receipt_path.is_file():
            try:
                _materialize_regular_receipt(
                    config,
                    receipt_path,
                    workflow_id,
                    stage,
                    attempt_id,
                    input_sha,
                    run,
                    run_dir=run.get("run_dir"),
                )
            except WorkflowError as exc:
                records.append({
                    "stage": stage,
                    "host_materialization": "unavailable",
                    "error": str(exc),
                })
        if run.get("ok"):
            _write_workflow_state(state_path, config, {
                "schema": STATE_SCHEMA, "status": "awaiting_receipt", "workflow_id": workflow_id,
                "manifest_sha256": config["manifest_sha256"], "current_stage": stage,
                "current_attempt_id": attempt_id, "current_input_sha256": input_sha,
                "current_mission_path": str(source), "receipt_path": str(receipt_path),
                "current_binding_source_path": str(source),
                "current_binding_source_sha256": input_sha,
                "current_augmented_mission_path": str(mission),
                "current_augmented_mission_sha256": sha(mission),
                "oracle_run_dir": run.get("run_dir"), "next_index": index, "records": records,
            })
        if run.get("ok") and not receipt_path.is_file():
            return {"ok": False, **_json(state_path)}
        if not run.get("ok"):
            failed_run_dir = Path(str(run.get("run_dir") or "")).resolve()
            recursive_terminal = (
                _terminal_recursive_self_observation_state(
                    config, workflow_id, failed_run_dir, records
                )
                if failed_run_dir.is_dir()
                else None
            )
            if recursive_terminal is not None:
                _write_workflow_state(state_path, config, recursive_terminal)
                return {"ok": False, **recursive_terminal}
            pre_submit_retries = int(_json(state_path).get("pre_submit_retries") or 0)
            if (
                _pre_submit_retry_count(
                    records,
                    stage=stage,
                    input_sha256=input_sha,
                    current_run_dir=failed_run_dir,
                    legacy_total=pre_submit_retries,
                ) < 1
                and failed_run_dir.is_dir()
                and _is_unambiguous_pre_submit_failure(failed_run_dir)
                and _user_confirmed_retry_binding_matches(
                    failed_run_dir,
                    config=config,
                    workflow_id=workflow_id,
                    stage=stage,
                    attempt_id=attempt_id,
                    input_sha256=input_sha,
                    augmented_mission_path=mission,
                    augmented_mission_sha256=sha(mission),
                    binding_source_path=source,
                )
            ):
                retry_record = _pre_submit_retry_record(
                    failed_run_dir,
                    stage=stage,
                    input_sha256=input_sha,
                    config=config,
                    workflow_id=workflow_id,
                    attempt_id=attempt_id,
                    augmented_mission_path=mission,
                    augmented_mission_sha256=sha(mission),
                    binding_source_path=source,
                )
                _write_workflow_state(state_path, config, {
                    "schema": STATE_SCHEMA,
                    "status": "prepared",
                    "workflow_id": workflow_id,
                    "manifest_sha256": config["manifest_sha256"],
                    "next_stage": stage,
                    "next_mission_path": str(source),
                    "next_mission_sha256": sha(source),
                    "next_index": index,
                    "records": records + [retry_record],
                    "pre_submit_retries": pre_submit_retries + 1,
                })
                return _run_workflow_locked(
                    manifest_path, oracle_execute=oracle_execute, oracle_recover=oracle_recover,
                    multi_execute=multi_execute, local_gate_runner=local_gate_runner,
                )
            retained = {
                **_json(state_path), "status": "attention_required", "records": records,
                "blocker": "Oracle stage needs exact recovery; no replacement was submitted",
            }
            _write_workflow_state(state_path, config, retained)
            return {"ok": False, **retained}
        receipt = _validate_receipt(config, receipt_path, workflow_id, stage, attempt_id, input_sha)
        terminal_review = _terminal_review_state(config, workflow_id, receipt, records)
        if terminal_review is not None:
            _write_workflow_state(state_path, config, terminal_review)
            return {"ok": False, **terminal_review}
        if receipt["next_stage"] == "complete":
            gate = _run_local_gate(config, local_gate_runner)
            if gate["exit_code"] != 0:
                result = {"schema": STATE_SCHEMA, "status": "attention_required", "workflow_id": workflow_id,
                          "manifest_sha256": config["manifest_sha256"], "records": records,
                          "blocker": "deterministic local gate failed", "local_gate": gate}
                _write_workflow_state(state_path, config, result)
                return {"ok": False, **result}
            result = {"schema": STATE_SCHEMA, "status": "complete", "workflow_id": workflow_id,
                      "manifest_sha256": config["manifest_sha256"], "records": records,
                      "final_output_path": receipt["output_path"], "local_gate": gate}
            result = _finalize_complete_workflow(state_path, config, result, gate)
            return {"ok": True, **result}
        stage, source = str(receipt["next_stage"]), receipt["_next_mission"]
        _write_workflow_state(state_path, config, {
            "schema": STATE_SCHEMA, "status": "prepared", "workflow_id": workflow_id,
            "manifest_sha256": config["manifest_sha256"], "next_stage": stage,
            "next_mission_path": str(source), "next_mission_sha256": receipt["next_mission_sha256"],
            "next_index": index + 1, "records": records,
        })
    result = {"schema": STATE_SCHEMA, "status": "attention_required", "workflow_id": workflow_id,
              "records": records, "next_stage": stage, "blocker": "stage failed or maximum stage count reached"}
    _write_workflow_state(state_path, config, {**result, "manifest_sha256": config["manifest_sha256"]})
    return {"ok": False, **result}


def run_workflow(
    manifest_path: Path,
    *,
    dry_run: bool = False,
    oracle_execute: Callable[..., dict[str, Any]] = RUNNER.execute_run,
    oracle_recover: Callable[..., dict[str, Any]] = _recover_oracle_under_workflow_mutex,
    multi_execute: Callable[..., dict[str, Any]] = MULTI.run_multi,
    local_gate_runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    config = load_manifest(manifest_path)
    if dry_run:
        return _run_workflow_locked(
            manifest_path,
            dry_run=True,
            oracle_execute=oracle_execute,
            oracle_recover=oracle_recover,
            multi_execute=multi_execute,
            local_gate_runner=local_gate_runner,
        )
    with RUNNER.STATE.project_submit_mutex(
        config["project_root"],
        timeout_seconds=30,
        source_thread_id=config.get("source_thread_id"),
    ):
        return _run_workflow_locked(
            manifest_path,
            dry_run=False,
            oracle_execute=oracle_execute,
            oracle_recover=oracle_recover,
            multi_execute=multi_execute,
            local_gate_runner=local_gate_runner,
        )


def _verify_prepared_final_receipt_retry(config: dict[str, Any], stored: dict[str, Any]) -> None:
    records = stored.get("records") or []
    if not records or not records[-1].get("final_receipt_retry_authority"):
        return
    record = records[-1]
    authority_path = Path(record["final_receipt_retry_authority"])
    authority, _ = _load_expected_json(authority_path, record["authority_sha256"], label="retry authority")
    state_path = _state_path(config, config["workflow_id"])
    expected_path = state_path.parent / "final-receipt-retries" / config["workflow_id"] / f"{authority['run_id']}.json"
    if (
        authority_path != expected_path
        or authority.get("workflow_id") != config["workflow_id"]
        or authority.get("target_source_thread_id") != config.get("source_thread_id")
        or stored.get("next_stage") != "final-web-gate"
        or stored.get("next_index") != authority.get("next_index")
        or stored.get("next_mission_sha256") != authority.get("input_mission_sha256")
    ):
        raise WorkflowError("retry authority binding mismatch")
    run_dir = RUNNER.STATE.oracle_state_root() / "projects" / state_path.parent.name / "runs" / authority["run_id"]
    for path, digest in (
        (Path(authority["receipt_path"]), authority["receipt_sha256"]),
        (run_dir / "state.json", authority["run_state_sha256"]),
        (run_dir / "output.md", authority["output_sha256"]),
        (run_dir / "mission.md", authority["augmented_mission_sha256"]),
        (Path(authority["stage_output_path"]), authority["stage_output_sha256"]),
        (Path(authority["empty_terminal_path"]), authority["empty_terminal_sha256"]),
    ):
        if path.is_symlink() or sha(path) != digest:
            raise WorkflowError("retry evidence changed before submission")


def prepare_final_receipt_retry(
    manifest_path: Path, *, expected_workflow_sha256: str,
    expected_scope_sha256: str, expected_run_state_sha256: str,
    expected_receipt_sha256: str, confirmation: str, dry_run: bool = False,
) -> dict[str, Any]:
    """Authorize one fresh final attestation; never repair a web verdict in place."""
    if confirmation != "user-authorized-final-receipt-retry":
        raise WorkflowError("explicit user-authorized-final-receipt-retry confirmation required")
    expected_workflow_sha256 = _required_sha256(expected_workflow_sha256, label="workflow state")
    expected_scope_sha256 = _required_sha256(expected_scope_sha256, label="scope state")
    expected_run_state_sha256 = _required_sha256(expected_run_state_sha256, label="Oracle run state")
    expected_receipt_sha256 = _required_sha256(expected_receipt_sha256, label="stage receipt")
    config = load_manifest(manifest_path)
    if config["workflow_profile"] != "standard" or _closed_audit_enabled(config):
        raise WorkflowError("final receipt retry supports standard workflows only")
    owner = RUNNER.STATE.current_source_thread_id()
    if not owner or owner != config.get("source_thread_id"):
        raise WorkflowError("FOREIGN_TASK_SESSION: final receipt retry requires the owning task")
    workflow_id = config["workflow_id"]
    state_path = _state_path(config, workflow_id)
    scope_path = _scope_path(config)
    with RUNNER.STATE.project_submit_mutex(
        config["project_root"], timeout_seconds=30, source_thread_id=owner,
    ):
        stored, _ = _load_expected_json(state_path, expected_workflow_sha256, label="workflow state")
        scope, _ = _load_expected_json(scope_path, expected_scope_sha256, label="scope state")
        if (
            stored.get("status") != "awaiting_receipt"
            or stored.get("current_stage") != "final-web-gate"
            or stored.get("workflow_id") != workflow_id
            or stored.get("manifest_sha256") != config["manifest_sha256"]
            or stored.get("source_thread_id") != owner
            or scope.get("source_thread_id") != owner
            or scope.get("active_workflow_id") != workflow_id
            or scope.get("status") != "active"
            or stored.get("final_receipt_retry")
            or any(record.get("final_receipt_retry_authority") for record in stored.get("records", []))
        ):
            raise WorkflowError("workflow is not an owned, unretried final receipt candidate")
        index = int(stored["next_index"]) + 1
        if index >= config["max_stages"]:
            raise WorkflowError("final receipt retry cannot reset the stage budget")
        attempt = str(stored["current_attempt_id"])
        if not re.fullmatch(r"[0-9a-f]{32}", attempt):
            raise WorkflowError("invalid exact attempt ID")
        project_key = state_path.parent.name
        run_dir = RUNNER.STATE.oracle_state_root() / "projects" / project_key / "runs" / attempt
        if Path(stored["oracle_run_dir"]).resolve() != run_dir.resolve():
            raise WorkflowError("Oracle run directory identity mismatch")
        run_path = run_dir / "state.json"
        run, _ = _load_expected_json(run_path, expected_run_state_sha256, label="Oracle run state")
        if (
            RUNNER.STATE.source_thread_id_from_state(run) != owner
            or run.get("run_id") != attempt
            or run.get("project_root") != str(config["project_root"])
            or run.get("status") != "complete"
            or run.get("session_authority") != "terminal"
            or run.get("terminal_harvested") is not True
            or run.get("task_outcome") != "executed"
            or not RUNNER.STATE.proven_ownership_receipt(run_path)
            or not RUNNER.STATE.proven_browser_identity_receipt(run_path)
        ):
            raise WorkflowError("exact Oracle run is not owned, completed and harvested")
        provider = run.get("provider_session") or {}
        meta_path = Path(str(provider.get("oracle_meta_path") or "")).expanduser()
        if (
            provider.get("terminal_confirmed") is not True
            or provider.get("status") != "completed"
            or not meta_path.is_absolute() or not meta_path.is_file() or meta_path.is_symlink()
            or sha(meta_path) != provider.get("oracle_meta_sha256")
        ):
            raise WorkflowError("terminal provider evidence changed")
        observer = run.get("browser_observer") or {}
        pid = observer.get("oracle_process_pid")
        if observer.get("status") != "process-exited" or not isinstance(pid, int) or RUNNER.STATE._process_may_be_alive(pid):
            raise WorkflowError("Oracle observer is live or uncertain")
        output = run_dir / "output.md"
        output_lines = output.read_text(encoding="utf-8").rstrip().splitlines()
        if sha(output) != run.get("artifact_sha256") or not output_lines or output_lines[-1] != "TASK_OUTCOME: EXECUTED":
            raise WorkflowError("terminal Oracle output changed")
        source = _inside(config["project_root"], stored["current_binding_source_path"])
        augmented = _inside(config["project_root"], stored["current_augmented_mission_path"])
        if (
            sha(source) != stored["current_input_sha256"]
            or sha(source) != stored["current_binding_source_sha256"]
            or sha(augmented) != stored["current_augmented_mission_sha256"]
            or sha(augmented) != (run.get("mission") or {}).get("sha256")
            or sha(run_dir / "mission.md") != sha(augmented)
        ):
            raise WorkflowError("immutable mission binding changed")
        receipt_path = _inside(config["project_root"], stored["receipt_path"])
        receipt, _ = _load_expected_json(receipt_path, expected_receipt_sha256, label="stage receipt")
        expected = {
            "schema": RECEIPT_SCHEMA, "workflow_id": workflow_id, "stage": "final-web-gate",
            "attempt_id": attempt, "input_mission_sha256": sha(source),
            "status": "PASS", "next_stage": "", "ready_for_next": False, "blocker": "",
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise WorkflowError("receipt is not the bounded empty-transition PASS error")
        result_path = _inside(config["project_root"], receipt["output_path"])
        terminal = _inside(config["project_root"], receipt["next_mission_path"])
        if (
            not result_path.read_bytes().strip() or sha(result_path) != receipt["output_sha256"]
            or terminal.read_bytes() != b"" or sha(terminal) != receipt["next_mission_sha256"]
        ):
            raise WorkflowError("stage output or empty terminal mission changed")
        authority_path = state_path.parent / "final-receipt-retries" / workflow_id / f"{attempt}.json"
        authority = {
            "schema": "codex.chatgpt.final-receipt-retry/v1", "workflow_id": workflow_id,
            "evaluated_from_thread": owner, "target_source_thread_id": owner,
            "run_id": attempt, "slug": (run.get("oracle") or {}).get("slug"),
            "confirmation": confirmation, "workflow_state_sha256": expected_workflow_sha256,
            "scope_state_sha256": expected_scope_sha256, "run_state_sha256": expected_run_state_sha256,
            "receipt_path": str(receipt_path), "receipt_sha256": expected_receipt_sha256,
            "output_sha256": sha(output), "stage_output_sha256": sha(result_path),
            "stage_output_path": str(result_path), "empty_terminal_path": str(terminal),
            "empty_terminal_sha256": sha(terminal),
            "input_mission_sha256": sha(source), "augmented_mission_sha256": sha(augmented),
            "next_index": index, "action": "prepare-same-workflow-final-attestation",
        }
        if not dry_run:
            config["_review_policy"] = dict(stored["review_policy"])
            _materialize_bound_text(authority_path, json.dumps(authority, sort_keys=True, indent=2) + "\n")
            _write_workflow_state(state_path, config, {
                **stored, "status": "prepared", "next_stage": "final-web-gate",
                "next_mission_path": str(source), "next_mission_sha256": sha(source),
                "next_index": index,
                "final_receipt_retry": {"path": str(authority_path), "sha256": sha(authority_path)},
                "records": list(stored.get("records") or []) + [{
                    "stage": "final-web-gate", "run_dir": str(run_dir),
                    "final_receipt_retry_authority": str(authority_path),
                    "authority_sha256": sha(authority_path),
                }],
            })
        return {"ok": True, "dry_run": dry_run, "workflow_id": workflow_id,
                "authority_path": str(authority_path), "submission_action": "none",
                "next_action": "resume-the-same-manifest"}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a bounded Oracle comprehensive workflow.")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cancel-user-stopped", action="store_true")
    parser.add_argument("--retry-final-receipt", action="store_true")
    parser.add_argument("--expected-receipt-sha256")
    parser.add_argument("--workflow-state", type=Path)
    parser.add_argument("--scope-state", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--workflow-id")
    parser.add_argument("--run-id")
    parser.add_argument("--expected-workflow-sha256")
    parser.add_argument("--expected-scope-sha256")
    parser.add_argument("--expected-run-state-sha256")
    parser.add_argument("--confirmation")
    args = parser.parse_args(argv)
    try:
        if args.retry_final_receipt:
            if args.cancel_user_stopped or args.manifest is None:
                raise WorkflowError("--retry-final-receipt requires --manifest and excludes cancellation")
            value = prepare_final_receipt_retry(
                args.manifest, expected_workflow_sha256=args.expected_workflow_sha256,
                expected_scope_sha256=args.expected_scope_sha256,
                expected_run_state_sha256=args.expected_run_state_sha256,
                expected_receipt_sha256=args.expected_receipt_sha256,
                confirmation=args.confirmation, dry_run=args.dry_run,
            )
        elif args.cancel_user_stopped:
            if args.manifest is not None:
                raise WorkflowError("--manifest cannot be combined with --cancel-user-stopped")
            required = {
                "workflow_state": args.workflow_state,
                "scope_state": args.scope_state,
                "run_dir": args.run_dir,
                "workflow_id": args.workflow_id,
                "run_id": args.run_id,
                "expected_workflow_sha256": args.expected_workflow_sha256,
                "expected_scope_sha256": args.expected_scope_sha256,
                "expected_run_state_sha256": args.expected_run_state_sha256,
                "confirmation": args.confirmation,
            }
            missing = sorted(key for key, value in required.items() if value in {None, ""})
            if missing:
                raise WorkflowError(f"cancel-user-stopped requires: {', '.join(missing)}")
            value = settle_user_stopped_workflow(
                workflow_state_path=args.workflow_state,
                scope_state_path=args.scope_state,
                run_dir=args.run_dir,
                workflow_id=args.workflow_id,
                run_id=args.run_id,
                expected_workflow_sha256=args.expected_workflow_sha256,
                expected_scope_sha256=args.expected_scope_sha256,
                expected_run_state_sha256=args.expected_run_state_sha256,
                confirmation=args.confirmation,
                dry_run=args.dry_run,
            )
        else:
            if args.manifest is None:
                raise WorkflowError("--manifest is required unless --cancel-user-stopped is selected")
            value = run_workflow(args.manifest, dry_run=args.dry_run)
    except Exception as exc:
        value = {"ok": False, "error": {"code": "ORACLE_COMPREHENSIVE_FAILED", "message": str(exc)}}
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
