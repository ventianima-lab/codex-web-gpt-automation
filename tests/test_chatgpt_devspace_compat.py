from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest



MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_devspace_compat.py"


@pytest.fixture(autouse=True)
def isolate_compat_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a compatibility test write the user's restart marker."""
    monkeypatch.setenv("CODEX_DEVSPACE_COMPAT_STATE_ROOT", str(tmp_path / "compat-state"))


def load_compat():
    name = "chatgpt_devspace_compat_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def prepare_service_package(
    compat,
    package: Path,
    *,
    version: str | None = None,
    cli_bytes: bytes = b"#!/usr/bin/env node\n",
    patched_bytes: bytes = b"patched service bytes\n",
    replace_patches: bool = True,
) -> Path:
    (package / "dist").mkdir(parents=True, exist_ok=True)
    (package / "package.json").write_text(
        json.dumps({
            "name": "@waishnav/devspace",
            "version": version or compat.SUPPORTED_VERSION,
        }),
        encoding="utf-8",
    )
    cli = package / "dist" / "cli.js"
    cli.write_bytes(cli_bytes)
    if replace_patches:
        (package / "dist" / "server.js").write_bytes(patched_bytes)
        compat.PATCHES = {
            "dist/server.js": {
                "patch": "unused.patch",
                "pristine": digest(b"pristine service bytes\n"),
                "patched": digest(patched_bytes),
            }
        }
    return cli


def fake_node_executable(tmp_path: Path) -> Path:
    node = tmp_path / ("node.exe" if os.name == "nt" else "node")
    node.write_bytes(b"test node executable\n")
    return node


def test_compat_tests_use_an_isolated_restart_marker(tmp_path: Path) -> None:
    compat = load_compat()

    assert compat.compat_state_root() == (tmp_path / "compat-state").resolve()
    assert compat.restart_marker_path().parent == (tmp_path / "compat-state").resolve()


