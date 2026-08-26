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
    package = tmp_path / "package"
    package.mkdir()
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


def test_service_stop_resolves_current_and_lkg_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compat = load_compat()
    current = tmp_path / "current"
    lkg = tmp_path / "lkg"
    foreign = tmp_path / "foreign"
    for root, version in ((current, compat.SUPPORTED_VERSION), (lkg, compat.LEGACY_LKG_VERSION), (foreign, "9.9.9")):
        root.mkdir()
        (root / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    monkeypatch.setattr(compat, "_candidate_roots", lambda: [foreign, lkg, current])

    assert compat.resolve_service_stop_roots() == [current.resolve(), lkg.resolve()]

    stopped: list[int] = []
    result = compat.stop_exact_devspace_service(
        service_probe=lambda port: {
            "pid": 46,
            "command_line": f"node {lkg / 'dist' / 'cli.js'} serve",
            "started_at_unix_ns": 1,
        },
        stopper=stopped.append,
    )
    assert result["stopped"] is True
    assert stopped == [46]


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
        "patched": "1370524581b75d6b91d281dea52e427004a5ac71c19ac8090d66fe521748760c",
        "upgrades": {
            "659cb1011cd7ab7fb75debb21a44f030001797c2160a42beac527354be93e497": "tool-read-receipts.patch",
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
    receipt_patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "tool-read-receipts.patch"
    ).read_text(encoding="utf-8")
    assert "AUDIT_NONCE_PATTERN" in receipt_patch
    assert 'open(receiptPath, "wx", 0o600)' in receipt_patch
    assert "codex.devspace.tool-read-receipt/v1" in receipt_patch
    assert "readChunkSha256: result.eof ? result.sha256 : null" in receipt_patch
    assert "readChunkOffsetBytes: result.offsetBytes" in receipt_patch
    assert "readChunkBytesReturned: result.bytesReturned" in receipt_patch
    assert "readChunkTotalBytes: result.totalBytes" in receipt_patch
    assert "readChunkEof: result.eof" in receipt_patch
    assert "auditNonce requires a nonempty OpenAI conversation scope" in receipt_patch
    assert 'const AUDIT_RECEIPT_SEQUENCE = ["open_workspace", "read", "read_chunk"]' in receipt_patch
    assert "reserveAuditReceipt" in receipt_patch
    assert "auditStep" in receipt_patch
    assert receipt_patch.count("Audit receipt ID:") == 3
    for tool in (
        'assertAuditReadonly(_meta, "exec_command")',
        'assertAuditReadonly(_meta, "write_stdin")',
        "assertAuditReadonly(_meta, toolNames.write)",
        "assertAuditReadonly(_meta, toolNames.edit)",
        'assertAuditReadonly(_meta, "apply_patch")',
        "assertAuditReadonly(_meta, toolNames.shell)",
        "assertAuditReadonly(_meta, toolNames.delete)",
        "assertAuditReadonly(_meta, toolNames.trash)",
    ):
        assert tool in receipt_patch
    assert "await writeToolReadReceipt" in receipt_patch


def test_108_artifact_write_is_bound_to_audit_readonly_scope() -> None:
    compat = load_compat()
    artifact_patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "artifact-audit-readonly.patch"
    ).read_text(encoding="utf-8")
    server_patch = (
        MODULE_PATH.parent
        / "devspace-compat"
        / compat.SUPPORTED_VERSION
        / "tool-read-receipts.patch"
    ).read_text(encoding="utf-8")

    assert compat.PATCHES["dist/artifact-tools.js"] == {
        "patch": "artifact-audit-readonly.patch",
        "pristine": "53a045b3961875afce5a95b3992aea3d156b64c0268b1d22724d2ed8e2c3aad2",
        "patched": "fd5204b37da657d6183c8394b5ee8bed09bbffd50999946b4d0421897a52dfa7",
    }
    assert "beforeMutation = () => {}" in artifact_patch
    assert "beforeMutation(input, _meta);" in artifact_patch
    assert 'assertAuditReadonly(_meta, "download_artifact")' in server_patch
    assert server_patch.index('assertAuditReadonly(_meta, "download_artifact")') < server_patch.index("return server;")


