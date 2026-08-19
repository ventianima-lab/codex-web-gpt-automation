from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import uuid
import re
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

SCHEMA = "codex.chatgpt.oracle-multi/v1"
STRICT_SCHEMA = "codex.chatgpt.oracle-multi/v2"
RESULT_SCHEMA = "codex.chatgpt.oracle-multi-result/v1"
STRICT_RESULT_SCHEMA = "codex.chatgpt.oracle-multi-result/v2"
LANE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
BIN = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"module unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load("chatgpt_oracle_multi_runner", BIN / "chatgpt_oracle_run.py")
STATE = RUNNER.STATE
WORKSPACE_CONFIG = _load("chatgpt_oracle_multi_workspace_config", BIN / "chatgpt_workspace_config.py")


class MultiError(RuntimeError):
    pass


def _git_common_dir(root: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        **STATE.windows_subprocess_kwargs(),
    )
    if completed.returncode != 0 or not (completed.stdout or "").strip():
        raise MultiError(f"write worktree is not a Git worktree: {root}")
    return Path(completed.stdout.strip()).resolve()


def _git(root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=text,
        check=False,
        **STATE.windows_subprocess_kwargs(),
    )
    if completed.returncode != 0:
        detail = completed.stderr or completed.stdout or ("" if text else b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise MultiError(f"git {' '.join(args)} failed for {root}: {str(detail).strip()}")
    return completed


def _normalized_relative(value: Any) -> str:
    raw = unicodedata.normalize("NFC", str(value or "").strip().replace("\\", "/"))
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or raw.startswith("//") or any(part in {"", ".", ".."} for part in path.parts):
        raise MultiError("owned_paths must be nonempty normalized project-relative paths")
    if any(":" in part for part in path.parts):
        raise MultiError("owned_paths cannot contain drive or alternate-data-stream syntax")
    normalized = path.as_posix()
    if normalized.casefold() == ".git" or normalized.casefold().startswith(".git/"):
        raise MultiError("owned_paths cannot include Git metadata")
    return normalized


def _claim_contains(claim: str, candidate: str) -> bool:
    left = unicodedata.normalize("NFC", claim).casefold().rstrip("/")
    right = unicodedata.normalize("NFC", candidate).casefold().rstrip("/")
    return right == left or right.startswith(left + "/")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MultiError("manifest must be a JSON object")
    return value


def _inside(root: Path, value: Any, *, exists: bool = True) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        raise MultiError("all paths must be absolute")
    path = path.resolve(strict=exists)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MultiError(f"path outside project: {path}") from exc
    return path


def load_manifest(path: Path) -> dict[str, Any]:
    value = _read_json(path.resolve(strict=True))
    schema = value.get("schema")
    if schema not in {SCHEMA, STRICT_SCHEMA}:
        raise MultiError(f"schema must be {SCHEMA} or {STRICT_SCHEMA}")
    strict = schema == STRICT_SCHEMA
    root = Path(str(value.get("project_root") or "")).expanduser().resolve(strict=True)
    output_dir = _inside(root, value.get("output_dir"), exists=False)
    allowed_worktrees = []
    for raw in value.get("allowed_worktree_roots") or []:
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            raise MultiError("allowed worktree roots must be absolute")
        allowed_worktrees.append(candidate.resolve(strict=True))
    solvers = value.get("solvers")
    maximum_solvers = 5 if strict else 25
    if not isinstance(solvers, list) or not 2 <= len(solvers) <= maximum_solvers:
        raise MultiError(f"solvers must contain 2..{maximum_solvers} lanes")
    normalized = []
    seen = set()
    for index, item in enumerate(solvers):
        if not isinstance(item, dict):
            raise MultiError("each solver must be an object")
        lane = str(item.get("id") or f"solver-{index}").strip()
        if LANE_RE.fullmatch(lane) is None or lane in seen:
            raise MultiError("solver ids must be unique")
        seen.add(lane)
        access = str(item.get("access") or "read-only")
        if access not in {"read-only", "worktree-write"}:
            raise MultiError("solver access must be read-only or worktree-write")
        if strict and access != "worktree-write":
            raise MultiError("strict Multi v2 requires worktree-write for every solver")
        lane_root = Path(str(item.get("project_root") or root)).expanduser().resolve(strict=True)
        if lane_root != root and lane_root not in allowed_worktrees:
            raise MultiError("external worktree root must be explicitly allowed")
        owned_paths: list[str] = []
        raw_owned_paths = item.get("owned_paths") or []
        if not isinstance(raw_owned_paths, list):
            raise MultiError("solver owned_paths must be an array")
        if strict:
            owned_paths = [_normalized_relative(raw) for raw in raw_owned_paths]
            if not owned_paths:
                raise MultiError("strict worktree-write requires nonempty owned_paths")
            for claim_index, claim in enumerate(owned_paths):
                for other in owned_paths[claim_index + 1 :]:
                    if _claim_contains(claim, other) or _claim_contains(other, claim):
                        raise MultiError("owned_paths inside one solver must not duplicate or overlap")
            mission_path = _inside(root, item.get("mission_path"))
        else:
            if raw_owned_paths:
                raise MultiError("owned_paths require strict Multi v2")
            mission_path = _inside(lane_root, item.get("mission_path"))
        normalized.append({
            "id": lane,
            "mission_path": mission_path,
            "access": access,
            "project_root": lane_root,
            "owned_paths": owned_paths,
        })
    write_roots = [item["project_root"] for item in normalized if item["access"] == "worktree-write"]
    if len(write_roots) != len(set(write_roots)) or any(path == root for path in write_roots):
        raise MultiError("write solvers require distinct pre-created worktree roots")
    if write_roots:
        canonical_common = _git_common_dir(root)
        if any(_git_common_dir(path) != canonical_common for path in write_roots):
            raise MultiError("write solver worktrees must belong to the canonical repository")
    if strict:
        worktree_parent = (output_dir / "worktrees").resolve()
        for lane_root in write_roots:
            try:
                lane_root.relative_to(worktree_parent)
            except ValueError as exc:
                raise MultiError("strict writer worktrees must be inside output_dir/worktrees") from exc
    strict_claims = [
        (item["id"], owned)
        for item in normalized
        if strict
        for owned in item["owned_paths"]
    ]
    for index, (lane_id, owned) in enumerate(strict_claims):
        for other_lane, other_owned in strict_claims[index + 1 :]:
            if lane_id == other_lane:
                continue
            if _claim_contains(owned, other_owned) or _claim_contains(other_owned, owned):
                raise MultiError("strict owned_paths must be pairwise non-overlapping across solvers")
    merger = _inside(root, value.get("merger_mission_path"))
    next_stage_result = (
        _inside(root, value.get("next_stage_result_path"), exists=False)
        if value.get("next_stage_result_path")
        else None
    )
    concurrency = int(value.get("max_concurrency", 5))
    if not 1 <= concurrency <= 5:
        raise MultiError("max_concurrency must be within 1..5")
    if strict and concurrency > 3:
        raise MultiError("strict Multi v2 max_concurrency must be at most 3")
    try:
        app_name = WORKSPACE_CONFIG.normalize_app_name(
            value.get("app_name") or WORKSPACE_CONFIG.configured_app_name()
        )
    except ValueError as exc:
        raise MultiError(str(exc)) from exc
    model = str(value.get("model") or "gpt-5.6").strip()
    if strict:
        if model != "gpt-5.6":
            raise MultiError("strict Multi v2 permits only the regular gpt-5.6 model")
        if value.get("all_lanes_required") is not True or value.get("partial_merge_allowed") is not False:
            raise MultiError("strict Multi v2 requires all_lanes_required=true and partial_merge_allowed=false")
        if next_stage_result is None:
            raise MultiError("strict Multi v2 requires next_stage_result_path")
        for special in [merger, next_stage_result, *(item["mission_path"] for item in normalized)]:
            try:
                special.relative_to(output_dir)
            except ValueError as exc:
                raise MultiError("strict missions and receipt must be inside output_dir") from exc
    return {
        **value,
        "strict": strict,
        "project_root": root,
        "output_dir": output_dir,
        "solvers": normalized,
        "merger_mission_path": merger,
        "next_stage_result_path": next_stage_result,
        "max_concurrency": concurrency,
        "app_name": app_name,
        "model": model,
        "copy_profile": Path(
            str(value.get("copy_profile") or (Path.home() / ".oracle" / "browser-profile"))
        ).expanduser().resolve(),
        "allowed_worktree_roots": allowed_worktrees,
        "manifest_sha256": hashlib.sha256(path.resolve(strict=True).read_bytes()).hexdigest(),
        "manifest_path": path.resolve(strict=True),
        "next_stage_binding": value.get("next_stage_binding") if isinstance(value.get("next_stage_binding"), dict) else {},
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _effective_lane_mission(config: dict[str, Any], lane: dict[str, Any]) -> Path:
    if not config.get("strict") or lane.get("access") != "worktree-write":
        return lane["mission_path"]
    target = lane["project_root"] / ".codex-ultra-missions" / f"{lane['id']}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    owned = "\n".join(f"- {path}" for path in lane["owned_paths"])
    source = lane["mission_path"].read_text(encoding="utf-8").rstrip()
    target.write_text(
        f"{source}\n\n[WEB_MULTI_STRICT_WORKTREE_WRITE_CONTRACT]\n"
        f"lane_id={lane['id']}\n"
        f"canonical_project_root={config['project_root']}\n"
        f"isolated_worktree_root={lane['project_root']}\n"
        "You are one parallel web implementation agent. You may create, edit, or remove only the exact "
        "project-relative owned paths listed below. Do not modify Git refs, the index, branches, worktrees, "
        "or any unlisted path. Other web agents are writing isolated worktrees concurrently. Keep all writes "
        "inside your scope, run only scope-safe checks, and report exact "
        "changed paths and validation results in the handoff.\n"
        f"{owned}\n",
        encoding="utf-8",
    )
    return target


def _child_manifest(config: dict[str, Any], lane: dict[str, Any], parent_id: str) -> Path:
    lane_root = config["output_dir"] / "lanes" / lane["id"]
    manifest = lane_root / "oracle.json"
    provenance = lane_root / "child-provenance.json"
    effective_mission = _effective_lane_mission(config, lane)
    _write_json(provenance, {
        "schema": "codex.chatgpt.oracle-multi-child-provenance/v1",
        "parent_id": parent_id,
        "parent_manifest_path": str(config["manifest_path"]),
        "parent_manifest_sha256": config["manifest_sha256"],
        "project_root": str(lane.get("project_root") or config["project_root"]),
        "canonical_project_root": str(config["project_root"]),
        "lane_id": lane["id"],
        "mission_path": str(effective_mission),
        "mission_sha256": hashlib.sha256(effective_mission.read_bytes()).hexdigest(),
        "source_mission_path": str(lane["mission_path"]),
        "source_mission_sha256": hashlib.sha256(lane["mission_path"].read_bytes()).hexdigest(),
        "access": lane.get("access", "read-only"),
        "owned_paths": list(lane.get("owned_paths") or []),
    })
    _write_json(
        manifest,
        {
            "schema": STATE.SCHEMA,
            "project_root": str(lane.get("project_root") or config["project_root"]),
            "mission_path": str(effective_mission),
            "app_name": config["app_name"],
            "mode": "browser",
            "model": config["model"],
            "model_strategy": "select",
            "thinking_time": "extra-high",
            "copy_profile": str(config["copy_profile"]),
            "research": "off",
            "archive": "auto",
            "parallel_parent_id": parent_id,
            "web_multi_child_provenance_path": str(provenance),
        },
    )
    return manifest


def _git_text(root: Path, *args: str) -> str:
    return str(_git(root, *args).stdout or "").strip()


def _git_zero_paths(root: Path, *args: str) -> list[str]:
    raw = _git(root, *args, text=False).stdout or b""
    return [item.decode("utf-8", errors="strict").replace("\\", "/") for item in raw.split(b"\0") if item]


def _strict_git_identity(root: Path) -> dict[str, str]:
    refs = _git_text(root, "for-each-ref", "--format=%(refname)%00%(objectname)")
    worktrees = _git_text(root, "worktree", "list", "--porcelain")
    return {
        "head": _git_text(root, "rev-parse", "HEAD"),
        "common_dir": str(_git_common_dir(root)),
        "refs_sha256": hashlib.sha256(refs.encode("utf-8")).hexdigest(),
        "worktrees_sha256": hashlib.sha256(worktrees.encode("utf-8")).hexdigest(),
    }


def _strict_worktree_clean(root: Path) -> None:
    changed = [
        path for path in _git_zero_paths(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if ".codex-ultra-missions/" not in path
    ]
    if changed:
        raise MultiError(f"strict writer worktree must be clean before submission: {root}")


def _strict_canonical_clean(config: dict[str, Any]) -> None:
    output_rel = config["output_dir"].relative_to(config["project_root"]).as_posix().casefold().rstrip("/")
    raw = _git_zero_paths(
        config["project_root"], "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    unexpected = []
    for record in raw:
        path = record[3:] if len(record) >= 4 else record
        normalized = path.replace("\\", "/").casefold()
        if normalized == output_rel or normalized.startswith(output_rel + "/"):
            continue
        unexpected.append(path)
    if unexpected:
        raise MultiError("strict Ultra canonical checkout must be clean outside output_dir")


def _strict_preflight(
    config: dict[str, Any], parent_id: str, execute: Callable[..., dict[str, Any]]
) -> dict[str, dict[str, str]]:
    _strict_canonical_clean(config)
    canonical = _strict_git_identity(config["project_root"])
    baselines: dict[str, dict[str, str]] = {}
    manifests: list[Path] = []
    for lane in config["solvers"]:
        _strict_worktree_clean(lane["project_root"])
        identity = _strict_git_identity(lane["project_root"])
        if identity["common_dir"] != canonical["common_dir"] or identity["head"] != canonical["head"]:
            raise MultiError("strict writer roots must be worktrees of the canonical repository at the same HEAD")
        baselines[lane["id"]] = identity
        manifests.append(_child_manifest(config, lane, parent_id))
    # Validate every exact root and child manifest before the first browser is created.
    for manifest in manifests:
        preview = execute(manifest, dry_run=True)
        if not preview.get("ok"):
            raise MultiError("strict all-root preflight failed before writer submission")
    return baselines


def _strict_changed_paths(root: Path) -> list[str]:
    tracked = _git_zero_paths(root, "diff", "--name-only", "-z", "HEAD", "--")
    untracked = _git_zero_paths(root, "ls-files", "--others", "--exclude-standard", "-z")
    values = {
        unicodedata.normalize("NFC", item.replace("\\", "/"))
        for item in [*tracked, *untracked]
        if not item.replace("\\", "/").startswith(".codex-ultra-missions/")
    }
    return sorted(values, key=str.casefold)


def _strict_audit_lane(
    config: dict[str, Any], lane: dict[str, Any], baseline: dict[str, str]
) -> dict[str, Any]:
    root = lane["project_root"]
    current = _strict_git_identity(root)
    if current != baseline:
        raise MultiError(f"lane {lane['id']} changed Git refs, HEAD, or worktree metadata")
    staged = _git_zero_paths(root, "diff", "--cached", "--name-only", "-z", "--")
    if staged:
        raise MultiError(f"lane {lane['id']} changed the Git index")
    changed = _strict_changed_paths(root)
    if not changed:
        raise MultiError(f"lane {lane['id']} produced no owned implementation delta")
    for relative in changed:
        if not any(_claim_contains(claim, relative) for claim in lane["owned_paths"]):
            raise MultiError(f"lane {lane['id']} wrote outside owned_paths: {relative}")
        path = root / Path(relative)
        if path.exists():
            attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
            if path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise MultiError(f"lane {lane['id']} produced a reparse or symlink path: {relative}")
            if not path.is_file():
                raise MultiError(f"lane {lane['id']} produced a non-regular path: {relative}")
    entries = []
    for relative in changed:
        path = root / Path(relative)
        entries.append({
            "path": relative,
            "deleted": not path.exists(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        })
    material = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"changed_paths": changed, "entries": entries, "audit_sha256": hashlib.sha256(material).hexdigest()}


def _strict_apply_audited_lanes(config: dict[str, Any], lanes: list[dict[str, Any]]) -> None:
    expected_entries = [entry for lane in lanes for entry in lane["audit"]["entries"]]
    output_rel = config["output_dir"].relative_to(config["project_root"]).as_posix().casefold().rstrip("/")
    status_records = _git_zero_paths(
        config["project_root"], "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    actual_changed = {
        (record[3:] if len(record) >= 4 else record).replace("\\", "/")
        for record in status_records
        if not (
            (record[3:] if len(record) >= 4 else record).replace("\\", "/").casefold() == output_rel
            or (record[3:] if len(record) >= 4 else record).replace("\\", "/").casefold().startswith(output_rel + "/")
        )
    }
    expected_changed = {str(entry["path"]) for entry in expected_entries}
    already_applied = actual_changed == expected_changed
    if already_applied:
        for entry in expected_entries:
            target = config["project_root"] / Path(str(entry["path"]))
            if entry["deleted"]:
                already_applied = already_applied and not target.exists()
            else:
                already_applied = (
                    already_applied
                    and target.is_file()
                    and not target.is_symlink()
                    and hashlib.sha256(target.read_bytes()).hexdigest() == entry["sha256"]
                )
    if already_applied:
        return
    _strict_canonical_clean(config)
    planned: list[tuple[Path, Path, dict[str, Any]]] = []
    for result in lanes:
        lane = next(item for item in config["solvers"] if item["id"] == result["id"])
        for entry in result["audit"]["entries"]:
            relative = str(entry["path"])
            source = lane["project_root"] / Path(relative)
            target = config["project_root"] / Path(relative)
            if not entry["deleted"]:
                if not source.is_file() or source.is_symlink():
                    raise MultiError(f"audited source is no longer a regular file: {relative}")
                if hashlib.sha256(source.read_bytes()).hexdigest() != entry["sha256"]:
                    raise MultiError(f"audited source changed before canonical apply: {relative}")
            if target.exists() and (target.is_symlink() or not target.is_file()):
                raise MultiError(f"canonical apply target is not a regular file: {relative}")
            planned.append((source, target, entry))

    # Copy all source bytes into an automation-owned staging directory first.
    # If a later canonical mutation fails, restore every prior byte before
    # returning so a failed apply never leaves a half-integrated checkout.
    with tempfile.TemporaryDirectory(prefix="strict-apply-", dir=config["output_dir"]) as raw_stage:
        stage = Path(raw_stage)
        backups: list[tuple[Path, Path | None]] = []
        staged: list[tuple[Path, Path, dict[str, Any]]] = []
        for index, (source, target, entry) in enumerate(planned):
            staged_source = stage / "sources" / str(index)
            if not entry["deleted"]:
                staged_source.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, staged_source)
            backup = None
            if target.is_file():
                backup = stage / "backups" / str(index)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            backups.append((target, backup))
            staged.append((staged_source, target, entry))
        try:
            for staged_source, target, entry in staged:
                if entry["deleted"]:
                    if target.is_file():
                        target.unlink()
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged_source, target)
        except Exception:
            for target, backup in reversed(backups):
                if backup is None:
                    if target.is_file() and not target.is_symlink():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
            raise


def _run_lane(
    config: dict[str, Any],
    lane: dict[str, Any],
    parent_id: str,
    execute: Callable[..., dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    manifest = _child_manifest(config, lane, parent_id)
    result = execute(manifest, dry_run=dry_run)
    output = None
    session_locator = None
    if not dry_run and result.get("run_dir"):
        run_dir = Path(str(result["run_dir"]))
        source = run_dir / "output.md"
        state_path = run_dir / "state.json"
        if state_path.is_file():
            state = _read_json(state_path)
            oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
            session_locator = oracle.get("session_locator")
        if source.is_file() and source.read_bytes().strip():
            output = config["output_dir"] / "handoffs" / f"{lane['id']}.md"
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
    lane_result = {
        "id": lane["id"],
        "ok": bool(result.get("ok")),
        "run_dir": result.get("run_dir"),
        "output_path": str(output) if output else None,
        "session_locator": session_locator,
    }
    if config.get("strict") and lane_result["ok"] and not dry_run:
        try:
            lane_result["audit"] = _strict_audit_lane(
                config, lane, config["_strict_baselines"][lane["id"]]
            )
        except MultiError as exc:
            lane_result["ok"] = False
            lane_result["audit_error"] = str(exc)
    return lane_result


def _merger_transport(
    config: dict[str, Any],
    successful: list[dict[str, Any]],
    parent_id: str,
) -> Path:
    source = config["merger_mission_path"].read_text(encoding="utf-8")
    paths = "\n".join(f"- {item['output_path']}" for item in successful)
    target = config["output_dir"] / "merger" / "mission.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    receipt_line = (
        "\n[NEXT_STAGE_RECEIPT_BINDING]\n"
        f"workflow_id={config['next_stage_binding'].get('workflow_id', '')}\n"
        f"stage={config['next_stage_binding'].get('stage', '')}\n"
        f"attempt_id={parent_id}\n"
        f"input_mission_sha256={config['manifest_sha256']}\n"
        f"Write the bound next-stage receipt to: {config['next_stage_result_path']}\n"
        if config.get("next_stage_result_path")
        else ""
    )
    target.write_text(f"{source.rstrip()}\n\n[INPUT_HANDOFFS]\n{paths}\n{receipt_line}", encoding="utf-8")
    return target


def reconcile_recovered_lanes(manifest_path: Path) -> dict[str, Any]:
    """Rebind durable exact-run outputs to an interrupted parent without submitting.

    This is intentionally a host-only recovery step.  It validates every
    original lane against the persisted parent/lane/mission identity, restores
    stable-order handoffs, and prepares the merger mission.  It never calls the
    Oracle runner and therefore cannot create a replacement conversation.
    """
    config = load_manifest(manifest_path)
    result_path = config["output_dir"] / "result.json"
    result = _read_json(result_path)
    expected_schema = STRICT_RESULT_SCHEMA if config.get("strict") else RESULT_SCHEMA
    if result.get("schema") != expected_schema:
        raise MultiError("existing multi result schema is invalid")
    if config.get("strict") and result.get("manifest_sha256") != config["manifest_sha256"]:
        raise MultiError("strict result manifest identity mismatch")
    parent_id = str(result.get("parent_id") or "").strip()
    if len(parent_id) != 64:
        raise MultiError("existing multi result has no valid parent identity")
    recorded = result.get("lanes")
    if not isinstance(recorded, list):
        raise MultiError("existing multi result has no lane ledger")
    by_id = {str(item.get("id") or ""): item for item in recorded if isinstance(item, dict)}
    expected_ids = [lane["id"] for lane in config["solvers"]]
    if set(by_id) != set(expected_ids) or len(by_id) != len(expected_ids):
        raise MultiError("existing lane ledger does not match the manifest")
    reconciled: list[dict[str, Any]] = []
    strict_baselines = result.get("strict_baselines") if isinstance(result.get("strict_baselines"), dict) else {}
    if config.get("strict"):
        if set(strict_baselines) != set(expected_ids):
            raise MultiError("strict result has no complete pre-submit baseline ledger")
        config["_strict_baselines"] = strict_baselines
    for lane in config["solvers"]:
        prior = by_id[lane["id"]]
        run_dir = Path(str(prior.get("run_dir") or "")).expanduser()
        if not run_dir.is_absolute():
            raise MultiError(f"lane {lane['id']} has no absolute exact run directory")
        run_dir = run_dir.resolve()
        if not STATE.is_within(STATE.oracle_state_root(), run_dir):
            raise MultiError(f"lane {lane['id']} exact run directory is outside Oracle host state")
        state_path = run_dir / "state.json"
        output_path = run_dir / "output.md"
        if not state_path.is_file() or not output_path.is_file() or not output_path.read_bytes().strip():
            raise MultiError(f"lane {lane['id']} has no durable recovered output")
        state = _read_json(state_path)
        mission = state.get("mission") if isinstance(state.get("mission"), dict) else {}
        oracle = state.get("oracle") if isinstance(state.get("oracle"), dict) else {}
        if state.get("run_id") not in {None, run_dir.name}:
            raise MultiError(f"lane {lane['id']} run identity mismatch")
        expected_project_root = lane["project_root"] if config.get("strict") else config["project_root"]
        if Path(str(state.get("project_root") or "")).resolve() != expected_project_root:
            raise MultiError(f"lane {lane['id']} project identity mismatch")
        if state.get("parallel_parent_id") != parent_id:
            raise MultiError(f"lane {lane['id']} parent identity mismatch")
        expected_mission_sha = hashlib.sha256(_effective_lane_mission(config, lane).read_bytes()).hexdigest()
        if mission.get("sha256") != expected_mission_sha:
            raise MultiError(f"lane {lane['id']} mission identity mismatch")
        if state.get("status") != "complete" or state.get("terminal_harvested") is not True:
            raise MultiError(f"lane {lane['id']} is not terminally harvested")
        artifact_sha = hashlib.sha256(output_path.read_bytes()).hexdigest()
        if state.get("artifact_sha256") != artifact_sha:
            raise MultiError(f"lane {lane['id']} durable output hash mismatch")
        prior_locator = str(prior.get("session_locator") or "").strip()
        exact_locator = str(oracle.get("session_locator") or oracle.get("slug") or "").strip()
        if prior_locator and prior_locator != exact_locator:
            raise MultiError(f"lane {lane['id']} exact session identity mismatch")
        handoff = config["output_dir"] / "handoffs" / f"{lane['id']}.md"
        handoff.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output_path, handoff)
        recovered = {
            "id": lane["id"],
            "ok": True,
            "run_dir": str(run_dir),
            "output_path": str(handoff),
            "session_locator": exact_locator,
            "artifact_sha256": artifact_sha,
        }
        if config.get("strict"):
            recovered["audit"] = _strict_audit_lane(config, lane, strict_baselines[lane["id"]])
        reconciled.append(recovered)
    if config.get("strict"):
        _strict_apply_audited_lanes(config, reconciled)
    merger_mission = _merger_transport(config, reconciled, parent_id)
    updated = {
        **result,
        "status": "merger_ready",
        "lanes": reconciled,
        "successful_lane_count": len(reconciled),
        "merger_mission_path": str(merger_mission),
        "recovery_mode": "exact-runs-no-submit",
    }
    _write_json(result_path, updated)
    return {"ok": True, **updated}


def resume_recovered_merger(
    manifest_path: Path,
    *,
    execute: Callable[..., dict[str, Any]] = RUNNER.execute_run,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Submit only the prepared merger after exact child recovery."""
    config = load_manifest(manifest_path)
    result_path = config["output_dir"] / "result.json"
    result = _read_json(result_path)
    expected_schema = STRICT_RESULT_SCHEMA if config.get("strict") else RESULT_SCHEMA
    if result.get("schema") != expected_schema or result.get("status") != "merger_ready":
        raise MultiError("multi result is not ready for merger-only resume")
    if config.get("strict") and result.get("manifest_sha256") != config["manifest_sha256"]:
        raise MultiError("strict result manifest identity mismatch")
    parent_id = str(result.get("parent_id") or "").strip()
    lanes = result.get("lanes")
    if len(parent_id) != 64 or not isinstance(lanes, list) or len(lanes) != len(config["solvers"]):
        raise MultiError("merger-ready result identity is incomplete")
    expected_ids = [lane["id"] for lane in config["solvers"]]
    if [str(lane.get("id") or "") for lane in lanes if isinstance(lane, dict)] != expected_ids:
        raise MultiError("merger-ready lane order does not match the manifest")
    merger_mission = Path(str(result.get("merger_mission_path") or "")).resolve(strict=True)
    expected_merger = (config["output_dir"] / "merger" / "mission.md").resolve(strict=True)
    if merger_mission != expected_merger:
        raise MultiError("merger mission identity mismatch")
    merger_text = merger_mission.read_text(encoding="utf-8")
    last_position = -1
    for lane in lanes:
        output_path = _inside(config["project_root"], lane.get("output_path"))
        artifact_sha = hashlib.sha256(output_path.read_bytes()).hexdigest()
        if lane.get("artifact_sha256") != artifact_sha:
            raise MultiError(f"lane {lane.get('id')} handoff hash mismatch")
        position = merger_text.find(str(output_path), last_position + 1)
        if position < 0:
            raise MultiError(f"lane {lane.get('id')} is absent or out of order in the merger mission")
        last_position = position
    merger_manifest = _child_manifest(
        config,
        {"id": "merger", "mission_path": merger_mission},
        parent_id,
    )
    merger = execute(merger_manifest, dry_run=dry_run)
    previous = [str(item) for item in result.get("prior_merger_run_dirs") or [] if str(item)]
    if result.get("merger_run_dir"):
        previous.append(str(result["merger_run_dir"]))
    updated = {
        **result,
        "status": "complete" if merger.get("ok") else "merger_attention_required",
        "merger_run_dir": merger.get("run_dir"),
        "prior_merger_run_dirs": list(dict.fromkeys(previous)),
    }
    _write_json(result_path, updated)
    return {"ok": bool(merger.get("ok")), **updated}


def run_multi(
    manifest_path: Path,
    *,
    dry_run: bool = False,
    execute: Callable[..., dict[str, Any]] = RUNNER.execute_run,
    parent_lock_held: bool = False,
) -> dict[str, Any]:
    config = load_manifest(manifest_path)
    parent_id = hashlib.sha256(f"{config['project_root']}:{uuid.uuid4().hex}".encode()).hexdigest()
    config["output_dir"].mkdir(parents=True, exist_ok=True)
    result_path = config["output_dir"] / "result.json"
    if config.get("strict") and not dry_run and result_path.exists():
        raise MultiError("STRICT_MULTI_EXISTING_LEDGER_REQUIRES_EXACT_RECOVERY")
    lanes: list[dict[str, Any]] = []
    # The parent owns normal same-project exclusion. Children use the separate
    # parent-scoped launch mutex and may wait concurrently after submission.
    lock = nullcontext() if parent_lock_held else STATE.project_submit_mutex(config["project_root"], timeout_seconds=30)
    with lock:
        if config.get("strict"):
            ledger = {
                "schema": STRICT_RESULT_SCHEMA,
                "status": "preflighting",
                "parent_id": parent_id,
                "manifest_sha256": config["manifest_sha256"],
                "lanes": [{"id": lane["id"], "status": "planned"} for lane in config["solvers"]],
            }
            if not dry_run:
                _write_json(result_path, ledger)
            try:
                config["_strict_baselines"] = _strict_preflight(config, parent_id, execute)
            except Exception:
                if not dry_run:
                    _write_json(result_path, {**ledger, "status": "preflight_failed"})
                raise
            ledger = {**ledger, "strict_baselines": config["_strict_baselines"]}
            if not dry_run:
                _write_json(result_path, ledger)
            if dry_run:
                merger_manifest = _child_manifest(
                    config, {"id": "merger", "mission_path": config["merger_mission_path"]}, parent_id
                )
                merger_preview = execute(merger_manifest, dry_run=True)
                result = {
                    **ledger,
                    "status": "dry-run",
                    "lanes": [{"id": lane["id"], "ok": True} for lane in config["solvers"]],
                    "merger_preview_ok": bool(merger_preview.get("ok")),
                }
                return {"ok": bool(merger_preview.get("ok")), **result}
            _write_json(result_path, {**ledger, "status": "writers_running"})
        for start in range(0, len(config["solvers"]), config["max_concurrency"]):
            wave = config["solvers"][start : start + config["max_concurrency"]]
            with ThreadPoolExecutor(max_workers=len(wave), thread_name_prefix="oracle-multi") as pool:
                futures = [pool.submit(_run_lane, config, lane, parent_id, execute, dry_run) for lane in wave]
                lanes.extend(future.result() for future in as_completed(futures))
        order = {item["id"]: index for index, item in enumerate(config["solvers"])}
        lanes.sort(key=lambda item: order[item["id"]])
        successful = [item for item in lanes if item["ok"] and (dry_run or item["output_path"])]
        if config.get("strict") and len(successful) != len(lanes):
            result = {
                "schema": STRICT_RESULT_SCHEMA,
                "status": "writers_attention_required",
                "parent_id": parent_id,
                "manifest_sha256": config["manifest_sha256"],
                "strict_baselines": config.get("_strict_baselines"),
                "lanes": lanes,
                "successful_lane_count": len(successful),
            }
            _write_json(result_path, result)
            return {"ok": False, **result}
        if not successful:
            result = {"schema": RESULT_SCHEMA, "status": "failed", "parent_id": parent_id, "lanes": lanes}
            _write_json(result_path, result)
            return {"ok": False, **result}
        if config.get("strict"):
            _write_json(result_path, {
                "schema": STRICT_RESULT_SCHEMA,
                "status": "writers_audited",
                "parent_id": parent_id,
                "manifest_sha256": config["manifest_sha256"],
                "strict_baselines": config.get("_strict_baselines"),
                "lanes": lanes,
                "successful_lane_count": len(successful),
            })
            _strict_apply_audited_lanes(config, successful)
            _write_json(result_path, {
                "schema": STRICT_RESULT_SCHEMA,
                "status": "canonical_applied",
                "parent_id": parent_id,
                "manifest_sha256": config["manifest_sha256"],
                "strict_baselines": config.get("_strict_baselines"),
                "lanes": lanes,
                "successful_lane_count": len(successful),
            })
        merger_mission = _merger_transport(config, successful, parent_id) if not dry_run else config["merger_mission_path"]
        merger_manifest = _child_manifest(
            config,
            {"id": "merger", "mission_path": merger_mission},
            parent_id,
        )
        if config.get("strict"):
            _write_json(result_path, {
                "schema": STRICT_RESULT_SCHEMA,
                "status": "merger_submitting",
                "parent_id": parent_id,
                "manifest_sha256": config["manifest_sha256"],
                "strict_baselines": config.get("_strict_baselines"),
                "lanes": lanes,
                "merger_mission_path": str(merger_mission),
                "merger_mission_sha256": hashlib.sha256(merger_mission.read_bytes()).hexdigest(),
            })
        merger = execute(merger_manifest, dry_run=dry_run)
    status = "complete" if merger.get("ok") and len(successful) == len(lanes) else (
        "partial" if merger.get("ok") else "failed"
    )
    result = {
        "schema": STRICT_RESULT_SCHEMA if config.get("strict") else RESULT_SCHEMA,
        "status": status,
        "parent_id": parent_id,
        "manifest_sha256": config["manifest_sha256"],
        "strict_baselines": config.get("_strict_baselines") if config.get("strict") else None,
        "lanes": lanes,
        "merger_run_dir": merger.get("run_dir"),
        "merger_mission_path": str(merger_mission),
        "merger_mission_sha256": hashlib.sha256(merger_mission.read_bytes()).hexdigest(),
        "successful_lane_count": len(successful),
        "next_stage_result_path": (
            str(config["next_stage_result_path"])
            if config.get("next_stage_result_path") and config["next_stage_result_path"].is_file()
            else None
        ),
    }
    _write_json(result_path, result)
    return {"ok": status == "complete" if config.get("strict") else status in {"complete", "partial"}, **result}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run independent Oracle browser sessions in waves and merge handoffs.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reconcile-recovered", action="store_true")
    parser.add_argument("--resume-merger", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.reconcile_recovered and args.resume_merger:
            raise MultiError("choose exactly one recovery action")
        if args.reconcile_recovered:
            if args.dry_run:
                raise MultiError("--reconcile-recovered cannot be combined with --dry-run")
            result = reconcile_recovered_lanes(args.manifest)
        elif args.resume_merger:
            result = resume_recovered_merger(args.manifest, dry_run=args.dry_run)
        else:
            result = run_multi(args.manifest, dry_run=args.dry_run)
    except Exception as exc:
        result = {"ok": False, "error": {"code": "ORACLE_MULTI_FAILED", "message": str(exc)}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