def test_native_runtime_probe_loads_exact_binding_and_fails_actionably(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "devspace"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": compat.SUPPORTED_VERSION}), encoding="utf-8")
    calls: list[tuple[list[str], dict]] = []

    def passing(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = compat.check_native_runtime(package_root=package, runner=passing)
    assert report["status"] == "loadable"
    assert "better-sqlite3" in calls[0][0][2]
    assert calls[0][1]["cwd"] == str(package.resolve())

    def failing(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="Could not locate the bindings file")

    with pytest.raises(compat.DevSpaceCompatError) as failure:
        compat.check_native_runtime(package_root=package, runner=failing)
    assert failure.value.code == "DEVSPACE_NATIVE_BINDING_UNAVAILABLE"
    assert "install-scripts" in failure.value.evidence["next_action"]


def test_native_prepare_is_exact_and_does_not_widen_script_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat = load_compat()
    workspace = tmp_path / "npx"
    package = workspace / "node_modules" / "@waishnav" / "devspace"
    dependency = workspace / "node_modules" / "better-sqlite3"
    dependency.mkdir(parents=True)
    package.mkdir(parents=True)
    package.joinpath("package.json").write_text(json.dumps({"version": compat.SUPPORTED_VERSION}), encoding="utf-8")
    dependency.joinpath("package.json").write_text(json.dumps({
        "name": compat.NATIVE_DEPENDENCY_NAME, "version": compat.NATIVE_DEPENDENCY_VERSION,
        "scripts": {"install": compat.NATIVE_DEPENDENCY_INSTALL_SCRIPT},
    }), encoding="utf-8")
    workspace.joinpath("package-lock.json").write_text(json.dumps({
        "lockfileVersion": 3,
        "packages": {f"node_modules/{compat.NATIVE_DEPENDENCY_NAME}": {
            "version": compat.NATIVE_DEPENDENCY_VERSION, "integrity": compat.NATIVE_DEPENDENCY_INTEGRITY,
            "resolved": compat.NATIVE_DEPENDENCY_RESOLVED, "hasInstallScript": True,
        }},
    }), encoding="utf-8")
    policy_path = workspace / "package.json"
    policy_path.write_text(json.dumps({"allowScripts": {"preserved@1.0.0": False}}), encoding="utf-8")
    monkeypatch.setattr(compat.shutil, "which", lambda name: "node.exe" if name == "node" else "npm.cmd" if name in {"npm", "npm.cmd"} else None)
    rebuilt = False
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        nonlocal rebuilt
        calls.append(list(argv))
        if argv[0] == "node.exe":
            return SimpleNamespace(returncode=0 if rebuilt else 1, stdout="", stderr="missing binding")
        if argv[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="12.0.0\n", stderr="")
        if "install-scripts" in argv:
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
            payload["allowScripts"][f"{compat.NATIVE_DEPENDENCY_NAME}@{compat.NATIVE_DEPENDENCY_VERSION}"] = True
            policy_path.write_text(json.dumps(payload), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "rebuild" in argv:
            rebuilt = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(argv)

    report = compat.prepare_native_runtime(package_root=package, runner=runner)
    assert report["checks"][0]["status"] == "rebuilt-and-loadable"
    assert json.loads(policy_path.read_text(encoding="utf-8"))["allowScripts"] == {
        "preserved@1.0.0": False,
        f"{compat.NATIVE_DEPENDENCY_NAME}@{compat.NATIVE_DEPENDENCY_VERSION}": True,
    }
    assert any("install-scripts" in call for call in calls)
    assert any("rebuild" in call for call in calls)


def test_oauth_refresh_replay_probe_is_isolated_and_fail_closed(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "devspace"
    package.mkdir()
    calls: list[tuple[list[str], dict]] = []
    expected = {
        "ok": True,
        "replayed_same_pair": True,
        "mismatch_rejected": True,
        "revoke_invalidated": True,
        "expired_rejected": True,
    }

    def passing(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout=json.dumps(expected), stderr="")

    report = compat.check_oauth_refresh_replay(package_root=package, runner=passing)
    assert report["status"] == "bounded-replay-verified"
    assert calls[0][1]["cwd"] == str(package.resolve())
    source = calls[0][0][-1]
    assert "fs.mkdtempSync" in source
    assert "fs.rmSync(state" in source
    assert "wrong-client" in source
    assert "other.test" in source
    assert "refresh_token" not in calls[0][1]

    def failing(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="synthetic failure")

    with pytest.raises(compat.DevSpaceCompatError) as failure:
        compat.check_oauth_refresh_replay(package_root=package, runner=failing)
    assert failure.value.code == "DEVSPACE_OAUTH_REFRESH_REPLAY_CHECK_FAILED"

    def timing_out(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with pytest.raises(compat.DevSpaceCompatError) as timeout:
        compat.check_oauth_refresh_replay(package_root=package, runner=timing_out)
    assert timeout.value.code == "DEVSPACE_OAUTH_REFRESH_REPLAY_CHECK_TIMEOUT"
    assert timeout.value.evidence == {
        "root": str(package.resolve()),
        "timeout_seconds": 30,
    }


def test_large_read_bridge_probe_is_utf8_bounded_and_fail_closed(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "devspace"
    (package / "dist").mkdir(parents=True)
    (package / "dist" / "server.js").write_text(
        "export async function readUtf8Chunk(path, offsetBytes, limitBytes) {\n"
        "  return {path, offsetBytes, limitBytes};\n"
        "}\n"
        "function serverInstructions() {}\n",
        encoding="utf-8",
    )
    calls: list[tuple[list[str], dict]] = []
    expected = {
        "ok": True,
        "reconstructed": True,
        "utf8_boundary_safe": True,
        "max_chunk_bytes": 24576,
    }

    def passing(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout=json.dumps(expected), stderr="")

    report = compat.check_large_read_bridge(package_root=package, runner=passing)
    assert report["status"] == "chunk-reconstruction-verified"
    assert calls[0][1]["cwd"] == str(package.resolve())
    source = calls[0][0][-1]
    assert "readUtf8Chunk" in source
    assert 'import { readUtf8Chunk } from "./dist/server.js"' not in source
    assert "return {path, offsetBytes, limitBytes};" in source
    assert '"\\uAC00".repeat(24000)' in source
    assert "chunks.join" in source
    assert "fs.rmSync" in source

    def failing(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="synthetic failure")

    with pytest.raises(compat.DevSpaceCompatError) as failure:
        compat.check_large_read_bridge(package_root=package, runner=failing)
    assert failure.value.code == "DEVSPACE_LARGE_READ_BRIDGE_CHECK_FAILED"


def test_large_read_bridge_probe_rejects_missing_exact_installed_function(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "devspace"
    (package / "dist").mkdir(parents=True)
    (package / "dist" / "server.js").write_text("export const nope = true;\n", encoding="utf-8")

    with pytest.raises(compat.DevSpaceCompatError) as failure:
        compat.check_large_read_bridge(package_root=package)
    assert failure.value.code == "DEVSPACE_LARGE_READ_BRIDGE_SOURCE_MISSING"


def test_exact_devspace_patch_is_hash_gated_idempotent_and_backed_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat = load_compat()
    package = tmp_path / "node_modules" / "@waishnav" / "devspace"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"version": compat.SUPPORTED_VERSION}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"before\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n"
        "--- a/sample.txt\n"
        "+++ b/sample.txt\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+after\n",
        encoding="utf-8",
    )
    compat.PATCHES = {
        "sample.txt": {
            "patch": "sample.patch",
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
        }
    }
    compat.patch_root = lambda: patches
    monkeypatch.setenv("CODEX_DEVSPACE_COMPAT_STATE_ROOT", str(tmp_path / "state"))
    backup = tmp_path / "backup"
    cli = prepare_service_package(compat, package, replace_patches=False)
    node = fake_node_executable(tmp_path)

    first = compat.ensure_devspace_compatibility(package_root=package, backup_root=backup)
    second = compat.ensure_devspace_compatibility(package_root=package, backup_root=backup)
    confirmed = compat.confirm_service_restarted(
        package_root=package,
        service_probe=lambda port: {
            "pid": 22,
            "command_line": f'node "{cli}" serve',
            "executable_path": str(node),
            "started_at_unix_ns": 2**63 - 1,
            "local_port": port,
        },
        sleep=lambda _: None,
    )
    third = compat.ensure_devspace_compatibility(package_root=package, backup_root=backup)

    assert first["changed"] == ["sample.txt"]
    assert first["service_restart_required"] is True
    assert second["already_patched"] == ["sample.txt"]
    assert second["service_restart_required"] is True
    assert confirmed["restart_marker_cleared"] is True
    assert third["service_restart_required"] is False
    assert target.read_bytes() == b"after\n"
    assert (backup / "sample.txt").read_bytes() == b"before\n"
    exact = backup / "by-sha256" / digest(b"before\n") / "sample.txt"
    assert exact.read_bytes() == b"before\n"
    assert first["migrations"] == [
        {
            "path": "sample.txt",
            "from_sha256": digest(b"before\n"),
            "to_sha256": digest(b"after\n"),
            "patch": "sample.patch",
            "reverse": False,
            "backup_path": str(exact),
        }
    ]


def test_exact_devspace_patch_accepts_only_hash_bound_upgrade_chain(
    tmp_path: Path,
) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": compat.SUPPORTED_VERSION}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"middle-one\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "from-pristine.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n"
        "@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    (patches / "from-middle-one.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n"
        "@@ -1 +1 @@\n-middle-one\n+middle-two\n",
        encoding="utf-8",
    )
    (patches / "from-middle-two.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n"
        "@@ -1 +1 @@\n-middle-two\n+after\n",
        encoding="utf-8",
    )
    compat.PATCHES = {
        "sample.txt": {
            "patch": "from-pristine.patch",
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
            "upgrades": {
                digest(b"middle-one\n"): "from-middle-one.patch",
                digest(b"middle-two\n"): "from-middle-two.patch",
            },
        }
    }
    compat.patch_root = lambda: patches

    result = compat.ensure_devspace_compatibility(
        package_root=package, backup_root=tmp_path / "backup"
    )

    assert result["changed"] == ["sample.txt"]
    assert target.read_bytes() == b"after\n"
    assert (tmp_path / "backup" / "sample.txt").read_bytes() == b"middle-one\n"


def test_restart_confirmation_rejects_old_or_foreign_listener(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat = load_compat()
    package = tmp_path / "node_modules" / "@waishnav" / "devspace"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"version": compat.SUPPORTED_VERSION}), encoding="utf-8")
    (package / "sample.txt").write_bytes(b"after\n")
    compat.PATCHES = {
        "sample.txt": {
            "patch": "unused.patch",
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
        }
    }
    monkeypatch.setenv("CODEX_DEVSPACE_COMPAT_STATE_ROOT", str(tmp_path / "state"))
    marker = compat._write_restart_marker([package])
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    patched_at = int(marker_payload["created_at_unix_ns"])

    with pytest.raises(compat.DevSpaceCompatError) as old:
        compat.confirm_service_restarted(
            package_root=package,
            wait_timeout_seconds=0,
            service_probe=lambda port: {
                "pid": 1,
                "command_line": f"node {package / 'dist' / 'cli.js'} serve",
                "started_at_unix_ns": patched_at - 1,
            },
        )
    assert old.value.code == "DEVSPACE_RESTART_NOT_PROVEN"
    assert marker.is_file()

    with pytest.raises(compat.DevSpaceCompatError) as foreign:
        compat.confirm_service_restarted(
            package_root=package,
            wait_timeout_seconds=0,
            service_probe=lambda port: {
                "pid": 2,
                "command_line": "node other-server.js",
                "started_at_unix_ns": patched_at + 1,
            },
        )
    assert foreign.value.code == "DEVSPACE_SERVICE_IDENTITY_MISMATCH"
    assert marker.is_file()


def test_restart_confirmation_waits_through_managed_npx_cold_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat = load_compat()
    package = tmp_path / "node_modules" / "@waishnav" / "devspace"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"version": compat.SUPPORTED_VERSION}), encoding="utf-8"
    )
    (package / "sample.txt").write_bytes(b"after\n")
    compat.PATCHES = {
        "sample.txt": {
            "patch": "unused.patch",
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
        }
    }
    cli = prepare_service_package(compat, package, replace_patches=False)
    node = fake_node_executable(tmp_path)
    monkeypatch.setenv("CODEX_DEVSPACE_COMPAT_STATE_ROOT", str(tmp_path / "state"))
    marker = compat._write_restart_marker([package])
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    patched_at = int(marker_payload["created_at_unix_ns"])
    probes = 0
    sleeps: list[float] = []
    clock = [0.0]

    def advance_clock(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(compat.time, "monotonic", lambda: clock[0])

    def delayed_service_probe(port: int) -> dict[str, object] | None:
        nonlocal probes
        probes += 1
        if probes < 121:
            return None
        if probes < 241:
            return {
                "pid": 11,
                "command_line": f'node "{cli}" serve',
                "executable_path": str(node),
                "started_at_unix_ns": patched_at - 1,
                "local_port": port,
            }
        return {
            "pid": 22,
            "command_line": f'node "{cli}" serve',
            "executable_path": str(node),
            "started_at_unix_ns": patched_at + 1,
            "local_port": port,
        }

    with pytest.raises(compat.DevSpaceCompatError) as legacy_bound:
        compat.confirm_service_restarted(
            package_root=package,
            wait_timeout_seconds=20,
            service_probe=delayed_service_probe,
            sleep=advance_clock,
        )
    assert legacy_bound.value.code == "DEVSPACE_RESTART_NOT_PROVEN"
    assert marker.exists()

    probes = 0
    sleeps.clear()
    clock[0] = 0.0
    confirmed = compat.confirm_service_restarted(
        package_root=package,
        service_probe=delayed_service_probe,
        sleep=advance_clock,
    )

    assert probes == 241
    assert sum(sleeps) == pytest.approx(60.0)
    assert confirmed["restart_confirmed"] is True
    assert confirmed["restart_marker_cleared"] is True
    assert confirmed["service_identity"]["pid"] == 22
    assert not marker.exists()


def test_stop_service_requires_exact_devspace_identity(tmp_path: Path) -> None:
    compat = load_compat()
    stopped: list[int] = []
    package = tmp_path / "node_modules" / "@waishnav" / "devspace"
    cli = prepare_service_package(compat, package)
    node = fake_node_executable(tmp_path)
    first_identity = {
        "pid": 44,
        "command_line": f'node "{cli}" serve',
        "executable_path": str(node),
        "started_at_unix_ns": 1,
    }
    first_probes = iter([first_identity, None])
    result = compat.stop_exact_devspace_service(
        service_probe=lambda port: next(first_probes),
        stopper=stopped.append,
        package_roots=[package],
    )
    assert result["stopped"] is True
    assert stopped == [44]

    # Real npm installs create .bin; strict POSIX resolution traverses it before .. .
    (package.parent.parent / ".bin").mkdir()
    npx_cli = package.parent.parent / ".bin" / ".." / "@waishnav" / "devspace" / "dist" / "cli.js"
    npx_identity = {
        "pid": 45,
        "command_line": f'"node" "{npx_cli}" serve',
        "executable_path": str(node),
        "started_at_unix_ns": 1,
    }
    npx_probes = iter([npx_identity, None])
    npx_result = compat.stop_exact_devspace_service(
        service_probe=lambda port: next(npx_probes),
        stopper=stopped.append,
        package_roots=[package],
    )
    assert npx_result["stopped"] is True
    assert stopped == [44, 45]

    with pytest.raises(compat.DevSpaceCompatError) as foreign:
        compat.stop_exact_devspace_service(
            service_probe=lambda port: {
                "pid": 55,
                "command_line": "node unrelated.js",
                "executable_path": str(node),
                "started_at_unix_ns": 1,
            },
            stopper=stopped.append,
            package_roots=[package],
        )
    assert foreign.value.code == "DEVSPACE_SERVICE_IDENTITY_MISMATCH"


def test_service_identity_accepts_equivalent_cache_package_and_rejects_forgeries(
    tmp_path: Path,
) -> None:
    compat = load_compat()
    expected = (
        tmp_path
        / "Packages"
        / "OpenAI.Codex_test"
        / "LocalCache"
        / "Local"
        / "npm-cache"
        / "_npx"
        / "expected"
        / "node_modules"
        / "@waishnav"
        / "devspace"
    )
    actual = (
        tmp_path
        / "Local"
        / "npm-cache"
        / "_npx"
        / "actual"
        / "node_modules"
        / "@waishnav"
        / "devspace"
    )
    expected_cli = prepare_service_package(compat, expected)
    actual_cli = prepare_service_package(compat, actual)
    node = fake_node_executable(tmp_path)

    accepted = compat._assert_devspace_service_identity(
        {
            "pid": 71,
            "command_line": f'"node" "{actual_cli}" serve',
            "executable_path": str(node),
        },
        [expected],
    )
    assert accepted["pid"] == 71
    assert expected_cli.read_bytes() == actual_cli.read_bytes()

    wrong_version = tmp_path / "wrong-version" / "node_modules" / "@waishnav" / "devspace"
    wrong_version_cli = prepare_service_package(
        compat, wrong_version, version="9.9.9", replace_patches=False
    )
    (wrong_version / "dist" / "server.js").write_bytes(
        (expected / "dist" / "server.js").read_bytes()
    )

    wrong_hash = tmp_path / "wrong-hash" / "node_modules" / "@waishnav" / "devspace"
    wrong_hash_cli = prepare_service_package(compat, wrong_hash, replace_patches=False)
    (wrong_hash / "dist" / "server.js").write_bytes(b"tampered service bytes\n")

    wrong_name = tmp_path / "wrong-name" / "node_modules" / "@waishnav" / "devspace"
    wrong_name_cli = prepare_service_package(compat, wrong_name, replace_patches=False)
    (wrong_name / "package.json").write_text(
        json.dumps({"name": "forged-devspace", "version": compat.SUPPORTED_VERSION}),
        encoding="utf-8",
    )
    (wrong_name / "dist" / "server.js").write_bytes(
        (expected / "dist" / "server.js").read_bytes()
    )

    forged_cli = Path(f"{actual_cli}.forged")
    forged_cli.write_bytes(actual_cli.read_bytes())
    python_executable = tmp_path / ("python.exe" if os.name == "nt" else "python")
    python_executable.write_bytes(b"not node\n")
    rejected = [
        (wrong_version_cli, "serve", node),
        (wrong_hash_cli, "serve", node),
        (wrong_name_cli, "serve", node),
        (actual_cli, "status", node),
        (forged_cli, "serve", node),
        (actual_cli, "serve", python_executable),
    ]
    for pid, (cli, subcommand, executable) in enumerate(rejected, start=72):
        with pytest.raises(compat.DevSpaceCompatError) as mismatch:
            compat._assert_devspace_service_identity(
                {
                    "pid": pid,
                    "command_line": f'"node" "{cli}" {subcommand}',
                    "executable_path": str(executable),
                },
                [expected],
            )
        assert mismatch.value.code == "DEVSPACE_SERVICE_IDENTITY_MISMATCH"


def test_service_stop_resolves_current_and_lkg_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compat = load_compat()
    current = tmp_path / "current" / "node_modules" / "@waishnav" / "devspace"
    lkg = tmp_path / "lkg" / "node_modules" / "@waishnav" / "devspace"
    foreign = tmp_path / "foreign" / "node_modules" / "@waishnav" / "devspace"
    prepare_service_package(compat, current)
    prepare_service_package(compat, lkg, version=compat.LEGACY_LKG_VERSION, replace_patches=False)
    prepare_service_package(compat, foreign, version="9.9.9", replace_patches=False)
    node = fake_node_executable(tmp_path)
    monkeypatch.setattr(compat, "_candidate_roots", lambda: [foreign, lkg, current])

    assert compat.resolve_service_stop_roots() == [current.resolve(), lkg.resolve()]

    stopped: list[int] = []
    lkg_cli = lkg / "dist" / "cli.js"
    identity = {
        "pid": 46,
        "command_line": f'node "{lkg_cli}" serve',
        "executable_path": str(node),
        "started_at_unix_ns": 1,
    }
    probes = iter([identity, None])
    result = compat.stop_exact_devspace_service(
        service_probe=lambda port: next(probes),
        stopper=stopped.append,
    )
    assert result["stopped"] is True
    assert stopped == [46]


def test_windows_stop_requires_pid_start_binding_and_listener_release(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "node_modules" / "@waishnav" / "devspace"
    cli = prepare_service_package(compat, package)
    node = fake_node_executable(tmp_path)
    identity = {
        "pid": 60008,
        "command_line": f'node "{cli}" serve',
        "executable_path": str(node),
        "started_at_unix_ns": 123_000_000,
    }
    probes = iter([identity, None])
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "ok": True, "pid": 60008, "stale_pid": False, "forced": False,
                "process_exited": True, "listener_released": True, "listener_pid": None,
            }),
            stderr="",
        )

    result = compat.stop_exact_devspace_service(
        service_probe=lambda port: next(probes), package_roots=[package], platform_name="nt",
        windows_runner=runner, sleeper=lambda _: None,
    )

    script = calls[0][-1]
    assert "Stop-Process -Id $targetPid -ErrorAction SilentlyContinue" in script
    assert "Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue" in script
    assert "listener_released=$released" in script
    assert result["stop"]["process_exited"] is True
    assert result["stop"]["listener_released"] is True


