from __future__ import annotations

import importlib.util
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
SPEC = importlib.util.spec_from_file_location(
    "codex_web_gpt_onboarding_test", ROOT / "bin" / "codex_web_gpt_onboarding.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture(autouse=True)
def isolate_host_devspace_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must never inherit the maintainer host's mutable allowedRoots."""
    original = module._persisted_allowed_roots
    host_default = (Path.home() / ".devspace").resolve()

    def persisted(path: Path) -> tuple[str, ...]:
        return () if path.expanduser().resolve() == host_default else original(path)

    monkeypatch.setattr(module, "_persisted_allowed_roots", persisted)


def test_plan_orders_the_complete_first_install_without_secrets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    plan = module.onboarding_plan(
        provider="tailscale",
        registration_url="https://host.tailnet.ts.net/mcp",
        roots=[str(project)],
    )
    assert plan["product"] == "Codex Web GPT Automation"
    assert plan["app_name"] == "codex"
    assert [stage["id"] for stage in plan["stages"]] == [
        "01_install",
        "02_stable_endpoint",
        "03_devspace_init",
        "04_reboot_service",
        "05_endpoint_check",
        "06_oracle_login",
        "06b_local_network_access",
        "07_chatgpt_app",
        "08_final_gate",
    ]
    dumped = json.dumps(plan)
    assert "owner_token" not in dumped.casefold()
    assert "--browser-manual-login" in dumped
    assert "DEVSPACE_OAUTH_SCOPES" in dumped
    assert plan["stages"][3]["environment"]["DEVSPACE_SUBAGENTS"] == "false"


@pytest.mark.parametrize(
    ("provider", "url", "error"),
    [
        ("tailscale", "https://example.com/mcp", "TAILSCALE_STABLE_TS_NET_URL_REQUIRED"),
        ("cloudflare", "https://random.trycloudflare.com/mcp", "CLOUDFLARE_NAMED_TUNNEL_REQUIRED"),
        ("ngrok", "https://random.ngrok-free.app/mcp", "NGROK_STATIC_DOMAIN_REQUIRED"),
        ("custom", "http://example.com/mcp", "PUBLIC_HTTPS_MCP_URL_REQUIRED"),
        ("custom", "https://example.com/not-mcp", "PUBLIC_MCP_URL_MUST_END_IN_MCP"),
        ("custom", "https://user:secret@example.com/mcp", "PUBLIC_MCP_URL_MUST_NOT_CONTAIN_CREDENTIALS_OR_QUERY"),
    ],
)
def test_unstable_or_unsafe_endpoint_fails_closed(
    tmp_path: Path, provider: str, url: str, error: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(module.OnboardingError, match=error):
        module.onboarding_plan(provider=provider, registration_url=url, roots=[str(project)])


def test_status_requires_exact_root_order_and_bootstrap_match(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    codex_home = tmp_path / ".codex"
    devspace_home = tmp_path / ".devspace"
    (codex_home / "config").mkdir(parents=True)
    devspace_home.mkdir()
    (devspace_home / "config.json").write_text(
        json.dumps({"allowedRoots": [str(project)]}), encoding="utf-8"
    )
    (codex_home / "config" / "codexpro-devspace-bootstrap.json").write_text(
        json.dumps({"roots": [str(project)]}), encoding="utf-8"
    )
    (codex_home / "chatgpt-workspace.json").write_text(
        json.dumps({"app_name": "codex"}), encoding="utf-8"
    )
    healthy = lambda _url: {"ok": True, "status": 401, "expected": 401}
    status = module.readiness_status(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(project)],
        codex_home=codex_home,
        devspace_home=devspace_home,
        http_probe=healthy,
        oracle_profile_dir=tmp_path / "browser-profile",
        local_network_policy_probe=lambda: {"enabled": True},
    )
    assert status["checks"]["exact_roots_configured"] is True
    assert status["checks"]["bootstrap_matches_config"] is True

    child = project / "child"
    child.mkdir()
    mismatch = module.readiness_status(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(child)],
        codex_home=codex_home,
        devspace_home=devspace_home,
        http_probe=healthy,
        oracle_profile_dir=tmp_path / "browser-profile",
        local_network_policy_probe=lambda: {"enabled": True},
    )
    assert mismatch["checks"]["exact_roots_configured"] is False
    assert mismatch["ready"] is False


def test_configure_app_name_is_atomic_and_contains_only_public_name(tmp_path: Path) -> None:
    target = module.configure_app_name(codex_home=tmp_path, app_name="dongju")
    assert json.loads(target.read_text(encoding="utf-8")) == {"app_name": "dongju"}
    assert not list(tmp_path.glob("*.tmp"))


def test_plan_status_and_cli_share_the_same_arbitrary_app_name(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    plan = module.onboarding_plan(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(project)],
        app_name="dongju",
    )
    assert plan["app_name"] == "dongju"
    assert "--app-name dongju" in plan["stages"][-1]["command"]

    codex_home = tmp_path / ".codex"
    devspace_home = tmp_path / ".devspace"
    (codex_home / "config").mkdir(parents=True)
    devspace_home.mkdir()
    (devspace_home / "config.json").write_text(
        json.dumps({"allowedRoots": [str(project)]}), encoding="utf-8"
    )
    (codex_home / "config" / "codexpro-devspace-bootstrap.json").write_text(
        json.dumps({"roots": [str(project)]}), encoding="utf-8"
    )
    module.configure_app_name(codex_home=codex_home, app_name="dongju")
    status = module.readiness_status(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(project)],
        app_name="dongju",
        codex_home=codex_home,
        devspace_home=devspace_home,
        http_probe=lambda _url: {"ok": True, "status": 401},
        oracle_profile_dir=tmp_path / "browser-profile",
        local_network_policy_probe=lambda: {"enabled": True},
    )
    assert status["checks"]["app_name_matches_expected"] is True
    assert status["expected_app_name"] == "dongju"


