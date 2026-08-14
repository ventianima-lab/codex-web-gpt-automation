from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "skills" / "chatgpt-workspace-setup" / "scripts" / "devspace_tailscale_setup.py"


def load_module():
    spec = importlib.util.spec_from_file_location("devspace_tailscale_setup_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def config(tmp_path: Path):
    module = load_module()
    root = tmp_path / "project"
    root.mkdir()
    return module, module.validate_config([str(root)], "device.tailnet.ts.net")


def test_roots_are_narrow_and_registration_url_is_exact(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    assert current.registration_url == "https://device.tailnet.ts.net/mcp"
    with pytest.raises(module.SetupError, match="ALLOWED_ROOT_REQUIRED"):
        module.validate_config([], "device.tailnet.ts.net")
    with pytest.raises(module.SetupError, match="ALLOWED_ROOT_TOO_BROAD"):
        module.validate_config([str(Path(tmp_path.anchor))], "device.tailnet.ts.net")


def test_setup_plan_has_no_secrets_and_is_explicit_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module, current = config(tmp_path)
    bash = tmp_path / "bash.exe"
    bash.write_text("", encoding="utf-8")
    monkeypatch.setenv("DEVSPACE_GIT_BASH", str(bash))
    plan = module.setup_plan(current, platform_name="nt")
    text = json.dumps(plan)
    assert "password" not in text.lower()
    assert "token" not in text.lower()
    assert plan["registration_url"] == "https://device.tailnet.ts.net/mcp"
    assert plan["recommended_app_name"] == "codex"
    assert plan["managed_service_environment"] == {
        "DEVSPACE_TOOL_MODE": "full",
        "DEVSPACE_OAUTH_SCOPES": "devspace,offline_access",
    }
    assert plan["startup_watchdog"] == {
        "windows_mode": "per-user login watchdog",
        "health_interval_seconds": 300,
        "runtime_root_source": str(Path.home() / ".devspace" / "config.json"),
    }
    assert plan["devspace_init"][1:3] == [
        "-lc",
        "exec npx --yes @waishnav/devspace@1.0.4 init",
    ]


def test_setup_subset_preview_preserves_every_persisted_allowed_root(tmp_path: Path) -> None:
    module = load_module()
    existing = tmp_path / "existing"
    requested = tmp_path / "requested"
    existing.mkdir()
    requested.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"allowedRoots": [str(existing)]}), encoding="utf-8")
    subset = module.validate_config([str(requested)], "device.tailnet.ts.net")

    merged, preserved = module.merge_persisted_setup_roots(subset, config_path)
    plan = module.setup_plan(
        merged,
        requested_roots=subset.roots,
        preserved_existing_roots=preserved,
        platform_name="posix",
    )

    assert plan["requested_roots"] == [str(requested.resolve())]
    assert plan["preserved_existing_roots"] == [str(existing.resolve())]
    assert plan["allowed_roots"] == [str(existing.resolve()), str(requested.resolve())]
    assert plan["root_merge_applied"] is True


def test_existing_setup_config_is_backed_up_and_atomically_replaced_without_init(tmp_path: Path) -> None:
    module = load_module()
    existing = tmp_path / "existing"
    requested = tmp_path / "오사카여행"
    existing.mkdir()
    requested.mkdir()
    current = module.validate_config(
        [str(existing), str(requested)],
        "device.tailnet.ts.net",
    )
    config_path = tmp_path / "config.json"
    original = {"allowedRoots": [str(requested)], "toolMode": "full", "custom": "preserved"}
    config_path.write_text(json.dumps(original), encoding="utf-8")

    backup = module.persist_existing_setup_config(config_path, current)
    persisted = json.loads(config_path.read_text(encoding="utf-8"))

    assert backup.is_file()
    assert config_path.read_bytes().isascii()
    assert json.loads(backup.read_text(encoding="utf-8")) == original
    assert persisted["allowedRoots"] == [str(existing.resolve()), str(requested.resolve())]
    assert persisted["publicBaseUrl"] == "https://device.tailnet.ts.net"
    assert persisted["port"] == 7676
    assert persisted["toolMode"] == "full"
    assert persisted["custom"] == "preserved"


