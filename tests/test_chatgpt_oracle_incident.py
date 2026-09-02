from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_incident.py"
DEFAULT_EVALUATOR = "99999999-9999-4999-8999-999999999999"


@pytest.fixture(autouse=True)
def _exact_evaluating_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_THREAD_ID", DEFAULT_EVALUATOR)


def load():
    name = "chatgpt_oracle_incident_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_run(
    root: Path,
    run_id: str,
    *,
    status: str,
    stdout: str = "",
    output: str | None = None,
    session_authority: str = "",
    terminal_harvested: bool = False,
    source_thread_id: str | None = None,
) -> Path:
    run_dir = root / "projects" / "projectkey" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    project_root = root / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "output.md"
    if output is not None:
        output_path.write_text(output, encoding="utf-8")
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    transcript_path = run_dir / "transcript.md"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    transcript_path.write_text(output or stdout, encoding="utf-8")
    state = {
        "schema": "codex.chatgpt.oracle-run-state/v1",
        "status": status,
        "run_id": run_id,
        "project_root": str(project_root),
        "session_authority": session_authority,
        "terminal_harvested": terminal_harvested,
        "task_outcome": "blocked" if output and "TASK_OUTCOME: BLOCKED" in output else "",
        "artifacts": {"output": str(output_path), "transcript": str(transcript_path), "stdout": str(stdout_path), "stderr": str(stderr_path)},
        "oracle": {"slug": "oracle-project-abc", "conversation_url": "https://chatgpt.com/c/exact"},
    }
    if source_thread_id is not None:
        state["ownership"] = {
            "schema": "codex.chatgpt.oracle-task-owner/v1",
            "source_thread_id": source_thread_id,
        }
        state["originating_task"] = {
            "schema": "codex.chatgpt.oracle-task-owner/v1",
            "source_thread_id": source_thread_id,
        }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return run_dir


def write_project_session_conflict(
    root: Path,
    run_id: str,
    *,
    source_thread_id: str,
) -> Path:
    run_dir = root / "projects" / "projectkey" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    project_root = root / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    mission_path = run_dir / "mission.md"
    mission_path.write_text("read only", encoding="utf-8")
    mission_sha256 = hashlib.sha256(mission_path.read_bytes()).hexdigest()
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    transcript_path = run_dir / "transcript.md"
    stdout_path.write_bytes(b"")
    error = (
        "Oracle launch/run failed: PROJECT_SESSION_STILL_LIVE: "
        "an exact Oracle session still owns this project; recover it before submitting\n"
    ).encode()
    stderr_path.write_bytes(error)
    transcript_path.write_bytes(error)
    slug = f"oracle-project-{run_id[:10]}"
    state = {
        "schema": "codex.chatgpt.oracle-run-state/v1",
        "status": "failed",
        "run_id": run_id,
        "project_root": str(project_root.resolve()),
        "mode": "browser",
        "transport": "pro-devspace-readonly",
        "transport_status": "prepared",
        "task_outcome": "pending",
        "session_authority": "pre_submit",
        "terminal_harvested": False,
        "artifact_sha256": None,
        "originating_task": {
            "schema": "codex.chatgpt.oracle-task-owner/v1",
            "source_thread_id": source_thread_id,
            "binding": "bound",
        },
        "ownership": {
            "schema": "codex.chatgpt.oracle-ownership/v1",
            "source_thread_id": source_thread_id,
            "binding": "bound",
            "project_root_sha256": hashlib.sha256(
                str(project_root.resolve()).casefold().encode("utf-8")
            ).hexdigest(),
            "run_id": run_id,
            "mission_sha256": mission_sha256,
            "slug": slug,
        },
        "mission": {
            "path": str(mission_path),
            "transport_path": str(mission_path),
            "sha256": mission_sha256,
        },
        "oracle": {
            "resolved_version": "0.17.1",
            "command": ["npx.cmd", "-y", "@steipete/oracle@0.17.1"],
            "slug": slug,
            "session_locator": slug,
        },
        "provider_session": {
            "schema": "codex.chatgpt.oracle-provider-session/v1",
            "status": "unobserved",
            "terminal_confirmed": False,
            "binding": "none",
            "reason": "oracle-runtime-not-yet-observed",
        },
        "browser_identity": {
            "schema": "codex.chatgpt.oracle-browser-identity/v1",
            "expected_cdp_port": 43102,
            "receipt_path": None,
            "receipt_sha256": None,
        },
        "artifacts": {
            "output": str(run_dir / "output.md"),
            "transcript": str(transcript_path),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "browser_temp": str(run_dir / "browser-temp"),
        },
    }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return run_dir


