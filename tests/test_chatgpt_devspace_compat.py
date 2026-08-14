from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_devspace_compat.py"


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


def test_native_runtime_probe_loads_exact_binding_and_fails_actionably(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "devspace"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "1.0.4"}), encoding="utf-8")
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


def test_exact_devspace_patch_is_hash_gated_idempotent_and_backed_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "1.0.4"}), encoding="utf-8")
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

    first = compat.ensure_devspace_compatibility(package_root=package, backup_root=backup)
    second = compat.ensure_devspace_compatibility(package_root=package, backup_root=backup)
    confirmed = compat.confirm_service_restarted(
        package_root=package,
        service_probe=lambda port: {
            "pid": 22,
            "command_line": f"node {package / 'dist' / 'cli.js'} serve",
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


def test_known_intermediate_patch_migrates_and_recovers_pristine_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "1.0.4"}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"middle\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "base.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n"
        "@@ -1 +1 @@\n-before\n+middle\n",
        encoding="utf-8",
    )
    (patches / "finish.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n"
        "@@ -1 +1 @@\n-middle\n+after\n",
        encoding="utf-8",
    )
    middle = digest(b"middle\n")
    compat.PATCHES = {
        "sample.txt": {
            "patches": ["base.patch", "finish.patch"],
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
            "legacy_patches": {middle: "finish.patch"},
            "legacy_reverse_patches": {middle: "base.patch"},
        }
    }
    compat.patch_root = lambda: patches
    monkeypatch.setenv("CODEX_DEVSPACE_COMPAT_STATE_ROOT", str(tmp_path / "state"))
    backup = tmp_path / "backup"

    result = compat.ensure_devspace_compatibility(package_root=package, backup_root=backup)

    assert result["changed"] == ["sample.txt"]
    assert target.read_bytes() == b"after\n"
    assert (backup / "sample.txt").read_bytes() == b"before\n"


def test_restart_confirmation_rejects_old_or_foreign_listener(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "1.0.4"}), encoding="utf-8")
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


def test_stop_service_requires_exact_devspace_identity() -> None:
    compat = load_compat()
    stopped: list[int] = []
    package = Path("C:/tested/node_modules/@waishnav/devspace")
    result = compat.stop_exact_devspace_service(
        service_probe=lambda port: {
            "pid": 44,
            "command_line": f"node {package / 'dist' / 'cli.js'} serve",
            "started_at_unix_ns": 1,
        },
        stopper=stopped.append,
        package_roots=[package],
    )
    assert result["stopped"] is True
    assert stopped == [44]

    npx_result = compat.stop_exact_devspace_service(
        service_probe=lambda port: {
            "pid": 45,
            "command_line": (
                r'"node" "C:\tested\node_modules\.bin\\..\@waishnav\devspace\dist\cli.js" serve'
            ),
            "started_at_unix_ns": 1,
        },
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
                "started_at_unix_ns": 1,
            },
            stopper=stopped.append,
            package_roots=[package],
        )
    assert foreign.value.code == "DEVSPACE_SERVICE_IDENTITY_MISMATCH"


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
    shim = tmp_path / "node_modules" / ".bin" / "devspace"
    shim.parent.mkdir()
    shim.symlink_to(cli)

    identity = compat._assert_devspace_service_identity(
        {"pid": 77, "command_line": f"node {shim} serve"},
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
    foreign_cli = tmp_path / "foreign-cli.js"
    foreign_cli.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    shim = tmp_path / "node_modules" / ".bin" / "devspace"
    shim.parent.mkdir()
    shim.symlink_to(foreign_cli)

    with pytest.raises(compat.DevSpaceCompatError) as mismatch:
        compat._assert_devspace_service_identity(
            {"pid": 88, "command_line": f"node {shim} serve"},
            [package],
        )

    assert mismatch.value.code == "DEVSPACE_SERVICE_IDENTITY_MISMATCH"


def test_unknown_devspace_version_or_file_hash_fails_closed(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "2.0.0"}), encoding="utf-8")
    with pytest.raises(compat.DevSpaceCompatError) as version:
        compat.ensure_devspace_compatibility(package_root=package)
    assert version.value.code == "DEVSPACE_VERSION_UNVALIDATED"

    (package / "package.json").write_text(json.dumps({"version": "1.0.4"}), encoding="utf-8")
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


def test_server_patch_routes_directories_and_bounds_oauth_resource_compatibility() -> None:
    compat = load_compat()
    patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "directory-read.patch"
    ).read_text(encoding="utf-8")
    oauth_patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "oauth-resource-origin.patch"
    ).read_text(encoding="utf-8")

    assert compat.PATCHES["dist/server.js"] == {
        "patches": ["directory-read.patch", "oauth-resource-origin.patch"],
        "pristine": "c49c1c607b42e040cdf0b15d5a4a93cfef9ddb8147d492a3cfa2a8c3889dab24",
        "patched": "e4ec82668aaa17913f6e29964f9a2b40e43f49bc7c0498001fadc22e62c4c788",
        "legacy_patches": {
            "d5d9b08c482b282f3390f415d69d460f4ee844046962a4013f11612cbb6b52e0":
                "oauth-resource-origin.patch",
        },
        "legacy_reverse_patches": {
            "d5d9b08c482b282f3390f415d69d460f4ee844046962a4013f11612cbb6b52e0":
                "directory-read.patch",
        },
    }
    assert "const readPath = workspaces.resolveReadPath(workspace, input.path);" in patch
    assert "isDirectory = (await stat(readPath.absolutePath)).isDirectory();" in patch
    assert "? await listDirectoryTool({ path: readPath.absolutePath }, {" in patch
    assert "+                root: workspace.root," in patch
    assert ": await readFileTool({ ...input, path: readPath.absolutePath }, {" in patch
    assert "+                readRoots: readPath.readRoots," in patch
    assert "!req.auth?.resource" in oauth_patch
    assert "new URL(req.auth.resource).origin === resourceServerUrl.origin" in oauth_patch
    assert "if (!resourceOk)" in oauth_patch


def test_directory_read_patch_unknown_upstream_hash_fails_closed(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "1.0.4"}), encoding="utf-8")
    server = package / "dist" / "server.js"
    server.parent.mkdir()
    server.write_text("unknown upstream bytes\\n", encoding="utf-8")
    compat.PATCHES = {"dist/server.js": compat.PATCHES["dist/server.js"]}

    with pytest.raises(compat.DevSpaceCompatError) as mismatch:
        compat.ensure_devspace_compatibility(package_root=package)

    assert mismatch.value.code == "DEVSPACE_FILE_HASH_MISMATCH"
    assert mismatch.value.evidence["path"] == str(server)