def test_existing_setup_preserves_nondefault_funnel_port_in_public_origin(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "project"
    root.mkdir()
    current = module.validate_config(
        [str(root)], "device.tailnet.ts.net", public_port=8443
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"allowedRoots": [str(root)]}), encoding="utf-8")

    module.persist_existing_setup_config(config_path, current)

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["publicBaseUrl"] == "https://device.tailnet.ts.net:8443"


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="PowerShell is unavailable")
def test_unicode_setup_config_parses_with_windows_powershell_default_get_content(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "오사카여행"
    root.mkdir()
    current = module.validate_config([str(root)], "device.tailnet.ts.net")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"allowedRoots": []}), encoding="utf-8")

    module.persist_existing_setup_config(config_path, current)
    literal_path = str(config_path).replace("'", "''")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"$parsed = Get-Content -Raw -LiteralPath '{literal_path}' | ConvertFrom-Json; "
            "if ($parsed.allowedRoots.Count -ne 1) { exit 2 }",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_existing_bootstrap_config_is_only_a_synchronized_mirror(tmp_path: Path) -> None:
    module = load_module()
    roots = [tmp_path / "one", tmp_path / "오사카여행"]
    for root in roots:
        root.mkdir()
    current = module.validate_config([str(root) for root in roots], "device.tailnet.ts.net")
    bootstrap = tmp_path / "bootstrap.json"
    original = {
        "schema": "codexpro.devspace-bootstrap/v1",
        "python_path": sys.executable,
        "roots": [str(tmp_path / "stale")],
        "hostname": "old.tailnet.ts.net",
        "local_port": 7000,
        "public_port": 8443,
    }
    bootstrap.write_text(json.dumps(original), encoding="utf-8")

    backup = module.synchronize_existing_bootstrap_config(bootstrap, current)
    persisted = json.loads(bootstrap.read_text(encoding="utf-8"))

    assert backup is not None and backup.is_file()
    assert bootstrap.read_bytes().isascii()
    assert json.loads(backup.read_text(encoding="utf-8")) == original
    assert persisted["roots"] == [str(root.resolve()) for root in roots]
    assert persisted["hostname"] == "device.tailnet.ts.net"
    assert persisted["local_port"] == 7676
    assert persisted["public_port"] == 443
    assert persisted["python_path"] == sys.executable


def test_doctor_orders_local_funnel_public_and_manual_failure_branch(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    seen: list[str] = []

    class Response:
        def __init__(self, status: int = 200):
            self.status = status
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def opener(request, timeout):
        seen.append(request.full_url)
        return Response()

    def runner(argv, **kwargs):
        assert argv == ["tailscale", "funnel", "status", "--json"]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"Web": {current.hostname + ":443": {"Proxy": f"http://127.0.0.1:{current.local_port}"}}}),
            stderr="",
        )

    report = module.doctor(current, opener=opener, runner=runner, chatgpt_call_failed=True)
    assert seen == [current.local_mcp_url, current.registration_url]
    assert report["next_action"] == "POST_REGISTER_REFRESH_OR_EXTERNAL_APP_CHECK"
    assert "run post-register once" in report["message"]
    assert "do not automate or repeat app registration" in report["message"]
    assert report["registration_url"] == current.registration_url


def test_doctor_returns_local_failure_before_funnel_or_public(tmp_path: Path) -> None:
    module, current = config(tmp_path)

    def opener(request, timeout):
        raise OSError("unavailable")

    def runner(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    report = module.doctor(current, opener=opener, runner=runner)
    assert report["next_action"] == "CHECK_DEVSPACE_LOCAL_SERVICE"


def test_doctor_reports_persisted_allowed_root_mismatch(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    config_path = tmp_path / "config.json"
    other = tmp_path / "other"
    other.mkdir()
    config_path.write_text(json.dumps({"allowedRoots": [str(other.resolve())]}), encoding="utf-8")

    class Response:
        status = 401
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    report = module.doctor(
        current,
        opener=lambda *args, **kwargs: Response(),
        config_path=config_path,
    )

    assert report["next_action"] == "CHECK_DEVSPACE_ALLOWED_ROOTS"
    assert report["config"]["missing_roots"] == [str(current.roots[0])]


def test_module_has_no_chatgpt_ui_or_browser_automation() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    for forbidden in (
        "agbrowse",
        "selenium",
        "playwright",
        "tab-switch",
        ".click(",
        "chatgpt.com",
    ):
        assert forbidden not in source


def test_secret_text_is_redacted_from_funnel_diagnostics() -> None:
    module = load_module()

    def runner(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="owner_token=very-secret password: also-secret")

    report = module.funnel_status(runner=runner)
    assert "very-secret" not in report["stderr"]
    assert "also-secret" not in report["stderr"]
    assert "[REDACTED]" in report["stderr"]


def test_doctor_rejects_404_and_unrelated_funnel_mapping(tmp_path: Path) -> None:
    module, current = config(tmp_path)

    class NotFound:
        status = 404
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    local_fail = module.http_probe(current.local_mcp_url, opener=lambda *args, **kwargs: NotFound())
    assert local_fail["ok"] is False
    report = module.funnel_status(
        current,
        runner=lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"Web": {"other.ts.net:443": {"Proxy": "http://127.0.0.1:9999"}}}),
            stderr="",
        ),
    )
    assert report["ok"] is False
    assert report["error"] == "TAILSCALE_FUNNEL_MAPPING_MISSING"