def test_stop_service_cli_forwards_validated_package_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    compat = load_compat()
    lkg = tmp_path / "lkg"
    lkg.mkdir()
    (lkg / "package.json").write_text(json.dumps({"version": compat.LEGACY_LKG_VERSION}), encoding="utf-8")
    calls: list[dict] = []
    monkeypatch.setattr(
        compat,
        "stop_exact_devspace_service",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "stopped": False},
    )

    assert compat.main(["--stop-exact-service", "--package-root", str(lkg)]) == 0
    assert calls == [{"local_port": 7676, "package_roots": [lkg.resolve()]}]
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_stop_service_cli_rejects_unvalidated_package_version(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    compat = load_compat()
    package = tmp_path / "foreign"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")

    assert compat.main(["--stop-exact-service", "--package-root", str(package)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "DEVSPACE_SERVICE_STOP_VERSION_UNSUPPORTED"


def test_service_identity_accepts_posix_npm_shim_only_for_exact_package(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX npm launchers use symlinks")
    compat = load_compat()
    package = tmp_path / "node_modules" / "@waishnav" / "devspace"
    cli = package / "dist" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    prepare_service_package(compat, package, replace_patches=True)
    node = fake_node_executable(tmp_path)
    shim = tmp_path / "node_modules" / ".bin" / "devspace"
    shim.parent.mkdir()
    shim.symlink_to(cli)

    identity = compat._assert_devspace_service_identity(
        {
            "pid": 77,
            "command_line": f"node {shim} serve",
            "executable_final_path": str(node),
        },
        [package],
    )

    assert identity["pid"] == 77


def test_service_identity_rejects_posix_npm_shim_for_foreign_package(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX npm launchers use symlinks")
    compat = load_compat()
    package = tmp_path / "node_modules" / "@waishnav" / "devspace"
    cli = package / "dist" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    prepare_service_package(compat, package, replace_patches=True)
    node = fake_node_executable(tmp_path)
    foreign_cli = tmp_path / "foreign-cli.js"
    foreign_cli.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    shim = tmp_path / "node_modules" / ".bin" / "devspace"
    shim.parent.mkdir()
    shim.symlink_to(foreign_cli)

    with pytest.raises(compat.DevSpaceCompatError) as mismatch:
        compat._assert_devspace_service_identity(
            {
                "pid": 88,
                "command_line": f"node {shim} serve",
                "executable_final_path": str(node),
            },
            [package],
        )

    assert mismatch.value.code == "DEVSPACE_SERVICE_IDENTITY_MISMATCH"


def test_unknown_devspace_version_or_file_hash_fails_closed(tmp_path: Path) -> None:
    compat = load_compat()
    assert compat.LEGACY_LKG_VERSION == "1.0.7"
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "2.0.0"}), encoding="utf-8")
    with pytest.raises(compat.DevSpaceCompatError) as version:
        compat.ensure_devspace_compatibility(package_root=package)
    assert version.value.code == "DEVSPACE_VERSION_UNVALIDATED"

    (package / "package.json").write_text(json.dumps({"version": compat.LEGACY_LKG_VERSION}), encoding="utf-8")
    with pytest.raises(compat.DevSpaceCompatError) as legacy:
        compat.ensure_devspace_compatibility(package_root=package)
    assert legacy.value.code == "DEVSPACE_VERSION_UNVALIDATED"

    (package / "package.json").write_text(json.dumps({"version": compat.SUPPORTED_VERSION}), encoding="utf-8")
    (package / "sample.txt").write_bytes(b"unknown\n")
    compat.PATCHES = {
        "sample.txt": {
            "patch": "sample.patch",
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
        }
    }
    with pytest.raises(compat.DevSpaceCompatError) as mismatch:
        compat.ensure_devspace_compatibility(package_root=package)
    assert mismatch.value.code == "DEVSPACE_FILE_HASH_MISMATCH"


def test_bounded_workspace_patch_skips_transient_trees_and_batches_discovery() -> None:
    compat = load_compat()
    patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "workspaces.patch"
    ).read_text(encoding="utf-8")

    assert 'entry.name.startsWith(".pytest-")' in patch
    assert '".tmp"' in patch
    assert '".venv"' in patch
    assert "const batchSize = 24" in patch
    assert "await Promise.all(batch.map" in patch


def test_oauth_refresh_patch_is_hash_gated_bounded_and_revocation_aware() -> None:
    compat = load_compat()
    patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "oauth-refresh-replay.patch"
    ).read_text(encoding="utf-8")

    assert compat.PATCHES["dist/oauth-provider.js"] == {
        "patch": "oauth-refresh-replay.patch",
        "pristine": "90ff3fd116735e98af5751de1065538964f6eaae913171223e8e19337b9831b8",
        "patched": "51376673f3def7a3dc05884a409ef52b1ae8580510ba9de86d0b4014b3cd6239",
    }
    assert "const REFRESH_REPLAY_GRACE_MS = 30 * 1000;" in patch
    assert "const MAX_REFRESH_REPLAYS = 32;" in patch
    assert "replay.clientId === client.client_id" in patch
    assert "sameStringSet(requestedScopes, replay.scopes)" in patch
    assert "requestedResource === replay.resource" in patch
    assert "hashToken(replay.tokens.refresh_token) === hashed" in patch
    assert "this.refreshReplays.clear();" in patch


