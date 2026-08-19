from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA = "codex.chatgpt.oracle-comprehensive/v1"
RECEIPT_SCHEMA = "codex.chatgpt.oracle-stage-result/v1"
PRO_OUTPUT_SCHEMA = "codex.chatgpt.oracle-pro-stage-output/v1"
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
STANDARD_PROFILE = "standard"
ULTRA_ECONOMY_PROFILE = "ultra-economy"
ULTRA_GPT_PROFILE = "ultra-gpt"
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
WORKSPACE_CONFIG = _load("oracle_comprehensive_workspace_config", BIN / "chatgpt_workspace_config.py")


class WorkflowError(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WorkflowError(f"JSON object required: {path}")
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
    if workflow_profile not in {STANDARD_PROFILE, ULTRA_ECONOMY_PROFILE, ULTRA_GPT_PROFILE}:
        raise WorkflowError("workflow_profile must be standard, ultra-economy, or ultra-gpt")
    allow_pro_raw = value.get("allow_pro", False)
    if not isinstance(allow_pro_raw, bool):
        raise WorkflowError("allow_pro must be a boolean explicit opt-in")
    allow_pro = allow_pro_raw or workflow_profile == ULTRA_ECONOMY_PROFILE
    initial_stage = str(
        value.get("initial_stage")
        or ("pro" if workflow_profile == ULTRA_ECONOMY_PROFILE else "plan")
    ).strip().casefold()
    if "local_runtime_contract" in value:
        raise WorkflowError(
            "local_runtime_contract is not accepted; ultra-economy activation is a one-time conversational handshake"
        )
    if workflow_profile == ULTRA_ECONOMY_PROFILE:
        if initial_stage != "pro":
            raise WorkflowError("ULTRA_ECONOMY_INITIAL_STAGE_REQUIRED: initial_stage must be pro")
        if maximum < 4:
            raise WorkflowError("ULTRA_ECONOMY_STAGE_BUDGET_TOO_SMALL: max_stages must be at least 4")
    elif workflow_profile == ULTRA_GPT_PROFILE:
        if allow_pro:
            raise WorkflowError(
                "ULTRA_GPT_PRO_IS_SEPARATE: use at most one explicitly authorized Pro design advisory before "
                "the ultra-gpt workflow; the workflow itself remains regular non-Pro"
            )
        if initial_stage != "plan":
            raise WorkflowError("ULTRA_GPT_INITIAL_STAGE_REQUIRED: initial_stage must be plan")
        if maximum < 5:
            raise WorkflowError("ULTRA_GPT_STAGE_BUDGET_TOO_SMALL: max_stages must be at least 5")
        if str(value.get("model") or "gpt-5.6").strip() != "gpt-5.6":
            raise WorkflowError("ULTRA_GPT_REGULAR_MODEL_REQUIRED: model must be gpt-5.6")
    else:
        if initial_stage != "plan":
            raise WorkflowError("standard workflow initial_stage must be plan")
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
    return {
        **value,
        "project_root": root,
        "workflow_dir": workflow_dir,
        "initial_mission_path": mission,
        "max_stages": maximum,
        "workflow_profile": workflow_profile,
        "allow_pro": allow_pro,
        "initial_stage": initial_stage,
        "app_name": app_name,
        "model": str(value.get("model") or "gpt-5.6"),
        "copy_profile": Path(
            str(value.get("copy_profile") or (Path.home() / ".oracle" / "browser-profile"))
        ).expanduser().resolve(),
        "local_gate_command": list(local_gate),
        "manifest_sha256": sha(path.resolve(strict=True)),
        "workflow_id": workflow_id,
    }


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    if config.get("workflow_profile") != ULTRA_GPT_PROFILE:
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
    if config.get("workflow_profile") == ULTRA_GPT_PROFILE:
        return {
            "plan": {"review"},
            "review": {"web-multi"},
            "web-multi": {"final-web-gate"},
            "final-web-gate": {"complete"},
        }.get(stage, set())
    return TRANSITIONS[stage]


def _scope_path(config: dict[str, Any]) -> Path:
    project_key = hashlib.sha256(str(config["project_root"]).casefold().encode("utf-8")).hexdigest()[:24]
    scope_material = f"{config['project_root']}|{config['workflow_dir'].parent}".casefold()
    scope_key = hashlib.sha256(scope_material.encode("utf-8")).hexdigest()[:32]
    return RUNNER.STATE.oracle_state_root() / "comprehensive-scopes" / project_key / f"{scope_key}.json"


def _claim_scope(config: dict[str, Any], workflow_id: str) -> None:
    path = _scope_path(config)
    if path.is_file():
        stored = _json(path)
        active = str(stored.get("active_workflow_id") or "")
        status = str(stored.get("status") or "")
        if active and active != workflow_id and status != "complete":
            raise WorkflowError(
                f"comprehensive scope already belongs to active workflow {active}; recover that exact workflow"
            )
    _write(path, {
        "schema": SCOPE_SCHEMA,
        "status": "active",
        "active_workflow_id": workflow_id,
        "project_root": str(config["project_root"]),
        "workflow_parent": str(config["workflow_dir"].parent),
        "review_policy": config["_review_policy"],
    })


def _write_workflow_state(path: Path, config: dict[str, Any], value: dict[str, Any]) -> None:
    payload = {**value, "review_policy": dict(config["_review_policy"])}
    _write(path, payload)
    scope_status = str(payload.get("status") or "active")
    _write(_scope_path(config), {
        "schema": SCOPE_SCHEMA,
        "status": scope_status if scope_status in {"complete", "attention_required", "failed"} else "active",
        "active_workflow_id": config["workflow_id"],
        "project_root": str(config["project_root"]),
        "workflow_parent": str(config["workflow_dir"].parent),
        "workflow_state_path": str(path),
        "review_policy": dict(config["_review_policy"]),
    })


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
    )
    if stage == "plan":
        pro_selection_instruction = (
            "A next_stage=pro transition is permitted when it is genuinely useful.\n"
            if config["allow_pro"]
            else (
                "Do not emit next_stage=pro; continue with review.\n"
                if config.get("workflow_profile") == ULTRA_GPT_PROFILE
                else "Do not emit next_stage=pro; continue with review or an authorized web-multi stage.\n"
            )
        )
        protocol += (
            "\n[PRO_SELECTION_POLICY]\n"
            f"pro_selection_allowed={'true' if config['allow_pro'] else 'false'}\n"
            "Pro is quota-limited and may be selected only when this manifest explicitly authorizes it. "
            f"{pro_selection_instruction}"
            "\n[PRO_ATTACHMENT_AUTHORING_CONTRACT]\n"
            "Use the default Pro DevSpace route unless frozen external evidence is unavailable or inappropriate "
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
        if config.get("workflow_profile") == ULTRA_GPT_PROFILE:
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
            if config.get("workflow_profile") == ULTRA_GPT_PROFILE
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
        if config.get("workflow_profile") == ULTRA_GPT_PROFILE:
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
        "thinking_time": "heavy" if stage == "pro" else "extra-high",
        "research": "off",
        "archive": "auto",
        "parallel_parent_id": config["_parallel_parent_id"],
        "run_id": run_id,
    }
    if stage == "pro":
        if pro_attachments:
            payload["transport"] = "pro-attachment-only"
            payload["attachments"] = [str(mission), *(str(item) for item in pro_attachments)]
        else:
            payload["transport"] = "pro-devspace"
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