def test_nondefault_public_port_is_explicit_and_existing_mapping_is_not_overwritten(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "project"
    root.mkdir()
    current = module.validate_config([str(root)], "device.tailnet.ts.net", public_port=8443)
    assert current.registration_url == "https://device.tailnet.ts.net:8443/mcp"
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"Web": {current.hostname + ":8443": {"Proxy": "http://127.0.0.1:9999"}}}),
            stderr="",
        )

    with pytest.raises(module.SetupError, match="TAILSCALE_FUNNEL_PORT_IN_USE"):
        module.apply_setup(current, runner=runner, popen_factory=lambda *args, **kwargs: None)
    assert calls == [["tailscale", "funnel", "status", "--json"]]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows STARTUPINFO exists only on Windows")
def test_windows_launch_is_hidden() -> None:
    module = load_module()
    kwargs = module.windows_subprocess_kwargs(platform_name="nt")
    assert kwargs["creationflags"] & module.subprocess.CREATE_NO_WINDOW


def test_recover_starts_missing_service_then_restores_exact_funnel(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    probes = 0
    calls: list[list[str]] = []
    launches: list[list[str]] = []

    class Response:
        status = 401
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def opener(request, timeout):
        nonlocal probes
        probes += 1
        if probes == 1:
            raise OSError("service is down")
        return Response()

    def runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[:4] == ["tailscale", "funnel", "status", "--json"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"Web": {current.hostname + ":443": {"Proxy": f"http://127.0.0.1:{current.local_port}"}}}),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = module.recover_service(
        current,
        opener=opener,
        runner=runner,
        popen_factory=lambda argv, **kwargs: launches.append(list(argv)),
        sleeper=lambda _: None,
        platform_name="posix",
    )

    assert report["ok"] is True
    assert report["service_started"] is True
    assert launches == [["npx", "--yes", module.DEVSPACE_PACKAGE, "serve"]]
    assert any("--stop-exact-service" in call for call in calls)
    assert any("--confirm-service-restarted" in call for call in calls)


