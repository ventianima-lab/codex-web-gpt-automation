from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / ".omp"


def test_project_harness_inherits_root_contract_and_routes_by_boundary() -> None:
    text = (HARNESS / "AGENTS.md").read_text(encoding="utf-8")

    assert "@../AGENTS.md" in text
    assert "authority/state and receipt contracts" in text
    assert "Oracle execution and recovery" in text
    assert "workflow topology" in text
    assert "all-lanes barrier" in text
    assert "single merger" in text
    assert "focused verification and a descriptive Git commit" in text


def test_project_harness_uses_cost_tiered_models_for_declared_agents() -> None:
    text = (HARNESS / "config.yml").read_text(encoding="utf-8")

    assert re.search(r"^modelRoleStorage: project$", text, re.MULTILINE)
    expected_roles = {
        "default": "openai-codex/gpt-5.6-terra:medium",
        "tiny": "openai-codex/gpt-5.3-codex-spark:low",
        "smol": "openai-codex/gpt-5.3-codex-spark:medium",
        "task": "openai-codex/gpt-5.4-mini:medium",
        "plan": "openai-codex/gpt-5.6-terra:high",
        "slow": "openai-codex/gpt-5.6-terra:high",
        "reviewer": "openai-codex/gpt-5.6-terra:high",
    }
    for role, model in expected_roles.items():
        assert re.search(rf"^  {role}: {re.escape(model)}$", text, re.MULTILINE)

    for agent, role in {
        "sonic": "tiny",
        "scout": "smol",
        "librarian": "smol",
        "task": "task",
        "reviewer": "reviewer",
        "security-reviewer": "reviewer",
    }.items():
        assert re.search(rf'^    {agent}: "@{role}"$', text, re.MULTILINE)


def test_project_harness_fails_closed_for_oracle_and_parallel_writes() -> None:
    text = (HARNESS / "RULES.md").read_text(encoding="utf-8")

    assert "New work uses Oracle only" in text
    assert "exact task, project root, mission bytes, workflow identity" in text
    assert "pairwise-disjoint `owned_paths`" in text
    assert "Partial merge and shared-checkout parallel writes are forbidden" in text
    assert "does not prove installation" in text