def test_packet_carries_exact_run_bucket_and_evidence(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(
        tmp_path,
        "a" * 8,
        status="failed",
        stdout="ERROR: ChatGPT app mention was not confirmed in the composer.\n",
        source_thread_id=DEFAULT_EVALUATOR,
    )

    packet = module.validate_packet(module.build_packet(run_dir))

    assert packet["schema"] == "codex.chatgpt.oracle-incident/v2"
    assert packet["run_dir"] == str(run_dir.resolve())
    assert packet["bucket"] == "pre-submit-ui-contract"
    assert packet["signature"] == "app-mention-not-confirmed"
    assert packet["conversation_url"] == "https://chatgpt.com/c/exact"
    assert packet["evaluated_from_thread"] == DEFAULT_EVALUATOR
    assert packet["target_source_thread_id"] == DEFAULT_EVALUATOR
    # A classifier label alone cannot authorize a replacement: this fixture
    # deliberately has no durable exact pre-submit state proof.
    assert packet["safe_for_fresh_run"] is False
    assert packet["fresh_run_authority"] is None
    assert str(run_dir / "state.json") in packet["evidence_paths"]


def test_bound_packet_routes_only_to_exact_owner_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load()
    owner = "11111111-1111-4111-8111-111111111111"
    foreign = "22222222-2222-4222-8222-222222222222"
    run_dir = write_run(
        tmp_path,
        "owned-run",
        status="running",
        session_authority="submitted_unknown",
        source_thread_id=owner,
    )
    monkeypatch.setenv("CODEX_THREAD_ID", foreign)

    packet = module.validate_packet(module.build_packet(run_dir))

    assert packet["run_owner_source_thread_id"] == owner
    assert packet["evaluated_from_thread"] == foreign
    assert packet["target_source_thread_id"] == owner
    assert packet["ownership_scope"] == "foreign-task"
    assert packet["operational_instruction"] == {
        "schema": "codex.chatgpt.oracle-operational-instruction/v1",
        "evaluated_from_thread": foreign,
        "target_source_thread_id": owner,
        "ownership_scope": "foreign-task",
        "run_id": "owned-run",
        "slug": "oracle-project-abc",
        "action": "route-to-owner-task",
        "reason": "foreign-task-must-not-operate-on-exact-run",
        "executable_by_evaluated_thread": False,
        "fresh_state_check_required": False,
    }


def test_ambiguous_submitted_unknown_pre_submit_bucket_never_authorizes_replacement(
    tmp_path: Path,
) -> None:
    module = load()
    run_dir = write_run(
        tmp_path,
        "ambiguous-run",
        status="attention_required",
        session_authority="submitted_unknown",
        stdout="ERROR: ChatGPT app mention was not confirmed in the composer.\n",
        source_thread_id=DEFAULT_EVALUATOR,
    )

    packet = module.validate_packet(module.build_packet(run_dir))

    assert packet["bucket"] == module.DIAGNOSE.PRE_SUBMIT_UI
    assert packet["safe_for_fresh_run"] is False
    assert packet["fresh_run_authority"] is None


def test_v2_build_requires_exact_evaluating_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load()
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    run_dir = write_run(tmp_path, "unscoped", status="failed", stdout="ERROR: unknown\n")

    with pytest.raises(module.IncidentError) as exc:
        module.build_packet(run_dir)
    assert exc.value.code == "INCIDENT_EVALUATED_FROM_THREAD_REQUIRED"


def test_legacy_v1_packet_is_evidence_only(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(tmp_path, "legacy", status="failed", stdout="ERROR: unknown\n")
    packet = module.build_packet(run_dir)
    packet["schema"] = module.LEGACY_SCHEMA
    for field in module.V2_REQUIRED_FIELDS:
        packet.pop(field, None)
    packet.pop("run_owner_source_thread_id", None)

    assert module.validate_packet(packet)["schema"] == module.LEGACY_SCHEMA
    packet["operational_instruction"] = {"action": "recover"}
    with pytest.raises(module.IncidentError) as exc:
        module.validate_packet(packet)
    assert exc.value.code == "INCIDENT_LEGACY_OPERATION_FORBIDDEN"


def test_terminal_packet_never_emits_recovery_instruction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load()
    owner = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setenv("CODEX_THREAD_ID", owner)
    run_dir = write_run(
        tmp_path,
        "terminal-run",
        status="attention_required",
        output="answer\nTASK_OUTCOME: EXECUTED\n",
        session_authority="terminal",
        terminal_harvested=True,
        source_thread_id=owner,
    )

    packet = module.validate_packet(module.build_packet(run_dir))

    assert packet["lifecycle"] == "complete"
    assert packet["ownership_scope"] == "same-task"
    assert packet["operational_instruction"]["target_source_thread_id"] == owner
    assert packet["operational_instruction"]["action"] == "none"
    assert packet["operational_instruction"]["reason"] == "exact-run-already-terminal"
    assert packet["operational_instruction"]["executable_by_evaluated_thread"] is False
    assert packet["operational_instruction"]["fresh_state_check_required"] is False


def test_foreign_evaluator_gets_no_action_for_terminal_harvested_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load()
    owner = "11111111-1111-4111-8111-111111111111"
    foreign = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setenv("CODEX_THREAD_ID", foreign)
    run_dir = write_run(
        tmp_path,
        "foreign-terminal",
        status="attention_required",
        output="answer\nTASK_OUTCOME: EXECUTED\n",
        session_authority="terminal",
        terminal_harvested=True,
        source_thread_id=owner,
    )

    packet = module.validate_packet(module.build_packet(run_dir))

    assert packet["evaluated_from_thread"] == foreign
    assert packet["target_source_thread_id"] == owner
    assert packet["ownership_scope"] == "foreign-task"
    assert packet["lifecycle"] == "complete"
    assert packet["operational_instruction"]["action"] == "none"
    assert packet["operational_instruction"]["executable_by_evaluated_thread"] is False
    assert packet["safe_for_fresh_run"] is False
    assert packet["unresolved_owners"] == []


def test_unresolved_owners_are_evaluated_from_run_owner_task_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load()
    owner = "11111111-1111-4111-8111-111111111111"
    foreign = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setenv("CODEX_THREAD_ID", owner)
    reported = write_run(
        tmp_path,
        "reported",
        status="failed",
        stdout="ERROR: ChatGPT app mention was not confirmed in the composer.\n",
        source_thread_id=owner,
    )
    same_task = write_run(
        tmp_path,
        "same-task",
        status="running",
        session_authority="submitted_unknown",
        source_thread_id=owner,
    )
    write_run(
        tmp_path,
        "foreign-task",
        status="running",
        session_authority="submitted_unknown",
        source_thread_id=foreign,
    )

    packet = module.build_packet(reported)

    assert packet["evaluated_from_thread"] == owner
    assert [item["run_id"] for item in packet["unresolved_owners"]] == [same_task.name]
    assert packet["unresolved_owners"][0]["source_thread_id"] == owner


def test_v2_validation_rejects_cross_task_recovery_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load()
    owner = "11111111-1111-4111-8111-111111111111"
    foreign = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setenv("CODEX_THREAD_ID", foreign)
    run_dir = write_run(
        tmp_path,
        "foreign-owned",
        status="running",
        session_authority="submitted_unknown",
        source_thread_id=owner,
    )
    packet = module.build_packet(run_dir)
    packet["operational_instruction"]["action"] = "recover --action live"

    with pytest.raises(module.IncidentError) as exc:
        module.validate_packet(packet)
    assert exc.value.code == "INCIDENT_OPERATIONAL_ACTION_INVALID"


def test_v2_validation_rejects_unknown_ownership_scope(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(tmp_path, "unknown-scope", status="failed", stdout="ERROR: unknown\n")
    packet = module.build_packet(run_dir)
    packet["ownership_scope"] = "project-wide"
    packet["operational_instruction"]["ownership_scope"] = "project-wide"

    with pytest.raises(module.IncidentError) as exc:
        module.validate_packet(packet)
    assert exc.value.code == "INCIDENT_OWNERSHIP_SCOPE_INVALID"


@pytest.mark.parametrize(
    ("owner", "scope"),
    [
        (None, "foreign-task"),
        ("11111111-1111-4111-8111-111111111111", "legacy-unbound"),
    ],
)
def test_v2_validation_rejects_impossible_owner_scope_pairs(
    tmp_path: Path, owner: str | None, scope: str
) -> None:
    module = load()
    run_dir = write_run(
        tmp_path,
        "bad-owner-scope",
        status="failed",
        stdout="ERROR: unknown\n",
        source_thread_id=owner,
    )
    packet = module.build_packet(run_dir)
    packet["ownership_scope"] = scope
    packet["operational_instruction"]["ownership_scope"] = scope

    with pytest.raises(module.IncidentError) as exc:
        module.validate_packet(packet)
    assert exc.value.code == "INCIDENT_OWNERSHIP_SCOPE_INVALID"


def test_version_resolution_label_without_durable_pre_submit_proof_is_not_safe_to_retry(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(
        tmp_path,
        "v" * 8,
        status="attention_required",
        source_thread_id=DEFAULT_EVALUATOR,
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pre_submit_failure"] = {
        "code": "ORACLE_VERSION_RESOLUTION_PRELAUNCH_FAILED",
        "output_absent": True,
        "conversation_url_absent": True,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    packet = module.build_packet(run_dir)

    assert packet["bucket"] == "pre-submit-host-environment"
    assert packet["signature"] == "oracle-version-resolution-prelaunch-timeout"
    assert packet["safe_for_fresh_run"] is False
    assert packet["fresh_run_authority"] is None


def test_model_switcher_label_without_durable_pre_submit_proof_is_not_safe_to_retry(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(
        tmp_path,
        "m" * 8,
        status="attention_required",
        source_thread_id=DEFAULT_EVALUATOR,
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["session_authority"] = "pre_submit"
    state["pre_submit_failure"] = {
        "code": "ORACLE_MODEL_SWITCHER_PRE_SUBMIT_FAILED",
        "output_absent": True,
        "conversation_url_absent": True,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    packet = module.build_packet(run_dir)

    assert packet["bucket"] == "pre-submit-ui-contract"
    assert packet["signature"] == "model-option-label-missing"
    assert packet["safe_for_fresh_run"] is False
    assert packet["fresh_run_authority"] is None


def test_version_compatibility_drift_incident_is_safe_to_retry(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(
        tmp_path,
        "c" * 8,
        status="failed",
        source_thread_id=DEFAULT_EVALUATOR,
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["oracle"] = {"resolved_version": "unresolved"}
    state["session_authority"] = "pre_submit"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (run_dir / "stderr.log").write_text(
        "version resolution failed: Oracle compatibility is validated only for the tested version\n",
        encoding="utf-8",
    )

    packet = module.build_packet(run_dir)

    assert packet["bucket"] == "pre-submit-host-environment"
    assert packet["signature"] == "oracle-version-resolution-prelaunch-compatibility-drift"
    assert packet["safe_for_fresh_run"] is True


def test_packet_never_marks_fresh_run_safe_while_another_session_owns_project(tmp_path: Path) -> None:
    module = load()
    failed = write_run(
        tmp_path,
        "1" * 8,
        status="failed",
        stdout="ERROR: ChatGPT app mention was not confirmed in the composer.\n",
        source_thread_id=DEFAULT_EVALUATOR,
    )
    owner = write_run(
        tmp_path,
        "2" * 8,
        status="running",
        session_authority="submitted_unknown",
        source_thread_id=DEFAULT_EVALUATOR,
    )

    packet = module.build_packet(failed)

    assert packet["safe_for_fresh_run"] is False
    assert [item["run_id"] for item in packet["unresolved_owners"]] == [owner.name]


def test_settled_project_session_conflict_is_safe_only_for_the_exact_owner_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load()
    owner = "11111111-1111-4111-8111-111111111111"
    foreign = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setenv("CODEX_THREAD_ID", owner)
    run_dir = write_project_session_conflict(
        tmp_path,
        "ownership-blocked",
        source_thread_id=owner,
    )

    owner_packet = module.validate_packet(module.build_packet(run_dir))

    assert owner_packet["bucket"] == "submission-ownership-conflict"
    assert owner_packet["signature"] == "same-task-project-session-still-live"
    assert owner_packet["safe_for_fresh_run"] is True
    assert owner_packet["fresh_run_authority"]["code"] == (
        "PROJECT_SESSION_STILL_LIVE_PRELAUNCH_FAILED"
    )
    assert owner_packet["unresolved_owners"] == []

    monkeypatch.setenv("CODEX_THREAD_ID", foreign)
    foreign_packet = module.validate_packet(module.build_packet(run_dir))

    assert foreign_packet["ownership_scope"] == "foreign-task"
    assert foreign_packet["safe_for_fresh_run"] is False
    assert foreign_packet["operational_instruction"]["action"] == "route-to-owner-task"


def test_reporter_is_never_the_repair_owner(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(tmp_path, "b" * 8, status="failed", stdout="ERROR: unknown\n")

    packet = module.build_packet(run_dir)

    assert packet["reporter_role"] == module.REPORTER_ROLE
    assert packet["repair_owner"] == module.MAINTENANCE_OWNER
    assert packet["reporter_may_edit_automation_sources"] is False


def test_packet_claiming_reporter_repair_rights_is_rejected(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(tmp_path, "c" * 8, status="failed", stdout="ERROR: unknown\n")
    packet = module.build_packet(run_dir)

    packet["reporter_may_edit_automation_sources"] = True
    with pytest.raises(module.IncidentError) as exc:
        module.validate_packet(packet)
    assert exc.value.code == "INCIDENT_REPORTER_SCOPE_INVALID"


def test_packet_reassigning_the_repair_owner_is_rejected(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(tmp_path, "d" * 8, status="failed", stdout="ERROR: unknown\n")
    packet = module.build_packet(run_dir)

    packet["repair_owner"] = "some-other-project-session"
    with pytest.raises(module.IncidentError) as exc:
        module.validate_packet(packet)
    assert exc.value.code == "INCIDENT_REPAIR_OWNER_INVALID"


def test_unclassified_bucket_value_is_rejected(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(tmp_path, "e" * 8, status="failed", stdout="ERROR: unknown\n")
    packet = module.build_packet(run_dir)

    packet["bucket"] = "made-up-bucket"
    with pytest.raises(module.IncidentError) as exc:
        module.validate_packet(packet)
    assert exc.value.code == "INCIDENT_BUCKET_UNKNOWN"


def test_active_run_is_not_marked_safe_for_a_fresh_run(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(
        tmp_path,
        "f" * 8,
        status="running",
        session_authority="submitted_unknown",
        stdout="status=response streaming\n",
    )

    packet = module.build_packet(run_dir)

    assert packet["lifecycle"] == "running"
    assert packet["safe_for_fresh_run"] is False


def test_recursive_self_observation_needs_append_only_user_authority(tmp_path: Path) -> None:
    module = load()
    run_id = "recursive1234"
    slug = "oracle-project-abc"
    output = (
        f"run ID: {run_id}\nexact slug: {slug}\nstatus: running\n"
        "task_outcome: pending\noutput.md absent\n"
        "continue-observing-same-exact-session\nTASK_OUTCOME: BLOCKED\n"
    )
    run_dir = write_run(
        tmp_path,
        run_id,
        status="attention_required",
        output=output,
        session_authority="terminal",
        terminal_harvested=True,
        source_thread_id=DEFAULT_EVALUATOR,
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["oracle"]["slug"] = slug
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = module.build_packet(run_dir)
    assert before["signature"] == "post-submit-recursive-self-observation"
    assert before["safe_for_fresh_run"] is False

    receipt_path = run_dir / "settlements" / "recursive-self-observation-fresh-run.json"
    receipt_path.parent.mkdir()
    receipt_path.write_text(json.dumps({
        "schema": module.STATE.RECURSIVE_SELF_OBSERVATION_SETTLEMENT_SCHEMA,
        "confirmation": module.STATE.USER_AUTHORIZED_FRESH_AFTER_RECURSIVE_SELF_OBSERVATION,
        "reason": "user authorized continued progress",
        "run_id": run_id,
        "project_root": state["project_root"],
        "slug": slug,
        "signature": "post-submit-recursive-self-observation",
        "state_sha256": module.STATE.sha256_file(state_path),
        "output_sha256": module.STATE.sha256_file(run_dir / "output.md"),
        "transcript_sha256": module.STATE.sha256_file(run_dir / "transcript.md"),
        "auto_retry": False,
        "submission_action": "none",
        "authorized_at": "2026-08-21T00:00:00Z",
    }), encoding="utf-8")

    after = module.build_packet(run_dir)
    assert after["safe_for_fresh_run"] is True
    assert after["fresh_run_authority"]["sha256"] == module.STATE.sha256_file(receipt_path)


@pytest.mark.parametrize(
    ("failure_kind", "expected_signature"),
    [
        ("checkout-502", "terminal-devspace-checkout-502-no-execution"),
        (
            "app-tools-unavailable",
            "terminal-devspace-app-tools-unavailable-no-execution",
        ),
    ],
)
def test_terminal_devspace_nonexecution_authority_releases_only_the_authorized_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    expected_signature: str,
) -> None:
    module = load()
    task_id = DEFAULT_EVALUATOR
    project_root = tmp_path / "project"
    if failure_kind == "checkout-502":
        output = (
            f"I opened the exact project root {project_root} in checkout mode.\n"
            "The checkout failed with 502 Upstream or external service errors and no workspace ID.\n"
            "I did not read the mission, did not run commands, and did not change files.\n"
            "TASK_OUTCOME: BLOCKED\n"
        )
    else:
        output = (
            "이 세션에는 dev 앱이 제공하는 workspace 도구가 노출되어 있지 않아 "
            f"지정한 {project_root}를 dev checkout 모드로 열 수 없습니다. "
            "사용자가 금지한 다른 workspace 커넥터·셸·웹·Oracle 우회는 시도하지 않았으며, "
            "따라서 미션 파일이나 AGENTS.md도 읽거나 수정하지 않았습니다.\n"
            "TASK_OUTCOME: BLOCKED\n"
        )
    run_dir = write_run(
        tmp_path,
        "devspace502run",
        status="attention_required",
        output=output,
        session_authority="terminal",
        terminal_harvested=True,
    )
    mission = run_dir / "mission.md"
    mission.write_text("review exact project", encoding="utf-8")
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({
        "transport": "pro-devspace",
        "task_outcome_contract": "v1",
        "mission": {"sha256": module.STATE.sha256_file(mission)},
    })
    if failure_kind == "app-tools-unavailable":
        state["app_name"] = "dev"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    before = module.validate_packet(module.build_packet(run_dir))
    assert before["signature"] == expected_signature
    assert before["ownership_scope"] == "legacy-unbound"
    assert before["safe_for_fresh_run"] is False

    receipt_path = run_dir / "settlements" / "terminal-devspace-nonexecution-fresh-run.json"
    receipt_path.parent.mkdir()
    receipt_path.write_text(json.dumps({
        "schema": module.STATE.TERMINAL_DEVSPACE_NONEXECUTION_SETTLEMENT_SCHEMA,
        "confirmation": module.STATE.USER_AUTHORIZED_FRESH_AFTER_TERMINAL_DEVSPACE_NONEXECUTION,
        "reason": "user authorized a new review after repairing the bounded outage",
        "authorized_source_thread_id": task_id,
        "historical_owner_scope": "legacy-unbound",
        "run_id": state["run_id"],
        "project_root": state["project_root"],
        "slug": state["oracle"]["slug"],
        "transport": state["transport"],
        "task_outcome": state["task_outcome"],
        "signature": expected_signature,
        "state_sha256": module.STATE.sha256_file(state_path),
        "output_sha256": module.STATE.sha256_file(run_dir / "output.md"),
        "transcript_sha256": module.STATE.sha256_file(run_dir / "transcript.md"),
        "stdout_sha256": module.STATE.sha256_file(run_dir / "stdout.log"),
        "stderr_sha256": module.STATE.sha256_file(run_dir / "stderr.log"),
        "mission_sha256": module.STATE.sha256_file(mission),
        "auto_retry": False,
        "submission_action": "none",
        "authorized_at": "2026-08-25T00:00:00Z",
    }), encoding="utf-8")

    after = module.validate_packet(module.build_packet(run_dir))

    assert after["safe_for_fresh_run"] is True
    assert after["fresh_run_authority"]["authorized_source_thread_id"] == task_id
    foreign = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setenv("CODEX_THREAD_ID", foreign)
    foreign_packet = module.build_packet(run_dir)
    assert foreign_packet["safe_for_fresh_run"] is False


def test_terminal_devspace_read_route_refresh_authority_releases_one_probe(
    tmp_path: Path,
) -> None:
    module = load()
    task_id = DEFAULT_EVALUATOR
    project_root = tmp_path / "project"
    escaped_root = str(project_root).replace("\\", "\\\\")
    output = (
        "* 앱: `dev`\n"
        "* Workspace ID: `ws_a0770e8338`\n"
        f"* 정확한 루트: `{escaped_root}`\n"
        "* 모드: `checkout`\n"
        "* 적용 `AGENTS.md`: 전체 확인 완료\n"
        "* 미션 파일: 전체 확인 완료\n"
        "* 보고서 첫 Markdown heading: `# Example`\n"
        "* 저장소 쓰기 작업: 없음\n"
        "* 금지된 Oracle controller/run 관련 파일·상태·프로세스: 검사하거나 호출하지 않음\n"
        "현재 `dev` 앱이 이 workspace에서 노출한 도구에 `read_chunk`가 없으며, "
        "`chunk` 관련 도구가 반환되지 않았습니다.\n"
        "따라서 다음 단계인 정확히 한 번의 `git status --short --branch` 명령도 실행하지 않았습니다.\n"
        "* 명령 실행: **안 함**\n"
        "* exit code: **미확인**\n"
        "* command output: **없음**\n"
        "TASK_OUTCOME: BLOCKED\n"
    )
    run_dir = write_run(
        tmp_path,
        "readrouteblocked",
        status="attention_required",
        output=output,
        session_authority="terminal",
        terminal_harvested=True,
        source_thread_id=task_id,
    )
    mission = run_dir / "mission.md"
    mission.write_text(
        "Call `read_chunk` from `offsetBytes=0` through `eof=true`.\n"
        "Run exactly one command: `git status --short --branch`.\n"
        "Run no other command. Do not create, edit, delete, rename, stage, commit, switch, build, or test.\n"
        "If any required operation fails, report the concrete blocker and stop.\n",
        encoding="utf-8",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({
        "transport": "devspace",
        "app_name": "dev",
        "profile": {
            "model": "gpt-5.6",
            "model_strategy": "select",
            "thinking_time": "extra-high",
        },
        "task_outcome_contract": "v1",
        "mission": {"sha256": module.STATE.sha256_file(mission)},
    })
    state_path.write_text(json.dumps(state), encoding="utf-8")

    before = module.validate_packet(module.build_packet(run_dir))
    assert before["signature"] == (
        module.STATE.TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SIGNATURE
    )
    assert before["safe_for_fresh_run"] is False

    receipt_path = (
        run_dir / "settlements" / "terminal-devspace-read-route-refresh-fresh-run.json"
    )
    receipt_path.parent.mkdir()
    receipt_path.write_text(json.dumps({
        "schema": module.STATE.TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SETTLEMENT_SCHEMA,
        "confirmation": module.STATE.USER_AUTHORIZED_FRESH_AFTER_DEVSPACE_READ_ROUTE_REFRESH,
        "reason": "user refreshed the configured app tools and completed post-register",
        "authorized_source_thread_id": task_id,
        "run_id": state["run_id"],
        "project_root": state["project_root"],
        "slug": state["oracle"]["slug"],
        "transport": state["transport"],
        "task_outcome": state["task_outcome"],
        "app_name": state["app_name"],
        "workspace_id": "ws_a0770e8338",
        "signature": module.STATE.TERMINAL_DEVSPACE_READ_ROUTE_REFRESH_SIGNATURE,
        "retry_ordinal": 1,
        "state_sha256": module.STATE.sha256_file(state_path),
        "output_sha256": module.STATE.sha256_file(run_dir / "output.md"),
        "transcript_sha256": module.STATE.sha256_file(run_dir / "transcript.md"),
        "stdout_sha256": module.STATE.sha256_file(run_dir / "stdout.log"),
        "stderr_sha256": module.STATE.sha256_file(run_dir / "stderr.log"),
        "mission_sha256": module.STATE.sha256_file(mission),
        "auto_retry": False,
        "submission_action": "none",
        "authorized_at": "2026-08-25T00:00:00Z",
    }), encoding="utf-8")

    after = module.validate_packet(module.build_packet(run_dir))

    assert after["safe_for_fresh_run"] is True
    assert after["fresh_run_authority"]["authorized_source_thread_id"] == task_id
    assert after["fresh_run_authority"]["retry_ordinal"] == 1


def test_packet_build_requires_the_exact_persisted_run(tmp_path: Path) -> None:
    module = load()
    empty = tmp_path / "no-run"
    empty.mkdir()

    with pytest.raises(module.IncidentError) as exc:
        module.build_packet(empty)
    assert exc.value.code == "INCIDENT_RUN_STATE_MISSING"


def test_missing_layout_packet_is_diagnostic_only_without_durable_run_state(
    tmp_path: Path,
) -> None:
    module = load()
    project_root = tmp_path / "project"
    project_root.mkdir()
    run_dir = tmp_path / "state" / "projects" / "projectkey" / "runs" / ("a" * 32)
    manifest_path = tmp_path / "oracle.json"
    manifest_path.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-run/v1",
        "project_root": str(project_root),
        "run_id": "a" * 32,
    }), encoding="utf-8")
    workflow_state = tmp_path / "workflow-state.json"
    workflow_state.write_text(json.dumps({
        "schema": "codex.chatgpt.oracle-comprehensive-state/v1",
        "status": "attention_required",
        "workflow_id": "b" * 32,
        "source_thread_id": DEFAULT_EVALUATOR,
        "records": [{
            "stage": "plan",
            "run_id": "a" * 32,
            "run_dir": str(run_dir),
            "pre_submit_failure": True,
            "pre_submit_retry_consumed": False,
            "settlement": "oracle-layout-not-created-pre-submit",
            "settlement_proof": {
                "schema": module.MISSING_LAYOUT_PRE_SUBMIT_SCHEMA,
                "kind": "oracle-layout-not-created",
                "safe_for_fresh_run": False,
                "workflow_id": "b" * 32,
                "attempt_id": "a" * 32,
                "run_dir": str(run_dir),
                "oracle_manifest_path": str(manifest_path),
            },
        }],
    }), encoding="utf-8")

    packet = module.validate_packet(module.build_missing_layout_packet(workflow_state))

    assert packet["lifecycle"] == "attention_required"
    assert packet["authority_source"] == "workflow-missing-layout-unproven"
    assert packet["safe_for_fresh_run"] is False
    assert packet["fresh_run_authority"] is None
    assert packet["operational_instruction"]["action"] == "inspect-owned-exact-run"


def test_build_is_read_only_for_the_reported_run(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(
        tmp_path,
        "9" * 8,
        status="failed",
        stdout="ERROR: --copy-profile requires rsync on PATH\n",
    )
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    }

    module.build_packet(run_dir)

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    }
    assert after == before


def test_non_project_reporter_role_is_rejected(tmp_path: Path) -> None:
    module = load()
    run_dir = write_run(tmp_path, "8" * 8, status="failed", stdout="ERROR: unknown\n")

    with pytest.raises(module.IncidentError) as exc:
        module.build_packet(run_dir, reporter_role=module.MAINTENANCE_OWNER)
    assert exc.value.code == "INCIDENT_REPORTER_ROLE_INVALID"