def test_status_fails_closed_without_persistent_local_network_grant(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    codex_home = tmp_path / ".codex"
    devspace_home = tmp_path / ".devspace"
    profile = tmp_path / "browser-profile"
    (codex_home / "config").mkdir(parents=True)
    devspace_home.mkdir()
    profile.mkdir()
    (profile / "marker").write_text("signed-in", encoding="utf-8")
    (devspace_home / "config.json").write_text(json.dumps({"allowedRoots": [str(project)]}))
    (codex_home / "config" / "codexpro-devspace-bootstrap.json").write_text(
        json.dumps({"roots": [str(project)]})
    )
    (codex_home / "chatgpt-workspace.json").write_text(json.dumps({"app_name": "codex"}))
    status = module.readiness_status(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(project)],
        codex_home=codex_home,
        devspace_home=devspace_home,
        http_probe=lambda _url: {"ok": True, "status": 401},
        oracle_profile_dir=profile,
        local_network_policy_probe=lambda: {"enabled": False},
    )
    assert status["checks"]["oracle_profile_initialized"] is True
    assert status["checks"]["chatgpt_local_network_allowed"] is False
    assert status["ready"] is False


def test_seed_profile_local_network_grant_is_accepted(tmp_path: Path) -> None:
    profile = tmp_path / "browser-profile"
    preferences = profile / "Default" / "Preferences"
    preferences.parent.mkdir(parents=True)
    preferences.write_text(
        json.dumps(
            {
                "profile": {
                    "content_settings": {
                        "exceptions": {
                            "local_network": {
                                "https://chatgpt.com:443,*": {"setting": 1}
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert module.browser_profile_local_network_allowed(profile) is True
    assert module.browser_profile_local_network_allowed(tmp_path / "missing") is False


@pytest.mark.parametrize("value", ["", "@dongju", "bad/name", "bad\\name", "bad\nname"])
def test_app_name_validation_fails_closed(value: str) -> None:
    with pytest.raises(module.OnboardingError, match="APP_NAME_INVALID"):
        module.normalize_app_name(value)


def _wizard_environment(tmp_path: Path, *, ready: bool) -> dict[str, object]:
    project = tmp_path / "project"
    project.mkdir()
    codex_home = tmp_path / ".codex"
    devspace_home = tmp_path / ".devspace"
    profile = tmp_path / "browser-profile"
    (codex_home / "config").mkdir(parents=True)
    (codex_home / "receipts").mkdir(parents=True)
    devspace_home.mkdir()
    profile.mkdir()
    (profile / "marker").write_text("signed-in", encoding="utf-8")
    (codex_home / "receipts" / "codexpro-automation-1.json").write_text("{}", encoding="utf-8")
    if ready:
        (devspace_home / "config.json").write_text(
            json.dumps({"allowedRoots": [str(project)]}), encoding="utf-8"
        )
        (codex_home / "config" / "codexpro-devspace-bootstrap.json").write_text(
            json.dumps({"roots": [str(project)]}), encoding="utf-8"
        )
        (codex_home / "chatgpt-workspace.json").write_text(
            json.dumps({"app_name": "codex"}), encoding="utf-8"
        )
    return {
        "project": project,
        "codex_home": codex_home,
        "devspace_home": devspace_home,
        "profile": profile,
        "probes": {
            "http_probe": (lambda _url: {"ok": True, "status": 401}) if ready else (lambda _url: {"ok": False, "status": None}),
            "oracle_profile_dir": profile,
            "local_network_policy_probe": (lambda: {"enabled": bool(ready)}),
        },
    }


def _confirm_ready_manual_stages(
    environment: dict[str, object],
    stages: tuple[str, ...] = (
        "02_stable_endpoint",
        "04_reboot_service",
        "06_oracle_login",
        "07_chatgpt_app",
    ),
) -> None:
    state = module.load_state(codex_home=environment["codex_home"])
    roots = state["allowed_roots"]
    Path(environment["devspace_home"]).joinpath("config.json").write_text(
        json.dumps({"allowedRoots": roots}), encoding="utf-8"
    )
    bootstrap = Path(environment["codex_home"]) / "config" / "codexpro-devspace-bootstrap.json"
    bootstrap.write_text(json.dumps({"roots": roots}), encoding="utf-8")
    for stage_id in stages:
        result = module.confirm_stage(
            stage_id,
            codex_home=environment["codex_home"],
            devspace_home=environment["devspace_home"],
            **environment["probes"],
        )
        assert result["accepted"] is True, result


def _bound_final_gate_run(
    environment: dict[str, object],
    listing: list[str],
    *,
    registered_app_final_gate: bool = False,
    source_thread_id: str | None = None,
) -> Path:
    codex_home = Path(environment["codex_home"])
    project = Path(environment["project"])
    run_dir = codex_home / "state" / "chatgpt-oracle" / "projects" / "test" / "runs" / ("f" * 32)
    run_dir.mkdir(parents=True, exist_ok=True)
    mission_path = project / "missions" / "onboarding-final-gate.md"
    mission_path.parent.mkdir(parents=True, exist_ok=True)
    mission_path.write_text("# Final gate cryptographic read challenge\nnonce: 4d3a8f1c\n", encoding="utf-8")
    mission_sha256 = hashlib.sha256(mission_path.read_bytes()).hexdigest()
    output = run_dir / "output.md"
    output.write_text(
        f"App codex opened and separately read workspace. Listed: {', '.join(listing)}\n"
        "Audit receipts: 00000000-0000-4000-8000-000000000001 "
        "00000000-0000-4000-8000-000000000002 "
        "00000000-0000-4000-8000-000000000003\n"
        "TASK_OUTCOME: EXECUTED\n",
        encoding="utf-8",
    )
    output_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    state = {
        "schema": "codex.chatgpt.oracle-run-state/v1",
        "run_id": "f" * 32,
        "project_root": str(project.resolve()),
        "transport": "devspace",
        "app_name": "codex",
        "profile": {"model": "gpt-5.6", "thinking_time": "extra-high"},
        "status": "complete",
        "transport_status": "complete",
        "session_authority": "terminal",
        "terminal_harvested": True,
        "task_outcome": "executed",
        "artifact_sha256": output_sha256,
        "mission": {"path": str(mission_path.resolve()), "sha256": mission_sha256},
        "oracle": {
            "slug": "oracle-onboarding-final",
            "conversation_url": "https://chatgpt.com/c/onboarding-final-test",
        },
        "artifacts": {"output": str(output.resolve())},
        **({"registered_app_final_gate": True} if registered_app_final_gate else {}),
        **(
            {
                "ownership": {
                    "schema": "codex.chatgpt.oracle-ownership/v1",
                    "source_thread_id": source_thread_id,
                }
            }
            if source_thread_id
            else {}
        ),
    }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    receipt_root = Path(environment["devspace_home"]) / "state" / "tool-read-receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    common = {
        "schema": "codex.devspace.tool-read-receipt/v1",
        "auditNonce": state["run_id"],
        "workspaceId": "ws_onboarding_test",
        "canonicalRoot": str(project.resolve()),
        "conversationScopeId": "v1/opaque-openai-session-scope",
    }
    receipts = (
        ("00000000-0000-4000-8000-000000000001", "open_workspace", None, None, None, None, None, None, "2026-08-22T00:00:01Z"),
        ("00000000-0000-4000-8000-000000000002", "read", "missions/onboarding-final-gate.md", None, None, None, None, None, "2026-08-22T00:00:02Z"),
        ("00000000-0000-4000-8000-000000000003", "read_chunk", "missions/onboarding-final-gate.md", mission_sha256, 0, mission_path.stat().st_size, mission_path.stat().st_size, True, "2026-08-22T00:00:03Z"),
    )
    for audit_step, (receipt_id, tool, requested_path, chunk_sha256, offset, returned, total, eof, timestamp) in enumerate(receipts, 1):
        payload = {
            **common,
            "receiptId": receipt_id,
            "auditStep": audit_step,
            "tool": tool,
            "requestedRelativePath": requested_path,
            "readChunkSha256": chunk_sha256,
            "readChunkOffsetBytes": offset,
            "readChunkBytesReturned": returned,
            "readChunkTotalBytes": total,
            "readChunkEof": eof,
            "timestamp": timestamp,
        }
        (receipt_root / f"{receipt_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    return run_dir


def _rewrite_tool_read_receipt(receipt: Path, **updates: object) -> None:
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload.update(updates)
    receipt.write_text(json.dumps(payload), encoding="utf-8")


def _tail_partial_chunk_receipt(receipts: list[Path]) -> None:
    receipt = receipts[2]
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    _rewrite_tool_read_receipt(
        receipt,
        readChunkOffsetBytes=1,
        readChunkBytesReturned=payload["readChunkTotalBytes"] - 1,
        readChunkEof=True,
    )


def _truncated_chunk_receipt(receipts: list[Path]) -> None:
    receipt = receipts[2]
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    _rewrite_tool_read_receipt(
        receipt,
        readChunkBytesReturned=payload["readChunkTotalBytes"] - 1,
        readChunkEof=True,
    )


def test_start_persists_resumable_state_without_secrets(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    state = module.start_onboarding(
        provider="tailscale",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
        hostname_discovery=lambda: "device.tailnet.ts.net",
    )
    assert state["registration_url"] == "https://device.tailnet.ts.net/mcp"
    assert list(state["stages"]) == list(module.STAGE_IDS)
    persisted = module.state_path(codex_home=environment["codex_home"])
    assert persisted.is_file()
    dumped = persisted.read_text(encoding="utf-8").casefold()
    for banned in ("password", "secret", "token", "cookie"):
        assert banned not in dumped


def test_non_tailscale_start_requires_an_explicit_stable_url(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    with pytest.raises(module.OnboardingError, match="PUBLIC_HTTPS_MCP_URL_REQUIRED"):
        module.start_onboarding(
            provider="custom",
            roots=[str(environment["project"])],
            codex_home=environment["codex_home"],
        )


def test_start_merges_existing_devspace_roots_without_dropping_them(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    existing = tmp_path / "existing-project"
    requested = tmp_path / "new-project"
    existing.mkdir()
    requested.mkdir()
    Path(environment["devspace_home"]).joinpath("config.json").write_text(
        json.dumps({"allowedRoots": [str(existing)]}), encoding="utf-8"
    )

    state = module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(requested)],
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )

    assert state["requested_roots"] == [str(requested.resolve())]
    assert state["allowed_roots"] == [str(existing.resolve()), str(requested.resolve())]


def test_start_rejects_invalid_existing_devspace_json(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    Path(environment["devspace_home"]).joinpath("config.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(module.OnboardingError, match="DEVSPACE_CONFIG_INVALID"):
        module.start_onboarding(
            provider="custom",
            registration_url="https://mcp.example.com/mcp",
            roots=[str(environment["project"])],
            codex_home=environment["codex_home"],
            devspace_home=environment["devspace_home"],
        )


def test_local_multi_gpt_opt_in_is_persisted_and_fail_closed_until_doctor_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    state = module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        enable_local_multi_gpt=True,
    )
    assert state["enable_local_multi_gpt"] is True
    assert "--enable-local-multi-gpt" in "\n".join(module.stage_instructions("01_install", state, "en"))

    evaluated = module.evaluate_stages(
        state,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert evaluated["checks"]["local_multi_gpt_ready"] is False
    assert evaluated["stages"]["01_install"]["verified"] is False

    monkeypatch.setattr(module, "_local_multi_gpt_ready", lambda _home: True)
    evaluated = module.evaluate_stages(
        state,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert evaluated["checks"]["local_multi_gpt_ready"] is True


def test_next_returns_one_stage_and_never_skips_ahead(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert step["done"] is False
    assert step["current_stage"] == "02_stable_endpoint"
    assert step["completion_state"] == "installed"
    assert step["pending_stages"][0] == "02_stable_endpoint"


def test_user_confirmation_alone_cannot_complete_a_stage(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    rejected = module.confirm_stage(
        "07_chatgpt_app",
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert rejected["accepted"] is False
    assert rejected["reason"] == "STAGE_OUT_OF_ORDER_EARLIER_STAGE_PENDING"
    assert rejected["blocking_stage"] in module.STAGE_IDS
    reloaded = module.load_state(codex_home=environment["codex_home"])
    assert reloaded["stages"]["07_chatgpt_app"]["status"] == "pending"


def test_final_gate_requires_recorded_non_pro_exact_root_read(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)
    before = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert before["current_stage"] == "08_final_gate"
    assert before["completion_state"] == "awaiting_verification"

    run_dir = _bound_final_gate_run(environment, ["AGENTS.md"])
    module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="regular oracle listed the exact root",
        listing=["AGENTS.md"],
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    after = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        language="ko",
        **environment["probes"],
    )
    assert after["done"] is True
    assert after["completion_state"] == "verified"
    assert after["completion_label"] == "전체 설치 및 실제 프로젝트 연결 검증 완료"


def test_final_gate_rejects_self_authored_open_output_without_receipts(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    run_dir = _bound_final_gate_run(environment, ["AGENTS.md"])
    output = run_dir / "output.md"
    output.write_text(
        "App codex opened workspace and listed AGENTS.md\n"
        "TASK_OUTCOME: EXECUTED\n",
        encoding="utf-8",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifact_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    state_path.write_text(json.dumps(state), encoding="utf-8")
    for receipt in (Path(environment["devspace_home"]) / "state" / "tool-read-receipts").glob("*.json"):
        receipt.unlink()
    with pytest.raises(module.OnboardingError, match="FINAL_GATE_TOOL_READ_RECEIPTS_MISSING_OR_DUPLICATE"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence="regular oracle opened but did not separately read",
            listing=["AGENTS.md"],
            run_dir=run_dir,
            codex_home=environment["codex_home"],
            devspace_home=environment["devspace_home"],
        )


def test_final_gate_accepts_valid_receipts_with_server_challenge_response(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    run_dir = _bound_final_gate_run(environment, ["AGENTS.md"])
    output = run_dir / "output.md"
    output.write_text(
        "App codex listed AGENTS.md after the connector calls.\n"
        "Audit receipts: 00000000-0000-4000-8000-000000000001 "
        "00000000-0000-4000-8000-000000000002 "
        "00000000-0000-4000-8000-000000000003\n"
        "TASK_OUTCOME: EXECUTED\n",
        encoding="utf-8",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifact_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    state_path.write_text(json.dumps(state), encoding="utf-8")

    recorded = module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="server receipts bind the exact mission read",
        listing=["AGENTS.md"],
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    assert recorded["read_file_sha256"] == hashlib.sha256(
        (Path(environment["project"]) / "missions" / "onboarding-final-gate.md").read_bytes()
    ).hexdigest()


def test_final_gate_rejects_receipts_not_echoed_by_exact_oracle_conversation(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    run_dir = _bound_final_gate_run(environment, ["AGENTS.md"])
    output = run_dir / "output.md"
    output.write_text(
        "App codex listed AGENTS.md but did not echo server challenges.\n"
        "TASK_OUTCOME: EXECUTED\n",
        encoding="utf-8",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifact_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(module.OnboardingError, match="FINAL_GATE_CONVERSATION_RECEIPT_CHALLENGE_MISSING"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence="different conversation cannot satisfy server challenge-response",
            listing=["AGENTS.md"],
            run_dir=run_dir,
            codex_home=environment["codex_home"],
            devspace_home=environment["devspace_home"],
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda receipts: receipts[0].write_text(receipts[0].read_text(encoding="utf-8")[:-1] + ', "unexpected": true}', encoding="utf-8"), "KEYSET_INVALID"),
        (lambda receipts: receipts[0].write_text(receipts[0].read_text(encoding="utf-8")[:-1] + ', "tool": "open_workspace"}', encoding="utf-8"), "INVALID"),
        (lambda receipts: receipts.append(receipts[2].with_name("duplicate.json")) or receipts[-1].write_text(receipts[2].read_text(encoding="utf-8"), encoding="utf-8"), "MISSING_OR_DUPLICATE"),
        (lambda receipts: _rewrite_tool_read_receipt(receipts[1], auditStep=3), "ORDER_INVALID"),
        (lambda receipts: receipts[1].write_text(receipts[1].read_text(encoding="utf-8").replace("ws_onboarding_test", "ws_other"), encoding="utf-8"), "WORKSPACE_MISMATCH"),
        (lambda receipts: receipts[0].write_text(receipts[0].read_text(encoding="utf-8").replace('"canonicalRoot": "', '"canonicalRoot": "C:/wrong-root'), encoding="utf-8"), "ROOT_MISMATCH"),
        (lambda receipts: receipts[0].write_text(receipts[0].read_text(encoding="utf-8").replace("v1/opaque-openai-session-scope", "other-scope"), encoding="utf-8"), "SCOPE_MISMATCH"),
        (lambda receipts: receipts[1].write_text(receipts[1].read_text(encoding="utf-8").replace("missions/onboarding-final-gate.md", "README.md"), encoding="utf-8"), "PATH_MISMATCH"),
        (lambda receipts: receipts[2].write_text(re.sub(r'("readChunkSha256": ")[0-9a-f]+', r'\g<1>' + "0" * 64, receipts[2].read_text(encoding="utf-8")), encoding="utf-8"), "SHA_MISMATCH"),
        (lambda receipts: _rewrite_tool_read_receipt(receipts[0], readChunkOffsetBytes=0), "CHUNK_METADATA_INVALID"),
        (_tail_partial_chunk_receipt, "CHUNK_METADATA_INVALID"),
        (_truncated_chunk_receipt, "CHUNK_METADATA_INVALID"),
        (lambda receipts: receipts[0].write_text(receipts[0].read_text(encoding="utf-8").replace('"auditNonce": "', '"auditNonce": "other-'), encoding="utf-8"), "MISSING_OR_DUPLICATE"),
    ],
)
def test_final_gate_rejects_invalid_tool_read_receipts(
    tmp_path: Path,
    mutation: object,
    error: str,
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    run_dir = _bound_final_gate_run(environment, ["AGENTS.md"])
    receipts = sorted((Path(environment["devspace_home"]) / "state" / "tool-read-receipts").glob("*.json"))
    mutation(receipts)
    with pytest.raises(module.OnboardingError, match=f"FINAL_GATE_TOOL_READ_RECEIPT.*{error}"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence="receipt mutation must block the final gate",
            listing=["AGENTS.md"],
            run_dir=run_dir,
            codex_home=environment["codex_home"],
            devspace_home=environment["devspace_home"],
        )


def test_final_gate_rejects_symlink_tool_read_receipt(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    run_dir = _bound_final_gate_run(environment, ["AGENTS.md"])
    receipt_root = Path(environment["devspace_home"]) / "state" / "tool-read-receipts"
    target = sorted(receipt_root.glob("*.json"))[0]
    target.unlink()
    try:
        target.symlink_to(receipt_root / "00000000-0000-4000-8000-000000000002.json")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(module.OnboardingError, match="FINAL_GATE_TOOL_READ_RECEIPT_SYMLINK_FORBIDDEN"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence="a symlink receipt must not be accepted",
            listing=["AGENTS.md"],
            run_dir=run_dir,
            codex_home=environment["codex_home"],
            devspace_home=environment["devspace_home"],
        )


def test_final_gate_rejects_a_root_outside_the_allowed_list(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(module.OnboardingError, match="FINAL_GATE_ROOT_NOT_IN_ALLOWED_ROOTS"):
        module.record_final_gate(
            read_ok=True,
            root=str(other),
            evidence="wrong root",
            codex_home=environment["codex_home"],
        )


def test_next_before_start_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(module.OnboardingError, match="ONBOARDING_NOT_STARTED"):
        module.next_step(codex_home=tmp_path / ".codex")


def test_chatgpt_stage_exposes_both_ui_paths_and_triage(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    (environment["codex_home"] / "chatgpt-workspace.json").unlink()
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(
        environment,
        stages=("02_stable_endpoint", "04_reboot_service", "06_oracle_login"),
    )
    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        language="ko",
        **environment["probes"],
    )
    assert step["current_stage"] == "07_chatgpt_app"
    assert step["needs_user_action"] is True
    assert step["confirm_command"] == "onboard.py confirm 07_chatgpt_app"
    assert any("플러그인" in path for path in step["chatgpt_ui_paths"])
    assert any("앱" in path for path in step["chatgpt_ui_paths"])
    assert len(step["missing_create_button_triage"]) == 4


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"CODEX_ONBOARDING_LANG": "ko"}, "ko"),
        ({"CODEX_ONBOARDING_LANG": "en"}, "en"),
        ({"LANG": "ko_KR.UTF-8"}, "ko"),
        ({"LC_ALL": "en_US.UTF-8"}, "en"),
        ({"LANG": "Korean_Korea.949"}, "ko"),
    ],
)
def test_language_follows_the_environment_locale(environment: dict[str, str], expected: str) -> None:
    assert module.resolve_language(None, environment) == expected


def test_explicit_language_wins_and_unknown_values_fail_closed() -> None:
    assert module.resolve_language("en", {"CODEX_ONBOARDING_LANG": "ko"}) == "en"
    with pytest.raises(module.OnboardingError, match="ONBOARDING_LANGUAGE_UNSUPPORTED"):
        module.resolve_language("fr", {})


@pytest.mark.parametrize("language", ["ko", "en"])
def test_every_stage_has_readable_instructions_in_both_languages(tmp_path: Path, language: str) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    state = module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    for stage_id in module.STAGE_IDS:
        instructions = module.stage_instructions(stage_id, state, language)
        assert instructions
        assert all(line.strip() for line in instructions)
        assert "No instructions found" not in instructions[0]
        assert "찾을 수 없습니다" not in instructions[0]


@pytest.mark.parametrize("language", ["ko", "en"])
def test_final_gate_instructions_require_manual_registered_app_action_refresh_before_fresh_canary(
    tmp_path: Path, language: str
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    state = module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    instructions = "\n".join(module.stage_instructions("08_final_gate", state, language))

    assert "Action control" in instructions
    assert "Refresh" in instructions
    assert "Business" in instructions
    assert "read_chunk" in instructions
    assert "post-register" in instructions
    assert "open_workspace/read" in instructions
    assert "prepare-final-gate" in instructions


def test_prepare_final_gate_writes_exact_host_state_manifest_and_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        app_name="codex",
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    mission = Path(environment["project"]) / "missions" / "onboarding-final-gate.md"
    mission.parent.mkdir(parents=True, exist_ok=True)
    mission.write_text("Read this exact file without mutation.\n", encoding="utf-8")
    source_thread_id = "00000000-0000-4000-8000-000000000123"
    monkeypatch.setenv("CODEX_THREAD_ID", source_thread_id)

    result = module.prepare_final_gate(
        root=str(environment["project"]),
        mission_path=mission,
        codex_home=environment["codex_home"],
    )

    manifest_path = Path(result["manifest_path"])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_path.is_relative_to(Path(environment["codex_home"]))
    assert re.fullmatch(r"[0-9a-f]{32}\.json", manifest_path.name)
    assert payload == {
        "schema": "codex.chatgpt.oracle-run/v1",
        "project_root": str(Path(environment["project"]).resolve()),
        "mission_path": str(mission.resolve()),
        "app_name": "codex",
        "mode": "browser",
        "transport": "devspace",
        "model": "gpt-5.6",
        "model_strategy": "select",
        "thinking_time": "extra-high",
        "research": "off",
        "task_outcome_contract": "v1",
        "archive": "never",
        "registered_app_final_gate": True,
        "source_thread_id": source_thread_id,
    }
    assert result["submission_action"] == "none"
    assert result["dry_run_command"].endswith(" --dry-run")
    assert "chatgpt_oracle_run.py run --manifest" in result["run_command"]
    assert "missions/onboarding-final-gate.md" in result["record_command_template"]
    first_manifest_bytes = manifest_path.read_bytes()

    second_thread_id = "00000000-0000-4000-8000-000000000456"
    monkeypatch.setenv("CODEX_THREAD_ID", second_thread_id)
    second = module.prepare_final_gate(
        root=str(environment["project"]),
        mission_path=mission,
        codex_home=environment["codex_home"],
    )
    assert second["manifest_path"] != result["manifest_path"]
    assert Path(result["manifest_path"]).read_bytes() == first_manifest_bytes

    same_bytes_different_path = mission.with_name("same-bytes-different-path.md")
    same_bytes_different_path.write_bytes(mission.read_bytes())
    third = module.prepare_final_gate(
        root=str(environment["project"]),
        mission_path=same_bytes_different_path,
        codex_home=environment["codex_home"],
    )
    assert third["mission_sha256"] == result["mission_sha256"]
    assert third["manifest_path"] != result["manifest_path"]


def test_prepare_final_gate_rejects_mission_outside_exact_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    outside = tmp_path / "outside.md"
    outside.write_text("not project-bound", encoding="utf-8")
    monkeypatch.setenv("CODEX_THREAD_ID", "00000000-0000-4000-8000-000000000123")

    with pytest.raises(module.OnboardingError, match="FINAL_GATE_MISSION_MUST_BE_INSIDE_EXACT_ROOT"):
        module.prepare_final_gate(
            root=str(environment["project"]),
            mission_path=outside,
            codex_home=environment["codex_home"],
        )


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (b"x" * (24 * 1024 + 1), "FINAL_GATE_MISSION_EXCEEDS_SINGLE_READ_CHUNK"),
        (b"\xff\xfe", "FINAL_GATE_MISSION_MUST_BE_UTF8"),
    ],
)
def test_prepare_final_gate_rejects_unreadable_single_chunk_mission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    error: str,
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    mission = Path(environment["project"]) / "missions" / "onboarding-final-gate.md"
    mission.parent.mkdir(parents=True, exist_ok=True)
    mission.write_bytes(payload)
    monkeypatch.setenv("CODEX_THREAD_ID", "00000000-0000-4000-8000-000000000123")

    with pytest.raises(module.OnboardingError, match=error):
        module.prepare_final_gate(
            root=str(environment["project"]),
            mission_path=mission,
            codex_home=environment["codex_home"],
        )


def test_prepare_final_gate_requires_current_codex_task_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    mission = Path(environment["project"]) / "missions" / "onboarding-final-gate.md"
    mission.parent.mkdir(parents=True, exist_ok=True)
    mission.write_text("read-only canary", encoding="utf-8")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)

    with pytest.raises(module.OnboardingError, match="FINAL_GATE_CODEX_TASK_REQUIRED"):
        module.prepare_final_gate(
            root=str(environment["project"]),
            mission_path=mission,
            codex_home=environment["codex_home"],
        )


def test_registered_final_gate_record_requires_live_matching_codex_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        app_name="codex",
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    owner = "00000000-0000-4000-8000-000000000123"
    run_dir = _bound_final_gate_run(
        environment,
        ["missions/onboarding-final-gate.md"],
        registered_app_final_gate=True,
        source_thread_id=owner,
    )

    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    with pytest.raises(module.OnboardingError, match="FINAL_GATE_CURRENT_TASK_BINDING_REQUIRED"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence="The exact registered-app final gate was independently verified.",
            listing=["missions/onboarding-final-gate.md"],
            run_dir=run_dir,
            codex_home=environment["codex_home"],
            devspace_home=environment["devspace_home"],
        )

    monkeypatch.setenv("CODEX_THREAD_ID", "00000000-0000-4000-8000-000000000999")
    with pytest.raises(module.OnboardingError, match="FINAL_GATE_FOREIGN_TASK_RUN"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence="The exact registered-app final gate was independently verified.",
            listing=["missions/onboarding-final-gate.md"],
            run_dir=run_dir,
            codex_home=environment["codex_home"],
            devspace_home=environment["devspace_home"],
        )

    monkeypatch.setenv("CODEX_THREAD_ID", owner)
    recorded = module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="The exact registered-app final gate was independently verified.",
        listing=["missions/onboarding-final-gate.md"],
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    assert recorded["source_thread_id"] == owner

    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    resumed = module.load_state(codex_home=environment["codex_home"])
    assert (
        module._final_gate_receipt(
            environment["codex_home"], environment["devspace_home"], resumed
        )
        == resumed["stages"]["08_final_gate"]["evidence"]
    )
    monkeypatch.setenv("CODEX_THREAD_ID", "00000000-0000-4000-8000-000000000999")
    assert (
        module._final_gate_receipt(
            environment["codex_home"], environment["devspace_home"], resumed
        )
        == resumed["stages"]["08_final_gate"]["evidence"]
    )


@pytest.mark.parametrize("provider", ["cloudflare", "ngrok", "custom"])
def test_non_tailscale_instructions_never_route_through_tailscale_helper(
    tmp_path: Path, provider: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = module.start_onboarding(
        provider=provider,
        registration_url="https://mcp.example.com/mcp",
        roots=[str(project)],
        codex_home=tmp_path / ".codex",
        devspace_home=tmp_path / ".devspace",
    )
    rendered = "\n".join(
        line
        for stage in module.STAGE_IDS
        for line in module.stage_instructions(stage, state, "en")
    )
    assert "devspace_tailscale_setup.py" not in rendered
    assert "OS login service" in rendered


@pytest.mark.parametrize(("language", "needle"), [("ko", "현재 상태"), ("en", "Current state")])
def test_render_step_is_human_readable_per_language(tmp_path: Path, language: str, needle: str) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        language=language,
        **environment["probes"],
    )
    rendered = module.render_step(step)
    assert needle in rendered
    assert step["current_stage"] in rendered
    assert "{" not in rendered


def test_pending_stages_never_skip_an_unverified_middle_stage(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    start = module.STAGE_IDS.index(step["current_stage"])
    assert step["pending_stages"] == list(module.STAGE_IDS[start:])


@pytest.mark.parametrize("evidence", ["", "too short"])
def test_final_gate_rejects_empty_or_too_short_evidence_without_completion(
    tmp_path: Path, evidence: str
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)

    with pytest.raises(module.OnboardingError, match="FINAL_GATE_EVIDENCE_INSUFFICIENT"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence=evidence,
            listing=["README.md"],
            codex_home=environment["codex_home"],
        )

    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert step["done"] is False
    assert step["current_stage"] == "08_final_gate"


def test_final_gate_rejects_empty_or_whitespace_only_listing(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )

    with pytest.raises(module.OnboardingError, match="FINAL_GATE_EVIDENCE_INSUFFICIENT"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence="The exact project directory was listed.",
            listing=["", "   "],
            codex_home=environment["codex_home"],
        )


def test_valid_final_gate_record_completes_onboarding_and_stores_listing_sample(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)
    listing = ["README.md", "bin", "tests"]
    run_dir = _bound_final_gate_run(environment, listing)

    recorded = module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="The exact project directory was listed.",
        listing=listing,
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    assert recorded["listing_sample"] == ["README.md", "bin", "tests"]

    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert step["done"] is True
    assert step["completion_state"] == "verified"


def test_final_gate_listing_sample_is_capped_at_ten_entries(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)
    listing = [f"entry-{index}" for index in range(15)]
    run_dir = _bound_final_gate_run(environment, listing)

    recorded = module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="The exact project directory was listed.",
        listing=listing,
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )

    assert recorded["listing_sample"] == listing[:10]


def test_final_gate_rejects_non_regular_non_pro_transport(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )

    with pytest.raises(
        module.OnboardingError, match="FINAL_GATE_TRANSPORT_MUST_BE_REGULAR_NON_PRO_ORACLE"
    ):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence="The exact project directory was listed.",
            listing=["README.md"],
            transport="pro-devspace",
            codex_home=environment["codex_home"],
        )


def test_final_gate_failure_can_be_recorded_without_minimum_evidence(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)

    recorded = module.record_final_gate(
        read_ok=False,
        root=str(environment["project"]),
        evidence="",
        codex_home=environment["codex_home"],
    )
    assert recorded["read_ok"] is False
    assert recorded["listing_sample"] == []

    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert step["done"] is False
    assert step["current_stage"] == "08_final_gate"


def test_confirm_stage_rejects_out_of_order_confirmation(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    (environment["codex_home"] / "chatgpt-workspace.json").write_text(
        json.dumps({"app_name": "codex"}), encoding="utf-8"
    )
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )

    rejected = module.confirm_stage(
        "07_chatgpt_app",
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert rejected["accepted"] is False
    assert rejected["reason"] == "STAGE_OUT_OF_ORDER_EARLIER_STAGE_PENDING"
    assert rejected["blocking_stage"] in module.STAGE_IDS[: module.STAGE_IDS.index("07_chatgpt_app")]
    reloaded = module.load_state(codex_home=environment["codex_home"])
    assert reloaded["stages"]["07_chatgpt_app"]["status"] == "pending"


def test_confirm_stage_accepts_explicit_stable_endpoint_plan_approval(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )

    accepted = module.confirm_stage(
        "02_stable_endpoint",
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert accepted["accepted"] is True
    assert accepted["reason"] is None
    assert accepted["blocking_stage"] is None


def test_local_network_policy_requires_explicit_consent_before_agent_mutation(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(
        environment,
        stages=("02_stable_endpoint", "04_reboot_service", "06_oracle_login"),
    )
    state = module.load_state(codex_home=environment["codex_home"])
    before = module.stage_instructions("06b_local_network_access", state, "en")
    assert any("consent" in line.casefold() for line in before)
    assert not any("chrome_local_network.py enable" in line for line in before)

    result = module.consent_stage(
        "06b_local_network_access",
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert result["accepted"] is True
    state = module.load_state(codex_home=environment["codex_home"])
    after = module.stage_instructions("06b_local_network_access", state, "en")
    assert any("chrome_local_network.py enable" in line for line in after)


@pytest.mark.parametrize(("language", "completion"), [("ko", "전체 설치"), ("en", "Full install")])
def test_clean_room_wizard_walks_every_user_boundary_to_hash_bound_completion(
    tmp_path: Path, language: str, completion: str
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    policy = {"enabled": False}
    probes = {
        **environment["probes"],
        "local_network_policy_probe": lambda: dict(policy),
    }
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )

    assert module.next_step(
        codex_home=environment["codex_home"], devspace_home=environment["devspace_home"],
        language=language, **probes
    )["current_stage"] == "02_stable_endpoint"
    for stage_id in ("02_stable_endpoint", "04_reboot_service", "06_oracle_login"):
        assert module.confirm_stage(
            stage_id,
            codex_home=environment["codex_home"],
            devspace_home=environment["devspace_home"],
            language=language,
            **probes,
        )["accepted"] is True
    pending = module.next_step(
        codex_home=environment["codex_home"], devspace_home=environment["devspace_home"],
        language=language, **probes
    )
    assert pending["current_stage"] == "06b_local_network_access"
    assert module.consent_stage(
        "06b_local_network_access",
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **probes,
    )["accepted"] is True
    policy["enabled"] = True
    assert module.confirm_stage(
        "07_chatgpt_app",
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        language=language,
        **probes,
    )["accepted"] is True
    assert module.next_step(
        codex_home=environment["codex_home"], devspace_home=environment["devspace_home"],
        language=language, **probes
    )["current_stage"] == "08_final_gate"

    run_dir = _bound_final_gate_run(environment, ["README.md"])
    module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="The exact project directory was listed and connector identity verified.",
        listing=["README.md"],
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    done = module.next_step(
        codex_home=environment["codex_home"], devspace_home=environment["devspace_home"],
        language=language, **probes
    )
    assert done["done"] is True
    assert completion in done["completion_label"]


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("ko", "로컬 설치·연결 설정 진행 중"),
        ("en", "Local install and connection setup in progress"),
    ],
)
def test_initial_completion_label_does_not_claim_install_is_complete(
    tmp_path: Path, language: str, expected: str
) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )

    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        language=language,
        **environment["probes"],
    )

    assert step["done"] is False
    assert step["current_stage"] in {"01_install", "02_stable_endpoint"}
    assert step["completion_label"] == expected
    assert "complete" not in step["completion_label"].lower()


@pytest.mark.parametrize(
    ("mutation", "case"),
    [
        (lambda state: state.pop("provider"), "missing-provider"),
        (lambda state: state.__setitem__("registration_url", "   "), "blank-registration-url"),
        (lambda state: state.pop("allowed_roots"), "missing-allowed-roots"),
        (lambda state: state.__setitem__("allowed_roots", []), "empty-allowed-roots"),
        (lambda state: state["stages"].__setitem__("01_install", "done"), "non-dict-stage"),
    ],
)
def test_load_state_rejects_corrupt_on_disk_state(tmp_path: Path, mutation: object, case: str) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    path = module.state_path(codex_home=environment["codex_home"])
    state = json.loads(path.read_text(encoding="utf-8"))
    mutation(state)
    path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(module.OnboardingError, match="ONBOARDING_STATE_CORRUPT"):
        module.load_state(codex_home=environment["codex_home"])


def test_load_state_backfills_missing_stage_defaults(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    path = module.state_path(codex_home=environment["codex_home"])
    state = json.loads(path.read_text(encoding="utf-8"))
    stage = state["stages"]["01_install"]
    for name in ("status", "verified_at", "evidence"):
        stage.pop(name)
    path.write_text(json.dumps(state), encoding="utf-8")

    module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    reloaded = module.load_state(codex_home=environment["codex_home"])
    assert {"status", "verified_at", "evidence"}.issubset(reloaded["stages"]["01_install"])


def _final_gate_record(root: Path, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "read_ok": True,
        "root": str(root),
        "evidence": "The exact project directory was listed.",
        "listing_sample": ["README.md"],
        "recorded_at": "2026-08-22T00:00:00Z",
        "transport": "regular-non-pro-oracle",
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize(
    "tampered_record",
    [
        lambda _environment, _tmp_path: {"read_ok": True},
        lambda environment, _tmp_path: _final_gate_record(environment["project"], listing_sample=[]),
        lambda environment, _tmp_path: _final_gate_record(environment["project"], evidence="too short"),
        lambda environment, _tmp_path: _final_gate_record(environment["project"], transport="pro-devspace"),
        lambda _environment, tmp_path: _final_gate_record(tmp_path / "outside-allowed-roots"),
        lambda environment, _tmp_path: _final_gate_record(environment["project"], recorded_at=""),
        lambda environment, _tmp_path: _final_gate_record(
            environment["project"], listing_sample=[" ", "\t"]
        ),
    ],
)
def test_next_rejects_tampered_final_gate_evidence_on_disk(
    tmp_path: Path, tampered_record: object
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)
    state_file = module.state_path(codex_home=environment["codex_home"])
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["stages"]["08_final_gate"]["evidence"] = tampered_record(environment, tmp_path)
    state_file.write_text(json.dumps(state), encoding="utf-8")

    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )

    assert step["done"] is False
    assert step["current_stage"] == "08_final_gate"


def test_honest_recorded_final_gate_still_completes_after_read_time_validation(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)
    run_dir = _bound_final_gate_run(environment, ["README.md"])
    module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="The exact project directory was listed.",
        listing=["README.md"],
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )

    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )

    assert step["done"] is True
    assert step["completion_state"] == "verified"


def test_final_gate_receipt_distinguishes_honest_and_tampered_evidence(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)
    run_dir = _bound_final_gate_run(environment, ["README.md"])
    module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="The exact project directory was listed.",
        listing=["README.md"],
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    honest = module.load_state(codex_home=environment["codex_home"])

    assert module._final_gate_receipt(environment["codex_home"], environment["devspace_home"], honest) == honest["stages"]["08_final_gate"]["evidence"]

    honest["stages"]["08_final_gate"]["evidence"] = _final_gate_record(
        environment["project"], listing_sample=[]
    )
    assert module._final_gate_receipt(environment["codex_home"], environment["devspace_home"], honest) is None


def test_legacy_final_gate_record_revalidates_without_new_binding_keys(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    _confirm_ready_manual_stages(environment)
    run_dir = _bound_final_gate_run(environment, ["README.md"])
    recorded = module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="Legacy final gate remains valid after the opt-in extension.",
        listing=["README.md"],
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )

    assert "registered_app_final_gate" not in recorded
    assert "source_thread_id" not in recorded
    state = module.load_state(codex_home=environment["codex_home"])
    assert module._final_gate_receipt(
        environment["codex_home"], environment["devspace_home"], state
    ) == recorded


def test_final_gate_hash_binding_rejects_output_tamper_after_recording(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)
    run_dir = _bound_final_gate_run(environment, ["README.md"])
    module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="The exact project directory was listed.",
        listing=["README.md"],
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    (run_dir / "output.md").write_text("tampered\nTASK_OUTCOME: EXECUTED\n", encoding="utf-8")

    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert step["done"] is False
    assert step["current_stage"] == "08_final_gate"


def test_final_gate_revalidation_rejects_receipt_path_or_hash_tamper(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)
    run_dir = _bound_final_gate_run(environment, ["README.md"])
    recorded = module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="receipt paths and hashes must remain unchanged",
        listing=["README.md"],
        run_dir=run_dir,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
    )
    assert len(recorded["tool_read_receipts"]) == 3
    receipt_path = Path(recorded["tool_read_receipts"][2]["path"])
    receipt_path.write_text(receipt_path.read_text(encoding="utf-8").replace('"timestamp": "2026-08-22T00:00:03Z"', '"timestamp": "2026-08-22T00:00:04Z"'), encoding="utf-8")

    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert step["done"] is False
    assert step["current_stage"] == "08_final_gate"


def test_load_state_reports_wrong_schema_as_corrupt(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    state_file = module.state_path(codex_home=environment["codex_home"])
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["schema"] = "wrong-schema"
    state_file.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(module.OnboardingError, match="ONBOARDING_STATE_CORRUPT"):
        module.load_state(codex_home=environment["codex_home"])


def test_load_state_reports_absent_file_as_not_started(tmp_path: Path) -> None:
    with pytest.raises(module.OnboardingError, match="ONBOARDING_NOT_STARTED"):
        module.load_state(codex_home=tmp_path / ".codex")


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--lang", "en", "next"], "en"),
        (["--lang=ko", "next"], "ko"),
        (["next"], None),
        (["--lang", "fr", "next"], None),
    ],
)
def test_global_language_flag_recovers_only_supported_values(
    arguments: list[str], expected: str | None
) -> None:
    assert module._global_language_flag(arguments) == expected


def test_cli_start_existing_state_requires_reset_and_corrupt_state_can_be_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    codex_home = tmp_path / ".codex"
    offline_probe = lambda _url: {"ok": False, "status": None}
    real_next_step = module.next_step
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(module, "probe_http", offline_probe)
    monkeypatch.setattr(
        module,
        "next_step",
        lambda *, language=None: real_next_step(
            codex_home=codex_home,
            devspace_home=environment["devspace_home"],
            http_probe=offline_probe,
            oracle_profile_dir=environment["profile"],
            local_network_policy_probe=lambda: {"enabled": False},
            language=language,
        ),
    )
    start_arguments = [
        "start",
        "--provider",
        "custom",
        "--public-url",
        "https://mcp.example.com/mcp",
        "--root",
        str(environment["project"]),
    ]
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=codex_home,
    )

    assert module.main(start_arguments) == 2
    assert "ONBOARDING_ALREADY_STARTED" in capsys.readouterr().out

    state_file = module.state_path(codex_home=codex_home)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state.pop("provider")
    state_file.write_text(json.dumps(state), encoding="utf-8")

    assert module.main(start_arguments) == 2
    assert "ONBOARDING_STATE_CORRUPT" in capsys.readouterr().out

    (codex_home / "receipts" / "codexpro-automation-1.json").unlink()
    assert module.main([*start_arguments, "--reset"]) != 2
    assert "01_install" in capsys.readouterr().out
