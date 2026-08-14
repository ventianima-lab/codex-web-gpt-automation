from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "codex_web_gpt_onboarding_test", ROOT / "bin" / "codex_web_gpt_onboarding.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_plan_orders_the_complete_first_install_without_secrets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    plan = module.onboarding_plan(
        provider="tailscale",
        registration_url="https://host.tailnet.ts.net/mcp",
        roots=[str(project)],
    )
    assert plan["product"] == "Agent Web GPT Automation"
    assert plan["app_name"] == "codex"
    assert [stage["id"] for stage in plan["stages"]] == [
        "01_install",
        "02_stable_endpoint",
        "03_devspace_init",
        "04_reboot_service",
        "05_endpoint_check",
        "06_oracle_login",
        "07_chatgpt_app",
        "08_final_gate",
    ]
    dumped = json.dumps(plan)
    assert "owner_token" not in dumped.casefold()
    assert "--browser-manual-login" in dumped
    assert "DEVSPACE_OAUTH_SCOPES" in dumped


@pytest.mark.parametrize(
    ("provider", "url", "error"),
    [
        ("tailscale", "https://example.com/mcp", "TAILSCALE_STABLE_TS_NET_URL_REQUIRED"),
        ("cloudflare", "https://random.trycloudflare.com/mcp", "CLOUDFLARE_NAMED_TUNNEL_REQUIRED"),
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
    )
    assert status["checks"]["app_name_matches_expected"] is True
    assert status["expected_app_name"] == "dongju"


@pytest.mark.parametrize("value", ["", "@dongju", "bad/name", "bad\\name", "bad\nname"])
def test_app_name_validation_fails_closed(value: str) -> None:
    with pytest.raises(module.OnboardingError, match="APP_NAME_INVALID"):
        module.normalize_app_name(value)