def test_108_workspace_bridge_patch_preserves_write_tools_and_adds_bounded_read_chunk() -> None:
    compat = load_compat()
    patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "workspace-write-and-read-bridge.patch"
    ).read_text(encoding="utf-8")

    assert compat.PATCHES["dist/server.js"] == {
        "patch": "workspace-write-and-read-bridge.patch",
        "pristine": "bf3db902241b631d7c6fbaf12385243b46b4f2d4bb776b6ea7ca6c9d429a3263",
        "patched": "eeaae28aff625c28940463fe0909a53250580ab90748956a216e07ebc8604988",
        "transitions": {
            "bf3db902241b631d7c6fbaf12385243b46b4f2d4bb776b6ea7ca6c9d429a3263": {
                "patch": "workspace-write-and-read-bridge.patch",
                "reverse": False,
                "result": "659cb1011cd7ab7fb75debb21a44f030001797c2160a42beac527354be93e497",
            },
            "659cb1011cd7ab7fb75debb21a44f030001797c2160a42beac527354be93e497": {
                "patch": "widget-domain.patch",
                "reverse": False,
                "result": "eeaae28aff625c28940463fe0909a53250580ab90748956a216e07ebc8604988",
            },
            "1370524581b75d6b91d281dea52e427004a5ac71c19ac8090d66fe521748760c": {
                "patch": "tool-read-receipts.patch",
                "reverse": True,
                "result": "659cb1011cd7ab7fb75debb21a44f030001797c2160a42beac527354be93e497",
            },
            "efd7a769601aae31b1f4d8a2e22767bba6c587b56488100dea85ad2c17f02985": {
                "patch": "widget-domain.patch",
                "reverse": True,
                "result": "1370524581b75d6b91d281dea52e427004a5ac71c19ac8090d66fe521748760c",
            },
            "d35a4cd7b5678b4fa16c05ba8ca1d8cc0937d9f4c2bdd48e454a46ffa28da598": {
                "patch": "receipt-structured-output.patch",
                "reverse": True,
                "result": "efd7a769601aae31b1f4d8a2e22767bba6c587b56488100dea85ad2c17f02985",
            },
        },
    }
    assert 'delete: "delete_file"' in patch
    assert 'trash: "trash_file"' in patch
    assert '+    readChunk: "read_chunk",' in patch
    assert "+const MAX_READ_CHUNK_BYTES = 24 * 1024;" in patch
    assert "+export async function readUtf8Chunk" in patch
    assert "new TextDecoder(\"utf-8\", { fatal: true })" in patch
    assert "nextOffsetBytes: offsetBytes + accepted" in patch
    assert "annotations: { readOnlyHint: true }" in patch
    migration = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "workspace-write-and-read-bridge.patch"
    ).read_text(encoding="utf-8")
    assert "-import { randomUUID } from \"node:crypto\";" in migration
    assert '+    readChunk: "read_chunk",' in migration
    widget_patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "widget-domain.patch"
    ).read_text(encoding="utf-8")
    assert widget_patch.count("domain: appDomain(config),") == 2
    assert 'publicBaseUrl.protocol !== "https:"' in widget_patch
    assert "publicBaseUrl.username || publicBaseUrl.password" in widget_patch
    assert 'hostname === "localhost"' in widget_patch
    assert '.replace(/\\.$/, "")' in widget_patch
    assert 'hostname.endsWith(".localhost")' in widget_patch
    assert 'hostname === "::1"' in widget_patch
    assert 'hostname.startsWith("::ffff:127.")' in widget_patch
    assert 'hostname.startsWith("::ffff:7f")' in widget_patch
    assert r'/^127(?:\.\d{1,3}){3}$/.test(hostname)' in widget_patch
    assert "return publicBaseUrl.origin;" in widget_patch


