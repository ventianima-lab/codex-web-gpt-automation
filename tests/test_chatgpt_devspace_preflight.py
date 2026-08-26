from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_devspace_preflight.py"


def load_module():
    spec = importlib.util.spec_from_file_location("chatgpt_devspace_preflight_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_config(path: Path, roots: list[Path]) -> None:
    path.write_text(json.dumps({"allowedRoots": [str(root) for root in roots]}), encoding="utf-8")


class FakeOnboarding:
    def __init__(self, *, codex_home: Path, state: dict, gate: dict | None, gate_error: Exception | None = None):
        self.codex_home = codex_home
        self.state = state
        self.gate = gate
        self.gate_error = gate_error

    def load_state(self, *, codex_home=None):
        assert codex_home in {None, self.codex_home}
        return self.state

    def _codex_home(self, codex_home=None):
        return (codex_home or self.codex_home).resolve()

    def _final_gate_receipt(self, codex_home, devspace_home, state):
        assert codex_home == self.codex_home.resolve()
        assert state is self.state
        if self.gate_error is not None:
            raise self.gate_error
        return self.gate


def test_recent_registered_app_read_gate_is_read_only_and_root_scoped(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "Coin"
    project.mkdir()
    codex_home = tmp_path / ".codex"
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    recorded_at = now - timedelta(hours=2)
    state = {"app_name": "codex", "allowed_roots": [str(project)]}
    gate = {
        "read_ok": True,
        "root": str(project),
        "recorded_at": recorded_at.isoformat(),
        "run_id": "regular-canary-1",
        "conversation_url": "https://chatgpt.com/c/canary",
        "tool_read_receipts": [{}, {}, {}],
    }
    fake = FakeOnboarding(codex_home=codex_home, state=state, gate=gate)

    result = module.ensure_recent_registered_app_read_gate(
        project,
        "codex",
        codex_home=codex_home,
        devspace_home=tmp_path / ".devspace",
        now=now,
        onboarding_loader=lambda: fake,
    )

    assert result["schema"] == module.PRO_APP_READ_GATE_SCHEMA
    assert result["qualified"] is True
    assert result["age_seconds"] == 7200
    assert result["receipt_count"] == 3
    assert not (codex_home / "state").exists()


@pytest.mark.parametrize(
    ("app_name", "root_kind", "age_hours", "reason"),
    [
        ("other", "exact", 1, "registered-app-name-mismatch"),
        ("codex", "other", 1, "exact-root-not-covered-by-verified-app"),
        ("codex", "exact", 25, "final-gate-expired"),
    ],
)
def test_pro_gate_fails_closed_for_wrong_app_root_or_stale_receipt(
    tmp_path: Path,
    app_name: str,
    root_kind: str,
    age_hours: int,
    reason: str,
) -> None:
    module = load_module()
    project = tmp_path / "Coin"
    other = tmp_path / "Other"
    project.mkdir()
    other.mkdir()
    codex_home = tmp_path / ".codex"
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    roots = [str(project if root_kind == "exact" else other)]
    state = {"app_name": app_name, "allowed_roots": roots}
    gate = {
        "read_ok": True,
        "root": roots[0],
        "recorded_at": (now - timedelta(hours=age_hours)).isoformat(),
        "run_id": "regular-canary-1",
        "conversation_url": "https://chatgpt.com/c/canary",
        "tool_read_receipts": [{}, {}, {}],
    }
    fake = FakeOnboarding(codex_home=codex_home, state=state, gate=gate)

    with pytest.raises(module.DevSpacePreflightError) as exc:
        module.ensure_recent_registered_app_read_gate(
            project,
            "codex",
            codex_home=codex_home,
            now=now,
            onboarding_loader=lambda: fake,
        )

    assert exc.value.code == "PRO_DEVSPACE_APP_READ_GATE_REQUIRED"
    assert exc.value.evidence["reason"] == reason
    assert exc.value.evidence["required_tools"] == ["open_workspace", "read", "read_chunk"]


def test_pro_gate_rejects_receipt_for_different_allowed_root(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "Coin"
    other = tmp_path / "Other"
    project.mkdir()
    other.mkdir()
    codex_home = tmp_path / ".codex"
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    state = {"app_name": "codex", "allowed_roots": [str(project), str(other)]}
    gate = {
        "read_ok": True,
        "root": str(other),
        "recorded_at": (now - timedelta(hours=1)).isoformat(),
        "run_id": "regular-canary-other-root",
        "conversation_url": "https://chatgpt.com/c/canary",
        "tool_read_receipts": [{}, {}, {}],
    }
    fake = FakeOnboarding(codex_home=codex_home, state=state, gate=gate)

    with pytest.raises(module.DevSpacePreflightError) as exc:
        module.ensure_recent_registered_app_read_gate(
            project,
            "codex",
            codex_home=codex_home,
            now=now,
            onboarding_loader=lambda: fake,
        )

    assert exc.value.code == "PRO_DEVSPACE_APP_READ_GATE_REQUIRED"
    assert exc.value.evidence["reason"] == "final-gate-root-mismatch"


def test_pro_gate_keeps_partial_registered_app_surface_fail_closed_with_manual_refresh_guidance(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "Coin"
    project.mkdir()
    codex_home = tmp_path / ".codex"
    state = {"app_name": "codex", "allowed_roots": [str(project)]}

    fake = FakeOnboarding(
        codex_home=codex_home,
        state=state,
        gate=None,
        gate_error=ValueError("FINAL_GATE_TOOL_READ_RECEIPTS_MISSING_OR_DUPLICATE"),
    )
    with pytest.raises(module.DevSpacePreflightError) as exc:
        module.ensure_recent_registered_app_read_gate(
            project,
            "codex",
            codex_home=codex_home,
            onboarding_loader=lambda: fake,
        )

    evidence = exc.value.evidence
    assert exc.value.code == "PRO_DEVSPACE_APP_READ_GATE_REQUIRED"
    assert evidence["final_gate_error"] == "FINAL_GATE_TOOL_READ_RECEIPTS_MISSING_OR_DUPLICATE"
    assert evidence["manual_chatgpt_action_required"] is True
    assert evidence["post_refresh_actions"] == [
        "RUN_POST_REGISTER_ONCE",
        "RUN_FRESH_REGULAR_NON_PRO_AUDIT_NONCE_CANARY",
    ]
    assert "Action control > Refresh" in "\n".join(evidence["registered_app_action_snapshot_guidance"]["en"])
    assert "Business" in "\n".join(evidence["registered_app_action_snapshot_guidance"]["en"])
    assert "read_chunk" in "\n".join(evidence["registered_app_action_snapshot_guidance"]["ko"])


def test_pro_gate_does_not_recommend_app_refresh_for_unrelated_or_unknown_failures(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "Coin"
    project.mkdir()
    codex_home = tmp_path / ".codex"
    state = {"app_name": "codex", "allowed_roots": [str(project)]}

    for gate_error, expected_code in [
        (ValueError("FINAL_GATE_ORACLE_STATE_INVALID"), "FINAL_GATE_ORACLE_STATE_INVALID"),
        (RuntimeError("sensitive arbitrary diagnostic"), None),
    ]:
        fake = FakeOnboarding(
            codex_home=codex_home,
            state=state,
            gate=None,
            gate_error=gate_error,
        )
        with pytest.raises(module.DevSpacePreflightError) as exc:
            module.ensure_recent_registered_app_read_gate(
                project,
                "codex",
                codex_home=codex_home,
                onboarding_loader=lambda fake=fake: fake,
            )

        evidence = exc.value.evidence
        assert evidence["final_gate_error"] == expected_code
        assert evidence["manual_chatgpt_action_required"] is False
        assert "registered_app_action_snapshot_guidance" not in evidence
        assert "post_refresh_actions" not in evidence
        conditional = evidence["conditional_registered_app_action_snapshot_guidance"]
        assert "If a fresh regular non-Pro canary exposes no read_chunk" in conditional["en"][0]


def test_pro_gate_without_recorded_final_gate_keeps_only_conditional_snapshot_guidance(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "Coin"
    project.mkdir()
    codex_home = tmp_path / ".codex"
    state = {"app_name": "codex", "allowed_roots": [str(project)]}
    fake = FakeOnboarding(codex_home=codex_home, state=state, gate=None)

    with pytest.raises(module.DevSpacePreflightError) as exc:
        module.ensure_recent_registered_app_read_gate(
            project,
            "codex",
            codex_home=codex_home,
            onboarding_loader=lambda: fake,
        )

    evidence = exc.value.evidence
    assert evidence["final_gate_error"] is None
    assert evidence["manual_chatgpt_action_required"] is False
    assert "registered_app_action_snapshot_guidance" not in evidence
    assert "post_refresh_actions" not in evidence
    conditional = evidence["conditional_registered_app_action_snapshot_guidance"]
    assert "read_chunk" in "\n".join(conditional["ko"])
    assert "Action control > Refresh" in "\n".join(conditional["en"])


def test_first_exact_root_qualification_is_cached_until_config_changes(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "config.json"
    write_config(config, [project])
    state = tmp_path / "qualifications"
    parse_calls: list[str] = []

    def parser(text: str):
        parse_calls.append(text)
        return json.loads(text)

    first = module.ensure_exact_root_qualified(
        project,
        config_path=config,
        qualification_root=state,
        bootstrap_path=tmp_path / "missing-bootstrap.json",
        json_loader=parser,
    )
    second = module.ensure_exact_root_qualified(
        project,
        config_path=config,
        qualification_root=state,
        bootstrap_path=tmp_path / "missing-bootstrap.json",
        json_loader=lambda _text: (_ for _ in ()).throw(AssertionError("cached config must not be reparsed")),
    )

    assert first["qualified"] is True and first["cached"] is False
    assert second["qualified"] is True and second["cached"] is True
    assert len(parse_calls) == 1

    other = tmp_path / "other"
    other.mkdir()
    write_config(config, [other])
    with pytest.raises(module.DevSpacePreflightError) as changed:
        module.ensure_exact_root_qualified(
            project,
            config_path=config,
            qualification_root=state,
            bootstrap_path=tmp_path / "missing-bootstrap.json",
        )
    assert changed.value.code == "DEVSPACE_EXACT_ROOT_UNAVAILABLE"


@pytest.mark.parametrize("registered_kind", ["parent", "child", "similar"])
def test_parent_child_or_similar_root_never_qualifies_exact_project(
    tmp_path: Path,
    registered_kind: str,
) -> None:
    module = load_module()
    parent = tmp_path / "workspace"
    project = parent / "Coin"
    child = project / "child"
    similar = parent / "Coin-copy"
    child.mkdir(parents=True)
    similar.mkdir()
    registered = {"parent": parent, "child": child, "similar": similar}[registered_kind]
    config = tmp_path / "config.json"
    write_config(config, [registered])

    with pytest.raises(module.DevSpacePreflightError) as exc:
        module.ensure_exact_root_qualified(
            project,
            config_path=config,
            qualification_root=tmp_path / "qualifications",
            bootstrap_path=tmp_path / "missing-bootstrap.json",
        )

    assert exc.value.code == "DEVSPACE_EXACT_ROOT_UNAVAILABLE"
    assert exc.value.evidence["missing_root"] == str(project.resolve())
    assert exc.value.evidence["configured_roots"] == [str(registered.resolve())]


def test_missing_root_error_includes_registration_and_preserves_existing_roots(tmp_path: Path) -> None:
    module = load_module()
    existing = tmp_path / "existing"
    project = tmp_path / "Coin"
    existing.mkdir()
    project.mkdir()
    config = tmp_path / "config.json"
    write_config(config, [existing])
    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(
        json.dumps({"hostname": "device.tailnet.ts.net", "public_port": 443}),
        encoding="utf-8",
    )

    with pytest.raises(module.DevSpacePreflightError) as exc:
        module.ensure_exact_root_qualified(
            project,
            config_path=config,
            qualification_root=tmp_path / "qualifications",
            bootstrap_path=bootstrap,
        )

    evidence = exc.value.evidence
    assert evidence["registration_url"] == "https://device.tailnet.ts.net/mcp"
    root_arguments = [
        evidence["setup_argv"][index + 1]
        for index, value in enumerate(evidence["setup_argv"])
        if value == "--root"
    ]
    assert root_arguments == [str(existing.resolve()), str(project.resolve())]
