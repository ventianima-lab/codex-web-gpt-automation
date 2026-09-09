from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "codex_global_agents_setup_test", ROOT / "bin" / "codex_global_agents_setup.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def seed_home(tmp_path: Path) -> Path:
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "config.toml").write_text(
        'model = "gpt-6-astra"\n'
        'model_reasoning_effort = "medium"\n'
        'custom_setting = "preserve-me"\n\n'
        '[features]\nmemories = true\n\n'
        '[mcp_servers.example]\nurl = "http://127.0.0.1:9999/mcp"\n',
        encoding="utf-8",
    )
    (home / "AGENTS.md").write_text(
        "# Existing policy\n\n<!-- BEGIN ANCHORMIND ACTIVE MEMORY POLICY -->\nkeep me\n<!-- END ANCHORMIND ACTIVE MEMORY POLICY -->\n",
        encoding="utf-8",
    )
    return home


def test_apply_preserves_existing_config_and_policy_and_is_idempotent(tmp_path: Path) -> None:
    home = seed_home(tmp_path)
    first = module.apply_setup(home, source_root=ROOT)
    assert first["ok"] is True
    assert set(first["changed"]) == {
        "config.toml",
        "AGENTS.md",
        "agents/scout.toml",
        "agents/implementer.toml",
        "agents/verifier.toml",
    }

    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    assert config["model"] == "gpt-6-astra"
    assert config["model_reasoning_effort"] == "medium"
    assert config["custom_setting"] == "preserve-me"
    assert config["features"] == {"memories": True}
    assert config["mcp_servers"]["example"]["url"].endswith("/mcp")
    assert config["agents"] == {
        "enabled": True,
        "max_concurrent_threads_per_session": 3,
        "default_subagent_model": "gpt-5.6-terra",
        "default_subagent_reasoning_effort": "medium",
    }
    assert "multi_agent_v2" not in config["features"]

    global_policy = (home / "AGENTS.md").read_text(encoding="utf-8")
    assert "ANCHORMIND ACTIVE MEMORY POLICY" in global_policy
    assert global_policy.count(module.MANAGED_BEGIN) == 1
    assert "docs/AUTOMATION_POLICY.md" in global_policy
    assert "non-overlapping write scopes" in global_policy
    assert "Preserve the configured commander" in global_policy
    assert "durable result capture" in global_policy
    assert "without automatic resubmission" in global_policy
    assert "Do not add execution modes" in global_policy
    assert "historical" in global_policy
    assert module.doctor(home, source_root=ROOT)["ok"] is True

    second = module.apply_setup(home, source_root=ROOT)
    assert second == {"ok": True, "changed": [], "receipt": None}
    receipt = json.loads(Path(first["receipt"]).read_text(encoding="utf-8"))
    assert receipt["schema"] == "codex.web-gpt.global-agents-receipt/v1"
    assert all(record["before_sha256"] != record["after_sha256"] for record in receipt["files"])


def test_existing_agents_table_is_merged_and_legacy_alias_removed() -> None:
    merged = module.merge_config(
        'model = "old"\nmodel_reasoning_effort = "low"\n\n'
        '[agents]\nmax_threads = 99\ninterrupt_message = false\n\n'
        '[features]\nmulti_agent_v2 = false\n'
    )
    config = tomllib.loads(merged)
    assert config["model"] == "old"
    assert config["model_reasoning_effort"] == "low"
    assert config["agents"]["max_concurrent_threads_per_session"] == 3
    assert "max_threads" not in config["agents"]
    assert config["agents"]["interrupt_message"] is False
    assert config["features"]["multi_agent_v2"] is False


def test_unmanaged_role_fails_closed_without_explicit_replace(tmp_path: Path) -> None:
    home = seed_home(tmp_path)
    (home / "agents").mkdir()
    (home / "agents" / "scout.toml").write_text(
        'name="scout"\ndescription="mine"\ndeveloper_instructions="mine"\n', encoding="utf-8"
    )
    with pytest.raises(module.AgentSetupError, match="unmanaged role exists"):
        module.apply_setup(home, source_root=ROOT)
    assert tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))["model_reasoning_effort"] == "medium"


def test_doctor_rejects_unstable_multi_agent_v2(tmp_path: Path) -> None:
    home = seed_home(tmp_path)
    module.apply_setup(home, source_root=ROOT)
    path = home / "config.toml"
    path.write_text(path.read_text(encoding="utf-8").replace("memories = true", "memories = true\nmulti_agent_v2 = true"), encoding="utf-8")
    result = module.doctor(home, source_root=ROOT)
    assert result["ok"] is False
    assert "UNSTABLE_MULTI_AGENT_V2_ENABLED" in result["errors"]


def test_doctor_reports_but_does_not_constrain_commander_settings(tmp_path: Path) -> None:
    home = seed_home(tmp_path)
    module.apply_setup(home, source_root=ROOT)

    result = module.doctor(home, source_root=ROOT)

    assert result["ok"] is True
    assert result["main"] == {
        "model": "gpt-6-astra",
        "model_reasoning_effort": "medium",
    }
    assert not any(error.startswith("TOP_LEVEL_MISMATCH:") for error in result["errors"])


def test_role_contracts_are_narrow_and_parseable() -> None:
    roles, _ = module._load_templates(ROOT)
    scout = tomllib.loads(roles["scout"])
    implementer = tomllib.loads(roles["implementer"])
    verifier = tomllib.loads(roles["verifier"])
    assert (scout["model"], scout["model_reasoning_effort"], scout["sandbox_mode"]) == (
        "gpt-5.6-luna",
        "max",
        "read-only",
    )
    assert implementer["model"] == "gpt-5.6-terra"
    assert implementer["model_reasoning_effort"] == "high"
    assert implementer["sandbox_mode"] == "workspace-write"
    assert "exact files explicitly named" in implementer["developer_instructions"]
    assert verifier["model_reasoning_effort"] == "high"
    assert verifier["sandbox_mode"] == "read-only"
