from __future__ import annotations

import importlib.util
import json
import sys
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
    def __init__(self, *, codex_home: Path, state: dict, result: dict | None):
        self.codex_home = codex_home
        self.state = state
        self.result = result

    def load_state(self, *, codex_home=None):
        assert codex_home in {None, self.codex_home}
        return self.state

    def _codex_home(self, codex_home=None):
        return (codex_home or self.codex_home).resolve()

    def _final_gate_receipt(self, codex_home, devspace_home, state):
        assert codex_home == self.codex_home.resolve()
        assert state is self.state
        return self.result


def app_result(module, project: Path, **updates: object) -> dict:
    result = {
        "schema": module.APP_READ_RESULT_SCHEMA,
        "read_ok": True,
        "root": str(project),
        "app_name": "codex",
        "auth_verified": True,
        "actual_model": "Latest",
        "outcome": "captured",
        "evidence": "authenticated app read completed",
        "listing_sample": ["README.md"],
        "recorded_at": "2020-01-01T00:00:00+00:00",
        "transport": "registered-app",
    }
    result.update(updates)
    return result


def test_registered_app_result_is_setup_once_root_scoped_and_not_freshness_gated(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "Coin"
    project.mkdir()
    codex_home = tmp_path / ".codex"
    state = {"app_name": "codex", "allowed_roots": [str(project)]}
    fake = FakeOnboarding(
        codex_home=codex_home,
        state=state,
        result=app_result(module, project),
    )

    result = module.ensure_registered_app_read_result(
        project,
        "codex",
        codex_home=codex_home,
        onboarding_loader=lambda: fake,
    )
    legacy = module.ensure_recent_registered_app_read_gate(
        project,
        "codex",
        codex_home=codex_home,
        onboarding_loader=lambda: fake,
    )

    assert result == legacy
    assert result == {
        "schema": module.APP_READ_RESULT_SCHEMA,
        "qualified": True,
        "project_root": str(project.resolve()),
        "app_name": "codex",
        "auth_verified": True,
        "actual_model": "Latest",
        "outcome": "captured",
        "recorded_at": "2020-01-01T00:00:00+00:00",
        "state_path": str(codex_home / "state" / "codex-web-gpt-automation" / "onboarding" / "state.json"),
        "setup_once": True,
    }
    assert not (codex_home / "state").exists()


@pytest.mark.parametrize(
    ("state_update", "result_update", "reason"),
    [
        ({"app_name": "other"}, {}, "registered-app-name-mismatch"),
        ({"allowed_roots": []}, {}, "exact-root-not-covered-by-verified-app"),
        ({}, {"root": "other"}, "app-read-root-mismatch"),
        ({}, {"auth_verified": False}, "app-auth-not-verified"),
        ({}, {"actual_model": ""}, "actual-model-invalid"),
        ({}, {"outcome": "failed"}, "app-read-outcome-not-captured"),
    ],
)
def test_registered_app_result_requires_only_minimal_policy_fields(
    tmp_path: Path,
    state_update: dict,
    result_update: dict,
    reason: str,
) -> None:
    module = load_module()
    project = tmp_path / "Coin"
    project.mkdir()
    codex_home = tmp_path / ".codex"
    state = {"app_name": "codex", "allowed_roots": [str(project)], **state_update}
    fake = FakeOnboarding(
        codex_home=codex_home,
        state=state,
        result=app_result(module, project, **result_update),
    )

    with pytest.raises(module.DevSpacePreflightError) as exc:
        module.ensure_registered_app_read_result(
            project,
            "codex",
            codex_home=codex_home,
            onboarding_loader=lambda: fake,
        )

    assert exc.value.code == "REGISTERED_APP_READ_RESULT_REQUIRED"
    assert exc.value.evidence["reason"] == reason
    assert exc.value.evidence["next_action"] == "COMPLETE_REGISTERED_APP_READ_CHECK_ONCE"
    serialized = json.dumps(exc.value.evidence)
    assert "auditNonce" not in serialized
    assert "read_chunk" not in serialized
    assert "fresh" not in serialized.casefold()


def test_missing_registered_app_result_has_no_prescribed_tool_or_model_gate(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "Coin"
    project.mkdir()
    codex_home = tmp_path / ".codex"
    fake = FakeOnboarding(
        codex_home=codex_home,
        state={"app_name": "codex", "allowed_roots": [str(project)]},
        result=None,
    )

    with pytest.raises(module.DevSpacePreflightError) as exc:
        module.ensure_registered_app_read_result(
            project,
            "codex",
            codex_home=codex_home,
            onboarding_loader=lambda: fake,
        )

    evidence = exc.value.evidence
    assert evidence["required"] == {
        "exact_root": True,
        "registered_app": True,
        "auth_verified": True,
        "actual_model": "one non-empty observed model",
        "outcome": "captured",
    }
    assert "required_tools" not in evidence
    assert "required_model" not in evidence

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