def test_108_widget_domain_upgrade_is_hash_gated_to_the_public_app_origin(
    tmp_path: Path,
) -> None:
    compat = load_compat()
    try:
        source_root = compat.resolve_package_roots()[0]
    except compat.DevSpaceCompatError as exc:
        pytest.skip(f"DevSpace {compat.SUPPORTED_VERSION} package unavailable: {exc.code}")
    source = source_root / "dist" / "server.js"
    prior_hash = "659cb1011cd7ab7fb75debb21a44f030001797c2160a42beac527354be93e497"
    if compat.sha256_file(source) != prior_hash:
        pytest.skip("installed DevSpace server is not the lean workspace bridge payload")
    package = tmp_path / "devspace"
    (package / "dist").mkdir(parents=True)
    shutil.copy2(source, package / "dist" / "server.js")
    compat._apply_patch(
        package,
        MODULE_PATH.parent / "devspace-compat" / compat.SUPPORTED_VERSION / "widget-domain.patch",
    )
    server = (package / "dist" / "server.js").read_text(encoding="utf-8")
    assert compat.sha256_file(package / "dist" / "server.js") == compat.PATCHES["dist/server.js"]["patched"]
    assert server.count("domain: appDomain(config),") == 2
    assert 'publicBaseUrl.protocol !== "https:"' in server
    assert 'hostname === "localhost"' in server
    assert '.replace(/\\.$/, "")' in server
    assert 'hostname.endsWith(".localhost")' in server
    assert 'hostname === "::1"' in server
    assert 'hostname.startsWith("::ffff:127.")' in server
    assert 'hostname.startsWith("::ffff:7f")' in server
    assert r'/^127(?:\.\d{1,3}){3}$/.test(hostname)' in server
    assert "return publicBaseUrl.origin;" in server
    assert "domain: config.localBaseUrl" not in server

    function_match = re.search(r"function appDomain\(config\) \{.*?\n\}", server, re.DOTALL)
    assert function_match is not None
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the widget-domain runtime probe")
    probe = function_match.group(0) + r'''
const accepted = appDomain({ publicBaseUrl: "https://lasal-pc.tail46ec90.ts.net/mcp" });
if (accepted !== "https://lasal-pc.tail46ec90.ts.net") process.exit(11);
for (const candidate of [
  "http://lasal-pc.tail46ec90.ts.net/mcp",
  "https://user:password@lasal-pc.tail46ec90.ts.net/mcp",
  "https://localhost:7676/mcp",
  "https://localhost.:7676/mcp",
  "https://dev.localhost:7676/mcp",
  "https://dev.localhost.:7676/mcp",
  "https://127.0.0.1:7676/mcp",
  "https://127.42.0.9:7676/mcp",
  "https://[::1]:7676/mcp",
  "https://[::ffff:127.0.0.1]:7676/mcp",
]) {
  let rejected = false;
  try { appDomain({ publicBaseUrl: candidate }); } catch { rejected = true; }
  if (!rejected) process.exit(12);
}
'''
    completed = subprocess.run(
        [node, "--input-type=module", "--eval", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_108_strict_audit_payloads_are_reverse_only_migrations() -> None:
    compat = load_compat()

    artifact = compat.PATCHES["dist/artifact-tools.js"]
    assert artifact["patched"] == artifact["pristine"]
    assert artifact["transitions"] == {
        "fd5204b37da657d6183c8394b5ee8bed09bbffd50999946b4d0421897a52dfa7": {
            "patch": "artifact-audit-readonly.patch",
            "reverse": True,
            "result": artifact["pristine"],
        }
    }

    server = compat.PATCHES["dist/server.js"]
    strict_hash = "d35a4cd7b5678b4fa16c05ba8ca1d8cc0937d9f4c2bdd48e454a46ffa28da598"
    path = []
    current = strict_hash
    while current != server["patched"]:
        transition = server["transitions"][current]
        path.append((transition["patch"], transition["reverse"]))
        current = transition["result"]
    assert path == [
        ("receipt-structured-output.patch", True),
        ("widget-domain.patch", True),
        ("tool-read-receipts.patch", True),
        ("widget-domain.patch", False),
    ]


def test_delete_file_contract_is_part_of_the_108_hash_gated_bridge() -> None:
    compat = load_compat()
    patch_path = MODULE_PATH.parent / "devspace-compat" / compat.SUPPORTED_VERSION / "workspace-write-and-read-bridge.patch"
    patch = patch_path.read_text(encoding="utf-8")

    assert 'delete: "delete_file"' in patch
    assert "await unlink(target);" in patch
    assert "existsAfter: false" in patch
    assert "annotations: DELETE_TOOL_ANNOTATIONS" in patch
    assert "destructiveHint: true" in patch
    assert "readOnlyHint: false" in patch


def test_trash_file_contract_is_part_of_the_108_hash_gated_bridge() -> None:
    compat = load_compat()
    patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "workspace-write-and-read-bridge.patch"
    ).read_text(encoding="utf-8")

    assert compat.PATCHES["dist/server.js"]["patch"] == "workspace-write-and-read-bridge.patch"
    assert 'trash: "trash_file"' in patch
    assert "await rename(target, destination);" in patch
    trash_contract = patch[patch.index("export async function trashWorkspaceFile"):]
    assert "await unlink(target);" not in trash_contract
    assert "originalRelativePath: segments.join" in patch
    assert "trashRelativePath:" in patch
    assert "before.sha256 !== after.sha256" in patch
    assert "annotations: TRASH_TOOL_ANNOTATIONS" in patch
    assert "destructiveHint: true" in patch
    assert "readOnlyHint: false" in patch


def test_published_108_default_contract_applies_every_current_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compat = load_compat()
    configured = os.environ.get("DEVSPACE_108_PACKAGE_ROOT", "").strip()
    source = Path(configured) if configured else Path("__devspace_108_cache_unset__")
    if not source.is_dir():
        if os.environ.get("CI"):
            pytest.fail("CI must prepare the exact published DevSpace 1.0.8 package")
        pytest.skip("published DevSpace 1.0.8 package root is unavailable")
    package = tmp_path / "devspace-published"
    shutil.copytree(source, package)
    monkeypatch.setenv("CODEX_DEVSPACE_COMPAT_STATE_ROOT", str(tmp_path / "state"))
    post_patch_checks: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        compat,
        "check_oauth_refresh_replay",
        lambda *, package_root: post_patch_checks.append(("oauth", package_root)) or {"ok": True},
    )
    monkeypatch.setattr(
        compat,
        "check_large_read_bridge",
        lambda *, package_root: post_patch_checks.append(("large-read", package_root)) or {"ok": True},
    )

    result = compat.ensure_devspace_compatibility(package_root=package, backup_root=tmp_path / "backup")
    touched = set(result["changed"]) | set(result["already_patched"])
    assert touched == set(compat.PATCHES)
    node = shutil.which("node")
    assert node is not None
    for relative, contract in compat.PATCHES.items():
        target = package / relative
        assert compat.sha256_file(target) == contract["patched"]
        syntax = subprocess.run([node, "--check", str(target)], capture_output=True, text=True, check=False)
        assert syntax.returncode == 0, f"{relative}: {syntax.stderr}"
    server = (package / "dist" / "server.js").read_text(encoding="utf-8")
    artifact_tools = (package / "dist" / "artifact-tools.js").read_text(encoding="utf-8")
    assert "readUtf8Chunk" in server
    assert "domain: appDomain(config)" in server
    for retired in (
        "AUDIT_NONCE_PATTERN",
        "auditNonce",
        "writeToolReadReceipt",
        "auditReceiptId",
        "assertAuditReadonly",
    ):
        assert retired not in server
    assert "beforeMutation" not in artifact_tools
    assert post_patch_checks == [("oauth", package.resolve()), ("large-read", package.resolve())]


