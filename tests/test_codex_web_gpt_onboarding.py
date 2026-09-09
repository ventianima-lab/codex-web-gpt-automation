from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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


def test_tailscale_hostname_discovery_decodes_utf8_bytes_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module.shutil, "which", lambda _name: "tailscale.exe")
    payload = json.dumps(
        {
            "Self": {
                "DNSName": "lasal-pc.tail46ec90.ts.net.",
                "HostName": "라살-PC",
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")
    observed: dict[str, object] = {}

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout=payload, stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module._discover_tailscale_hostname() == "lasal-pc.tail46ec90.ts.net"
    assert observed["argv"] == ["tailscale.exe", "status", "--json"]
    assert observed["kwargs"] == {"check": True, "capture_output": True, "timeout": 30}


def test_tailscale_hostname_discovery_rejects_non_utf8_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module.shutil, "which", lambda _name: "tailscale.exe")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, stdout=b'{"Self":{"DNSName":"host.ts.net","HostName":"\x80"}}', stderr=b""
        ),
    )
    with pytest.raises(module.OnboardingError, match="TAILSCALE_HOSTNAME_UNAVAILABLE"):
        module._discover_tailscale_hostname()


def test_start_uses_utf8_tailscale_auto_discovery_without_injected_hostname(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "프로젝트"
    project.mkdir()
    monkeypatch.setattr(module.shutil, "which", lambda _name: "tailscale.exe")
    payload = json.dumps(
        {"Self": {"DNSName": "lasal-pc.tail46ec90.ts.net.", "HostName": "라살-PC"}},
        ensure_ascii=False,
    ).encode("utf-8")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout=payload, stderr=b""),
    )

    state = module.start_onboarding(
        provider="tailscale",
        roots=[str(project)],
        codex_home=tmp_path / ".codex",
        devspace_home=tmp_path / ".devspace",
    )
    assert state["registration_url"] == "https://lasal-pc.tail46ec90.ts.net/mcp"
    assert state["allowed_roots"] == [str(project.resolve())]


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


def _write_bootstrap_recovery_receipt(
    *,
    codex_home: Path,
    devspace_home: Path,
    hostname: str,
    observed_at: datetime | None = None,
    mode: str = "Watch",
    watch_interval_seconds: int = 30,
) -> Path:
    config_text = (devspace_home / "config.json").read_bytes().decode("utf-8-sig")
    target = codex_home / module.BOOTSTRAP_RECOVERY_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({
            "schema": module.BOOTSTRAP_RECOVERY_SCHEMA,
            "healthy": True,
            "config_sha256": hashlib.sha256(config_text.encode("utf-8")).hexdigest(),
            "hostname": hostname,
            "mode": mode,
            "watch_interval_seconds": watch_interval_seconds,
            "watchdog_pid": 4242,
            "reason": "healthy-recovery-observed",
            "observed_at": (observed_at or datetime.now(timezone.utc)).isoformat(),
        }),
        encoding="utf-8",
    )
    return target