def _validate_receipt(
    config: dict[str, Any],
    receipt_path: Path,
    workflow_id: str,
    stage: str,
    attempt_id: str,
    input_sha: str,
) -> dict[str, Any]:
    value = _json(receipt_path)
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
    if config.get("workflow_profile") == ULTRA_GPT_PROFILE:
        required_next = {
            "plan": "review", "review": "web-multi", "web-multi": "final-web-gate",
            "final-web-gate": "complete",
        }.get(stage)
        if required_next and next_stage != required_next:
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
            "web-multi" if config.get("workflow_profile") == ULTRA_GPT_PROFILE else "implementation"
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
    completed = runner(
        config["local_gate_command"],
        cwd=str(config["project_root"]),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        **RUNNER.STATE.windows_subprocess_kwargs(),
    )
    return {
        "exit_code": int(completed.returncode),
        "stdout_sha256": hashlib.sha256((completed.stdout or "").encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256((completed.stderr or "").encode("utf-8")).hexdigest(),
    }


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
        "status": "attention_required",
        "workflow_id": workflow_id,
        "manifest_sha256": config["manifest_sha256"],
        "records": records,
        "review_status": receipt.get("status"),
        "review_output_path": receipt.get("output_path"),
        "blocker": blocker,
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
                _write_workflow_state(state_path, config, complete)
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
            if (
                _pre_submit_retry_count(
                    stored_records,
                    stage=stored_stage,
                    input_sha256=stored_input_sha,
                    current_run_dir=persisted_run_dir,
                    legacy_total=pre_submit_retries,
                ) < 1
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
        run = oracle_execute(oracle_manifest, dry_run=False)
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
            _write_workflow_state(state_path, config, result)
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
    with RUNNER.STATE.project_submit_mutex(config["project_root"], timeout_seconds=30):
        return _run_workflow_locked(
            manifest_path,
            dry_run=False,
            oracle_execute=oracle_execute,
            oracle_recover=oracle_recover,
            multi_execute=multi_execute,
            local_gate_runner=local_gate_runner,
        )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a bounded Oracle comprehensive workflow.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = run_workflow(args.manifest, dry_run=args.dry_run)
    except Exception as exc:
        value = {"ok": False, "error": {"code": "ORACLE_COMPREHENSIVE_FAILED", "message": str(exc)}}
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