def test_delete_file_patch_safety_contract_on_temporary_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat = load_compat()
    try:
        source_root = compat.resolve_package_roots()[0]
    except compat.DevSpaceCompatError as exc:
        pytest.skip(f"DevSpace {compat.SUPPORTED_VERSION} package unavailable: {exc.code}")
    package = tmp_path / "devspace"
    (package / "dist").mkdir(parents=True)
    shutil.copy2(source_root / "dist" / "server.js", package / "dist" / "server.js")
    (package / "package.json").write_text(json.dumps({"version": compat.SUPPORTED_VERSION}), encoding="utf-8")
    monkeypatch.setenv("CODEX_DEVSPACE_COMPAT_STATE_ROOT", str(tmp_path / "state"))
    compat.PATCHES = {"dist/server.js": compat.PATCHES["dist/server.js"]}
    compat.ensure_devspace_compatibility(package_root=package, backup_root=tmp_path / "backup")
    server = (package / "dist" / "server.js").read_text(encoding="utf-8")
    assert compat.sha256_file(package / "dist" / "server.js") == compat.PATCHES["dist/server.js"]["patched"]
    assert 'registerAppTool(server, toolNames.read' in server
    assert 'registerAppTool(server, toolNames.write' in server
    assert 'registerAppTool(server, toolNames.delete' in server
    assert "readFileTool({ ...input, path: readPath.absolutePath }" in server
    assert "workspaces.markReadPathLoaded(workspace, readPath);" in server
    write_annotations = re.search(r"const WRITE_TOOL_ANNOTATIONS = \{([^}]+)\};", server)
    delete_annotations = re.search(r"const DELETE_TOOL_ANNOTATIONS = \{([^}]+)\};", server)
    assert write_annotations is not None and delete_annotations is not None
    assert delete_annotations.group(1) == write_annotations.group(1)
    start = server.index("function deleteFailure")
    end = server.index("const workspaceSkillOutputSchema", start)
    helper = server[start:end].replace("export async function", "async function")
    harness = tmp_path / "delete-file-safety.mjs"
    harness.write_text(
        'import assert from "node:assert/strict";\n'
        'import {mkdir, readFile, symlink, writeFile} from "node:fs/promises";\n'
        'import {lstat, realpath, unlink} from "node:fs/promises";\n'
        'import {isAbsolute, join, relative, resolve, sep} from "node:path";\n'
        + helper
        + '\nconst root=process.argv[2], outside=process.argv[3]; await mkdir(root,{recursive:true}); await mkdir(outside,{recursive:true});\n'
        + 'const ordinary=join(root,"ordinary.txt"), neighbor=join(root,"neighbor.txt"); await writeFile(ordinary,"delete"); await writeFile(neighbor,"keep");\n'
        + 'const result=await deleteWorkspaceFile({root},"ordinary.txt"); assert.deepEqual(result,{requestedPath:"ordinary.txt",existedBefore:true,deleted:true,existsAfter:false}); await assert.rejects(lstat(ordinary)); assert.equal(await readFile(neighbor,"utf8"),"keep");\n'
        + 'const reject=async(path,code)=>assert.rejects(()=>deleteWorkspaceFile({root},path),new RegExp(code));\n'
        + 'await reject("missing.txt","DELETE_TARGET_NOT_FOUND"); await mkdir(join(root,"directory")); await reject("directory","DELETE_TARGET_NOT_REGULAR_FILE");\n'
        + 'for(const path of ["../outside.txt","foo/../../outside.txt","..\\\\outside.txt","foo\\\\..\\\\..\\\\outside.txt"]){await reject(path,"DELETE_TRAVERSAL_FORBIDDEN");}\n'
        + 'await reject("C:\\\\outside.txt","DELETE_ABSOLUTE_PATH_FORBIDDEN"); await reject("\\\\\\\\server\\\\share\\\\outside.txt","DELETE_ABSOLUTE_PATH_FORBIDDEN"); await reject(".","DELETE_TRAVERSAL_FORBIDDEN");\n'
        + 'await mkdir(join(root,".git")); await writeFile(join(root,".git","config"),"x"); await reject(".git","DELETE_PROTECTED_TARGET"); await reject(".git/config","DELETE_PROTECTED_TARGET");\n'
        + 'await writeFile(join(outside,"victim.txt"),"safe"); let linkStatus="PASS"; try{await symlink(outside,join(root,"escape"),process.platform==="win32"?"junction":"dir"); await reject("escape/victim.txt","DELETE_REPARSE_FORBIDDEN"); assert.equal(await readFile(join(outside,"victim.txt"),"utf8"),"safe");}catch(error){if(error?.code==="EPERM"||error?.code==="EACCES")linkStatus="SKIPPED:"+error.code;else throw error;}\n'
        + 'console.log(JSON.stringify({ok:true,linkStatus}));\n',
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    completed = subprocess.run(
        ["node", str(harness), str(workspace), str(outside)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    if result["linkStatus"].startswith("SKIPPED:"):
        pytest.skip(f"junction/symlink creation unavailable: {result['linkStatus']}")


def test_trash_file_patch_safety_and_byte_identity_on_temporary_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat = load_compat()
    try:
        source_root = compat.resolve_package_roots()[0]
    except compat.DevSpaceCompatError as exc:
        pytest.skip(f"DevSpace {compat.SUPPORTED_VERSION} package unavailable: {exc.code}")
    package = tmp_path / "devspace"
    (package / "dist").mkdir(parents=True)
    shutil.copy2(source_root / "dist" / "server.js", package / "dist" / "server.js")
    (package / "package.json").write_text(json.dumps({"version": compat.SUPPORTED_VERSION}), encoding="utf-8")
    monkeypatch.setenv("CODEX_DEVSPACE_COMPAT_STATE_ROOT", str(tmp_path / "state"))
    compat.PATCHES = {"dist/server.js": compat.PATCHES["dist/server.js"]}

    result = compat.ensure_devspace_compatibility(
        package_root=package,
        backup_root=tmp_path / "backup",
    )

    server_path = package / "dist" / "server.js"
    server = server_path.read_text(encoding="utf-8")
    # The resolved host package may already be the exact verified patched
    # payload after a managed DevSpace restart.  Copying that payload is an
    # idempotent no-op; a pristine host copy performs the one expected patch.
    assert result["changed"] in ([], ["dist/server.js"])
    assert compat.sha256_file(server_path) == compat.PATCHES["dist/server.js"]["patched"]
    assert 'registerAppTool(server, toolNames.delete' in server
    assert 'registerAppTool(server, toolNames.trash' in server
    assert "await unlink(target);" in server
    assert "await rename(target, destination);" in server
    write_annotations = re.search(r"const WRITE_TOOL_ANNOTATIONS = \{([^}]+)\};", server)
    trash_annotations = re.search(r"const TRASH_TOOL_ANNOTATIONS = \{([^}]+)\};", server)
    assert write_annotations is not None and trash_annotations is not None
    assert trash_annotations.group(1) == write_annotations.group(1)
    start = server.index("function deleteFailure")
    end = server.index("const workspaceSkillOutputSchema", start)
    helper = server[start:end].replace("export async function", "async function")
    harness = tmp_path / "trash-file-safety.mjs"
    harness.write_text(
        'import assert from "node:assert/strict";\n'
        'import {createHash, randomUUID} from "node:crypto";\n'
        'import {createReadStream} from "node:fs";\n'
        'import {lstat, mkdir, readFile, realpath, rename, symlink, unlink, writeFile} from "node:fs/promises";\n'
        'import {isAbsolute, join, relative, resolve, sep} from "node:path";\n'
        + helper
        + '\nconst root=process.argv[2], outside=process.argv[3], linkRoot=process.argv[4]; await mkdir(join(root,"nested"),{recursive:true}); await mkdir(outside,{recursive:true}); await mkdir(linkRoot,{recursive:true});\n'
        + 'const original=join(root,"nested","ordinary.bin"), neighbor=join(root,"neighbor.txt"), bytes=Buffer.from([0,1,2,3,255,128,64]); await writeFile(original,bytes); await writeFile(neighbor,"keep");\n'
        + 'const moved=await trashWorkspaceFile({root},"nested/ordinary.bin",{uniqueId:"fixed-id"}); assert.deepEqual(moved,{originalRelativePath:"nested/ordinary.bin",trashRelativePath:".webjjongku-trash/fixed-id/nested/ordinary.bin",trashId:"fixed-id",moved:true,originalExistsAfter:false,trashExistsAfter:true,bytes:bytes.length,sha256:createHash("sha256").update(bytes).digest("hex")}); await assert.rejects(lstat(original)); assert.deepEqual(await readFile(join(root,...moved.trashRelativePath.split("/"))),bytes); assert.equal(await readFile(neighbor,"utf8"),"keep");\n'
        + 'const reject=async(path,code,options)=>assert.rejects(()=>trashWorkspaceFile({root},path,options),new RegExp(code)); await reject("missing.txt","TRASH_TARGET_NOT_FOUND"); await mkdir(join(root,"directory")); await reject("directory","TRASH_TARGET_NOT_REGULAR_FILE");\n'
        + 'for(const path of ["../outside.txt","foo/../../outside.txt","..\\\\outside.txt","foo\\\\..\\\\..\\\\outside.txt"]){await reject(path,"TRASH_TRAVERSAL_FORBIDDEN");} await reject("C:\\\\outside.txt","TRASH_ABSOLUTE_PATH_FORBIDDEN"); await reject("\\\\\\\\server\\\\share\\\\outside.txt","TRASH_ABSOLUTE_PATH_FORBIDDEN"); await reject(".","TRASH_TRAVERSAL_FORBIDDEN");\n'
        + 'await mkdir(join(root,".git")); await writeFile(join(root,".git","config"),"x"); await reject(".git/config","TRASH_PROTECTED_TARGET"); await reject(".webjjongku-trash/fixed-id/nested/ordinary.bin","TRASH_PROTECTED_TARGET");\n'
        + 'const collision=join(root,"collision.txt"); await writeFile(collision,"source"); await mkdir(join(root,".webjjongku-trash","collision")); await reject("collision.txt","TRASH_DESTINATION_COLLISION",{uniqueId:"collision"}); assert.equal(await readFile(collision,"utf8"),"source");\n'
        + 'await writeFile(join(outside,"victim.txt"),"safe"); await writeFile(join(linkRoot,"source.txt"),"source"); let linkStatus="PASS"; try{await symlink(outside,join(linkRoot,"escape"),process.platform==="win32"?"junction":"dir"); await assert.rejects(()=>trashWorkspaceFile({root:linkRoot},"escape/victim.txt"),/TRASH_REPARSE_FORBIDDEN/); assert.equal(await readFile(join(outside,"victim.txt"),"utf8"),"safe"); const trashLinkRoot=join(linkRoot,"trash-link-workspace"); await mkdir(trashLinkRoot); await writeFile(join(trashLinkRoot,"source.txt"),"source"); await symlink(outside,join(trashLinkRoot,".webjjongku-trash"),process.platform==="win32"?"junction":"dir"); await assert.rejects(()=>trashWorkspaceFile({root:trashLinkRoot},"source.txt"),/TRASH_REPARSE_FORBIDDEN/); assert.equal(await readFile(join(trashLinkRoot,"source.txt"),"utf8"),"source");}catch(error){if(error?.code==="EPERM"||error?.code==="EACCES")linkStatus="SKIPPED:"+error.code;else throw error;}\n'
        + 'console.log(JSON.stringify({ok:true,linkStatus}));\n',
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(harness), str(tmp_path / "workspace"), str(tmp_path / "outside"), str(tmp_path / "links")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    harness_result = json.loads(completed.stdout)
    assert harness_result["ok"] is True
    if harness_result["linkStatus"].startswith("SKIPPED:"):
        pytest.skip(f"junction/symlink creation unavailable: {harness_result['linkStatus']}")


def test_directory_read_patch_unknown_upstream_hash_fails_closed(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": compat.SUPPORTED_VERSION}), encoding="utf-8")
    server = package / "dist" / "server.js"
    server.parent.mkdir()
    server.write_text("unknown upstream bytes\\n", encoding="utf-8")
    compat.PATCHES = {"dist/server.js": compat.PATCHES["dist/server.js"]}

    with pytest.raises(compat.DevSpaceCompatError) as mismatch:
        compat.ensure_devspace_compatibility(package_root=package)

    assert mismatch.value.code == "DEVSPACE_FILE_HASH_MISMATCH"
    assert mismatch.value.evidence["path"] == str(server)