def test_108_tool_read_receipt_upgrade_is_immutable_and_records_only_successes(
    tmp_path: Path,
) -> None:
    compat = load_compat()
    try:
        source_root = compat.resolve_package_roots()[0]
    except compat.DevSpaceCompatError as exc:
        pytest.skip(f"DevSpace {compat.SUPPORTED_VERSION} package unavailable: {exc.code}")
    source = source_root / "dist" / "server.js"
    if compat.sha256_file(source) != next(iter(compat.PATCHES["dist/server.js"]["upgrades"])):
        pytest.skip("installed DevSpace server is not the prior hash-gated bridge payload")
    package = tmp_path / "devspace"
    (package / "dist").mkdir(parents=True)
    shutil.copy2(source, package / "dist" / "server.js")
    compat._apply_patch(
        package,
        MODULE_PATH.parent / "devspace-compat" / compat.SUPPORTED_VERSION / "tool-read-receipts.patch",
    )
    server = (package / "dist" / "server.js").read_text(encoding="utf-8")
    assert compat.sha256_file(package / "dist" / "server.js") == compat.PATCHES["dist/server.js"]["patched"]
    assert "open(receiptPath, \"wx\", 0o600)" in server
    read_handler = server[server.index("registerAppTool(server, toolNames.read,"):server.index("registerAppTool(server, toolNames.readChunk,")]
    chunk_handler = server[server.index("registerAppTool(server, toolNames.readChunk,"):server.index("if (config.toolMode !== \"codex\")")]
    assert read_handler.index("if (response.isError)") < read_handler.index("await writeToolReadReceipt")
    assert chunk_handler.index("await readUtf8Chunk") < chunk_handler.index("await writeToolReadReceipt")
    helper = re.search(
        r"const AUDIT_NONCE_PATTERN = .*?(?=export async function readUtf8Chunk)",
        server,
        flags=re.DOTALL,
    )
    assert helper is not None
    helper_source = helper.group(0).replace(
        "export async function writeToolReadReceipt", "async function writeToolReadReceipt", 1
    ).replace("randomUUID()", '"fixed-receipt-id"', 1)
    harness = tmp_path / "receipt-harness.mjs"
    harness.write_text(
        'import assert from "node:assert/strict";\n'
        'import { mkdir, open, readdir } from "node:fs/promises";\n'
        'import { homedir } from "node:os";\n'
        'import { isAbsolute, join } from "node:path";\n'
        + 'function openAiConversationScopeId(meta) { return meta?.scope ?? null; }\n'
        + helper_source
        + '\nconst root = process.argv[2];\n'
        + 'const base = {auditStep:3,tool:"read_chunk",workspaceId:"workspace-1",canonicalRoot:"C:/workspace",requestedRelativePath:"AGENTS.md",readChunkSha256:"a".repeat(64),readChunkOffsetBytes:0,readChunkBytesReturned:9,readChunkTotalBytes:9,readChunkEof:true,conversationScopeId:"conversation-1"};\n'
        + 'assert.equal(await writeToolReadReceipt({...base}, root), null);\n'
        + 'await assert.rejects(() => writeToolReadReceipt({...base,auditNonce:"too-short"}, root), /auditNonce/);\n'
        + 'await mkdir(root,{recursive:true}); assert.equal((await readdir(root)).length,0);\n'
        + 'const first = await writeToolReadReceipt({...base,auditNonce:"audit-nonce-0001"}, root);\n'
        + 'assert.equal(first.receipt.schema,"codex.devspace.tool-read-receipt/v1"); assert.equal(first.receipt.auditNonce,"audit-nonce-0001"); assert.equal(first.receipt.readChunkSha256,"a".repeat(64)); assert.deepEqual([first.receipt.readChunkOffsetBytes,first.receipt.readChunkBytesReturned,first.receipt.readChunkTotalBytes,first.receipt.readChunkEof],[0,9,9,true]); assert.ok(!("content" in first.receipt));\n'
        + 'await assert.rejects(() => writeToolReadReceipt({...base,auditNonce:"audit-nonce-0002"}, root), error => error?.code === "EEXIST");\n'
        + 'assert.equal((await readdir(root)).length,1);\n'
        + 'assert.throws(() => registerAuditReadonlyWorkspaceOpen({}, "audit-nonce-0003"), /nonempty OpenAI conversation scope/);\n'
        + 'registerAuditReadonlyWorkspaceOpen({scope:"ordinary-scope"}); assert.throws(() => registerAuditReadonlyWorkspaceOpen({scope:"ordinary-scope"}, "audit-nonce-0004"), /precede every ordinary/);\n'
        + 'registerAuditReadonlyWorkspaceOpen({scope:"audit-scope"}, "audit-nonce-0005"); registerAuditReadonlyWorkspaceOpen({scope:"audit-scope"}, "audit-nonce-0005"); assert.throws(() => registerAuditReadonlyWorkspaceOpen({scope:"audit-scope"}), /without auditNonce/);\n'
        + 'assert.deepEqual(reserveAuditReceipt({scope:"audit-scope"},"audit-nonce-0005","open_workspace"),{conversationScopeId:"audit-scope",auditStep:1}); assert.equal(reserveAuditReceipt({scope:"audit-scope"},"audit-nonce-0005","read").auditStep,2); assert.equal(reserveAuditReceipt({scope:"audit-scope"},"audit-nonce-0005","read_chunk").auditStep,3); assert.throws(() => reserveAuditReceipt({scope:"audit-scope"},"audit-nonce-0005","read_chunk"),/tool order invalid/);\n'
        + 'for (const tool of ["write","edit","apply_patch","exec_command","write_stdin","bash","delete_file","trash_file"]) assert.throws(() => assertAuditReadonly({scope:"audit-scope"}, tool), /audit-readonly/); assert.doesNotThrow(() => assertAuditReadonly({scope:"ordinary-scope"}, "write")); assert.doesNotThrow(() => assertAuditReadonly({scope:"pre-mutated-scope"}, "exec_command")); assert.throws(() => registerAuditReadonlyWorkspaceOpen({scope:"pre-mutated-scope"}, "audit-nonce-0006"), /precede every ordinary/);\n'
        + 'console.log(JSON.stringify({ok:true}));\n',
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(harness), str(tmp_path / "receipts")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"ok": True}


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