def test_post_register_always_recycles_service_and_preserves_oauth_state(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    calls: list[list[str]] = []
    launches: list[tuple[list[str], dict[str, str] | None]] = []

    class Response:
        status = 401
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def runner(argv, **kwargs):
        calls.append(list(argv))
        if argv == ["tailscale", "funnel", "status", "--json"]:
            funnel_status_reads = sum(
                call == ["tailscale", "funnel", "status", "--json"] for call in calls
            )
            if funnel_status_reads == 2:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"Web": {}}), stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"Web": {current.hostname + ":443": {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{current.local_port}"}}}}}),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = module.refresh_after_app_registration(
        current,
        opener=lambda *args, **kwargs: Response(),
        runner=runner,
        popen_factory=lambda argv, **kwargs: launches.append((list(argv), kwargs.get("env"))),
        sleeper=lambda _: None,
        platform_name="posix",
    )

    assert report["ok"] is True
    assert report["service_restarted"] is True
    assert report["credentials_preserved"] is True
    assert report["exact_funnel_recycled"] is True
    assert report["funnel_recycle_scope"] == "https:443"
    assert report["next_action"] == "VERIFY_REGISTERED_CHATGPT_APP_WITH_ORACLE"
    assert "different connector" in report["verification_boundary"]
    assert calls[0] == module.devspace_native_argv()
    assert calls[1] == module.devspace_compat_argv()
    assert calls[2] == module.devspace_compat_argv(stop_exact_service=True)
    assert calls[3] == module.devspace_compat_argv(confirm_restarted=True)
    assert ["tailscale", "funnel", "--bg", "--https=443", "off"] in calls
    assert [
        "tailscale", "funnel", "--bg", "--https=443", f"http://127.0.0.1:{current.local_port}"
    ] in calls
    assert launches[0][0] == ["npx", "--yes", module.DEVSPACE_PACKAGE, "serve"]
    assert launches[0][1]["DEVSPACE_TOOL_MODE"] == "full"
    assert launches[0][1]["DEVSPACE_OAUTH_SCOPES"] == "devspace,offline_access"


def test_post_register_preserves_shared_funnel_port(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    calls: list[list[str]] = []

    class Response:
        status = 401
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def runner(argv, **kwargs):
        calls.append(list(argv))
        if argv == ["tailscale", "funnel", "status", "--json"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"Web": {current.hostname + ":443": {"Handlers": {
                    "/": {"Proxy": f"http://127.0.0.1:{current.local_port}"},
                    "/other": {"Proxy": "http://127.0.0.1:9000"},
                }}}}),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = module.refresh_after_app_registration(
        current,
        opener=lambda *args, **kwargs: Response(),
        runner=runner,
        popen_factory=lambda *args, **kwargs: SimpleNamespace(),
        sleeper=lambda _: None,
        platform_name="posix",
    )

    assert report["ok"] is True
    assert report["exact_funnel_recycled"] is False
    assert not any(call[-1:] == ["off"] for call in calls)


def test_parser_exposes_explicit_post_register_command() -> None:
    module = load_module()
    parsed = module.parser().parse_args([
        "post-register",
        "--root",
        str(Path.cwd()),
        "--hostname",
        "device.tailnet.ts.net",
    ])
    assert parsed.command == "post-register"


def test_setup_applies_hash_validated_devspace_compat_before_service_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, current = config(tmp_path)
    bash = tmp_path / "bash.exe"
    bash.write_text("", encoding="utf-8")
    monkeypatch.setenv("DEVSPACE_GIT_BASH", str(bash))
    calls: list[list[str]] = []
    call_kwargs: list[dict] = []
    launched: list[tuple[list[str], dict[str, str] | None]] = []
    funnel_reads = 0

    class Response:
        status = 401
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def runner(argv, **kwargs):
        nonlocal funnel_reads
        calls.append(list(argv))
        call_kwargs.append(dict(kwargs))
        if argv == ["tailscale", "funnel", "status", "--json"]:
            funnel_reads += 1
            web = {} if funnel_reads < 3 else {
                current.hostname + ":443": {"Proxy": f"http://127.0.0.1:{current.local_port}"}
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps({"Web": web}), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    module.apply_setup(
        current,
        opener=lambda *args, **kwargs: Response(),
        runner=runner,
        popen_factory=lambda argv, **kwargs: launched.append((list(argv), kwargs.get("env"))),
        sleeper=lambda _: None,
        platform_name="nt",
        owner_password_reviewer=lambda: {"ok": True},
        terminal_check=lambda: True,
    )

    assert calls[1][1:3] == [
        "-lc",
        "exec npx --yes @waishnav/devspace@1.0.4 init",
    ]
    assert "creationflags" not in call_kwargs[1]
    assert "startupinfo" not in call_kwargs[1]
    assert calls[2] == module.devspace_native_argv()
    assert calls[3] == module.devspace_compat_argv()
    assert calls[4] == module.devspace_compat_argv(stop_exact_service=True)
    assert calls[5] == module.devspace_compat_argv(confirm_restarted=True)
    assert launched and launched[0][0][1:3] == [
        "-lc",
        "exec npx --yes @waishnav/devspace@1.0.4 serve",
    ]
    assert launched[0][1]["DEVSPACE_TOOL_MODE"] == "full"
    assert launched[0][1]["DEVSPACE_OAUTH_SCOPES"] == "devspace,offline_access"


def test_windows_startup_watchdog_registration_is_hidden_and_deterministic(tmp_path: Path) -> None:
    module = load_module()
    script = tmp_path / "scripts" / "start_devspace_bootstrap.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("exit 0\n", encoding="utf-8")
    runs: list[tuple[list[str], dict]] = []
    launches: list[tuple[list[str], dict]] = []

    def runner(argv, **kwargs):
        runs.append((list(argv), kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def popen(argv, **kwargs):
        launches.append((list(argv), kwargs))
        return SimpleNamespace(pid=123)

    result = module.register_windows_bootstrap_watchdog(
        codex_home=tmp_path,
        runner=runner,
        popen_factory=popen,
        platform_name="nt",
    )

    assert result["mode"] == "per-user-login-watchdog"
    assert result["watch_interval_seconds"] == 300
    assert runs[0][0][:3] == [
        "reg.exe",
        "ADD",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
    ]
    assert module.WINDOWS_BOOTSTRAP_RUN_NAME in runs[0][0]
    command = runs[0][0][runs[0][0].index("/d") + 1]
    assert "-Mode Watch -WatchIntervalSeconds 300" in command
    assert str(script.resolve()) in command
    assert launches[0][0][-4:] == ["-Mode", "Watch", "-WatchIntervalSeconds", "300"]
    assert launches[0][1]["stdin"] is subprocess.DEVNULL
    assert launches[0][1]["stdout"] is subprocess.DEVNULL
    assert launches[0][1]["stderr"] is subprocess.DEVNULL


def test_first_init_refuses_noninteractive_secret_capture_before_launch(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"Web": {}}), stderr="")

    with pytest.raises(module.SetupError, match="FIRST_INIT_REQUIRES_INTERACTIVE_TTY"):
        module.apply_setup(
            current,
            runner=runner,
            terminal_check=lambda: False,
        )
    assert calls == [["tailscale", "funnel", "status", "--json"]]


def test_public_route_retries_bounded_funnel_propagation(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    public_calls = 0
    sleeps: list[float] = []

    class Response:
        status = 401
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def opener(request, timeout):
        nonlocal public_calls
        if request.full_url == current.local_mcp_url:
            return Response()
        public_calls += 1
        if public_calls == 1:
            raise OSError("relay still propagating")
        return Response()

    status = json.dumps({"Web": {current.hostname + ":443": {
        "Proxy": f"http://127.0.0.1:{current.local_port}"
    }}})
    report = module.ensure_public_route(
        current,
        opener=opener,
        runner=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=status, stderr=""),
        sleeper=sleeps.append,
    )

    assert report["ok"] is True
    assert public_calls == 2
    assert sleeps == [2.0]


def test_owner_password_review_keeps_or_atomically_replaces_secret(tmp_path: Path) -> None:
    module = load_module()
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"ownerToken": "generated-high-entropy-owner-token"}), encoding="utf-8")
    shown: list[str] = []

    kept = module.review_owner_password_interactive(
        auth_path=auth,
        input_fn=lambda _prompt: "",
        output_fn=shown.append,
        interactive=True,
    )
    assert kept["changed"] is False
    assert "ownerToken" not in json.dumps(kept)
    assert shown.count("generated-high-entropy-owner-token") == 1

    answers = iter(["Better-Owner-Password-2026!", "Better-Owner-Password-2026!"])
    shown.clear()
    changed = module.review_owner_password_interactive(
        auth_path=auth,
        input_fn=lambda _prompt: "custom",
        getpass_fn=lambda _prompt: next(answers),
        output_fn=shown.append,
        interactive=True,
    )
    assert changed["changed"] is True
    assert json.loads(auth.read_text(encoding="utf-8"))["ownerToken"] == "Better-Owner-Password-2026!"
    assert shown.count("Better-Owner-Password-2026!") == 1
    assert not list(tmp_path.glob("*.tmp-*"))


