from __future__ import annotations

import hashlib
import importlib.util
import json
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


def test_compat_tests_use_an_isolated_restart_marker(tmp_path: Path) -> None:
    compat = load_compat()

    assert compat.compat_state_root() == (tmp_path / "compat-state").resolve()
    assert compat.restart_marker_path().parent == (tmp_path / "compat-state").resolve()


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


def test_exact_devspace_patch_accepts_only_hash_bound_upgrade_chain(
    tmp_path: Path,
) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "1.0.4"}), encoding="utf-8")
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
    assert (tmp_path / "backup" / "sample.txt").read_bytes() == b"middle\n"


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


def test_directory_read_patch_routes_directories_and_adds_bounded_read_chunk() -> None:
    compat = load_compat()
    patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "directory-read.patch"
    ).read_text(encoding="utf-8")

    assert compat.PATCHES["dist/server.js"] == {
        "patch": "directory-read.patch",
        "pristine": "c49c1c607b42e040cdf0b15d5a4a93cfef9ddb8147d492a3cfa2a8c3889dab24",
        "patched": "4ccb51f68e688c0ed1bbd971a15e33d2c1b6bb7eeb555285e1ab9ea75b01f741",
        "upgrades": {
            "d5d9b08c482b282f3390f415d69d460f4ee844046962a4013f11612cbb6b52e0":
                "delete-file.patch",
            "6528326240308f096c64db9a9cf45040cb6670957b38df772fc0e62af7193b2c":
                "trash-file.patch",
            "bc7293f3585cbbd0c5be8ef090d79654c2c79e1d79c698856e9d94613c99f746":
                "file-safety-to-read-chunk.patch",
            "75c68feb2ba9073bae277a25f663cd4ab369736ce62f2b4140197123df27a85e":
                "directory-read-to-file-safety.patch",
        },
    }
    assert "const readPath = workspaces.resolveReadPath(workspace, input.path);" in patch
    assert "isDirectory = (await stat(readPath.absolutePath)).isDirectory();" in patch
    assert "? await listDirectoryTool({ path: readPath.absolutePath }, {" in patch
    assert "+                root: workspace.root," in patch
    assert ": await readFileTool({ ...input, path: readPath.absolutePath }, {" in patch
    assert "+                readRoots: readPath.readRoots," in patch
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
        / "directory-read-to-chunk.patch"
    ).read_text(encoding="utf-8")
    assert "-import { randomUUID } from \"node:crypto\";" in migration
    assert '+    readChunk: "read_chunk",' in migration


def test_delete_file_patch_is_hash_gated_and_preserves_read_write_registration() -> None:
    patch_path = (
        MODULE_PATH.parent
        / "devspace-compat"
        / "1.0.4"
        / "delete-file.patch"
    )
    patch = patch_path.read_text(encoding="utf-8")

    assert digest(patch_path.read_bytes()) == "ba495e1b7430843528a684654b422253d2bea83a30102ffcd76857ff05efac5d"
    assert 'delete: "delete_file"' in patch
    assert "await unlink(target);" in patch
    assert "existsAfter: false" in patch
    assert "annotations: DELETE_TOOL_ANNOTATIONS" in patch
    assert "destructiveHint: true" in patch
    assert "readOnlyHint: false" in patch


def test_trash_file_patch_is_hash_gated_and_preserves_delete_registration() -> None:
    compat = load_compat()
    patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "trash-file.patch"
    ).read_text(encoding="utf-8")

    assert compat.PATCHES["dist/server.js"]["upgrades"][
        "6528326240308f096c64db9a9cf45040cb6670957b38df772fc0e62af7193b2c"
    ] == "trash-file.patch"
    assert 'trash: "trash_file"' in patch
    assert "await rename(target, destination);" in patch
    assert "await unlink(target);" not in patch
    assert "originalRelativePath: segments.join" in patch
    assert "trashRelativePath:" in patch
    assert "before.sha256 !== after.sha256" in patch
    assert "annotations: TRASH_TOOL_ANNOTATIONS" in patch
    assert "destructiveHint: true" in patch
    assert "readOnlyHint: false" in patch


def test_delete_file_patch_safety_contract_on_temporary_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat = load_compat()
    try:
        source_root = compat.resolve_package_roots()[0]
    except compat.DevSpaceCompatError as exc:
        pytest.skip(f"DevSpace 1.0.4 package unavailable: {exc.code}")
    package = tmp_path / "devspace"
    (package / "dist").mkdir(parents=True)
    shutil.copy2(source_root / "dist" / "server.js", package / "dist" / "server.js")
    (package / "package.json").write_text(json.dumps({"version": "1.0.4"}), encoding="utf-8")
    monkeypatch.setenv("CODEX_DEVSPACE_COMPAT_STATE_ROOT", str(tmp_path / "state"))
    compat.PATCHES = {"dist/server.js": compat.PATCHES["dist/server.js"]}
    compat.ensure_devspace_compatibility(package_root=package, backup_root=tmp_path / "backup")
    server = (package / "dist" / "server.js").read_text(encoding="utf-8")
    assert compat.sha256_file(package / "dist" / "server.js") == compat.PATCHES["dist/server.js"]["patched"]
    assert 'registerAppTool(server, toolNames.read' in server
    assert 'registerAppTool(server, toolNames.write' in server
    assert 'registerAppTool(server, toolNames.delete' in server
    assert "isDirectory = (await stat(readPath.absolutePath)).isDirectory();" in server
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
        pytest.skip(f"DevSpace 1.0.4 package unavailable: {exc.code}")
    package = tmp_path / "devspace"
    (package / "dist").mkdir(parents=True)
    shutil.copy2(source_root / "dist" / "server.js", package / "dist" / "server.js")
    (package / "package.json").write_text(json.dumps({"version": "1.0.4"}), encoding="utf-8")
    monkeypatch.setenv("CODEX_DEVSPACE_COMPAT_STATE_ROOT", str(tmp_path / "state"))
    compat.PATCHES = {"dist/server.js": compat.PATCHES["dist/server.js"]}

    result = compat.ensure_devspace_compatibility(
        package_root=package,
        backup_root=tmp_path / "backup",
    )

    server_path = package / "dist" / "server.js"
    server = server_path.read_text(encoding="utf-8")
    assert result["changed"] == ["dist/server.js"]
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
    (package / "package.json").write_text(json.dumps({"version": "1.0.4"}), encoding="utf-8")
    server = package / "dist" / "server.js"
    server.parent.mkdir()
    server.write_text("unknown upstream bytes\\n", encoding="utf-8")
    compat.PATCHES = {"dist/server.js": compat.PATCHES["dist/server.js"]}

    with pytest.raises(compat.DevSpaceCompatError) as mismatch:
        compat.ensure_devspace_compatibility(package_root=package)

    assert mismatch.value.code == "DEVSPACE_FILE_HASH_MISMATCH"
    assert mismatch.value.evidence["path"] == str(server)