def test_tailscale_bootstrap_recovery_requires_recent_raw_utf8_hash_and_hostname(
    tmp_path: Path,
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    codex_home = Path(environment["codex_home"])
    devspace_home = Path(environment["devspace_home"])
    config = devspace_home / "config.json"
    payload = json.loads(config.read_text(encoding="utf-8"))
    config.write_bytes(json.dumps(payload, indent=2).replace("\n", "\r\n").encode("utf-8"))
    receipt = _write_bootstrap_recovery_receipt(
        codex_home=codex_home,
        devspace_home=devspace_home,
        hostname="device.tailnet.ts.net",
    )

    assert module.stateful_bootstrap_recovery_verified(
        codex_home=codex_home,
        devspace_home=devspace_home,
        registration_url="https://device.tailnet.ts.net/mcp",
        watchdog_identity_probe=lambda **_kwargs: True,
    ) is True

    stale = json.loads(receipt.read_text(encoding="utf-8"))
    stale["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    receipt.write_text(json.dumps(stale), encoding="utf-8")
    assert module.stateful_bootstrap_recovery_verified(
        codex_home=codex_home,
        devspace_home=devspace_home,
        registration_url="https://device.tailnet.ts.net/mcp",
        watchdog_identity_probe=lambda **_kwargs: True,
    ) is False

    _write_bootstrap_recovery_receipt(
        codex_home=codex_home,
        devspace_home=devspace_home,
        hostname="other.tailnet.ts.net",
    )
    assert module.stateful_bootstrap_recovery_verified(
        codex_home=codex_home,
        devspace_home=devspace_home,
        registration_url="https://device.tailnet.ts.net/mcp",
        watchdog_identity_probe=lambda **_kwargs: True,
    ) is False

    _write_bootstrap_recovery_receipt(
        codex_home=codex_home,
        devspace_home=devspace_home,
        hostname="device.tailnet.ts.net",
    )
    config.write_bytes(config.read_bytes() + b"\r\n")
    assert module.stateful_bootstrap_recovery_verified(
        codex_home=codex_home,
        devspace_home=devspace_home,
        registration_url="https://device.tailnet.ts.net/mcp",
        watchdog_identity_probe=lambda **_kwargs: True,
    ) is False


def test_bootstrap_persistence_rejects_recent_once_and_dead_watch_receipts(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    codex_home = Path(environment["codex_home"])
    devspace_home = Path(environment["devspace_home"])
    probe_calls: list[int] = []

    _write_bootstrap_recovery_receipt(
        codex_home=codex_home,
        devspace_home=devspace_home,
        hostname="device.tailnet.ts.net",
        mode="Once",
    )
    assert module.stateful_bootstrap_recovery_verified(
        codex_home=codex_home,
        devspace_home=devspace_home,
        registration_url="https://device.tailnet.ts.net/mcp",
        watchdog_identity_probe=lambda **kwargs: probe_calls.append(kwargs["watchdog_pid"]) or True,
    ) is False
    assert probe_calls == []

    _write_bootstrap_recovery_receipt(
        codex_home=codex_home,
        devspace_home=devspace_home,
        hostname="device.tailnet.ts.net",
        mode="Watch",
    )
    assert module.stateful_bootstrap_recovery_verified(
        codex_home=codex_home,
        devspace_home=devspace_home,
        registration_url="https://device.tailnet.ts.net/mcp",
        watchdog_identity_probe=lambda **_kwargs: False,
    ) is False


def test_windows_watchdog_identity_requires_exact_registration_and_live_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex home"
    script = codex_home / "scripts" / "start_devspace_bootstrap.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("exit 0\n", encoding="utf-8")
    system_root = tmp_path / "Windows"
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"MZ")
    monkeypatch.setenv("SystemRoot", str(system_root))
    expected = module._expected_windows_watchdog_command(codex_home)
    observed: dict[str, object] = {}

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        payload = {
            "registered_command": expected,
            "process_id": 4242,
            "executable_path": str(powershell.resolve()),
            "command_line": (
                f'"{powershell.resolve()}" -NoProfile -File "{script.resolve()}" '
                "-Mode Watch -WatchIntervalSeconds 30"
            ),
        }
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    assert module._windows_watchdog_identity_verified(
        codex_home=codex_home,
        watchdog_pid=4242,
        runner=runner,
        platform_name="nt",
    ) is True
    assert observed["kwargs"]["timeout"] == module.WATCHDOG_IDENTITY_TIMEOUT_SECONDS
    command_text = " ".join(observed["argv"])
    assert "Get-ItemProperty" in command_text
    assert "Get-CimInstance" in command_text
    assert "reg.exe" not in command_text

    def wrong_registration(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        payload = {
            "registered_command": expected + " -Unexpected",
            "process_id": 4242,
            "executable_path": str(powershell.resolve()),
            "command_line": f'"{powershell.resolve()}" -File "{script.resolve()}" -Mode Watch -WatchIntervalSeconds 30',
        }
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    assert module._windows_watchdog_identity_verified(
        codex_home=codex_home,
        watchdog_pid=4242,
        runner=wrong_registration,
        platform_name="nt",
    ) is False


def test_tailscale_manual_confirmation_cannot_bypass_bootstrap_receipt(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    state = module.start_onboarding(
        provider="tailscale",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        hostname_discovery=lambda: "device.tailnet.ts.net",
    )
    state["stages"]["04_reboot_service"] = {
        "status": "user_confirmed",
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }

    evaluated = module.evaluate_stages(
        state,
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )

    assert evaluated["checks"]["bootstrap_recovery_verified"] is False
    assert evaluated["checks"]["restart_persistence_verified"] is False


def test_configure_app_name_is_atomic_and_contains_only_public_name(tmp_path: Path) -> None:
    target = module.configure_app_name(codex_home=tmp_path, app_name="dongju")
    assert json.loads(target.read_text(encoding="utf-8")) == {"app_name": "dongju"}
    assert not list(tmp_path.glob("*.tmp"))


def test_configure_app_name_preserves_existing_non_secret_fields(tmp_path: Path) -> None:
    target = tmp_path / "chatgpt-workspace.json"
    target.write_text(
        json.dumps({"app_name": "old-name", "registration_url": "https://mcp.example.com/mcp", "future": {"enabled": True}}),
        encoding="utf-8",
    )

    module.configure_app_name(codex_home=tmp_path, app_name="dongju")

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "app_name": "dongju",
        "registration_url": "https://mcp.example.com/mcp",
        "future": {"enabled": True},
    }


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
                            "loopback_network": {
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


def test_pre_migration_profile_grant_remains_accepted(tmp_path: Path) -> None:
    profile = tmp_path / "browser-profile"
    preferences = profile / "Default" / "Preferences"
    preferences.parent.mkdir(parents=True)
    preferences.write_text(
        json.dumps(
            {
                "profile": {
                    "content_settings": {
                        "exceptions": {
                            "local_network_access": {
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


def test_profile_split_permissions_require_loopback_allow_and_fail_closed(tmp_path: Path) -> None:
    profile = tmp_path / "browser-profile"
    preferences = profile / "Default" / "Preferences"
    preferences.parent.mkdir(parents=True)
    base = {
        "profile": {
            "content_settings": {
                "exceptions": {
                    "local_network": {"https://chatgpt.com:443,*": {"setting": 1}},
                }
            }
        }
    }
    preferences.write_text(json.dumps(base), encoding="utf-8")
    assert module.browser_profile_local_network_allowed(profile) is False

    base["profile"]["content_settings"]["exceptions"]["loopback_network"] = {
        "https://chatgpt.com:443,*": {"setting": 1}
    }
    preferences.write_text(json.dumps(base), encoding="utf-8")
    assert module.browser_profile_local_network_allowed(profile) is True

    base["profile"]["content_settings"]["exceptions"]["loopback_network"] = {
        "https://chatgpt.com:443,*": {"setting": 2}
    }
    preferences.write_text(json.dumps(base), encoding="utf-8")
    assert module.browser_profile_local_network_allowed(profile) is False


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








def _lean_app_execution_run(environment: dict[str, object]) -> Path:
    project = Path(environment["project"]).resolve()
    proof_line = "Verified project content from the exact onboarding root."
    (project / "README.md").write_text(proof_line + "\n", encoding="utf-8")
    mission = project / "mission.md"
    mission.write_text(
        "Use the registered app to read README.md from this exact project root.\n",
        encoding="utf-8",
    )
    run_dir = Path(environment["codex_home"]) / "state" / "chatgpt-oracle" / "lean-run"
    run_dir.mkdir(parents=True)
    output = run_dir / "output.md"
    output.write_text(
        "Authenticated codex app read completed.\n" + proof_line + "\n",
        encoding="utf-8",
    )
    output_bytes = output.read_bytes()
    state = {
        "schema": "codex.chatgpt.oracle-execution-state/v1",
        "run_id": "lean-run",
        "project_root": str(project),
        "mission": {
            "path": str(mission),
            "sha256": hashlib.sha256(mission.read_bytes()).hexdigest(),
        },
        "selection": {"model": "latest", "effort": "pro", "app_name": "codex"},
        "status": "captured",
        "capture": "durable",
        "semantic_outcome": "unknown",
        "model_check": {
            "verified": True,
            "model": "latest",
            "effort": "pro",
            "source": "oracle-picker-dom-log",
        },
        "artifacts": {
            "output": str(output),
            "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
            "output_bytes": len(output_bytes),
        },
    }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return run_dir


def test_lean_app_onboarding_uses_execute_reconnect_contract(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    mission = Path(environment["project"]) / "mission.md"
    mission.write_text("Read this project and persist the result.\n", encoding="utf-8")

    plan = module.prepare_final_gate(
        root=str(environment["project"]),
        mission_path=mission,
        codex_home=environment["codex_home"],
    )

    assert plan["schema"] == "codex-web-gpt.onboarding-app-read-plan/v1"
    assert "chatgpt_oracle_run.py execute" in plan["execute_command"]
    assert "--project-root" in plan["execute_command"]
    assert "--mission-path" in plan["execute_command"]
    assert "--app-name codex" in plan["execute_command"]
    assert "chatgpt_oracle_run.py reconnect --run-dir" in plan["reconnect_command_template"]
    assert "<PROJECT_FILE_READ_THROUGH_REGISTERED_APP>" in plan["record_command_template"]
    assert "--listing mission.md" not in plan["record_command_template"]
    serialized = json.dumps(plan)
    for retired in ("auditNonce", "read_chunk", "receipt", "fresh"):
        assert retired not in serialized


def test_lean_app_onboarding_records_one_durable_actual_model_result(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    run_dir = _lean_app_execution_run(environment)

    recorded = module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="The authenticated codex app captured the exact-root read result.",
        listing=["README.md"],
        run_dir=run_dir,
        codex_home=environment["codex_home"],
    )

    assert recorded["schema"] == "codex.chatgpt.registered-app-read-result/v1"
    assert recorded["auth_verified"] is True
    assert recorded["actual_model"] == "latest"
    assert recorded["outcome"] == "captured"
    assert recorded["read_proof"]["kind"] == "project-file-content-match"
    assert recorded["read_proof"]["relative_path"] == "README.md"
    state = module.load_state(codex_home=environment["codex_home"])
    assert module._final_gate_receipt(
        Path(environment["codex_home"]), Path(environment["devspace_home"]), state
    ) == recorded
    Path(recorded["output_path"]).write_text("changed after setup\n", encoding="utf-8")
    assert module._final_gate_receipt(
        Path(environment["codex_home"]), Path(environment["devspace_home"]), state
    ) == recorded


def test_lean_app_onboarding_rejects_uncaptured_or_unbound_execution(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    run_dir = _lean_app_execution_run(environment)
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "attention_required"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(module.OnboardingError, match="FINAL_GATE_EXECUTION_NOT_DURABLY_CAPTURED"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence="The authenticated codex app captured the exact-root read result.",
            run_dir=run_dir,
            codex_home=environment["codex_home"],
        )


def test_final_gate_rejects_generic_workspace_open_without_project_file_content(
    tmp_path: Path,
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    run_dir = _lean_app_execution_run(environment)
    output = run_dir / "output.md"
    output.write_text(
        "Workspace opened successfully. The registered app is available.\n",
        encoding="utf-8",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    output_bytes = output.read_bytes()
    state["artifacts"]["output_sha256"] = hashlib.sha256(output_bytes).hexdigest()
    state["artifacts"]["output_bytes"] = len(output_bytes)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(module.OnboardingError, match="FINAL_GATE_PROJECT_READ_NOT_PROVEN"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence="The workspace opened but no file content was captured.",
            listing=["README.md"],
            run_dir=run_dir,
            codex_home=environment["codex_home"],
        )


def test_final_gate_never_accepts_echoed_mission_text_as_project_read_proof(
    tmp_path: Path,
) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    run_dir = _lean_app_execution_run(environment)
    project = Path(environment["project"])
    mission = project / "mission.md"
    output = run_dir / "output.md"
    output.write_text(mission.read_text(encoding="utf-8"), encoding="utf-8")
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    output_bytes = output.read_bytes()
    state["artifacts"]["output_sha256"] = hashlib.sha256(output_bytes).hexdigest()
    state["artifacts"]["output_bytes"] = len(output_bytes)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(module.OnboardingError, match="FINAL_GATE_PROJECT_READ_NOT_PROVEN"):
        module.record_final_gate(
            read_ok=True,
            root=str(project),
            evidence="The mission text was echoed without a separate project-file read.",
            listing=["mission.md"],
            run_dir=run_dir,
            codex_home=environment["codex_home"],
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


def test_final_gate_requires_recorded_lean_app_exact_root_read(tmp_path: Path) -> None:
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

    run_dir = _lean_app_execution_run(environment)
    module.record_final_gate(
        read_ok=True,
        root=str(environment["project"]),
        evidence="The authenticated codex app captured the exact-root read result.",
        listing=["README.md"],
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
def test_final_gate_instructions_describe_the_lean_registered_app_check(
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

    assert "prepare-final-gate" in instructions
    assert "execute" in instructions
    assert "capture" in instructions
    assert "durable" in instructions
    assert "read_chunk" not in instructions




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


def test_prepare_final_gate_rejects_non_utf8_mission(tmp_path: Path) -> None:
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
    mission.write_bytes(b"\xff\xfe")

    with pytest.raises(module.OnboardingError, match="FINAL_GATE_MISSION_UNREADABLE"):
        module.prepare_final_gate(
            root=str(environment["project"]),
            mission_path=mission,
            codex_home=environment["codex_home"],
        )


def test_prepare_final_gate_accepts_large_utf8_mission(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    mission = Path(environment["project"]) / "large-utf8.md"
    mission.write_text("한글 mission\n" * 4096, encoding="utf-8")

    plan = module.prepare_final_gate(
        root=str(environment["project"]),
        mission_path=mission,
        codex_home=environment["codex_home"],
    )

    assert plan["mission_sha256"] == hashlib.sha256(mission.read_bytes()).hexdigest()






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


@pytest.mark.parametrize(
    ("language", "needle", "name_index"),
    [("ko", "사용자 확인 필요", 0), ("en", "Your action is needed", 1)],
)
def test_render_step_is_human_readable_per_language(
    tmp_path: Path,
    language: str,
    needle: str,
    name_index: int,
) -> None:
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
    assert module.UI.STAGE_NAMES[step["current_stage"]][name_index] in rendered
    assert step["current_stage"] not in rendered
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
    run_dir = _lean_app_execution_run(environment)

    with pytest.raises(module.OnboardingError, match="FINAL_GATE_EVIDENCE_INSUFFICIENT"):
        module.record_final_gate(
            read_ok=True,
            root=str(environment["project"]),
            evidence=evidence,
            listing=["README.md"],
            run_dir=run_dir,
            codex_home=environment["codex_home"],
        )

    step = module.next_step(
        codex_home=environment["codex_home"],
        devspace_home=environment["devspace_home"],
        **environment["probes"],
    )
    assert step["done"] is False
    assert step["current_stage"] == "08_final_gate"




def test_valid_lean_final_gate_completes_onboarding_and_stores_listing_sample(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)
    listing = ["README.md", "bin", "tests"]
    run_dir = _lean_app_execution_run(environment)

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
    listing = ["README.md", *(f"entry-{index}" for index in range(15))]
    run_dir = _lean_app_execution_run(environment)

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


def test_final_gate_rejects_non_registered_app_transport(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )

    with pytest.raises(
        module.OnboardingError, match="FINAL_GATE_TRANSPORT_MUST_BE_REGISTERED_APP"
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

    run_dir = _lean_app_execution_run(environment)
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


def test_load_state_migrates_additive_stage_without_erasing_verified_evidence(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    path = module.state_path(codex_home=environment["codex_home"])
    state = json.loads(path.read_text(encoding="utf-8"))
    oracle_evidence = {"kind": "user-confirmed", "confirmed_at": "2026-08-27T00:00:00Z"}
    app_evidence = {"kind": "user-confirmed", "confirmed_at": "2026-08-27T00:01:00Z"}
    final_evidence = {"transport": "regular-non-pro-oracle", "read_ok": True, "evidence": "preserved"}
    state["stages"]["06_oracle_login"] = {"status": "complete", "verified_at": "2026-08-27T00:00:00Z", "evidence": oracle_evidence}
    state["stages"]["07_chatgpt_app"] = {"status": "complete", "verified_at": "2026-08-27T00:01:00Z", "evidence": app_evidence}
    state["stages"]["08_final_gate"] = {"status": "complete", "verified_at": "2026-08-27T00:02:00Z", "evidence": final_evidence}
    state["stages"].pop("06b_local_network_access")
    state["stages"]["09_future_stage"] = {"status": "complete", "verified_at": "2026-08-27T00:03:00Z", "evidence": {"kind": "future"}}
    path.write_text(json.dumps(state), encoding="utf-8")

    migrated = module.load_state(codex_home=environment["codex_home"])

    assert migrated["stages"]["06b_local_network_access"] == {
        "status": "pending",
        "verified_at": None,
        "evidence": None,
    }
    assert migrated["stages"]["06_oracle_login"]["evidence"] == oracle_evidence
    assert migrated["stages"]["07_chatgpt_app"]["evidence"] == app_evidence
    assert migrated["stages"]["08_final_gate"]["evidence"] == final_evidence
    assert migrated["stages"]["09_future_stage"]["evidence"] == {"kind": "future"}


def test_load_state_rejects_banned_state_content_before_migration(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=False)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    path = module.state_path(codex_home=environment["codex_home"])
    state = json.loads(path.read_text(encoding="utf-8"))
    state["stages"].pop("06b_local_network_access")
    state["stages"]["09_future_stage"] = {"status": "pending", "token": "must-not-be-stored"}
    path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(module.OnboardingError, match="ONBOARDING_STATE_MUST_NOT_CARRY_SECRETS"):
        module.load_state(codex_home=environment["codex_home"])


def _final_gate_record(root: Path, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema": module.APP_READ_RESULT_SCHEMA,
        "read_ok": True,
        "root": str(root),
        "app_name": "codex",
        "auth_verified": True,
        "actual_model": "latest",
        "outcome": "captured",
        "semantic_outcome": "unknown",
        "evidence": "The exact project directory was listed.",
        "listing_sample": ["README.md"],
        "read_proof": {
            "kind": "project-file-content-match",
            "relative_path": "README.md",
            "excerpt_sha256": "0" * 64,
            "source_bytes": 64,
        },
        "recorded_at": "2026-08-22T00:00:00Z",
        "transport": "registered-app",
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize(
    "tampered_record",
    [
        lambda _environment, _tmp_path: {"read_ok": True},
        lambda environment, _tmp_path: _final_gate_record(environment["project"], auth_verified=False),
        lambda environment, _tmp_path: _final_gate_record(environment["project"], actual_model=""),
        lambda environment, _tmp_path: _final_gate_record(environment["project"], outcome="failed"),
        lambda environment, _tmp_path: _final_gate_record(environment["project"], evidence="too short"),
        lambda environment, _tmp_path: _final_gate_record(environment["project"], transport="pro-devspace"),
        lambda environment, _tmp_path: _final_gate_record(environment["project"], read_proof=None),
        lambda _environment, tmp_path: _final_gate_record(tmp_path / "outside-allowed-roots"),
        lambda environment, _tmp_path: _final_gate_record(environment["project"], recorded_at=""),
        lambda environment, _tmp_path: _final_gate_record(environment["project"], app_name="other"),
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




def test_final_gate_receipt_distinguishes_honest_and_tampered_evidence(tmp_path: Path) -> None:
    environment = _wizard_environment(tmp_path, ready=True)
    module.start_onboarding(
        provider="custom",
        registration_url="https://mcp.example.com/mcp",
        roots=[str(environment["project"])],
        codex_home=environment["codex_home"],
    )
    _confirm_ready_manual_stages(environment)
    run_dir = _lean_app_execution_run(environment)
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
        environment["project"], auth_verified=False
    )
    assert module._final_gate_receipt(environment["codex_home"], environment["devspace_home"], honest) is None








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

    # Human mode resumes the saved wizard. Machine mode retains the explicit
    # duplicate-start diagnostic for automation callers.
    assert module.main(start_arguments) == 4
    assert capsys.readouterr().out
    assert module.main(["--json", *start_arguments]) == 2
    assert "ONBOARDING_ALREADY_STARTED" in capsys.readouterr().out

    state_file = module.state_path(codex_home=codex_home)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state.pop("provider")
    state_file.write_text(json.dumps(state), encoding="utf-8")

    assert module.main(["--json", *start_arguments]) == 2
    assert "ONBOARDING_STATE_CORRUPT" in capsys.readouterr().out

    (codex_home / "receipts" / "codexpro-automation-1.json").unlink()
    assert module.main([*start_arguments, "--reset"]) != 2
    assert capsys.readouterr().out