def test_owner_password_review_requires_tty_and_rejects_numeric_only(tmp_path: Path) -> None:
    module = load_module()
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"ownerToken": "generated-high-entropy-owner-token"}), encoding="utf-8")
    with pytest.raises(module.SetupError, match="REQUIRES_INTERACTIVE_TTY"):
        module.review_owner_password_interactive(auth_path=auth, interactive=False)
    with pytest.raises(module.SetupError, match="STRENGTH_INVALID"):
        module.review_owner_password_interactive(
            auth_path=auth,
            input_fn=lambda _prompt: "custom",
            getpass_fn=lambda _prompt: "0" * 16,
            output_fn=lambda _value: None,
            interactive=True,
        )


def test_posix_setup_invokes_pinned_devspace_directly(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    plan = module.setup_plan(current, platform_name="posix")
    assert plan["devspace_init"] == ["npx", "--yes", "@waishnav/devspace@1.0.4", "init"]
    assert plan["devspace_serve"] == ["npx", "--yes", "@waishnav/devspace@1.0.4", "serve"]


def test_tailscale_hostname_is_discovered_from_status_json() -> None:
    module = load_module()
    result = SimpleNamespace(returncode=0, stdout=json.dumps({
        "Self": {"DNSName": "macmini.tailnet.ts.net."},
        "Peer": {"peer": {"HostName": "오사카-PC"}},
    }, ensure_ascii=False), stderr="")
    seen: dict[str, object] = {}

    def runner(*args, **kwargs):
        seen.update(kwargs)
        return result

    assert module.discover_tailscale_hostname(runner=runner) == "macmini.tailnet.ts.net"
    assert seen["encoding"] == "utf-8"
    assert seen["errors"] == "strict"


def test_ensure_public_route_restores_missing_mapping_after_exact_local_health(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    calls: list[list[str]] = []
    status_reads = iter((
        {"Web": {}},
        {"Web": {current.hostname + ":443": {"Proxy": f"http://127.0.0.1:{current.local_port}"}}},
    ))

    class Response:
        status = 401
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def runner(argv, **kwargs):
        calls.append(list(argv))
        if argv == ["tailscale", "funnel", "status", "--json"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(next(status_reads)), stderr="")
        assert argv == [
            "tailscale", "funnel", "--bg", "--https=443",
            f"http://127.0.0.1:{current.local_port}",
        ]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = module.ensure_public_route(current, opener=lambda *args, **kwargs: Response(), runner=runner)
    assert report["ok"] is True
    assert report["changed"] is True
    assert calls.count(["tailscale", "funnel", "status", "--json"]) == 2


def test_ensure_public_route_is_idempotent_when_mapping_matches(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    calls: list[list[str]] = []

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def runner(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"Web": {current.hostname + ":443": {"Proxy": f"http://127.0.0.1:{current.local_port}"}}}),
            stderr="",
        )

    report = module.ensure_public_route(current, opener=lambda *args, **kwargs: Response(), runner=runner)
    assert report["changed"] is False
    assert calls == [
        ["tailscale", "funnel", "status", "--json"],
        ["tailscale", "funnel", "status", "--json"],
    ]


def test_wait_for_local_service_rejects_port_without_mcp_health(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    sleeps: list[float] = []

    with pytest.raises(module.SetupError, match="DEVSPACE_LOCAL_SERVICE_NOT_READY"):
        module.wait_for_local_service(
            current,
            opener=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("not MCP")),
            sleeper=sleeps.append,
            attempts=3,
            delay_seconds=0.25,
        )
    assert sleeps == [0.25, 0.25]


def test_doctor_reports_full_mode_and_advises_on_explicit_nonfull_config(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"allowedRoots": [str(current.roots[0])], "toolMode": "restricted"}),
        encoding="utf-8",
    )

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    report = module.doctor(current, opener=lambda *args, **kwargs: Response(), config_path=config_path)
    assert report["tool_mode"] == {
        "required": "full",
        "managed_launch": "full",
        "configured": "restricted",
        "effective": None,
        "effective_observable": False,
    }
    assert report["next_action"] == "CHECK_DEVSPACE_TOOL_MODE"


def test_doctor_reports_persisted_full_mode_without_guessing_process_environment(tmp_path: Path) -> None:
    module, current = config(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"allowedRoots": [str(current.roots[0])], "tool_mode": "full"}),
        encoding="utf-8",
    )

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    report = module.doctor(
        current,
        opener=lambda *args, **kwargs: Response(),
        config_path=config_path,
        runner=lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"Web": {current.hostname + ":443": {"Proxy": f"http://127.0.0.1:{current.local_port}"}}}),
            stderr="",
        ),
    )
    assert report["tool_mode"]["configured"] == "full"
    assert report["tool_mode"]["effective"] is None
    assert report["tool_mode"]["effective_observable"] is False
