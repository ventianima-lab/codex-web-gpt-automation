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
    def __init__(self, *, codex_home: Path, state: dict, gate: dict | None):
        self.codex_home = codex_home
        self.state = state
        self.gate = gate

    def load_state(self, *, codex_home=None):
        assert codex_home in {None, self.codex_home}
        return self.state

    def _codex_home(self, codex_home=None):
        return (codex_home or self.codex_home).resolve()

    def _final_gate_receipt(self, codex_home, devspace_home, state):
        assert codex_home == self.codex_home.resolve()
        assert state is self.state
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
